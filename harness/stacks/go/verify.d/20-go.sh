# shellcheck shell=bash
# Go stack fragment for scripts/verify.sh (sourced; contract: docs/contract.md, "verify and stacks").
# GO_DIR is the Go module directory (harness-init fills in the default; override with GO_DIR=...).
# Dev tools are pinned in $GO_DIR/tools/go.mod and run with `go tool -modfile=tools/go.mod <tool>`;
# without that file they must be on PATH. A missing tool fails the step: the harness fails closed.
GO_DIR="${GO_DIR:-{{GO_DIR}}}"
gotool() { if [[ -f tools/go.mod ]]; then go tool -modfile=tools/go.mod "$@"; else "$@"; fi; }
pushd "$GO_DIR" >/dev/null || exit 1

step "go: mod tidy check"
go mod tidy -diff

step "go: gofmt"
unformatted=$(gofmt -l . | grep -v '^vendor/' || true)
if [[ -n "$unformatted" ]]; then
  echo "gofmt needed on:"; echo "$unformatted"; exit 1
fi

step "go: golangci-lint (govet, staticcheck, gosec, revive, errcheck, goimports)"
gotool golangci-lint run ./...

step "go: test -race with coverage (min ${COVERAGE_MIN}%)"
go test -race -covermode=atomic -coverprofile=coverage.out ./...
total=$(go tool cover -func=coverage.out | awk '/^total:/ {sub("%","",$3); print $3}')
echo "total coverage: ${total:-0}%"
awk -v t="${total:-0}" -v m="$COVERAGE_MIN" 'BEGIN { exit (t+0 >= m+0) ? 0 : 1 }' \
  || { echo "coverage ${total:-0}% is below ${COVERAGE_MIN}%"; exit 1; }

step "go: short -fuzz run of every Fuzz* target (FUZZTIME=${FUZZTIME:-10s}; seeds already ran above)"
for pkg in $(go list ./...); do
  for f in $(go test -list '^Fuzz' "$pkg" 2>/dev/null | grep '^Fuzz' || true); do
    go test -run '^$' -fuzz "^${f}\$" -fuzztime "${FUZZTIME:-10s}" "$pkg"
  done
done

step "go: govulncheck"
gotool govulncheck ./...

popd >/dev/null || exit 1

if [[ "$VERIFY_MUTATION" == 1 ]]; then
  step "go: mutation testing (gremlins, changed packages, efficacy >= ${MUTATION_MIN}%)"
  scripts/mutation-go.sh
fi
