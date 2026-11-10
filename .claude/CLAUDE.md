# claude-harness: agent instructions

This repo publishes the **agent-harness** Claude Code plugin from the **claude-harness**
marketplace. The plugin's `harness-init` skill copies `harness/` into target projects.
**`docs/contract.md` is the source of truth**; read it before changing anything.

(These instructions live in `.claude/CLAUDE.md`, not the root, because a root CLAUDE.md
in a plugin triggers a `claude plugin validate` warning.)

## Layout

| Path | Purpose |
|---|---|
| `.claude-plugin/` | `plugin.json` (name `agent-harness`) + `marketplace.json` (name `claude-harness`, source `./`) |
| `skills/harness-init/` | the installer skill |
| `hooks/hooks.json` | plugin-level guard (`--require-config`) |
| `scripts/guard-bash.py` | canonical guard; copy to `harness/core/hooks/` after edits |
| `harness/core/`, `harness/stacks/{go,ts}/` | files shipped into target projects |
| `tests/` | table test + python unittests for the harness itself |
| `docs/contract.md`, `docs/adr/` | contract and decisions |

## Commands

- `scripts/verify.sh`: everything CI runs (shellcheck, unittests, guard table test,
  manifest checks, `claude plugin validate --strict`, gitleaks). **Must pass before you open a PR.**
- `tests/guard-bash_test.sh`: the guard table test alone.
- `tests/adversarial/run.py`: the adversarial guard suite (`tests/adversarial/cases.tsv`); its accepted
  limits and their backstops are in `docs/threat-model.md`.
- `python3 harness/core/scripts/risk-tier.py --config harness.config.json < files`: tier of a change.

## Engineering rules

1. **TDD.** Commit the failing test (`test(x): ... (red)`), then the fix (`feat(x): ... (green)`).
   Every guard change adds table-test rows for the case it fixes.
2. Python: stdlib only, 3.9+. Shell: bash, `set -euo pipefail`, shellcheck-clean.
3. Edit the canonical file, then copy: `scripts/guard-bash.py` → `harness/core/hooks/guard-bash.py`,
   `tests/guard-bash_test.sh` → `harness/core/hooks/guard-bash_test.sh`,
   `.github/pull_request_template.md` → `harness/core/github/pull_request_template.md`.
   A test fails if they differ.
4. Keep `harness/` generic: no project, company or personal names, IDs or paths.
5. Fail closed. Any harness component that cannot decide must block or report NOT READY.
6. No secrets or personal data anywhere.

## Workflow (orchestrator / builder / reviewer)

- Branch `lane-<letter>-<slug>` from latest `origin/main`, one lane per PR. Conventional commits,
  each ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- PR body starts with `<!-- harness:builder id=<lane> -->`, follows `.github/pull_request_template.md`
  and ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- A reviewer with fresh context posts **one** review containing `<!-- harness:reviewer id=<id> -->`.
- After fixing, the builder posts **one** comment starting `## Review findings → fixes`, mapping each
  finding to a commit sha or a reason.
- Merge gate: `harness/core/scripts/pr-ready.sh <n>` (exit 0 READY, 1 NOT READY, 3 NEEDS HUMAN).
  Merge commits only.
- `main` is protected: required checks `verify` and `gitleaks`, enforce_admins, no force pushes,
  conversation resolution.
- `.claude/settings.json` runs `scripts/guard-bash.py` on every Bash call. Builders cannot merge,
  push to `main`, force-push (including `--force-with-lease`), push tags, cut releases, run workflows
  or write through `gh api`. Only the orchestrator uses the `HARNESS_ORCHESTRATOR=1` prefix.
- If `main` moved: rebase your lane locally and push it as a new branch, or ask the orchestrator.
