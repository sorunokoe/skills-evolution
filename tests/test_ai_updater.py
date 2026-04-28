from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from skills_evolution import ai_updater


def make_skill(tmp_dir: str, content: str, agent_dir: str = ".github") -> tuple[Path, Path, str]:
	repo_root = Path(tmp_dir) / "repo"
	skill_dir = repo_root / agent_dir / "skills" / "tca-standards"
	skill_dir.mkdir(parents=True)
	skill_path = skill_dir / "SKILL.md"
	skill_path.write_text(content, encoding="utf-8")
	return repo_root, skill_path, str(skill_path.relative_to(repo_root))


class ApplyPatchesTests(unittest.TestCase):
	def test_unique_match_is_applied(self) -> None:
		content = "# TCA\n\nUse TCA 1.5 pattern.\n"
		with tempfile.TemporaryDirectory() as tmp:
			_, path, rel = make_skill(tmp, content)
			patches = [{"old_text": "TCA 1.5", "new_text": "TCA 1.10", "reason": "bump"}]
			applied, skipped, ambiguous = ai_updater.apply_patches(patches, path, rel, content)
			self.assertEqual((applied, skipped, ambiguous), (1, 0, 0))
			self.assertIn("TCA 1.10", path.read_text())
			self.assertNotIn("TCA 1.5", path.read_text())

	def test_not_found_is_skipped(self) -> None:
		content = "# TCA\n\nNo version here.\n"
		with tempfile.TemporaryDirectory() as tmp:
			_, path, rel = make_skill(tmp, content)
			patches = [{"old_text": "TCA 1.5", "new_text": "TCA 1.10", "reason": "bump"}]
			applied, skipped, _ = ai_updater.apply_patches(patches, path, rel, content)
			self.assertEqual(applied, 0)
			self.assertEqual(skipped, 1)
			self.assertEqual(path.read_text(), content)

	def test_duplicate_match_is_ambiguous(self) -> None:
		content = "TCA 1.5 pattern.\nSee also TCA 1.5.\n"
		with tempfile.TemporaryDirectory() as tmp:
			_, path, rel = make_skill(tmp, content)
			patches = [{"old_text": "TCA 1.5", "new_text": "TCA 1.10", "reason": "bump"}]
			applied, _, ambiguous = ai_updater.apply_patches(patches, path, rel, content)
			self.assertEqual(applied, 0)
			self.assertEqual(ambiguous, 1)
			self.assertEqual(path.read_text(), content)

	def test_path_outside_skills_is_rejected(self) -> None:
		content = "# Bad\n"
		with tempfile.TemporaryDirectory() as tmp:
			repo_root = Path(tmp) / "repo"
			repo_root.mkdir()
			bad_path = repo_root / "some" / "other" / "file.md"
			bad_path.parent.mkdir(parents=True)
			bad_path.write_text(content)
			patches = [{"old_text": "Bad", "new_text": "Replaced", "reason": "test"}]
			applied, _, _ = ai_updater.apply_patches(patches, bad_path, "some/other/file.md", content)
			self.assertEqual(applied, 0)
			self.assertEqual(bad_path.read_text(), content)

	def test_claude_skills_path_is_allowed(self) -> None:
		content = "# TCA\n\nUse TCA 1.5 pattern.\n"
		with tempfile.TemporaryDirectory() as tmp:
			_, path, rel = make_skill(tmp, content, agent_dir=".claude")
			self.assertTrue(rel.startswith(".claude/skills/"))
			patches = [{"old_text": "TCA 1.5", "new_text": "TCA 1.10", "reason": "bump"}]
			applied, _, _ = ai_updater.apply_patches(patches, path, rel, content)
			self.assertEqual(applied, 1)

	def test_multiple_patches_applied_sequentially(self) -> None:
		content = "Use TCA 1.5 and Factory 2.1.\n"
		with tempfile.TemporaryDirectory() as tmp:
			_, path, rel = make_skill(tmp, content)
			patches = [
				{"old_text": "TCA 1.5", "new_text": "TCA 1.10", "reason": "TCA bump"},
				{"old_text": "Factory 2.1", "new_text": "Factory 2.4", "reason": "Factory bump"},
			]
			applied, _, _ = ai_updater.apply_patches(patches, path, rel, content)
			self.assertEqual(applied, 2)
			updated = path.read_text()
			self.assertIn("TCA 1.10", updated)
			self.assertIn("Factory 2.4", updated)

	def test_empty_old_text_is_skipped(self) -> None:
		content = "# TCA\n"
		with tempfile.TemporaryDirectory() as tmp:
			_, path, rel = make_skill(tmp, content)
			patches = [{"old_text": "", "new_text": "anything", "reason": "empty"}]
			applied, skipped, _ = ai_updater.apply_patches(patches, path, rel, content)
			self.assertEqual((applied, skipped), (0, 1))


