#!/usr/bin/env python3
"""Claude Code PreToolUse hook (Bash) for builder agents.

Blocks what only the human or the orchestrator may do: merging PRs, pushing
to the default branch, force-pushing (including --force-with-lease), pushing
tags, creating releases, running workflows, changing repo settings or secrets,
and any `gh api` write. The project's CLAUDE.md states these rules; this hook
enforces them. Branch protection on the default branch is the server-side
backstop: this is a guard against mistakes, not a security boundary.

Contract (docs/contract.md, "Guard hook"):
  stdin   PreToolUse JSON: {"tool_input": {"command": "..."}, "cwd": "...", ...}
  exit 0  allow (no output; the normal permission flow continues)
  exit 2  block; the reason goes to stderr and is shown to the agent
  Bad input, a broken harness.config.json or an internal error blocks (fails closed).

The orchestrator opts in per command segment with a visible leading
HARNESS_ORCHESTRATOR=1 assignment, e.g. `HARNESS_ORCHESTRATOR=1 gh pr merge 3`.

--require-config (plugin mode): allow everything in a project that has no
harness.config.json, so installing the plugin does not affect projects that
have not opted in.
"""
import json
import os
import re
import shlex
import subprocess
import sys

OVERRIDE_VAR = "HARNESS_ORCHESTRATOR"
CONFIG_FILE = "harness.config.json"
PUNCT = ";&|()\n"
MAX_DEPTH = 4
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
WRAPPERS = {"env", "command", "builtin", "nohup", "time", "nice", "exec", "sudo", "xargs"}
ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)
TAG_NAME = re.compile(r"([A-Za-z0-9._-]+-)?v\d+(\.\d+)*|([A-Za-z0-9._-]+-)?v\d+\.\d+\.\d+[-+].*")
PUSH_BLOCKED_OPTS = {
    "--force": "force push",
    "--force-with-lease": "force push (--force-with-lease is still a force push)",
    "--force-if-includes": "force push",
    "--tags": "pushing tags (tags start releases)",
    "--follow-tags": "pushing tags (tags start releases)",
    "--mirror": "mirror push",
    "--all": "pushing all branches",
    "--delete": "deleting remote refs",
    "--prune": "deleting remote refs",
}
PUSH_OPTS_WITH_VALUE = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
GH_API_OPTS_WITH_VALUE = {
    "-X", "--method", "-f", "--raw-field", "-F", "--field", "-H", "--header",
    "--input", "-q", "--jq", "-t", "--template", "--cache", "-p", "--preview", "--hostname",
}
GH_API_FIELD_OPTS = {"-f", "--raw-field", "-F", "--field"}


class Blocked(Exception):
    """Raised with the reason a command is blocked."""


def deny(reason):
    print(f"Blocked by agent harness: {reason}. Ask the orchestrator.", file=sys.stderr)
    sys.exit(2)


def load_config(project_dir):
    """Returns the parsed harness.config.json, or None if the project has none."""
    path = os.path.join(project_dir, CONFIG_FILE)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        raise ValueError("top level must be an object")
    branch = cfg.get("defaultBranch", "main")
    if not isinstance(branch, str) or not branch:
        raise ValueError("defaultBranch must be a non-empty string")
    return cfg


def segments(cmd):
    """Splits a shell command into simple-command token lists."""
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=PUNCT)
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    seg = []
    for tok in lex:
        if set(tok) <= set(PUNCT):
            if seg:
                yield seg
            seg = []
        else:
            seg.append(tok)
    if seg:
        yield seg


def substitutions(cmd):
    """Yields the inner text of every $(...) and `...` in cmd."""
    for m in re.finditer(r"`([^`]*)`", cmd):
        yield m.group(1)
    i = cmd.find("$(")
    while i != -1:
        depth, j = 1, i + 2
        while j < len(cmd) and depth:
            depth += {"(": 1, ")": -1}.get(cmd[j], 0)
            j += 1
        yield cmd[i + 2 : j - 1] if depth == 0 else cmd[i + 2 :]
        i = cmd.find("$(", i + 2)


