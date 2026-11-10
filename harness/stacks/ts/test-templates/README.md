# TypeScript property test template

`property.test.ts.tmpl` uses vitest + fast-check (`npm i -D fast-check`). harness-init offers it
for a pure function you name; it never writes tests for code it has not read. Placeholders:
`{{FUNC}}` (exported function) and `{{MODULE}}` (import path, e.g. `./slug`). Save it next to
the code as `<file>.property.test.ts`.

Use it for parsers, encoders, validators, sanitisers and anything that handles untrusted input
(URLs, paths, user text): state a rule that holds for every input (round trip, idempotence,
"never throws", an output invariant) and let fast-check find the counterexample. Change the
arbitrary (`fc.string()`) to match the input: `fc.json()`, `fc.webUrl()`, `fc.array(...)`,
`fc.record({...})`. Every parser, encoder or validator gets one (contract §11).