class DiscoverDepsTests(unittest.TestCase):
	def test_parses_package_resolved_v3(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			repo_root = Path(tmp)
			resolved = {
				"pins": [
					{
						"identity": "swift-composable-architecture",
						"location": "https://github.com/pointfreeco/swift-composable-architecture",
						"state": {"version": "1.10.0"},
					},
					{
						"identity": "private-dep",
						"location": "https://example.com/private/dep",
						"state": {"revision": "def"},
					},
				],
				"version": 3,
			}
			(repo_root / "Package.resolved").write_text(json.dumps(resolved))
			deps = ai_updater.discover_deps(repo_root)
			self.assertEqual(len(deps), 1)
			self.assertEqual(deps[0]["alias"], "swift-composable-architecture")
			self.assertEqual(deps[0]["repo"], "pointfreeco/swift-composable-architecture")
			self.assertEqual(deps[0]["pinned"], "1.10.0")

	def test_parses_package_resolved_v2(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			repo_root = Path(tmp)
			resolved = {
				"object": {
					"pins": [
						{
							"package": "TCA",
							"repositoryURL": "https://github.com/pointfreeco/swift-composable-architecture.git",
							"state": {"version": "1.5.0"},
						}
					]
				},
				"version": 2,
			}
			(repo_root / "Package.resolved").write_text(json.dumps(resolved))
			deps = ai_updater.discover_deps(repo_root)
			self.assertEqual(len(deps), 1)
			self.assertEqual(deps[0]["repo"], "pointfreeco/swift-composable-architecture")
			self.assertEqual(deps[0]["pinned"], "1.5.0")

	def test_skips_build_directory(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			repo_root = Path(tmp)
			build_dir = repo_root / ".build" / "checkouts" / "some-pkg"
			build_dir.mkdir(parents=True)
			(build_dir / "Package.resolved").write_text(json.dumps({"pins": [], "version": 3}))
			deps = ai_updater.discover_deps(repo_root)
			self.assertEqual(deps, [])

	def test_handles_non_github_url(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			repo_root = Path(tmp)
			resolved = {
				"pins": [{"identity": "other", "location": "https://example.com/repo.git", "state": {"version": "1.0.0"}}],
				"version": 3,
			}
			(repo_root / "Package.resolved").write_text(json.dumps(resolved))
			deps = ai_updater.discover_deps(repo_root)
			self.assertEqual(deps, [])

	def test_parses_go_mod(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			repo_root = Path(tmp)
			(repo_root / "go.mod").write_text(
				"module github.com/myorg/myapp\n\ngo 1.21\n\nrequire (\n"
				"\tgithub.com/gin-gonic/gin v1.9.1\n"
				"\tgithub.com/stretchr/testify v1.8.4 // indirect\n"
				")\n"
			)
			deps = ai_updater.discover_deps(repo_root)
			repos = [d["repo"] for d in deps]
			self.assertIn("gin-gonic/gin", repos)
			self.assertIn("stretchr/testify", repos)
			gin = next(d for d in deps if d["repo"] == "gin-gonic/gin")
			self.assertEqual(gin["pinned"], "v1.9.1")

	def test_parses_npm_github_ref(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			repo_root = Path(tmp)
			(repo_root / "package.json").write_text(json.dumps({
				"dependencies": {
					"my-lib": "github:owner/my-lib#v2.0.0",
					"react": "^18.2.0",
				}
			}))
			deps = ai_updater.discover_deps(repo_root)
			self.assertEqual(len(deps), 1)
			self.assertEqual(deps[0]["repo"], "owner/my-lib")
			self.assertEqual(deps[0]["pinned"], "v2.0.0")

	def test_deduplicates_across_ecosystems(self) -> None:
		"""Same GitHub repo referenced in two files → appears once."""
		with tempfile.TemporaryDirectory() as tmp:
			repo_root = Path(tmp)
			# SPM reference
			resolved = {
				"pins": [{"identity": "gin", "location": "https://github.com/gin-gonic/gin", "state": {"version": "1.9.1"}}],
				"version": 3,
			}
			(repo_root / "Package.resolved").write_text(json.dumps(resolved))
			# npm reference to same repo
			(repo_root / "package.json").write_text(json.dumps({
				"dependencies": {"gin": "github:gin-gonic/gin#v1.9.1"}
			}))
			deps = ai_updater.discover_deps(repo_root)
			repos = [d["repo"] for d in deps]
			self.assertEqual(repos.count("gin-gonic/gin"), 1)


class WriteReportTests(unittest.TestCase):
	def test_no_patches_says_up_to_date(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			output_dir = Path(tmp)
			report = {
				"skills_changed": 0,
				"total_patches_applied": 0,
				"total_patches_skipped": 0,
				"total_patches_ambiguous": 0,
				"by_skill": [],
			}
			ai_updater.write_report(output_dir, report, [])
			md = (output_dir / "skills-ai-updates.md").read_text()
			self.assertIn("up to date", md)

	def test_with_patches_lists_applied(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			output_dir = Path(tmp)
			report = {
				"skills_changed": 1,
				"total_patches_applied": 1,
				"total_patches_skipped": 0,
				"total_patches_ambiguous": 0,
				"by_skill": [
					{
						"skill": "tca-standards",
						"summary": "Updated TCA ref.",
						"applied": 1,
						"patches": [{"old_text": "x", "new_text": "y", "reason": "version bump", "_status": "applied"}],
					}
				],
			}
			deps = [{"alias": "TCA", "repo": "pointfreeco/swift-composable-architecture", "pinned": "1.5.0"}]
			ai_updater.write_report(output_dir, report, deps)
			md = (output_dir / "skills-ai-updates.md").read_text()
			self.assertIn("tca-standards", md)
			self.assertIn("version bump", md)
			self.assertIn("TCA", md)


class OssPathSafetyTests(unittest.TestCase):
	def test_oss_skill_md_path_allowed(self) -> None:
		content = "# Swift KMP\n\nUse SKIE 0.9 pattern.\n"
		with tempfile.TemporaryDirectory() as tmp:
			repo_root = Path(tmp) / "swift-kmp"
			repo_root.mkdir()
			skill_path = repo_root / "SKILL.md"
			skill_path.write_text(content, encoding="utf-8")
			rel = skill_path.relative_to(repo_root).as_posix()
			patches = [{"old_text": "SKIE 0.9", "new_text": "SKIE 1.0", "reason": "bump"}]
			applied, _, _ = ai_updater.apply_patches(patches, skill_path, rel, content, allowed_path_re=ai_updater._ALLOWED_PATH_OSS)
			self.assertEqual(applied, 1)
			self.assertIn("SKIE 1.0", skill_path.read_text())

	def test_oss_reference_path_allowed(self) -> None:
		content = "# Flow\n\nSKIE 0.9 pattern.\n"
		with tempfile.TemporaryDirectory() as tmp:
			repo_root = Path(tmp) / "swift-kmp"
			refs = repo_root / "references"
			refs.mkdir(parents=True)
			ref_path = refs / "flow-bridging.md"
			ref_path.write_text(content, encoding="utf-8")
			rel = ref_path.relative_to(repo_root).as_posix()
			self.assertEqual(rel, "references/flow-bridging.md")
			patches = [{"old_text": "SKIE 0.9", "new_text": "SKIE 1.0", "reason": "bump"}]
			applied, _, _ = ai_updater.apply_patches(patches, ref_path, rel, content, allowed_path_re=ai_updater._ALLOWED_PATH_OSS)
			self.assertEqual(applied, 1)

	def test_oss_readme_path_rejected(self) -> None:
		content = "# README\n"
		with tempfile.TemporaryDirectory() as tmp:
			repo_root = Path(tmp) / "swift-kmp"
			repo_root.mkdir()
			readme = repo_root / "README.md"
			readme.write_text(content, encoding="utf-8")
			patches = [{"old_text": "README", "new_text": "REPLACED", "reason": "test"}]
			applied, _, _ = ai_updater.apply_patches(patches, readme, "README.md", content, allowed_path_re=ai_updater._ALLOWED_PATH_OSS)
			self.assertEqual(applied, 0)
			self.assertEqual(readme.read_text(), content)

	def test_oss_agents_md_rejected(self) -> None:
		content = "# AGENTS\n"
		with tempfile.TemporaryDirectory() as tmp:
			repo_root = Path(tmp) / "swift-kmp"
			repo_root.mkdir()
			agents = repo_root / "AGENTS.md"
			agents.write_text(content, encoding="utf-8")
			patches = [{"old_text": "AGENTS", "new_text": "REPLACED", "reason": "test"}]
			applied, _, _ = ai_updater.apply_patches(patches, agents, "AGENTS.md", content, allowed_path_re=ai_updater._ALLOWED_PATH_OSS)
			self.assertEqual(applied, 0)


class OssSkillNameTests(unittest.TestCase):
	def test_extracts_name_from_frontmatter(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			repo_root = Path(tmp) / "workspace"
			repo_root.mkdir()
			(repo_root / "SKILL.md").write_text(
				"---\nname: swift-kmp\ndescription: KMP patterns\napplyTo: '**/*.swift'\n---\n",
				encoding="utf-8",
			)
			name = ai_updater._extract_oss_skill_name(repo_root)
			self.assertEqual(name, "swift-kmp")

	def test_fallback_to_dir_name_when_no_skill_md(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			repo_root = Path(tmp) / "my-skill"
			repo_root.mkdir()
			name = ai_updater._extract_oss_skill_name(repo_root)
			self.assertEqual(name, "my-skill")

	def test_fallback_to_dir_name_when_no_frontmatter(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			repo_root = Path(tmp) / "my-skill"
			repo_root.mkdir()
			(repo_root / "SKILL.md").write_text("# No frontmatter here\n", encoding="utf-8")
			name = ai_updater._extract_oss_skill_name(repo_root)
			self.assertEqual(name, "my-skill")

	def test_fallback_when_name_field_absent(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			repo_root = Path(tmp) / "my-skill"
			repo_root.mkdir()
			(repo_root / "SKILL.md").write_text("---\ndescription: no name field\n---\n", encoding="utf-8")
			name = ai_updater._extract_oss_skill_name(repo_root)
			self.assertEqual(name, "my-skill")


class OssIterFilesTests(unittest.TestCase):
	def test_discovers_skill_and_refs(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			repo_root = Path(tmp) / "swift-kmp"
			refs = repo_root / "references"
			refs.mkdir(parents=True)
			(repo_root / "SKILL.md").write_text("---\nname: swift-kmp\n---\n", encoding="utf-8")
			(refs / "architecture.md").write_text("# Arch\n", encoding="utf-8")
			(refs / "flow-bridging.md").write_text("# Flow\n", encoding="utf-8")
			(repo_root / "README.md").write_text("# README\n", encoding="utf-8")
			files = ai_updater._iter_oss_skill_files(repo_root)
			names = [f.name for f in files]
			self.assertIn("SKILL.md", names)
			self.assertIn("architecture.md", names)
			self.assertIn("flow-bridging.md", names)
			self.assertNotIn("README.md", names)

	def test_missing_skill_md_returns_empty(self) -> None:
		with tempfile.TemporaryDirectory() as tmp:
			repo_root = Path(tmp) / "swift-kmp"
			repo_root.mkdir()
			files = ai_updater._iter_oss_skill_files(repo_root)
			self.assertEqual(files, [])


if __name__ == "__main__":
	unittest.main()
