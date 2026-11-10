# Catch log

One row per bug, appended by whoever fixes it. The point is to learn which safety
layer is pulling its weight and which one let the bug through.

- **Caught by** / **Should have caught**: one of `unit`, `property/fuzz`, `mutation`,
  `reviewer`, `CI-scan`, `e2e`, `human`, `prod`.
- **Follow-up**: what changed so the "should have" layer catches it next time
  (a test, a lint rule, a guard case, a review checklist item), with a PR or commit link,
  or `none` and why.
- Keep one line per row; no `|` inside cells.

| Date | Bug | Caught by | Should have caught | Follow-up |
|---|---|---|---|---|
| 2026-01-01 | Example: guard allowed `git push --force-with-lease` | reviewer | unit | Added force-with-lease cases to the guard table test (#1) |
