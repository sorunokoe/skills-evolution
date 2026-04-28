---
on:
  pull_request:
    types: [opened, edited, reopened, synchronize]
    paths:
      - "SKILL.md"
      - "references/**"

skip-bots: true
description: "Reviews changed skill guidance files in pull requests for open-source skill repositories"
source: "sorunokoe/skills-evolution/workflows/oss-skill-pr-check.md@main"
labels: ["skill"]

tools:
  github:
    toolsets: [pull-requests]
  bash: ["git", "cat", "head", "find"]

safe-outputs:
  add-comment:
---

# OSS Skill File Reviewer

Find all skill content files changed in this pull request:

```bash
git diff --name-only origin/${{ github.event.pull_request.base.ref }}...HEAD -- 'SKILL.md' 'references/*.md'
```

Read each changed file and review it for:

1. **Accuracy** — does the guidance reflect current best practices for the libraries or patterns mentioned?
2. **Clarity** — are the instructions actionable and unambiguous?
3. **Scope** — is the "When to use" section precise (not too broad, not too narrow)?
4. **Anti-patterns** — are the most common mistakes clearly listed?
5. **Both ✅ and ❌ examples** — skill files must show both correct and incorrect patterns.

Add a concise comment to this pull request with your findings.
If all files look good, say so briefly. Only flag genuine issues.
