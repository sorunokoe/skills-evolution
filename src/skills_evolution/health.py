from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import AUTO_BEGIN, AUTO_END, parse_trace_lines

LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
TABLE_SKILL_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|", re.MULTILINE)
TRACE_BLOCK_RE = re.compile(re.escape(AUTO_BEGIN) + r"\n(.*?)\n" + re.escape(AUTO_END), re.DOTALL)
TRACE_VERDICT_RE = re.compile(
	r"skill-verdict:\s*trace=([A-Za-z0-9._:-]+)\s+verdict=(tp|fp|fix-needed)(?:\s+reason=([a-z0-9_\-]+))?(?:\s+target=(line|section))?",
	re.IGNORECASE,
)
SKILL_MISS_RE = re.compile(
	r"skill-miss:\s*skill=([a-z0-9\-]+)(?:\s+reason=([a-z0-9_\-]+))?(?:\s+section=([a-zA-Z0-9_.\-]+))?",
	re.IGNORECASE,
)

_MAX_FEEDBACK_PAGES = 20
_SKILL_ALIAS_SUFFIXES = ("-standards", "-golden-path", "-template")
_COMMENT_GAP_HINTS = (
	"missing",
	"not covered",
	"should cover",
	"should mention",
	"should explain",
	"needs guidance",
	"need guidance",
	"needs docs",
	"need docs",
	"needs documentation",
	"need documentation",
	"not documented",
	"add guidance",
	"add example",
	"add examples",
	"document ",
	"documentation ",
)
_COMMENT_FIX_HINTS = (
	"outdated",
	"stale",
	"wrong",
	"incorrect",
	"inaccurate",
	"obsolete",
	"misleading",
	"confusing",
	"unclear",
	"contradict",
	"does not match",
	"doesn't match",
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class Finding:
	type: str
	severity: str
	skill: str
	file: str
	message: str
	line: int | None = None
	autofixable: bool = False
	suggestion: str | None = None

	def to_dict(self) -> dict[str, Any]:
		data = {
			"type": self.type,
			"severity": self.severity,
			"skill": self.skill,
			"file": self.file,
			"message": self.message,
			"autofixable": self.autofixable,
		}
		if self.line is not None:
			data["line"] = self.line
		if self.suggestion:
			data["suggestion"] = self.suggestion
		return data


def ensure_dir(path: Path) -> None:
	path.mkdir(parents=True, exist_ok=True)


def read_text(path: Path) -> str:
	return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
	path.write_text(content, encoding="utf-8")


def normalize_phrase(text: str) -> str:
	return _NON_ALNUM_RE.sub(" ", text.lower()).strip()


def comment_excerpt(text: str, limit: int = 160) -> str:
	flat = _WHITESPACE_RE.sub(" ", text).strip()
	if len(flat) <= limit:
		return flat
	return flat[: max(0, limit - 3)].rstrip() + "..."


def parse_frontmatter(content: str) -> tuple[dict[str, str], int | None, int | None]:
	match = FRONTMATTER_RE.match(content)
	if not match:
		return {}, None, None
	block = match.group(1)
	data: dict[str, str] = {}
	for raw_line in block.splitlines():
		line = raw_line.strip()
		if not line or ":" not in line:
			continue
		key, value = line.split(":", 1)
		data[key.strip()] = value.strip().strip("'").strip('"')
	return data, match.start(1), match.end(1)


def iter_skill_files(repo_root: Path) -> list[Path]:
	"""Return SKILL.md paths from .github/skills/ and .claude/skills/, deduplicating by skill name."""
	seen: set[str] = set()
	result: list[Path] = []
	for base_dir in [repo_root / ".github" / "skills", repo_root / ".claude" / "skills"]:
		for skill_file in sorted(base_dir.glob("*/SKILL.md")):
			name = skill_file.parent.name
			if name not in seen:
				seen.add(name)
				result.append(skill_file)
	return result


def iter_oss_skill_files(repo_root: Path) -> list[Path]:
	"""Return [SKILL.md] from the root of an open-source skill repository.

	Returns an empty list if SKILL.md is missing at the repo root.
	Callers should treat an empty result as a configuration error, not a silent no-op.
	"""
	skill_file = repo_root / "SKILL.md"
	if skill_file.exists():
		return [skill_file]
	return []


def iter_oss_markdown_files(skill_root: Path) -> list[Path]:
	"""Return authoritative skill content files for an OSS skill repo.

	Scope: SKILL.md at root + all *.md files under references/.
	Intentionally excludes README.md, AGENTS.md, CONTRIBUTING.md, CHANGELOG.md
	— those are repository docs, not authoritative skill content.
	"""
	result: list[Path] = []
	root_skill = skill_root / "SKILL.md"
	if root_skill.exists():
		result.append(root_skill)
	refs_dir = skill_root / "references"
	if refs_dir.is_dir():
		result.extend(sorted(refs_dir.glob("*.md")))
	return result


def iter_markdown_files(skill_dir: Path) -> list[Path]:
	return sorted(skill_dir.rglob("*.md"))


def build_skill_aliases(repo_root: Path) -> dict[str, set[str]]:
	aliases: dict[str, set[str]] = {}
	for skill_file in iter_skill_files(repo_root):
		skill = skill_file.parent.name
		content = read_text(skill_file)
		frontmatter, _, _ = parse_frontmatter(content)
		values = {skill, skill.replace("-", " ")}
		name = frontmatter.get("name", "")
		if name:
			values.add(name)
		for suffix in _SKILL_ALIAS_SUFFIXES:
			if skill.endswith(suffix):
				base = skill[: -len(suffix)]
				if base:
					values.add(base)
					values.add(base.replace("-", " "))
		normalized = {normalize_phrase(value) for value in values}
		aliases[skill] = {value for value in normalized if value}
	return aliases


def detect_comment_feedback_type(text: str) -> str | None:
	normalized = normalize_phrase(text)
	if not normalized:
		return None
	if any(phrase in normalized for phrase in _COMMENT_FIX_HINTS):
		return "fix"
	if any(phrase in normalized for phrase in _COMMENT_GAP_HINTS):
		return "gap"
	return None


def extract_comment_feedback_signals(text: str, skill_aliases: dict[str, set[str]]) -> list[dict[str, str]]:
	if TRACE_VERDICT_RE.search(text) or SKILL_MISS_RE.search(text):
		return []
	feedback_type = detect_comment_feedback_type(text)
	if feedback_type is None:
		return []
	normalized = f" {normalize_phrase(text)} "
	matched_skills = sorted(
		skill
		for skill, aliases in skill_aliases.items()
		if any(f" {alias} " in normalized for alias in aliases)
	)
	if not matched_skills:
		return []
	snippet = comment_excerpt(text)
	return [{"skill": skill, "type": feedback_type, "comment": snippet} for skill in matched_skills]


def local_link_target(md_file: Path, link: str) -> tuple[Path | None, str | None]:
	if link.startswith(("http://", "https://", "mailto:", "#", "/")):
		return None, None
	clean_link = link.split("?", 1)[0]
	if "#" in clean_link:
		path_part, fragment = clean_link.split("#", 1)
	else:
		path_part, fragment = clean_link, None
	if not path_part:
		return None, fragment
	return (md_file.parent / path_part).resolve(), fragment


def maybe_fix_link(md_file: Path, skill_dir: Path, broken_link: str) -> str | None:
	path_part = broken_link.split("#", 1)[0]
	if not path_part:
		return None
	base = Path(path_part).name.lower()
	candidates = [p for p in skill_dir.rglob("*") if p.is_file() and p.name.lower() == base]
	if len(candidates) != 1:
		return None
	rel = os.path.relpath(candidates[0], md_file.parent).replace("\\", "/")
	if "#" in broken_link:
		fragment = broken_link.split("#", 1)[1]
		return f"{rel}#{fragment}"
	return rel


def audit_skills(repo_root: Path, output_dir: Path, apply_autofix: bool, oss: bool = False) -> int:
	if oss:
		skill_files = iter_oss_skill_files(repo_root)
	else:
		skill_files = iter_skill_files(repo_root)

	findings: list[Finding] = []
	skill_name_to_paths: dict[str, list[Path]] = defaultdict(list)
	autofix_changes = 0
	link_fixes: list[dict[str, str]] = []

	if oss and not skill_files:
		findings.append(
			Finding(
				type="MISSING_SKILL_FILE",
				severity="error",
				skill=repo_root.name,
				file="SKILL.md",
				message="SKILL.md not found at repository root. Open-source skill repos must have SKILL.md at the root.",
			)
		)

	for skill_file in skill_files:
		skill_dir = skill_file.parent
		folder_name = skill_dir.name
		content = read_text(skill_file)
		frontmatter, _, _ = parse_frontmatter(content)

		# In OSS mode use frontmatter name as the canonical identifier; the checkout
		# directory name is not meaningful (e.g., "workspace" on CI).
		if oss and frontmatter:
			effective_skill = frontmatter.get("name") or folder_name
		else:
			effective_skill = folder_name

		if not frontmatter:
			findings.append(
				Finding(
					type="METADATA_DRIFT",
					severity="error",
					skill=effective_skill,
					file=str(skill_file.relative_to(repo_root)),
					message="Missing YAML frontmatter block.",
				)
			)
		else:
			for field in ("name", "description", "applyTo"):
				if not frontmatter.get(field):
					findings.append(
						Finding(
							type="METADATA_DRIFT",
							severity="error",
							skill=effective_skill,
							file=str(skill_file.relative_to(repo_root)),
							message=f"Missing required frontmatter field: {field}",
						)
					)

			name = frontmatter.get("name", "")
			if name:
				skill_name_to_paths[name].append(skill_file)
				# Name/folder mismatch is intentional in OSS mode — the repo can be
				# checked out to any directory name on CI.
				if not oss and name != folder_name:
					findings.append(
						Finding(
							type="METADATA_DRIFT",
							severity="warning",
							skill=effective_skill,
							file=str(skill_file.relative_to(repo_root)),
							message=f"Frontmatter name '{name}' does not match folder '{folder_name}'.",
							autofixable=True,
							suggestion=f"Use name: {folder_name}",
						)
					)
					if apply_autofix:
						new_content = re.sub(r"(?m)^name:\s*.*$", f"name: {folder_name}", content, count=1)
						if new_content != content:
							write_text(skill_file, new_content)
							content = new_content
							autofix_changes += 1

		md_files = iter_oss_markdown_files(skill_dir) if oss else iter_markdown_files(skill_dir)
		for md_file in md_files:
			md_content = read_text(md_file)
			for idx, line in enumerate(md_content.splitlines(), start=1):
				for _, link in LINK_RE.findall(line):
					target, _ = local_link_target(md_file, link)
					if target is None or target.exists():
						continue
					rel_file = str(md_file.relative_to(repo_root))
					finding = Finding(
						type="BROKEN_LINK",
						severity="warning",
						skill=effective_skill,
						file=rel_file,
						line=idx,
						message=f"Broken local markdown link: {link}",
						autofixable=False,
					)
					fixed = maybe_fix_link(md_file, skill_dir, link)
					if fixed:
						finding.autofixable = True
						finding.suggestion = f"Replace with: {fixed}"
						if apply_autofix:
							replacement = md_content.replace(f"({link})", f"({fixed})")
							if replacement != md_content:
								md_content = replacement
								autofix_changes += 1
								link_fixes.append({"file": rel_file, "from": link, "to": fixed})
					findings.append(finding)
			if apply_autofix:
				write_text(md_file, md_content)

	for name, paths in sorted(skill_name_to_paths.items()):
		if len(paths) > 1:
			for path in paths:
				findings.append(
					Finding(
						type="METADATA_DRIFT",
						severity="error",
						skill=path.parent.name,
						file=str(path.relative_to(repo_root)),
						message=f"Duplicate skill name '{name}' appears in multiple folders.",
					)
				)

	# REGISTRY_DRIFT is only meaningful in consumer repos where a skills table
	# is expected in .github/copilot-instructions.md. Skip in OSS mode.
	if not oss:
		copilot_instructions = repo_root / ".github" / "copilot-instructions.md"
		if copilot_instructions.exists():
			table_skills = set(TABLE_SKILL_RE.findall(read_text(copilot_instructions)))
			folder_skills = {p.parent.name for p in skill_files}
			for skill in sorted(folder_skills - table_skills):
				findings.append(
					Finding(
						type="REGISTRY_DRIFT",
						severity="warning",
						skill=skill,
						file=".github/copilot-instructions.md",
						message=f"Skill '{skill}' exists in .github/skills but is missing from the skills table.",
					)
				)
			for skill in sorted(table_skills - folder_skills):
				findings.append(
					Finding(
						type="REGISTRY_DRIFT",
						severity="warning",
						skill=skill,
						file=".github/copilot-instructions.md",
						message=f"Skill '{skill}' is listed in the skills table but folder is missing.",
					)
				)

	by_type = Counter(f.type for f in findings)
	output = {
		"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
		"skill_count": len(skill_files),
		"findings_count": len(findings),
		"by_type": dict(by_type),
		"autofix_changes": autofix_changes,
		"link_fixes": link_fixes,
		"findings": [f.to_dict() for f in findings],
	}
	ensure_dir(output_dir)
	write_text(output_dir / "skills-audit.json", json.dumps(output, indent=2))

	lines = [
		"# Skills Audit Report",
		"",
		f"- Generated: {output['generated_at']}",
		f"- Skills scanned: {output['skill_count']}",
		f"- Findings: {output['findings_count']}",
		f"- Autofix changes applied: {output['autofix_changes']}",
		"",
		"## Findings by type",
		"",
	]
	for key, value in sorted(by_type.items()):
		lines.append(f"- {key}: {value}")
	lines.extend(["", "## Finding details", ""])
	if not findings:
		lines.append("- No findings.")
	else:
		for finding in findings:
			location = finding.file + (f":{finding.line}" if finding.line else "")
			suffix = f" _(suggestion: {finding.suggestion})_" if finding.suggestion else ""
			lines.append(f"- **{finding.type}** [{finding.severity}] `{location}` — {finding.message}{suffix}")
	write_text(output_dir / "skills-audit.md", "\n".join(lines) + "\n")
	return len(findings)


def github_get(url: str, token: str) -> tuple[Any, str | None]:
	req = urllib.request.Request(url)
	req.add_header("Authorization", f"Bearer {token}")
	req.add_header("Accept", "application/vnd.github+json")
	with urllib.request.urlopen(req) as resp:
		body = resp.read().decode("utf-8")
		link = resp.headers.get("Link")
		return json.loads(body), link


def parse_next_link(link_header: str | None) -> str | None:
	if not link_header:
		return None
	for part in link_header.split(","):
		section = part.strip()
		if 'rel="next"' in section:
			return section[section.find("<") + 1 : section.find(">")]
	return None


def collect_feedback(repo: str, token: str, since_days: int, output_path: Path) -> int:
	now = dt.datetime.now(dt.timezone.utc)
	since = now - dt.timedelta(days=since_days)
	api = f"https://api.github.com/repos/{repo}"
	url: str | None = f"{api}/pulls?state=closed&sort=updated&direction=desc&per_page=100"
	pulls: list[dict[str, Any]] = []
	page_count = 0

	while url:
		if page_count >= _MAX_FEEDBACK_PAGES:
			print(
				f"::warning::collect-feedback: reached {_MAX_FEEDBACK_PAGES}-page limit; some PRs may be excluded.",
				file=sys.stderr,
			)
			break
		page_count += 1
		items, link = github_get(url, token)
		if not isinstance(items, list):
			break
		for pr in items:
			merged_at = pr.get("merged_at")
			if not merged_at:
				continue
			merged_time = dt.datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
			if merged_time < since:
				continue
			number = pr["number"]
			issue_comments, _ = github_get(f"{api}/issues/{number}/comments?per_page=100", token)
			review_comments, _ = github_get(f"{api}/pulls/{number}/comments?per_page=100", token)
			reviews, _ = github_get(f"{api}/pulls/{number}/reviews?per_page=100", token)
			pulls.append(
				{
					"number": number,
					"title": pr.get("title"),
					"url": pr.get("html_url"),
					"merged_at": merged_at,
					"body": pr.get("body") or "",
					"issue_comments": issue_comments or [],
					"review_comments": review_comments or [],
					"reviews": reviews or [],
				}
			)
		url = parse_next_link(link)

	data = {
		"generated_at": now.isoformat(),
		"repo": repo,
		"since_days": since_days,
		"since": since.isoformat(),
		"pull_requests": pulls,
	}
	ensure_dir(output_path.parent)
	write_text(output_path, json.dumps(data, indent=2))
	return len(pulls)


def extract_text_blobs(pr: dict[str, Any]) -> list[str]:
	texts = [pr.get("body") or ""]
	for container_key in ("issue_comments", "review_comments", "reviews"):
		for item in pr.get(container_key, []):
			texts.append(item.get("body") or "")
	return texts


def extract_trace_records(pr: dict[str, Any]) -> list[dict[str, Any]]:
	body = pr.get("body") or ""
	records: list[dict[str, Any]] = []
	for block in TRACE_BLOCK_RE.findall(body):
		for record in parse_trace_lines(block):
			record["trace_key"] = f"{pr['number']}:{record['trace_id']}"
			record["pr_number"] = pr["number"]
			records.append(record)
	return records


def analyze_feedback(raw_path: Path, repo_root: Path, output_dir: Path) -> int:
	raw = json.loads(read_text(raw_path))
	known_skills = {p.parent.name for p in iter_skill_files(repo_root)}
	skill_aliases = build_skill_aliases(repo_root)
	per_skill = {
		skill: {
			"tp": 0,
			"fp": 0,
			"fn": 0,
			"usage": 0,
			"fix_needed": 0,
			"comment_gap": 0,
			"comment_fix": 0,
			"fp_reasons": Counter(),
			"miss_reasons": Counter(),
			"comment_examples": {"gap": [], "fix": []},
		}
		for skill in known_skills
	}
	trace_index: dict[str, dict[str, Any]] = {}
	disputed_sections: dict[tuple[str, str, str, int, int], dict[str, Any]] = {}
	orphan_verdicts: list[dict[str, Any]] = []
	proposals: list[dict[str, Any]] = []

	for pr in raw.get("pull_requests", []):
		for trace in extract_trace_records(pr):
			skill = trace["skill"]
			if skill not in per_skill:
				continue
			per_skill[skill]["usage"] += 1
			trace_index[trace["trace_key"]] = trace
			section_key = (skill, trace["file"], trace["section_id"], trace["line_start"], trace["line_end"])
			if section_key not in disputed_sections:
				disputed_sections[section_key] = {
					"skill": skill,
					"file": trace["file"],
					"section_id": trace["section_id"],
					"line_start": trace["line_start"],
					"line_end": trace["line_end"],
					"usage": 0,
					"tp": 0,
					"fp": 0,
					"fix_needed": 0,
					"reasons": Counter(),
					"sample_reason": trace.get("reason", ""),
				}
			disputed_sections[section_key]["usage"] += 1

		texts = extract_text_blobs(pr)
		for text in texts:
			if not text:
				continue
			for trace_id, verdict, reason, target in TRACE_VERDICT_RE.findall(text):
				trace = trace_index.get(f"{pr['number']}:{trace_id}")
				if trace is None:
					orphan_verdicts.append(
						{
							"pr_number": pr["number"],
							"trace_id": trace_id,
							"verdict": verdict.lower(),
							"reason": reason,
							"target": target,
						}
					)
					continue
				skill = trace["skill"]
				section_key = (skill, trace["file"], trace["section_id"], trace["line_start"], trace["line_end"])
				stats = disputed_sections[section_key]
				if verdict.lower() == "tp":
					per_skill[skill]["tp"] += 1
					stats["tp"] += 1
				elif verdict.lower() == "fp":
					per_skill[skill]["fp"] += 1
					stats["fp"] += 1
					if reason:
						per_skill[skill]["fp_reasons"][reason] += 1
						stats["reasons"][reason] += 1
				else:
					per_skill[skill]["fix_needed"] += 1
					stats["fix_needed"] += 1
					if reason:
						stats["reasons"][reason] += 1
			for skill, reason, section in SKILL_MISS_RE.findall(text):
				s = skill.strip().lower()
				if s not in per_skill:
					continue
				per_skill[s]["fn"] += 1
				if reason:
					per_skill[s]["miss_reasons"][reason] += 1
			for signal in extract_comment_feedback_signals(text, skill_aliases):
				stats = per_skill[signal["skill"]]
				if signal["type"] == "gap":
					stats["comment_gap"] += 1
				else:
					stats["comment_fix"] += 1
				examples = stats["comment_examples"][signal["type"]]
				if signal["comment"] not in examples and len(examples) < 3:
					examples.append(signal["comment"])

	metrics = {}
	for skill, counts in per_skill.items():
		tp = counts["tp"]
		fp = counts["fp"]
		fn = counts["fn"]
		den = tp + fp
		precision = (tp / den) if den > 0 else None
		fp_rate = (fp / den) if den > 0 else None
		recall_den = tp + fn
		recall = (tp / recall_den) if recall_den > 0 else None
		metrics[skill] = {
			"tp": tp,
			"fp": fp,
			"fn": fn,
			"usage": counts["usage"],
			"fix_needed": counts["fix_needed"],
			"comment_gap": counts["comment_gap"],
			"comment_fix": counts["comment_fix"],
			"precision": precision,
			"precision_proxy": precision,  # backward-compat alias; deprecated
			"recall": recall,
			"fp_rate": fp_rate,
			"fp_reasons": dict(counts["fp_reasons"]),
			"miss_reasons": dict(counts["miss_reasons"]),
			"comment_examples": counts["comment_examples"],
		}
		total_gap_signals = fn + counts["comment_gap"]
		if total_gap_signals >= 2:
			proposals.append(
				{
					"skill": skill,
					"type": "ADD_MISSING_GUIDANCE",
					"action": "Add missing guidance or examples for scenarios repeatedly called out as absent.",
					"evidence": {
						"fn": fn,
						"comment_gap": counts["comment_gap"],
						"miss_reasons": dict(counts["miss_reasons"]),
						"sample_comments": counts["comment_examples"]["gap"],
					},
				}
			)
		if counts["comment_fix"] >= 2:
			proposals.append(
				{
					"skill": skill,
					"type": "REVIEW_SKILL_FEEDBACK",
					"action": "Review this skill for stale, inaccurate, or confusing guidance repeatedly called out in PR comments.",
					"evidence": {"comment_fix": counts["comment_fix"], "sample_comments": counts["comment_examples"]["fix"]},
				}
			)
	for section in disputed_sections.values():
		total_disputes = section["fp"] + section["fix_needed"]
		if total_disputes >= 2:
			proposals.append(
				{
					"skill": section["skill"],
					"type": "REVIEW_SECTION",
					"action": "Review this exact section/line range for stale, overly strict, or inaccurate guidance.",
					"evidence": {
						"file": section["file"],
						"section_id": section["section_id"],
						"line_start": section["line_start"],
						"line_end": section["line_end"],
						"fp": section["fp"],
						"fix_needed": section["fix_needed"],
						"reasons": dict(section["reasons"]),
					},
				}
			)

	sorted_sections = sorted(
		disputed_sections.values(),
		key=lambda item: (-(item["fp"] + item["fix_needed"]), -item["usage"], item["skill"], item["line_start"]),
	)

	comment_signals = []
	for skill in sorted(metrics.keys()):
		m = metrics[skill]
		if m["comment_gap"] == 0 and m["comment_fix"] == 0:
			continue
		comment_signals.append(
			{
				"skill": skill,
				"gap": m["comment_gap"],
				"fix": m["comment_fix"],
				"examples": m["comment_examples"],
			}
		)

	output = {
		"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
		"source_pull_requests": len(raw.get("pull_requests", [])),
		"trace_count": len(trace_index),
		"comment_signal_count": sum(item["gap"] + item["fix"] for item in comment_signals),
		"metrics_by_skill": metrics,
		"comment_signals": comment_signals,
		"disputed_sections": [
			{
				"skill": item["skill"],
				"file": item["file"],
				"section_id": item["section_id"],
				"line_start": item["line_start"],
				"line_end": item["line_end"],
				"usage": item["usage"],
				"tp": item["tp"],
				"fp": item["fp"],
				"fix_needed": item["fix_needed"],
				"reasons": dict(item["reasons"]),
				"sample_reason": item["sample_reason"],
			}
			for item in sorted_sections
			if item["fp"] > 0 or item["fix_needed"] > 0
		],
		"orphan_verdicts": orphan_verdicts,
		"proposal_count": len(proposals),
		"proposals": proposals,
	}
	ensure_dir(output_dir)
	write_text(output_dir / "skills-feedback.json", json.dumps(output, indent=2))

	lines = [
		"# Skills Feedback Report",
		"",
		f"- Pull requests analyzed: {output['source_pull_requests']}",
		f"- Explicit skill traces analyzed: {output['trace_count']}",
		f"- Review comment signals analyzed: {output['comment_signal_count']}",
		f"- Improvement proposals: {output['proposal_count']}",
		"",
		"## Skill metrics",
		"",
		"| Skill | Usage | TP | FP | Fix needed | Misses | Comment gaps | Comment fixes | Precision | Recall |",
		"|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
	]
	for skill in sorted(metrics.keys()):
		m = metrics[skill]
		precision = "n/a" if m["precision"] is None else f"{m['precision']:.2f}"
		recall = "n/a" if m["recall"] is None else f"{m['recall']:.2f}"
		lines.append(
			f"| `{skill}` | {m['usage']} | {m['tp']} | {m['fp']} | {m['fix_needed']} | {m['fn']} | {m['comment_gap']} | {m['comment_fix']} | {precision} | {recall} |"
		)
	lines.extend(["", "## Review comment signals", ""])
	if not comment_signals:
		lines.append("- No normal PR comments were confidently mapped to a skill.")
	else:
		for item in comment_signals[:10]:
			lines.append(f"- `{item['skill']}` gap={item['gap']} fix={item['fix']}")
			for example in item["examples"]["gap"][:2]:
				lines.append(f"  - gap example: {example}")
			for example in item["examples"]["fix"][:2]:
				lines.append(f"  - fix example: {example}")
	lines.extend(["", "## Disputed sections", ""])
	disputed = output["disputed_sections"]
	if not disputed:
		lines.append("- No disputed traced sections.")
	else:
		for item in disputed[:10]:
			span = f"{item['line_start']}"
			if item["line_end"] != item["line_start"]:
				span += f"-{item['line_end']}"
			lines.append(
				f"- `{item['skill']}` `{item['file']}:{span}` (`{item['section_id']}`) — "
				f"usage={item['usage']}, fp={item['fp']}, fix-needed={item['fix_needed']}, reasons={item['reasons']}"
			)
	lines.extend(["", "## Proposed improvements", ""])
	if not proposals:
		lines.append("- No proposals generated from current feedback window.")
	else:
		for proposal in proposals:
			lines.append(
				f"- `{proposal['skill']}` **{proposal['type']}**: {proposal['action']} (evidence: {proposal['evidence']})"
			)
	write_text(output_dir / "skills-feedback.md", "\n".join(lines) + "\n")
	return len(proposals)


def combine_reports(output_dir: Path) -> tuple[int, int]:
	audit_path = output_dir / "skills-audit.json"
	feedback_path = output_dir / "skills-feedback.json"
	semantic_path = output_dir / "skills-semantic.json"
	ai_updates_path = output_dir / "skills-ai-updates.json"
	audit = json.loads(read_text(audit_path)) if audit_path.exists() else {"findings_count": 0}
	feedback = (
		json.loads(read_text(feedback_path))
		if feedback_path.exists()
		else {"proposal_count": 0, "trace_count": 0, "comment_signal_count": 0, "disputed_sections": []}
	)
	semantic_enabled = semantic_path.exists()
	semantic = json.loads(read_text(semantic_path)) if semantic_enabled else {"content_findings": [], "proposals": [], "note": "Optional AI semantic review was not run."}
	ai_updates_enabled = ai_updates_path.exists()
	ai_updates = json.loads(read_text(ai_updates_path)) if ai_updates_enabled else {}
	findings_count = int(audit.get("findings_count", 0))
	proposal_count = int(feedback.get("proposal_count", 0))
	semantic_findings_count = len(semantic.get("content_findings", [])) if semantic_enabled else 0
	semantic_proposals_count = len(semantic.get("proposals", [])) if semantic_enabled else 0
	trace_count = int(feedback.get("trace_count", 0))
	comment_signal_count = int(feedback.get("comment_signal_count", 0))
	disputed_count = len(feedback.get("disputed_sections", []))
	proposal_count += semantic_proposals_count
	ai_patches_applied = int(ai_updates.get("total_patches_applied", 0)) if ai_updates_enabled else 0
	ai_skills_changed = int(ai_updates.get("skills_changed", 0)) if ai_updates_enabled else 0
	# Include applied AI patches in findings so the PR-creation gate fires when skills were updated.
	findings_count += ai_patches_applied
	now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

	lines = [f"🤖 Monthly skill health check — {now}", ""]

	# --- AI patches: show the actual diffs so the reviewer can judge ---
	if ai_updates_enabled and ai_patches_applied > 0:
		patch_word = "patch" if ai_patches_applied == 1 else "patches"
		lines += [
			f"## {ai_patches_applied} version {patch_word} applied across {ai_skills_changed} file(s)",
			"",
			"Versions are fetched live from GitHub releases — no training-data guesses.",
			"",
		]
		for entry in ai_updates.get("by_skill", []):
			if entry.get("applied", 0) == 0:
				continue
			label = entry.get("file") or entry.get("skill", "?")
			applied_patches = [p for p in entry.get("patches", []) if p.get("_status") == "applied"]
			lines.append(f"### `{label}`")
			lines.append("")
			if applied_patches:
				for p in applied_patches:
					reason = p.get("reason", "").strip()
					lines.append(f"- {reason}" if reason else "- version bump")
				lines.append("")
			else:
				lines += [f"_{entry.get('applied', 0)} patch(es) applied — see Files changed tab._", ""]

	# --- Structural findings ---
	structural_count = findings_count - ai_patches_applied
	if structural_count > 0:
		lines += [
			"## Structural findings",
			"",
			f"{structural_count} issue(s) found — see `skills-audit.md` in the run artifacts for details.",
			"",
		]

	# --- Content-level findings (optional Copilot step) ---
	if semantic_findings_count:
		lines += ["## Content findings (Copilot review)", ""]
		for item in semantic.get("content_findings", [])[:5]:
			file_path = item.get("file", "unknown")
			issue = item.get("issue_type", "CONTENT_ACCURACY")
			evidence = item.get("evidence", "").strip()
			span = ""
			if item.get("line_start"):
				span = f":{item.get('line_start')}"
				if item.get("line_end") and item.get("line_end") != item.get("line_start"):
					span += f"-{item.get('line_end')}"
			lines.append(f"- **{issue}** in `{file_path}{span}` — {evidence}")
		lines.append("")

	# --- Nothing to show ---
	if findings_count == 0:
		lines += ["_Nothing to fix this month. The skills are in great shape! 🎉_", ""]

	lines += [
		"---",
		"_Review each diff above. If something looks wrong, close this PR — the bot runs again next month._",
	]
	write_text(output_dir / "skills-health-summary.md", "\n".join(lines) + "\n")
	return findings_count, proposal_count


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Skills health automation toolkit")
	sub = parser.add_subparsers(dest="command", required=True)

	audit = sub.add_parser("audit")
	audit.add_argument("--repo-root", required=True)
	audit.add_argument("--output-dir", required=True)
	audit.add_argument("--apply-autofix", action="store_true")
	audit.add_argument(
		"--oss",
		action="store_true",
		help="Audit an open-source skill repo where SKILL.md is at the repository root.",
	)

	collect = sub.add_parser("collect-feedback")
	collect.add_argument("--repo", required=True)
	collect.add_argument("--token", required=True)
	collect.add_argument("--since-days", type=int, default=35)
	collect.add_argument("--output", required=True)

	feedback = sub.add_parser("feedback")
	feedback.add_argument("--repo-root", required=True)
	feedback.add_argument("--raw", required=True)
	feedback.add_argument("--output-dir", required=True)

	combine = sub.add_parser("combine")
	combine.add_argument("--output-dir", required=True)

	args = parser.parse_args(argv)
	if args.command == "audit":
		findings = audit_skills(Path(args.repo_root), Path(args.output_dir), args.apply_autofix, oss=args.oss)
		print(f"findings_count={findings}")
		return 0
	if args.command == "collect-feedback":
		pr_count = collect_feedback(args.repo, args.token, args.since_days, Path(args.output))
		print(f"pull_requests={pr_count}")
		return 0
	if args.command == "feedback":
		proposal_count = analyze_feedback(Path(args.raw), Path(args.repo_root), Path(args.output_dir))
		print(f"proposal_count={proposal_count}")
		return 0
	if args.command == "combine":
		findings, proposals = combine_reports(Path(args.output_dir))
		print(f"findings_count={findings}")
		print(f"proposal_count={proposals}")
		semantic_path = Path(args.output_dir) / "skills-semantic.json"
		semantic = json.loads(read_text(semantic_path)) if semantic_path.exists() else {"content_findings": []}
		has_findings = findings > 0 or proposals > 0 or len(semantic.get("content_findings", [])) > 0
		print(f"has_findings={'true' if has_findings else 'false'}")
		return 0
	return 1


if __name__ == "__main__":
	sys.exit(main())
