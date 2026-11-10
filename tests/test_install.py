"""Tests for harness/core/scripts/install.sh: the deterministic file operations of harness-init.

Each test copies a fixture project from tests/fixtures/ into a temp dir, runs the installer
against it and checks what landed, that the config is valid against the schema, and that a
re-run changes nothing.
"""
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL = os.path.join(ROOT, "harness", "core", "scripts", "install.sh")
VALIDATOR = os.path.join(ROOT, "harness", "core", "scripts", "validate-config.py")
SCHEMA = os.path.join(ROOT, "harness", "core", "harness.config.schema.json")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
# A template placeholder such as {{DEFAULT_BRANCH}}, but not a GitHub expression ${{ ... }}.
PLACEHOLDER = re.compile(r"(?<!\$)\{\{\s*[A-Z_]+\s*\}\}")

CORE_FILES = [
    "harness.config.json",
    "CLAUDE.md",
    ".claude/settings.json",
    "scripts/hooks/guard-bash.py",
    "scripts/hooks/guard-bash_test.sh",
    "scripts/verify.sh",
    "scripts/pr-ready.sh",
    "scripts/risk-tier.py",
    "scripts/branch-protection.sh",
    ".github/pull_request_template.md",
    "docs/catch-log.md",
]
GO_FILES = ["scripts/verify.d/20-go.sh", "scripts/mutation-go.sh"]
TS_FILES = ["scripts/verify.d/30-ts.sh", "scripts/mutation-ts.sh"]
EXECUTABLES = [
    "scripts/hooks/guard-bash.py",
    "scripts/hooks/guard-bash_test.sh",
    "scripts/verify.sh",
    "scripts/pr-ready.sh",
    "scripts/risk-tier.py",
    "scripts/branch-protection.sh",
]


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_config", VALIDATOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def snapshot(root):
    """Map of relative path -> (bytes, mode) for every file under root."""
    out = {}
    for dirpath, _, files in os.walk(root):
        for name in files:
            p = os.path.join(dirpath, name)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root)] = (f.read(), stat.S_IMODE(os.stat(p).st_mode))
    return out


