from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skills_evolution import core


class CoreTests(unittest.TestCase):
	def test_append_and_merge_local_traces_into_body(self) -> None:
		with tempfile.TemporaryDirectory() as tmp_dir:
			repo_root = Path(tmp_dir)
			core.append_local_trace(
				repo_root=repo_root,
				skill="swiftui-standards",
				file=".github/skills/swiftui-standards/references/state-management.md",
				section_id="store-ownership",
				line_start=1,
				line_end=5,
				reason="Smoke test",
			)

			prepared = core.merge_local_traces_into_body(repo_root, "Draft PR body")

			self.assertEqual(len(prepared.traces), 1)
			self.assertEqual(prepared.total_traces, 1)
			self.assertIn(core.AUTO_BEGIN, prepared.body)
			self.assertIn("Draft PR body", prepared.body)
			self.assertEqual(prepared.trace_path, (repo_root / core.TRACE_FILE).resolve())

	def test_merge_traces_keeps_order_and_overwrites_duplicates(self) -> None:
		existing = [
			{
				"trace_id": "trace-a",
				"skill": "swiftui-standards",
				"file": "a.md",
				"section_id": "one",
				"line_start": 1,
				"line_end": 1,
				"reason": "old",
			},
			{
				"trace_id": "trace-b",
				"skill": "tca-standards",
				"file": "b.md",
				"section_id": "two",
				"line_start": 2,
				"line_end": 2,
				"reason": "keep",
			},
		]
		incoming = [
			{
				"trace_id": "trace-a",
				"skill": "swiftui-standards",
				"file": "a.md",
				"section_id": "one",
				"line_start": 1,
				"line_end": 1,
				"reason": "new",
			},
			{
				"trace_id": "trace-c",
				"skill": "swift-testing-standards",
				"file": "c.md",
				"section_id": "three",
				"line_start": 3,
				"line_end": 3,
				"reason": "added",
			},
		]

		merged = core.merge_traces(existing, incoming)

		self.assertEqual([item["trace_id"] for item in merged], ["trace-a", "trace-b", "trace-c"])
		self.assertEqual(merged[0]["reason"], "new")

	def test_publish_local_traces_patches_pr_and_clears_local_file(self) -> None:
		with tempfile.TemporaryDirectory() as tmp_dir:
			repo_root = Path(tmp_dir)
			core.append_local_trace(
				repo_root=repo_root,
				skill="swiftui-standards",
				file=".github/skills/swiftui-standards/references/state-management.md",
				section_id="store-ownership",
				line_start=1,
				line_end=5,
				reason="Smoke test",
			)

			with (
				patch("skills_evolution.core.resolve_token", return_value="token"),
				patch("skills_evolution.core.detect_pr_context_with_gh", return_value=("owner/repo", 42)),
				patch("skills_evolution.core.fetch_pr", return_value={"body": "Draft PR body"}),
				patch("skills_evolution.core.patch_pr_body") as patch_body,
			):
				result = core.publish_local_traces(repo_root=repo_root)

			self.assertEqual(result.repo, "owner/repo")
			self.assertEqual(result.pr_number, 42)
			self.assertEqual(result.trace_count, 1)
			self.assertEqual(result.total_traces, 1)
			self.assertFalse((repo_root / core.TRACE_FILE).exists())
			patch_body.assert_called_once()


if __name__ == "__main__":
	unittest.main()
