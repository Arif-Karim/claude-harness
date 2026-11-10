#!/usr/bin/env bash
# Table test for the guard hook: feeds Claude Code PreToolUse JSON on stdin and
# checks the exit code (2 = blocked, 0 = allowed).
# Usage: tests/guard-bash_test.sh [path/to/guard-bash.py]
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
GUARD="${1:-$root/scripts/guard-bash.py}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
# A project with no harness.config.json: default branch is "main".
mkdir -p "$tmp/plain"
# A project whose default branch is "trunk".
mkdir -p "$tmp/trunk"
printf '{"version":1,"defaultBranch":"trunk"}\n' >"$tmp/trunk/harness.config.json"
# A project whose config is broken: the guard must fail closed.
mkdir -p "$tmp/broken"
printf '{not json' >"$tmp/broken/harness.config.json"
# A git repo checked out on main, and one on a lane branch (for bare `git push`).
for b in main lane-a; do
  git init -q -b "$b" "$tmp/repo-$b"
done

fail=0
run() { # run <project-dir> <cwd> <command> [guard args...]
  local proj="$1" cwd="$2" cmd="$3" got=0
  shift 3
  jq -n --arg c "$cmd" --arg d "$cwd" \
    '{hook_event_name:"PreToolUse",tool_name:"Bash",cwd:$d,tool_input:{command:$c}}' |
    CLAUDE_PROJECT_DIR="$proj" "$GUARD" "$@" >/dev/null 2>&1 || got=$?
  echo "$got"
}
expect() { # expect <want> <got> <label>
  if [[ "$2" != "$1" ]]; then echo "FAIL: want exit $1, got $2: $3"; fail=1; fi
}
check() { expect "$1" "$(run "$tmp/plain" "$tmp/plain" "$2")" "$2"; }
check_trunk() { expect "$1" "$(run "$tmp/trunk" "$tmp/trunk" "$2")" "[trunk] $2"; }
check_in() { expect "$1" "$(run "$tmp/plain" "$2" "$3")" "[cwd $(basename "$2")] $3"; }

# --- Blocked: only the human (or the orchestrator, explicitly) merges and touches the default branch.
check 2 'gh pr merge 12 --merge'
check 2 'cd x && gh pr merge 3'
check 2 'git push --force origin lane-a'
check 2 'git push -f origin lane-a'
check 2 'git push origin main'
check 2 'git push origin HEAD:main'
check 2 'git push --delete origin main'
check 2 'gh api -X PUT repos/o/r/branches/main/protection'
check 2 'gh release create v1.0.0'
check 2 'git tag v1.0.0 && git push origin v1.0.0'
check 2 'gh api -X PUT repos/o/r/pulls/3/merge'
check 2 'gh api --method PATCH repos/o/r'
check 2 'gh repo edit --enable-auto-merge'
check 2 'git push --tags'
check 2 'git push origin --follow-tags lane-a'
check 2 'git push origin mypkg-v0.2.0'
check 2 'git push origin refs/tags/v1'
check 2 'git push origin HEAD:refs/heads/main'
check 2 'git push origin "HEAD:main"'
check 2 'git -C ../wt push origin main'
check 2 'git push -uf origin lane-a'
check 2 'git push origin +lane-a'
check 2 'gh workflow run release.yml'
check 2 'echo ok; gh pr merge 3'
check 2 $'go test ./...\ngh pr merge 3'

# --- Force-with-lease is still a force push (the bug this repo was started to fix).
check 2 'git push --force-with-lease origin lane-a'
check 2 'git push --force-with-lease=lane-a origin lane-a'
check 2 'git push --force-with-lease=lane-a:abc123 origin lane-a'
check 2 'git push --force-if-includes --force-with-lease origin lane-a'
check 2 'git push origin lane-a --force-with-lease'
check 2 'git -C ../wt push --force-with-lease origin lane-a'

# --- gh api writes: any non-GET method, implicit POST from fields, GraphQL mutations.
check 2 'gh api -XPOST repos/o/r/issues/1/comments'
check 2 'gh api --method=DELETE repos/o/r/git/refs/heads/x'
check 2 'gh api repos/o/r/issues/1/comments -f body=hi'
check 2 'gh api repos/o/r/actions/workflows/ci.yml/dispatches -F ref=main'
check 2 'gh api repos/o/r/pulls --input body.json'
check 2 "gh api graphql -f query='mutation { mergePullRequest(input:{}) { clientMutationId } }'"
check 2 'gh api graphql -F query=@q.graphql'
check 2 'gh secret set TOKEN'
check 2 'gh variable set X --body 1'

