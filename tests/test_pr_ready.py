"""Table tests for harness/core/scripts/pr-ready.sh against the fake gh (tests/fakes/gh).

Each case starts from a PR that passes every gate (tier `standard`, so READY) and
changes one thing. The fake serves canned JSON per gh subcommand; any call it has no
route for fails, like a broken gh would.
"""
import copy
import json
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "harness", "core", "scripts", "pr-ready.sh")
FAKES = os.path.join(ROOT, "tests", "fakes")
N = 7
REPO = "acme/widgets"

CONFIG = {
    "version": 1,
    "stacks": ["other"],
    "defaultBranch": "main",
    "requiredChecks": ["verify", "gitleaks"],
    "riskTiers": {
        "default": "standard",
        "rules": [
            {"glob": "docs/**", "tier": "fast"},
            {"glob": "**/*.md", "tier": "fast"},
            {"glob": ".github/**", "tier": "sensitive"},
        ],
    },
    "review": {"independence": "author-or-marker", "humanApprovalTiers": ["sensitive"]},
}

BUILDER = "<!-- harness:builder id=lane-b -->"
REVIEWER = "<!-- harness:reviewer id=rev-1 -->"
FIXES = "## Review findings → fixes\n1 → abc123"


def review(login="dev", body=REVIEWER + "\n1. nit", state="COMMENTED", at="2026-11-10T10:00:00Z"):
    return {"author": {"login": login}, "body": body, "state": state, "submittedAt": at}


def comment(body=FIXES, at="2026-11-10T11:00:00Z", login="dev"):
    return {"author": {"login": login}, "body": body, "createdAt": at}


def thread(resolved=False, typename="Bot"):
    return {"isResolved": resolved, "comments": {"nodes": [{"author": {"__typename": typename}}]}}


def base():
    """A PR that passes every gate: shared account, distinct markers, tier standard."""
    return {
        "config": copy.deepcopy(CONFIG),
        "author": "dev",
        "body": BUILDER + "\n## Summary\nstuff",
        "reviews": [review()],
        "comments": [comment()],
        "files": ["src/main.go"],  # str, or {"filename", "previous_filename"}
        "changed_files": None,  # None: len(files)
        "checks_exit": 0,
        "checks": [{"name": "verify", "bucket": "pass"}, {"name": "gitleaks", "bucket": "pass"}],
        "checks_json_exit": 0,
        "alerts": {"merge": [0], "head": [0]},  # open alerts per page
        "threads": [[]],  # review threads per page
        "fail": None,  # route name that returns a gh error
    }


def chunks(items, size=100):
    return [items[i:i + size] for i in range(0, len(items), size)] or [[]]


def routes(s):
    files = [f if isinstance(f, dict) else {"filename": f} for f in s["files"]]
    rest_files = [dict(f, status="renamed" if "previous_filename" in f else "modified") for f in files]
    pr = {
        "author": {"login": s["author"]},
        "body": s["body"],
        "reviews": s["reviews"],
        "comments": s["comments"],
        # gh pr view --json files returns at most the first 100 files
        "files": [{"path": f["filename"], "additions": 1, "deletions": 0} for f in files[:100]],
        "changedFiles": len(files) if s["changed_files"] is None else s["changed_files"],
    }
    thread_pages = []
    for i, nodes in enumerate(s["threads"]):
        more = i + 1 < len(s["threads"])
        thread_pages.append({"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": nodes, "pageInfo": {"hasNextPage": more, "endCursor": f"c{i}" if more else None}}}}}})
    r = {
        "repo view": {"match": r"^repo view", "stdout": {"nameWithOwner": REPO}},
        "pr view": {"match": rf"^pr view {N} ", "stdout": pr},
        "pr checks": {"match": rf"^pr checks {N}$", "exit": s["checks_exit"]},
        "pr checks json": {"match": rf"^pr checks {N} --json", "stdout": s["checks"],
                           "exit": s["checks_json_exit"]},
        "alerts merge": {"match": rf"code-scanning/alerts\?ref=refs/pull/{N}/merge",
                         "pages": [[{"number": i} for i in range(c)] for c in s["alerts"]["merge"]]},
        "alerts head": {"match": rf"code-scanning/alerts\?ref=refs/pull/{N}/head",
                        "pages": [[{"number": i} for i in range(c)] for c in s["alerts"]["head"]]},
        "graphql": {"match": r"^api graphql", "pages": thread_pages},
        "pr files": {"match": rf"pulls/{N}/files", "pages": chunks(rest_files)},
    }
    if s["fail"]:
        r[s["fail"]] = {"match": r[s["fail"]]["match"], "stderr": "HTTP 502: Bad Gateway\n", "exit": 1}
    return list(r.values())


