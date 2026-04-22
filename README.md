# skills-evolution

`skills-evolution` is the shared toolkit for trace-based AI skill governance.

It packages five pieces:

1. **Trace CLI** for writing local trace records and publishing them into a PR body.
2. **MCP server** so agents can record/publish traces through standard tools.
3. **Health toolkit** for structural audit, trace/verdict aggregation, and summary generation.
4. **Optional semantic pass** for disputed sections only.
5. **Shared GitHub Action/workflow assets** for GitHub-web PR fallback and monthly health automation.

## Design

The default path is intentionally simple:

1. AI records exact skill usage into `.github/.skill-trace.ndjson` locally.
2. After the PR exists, the CLI publishes those traces into the hidden PR-body block.
3. Reviewers judge exact trace IDs in comments.

The only unavoidable edge case is a PR opened on GitHub.com after traces were created on a laptop. In that flow, some pushed artifact is required. The fallback action solves that by consuming a force-added `.github/.skill-trace.ndjson` and moving it into the PR body.

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
      - uses: sorunokoe/skills-evolution@v0.1.0
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
    uses: sorunokoe/skills-evolution/.github/workflows/skills_health.yml@v0.1.0
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
    uses: sorunokoe/skills-evolution/.github/workflows/skills-trace-capture.yml@v0.1.0
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

## Smoke test against GolfApp

```bash
PYTHONPATH=src python3 -m skills_evolution.cli write \
  --repo-root /Users/yesa/Documents/Projects/Trackman/GolfApp \
  --skill swiftui-standards \
  --file .github/skills/swiftui-standards/references/state-management.md \
  --section-id tca-store-ownership \
  --line-start 1 \
  --line-end 5 \
  --reason "Smoke test"
```
