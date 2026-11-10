---
name: harness-init
description: Set up the agent harness in the current project (guard hook, merge gate, CI, branch protection, orchestrator/builder/reviewer workflow) adapted to its stack. Use when the user says "set up the harness", "install the harness", "upgrade the harness", "add guardrails for agents", or asks to add the agentic delivery workflow to a repo.
---

# harness-init

Installs (or upgrades) the agent harness in the project you are working in: the Bash guard hook,
`scripts/verify.sh` with the Go/TS stack checks, CI and CodeQL workflows, the `pr-ready.sh`
merge gate, risk tiers, the catch log and the CLAUDE.md workflow rules. Contract:
`docs/contract.md` §3 and §14 in the plugin repo.

- Plugin files: `${CLAUDE_PLUGIN_ROOT}/harness/` (`core/` plus `stacks/go`, `stacks/ts`).
- The deterministic file work is done by **`${CLAUDE_PLUGIN_ROOT}/harness/core/scripts/install.sh`**.
  Do not copy or merge harness files by hand; run the script so that every install is the same
  and re-runs are clean. `install.sh --help` lists every flag.
- Your job is everything around it: preflight, asking the user, git, running verify, and the report.

**Idempotent.** Running this skill again upgrades the project to the plugin's current version:
harness-owned files are replaced, the CLAUDE.md managed block is replaced in place, the guard
hook in `.claude/settings.json` is added once (or refreshed), `harness.config.json` keeps the
user's edits, and files the project owns (catch log, `.golangci.yml`, `stryker.config.json`, a kept
PR template, its own CI) are never touched. Nothing is ever deleted. Always show the dry run first.

## Ground rules

- **Ask before overwriting anything.** Every write goes through a `--dry-run` that the user sees first.
- Never write on the default branch, never push to it, never merge. Work on `harness/init`.
- Never change repo settings (branch protection, secrets) without an explicit yes for that action.
- Don't lower `coverageMin`/`mutationMin` silently. Propose a number, say why, let the user choose.
- Never generate tests for code you have not read.
- Stop and ask whenever a step fails in a way this file does not cover.

## Steps

### 1. Preflight and detect

Run these and stop with a clear message if any fails:

```bash
git rev-parse --show-toplevel           # inside a git repo; cd to its root
git remote -v | grep -i github.com      # a GitHub remote
gh auth status                          # gh is logged in
command -v python3 jq git               # required tools
git status --porcelain                  # must be empty: if not, stop and ask
```

Then detect:

```bash
${CLAUDE_PLUGIN_ROOT}/harness/core/scripts/install.sh --detect --target .
# {"stacks":["go","ts"],"goDir":"backend","tsDir":"web"}
gh repo view --json defaultBranchRef -q .defaultBranchRef.name
ls harness.config.json CLAUDE.md .claude/settings.json .github/pull_request_template.md \
   .github/workflows/ 2>/dev/null
```

- Stacks: `go.mod` → go; `package.json` with `tsconfig.json` or a `typescript` dependency → ts.
  The shallowest match wins; if the repo has several Go modules or TS packages, list them and ask
  which one the harness should gate (`--go-dir`, `--ts-dir`).
- Look for sensitive paths to add as risk-tier rules: `migrations/`, `auth/`, `payments/`,
  `crypto/`, infra (`terraform/`, `deploy/`, `helm/`), and anything that handles secrets.
- If `harness.config.json` exists this is an **upgrade**: read it, and treat its values as the
  user's answers unless they ask to change them.

### 2. Propose, then ask once

In **one** message, propose and ask the user to confirm or edit:

1. stacks and their dirs;
2. the default branch;
3. required checks (default `verify`, `mutation`, `gitleaks`; `verify`, `gitleaks` with no Go/TS);
4. risk-tier rules: the template's (`docs/**` and `**/*.md` fast; `.github/**`, `.claude/**`,
   `scripts/hooks/**`, `harness.config.json`, `**/auth/**`, `**/migrations/**` sensitive), plus the
   sensitive paths you found;
