"""レビュー済みのマーカーの `actor` と、依頼の後に動いたかの判定の受入テスト。

見るのは 4 つ。

1. 手元の `review confirm --actor <アカウント>` は、マーカーに `actor` と `via: cli` を書き、
   履歴にもアカウントを足す。`--actor` が無ければ（sh がアカウントを引けなかった）
   マーカーも履歴も前と同じ中身
2. `--actor` の形と、`review confirm` の外で渡されたときは断る
3. 依頼の後にユーザが見るものが動いたかは、手元と Chrome が同じ関数（`review.moved_since`）
   で決める。Chrome は compare API の一覧を渡し、打ち切られた（null）なら動いたと数える
4. Chrome のレビュー済みのマーカーは、
   手元の `--actor` つきのマーカーと経路（`via`）と時刻のほかは同じ
"""

from __future__ import annotations

import json
import os
import shutil

from ccnavi.entry import lint, version
from ccnavi.infra import settings
from ccnavi.tickets import history, review
from tests.ticket.test_core import STAMP, CoreHarness
from tests.ticket.test_phases import child_text
from tests.ticket.test_ticket import git, read_json, write

RESULT = {"host": "fixture", "mr": {"number": 7, "url": "u/7"}, "threads": [], "reviews": []}


class ActorHarness(CoreHarness):
    def setUp(self):
        super().setUp()
        # 統合先の互換のマーカー（Chrome は版が違えば書く操作を受けない）
        write(
            os.path.join(self.root, *lint.SH_COMPAT_FILE.split(os.sep)),
            f"#!/bin/sh\nCCNAVI_COMPAT={version.COMPAT}\n",
        )

    def mark_path(self, kind="reviewed"):
        return os.path.join(self.parent_tree, ".ccnavi", "approved", "phases", "i0001", f"1.{kind}")

    def ready(self):
        """フェーズ 1 を依頼まで進め、取得した結果を置いた fixture を返す。"""
        fixture = self.reviewed_phase_one()
        self.commit_parent("requested")
        write(fixture, json.dumps(RESULT))
        return fixture

    def confirm_as(self, fixture, *extra):
        return self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--phase",
            "1",
            "review",
            "confirm",
            "--result",
            fixture,
            *extra,
        )

    def last_event(self, ident="i0001-01-01"):
        events, _ = history.read(self.approved, ident)
        return events[-1]

    def host_compare(self):
        recorded = read_json(self.mark_path("requested"))["head"]
        head = git(self.parent_tree, "rev-parse", "HEAD").strip()
        names = git(self.parent_tree, "diff", "--name-only", "--no-renames", f"{recorded}..HEAD")
        return {"base": recorded, "head": head, "files": names.split()}

    def chrome_confirm(self, compare, actor=None, head=None):
        """Chrome の confirm。読んだ `P` の先頭は `head`（既定は compare の先頭）。"""
        request = self.chrome_request(
            "confirm",
            "i0001",
            heads={"i0001": head or compare["head"]},
            phase=1,
            result=RESULT,
            compare=compare,
            **({"actor": actor} if actor else {}),
        )
        return self.ask_chrome(request)


class LocalActorTest(ActorHarness):
    def test_without_an_actor_the_mark_is_as_before(self):
        fixture = self.ready()
        done = self.confirm_as(fixture)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        mark = read_json(self.mark_path())
        self.assertEqual(sorted(mark), ["accepted", "at", "mr"])
        self.assertEqual((mark["mr"], mark["accepted"]), (7, []))
        event = self.last_event()
        self.assertNotIn("actor", event)
        self.assertEqual(event["via"], history.VIA_CLI)

    def test_an_actor_goes_into_the_mark_and_the_event(self):
        fixture = self.ready()
        done = self.confirm_as(fixture, "--actor", "octo-reviewer")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        mark = read_json(self.mark_path())
        self.assertEqual(list(mark), ["mr", "accepted", "actor", "via", "at"])
        self.assertEqual(
            {k: mark[k] for k in ("mr", "accepted", "actor", "via")},
            {"mr": 7, "accepted": [], "actor": "octo-reviewer", "via": "cli"},
        )
        event = self.last_event()
        self.assertEqual((event["actor"], event["via"]), ("octo-reviewer", history.VIA_CLI))

    def test_a_malformed_actor_is_refused_and_nothing_is_written(self):
        fixture = self.ready()
        for bad in ("a b", "x/y", "<script>", "a" * 101):
            done = self.confirm_as(fixture, "--actor", bad)
            self.assertNotEqual(done.returncode, 0, bad)
            self.assertIn("--actor", done.stderr)
            self.assertFalse(os.path.exists(self.mark_path()), bad)

    def test_an_actor_outside_review_confirm_is_refused(self):
        done = self.ccnavi("--actor", "octo", "--explain", "--json")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("review confirm", done.stderr)


