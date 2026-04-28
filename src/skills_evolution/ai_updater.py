"""AI-powered skill updater.

Discovers library versions from common package manager files, compares with latest GitHub releases,
and applies conservative inline patches to skill files via GitHub Models API.
"""
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

from .health import iter_skill_files, iter_oss_skill_files

_ALLOWED_PATH = re.compile(r"^(\.github|\.claude)/skills/[a-z0-9\-]+/SKILL\.md$")
_ALLOWED_PATH_OSS = re.compile(r"^(SKILL\.md|references/[A-Za-z0-9._-]+\.md)$")
_KEYWORD_RE = re.compile(r"(?i)(breaking|deprecated|removed|migration|api change|incompatible|renamed)")
_GITHUB_API = "https://api.github.com"
_MODELS_URL = "https://models.inference.ai.azure.com/chat/completions"
_MAX_PATCHES = 6
_MAX_CONTENT = 6000


# --- Dependency discovery ---


def _extract_github_repo(url: str) -> tuple[str, str] | None:
	"""Return (owner/name, name) from a GitHub URL/ref, or None if not a GitHub URL."""
	if "github.com" not in url:
		return None
	slug = url.split("github.com/")[-1].removesuffix(".git").rstrip("/")
	parts = slug.split("/")
	return (f"{parts[0]}/{parts[1]}", parts[-1]) if len(parts) >= 2 else None


def _find_spm_deps(repo_root: Path) -> list[dict[str, str]]:
	"""Package.resolved format (v2 and v3)."""
	deps = []
	for resolved in sorted(repo_root.rglob("Package.resolved")):
		if ".build" in resolved.parts:
			continue
		try:
			data = json.loads(resolved.read_text(encoding="utf-8"))
			pins = data.get("pins") or data.get("object", {}).get("pins", [])
			for pin in pins:
				url = (pin.get("location") or pin.get("repositoryURL", "")).rstrip("/")
				result = _extract_github_repo(url)
				if result:
					repo, name = result
					deps.append({"alias": pin.get("identity") or name, "repo": repo, "pinned": (pin.get("state") or {}).get("version", "")})
		except Exception:
			continue
	return deps


def _find_go_deps(repo_root: Path) -> list[dict[str, str]]:
	"""Go modules: go.mod — module paths starting with github.com are GitHub repos."""
	deps = []
	_require_re = re.compile(r"^\s+(github\.com/[\w.\-]+/[\w.\-]+)\s+v([\w.\-]+)", re.MULTILINE)
	for gomod in sorted(repo_root.rglob("go.mod")):
		if ".build" in gomod.parts or "vendor" in gomod.parts:
			continue
		try:
			for match in _require_re.finditer(gomod.read_text(encoding="utf-8")):
				module, version = match.group(1), match.group(2)
				parts = module.split("/")
				deps.append({"alias": parts[-1], "repo": f"{parts[1]}/{parts[2]}", "pinned": f"v{version}"})
		except Exception:
			continue
	return deps


def _find_cargo_deps(repo_root: Path) -> list[dict[str, str]]:
	"""Rust/Cargo: Cargo.lock — only git-sourced deps have GitHub URLs."""
	deps = []
	_source_re = re.compile(r'source\s*=\s*"git\+(https://github\.com/[^"?#]+)')
	_name_re = re.compile(r'^name\s*=\s*"([^"]+)"', re.MULTILINE)
	_ver_re = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)
	for lock in sorted(repo_root.rglob("Cargo.lock")):
		if "target" in lock.parts:
			continue
		try:
			content = lock.read_text(encoding="utf-8")
			for block in content.split("[[package]]"):
				src = _source_re.search(block)
				if not src:
					continue
				result = _extract_github_repo(src.group(1))
				if not result:
					continue
				repo, _ = result
				name = (_name_re.search(block) or ["", ""])[1] if _name_re.search(block) else ""
				ver = (_ver_re.search(block) or ["", ""])[1] if _ver_re.search(block) else ""
				nm = _name_re.search(block)
				vr = _ver_re.search(block)
				deps.append({"alias": nm.group(1) if nm else repo.split("/")[-1], "repo": repo, "pinned": vr.group(1) if vr else ""})
		except Exception:
			continue
	return deps