5. coverage and mutation thresholds (default 70 / 60);
6. whether to add the opt-in headless Claude Code e2e workflow (needs the `ANTHROPIC_API_KEY`
   secret; runs nightly and on demand; see `${CLAUDE_PLUGIN_ROOT}/harness/core/e2e/README.md`);
7. whether to set up the dev tools: Go `tools/go.mod` (golangci-lint, govulncheck, gremlins) and
   npm dev deps (eslint, vitest, @vitest/coverage-v8, fast-check, @stryker-mutator/core,
   @stryker-mutator/vitest-runner), as the project needs.

Then run the dry run and show its output (status per file, diffs for updates):

```bash
${CLAUDE_PLUGIN_ROOT}/harness/core/scripts/install.sh --target . --default-branch <B> --stacks <go,ts> [--go-dir D] [--ts-dir D] \
  --coverage-min <N> --mutation-min <N> [--required-checks a,b,c] [--e2e] --dry-run
```

- `conflict <path>` (exit 4): on a first install a file the harness owns already exists with other
  content (for example the project's own `scripts/verify.sh`). Show `--dry-run --force` diffs for
  those files and ask. Only with a yes, add `--force`. Otherwise stop: the user must move their file.
- `skip .github/pull_request_template.md`: the project has its own. Ask whether to replace it
  (`--replace-pr-template`) or keep theirs and add the `<!-- harness:builder id=... -->` line
  and "Review findings → fixes" note to it yourself.
- A note that `ci.yml`/`codeql.yml` is the project's own means the harness workflow goes to
  `harness-ci.yml` / `harness-codeql.yml` beside it. If the note says the existing file already has a
  `verify`, `mutation` or `gitleaks` job, ask the user to rename theirs: check names must be unique.

Do not continue until the user says yes to the plan.

### 3. Branch

```bash
git switch -c harness/init        # on an upgrade: harness/upgrade-<date>
```

### 4. Write the config, copy core and stack packs, wire hooks, add CI

Run the same command without `--dry-run`. In one pass it:

- writes `harness.config.json` (from the template, or merged into the existing one) and validates
  it against the schema (`validate-config.py`; an invalid config is exit 2, nothing written);
- copies core: `scripts/hooks/guard-bash.py` + its table test, `scripts/verify.sh`,
  `scripts/pr-ready.sh`, `scripts/risk-tier.py`, `scripts/branch-protection.sh`, the PR template;
- copies the stack packs with names and paths adapted (`GO_DIR`/`TS_DIR` defaults, workflow paths):
  `scripts/verify.d/20-go.sh`, `scripts/mutation-go.sh`, `<goDir>/.golangci.yml`;
  `scripts/verify.d/30-ts.sh`, `scripts/mutation-ts.sh`, `<tsDir>/stryker.config.json`;
- wires the guard into `.claude/settings.json` (PreToolUse `Bash`, exec form, `onFailure: block`),
  keeping every other setting and hook;
- adds `.github/workflows/ci.yml` (jobs `verify`, `mutation`, `gitleaks`; several stacks are merged
  into one workflow) and `codeql.yml`, plus `claude-e2e.yml` with `--e2e`;
- adds the CLAUDE.md workflow rules as the managed block between `<!-- harness:begin -->` and
  `<!-- harness:end -->` (appended to an existing CLAUDE.md, replaced in place on re-runs);
- creates `docs/catch-log.md` (header only) if there is none;
- adds tool output (`coverage.out`, `.stryker-tmp/`, `reports/mutation/`) to `.gitignore`.

Then edit `harness.config.json` for the risk-tier rules the user agreed to beyond the template's
(append to `riskTiers.rules`; a rule can only raise a tier) and re-check it:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/harness/core/scripts/validate-config.py harness.config.json
```

Dev tools, only with the user's OK from step 2:

```bash
# Go, in <goDir>: pinned tools, run by verify via `go tool -modfile=tools/go.mod`
go get -modfile=tools/go.mod -tool github.com/golangci/golangci-lint/v2/cmd/golangci-lint@latest
go get -modfile=tools/go.mod -tool golang.org/x/vuln/cmd/govulncheck@latest
go get -modfile=tools/go.mod -tool github.com/go-gremlins/gremlins/cmd/gremlins@latest
# TS, in <tsDir>
npm i -D eslint typescript vitest @vitest/coverage-v8 fast-check @stryker-mutator/core @stryker-mutator/vitest-runner
```

The TS fragment uses the package's `lint`, `typecheck`, `test` and `build` npm scripts when they
exist, else `eslint .`, `tsc --noEmit` and `vitest run --coverage` with `COVERAGE_MIN`.

Commit in small steps, for example:
`chore(harness): config, guard hook and settings`, `chore(harness): verify and stack packs`,
`ci(harness): workflows`, `docs(harness): CLAUDE.md rules and catch log`.

### 5. Offer one fuzz or property test

Ask the user to name one pure function that parses, decodes, validates or handles untrusted input.
Read it first, then fill the matching template next to it:

- Go: `${CLAUDE_PLUGIN_ROOT}/harness/stacks/go/test-templates/fuzz_test.go.tmpl` (bytes/strings from
  outside: parsers, archive and path handling) or `property_test.go.tmpl` (structured inputs with a
  rule that always holds). When to use which: `.../go/test-templates/README.md`.
- TS: `${CLAUDE_PLUGIN_ROOT}/harness/stacks/ts/test-templates/property.test.ts.tmpl`.

Replace `{{PACKAGE}}`, `{{FUNC}}`, `{{MODULE}}` and swap the example properties for real ones.
Skip this step if the user declines.

### 6. Run verify

```bash
scripts/hooks/guard-bash_test.sh
scripts/verify.sh
```

- Fix anything the harness itself broke (paths, a missing tool you agreed to install).
- Pre-existing failures (lint debt, coverage below the threshold, vulnerable deps) are **not** yours
  to hide. Report them with the numbers, and propose either fixing them in follow-up PRs or a
  temporary threshold in `harness.config.json` with a dated note in the PR. The user decides.
- If `--mutation` tooling was installed, also run `scripts/verify.sh --mutation` once.

### 7. Push and open the PR

```bash
git push -u origin harness/init
gh pr create --title "chore: install agent harness" --body-file <file>
```

The body follows `.github/pull_request_template.md` and starts with
`<!-- harness:builder id=harness-init -->`. Include the verify tail, the risk tier
(`git diff --name-only origin/<B>...HEAD | scripts/risk-tier.py`, which is `sensitive` because the
PR touches `.github/**` and `.claude/**`), and the known gaps from step 6. Do not merge it.

### 8. Branch protection (after the human merges, and only with an explicit yes)

Branch protection is a repo setting change. Ask first, show the payload, then apply:

```bash
scripts/branch-protection.sh --dry-run                      # show the user exactly what changes
HARNESS_ORCHESTRATOR=1 scripts/branch-protection.sh         # only after the user says yes
```

The required checks must have run at least once on the default branch (the merge does that), or
GitHub will wait for checks that never report. If the user says no, record it in the report.

### 9. Summary

Print a short report:

- what was installed (the `install.sh` summary line and any `skip`/`conflict` decisions);
- stacks, dirs, default branch, required checks, thresholds, risk-tier rules added;
- verify result, and pre-existing failures with the agreed plan;
- the PR URL;
- what needs the user: merge the PR, approve branch protection, add `ANTHROPIC_API_KEY` if e2e
  was added, install tools that were declined;
- how to use it day to day: `scripts/verify.sh` before every PR, `scripts/pr-ready.sh <n>` as the
  merge gate, a `docs/catch-log.md` row for every bug fix, and re-run this skill to upgrade.

## Upgrading

Same steps. With an existing `harness.config.json`, `install.sh` needs no flags beyond `--target`
(the config supplies the stacks, branch and thresholds; installed fragments supply the dirs).
The dry run shows exactly which harness files change; nothing is a conflict on an upgrade, so read
the diffs with the user before writing. Local edits to harness-owned files are overwritten: move
project-specific checks into a new `scripts/verify.d/NN-<name>.sh` fragment, which the harness never
touches.
