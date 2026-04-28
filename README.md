# skills-evolution

AI skill governance for software teams — keep your AI guidance files accurate, up to date, and evolving automatically.

## Quick start (recommended)

Install the [GitHub Agentic Workflows](https://github.github.com/gh-aw/introduction/overview/) into your repository:

```bash
# Review skill files automatically on every PR that changes them
gh aw add sorunokoe/skills-evolution/workflows/skills-pr-check.md@latest
gh aw compile

# Auto-update skill files on a schedule (default: monthly — edit the frontmatter to change)
gh aw add sorunokoe/skills-evolution/workflows/skills-monthly-update.md@latest
gh aw compile
```

That's it. No Python, no YAML to maintain. The AI handles the rest.

**Changing the schedule:** After `gh aw add`, open `.github/workflows/skills-monthly-update.md` and edit the `schedule:` line — for example `schedule: weekly` or `schedule: daily around 9:00` — then `gh aw compile` to apply. Run it any time on demand with `gh aw run skills-monthly-update`.

See [`workflows/`](./workflows/) for the workflow source files.

---

## Advanced: reusable GitHub Actions workflows

If you prefer standard GitHub Actions (or gh-aw isn't available), use the reusable YAML workflows.
**The consumer's wrapper fully controls the trigger** — set any cron or add `workflow_dispatch`.

### PR skill review

```yaml
# .github/workflows/skills-pr-check.yml
name: Skills PR Check
on:
  pull_request:
    paths: [".github/skills/**", ".claude/skills/**"]
permissions:
  contents: read
  pull-requests: write
jobs:
  check:
    uses: sorunokoe/skills-evolution/.github/workflows/skills_pr_check.yml@latest
    with:
      tools_ref: latest
      tech_stack: ""        # optional: describe your project's stack
    secrets:
      copilot_token: ${{ secrets.COPILOT_TOKEN }}
```

### Skill update — consumer controls the schedule

```yaml
# .github/workflows/skills-health.yml
name: Skills Health
on:
  schedule:
    - cron: "0 3 1 * *"   # ← change to any frequency you like
  workflow_dispatch:        # ← always available for on-demand runs
permissions:
  contents: write
  pull-requests: write
  models: read
jobs:
  health:
    uses: sorunokoe/skills-evolution/.github/workflows/skills_health.yml@latest
    with:
      tools_ref: latest
      enable_ai_skill_update: true
    secrets:
      token: ${{ secrets.GITHUB_TOKEN }}
```

---

## For open-source skill maintainers

If you publish a standalone AI skill repository (like [swift-kmp-skill](https://github.com/sorunokoe/swift-kmp-skill)),
skills-evolution can keep your skill files current using the `--oss` flag, which expects:

```
SKILL.md          ← at the repository root
references/       ← optional: all *.md files here are treated as authoritative skill content
```

### GitHub Actions (recommended)

```yaml
# .github/workflows/skill-health.yml
name: Skill Health
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
    uses: sorunokoe/skills-evolution/.github/workflows/oss_skill_health.yml@latest
    with:
      tools_ref: latest
      enable_ai_skill_update: true   # optional: AI version-patch pass
    secrets:
      github_token: ${{ secrets.GITHUB_TOKEN }}
```

The workflow audits `SKILL.md` and `references/*.md`, optionally patches version references
via GitHub Models, and opens a PR if any changes are needed.

### PR check for skill PRs

Copy [`workflows/oss-skill-pr-check.md`](workflows/oss-skill-pr-check.md) into your skill repo
and install it with `gh aw add`:

```bash
gh aw add oss-skill-pr-check
```

This triggers on PRs that change `SKILL.md` or `references/**` and posts a concise AI review
comment covering accuracy, clarity, scope, and anti-pattern coverage.

### gh-aw agentic workflow

Copy [`workflows/oss-skill-update.md`](workflows/oss-skill-update.md) into your skill repo
and install it with `gh aw add`:

```bash
# From your skill repo root:
gh aw add oss-skill-update
```

### CLI — manual run

```bash
# Audit only
python3 -m skills_evolution.health audit --repo-root . --output-dir outputs --oss

# Audit + AI version patches
python3 -m skills_evolution.ai_updater --repo-root . --output-dir outputs --oss
```

### What changes in OSS mode

| Behaviour | Default mode | `--oss` mode |
|-----------|-------------|--------------|
| Skill files discovered | `.github/skills/*/SKILL.md` | `SKILL.md` at root |
| Markdown files audited | All `*.md` under skill dir | `SKILL.md` + `references/*.md` only |
| Skill identity | Folder name | `name:` from frontmatter |
| Name/folder mismatch check | ✅ | ❌ (CI checkout dir is arbitrary) |
| Registry drift check | ✅ | ❌ (no copilot-instructions.md expected) |
| Feedback collection | ✅ | ❌ (OSS PRs are maintainer edits, not usage signals) |
| Missing `SKILL.md` | Silent (0 skills) | Error finding emitted |

---

## What it does

| Feature | How |
|---|---|
| PR review | Reads changed `SKILL.md` files, posts a concise review comment |
| Monthly update | Discovers library versions from package manager files, patches outdated skill content, opens a PR |
| Multi-ecosystem | Reads `Package.resolved`, `go.mod`, `Cargo.lock`, `pubspec.yaml`, `package.json` |
| Multi-agent | Finds skills in both `.github/skills/` and `.claude/skills/` |
| Path safety | Only patches files inside `*/skills/*/SKILL.md` — never arbitrary files |

## Skill file location

Skills are discovered from:
- `.github/skills/<skill-name>/SKILL.md` — visible to Copilot, Xcode, and other GitHub-aware agents
- `.claude/skills/<skill-name>/SKILL.md` — Claude-specific skills

If the same skill name exists in both locations, `.github/skills/` takes priority.

---

## Python package

For programmatic use or CI without gh-aw:

```bash
pip install skills-evolution
```



## Install

```bash
pip install -e .
```

Or run directly from source:

```bash
PYTHONPATH=src python3 -m skills_evolution.cli --help
PYTHONPATH=src python3 -m skills_evolution.mcp_server
PYTHONPATH=src python3 -m skills_evolution.health --help
PYTHONPATH=src python3 -m skills_evolution.semantic --help
```

## Trace CLI

### Record a trace

```bash
skills-evolution write \
  --repo-root /path/to/repo \
  --skill swiftui-standards \
  --file .github/skills/swiftui-standards/references/state-management.md \
  --section-id tca-store-ownership \
  --line-start 1 \
  --line-end 34 \
  --reason "Used ownership rule for StoreOf wrapper choice" \
  --confidence 0.86
```

### Publish traces to the current branch PR

Useful after `gh pr create` or `gh pr edit`.

```bash
skills-evolution publish --repo-root /path/to/repo
```

By default it auto-detects:

- PR context from `gh pr view` when available
- otherwise repo from `origin`
- otherwise current branch open PR via the GitHub API
- auth from `GH_TOKEN`, `GITHUB_TOKEN`, or `gh auth token`

### Fallback: move committed traces into the PR body

Used by GitHub Actions when the PR was opened on GitHub.com.

```bash
skills-evolution fallback \
  --repo owner/repo \
  --pr-number 123 \
  --token "$GITHUB_TOKEN"
```

## MCP tools

The server exposes:

- `record_skill_trace`
- `publish_skill_traces_to_pr`

Start it with:

```bash
skills-evolution-mcp
```

## Health toolkit

### Structural audit / feedback / combine

```bash
skills-evolution-health audit --repo-root /path/to/repo --output-dir outputs
skills-evolution-health collect-feedback --repo owner/repo --token "$GITHUB_TOKEN" --output outputs/skills-feedback-raw.json
skills-evolution-health feedback --repo-root /path/to/repo --raw outputs/skills-feedback-raw.json --output-dir outputs
skills-evolution-health combine --output-dir outputs
```

The feedback step uses **normal PR comments/reviews as the primary monthly signal** and treats structured tags such as `skill-miss:` or trace-linked `skill-verdict:` comments as optional stronger evidence.

### Optional semantic pass

```bash
skills-evolution-semantic-pass \
  --repo-root /path/to/repo \
  --output-dir outputs \
  --copilot-token "$COPILOT_TOKEN"
```

## Shared GitHub integration

### Composite action

This repository includes a composite action at `action.yml`.

Example:

```yaml
name: Skills Trace Fallback Capture

on:
  pull_request_target:
    types: [opened, edited, reopened, synchronize]

permissions:
  contents: write
  pull-requests: write

jobs:
  publish-traces:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: sorunokoe/skills-evolution@v0.1.1
        with:
          repo: ${{ github.repository }}
          pr-number: ${{ github.event.pull_request.number }}
          token: ${{ secrets.GITHUB_TOKEN }}
```

### Reusable workflows

For published use, clients should keep only tiny wrappers and call the shared workflows:

```yaml
jobs:
  skills-health:
    uses: sorunokoe/skills-evolution/.github/workflows/skills_health.yml@v0.1.1
    with:
      since_days: "35"
      auto_fix_metadata_links: true
      enable_ai_review: false
      open_pr_on_findings: false
      task_number: ${{ vars.SKILLS_HEALTH_TASK_NUMBER }}
    secrets:
      github_token: ${{ secrets.GITHUB_TOKEN }}
      copilot_token: ${{ secrets.COPILOT_TOKEN }}
```

```yaml
jobs:
  fallback-traces:
    uses: sorunokoe/skills-evolution/.github/workflows/skills-trace-capture.yml@v0.1.1
    with:
      repo: ${{ github.repository }}
      pr_number: ${{ github.event.pull_request.number }}
    secrets:
      github_token: ${{ secrets.GITHUB_TOKEN }}
```

## Ideal repo integration

The maintainable end state is:

1. Repositories **do not copy** the Python trace or health logic.
2. Local developers and agents use the installed `skills-evolution` CLI or the MCP server.
3. Repositories keep only tiny workflow wrappers that reference the shared reusable workflows/actions.

That keeps `skills-evolution` as the single source of truth for:

- trace schema
- PR-body block format
- publish/fallback behavior
- MCP tool surface
- health audit/feedback logic
- optional semantic-review orchestration

If a repository currently carries local copies of the transport scripts, treat them as transitional test glue and delete them once the published package/action is available.

## Smoke test

```bash
PYTHONPATH=src python3 -m skills_evolution.cli write \
  --repo-root /path/to/your/repo \
  --skill my-skill \
  --file .github/skills/my-skill/SKILL.md \
  --section-id section-id \
  --line-start 1 \
  --line-end 5 \
  --reason "Smoke test"
```

---

## Skills using skills-evolution

Open-source AI skill repos governed by this toolkit:

| Skill | What it covers | Repo |
|-------|---------------|------|
| **swift-kmp** | KMP ↔ Swift bridge patterns — interactors, `SkieSwiftFlow` → `AsyncStream`, type mapping, `KotlinThrowable` containment | [sorunokoe/swift-kmp-skill](https://github.com/sorunokoe/swift-kmp-skill) |
| **swiftui-compose** | Bidirectional Compose Multiplatform ↔ SwiftUI interop — `UIViewControllerRepresentable`, coordinator, state sharing | [sorunokoe/swiftui-compose-skill](https://github.com/sorunokoe/swiftui-compose-skill) |

> Maintaining an open-source skill? Add the `maintained by skills-evolution` badge and the OSS workflows — see [For open-source skill maintainers](#for-open-source-skill-maintainers) above.
