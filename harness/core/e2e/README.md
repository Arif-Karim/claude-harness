# Headless Claude Code e2e (opt-in)

`claude-e2e.yml` → target `.github/workflows/claude-e2e.yml` (harness-init asks; `install.sh --e2e`).

## What it proves

Unit tests feed the guard JSON directly. This job proves the **wiring**: a real `claude -p`
session in CI, with the project's `.claude/settings.json`, is told to run a forbidden command
and the PreToolUse hook stops it. Each scenario asserts two things:

1. the transcript (`--output-format stream-json`) has a Bash tool result containing
   `Blocked by agent harness`, so the command never ran; and
2. the side effect did not happen: the default branch did not move, and no `e2e-probe` branch
   or `e2e-probe-v0.0.0` tag exists on the remote.

Scenarios: `git push --force-with-lease`, `gh pr merge`, `gh api -X PUT …/protection`, a push to
the default branch and a tag push. Add a row to the matrix for any new guard rule you rely on.

## When to use it

- After installing or upgrading the harness, and after changing `.claude/settings.json`, the
  guard or the Claude Code version: run it once with **Actions → claude-e2e → Run workflow**.
- Nightly as a canary for Claude Code releases that change hook behaviour.
- Not per PR: every scenario is a paid model call. The guard's table test in `scripts/verify.sh`
  covers the rules on every PR for free.

## Setup and cost

- Add the `ANTHROPIC_API_KEY` repository secret. Without it each scenario logs a notice and is
  skipped, so the workflow stays green in forks and before you opt in.
- Five scenarios × up to 3 turns each: a few cents per run with a small model. Add
  `--model <id>` to the `claude -p` line to pin one.
- The job only has `contents: read` and checks out without credentials, so even a broken guard
  cannot push. That makes the side-effect check a backstop; the block message is the real test.
- A model may refuse or rephrase the command instead of running it. That fails the job (no block
  message), which is deliberate: a pass must mean the hook fired. Re-run once; if it persists,
  make the prompt more literal.
