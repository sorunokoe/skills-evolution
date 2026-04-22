from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import __version__, core

SERVER_NAME = "skills-evolution"
PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
	{
		"name": "record_skill_trace",
		"description": "Append one local skill trace record to .github/.skill-trace.ndjson.",
		"inputSchema": {
			"type": "object",
			"properties": {
				"repoRoot": {"type": "string", "default": "."},
				"skill": {"type": "string"},
				"file": {"type": "string"},
				"sectionId": {"type": "string"},
				"lineStart": {"type": "integer"},
				"lineEnd": {"type": "integer"},
				"reason": {"type": "string"},
				"confidence": {"type": "number"},
				"traceId": {"type": "string"},
			},
			"required": ["skill", "file", "sectionId", "lineStart", "reason"],
		},
	},
	{
		"name": "publish_skill_traces_to_pr",
		"description": "Publish local traces into the current branch PR body.",
		"inputSchema": {
			"type": "object",
			"properties": {
				"repoRoot": {"type": "string", "default": "."},
				"repo": {"type": "string"},
				"prNumber": {"type": "integer"},
				"token": {"type": "string"},
				"keepLocalFile": {"type": "boolean", "default": False},
			},
		},
	},
]


def read_message() -> dict[str, Any] | None:
	headers: dict[str, str] = {}
	while True:
		line = sys.stdin.buffer.readline()
		if not line:
			return None
		decoded = line.decode("utf-8").strip()
		if not decoded:
			break
		key, value = decoded.split(":", 1)
		headers[key.lower()] = value.strip()
	length = int(headers.get("content-length", "0"))
	if length <= 0:
		return None
	payload = sys.stdin.buffer.read(length)
	return json.loads(payload.decode("utf-8"))


def send_message(message: dict[str, Any]) -> None:
	data = json.dumps(message).encode("utf-8")
	sys.stdout.buffer.write(f"Content-Length: {len(data)}\r\n\r\n".encode("utf-8"))
	sys.stdout.buffer.write(data)
	sys.stdout.buffer.flush()


def send_response(message_id: Any, result: dict[str, Any]) -> None:
	send_message({"jsonrpc": "2.0", "id": message_id, "result": result})


def send_error(message_id: Any, code: int, message: str) -> None:
	send_message({"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}})


def text_tool_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
	result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
	if is_error:
		result["isError"] = True
	return result


def handle_record_skill_trace(arguments: dict[str, Any]) -> dict[str, Any]:
	record = core.append_local_trace(
		repo_root=Path(arguments.get("repoRoot", ".")).resolve(),
		skill=arguments["skill"],
		file=arguments["file"],
		section_id=arguments["sectionId"],
		line_start=int(arguments["lineStart"]),
		line_end=arguments.get("lineEnd"),
		reason=arguments["reason"],
		confidence=arguments.get("confidence"),
		trace_id=arguments.get("traceId"),
	)
	return text_tool_result(f"Recorded trace {record['trace_id']} in {core.TRACE_FILE}.")

def handle_publish_skill_traces(arguments: dict[str, Any]) -> dict[str, Any]:
	result = core.publish_local_traces(
		repo_root=Path(arguments.get("repoRoot", ".")).resolve(),
		repo=arguments.get("repo"),
		pr_number=arguments.get("prNumber"),
		token=arguments.get("token"),
		keep_local_file=arguments.get("keepLocalFile", False),
	)
	return text_tool_result(
		f"Published {result.trace_count} local trace(s) to PR #{result.pr_number} in {result.repo}. "
		f"Total traces in PR body: {result.total_traces}."
	)


def handle_tool_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
	if name == "record_skill_trace":
		return handle_record_skill_trace(arguments)
	if name == "publish_skill_traces_to_pr":
		return handle_publish_skill_traces(arguments)
	raise ValueError(f"Unknown tool: {name}")


def main() -> int:
	while True:
		message = read_message()
		if message is None:
			return 0

		message_id = message.get("id")
		method = message.get("method")
		params = message.get("params") or {}

		if method == "initialize":
			send_response(
				message_id,
				{
					"protocolVersion": PROTOCOL_VERSION,
					"capabilities": {"tools": {}},
					"serverInfo": {"name": SERVER_NAME, "version": __version__},
				},
			)
			continue

		if method == "notifications/initialized":
			continue

		if method == "ping":
			send_response(message_id, {})
			continue

		if method == "tools/list":
			send_response(message_id, {"tools": TOOLS})
			continue

		if method == "tools/call":
			try:
				result = handle_tool_call(params.get("name", ""), params.get("arguments") or {})
			except Exception as error:  # noqa: BLE001
				send_response(message_id, text_tool_result(f"Error: {error}", is_error=True))
				continue
			send_response(message_id, result)
			continue

		if message_id is not None:
			send_error(message_id, -32601, f"Unsupported method: {method}")


if __name__ == "__main__":
	raise SystemExit(main())
