# shellcheck shell=bash
# Go stack fragment for scripts/verify.sh (sourced; contract: docs/contract.md, "Stacks").
# Dev tools are pinned in tools/go.mod and run with `go tool -modfile=tools/go.mod <tool>`.
gotool() { if [[ -f tools/go.mod ]]; then go tool -modfile=tools/go.mod "$@"; else "$@"; fi; }

step "go: mod tidy check"
go mod tidy -diff

step "go: golangci-lint (gofmt, govet, staticcheck, gosec, revive, errcheck)"
gotool golangci-lint run ./...

step "go: test -race with coverage (min ${COVERAGE_MIN}%)"
go test -race -covermode=atomic -coverprofile=coverage.out ./...
total=$(go tool cover -func=coverage.out | awk '/^total:/ {sub("%","",$3); print $3}')
echo "total coverage: ${total}%"
awk -v t="$total" -v m="$COVERAGE_MIN" 'BEGIN { exit (t+0 >= m+0) ? 0 : 1 }' \
  || { echo "coverage ${total}% is below ${COVERAGE_MIN}%"; exit 1; }

step "go: fuzz seed corpora (go test runs Fuzz* seeds as unit tests above; short fuzz here)"
for pkg in $(go list ./... ); do
  for f in $(go test -list '^Fuzz' "$pkg" 2>/dev/null | grep '^Fuzz' || true); do
    go test -run '^$' -fuzz "^${f}\$" -fuzztime "${FUZZTIME:-10s}" "$pkg"
  done
done

step "go: govulncheck"
gotool govulncheck ./...

if [[ "$VERIFY_MUTATION" == 1 ]]; then
  step "go: mutation testing (changed packages, efficacy >= ${MUTATION_MIN}%)"
  scripts/mutation-go.sh
fi