def run(s, args=None, config_text=None):
    with tempfile.TemporaryDirectory() as d:
        cfg = os.path.join(d, "harness.config.json")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write(config_text if config_text is not None else json.dumps(s["config"]))
        rf = os.path.join(d, "routes.json")
        with open(rf, "w", encoding="utf-8") as f:
            json.dump(routes(s), f)
        env = dict(os.environ, PATH=FAKES + os.pathsep + os.environ["PATH"],
                   FAKE_GH_ROUTES=rf, FAKE_GH_LOG=os.path.join(d, "log"), HARNESS_CONFIG=cfg)
        p = subprocess.run([SCRIPT] + (args if args is not None else [str(N)]), cwd=ROOT, env=env,
                           capture_output=True, text=True, check=False, timeout=60)
        return p.returncode, p.stdout + p.stderr


def mod(**kw):
    s = base()
    for k, v in kw.items():
        s[k] = v
    return s


def with_config(**kw):
    s = base()
    s["config"].update(kw)
    return s


def with_review_cfg(**kw):
    s = base()
    s["config"]["review"].update(kw)
    return s


SENSITIVE = [".github/workflows/ci.yml"]
HUMAN_OK = review(login="the-human", body="lgtm", state="APPROVED", at="2026-11-10T09:00:00Z")

# (name, scenario, want exit, substrings that must appear in the output; "!x": must not appear)
CASES = [
    ("all gates pass", base(), 0, ["READY", "risk tier 'standard'"]),
    # 1. checks
    ("checks failing", mod(checks_exit=1, checks=[{"name": "verify", "bucket": "fail"},
                                                  {"name": "gitleaks", "bucket": "pass"}]),
     1, ["FAIL  checks not all passing", "required check 'verify': fail", "NOT READY"]),
    ("checks pending (exit 8)", mod(checks_exit=8, checks=[{"name": "verify", "bucket": "pending"},
                                                           {"name": "gitleaks", "bucket": "pass"}]),
     1, ["FAIL  checks not all passing", "required check 'verify': pending\n", "NOT READY"]),
    ("checks --json exits 8 with valid JSON", mod(checks_exit=8, checks_json_exit=8,
                                                 checks=[{"name": "verify", "bucket": "pending"},
                                                         {"name": "gitleaks", "bucket": "pass"}]),
     1, ["required check 'verify': pending\n", "!\nmissing", "NOT READY"]),
    ("required check missing", mod(checks=[{"name": "verify", "bucket": "pass"}]),
     1, ["required check 'gitleaks': missing", "NOT READY"]),
    ("required check skipped", mod(checks=[{"name": "verify", "bucket": "pass"},
                                          {"name": "gitleaks", "bucket": "skipping"}]),
     1, ["required check 'gitleaks': skipping", "NOT READY"]),
    # 2. code scanning
    ("alert on merge ref only", mod(alerts={"merge": [1], "head": [0]}), 1, ["1 open code-scanning alerts"]),
    ("alert on head ref only", mod(alerts={"merge": [0], "head": [1]}), 1, ["1 open code-scanning alerts"]),
    ("alerts on both refs", mod(alerts={"merge": [2], "head": [1]}), 1, ["3 open code-scanning alerts"]),
    ("alerts on a second page", mod(alerts={"merge": [100, 50], "head": [0]}), 1,
     ["150 open code-scanning alerts"]),
    ("no alerts on either ref", base(), 0, ["ok    0 open code-scanning alerts"]),
    # 3. bot threads
    ("unresolved bot thread", mod(threads=[[thread()]]), 1, ["1 unresolved bot review threads"]),
    ("resolved bot thread", mod(threads=[[thread(resolved=True)]]), 0, ["READY"]),
    ("unresolved human thread", mod(threads=[[thread(typename="User")]]), 0, ["READY"]),
    ("unresolved bot thread on page 2", mod(threads=[[thread(typename="User")] * 100, [thread()]]), 1,
     ["1 unresolved bot review threads"]),
    # 4. independent review
    ("no review", mod(reviews=[]), 1, ["no independent review", "no review posted"]),
    ("reviewer marker id == builder id", mod(reviews=[review(body="<!-- harness:reviewer id=lane-b -->")]),
     1, ["no independent review"]),
    ("same account, no reviewer marker", mod(reviews=[review(body="looks fine")]), 1, ["no independent review"]),
    ("same account, no builder marker", mod(body="no marker here"), 1, ["no independent review"]),
    ("different account, no markers", mod(body="plain", reviews=[review(login="someone", body="ok")]),
     0, ["READY"]),
    ("author mode ignores markers", with_review_cfg(independence="author"), 1, ["no independent review"]),
    # 5. findings → fixes
    ("no findings→fixes comment", mod(comments=[]), 1, ["no '## Review findings → fixes' comment"]),
    ("findings→fixes before the last review",
     mod(reviews=[review(), review(at="2026-11-10T12:00:00Z")]), 1, ["no '## Review findings → fixes' comment"]),
    ("findings→fixes heading not at the start", mod(comments=[comment(body="see below\n" + FIXES)]), 1,
     ["no '## Review findings → fixes' comment"]),
    # 6. risk tiers
    ("fast tier", mod(files=["docs/a.md", "README.md"]), 0, ["risk tier 'fast'", "READY"]),
    ("sensitive tier without approval", mod(files=SENSITIVE), 3,
     ["HUMAN risk tier 'sensitive'", "NEEDS HUMAN"]),
    ("sensitive tier, human approved", mod(files=SENSITIVE, reviews=[HUMAN_OK, review()]), 0,
     ["human approval present", "READY"]),
    ("sensitive tier, approval by a bot",
     mod(files=SENSITIVE, reviews=[review(login="helper[bot]", state="APPROVED"), review()]), 3, ["NEEDS HUMAN"]),
    ("sensitive tier, approval by the PR author (shared account)",
     mod(files=SENSITIVE, reviews=[review(state="APPROVED")]), 3, ["NEEDS HUMAN"]),
    ("sensitive tier, approval later replaced by changes requested",
     mod(files=SENSITIVE, reviews=[HUMAN_OK, review(login="the-human", state="CHANGES_REQUESTED",
                                                    at="2026-11-10T09:30:00Z"), review()]), 3, ["NEEDS HUMAN"]),
    ("sensitive tier, later comment-only review keeps approval",
     mod(files=SENSITIVE, reviews=[HUMAN_OK, review(login="the-human", state="COMMENTED", body="one more",
                                                    at="2026-11-10T09:30:00Z"), review()]), 0, ["READY"]),
    ("sensitive tier and a failing gate is NOT READY, not NEEDS HUMAN", mod(files=SENSITIVE, comments=[]), 1,
     ["NOT READY"]),
    ("sensitive file past the first 100", mod(files=[f"src/f{i}.go" for i in range(100)] + SENSITIVE), 3,
     ["risk tier 'sensitive'", "NEEDS HUMAN"]),
    ("file renamed out of a sensitive dir",
     mod(files=[{"filename": "old/ci.yml", "previous_filename": ".github/workflows/ci.yml"}]), 3,
     ["risk tier 'sensitive'", "NEEDS HUMAN"]),
    ("changed-file count does not match the listing", mod(changed_files=5), 1, ["NOT READY"]),
    ("humanApprovalTiers from the config", with_review_cfg(humanApprovalTiers=["standard"]), 3, ["NEEDS HUMAN"]),
    # usage and config
    ("requiredChecks not a list", with_config(requiredChecks="verify"), 2, []),
    ("unknown tier in the config", with_config(riskTiers={"default": "yolo", "rules": []}), 2, []),
]

