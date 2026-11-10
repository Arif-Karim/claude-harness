"""Table tests for harness/core/scripts/pr-ready.sh against the fake gh (tests/fakes/gh).

Each case starts from a PR that passes every gate (tier `standard`, so READY) and
changes one thing. The fake serves canned JSON per gh subcommand; any call it has no
route for fails, like a broken gh would.
"""
import copy
import json
import os
import shutil
import subprocess
import sys
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


HEAD = "a" * 40
MERGE = "b" * 40
OLD_MERGE = "c" * 40


def review(login="dev", body=REVIEWER + "\n1. nit", state="COMMENTED", at="2026-11-10T10:00:00Z", bot=False):
    """A review as the scenario sees it; routes() renders it in each API's real shape."""
    return {"login": login, "bot": bot, "body": body, "state": state, "at": at}


def comment(body=FIXES, at="2026-11-10T11:00:00Z", login="dev"):
    return {"login": login, "body": body, "at": at}


def thread(resolved=False, typename="Bot"):
    return {"isResolved": resolved, "comments": {"nodes": [{"author": {"__typename": typename}}]}}


def base():
    """A PR that passes every gate: shared account, distinct markers, tier standard.
    Timeline: harness review 10:00, findings→fixes comment 11:00."""
    return {
        "config": copy.deepcopy(CONFIG),
        "author": "dev",
        "body": BUILDER + "\n## Summary\nstuff",
        "head": HEAD,
        "head_later": None,  # head sha seen by the final re-read (None: unchanged)
        "merge_sha": MERGE,
        "reviews": [review()],
        "comments": [comment()],
        "files": ["src/main.go"],  # str, or {"filename", "previous_filename"}
        "changed_files": None,  # None: len(files)
        "checks_exit": 0,
        "checks": [{"name": "verify", "bucket": "pass"}, {"name": "gitleaks", "bucket": "pass"}],
        "checks_json_exit": 0,
        "analyses": [[MERGE]],  # commit_sha of each code-scanning analysis on the merge ref, per page
        "alerts": {"merge": [0], "head": [0]},  # open alerts per page
        "threads": [[]],  # review threads per page
        "fail": None,  # route name that returns a gh error
    }


def chunks(items, size=100):
    return [items[i:i + size] for i in range(0, len(items), size)] or [[]]


def rest_user(login, bot=False):
    # REST: bots are "<app>[bot]" with type "Bot"
    return {"login": login + "[bot]" if bot else login, "type": "Bot" if bot else "User"}


