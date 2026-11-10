# Go fuzz and property test templates

harness-init offers one of these for a pure function you name; it never writes tests for code
it has not read. Placeholders: `{{PACKAGE}}` (package name), `{{FUNC}}` (function under test).
Put the file next to the code under test, named `<file>_fuzz_test.go` or `<file>_prop_test.go`.

| Template | Use it when | Examples |
|---|---|---|
| `fuzz_test.go.tmpl` (native `testing.F`) | The input is **bytes or a string from outside**: anything that parses, decodes or sanitises untrusted input. The fuzzer mutates raw bytes and finds panics, hangs and crashes you would not think to write. | parsers (JSON, YAML, CSV, headers, query strings), decoders, archive and path handling (zip/tar entries, `filepath.Join` of user input: no `..` escapes, no absolute paths), template rendering, anything behind an HTTP handler |
| `property_test.go.tmpl` (`pgregory.net/rapid`) | The input is **structured** (slices, structs, numbers) and you can state a rule that holds for all of them. rapid generates valid values and shrinks a failure to a minimal case. | round trips (`Decode(Encode(x)) == x`), sort/merge/dedupe invariants, money and date arithmetic, state machines, idempotent normalisers |

Rules of thumb:
- Every parser, encoder or validator gets one fuzz or property test (contract §11).
- Assert a property, not just "no panic": round trip, idempotence, an output invariant
  (valid UTF-8, length bound, path stays under its root), or equivalence with a simple
  reference implementation.
- Seed corpus: `f.Add(...)` for typical, empty, boundary and previously buggy inputs. When the
  fuzzer finds a failure, Go writes it to `testdata/fuzz/Fuzz<Name>/`; commit that file so the
  case runs on every `go test` from then on.
- `go test` runs the seeds only. `scripts/verify.sh` also runs each `Fuzz*` target for
  `FUZZTIME` (default 10s). Run longer locally: `go test -run '^$' -fuzz '^FuzzX$' -fuzztime 5m ./pkg`.
- `rapid` is a test-only dependency (`go get pgregory.net/rapid`). Without it, the template's
  comment shows the `testing/quick` fallback.
