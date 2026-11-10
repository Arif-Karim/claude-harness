---
name: harness-init
description: Set up the agent harness in the current project (guard hook, merge gate, CI, branch protection, orchestrator/builder/reviewer workflow) adapted to its stack. Use when the user says "set up the harness", "install the harness", "add guardrails for agents", or asks to add the agentic delivery workflow to a repo.
---

# harness-init

Status: Phase 0 stub. The steps below are the contract (see `docs/contract.md`,
section "harness-init"); lanes fill in the details.

Source files live in `${CLAUDE_PLUGIN_ROOT}/harness/`: `core/` is
language-agnostic, `stacks/<stack>/` holds per-stack fragments.

## Steps

1. **Detect** the stack(s) (`go.mod` → go, `package.json` + `tsconfig.json` → ts), the
   default branch (`gh repo view --json defaultBranchRef`) and the GitHub remote.
2. **Confirm** with the user: stacks, default branch, risk-tier globs, required checks.
3. **Write `harness.config.json`** at the project root (schema in `docs/contract.md`).
4. **Copy core**: guard hook, `.claude/settings.json` hook wiring (merge, never clobber),
   `CLAUDE.md` from `CLAUDE.md.tmpl` (append a section if one exists), PR template,
   `pr-ready.sh`, `risk-tier.py`, `branch-protection.sh`, `catch-log.md`.
5. **Copy stack fragments**: `verify` steps, CI workflow, mutation config, fuzz/property
   test templates.
6. **Run** `scripts/verify.sh` and the guard table test; fix until green.
7. **Commit on a branch and open a PR**; never push to the default branch.
8. **After merge**, the orchestrator runs
   `HARNESS_ORCHESTRATOR=1 scripts/branch-protection.sh` (asks the user first).
9. **Report** what was installed and what still needs the user (secrets, approvals).
