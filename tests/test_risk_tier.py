"""Tests for harness/core/scripts/risk-tier.py."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "harness", "core", "scripts", "risk-tier.py")
spec = importlib.util.spec_from_file_location("risk_tier", SCRIPT)
risk_tier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(risk_tier)

CONFIG = {
    "version": 1,
    "riskTiers": {
        "default": "standard",
        "rules": [
            {"glob": "docs/**", "tier": "fast"},
            {"glob": "**/*.md", "tier": "fast"},
            {"glob": "skills/**", "tier": "standard"},
            {"glob": ".github/**", "tier": "sensitive"},
            {"glob": "**/auth/**", "tier": "sensitive"},
            {"glob": "harness.config.json", "tier": "sensitive"},
            {"glob": "cmd/*/main.go", "tier": "sensitive"},
        ],
    },
}


class GlobTest(unittest.TestCase):
    def test_matches(self):
        cases = [
            ("docs/**", "docs/a.md", True),
            ("docs/**", "docs/x/y/z.txt", True),
            ("docs/**", "src/docs/a.md", False),
            ("**/*.md", "README.md", True),
            ("**/*.md", "a/b/c.md", True),
            ("**/*.md", "a/b/c.mdx", False),
            ("**/auth/**", "auth/x.go", True),
            ("**/auth/**", "internal/auth/token.go", True),
            ("**/auth/**", "internal/oauth/token.go", False),
            ("cmd/*/main.go", "cmd/app/main.go", True),
            ("cmd/*/main.go", "cmd/a/b/main.go", False),
            ("harness.config.json", "harness.config.json", True),
            ("harness.config.json", "x/harness.config.json", False),
            ("src/?.ts", "src/a.ts", True),
            ("src/?.ts", "src/ab.ts", False),
            ("a.b", "axb", False),
        ]
        for glob, path, want in cases:
            with self.subTest(glob=glob, path=path):
                self.assertEqual(risk_tier.glob_match(glob, path), want)


class TierTest(unittest.TestCase):
    def tier(self, files):
        return risk_tier.tier_for(CONFIG, files)

    def test_no_files_is_default(self):
        self.assertEqual(self.tier([]), "standard")

    def test_unmatched_is_default(self):
        self.assertEqual(self.tier(["src/main.go"]), "standard")

    def test_docs_only_is_fast(self):
        self.assertEqual(self.tier(["docs/a.md", "README.md"]), "fast")

    def test_highest_matching_rule_wins_per_file(self):
        self.assertEqual(self.tier(["skills/x/SKILL.md"]), "standard")

    def test_highest_file_wins_per_pr(self):
        self.assertEqual(self.tier(["docs/a.md", ".github/workflows/ci.yml"]), "sensitive")
        self.assertEqual(self.tier(["docs/a.md", "src/x.go"]), "standard")

    def test_bad_tier_rejected(self):
        bad = {"riskTiers": {"default": "yolo", "rules": []}}
        with self.assertRaises(ValueError):
            risk_tier.tier_for(bad, ["a"])
        bad = {"riskTiers": {"default": "fast", "rules": [{"glob": "a", "tier": "nope"}]}}
        with self.assertRaises(ValueError):
            risk_tier.tier_for(bad, ["a"])

    def test_missing_section_rejected(self):
        with self.assertRaises(ValueError):
            risk_tier.tier_for({}, ["a"])


class CliTest(unittest.TestCase):
    def run_cli(self, cfg_text, stdin):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "harness.config.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write(cfg_text)
            return subprocess.run(
                [sys.executable, SCRIPT, "--config", path],
                input=stdin, capture_output=True, text=True, check=False,
            )

    def test_reads_files_from_stdin(self):
        r = self.run_cli(json.dumps(CONFIG), "docs/a.md\n.github/workflows/ci.yml\n")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "sensitive"))

    def test_broken_config_exits_2(self):
        r = self.run_cli("{nope", "a\n")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")


if __name__ == "__main__":
    unittest.main()
