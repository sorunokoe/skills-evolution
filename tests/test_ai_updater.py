from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from skills_evolution import ai_updater


class ApplyPatchesTests(unittest.TestCase):
	def _skill_path(self, tmp_dir: str, content: str) -> tuple[Path, Path, str]:
		repo_root = Path(tmp_dir) / "repo"
		skill_dir = repo_root / ".github" / "skills" / "tca-standards"
		skill_dir.mkdir(parents=True)
		skill_path = skill_dir / "SKILL.md"
		skill_path.write_text(content, encoding="utf-8")
		file_rel = str(skill_path.relative_to(repo_root))
		return repo_root, skill_path, file_rel

	def test_apply_patches_unique_match_succeeds(self) -> None:
		content = "# TCA\n\nUse TCA 1.5 pattern.\n"
		with tempfile.TemporaryDirectory() as tmp:
			_, skill_path, file_rel = self._skill_path(tmp, content)
			patches = [{"old_text": "TCA 1.5", "new_text": "TCA 1.10", "reason": "version bump"}]
			annotated, applied, skipped, ambiguous = ai_updater.apply_patches(patches, skill_path, file_rel, content)
			self.assertEqual(applied, 1)
			self.assertEqual(skipped, 0)
			self.assertEqual(ambiguous, 0)
			self.assertEqual(annotated[0]["_status"], "applied")
			updated = skill_path.read_text(encoding="utf-8")
			self.assertIn("TCA 1.10", updated)
			self.assertNotIn("TCA 1.5", updated)

	def test_apply_patches_not_found_is_skipped(self) -> None:
		content = "# TCA\n\nNo version references here.\n"
		with tempfile.TemporaryDirectory() as tmp:
			_, skill_path, file_rel = self._skill_path(tmp, content)
			patches = [{"old_text": "TCA 1.5", "new_text": "TCA 1.10", "reason": "version bump"}]
			annotated, applied, skipped, ambiguous = ai_updater.apply_patches(patches, skill_path, file_rel, content)
			self.assertEqual(applied, 0)
			self.assertEqual(skipped, 1)
			self.assertEqual(ambiguous, 0)
			self.assertEqual(annotated[0]["_status"], "skipped_not_found")
			# File must not be modified.
			self.assertEqual(skill_path.read_text(encoding="utf-8"), content)

	def test_apply_patches_duplicate_match_is_ambiguous(self) -> None:
		content = "# TCA\n\nTCA 1.5 pattern.\nSee also TCA 1.5.\n"
		with tempfile.TemporaryDirectory() as tmp:
			_, skill_path, file_rel = self._skill_path(tmp, content)
			patches = [{"old_text": "TCA 1.5", "new_text": "TCA 1.10", "reason": "version bump"}]
			annotated, applied, skipped, ambiguous = ai_updater.apply_patches(patches, skill_path, file_rel, content)
			self.assertEqual(applied, 0)
			self.assertEqual(ambiguous, 1)
			self.assertEqual(annotated[0]["_status"], "skipped_ambiguous")
			# File must not be modified.
			self.assertEqual(skill_path.read_text(encoding="utf-8"), content)

	def test_apply_patches_path_safety_rejects_outside_skills(self) -> None:
		content = "# Bad\n"
		with tempfile.TemporaryDirectory() as tmp:
			repo_root = Path(tmp) / "repo"
			repo_root.mkdir()
			evil_path = repo_root / "some" / "other" / "file.md"
			evil_path.parent.mkdir(parents=True)
			evil_path.write_text(content, encoding="utf-8")
			patches = [{"old_text": "Bad", "new_text": "Replaced", "reason": "evil"}]
			annotated, applied, skipped, ambiguous = ai_updater.apply_patches(
				patches, evil_path, "some/other/file.md", content
			)
			self.assertEqual(applied, 0)
			# File must not be modified.
			self.assertEqual(evil_path.read_text(encoding="utf-8"), content)

	def test_apply_patches_multiple_patches_sequentially(self) -> None:
		content = "# Guide\n\nUse TCA 1.5 here.\nFactory 2.1 is the DI.\n"
		with tempfile.TemporaryDirectory() as tmp:
			_, skill_path, file_rel = self._skill_path(tmp, content)
			patches = [
				{"old_text": "TCA 1.5", "new_text": "TCA 1.10", "reason": "TCA bump"},
				{"old_text": "Factory 2.1", "new_text": "Factory 2.4", "reason": "Factory bump"},
			]
			annotated, applied, skipped, ambiguous = ai_updater.apply_patches(patches, skill_path, file_rel, content)
			self.assertEqual(applied, 2)
			updated = skill_path.read_text(encoding="utf-8")
			self.assertIn("TCA 1.10", updated)
			self.assertIn("Factory 2.4", updated)

	def test_apply_patches_empty_old_text_is_skipped(self) -> None:
		content = "# TCA\n\nSome content.\n"
		with tempfile.TemporaryDirectory() as tmp:
			_, skill_path, file_rel = self._skill_path(tmp, content)
			patches = [{"old_text": "", "new_text": "anything", "reason": "empty"}]
			_, applied, skipped, _ = ai_updater.apply_patches(patches, skill_path, file_rel, content)
			self.assertEqual(applied, 0)
			self.assertEqual(skipped, 1)


class CuratedReleaseNotesTests(unittest.TestCase):
	def test_extracts_breaking_lines(self) -> None:
		body = "# v1.10\n\nBreaking: removed old API.\nSome other note.\nDeprecated: xyz.\n"
		result = ai_updater._curated_release_notes(body)
		self.assertIn("Breaking", result)
		self.assertIn("Deprecated", result)
		self.assertNotIn("Some other note", result)

	def test_fallback_to_first_lines_when_no_keywords(self) -> None:
		body = "# v1.10\n\nAdded feature X.\nFixed bug Y.\n"
		result = ai_updater._curated_release_notes(body)
		self.assertIn("Added feature X", result)

	def test_empty_body_returns_empty(self) -> None:
		self.assertEqual(ai_updater._curated_release_notes(""), "")


class WriteReportTests(unittest.TestCase):
	def test_write_report_no_patches(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			output_dir = Path(tmp)
			ai_updater.write_report(output_dir, [], [], 0, 0, 0)
			md = (output_dir / "skills-ai-updates.md").read_text(encoding="utf-8")
			self.assertIn("up to date", md)
			data = json.loads((output_dir / "skills-ai-updates.json").read_text(encoding="utf-8"))
			self.assertEqual(data["total_patches_applied"], 0)
			self.assertEqual(data["skills_changed"], 0)

	def test_write_report_with_patches(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			output_dir = Path(tmp)
			skills_data = [
				{
					"skill": "tca-standards",
					"file": ".github/skills/tca-standards/SKILL.md",
					"summary": "Updated TCA version reference.",
					"patches": [{"old_text": "TCA 1.5", "new_text": "TCA 1.10", "reason": "version bump", "_status": "applied"}],
					"applied": 1,
					"skipped": 0,
					"ambiguous": 0,
				}
			]
			dep_infos = [{"alias": "TCA", "repo": "pointfreeco/swift-composable-architecture", "version": "1.10.0", "published_at": "2025-01-01", "url": "", "notes": "", "error": ""}]
			ai_updater.write_report(output_dir, skills_data, dep_infos, 1, 0, 0)
			md = (output_dir / "skills-ai-updates.md").read_text(encoding="utf-8")
			self.assertIn("tca-standards", md)
			self.assertIn("version bump", md)
			self.assertIn("TCA", md)


if __name__ == "__main__":
	unittest.main()
