from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .health import iter_skill_files

_DEFAULT_TRACKED_DEPS: list[dict[str, str]] = [
	{"alias": "TCA (swift-composable-architecture)", "repo": "pointfreeco/swift-composable-architecture"},
	{"alias": "FactoryKit", "repo": "hmlongco/Factory"},
	{"alias": "Tuist", "repo": "tuist/tuist"},
	{"alias": "swift-navigation", "repo": "pointfreeco/swift-navigation"},
	{"alias": "Kotlin", "repo": "JetBrains/kotlin"},
	{"alias": "Compose Multiplatform", "repo": "JetBrains/compose-multiplatform"},
	{"alias": "Circuit", "repo": "slackhq/circuit"},
	{"alias": "kotlin-inject", "repo": "evant/kotlin-inject"},
]

_RELEASE_NOTES_KEYWORD_RE = re.compile(
	r"(?i)(breaking|deprecated|removed|migration|api change|incompatible|renamed|requires)",
)
_ALLOWED_SKILL_PATH_RE = re.compile(r"^\.github/skills/[a-z0-9\-]+/SKILL\.md$")

_RELEASE_NOTES_MAX_LINES = 30
_SKILL_CONTENT_MAX_CHARS = 6000
_MAX_PATCHES_PER_SKILL = 6

_GITHUB_API = "https://api.github.com"
_GITHUB_MODELS_URL = "https://models.inference.ai.azure.com/chat/completions"


def _gh_get(path: str, token: str) -> dict[str, Any] | list[Any]:
	req = urllib.request.Request(f"{_GITHUB_API}/{path}")
	req.add_header("Authorization", f"Bearer {token}")
	req.add_header("Accept", "application/vnd.github+json")
	req.add_header("X-GitHub-Api-Version", "2022-11-28")
	try:
		with urllib.request.urlopen(req, timeout=15) as resp:
			return json.loads(resp.read())
	except urllib.error.HTTPError as exc:
		return {"_error": str(exc), "_status": exc.code}
	except Exception as exc:
		return {"_error": str(exc)}


def _curated_release_notes(body: str) -> str:
	"""Extract lines mentioning breaking/deprecated/removed/migration keywords.
	Falls back to the first few non-empty lines when no keyword lines are found."""
	if not body:
		return ""
	lines = body.splitlines()
	relevant = [ln.strip() for ln in lines if ln.strip() and _RELEASE_NOTES_KEYWORD_RE.search(ln)]
	if not relevant:
		relevant = [ln.strip() for ln in lines if ln.strip()][:10]
	return "\n".join(relevant[:_RELEASE_NOTES_MAX_LINES])


def fetch_dep_info(dep: dict[str, str], token: str) -> dict[str, Any]:
	"""Fetch latest release version and curated release notes for a tracked dependency."""
	data = _gh_get(f"repos/{dep['repo']}/releases/latest", token)
	if isinstance(data, dict) and not data.get("_error") and data.get("tag_name"):
		return {
			"alias": dep["alias"],
			"repo": dep["repo"],
			"version": data["tag_name"],
			"published_at": (data.get("published_at") or "")[:10],
			"url": data.get("html_url", ""),
			"notes": _curated_release_notes(data.get("body") or ""),
			"error": "",
		}
	# Fall back to tags list for repos that use tags but not formal releases.
	tags = _gh_get(f"repos/{dep['repo']}/tags", token)
	version = "unknown"
	if isinstance(tags, list) and tags:
		version = tags[0].get("name", "unknown")
	error_msg = data.get("_error", "") if isinstance(data, dict) else ""
	return {
		"alias": dep["alias"],
		"repo": dep["repo"],
		"version": version,
		"published_at": "",
		"url": "",
		"notes": "",
		"error": str(error_msg),
	}


def _versions_context(dep_infos: list[dict[str, Any]]) -> str:
	parts = ["## Library Versions (fetched live from GitHub releases)"]
	for dep in dep_infos:
		if dep.get("error"):
			parts.append(f"\n- {dep['alias']}: version lookup failed — {dep['error']}")
			continue
		header = f"\n### {dep['alias']} — {dep['version']}"
		if dep.get("published_at"):
			header += f" (released {dep['published_at']})"
		if dep.get("url"):
			header += f"\n  {dep['url']}"
		parts.append(header)
		if dep.get("notes"):
			parts.append("Key changes:\n" + dep["notes"])
	return "\n".join(parts)


def _system_prompt() -> str:
	return (
		"You are a technical writer maintaining AI skill guidance files for a software development team. "
		"Your role: review a single skill file and propose EXACT inline text patches where the content "
		"references outdated version numbers, deprecated APIs, removed patterns, or misses critical new "
		"best practices introduced in the listed library versions.\n\n"
		"Strict rules:\n"
		f"- Maximum {_MAX_PATCHES_PER_SKILL} patches.\n"
		"- old_text MUST appear EXACTLY ONCE in the file — verbatim, character-for-character including "
		"  whitespace and punctuation. If you cannot guarantee uniqueness, omit the patch.\n"
		"- new_text must preserve all surrounding markdown formatting (headings, code fences, bullets).\n"
		"- Be conservative: only propose changes you are confident about given the library release notes. "
		"  Never invent changes. Never change examples unless you can cite a concrete version change.\n"
		"- Return STRICT JSON only — no markdown fences, no text outside the JSON object.\n"
		"- If no patches are needed, return {\"skill\": \"...\", \"patches\": [], "
		"\"summary\": \"Up to date.\"}."
	)


