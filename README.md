<div align="center">

# skills-evolution

**Keep your AI skill files accurate, up to date, and evolving — automatically.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)

[Overview](#overview) · [Quick start](#quick-start) · [Team setup](#setup-for-teams) · [Standalone skill repos](#setup-for-standalone-skill-repos) · [Showcase](#showcase)

</div>

---

## Overview

**skills-evolution** automatically maintains AI skill guidance files in your repositories. It keeps `.github/skills/` and `.claude/skills/` directories up to date by:

- **Detecting version drift** — Scans your package manifests (`package.json`, `go.mod`, `Cargo.lock`, etc.) and finds outdated library versions referenced in your skills
- **Patching stale guidance** — Uses Claude AI to update version references and modernize best practices once per month, then opens a PR for your review
- **Reviewing skill changes** — On every PR touching a skill file, posts an AI-powered review checking for accuracy, scope issues, and common anti-patterns
- **Auditing structure** — Validates YAML frontmatter, detects broken internal links, and auto-fixes what it can safely repair

Everything runs on a schedule or on-demand — no local setup required. Skills stay fresh and AI agents get better guidance.

---

## What gets maintained

Skill files are discovered in two standard locations:

- **`.github/skills/<name>/SKILL.md`** — For GitHub Copilot, Xcode, and other GitHub-aware AI agents
- **`.claude/skills/<name>/SKILL.md`** — For Claude-specific guidance

Each skill directory can include supplementary markdown files (references, examples, tutorials, etc.). The tool automatically detects and maintains all of them.

---

## Quick start

Choose one of these integration methods. Both do the same thing — pick what fits your workflow best.

---

## Setup for teams

### Option 1: Copy workflow files (Recommended — 2 minutes)

Add these two workflows to your `.github/workflows/` directory:

**Step 1: Create `.github/workflows/skills-pr-check.yml`**

```yaml
name: Skills PR Review
on:
  pull_request:
    paths: [".github/skills/**", ".claude/skills/**"]
permissions:
  contents: read
  pull-requests: write
jobs:
  review:
    uses: sorunokoe/skills-evolution/.github/workflows/skills_pr_check.yml@latest
    with:
      tech_stack: ""   # optional: e.g. "Swift, KMP, SwiftUI"
    secrets:
      copilot_token: ${{ secrets.COPILOT_TOKEN }}
```

This workflow reviews your skill changes on every PR.

**Step 2: Create `.github/workflows/skills-health.yml`**

```yaml
name: Monthly Skill Update
on:
  schedule:
    - cron: "0 3 1 * *"   # runs on the 1st of each month at 3am UTC
  workflow_dispatch:        # or trigger manually anytime
permissions:
  contents: write
  pull-requests: write
  models: read
jobs:
  health:
    uses: sorunokoe/skills-evolution/.github/workflows/skills-health.yml@latest
    with:
      enable_ai_skill_update: true
    secrets:
      token: ${{ secrets.GITHUB_TOKEN }}
```

This workflow checks for version drift and opens a PR with updates once per month. **Change the schedule by editing the `cron` line above** (or use `workflow_dispatch` to run manually anytime).

That's it! Commit these files and you're done.

### Option 2: Use GitHub's automation tools

If your team already uses GitHub's automation platform (like gh-aw):

```bash
# Add PR review workflow
gh aw add sorunokoe/skills-evolution/workflows/skills-pr-check.md@latest
gh aw compile

# Add monthly update workflow
gh aw add sorunokoe/skills-evolution/workflows/skills-monthly-update.md@latest
gh aw compile
```

---

## Setup for standalone skill repos

Publishing a standalone skill repository (like [swift-kmp-skill](https://github.com/sorunokoe/swift-kmp-skill))? 

This tool has a **standalone mode** for repos where:
- A single `SKILL.md` file lives at the repository root
- Supporting content goes in `references/` and `examples/` directories
- You want the same version tracking and AI reviews, but for a single published skill

### Required GitHub repository settings

Before the monthly health workflow can open PRs automatically, enable this in **each skill repo**:

> **Settings → Actions → General → Workflow permissions**  
> ✅ Check **"Allow GitHub Actions to create and approve pull requests"**

Without this, the workflow still runs and pushes the update branch — but you'll need to open the PR manually.

### Integration

**Using GitHub Actions workflows:**

```yaml
# .github/workflows/skill-pr-review.yml
name: Skill Review
on:
  pull_request:
    paths: ["SKILL.md", "references/**", "examples/**"]
permissions:
  contents: read
  pull-requests: write
jobs:
  review:
    uses: sorunokoe/skills-evolution/.github/workflows/oss_skill_pr_check.yml@latest
    secrets:
      copilot_token: ${{ secrets.COPILOT_TOKEN }}
```

```yaml
# .github/workflows/skill-update.yml
name: Monthly Skill Health Check
on:
  schedule:
    - cron: "0 3 1 * *"
  workflow_dispatch:
permissions:
  contents: write
  pull-requests: write
  models: read
jobs:
  health:
    uses: sorunokoe/skills-evolution/.github/workflows/oss-skill-health.yml@latest
    with:
      enable_ai_skill_update: true
    secrets:
      token: ${{ secrets.GITHUB_TOKEN }}
```

### Differences from team mode

| Aspect | Team repos | Standalone skill repos |
|--------|-----------|------------------------|
| **Skill files** | `.github/skills/*/SKILL.md` | `SKILL.md` at root |
| **Content audited** | All `.md` files in skill directories | `SKILL.md` + `references/` + `examples/` |
| **Skill identity** | Determined by folder name | Determined by `name:` field in frontmatter |
| **Version detection** | From package manifests in repo | From package manifests in repo |
| **PR feedback** | Comprehensive audit reports | Simplified reviews (you maintain it directly) |
| **Target use case** | Multi-skill monorepos | Single published skill + references |

---

## Reference

### Python package

```bash
pip install skills-evolution
```

Entry points: `skills-evolution`, `skills-evolution-health`, `skills-evolution-ai-update`, `skills-evolution-mcp`, `skills-evolution-semantic-pass`.

Run from source:

```bash
PYTHONPATH=src python3 -m skills_evolution.cli --help
PYTHONPATH=src python3 -m skills_evolution.health --help
```

### CLI: Record skill usage traces

Track which skill sections an agent used while solving a task:

```bash
skills-evolution write \
  --repo-root /path/to/repo \
  --skill swiftui-standards \
  --file .github/skills/swiftui-standards/references/state-management.md \
  --section-id tca-store-ownership \
  --line-start 1 --line-end 34 \
  --reason "Used ownership rule for StoreOf wrapper choice" \
  --confidence 0.86

# Publish traces to the current branch's PR
skills-evolution publish --repo-root /path/to/repo
```

### CLI: Health and audit toolkit

```bash
# Structural audit (validates frontmatter, checks links)
skills-evolution-health audit --repo-root . --output-dir outputs

# Collect PR feedback signals from recent PRs
skills-evolution-health collect-feedback \
  --repo owner/repo --token "$GH_TOKEN" --output outputs/raw.json

# Analyze feedback into actionable proposals
skills-evolution-health feedback \
  --repo-root . --raw outputs/raw.json --output-dir outputs

# Combine all reports into a summary
skills-evolution-health combine --output-dir outputs
```

### MCP server

Expose skill tracing as a Model Context Protocol server for Claude:

```bash
skills-evolution-mcp
```

Provides: `record_skill_trace`, `publish_skill_traces_to_pr`.

### Optional: AI semantic review pass

Run an additional semantic analysis pass over your skills:

```bash
skills-evolution-semantic-pass \
  --repo-root . \
  --output-dir outputs \
  --copilot-token "$COPILOT_TOKEN"
```

---

## Showcase

Standalone skill repositories maintained by skills-evolution:

| Skill | What it covers | Repository |
|-------|---------------|-----------|
| **swift-kmp** | KMP ↔ Swift bridge patterns — interactors, `SkieSwiftFlow` → `AsyncStream`, type mapping, `KotlinThrowable` containment | [sorunokoe/swift-kmp-skill](https://github.com/sorunokoe/swift-kmp-skill) |
| **swiftui-compose** | Bidirectional Compose Multiplatform ↔ SwiftUI interop — `UIViewControllerRepresentable`, coordinator, state sharing | [sorunokoe/swiftui-compose-skill](https://github.com/sorunokoe/swiftui-compose-skill) |

**Maintaining a published skill?** Add the `maintained by skills-evolution` badge and workflows — see [Setup for standalone skill repos](#setup-for-standalone-skill-repos) above.

---

## Contributing

Open an issue or pull request. Tests live in `tests/` — run with `python3 -m pytest tests/`.

## License

[MIT](LICENSE)
