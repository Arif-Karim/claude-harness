# Contract

This is the contract every lane builds against. If code and this file disagree, fix one
of them in the same PR. Change this file only in a PR of its own, marked `sensitive`.

Words used here:
- **This repo**: `claude-harness`, which publishes the plugin.
- **Target project**: a repo the harness is installed into.
- **Orchestrator / builder / reviewer / human**: the roles in `harness/core/CLAUDE.md.tmpl`.

---

## 1. Plugin format (confirmed against the docs, 2026-11-10)

Sources:
- code.claude.com/docs/en/plugins-reference (manifest reference)
- code.claude.com/docs/en/plugins/marketplace-reference
- code.claude.com/docs/en/plugin-marketplaces
- code.claude.com/docs/en/hooks

Checked with `claude plugin validate` on Claude Code 2.1.317.

| Fact | Consequence here |
|---|---|
| The manifest is `.claude-plugin/plugin.json`. Only `name` is required. Every other plugin dir (`skills/`, `commands/`, `agents/`, `hooks/`) sits at the plugin root, not inside `.claude-plugin/`. | Layout below. |
| **Plugin names starting with `claude-`, `anthropic-`, `anthropics-` or `cc-plugin-` are reserved.** `claude plugin validate` reports this as an error. | The plugin is **`agent-harness`**, not `claude-harness`. The repo and the marketplace keep the name `claude-harness`; marketplace names are not reserved this way (validated). |
| The marketplace is `.claude-plugin/marketplace.json` with `name`, `owner.name` and `plugins[]`. Each plugin entry has a `name` and a `source`. A relative `source` must start with `./` and resolves from the directory that holds `.claude-plugin/`. | One entry, `"source": "./"`: the repo root is both the marketplace and the plugin. |
| The entry name must equal the `plugin.json` name. The entry name is the install id. | Install with `/plugin install agent-harness@claude-harness`. |
| Skills are `skills/<name>/SKILL.md` with YAML frontmatter `name` and `description`. Plugin skills are namespaced. | `/agent-harness:harness-init`. It also triggers on "set up the harness" through its description. |
| Plugin hooks are `hooks/hooks.json`, shaped `{"description"?, "hooks": {<Event>: [{matcher, hooks:[...]}]}}`, and merge with user and project hooks. | `hooks/hooks.json` wires the guard. |
| `${CLAUDE_PLUGIN_ROOT}` is the plugin's install dir; it changes on update, so never write state there. `${CLAUDE_PROJECT_DIR}` is the project root. Both are substituted in hook `command`/`args` and exported to hook processes. Neither is set for commands Claude runs through the Bash tool. | Hooks use them; skills write `${CLAUDE_PLUGIN_ROOT}` in the Markdown body, and Claude Code substitutes it. |
| Exec-form hooks (`command` plus `args`) run without a shell, so paths need no quoting. | Every hook here uses exec form: `python3` with the script as `args[0]`. |
| **`"onFailure": "block"`** (v2.1.295+) turns hook failures into a block. Failures are: can't start, exit code other than 0/2, timeout, invalid JSON output. Without it, a missing `python3` or a crash **allows** the call. | Every guard hook sets it, so the guard fails closed even when Python is missing. |
| PreToolUse exit codes: 0 allows, with optional JSON on stdout. 2 blocks; stderr is the reason shown to Claude. Anything else is a non-blocking error. JSON `hookSpecificOutput.permissionDecision` can be `allow`, `deny`, `ask` or `defer`. | The guard uses exit 2 and stderr only. |
| A root `CLAUDE.md` in a plugin is not loaded as context, and `validate` warns about it. | This repo's agent instructions are in **`.claude/CLAUDE.md`**, which Claude Code loads as project memory. Plugin validation then passes `--strict`. |
| `claude plugin validate <dir|marketplace.json> [--strict]` is the authoritative check. | Run in `scripts/verify.sh` and in CI (the CLI is installed with npm there). |
| `commands/` and `agents/` are optional. Skills are preferred over commands for new plugins. | Phase 0 ships no commands or agents. A later lane may add `agents/reviewer.md` (see §11). |

---

## 2. Repo layout (this repo)