GH_ROUTES = ["repo view", "pr view", "pr checks", "pr checks json", "alerts merge", "alerts head",
             "graphql", "pr files"]


class PrReadyTableTest(unittest.TestCase):
    def test_cases(self):
        for name, s, want, needles in CASES:
            with self.subTest(name):
                code, out = run(s)
                self.assertEqual(code, want, out)
                for needle in needles:
                    if needle.startswith("!"):
                        self.assertNotIn(needle[1:], out)
                    else:
                        self.assertIn(needle, out)
                if want == 0:
                    self.assertTrue(out.rstrip().endswith("READY") and "NOT READY" not in out, out)

    def test_any_gh_error_is_not_ready(self):
        for route in GH_ROUTES:
            for tier_files in (["src/main.go"], SENSITIVE):
                with self.subTest(route=route, files=tier_files):
                    code, out = run(mod(fail=route, files=tier_files))
                    self.assertEqual(code, 1, out)
                    self.assertIn("NOT READY", out)

    def test_gh_garbage_is_not_ready(self):
        s = base()
        r = routes(s)
        for route in r:
            if route["match"].startswith("^pr view"):
                route["stdout"] = "<html>oops</html>"
        with tempfile.TemporaryDirectory() as d:
            rf = os.path.join(d, "routes.json")
            cfg = os.path.join(d, "c.json")
            with open(rf, "w", encoding="utf-8") as f:
                json.dump(r, f)
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump(s["config"], f)
            env = dict(os.environ, PATH=FAKES + os.pathsep + os.environ["PATH"], FAKE_GH_ROUTES=rf,
                       HARNESS_CONFIG=cfg)
            p = subprocess.run([SCRIPT, str(N)], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("NOT READY", p.stdout)


class PrReadyUsageTest(unittest.TestCase):
    def test_usage_errors(self):
        for args in ([], ["abc"], ["7x"], ["-1"]):
            with self.subTest(args=args):
                code, _ = run(base(), args=args)
                self.assertEqual(code, 2)

    def test_config_errors(self):
        code, _ = run(base(), config_text="{nope")
        self.assertEqual(code, 2)
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, HARNESS_CONFIG=os.path.join(d, "missing.json"))
            p = subprocess.run([SCRIPT, "7"], cwd=d, env=env, capture_output=True, text=True, check=False)
            self.assertEqual(p.returncode, 2, p.stderr)


if __name__ == "__main__":
    unittest.main()
