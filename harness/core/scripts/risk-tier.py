#!/usr/bin/env python3
"""Prints the risk tier (fast | standard | sensitive) of a change.

Usage:
  risk-tier.py [--config harness.config.json] [FILE ...]
  git diff --name-only origin/main...HEAD | risk-tier.py
  gh pr view 12 --json files --jq '.files[].path' | risk-tier.py

Each file takes the highest tier of every riskTiers.rules glob that matches it,
or riskTiers.default when none does. The change takes the highest tier of its
files (the default when there are none). Globs: `**` matches any number of
path segments, `*` and `?` stay inside one segment.

Exit 0 and print the tier; exit 2 on a missing or invalid config.
"""
import argparse
import json
import re
import sys

TIERS = ("fast", "standard", "sensitive")


def _glob_regex(glob):
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def glob_match(glob, path):
    return _glob_regex(glob).match(path) is not None


def _tier(value):
    if not isinstance(value, str) or value not in TIERS:
        raise ValueError(f"unknown tier {value!r} (want one of {', '.join(TIERS)})")
    return TIERS.index(value)


def _rules(rt):
    rules = rt.get("rules", [])
    if not isinstance(rules, list):
        raise ValueError("riskTiers.rules must be a list")
    out = []
    for r in rules:
        if not isinstance(r, dict) or not isinstance(r.get("glob"), str) or not r["glob"]:
            raise ValueError(f"bad riskTiers rule {r!r} (want {{glob: string, tier: string}})")
        out.append((r["glob"], _tier(r.get("tier"))))
    return out


def tier_for(config, files):
    rt = config.get("riskTiers") if isinstance(config, dict) else None
    if not isinstance(rt, dict) or "default" not in rt:
        raise ValueError("riskTiers.default is required")
    default = _tier(rt["default"])
    rules = _rules(rt)
    best = None
    for f in files:
        matched = [t for g, t in rules if glob_match(g, f)]
        t = max(matched) if matched else default
        best = t if best is None else max(best, t)
    return TIERS[default if best is None else best]


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="harness.config.json")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args(argv)
    try:
        with open(args.config, encoding="utf-8") as f:
            config = json.load(f)
        # Paths are taken verbatim (only the line ending is dropped): trimming spaces
        # could turn "a.md " into "a.md" and lower its tier.
        files = args.files or [ln.rstrip("\r\n") for ln in sys.stdin if ln.rstrip("\r\n")]
        print(tier_for(config, files))
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"risk-tier: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
