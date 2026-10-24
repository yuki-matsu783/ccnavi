"""レビュー済みのマーカーの `actor` と、依頼の後に動いたかの判定の受入テスト。

見るのは 4 つ。

1. 手元の `review confirm --actor <アカウント>` は、マーカーに `actor` と `via: cli` を書き、
   マーカーにアカウントを書く。`--actor` が無ければ（sh がアカウントを引けなかった）
   マーカーは前と同じ中身
2. `--actor` の形と、`review confirm` の外で渡されたときは断る
3. 依頼の後にユーザが見るものが動いたかは、手元と Chrome が同じ関数（`review.moved_since`）
   で決める。Chrome は compare API の一覧を渡し、打ち切られた（null）なら動いたと数える
4. Chrome のレビュー済みのマーカーは、
   手元の `--actor` つきのマーカーと経路（`via`）と時刻のほかは同じ
"""

from __future__ import annotations

import json
import os

from ccnavi.entry import lint, version
from ccnavi.infra import settings
from ccnavi.tickets import review, review_host
from tests.ticket.test_swap_points import STAMP, CoreHarness
from tests.ticket.test_ticket import git, read_json, write
from tests.ticketsample import child_text

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

    def test_an_actor_goes_into_the_mark(self):
        fixture = self.ready()
        done = self.confirm_as(fixture, "--actor", "octo-reviewer")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        mark = read_json(self.mark_path())
        self.assertEqual(list(mark), ["mr", "accepted", "actor", "via", "at"])
        self.assertEqual(
            {k: mark[k] for k in ("mr", "accepted", "actor", "via")},
            {"mr": 7, "accepted": [], "actor": "octo-reviewer", "via": "cli"},
        )

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
        # Windows では案内の中のパスが `/` 区切りで来るので、区切りを揃えてから根を除く。
        root = self.root.replace(os.sep, "/")
        same = [p.replace(os.sep, "/").replace(f"{root}/memfs/", "") for p in chrome["problems"]]
        said = [line.replace(os.sep, "/").replace(f"{root}/", "") for line in said]
        # コミットの一覧は手元の git で取れたときだけ（Chrome は出さない）。ほかは同じ文面
        listed = [i for i, line in enumerate(said) if line.startswith("依頼のあとのコミット")]
        self.assertEqual(len(listed), 1)
        self.assertIn("late code", said[listed[0] + 1])
        del said[listed[0] : listed[0] + 2]
        self.assertEqual([p.replace(f"{root}/", "") for p in same], said)
        self.assertIn("依頼の後に親の HEAD が動いている", chrome["problems"][0])
        self.assertIsNone(chrome["changes"])

    def test_the_explanation_by_kind_and_the_fixed_guidance(self):
        """止めたあとの説明だけを足す。判定は変えない。"""
        conf = self.conf()
        mark = {"head": "a" * 40}
        seen = review.moved_detail(conf, mark, "b" * 40, ["src/app.py"])
        self.assertEqual(seen.kind, review.KIND_SEEN)
        text = "\n".join(review.moved_explanation(seen))
        self.assertIn(
            "ユーザが見たのは依頼時の HEAD で、その後の変更はレビューに入っていない", text
        )
        self.assertIn("GitLab の「提案を適用」や Web IDE の編集もここに入る", text)
        self.assertIn(
            "一覧にエージェントが作っていないコミットがあれば、取り込んだことをユーザに", text
        )
        # 確認できない（読めない・祖先でない・compare の打ち切り）: 変更があると断定しない
        for unverified in (
            review.moved_detail(conf, mark, "b" * 40, None),
            review.moved_detail(conf, mark, "b" * 40, ["src/app.py"], ancestor=False),
            review.moved_detail(conf, {}, "b" * 40, []),
        ):
            self.assertEqual(unverified.kind, review.KIND_UNVERIFIED)
            text = "\n".join(review.moved_explanation(unverified))
            self.assertIn("依頼時と同じレビュー対象だと確認できない", text)
            self.assertNotIn("その後の変更はレビューに入っていない", text)
            self.assertIn("取り込んだことをユーザに", text)
        # 動いていなければ説明は無い
        self.assertEqual(
            review.moved_explanation(review.moved_detail(conf, mark, "a" * 40, [])), []
        )
        self.assertEqual(
            review.moved_explanation(
                review.moved_detail(conf, mark, "b" * 40, [".ccnavi/approved/x"])
            ),
            [],
        )

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
            # 確認できないときは、変更があると断定しない（一覧も出さない）
            said = "\n".join(chrome["problems"])
            self.assertIn("依頼時と同じレビュー対象だと確認できない", said)
            self.assertNotIn("その後の変更はレビューに入っていない", said)
            self.assertNotIn("依頼のあとのコミット", said)
            self.assertIn("取り込んだことをユーザに伝えてから依頼し直す", said)

    def test_only_the_places_moved_so_chrome_writes(self):
        self.ready()
        chrome = self.chrome_confirm(self.host_compare())
        self.assertEqual(chrome["problems"], [])
        self.assertIsNotNone(chrome["changes"])


