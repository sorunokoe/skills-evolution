from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any) -> Any:
	if not path.exists():
		return default
	return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
	path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def top_disputed_sections(output_dir: Path, limit: int = 8) -> list[dict[str, Any]]:
	feedback = read_json(output_dir / "skills-feedback.json", {"disputed_sections": []})
	sections = feedback.get("disputed_sections", [])
	return sorted(
		sections,
		key=lambda item: (-(item.get("fp", 0) + item.get("fix_needed", 0)), -item.get("usage", 0), item.get("skill", "")),
	)[:limit]


def extract_excerpt(path: Path, line_start: int, line_end: int, padding: int = 6, *, _lines: list[str] | None = None) -> str:
	file_lines = _lines if _lines is not None else path.read_text(encoding="utf-8").splitlines()
	start = max(1, line_start - padding)
	end = min(len(file_lines), line_end + padding)
	return "\n".join(f"{idx:>5}\t{file_lines[idx - 1]}" for idx in range(start, end + 1))


def build_context(repo_root: Path, output_dir: Path, sections: list[dict[str, Any]]) -> Path:
	context_path = output_dir / "skills-semantic-context.txt"
	lines = ["## Disputed traced sections", json.dumps(sections, indent=2), "", "## Source excerpts"]
	file_cache: dict[Path, list[str]] = {}
	for section in sections:
		path = repo_root / section["file"]
		if not path.exists():
			continue
		if path not in file_cache:
			file_cache[path] = path.read_text(encoding="utf-8").splitlines()
		lines.extend(
			[
				"",
				f"### {section['skill']} :: {section['section_id']} :: {section['file']}:{section['line_start']}-{section['line_end']}",
				extract_excerpt(path, section["line_start"], section["line_end"], _lines=file_cache[path]),
			]
		)
	context_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
	return context_path


def parse_json_from_text(raw: str) -> dict[str, Any]:
	match = re.search(r"\{[\s\S]*\}\s*$", raw.strip())
	if not match:
		return {"overall_assessment": "", "content_findings": [], "proposals": [], "note": "Could not parse JSON from Copilot output."}
	try:
		return json.loads(match.group(0))
	except Exception:
		return {"overall_assessment": "", "content_findings": [], "proposals": [], "note": "Invalid JSON from Copilot output."}


def run_copilot(prompt: str, token: str) -> str:
	env = os.environ.copy()
	env["COPILOT_GITHUB_TOKEN"] = token
	proc = subprocess.run(
		["copilot", "-p", prompt, "--no-ask-user"],
		capture_output=True,
		text=True,
		env=env,
		check=False,
	)
	if proc.returncode != 0 and not proc.stdout.strip():
		return ""
	return proc.stdout.strip()


def write_semantic_md(output_dir: Path, data: dict[str, Any]) -> None:
	lines = ["# Skills Semantic Report", ""]
	overall = data.get("overall_assessment")
	if overall:
		lines.extend([f"- Overall: {overall}", ""])
	lines.append(f"- Content findings: {len(data.get('content_findings', []))}")
	lines.append(f"- Proposals: {len(data.get('proposals', []))}")
	lines.extend(["", "## Findings", ""])
	if not data.get("content_findings"):
		lines.append("- No content-level findings reported.")
	else:
		for item in data["content_findings"]:
			span = f":{item.get('line_start')}" if item.get("line_start") else ""
			if item.get("line_end") and item.get("line_end") != item.get("line_start"):
				span += f"-{item['line_end']}"
			lines.append(
				f"- `{item.get('skill', 'unknown')}` [{item.get('severity', 'medium')}] **{item.get('issue_type', 'CONTENT_ACCURACY')}** "
				f"in `{item.get('file', 'unknown')}{span}` — {item.get('evidence', '')}"
			)
			if item.get("fix_recommendation"):
				lines.append(f"  - Fix: {item['fix_recommendation']}")
	lines.extend(["", "## Proposals", ""])
	if not data.get("proposals"):
		lines.append("- No proposals.")
	else:
		for proposal in data["proposals"]:
			lines.append(
				f"- `{proposal.get('skill', 'unknown')}` {proposal.get('change_type', 'EDIT_LINE')}: "
				f"{proposal.get('proposed_patch_summary', '')}"
			)
	(output_dir / "skills-semantic.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Run Copilot semantic pass only on disputed traced sections.")
	parser.add_argument("--repo-root", required=True)
	parser.add_argument("--output-dir", required=True)
	parser.add_argument("--copilot-token", default="")
	args = parser.parse_args(argv)

	repo_root = Path(args.repo_root).resolve()
	output_dir = Path(args.output_dir).resolve()
	output_dir.mkdir(parents=True, exist_ok=True)

	sections = top_disputed_sections(output_dir)
	if not args.copilot_token:
		data = {
			"generated_at": "",
			"overall_assessment": "",
			"content_findings": [],
			"proposals": [],
			"note": "COPILOT_TOKEN not configured; semantic pass skipped.",
		}
		write_json(output_dir / "skills-semantic.json", data)
		write_semantic_md(output_dir, data)
		print("semantic_status=skipped")
		return 0

	if not sections:
		data = {
			"generated_at": "",
			"overall_assessment": "No disputed traced sections to review.",
			"content_findings": [],
			"proposals": [],
			"note": "No disputed traced sections were available for focused semantic analysis.",
		}
		write_json(output_dir / "skills-semantic.json", data)
		write_semantic_md(output_dir, data)
		print("semantic_status=no_disputes")
		return 0

	context_path = build_context(repo_root, output_dir, sections)
	prompt = (
		"You are reviewing disputed sections in AI skill files.\n"
		"Use only the disputed section evidence and source excerpts below.\n"
		"Return STRICT JSON only with schema:\n"
		"{\"overall_assessment\":\"...\",\"content_findings\":[{\"skill\":\"...\",\"file\":\"...\",\"line_start\":1,"
		"\"line_end\":1,\"severity\":\"low|medium|high\",\"issue_type\":\"CONTENT_ACCURACY|CONTRADICTION|OVERLY_STRICT|STALE_GUIDANCE|MISSING_GUIDANCE\","
		"\"evidence\":\"...\",\"fix_recommendation\":\"...\"}],"
		"\"proposals\":[{\"skill\":\"...\",\"section_hint\":\"...\",\"change_type\":\"EDIT_LINE|REWRITE_SECTION|REMOVE_RULE|ADD_EXAMPLE\","
		"\"proposed_patch_summary\":\"...\",\"confidence\":0.0}]}\n"
		"Rules: max 20 findings, cite only exact disputed lines/sections, no markdown outside JSON.\n\n"
		+ context_path.read_text(encoding="utf-8")
	)

	raw = run_copilot(prompt, args.copilot_token)
	if not raw:
		data = {
			"generated_at": "",
			"overall_assessment": "",
			"content_findings": [],
			"proposals": [],
			"note": "Copilot returned empty output.",
		}
	else:
		data = parse_json_from_text(raw)

	write_json(output_dir / "skills-semantic.json", data)
	write_semantic_md(output_dir, data)
	(output_dir / "skills-semantic-raw.txt").write_text(raw or "", encoding="utf-8")
	print(f"semantic_findings={len(data.get('content_findings', []))}")
	print(f"semantic_proposals={len(data.get('proposals', []))}")
	print("semantic_status=ok")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
