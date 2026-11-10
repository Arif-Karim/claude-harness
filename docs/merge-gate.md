# The merge gate

`scripts/pr-ready.sh <n>` decides whether the orchestrator may merge PR `<n>`. The
contract is [`contract.md` §6](contract.md#6-pr-ready-gate) (gates) and §7 (risk tiers).
This page explains how to use it, and what it can't do when everyone shares one
GitHub account.

## "No code-scanning analysis for <sha>": update the branch

The analysis must be of the PR's **current** merge commit. When another PR merges into the
default branch, GitHub recomputes this PR's merge commit, but CodeQL on `pull_request` doesn't
run on pushes to the base, and "Re-run jobs" re-analyses the **old** merge commit. So after main
moves, gate 2 stays NOT READY until the branch is updated: merge `origin/main` into the lane
branch (or rebase and push a new branch) and push, or use "Update branch" on GitHub. Re-running
CodeQL won't help. In multi-lane work this happens to most open PRs after each merge.

## Exit codes and what to do

| Exit | Last line | Meaning | Who acts |
|---|---|---|---|
| 0 | `READY` | Every gate passed, and the risk tier needs no human approval (or has one). | The orchestrator may merge the printed head sha (below), **if the human has given it merge authority**. |
| 1 | `NOT READY` | A gate failed, **or any `gh`/API call failed** (fail closed). The `FAIL` lines say which. | The builder fixes it; the orchestrator re-runs the gate. |
| 2 | (stderr) | Usage or config error: bad PR number, missing or invalid `harness.config.json`. Nothing was asked of GitHub. | Fix the config. |
| 3 | `NEEDS HUMAN` | Every automated gate passed, but the risk tier is in `review.humanApprovalTiers` (by default `sensitive`) and no human has approved. | **The human.** See below. |

A failing gate always wins: a sensitive PR that also has a failing check is exit 1, not 3.

## The verdict is for one commit

The gate prints `head <sha>` and re-reads the head at the end: if a commit was pushed during
the run, it is NOT READY. A commit can still land after the verdict, so the orchestrator merges
only the judged commit:

```sh
HARNESS_ORCHESTRATOR=1 gh pr merge <n> --merge --match-head-commit <sha>
```

GitHub refuses that merge if the head is no longer `<sha>`; re-run the gate. The guard allows
this exact form only behind the `HARNESS_ORCHESTRATOR=1` prefix (`tests/test_pr_ready.py`
checks both).

## The gates, briefly

Reviews and comments are read from the REST API, where a bot is `user.type == "Bot"`.
(`gh pr view --json reviews` shows `github-actions[bot]` as plain `github-actions`, so it can't
be used to tell bots from people.)

1. **Checks**: `gh pr checks` all pass, and every `requiredChecks` name is present with bucket `pass` (missing, pending or skipped fails).
2. **Code scanning**: a code-scanning analysis must exist for the PR's **current merge commit** (`refs/pull/<n>/merge`, matching `merge_commit_sha`); otherwise "no code-scanning analysis for <sha>", NOT READY. Then 0 open alerts on `refs/pull/<n>/merge` plus `refs/pull/<n>/head`, every page. The alerts API returns an empty list for a ref nobody analysed, so without the analysis check a skipped, pending or stale scan would pass.
3. **Bot threads**: 0 unresolved review threads started by a bot, every page.
4. **Independent review**: a non-bot review by another login, or (mode `author-or-marker`) a non-bot review with a `harness:reviewer` marker whose id differs from the PR body's `harness:builder` id. Reviews from code scanning, Copilot or Actions never count.
5. **Findings → fixes**: a comment starting `## Review findings → fixes`, posted strictly after the last harness review (the last non-bot review with a reviewer marker; if no review has a marker, the last non-bot review that isn't an approval). A human's approval or a bot's review posted later doesn't reset it. **Every agent review must carry the `<!-- harness:reviewer id=… -->` marker:** once a marker review exists, a later review from the shared account without the marker is ignored by this gate, so its findings could go unanswered.
   **Human block:** in every tier, if any human's latest `APPROVED`/`CHANGES_REQUESTED` review is `CHANGES_REQUESTED`, the PR is NOT READY (not NEEDS HUMAN) until that human approves or the review is dismissed.
6. **Risk tier**: the tier of every changed file, including the old path of a rename, from the paginated file listing. If the tier needs a human, each human's latest decisive review (`APPROVED`, `CHANGES_REQUESTED` or `DISMISSED`; comment-only reviews don't count) must be `APPROVED`, by a non-bot login that is not the PR author.

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
(builders can't merge, push to the default branch or write through `gh api`), and gates 1–3
and 5. Gate 4 holds only as far as the markers are honest.

## The exit-3 flow

1. The orchestrator runs `scripts/pr-ready.sh <n>` and gets exit 3, `NEEDS HUMAN`.
2. It **stops** and tells the human: the PR link, the tier, and why the tier is what it is
   (for example "touches `.github/workflows/ci.yml`"). It does not merge, retry, or try to
   produce an approval.
3. The human reviews the PR, then does one of:
   - **Shared account:** merges it themselves in the GitHub UI or with `gh pr merge <n> --merge`
     in their own terminal (outside the agent's guard). Their merge is the approval.
   - **Separate human account:** approves it on GitHub. The orchestrator re-runs the gate,
     gets exit 0 (the approval comes after the findings → fixes comment, and that's fine), and
     merges the printed sha with `--match-head-commit` if it has merge authority.
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
`HARNESS_CONFIG` is set) and `python3`. Gate 2 needs a code-scanning analysis of every PR's
merge commit: if the repo has no code scanning, or CodeQL skips a PR (a `paths` filter) or
hasn't uploaded yet, the PR is NOT READY. Enable CodeQL on `pull_request` for all paths.

## Known limits

- **Self-asserted markers** (above): gate 4 on a shared account is a process check.
- **Approvals don't reset on new commits.** Use GitHub's "dismiss stale approvals" if you have
  separate accounts; on a shared account the human merges sensitive PRs anyway.
- **Filenames containing a newline** are split into two paths before `risk-tier.py` sees them.
  That can only lower the tier if a glob matches the whole name but neither half; it would need
  NUL-separated paths to close (review nit N2, not done).
- **`branch-protection.sh` doesn't URL-encode the branch** (review nit N4). A default branch with
  `%`, `?` or `#` (and possibly `/`) builds a wrong API path; the call then fails (exit 1) with a
  confusing error. Pass such a branch already URL-encoded with `--branch`.
- **Who controls the fake controls the tests**: `tests/fakes/**`, the gate scripts and the
  contract are `sensitive` in this repo's `harness.config.json`, so changing them needs a human.

## Tests

`tests/test_pr_ready.py`, `tests/test_risk_tier.py` and `tests/test_branch_protection.py`
run the scripts against `tests/fakes/gh`, a fake `gh` put first on `PATH`. It serves canned
JSON from a routes file (`$FAKE_GH_ROUTES`), applies `--jq` like gh, emits extra pages only
with `--paginate`, records each call's argv and stdin (`$FAKE_GH_LOG`), and fails on any call
it has no route for. Each pr-ready case starts from a PR that passes every gate and changes
one thing; every `gh` call is also made to fail in turn, and each must give NOT READY.
