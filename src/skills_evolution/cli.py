from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import core


def cmd_write(args: argparse.Namespace) -> int:
	record = core.append_local_trace(
		repo_root=Path(args.repo_root).resolve(),
		skill=args.skill,
		file=args.file,
		section_id=args.section_id,
		line_start=args.line_start,
		line_end=args.line_end,
		reason=args.reason,
		confidence=args.confidence,
		trace_id=args.trace_id,
	)
	print(f"trace_file={core.TRACE_FILE}")
	print(f"trace_id={record['trace_id']}")
	return 0


def cmd_publish(args: argparse.Namespace) -> int:
	result = core.publish_local_traces(
		repo_root=Path(args.repo_root).resolve(),
		repo=args.repo,
		pr_number=args.pr_number,
		token=args.token,
		keep_local_file=args.keep_local_file,
	)
	print(
		f"Published {result.trace_count} local trace(s) to PR #{result.pr_number} in {result.repo}. "
		f"Total traces in PR body: {result.total_traces}."
	)
	return 0


def cmd_fallback(args: argparse.Namespace) -> int:
	result = core.publish_branch_traces(
		repo=args.repo,
		pr_number=args.pr_number,
		token=args.token,
	)
	print(
		f"Published {result.trace_count} committed trace(s) to PR #{result.pr_number} in {result.repo}. "
		f"Total traces in PR body: {result.total_traces}."
	)
	return 0


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(prog="skills-evolution")
	subparsers = parser.add_subparsers(dest="command", required=True)

	write = subparsers.add_parser("write", help="Append one local skill trace record.")
	write.add_argument("--repo-root", default=".")
	write.add_argument("--skill", required=True)
	write.add_argument("--file", required=True)
	write.add_argument("--section-id", required=True)
	write.add_argument("--line-start", type=int, required=True)
	write.add_argument("--line-end", type=int)
	write.add_argument("--reason", required=True)
	write.add_argument("--confidence", type=float)
	write.add_argument("--trace-id")
	write.set_defaults(func=cmd_write)

	publish = subparsers.add_parser("publish", help="Publish local traces into the current PR body.")
	publish.add_argument("--repo-root", default=".")
	publish.add_argument("--repo")
	publish.add_argument("--pr-number", type=int)
	publish.add_argument("--token")
	publish.add_argument("--keep-local-file", action="store_true")
	publish.set_defaults(func=cmd_publish)

	fallback = subparsers.add_parser("fallback", help="Publish committed branch traces into the PR body.")
	fallback.add_argument("--repo", required=True)
	fallback.add_argument("--pr-number", type=int, required=True)
	fallback.add_argument("--token", required=True)
	fallback.set_defaults(func=cmd_fallback)

	return parser


def main(argv: list[str] | None = None) -> int:
	parser = build_parser()
	args = parser.parse_args(argv)
	try:
		return args.func(args)
	except RuntimeError as error:
		print(str(error), file=sys.stderr)
		return 1


if __name__ == "__main__":
	raise SystemExit(main())
