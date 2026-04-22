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
	return sorted((repo_root / ".github" / "skills").glob("*/SKILL.md"))


def iter_markdown_files(skill_dir: Path) -> list[Path]:
	return sorted(skill_dir.rglob("*.md"))


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


def audit_skills(repo_root: Path, output_dir: Path, apply_autofix: bool) -> int:
	skill_files = iter_skill_files(repo_root)
	findings: list[Finding] = []
	skill_name_to_paths: dict[str, list[Path]] = defaultdict(list)
	autofix_changes = 0
	link_fixes: list[dict[str, str]] = []

	for skill_file in skill_files:
		skill_dir = skill_file.parent
		folder_name = skill_dir.name
		content = read_text(skill_file)
		frontmatter, _, _ = parse_frontmatter(content)

		if not frontmatter:
			findings.append(
				Finding(
					type="METADATA_DRIFT",
					severity="error",
					skill=folder_name,
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
							skill=folder_name,
							file=str(skill_file.relative_to(repo_root)),
							message=f"Missing required frontmatter field: {field}",
						)
					)

			name = frontmatter.get("name", "")
			if name:
				skill_name_to_paths[name].append(skill_file)
				if name != folder_name:
					findings.append(
						Finding(
							type="METADATA_DRIFT",
							severity="warning",
							skill=folder_name,
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

		for md_file in iter_markdown_files(skill_dir):
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
						skill=folder_name,
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
	per_skill = {
		skill: {"tp": 0, "fp": 0, "fn": 0, "usage": 0, "fix_needed": 0, "fp_reasons": Counter(), "miss_reasons": Counter()}
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
			"precision": precision,
			"precision_proxy": precision,  # backward-compat alias; deprecated
			"recall": recall,
			"fp_rate": fp_rate,
			"fp_reasons": dict(counts["fp_reasons"]),
			"miss_reasons": dict(counts["miss_reasons"]),
		}
		if fn >= 2:
			proposals.append(
				{
					"skill": skill,
					"type": "ADD_MISSING_GUIDANCE",
					"action": "Add missing guidance or examples for scenarios repeatedly called out as absent.",
					"evidence": {"fn": fn, "miss_reasons": dict(counts["miss_reasons"])},
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

	output = {
		"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
		"source_pull_requests": len(raw.get("pull_requests", [])),
		"trace_count": len(trace_index),
		"metrics_by_skill": metrics,
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
		f"- Improvement proposals: {output['proposal_count']}",
		"",
		"## Skill metrics",
		"",
		"| Skill | Usage | TP | FP | Fix needed | Misses | Precision | Recall |",
		"|---|---:|---:|---:|---:|---:|---:|---:|",
	]
	for skill in sorted(metrics.keys()):
		m = metrics[skill]
		precision = "n/a" if m["precision"] is None else f"{m['precision']:.2f}"
		recall = "n/a" if m["recall"] is None else f"{m['recall']:.2f}"
		lines.append(
			f"| `{skill}` | {m['usage']} | {m['tp']} | {m['fp']} | {m['fix_needed']} | {m['fn']} | {precision} | {recall} |"
		)
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
	audit = json.loads(read_text(audit_path)) if audit_path.exists() else {"findings_count": 0}
	feedback = json.loads(read_text(feedback_path)) if feedback_path.exists() else {"proposal_count": 0, "trace_count": 0, "disputed_sections": []}
	semantic_enabled = semantic_path.exists()
	semantic = json.loads(read_text(semantic_path)) if semantic_enabled else {"content_findings": [], "proposals": [], "note": "Optional AI semantic review was not run."}
	findings_count = int(audit.get("findings_count", 0))
	proposal_count = int(feedback.get("proposal_count", 0))
	semantic_findings_count = len(semantic.get("content_findings", [])) if semantic_enabled else 0
	semantic_proposals_count = len(semantic.get("proposals", [])) if semantic_enabled else 0
	trace_count = int(feedback.get("trace_count", 0))
	disputed_count = len(feedback.get("disputed_sections", []))
	proposal_count += semantic_proposals_count
	now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

	lines = [
		f"# Skills Health Summary ({now})",
		"",
		"## Executive summary",
		"",
		f"- Audit findings: **{findings_count}**",
		f"- Explicit AI traces analyzed: **{trace_count}**",
		f"- Disputed traced sections: **{disputed_count}**",
		f"- Optional AI content-level findings: **{semantic_findings_count}**" if semantic_enabled else "- Optional AI content-level findings: **not run**",
		f"- Total improvement proposals (rules + content): **{proposal_count}**",
		"",
		"## Root-cause buckets",
		"",
		"- `BROKEN_LINK`: invalid intra-skill references",
		"- `METADATA_DRIFT`: frontmatter/schema drift",
		"- `REGISTRY_DRIFT`: skill table mismatch",
		"- `DISPUTED_SECTION`: specific traced section repeatedly marked FP or fix-needed",
		"- `MISSING_GUIDANCE`: developers reported a guidance gap for a skill",
		"",
		"## Reports",
		"",
		"- See `skills-audit.md` for structural findings",
		"- See `skills-feedback.md` for trace/verdict analysis and disputed sections",
		"",
		"## TRIZ lens applied",
		"",
		"- AC: improve skill quality without intrusive telemetry or broad guesswork",
		"- TC/PC: precise evidence vs low operational complexity",
		"- IFR: exact skill sections self-identify during AI use, then reviewers judge only those exact sections",
		"- Principles used: #2 Taking Out, #10 Preliminary Action, #23 Feedback, #24 Mediator, #26 Copying",
	]
	if semantic_enabled:
		lines.insert(15, "- `CONTENT_ACCURACY`: optional AI review found likely wrong, stale, or contradictory lines in disputed sections")
		lines.insert(21, "- See `skills-semantic.md` for optional AI content-level analysis and line-targeted fixes")
	if trace_count == 0:
		lines.extend(["", "## Honesty note", "", "- No explicit AI traces were found in the analyzed PR window. Structural checks still ran, but monthly content scoring is limited until agents emit traces."])
	if semantic_findings_count:
		lines.extend(["", "## Top content-level findings (Copilot)", ""])
		for item in semantic.get("content_findings", [])[:5]:
			file_path = item.get("file", "unknown")
			skill = item.get("skill", "unknown")
			issue = item.get("issue_type", "CONTENT_ACCURACY")
			evidence = item.get("evidence", "").strip()
			span = ""
			if item.get("line_start"):
				span = f":{item.get('line_start')}"
				if item.get("line_end") and item.get("line_end") != item.get("line_start"):
					span += f"-{item.get('line_end')}"
			lines.append(f"- `{skill}` **{issue}** in `{file_path}{span}` — {evidence}")
	write_text(output_dir / "skills-health-summary.md", "\n".join(lines) + "\n")
	return findings_count, proposal_count


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Skills health automation toolkit")
	sub = parser.add_subparsers(dest="command", required=True)

	audit = sub.add_parser("audit")
	audit.add_argument("--repo-root", required=True)
	audit.add_argument("--output-dir", required=True)
	audit.add_argument("--apply-autofix", action="store_true")

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
		findings = audit_skills(Path(args.repo_root), Path(args.output_dir), args.apply_autofix)
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
