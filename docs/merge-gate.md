# The merge gate

`scripts/pr-ready.sh <n>` decides whether the orchestrator may merge PR `<n>`. The
contract is [`contract.md` §6](contract.md#6-pr-ready-gate) (gates) and §7 (risk tiers).
This page explains how to use it, and what it can't do when everyone shares one
GitHub account.

## Exit codes and what to do

| Exit | Last line | Meaning | Who acts |
|---|---|---|---|
| 0 | `READY` | Every gate passed, and the risk tier needs no human approval (or has one). | The orchestrator may merge (merge commit), **if the human has given it merge authority**. |
| 1 | `NOT READY` | A gate failed, **or any `gh`/API call failed** (fail closed). The `FAIL` lines say which. | The builder fixes it; the orchestrator re-runs the gate. |
| 2 | (stderr) | Usage or config error: bad PR number, missing or invalid `harness.config.json`. Nothing was asked of GitHub. | Fix the config. |
| 3 | `NEEDS HUMAN` | Every automated gate passed, but the risk tier is in `review.humanApprovalTiers` (by default `sensitive`) and no human has approved. | **The human.** See below. |

A failing gate always wins: a sensitive PR that also has a failing check is exit 1, not 3.

## The gates, briefly

1. **Checks**: `gh pr checks` all pass, and every `requiredChecks` name is present with bucket `pass` (missing, pending or skipped fails).
2. **Code scanning**: 0 open alerts on `refs/pull/<n>/merge` plus `refs/pull/<n>/head`, every page.
3. **Bot threads**: 0 unresolved review threads started by a bot, every page.
4. **Independent review**: a review by another login, or (mode `author-or-marker`) a review with a `harness:reviewer` marker whose id differs from the PR body's `harness:builder` id.
5. **Findings → fixes**: a comment starting `## Review findings → fixes`, posted after the last review.
6. **Risk tier**: the tier of every changed file, including the old path of a rename, from the paginated file listing. If the tier needs a human, each human's latest decisive review (`APPROVED`, `CHANGES_REQUESTED` or `DISMISSED`; comment-only reviews don't count) must be `APPROVED`, by a login that is not the PR author and not a `[bot]`.

## Single shared GitHub account: the limitation

Often the human and every agent (orchestrator, builders, reviewers) are logged in to `gh`
as the **same GitHub user**. Then:

- **GitHub can't tell the roles apart.** Every PR, review and comment has the same author.
  GitHub doesn't let an author approve their own PR, so `required_pull_request_reviews`
  would block every merge. `branch-protection.sh` sets it to `null` for that reason.
- **Gate 4 falls back to markers.** The PR body carries `<!-- harness:builder id=X -->`
  and the reviewer's review carries `<!-- harness:reviewer id=Y -->`. The gate needs
  `Y != X`. These markers are **self-asserted**: any agent can write any id. They prove
  the process was followed (a separate reviewer run with fresh context posted the review),
  not who posted it. They are a process check, not authentication. Set
  `review.independence` to `author` to ignore markers when you do have separate accounts.
- **Gate 6 can never pass for `sensitive` PRs.** A human approval must come from a login
  other than the PR author, and on a shared account there is none. So every sensitive PR
  ends at **exit 3**. That is intended: it is the signal that a human must look.
- **A comment from "the human" is not accepted** as an approval: on a shared account it
  can't be told apart from one an agent wrote (contract §16, question 3).

What still holds on a shared account: the server-side branch protection (required checks,
no force pushes, no deletions, conversation resolution, `enforce_admins`), the guard hook
(builders can't merge, push to the default branch or write through `gh api`), and gates 1–5.

## The exit-3 flow

1. The orchestrator runs `scripts/pr-ready.sh <n>` and gets exit 3, `NEEDS HUMAN`.
2. It **stops** and tells the human: the PR link, the tier, and why the tier is what it is
   (for example "touches `.github/workflows/ci.yml`"). It does not merge, retry, or try to
   produce an approval.
3. The human reviews the PR, then does one of:
   - **Shared account:** merges it themselves in the GitHub UI or with `gh pr merge <n> --merge`
     in their own terminal (outside the agent's guard). Their merge is the approval.
   - **Separate human account:** approves it on GitHub. The orchestrator re-runs the gate,
     gets exit 0, and merges if it has merge authority.
   - Requests changes: the builder addresses them, and the loop starts again. A later
     `CHANGES_REQUESTED` from the same human cancels an earlier approval.
4. If commits are pushed after an approval, the gate does not reset the approval; the
   human should re-review anything substantial.

## Running it

```sh
scripts/pr-ready.sh 42                       # uses harness.config.json at the repo root
HARNESS_CONFIG=path/to/config.json scripts/pr-ready.sh 42
```

It needs `gh` (authenticated, read access to code scanning), `jq`, `git` (unless
`HARNESS_CONFIG` is set) and `python3`. If the repo has no code scanning, gate 2's API call
fails and the PR is NOT READY: enable CodeQL, or the gate can't pass.

## Tests

`tests/test_pr_ready.py`, `tests/test_risk_tier.py` and `tests/test_branch_protection.py`
run the scripts against `tests/fakes/gh`, a fake `gh` put first on `PATH`. It serves canned
JSON from a routes file (`$FAKE_GH_ROUTES`), applies `--jq` like gh, emits extra pages only
with `--paginate`, records each call's argv and stdin (`$FAKE_GH_LOG`), and fails on any call
it has no route for. Each pr-ready case starts from a PR that passes every gate and changes
one thing; every `gh` call is also made to fail in turn, and each must give NOT READY.
