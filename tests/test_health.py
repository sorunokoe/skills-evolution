from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from skills_evolution import health


class HealthTests(unittest.TestCase):
	def test_combine_reports_without_semantic_file_stays_honest(self) -> None:
		with tempfile.TemporaryDirectory() as tmp_dir:
			output_dir = Path(tmp_dir)
			(output_dir / "skills-audit.json").write_text(json.dumps({"findings_count": 2}), encoding="utf-8")
			(output_dir / "skills-feedback.json").write_text(
				json.dumps({"proposal_count": 1, "trace_count": 3, "disputed_sections": [{"skill": "swiftui-standards"}]}),
				encoding="utf-8",
			)

			findings, proposals = health.combine_reports(output_dir)

			self.assertEqual(findings, 2)
			self.assertEqual(proposals, 1)
			summary = (output_dir / "skills-health-summary.md").read_text(encoding="utf-8")
			self.assertIn("Optional AI content-level findings: **not run**", summary)
			self.assertIn("Explicit AI traces analyzed: **3**", summary)


if __name__ == "__main__":
	unittest.main()
