#!/usr/bin/env bash
# Runs the same checks as CI (contract: docs/contract.md, "verify"). Usage:
#   scripts/verify.sh              # harness checks + every stack fragment + secret scan
#   scripts/verify.sh --mutation   # also mutation-test packages changed vs origin/<default branch>
# Stack fragments live in scripts/verify.d/NN-<stack>.sh and are sourced in order; they
# can use step(), have_tool(), COVERAGE_MIN, MUTATION_MIN, DEFAULT_BRANCH and VERIFY_MUTATION.
set -euo pipefail
cd "$(dirname "$0")/.."

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
have_tool() { command -v "$1" >/dev/null 2>&1; }
cfg() { jq -r "$1 // empty" harness.config.json; }
VERIFY_MUTATION=0
[[ "${1:-}" == "--mutation" ]] && VERIFY_MUTATION=1
COVERAGE_MIN="${COVERAGE_MIN:-$(cfg .coverageMin)}"; COVERAGE_MIN="${COVERAGE_MIN:-70}"
MUTATION_MIN="${MUTATION_MIN:-$(cfg .mutationMin)}"; MUTATION_MIN="${MUTATION_MIN:-60}"
DEFAULT_BRANCH="$(cfg .defaultBranch)"; DEFAULT_BRANCH="${DEFAULT_BRANCH:-main}"
export COVERAGE_MIN MUTATION_MIN DEFAULT_BRANCH VERIFY_MUTATION

step "harness: guard hook table test"
scripts/hooks/guard-bash_test.sh

if have_tool shellcheck; then
  step "shellcheck"
  git ls-files -z '*.sh' | xargs -0 -r shellcheck
fi

for fragment in scripts/verify.d/*.sh; do
  [[ -e "$fragment" ]] || continue
  # shellcheck source=/dev/null
  source "$fragment"
done

step "gitleaks (secret scan)"
if have_tool gitleaks; then
  gitleaks git . --no-banner --redact
elif [[ -n "${CI:-}" ]]; then
  echo "gitleaks not installed (CI runs it as its own job)"
else
  echo "gitleaks not installed: skipping locally (CI runs it)"
fi

step "all checks passed"