```
.claude-plugin/plugin.json        plugin manifest (name: agent-harness)
.claude-plugin/marketplace.json   marketplace (name: claude-harness), one entry, source "./"
skills/harness-init/SKILL.md      the installer skill
hooks/hooks.json                  plugin-level guard (--require-config mode)
scripts/guard-bash.py             THE guard (canonical); this repo and the plugin hook run it
scripts/verify.sh                 this repo's verify
tests/                            tests for the harness itself (table test, python unittests)
harness/core/                     language-agnostic files harness-init copies into a target
harness/stacks/{go,ts}/           per-stack fragments
docs/contract.md, docs/adr/       this file, decisions
.claude/CLAUDE.md                 agent rules for this repo
.claude/settings.json             wires scripts/guard-bash.py for agents working on this repo
harness.config.json               this repo's own harness config (dogfooding)
.github/workflows/ci.yml          jobs: verify, gitleaks   (required checks)
.github/workflows/codeql.yml      CodeQL python + actions
```

**Copies that must not drift** (`tests/test_manifests.py::CopiesInSyncTest` enforces this):
- `scripts/guard-bash.py` = `harness/core/hooks/guard-bash.py`
- `tests/guard-bash_test.sh` = `harness/core/hooks/guard-bash_test.sh`
- `.github/pull_request_template.md` = `harness/core/github/pull_request_template.md`

Edit the first file of each pair, then `cp` it over the second.

## 3. Target-project layout (what harness-init writes)

| Target path | Source in this repo |
|---|---|
| `harness.config.json` | `harness/core/harness.config.json.tmpl` (placeholders filled) |
| `CLAUDE.md` (managed block between `<!-- harness:begin -->` / `<!-- harness:end -->`) | `harness/core/CLAUDE.md.tmpl` |
| `.claude/settings.json` (merged, never clobbered) | `harness/core/settings.json` |
| `scripts/hooks/guard-bash.py`, `scripts/hooks/guard-bash_test.sh` | `harness/core/hooks/` |
| `scripts/verify.sh` | `harness/core/scripts/verify.sh` |
| `scripts/verify.d/20-go.sh`, `scripts/mutation-go.sh`, `.golangci.yml` | `harness/stacks/go/` |
| `scripts/verify.d/30-ts.sh`, `scripts/mutation-ts.sh`, `stryker.config.json` | `harness/stacks/ts/` |
| `scripts/pr-ready.sh`, `scripts/risk-tier.py`, `scripts/branch-protection.sh` | `harness/core/scripts/` |
| `.github/pull_request_template.md` | `harness/core/github/` |
| `.github/workflows/ci.yml`, `codeql.yml` | `harness/stacks/<stack>/workflows/`. For several stacks, merge the jobs; job names stay `verify`, `mutation`, `gitleaks`. |
| `.github/workflows/claude-e2e.yml` (opt-in) | `harness/core/e2e/claude-e2e.yml` |
| `docs/catch-log.md` | `harness/core/catch-log.md` (keep the header, drop the example row) |
| fuzz/property tests next to the code under test | `harness/stacks/<stack>/test-templates/*.tmpl` |

Placeholders in templates: `{{PROJECT_NAME}}`, `{{DEFAULT_BRANCH}}`, `{{STACK}}`,
`{{REQUIRED_CHECKS}}`, `{{COVERAGE_MIN}}`, `{{MUTATION_MIN}}`, `{{PACKAGE}}`, `{{FUNC}}`,
`{{MODULE}}`. Workflow files also contain GitHub `${{ ... }}` expressions; replace only the
double-brace names listed here.

## 4. `harness.config.json`

Schema: `harness/core/harness.config.schema.json` (JSON Schema 2020-12). It lives at the
target project's root.

```jsonc
{
  "version": 1,                              // required, const 1
  "stacks": ["go", "ts"],                    // required: go | ts | python | other
  "defaultBranch": "main",                   // required
  "requiredChecks": ["verify", "mutation", "gitleaks"], // required: CI job names = branch-protection contexts
  "riskTiers": {                             // required
    "default": "standard",                   // fast | standard | sensitive
    "rules": [ { "glob": ".github/**", "tier": "sensitive" } ]
  },
  "review": {                                // required
    "independence": "author-or-marker",      // author-or-marker | author
    "humanApprovalTiers": ["sensitive"]      // default ["sensitive"]
  },
  "coverageMin": 70,                         // optional, default 70
  "mutationMin": 60                          // optional, default 60
}
```