def routes(s):
    files = [f if isinstance(f, dict) else {"filename": f} for f in s["files"]]
    rest_files = [dict(f, status="renamed" if "previous_filename" in f else "modified") for f in files]
    changed = len(files) if s["changed_files"] is None else s["changed_files"]

    def pull(head):
        return {"number": N, "user": rest_user(s["author"]), "body": s["body"], "head": {"sha": head},
                "merge_commit_sha": s["merge_sha"], "changed_files": changed}

    rest_reviews = [{"user": rest_user(r["login"], r["bot"]), "body": r["body"], "state": r["state"],
                     "submitted_at": r["at"]} for r in s["reviews"]]
    rest_comments = [{"user": rest_user(c["login"]), "body": c["body"], "created_at": c["at"]}
                     for c in s["comments"]]
    # gh pr view --json shape, for realism: bot logins have no "[bot]" suffix and no type, files stop at 100
    view = {"author": {"login": s["author"]}, "body": s["body"], "headRefOid": s["head"],
            "reviews": [{"author": {"login": r["login"]}, "body": r["body"], "state": r["state"],
                         "submittedAt": r["at"]} for r in s["reviews"]],
            "comments": [{"author": {"login": c["login"]}, "body": c["body"], "createdAt": c["at"]}
                         for c in s["comments"]],
            "files": [{"path": f["filename"]} for f in files[:100]], "changedFiles": changed}
    thread_pages = []
    for i, nodes in enumerate(s["threads"]):
        more = i + 1 < len(s["threads"])
        thread_pages.append({"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": nodes, "pageInfo": {"hasNextPage": more, "endCursor": f"c{i}" if more else None}}}}}})
    api = f"repos/{REPO}"
    pr_route = {"match": rf"^api {api}/pulls/{N}$", "stdout": pull(s["head"])}
    if s["head_later"]:
        pr_route["sequence"] = [{"stdout": pull(s["head"])}, {"stdout": pull(s["head_later"])}]
    r = {
        "repo view": {"match": r"^repo view --json nameWithOwner$", "stdout": {"nameWithOwner": REPO}},
        "pr view": {"match": rf"^pr view {N} ", "stdout": view},
        "pr": pr_route,
        "reviews": {"match": rf"^api --paginate {api}/pulls/{N}/reviews\?per_page=100$",
                    "pages": chunks(rest_reviews)},
        "comments": {"match": rf"^api --paginate {api}/issues/{N}/comments\?per_page=100$",
                     "pages": chunks(rest_comments)},
        "pr checks": {"match": rf"^pr checks {N}$", "exit": s["checks_exit"]},
        "pr checks json": {"match": rf"^pr checks {N} --json name,bucket$", "stdout": s["checks"],
                           "exit": s["checks_json_exit"]},
        "analyses": {"match": rf"^api --paginate {api}/code-scanning/analyses\?ref=refs/pull/{N}/merge&per_page=100$",
                     "pages": [[{"commit_sha": c, "ref": f"refs/pull/{N}/merge", "tool": {"name": "CodeQL"}}
                                for c in page] for page in s["analyses"]]},
        "alerts merge": {"match": rf"^api --paginate {api}/code-scanning/alerts\?ref=refs/pull/{N}/merge"
                                  r"&state=open&per_page=100$",
                         "pages": [[{"number": i} for i in range(c)] for c in s["alerts"]["merge"]]},
        "alerts head": {"match": rf"^api --paginate {api}/code-scanning/alerts\?ref=refs/pull/{N}/head"
                                 r"&state=open&per_page=100$",
                        "pages": [[{"number": i} for i in range(c)] for c in s["alerts"]["head"]]},
        "graphql": {"match": rf"^api graphql --paginate -F owner=acme -F name=widgets -F n={N} -f query=",
                    "pages": thread_pages},
        "pr files": {"match": rf"^api --paginate {api}/pulls/{N}/files\?per_page=100$", "pages": chunks(rest_files)},
    }
    if s["fail"] == "pr head recheck":
        r["pr"] = {"match": r["pr"]["match"], "sequence": [{"stdout": pull(s["head"])},
                                                          {"stderr": "HTTP 502\n", "exit": 1}]}
    elif s["fail"]:
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


def with_review_cfg_mod(review_cfg, **kw):
    s = mod(**kw)
    s["config"]["review"].update(review_cfg)
    return s


SENSITIVE = [".github/workflows/ci.yml"]
# the real order: harness review 10:00, fixes comment 11:00, then the human approves at 12:00
HUMAN_OK = review(login="the-human", body="lgtm", state="APPROVED", at="2026-11-10T12:00:00Z")
ACTIONS_OK = review(login="github-actions", bot=True, body="", state="APPROVED", at="2026-11-10T12:00:00Z")
SCANNER = review(login="github-advanced-security", bot=True, body="1 new alert", at="2026-11-10T09:00:00Z")

