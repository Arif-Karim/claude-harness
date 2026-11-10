#!/usr/bin/env bash
# Is PR <n> ready to merge? The orchestrator's merge gate (contract: docs/contract.md, "pr-ready gate").
#   1. every check on the latest head passed, including each harness.config.json requiredChecks entry
#   2. 0 open code-scanning alerts on refs/pull/<n>/merge AND refs/pull/<n>/head
#   3. 0 unresolved review threads started by bots
#   4. an independent review exists (author differs from the PR author, or its
#      harness:reviewer marker id differs from the PR body's harness:builder marker id)
#   5. a "## Review findings → fixes" comment was posted after the last review
#   6. the risk tier of every changed file (renames: old and new path) decides whether a human's
#      latest decisive review must be APPROVED
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
cfgerr() { echo "pr-ready: $*" >&2; exit 2; }
if [[ -n "${HARNESS_CONFIG:-}" ]]; then
  config="$HARNESS_CONFIG"
else
  root="$(git rev-parse --show-toplevel 2>/dev/null)" || cfgerr "not in a git repo (or set HARNESS_CONFIG)"
  config="$root/harness.config.json"
fi
[[ -f "$config" ]] || cfgerr "$config not found"
jq -e . "$config" >/dev/null 2>&1 || cfgerr "$config is not valid JSON"
jq -e '.requiredChecks | type == "array" and all(type == "string")' "$config" >/dev/null \
  || cfgerr "$config: requiredChecks must be a list of strings"
jq -e '(.review.humanApprovalTiers // ["sensitive"]) | type == "array" and all(type == "string")' "$config" >/dev/null \
  || cfgerr "$config: review.humanApprovalTiers must be a list of strings"
independence="$(jq -r '.review.independence // "author-or-marker"' "$config")"
[[ "$independence" == author-or-marker || "$independence" == author ]] \
  || cfgerr "$config: review.independence must be author-or-marker or author"
"$here/risk-tier.py" --config "$config" </dev/null >/dev/null || cfgerr "$config: invalid riskTiers"
trap 'echo "  FAIL  a gh/API call or its JSON failed (line $LINENO)"; echo "NOT READY"; exit 1' ERR

repo="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
owner="${repo%/*}" name="${repo#*/}"
ok=1
pass() { printf '  ok    %s\n' "$*"; }
miss() { printf '  FAIL  %s\n' "$*"; ok=0; }

echo "PR #$n ($repo)"
pr="$(gh pr view "$n" --json author,body,reviews,comments,changedFiles)"

# 1. checks
if gh pr checks "$n" >/dev/null 2>&1; then pass "all checks passed"; else miss "checks not all passing (gh pr checks $n)"; fi
# gh may exit non-zero here while checks fail or are pending; use its JSON if it is an array,
# otherwise treat every required check as missing.
checks="$(gh pr checks "$n" --json name,bucket 2>/dev/null)" || true
jq -e 'type == "array"' <<<"$checks" >/dev/null 2>&1 || checks='[]'
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

# 3. bot threads (every page of review threads)
# shellcheck disable=SC2016 # GraphQL variables, not shell
bots="$(gh api graphql --paginate -F owner="$owner" -F name="$name" -F n="$n" -f query='
  query($owner:String!,$name:String!,$n:Int!,$endCursor:String){repository(owner:$owner,name:$name){
    pullRequest(number:$n){reviewThreads(first:100,after:$endCursor){
      nodes{isResolved comments(first:1){nodes{author{__typename}}}} pageInfo{hasNextPage endCursor}}}}}' \
  --jq '[.data.repository.pullRequest.reviewThreads.nodes[]
         | select(.isResolved | not) | select(.comments.nodes[0].author.__typename == "Bot")] | length' \
  | awk '{s+=$1} END {print s+0}')"
[[ "$bots" == 0 ]] && pass "0 unresolved bot review threads" || miss "$bots unresolved bot review threads"

# 4. independent review
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
# Every changed file, from the paginated REST listing (gh pr view stops at 100 files), plus the
# old path of renames, so moving a file out of a sensitive dir still counts as sensitive.
files="$(gh api --paginate "repos/$repo/pulls/$n/files?per_page=100" --jq '.[] | {filename, previous_filename}')"
listed="$(jq -s length <<<"$files")"
changed="$(jq '.changedFiles' <<<"$pr")"
[[ "$listed" == "$changed" ]] && pass "listed all $changed changed files" \
  || miss "listed $listed of $changed changed files (the API lists at most 3000)"
tier="$(jq -rs '.[] | .filename, (.previous_filename // empty)' <<<"$files" | "$here/risk-tier.py" --config "$config")"
needs_human="$(jq --arg t "$tier" '(.review.humanApprovalTiers // ["sensitive"]) | index($t) != null' "$config")"
human=0
if [[ "$needs_human" == true ]]; then
  # Each human's latest decisive review (APPROVED / CHANGES_REQUESTED / DISMISSED) counts;
  # comment-only reviews do not cancel an approval.
  human="$(jq '.author.login as $pa
    | [.reviews[] | select(.state != "COMMENTED" and .state != "PENDING")
       | select((.author.login // "") as $l | $l != "" and $l != $pa and ($l | endswith("[bot]") | not))]
    | group_by(.author.login) | map(max_by(.submittedAt)) | map(select(.state == "APPROVED")) | length' <<<"$pr")"
  [[ "$human" -gt 0 ]] && pass "risk tier '$tier': human approval present" \
    || echo "  HUMAN risk tier '$tier' needs an APPROVED review from a human"
else
  pass "risk tier '$tier': no human approval required"
fi

if [[ "$ok" != 1 ]]; then echo "NOT READY"; exit 1; fi
if [[ "$needs_human" == true && "$human" == 0 ]]; then echo "NEEDS HUMAN"; exit 3; fi
echo "READY"
