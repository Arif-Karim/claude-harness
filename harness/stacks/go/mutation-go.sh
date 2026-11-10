#!/usr/bin/env bash
# Mutation-tests Go packages changed vs BASE_REF (default origin/<default branch>) with gremlins.
# Used by verify.sh --mutation and the CI mutation job so both apply the same gate.
set -euo pipefail
cd "$(dirname "$0")/.."

BASE_REF="${BASE_REF:-origin/${DEFAULT_BRANCH:-main}}"
MUTATION_MIN="${MUTATION_MIN:-60}"
# A generous timeout: under load gremlins otherwise marks mutants TIMED OUT,
# which are excluded from efficacy, so the gate could pass having tested nothing.
TIMEOUT_COEFFICIENT="${TIMEOUT_COEFFICIENT:-20}"
gotool() { if [[ -f tools/go.mod ]]; then go tool -modfile=tools/go.mod "$@"; else "$@"; fi; }

pkgs=$(git diff --name-only "${BASE_REF}"...HEAD -- '*.go' 2>/dev/null \
  | grep -v '_test.go$' | xargs -r -n1 dirname | sort -u || true)
if [[ -z "$pkgs" ]]; then
  echo "no changed Go packages vs ${BASE_REF}; skipping"
  exit 0
fi
for p in $pkgs; do
  echo "--- mutation: $p (efficacy >= ${MUTATION_MIN}%)"
  out=$(gotool gremlins unleash --workers "${MUTATION_WORKERS:-2}" \
    --timeout-coefficient "$TIMEOUT_COEFFICIENT" \
    --threshold-efficacy "$MUTATION_MIN" "./$p" 2>&1) || { echo "$out"; exit 1; }
  echo "$out" | tail -4
  # Efficacy ignores TIMED OUT mutants, so "Killed: 0, Lived: 0" would pass the
  # threshold vacuously. Fail if mutants existed but none were actually tested.
  if echo "$out" | grep -q 'Killed: 0, Lived: 0' && ! echo "$out" | grep -q 'Timed out: 0, Not viable: 0'; then
    echo "mutation gate: no mutants were tested in $p (all timed out/not viable); failing"
    exit 1
  fi
done
