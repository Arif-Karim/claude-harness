"""Unit tests for scripts/guard-bash.py internals (the table test covers end-to-end behaviour)."""
import importlib.util
import json
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("guard", os.path.join(ROOT, "scripts", "guard-bash.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class ConfigTest(unittest.TestCase):
    def write(self, d, text):
        with open(os.path.join(d, "harness.config.json"), "w", encoding="utf-8") as f:
            f.write(text)

    def test_absent_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(guard.load_config(d))

    def test_reads_default_branch(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, json.dumps({"defaultBranch": "trunk"}))
            self.assertEqual(guard.load_config(d)["defaultBranch"], "trunk")

    def test_rejects_bad_shapes(self):
        for text in ("{nope", "[]", '{"defaultBranch": ""}', '{"defaultBranch": 3}'):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as d:
                self.write(d, text)
                with self.assertRaises(ValueError):
                    guard.load_config(d)


class ParsingTest(unittest.TestCase):
    def test_segments_split_on_operators(self):
        segs = list(guard.segments("a b && c; d | e\nf"))
        self.assertEqual(segs, [["a", "b"], ["c"], ["d"], ["e"], ["f"]])

    def test_segments_keep_quoted_operators(self):
        self.assertEqual(list(guard.segments("echo 'a && b'")), [["echo", "a && b"]])

    def test_substitutions(self):
        self.assertEqual(list(guard.substitutions("echo $(a $(b)) `c`")), ["c", "a $(b)", "b"])
        self.assertEqual(list(guard.substitutions("echo $(unclosed")), ["unclosed"])

    def test_tag_names(self):
        for name, want in [
            ("v1", True), ("v1.2.3", True), ("pkg-v0.2.0", True), ("v1.2.3-rc.1", True),
            ("lane-v2-ui", False), ("lane-a", False), ("main", False), ("vnext", False),
        ]:
            with self.subTest(name=name):
                self.assertEqual(bool(guard.TAG_NAME.fullmatch(name)), want)


class GhApiTest(unittest.TestCase):
    def check(self, args):
        guard.Guard("main", ".").check_gh_api(args)

    def test_reads_allowed(self):
        self.check(["repos/o/r/pulls", "--jq", ".[] | .number"])
        self.check(["-X", "GET", "repos/o/r/code-scanning/alerts", "-f", "ref=x"])
        self.check(["--method=HEAD", "repos/o/r"])
        self.check(["graphql", "-f", "query=query { viewer { login } }"])

    def test_writes_blocked(self):
        for args in (
            ["-X", "POST", "repos/o/r/issues"],
            ["repos/o/r/issues", "-fbody=x"],
            ["repos/o/r/issues", "--field=body=x"],
            ["graphql", "-f", "query=mutation{x}"],
            ["graphql", "--input", "q.json"],
            ["graphql"],
        ):
            with self.subTest(args=args), self.assertRaises(guard.Blocked):
                self.check(args)


if __name__ == "__main__":
    unittest.main()
