"""Tests for harness/core/scripts/branch-protection.sh against the fake gh (tests/fakes/gh).

The dry run prints the request body; the real run must send exactly that body to
PUT repos/{repo}/branches/{branch}/protection (the fake records stdin).
"""
import json
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "harness", "core", "scripts", "branch-protection.sh")
FAKES = os.path.join(ROOT, "tests", "fakes")
CONFIG = {"version": 1, "defaultBranch": "trunk", "requiredChecks": ["verify", "gitleaks"]}


def body(checks):
    return {
        "required_status_checks": {"strict": True, "checks": [{"context": c} for c in checks]},
        "enforce_admins": True,
        "required_pull_request_reviews": None,
        "restrictions": None,
        "required_linear_history": False,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "required_conversation_resolution": True,
    }


PROTECTION = {
    "required_status_checks": {"strict": True, "checks": [{"context": "verify"}, {"context": "gitleaks"}]},
    "enforce_admins": {"enabled": True},
    "allow_force_pushes": {"enabled": False},
    "allow_deletions": {"enabled": False},
    "required_conversation_resolution": {"enabled": True},
}


def default_routes(fail=None):
    r = {
        "repo view default": {"match": r"^repo view \S+ --json defaultBranchRef",
                              "stdout": {"defaultBranchRef": {"name": "main"}}},
        "repo view": {"match": r"^repo view --json nameWithOwner", "stdout": {"nameWithOwner": "acme/widgets"}},
        "put": {"match": r"^api -X PUT repos/[^/]+/[^/]+/branches/[^ ]+/protection ", "stdout": "{}"},
        "get": {"match": r"^api repos/[^/]+/[^/]+/branches/[^ ]+/protection$", "stdout": PROTECTION},
    }
    if fail:
        r[fail] = {"match": r[fail]["match"], "stderr": "HTTP 403\n", "exit": 1}
    return list(r.values())


class Run:
    def __init__(self, args, config=CONFIG, env=None, routes=None, config_text=None):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "harness.config.json")
            if config is not None or config_text is not None:
                with open(cfg, "w", encoding="utf-8") as f:
                    f.write(config_text if config_text is not None else json.dumps(config))
            rf = os.path.join(d, "routes.json")
            with open(rf, "w", encoding="utf-8") as f:
                json.dump(default_routes() if routes is None else routes, f)
            log = os.path.join(d, "log")
            e = dict(os.environ, PATH=FAKES + os.pathsep + os.environ["PATH"], FAKE_GH_ROUTES=rf,
                     FAKE_GH_LOG=log)
            e.pop("HARNESS_ORCHESTRATOR", None)
            e.pop("HARNESS_CONFIG", None)
            if os.path.exists(cfg):
                e["HARNESS_CONFIG"] = cfg
            e.update(env or {})
            p = subprocess.run([SCRIPT] + args, cwd=d, env=e, capture_output=True, text=True, check=False)
            self.code, self.out, self.err = p.returncode, p.stdout, p.stderr
            self.calls = []
            if os.path.exists(log):
                with open(log, encoding="utf-8") as f:
                    self.calls = [json.loads(ln) for ln in f]

    def dry_body(self):
        first, rest = self.out.split("\n", 1)
        return first, json.loads(rest)


class DryRunTest(unittest.TestCase):
    def test_body_from_config(self):
        r = Run(["--dry-run"])
        self.assertEqual(r.code, 0, r.err)
        first, payload = r.dry_body()
        self.assertEqual(first, "branch: trunk")
        self.assertEqual(payload, body(["verify", "gitleaks"]))
        self.assertEqual(r.calls, [], "dry run must not call gh")

    def test_flags_override_config(self):
        r = Run(["--dry-run", "--branch", "dev", "--check", "a", "--check", "b c"])
        self.assertEqual(r.code, 0, r.err)
        first, payload = r.dry_body()
        self.assertEqual(first, "branch: dev")
        self.assertEqual(payload, body(["a", "b c"]))

    def test_no_config_uses_repo_default_branch(self):
        r = Run(["--dry-run", "--check", "verify"], config=None)
        self.assertEqual(r.code, 0, r.err)
        self.assertEqual(r.dry_body()[0], "branch: <repo default>")

    def test_dry_run_needs_no_override(self):
        self.assertEqual(Run(["--dry-run"]).code, 0)


class ApplyTest(unittest.TestCase):
    ORCH = {"HARNESS_ORCHESTRATOR": "1"}

    def test_sends_the_dry_run_body(self):
        dry = Run(["--dry-run"]).dry_body()[1]
        r = Run(["--repo", "acme/widgets"], env=self.ORCH)
        self.assertEqual(r.code, 0, r.err)
        puts = [c for c in r.calls if "PUT" in c["argv"]]
        self.assertEqual(len(puts), 1)
        self.assertIn("repos/acme/widgets/branches/trunk/protection", puts[0]["argv"])
        self.assertEqual(json.loads(puts[0]["stdin"]), dry)
        printed = json.loads(r.out.split("\n", 1)[1])
        self.assertEqual(printed["checks"], ["verify", "gitleaks"])
        self.assertTrue(printed["enforce_admins"])

    def test_repo_and_branch_from_gh(self):
        r = Run(["--check", "verify"], config=None, env=self.ORCH)
        self.assertEqual(r.code, 0, r.err)
        puts = [c for c in r.calls if "PUT" in c["argv"]]
        self.assertIn("repos/acme/widgets/branches/main/protection", puts[0]["argv"])
        self.assertEqual(json.loads(puts[0]["stdin"]), body(["verify"]))

    def test_refused_without_override(self):
        for env in ({}, {"HARNESS_ORCHESTRATOR": "01"}, {"HARNESS_ORCHESTRATOR": "true"}):
            with self.subTest(env=env):
                r = Run(["--repo", "acme/widgets"], env=env)
                self.assertEqual(r.code, 3)
                self.assertEqual(r.calls, [])

    def test_gh_failures_exit_1(self):
        for fail, args in (("put", ["--repo", "acme/widgets"]), ("get", ["--repo", "acme/widgets"]),
                           ("repo view", []), ("repo view default", ["--repo", "acme/widgets"])):
            with self.subTest(fail=fail):
                cfg = CONFIG if fail != "repo view default" else {"requiredChecks": ["verify"]}
                r = Run(args, config=cfg, env=self.ORCH, routes=default_routes(fail))
                self.assertEqual(r.code, 1, r.out + r.err)


class UsageTest(unittest.TestCase):
    def test_usage_and_config_errors_exit_2(self):
        cases = [
            (["--bogus"], CONFIG, None),
            (["--repo"], CONFIG, None),
            (["--check"], CONFIG, None),
            (["--branch"], CONFIG, None),
            (["--dry-run"], {"version": 1}, None),  # no checks anywhere
            (["--dry-run"], None, "{nope"),
            (["--dry-run"], {"requiredChecks": "verify"}, None),
        ]
        for args, cfg, text in cases:
            with self.subTest(args=args, cfg=cfg, text=text):
                r = Run(args, config=cfg, config_text=text)
                self.assertEqual(r.code, 2, r.out + r.err)
                self.assertEqual(r.calls, [])

    def test_explicit_missing_config_exits_2(self):
        r = Run(["--dry-run", "--check", "verify"], config=None,
                env={"HARNESS_CONFIG": "/nonexistent/harness.config.json"})
        self.assertEqual(r.code, 2, r.out + r.err)


if __name__ == "__main__":
    unittest.main()
