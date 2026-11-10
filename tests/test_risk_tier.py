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
            ("a/**/b", "a/b", True),
            ("a/**/b", "a/x/y/b", True),
            ("a/**/b", "a/xb", False),
            ("**", "anything/at/all", True),
            ("src/*.ts", "src/a.ts", True),
            ("src/*.ts", "src/x/a.ts", False),
            ("*.md", "README.md", True),
            ("*.md", "docs/README.md", False),
            ("f[1].txt", "f[1].txt", True),
            ("f[1].txt", "f1.txt", False),
            ("a+b/(c)", "a+b/(c)", True),
            ("docs/**", "docs", False),
            (".github/**", ".github/workflows/ci.yml", True),
            (".github/**", "x.github/a", False),
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

    def test_unknown_paths_use_the_default(self):
        for default in ("fast", "standard", "sensitive"):
            cfg = {"riskTiers": {"default": default, "rules": CONFIG["riskTiers"]["rules"]}}
            with self.subTest(default=default):
                self.assertEqual(risk_tier.tier_for(cfg, ["unknown/path.xyz"]), default)

    def test_rule_order_does_not_matter(self):
        rules = list(reversed(CONFIG["riskTiers"]["rules"]))
        cfg = {"riskTiers": {"default": "standard", "rules": rules}}
        self.assertEqual(risk_tier.tier_for(cfg, [".github/README.md"]), "sensitive")
        self.assertEqual(risk_tier.tier_for(cfg, ["skills/a/SKILL.md"]), "standard")

    def test_rules_only_raise_above_a_matching_lower_rule(self):
        # a fast rule still lowers an unmatched file below the default: that's the contract
        self.assertEqual(self.tier(["notes.md"]), "fast")
        self.assertEqual(self.tier([".github/notes.md"]), "sensitive")

    def test_tier_table(self):
        cases = [
            (["docs/a.md"], "fast"),
            (["src/a.go"], "standard"),
            (["internal/auth/x.go"], "sensitive"),
            (["harness.config.json"], "sensitive"),
            (["cmd/app/main.go"], "sensitive"),
            (["cmd/app/other.go"], "standard"),
            (["docs/a.md", "docs/b.md", "src/a.go"], "standard"),
            (["docs/a.md", "src/a.go", "internal/auth/x.go"], "sensitive"),
        ]
        for files, want in cases:
            with self.subTest(files=files):
                self.assertEqual(self.tier(files), want)

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


class RepoConfigTest(unittest.TestCase):
    """This repo's own harness.config.json: whoever can change the gate, its rules or its
    tests' fake must not get a self-mergeable (non-sensitive) PR."""

    def test_gate_and_contract_paths_are_sensitive(self):
        with open(os.path.join(ROOT, "harness.config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        for path in ["harness/core/scripts/pr-ready.sh", "harness/core/scripts/risk-tier.py",
                     "harness/core/scripts/branch-protection.sh", "harness/core/hooks/guard-bash.py",
                     "scripts/guard-bash.py", "scripts/verify.sh", "hooks/hooks.json", ".claude/settings.json",
                     ".claude/CLAUDE.md", ".github/workflows/ci.yml", "docs/contract.md", "tests/fakes/gh",
                     "harness.config.json"]:
            with self.subTest(path=path):
                self.assertEqual(risk_tier.tier_for(cfg, [path]), "sensitive")
        self.assertEqual(risk_tier.tier_for(cfg, ["docs/merge-gate.md"]), "fast")


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

    def test_malformed_configs_exit_2_without_traceback(self):
        bad = [
            [],
            "standard",
            {"riskTiers": []},
            {"riskTiers": {"default": "yolo"}},
            {"riskTiers": {"default": "fast", "rules": [{"glob": "a", "tier": "nope"}]}},
            {"riskTiers": {"default": "fast", "rules": [{"tier": "fast"}]}},
            {"riskTiers": {"default": "fast", "rules": [{"glob": 5, "tier": "fast"}]}},
            {"riskTiers": {"default": "fast", "rules": [{"glob": None, "tier": "fast"}]}},
            {"riskTiers": {"default": "fast", "rules": "docs/**"}},
            {"riskTiers": {"default": "fast", "rules": ["docs/**"]}},
            {"riskTiers": {"default": ["fast"]}},
        ]
        for cfg in bad:
            with self.subTest(cfg=cfg):
                r = self.run_cli(json.dumps(cfg), "a\n")
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertEqual(r.stdout, "")
                self.assertNotIn("Traceback", r.stderr)

    def test_missing_config_file_exits_2(self):
        r = subprocess.run([sys.executable, SCRIPT, "--config", "/nonexistent/c.json", "a"],
                           capture_output=True, text=True, check=False)
        self.assertEqual((r.returncode, r.stdout), (2, ""))

    def test_file_args_instead_of_stdin(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(CONFIG, f)
            r = subprocess.run([sys.executable, SCRIPT, "--config", path, "docs/a.md", "src/x.go"],
                               input=".github/x\n", capture_output=True, text=True, check=False)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "standard"))

    def test_empty_stdin_is_default(self):
        r = self.run_cli(json.dumps(CONFIG), "")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "standard"))

    def test_blank_lines_and_crlf_ignored(self):
        r = self.run_cli(json.dumps(CONFIG), "\n\ndocs/a.md\r\n\n")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "fast"))

    def test_paths_are_not_trimmed_of_spaces(self):
        # "docs/a.md " is not a .md file; trimming it would lower its tier
        cfg = {"riskTiers": {"default": "standard", "rules": [{"glob": "**/*.md", "tier": "fast"}]}}
        r = self.run_cli(json.dumps(cfg), "docs/a.md \n")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "standard"))


if __name__ == "__main__":
    unittest.main()