def _user_prompt(
	skill_name: str,
	file_rel: str,
	skill_content: str,
	versions_ctx: str,
) -> str:
	schema = (
		"{\n"
		f'  "skill": "{skill_name}",\n'
		'  "file": "' + file_rel + '",\n'
		'  "patches": [\n'
		"    {\n"
		'      "old_text": "<exact verbatim string — unique in file>",\n'
		'      "new_text": "<replacement string>",\n'
		'      "reason": "<cite the library version and specific change>"\n'
		"    }\n"
		"  ],\n"
		'  "summary": "<one-sentence summary or \\"Up to date.\\""\n'
		"}"
	)
	truncated = skill_content[:_SKILL_CONTENT_MAX_CHARS]
	if len(skill_content) > _SKILL_CONTENT_MAX_CHARS:
		truncated += "\n... [truncated]"
	return (
		f"{versions_ctx}\n\n"
		f"## Skill File: `{skill_name}`\n"
		f"Path: `{file_rel}`\n\n"
		f"```markdown\n{truncated}\n```\n\n"
		"## Task\n"
		"Review the skill file above. Identify any text that is outdated based on the library versions "
		"listed. Produce exact inline patches. Remember: old_text must appear EXACTLY ONCE verbatim.\n\n"
		f"Return JSON matching this schema:\n{schema}"
	)


def call_github_models(messages: list[dict[str, str]], token: str, model: str) -> str:
	payload = json.dumps({
		"model": model,
		"messages": messages,
		"response_format": {"type": "json_object"},
		"max_tokens": 4096,
		"temperature": 0.1,
	}).encode()
	req = urllib.request.Request(_GITHUB_MODELS_URL, data=payload, method="POST")
	req.add_header("Authorization", f"Bearer {token}")
	req.add_header("Content-Type", "application/json")
	try:
		with urllib.request.urlopen(req, timeout=90) as resp:
			result = json.loads(resp.read())
		return result["choices"][0]["message"]["content"]
	except urllib.error.HTTPError as exc:
		body = exc.read().decode(errors="replace")[:300]
		return json.dumps({"skill": "", "patches": [], "summary": f"API error {exc.code}: {body}"})
	except Exception as exc:
		return json.dumps({"skill": "", "patches": [], "summary": f"Error: {exc}"})


def apply_patches(
	patches: list[dict[str, str]],
	file_path: Path,
	file_rel: str,
	original_content: str,
) -> tuple[list[dict[str, str]], int, int, int]:
	"""Apply patches to a skill file with exact-match enforcement.

	Returns (annotated_patches, applied, skipped, ambiguous).
	Rejects any file path that does not match the expected skill path pattern.
	"""
	annotated: list[dict[str, str]] = []
	if not file_path.exists() or not _ALLOWED_SKILL_PATH_RE.match(file_rel):
		for patch in patches:
			annotated.append({**patch, "_status": "skipped_path_rejected"})
		return annotated, 0, len(patches), 0

	content = original_content
	applied = skipped = ambiguous = 0

	for patch in patches:
		old_text = patch.get("old_text", "")
		new_text = patch.get("new_text", "")
		if not old_text:
			annotated.append({**patch, "_status": "skipped_empty"})
			skipped += 1
			continue
		count = content.count(old_text)
		if count == 0:
			annotated.append({**patch, "_status": "skipped_not_found"})
			skipped += 1
		elif count == 1:
			content = content.replace(old_text, new_text, 1)
			annotated.append({**patch, "_status": "applied"})
			applied += 1
		else:
			annotated.append({**patch, "_status": "skipped_ambiguous"})
			ambiguous += 1

	if applied > 0:
		file_path.write_text(content, encoding="utf-8")

	return annotated, applied, skipped, ambiguous