Who reads which field:

| Field | Readers |
|---|---|
| `defaultBranch` | guard, verify, pr-ready (via mutation base), branch-protection |
| `requiredChecks` | pr-ready, branch-protection |
| `riskTiers` | risk-tier.py, so also pr-ready |
| `review` | pr-ready |
| `coverageMin`, `mutationMin` | verify and its fragments (env vars `COVERAGE_MIN`/`MUTATION_MIN` override) |

## 5. Guard hook

| | |
|---|---|
| Files | `scripts/guard-bash.py` (canonical). Target: `scripts/hooks/guard-bash.py`. Python 3.9+, stdlib only. |
| Wiring | **Project:** `.claude/settings.json` → PreToolUse, matcher `Bash`, exec form `python3 ${CLAUDE_PROJECT_DIR}/scripts/hooks/guard-bash.py`, `timeout: 10`, `onFailure: "block"`. **Plugin:** `hooks/hooks.json`, the same but `${CLAUDE_PLUGIN_ROOT}/scripts/guard-bash.py --require-config`. |
| Stdin | PreToolUse JSON. It reads `tool_input.command` (required string) and `cwd` (optional; defaults to the process cwd). Other fields are ignored. |
| Config | `harness.config.json` in `$CLAUDE_PROJECT_DIR`, else in `cwd`. It uses `defaultBranch` (default `main`). |
| Exit 0 | Allow. No stdout, so the normal permission flow continues (the hook never auto-approves). |
| Exit 2 | Block. Stderr says `Blocked by agent harness: <reason>. Ask the orchestrator.` |
| Fails closed | Unparseable stdin, a missing or non-string command, unbalanced quotes, an invalid `harness.config.json`, nesting deeper than 4, and any internal exception all exit 2. A crash or missing interpreter is covered by `onFailure: "block"`. |
| `--require-config` | Plugin mode. If the project has **no** `harness.config.json`, exit 0, so the plugin is inert in projects that have not opted in. If the config exists but is broken, still exit 2. |
| Override | A leading `HARNESS_ORCHESTRATOR=1` assignment on a **command segment** skips the checks for that segment only. `HARNESS_ORCHESTRATOR=1 git status && gh pr merge 1` is still blocked. It is visible in the transcript by design. The environment of the hook process is **not** consulted. |

**Blocked** (each has table-test cases):
- `gh pr merge`, and `gh api` merge endpoints (as writes).
- `git push`:
  - with `--force`, `--force-with-lease[=…]`, `--force-if-includes`, `-f`, a combined short flag containing `f` or `d`, `--delete`, `--prune`, `--mirror`, `--all`, `--tags` or `--follow-tags`;
  - with a `+refspec` or a `:ref` deletion;
  - to the default branch (`main`, `HEAD:main`, `refs/heads/main`);
  - of a tag (`refs/tags/*`, `v1`, `v1.2.3`, `pkg-v1.2.3`, `v1.2.3-rc.1`);
  - a bare `git push`, or `git push <remote> [HEAD]`, when the current branch (`git symbolic-ref` in cwd or `-C dir`) is the default branch or can't be determined, or when the command `cd`'d earlier.
- `gh release create|edit|delete|upload|delete-asset`, `gh workflow run|enable|disable`, `gh repo edit|delete|rename|archive|unarchive`, `gh secret|variable set|delete|remove`.
- `gh api`:
  - any method other than GET/HEAD;
  - an implicit POST, i.e. `-f`/`-F`/`--field`/`--raw-field`/`--input` without `-X GET`;
  - `graphql` with a `mutation`, with `--input`, with `query=@file`, or with no inline query.
- The same commands when wrapped in `bash|sh|zsh|dash|ksh -c '…'`, `eval '…'`, `$(…)`, backticks, `env …`, `command …`, `nohup …`, `time …`, `sudo …`, `xargs …`, or written with an absolute path (`/usr/bin/gh`).