def _find_pubspec_deps(repo_root: Path) -> list[dict[str, str]]:
	"""pubspec.yaml: git dependencies with GitHub URLs."""
	deps = []
	_git_url_re = re.compile(r"url:\s*(https://github\.com/[^\s]+)")
	_ref_re = re.compile(r"ref:\s*([^\s]+)")
	_name_re = re.compile(r"^(\w[\w_-]*):\s*$", re.MULTILINE)
	for pubspec in sorted(repo_root.rglob("pubspec.yaml")):
		try:
			content = pubspec.read_text(encoding="utf-8")
			for url_match in _git_url_re.finditer(content):
				result = _extract_github_repo(url_match.group(1))
				if not result:
					continue
				repo, name = result
				before = content[: url_match.start()]
				ref_match = _ref_re.search(content, url_match.end(), url_match.end() + 100)
				deps.append({"alias": name, "repo": repo, "pinned": ref_match.group(1) if ref_match else ""})
		except Exception:
			continue
	return deps


def _find_npm_deps(repo_root: Path) -> list[dict[str, str]]:
	"""npm: package.json — dependencies with 'github:owner/repo' or full GitHub URL."""
	deps = []
	_github_ref_re = re.compile(r"github:([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)(?:#(.+))?")
	for pkgjson in sorted(repo_root.rglob("package.json")):
		if "node_modules" in pkgjson.parts:
			continue
		try:
			data = json.loads(pkgjson.read_text(encoding="utf-8"))
			all_deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
			for name, spec in all_deps.items():
				m = _github_ref_re.match(str(spec))
				if m:
					repo = m.group(1)
					ref = m.group(2) or ""
					deps.append({"alias": name, "repo": repo, "pinned": ref})
		except Exception:
			continue
	return deps


def discover_deps(repo_root: Path) -> list[dict[str, str]]:
	"""Discover GitHub-hosted dependencies from all supported package manager files.

	Deduplicates by GitHub repo slug — first occurrence wins.
	"""
	seen: set[str] = set()
	result: list[dict[str, str]] = []
	for dep in (
		_find_spm_deps(repo_root)
		+ _find_go_deps(repo_root)
		+ _find_cargo_deps(repo_root)
		+ _find_pubspec_deps(repo_root)
		+ _find_npm_deps(repo_root)
	):
		if dep["repo"] not in seen:
			seen.add(dep["repo"])
			result.append(dep)
	return result


# --- GitHub API helpers ---


def _gh_get(path: str, token: str) -> Any:
	req = urllib.request.Request(f"{_GITHUB_API}/{path}")
	req.add_header("Authorization", f"Bearer {token}")
	req.add_header("Accept", "application/vnd.github+json")
	req.add_header("X-GitHub-Api-Version", "2022-11-28")
	try:
		with urllib.request.urlopen(req, timeout=15) as r:
			return json.loads(r.read())
	except Exception as exc:
		return {"_error": str(exc)}


def _latest_tag(repo: str, token: str) -> str:
	data = _gh_get(f"repos/{repo}/releases/latest", token)
	if isinstance(data, dict) and data.get("tag_name"):
		return data["tag_name"]
	tags = _gh_get(f"repos/{repo}/tags", token)
	return tags[0]["name"] if isinstance(tags, list) and tags else "unknown"


def _key_release_notes(repo: str, token: str) -> str:
	data = _gh_get(f"repos/{repo}/releases/latest", token)
	body = data.get("body", "") if isinstance(data, dict) else ""
	key_lines = [ln.strip() for ln in body.splitlines() if ln.strip() and _KEYWORD_RE.search(ln)]
	if key_lines:
		return "\n".join(key_lines[:20])
	return "\n".join(ln.strip() for ln in body.splitlines() if ln.strip())[:400]


def build_versions_context(deps: list[dict[str, str]], token: str) -> str:
	"""Build a markdown block comparing pinned vs latest version for each dependency."""
	lines = ["## Project dependencies (Package.resolved) vs latest GitHub releases"]
	for dep in deps:
		latest = _latest_tag(dep["repo"], token)
		notes = _key_release_notes(dep["repo"], token)
		pinned = dep.get("pinned", "")
		status = f"pinned {pinned} → latest {latest}" if pinned else f"latest {latest}"
		lines.append(f"\n### {dep['alias']} ({dep['repo']}) — {status}")
		if notes:
			lines.append(notes)
	return "\n".join(lines)


# --- GitHub Models API ---