# --- Wrappers and nesting do not hide a blocked command.
check 2 "bash -c 'gh pr merge 3'"
check 2 "sh -c \"git push --force-with-lease origin lane-a\""
check 2 "eval 'gh pr merge 3'"
check 2 'env GH_DEBUG=1 gh pr merge 3'
check 2 '/usr/local/bin/gh pr merge 3'
# shellcheck disable=SC2016 # literal command text, not expanded here
check 2 'echo "$(gh pr merge 3)"'
# shellcheck disable=SC2016
check 2 'echo `gh pr merge 3`'

# --- Default branch comes from harness.config.json.
check_trunk 2 'git push origin trunk'
check_trunk 2 'git push origin HEAD:refs/heads/trunk'
check_trunk 0 'git push -u origin lane-a'
# Broken config fails closed.
expect 2 "$(run "$tmp/broken" "$tmp/broken" 'ls')" '[broken config] ls'

# --- A bare push pushes the current branch: blocked on the default branch.
check_in 2 "$tmp/repo-main" 'git push'
check_in 2 "$tmp/repo-main" 'git push origin HEAD'
check_in 2 "$tmp/repo-main" 'git push -u origin'
check_in 0 "$tmp/repo-lane-a" 'git push'
check_in 0 "$tmp/repo-lane-a" 'git push -u origin HEAD'
# After a cd the guard cannot know the branch, so it asks for an explicit refspec.
check_in 2 "$tmp/repo-lane-a" 'cd ../elsewhere && git push'

# --- Allowed: normal lane work.
check 0 'git push -u origin lane-a && gh pr create --base main --title x'
check 0 'git push -u origin lane-a && gh pr create -f'
check 0 $'git commit -m "fix: x\n\nnever run gh pr merge here"'
check 0 'gh pr comment 3 --body-file /tmp/x.md'
check 0 'gh api repos/o/r/pulls/3/comments --jq length'
check 0 'gh api -X GET repos/o/r/code-scanning/alerts -f ref=refs/pull/3/head'
check 0 "gh api graphql -f query='query { viewer { login } }'"
check 0 'git push -u origin lane-main-menu'
check 0 'git push -u origin lane-a-web-ui'
check 0 'git push -u origin lane-v2-ui'
check 0 'gh pr create --base main --title x'
check 0 'gh pr view 12 --json state'
check 0 'go test ./...'
check 0 'grep -n "gh pr merge" docs/x.md'
check 0 'git tag -l'
check 0 "bash -c 'go test ./...'"

# --- Missing or bad input fails closed.
got=0; echo 'not json' | "$GUARD" >/dev/null 2>&1 || got=$?
expect 2 "$got" 'bad input'
got=0; echo '{"tool_input":{}}' | "$GUARD" >/dev/null 2>&1 || got=$?
expect 2 "$got" 'missing command'
check 2 "echo 'unbalanced"

# --- Orchestrator override: explicit, visible, and per command segment.
check 0 'HARNESS_ORCHESTRATOR=1 gh pr merge 12 --merge'
check 0 'HARNESS_ORCHESTRATOR=1 git push --force-with-lease origin lane-a'
check 2 'HARNESS_ORCHESTRATOR=1 git status && gh pr merge 12'
check 2 'HARNESS_ORCHESTRATOR=0 gh pr merge 12'

# --- Plugin mode (--require-config): inert in projects that have not opted in.
expect 0 "$(run "$tmp/plain" "$tmp/plain" 'gh pr merge 3' --require-config)" '[plugin, no config] gh pr merge'
expect 2 "$(run "$tmp/trunk" "$tmp/trunk" 'gh pr merge 3' --require-config)" '[plugin, config] gh pr merge'
expect 2 "$(run "$tmp/broken" "$tmp/broken" 'ls' --require-config)" '[plugin, broken config] ls'

[[ "$fail" == 0 ]] && echo "guard-bash: all cases pass"
exit "$fail"