**Allowed:**
- normal lane work: pushing a lane branch with `-u`, `gh pr create|view|comment|checks`;
- reads (`gh api` GETs and GraphQL queries);
- the text of a blocked command inside a commit message or `grep` pattern;
- `git tag -l`.

**Known limits (not bugs; the lanes may tighten them):**
- The guard sees the Bash command text only. A script that calls `gh` inside is not inspected; that is why `branch-protection.sh` checks `HARNESS_ORCHESTRATOR=1` itself.
- `curl` to the GitHub API, `git` aliases and other languages' HTTP clients are out of scope.
- Server-side branch protection is the real boundary.

## 6. pr-ready gate

| | |
|---|---|
| File | `harness/core/scripts/pr-ready.sh` → target `scripts/pr-ready.sh` |
| Usage | `scripts/pr-ready.sh <pr-number>` (env `HARNESS_CONFIG` overrides the config path) |
| Needs | `gh` (authenticated), `jq`, `git`, `python3` (for `risk-tier.py`, found next to the script) |
| Output | One line per gate (`ok` / `FAIL` / `HUMAN`), then `READY`, `NOT READY` or `NEEDS HUMAN` |
| Exit | **0** READY. **1** NOT READY: a gate failed, or any gh/API call failed (the ERR trap makes it fail closed). **2** usage or config error. **3** NEEDS HUMAN: every automated gate passed, but the tier needs a human approval that is missing. |

The orchestrator may merge only on exit 0, and only if the human has given merge authority.
On exit 3 the orchestrator asks the human.

Gates, in order:
1. **Checks:** `gh pr checks <n>` all pass, **and** every `requiredChecks` name exists with bucket `pass`. A required check that is missing is a failure.
2. **Code scanning:** open alerts on `refs/pull/<n>/merge` + `refs/pull/<n>/head`, summed, must be 0.
3. **Bot threads:** unresolved review threads whose first comment's author `__typename == "Bot"` must be 0.
4. **Independent review:** at least one review where
   - `review.author.login != pr.author.login`, **or**
   - (mode `author-or-marker`) the PR body has `<!-- harness:builder id=X -->` and the review body has `<!-- harness:reviewer id=Y -->` with `Y != X`.

   Markers exist because agents and the human often share one GitHub account. They are self-asserted, so they are a process check, not authentication.
5. **Findings → fixes:** a PR comment whose body **starts with** `## Review findings → fixes` and whose `createdAt` is later than the latest review's `submittedAt`.
6. **Risk tier:** `risk-tier.py` over the PR's changed files. If the tier is in `review.humanApprovalTiers`, it needs a review with `state == APPROVED`, author ≠ PR author, and author not ending in `[bot]`. Without one, exit 3.

**Comment formats** (agents must use them exactly):
- PR body, first line: `<!-- harness:builder id=<lane-id> -->`. Ids match `[A-Za-z0-9._-]+`.
- Reviewer: **one** review (`gh pr review <n> --comment --body-file f.md`) whose body contains `<!-- harness:reviewer id=<reviewer-id> -->`, with all findings numbered.
- Builder, after fixes: **one** comment starting `## Review findings → fixes`, with one line per finding number → commit sha or "won't fix: why", then the final verify/mutation numbers.

## 7. Risk tiers

| | |
|---|---|
| File | `harness/core/scripts/risk-tier.py` → target `scripts/risk-tier.py` (stdlib only) |
| Usage | `risk-tier.py [--config PATH] [FILE...]`; with no FILE args it reads newline-separated paths from stdin |
| Output | `fast`, `standard` or `sensitive` on stdout, exit 0. Missing or invalid config, or an unknown tier: message on stderr, exit 2, no stdout. |
| Rule | For each file, take the **highest** tier among all rules whose glob matches it; if none match, use `riskTiers.default`. For the PR, take the highest across its files. No files gives the default. Rules can only raise a tier: to keep `skills/**/*.md` out of `**/*.md → fast`, add `skills/** → standard`. |
| Globs | Paths are repo-relative with `/`. `**/` matches zero or more directories, `**` matches anything, `*` and `?` stay inside one segment, everything else is literal. Anchored to the whole path. |

