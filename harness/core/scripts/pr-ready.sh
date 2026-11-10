#!/usr/bin/env bash
# Is PR <n> ready to merge? The orchestrator's merge gate (contract: docs/contract.md, "pr-ready gate").
#   1. every check on the latest head passed, including each harness.config.json requiredChecks entry
#   2. 0 open code-scanning alerts on refs/pull/<n>/merge AND refs/pull/<n>/head
#   3. 0 unresolved review threads started by bots
#   4. an independent review exists (author differs from the PR author, or its
#      harness:reviewer marker id differs from the PR body's harness:builder marker id)
#   5. a "## Review findings → fixes" comment was posted after the last review
#   6. the risk tier of the changed files decides whether a human APPROVED review is needed
# Any gh/API failure prints NOT READY rather than passing silently.
#
# Usage: scripts/pr-ready.sh <pr-number>
# Env:   HARNESS_CONFIG (default: harness.config.json at the repo root)
# Exit:  0 READY | 1 NOT READY | 2 usage or config error | 3 NEEDS HUMAN (all automated gates pass)
# shellcheck disable=SC2015 # pass() always succeeds, so A && pass || miss is safe
set -euo pipefail
n="${1:-}"
[[ "$n" =~ ^[0-9]+$ ]] || { echo "usage: scripts/pr-ready.sh <pr-number>" >&2; exit 2; }
here="$(cd "$(dirname "$0")" && pwd)"
root="$(git rev-parse --show-toplevel)"
config="${HARNESS_CONFIG:-$root/harness.config.json}"
[[ -f "$config" ]] || { echo "pr-ready: $config not found" >&2; exit 2; }
jq -e . "$config" >/dev/null || { echo "pr-ready: $config is not valid JSON" >&2; exit 2; }
trap 'echo "  FAIL  a gh/API call failed (line $LINENO)"; echo "NOT READY"; exit 1' ERR

repo="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
owner="${repo%/*}" name="${repo#*/}"
ok=1
pass() { printf '  ok    %s\n' "$*"; }
miss() { printf '  FAIL  %s\n' "$*"; ok=0; }

echo "PR #$n ($repo)"
pr="$(gh pr view "$n" --json author,body,reviews,comments,files)"

# 1. checks
if gh pr checks "$n" >/dev/null 2>&1; then pass "all checks passed"; else miss "checks not all passing (gh pr checks $n)"; fi
checks="$(gh pr checks "$n" --json name,bucket 2>/dev/null || echo '[]')"
while IFS= read -r want; do
  [[ -z "$want" ]] && continue
  got="$(jq -r --arg w "$want" '[.[] | select(.name == $w) | .bucket] | first // "missing"' <<<"$checks")"
  [[ "$got" == pass ]] && pass "required check '$want' passed" || miss "required check '$want': $got"
done < <(jq -r '.requiredChecks[]?' "$config")

# 2. code scanning on both PR refs
alerts=0
for ref in "refs/pull/$n/merge" "refs/pull/$n/head"; do
  c="$(gh api --paginate "repos/$repo/code-scanning/alerts?ref=$ref&state=open&per_page=100" --jq length \
    | awk '{s+=$1} END {print s+0}')"
  alerts=$((alerts + c))
done
[[ "$alerts" == 0 ]] && pass "0 open code-scanning alerts (merge + head refs)" || miss "$alerts open code-scanning alerts"

# 3. bot threads
# shellcheck disable=SC2016 # GraphQL variables, not shell
bots="$(gh api graphql -F owner="$owner" -F name="$name" -F n="$n" -f query='
  query($owner:String!,$name:String!,$n:Int!){repository(owner:$owner,name:$name){pullRequest(number:$n){
    reviewThreads(first:100){nodes{isResolved comments(first:1){nodes{author{__typename}}}}}}}}' \
  --jq '[.data.repository.pullRequest.reviewThreads.nodes[]
         | select(.isResolved | not) | select(.comments.nodes[0].author.__typename == "Bot")] | length')"
[[ "$bots" == 0 ]] && pass "0 unresolved bot review threads" || miss "$bots unresolved bot review threads"

# 4. independent review
independence="$(jq -r '.review.independence // "author-or-marker"' "$config")"
independent="$(jq --arg mode "$independence" '
  def mid($role): (capture("<!-- harness:" + $role + " id=(?<id>[A-Za-z0-9._-]+) -->") | .id) // null;
  .author.login as $pa | (.body // "" | mid("builder")) as $b
  | [.reviews[] | select(
      (.author.login != $pa)
      or ($mode == "author-or-marker" and $b != null
          and ((.body // "" | mid("reviewer")) as $r | $r != null and $r != $b)))]
  | length' <<<"$pr")"
[[ "$independent" -gt 0 ]] && pass "independent review present ($independence)" \
  || miss "no independent review (reviewer must differ from the builder: $independence)"

# 5. findings → fixes after the last review
last_review="$(jq -r '[.reviews[].submittedAt] | max // ""' <<<"$pr")"
if [[ -z "$last_review" ]]; then
  miss "no review posted"
else
  final="$(jq --arg t "$last_review" \
    '[.comments[] | select(.createdAt > $t and (.body | test("^## Review findings → fixes")))] | length' <<<"$pr")"
  [[ "$final" -gt 0 ]] && pass "'## Review findings → fixes' comment after the last review" \
    || miss "no '## Review findings → fixes' comment after the last review"
fi

# 6. risk tier → human approval
tier="$(jq -r '.files[].path' <<<"$pr" | "$here/risk-tier.py" --config "$config")"
needs_human="$(jq --arg t "$tier" '(.review.humanApprovalTiers // ["sensitive"]) | index($t) != null' "$config")"
human=0
if [[ "$needs_human" == true ]]; then
  human="$(jq '.author.login as $pa | [.reviews[] | select(.state == "APPROVED"
    and .author.login != $pa and (.author.login | endswith("[bot]") | not))] | length' <<<"$pr")"
  [[ "$human" -gt 0 ]] && pass "risk tier '$tier': human approval present" \
    || echo "  HUMAN risk tier '$tier' needs an APPROVED review from a human"
else
  pass "risk tier '$tier': no human approval required"
fi

if [[ "$ok" != 1 ]]; then echo "NOT READY"; exit 1; fi
if [[ "$needs_human" == true && "$human" == 0 ]]; then echo "NEEDS HUMAN"; exit 3; fi
echo "READY"
