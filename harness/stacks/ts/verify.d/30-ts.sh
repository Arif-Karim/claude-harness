# shellcheck shell=bash
# TypeScript stack fragment for scripts/verify.sh (sourced; contract: docs/contract.md, "verify and stacks").
# TS_DIR is the package directory (harness-init fills in the default; override with TS_DIR=...).
# Each check uses the package's npm script when it defines one, else the standard tool:
#   lint      -> eslint .
#   typecheck -> tsc --noEmit
#   test      -> vitest run --coverage (lines/functions/branches/statements >= COVERAGE_MIN)
#   build     -> only if the package defines it
# A missing tool fails the step: the harness fails closed.
TS_DIR="${TS_DIR:-{{TS_DIR}}}"
ts_has_script() { jq -e --arg s "$1" '.scripts[$s] // empty' package.json >/dev/null; }
ts_run() { # ts_run <npm script> <fallback command...>
  local name="$1"; shift
  if ts_has_script "$name"; then npm run --silent "$name"; else "$@"; fi
}
pushd "$TS_DIR" >/dev/null || exit 1

step "ts: install (npm ci)"
[[ -d node_modules ]] || npm ci

step "ts: eslint"
ts_run lint npx --no-install eslint .

step "ts: tsc --noEmit"
ts_run typecheck npx --no-install tsc --noEmit

step "ts: unit + property tests (vitest run --coverage, min ${COVERAGE_MIN}%)"
ts_run test npx --no-install vitest run --coverage \
  --coverage.thresholds.lines="$COVERAGE_MIN" --coverage.thresholds.functions="$COVERAGE_MIN" \
  --coverage.thresholds.branches="$COVERAGE_MIN" --coverage.thresholds.statements="$COVERAGE_MIN"

if ts_has_script build; then
  step "ts: build"
  npm run --silent build
fi

step "ts: npm audit (shipped dependencies, high+)"
npm audit --omit=dev --audit-level=high

popd >/dev/null || exit 1

if [[ "$VERIFY_MUTATION" == 1 ]]; then
  step "ts: mutation testing (Stryker, changed files, score >= ${MUTATION_MIN}%)"
  scripts/mutation-ts.sh
fi
