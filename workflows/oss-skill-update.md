---
# Schedule: change to any frequency that suits your release cadence.
# Examples: "monthly", "weekly", "every 2 weeks"
# workflow_dispatch lets you trigger a run manually at any time via:
#   gh aw run oss-skill-update
on:
  schedule: monthly
  workflow_dispatch:
skip-bots: true
description: "Monthly review and update of an open-source AI skill against current library versions and documentation"
source: "sorunokoe/skills-evolution/workflows/oss-skill-update.md@main"
labels: ["skill", "maintenance"]

tools:
  github:
    toolsets: [pull-requests, files]
  bash: ["find", "cat", "head", "gh", "git", "python3"]

safe-outputs:
  create-pull-request:
---

# Monthly OSS Skill Update

Review and update the skill files in this open-source skill repository:
`SKILL.md` at the root and all files under `references/`.

## 1 — Identify tracked dependencies

Read `SKILL.md` to find which frameworks, libraries, or tools this skill covers.
Look for version numbers pinned inside any skill file:

```bash
grep -rn "[0-9]\+\.[0-9]" SKILL.md references/ 2>/dev/null | head -40
```

## 2 — Check latest releases

For each GitHub-hosted library mentioned, look up the latest published release:

```bash
gh api repos/{owner}/{repo}/releases/latest --jq '.tag_name'
```

Also check official documentation URLs referenced in the skill files to verify
they are still live and accurate.

## 3 — Review and update skill files

Files in scope:
- `SKILL.md`
- `references/*.md` (all files)

Files **not** in scope (do not modify):
- `README.md`, `AGENTS.md`, `CONTRIBUTING.md`, `CHANGELOG.md`

For each in-scope file:
- Update version numbers where you can confirm the change from release data
- Fix any documentation links that have moved or are no longer accurate
- Apply conservative inline edits only — do not restructure sections

## 4 — Create pull request

If any files were changed, create a pull request:

- **Title**: `chore(skill): monthly skill update`
- **Body**: a concise bullet list of what was updated and which version/docs evidence drove each change

If nothing needed updating, do nothing.
