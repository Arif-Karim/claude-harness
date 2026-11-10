#!/usr/bin/env bash
# Runs the same checks as CI for this repo. Must pass before you open a PR.
#   ShellCheck (if installed), python unit tests, the guard table test (both copies),
#   the adversarial guard suite,
#   plugin manifest validation (python checks + `claude plugin validate` if installed),
#   gitleaks (if installed; CI runs it as its own job).
set -euo pipefail
cd "$(dirname "$0")/.."

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

step "shellcheck"
if have shellcheck; then
  git ls-files -z '*.sh' | xargs -0 shellcheck
  echo "shellcheck: ok"
else
  echo "shellcheck not installed: skipping (CI runs it)"
fi

step "python unit tests (guard internals, risk tiers, manifests, copies in sync)"
python3 -m unittest discover -s tests -p 'test_*.py'

step "guard hook table test"
tests/guard-bash_test.sh
harness/core/hooks/guard-bash_test.sh

step "adversarial guard suite (tests/adversarial)"
tests/adversarial/run.py

step "plugin + marketplace manifests"
python3 -m json.tool .claude-plugin/plugin.json >/dev/null
python3 -m json.tool .claude-plugin/marketplace.json >/dev/null
if have claude; then
  claude plugin validate .claude-plugin/marketplace.json
  claude plugin validate . --strict
else
  echo "claude CLI not installed: skipping 'claude plugin validate' (CI runs it)"
fi

step "gitleaks (secret scan)"
if have gitleaks; then
  gitleaks git . --no-banner --redact
else
  echo "gitleaks not installed: skipping (CI runs it as its own job)"
fi

step "all checks passed"