class MovedSinceTest(ActorHarness):
    def conf(self):
        conf, _ = settings.load(self.root)
        conf.state = self.state
        conf.approved = ".ccnavi/approved"
        return conf

    def test_the_rule(self):
        conf = self.conf()
        mark = {"head": "a" * 40}
        self.assertEqual(review.moved_since(conf, mark, "a" * 40, None), "")
        self.assertEqual(review.moved_since(conf, mark, "b" * 40, [".ccnavi/approved/x.md"]), "")
        self.assertEqual(review.moved_since(conf, mark, "b" * 40, ["wip/proposals/todo/x.md"]), "")
        for changed in (None, ["src/app.py"], [".ccnavi/approved/x", "README.md"]):
            self.assertIn(
                "依頼の後に親の HEAD が動いている",
                review.moved_since(conf, mark, "b" * 40, changed),
            )
        self.assertIn("記録されていない", review.moved_since(conf, {}, "b" * 40, []))
        self.assertIn("動いている", review.moved_since(conf, {"head": "HEAD"}, "b" * 40, []))

    def test_code_moved_after_the_request_is_refused_the_same_by_chrome_and_locally(self):
        fixture = self.ready()
        write(os.path.join(self.parent_tree, "src", "late.py"), "print(1)\n")
        self.commit_parent("late code")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        compare = self.host_compare()
        self.assertIn("src/late.py", compare["files"])
        chrome = self.chrome_confirm(compare)
        local = self.confirm_as(fixture)
        self.assertEqual(local.returncode, 1)
        # 手元の案内はワークスペースルートからの絶対パス、Chrome は仮のツリーの部分を除いた相対パス
        said = local.stderr.replace(self.root + os.sep, "").splitlines()
        self.assertEqual(chrome["problems"], said)
        self.assertIn("依頼の後に親の HEAD が動いている", chrome["problems"][0])
        self.assertIsNone(chrome["changes"])

    def test_a_truncated_or_mismatched_compare_counts_as_moved(self):
        self.ready()
        compare = self.host_compare()
        for broken in (
            {**compare, "files": None},
            {**compare, "base": "c" * 40},
            {**compare, "head": "d" * 40},
        ):
            chrome = self.chrome_confirm(broken, head=compare["head"])
            self.assertIsNone(chrome["changes"], broken)
            self.assertTrue(
                any("依頼の後に親の HEAD が動いている" in p for p in chrome["problems"]), chrome
            )

    def test_only_the_places_moved_so_chrome_writes(self):
        self.ready()
        chrome = self.chrome_confirm(self.host_compare())
        self.assertEqual(chrome["problems"], [])
        self.assertIsNotNone(chrome["changes"])


class SameMarkTest(ActorHarness):
    def test_chrome_and_local_marks_differ_only_by_the_route_and_the_time(self):
        fixture = self.ready()
        chrome = self.chrome_confirm(
            self.host_compare(), actor={"account": "octo-reviewer", "version": "9.9.9"}
        )
        row = next(r for r in chrome["changes"]["i0001"] if r["path"].endswith("1.reviewed"))
        remote = json.loads(row["content"])
        done = self.confirm_as(fixture, "--actor", "octo-reviewer")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        local = read_json(self.mark_path())
        self.assertEqual((remote["via"], local["via"]), ("chrome", "cli"))
        self.assertEqual(remote["at"], STAMP)
        drop = ("via", "at")
        self.assertEqual(
            {k: v for k, v in remote.items() if k not in drop},
            {k: v for k, v in local.items() if k not in drop},
        )
        self.assertEqual(list(remote), list(local))


