"""パターンの状態の決め方と、`ccnavi --learnings`。"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import tempfile
import unittest

from ccnavi.records import wiki, wiki_load
from tests import ROOT
from tests.inproc import run_ccnavi
from tests.learningsample import impact, pattern

UTC = datetime.UTC


def status_of(name, sources, impacts):
    pats = {name: pattern(sources)}
    imps = {f"i{n}": text for n, text in enumerate(impacts)}
    found = wiki.build(pats, imps)
    return wiki.rows(found)[0].status


class StatusTest(unittest.TestCase):
    def test_no_impact_is_candidate(self):
        self.assertEqual(status_of("p", ["a"], []), wiki.CANDIDATE)

    def test_pending_wins_over_everything(self):
        got = status_of(
            "p",
            ["a"],
            [
                impact("x", "却下", {"p": 1}, "2026-02-01T00:00:00+00:00"),
                impact("y", "承認待ち", {"p": 1}, ""),
            ],
        )
        self.assertEqual(got, wiki.PROPOSING)

    def test_latest_decision_wins(self):
        old_defer = impact("a", "延期", {"p": 1}, "2026-01-01T00:00:00+00:00")
        new_reject = impact("b", "却下", {"p": 1}, "2026-02-01T00:00:00+00:00")
        self.assertEqual(status_of("p", ["a", "b", "c"], [old_defer, new_reject]), wiki.SETTLED)
        new_defer = impact("c", "延期", {"p": 1}, "2026-03-01T00:00:00+00:00")
        self.assertEqual(status_of("p", ["a", "b", "c"], [new_reject, new_defer]), wiki.CANDIDATE)

    def test_same_day_is_ordered_by_time_and_timezone(self):
        # 09:00+09:00 は 00:00Z。UTC の 00:30 のほうが新しい。
        earlier = impact("a", "却下", {"p": 1}, "2026-01-01T09:00:00+09:00")
        later = impact("b", "延期", {"p": 1}, "2026-01-01T00:30:00+00:00")
        self.assertEqual(status_of("p", ["a", "b"], [earlier, later]), wiki.CANDIDATE)
        self.assertEqual(status_of("p", ["a", "b"], [later, earlier]), wiki.CANDIDATE)

    def test_tie_goes_to_the_later_proposal_name(self):
        a = impact("aaa", "延期", {"p": 1})
        b = impact("zzz", "却下", {"p": 1})
        self.assertEqual(status_of("p", ["a", "b"], [a, b]), wiki.SETTLED)

    def test_deferral_is_compared_per_pattern(self):
        text = impact("d", "延期", {"p": 2, "q": 5})
        pats = {"p": pattern(["a", "b", "c"]), "q": pattern(["a", "b", "c"])}
        found = wiki.build(pats, {"d": text})
        by = {r.name: r.status for r in wiki.rows(found)}
        self.assertEqual(by, {"p": wiki.CANDIDATE, "q": wiki.SETTLED})

    def test_deferral_with_rewritten_count_does_not_return_at_once(self):
        # 決着のときに数を今の値へ書き直した延期は、マージ直後に候補へ戻らない
        defer = impact("d", "延期", {"p": 3})
        self.assertEqual(status_of("p", ["a", "b", "c"], [defer]), wiki.SETTLED)
        self.assertEqual(status_of("p", ["a", "b", "c", "d"], [defer]), wiki.CANDIDATE)

    def test_duplicate_sources_count_once(self):
        found = wiki.build({"p": pattern(["a", "A", "a", "b"])}, {})
        self.assertEqual(wiki.rows(found)[0].count, 2)

    def test_broken_files_are_named_and_not_counted(self):
        found = wiki.build(
            {"ok": pattern(["a"]), "bad": "no front"},
            {"i": "---\nstatus: ?\n---\n", "good": impact("g", "却下", {"ok": 1})},
        )
        self.assertEqual([r.name for r in wiki.rows(found)], ["ok"])
        self.assertEqual(
            sorted(b.file for b in found.broken),
            ["docs/wiki/impact/i.md", "docs/wiki/patterns/bad.md"],
        )
        self.assertEqual(len(found.impacts), 1)

    def test_pending_list_and_naive_datetime_is_broken(self):
        naive = impact("n", "却下", {"p": 1}, "2026-01-01T00:00:00")
        found = wiki.build(
            {"p": pattern(["a"])},
            {"w": impact("w", "承認待ち", {"p": 1}, ""), "n": naive},
        )
        self.assertEqual([i.proposal for i in wiki.pending(found)], ["w"])
        self.assertEqual([b.file for b in found.broken], ["docs/wiki/impact/n.md"])


class LearningsCliTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-learnings-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".claude"))
        self.write("docs/wiki/patterns/p1.md", pattern(["a", "b", "c"], "rule"))
        self.write("docs/wiki/patterns/p2.md", pattern(["a"], "skill"))
        self.write("docs/wiki/patterns/broken.md", "x\n")
        self.write("docs/wiki/impact/w.md", impact("w", "承認待ち", {"p2": 1}, ""))

    def write(self, rel, text):
        path = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def run_cli(self, *args, env=None):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.update(env or {})
        return run_ccnavi(["--root", self.root, "--learnings", *args], cwd=ROOT, env=environment)

    def test_json(self):
        done = self.run_cli("--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        data = json.loads(done.stdout)
        self.assertEqual(data["threshold"], 3)
        self.assertEqual(data["candidates"], 1)
        self.assertEqual(data["reached"], 1)
        rows = {r["name"]: r for r in data["patterns"]}
        self.assertEqual(rows["p1"]["status"], "候補")
        self.assertTrue(rows["p1"]["reached"])
        self.assertEqual(rows["p2"]["status"], "提案中")
        self.assertEqual([p["proposal"] for p in data["pending"]], ["w"])
        self.assertEqual([b["file"] for b in data["broken"]], ["docs/wiki/patterns/broken.md"])
        self.assertEqual(data["by_target"]["rule"]["candidates"], 1)

    def test_threshold_from_env(self):
        data = json.loads(self.run_cli("--json", env={"CCNAVI_LEARNING_SOURCES": "1"}).stdout)
        self.assertEqual(data["threshold"], 1)
        data = json.loads(self.run_cli("--json", env={"CCNAVI_LEARNING_SOURCES": "zero"}).stdout)
        self.assertEqual(data["threshold"], 3)

    def test_text_names_the_broken_file(self):
        done = self.run_cli()
        self.assertIn("broken.md", done.stdout)
        self.assertIn("承認待ちの提案", done.stdout)

    def test_not_with_other_routes(self):
        done = run_ccnavi(
            ["--root", self.root, "--learnings", "--lint"], cwd=ROOT, env=dict(os.environ)
        )
        self.assertNotEqual(done.returncode, 0)

    def test_missing_root_is_an_error(self):
        done = run_ccnavi(
            ["--root", os.path.join(self.root, "nope"), "--learnings"],
            cwd=ROOT,
            env=dict(os.environ),
        )
        self.assertNotEqual(done.returncode, 0)

    def test_loader_reads_a_commit_without_checkout(self):
        import subprocess

        def git(*a):
            subprocess.run(
                ["git", "-C", self.root, "-c", "user.email=a@b", "-c", "user.name=t", *a],
                check=True,
                capture_output=True,
            )

        git("init", "--quiet", "-b", "main")
        git("add", "-A")
        git("commit", "--quiet", "-m", "wiki")
        sha = wiki_load.commit_of(self.root, "main")
        os.remove(os.path.join(self.root, "docs/wiki/patterns/p1.md"))
        found = wiki_load.load_commit(self.root, sha)
        self.assertEqual(sorted(p.name for p in found.patterns), ["p1", "p2"])
        self.assertIsNone(wiki_load.load_commit(self.root, "0" * 40))


if __name__ == "__main__":
    unittest.main()
