"""Checks the plugin/marketplace manifests, hook wiring, harness config and copied files."""
import filecmp
import glob
import json
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESERVED_PREFIXES = ("claude-", "anthropic-", "anthropics-", "cc-plugin-")
TIERS = {"fast", "standard", "sensitive"}


def load(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


class JsonFilesTest(unittest.TestCase):
    def test_every_tracked_json_file_parses(self):
        files = subprocess.run(
            ["git", "ls-files", "*.json", "*.json.tmpl"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.split()
        self.assertTrue(files)
        for rel in files:
            with self.subTest(file=rel):
                load(rel)


class PluginManifestTest(unittest.TestCase):
    def setUp(self):
        self.plugin = load(".claude-plugin/plugin.json")
        self.market = load(".claude-plugin/marketplace.json")

    def test_plugin_name(self):
        name = self.plugin["name"]
        self.assertRegex(name, r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertFalse(name.startswith(RESERVED_PREFIXES), "reserved plugin name prefix")
        for key in ("version", "description", "author", "license"):
            self.assertIn(key, self.plugin)

    def test_marketplace(self):
        for key in ("name", "owner", "plugins"):
            self.assertIn(key, self.market)
        self.assertRegex(self.market["name"], r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
        self.assertTrue(self.market["owner"].get("name"))
        entries = {p["name"]: p for p in self.market["plugins"]}
        entry = entries[self.plugin["name"]]  # entry name must equal the manifest name
        self.assertEqual(entry["source"], "./")

    def test_plugin_hooks_fail_closed(self):
        hooks = load("hooks/hooks.json")["hooks"]["PreToolUse"]
        bash = [h for m in hooks if m["matcher"] == "Bash" for h in m["hooks"]]
        self.assertEqual(len(bash), 1)
        h = bash[0]
        self.assertEqual(h["onFailure"], "block")
        self.assertEqual(h["args"][0], "${CLAUDE_PLUGIN_ROOT}/scripts/guard-bash.py")
        self.assertIn("--require-config", h["args"])
        self.assertTrue(os.path.exists(os.path.join(ROOT, "scripts", "guard-bash.py")))

    def test_project_settings_wire_guard(self):
        for rel, script in ((".claude/settings.json", "/scripts/guard-bash.py"),
                            ("harness/core/settings.json", "/scripts/hooks/guard-bash.py")):
            with self.subTest(file=rel):
                h = load(rel)["hooks"]["PreToolUse"][0]["hooks"][0]
                self.assertEqual(h["onFailure"], "block")
                self.assertEqual(h["args"], ["${CLAUDE_PROJECT_DIR}" + script])
                self.assertNotIn("--require-config", h["args"])

    def test_skills_have_frontmatter(self):
        skills = glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md"))
        self.assertTrue(skills)
        for path in skills:
            with self.subTest(skill=path), open(path, encoding="utf-8") as f:
                text = f.read()
            m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
            self.assertIsNotNone(m)
            self.assertRegex(m.group(1), r"(?m)^name: " + re.escape(os.path.basename(os.path.dirname(path))) + "$")
            self.assertRegex(m.group(1), r"(?m)^description: .{20,}")


class HarnessConfigTest(unittest.TestCase):
    """Minimal structural check against harness/core/harness.config.schema.json (no jsonschema dependency)."""

    def check_config(self, cfg):
        schema = load("harness/core/harness.config.schema.json")
        for key in schema["required"]:
            self.assertIn(key, cfg)
        self.assertLessEqual(set(cfg), set(schema["properties"]))
        self.assertEqual(cfg["version"], 1)
        self.assertTrue(cfg["requiredChecks"])
        self.assertIn(cfg["riskTiers"]["default"], TIERS)
        for rule in cfg["riskTiers"]["rules"]:
            self.assertEqual(set(rule), {"glob", "tier"})
            self.assertIn(rule["tier"], TIERS)
        self.assertIn(cfg["review"]["independence"], {"author-or-marker", "author"})

    def test_repo_config(self):
        self.check_config(load("harness.config.json"))

    def test_template_config(self):
        self.check_config(load("harness/core/harness.config.json.tmpl"))


class CopiesInSyncTest(unittest.TestCase):
    """Files this repo uses itself and also ships to target projects must not drift."""

    PAIRS = [
        ("scripts/guard-bash.py", "harness/core/hooks/guard-bash.py"),
        ("tests/guard-bash_test.sh", "harness/core/hooks/guard-bash_test.sh"),
        (".github/pull_request_template.md", "harness/core/github/pull_request_template.md"),
    ]

    def test_pairs_identical(self):
        for a, b in self.PAIRS:
            with self.subTest(a=a, b=b):
                self.assertTrue(filecmp.cmp(os.path.join(ROOT, a), os.path.join(ROOT, b), shallow=False))

    def test_scripts_executable(self):
        files = subprocess.run(["git", "ls-files", "*.sh", "*.py"], cwd=ROOT, capture_output=True,
                               text=True, check=True).stdout.split()
        for rel in files:
            if rel.startswith("tests/test_") or "/verify.d/" in rel:
                continue  # unittest modules and sourced fragments
            with self.subTest(file=rel):
                self.assertTrue(os.access(os.path.join(ROOT, rel), os.X_OK))


if __name__ == "__main__":
    unittest.main()