# (name, scenario, want exit, substrings that must appear in the output; "!x": must not appear)
CASES = [
    ("all gates pass", base(), 0, ["READY", "risk tier 'standard'", f"head {HEAD}"]),
    # head sha: the verdict is for one commit
    ("head moved during the run", mod(head_later="d" * 40), 1, ["head moved", "NOT READY"]),
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
    ("no analysis for the merge commit", mod(analyses=[[]]), 1,
     [f"no code-scanning analysis for {MERGE}", "main moved: update the branch (merge origin/main and push); "
      "re-running CodeQL won't help", "NOT READY"]),
    ("only an analysis of an older merge commit", mod(analyses=[[OLD_MERGE]]), 1,
     [f"no code-scanning analysis for {MERGE}"]),
    ("analysis of the merge commit on page 2", mod(analyses=[[OLD_MERGE] * 100, [MERGE]]), 0, ["READY"]),
    ("no merge commit (conflicts or not computed yet)", mod(merge_sha=None), 1, ["NOT READY"]),
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
    ("only a bot review (code scanning) is not independent", mod(reviews=[SCANNER]), 1,
     ["no independent review"]),
    ("a bot review with a reviewer marker is not independent",
     mod(reviews=[review(login="some-app", bot=True)]), 1, ["no independent review"]),
    ("author mode ignores markers", with_review_cfg(independence="author"), 1, ["no independent review"]),
    # 5. findings → fixes
    ("no findings→fixes comment", mod(comments=[]), 1, ["no '## Review findings → fixes' comment"]),
    ("findings→fixes before the last harness review",
     mod(reviews=[review(), review(at="2026-11-10T12:00:00Z")]), 1, ["no '## Review findings → fixes' comment"]),
    ("findings→fixes at the same second as the review", mod(comments=[comment(at="2026-11-10T10:00:00Z")]), 1,
     ["no '## Review findings → fixes' comment"]),
    ("findings→fixes heading not at the start", mod(comments=[comment(body="see below\n" + FIXES)]), 1,
     ["no '## Review findings → fixes' comment"]),
    ("a bot review after the fixes comment doesn't reset gate 5",
     mod(reviews=[review(), dict(SCANNER, at="2026-11-10T12:00:00Z")]), 0, ["READY"]),
    ("a human comment-only review after the fixes comment doesn't reset gate 5",
     mod(reviews=[review(), review(login="the-human", body="q?", at="2026-11-10T12:00:00Z")]), 0, ["READY"]),
    # 5. no-marker fallback: anchored on the last non-bot, non-APPROVED review
    ("fallback: a later bot review doesn't reset gate 5",
     mod(body="plain", reviews=[review(login="someone", body="ok"), dict(SCANNER, at="2026-11-10T12:00:00Z")]),
     0, ["READY"]),
    ("fallback: a later human approval doesn't reset gate 5",
     mod(body="plain", reviews=[review(login="someone", body="ok"), HUMAN_OK]), 0, ["READY"]),
    ("fallback: a later human comment review resets gate 5",
     mod(body="plain", reviews=[review(login="someone", body="ok"),
                                review(login="other", body="hmm", at="2026-11-10T12:00:00Z")]),
     1, ["no '## Review findings → fixes' comment"]),
    ("fallback in author mode: a later bot review doesn't reset gate 5",
     with_review_cfg_mod(dict(independence="author"),
                         reviews=[review(login="someone", body="ok"), dict(SCANNER, at="2026-11-10T12:00:00Z")]),
     0, ["READY"]),
    # human changes requested blocks in every tier
    ("standard tier: human requests changes after the fixes comment",
     mod(reviews=[review(), review(login="the-human", state="CHANGES_REQUESTED", body="no",
                                   at="2026-11-10T12:00:00Z")]), 1, ["changes requested by the-human", "NOT READY"]),
    ("fast tier: human requests changes", mod(files=["docs/a.md"], reviews=[
        review(), review(login="the-human", state="CHANGES_REQUESTED", body="no", at="2026-11-10T12:00:00Z")]),
     1, ["changes requested by the-human"]),
    ("sensitive tier: changes requested is NOT READY, not NEEDS HUMAN", mod(files=SENSITIVE, reviews=[
        review(), review(login="the-human", state="CHANGES_REQUESTED", body="no", at="2026-11-10T12:00:00Z")]),
     1, ["changes requested by the-human", "NOT READY"]),
    ("changes requested, then approved by the same human",
     mod(reviews=[review(), review(login="the-human", state="CHANGES_REQUESTED", body="no",
                                   at="2026-11-10T09:00:00Z"), HUMAN_OK]), 0, ["READY"]),
    ("a bot requesting changes doesn't block",
     mod(reviews=[review(), review(login="some-app", bot=True, state="CHANGES_REQUESTED", body="x",
                                   at="2026-11-10T12:00:00Z")]), 0, ["READY"]),
    # 6. risk tiers
    ("fast tier", mod(files=["docs/a.md", "README.md"]), 0, ["risk tier 'fast'", "READY"]),
    ("sensitive tier without approval", mod(files=SENSITIVE), 3,
     ["HUMAN risk tier 'sensitive'", "NEEDS HUMAN", f"head {HEAD}"]),
    ("sensitive tier, human approves after the fixes comment", mod(files=SENSITIVE, reviews=[review(), HUMAN_OK]),
     0, ["human approval present", "READY"]),
    ("sensitive tier, github-actions approval is not human",
     mod(files=SENSITIVE, reviews=[review(), ACTIONS_OK]), 3, ["NEEDS HUMAN"]),
    ("sensitive tier, approval by the PR author (shared account)",
     mod(files=SENSITIVE, reviews=[review(), review(body="", state="APPROVED", at="2026-11-10T12:00:00Z")]),
     3, ["NEEDS HUMAN"]),
    ("sensitive tier, approval later replaced by changes requested",
     mod(files=SENSITIVE, reviews=[review(), HUMAN_OK, review(login="the-human", state="CHANGES_REQUESTED",
                                                              body="no", at="2026-11-10T12:30:00Z")]),
     3, ["NEEDS HUMAN"]),
    ("sensitive tier, later comment-only review keeps approval",
     mod(files=SENSITIVE, reviews=[review(), HUMAN_OK, review(login="the-human", body="one more",
                                                              at="2026-11-10T12:30:00Z")]), 0, ["READY"]),
    ("sensitive tier and a failing gate is NOT READY, not NEEDS HUMAN", mod(files=SENSITIVE, comments=[]), 1,
     ["NOT READY"]),
    ("sensitive file past the first 100", mod(files=[f"src/f{i}.go" for i in range(100)] + SENSITIVE), 3,
     ["risk tier 'sensitive'", "NEEDS HUMAN"]),
    ("file renamed out of a sensitive dir",
     mod(files=[{"filename": "old/ci.yml", "previous_filename": ".github/workflows/ci.yml"}]), 3,
     ["risk tier 'sensitive'", "NEEDS HUMAN"]),
    ("changed-file count does not match the listing", mod(changed_files=5), 1, ["NOT READY"]),
    ("humanApprovalTiers from the config", with_review_cfg(humanApprovalTiers=["standard"]), 3, ["NEEDS HUMAN"]),
    ("many reviews and comments across pages",
     mod(reviews=[review(at=f"2026-11-10T09:{i % 60:02d}:00Z", body="x") for i in range(120)] + [review()],
         comments=[comment(body="chatter", at="2026-11-10T09:00:00Z")] * 120 + [comment()]), 0, ["READY"]),
    # usage and config
    ("requiredChecks not a list", with_config(requiredChecks="verify"), 2, []),
    ("unknown tier in the config", with_config(riskTiers={"default": "yolo", "rules": []}), 2, []),
]

