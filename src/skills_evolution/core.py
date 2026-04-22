from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

TRACE_FILE = ".github/.skill-trace.ndjson"
AUTO_BEGIN = "<!-- skill-traces:auto:begin -->"
AUTO_END = "<!-- skill-traces:auto:end -->"
TRACE_BLOCK_RE = re.compile(
	re.escape(AUTO_BEGIN) + r"\n(.*?)\n" + re.escape(AUTO_END),
	re.DOTALL,
)


@dataclass(frozen=True)
class PreparedTraceUpdate:
	body: str
	traces: list[dict[str, Any]]
	total_traces: int
	trace_path: Path


@dataclass(frozen=True)
class PublishResult:
	source: str
	trace_count: int
	total_traces: int
	repo: str
	pr_number: int


def trace_file_path(repo_root: Path) -> Path:
	return (repo_root / TRACE_FILE).resolve()


def gh_request(method: str, url: str, token: str, payload: dict[str, Any] | None = None) -> Any:
	req = urllib.request.Request(url=url, method=method)
	req.add_header("Authorization", f"Bearer {token}")
	req.add_header("Accept", "application/vnd.github+json")
	if payload is not None:
		req.add_header("Content-Type", "application/json")
		data = json.dumps(payload).encode("utf-8")
	else:
		data = None
	with urllib.request.urlopen(req, data=data) as resp:
		return json.loads(resp.read().decode("utf-8"))


def gh_request_optional(method: str, url: str, token: str) -> Any | None:
	try:
		return gh_request(method, url, token)
	except HTTPError as error:
		if error.code == 404:
			return None
		raise


def git_output(repo_root: Path, *args: str) -> str | None:
	try:
		result = subprocess.run(
			["git", "-C", str(repo_root), *args],
			check=False,
			capture_output=True,
			text=True,
		)
	except FileNotFoundError:
		return None
	if result.returncode != 0:
		return None
	value = result.stdout.strip()
	return value or None


def gh_cli_output(*args: str, repo_root: Path | None = None) -> str | None:
	try:
		result = subprocess.run(
			["gh", *args],
			check=False,
			capture_output=True,
			text=True,
			cwd=str(repo_root) if repo_root else None,
		)
	except FileNotFoundError:
		return None
	if result.returncode != 0:
		return None
	value = result.stdout.strip()
	return value or None


def resolve_token(explicit_token: str | None, repo_root: Path | None = None) -> str | None:
	if explicit_token:
		return explicit_token
	for env_var in ("GH_TOKEN", "GITHUB_TOKEN"):
		token = os.getenv(env_var)
		if token:
			return token
	return gh_cli_output("auth", "token", repo_root=repo_root)


def detect_pr_context_with_gh(repo_root: Path) -> tuple[str, int] | None:
	raw = gh_cli_output("pr", "view", "--json", "number,url", repo_root=repo_root)
	if not raw:
		return None
	try:
		data = json.loads(raw)
	except json.JSONDecodeError:
		return None
	url = data.get("url") or ""
	match = re.match(r"^https://github\.com/(?P<repo>[^/]+/[^/]+)/pull/(?P<number>\d+)$", url)
	if not match:
		return None
	return match.group("repo"), int(match.group("number"))


def detect_repo(repo_root: Path) -> str | None:
	remote = git_output(repo_root, "remote", "get-url", "origin")
	if not remote:
		return None
	if remote.endswith(".git"):
		remote = remote[:-4]
	patterns = (
		r"^git@github\.com:(?P<repo>[^/]+/[^/]+)$",
		r"^https://github\.com/(?P<repo>[^/]+/[^/]+)$",
		r"^ssh://git@github\.com/(?P<repo>[^/]+/[^/]+)$",
	)
	for pattern in patterns:
		match = re.match(pattern, remote)
		if match:
			return match.group("repo")
	return None


