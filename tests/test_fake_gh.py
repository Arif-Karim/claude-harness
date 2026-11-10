"""The fake gh must reject what real gh / GitHub reject, or the gate tests prove too little."""
import json
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE = os.path.join(ROOT, "tests", "fakes", "gh")
PAGES = [{"n": 1}, {"n": 2}]
GOOD = ("query($n:Int!,$endCursor:String){x(first:100,after:$endCursor){nodes{id} "
        "pageInfo{hasNextPage endCursor}}}")


def fake(args, routes):
    with tempfile.TemporaryDirectory() as d:
        rf = os.path.join(d, "routes.json")
        with open(rf, "w", encoding="utf-8") as f:
            json.dump(routes, f)
        p = subprocess.run([FAKE] + args, env=dict(os.environ, FAKE_GH_ROUTES=rf), capture_output=True,
                           text=True, check=False)
        return p.returncode, p.stdout


class FakeGhTest(unittest.TestCase):
    def test_graphql_paginate_needs_cursor_plumbing(self):
        routes = [{"match": r"^api graphql", "pages": PAGES}]
        code, out = fake(["api", "graphql", "--paginate", "-f", "query=" + GOOD, "--jq", ".n"], routes)
        self.assertEqual((code, out), (0, "1\n2\n"))
        for bad in (GOOD.replace("after:$endCursor", ""),            # declared, unused
                    GOOD.replace(" pageInfo{hasNextPage endCursor}", ""),  # no pageInfo
                    GOOD.replace(",$endCursor:String", "").replace(",after:$endCursor", "")):
            with self.subTest(query=bad):
                code, _ = fake(["api", "graphql", "--paginate", "-f", "query=" + bad], routes)
                self.assertEqual(code, 1)

    def test_unused_cursor_rejected_without_paginate(self):
        q = GOOD.replace("after:$endCursor", "")
        code, _ = fake(["api", "graphql", "-f", "query=" + q], [{"match": r"^api graphql", "stdout": "{}"}])
        self.assertEqual(code, 1)

    def test_pages_only_with_paginate(self):
        routes = [{"match": r"^api x$", "pages": PAGES}, {"match": r"^api --paginate x$", "pages": PAGES}]
        self.assertEqual(fake(["api", "x", "--jq", ".n"], routes), (0, "1\n"))
        self.assertEqual(fake(["api", "--paginate", "x", "--jq", ".n"], routes), (0, "1\n2\n"))

    def test_anchored_query_strings(self):
        routes = [{"match": r"^api a\?ref=r&state=open$", "stdout": "[]"}]
        self.assertEqual(fake(["api", "a?ref=r&state=open"], routes)[0], 0)
        self.assertEqual(fake(["api", "a?ref=r"], routes)[0], 1)

    def test_sequence(self):
        with tempfile.TemporaryDirectory() as d:
            rf = os.path.join(d, "r.json")
            with open(rf, "w", encoding="utf-8") as f:
                json.dump([{"match": "^x$", "sequence": [{"stdout": "one"}, {"stdout": "two"}]}], f)
            env = dict(os.environ, FAKE_GH_ROUTES=rf)
            outs = [subprocess.run([FAKE, "x"], env=env, capture_output=True, text=True, check=True).stdout
                    for _ in range(3)]
        self.assertEqual(outs, ["one\n", "two\n", "two\n"])


if __name__ == "__main__":
    unittest.main()
