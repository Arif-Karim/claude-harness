#!/usr/bin/env bash
# Installs or upgrades the agent harness in a target project: the deterministic file operations of
# the harness-init skill (contract: docs/contract.md §3 and §14). It never touches git or GitHub.
#
# Usage:
#   install.sh --detect --target DIR
#       Print the detected stacks and their dirs as JSON, e.g.
#       {"stacks":["go","ts"],"goDir":"backend","tsDir":"web"}
#   install.sh --target DIR --default-branch B [options]
#       --project-name NAME     CLAUDE.md title for a new CLAUDE.md (default: target dir name)
#       --stacks go,ts          go | ts | python | other (default: existing config, else detected)
#       --go-dir D, --ts-dir D  module / package dir, relative (default: installed value, else detected)
#       --required-checks a,b   (default: existing config, else verify,mutation,gitleaks;
#                               verify,gitleaks when no go/ts stack)
#       --coverage-min N, --mutation-min N   0..100 (default: existing config, else 70 / 60)
#       --e2e                   also install .github/workflows/claude-e2e.yml (opt-in)
#       --replace-pr-template   replace an existing, different .github/pull_request_template.md
#       --force                 on a first install, overwrite files that exist and differ
#       --dry-run               print what would change (with diffs) and write nothing
#
# Re-running is safe: harness-owned files are brought to this version, CLAUDE.md's managed block
# is replaced in place, the settings.json hook and harness.config.json are merged, and files the
# project owns (catch log, lint/Stryker config, a kept PR template) are left alone. Nothing is
# ever deleted. Every line of output is "<status> <path>" with status create | update |
# unchanged | skip | conflict, then a summary.
#
# Exit: 0 done | 2 usage, detection or config error | 4 conflicts on a first install (nothing written)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CORE="$(cd "$HERE/.." && pwd)"
STACKS="$(cd "$CORE/../stacks" && pwd)"

die() { echo "install: $*" >&2; exit 2; }
usage() { sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; }

target="" detect_only=0 dry=0 force=0 e2e=0 replace_pr=0
f_branch="" f_name="" f_stacks="" f_go="" f_ts="" f_checks="" f_cov="" f_mut=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) target="${2:?--target needs a value}"; shift 2 ;;
    --detect) detect_only=1; shift ;;
    --default-branch) f_branch="${2:?--default-branch needs a value}"; shift 2 ;;
    --project-name) f_name="${2:?--project-name needs a value}"; shift 2 ;;
    --stacks) f_stacks="${2:?--stacks needs a value}"; shift 2 ;;
    --go-dir) f_go="${2:?--go-dir needs a value}"; shift 2 ;;
    --ts-dir) f_ts="${2:?--ts-dir needs a value}"; shift 2 ;;
    --required-checks) f_checks="${2:?--required-checks needs a value}"; shift 2 ;;
    --coverage-min) f_cov="${2:?--coverage-min needs a value}"; shift 2 ;;
    --mutation-min) f_mut="${2:?--mutation-min needs a value}"; shift 2 ;;
    --e2e) e2e=1; shift ;;
    --replace-pr-template) replace_pr=1; shift ;;
    --force) force=1; shift ;;
    --dry-run) dry=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
done
for tool in jq python3 diff; do command -v "$tool" >/dev/null 2>&1 || die "$tool is required"; done
[[ -n "$target" ]] || die "--target is required"
[[ -d "$target" ]] || die "target $target is not a directory"
target="$(cd "$target" && pwd)"

