# claude-harness

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![ci](https://github.com/Arif-Karim/claude-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/Arif-Karim/claude-harness/actions/workflows/ci.yml)

A Claude Code plugin that lets AI agents ship fast without breaking things. It installs a
proven delivery harness into your project and adapts it to your stack:

- an orchestrator, builder and reviewer workflow;
- a hook that blocks risky commands;
- a merge gate;
- CI;
- branch protection.

> **Status: Phase 0.** The contract, plugin layout, guard hook and templates are in place.
> Parallel lanes are filling in the `harness-init` skill, the pr-ready tests, the adversarial
> guard suite and the catch-log summariser. See [`docs/contract.md`](docs/contract.md).

## Install

In Claude Code, inside the project you want to harness:

```
/plugin marketplace add Arif-Karim/claude-harness
/plugin install agent-harness@claude-harness
set up the harness
```

The marketplace is `claude-harness` and the plugin is `agent-harness`. Claude Code reserves
plugin names that start with `claude-`. The last line triggers the `harness-init` skill
(you can also run `/agent-harness:harness-init`). It detects your stack, asks a few questions,
and opens a PR with the harness.

## What you get

- **Guard hook.** A PreToolUse hook for Bash. Builder agents cannot:
  - merge PRs;
  - push to the default branch;
  - force-push, **including `--force-with-lease`**;
  - push tags or cut releases;
  - run workflows;
  - change repo settings or secrets;
  - write through `gh api`.

  It also sees through wrappers such as `bash -c`, `eval` and `$(...)`. It fails closed: bad
  input, a broken config or a missing interpreter all block. The orchestrator opts in per
  command with a visible `HARNESS_ORCHESTRATOR=1` prefix.
- **Merge gate** (`pr-ready.sh`). A PR is ready only when all of these hold:
  - every check passes, including the required ones;
  - there are 0 code-scanning alerts on the PR's merge **and** head refs;
  - there are 0 unresolved bot threads;
  - a reviewer independent of the builder has reviewed it;
  - a `## Review findings → fixes` comment was posted after the last review.

  `sensitive` changes also need a human approval. When the human and the agents share one
  GitHub account, the gate stops at exit 3 (NEEDS HUMAN) and the human merges those PRs
  themselves: see [docs/merge-gate.md](docs/merge-gate.md).
- **Risk tiers.** Path globs in `harness.config.json` map changes to `fast`, `standard` or `sensitive`.
- **CI per stack** (Go, TypeScript): lint, tests with a coverage gate, mutation testing
  (gremlins, Stryker), fuzz and property tests (Go fuzzing and rapid, fast-check), CodeQL,
  gitleaks and vulnerability scans. It all runs through one `scripts/verify.sh`, which mirrors CI locally.
- **Branch protection** with one script:
  - required checks;
  - enforce admins;
  - no force pushes;
  - conversation resolution.
- **Workflow rules** in your `CLAUDE.md`:
  - test-first commits (red → green);
  - one lane per PR;
  - one batched review;
  - a findings → fixes comment before every merge;
  - merge commits only.
- **Catch log.** One row per bug, recording which safety layer caught it and which should have.
- **Headless e2e template.** A `claude -p` job in CI that checks the guard holds against a real agent.

## How it works

The plugin ships the `harness-init` skill, the templates in `harness/`, and a plugin-level guard
that is active only in projects with a `harness.config.json`. The skill copies and adapts the
templates into your repo, so CI and teammates without the plugin are covered too. Why a plugin
and not a template repo: [ADR 0001](docs/adr/0001-plugin-plus-template.md).

## Develop

```
scripts/verify.sh
```

This runs shellcheck, the Python tests, the guard table test, `claude plugin validate --strict`
and gitleaks. Agent rules for this repo are in [`.claude/CLAUDE.md`](.claude/CLAUDE.md).

## License

[MIT](LICENSE)