class Guard:
    def __init__(self, default_branch, cwd):
        self.default_branch = default_branch
        self.cwd = cwd
        self.cd_seen = False

    def check_command(self, cmd, depth=0):
        if depth > MAX_DEPTH:
            raise Blocked("command nesting too deep to check")
        try:
            segs = list(segments(cmd))
        except ValueError:
            raise Blocked("could not parse the command (unbalanced quotes)") from None
        for inner in substitutions(cmd):
            self.check_command(inner, depth + 1)
        for seg in segs:
            self.check_segment(seg, depth)

    def check_segment(self, seg, depth):
        override = False
        while seg and ASSIGNMENT.match(seg[0]):
            name, value = ASSIGNMENT.match(seg[0]).groups()
            override = override or (name == OVERRIDE_VAR and value == "1")
            seg = seg[1:]
        if override or not seg:
            return
        while seg and os.path.basename(seg[0]) in WRAPPERS:
            seg = seg[1:]
            while seg and (seg[0].startswith("-") or ASSIGNMENT.match(seg[0])):
                seg = seg[1:]
        if not seg:
            return
        tool, args = os.path.basename(seg[0]), seg[1:]
        if tool in ("cd", "pushd", "popd"):
            self.cd_seen = True
        elif tool in SHELLS:
            self.check_shell(args, depth)
        elif tool == "eval":
            self.check_command(" ".join(args), depth + 1)
        elif tool == "git":
            self.check_git(args)
        elif tool == "gh":
            self.check_gh(args)

    def check_shell(self, args, depth):
        for i, a in enumerate(args):
            if re.match(r"^-[A-Za-z]*c[A-Za-z]*$", a) and i + 1 < len(args):
                self.check_command(args[i + 1], depth + 1)
                return

    # --- git -------------------------------------------------------------

    def check_git(self, args):
        cdir, i = None, 0
        while i < len(args) and args[i].startswith("-"):
            if args[i] in GIT_OPTS_WITH_VALUE:
                if args[i] == "-C" and i + 1 < len(args):
                    cdir = args[i + 1]
                i += 2
            else:
                i += 1
        if i < len(args) and args[i] == "push":
            self.check_push(args[i + 1 :], cdir)

    def check_push(self, args, cdir):
        positional, i = [], 0
        while i < len(args):
            a = args[i]
            if a in PUSH_OPTS_WITH_VALUE:
                i += 2
                continue
            if a.startswith("-"):
                name = a.split("=", 1)[0]
                if name in PUSH_BLOCKED_OPTS:
                    raise Blocked(f"git push {name}: {PUSH_BLOCKED_OPTS[name]}")
                if re.match(r"^-[A-Za-z]+$", a) and set(a[1:]) & set("fd"):
                    raise Blocked(f"git push {a}: force push or delete")
            else:
                positional.append(a)
            i += 1
        refs = positional[1:]  # drop the remote
        if not refs:
            self.check_current_branch(cdir)
        for r in refs:
            if r.startswith("+"):
                raise Blocked("force refspec (+)")
            if r.startswith(":"):
                raise Blocked("deleting a remote ref (:ref)")
            src, _, dst = r.partition(":")
            dst = dst or src
            if dst == "HEAD":
                self.check_current_branch(cdir)
                continue
            if dst.startswith("refs/tags/") or TAG_NAME.fullmatch(dst):
                raise Blocked("pushing tags (tags start releases)")
            if dst.removeprefix("refs/heads/") == self.default_branch:
                raise Blocked(f"pushing to the default branch '{self.default_branch}'")

    def check_current_branch(self, cdir):
        """A push without an explicit branch pushes the current branch."""
        if self.cd_seen:
            raise Blocked("bare `git push` after `cd`: push an explicit branch (git push origin <branch>)")
        where = os.path.join(self.cwd, cdir) if cdir else self.cwd
        try:
            out = subprocess.run(
                ["git", "-C", where, "symbolic-ref", "--short", "-q", "HEAD"],
                capture_output=True, text=True, timeout=5, check=True,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            out = ""
        if not out:
            raise Blocked("cannot tell which branch a bare `git push` targets: push an explicit branch")
        if out == self.default_branch:
            raise Blocked(f"pushing to the default branch '{self.default_branch}' (current branch)")

    # --- gh --------------------------------------------------------------

    def check_gh(self, args):
        while args and args[0].startswith("-"):
            args = args[2:] if args[0] in ("-R", "--repo") else args[1:]
        if not args:
            return
        sub, rest = args[0], args[1:]
        verb = rest[0] if rest else ""
        if sub == "pr" and verb == "merge":
            raise Blocked("only the human or the orchestrator merges PRs")
        if sub == "release" and verb in ("create", "edit", "delete", "upload", "delete-asset"):
            raise Blocked("releases")
        if sub == "workflow" and verb in ("run", "enable", "disable"):
            raise Blocked("running or toggling workflows")
        if sub == "repo" and verb in ("edit", "delete", "rename", "archive", "unarchive"):
            raise Blocked("changing repo settings")
        if sub in ("secret", "variable") and verb in ("set", "delete", "remove"):
            raise Blocked(f"changing repo {sub}s")
        if sub == "api":
            self.check_gh_api(rest)

    def check_gh_api(self, args):
        method, endpoint, fields, has_input = None, None, [], False
        i = 0
        while i < len(args):
            a = args[i]
            name, eq, inline = a.partition("=") if a.startswith("--") else (a, "", "")
            if a.startswith("-X") and len(a) > 2:
                method = a[2:]
            elif name in GH_API_OPTS_WITH_VALUE:
                value = inline if eq else (args[i + 1] if i + 1 < len(args) else "")
                if not eq:
                    i += 1
                if name in ("-X", "--method"):
                    method = value
                elif name in GH_API_FIELD_OPTS:
                    fields.append(value)
                elif name == "--input":
                    has_input = True
            elif re.match(r"^-[fF].+", a):
                fields.append(a[2:])
            elif not a.startswith("-") and endpoint is None:
                endpoint = a
            i += 1
        method = (method or ("POST" if fields or has_input else "GET")).upper()
        if endpoint == "graphql":
            self.check_graphql(fields, has_input)
            return
        if method not in ("GET", "HEAD"):
            raise Blocked(f"gh api {method} (writes change repo or PR state)")

    @staticmethod
    def check_graphql(fields, has_input):
        queries = [f.split("=", 1)[1] for f in fields if f.startswith("query=")]
        if has_input or not queries:
            raise Blocked("gh api graphql without an inline query cannot be checked")
        for q in queries:
            if q.startswith("@"):
                raise Blocked("gh api graphql with a query file cannot be checked")
            if re.search(r"\bmutation\b", q, re.I):
                raise Blocked("GraphQL mutations (writes)")


def main(argv):
    require_config = "--require-config" in argv[1:]
    try:
        data = json.load(sys.stdin)
        cmd = data["tool_input"]["command"]
        if not isinstance(cmd, str):
            raise TypeError("command is not a string")
    except Exception:  # noqa: BLE001 - any malformed input fails closed
        deny("could not parse hook input")
    cwd = data.get("cwd") or os.getcwd()
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or cwd
    try:
        cfg = load_config(project_dir)
    except Exception as e:  # noqa: BLE001 - a broken config fails closed
        deny(f"{CONFIG_FILE} is invalid ({e})")
    if cfg is None and require_config:
        return
    try:
        Guard((cfg or {}).get("defaultBranch", "main"), cwd).check_command(cmd)
    except Blocked as b:
        deny(str(b))
    except Exception as e:  # noqa: BLE001 - internal errors fail closed
        deny(f"guard error ({type(e).__name__}: {e})")


if __name__ == "__main__":
    main(sys.argv)