def ask_ai_for_patches(skill_name: str, file_rel: str, content: str, versions: str, token: str, model: str) -> dict[str, Any]:
	"""Ask GitHub Models to review one skill file and return proposed patches."""
	system = (
		"You are a technical writer maintaining AI skill guidance files for a software team. "
		"Review the skill file and propose EXACT inline patches where content references outdated "
		"versions, deprecated APIs, or removed patterns based on the library versions provided. "
		f"Rules: max {_MAX_PATCHES} patches; old_text must appear EXACTLY ONCE verbatim in the file; "
		"preserve markdown formatting in new_text; be conservative — only propose changes you can "
		"cite from the version info. "
		'Return JSON only: {"patches": [{"old_text": "...", "new_text": "...", "reason": "..."}], "summary": "..."}'
	)
	truncated = content[:_MAX_CONTENT] + ("\n...[truncated]" if len(content) > _MAX_CONTENT else "")
	user = f"{versions}\n\n## Skill: `{skill_name}` — `{file_rel}`\n\n```markdown\n{truncated}\n```"
	payload = json.dumps({
		"model": model,
		"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
		"response_format": {"type": "json_object"},
		"max_tokens": 4096,
		"temperature": 0.1,
	}).encode()
	req = urllib.request.Request(_MODELS_URL, data=payload, method="POST")
	req.add_header("Authorization", f"Bearer {token}")
	req.add_header("Content-Type", "application/json")
	try:
		with urllib.request.urlopen(req, timeout=90) as r:
			return json.loads(json.loads(r.read())["choices"][0]["message"]["content"])
	except Exception as exc:
		return {"patches": [], "summary": f"Error: {exc}"}


# --- Patch application ---


def _extract_oss_skill_name(repo_root: Path) -> str:
	"""Extract skill name from root SKILL.md frontmatter, falling back to directory name.

	OSS skill repos must not rely on the checkout directory name for their identity —
	CI can check out to "workspace" or any arbitrary name.
	"""
	skill_file = repo_root / "SKILL.md"
	if not skill_file.exists():
		return repo_root.name
	try:
		content = skill_file.read_text(encoding="utf-8")
	except OSError:
		return repo_root.name
	match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
	if not match:
		return repo_root.name
	for line in match.group(1).splitlines():
		if ":" in line:
			key, val = line.split(":", 1)
			if key.strip() == "name":
				return val.strip().strip("'\"")
	return repo_root.name


def _iter_oss_skill_files(repo_root: Path) -> list[Path]:
	"""Discover SKILL.md and references/*.md for an OSS skill repo AI update pass."""
	files: list[Path] = []
	root_skill = repo_root / "SKILL.md"
	if not root_skill.exists():
		return files
	files.append(root_skill)
	refs_dir = repo_root / "references"
	if refs_dir.is_dir():
		files.extend(sorted(refs_dir.glob("*.md")))
	return files


def apply_patches(
	patches: list[dict[str, str]],
	file_path: Path,
	file_rel: str,
	original: str,
	*,
	allowed_path_re: re.Pattern[str] = _ALLOWED_PATH,
) -> tuple[int, int, int]:
	"""Apply patches with exact-match enforcement. Returns (applied, skipped, ambiguous).

	Rejects writes to any path outside the allowed skill directories.
	Skips patches where old_text is not found or appears more than once.
	"""
	if not allowed_path_re.match(file_rel):
		for p in patches:
			p["_status"] = "rejected_path"
		return 0, len(patches), 0

	content, applied, skipped, ambiguous = original, 0, 0, 0
	for p in patches:
		old, new = p.get("old_text", ""), p.get("new_text", "")
		if not old:
			p["_status"] = "skipped_empty"
			skipped += 1
		elif content.count(old) == 1:
			content = content.replace(old, new, 1)
			p["_status"] = "applied"
			applied += 1
		elif content.count(old) == 0:
			p["_status"] = "not_found"
			skipped += 1
		else:
			p["_status"] = "ambiguous"
			ambiguous += 1

	if applied:
		file_path.write_text(content, encoding="utf-8")
	return applied, skipped, ambiguous


# --- Report ---