def read(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as f:
        return f.read()


class InstallerCase(unittest.TestCase):
    fixture = None

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="harness-install-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.target = os.path.join(self.tmp, "project")
        if self.fixture:
            shutil.copytree(os.path.join(FIXTURES, self.fixture), self.target)
        else:
            os.makedirs(self.target)

    def install(self, *args, check=True):
        cmd = [INSTALL, "--target", self.target, "--project-name", "demo", "--default-branch", "main", *args]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if check:
            self.assertEqual(res.returncode, 0, f"install failed:\n{res.stdout}\n{res.stderr}")
        return res

    def assert_files(self, rels):
        for rel in rels:
            with self.subTest(file=rel):
                self.assertTrue(os.path.isfile(os.path.join(self.target, rel)), f"missing {rel}")

    def assert_valid_config(self):
        errors = load_validator().validate_file(os.path.join(self.target, "harness.config.json"), SCHEMA)
        self.assertEqual(errors, [])
        return json.loads(read(self.target, "harness.config.json"))

    def assert_idempotent(self, *args):
        before = snapshot(self.target)
        res = self.install(*args)
        self.assertEqual(snapshot(self.target), before, "a re-run changed files")
        self.assertRegex(res.stdout, r"\b0 created, 0 updated\b")

    def assert_no_placeholders(self):
        for rel in snapshot(self.target):
            if rel.endswith((".sh", ".py", ".yml", ".yaml", ".json", ".md")):
                with self.subTest(file=rel):
                    self.assertIsNone(PLACEHOLDER.search(read(self.target, rel)), f"unfilled placeholder in {rel}")

    def assert_common(self):
        self.assert_files(CORE_FILES)
        for rel in EXECUTABLES:
            with self.subTest(executable=rel):
                self.assertTrue(os.access(os.path.join(self.target, rel), os.X_OK))
        claude = read(self.target, "CLAUDE.md")
        self.assertEqual(claude.count("<!-- harness:begin"), 1)
        self.assertEqual(claude.count("<!-- harness:end -->"), 1)
        settings = json.loads(read(self.target, ".claude/settings.json"))
        guards = [h for g in settings["hooks"]["PreToolUse"] for h in g["hooks"]
                  if h.get("args") == ["${CLAUDE_PROJECT_DIR}/scripts/hooks/guard-bash.py"]]
        self.assertEqual(len(guards), 1)
        self.assertEqual(guards[0]["onFailure"], "block")
        log = read(self.target, "docs/catch-log.md")
        self.assertIn("| Date | Bug | Caught by | Should have caught | Follow-up |", log)
        self.assertNotIn("Example:", log)
        self.assert_no_placeholders()
        for rel in snapshot(self.target):
            if rel.endswith(".sh"):
                with self.subTest(syntax=rel):
                    res = subprocess.run(["bash", "-n", os.path.join(self.target, rel)], capture_output=True, text=True)
                    self.assertEqual(res.returncode, 0, res.stderr)
                if shutil.which("shellcheck") and rel.startswith("scripts/"):
                    with self.subTest(shellcheck=rel):
                        res = subprocess.run(["shellcheck", "-x", rel], cwd=self.target, capture_output=True, text=True)
                        self.assertEqual(res.returncode, 0, res.stdout)

    def detect(self):
        res = subprocess.run([INSTALL, "--detect", "--target", self.target], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout)


class GoAppTest(InstallerCase):
    fixture = "go-app"

    def test_detect(self):
        self.assertEqual(self.detect(), {"stacks": ["go"], "goDir": "."})

    def test_install_and_rerun(self):
        self.install()
        self.assert_common()
        self.assert_files(GO_FILES + [".golangci.yml", ".github/workflows/ci.yml", ".github/workflows/codeql.yml"])
        for rel in TS_FILES:
            self.assertFalse(os.path.exists(os.path.join(self.target, rel)), rel)
        cfg = self.assert_valid_config()
        self.assertEqual(cfg["stacks"], ["go"])
        self.assertEqual(cfg["defaultBranch"], "main")
        self.assertEqual(cfg["requiredChecks"], ["verify", "mutation", "gitleaks"])
        ci = read(self.target, ".github/workflows/ci.yml")
        for job in ("verify:", "mutation:", "gitleaks:"):
            self.assertRegex(ci, r"(?m)^  " + job)
        self.assertIn("go-version-file: ./go.mod", ci)
        self.assertNotIn("setup-node", ci)
        self.assertIn('branches: ["main"]', ci)
        self.assertIn('GO_DIR="${GO_DIR:-.}"', read(self.target, "scripts/verify.d/20-go.sh"))
        self.assertIn("# demo: agent instructions", read(self.target, "CLAUDE.md"))
        self.assertFalse(os.path.exists(os.path.join(self.target, ".github/workflows/claude-e2e.yml")))
        self.assert_idempotent()

    def test_installed_guard_table_test_passes(self):
        self.install()
        res = subprocess.run(["scripts/hooks/guard-bash_test.sh"], cwd=self.target, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_rerun_upgrades_harness_files_and_keeps_user_edits(self):
        self.install()
        claude = os.path.join(self.target, "CLAUDE.md")
        with open(claude, "a", encoding="utf-8") as f:
            f.write("\n## Team notes\nKeep me.\n")
        text = read(self.target, "CLAUDE.md").replace("### Roles", "### Roles (stale)")
        with open(claude, "w", encoding="utf-8") as f:
            f.write(text)
        with open(os.path.join(self.target, "scripts/pr-ready.sh"), "a", encoding="utf-8") as f:
            f.write("# stale local edit\n")
        cfg_path = os.path.join(self.target, "harness.config.json")
        cfg = json.loads(read(self.target, "harness.config.json"))
        cfg["riskTiers"]["rules"].append({"glob": "internal/payments/**", "tier": "sensitive"})
        cfg["coverageMin"] = 85
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

        res = self.install()
        self.assertIn("update scripts/pr-ready.sh", res.stdout)
        self.assertNotIn("stale", read(self.target, "scripts/pr-ready.sh"))
        claude_text = read(self.target, "CLAUDE.md")
        self.assertNotIn("(stale)", claude_text)
        self.assertIn("Keep me.", claude_text)
        self.assertEqual(claude_text.count("<!-- harness:begin"), 1)
        cfg = self.assert_valid_config()
        self.assertIn({"glob": "internal/payments/**", "tier": "sensitive"}, cfg["riskTiers"]["rules"])
        self.assertEqual(cfg["coverageMin"], 85)
        self.assertIn("Coverage ≥ 85%", claude_text)
        self.assert_idempotent()

    def test_flags_update_config_fields(self):
        self.install()
        self.install("--coverage-min", "80", "--mutation-min", "50", "--default-branch", "trunk")
        cfg = self.assert_valid_config()
        self.assertEqual((cfg["coverageMin"], cfg["mutationMin"], cfg["defaultBranch"]), (80, 50, "trunk"))
        self.assertIn('branches: ["trunk"]', read(self.target, ".github/workflows/ci.yml"))
        self.assert_idempotent()

    def test_dry_run_writes_nothing(self):
        before = snapshot(self.target)
        res = self.install("--dry-run")
        self.assertEqual(snapshot(self.target), before)
        self.assertIn("create scripts/verify.sh", res.stdout)

    def test_dry_run_after_install_shows_diff(self):
        self.install()
        with open(os.path.join(self.target, "scripts/verify.sh"), "a", encoding="utf-8") as f:
            f.write("# local edit\n")
        before = snapshot(self.target)
        res = self.install("--dry-run")
        self.assertEqual(snapshot(self.target), before)
        self.assertIn("update scripts/verify.sh", res.stdout)
        self.assertIn("-# local edit", res.stdout)

    def test_e2e_opt_in(self):
        self.install("--e2e")
        e2e = read(self.target, ".github/workflows/claude-e2e.yml")
        self.assertIn("secrets.ANTHROPIC_API_KEY", e2e)
        self.assert_idempotent("--e2e")
        # A later run without --e2e keeps the workflow (never deletes).
        self.install()
        self.assertTrue(os.path.exists(os.path.join(self.target, ".github/workflows/claude-e2e.yml")))


class TsAppTest(InstallerCase):
    fixture = "ts-app"

    def test_detect(self):
        self.assertEqual(self.detect(), {"stacks": ["ts"], "tsDir": "."})

    def test_install_merges_existing_files_and_reruns_cleanly(self):
        self.install()
        self.assert_common()
        self.assert_files(TS_FILES + ["stryker.config.json", ".github/workflows/ci.yml", ".github/workflows/codeql.yml"])
        cfg = self.assert_valid_config()
        self.assertEqual(cfg["stacks"], ["ts"])
        claude = read(self.target, "CLAUDE.md")
        self.assertTrue(claude.startswith("# ts-app\n\nExisting project notes"), claude[:80])
        settings = json.loads(read(self.target, ".claude/settings.json"))
        self.assertEqual(settings["permissions"], {"allow": ["Bash(npm test:*)"]})
        self.assertEqual(settings["hooks"]["PostToolUse"][0]["matcher"], "Edit")
        ci = read(self.target, ".github/workflows/ci.yml")
        self.assertIn("setup-node", ci)
        self.assertNotIn("setup-go@", ci.split("gitleaks:")[0])
        self.assertIn("cache-dependency-path: ./package-lock.json", ci)
        self.assert_idempotent()


class MonorepoTest(InstallerCase):
    fixture = "go-ts-monorepo"

    def test_detect(self):
        self.assertEqual(self.detect(), {"stacks": ["go", "ts"], "goDir": "backend", "tsDir": "web"})

    def test_install_composes_stacks_and_keeps_existing_ci(self):
        own_ci = read(self.target, ".github/workflows/ci.yml")
        self.install()
        self.assert_common()
        self.assert_files(GO_FILES + TS_FILES + [
            "backend/.golangci.yml", "web/stryker.config.json",
            ".github/workflows/harness-ci.yml", ".github/workflows/codeql.yml",
        ])
        self.assertEqual(read(self.target, ".github/workflows/ci.yml"), own_ci)
        cfg = self.assert_valid_config()
        self.assertEqual(cfg["stacks"], ["go", "ts"])
        ci = read(self.target, ".github/workflows/harness-ci.yml")
        for job in ("verify", "mutation", "gitleaks"):
            self.assertEqual(len(re.findall(r"(?m)^  " + job + r":$", ci), 1), 1, job)
        verify_job = ci.split("\n  mutation:")[0]
        self.assertIn("go-version-file: backend/go.mod", verify_job)
        self.assertIn("setup-node", verify_job)
        mutation_job = ci.split("\n  mutation:")[1].split("\n  gitleaks:")[0]
        self.assertIn("scripts/mutation-go.sh", mutation_job)
        self.assertIn("scripts/mutation-ts.sh", mutation_job)
        self.assertIn("working-directory: web", mutation_job)
        codeql = read(self.target, ".github/workflows/codeql.yml")
        for lang in ("language: go", "language: javascript-typescript", "language: actions"):
            self.assertEqual(codeql.count(lang), 1, lang)
        self.assertIn('GO_DIR="${GO_DIR:-backend}"', read(self.target, "scripts/verify.d/20-go.sh"))
        self.assertIn('TS_DIR="${TS_DIR:-web}"', read(self.target, "scripts/verify.d/30-ts.sh"))
        self.assertIn('TS_DIR="${TS_DIR:-web}"', read(self.target, "scripts/mutation-ts.sh"))
        self.assertIn('GO_DIR="${GO_DIR:-backend}"', read(self.target, "scripts/mutation-go.sh"))
        self.assert_idempotent()

    def test_explicit_stacks_flag(self):
        self.install("--stacks", "go", "--go-dir", "backend")
        cfg = self.assert_valid_config()
        self.assertEqual(cfg["stacks"], ["go"])
        self.assertFalse(os.path.exists(os.path.join(self.target, "scripts/verify.d/30-ts.sh")))
        # A re-run without --stacks keeps what the config says, instead of re-detecting.
        self.assert_idempotent()


class ConflictTest(InstallerCase):
    fixture = "go-app"

    def write(self, rel, text):
        path = os.path.join(self.target, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_first_install_refuses_to_overwrite_foreign_files(self):
        self.write("scripts/verify.sh", "#!/bin/sh\necho mine\n")
        before = snapshot(self.target)
        res = self.install(check=False)
        self.assertEqual(res.returncode, 4, res.stdout + res.stderr)
        self.assertIn("conflict scripts/verify.sh", res.stdout)
        self.assertEqual(snapshot(self.target), before, "nothing may be written when there is a conflict")
        self.install("--force")
        self.assertNotIn("echo mine", read(self.target, "scripts/verify.sh"))

    def test_pr_template_kept_unless_asked(self):
        self.write(".github/pull_request_template.md", "our template\n")
        res = self.install()
        self.assertIn("skip .github/pull_request_template.md", res.stdout)
        self.assertEqual(read(self.target, ".github/pull_request_template.md"), "our template\n")
        self.assert_idempotent()
        self.install("--replace-pr-template")
        self.assertIn("harness:builder", read(self.target, ".github/pull_request_template.md"))

    def test_existing_catch_log_is_kept(self):
        self.write("docs/catch-log.md", "| Date | Bug | Caught by | Should have caught | Follow-up |\n|---|---|---|---|---|\n"
                   "| 2026-02-02 | real row | unit | unit | none |\n")
        self.install()
        self.assertIn("real row", read(self.target, "docs/catch-log.md"))

    def test_bad_arguments(self):
        for args in (["--stacks", "cobol"], ["--coverage-min", "101"], ["--bogus"]):
            with self.subTest(args=args):
                res = self.install(*args, check=False)
                self.assertEqual(res.returncode, 2, res.stdout + res.stderr)

    def test_invalid_existing_config_fails_closed(self):
        self.write("harness.config.json", '{"version": 2}')
        res = self.install(check=False)
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)


class EmptyProjectTest(InstallerCase):
    fixture = None

    def test_no_stack_installs_core_only(self):
        self.assertEqual(self.detect(), {"stacks": ["other"]})
        self.install()
        self.assert_common()
        cfg = self.assert_valid_config()
        self.assertEqual(cfg["stacks"], ["other"])
        self.assertEqual(cfg["requiredChecks"], ["verify", "gitleaks"])
        ci = read(self.target, ".github/workflows/ci.yml")
        self.assertNotIn("mutation:", ci)
        self.assert_idempotent()


class ValidatorTest(unittest.TestCase):
    def setUp(self):
        self.v = load_validator()
        with open(SCHEMA, encoding="utf-8") as f:
            self.schema = json.load(f)
        with open(os.path.join(ROOT, "harness.config.json"), encoding="utf-8") as f:
            self.good = json.load(f)

    def test_repo_config_is_valid(self):
        self.assertEqual(self.v.validate(self.good, self.schema), [])

    def test_invalid_configs(self):
        cases = {
            "version": lambda c: c.update(version=2),
            "missing review": lambda c: c.pop("review"),
            "extra key": lambda c: c.update(extra=1),
            "bad stack": lambda c: c.update(stacks=["cobol"]),
            "duplicate stack": lambda c: c.update(stacks=["go", "go"]),
            "no stacks": lambda c: c.update(stacks=[]),
            "bad tier": lambda c: c["riskTiers"].update(default="slow"),
            "rule extra key": lambda c: c["riskTiers"]["rules"].append({"glob": "a", "tier": "fast", "x": 1}),
            "empty branch": lambda c: c.update(defaultBranch=""),
            "coverage > 100": lambda c: c.update(coverageMin=101),
            "coverage type": lambda c: c.update(coverageMin="70"),
            "checks type": lambda c: c.update(requiredChecks="verify"),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                cfg = json.loads(json.dumps(self.good))
                mutate(cfg)
                self.assertNotEqual(self.v.validate(cfg, self.schema), [])


class TemplateContentTest(unittest.TestCase):
    """The stack packs and the e2e template carry the checks the contract promises."""

    def text(self, rel):
        return read(ROOT, rel)

    def test_go_pack(self):
        frag = self.text("harness/stacks/go/verify.d/20-go.sh")
        for needle in ("gofmt -l", "golangci-lint run", "go test -race", "COVERAGE_MIN", "govulncheck",
                       "-fuzz", "scripts/mutation-go.sh"):
            self.assertIn(needle, frag)
        lint = self.text("harness/stacks/go/.golangci.yml")
        for linter in ("gosec", "staticcheck", "errcheck", "revive"):
            self.assertIn(linter, lint)
        self.assertIn("--threshold-efficacy", self.text("harness/stacks/go/mutation-go.sh"))
        fuzz = self.text("harness/stacks/go/test-templates/fuzz_test.go.tmpl")
        self.assertIn("func Fuzz{{FUNC}}(f *testing.F)", fuzz)
        self.assertIn("f.Add(", fuzz)
        self.assertIn("rapid.Check", self.text("harness/stacks/go/test-templates/property_test.go.tmpl"))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "harness/stacks/go/test-templates/README.md")))

    def test_ts_pack(self):
        frag = self.text("harness/stacks/ts/verify.d/30-ts.sh")
        for needle in ("eslint", "tsc --noEmit", "vitest run --coverage", "scripts/mutation-ts.sh"):
            self.assertIn(needle, frag)
        self.assertIn("--thresholds.break", self.text("harness/stacks/ts/mutation-ts.sh"))
        self.assertIn("fc.assert", self.text("harness/stacks/ts/test-templates/property.test.ts.tmpl"))

    def test_e2e_template(self):
        e2e = self.text("harness/core/e2e/claude-e2e.yml")
        self.assertIn("secrets.ANTHROPIC_API_KEY != ''", e2e)
        self.assertIn("workflow_dispatch", e2e)
        self.assertNotIn("pull_request", e2e)
        for scenario in ("git push --force-with-lease", "gh pr merge", "gh api -X PUT",
                         "HEAD:{{DEFAULT_BRANCH}}", "refs/tags/"):
            self.assertIn(scenario, e2e)
        self.assertIn("Blocked by agent harness", e2e)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "harness/core/e2e/README.md")))

    def test_skill_covers_every_step(self):
        skill = self.text("skills/harness-init/SKILL.md")
        self.assertNotIn("stub", skill.lower())
        for needle in ("install.sh --detect", "--dry-run", "harness.config.json", "branch-protection.sh",
                       "scripts/verify.sh", "catch-log", ".claude/settings.json", "Idempotent"):
            self.assertIn(needle, skill)


if __name__ == "__main__":
    unittest.main()