class ReviewableTest(ActorHarness):
    def test_the_board_names_requested_phases_until_they_are_reviewed(self):
        fixture = self.ready()
        board = self.ask_chrome(self.chrome_request("board", "i0001"))
        self.assertEqual(
            board["reviewable"],
            [{"phase": 1, "mr": 7, "host": "fixture", "children": ["i0001-01-01"]}],
        )
        # ボードの要求は取り込み状態相当を手元にもコピーする（手元は C1 の対象になり、直打ちの
        # confirm を断る）。ここでは手元のマーカーを置くためだけに取り込み状態を外す
        shutil.rmtree(os.path.join(self.state, "sync"))
        self.assertEqual(self.confirm_as(fixture).returncode, 0)
        self.commit_parent("reviewed")
        board = self.ask_chrome(self.chrome_request("board", "i0001"))
        self.assertEqual(board["reviewable"], [])


class ReviewRuleTest(ActorHarness):
    """Chrome のレビュー済みを入れた後のレビューで決めたこと。どれも厳しくする向き。

    ユーザごとの最新のレビューは Approve・変更要求・dismiss だけで選ぶ。レビュー済みのフェーズに
    confirm を重ねない。ccnavi の投稿のスレッドを未解決から除くのは、GitLab で依頼を投稿した
    アカウントが書いたときだけ。マージリクエストは親のブランチから引き、依頼の記録の番号と照合する。
    """

    @staticmethod
    def review(state, at, author="9001"):
        return review.Review(state, f"r/{state}/{at}", at, author)

    def test_a_comment_or_a_pending_review_does_not_clear_a_change_request(self):
        cr = self.review("CHANGES_REQUESTED", "2026-09-29T01:00:00Z")
        for later in ("COMMENTED", "PENDING"):
            got = review.effective([cr, self.review(later, "2026-09-29T02:00:00Z")])
            self.assertEqual([r.state for r in got], ["CHANGES_REQUESTED"], later)
        pending = review.Review("PENDING", "r/p", "", "9001")
        self.assertEqual([r.state for r in review.effective([cr, pending])], ["CHANGES_REQUESTED"])
        for later, left in (("APPROVED", ["APPROVED"]), ("DISMISSED", [])):
            got = review.effective([cr, self.review(later, "2026-09-29T02:00:00Z")])
            self.assertEqual([r.state for r in got], left, later)
        self.assertEqual(review.effective([self.review("COMMENTED", "2026-09-29T00:00:00Z")]), [])

    def test_a_marker_thread_is_skipped_only_on_gitlab_by_the_poster(self):
        marker = review.Thread(id="t", body="<!-- ccnavi:request i0001:1 -->", author="bot")
        self.assertEqual(review._unresolved([marker], set(), "github", "bot"), [marker])
        self.assertEqual(review._unresolved([marker], set(), "gitlab", ""), [marker])
        self.assertEqual(review._unresolved([marker], set(), "gitlab", "someone"), [marker])
        self.assertEqual(review._unresolved([marker], set(), "gitlab", "bot"), [])

    def test_a_crit_push_thread_is_counted_even_from_the_poster(self):
        """crit push の行のスレッドは目印で始まらないので、依頼を投稿したアカウントからでも数える。

        ユーザが依頼者と同じアカウントで crit push しても、指摘はレビュー済みを止める。
        """
        crit = review.Thread(
            id="d1", body="ここは X ではなく Y では", author="bot", path="wip/eli5/phase-1.html"
        )
        for host in ("github", "gitlab"):
            self.assertEqual(review._unresolved([crit], set(), host, "bot"), [crit], host)

    def test_a_request_record_without_host_or_mr_is_not_matched(self):
        result = review.Result(host="github", mr=review.MergeRequest(7, "u"))
        for mark in ({"mr": 7}, {"host": "github"}, {}):
            self.assertIn("依頼し直してください", review.matching_problems(result, mark)[0], mark)
        self.assertEqual(review.matching_problems(result, {"host": "github", "mr": 7}), [])
        self.assertTrue(review.matching_problems(result, {"host": "github", "mr": 8}))

    def test_a_reviewed_phase_is_not_confirmed_again_locally_or_from_chrome(self):
        fixture = self.ready()
        compare = self.host_compare()
        self.assertEqual(self.confirm_as(fixture).returncode, 0)
        before = read_json(self.mark_path())
        again = self.confirm_as(fixture, "--actor", "someone")
        self.assertEqual(again.returncode, 1)
        self.assertEqual(again.stderr.splitlines(), ["ccnavi: フェーズ 1 はレビュー済み"])
        self.assertEqual(read_json(self.mark_path()), before)
        self.commit_parent("reviewed")
        compare = {**compare, "head": git(self.parent_tree, "rev-parse", "HEAD").strip()}
        chrome = self.chrome_confirm(compare)
        self.assertEqual(chrome["problems"], ["ccnavi: フェーズ 1 はレビュー済み"])
        self.assertIsNone(chrome["changes"])

    def test_the_request_records_the_poster_when_known(self):
        self.family(plan=["design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")
        self.remote()
        body = write(os.path.join(self.root, "body.md"), "見てほしい\n")
        prepared = self.ccnavi(
            "--cwd", self.parent_tree, "--phase", "1", "--body-file", body, "review", "prepare"
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        posted = write(
            os.path.join(self.root, "posted.json"),
            json.dumps(
                {
                    "host": "gitlab",
                    "mr": {"number": 7, "url": "u/7"},
                    "url": "u/7#1",
                    "created_at": "t",
                    "author": "ccnavi-bot",
                }
            ),
        )
        done = self.ccnavi(
            "--cwd", self.parent_tree, "--phase", "1", "review", "requested", "--result", posted
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(read_json(self.mark_path("requested"))["poster"], "ccnavi-bot")


class DecideActorTest(ActorHarness):
    """decide のマーカーにも `actor` と `via`（confirm と同じ形）。"""

    def decide(self, fixture, *extra):
        preview = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--reviewed",
            "1",
            "--accept-unresolved",
            "--preview",
            "--json",
            "--result",
            fixture,
        )
        self.assertEqual(preview.returncode, 0, preview.stderr)
        digest = json.loads(preview.stdout)["digest"]
        return self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--reviewed",
            "1",
            "--accept-unresolved",
            "--yes",
            "{}",
            "--digest",
            digest,
            "--json",
            "--result",
            fixture,
            *extra,
        )

    def test_without_an_actor_the_decide_mark_is_as_before(self):
        fixture = self.ready()
        done = self.decide(fixture)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        mark = read_json(self.mark_path())
        self.assertEqual(sorted(mark), ["accepted", "at", "mr"])
        self.assertNotIn("actor", self.last_event())

    def test_an_actor_and_the_way_go_into_the_decide_mark(self):
        for via in ("board", "terminal"):
            with self.subTest(via=via):
                fixture = self.ready() if via == "board" else self.again()
                done = self.decide(fixture, "--actor=octo-reviewer", f"--via={via}")
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                mark = read_json(self.mark_path())
                self.assertEqual(list(mark), ["mr", "accepted", "actor", "via", "at"])
                self.assertEqual((mark["actor"], mark["via"]), ("octo-reviewer", via))
                # マーカーを置いた履歴（親の phase-mark）にもアカウントと経路が入る
                event = self.last_event("i0001")
                self.assertEqual(event["kind"], history.KIND_PHASE_MARK)
                self.assertEqual((event["actor"], event["via"]), ("octo-reviewer", via))

    def again(self):
        """マーカーを外して、同じフェーズをもう 1 度決められるようにする。"""
        os.remove(self.mark_path())
        fixture = os.path.join(self.root, "again.json")
        write(fixture, json.dumps(RESULT))
        return fixture

    def test_the_way_is_refused_alone_or_with_the_wrong_word(self):
        fixture = self.ready()
        for extra, word in (
            (["--via=board"], "--via は decide の --actor"),
            (["--actor=octo", "--via=chrome"], "--via に渡せるのは terminal か board"),
        ):
            with self.subTest(extra=extra):
                done = self.decide(fixture, *extra)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn(word, done.stderr)
                self.assertFalse(os.path.exists(self.mark_path()))
        preview = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--reviewed",
            "1",
            "--accept-unresolved",
            "--preview",
            "--json",
            "--result",
            fixture,
            "--actor=octo",
        )
        self.assertNotEqual(preview.returncode, 0)
        self.assertIn("--actor", preview.stderr)

    def test_board_is_refused_for_the_way_that_picks_one_by_one(self):
        """端末で 1 件ずつ選ぶ形（--yes 無し）に --via board は受けない。"""
        fixture = self.ready()
        done = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--reviewed",
            "1",
            "--accept-unresolved",
            "--result",
            fixture,
            "--actor=octo",
            "--via=board",
        )
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("--via board は --yes", done.stderr)
        self.assertFalse(os.path.exists(self.mark_path()))