What each tier means:

| Tier | Gates |
|---|---|
| `fast` | All automated gates. No human approval. Intended for docs and comments. |
| `standard` | All automated gates. No human approval. The default. |
| `sensitive` | All automated gates plus a human APPROVED review (exit 3 until then). Use it for CI, hooks, auth, migrations, the harness config and secrets handling. |

## 8. Catch-layer log

- **Template:** `harness/core/catch-log.md` → target `docs/catch-log.md`.
- **Format:** one Markdown table, header exactly
  `| Date | Bug | Caught by | Should have caught | Follow-up |`.
  - `Date`: ISO `YYYY-MM-DD`.
  - `Caught by` and `Should have caught`: each one of `unit`, `property/fuzz`, `mutation`, `reviewer`, `CI-scan`, `e2e`, `human`, `prod`.
  - `Follow-up`: free text, no `|`, ideally with a PR or commit link.
- **Rule:** every bug-fix PR appends one row. The reviewer checks it.

**Summariser spec** (lane D; not yet built). `scripts/catch-log-summary.py [--file docs/catch-log.md] [--since YYYY-MM-DD] [--json]`:
- Parse the table rows. Bad rows are reported with their line number and give exit 2.
- Print the counts per `Caught by`.
- Print the counts per `Should have caught`.
- Print a "leak matrix" (should-have × caught-by).
- Print the **escape rate** per layer: rows where `Should have caught = L` and `Caught by ≠ L`, divided by rows where `Should have caught = L`.
- List rows whose Follow-up is empty or `none`.
- `--json` emits `{rows, byCaught, byShould, matrix, escapeRate, missingFollowUp}`.
- Exit 0 on success.

## 9. branch-protection.sh

| | |
|---|---|
| File | `harness/core/scripts/branch-protection.sh` → target `scripts/branch-protection.sh` |
| Usage | `HARNESS_ORCHESTRATOR=1 scripts/branch-protection.sh [--repo owner/name] [--branch B] [--check NAME]... [--dry-run]` |
| Defaults | `--check` falls back to `requiredChecks`. `--branch` falls back to `defaultBranch`, then the repo's default branch. `--repo` falls back to `gh repo view`. |
| Applies | `PUT repos/{repo}/branches/{branch}/protection` with these settings: |
| | `required_status_checks: {strict: true, checks: [{context}...]}` |
| | `enforce_admins: true` |
| | `required_pull_request_reviews: null` (a shared account can't approve its own PRs; pr-ready's tiers decide when a human must approve) |
| | `restrictions: null` |
| | `required_linear_history: false` (we use merge commits) |
| | `allow_force_pushes: false` |
| | `allow_deletions: false` |
| | `required_conversation_resolution: true` |
| | Then it prints the resulting settings as JSON. |
| Exit | **0** applied, or `--dry-run` printed the payload. **1** gh/API failure. **2** usage or config error, or no checks. **3** refused because `HARNESS_ORCHESTRATOR=1` is not set (except with `--dry-run`). |

## 10. verify and stacks

- **This repo:** `scripts/verify.sh` runs:
  - shellcheck on every tracked `*.sh` (if installed);
  - `python3 -m unittest discover -s tests`;
  - both copies of the guard table test;
  - `json.tool` on both manifests;
  - `claude plugin validate .claude-plugin/marketplace.json` and `claude plugin validate . --strict` (if `claude` is installed; CI installs it);
  - gitleaks (if installed; CI runs it as its own job).
- **Target:** `scripts/verify.sh` (from core) does these steps in order:
  1. Runs the guard table test.
  2. Runs shellcheck (if installed).
  3. **Sources** every `scripts/verify.d/*.sh` in lexical order. The fragments get `step`, `have_tool`, `COVERAGE_MIN`, `MUTATION_MIN`, `DEFAULT_BRANCH` and `VERIFY_MUTATION` (0/1, set by `--mutation`).
  4. Runs gitleaks.

  Exit is non-zero on the first failure (`set -e`).
