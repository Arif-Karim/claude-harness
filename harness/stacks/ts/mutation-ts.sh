#!/usr/bin/env bash
# Mutation-tests TS files changed vs BASE_REF (default origin/<default branch>) with Stryker.
# Used by verify.sh --mutation and the CI mutation job so both apply the same gate.
# TS_DIR is the package directory (harness-init fills in the default).
set -euo pipefail
cd "$(dirname "$0")/.."

TS_DIR="${TS_DIR:-{{TS_DIR}}}"
BASE_REF="${BASE_REF:-origin/${DEFAULT_BRANCH:-main}}"
MUTATION_MIN="${MUTATION_MIN:-$(jq -r '.mutationMin // 60' harness.config.json 2>/dev/null || echo 60)}"

files=$(git diff --name-only "${BASE_REF}"...HEAD -- "$TS_DIR" 2>/dev/null \
  | grep -E '\.(ts|tsx|mts|cts)$' | grep -vE '\.(test|spec)\.' | grep -v '\.d\.ts$' || true)
if [[ -z "$files" ]]; then
  echo "no changed TS files vs ${BASE_REF}; skipping"
  exit 0
fi
prefix="${TS_DIR%/}/"; [[ "$TS_DIR" == "." ]] && prefix=""
mutate=$(echo "$files" | sed "s#^${prefix}##" | paste -sd, -)
cd "$TS_DIR"
npx --no-install stryker run --mutate "$mutate" --thresholds.break "$MUTATION_MIN"