class SameMarkTest(ActorHarness):
    def test_chrome_and_local_marks_differ_only_by_the_route_and_the_time(self):
        fixture = self.ready()
        chrome = self.chrome_confirm(self.host_compare(), actor={"account": "octo-reviewer"})
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
        return review_host.Review(state, f"r/{state}/{at}", at, author)

    def test_a_comment_or_a_pending_review_does_not_clear_a_change_request(self):
        cr = self.review("CHANGES_REQUESTED", "2026-09-29T01:00:00Z")
        for later in ("COMMENTED", "PENDING"):
            got = review_host.effective([cr, self.review(later, "2026-09-29T02:00:00Z")])
            self.assertEqual([r.state for r in got], ["CHANGES_REQUESTED"], later)
        pending = review_host.Review("PENDING", "r/p", "", "9001")
        self.assertEqual(
            [r.state for r in review_host.effective([cr, pending])], ["CHANGES_REQUESTED"]
        )
        for later, left in (("APPROVED", ["APPROVED"]), ("DISMISSED", [])):
            got = review_host.effective([cr, self.review(later, "2026-09-29T02:00:00Z")])
            self.assertEqual([r.state for r in got], left, later)
        self.assertEqual(
            review_host.effective([self.review("COMMENTED", "2026-09-29T00:00:00Z")]), []
        )

    def test_a_marker_thread_is_skipped_only_on_gitlab_by_the_poster(self):
        marker = review_host.Thread(id="t", body="<!-- ccnavi:request i0001:1 -->", author="bot")
        self.assertEqual(review_host._unresolved([marker], "github", "bot"), [marker])
        self.assertEqual(review_host._unresolved([marker], "gitlab", ""), [marker])
        self.assertEqual(review_host._unresolved([marker], "gitlab", "someone"), [marker])
        self.assertEqual(review_host._unresolved([marker], "gitlab", "bot"), [])

    def test_a_crit_push_thread_is_counted_even_from_the_poster(self):
        """crit push の行のスレッドは目印で始まらないので、依頼を投稿したアカウントからでも数える。

        ユーザが依頼者と同じアカウントで crit push しても、指摘はレビュー済みを止める。
        """
        crit = review_host.Thread(
            id="d1", body="ここは X ではなく Y では", author="bot", path="wip/eli5/phase-1.html"
        )
        for host in ("github", "gitlab"):
            self.assertEqual(review_host._unresolved([crit], host, "bot"), [crit], host)

    def test_a_request_record_without_host_or_mr_is_not_matched(self):
        result = review_host.Result(host="github", mr=review_host.MergeRequest(7, "u"))
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
        self.assertEqual(
            again.stderr.splitlines(),
            ["ccnavi: [CORE_CONFIRM_ALREADY_REVIEWED] フェーズ 1 はレビュー済み"],
        )
        self.assertEqual(read_json(self.mark_path()), before)
        self.commit_parent("reviewed")
        compare = {**compare, "head": git(self.parent_tree, "rev-parse", "HEAD").strip()}
        chrome = self.chrome_confirm(compare)
        self.assertEqual(
            chrome["problems"],
            ["ccnavi: [CORE_CONFIRM_ALREADY_REVIEWED] フェーズ 1 はレビュー済み"],
        )
        self.assertIsNone(chrome["changes"])

    def test_the_request_records_the_poster_when_known(self):
        done = self.request_posted()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(read_json(self.mark_path("requested"))["poster"], "ccnavi-bot")

    def doing_text(self):
        with open(os.path.join(self.approved, "doing", "i0001.md"), encoding="utf-8") as f:
            return f.read()

    def test_the_request_writes_mr_into_an_empty_field_only(self):
        # 承認済みチケットの `mr:` が空なら、依頼で MR の番号を書き足す
        def clear():
            self.set_doing_mr("")
            self.assertNotIn("mr: 1", self.doing_text())

        done = self.request_posted(before=clear)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("mr: 7\n", self.doing_text())
        self.assertIn("`mr:` に MR !7 を追記しました", done.stderr)
        # 欄の行のほかは変わっていない
        self.assertEqual(1, self.doing_text().count("mr: 7"))

    def test_the_mr_write_is_in_the_write_and_push_write_list(self):
        # write-and-push は書いたパスの一覧のパスだけをコミットする。`mr:` の書き足しも一覧に入る
        state = os.path.join(self.root, "logs", "state", "write-and-push", "demo")
        os.makedirs(state, exist_ok=True)
        listing = os.path.join(state, "writes.txt")
        done = self.request_posted(
            extra=("--record-writes", listing), before=lambda: self.set_doing_mr("")
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        with open(listing, encoding="utf-8") as f:
            listed = [os.path.normpath(line.strip()) for line in f if line.strip()]
        rel = os.path.join(".ccnavi", "approved", "doing", "i0001.md")
        self.assertTrue(any(p.endswith(rel) for p in listed), listed)

    def test_the_request_with_the_same_mr_does_nothing(self):
        def put():
            self.set_doing_mr("7")
            self.held = self.doing_text()

        done = self.request_posted(before=put)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.held, self.doing_text())
        self.assertNotIn("追記しました", done.stderr)
        self.assertNotIn("警告", done.stderr)

    def test_the_request_with_another_mr_warns_and_keeps_the_value(self):
        def put():
            self.set_doing_mr("9")
            self.held = self.doing_text()

        done = self.request_posted(before=put)
        # 依頼そのものは通る（マーカーは置けている）。値は上書きしない
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.held, self.doing_text())
        self.assertIn("警告", done.stderr)
        self.assertIn("別の値が既にある", done.stderr)
        self.assertIn("!7", done.stderr)

    def set_doing_mr(self, value):
        path = os.path.join(self.approved, "doing", "i0001.md")
        text = self.doing_text().replace("\nmr: 1\n", f"\nmr: {value}\n" if value else "\n", 1)
        write(path, text)
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "mr")

    def request_posted(self, before=None, extra=()):
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
        if before is not None:
            before()
        return self.ccnavi(
            *extra,
            "--cwd",
            self.parent_tree,
            "--phase",
            "1",
            "review",
            "requested",
            "--result",
            posted,
        )


class DecideActorTest(ActorHarness):
    """decide のマーカーにも `actor` と `via`（confirm と同じ形）。"""

    def decide(self, fixture, *extra):
        preview = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "user",
            "reviewed",
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
            "user",
            "reviewed",
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

    def test_an_actor_and_the_way_go_into_the_decide_mark(self):
        for via in ("board", "terminal"):
            with self.subTest(via=via):
                fixture = self.ready() if via == "board" else self.again()
                done = self.decide(fixture, "--actor=octo-reviewer", f"--via={via}")
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                mark = read_json(self.mark_path())
                self.assertEqual(list(mark), ["mr", "accepted", "actor", "via", "at"])
                self.assertEqual((mark["actor"], mark["via"]), ("octo-reviewer", via))

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
            "user",
            "reviewed",
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
        """ターミナルで 1 件ずつ選ぶ形（--yes 無し）に --via board は受けない。"""
        fixture = self.ready()
        done = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "user",
            "reviewed",
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
