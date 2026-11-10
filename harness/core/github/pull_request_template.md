<!-- harness:builder id=REPLACE-WITH-BUILDER-ID -->
## What / why

## How verified
<!-- paste the tail of scripts/verify.sh (and --mutation if code changed) -->

## Risk tier
<!-- output of: git diff --name-only origin/<default>...HEAD | scripts/risk-tier.py -->

## Checklist
- [ ] Red → green commits for behaviour changes (TDD)
- [ ] `scripts/verify.sh` passes locally (and `--mutation` for code changes)
- [ ] No secrets, real customer/staff data or personal paths in code or tests
- [ ] Catch log updated if this PR fixes a bug (`docs/catch-log.md`)

## Known gaps

<!-- After review: post one comment headed "## Review findings → fixes" mapping each finding
     to its fix (commit sha) or why not. Merge gate: scripts/pr-ready.sh <n> -->
