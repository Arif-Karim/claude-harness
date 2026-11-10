#!/usr/bin/env python3
"""Claude Code PreToolUse hook (Bash) for builder agents.

Blocks what only the human or the orchestrator may do: merging PRs, pushing
to main, force-pushing, pushing tags (tags start releases), creating
releases, running workflows and changing repo settings. CLAUDE.md states
these rules; this enforces them. Branch protection on main is the
server-side backstop; this is a guard against mistakes, not a security
boundary.

The orchestrator opts in per command with a visible HARNESS_ORCHESTRATOR=1
prefix. Exit 2 blocks the call and shows the reason to the agent. Bad input
fails closed.
"""
import json
import re
import shlex
import sys

OVERRIDE = "HARNESS_ORCHESTRATOR=1"
SEPARATORS = {";", "&&", "||", "|", "&", "\n", "(", ")"}
TAG_REF = re.compile(r"(^|[:/])(refs/tags/|([a-z0-9-]+-)?v\d)")
MAIN_REF = re.compile(r"(^|:)(refs/heads/)?main$")


def block(reason):
    print(f"Blocked by agent harness: {reason}. Ask the orchestrator.", file=sys.stderr)
    sys.exit(2)


def segments(cmd):
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|()\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    seg = []
    for tok in lex:
        if tok in SEPARATORS or set(tok) <= set(";&|()\n"):
            if seg:
                yield seg
            seg = []
        else:
            seg.append(tok)
    if seg:
        yield seg


def strip_prefix(seg):
    # Leading VAR=value assignments and `git -C dir` don't change the command.
    while seg and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", seg[0]):
        seg = seg[1:]
    if len(seg) >= 3 and seg[0] == "git" and seg[1] == "-C":
        seg = ["git"] + seg[3:]
    return seg


def check_push(args):
    opts = [a for a in args if a.startswith("-")]
    refs = [a for a in args if not a.startswith("-")][1:]  # drop the remote
    for o in opts:
        if o in ("--force", "--tags", "--follow-tags", "--mirror", "--all", "--delete") or (
            re.match(r"^-[a-zA-Z]+$", o) and set(o[1:]) & set("fd")
        ):
            block(f"git push {o} (use --force-with-lease on your own lane branch)")
    for r in refs:
        if r.startswith("+"):
            block("force refspec (+)")
        if MAIN_REF.search(r):
            block("pushing to main")
        if TAG_REF.search(r):
            block("pushing tags (tags start releases)")


def check(seg):
    seg = strip_prefix(seg)
    if len(seg) < 2:
        return
    tool, sub = seg[0], seg[1]
    rest = seg[2:]
    if tool == "git" and sub == "push":
        check_push(rest)
    if tool != "gh":
        return
    if sub == "pr" and rest[:1] == ["merge"]:
        block("only the human or orchestrator merges PRs")
    if sub == "release" and rest[:1] and rest[0] in ("create", "edit", "delete", "upload"):
        block("releases")
    if sub == "workflow" and rest[:1] and rest[0] in ("run", "enable", "disable"):
        block("running or toggling workflows")
    if sub == "repo" and rest[:1] and rest[0] in ("edit", "delete", "rename", "archive"):
        block("changing repo settings")
    if sub == "api":
        method = None
        for i, a in enumerate(rest):
            if a in ("-X", "--method") and i + 1 < len(rest):
                method = rest[i + 1].upper()
            elif a.startswith("-X") and len(a) > 2:
                method = a[2:].upper()
            elif a.startswith("--method="):
                method = a.split("=", 1)[1].upper()
        path = " ".join(a for a in rest if not a.startswith("-"))
        if re.search(r"/merge\b|/protection|/rulesets", path) and method not in (None, "GET"):
            block("merging or changing protection via the API")
        if method in ("PUT", "PATCH", "DELETE"):
            block(f"gh api {method} (repo/PR state changes)")


def main():
    try:
        cmd = json.load(sys.stdin)["tool_input"]["command"]
    except Exception:  # noqa: BLE001 - any malformed input fails closed
        block("could not parse hook input")
    if cmd.startswith(OVERRIDE + " "):
        return
    try:
        segs = list(segments(cmd))
    except ValueError:
        block("could not parse the command (unbalanced quotes)")
    for seg in segs:
        check(seg)


if __name__ == "__main__":
    main()