def detect_branch(repo_root: Path) -> str | None:
	branch = git_output(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
	if branch == "HEAD":
		return None
	return branch


def detect_open_pr_number(repo: str, branch: str, token: str) -> int | None:
	owner, _ = repo.split("/", 1)
	head = urllib.parse.quote(f"{owner}:{branch}", safe="")
	url = f"https://api.github.com/repos/{repo}/pulls?state=open&head={head}&per_page=2"
	items = gh_request("GET", url, token)
	if not isinstance(items, list) or not items:
		return None
	if len(items) > 1:
		raise RuntimeError(f"Multiple open pull requests found for branch '{branch}'. Pass the PR number explicitly.")
	return int(items[0]["number"])


def validate_trace(trace: dict[str, Any]) -> bool:
	required = ("trace_id", "skill", "file", "section_id", "line_start", "reason")
	if any(field not in trace for field in required):
		return False
	if not re.fullmatch(r"[a-z0-9\-]+", str(trace["skill"])):
		return False
	try:
		int(trace["line_start"])
	except (TypeError, ValueError):
		return False
	return True


def normalize_trace(trace: dict[str, Any]) -> dict[str, Any]:
	record = dict(trace)
	record["trace_id"] = str(record["trace_id"])
	record["line_start"] = int(record["line_start"])
	record["line_end"] = int(record.get("line_end") or record["line_start"])
	return record


def parse_trace_lines(content: str) -> list[dict[str, Any]]:
	traces: list[dict[str, Any]] = []
	for raw_line in content.splitlines():
		line = raw_line.strip()
		if not line:
			continue
		try:
			record = json.loads(line)
		except json.JSONDecodeError:
			continue
		if not validate_trace(record):
			continue
		traces.append(normalize_trace(record))
	return traces


def build_block(traces: list[dict[str, Any]]) -> str:
	lines = [AUTO_BEGIN]
	for trace in traces:
		lines.append(json.dumps(trace, sort_keys=True))
	lines.append(AUTO_END)
	return "\n".join(lines)


def extract_trace_records(body: str) -> list[dict[str, Any]]:
	records: list[dict[str, Any]] = []
	for block in TRACE_BLOCK_RE.findall(body):
		records.extend(parse_trace_lines(block))
	return records


def merge_traces(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
	merged: dict[str, dict[str, Any]] = {}
	order: list[str] = []
	for trace in existing + incoming:
		trace_id = trace["trace_id"]
		if trace_id not in merged:
			order.append(trace_id)
		merged[trace_id] = trace
	return [merged[trace_id] for trace_id in order]


def replace_or_append_block(body: str, block: str) -> str:
	if AUTO_BEGIN in body and AUTO_END in body:
		return re.sub(
			f"{re.escape(AUTO_BEGIN)}.*?{re.escape(AUTO_END)}",
			block,
			body,
			flags=re.DOTALL,
		)
	if body and not body.endswith("\n"):
		body += "\n"
	if body.strip():
		return f"{body}\n{block}\n"
	return block + "\n"


def append_local_trace(
	repo_root: Path,
	skill: str,
	file: str,
	section_id: str,
	line_start: int,
	line_end: int | None,
	reason: str,
	confidence: float | None = None,
	trace_id: str | None = None,
) -> dict[str, Any]:
	record = {
		"trace_id": trace_id or uuid.uuid4().hex[:12],
		"skill": skill,
		"file": file,
		"section_id": section_id,
		"line_start": line_start,
		"line_end": line_end or line_start,
		"reason": reason,
	}
	if confidence is not None:
		record["confidence"] = confidence

	path = trace_file_path(repo_root.resolve())
	path.parent.mkdir(parents=True, exist_ok=True)
	with path.open("a", encoding="utf-8") as handle:
		handle.write(json.dumps(record, sort_keys=True) + "\n")
	return record


def load_local_trace_file(repo_root: Path) -> tuple[list[dict[str, Any]], Path]:
	path = trace_file_path(repo_root.resolve())
	if not path.exists():
		return [], path
	return parse_trace_lines(path.read_text(encoding="utf-8")), path


def clear_local_trace_file(path: Path) -> None:
	if path.exists():
		path.unlink()


def load_branch_trace_file(repo: str, sha: str, token: str) -> tuple[list[dict[str, Any]], str | None]:
	url = f"https://api.github.com/repos/{repo}/contents/{TRACE_FILE}?ref={sha}"
	data = gh_request_optional("GET", url, token)
	if not data:
		return [], None
	content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
	return parse_trace_lines(content), data.get("sha")


def cleanup_branch_trace_file(repo: str, branch: str, file_sha: str, token: str) -> None:
	url = f"https://api.github.com/repos/{repo}/contents/{TRACE_FILE}"
	payload = {
		"message": "chore: remove consumed skill trace file",
		"sha": file_sha,
		"branch": branch,
	}
	try:
		gh_request("DELETE", url, token, payload)
	except HTTPError:
		pass


def fetch_pr(repo: str, pr_number: int, token: str) -> dict[str, Any]:
	url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
	return gh_request("GET", url, token)


def patch_pr_body(repo: str, pr_number: int, token: str, body: str) -> None:
	url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
	gh_request("PATCH", url, token, {"body": body})


def merge_local_traces_into_body(repo_root: Path, body: str) -> PreparedTraceUpdate:
	existing = extract_trace_records(body)
	traces, path = load_local_trace_file(repo_root)
	if not traces:
		return PreparedTraceUpdate(body=body, traces=[], total_traces=len(existing), trace_path=path)
	merged = merge_traces(existing, traces)
	return PreparedTraceUpdate(
		body=replace_or_append_block(body, build_block(merged)),
		traces=traces,
		total_traces=len(merged),
		trace_path=path,
	)


def publish_local_traces(
	repo_root: Path,
	repo: str | None = None,
	pr_number: int | None = None,
	token: str | None = None,
	keep_local_file: bool = False,
) -> PublishResult:
	"""Preferred path: publish local scratch traces into an existing PR body."""
	resolved_token = resolve_token(token, repo_root=repo_root)
	if not resolved_token:
		raise RuntimeError("Provide a token, set GH_TOKEN/GITHUB_TOKEN, or authenticate with gh.")

	resolved_repo = repo
	resolved_pr_number = pr_number
	gh_context = detect_pr_context_with_gh(repo_root)
	if gh_context is not None:
		gh_repo, gh_pr_number = gh_context
		if resolved_repo is None:
			resolved_repo = gh_repo
		if resolved_pr_number is None:
			resolved_pr_number = gh_pr_number

	if not resolved_repo:
		resolved_repo = detect_repo(repo_root)
	if not resolved_repo:
		raise RuntimeError("Could not detect the GitHub repo. Pass it explicitly.")

	if resolved_pr_number is None:
		branch = detect_branch(repo_root)
		if not branch:
			raise RuntimeError("Could not detect the current branch. Pass the PR number explicitly.")
		resolved_pr_number = detect_open_pr_number(resolved_repo, branch, resolved_token)
		if resolved_pr_number is None:
			raise RuntimeError(
				"No open pull request found for the current branch. Create the PR first, "
				"or use inject-body before gh pr create."
			)

	pr = fetch_pr(resolved_repo, resolved_pr_number, resolved_token)
	body = pr.get("body") or ""
	prepared = merge_local_traces_into_body(repo_root, body)
	if prepared.body != body:
		patch_pr_body(resolved_repo, resolved_pr_number, resolved_token, prepared.body)
	if prepared.traces and not keep_local_file:
		try:
			clear_local_trace_file(prepared.trace_path)
		except OSError:
			pass

	return PublishResult(
		source="local_file",
		trace_count=len(prepared.traces),
		total_traces=prepared.total_traces,
		repo=resolved_repo,
		pr_number=resolved_pr_number,
	)


def publish_branch_traces(repo: str, pr_number: int, token: str) -> PublishResult:
	"""Fallback path: consume a committed branch trace file from GitHub Actions."""
	pr = fetch_pr(repo, pr_number, token)
	body = pr.get("body") or ""
	head = pr.get("head") or {}
	head_sha = head.get("sha") or ""
	head_ref = head.get("ref") or ""
	head_repo = ((head.get("repo") or {}).get("full_name")) or repo

	traces, file_sha = load_branch_trace_file(head_repo, head_sha, token) if head_sha else ([], None)
	existing = extract_trace_records(body)
	if traces:
		merged = merge_traces(existing, traces)
		new_body = replace_or_append_block(body, build_block(merged))
		if new_body != body:
			patch_pr_body(repo, pr_number, token, new_body)
		if file_sha and head_ref:
			cleanup_branch_trace_file(head_repo, head_ref, file_sha, token)
		total_traces = len(merged)
	else:
		total_traces = len(existing)

	return PublishResult(
		source="branch_file",
		trace_count=len(traces),
		total_traces=total_traces,
		repo=repo,
		pr_number=pr_number,
	)