def write_report(output_dir: Path, report: dict[str, Any], deps: list[dict[str, str]]) -> None:
	output_dir.mkdir(parents=True, exist_ok=True)
	(output_dir / "skills-ai-updates.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

	lines = [
		"# Skills AI Update Report",
		f"Generated: {dt.datetime.now(dt.timezone.utc).isoformat()}",
		"",
		f"- Skills updated: **{report['skills_changed']}**",
		f"- Patches: applied **{report['total_patches_applied']}** / skipped {report['total_patches_skipped']} / ambiguous {report['total_patches_ambiguous']}",
		"",
	]
	if deps:
		lines += ["## Dependencies discovered", ""]
		for dep in deps[:20]:
			lines.append(f"- **{dep.get('alias', '?')}** (`{dep.get('repo', '?')}`): pinned {dep.get('pinned', '?')}")
		lines.append("")

	if report["skills_changed"] > 0:
		lines.append("## Patches applied")
		for entry in report["by_skill"]:
			if not entry.get("applied"):
				continue
			lines.append(f"\n### `{entry['skill']}` — {entry.get('summary', '')}")
			for p in entry.get("patches", []):
				if p.get("_status") == "applied":
					lines.append(f"- ✅ {p.get('reason', '')}")
	else:
		lines.append("All reviewed skills are up to date.")

	(output_dir / "skills-ai-updates.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- Entry point ---


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Update skill files using GitHub Models and live dependency versions.")
	parser.add_argument("--repo-root", required=True)
	parser.add_argument("--output-dir", required=True)
	parser.add_argument("--token", default="", help="GitHub token (or GH_TOKEN / GITHUB_TOKEN env)")
	parser.add_argument("--tracked-deps", default="", help="JSON array [{alias, repo}] — overrides auto-discovery")
	parser.add_argument("--model", default="gpt-4o-mini", help="GitHub Models model ID")
	parser.add_argument("--max-skills", type=int, default=15, help="Max skill files to process per run")
	parser.add_argument(
		"--oss",
		action="store_true",
		help="OSS skill repo mode: look for SKILL.md at root and references/*.md.",
	)
	args = parser.parse_args(argv)

	repo_root = Path(args.repo_root).resolve()
	output_dir = Path(args.output_dir).resolve()
	token = args.token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")

	if not token:
		print("ai_update_status=skipped_no_token")
		return 0

	# 1. Discover dependencies (Package.resolved, or explicit override).
	deps = json.loads(args.tracked_deps) if args.tracked_deps else discover_deps(repo_root)
	versions_ctx = (
		build_versions_context(deps, token)
		if deps
		else "No Package.resolved found — review for general best practices only."
	)

	# 2. Load proposals from the PR feedback step as additional context.
	feedback_path = output_dir / "skills-feedback.json"
	if feedback_path.exists():
		proposals = json.loads(feedback_path.read_text(encoding="utf-8")).get("proposals", [])
		if proposals:
			versions_ctx += f"\n\n## PR feedback proposals (for context)\n{json.dumps(proposals[:8], indent=2)}"

	# 3. Discover skill files and resolve mode-specific settings.
	if args.oss:
		skill_files = _iter_oss_skill_files(repo_root)[: args.max_skills]
		oss_skill_name = _extract_oss_skill_name(repo_root)
		allowed_re = _ALLOWED_PATH_OSS
		if not skill_files:
			print("ai_update_status=skipped_no_skill_md")
			return 0
	else:
		skill_files = iter_skill_files(repo_root)[: args.max_skills]
		oss_skill_name = None
		allowed_re = _ALLOWED_PATH

	# 4. Process each skill file — one GitHub Models call per skill.
	results: list[dict[str, Any]] = []
	total_applied = total_skipped = total_ambiguous = 0

	for skill_path in skill_files:
		# Use .as_posix() in OSS mode so paths are always forward-slash (CI-safe).
		file_rel = skill_path.relative_to(repo_root).as_posix() if args.oss else str(skill_path.relative_to(repo_root))
		skill_name = oss_skill_name if args.oss else skill_path.parent.name
		content = skill_path.read_text(encoding="utf-8")
		data = ask_ai_for_patches(skill_name, file_rel, content, versions_ctx, token, args.model)
		applied, skipped, ambiguous = apply_patches(data.get("patches", []), skill_path, file_rel, content, allowed_path_re=allowed_re)
		total_applied += applied
		total_skipped += skipped
		total_ambiguous += ambiguous
		results.append({"skill": skill_name, "file": file_rel, **data, "applied": applied})

	# 5. Write reports.
	report = {
		"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
		"skills_changed": sum(1 for r in results if r.get("applied", 0) > 0),
		"total_patches_applied": total_applied,
		"total_patches_skipped": total_skipped,
		"total_patches_ambiguous": total_ambiguous,
		"by_skill": results,
	}
	write_report(output_dir, report, deps)

	print(f"ai_update_patches={total_applied}")
	print(f"ai_update_skills_changed={report['skills_changed']}")
	print("ai_update_status=ok")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