- **Go fragment** (`20-go.sh`), in order:
  - `go mod tidy -diff`;
  - golangci-lint, using `.golangci.yml` (errcheck, gosec, govet, revive, staticcheck, unused, ineffassign; gofmt, goimports);
  - `go test -race` with a coverage gate;
  - a short `-fuzz` run of every `Fuzz*` target (`FUZZTIME`, default 10s);
  - govulncheck;
  - gremlins on changed packages when `--mutation` is set.

  Tools run through `go tool -modfile=tools/go.mod` when that file exists.
- **TS fragment** (`30-ts.sh`, `TS_DIR` defaults to `.`):
  - `npm ci`;
  - `npm run lint`, `typecheck`, `test` and `build`;
  - `npm audit --omit=dev --audit-level=high`;
  - Stryker on changed files when `--mutation` is set (`--thresholds.break MUTATION_MIN`).

  The project's test config owns its coverage threshold.
- **CI job names are the contract:** `verify`, `mutation` (PRs only) and `gitleaks`, plus CodeQL `analyze (<lang>)`. They match `requiredChecks`.

## 11. Fuzz / property templates

- **Go:**
  - `test-templates/fuzz_test.go.tmpl`: native `testing.F` with seeds. The body asserts properties; panics fail the test.
  - `test-templates/property_test.go.tmpl`: `pgregory.net/rapid`, with a `testing/quick` fallback in a comment.
- **TS:** `test-templates/property.test.ts.tmpl`, using vitest + fast-check.
- **harness-init** offers one fuzz/property test for a pure function the user names. It never generates tests for code it hasn't read.
- **Coverage expectation:** every parser, encoder or validator in a target project gets one property or fuzz test.

## 12. Headless Claude Code e2e

| | |
|---|---|
| File | `harness/core/e2e/claude-e2e.yml` → target `.github/workflows/claude-e2e.yml` (opt-in) |
| Trigger | `workflow_dispatch` and nightly cron. Not per-PR, because of cost. |
| Needs | the `ANTHROPIC_API_KEY` repo secret, and Node to install `@anthropic-ai/claude-code` |
| Behaviour | Runs `claude -p "<task the guard must block>" --output-format json --max-turns 3 --allowedTools Bash`. The job **fails** if the output doesn't show a block, or if the side effect happened: a remote branch `e2e-probe` exists. |
| Exit | the job fails, as above, if the harness did not hold |

Scenarios to add (lane C):
- the force-with-lease push (already there);
- `gh pr merge`;
- `gh api -X PUT …/protection`;
- a push to the default branch;
- a tag push.

Each scenario asserts the block message **and** the absence of the side effect.

## 13. Adversarial harness tests

Lane A owns these. The files:
- `tests/guard-bash_test.sh`: the canonical table, shipped to targets.
- `tests/adversarial/` (new): cases that try to slip a blocked command past the guard. Each case is `want<TAB>command`, one per line. A runner feeds every line through the guard.

Categories to cover, as a minimum:
- **Quoting and escapes:** `g"h" pr merge`, `\gh pr merge`, `$'gh' pr merge`.
- **Variables and indirection:** `x=gh; $x pr merge`, `${GH:-gh} pr merge`.
- **Aliases and functions:** `f(){ gh pr merge 1; }; f`.
- **Subshells, here-docs and process substitution:** `bash <<< 'gh pr merge 1'`, `source <(echo …)`.
- **Other runners:** `python -c 'import os; os.system("gh pr merge 1")'`, `xargs -I{} gh pr merge {}`.
- **Option spellings:** `--force-with-lease` abbreviations (git accepts `--force-w`), `-o` values, `--repo` before `push`.
- **Refspec spellings:** `refs/heads//main`, a trailing `^{}`, uppercase `MAIN` on case-insensitive filesystems.
- **Override abuse:** `HARNESS_ORCHESTRATOR=1` on a different segment, `HARNESS_ORCHESTRATOR=01`, `export HARNESS_ORCHESTRATOR=1; gh pr merge`.
- **Input robustness:** huge commands, NUL bytes, non-UTF-8, deep nesting.

For each case, either:
- block it, **fail-closed by default** (an unknown construct that contains `gh`/`git push` tokens is blocked); or
- record it as an accepted limit in §5, with a reason.

Every guard change must keep `tests/guard-bash_test.sh` green and add a row for the case it fixes.

