#!/usr/bin/env bash
# Mutation-tests TS files changed vs BASE_REF (default origin/<default branch>) with Stryker.
set -euo pipefail
cd "$(dirname "$0")/.."
BASE_REF="${BASE_REF:-origin/${DEFAULT_BRANCH:-main}}"
MUTATION_MIN="${MUTATION_MIN:-60}"
TS_DIR="${TS_DIR:-.}"

files=$(git diff --name-only "${BASE_REF}"...HEAD -- "$TS_DIR" 2>/dev/null \
  | grep -E '\.(ts|tsx|vue)$' | grep -vE '\.(test|spec)\.' | grep -v '\.d\.ts$' || true)
if [[ -z "$files" ]]; then
  echo "no changed TS files vs ${BASE_REF}; skipping"
  exit 0
fi
prefix="${TS_DIR%/}/"; [[ "$TS_DIR" == "." ]] && prefix=""
mutate=$(echo "$files" | sed "s#^${prefix}##" | paste -sd, -)
(cd "$TS_DIR" && npx stryker run --mutate "$mutate" --thresholds.break "$MUTATION_MIN")
