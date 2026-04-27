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
				json.dumps(
					{
						"proposal_count": 1,
						"trace_count": 3,
						"comment_signal_count": 0,
						"disputed_sections": [{"skill": "swiftui-standards"}],
					}
				),
				encoding="utf-8",
			)

			findings, proposals = health.combine_reports(output_dir)

			self.assertEqual(findings, 2)
			self.assertEqual(proposals, 1)
			summary = (output_dir / "skills-health-summary.md").read_text(encoding="utf-8")
			self.assertIn("Optional AI content-level findings: **not run**", summary)
			self.assertIn("AI inline skill update: **not run**", summary)
			self.assertIn("Explicit AI traces analyzed: **3**", summary)
			self.assertIn("Review comment signals analyzed: **0**", summary)

	def test_combine_reports_with_ai_updates_increments_findings(self) -> None:
		with tempfile.TemporaryDirectory() as tmp_dir:
			output_dir = Path(tmp_dir)
			(output_dir / "skills-audit.json").write_text(json.dumps({"findings_count": 0}), encoding="utf-8")
			(output_dir / "skills-feedback.json").write_text(
				json.dumps({"proposal_count": 0, "trace_count": 0, "comment_signal_count": 0, "disputed_sections": []}),
				encoding="utf-8",
			)
			(output_dir / "skills-ai-updates.json").write_text(
				json.dumps({
					"total_patches_applied": 3,
					"skills_changed": 2,
					"skills_processed": 5,
					"total_patches_skipped": 1,
					"total_patches_ambiguous": 0,
					"by_skill": [
						{"skill": "tca-standards", "applied": 2, "summary": "Updated TCA version."},
						{"skill": "swiftui-standards", "applied": 1, "summary": "Updated SwiftUI API."},
					],
				}),
				encoding="utf-8",
			)

			findings, proposals = health.combine_reports(output_dir)

			# AI patches applied (3) should be folded into findings_count.
			self.assertEqual(findings, 3)
			summary = (output_dir / "skills-health-summary.md").read_text(encoding="utf-8")
			self.assertIn("AI inline patches applied: **3**", summary)
			self.assertIn("tca-standards", summary)
			self.assertIn("swiftui-standards", summary)

	def test_analyze_feedback_uses_normal_review_comments(self) -> None:
		with tempfile.TemporaryDirectory() as tmp_dir:
			repo_root = Path(tmp_dir) / "repo"
			skill_dir = repo_root / ".github" / "skills" / "swiftui-standards"
			skill_dir.mkdir(parents=True)
			(skill_dir / "SKILL.md").write_text(
				"---\nname: swiftui-standards\ndescription: SwiftUI guidance\napplyTo: '**/*.swift'\n---\n",
				encoding="utf-8",
			)
			raw_path = Path(tmp_dir) / "raw.json"
			raw_path.write_text(
				json.dumps(
					{
						"pull_requests": [
							{
								"number": 42,
								"body": "",
								"issue_comments": [
									{"body": "swiftui-standards should mention NavigationPath handling for deep links."},
									{"body": "SwiftUI standards needs guidance for path restoration."},
								],
								"review_comments": [],
								"reviews": [],
							}
						]
					}
				),
				encoding="utf-8",
			)
			output_dir = Path(tmp_dir) / "outputs"

			proposals = health.analyze_feedback(raw_path, repo_root, output_dir)

			self.assertEqual(proposals, 1)
			feedback = json.loads((output_dir / "skills-feedback.json").read_text(encoding="utf-8"))
			self.assertEqual(feedback["comment_signal_count"], 2)
			self.assertEqual(feedback["metrics_by_skill"]["swiftui-standards"]["comment_gap"], 2)
			self.assertEqual(feedback["proposal_count"], 1)
			report = (output_dir / "skills-feedback.md").read_text(encoding="utf-8")
			self.assertIn("Review comment signals analyzed: 2", report)
			self.assertIn("gap=2", report)


if __name__ == "__main__":
	unittest.main()