def write_report(
	output_dir: Path,
	skills_data: list[dict[str, Any]],
	dep_infos: list[dict[str, Any]],
	total_applied: int,
	total_skipped: int,
	total_ambiguous: int,
) -> None:
	output_dir.mkdir(parents=True, exist_ok=True)
	skills_changed = sum(1 for s in skills_data if s.get("applied", 0) > 0)
	report = {
		"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
		"library_versions": dep_infos,
		"skills_processed": len(skills_data),
		"skills_changed": skills_changed,
		"total_patches_applied": total_applied,
		"total_patches_skipped": total_skipped,
		"total_patches_ambiguous": total_ambiguous,
		"by_skill": skills_data,
	}
	(output_dir / "skills-ai-updates.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

	lines = [
		"# Skills AI Update Report",
		f"Generated: {dt.datetime.now(dt.timezone.utc).isoformat()}",
		"",
		f"- Skills processed: {len(skills_data)}",
		f"- Skills updated: **{skills_changed}**",
		f"- Patches applied: **{total_applied}** / skipped (not found): {total_skipped} / ambiguous (skipped): {total_ambiguous}",
		"",
		"## Library Versions Checked",
		"",
	]
	for dep in dep_infos:
		if dep.get("error"):
			lines.append(f"- **{dep['alias']}**: ⚠️ fetch failed — {dep['error']}")
		else:
			ver = dep.get("version", "unknown")
			date = f" ({dep['published_at']})" if dep.get("published_at") else ""
			lines.append(f"- **{dep['alias']}**: {ver}{date}")

	lines.append("")
	if skills_changed == 0:
		lines.append("## Result\n\nAll reviewed skills are up to date — no patches applied.")
	else:
		lines.append("## Patches Applied")
		for entry in skills_data:
			if entry.get("applied", 0) == 0:
				continue
			lines.append(f"\n### `{entry['skill']}`")
			if entry.get("summary"):
				lines.append(entry["summary"])
			for p in entry.get("patches", []):
				if p.get("_status") == "applied":
					lines.append(f"- ✅ {p.get('reason', '')}")

	(output_dir / "skills-ai-updates.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(
		description="AI-powered skill updater: fetch latest library versions and apply inline patches.",
	)
	parser.add_argument("--repo-root", required=True)
	parser.add_argument("--output-dir", required=True)
	parser.add_argument("--token", default="", help="GitHub token (also read from GH_TOKEN / GITHUB_TOKEN)")
	parser.add_argument("--tracked-deps", default="", help="JSON array of {alias, repo} objects")
	parser.add_argument("--model", default="gpt-4o-mini", help="GitHub Models model ID")
	parser.add_argument("--max-skills", type=int, default=15, help="Max skill files to process per run")
	args = parser.parse_args(argv)

	repo_root = Path(args.repo_root).resolve()
	output_dir = Path(args.output_dir).resolve()
	token = args.token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")

	if not token:
		print("ai_update_status=skipped_no_token")
		write_report(output_dir, [], [], 0, 0, 0)
		return 0

	tracked_deps = json.loads(args.tracked_deps) if args.tracked_deps else _DEFAULT_TRACKED_DEPS

	# 1. Fetch library versions from GitHub releases API.
	dep_infos = [fetch_dep_info(dep, token) for dep in tracked_deps]
	versions_ctx = _versions_context(dep_infos)

	# 2. Load proposals from the feedback analysis step as additional context.
	feedback_path = output_dir / "skills-feedback.json"
	if feedback_path.exists():
		feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
		proposals = feedback.get("proposals", [])
		if proposals:
			versions_ctx += f"\n\n## PR Feedback Proposals (additional context)\n{json.dumps(proposals[:8], indent=2)}"

	# 3. Process each skill file with one GitHub Models call per skill.
	skill_files = iter_skill_files(repo_root)[: args.max_skills]
	if not skill_files:
		print("ai_update_status=no_skills")
		write_report(output_dir, [], dep_infos, 0, 0, 0)
		return 0

	system = _system_prompt()
	skills_data: list[dict[str, Any]] = []
	total_applied = total_skipped = total_ambiguous = 0

	for skill_path in skill_files:
		skill_name = skill_path.parent.name
		file_rel = str(skill_path.relative_to(repo_root))
		skill_content = skill_path.read_text(encoding="utf-8")

		messages = [
			{"role": "system", "content": system},
			{"role": "user", "content": _user_prompt(skill_name, file_rel, skill_content, versions_ctx)},
		]
		raw = call_github_models(messages, token, args.model)
		try:
			data = json.loads(raw)
		except Exception:
			skills_data.append({
				"skill": skill_name,
				"file": file_rel,
				"patches": [],
				"applied": 0,
				"skipped": 0,
				"ambiguous": 0,
				"summary": "",
				"error": f"JSON parse error: {raw[:200]}",
			})
			continue

		patches_raw = data.get("patches", [])
		annotated, applied, skipped, ambiguous = apply_patches(patches_raw, skill_path, file_rel, skill_content)
		total_applied += applied
		total_skipped += skipped
		total_ambiguous += ambiguous

		skills_data.append({
			"skill": skill_name,
			"file": file_rel,
			"summary": data.get("summary", ""),
			"patches": annotated,
			"applied": applied,
			"skipped": skipped,
			"ambiguous": ambiguous,
		})

	# 4. Write JSON and markdown reports.
	write_report(output_dir, skills_data, dep_infos, total_applied, total_skipped, total_ambiguous)

	skills_changed = sum(1 for s in skills_data if s.get("applied", 0) > 0)
	print(f"ai_update_patches={total_applied}")
	print(f"ai_update_skills_changed={skills_changed}")
	print(f"ai_update_ambiguous={total_ambiguous}")
	print("ai_update_status=ok")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