GH_ROUTES = ["repo view", "pr", "reviews", "comments", "pr checks", "pr checks json", "analyses", "alerts merge",
             "alerts head", "graphql", "pr files", "pr head recheck"]


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
            if route["match"].endswith(f"/pulls/{N}$"):
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
        for args in ([], ["abc"], ["7x"], ["-1"], ["007"], ["0"]):
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


class PrReadyMissingRiskTierTest(unittest.TestCase):
    def test_missing_risk_tier_script_is_named(self):
        with tempfile.TemporaryDirectory() as d:
            script = os.path.join(d, "pr-ready.sh")
            shutil.copy(SCRIPT, script)
            cfg = os.path.join(d, "c.json")
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump(CONFIG, f)
            p = subprocess.run([script, "7"], cwd=d, env=dict(os.environ, HARNESS_CONFIG=cfg),
                               capture_output=True, text=True, check=False)
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("risk-tier.py", p.stderr)
        self.assertNotIn("invalid riskTiers", p.stderr)


class MergeCommandGuardTest(unittest.TestCase):
    """The orchestrator merges the judged commit with --match-head-commit; the guard must allow
    that exact form behind the override, and still block it without."""
    GUARD = os.path.join(ROOT, "scripts", "guard-bash.py")

    def guard(self, command):
        payload = json.dumps({"tool_input": {"command": command}, "cwd": ROOT})
        return subprocess.run([sys.executable, self.GUARD], input=payload, capture_output=True, text=True,
                              check=False).returncode

    def test_merge_form(self):
        cmd = f"gh pr merge {N} --merge --match-head-commit {HEAD}"
        self.assertEqual(self.guard("HARNESS_ORCHESTRATOR=1 " + cmd), 0)
        self.assertEqual(self.guard(cmd), 2)


if __name__ == "__main__":
    unittest.main()
