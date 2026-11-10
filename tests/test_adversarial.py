"""Adversarial input-robustness tests for scripts/guard-bash.py (contract §13).

The command-level adversarial cases live in tests/adversarial/cases.tsv and run through
tests/adversarial/run.py (scripts/verify.sh runs it). This file covers inputs a TSV line can't
hold (NUL bytes, invalid UTF-8, huge commands) and checks the cases file itself.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(ROOT, "scripts", "guard-bash.py")
spec = importlib.util.spec_from_file_location("adv_run", os.path.join(ROOT, "tests", "adversarial", "run.py"))
adv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adv)


def guard(stdin_bytes, cwd):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=cwd)
    env.pop("HARNESS_ORCHESTRATOR", None)
    start = time.monotonic()
    p = subprocess.run([sys.executable, GUARD], input=stdin_bytes, capture_output=True, env=env,
                       cwd=cwd, timeout=30, check=False)
    return p.returncode, time.monotonic() - start


def payload(cmd, cwd):
    return json.dumps({"tool_name": "Bash", "cwd": cwd, "tool_input": {"command": cmd}}).encode()


class InputRobustnessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_nul_byte_blocks(self):
        for cmd in ("gh\x00 pr merge 1", "echo ok\x00; gh pr merge 1", "ls\x00"):
            with self.subTest(cmd=cmd):
                self.assertEqual(guard(payload(cmd, self.dir), self.dir)[0], 2)

    def test_invalid_utf8_blocks(self):
        raw = b'{"tool_input": {"command": "gh pr merge 1 \xff\xfe"}, "cwd": "/"}'
        self.assertEqual(guard(raw, self.dir)[0], 2)

    def test_lone_surrogate_does_not_crash_open(self):
        self.assertEqual(guard(payload("gh pr merge 1 \udc80", self.dir), self.dir)[0], 2)

    def test_huge_command_blocks_quickly(self):
        code, secs = guard(payload("echo x; " * 200_000, self.dir), self.dir)
        self.assertEqual(code, 2)
        self.assertLess(secs, 5)

    def test_large_safe_command_allowed_quickly(self):
        code, secs = guard(payload("echo x; " * 5_000, self.dir), self.dir)
        self.assertEqual(code, 0)
        self.assertLess(secs, 5)

    def test_large_command_with_blocked_tail(self):
        code, _ = guard(payload("echo x; " * 5_000 + "gh pr merge 1", self.dir), self.dir)
        self.assertEqual(code, 2)


class CasesFileTest(unittest.TestCase):
    def test_cases_parse_and_cover_every_want(self):
        cases = adv.parse_cases(os.path.join(ROOT, "tests", "adversarial", "cases.tsv"))
        self.assertGreater(len(cases), 100)
        self.assertEqual({w for _, w, _ in cases}, {"2", "0", "limit"})

    def test_bad_line_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False) as f:
            f.write("block gh pr merge 1\n")
        try:
            with self.assertRaises(ValueError):
                adv.parse_cases(f.name)
        finally:
            os.unlink(f.name)

    def test_every_limit_is_in_threat_model(self):
        with open(os.path.join(ROOT, "docs", "threat-model.md"), encoding="utf-8") as f:
            model = f.read()
        cases = adv.parse_cases(os.path.join(ROOT, "tests", "adversarial", "cases.tsv"))
        for _, want, cmd in cases:
            if want == "limit":
                with self.subTest(cmd=cmd):
                    self.assertIn(f"`{cmd}`", model)


if __name__ == "__main__":
    unittest.main()
