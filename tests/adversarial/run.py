#!/usr/bin/env python3
"""Runs the adversarial guard cases (contract §13) against the guard hook.

Usage: tests/adversarial/run.py [--guard PATH] [CASES_FILE...]
Defaults: scripts/guard-bash.py and every *.tsv file next to this script.

Each case line is `want<TAB>command`:
  2      must be blocked (exit 2)
  0      must be allowed (exit 0)
  limit  accepted limit, listed in docs/threat-model.md: the guard allows it today. If it
         starts blocking, the runner fails so the row is moved to 2 and the threat model updated.
A command starting with `json:` is a JSON string, so cases can hold newlines and control characters.

The guard runs with a project that has no harness.config.json (default branch main) and a cwd
that is a git repo on branch lane-x. Exit 0 when every case matches, 1 otherwise.
"""
import argparse
import concurrent.futures
import glob
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
WANTS = {"2": 2, "0": 0, "limit": 0}


def parse_cases(path):
    """Returns [(line_number, want, command)] from a cases file; raises ValueError on a bad line."""
    cases = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            want, tab, cmd = line.partition("\t")
            if not tab or want not in WANTS or not cmd:
                raise ValueError(f"{path}:{n}: want a line `2|0|limit<TAB>command`, got {line!r}")
            if cmd.startswith("json:"):
                cmd = json.loads(cmd[len("json:"):])
            cases.append((n, want, cmd))
    return cases


def make_repo(tmp):
    """A git repo on branch lane-x with one commit, used as both project dir and cwd."""
    repo = os.path.join(tmp, "repo")
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    for args in (["init", "-q", "-b", "lane-x", repo],
                 ["-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.com",
                  "commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(["git", *args], check=True, env=env, capture_output=True)
    return repo


def run_guard(guard, repo, cmd):
    payload = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                          "cwd": repo, "tool_input": {"command": cmd}})
    env = dict(os.environ, CLAUDE_PROJECT_DIR=repo)
    env.pop("HARNESS_ORCHESTRATOR", None)
    p = subprocess.run([sys.executable, guard], input=payload, text=True, capture_output=True,
                       env=env, cwd=repo, timeout=20, check=False)
    return p.returncode, p.stderr.strip()


def run(guard, files):
    """Runs every case; returns a list of failure messages."""
    cases = [(f, n, want, cmd) for f in files for (n, want, cmd) in parse_cases(f)]
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = pool.map(lambda c: run_guard(guard, repo, c[3]), cases)
            for (f, n, want, cmd), (got, err) in zip(cases, results):
                if got == WANTS[want]:
                    continue
                where = f"{os.path.relpath(f, ROOT)}:{n}"
                if want == "limit":
                    failures.append(f"{where}: accepted limit is now blocked; change it to 2 and update "
                                    f"docs/threat-model.md: {cmd!r}")
                else:
                    failures.append(f"{where}: want exit {WANTS[want]}, got {got}: {cmd!r}"
                                    + (f"\n    {err}" if err else ""))
    return len(cases), failures


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--guard", default=os.path.join(ROOT, "scripts", "guard-bash.py"))
    ap.add_argument("files", nargs="*")
    a = ap.parse_args(argv)
    files = a.files or sorted(glob.glob(os.path.join(HERE, "*.tsv")))
    total, failures = run(a.guard, files)
    for msg in failures:
        print("FAIL: " + msg)
    print(f"adversarial: {total - len(failures)}/{total} cases pass")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
