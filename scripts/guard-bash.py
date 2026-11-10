#!/usr/bin/env python3
"""Claude Code PreToolUse hook (Bash) for builder agents.

Blocks what only the human or the orchestrator may do: merging PRs, pushing
to the default branch, force-pushing (including --force-with-lease), pushing
tags, creating releases, running workflows, changing repo settings or secrets,
and any `gh api` (or curl/wget/httpie to the GitHub API) write. It also blocks
Bash writes to the files that keep the harness in force (.claude/settings*.json,
the guard itself, hooks/hooks.json, harness.config.json, .git/config and
.git/hooks/), git config that changes what a push or a hook does, and
constructs it cannot see through (a program piped into a shell, a script
written and run in the same command, xargs supplying gh/git arguments).

The project's CLAUDE.md states these rules; this hook enforces them. Branch
protection on the default branch is the server-side backstop: this is a guard
against mistakes, not a security boundary. docs/threat-model.md lists what it
cannot see.

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
import glob as globmod
import json
import os
import posixpath
import re
import shlex
import subprocess
import sys

OVERRIDE_VAR = "HARNESS_ORCHESTRATOR"
CONFIG_FILE = "harness.config.json"
MAX_DEPTH = 4
MAX_COMMAND = 256 * 1024
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\+?=(.*)$", re.S)
TAG_NAME = re.compile(r"([A-Za-z0-9._-]+-)?v\d+(\.\d+)*|([A-Za-z0-9._-]+-)?v\d+\.\d+\.\d+[-+].*")
DURATION = re.compile(r"^\d+(\.\d+)?[smhd]?$")
PYTHON = re.compile(r"^(python|pypy)(\d+(\.\d+)*)?$")

SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "mksh", "ash", "fish"}
RESERVED = {"!", "{", "}", "if", "then", "else", "elif", "fi", "do", "done", "while", "until", "coproc"}
# Wrapper -> options that take a value. The wrapper and its options are stripped to find the command.
WRAPPERS = {
    "env": {"-u", "--unset", "-C", "--chdir", "-P"},
    "command": set(), "builtin": set(), "nohup": set(), "noglob": set(), "nocorrect": set(),
    "time": {"-o", "-f", "--output", "--format"},
    "nice": {"-n", "--adjustment"},
    "exec": {"-a"},
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T", "--user", "--group", "--host",
             "--prompt", "--chdir", "--role", "--type", "--other-user", "--command-timeout"},
    "doas": {"-u", "-C"},
    "xargs": {"-I", "-L", "-n", "-P", "-s", "-E", "-d", "-a", "--max-args", "--max-procs", "--max-chars",
              "--delimiter", "--arg-file", "--eof", "--replace", "--max-lines", "--process-slot-var"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "caffeinate": {"-w", "-t"},
    "setsid": set(), "unbuffer": set(), "chronic": set(),
    "ionice": {"-c", "-n", "-p", "--class", "--classdata"},
}
INTERPRETER_CODE_FLAGS = {  # interpreter -> flags whose value is inline code
    "perl": {"-e", "-E"}, "ruby": {"-e"}, "node": {"-e", "--eval", "-p", "--print"},
    "nodejs": {"-e", "--eval", "-p", "--print"}, "bun": {"-e", "--eval", "-p", "--print"},
    "deno": {"eval"}, "php": {"-r"}, "lua": {"-e"}, "osascript": {"-e"}, "rscript": {"-e"},
    "pwsh": {"-c", "-command", "-encodedcommand", "-e", "-ec"},
    "powershell": {"-c", "-command", "-encodedcommand", "-e", "-ec"},
    "tclsh": set(), "jshell": set(),
}
AWKS = {"awk", "gawk", "nawk", "mawk"}
HTTP_CLIENTS = {"curl", "wget", "http", "https", "xh", "xhs"}
WRITE_ALL_ARGS = {"tee", "rm", "unlink", "rmdir", "shred", "truncate", "chmod", "chown", "chgrp", "chflags",
                  "xattr", "setfacl", "vi", "vim", "nvim", "ex", "ed", "nano", "emacs", "pico", "micro", "joe"}
COPIERS = {"cp", "mv", "ln", "install", "rsync", "ditto", "scp", "gcp", "gmv"}
STDIN_RUNNERS = {"at", "batch"}
PIPE_DECODE_HINT = "write the program to a file the human can read, or pass it with -c"

PUSH_BLOCKED_OPTS = {
    "--force": "force push",
    "--force-with-lease": "force push (--force-with-lease is still a force push)",
    "--force-if-includes": "force push",
    "--tags": "pushing tags (tags start releases)",
    "--follow-tags": "pushing tags (tags start releases)",
    "--mirror": "mirror push",
    "--all": "pushing all branches",
    "--branches": "pushing all branches",
    "--delete": "deleting remote refs",
    "--prune": "deleting remote refs",
}
PUSH_OPTS_WITH_VALUE = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
PUSH_OPTS_RUN = {"--receive-pack", "--exec"}
GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--attr-source", "--config-env"}
GIT_SENSITIVE_KEYS = re.compile(
    r"^(alias\..*|core\.hookspath|remote\..*\.(push|mirror)|push\..*|include\.path|includeif\..*)$")
GIT_CONFIG_ENV = re.compile(r"^GIT_CONFIG_(COUNT|KEY_\d+|VALUE_\d+|PARAMETERS)$")
GIT_PATH_WRITERS = {"checkout", "restore", "rm", "mv"}

GH_KNOWN = {
    "auth", "browse", "codespace", "cs", "gist", "issue", "org", "pr", "project", "release", "repo", "cache",
    "run", "workflow", "co", "agent-task", "alias", "api", "attestation", "completion", "config", "copilot",
    "extension", "extensions", "ext", "gpg-key", "label", "licenses", "preview", "ruleset", "rs", "search",
    "secret", "ssh-key", "status", "variable", "help", "version", "accessibility", "actions", "environment",
    "exit-codes", "formatting", "mintty", "reference",
}
GH_BLOCKED = {
    "pr": ({"merge"}, "only the human or the orchestrator merges PRs"),
    "release": ({"create", "new", "edit", "delete", "upload", "delete-asset"}, "releases"),
    "workflow": ({"run", "enable", "disable"}, "running or toggling workflows"),
    "repo": ({"edit", "delete", "rename", "archive", "unarchive", "sync"}, "changing repo settings or branches"),
    "secret": ({"set", "delete", "remove"}, "changing repo secrets"),
    "variable": ({"set", "delete", "remove"}, "changing repo variables"),
}
GH_API_OPTS_WITH_VALUE = {
    "-X", "--method", "-f", "--raw-field", "-F", "--field", "-H", "--header",
    "--input", "-q", "--jq", "-t", "--template", "--cache", "-p", "--preview", "--hostname",
}
GH_API_FIELD_OPTS = {"-f", "--raw-field", "-F", "--field"}

# Files that keep the harness in force. Matched case-insensitively as a path or a path suffix.
PROTECTED_FILES = (".claude/settings.json", ".claude/settings.local.json", "scripts/guard-bash.py",
                   "scripts/hooks/guard-bash.py", "hooks/hooks.json", CONFIG_FILE, ".git/config")
PROTECTED_DIRS = (".git/hooks",)
# Project-relative directories whose removal or replacement removes a protected file.
PROTECTED_ANCESTORS = {".claude", "scripts", "scripts/hooks", "hooks", ".git", ".git/hooks"}
PROTECTED_BASENAMES = {posixpath.basename(p) for p in PROTECTED_FILES}
GIT_HOOKS = {
    "applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit", "pre-merge-commit",
    "prepare-commit-msg", "commit-msg", "post-commit", "pre-rebase", "post-checkout", "post-merge",
    "pre-push", "pre-receive", "update", "proc-receive", "post-receive", "post-update",
    "reference-transaction", "push-to-checkout", "pre-auto-gc", "post-rewrite", "sendemail-validate",
    "fsmonitor-watchman", "p4-changelist", "p4-prepare-changelist", "p4-post-changelist", "p4-pre-submit",
    "post-index-change",
}
# Inline code (python -c, perl -e, ...) that mentions any of these cannot be checked, so it is blocked.
CODE_RISK = re.compile(
    r"(?<![\w.-])(gh|git)(?![\w-])|git-(push|send-pack)|api\.github\.com|/api/v3/|/api/graphql"
    r"|\.claude[/\\]+settings|guard-bash|\.git[/\\]+(hooks|config)|hooks\.json|harness\.config\.json"
    r"|hookspath", re.I)
GITHUB_API = re.compile(r"api\.github\.com|/api/v3(/|$)|/api/graphql", re.I)


class Blocked(Exception):
    """Raised with the reason a command is blocked."""


class ParseError(Exception):
    """The command could not be parsed."""


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


# --- lexer -------------------------------------------------------------------
#
# A small bash lexer: quotes, escapes, $'...', comments, operators, redirections,
# here-docs, and $(...) / `...` / <(...) substitutions. It is not a full bash
# parser; anything it cannot make sense of raises ParseError, and the guard blocks.


class Word:
    """One shell word after quote removal.

    dynamic: its value is only known at run time ($var, $(...), globs, brace expansion, {}).
    quoted:  some part of it was quoted or escaped.
    procsub: it is a <(...) or >(...) process substitution.
    """

    __slots__ = ("text", "dynamic", "quoted", "procsub", "glob", "assign_ok")

    def __init__(self, text, dynamic=False, quoted=False, procsub=False, glob=False, assign_ok=False):
        self.text, self.dynamic, self.quoted = text, dynamic, quoted
        self.procsub, self.glob, self.assign_ok = procsub, glob, assign_ok

    def __repr__(self):
        return f"Word({self.text!r}{', dynamic' if self.dynamic else ''})"


def W(x):
    return x if isinstance(x, Word) else Word(x)


class Seg:
    """One simple command: words, redirections [(op, Word)], here-doc/here-string bodies, pipe input."""

    __slots__ = ("words", "redirs", "bodies", "pipe_in")

    def __init__(self, pipe_in=False):
        self.words, self.redirs, self.bodies, self.pipe_in = [], [], [], pipe_in


OPERATORS = (";;&", ";;", ";&", "&&", "||", "|&", "&>>", "&>", ";", "&", "|", "(", ")")
REDIRECTS = ("<<<", "<<-", "<<", "<>", "<&", ">>", ">|", ">&", "<", ">")
ANSI_C = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f",
          "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}


def find_close(s, i):
    """s[i:] follows an opening '('. Returns the index of the matching ')'."""
    depth = 1
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = s.find("'", i + 1)
            if j < 0:
                raise ParseError("unbalanced quotes")
            i = j + 1
            continue
        if c == '"':
            i = skip_double(s, i + 1)
            continue
        if c == "`":
            j = find_backtick(s, i + 1)
            i = j + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ParseError("unterminated $(...)")


def skip_double(s, i):
    """s[i:] follows an opening '"'. Returns the index after the closing '"'."""
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == '"':
            return i + 1
        if c == "$" and i + 1 < n and s[i + 1] == "(":
            i = find_close(s, i + 2) + 1
            continue
        if c == "`":
            i = find_backtick(s, i + 1) + 1
            continue
        i += 1
    raise ParseError("unbalanced quotes")


def find_backtick(s, i):
    n = len(s)
    while i < n:
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == "`":
            return i
        i += 1
    raise ParseError("unterminated `...`")


def find_brace(s, i):
    """s[i:] follows '${'. Returns the index of the matching '}'."""
    depth, n = 1, len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = s.find("'", i + 1)
            i = (j if j >= 0 else n) + 1
            continue
        if c == '"':
            i = skip_double(s, i + 1)
            continue
        if c == "$" and i + 1 < n and s[i + 1] == "(":
            i = find_close(s, i + 2) + 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ParseError("unterminated ${...}")


def decode_ansi_c(s, i):
    """s[i:] follows "$'". Returns (decoded text, index after the closing quote)."""
    out, n = [], len(s)
    while i < n:
        c = s[i]
        if c == "'":
            return "".join(out), i + 1
        if c != "\\" or i + 1 >= n:
            out.append(c)
            i += 1
            continue
        e = s[i + 1]
        if e in ANSI_C:
            out.append(ANSI_C[e])
            i += 2
        elif e in "xuU":
            width = {"x": 2, "u": 4, "U": 8}[e]
            m = re.match(r"[0-9A-Fa-f]{1,%d}" % width, s[i + 2:])
            if m:
                out.append(chr(int(m.group(0), 16)))
                i += 2 + len(m.group(0))
            else:
                out.append("\\" + e)
                i += 2
        elif e in "01234567":
            m = re.match(r"[0-7]{1,3}", s[i + 1:])
            out.append(chr(int(m.group(0), 8)))
            i += 1 + len(m.group(0))
        elif e == "c" and i + 2 < n:
            out.append(chr(ord(s[i + 2]) & 0x1F))
            i += 3
        else:
            out.append("\\" + e)
            i += 2
    raise ParseError("unbalanced quotes")


def heredoc_substitutions(body):
    """$(...) and `...` inside an unquoted here-doc body run when the here-doc is read."""
    subs, i, n = [], 0, len(body)
    while i < n:
        c = body[i]
        if c == "\\":
            i += 2
        elif c == "$" and i + 1 < n and body[i + 1] == "(":
            j = find_close(body, i + 2)
            subs.append((body[i + 2:j], False))
            i = j + 1
        elif c == "`":
            j = find_backtick(body, i + 1)
            subs.append((body[i + 1:j], False))
            i = j + 1
        else:
            i += 1
    return subs


def lex(cmd):
    """Splits cmd into simple commands. Returns (segments, substitutions [(text, lenient)])."""
    segs, subs = [], []
    cur = Seg()
    buf = None  # current word: dict(text=[...], dynamic, quoted, procsub, glob, brace, first_quote)
    pending = None  # redirection operator waiting for its target word
    heredocs = []  # (seg, delimiter, strip_tabs, quoted) waiting for the next newline
    i, n = 0, len(cmd)

    def start():
        nonlocal buf
        if buf is None:
            buf = {"text": [], "dynamic": False, "quoted": False, "procsub": False, "glob": False,
                   "brace": False, "first_quote": None}
        return buf

    def add(text, quoted=False):
        b = start()
        if quoted and b["first_quote"] is None:
            b["first_quote"] = sum(len(t) for t in b["text"])
        b["text"].append(text)
        b["quoted"] = b["quoted"] or quoted

    def end_word():
        nonlocal buf, pending
        if buf is None:
            return
        text = "".join(buf["text"])
        dynamic = buf["dynamic"] or buf["glob"] or "{}" in text
        if buf["brace"] and ("," in text or ".." in text):
            dynamic = True
        m = ASSIGNMENT.match(text)
        fq = buf["first_quote"]
        assign_ok = bool(m) and (fq is None or fq >= text.index("=") + 1)
        w = Word(text, dynamic, buf["quoted"], buf["procsub"], buf["glob"], assign_ok)
        buf = None
        if pending:
            cur.redirs.append((pending, w))
            if pending in ("<<", "<<-"):
                heredocs.append((cur, w.text, pending == "<<-", w.quoted))
            pending = None
        else:
            cur.words.append(w)

    def end_seg(op):
        nonlocal cur
        end_word()
        if pending:
            raise ParseError("redirection without a target")
        if cur.words or cur.redirs:
            segs.append(cur)
        cur = Seg(pipe_in=op in ("|", "|&"))

    while i < n:
        c = cmd[i]
        if c in " \t":
            end_word()
            i += 1
        elif c == "\n":
            end_seg("\n")
            i += 1
            for seg, delim, strip, quoted in heredocs:
                lines = []
                while i < n:
                    j = cmd.find("\n", i)
                    line = cmd[i:] if j < 0 else cmd[i:j]
                    i = n if j < 0 else j + 1
                    if (line.lstrip("\t") if strip else line) == delim:
                        break
                    lines.append(line)
                body = "\n".join(lines)
                seg.bodies.append(body)
                if not quoted:
                    subs.extend(heredoc_substitutions(body))
            heredocs = []
        elif c == "#" and buf is None:
            j = cmd.find("\n", i)
            i = n if j < 0 else j
        elif c in ";&|()":
            op = next(o for o in OPERATORS if cmd.startswith(o, i))
            i += len(op)
            if op in ("&>", "&>>"):
                end_word()
                pending = op
            else:
                end_seg(op)
        elif c in "<>" and i + 1 < n and cmd[i + 1] == "(":
            j = find_close(cmd, i + 2)
            subs.append((cmd[i + 2:j], False))
            b = start()
            b["text"].append("/dev/fd/63")
            b["dynamic"] = b["procsub"] = True
            i = j + 1
        elif c in "<>":
            if buf is not None and "".join(buf["text"]).isdigit() and not buf["quoted"]:
                buf = None  # an fd number such as the 2 in 2>
            else:
                end_word()
            op = next(o for o in REDIRECTS if cmd.startswith(o, i))
            i += len(op)
            if pending:
                raise ParseError("redirection without a target")
            pending = op
        elif c == "\\":
            if i + 1 < n and cmd[i + 1] == "\n":
                i += 2  # line continuation
            elif i + 1 < n:
                add(cmd[i + 1], quoted=True)
                i += 2
            else:
                add("\\")
                i += 1
        elif c == "'":
            j = cmd.find("'", i + 1)
            if j < 0:
                raise ParseError("unbalanced quotes")
            add(cmd[i + 1:j], quoted=True)
            i = j + 1
        elif c == "$" and i + 1 < n and cmd[i + 1] == "'":
            text, i = decode_ansi_c(cmd, i + 2)
            add(text, quoted=True)
        elif c == '"' or (c == "$" and i + 1 < n and cmd[i + 1] == '"'):
            i += 1 if c == '"' else 2
            start()
            if buf["first_quote"] is None:
                buf["first_quote"] = sum(len(t) for t in buf["text"])
            buf["quoted"] = True
            while True:
                if i >= n:
                    raise ParseError("unbalanced quotes")
                d = cmd[i]
                if d == '"':
                    i += 1
                    break
                if d == "\\" and i + 1 < n:
                    buf["text"].append(cmd[i + 1] if cmd[i + 1] in '$`"\\\n' else "\\" + cmd[i + 1])
                    i += 2
                elif d == "$" or d == "`":
                    i = lex_dollar(cmd, i, buf, subs)
                else:
                    buf["text"].append(d)
                    i += 1
        elif c == "$" or c == "`":
            start()
            i = lex_dollar(cmd, i, buf, subs)
        else:
            b = start()
            if c in "*?[":
                b["glob"] = True
            elif c in "{}":
                b["brace"] = True
            b["text"].append(c)
            i += 1
    end_seg(None)
    if heredocs:
        for seg, _, _, _ in heredocs:
            seg.bodies.append("")
    return segs, subs


def lex_dollar(cmd, i, buf, subs):
    """Lexes a $-expansion or backtick at cmd[i] into buf; returns the next index."""
    n = len(cmd)
    if cmd[i] == "`":
        j = find_backtick(cmd, i + 1)
        subs.append((re.sub(r"\\([`$\\])", r"\1", cmd[i + 1:j]), False))
        buf["text"].append("$_")
        buf["dynamic"] = True
        return j + 1
    nxt = cmd[i + 1] if i + 1 < n else ""
    if nxt == "(":
        j = find_close(cmd, i + 2)
        subs.append((cmd[i + 2:j], False))
        buf["text"].append("$_")
        buf["dynamic"] = True
        return j + 1
    if nxt == "{":
        j = find_brace(cmd, i + 2)
        subs.append((cmd[i + 2:j], True))
        buf["text"].append("$_")
        buf["dynamic"] = True
        return j + 1
    m = NAME.match(cmd, i + 1)
    if m:
        buf["text"].append("$" + m.group(0))
        buf["dynamic"] = True
        return m.end()
    if nxt and (nxt.isdigit() or nxt in "@*#?$!-"):
        buf["text"].append("$" + nxt)
        buf["dynamic"] = True
        return i + 2
    buf["text"].append("$")
    return i + 1


def segments(cmd):
    """Splits a shell command into simple-command token lists (word texts)."""
    return [[w.text for w in s.words] for s in lex(cmd)[0]]


def words(cmd):
    """The words of a single simple command (test helper)."""
    return [w for s in lex(cmd)[0] for w in s.words]


def tool_name(word):
    return posixpath.basename(word.text).casefold()


def glob_regex(pattern):
    """A regex for a shell glob in which * and ? do not cross '/'."""
    out, i, n = [], 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = pattern.find("]", i + 2)
            if j < 0:
                out.append(re.escape(c))
            else:
                body = pattern[i + 1:j].replace("\\", "\\\\")
                out.append("[" + ("^" + body[1:] if body.startswith("!") else body) + "]")
                i = j
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out))


def code_is_risky(code):
    return bool(CODE_RISK.search(code))


# --- the guard -----------------------------------------------------------------


class Guard:
    def __init__(self, default_branch, cwd, project_dir=None):
        self.default_branch = default_branch
        self.cwd = cwd
        self.project_dir = project_dir or cwd
        self.vcwd = cwd  # the cwd as `cd` changes it; None once unknown
        self.cd_seen = False
        self.aliases = {}
        self.written = set()
        self.root = ""

    # --- entry points ------------------------------------------------------

    def check_command(self, cmd, depth=0, lenient=False):
        """Checks a command string. lenient: a parse error is ignored (the text may not be a command)."""
        if depth > MAX_DEPTH:
            raise Blocked("command nesting too deep to check")
        if depth == 0:
            self.root = cmd
            if len(cmd) > MAX_COMMAND:
                raise Blocked(f"command longer than {MAX_COMMAND} bytes cannot be checked")
            if "\x00" in cmd:
                raise Blocked("command contains a NUL byte")
        try:
            segs, subs = lex(cmd)
        except ParseError as e:
            if lenient:
                return
            raise Blocked(f"could not parse the command ({e})") from None
        for inner, sub_lenient in subs:
            self.check_command(inner, depth + 1, lenient or sub_lenient)
        for seg in segs:
            self.check_segment(seg, depth)

    def check_text(self, text, depth):
        """Checks text that will probably run as a command (a variable, an alias, a config value)."""
        text = text.strip()
        if text.startswith("!"):
            text = text[1:]
        self.check_command(text, depth + 1, lenient=True)

    def check_segment(self, seg, depth):
        ws = list(seg.words)
        override = False
        assigns = []
        while ws and ws[0].assign_ok:
            name, value = ASSIGNMENT.match(ws[0].text).groups()
            if name == OVERRIDE_VAR and value == "1" and not ws[0].dynamic:
                override = True
            assigns.append((name, value))
            ws = ws[1:]
        if override:
            return
        for name, value in assigns:
            self.check_assignment(name, value, depth)
        while ws and not ws[0].quoted and ws[0].text in RESERVED:
            ws = ws[1:]
        if ws and ws[0].text == "function" and not ws[0].quoted:
            ws = ws[2:]
        tool = tool_name(ws[0]) if ws else ""
        self.check_redirections(seg, tool, depth)
        if not ws:
            return
        self.dispatch(ws, seg, depth, nested=False, via_xargs=False)
        # A blocked command can hide behind any prefix (`timeout 5 gh ...`, `f gh ...` for a
        # function f, `find -exec gh ...`). Check every later word that names a tool we guard.
        for k in range(1, len(ws)):
            if ws[k].quoted and " " in ws[k].text:
                continue
            if tool_name(ws[k]) in SCANNED or ws[k].text.casefold().startswith("git-"):
                via = any(tool_name(w) in ("xargs", "parallel", "find") for w in ws[:k])
                self.dispatch(ws[k:], seg, depth, nested=True, via_xargs=via)

    def check_assignment(self, name, value, depth):
        if GIT_CONFIG_ENV.match(name):
            raise Blocked(f"{name}: git config from the environment cannot be checked")
        self.check_text(value, depth)

    def check_redirections(self, seg, tool, depth):
        for op, target in seg.redirs:
            if op in (">", ">>", ">|", "&>", "&>>", "<>") or (op == ">&" and not re.fullmatch(r"\d+-?|-", target.text)):
                self.check_write(target, "redirecting output to")
                self.written.add(self.resolve(target.text))
            elif op == "<<<":
                seg.bodies.append(target.text)

    # --- dispatch ----------------------------------------------------------

    def dispatch(self, ws, seg, depth, nested, via_xargs):
        """ws[0] is the command word. nested: found later in the words (see check_segment)."""
        # Strip wrappers (`env`, `sudo`, `timeout 5`, `xargs -n1`, ...).
        while ws and tool_name(ws[0]) in WRAPPERS:
            wrapper = tool_name(ws[0])
            via_xargs = via_xargs or wrapper == "xargs"
            ws = ws[1:]
            values = WRAPPERS[wrapper]
            while ws:
                t = ws[0].text
                if wrapper == "env" and (t in ("-S", "--split-string") or t.startswith(("-S", "--split-string="))):
                    script = t.split("=", 1)[1] if "=" in t else (t[2:] if len(t) > 2 and t[1] == "S"
                                                                  else (ws[1].text if len(ws) > 1 else ""))
                    rest = ws[1:] if "=" in t or len(t) > 2 else ws[2:]
                    self.check_command(script + " " + " ".join(shlex.quote(w.text) for w in rest), depth + 1)
                    return
                if wrapper == "command" and t in ("-v", "-V"):
                    return  # a lookup, not a run
                if t in values:
                    ws = ws[2:]
                elif t.startswith("-") and len(t) > 1:
                    ws = ws[1:]
                elif ws[0].assign_ok:
                    name, value = ASSIGNMENT.match(t).groups()
                    self.check_assignment(name, value, depth)
                    ws = ws[1:]
                elif wrapper == "timeout" and DURATION.match(t):
                    ws = ws[1:]
                    break
                else:
                    break
        if not ws:
            return
        w0, args = ws[0], ws[1:]
        tool = tool_name(w0)
        if not nested:
            if w0.text in self.aliases and not w0.quoted:
                self.check_command(self.aliases[w0.text] + " " + " ".join(shlex.quote(a.text) for a in args),
                                   depth + 1)
            if "/" in w0.text and self.resolve(w0.text) in self.written:
                raise Blocked("running a script written in the same command cannot be checked")
            if w0.dynamic:
                self.check_gh(args, depth, strict=False, via_xargs=via_xargs)
                self.check_git(args, depth, strict=False, via_xargs=via_xargs)
                return
        if tool in ("cd", "pushd", "popd"):
            self.change_dir(args if tool != "popd" else None)
        elif tool == "git":
            self.check_git(args, depth, strict=not nested, via_xargs=via_xargs)
        elif tool.startswith("git-"):
            self.check_git([Word(tool[4:])] + args, depth, strict=not nested, via_xargs=via_xargs)
        elif tool == "gh":
            self.check_gh(args, depth, strict=not nested, via_xargs=via_xargs)
        elif tool in SHELLS:
            self.check_shell(args, seg, depth, nested)
        elif tool == "eval":
            self.check_command(" ".join(a.text for a in args), depth + 1)
        elif tool in ("source", "."):
            if args:
                self.check_script_arg(args[0], seg, depth, nested, shell=True)
        elif PYTHON.match(tool) or tool in INTERPRETER_CODE_FLAGS or tool in AWKS:
            self.check_interpreter(tool, args, seg, depth, nested)
        elif tool in HTTP_CLIENTS:
            self.check_http(tool, args)
        elif nested:
            return
        elif tool == "alias":
            for a in args:
                name, eq, value = a.text.partition("=")
                if eq:
                    self.check_text(value, depth)
                    self.aliases[name] = value
        elif tool in ("export", "declare", "typeset", "local", "readonly"):
            for a in args:
                if a.assign_ok:
                    self.check_assignment(*ASSIGNMENT.match(a.text).groups(), depth)
        elif tool in STDIN_RUNNERS:
            if not any(a.text == "-f" for a in args):
                self.check_stdin_program(seg, depth, shell=True)
        elif tool == "watch":
            self.check_watch(args, depth)
        elif tool in ("script", "su", "runuser", "flock"):
            for i, a in enumerate(args):
                if a.text in ("-c", "--command") and i + 1 < len(args):
                    self.check_command(args[i + 1].text, depth + 1)
                elif a.text.startswith("--command="):
                    self.check_command(a.text.split("=", 1)[1], depth + 1)
        elif tool == "ssh":
            self.check_ssh(args, depth)
        elif tool == "parallel":
            texts = [a.text for a in args]
            if ":::" in texts:
                for a in args[texts.index(":::") + 1:]:
                    self.check_text(a.text, depth)
        elif tool == "sed" or tool == "gsed":
            if any(re.match(r"^(-[A-Za-z]*i|--in-place)", a.text) for a in args):
                self.check_write_args(args, "editing")
        elif tool in WRITE_ALL_ARGS:
            self.check_write_args(args, "changing")
            if tool == "tee":
                for a in args:
                    if not a.text.startswith("-"):
                        self.written.add(self.resolve(a.text))
        elif tool in COPIERS:
            self.check_copy(tool, args)
        elif tool == "dd":
            for a in args:
                if a.text.startswith("of="):
                    self.check_write(Word(a.text[3:], a.dynamic), "writing")
        elif tool == "find":
            if any(a.text == "-delete" for a in args):
                for a in args:
                    if a.text.startswith(("-", "(", "!")):
                        break
                    self.check_write(a, "deleting")
        else:
            self.check_gh_lookalike(args)

    # --- paths ---------------------------------------------------------------

    def resolve(self, text):
        base = self.vcwd if self.vcwd is not None else self.cwd
        return posixpath.normpath(posixpath.join(base, os.path.expanduser(text)))

    def change_dir(self, args):
        self.cd_seen = True
        targets = [a for a in (args or []) if not a.text.startswith("-")]
        if not targets or self.vcwd is None or targets[0].dynamic or targets[0].text.startswith("~"):
            self.vcwd = None
        else:
            self.vcwd = posixpath.normpath(posixpath.join(self.vcwd, targets[0].text))

    def protected(self, word, ancestors=True):
        """True if writing to or removing the path word names could switch the harness off.

        ancestors: also count the directories that hold protected files (removing or replacing
        one removes them). False for a copy destination that is an existing directory.
        """
        text = word.text.replace("\\", "/")
        if not text:
            return False
        if word.glob:
            return self.protected_glob(word, text)
        lit = posixpath.normpath(text).casefold()
        cands = {lit}
        rels = set()
        if not word.dynamic and not text.startswith("~") and self.vcwd is not None:
            absolute = posixpath.normpath(posixpath.join(self.vcwd, text))
            real = os.path.realpath(absolute)
            cands.update({absolute.casefold(), real.casefold()})
            proj = os.path.realpath(self.project_dir)
            for p, root in ((absolute, self.project_dir), (real, proj)):
                rel = posixpath.relpath(p, root)
                if not rel.startswith(".."):
                    rels.add(rel.casefold())
        for c in cands | rels:
            padded = "/" + c.lstrip("/")
            if any(c == f or padded.endswith("/" + f) for f in PROTECTED_FILES):
                return True
            if any("/" + d + "/" in padded + "/" for d in PROTECTED_DIRS):
                return True
        if ancestors and rels & PROTECTED_ANCESTORS:
            return True
        base = posixpath.basename(lit)
        if word.dynamic and base in GIT_HOOKS:
            return True
        if self.vcwd is None and (base in PROTECTED_BASENAMES or base in GIT_HOOKS or base == "config"):
            return True
        return False

    def protected_glob(self, word, text):
        """A glob expands against the filesystem, as bash does; with no match it stays literal."""
        if "$" not in text and self.vcwd is not None:
            matches = globmod.glob(posixpath.join(self.vcwd, os.path.expanduser(text)))
            if matches:
                return any(self.protected(Word(m)) for m in matches)
        lit = posixpath.normpath(text).casefold()
        if any("/" + d + "/" in "/" + lit for d in PROTECTED_DIRS):
            return True
        parts = lit.split("/")
        for f in PROTECTED_FILES:
            n = f.count("/") + 1
            if (len(parts) == n or (n > 1 and len(parts) > n)) and glob_regex("/".join(parts[-n:])).fullmatch(f):
                return True
        return False

    def check_write(self, word, what, ancestors=True):
        if self.protected(word, ancestors):
            raise Blocked(f"{what} {word.text!r}: harness files (.claude/settings*.json, the guard, "
                          "hooks, harness.config.json, .git/config, .git/hooks) are changed by the human")

    def check_write_args(self, args, what):
        for a in args:
            if a.text and not a.text.startswith("-"):
                self.check_write(a, what)

    def check_copy(self, tool, args):
        pos, target_dir, i = [], None, 0
        while i < len(args):
            t = args[i].text
            if t in ("-t", "--target-directory"):
                target_dir = args[i + 1] if i + 1 < len(args) else None
                i += 2
                continue
            if t.startswith("--target-directory="):
                target_dir = Word(t.split("=", 1)[1], args[i].dynamic)
            elif t in ("-m", "--mode", "-o", "--owner", "-g", "--group", "-S", "--suffix") and tool == "install":
                i += 2
                continue
            elif not t.startswith("-") or t == "-":
                pos.append(args[i])
            i += 1
        if target_dir is None:
            if not pos:
                return
            target_dir, sources = pos[-1], pos[:-1]
        else:
            sources = pos
        into_dir = target_dir.text.endswith("/") or (
            not target_dir.dynamic and os.path.isdir(self.resolve(target_dir.text)))
        self.check_write(target_dir, f"{tool} to", ancestors=not into_dir)
        for s in sources:
            base = posixpath.basename(s.text.rstrip("/"))
            if base:
                joined = Word(posixpath.join(target_dir.text, base), target_dir.dynamic or s.dynamic)
                self.check_write(joined, f"{tool} to")
            if tool in ("mv", "gmv", "ln"):
                self.check_write(s, f"{tool} of")

    # --- shells, interpreters, stdin programs ----------------------------------

    def check_shell(self, args, seg, depth, nested):
        cflag = stdin = False
        i = 0
        while i < len(args):
            t = args[i].text
            if t == "--" or t == "-":
                i += 1
                break
            if t in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
                i += 2
                continue
            if t.startswith("--"):
                i += 1
                continue
            if t.startswith(("-", "+")) and len(t) > 1:
                cflag = cflag or "c" in t[1:]
                stdin = stdin or "s" in t[1:]
                i += 1
                continue
            break
        rest = args[i:]
        if cflag:
            if rest:
                self.check_command(rest[0].text, depth + 1)
            return
        if rest and not stdin:
            self.check_script_arg(rest[0], seg, depth, nested, shell=True)
        elif not nested:
            self.check_stdin_program(seg, depth, shell=True)

    def check_script_arg(self, word, seg, depth, nested, shell):
        if word.procsub or word.text in ("/dev/stdin", "/dev/fd/0", "-"):
            if not nested:
                self.check_stdin_program(seg, depth, shell, piped=True)
            return
        if self.resolve(word.text) in self.written:
            raise Blocked("running a script written in the same command cannot be checked")

    def check_stdin_program(self, seg, depth, shell, piped=False):
        """The program comes from stdin: a here-doc, a here-string or a pipe."""
        if seg.pipe_in or piped:
            raise Blocked("piping a program into a shell or interpreter cannot be checked: " + PIPE_DECODE_HINT)
        for body in seg.bodies:
            if shell:
                self.check_command(body, depth + 1)
            elif code_is_risky(body):
                raise Blocked("inline code that runs gh/git, calls the GitHub API or touches harness files "
                              "cannot be checked")

    def check_interpreter(self, tool, args, seg, depth, nested):
        if tool in AWKS:
            i = 0
            while i < len(args):
                t = args[i].text
                if t in ("-f", "--file"):
                    return
                if t in ("-F", "-v", "--assign", "--field-separator"):
                    i += 2
                    continue
                if t.startswith("-") and len(t) > 1:
                    i += 1
                    continue
                code = t
                if re.search(r"system|\|", code) and code_is_risky(code):
                    raise Blocked("awk code that runs gh/git or touches harness files cannot be checked")
                return
            return
        code_flags = {"-c"} if PYTHON.match(tool) else INTERPRETER_CODE_FLAGS[tool]
        in_place = False
        i = 0
        while i < len(args):
            t = args[i].text
            low = t.casefold()
            if PYTHON.match(tool) and t.startswith("-c") and len(t) > 2:
                self.check_code(t[2:])
                return
            if low in code_flags or (tool in ("perl", "ruby") and re.match(r"^-[A-Za-z]*[eE]$", t)):
                if tool in ("perl", "ruby") and re.match(r"^-[A-Za-z]*i", t):
                    in_place = True
                if i + 1 < len(args):
                    self.check_code(args[i + 1].text)
                if in_place:
                    self.check_write_args(args[i + 2:], "editing")
                return
            if PYTHON.match(tool) and t in ("-m",):
                return
            if PYTHON.match(tool) and t in ("-W", "-X", "-Q"):
                i += 2
                continue
            if tool in ("perl", "ruby") and re.match(r"^-[A-Za-z]*i", t):
                in_place = True
            if t == "-":
                break
            if t.startswith("-") and len(t) > 1:
                i += 1
                continue
            self.check_script_arg(args[i], seg, depth, nested, shell=False)
            return
        if not nested:
            self.check_stdin_program(seg, depth, shell=False)

    @staticmethod
    def check_code(code):
        if code_is_risky(code):
            raise Blocked("inline code that runs gh/git, calls the GitHub API or touches harness files "
                          "cannot be checked")

    def check_watch(self, args, depth):
        i = 0
        while i < len(args) and args[i].text.startswith("-"):
            i += 2 if args[i].text in ("-n", "--interval", "-d") else 1
        if i < len(args):
            self.check_command(" ".join(a.text for a in args[i:]), depth + 1)

    def check_ssh(self, args, depth):
        with_value = set("bcDEeFIiJLlmOopQRSWwB")
        i = 0
        while i < len(args) and args[i].text.startswith("-"):
            t = args[i].text
            i += 2 if len(t) == 2 and t[1] in with_value else 1
        if i + 1 < len(args):
            self.check_command(" ".join(a.text for a in args[i + 1:]), depth + 1)

    # --- HTTP clients to the GitHub API -------------------------------------------

    def check_http(self, tool, args):
        if tool == "curl":
            method, data, urls, get, head, auth = self.parse_curl(args)
        elif tool == "wget":
            method, data, urls, get, head, auth = self.parse_wget(args)
        else:
            method, data, urls, get, head, auth = self.parse_httpie(args)
        github = [u for u in urls if GITHUB_API.search(u.text)]
        if not github:
            if auth and any(u.dynamic for u in urls):
                raise Blocked(f"{tool} with credentials to a URL that is only known at run time")
            return
        if head:
            method = method or "HEAD"
        method = (method or ("GET" if get or not data else "POST")).upper()
        if method in ("GET", "HEAD"):
            return
        if any("/graphql" in u.text for u in github) and data and all(
                not d.dynamic and not d.text.startswith("@") and not re.search(r"\bmutation\b", d.text, re.I)
                for d in data):
            return
        raise Blocked(f"{tool} {method} to the GitHub API (writes change repo or PR state)")

    @staticmethod
    def parse_curl(args):
        value_short = set("AbcCdDEFHKmoPQrTuUwxXyYzet")
        data_opts = {"-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "--data-ascii",
                     "--json", "-F", "--form", "--form-string", "-T", "--upload-file"}
        method, data, urls, get, head, auth = None, [], [], False, False, False
        i = 0
        while i < len(args):
            a = args[i]
            t = a.text
            if t.startswith("--"):
                name, eq, inline = t.partition("=")
                takes = name in data_opts or name in (
                    "--request", "--header", "--url", "--config", "--user", "--oauth2-bearer", "--output",
                    "--write-out", "--cookie", "--referer", "--user-agent", "--proxy", "--max-time")
                value = Word(inline, a.dynamic) if eq else (args[i + 1] if takes and i + 1 < len(args) else None)
                if takes and not eq:
                    i += 1
                if name == "--config":
                    raise Blocked("curl --config files cannot be checked")
                if name == "--request" and value is not None:
                    method = value.text
                elif name in data_opts and value is not None:
                    data.append(value)
                elif name == "--url" and value is not None:
                    urls.append(value)
                elif name in ("--header",) and value is not None and "authorization" in value.text.casefold():
                    auth = True
                elif name in ("--user", "--oauth2-bearer"):
                    auth = True
                elif name == "--get":
                    get = True
                elif name == "--head":
                    head = True
            elif t.startswith("-") and len(t) > 1:
                j = 1
                while j < len(t):
                    f = t[j]
                    if f in value_short:
                        rest = t[j + 1:]
                        if rest:
                            value = Word(rest, a.dynamic)
                        else:
                            value = args[i + 1] if i + 1 < len(args) else Word("")
                            i += 1
                        if f == "X":
                            method = value.text
                        elif f in "dFT":
                            data.append(value)
                        elif f == "K":
                            raise Blocked("curl --config files cannot be checked")
                        elif f == "H" and "authorization" in value.text.casefold():
                            auth = True
                        elif f == "u":
                            auth = True
                        break
                    if f == "G":
                        get = True
                    elif f == "I":
                        head = True
                    j += 1
            else:
                urls.append(a)
            i += 1
        return method, data, urls, get, head, auth

    @staticmethod
    def parse_wget(args):
        method, data, urls, auth = None, [], [], False
        for i, a in enumerate(args):
            t = a.text
            name, eq, inline = t.partition("=")
            nxt = args[i + 1] if i + 1 < len(args) else Word("")
            value = Word(inline, a.dynamic) if eq else nxt
            if name == "--method":
                method = value.text
            elif name in ("--post-data", "--post-file", "--body-data", "--body-file"):
                data.append(value)
            elif name == "--header" and "authorization" in value.text.casefold():
                auth = True
            elif not t.startswith("-"):
                urls.append(a)
        return method, data, urls, False, False, auth

    @staticmethod
    def parse_httpie(args):
        method, data, urls, auth = None, [], [], False
        pos = [a for a in args if not a.text.startswith("-")]
        if pos and re.fullmatch(r"[A-Za-z]+", pos[0].text) and pos[0].text.upper() in (
                "GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            method, pos = pos[0].text, pos[1:]
        if pos:
            urls.append(pos[0])
        for item in pos[1:]:
            if re.match(r"^[^:=@]+(:=|==|=|@)", item.text) and "==" not in item.text:
                data.append(item)
            elif item.text.casefold().startswith("authorization:"):
                auth = True
        return method, data, urls, False, False, auth

    # --- git -------------------------------------------------------------

    def check_git(self, args, depth, strict=True, via_xargs=False):
        cdir, i, aliases = None, 0, {}
        while i < len(args) and args[i].text.startswith("-"):
            t = args[i].text
            if t == "--config-env" or t.startswith("--config-env="):
                raise Blocked("git --config-env cannot be checked")
            if t in GIT_OPTS_WITH_VALUE:
                if i + 1 < len(args):
                    if t == "-C":
                        cdir = args[i + 1].text
                    elif t == "-c":
                        self.git_config_kv(args[i + 1], aliases, depth)
                i += 2
            else:
                i += 1
        if i >= len(args):
            if via_xargs:
                raise Blocked("xargs supplies git's arguments, so they cannot be checked")
            return
        sub, rest = args[i], args[i + 1:]
        if sub.dynamic:
            if strict or via_xargs:
                raise Blocked("git subcommand from a variable cannot be checked")
            return
        s = sub.text.casefold()
        if s in aliases:
            value = aliases[s]
            if value.startswith("!"):
                self.check_command(value[1:] + " " + " ".join(shlex.quote(a.text) for a in rest), depth + 1)
            else:
                try:
                    expanded = words(value)
                except ParseError:
                    raise Blocked("could not parse a git alias") from None
                self.check_git(args[:i] + expanded + rest, depth + 1, strict, via_xargs)
            return
        if s in ("push", "send-pack"):
            if via_xargs:
                raise Blocked("xargs supplies git push's arguments, so they cannot be checked")
            self.check_push(rest, cdir, depth)
        elif s == "subtree" and rest and rest[0].text == "push":
            refs = [a for a in rest[1:] if not a.text.startswith("-")]
            for r in refs[1:]:
                self.check_refspec(r, cdir)
        elif s == "config":
            self.check_git_config(rest, depth)
        elif s in GIT_PATH_WRITERS:
            for a in rest:
                if not a.text.startswith("-"):
                    self.check_write(a, f"git {s} of")
        elif s == "rebase":
            for j, a in enumerate(rest):
                if a.text in ("-x", "--exec") and j + 1 < len(rest):
                    self.check_command(rest[j + 1].text, depth + 1)
                elif a.text.startswith("--exec="):
                    self.check_command(a.text.split("=", 1)[1], depth + 1)
        elif s == "submodule" and rest and rest[0].text == "foreach":
            body = [a for a in rest[1:] if not a.text.startswith("--")]
            self.check_command(" ".join(a.text for a in body), depth + 1)
        elif s == "bisect" and rest and rest[0].text == "run":
            self.check_command(" ".join(shlex.quote(a.text) for a in rest[1:]), depth + 1)

    def git_config_kv(self, kv, aliases, depth):
        key, _, value = kv.text.partition("=")
        key = key.casefold()
        if key.startswith("alias."):
            aliases[key[len("alias."):]] = value
        elif GIT_SENSITIVE_KEYS.match(key):
            raise Blocked(f"git -c {key}: changes what a push or a hook does")
        if kv.dynamic:
            raise Blocked("git -c value from a variable cannot be checked")
        self.check_text(value, depth)

    def check_git_config(self, args, depth):
        pos, i = [], 0
        read_only = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "-l", "--list", "--unset",
                     "--unset-all", "--remove-section", "--rename-section", "-e", "--edit", "--show-origin",
                     "--show-scope", "--name-only", "--get-color", "--get-colorbool"}
        while i < len(args):
            t = args[i].text
            if t in read_only:
                return
            if t in ("-f", "--file", "--blob", "--type", "--default", "--comment", "--value"):
                i += 2
                continue
            if t.startswith("-"):
                i += 1
                continue
            pos.append(args[i])
            i += 1
        if pos and pos[0].text in ("get", "list", "unset", "rename-section", "remove-section", "edit"):
            return
        if pos and pos[0].text == "set":
            pos = pos[1:]
        if len(pos) < 2:
            return
        key = pos[0].text.casefold()
        if pos[0].dynamic or GIT_SENSITIVE_KEYS.match(key):
            raise Blocked(f"git config {pos[0].text}: changes what a push, an alias or a hook does")
        self.check_text(pos[1].text, depth)

    def check_push(self, args, cdir, depth):
        positional, i = [], 0
        while i < len(args):
            a = args[i]
            t = a.text
            if a.dynamic:
                raise Blocked("git push argument from a variable cannot be checked: write the branch out")
            if t in PUSH_OPTS_WITH_VALUE:
                if t in PUSH_OPTS_RUN and i + 1 < len(args):
                    self.check_command(args[i + 1].text, depth + 1)
                i += 2
                continue
            if t == "--":
                positional.extend(x.text for x in args[i + 1:])
                break
            if t.startswith("--"):
                name, eq, value = t.partition("=")
                if name in PUSH_OPTS_RUN and eq:
                    self.check_command(value, depth + 1)
                for opt, why in PUSH_BLOCKED_OPTS.items():
                    if len(name) > 2 and opt.startswith(name):
                        raise Blocked(f"git push {name}: {why}")
            elif t.startswith("-") and len(t) > 1:
                flags = t[1:]
                if "o" in flags:
                    flags = flags[:flags.index("o")]  # -o takes the rest as its value
                if set(flags) & set("fd"):
                    raise Blocked(f"git push {t}: force push or delete")
            else:
                positional.append(t)
            i += 1
        refs = positional[1:]  # drop the remote
        if not refs:
            self.check_current_branch(cdir)
        for r in refs:
            self.check_refspec(Word(r), cdir)

    def check_refspec(self, word, cdir):
        r = word.text
        if word.dynamic:
            raise Blocked("refspec from a variable cannot be checked: write the branch out")
        if r.startswith("+"):
            raise Blocked("force refspec (+)")
        if r.startswith(":"):
            raise Blocked("deleting a remote ref (:ref)")
        if r.startswith("^"):
            return  # a negative refspec only excludes refs
        src, _, dst = r.partition(":")
        dst = dst or src
        if "*" in dst:
            raise Blocked("wildcard refspec (it can match the default branch)")
        d = re.sub(r"/+", "/", dst).casefold()
        if d in ("head", "@"):
            self.check_current_branch(cdir)
            return
        if d.startswith("refs/tags/") or d.startswith("tags/") or TAG_NAME.fullmatch(dst):
            raise Blocked("pushing tags (tags start releases)")
        for prefix in ("refs/heads/", "heads/"):
            if d.startswith(prefix):
                d = d[len(prefix):]
                break
        if d == self.default_branch.casefold():
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
        if out.casefold() == self.default_branch.casefold():
            raise Blocked(f"pushing to the default branch '{self.default_branch}' (current branch)")

    # --- gh --------------------------------------------------------------

    def check_gh(self, args, depth=0, strict=True, via_xargs=False):
        args = [W(a) for a in args]
        pos, i = [], 0
        while i < len(args) and len(pos) < 3:
            t = args[i].text
            if t in ("-R", "--repo"):
                i += 2
                continue
            if t.startswith("-") and not args[i].dynamic:
                i += 1
                continue
            pos.append((i, args[i]))
            i += 1
        if not pos:
            if via_xargs:
                raise Blocked("xargs supplies gh's arguments, so they cannot be checked")
            return
        sub = pos[0][1]
        verb = pos[1][1] if len(pos) > 1 else None
        if sub.dynamic or (verb is not None and verb.dynamic):
            if strict or via_xargs:
                raise Blocked("gh subcommand from a variable cannot be checked")
        if strict and not sub.dynamic and sub.text not in GH_KNOWN:
            raise Blocked(f"unknown gh command '{sub.text}' (an alias or extension) cannot be checked")
        if via_xargs and (verb is None or sub.text == "api"):
            raise Blocked("xargs supplies gh's arguments, so they cannot be checked")
        self.check_gh_verbs(sub.text, verb.text if verb is not None else "",
                            pos[2][1].text if len(pos) > 2 else "")
        if sub.text == "api":
            self.check_gh_api(args[pos[0][0] + 1:])

    @staticmethod
    def check_gh_verbs(sub, verb, third=""):
        if sub in GH_BLOCKED and verb in GH_BLOCKED[sub][0]:
            raise Blocked(GH_BLOCKED[sub][1])
        if sub == "repo" and verb == "deploy-key" and third in ("add", "delete"):
            raise Blocked("changing repo deploy keys")

    def check_gh_lookalike(self, args):
        """`<anything> pr merge`: gh under another name (an alias, a copy, a function)."""
        if len(args) >= 2 and not args[0].quoted:
            try:
                self.check_gh_verbs(args[0].text, args[1].text, args[2].text if len(args) > 2 else "")
            except Blocked as b:
                raise Blocked(f"looks like `gh {args[0].text} {args[1].text}` under another name: {b}") from None

    def check_gh_api(self, args):
        args = [W(a) for a in args]
        method, endpoint, fields, has_input = None, None, [], False
        i = 0
        while i < len(args):
            a = args[i].text
            name, eq, inline = a.partition("=") if a.startswith("--") else (a, "", "")
            if a.startswith("-X") and len(a) > 2:
                method = Word(a[2:], args[i].dynamic)
            elif name in GH_API_OPTS_WITH_VALUE:
                if eq:
                    value = Word(inline, args[i].dynamic)
                else:
                    value = args[i + 1] if i + 1 < len(args) else Word("")
                    i += 1
                if name in ("-X", "--method"):
                    method = value
                elif name in GH_API_FIELD_OPTS:
                    fields.append(value)
                elif name == "--input":
                    has_input = True
            elif re.match(r"^-[fF].+", a):
                fields.append(Word(a[2:], args[i].dynamic))
            elif not a.startswith("-") and endpoint is None:
                endpoint = a
            i += 1
        if method is not None and method.dynamic:
            raise Blocked("gh api method from a variable cannot be checked")
        m = (method.text if method is not None else ("POST" if fields or has_input else "GET")).upper()
        if endpoint == "graphql":
            self.check_graphql(fields, has_input)
            return
        if m not in ("GET", "HEAD"):
            raise Blocked(f"gh api {m} (writes change repo or PR state)")

    @staticmethod
    def check_graphql(fields, has_input):
        fields = [W(f) for f in fields]
        queries = [f for f in fields if f.text.startswith("query=")]
        if has_input or not queries:
            raise Blocked("gh api graphql without an inline query cannot be checked")
        for f in queries:
            q = f.text.split("=", 1)[1]
            if q.startswith("@"):
                raise Blocked("gh api graphql with a query file cannot be checked")
            if f.dynamic and q.startswith("$"):
                raise Blocked("gh api graphql query from a variable cannot be checked")
            if re.search(r"\bmutation\b", q, re.I):
                raise Blocked("GraphQL mutations (writes)")


SCANNED = {"git", "gh", "eval"} | SHELLS | HTTP_CLIENTS | set(INTERPRETER_CODE_FLAGS) | AWKS | {
    "python", "python3", "python2", "pypy", "pypy3"}


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
        Guard((cfg or {}).get("defaultBranch", "main"), cwd, project_dir).check_command(cmd)
    except Blocked as b:
        deny(str(b))
    except Exception as e:  # noqa: BLE001 - internal errors fail closed
        deny(f"guard error ({type(e).__name__}: {e})")


if __name__ == "__main__":
    main(sys.argv)