## 14. harness-init (skill)

`skills/harness-init/SKILL.md`. Trigger: "set up the harness" and similar phrases, or
`/agent-harness:harness-init`. Source files: `${CLAUDE_PLUGIN_ROOT}/harness/`.

1. **Preflight.**
   - Must be inside a git repo with a GitHub remote.
   - `gh auth status` must be OK.
   - `python3`, `jq` and `git` must be present.
   - The working tree must be clean. If it isn't, stop and ask.
2. **Detect.**
   - Stacks: `go.mod` → go; `package.json` + (`tsconfig.json` or a `typescript` dependency) → ts. A monorepo can have several stacks; record each one's dir (e.g. `TS_DIR`).
   - Default branch: `gh repo view --json defaultBranchRef`.
   - Existing CI, CLAUDE.md, `.claude/settings.json` and PR template.
3. **Propose and confirm** with the user, in one message:
   - the stacks;
   - the default branch;
   - the required checks;
   - the risk-tier rules (start from the template's, plus detected paths such as `migrations/` and `auth/`);
   - the coverage and mutation thresholds;
   - whether to add the opt-in e2e workflow.
4. **Branch:** `git switch -c harness/init`.
5. **Write `harness.config.json`** and validate it against the schema.
6. **Copy core** per §3:
   - Fill the placeholders.
   - Merge into existing files: CLAUDE.md gets the managed block, replaced if present; settings.json has the hook appended if no identical hook exists; the PR template is replaced only if the user agrees.
   - Never delete user content.
7. **Copy stack fragments** per §3. If CI workflows already exist, add jobs with the contract job names instead of replacing the files. Set up `tools/go.mod` (golangci-lint, govulncheck, gremlins) or the npm dev deps (fast-check, @stryker-mutator/core, @stryker-mutator/vitest-runner) with the user's OK.
8. **Run** `scripts/hooks/guard-bash_test.sh` and `scripts/verify.sh`. Fix what the harness broke. Report pre-existing failures (lint debt, low coverage) and propose temporary thresholds. Don't silently lower them.
9. **Commit** in small steps, push `harness/init`, and open a PR using the template, with a builder marker.
10. **After the human merges:** with the user's explicit OK, `HARNESS_ORCHESTRATOR=1 scripts/branch-protection.sh`.
11. **Report** what was installed, the required checks, what needs the user (secrets such as `ANTHROPIC_API_KEY` for e2e, approvals), and how to use the workflow.

Idempotent: re-running it updates the managed blocks and the copied files to the plugin's
current version, and shows a diff before writing.

---

## 15. Lanes after Phase 0 (proposal; at most 3 at once)

| Lane | Scope | Main files |
|---|---|---|
| A | Guard hardening + adversarial suite (§13) | `scripts/guard-bash.py`, `tests/adversarial/` |
| B | pr-ready + risk tier tests with a fake `gh` on PATH; branch-protection dry-run tests | `harness/core/scripts/`, `tests/` |
| C | harness-init skill body + stack adaptation + headless e2e scenarios | `skills/harness-init/`, `harness/stacks/`, `harness/core/e2e/` |
| D | catch-log summariser (§8) | `scripts/catch-log-summary.py` (+ copy in `harness/core/scripts/`) |

## 16. Open questions

1. **Plugin name.** The brief asked for `claude-harness@claude-harness`; the validator reserves `claude-*` plugin names. Using `agent-harness@claude-harness`. Rename if you prefer another name.
2. **Double guard.** With the plugin installed **and** the project copy wired, the guard runs twice per Bash call. That is harmless (same verdict) but costs about 50 ms. Keep both: the project copy protects teammates and CI without the plugin, and the plugin copy protects projects that have opted in before init finishes.
3. **Human approval on a shared account.** If the human and the agents use one GitHub account, `sensitive` PRs can never satisfy gate 6. The human then merges these PRs themselves (exit 3 is the signal). Alternative: accept a signed-off comment from the human. Not done, because it can't be told apart from an agent.
4. **CodeQL as a required check** for this repo: left out for now (`verify`, `gitleaks` only). Add `analyze (python)` once the job is stable.