# ---------------------------------------------------------------- validation helpers
valid_pct() { [[ "$1" =~ ^[0-9]+(\.[0-9]+)?$ ]] && awk -v v="$1" 'BEGIN { exit (v >= 0 && v <= 100) ? 0 : 1 }'; }
valid_dir() { [[ "$1" =~ ^[A-Za-z0-9._/-]+$ && "$1" != /* && ! "$1" =~ (^|/)\.\.(/|$) ]]; }
valid_name() { [[ "$1" =~ ^[A-Za-z0-9._/-]+$ ]]; }
norm_dir() { local d="${1%/}"; d="${d#./}"; echo "${d:-.}"; }
[[ -z "$f_cov" ]] || valid_pct "$f_cov" || die "--coverage-min must be a number from 0 to 100"
[[ -z "$f_mut" ]] || valid_pct "$f_mut" || die "--mutation-min must be a number from 0 to 100"
[[ -z "$f_go" ]] || valid_dir "$f_go" || die "--go-dir must be a relative path inside the project"
[[ -z "$f_ts" ]] || valid_dir "$f_ts" || die "--ts-dir must be a relative path inside the project"
[[ -z "$f_branch" ]] || git check-ref-format --branch "$f_branch" >/dev/null 2>&1 || die "bad --default-branch: $f_branch"
[[ "$f_name" != *$'\n'* ]] || die "--project-name must be one line"
if [[ -n "$f_stacks" ]]; then
  IFS=, read -r -a _s <<<"$f_stacks"
  for s in "${_s[@]}"; do [[ "$s" =~ ^(go|ts|python|other)$ ]] || die "unknown stack: $s (go|ts|python|other)"; done
fi
if [[ -n "$f_checks" ]]; then
  IFS=, read -r -a _c <<<"$f_checks"
  for c in "${_c[@]}"; do valid_name "$c" || die "bad check name: '$c'"; done
fi

# ---------------------------------------------------------------- detection
# Shallowest match wins; build output, dependencies and test data are not searched.
find_marker() { # find_marker <file name> -> repo-relative paths, shallowest first
  find "$target" -maxdepth 3 \( -name .git -o -name node_modules -o -name vendor -o -name testdata \
      -o -name dist -o -name build \) -prune -o -type f -name "$1" -print 2>/dev/null |
    sed "s#^$target/##" | awk -F/ '{ print NF "\t" $0 }' | sort -n -k1,1 -k2 | cut -f2-
}
detect_go() { local m; m="$(find_marker go.mod | head -n1)"; [[ -n "$m" ]] && dirname "$m"; return 0; }
detect_ts() {
  local m d
  while IFS= read -r m; do
    d="$(dirname "$m")"
    if [[ -f "$target/$d/tsconfig.json" ]] ||
       jq -e '(.dependencies.typescript // .devDependencies.typescript) != null' "$target/$m" >/dev/null 2>&1; then
      echo "$d"; return 0
    fi
  done < <(find_marker package.json)
  return 0
}
det_go="$(detect_go)"; det_ts="$(detect_ts)"
if [[ "$detect_only" == 1 ]]; then
  jq -cn --arg go "$det_go" --arg ts "$det_ts" '
    {stacks: ([if $go != "" then "go" else empty end, if $ts != "" then "ts" else empty end]
              | if length == 0 then ["other"] else . end)}
    + (if $go != "" then {goDir: $go} else {} end) + (if $ts != "" then {tsDir: $ts} else {} end)'
  exit 0
fi

# ---------------------------------------------------------------- resolve settings
cfg_path="$target/harness.config.json"
validate() { python3 "$HERE/validate-config.py" --schema "$CORE/harness.config.schema.json" "$1"; }
existing=0
if [[ -f "$cfg_path" ]]; then
  validate "$cfg_path" || die "existing harness.config.json is invalid; fix it first"
  existing=1
fi
cfg_get() { [[ "$existing" == 1 ]] && jq -r "$1 // empty" "$cfg_path" || true; }

if [[ -n "$f_stacks" ]]; then stacks_csv="$f_stacks"
elif [[ "$existing" == 1 ]]; then stacks_csv="$(jq -r '.stacks | join(",")' "$cfg_path")"
else
  stacks_csv="$(printf '%s\n' ${det_go:+go} ${det_ts:+ts} | paste -sd, -)"
  stacks_csv="${stacks_csv:-other}"
fi
IFS=, read -r -a STACK_LIST <<<"$stacks_csv"
has_stack() { local s; for s in "${STACK_LIST[@]}"; do [[ "$s" == "$1" ]] && return 0; done; return 1; }

branch="${f_branch:-$(cfg_get .defaultBranch)}"
[[ -n "$branch" ]] || die "--default-branch is required on a first install (gh repo view --json defaultBranchRef)"
project="${f_name:-$(basename "$target")}"

installed_dir() { # installed_dir <fragment> <VAR>: the default a previous install wrote into a fragment
  [[ -f "$target/$1" ]] && sed -n "s/^$2=\"\${$2:-\(.*\)}\"\$/\1/p" "$target/$1" | head -n1
  return 0
}
go_dir="$(norm_dir "${f_go:-$(installed_dir scripts/verify.d/20-go.sh GO_DIR)}")"
[[ "$go_dir" != "." || -n "$f_go" || -z "$det_go" ]] || go_dir="$(norm_dir "$det_go")"
ts_dir="$(norm_dir "${f_ts:-$(installed_dir scripts/verify.d/30-ts.sh TS_DIR)}")"
[[ "$ts_dir" != "." || -n "$f_ts" || -z "$det_ts" ]] || ts_dir="$(norm_dir "$det_ts")"
{ valid_dir "$go_dir" && valid_dir "$ts_dir"; } || die "bad stack dir"
if has_stack go && [[ ! -f "$target/$go_dir/go.mod" ]]; then die "stack go: no go.mod in $go_dir (use --go-dir)"; fi
if has_stack ts && [[ ! -f "$target/$ts_dir/package.json" ]]; then die "stack ts: no package.json in $ts_dir (use --ts-dir)"; fi
packs=(); for s in go ts; do has_stack "$s" && packs+=("$s"); done

# ---------------------------------------------------------------- staging
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
REL=() MODE=() EXEC=()
# Modes: own (harness-owned; updated on re-run) | once (created if absent, then the project's) |
#        merged (rebuilt from the project's current file) | optin (PR template)
add() { REL+=("$1"); MODE+=("$2"); EXEC+=("${3:-0}"); mkdir -p "$(dirname "$stage/$1")"; }

# Config first: CLAUDE.md and the workflows render from it.
if [[ "$existing" == 1 ]]; then base_cfg="$cfg_path"; else base_cfg="$CORE/harness.config.json.tmpl"; fi
if [[ -n "$f_checks" ]]; then checks_json="$(jq -cn --arg c "$f_checks" '$c | split(",")')"
elif [[ "$existing" == 1 ]]; then checks_json="$(jq -c .requiredChecks "$cfg_path")"
elif [[ ${#packs[@]} -gt 0 ]]; then checks_json='["verify","mutation","gitleaks"]'
else checks_json='["verify","gitleaks"]'; fi
add harness.config.json merged
jq --indent 2 \
  --argjson stacks "$(jq -cn --arg s "$stacks_csv" '$s | split(",")')" \
  --arg branch "$branch" --argjson checks "$checks_json" \
  --arg cov "$f_cov" --arg mut "$f_mut" '
  .stacks = $stacks | .defaultBranch = $branch | .requiredChecks = $checks
  | if $cov != "" then .coverageMin = ($cov | tonumber) else . end
  | if $mut != "" then .mutationMin = ($mut | tonumber) else . end' \
  "$base_cfg" >"$stage/harness.config.json"
if [[ "$existing" == 1 ]] && [[ "$(jq -S . "$cfg_path")" == "$(jq -S . "$stage/harness.config.json")" ]]; then
  cp "$cfg_path" "$stage/harness.config.json" # same content: keep the project's formatting
fi
validate "$stage/harness.config.json" || die "generated harness.config.json is invalid (bug)"
cov="$(jq -r '.coverageMin // 70' "$stage/harness.config.json")"
mut="$(jq -r '.mutationMin // 60' "$stage/harness.config.json")"
checks_md="$(jq -r '.requiredChecks | map("`" + . + "`") | join(", ")' "$stage/harness.config.json")"
stacks_txt="$(jq -r '.stacks | join(", ")' "$stage/harness.config.json")"

sed_esc() { printf '%s' "$1" | sed -e 's/[\/&]/\\&/g'; }
render() { # render <src> <dst>: fill the contract's placeholders (never GitHub ${{ }} expressions)
  sed -e "s/{{PROJECT_NAME}}/$(sed_esc "$project")/g" \
      -e "s/{{DEFAULT_BRANCH}}/$(sed_esc "$branch")/g" \
      -e "s/{{STACK}}/$(sed_esc "$stacks_txt")/g" \
      -e "s/{{REQUIRED_CHECKS}}/$(sed_esc "$checks_md")/g" \
      -e "s/{{COVERAGE_MIN}}/$(sed_esc "$cov")/g" \
      -e "s/{{MUTATION_MIN}}/$(sed_esc "$mut")/g" \
      -e "s/{{GO_DIR}}/$(sed_esc "$go_dir")/g" \
      -e "s/{{TS_DIR}}/$(sed_esc "$ts_dir")/g" \
      "$1" >"$2"
}
copy() { add "$1" "$2" "${4:-0}"; render "$3" "$stage/$1"; } # copy <rel> <mode> <src> [exec]

# Core scripts and hook.
copy scripts/hooks/guard-bash.py own "$CORE/hooks/guard-bash.py" 1
copy scripts/hooks/guard-bash_test.sh own "$CORE/hooks/guard-bash_test.sh" 1
for f in verify.sh pr-ready.sh risk-tier.py branch-protection.sh; do
  copy "scripts/$f" own "$CORE/scripts/$f" 1
done
copy .github/pull_request_template.md optin "$CORE/github/pull_request_template.md"

# Catch log: header only (the template's example rows are dropped).
add docs/catch-log.md once
grep -vE '^\| [0-9]{4}-[0-9]{2}-[0-9]{2} \|' "$CORE/catch-log.md" >"$stage/docs/catch-log.md"

# CLAUDE.md: the managed block, replaced in place, or appended; the rest is the project's.
add CLAUDE.md merged
render "$CORE/CLAUDE.md.tmpl" "$stage/claude.full"
awk '/<!-- harness:begin/ { on = 1 } on { print } /<!-- harness:end -->/ { on = 0 }' \
  "$stage/claude.full" >"$stage/claude.block"
if [[ ! -f "$target/CLAUDE.md" ]]; then
  cp "$stage/claude.full" "$stage/CLAUDE.md"
else
  begins=$(grep -c '<!-- harness:begin' "$target/CLAUDE.md" || true)
  ends=$(grep -c '<!-- harness:end -->' "$target/CLAUDE.md" || true)
  if [[ "$begins" == 0 && "$ends" == 0 ]]; then
    { cat "$target/CLAUDE.md"; [[ -z "$(tail -c1 "$target/CLAUDE.md")" ]] || echo; echo; cat "$stage/claude.block"; } \
      >"$stage/CLAUDE.md"
  elif [[ "$begins" == 1 && "$ends" == 1 ]]; then
    awk -v blk="$stage/claude.block" '
      /<!-- harness:begin/ { while ((getline l < blk) > 0) print l; skip = 1; next }
      skip && /<!-- harness:end -->/ { skip = 0; next }
      !skip { print }' "$target/CLAUDE.md" >"$stage/CLAUDE.md"
  else
    die "CLAUDE.md has $begins harness:begin and $ends harness:end markers; expected one pair"
  fi
fi
rm -f "$stage/claude.full" "$stage/claude.block"

# .claude/settings.json: add (or refresh) the guard hook; keep everything else.
add .claude/settings.json merged
if [[ -f "$target/.claude/settings.json" ]]; then
  jq -e 'type == "object"' "$target/.claude/settings.json" >/dev/null 2>&1 \
    || die ".claude/settings.json is not a JSON object; fix it first"
  jq --indent 2 --slurpfile ours "$CORE/settings.json" '
    ($ours[0].hooks.PreToolUse[0]) as $grp | ($grp.hooks[0]) as $h
    | def ours: (type == "object" and .args == $h.args);
      if any(.hooks.PreToolUse[]?.hooks[]?; ours)
      then .hooks.PreToolUse |= map(.hooks |= ((. // []) | map(if ours then $h else . end)))
      else .hooks.PreToolUse = ((.hooks.PreToolUse // []) + [$grp]) end' \
    "$target/.claude/settings.json" >"$stage/.claude/settings.json"
  if [[ "$(jq -S . "$target/.claude/settings.json")" == "$(jq -S . "$stage/.claude/settings.json")" ]]; then
    cp "$target/.claude/settings.json" "$stage/.claude/settings.json"
  fi
else
  jq --indent 2 . "$CORE/settings.json" >"$stage/.claude/settings.json"
fi

# Stack packs.
if has_stack go; then
  copy scripts/verify.d/20-go.sh own "$STACKS/go/verify.d/20-go.sh"
  copy scripts/mutation-go.sh own "$STACKS/go/mutation-go.sh" 1
  copy "$(norm_dir "$go_dir/.golangci.yml")" once "$STACKS/go/.golangci.yml"
fi
if has_stack ts; then
  copy scripts/verify.d/30-ts.sh own "$STACKS/ts/verify.d/30-ts.sh"
  copy scripts/mutation-ts.sh own "$STACKS/ts/mutation-ts.sh" 1
  copy "$(norm_dir "$ts_dir/stryker.config.json")" once "$STACKS/ts/stryker.config.json"
fi

# Workflows. Several packs: the first pack's file is the base and the others' marked blocks
# ("# >>> harness:<name>" ... "# <<< harness:<name>") are added inside the base's same-named blocks.
compose() { # compose <workflow file name> <out>
  local files=() p
  for p in "${packs[@]:1}"; do files+=("$STACKS/$p/workflows/$1"); done
  files+=("$STACKS/${packs[0]}/workflows/$1")
  awk -v base="${files[${#files[@]}-1]}" '
    FILENAME != base {
      if (match($0, /# >>> harness:[a-z-]+/)) { name = substr($0, RSTART + 14, RLENGTH - 14); next }
      if ($0 ~ /# <<< harness:/) { name = ""; next }
      if (name != "") blk[name] = blk[name] $0 "\n"
      next
    }
    match($0, /# <<< harness:[a-z-]+/) { printf "%s", blk[substr($0, RSTART + 14, RLENGTH - 14)] }
    { print }' "${files[@]}" >"$2"
}
wf_dest() { # wf_dest <name>: keep a workflow the project already owns; ours goes next to it
  local f="$target/.github/workflows/$1"
  if [[ -f "$f" ]] && ! head -n1 "$f" | grep -q 'harness:managed'; then
    echo "note: .github/workflows/$1 is the project's own; the harness one is harness-$1" >&2
    if grep -qE '^  (verify|mutation|gitleaks):' "$f"; then
      echo "note: .github/workflows/$1 already has a verify/mutation/gitleaks job; rename it so the" \
        "required check names stay unambiguous" >&2
    fi
    echo ".github/workflows/harness-$1"
  else
    echo ".github/workflows/$1"
  fi
}
for wf in ci.yml codeql.yml; do
  if [[ ${#packs[@]} -gt 0 ]]; then
    compose "$wf" "$stage/composed.yml"
  elif [[ "$wf" == ci.yml ]]; then
    cp "$CORE/workflows/ci.yml" "$stage/composed.yml"
  else
    continue # CodeQL needs a language pack
  fi
  copy "$(wf_dest "$wf")" own "$stage/composed.yml"
  rm -f "$stage/composed.yml"
done
if [[ "$e2e" == 1 ]]; then
  copy .github/workflows/claude-e2e.yml own "$CORE/e2e/claude-e2e.yml"
fi

# .gitignore: tool output the verify steps write.
ignores=()
has_stack go && ignores+=("coverage.out")
has_stack ts && ignores+=(".stryker-tmp/" "reports/mutation/")
if [[ ${#ignores[@]} -gt 0 ]]; then
  add .gitignore merged
  { [[ -f "$target/.gitignore" ]] && cat "$target/.gitignore"; true; } >"$stage/.gitignore"
  [[ ! -s "$stage/.gitignore" || -z "$(tail -c1 "$stage/.gitignore")" ]] || echo >>"$stage/.gitignore"
  for i in "${ignores[@]}"; do grep -qxF "$i" "$stage/.gitignore" || echo "$i" >>"$stage/.gitignore"; done
fi

# ---------------------------------------------------------------- plan
STATUS=() conflicts=0
for i in "${!REL[@]}"; do
  rel="${REL[$i]}" mode="${MODE[$i]}" dst="$target/${REL[$i]}"
  if [[ ! -e "$dst" ]]; then st=create
  elif cmp -s "$dst" "$stage/$rel" && { [[ "${EXEC[$i]}" == 0 ]] || [[ -x "$dst" ]]; }; then st=unchanged
  elif [[ "$mode" == once ]]; then st="skip"
  elif [[ "$mode" == optin && "$replace_pr" == 0 ]]; then st="skip"
  elif [[ "$mode" == own && "$existing" == 0 && "$force" == 0 ]]; then st=conflict; conflicts=$((conflicts + 1))
  else st=update; fi
  STATUS+=("$st")
done
if [[ "$conflicts" -gt 0 ]]; then
  for i in "${!REL[@]}"; do [[ "${STATUS[$i]}" == conflict ]] && echo "conflict ${REL[$i]}"; done
  echo "install: $conflicts file(s) exist and differ, and this project has no harness.config.json yet." >&2
  echo "install: nothing was written. Review them (--dry-run --force shows diffs), then re-run with --force." >&2
  exit 4
fi

# ---------------------------------------------------------------- apply
n_create=0 n_update=0 n_same=0 n_skip=0
for i in "${!REL[@]}"; do
  rel="${REL[$i]}" st="${STATUS[$i]}" dst="$target/${REL[$i]}"
  case "$st" in
    create) n_create=$((n_create + 1)); echo "create $rel" ;;
    update) n_update=$((n_update + 1)); echo "update $rel" ;;
    unchanged) n_same=$((n_same + 1)); echo "unchanged $rel" ;;
    skip)
      n_skip=$((n_skip + 1))
      if [[ "${MODE[$i]}" == once ]]; then echo "skip $rel (exists; the project owns it)"
      else echo "skip $rel (exists and differs; --replace-pr-template replaces it)"; fi ;;
  esac
  if [[ "$dry" == 1 ]]; then
    [[ "$st" == update ]] && { diff -u --label "a/$rel" --label "b/$rel" "$dst" "$stage/$rel" || true; }
    continue
  fi
  if [[ "$st" == create || "$st" == update ]]; then
    mkdir -p "$(dirname "$dst")"
    cp "$stage/$rel" "$dst"
    if [[ "${EXEC[$i]}" == 1 ]]; then chmod 755 "$dst"; fi
  fi
done
prefix=""; [[ "$dry" == 1 ]] && prefix="(dry run, nothing written) "
echo "${prefix}harness install: $n_create created, $n_update updated, $n_same unchanged, $n_skip skipped" \
  "(stacks: $stacks_txt; default branch: $branch)"
