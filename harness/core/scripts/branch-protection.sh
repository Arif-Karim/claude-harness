#!/usr/bin/env bash
# Turns on branch protection for the default branch (contract: docs/contract.md, "branch-protection.sh"):
# required status checks (strict), enforce_admins, no force pushes, no deletions,
# conversation resolution. No required-approval count: agents and the human often
# share one GitHub account, which cannot approve its own PRs; pr-ready.sh's risk
# tiers decide when a human approval is needed.
#
# Usage: HARNESS_ORCHESTRATOR=1 scripts/branch-protection.sh [--repo owner/name] [--branch b] [--check NAME]... [--dry-run]
#   --check may repeat; defaults to harness.config.json requiredChecks.
#   --branch defaults to harness.config.json defaultBranch, then the repo default branch.
#   --dry-run prints the API payload and changes nothing (no override needed).
# Env:  HARNESS_CONFIG (default: harness.config.json at the repo root; if set, the file must exist)
# Exit: 0 applied (or printed) | 1 gh/API failure | 2 usage or config error | 3 refused (no HARNESS_ORCHESTRATOR=1)
set -euo pipefail
repo="" branch="" dry=0 checks=()
usage() { echo "branch-protection: $*" >&2; exit 2; }
need() { [[ $# -ge 2 && -n "$2" ]] || usage "$1 needs a value"; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) need "$@"; repo="$2"; shift 2 ;;
    --branch) need "$@"; branch="$2"; shift 2 ;;
    --check) need "$@"; checks+=("$2"); shift 2 ;;
    --dry-run) dry=1; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) usage "unknown argument: $1" ;;
  esac
done

if [[ -n "${HARNESS_CONFIG:-}" ]]; then
  config="$HARNESS_CONFIG"
  [[ -f "$config" ]] || usage "HARNESS_CONFIG=$config not found"
else
  config="$(git rev-parse --show-toplevel 2>/dev/null || pwd)/harness.config.json"
fi
if [[ -f "$config" ]]; then
  jq -e . "$config" >/dev/null 2>&1 || usage "$config is not valid JSON"
  jq -e '(.requiredChecks // []) | type == "array" and all(type == "string")' "$config" >/dev/null \
    || usage "$config: requiredChecks must be a list of strings"
  [[ -n "$branch" ]] || branch="$(jq -r '.defaultBranch // ""' "$config")"
  if [[ ${#checks[@]} -eq 0 ]]; then
    while IFS= read -r c; do [[ -n "$c" ]] && checks+=("$c"); done < <(jq -r '.requiredChecks[]?' "$config")
  fi
fi
[[ ${#checks[@]} -gt 0 ]] || usage "no required checks (use --check or harness.config.json)"

payload="$(printf '%s\n' "${checks[@]}" | jq -R . | jq -s '{
  required_status_checks: {strict: true, checks: map({context: .})},
  enforce_admins: true,
  required_pull_request_reviews: null,
  restrictions: null,
  required_linear_history: false,
  allow_force_pushes: false,
  allow_deletions: false,
  required_conversation_resolution: true
}')"

if [[ "$dry" == 1 ]]; then
  echo "branch: ${branch:-<repo default>}"
  echo "$payload"
  exit 0
fi
if [[ "${HARNESS_ORCHESTRATOR:-}" != 1 ]]; then
  echo "branch-protection: refusing without HARNESS_ORCHESTRATOR=1 (orchestrator-only; ask the user first)" >&2
  exit 3
fi
[[ -n "$repo" ]] || repo="$(gh repo view --json nameWithOwner -q .nameWithOwner)" || exit 1
[[ -n "$branch" ]] || branch="$(gh repo view "$repo" --json defaultBranchRef -q .defaultBranchRef.name)" || exit 1

echo "protecting $repo:$branch (checks: ${checks[*]})"
gh api -X PUT "repos/$repo/branches/$branch/protection" -H "Accept: application/vnd.github+json" --input - <<<"$payload" >/dev/null || exit 1
gh api "repos/$repo/branches/$branch/protection" --jq \
  '{checks: [.required_status_checks.checks[].context], strict: .required_status_checks.strict,
    enforce_admins: .enforce_admins.enabled, force_pushes: .allow_force_pushes.enabled,
    deletions: .allow_deletions.enabled, conversation_resolution: .required_conversation_resolution.enabled}' || exit 1
