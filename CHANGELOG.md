# Changelog

## v0.2.0 — Evolution badge + smart quality checks

### New features

**Evolution badge**
- PR descriptions now include a shields.io evolution badge that tracks how many times a skill has been updated
- Badge is committed to the skill's `README.md` after merge, inserted after existing badge lines (not inside code blocks)
- 5 stages: 🦠 newborn → 🐛 evolving → 🦎 thriving → 🧠 sentient → 🤖 legendary
- New CLI commands: `health update-badge`, `health read-evolution-num`
- `health combine` gains `--evolution-num` flag

**Smart skill quality checks**
- `SKILL_TOO_LONG` — warns when `SKILL.md` exceeds 300 lines
- `REFERENCE_TOO_LONG` — info when a reference file exceeds 400 lines
- `NO_ROUTING_SECTION` — warns when a skill has no routing/dispatch section for AI agents
- `RULES_WITHOUT_EXAMPLES` — warns when ❌/✅ rules have no code example
- `DEEP_HEADING_NESTING` — info on H4+ headings that fragment context
- `CONTRADICTING_RULES` — cross-file contradiction detection: same backtick term marked ❌ in one file and ✅ in another; code fences stripped to avoid false positives
- Contradictions surfaced inline in the PR body, not just in the artifact

**PR description redesign**
- Replaced before→after change table with a plain-English summary built from AI `summary` fields
- Evolution badge rendered at top of PR body

### Fixes

- `fix(badge)` — badge insertion now targets the last `[![` badge line in the first 60 lines; previously matched `# comment` lines inside bash code blocks as headings
- `fix(ai_updater)` — AI patches are grounded in verified GitHub release dates; hallucinated versions are rejected before they reach the diff
- `fix(health)` — PR description rewritten to be concise and reviewer-friendly
- `fix(workflow)` — opens a new PR when a previous PR on the same branch was closed
- `fix(workflow)` — `tracked_deps` passed as bash array to prevent word-splitting
- `fix(workflow)` — secret renamed from `github_token` to `token` (reserved name collision)
- `fix(workflow)` — PR creation is non-fatal; surfaces a helpful message when Actions lacks PR permissions

### Other

- Default AI model bumped from `gpt-4o-mini` to `gpt-4o`
- OSS skill PR check workflow (`oss_skill_pr_check.yml`) for standalone skill repos
- README overhauled: simpler structure, evolution badge section, cleaner quick-start

---

## v0.1.9 — README rewrite

README restructured with clearer setup instructions and showcase section.

---

## v0.1.8 — OSS skill repo support

Added standalone mode (`--oss`) for skill repos where `SKILL.md` lives at the repository root.

---

## v0.1.1

Initial public release with trace recording, PR feedback collection, and health audit.
