# shellcheck shell=bash
# TypeScript stack fragment for scripts/verify.sh (sourced; contract: docs/contract.md, "Stacks").
# TS_DIR is the package directory (default "."). Expects npm scripts: lint, typecheck, test, build;
# `test` runs vitest (or jest) with coverage thresholds set from COVERAGE_MIN in the test config.
TS_DIR="${TS_DIR:-.}"

step "ts: install (npm ci)"
(cd "$TS_DIR" && { [[ -d node_modules ]] || npm ci; })

step "ts: lint, typecheck, unit + property tests, build"
(cd "$TS_DIR" && npm run lint && npm run typecheck && npm test && npm run build)

step "ts: npm audit (shipped dependencies, high+)"
(cd "$TS_DIR" && npm audit --omit=dev --audit-level=high)

if [[ "$VERIFY_MUTATION" == 1 ]]; then
  step "ts: mutation testing (Stryker, changed files, score >= ${MUTATION_MIN}%)"
  scripts/mutation-ts.sh
fi
