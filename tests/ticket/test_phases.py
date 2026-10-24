"""フェーズ定義と計画の受入テスト。

見るのは 9 つ。

1. フェーズ定義の検証（識別子と表示名の一意、フィードバック対応は mr 固定、参照先の有無）
2. 全体計画の承認と、計画に合わない子の拒否（kind、無い番号）。範囲の上限の超過は拒まず見せる
3. 順序は承認で止まる（先行の番号が閉じてレビューが済むまで承認しない。先行のない番号は並行）
4. 成果物が無ければフェーズの最後の子を閉じられない
5. 延期したフェーズではレビューで止まらず、次の依頼に含まれる
6. フィードバック計画は最後のレビューの後に 1 回だけ、空でも証跡になる
7. 親はフィードバック計画が承認されるまで閉じられない
8. 残った指摘の切り出しの下書き
9. 親を早めに閉じる操作

範囲の上限（設計 wip/design/approve-carry.md 3・4）は ScopeLimitTest が見る。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from tests import can_case_sensitive, common_path, config_path, userform, write_and_push_flag
from tests.inproc import run_ccnavi
from tests.ticket.test_ticket import ROOT, git, read_json, write
from tests.ticketsample import PHASES, RULES, child_text, parent_text, record_merge


class PhaseHarness(unittest.TestCase):
    """親と子を動かす道具。テストは持たない（他のテストが継いで使う）。"""

    # 親の `phases:` にコピーする元の phases.yml の本文。phases.yml を差し替えるテストは上書きする。
    phases_text = PHASES
    # sh（write-and-push）を通らずに状態の操作を直に起こすテストは、write-and-push
    # の印を付ける（`tests/write_and_push_flag.py`）。
    # 印が無い呼び出しを断る検査は False にする。
    mark_write_and_push = True

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-phase-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".claude"))
        git(self.root, "init", "--quiet", "-b", "main")
        write(os.path.join(self.root, "src", "keep.py"), "print(1)\n")
        write(os.path.join(self.root, ".gitignore"), ".claude/\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "--quiet", "-m", "init")
        # 共通レイヤーは `--root` の下の既定の置き場に置く。`--rules` / `--phases` は診断
        # （`--lint` / `--test` / `--explain`）でだけ有効なので、hook の判定と `--reviewed`
        # には渡せない。差し替えたいテストはこのファイルに書き直す。
        self.rules = write(common_path(self.root, "rules"), json.dumps(RULES))
        self.phases = write(config_path(self.root, "phases"), PHASES)
        self.state = os.path.join(self.root, "logs", "state")
        self.parent_tree = self.worktree("i0001", "main")
        # 承認済みチケットとマーカーは親のツリーに置かれ、親のブランチに乗る。
        self.approved = os.path.join(self.parent_tree, ".ccnavi", "approved")

    # ---- 道具

    def worktree(self, name, base):
        path = os.path.join(self.root, ".claude", "worktrees", name)
        git(self.root, "worktree", "add", "--quiet", path, "-b", name, base)
        return path

    def ccnavi(self, *args, stdin="", env=None):
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        # 学びの蓄積の既定は enable。学びを扱わないテストは切る（扱うテストは env で渡す）。
        environment.update(env or {})
        if self.mark_write_and_push:
            args = write_and_push_flag.as_write_and_push(
                self.root, self.state, self.parent_tree, args
            )
        return run_ccnavi(
            userform.place(
                [
                    "--root",
                    self.root,
                    "--guard-core-files",
                    "disable",
                    "--restore-if-deny",
                    "disable",
                    "--guard-ticket-approval",
                    "disable",
                ],
                args,
            ),
            input=stdin,
            cwd=ROOT,
            env=environment,
        )

    def hook(self, event, tool, cwd, mode="enable", agent_id="", permission_mode="", **tool_input):
        payload = {
            "hook_event_name": event,
            "tool_name": tool,
            "cwd": cwd,
            "session_id": "s1",
            "tool_input": tool_input,
        }
        if agent_id:
            payload["agent_id"] = agent_id
        # Claude Code 側の権限モード。どのルールも言及しない呼び出しの結末がこれで変わる
        # （judge.undeclared_verdict）ので、そこを見るテストだけが渡す。
        if permission_mode:
            payload["permission_mode"] = permission_mode
        return self.ccnavi("--mode", mode, stdin=json.dumps(payload))

    def reason(self, result):
        if not result.stdout.strip():
            return ""
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        return out.get("permissionDecisionReason") or out.get("additionalContext") or ""

    def propose(self, name, text, tree=None):
        """提案を書く。`tree` が無ければ親 i0001 のワークツリー。別の親は同名のツリーに書く。"""
        return write(
            os.path.join(tree or self.parent_tree, "wip", "proposals", "todo", name + ".md"), text
        )

    def approve(self):
        """承認して、承認済みチケットを親のブランチに乗せる。

        承認済みチケットは親のツリーに置かれるので、コミットするまでワークツリーは汚れたまま。
        本番で `ccnavi-user-agree.sh` がやることを、テストでも同じ順でたどる。
        """
        result = self.ccnavi("user", "agree", stdin="y\n")
        if os.path.isdir(self.approved):
            git(self.parent_tree, "add", "-A")
            git(self.parent_tree, "commit", "--quiet", "--allow-empty", "-m", "approve")
        return result

    def commit_parent(self, message="tickets"):
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "--allow-empty", "-m", message)

    def start_parent(self, name="i0001"):
        """親を着手する（済んでいれば何もしない）。

        子の着手は親が着手済みであることを前提にする。親を飛ばしたまま
        子を進められたころの手順をそのまま残すと、最初の子の着手で止まる。
        """
        started = self.ccnavi("ticket", "start", name)
        if started.returncode != 0:
            self.assertIn("着手済み", started.stderr, started.stdout + started.stderr)

    def run_child(self, name, files=()):
        """子のワークツリーを作って着手し、ファイルを置いてコミットし、閉じる。"""
        self.start_parent()
        tree = self.worktree(name, "i0001")
        started = self.ccnavi("ticket", "start", name)
        self.assertEqual(started.returncode, 0, started.stderr)
        for rel, text in files:
            write(os.path.join(tree, *rel.split("/")), text)
        git(tree, "add", "-A")
        if files:
            git(tree, "commit", "--quiet", "-m", name)
        return tree

    def close_child(self, name):
        return self.ccnavi("ticket", "finish", name)

    def merge(self, child):
        git(self.parent_tree, "merge", "--quiet", "--no-edit", child)
        record_merge(self.parent_tree, child)

    def remote(self):
        bare = os.path.join(self.root, "origin.git")
        git(self.root, "init", "--quiet", "--bare", bare)
        git(self.root, "remote", "add", "origin", bare)
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        fixture = os.path.join(self.root, "fixture.json")
        write(
            fixture,
            json.dumps({"host": "fixture", "mr": {"number": 7, "url": "u/7", "draft": True}}),
        )
        return fixture

    def request(self, fixture, phase):
        body = write(os.path.join(self.root, "body.md"), "見てほしい\n")
        prepared = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--phase",
            str(phase),
            "--body-file",
            body,
            "review",
            "prepare",
        )
        if prepared.returncode != 0:
            return prepared
        body_path = prepared.stdout.splitlines()[0]
        with open(body_path, encoding="utf-8") as f:
            self.last_request_body = f.read()
        data = read_json(fixture)
        result = write(
            os.path.join(self.root, "posted.json"),
            json.dumps(
                {
                    "host": "fixture",
                    "mr": data["mr"],
                    "url": "u/7#1",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ),
        )
        return self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--phase",
            str(phase),
            "review",
            "requested",
            "--result",
            result,
        )

    def confirm(self, fixture, phase):
        return self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--phase",
            str(phase),
            "review",
            "confirm",
            "--result",
            fixture,
        )

    def board_phase(self, number, parent="i0001"):
        """ボードの JSON のフェーズ 1 つを (gate_closed, review_waiting, マーカーの種類) で。"""
        result = self.ccnavi("--explain", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        board = json.loads(result.stdout)
        owner = next(p for p in board["parents"] if p["ticket"] == parent)
        ph = next(p for p in owner["phases"] if p["number"] == number)
        return ph["gate_closed"], ph["review_waiting"], sorted(ph["marks"])

    def family(self, plan=("research", "design"), feedback=None):
        """親を提案して承認する。plan の 1 番目の子もまとめて承認する。"""
        self.propose("i0001", parent_text("i0001", list(plan), feedback, copy=self.phases_text))
        self.commit_parent()
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result


class ApproveOnlyTest(PhaseHarness):
    """`--agree <識別子>...` で承認の対象を絞っても、絞らないときに外れるものは通らない。"""

    def test_child_cannot_be_approved_without_the_parents_pending_revision(self):
        self.family(plan=("acceptance", "implement"))
        # 親を改版してフェーズ 1 を設計にする。子はまだ旧計画（受入テスト作成）の範囲で出す
        self.propose("i0001", parent_text("i0001", ["design", "acceptance", "implement"]))
        self.propose("i0001-01-02", child_text("i0001-01-02", "i0001", 1, ("tests/x*",)))
        self.commit_parent()
        # 絞らないときは、改版後の計画で検証される。定義の超過は承認を拒まず、承認画面に
        # 「編集対象としているが」として出る（設計 approve-carry 3.2）。n で何も適用しない
        whole = self.ccnavi("user", "agree", stdin="n\n")
        self.assertIn("編集対象としているが", whole.stdout)
        self.assertIn("超えている", whole.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001-01-02.md")))
        # 改版を外して子だけ並べても、旧計画で通してはいけない
        only = self.ccnavi("user", "agree", "i0001-01-02", stdin="y\n")
        self.assertEqual(only.returncode, 1, only.stdout + only.stderr)
        self.assertIn("改版", only.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001-01-02.md")))

    def test_child_of_a_rejected_parent_is_not_approved(self):
        # 親が外れたら（置き場に無いプロジェクト）、その子も親が承認されていないので外れる。
        # 子自身は正しいので、外れた親を対応表に残すと子だけ承認済みチケットになる
        parent = parent_text("i0001", ["design"]).replace("plan:", "project: nope\nplan:", 1)
        self.propose("i0001", parent)
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("wip/design/*",)))
        self.commit_parent()
        result = self.approve()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("i0001 は承認の対象にしない", result.stderr)
        self.assertIn("親 i0001 が承認されていない", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001.md")))
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001-01-01.md")))


class PhaseTest(PhaseHarness):
    # ---- 1. フェーズ定義

    def test_phase_types_must_be_unique_and_well_formed(self):
        from ccnavi.tickets import phasetypes

        _, problems = phasetypes.parse(
            "version: 1\nphases:\n  a: {title: 同じ, kind: work}\n  b: {title: 同じ, kind: work}\n"
        )
        self.assertTrue(any("表示名" in p.detail for p in problems), problems)
        # フィードバック対応はユーザが見ない経路を作らない。
        _, problems = phasetypes.parse(
            "version: 1\nphases:\n  fb: {title: 対応, kind: feedback, review: none}\n"
        )
        self.assertTrue(any("review: none" in p.detail for p in problems), problems)
        # `chat` は廃止した値。読めない。
        _, problems = phasetypes.parse(
            "version: 1\nphases:\n  fb: {title: 対応, kind: feedback, review: chat}\n"
        )
        self.assertTrue(any("`review` は" in p.detail for p in problems), problems)
        # 順序の欄（overlap など）は読まない。指す先が無くても問題にしない。
        types, problems = phasetypes.parse(
            "version: 1\nphases:\n  a: {title: A, kind: work, overlap: [nope]}\n"
        )
        self.assertIsNotNone(types, problems)
        self.assertEqual(problems, [])
        types, problems = phasetypes.parse(PHASES)
        self.assertEqual(problems, [])
        self.assertEqual(
            set(types),
            {
                "research",
                "design",
                "acceptance",
                "implement",
                "implement-feedback",
                "chores",
                "chores-feedback",
            },
        )

    def test_lint_reports_a_broken_phases_file(self):
        write(
            self.phases,
            "version: 1\nphases:\n  a: {title: X, kind: work}\n  b: {title: X, kind: work}\n",
        )
        result = self.ccnavi("--lint", "--mode", "enable")
        self.assertIn("表示名", result.stdout)
        self.assertNotEqual(result.returncode, 0)

    # ---- 2. 全体計画の承認と、計画に合わない子

    def test_plan_is_approved_and_copied(self):
        self.family(plan=["research", ("design", "defer"), "acceptance", "implement"])
        copy = os.path.join(self.approved, "doing", "i0001.md")
        with open(copy, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("plan:", text)
        self.assertIn("research", text)
        self.assertIn("defer", text)
        result = self.ccnavi("--explain")
        self.assertIn("局面: 作業中（1（調査））", result.stdout)
        self.assertIn("フェーズ 2（設計）: 未計画", result.stdout)
        self.assertIn("レビューは 3 と一緒に", result.stdout)

    def test_plan_that_breaks_the_rules_is_refused(self):
        cases = {
            "kind": ["implement-feedback"],
            "defer-none": [("research", "defer"), "design"],
            "defer-last": ["design", ("implement", "defer")],
            "unknown": ["nope"],
        }
        for name, plan in cases.items():
            with self.subTest(name=name):
                shutil.rmtree(self.approved, ignore_errors=True)
                self.propose("i0001", parent_text("i0001", plan))
                result = self.approve()
                self.assertNotEqual(result.returncode, 0, name)
                self.assertFalse(
                    os.path.exists(os.path.join(self.approved, "doing", "i0001.md")), name
                )

    def test_child_must_fit_the_phase_type(self):
        """定義の範囲の超過は承認を拒まず、承認画面で見せる。計画に無い番号は今までどおり拒む。

        超過は判定が切り詰めるので、承認で止める理由が無い（設計 approve-carry 3.1）。
        チケットの形の誤り（計画に無い番号）は判定で補えないので、承認で止める。
        """
        self.family(plan=["research", "design"])
        # 定義の範囲を超える子。承認でき、承認済みチケットが置かれる。
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["src/a/*"]))
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "doing", "i0001-01-01.md")))
        self.assertIn("編集対象としているが", approved.stdout)
        self.assertIn("超えている", approved.stdout)
        self.assertIn("調査", approved.stdout)
        self.assertNotIn("i0001-01-01 は承認の対象にしない", approved.stderr)
        # 計画に無い番号。承認した子は todo/ から動いているので、消すものは無い。
        self.propose("i0001-05-05", child_text("i0001-05-05", "i0001", 5, ["wip/research/*"]))
        refused = self.approve()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("計画にありません", refused.stderr)

    def test_phase_scope_ignores_letter_case_on_every_machine(self):
        """定義の範囲の上限は、子チケットの範囲と同じく大文字小文字を区別しない。

        機械ごとに変えると、同じ提案が Linux では「定義の上限を超えている」で
        承認を拒まれ、Windows では通る。範囲はユーザが宣言する意図なので、書かれたパスの
        意味で読む（子 ⊆ 定義 ⊆ 親 の 3 つを 1 つの規則で揃える）。
        """
        self.family(plan=["research", "design"])
        # 定義は `wip/research/*`。子は表記だけ違う `WIP/Research/*` を宣言する。
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["WIP/Research/*"]))
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "doing", "i0001-01-01.md")))
        self.assertNotIn("超えている", result.stderr)

    # ---- 3. 順序は承認で止まる

    def test_next_phase_waits_for_the_previous_one(self):
        self.family(plan=["research", "design"])
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], review=False)
        )
        self.propose("i0001-02-02", child_text("i0001-02-02", "i0001", 2, ["wip/design/*"]))
        self.commit_parent()
        result = self.approve()
        # 1 番目は通り、2 番目は「1 が閉じるまで」で外れる。
        self.assertTrue(os.path.exists(os.path.join(self.approved, "doing", "i0001-01-01.md")))
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001-02-02.md")))
        self.assertIn("閉じるまで承認しない", result.stderr)
        self.assertIn("子がまだ無い", result.stderr) if "子がまだ無い" in result.stderr else None

        # 1 番目を閉じる（成果物を置く）。
        self.run_child("i0001-01-01", [("wip/research/summary.md", "まとめ\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        # レビュー不要の定義なので、マーカーは skipped。2 番目が承認される。
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "doing", "i0001-02-02.md")))

    def test_items_that_do_not_wait_on_each_other_are_planned_together(self):
        """`after` で先行しないステップは並行する（前の版の `overlap` の代わり）。"""
        plan = [
            "acceptance",
            {"type": "implement", "after": []},
            {"type": "design", "after": [1, 2]},
        ]
        self.family(plan=plan)
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["tests/a/*"]))
        self.propose("i0001-02-02", child_text("i0001-02-02", "i0001", 2, ["src/a/*"]))
        self.commit_parent()
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "doing", "i0001-02-02.md")))

    def test_requires_is_no_longer_checked(self):
        """定義の `requires` は読まない。一緒に置く定義の組は `when` の案内と承認画面で見る。"""
        write(
            self.phases,
            PHASES.replace(
                "    title: 実装とテスト\n", "    title: 実装とテスト\n    requires: [acceptance]\n"
            ),
        )
        self.propose("i0001", parent_text("i0001", ["implement"]))
        self.commit_parent()
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_batch_does_not_pass_a_later_child_over_one_that_reopens_an_earlier_phase(self):
        """前のフェーズに足す子と次のフェーズの子を一緒に承認しても、次の子は通さない（issue #31）。

        1 本ずつ承認すれば、前の子の承認でフェーズが開き直り、次の子は外れる。まとめて承認しても
        同じ答えにする。
        識別子の順（-02 が -03 より先）で検査すると、開き直す前の状態で次の子が通っていた。
        """
        self.family(plan=["research", "design"])
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], review=False)
        )
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/research/summary.md", "まとめ\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        # フェーズ 1 は閉じてレビュー不要。次のフェーズの子と、フェーズ 1 に足す子を一緒に出す。
        self.propose("i0001-02-02", child_text("i0001-02-02", "i0001", 2, ["wip/design/*"]))
        self.propose(
            "i0001-01-03", child_text("i0001-01-03", "i0001", 1, ["wip/research/*"], review=False)
        )
        self.commit_parent("propose 02 03")
        result = self.approve()
        self.assertTrue(
            os.path.exists(os.path.join(self.approved, "doing", "i0001-01-03.md")), result.stderr
        )
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001-02-02.md")))
        self.assertIn("同じ承認で i0001-01-03 を追加するので再び開く", result.stderr)

    def test_a_new_parent_and_a_later_phase_child_together_still_keep_the_order(self):
        """承認済みチケットの無い親と、2 番目のフェーズの子を一緒に承認しても、子は通さない。

        1 本ずつなら、親を承認したあと子は「1 が閉じるまで」で外れる。一緒に出すと親の計画が
        ディスクに無く、フェーズが 1 つも並ばないので、順序の検査が何も見ずに通っていた。
        """
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.propose("i0001-02-01", child_text("i0001-02-01", "i0001", 2, ["wip/design/*"]))
        self.commit_parent()
        result = self.approve()
        self.assertTrue(
            os.path.exists(os.path.join(self.approved, "doing", "i0001.md")), result.stderr
        )
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001-02-01.md")))
        self.assertIn("1（調査） が閉じるまで承認しない（子がまだ無い）", result.stderr)

    def test_a_new_parent_with_children_in_two_phases_passes_only_the_first(self):
        """親・1 番目の子・2 番目の子を一緒に承認すると、1 番目の子までが通り、2 番目は外れる。"""
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], review=False)
        )
        self.propose("i0001-02-02", child_text("i0001-02-02", "i0001", 2, ["wip/design/*"]))
        self.commit_parent()
        result = self.approve()
        # 一部だけ置いて失敗した。何も置かずに失敗したとき（1）と分けるので 4。
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertTrue(
            os.path.exists(os.path.join(self.approved, "doing", "i0001.md")), result.stderr
        )
        self.assertTrue(
            os.path.exists(os.path.join(self.approved, "doing", "i0001-01-01.md")), result.stderr
        )
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001-02-02.md")))
        self.assertIn("同じ承認で i0001-01-01 を追加するが、まだ閉じていない", result.stderr)

    # ---- 4. 成果物

    def test_last_child_cannot_close_without_deliverables(self):
        self.family(plan=["research", "design"])
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], review=False)
        )
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01")
        refused = self.close_child("i0001-01-01")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("成果物が無い", refused.stderr)
        self.assertIn("wip/research/summary.md", refused.stderr)
        tree = os.path.join(self.root, ".claude", "worktrees", "i0001-01-01")
        write(os.path.join(tree, "wip", "research", "summary.md"), "x\n")
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "summary")
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)

    def test_deliverable_tracked_but_deleted_from_the_worktree_is_missing(self):
        """追跡済みでもワークツリーから消えていれば、成果物は無いものとして閉じない。

        `git ls-files` はインデックスを見るので、削除をステージするまでは名前が返る。
        """
        self.family(plan=["research", "design"])
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], review=False)
        )
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        tree = self.run_child("i0001-01-01", [("wip/research/summary.md", "x\n")])
        os.remove(os.path.join(tree, "wip", "research", "summary.md"))
        refused = self.close_child("i0001-01-01")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("成果物が無い", refused.stderr)

    # ---- 5. 延期

    def test_deferred_phase_does_not_close_the_gate_and_is_covered_later(self):
        self.family(plan=[("design", "defer"), "implement", "acceptance"])
        # implement は acceptance を要るが、acceptance が後ろにあっても計画としては足りる。
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        said = self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        self.assertIn("一緒に見る", self.reason(said))
        spawn = self.hook("PreToolUse", "Agent", self.parent_tree, description="次")
        self.assertNotIn("DENY_PHASE_REVIEW", self.reason(spawn))
        # 延期したフェーズには依頼できない。
        self.merge("i0001-01-01")
        fixture = self.remote()
        refused = self.request(fixture, 1)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("一緒に見る", refused.stderr)
        # 2 番目を閉じて依頼すると、1 番目も含まれる。
        self.propose("i0001-02-02", child_text("i0001-02-02", "i0001", 2, ["src/a/*"]))
        self.commit_parent("propose 02")
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-02-02", [("src/a/x.py", "x\n")])
        self.assertEqual(self.close_child("i0001-02-02").returncode, 0)
        self.commit_parent("close 02")
        self.merge("i0001-02-02")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        spawn = self.hook("PreToolUse", "Agent", self.parent_tree, description="次")
        self.assertIn("DENY_PHASE_REVIEW", self.reason(spawn))
        ok = self.request(fixture, 2)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("このレビューが含むフェーズ", self.last_request_body)
        self.assertIn("1（設計）", self.last_request_body)
        self.assertIn("2（実装とテスト）", self.last_request_body)

    # ---- 5.5 子の経路の欄

    def test_finish_records_where_it_put_the_child_and_confirm_records_the_route(self):
        """finish は置き場の名前を `finished_to` に、confirm は動かした経路を `settled_by` に書く。

        ユーザが手で動かした子は `settled_by` を持たないので、ccnavi が動かしたものと区別できる。
        """
        self.family(plan=["design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        waiting = os.path.join(self.parent_tree, "wip", "proposals", "review", "i0001-01-01.md")
        self.assertTrue(os.path.exists(waiting), "finish したレビュー待ちの子が無い")
        with open(waiting, encoding="utf-8") as f:
            text = f.read()
        self.assertIn('finished_to: "review"', text)
        self.assertNotIn("settled_by", text)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        self.assertEqual(self.confirm(fixture, 1).returncode, 0)
        with open(os.path.join(self.approved, "done", "i0001-01-01.md"), encoding="utf-8") as f:
            done = f.read()
        self.assertIn('finished_to: "review"', done)
        self.assertIn('settled_by: "confirm"', done)

    # ---- 6. フィードバック計画

    def test_a_failed_copy_does_not_clear_the_marks_of_a_reviewed_phase(self):
        """置けなかったときに、済んでいるレビューを巻き戻さない。

        承認済みチケットを置くのと、そのフェーズのマーカーを消すのは、置けたときだけ両方起きる。
        置けなかったのにマーカーだけ消えると、子は 1 枚も増えていないのにレビュー準備中へ戻り、
        サブエージェントの起動が止まる（DENY_PHASE_REVIEW）。ユーザは何が起きたか分からない。
        """
        self.family(plan=["design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        self.assertEqual(self.confirm(fixture, 1).returncode, 0)
        self.assertEqual(self.board_phase(1), (False, False, ["requested", "reviewed"]))
        # 同じフェーズに子をもう 1 枚足すが、置き場に同じ名前のディレクトリを作って書けなくする。
        self.propose("i0001-01-02", child_text("i0001-01-02", "i0001", 1, ["wip/design/*"]))
        self.commit_parent("propose 02")
        os.makedirs(os.path.join(self.approved, "doing", "i0001-01-02.md"))
        refused = self.approve()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("保存できませんでした", refused.stderr)
        # 子は増えていない。だからマーカーも消えていない。
        self.assertEqual(self.board_phase(1), (False, False, ["requested", "reviewed"]))

    def test_feedback_plan_comes_after_the_last_review_and_only_once(self):
        self.family(plan=["design"])
        # 早すぎる改版は拒む。
        self.propose("i0001", parent_text("i0001", ["design"], feedback=[]))
        early = self.approve()
        self.assertNotEqual(early.returncode, 0)
        self.assertIn("全部閉じてレビューが済んでから", early.stderr)
        self.propose("i0001", parent_text("i0001", ["design"]))

        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        # ボードの JSON は「依頼済みで止まったまま」を review_waiting で言う。レビューが
        # 済んで止まらなくなれば false に戻り、依頼のマーカーは残る。
        self.assertEqual(self.board_phase(1), (True, True, ["requested"]))
        self.assertEqual(self.confirm(fixture, 1).returncode, 0)
        self.assertEqual(self.board_phase(1), (False, False, ["requested", "reviewed"]))
        # 親はまだ閉じられない。
        self.start_parent()
        refused = self.close_child("i0001")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("フィードバック計画がまだ", refused.stderr)
        # 空のフィードバック計画を改版で出す。改版は同じ識別子の提案を todo/ に書く（承認済み
        # チケットは doing/ に在ってエージェントは書けない）。証跡が残る。
        self.propose("i0001", parent_text("i0001", ["design"], feedback=[]))
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.assertIn("対応なし", approved.stdout)
        with open(os.path.join(self.approved, "doing", "i0001.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("feedback: []", text)
        # 改版の時刻はチケットに書かない。
        self.assertNotIn("feedback_at", text)
        # 改版の提案は承認で todo/ から消える。
        self.assertFalse(
            os.path.exists(os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001.md"))
        )
        # 2 度目は拒む。
        self.propose("i0001", parent_text("i0001", ["design"], feedback=["implement-feedback"]))
        again = self.approve()
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("1 回だけ", again.stderr)
        os.remove(os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001.md"))
        # 閉じられる。
        self.assertEqual(self.close_child("i0001").returncode, 0)

    def test_feedback_work_phase_runs_and_decide_sends_the_rest_to_an_issue(self):
        self.family(plan=["design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        # 差し戻し。未解決の指摘が付いて check は通らない。
        data = read_json(fixture)
        data["threads"] = [{"id": "t0", "resolved": False, "url": "u/7#t0", "body": "直して"}]
        write(fixture, json.dumps(data))
        self.assertNotEqual(self.confirm(fixture, 1).returncode, 0)
        spawn = self.hook("PreToolUse", "Agent", self.parent_tree, description="次")
        self.assertIn("DENY_PHASE_REVIEW", self.reason(spawn))
        # フィードバック計画の承認ではレビューの状態を変えない。レビューが済む前は拒む。
        self.start_parent()
        self.propose("i0001", parent_text("i0001", ["design"], feedback=["implement-feedback"]))
        early = self.approve()
        self.assertNotEqual(early.returncode, 0)
        self.assertIn("レビューが済んでから", early.stderr)
        # 最後のレビューでも、残った指摘を issue に回せる（フィードバック計画の有無に関わらない）。
        # 回す先を選んだ decide がレビュー済みにする。
        last = self.ccnavi(
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
        self.assertEqual(last.returncode, 0, last.stderr)
        last_preview = json.loads(last.stdout)
        self.assertTrue(last_preview["can_issue"])
        settled = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "user",
            "reviewed",
            "1",
            "--accept-unresolved",
            "--yes",
            json.dumps({"u/7#t0": "issue"}),
            "--digest",
            last_preview["digest"],
            "--json",
            "--result",
            fixture,
        )
        self.assertEqual(settled.returncode, 0, settled.stdout + settled.stderr)
        self.assertTrue(json.loads(settled.stdout)["reviewed"])
        reviewed_mark = os.path.join(self.approved, "phases", "i0001", "1.reviewed")
        with open(reviewed_mark, "rb") as f:
            reviewed_before = f.read()
        planned = self.approve()
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
        self.assertNotIn("済ませた扱い", planned.stdout)
        with open(reviewed_mark, "rb") as f:
            self.assertEqual(f.read(), reviewed_before)
        spawn = self.hook("PreToolUse", "Agent", self.parent_tree, description="次")
        self.assertNotIn("DENY_PHASE_REVIEW", self.reason(spawn))
        result = self.ccnavi("--explain")
        self.assertIn("フェーズ 2（実装フィードバック対応）", result.stdout)
        self.assertIn("局面: フィードバック対応中", result.stdout)
        # 2 番目の子（範囲は親の範囲そのまま）。
        self.propose("i0001-02-02", child_text("i0001-02-02", "i0001", 2, ["src/*"]))
        self.commit_parent("propose 02")
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-02-02", [("src/fix.py", "x\n")])
        self.assertEqual(self.close_child("i0001-02-02").returncode, 0)
        self.commit_parent("close 02")
        self.merge("i0001-02-02")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        self.assertEqual(self.request(fixture, 2).returncode, 0)
        # 前の指摘が残ったままなので、フィードバック作業フェーズの check も通らない。
        refused = self.confirm(fixture, 2)
        self.assertNotEqual(refused.returncode, 0)
        data = read_json(fixture)
        data["threads"].append({"id": "t1", "resolved": False, "url": "u/7#t1", "body": "まだ"})
        write(fixture, json.dumps(data))
        # フィードバック計画が承認済みなので、ユーザは残りを issue に回せる。
        shown = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "user",
            "reviewed",
            "2",
            "--accept-unresolved",
            "--preview",
            "--json",
            "--result",
            fixture,
        )
        self.assertEqual(shown.returncode, 0, shown.stderr)
        preview = json.loads(shown.stdout)
        self.assertTrue(preview["can_issue"])
        choices = json.dumps({"u/7#t0": "keep", "u/7#t1": "issue"})
        self.assertNotEqual(self.confirm(fixture, 2).returncode, 0)
        done = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "user",
            "reviewed",
            "2",
            "--accept-unresolved",
            "--yes",
            choices,
            "--digest",
            preview["digest"],
            "--json",
            "--result",
            fixture,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        answer = json.loads(done.stdout)
        self.assertTrue(answer["reviewed"])
        with open(answer["issue_draft"], encoding="utf-8") as f:
            text = f.read()
        self.assertTrue(text.startswith("レビューで残った指摘（i0001 のフェーズ 2）\n\n"))
        self.assertIn("u/7#t1", text)
        self.assertNotIn("u/7#t0", text)
        # レビュー済みのフェーズに confirm を重ねない。重ねるとマーカーと履歴が書き直される
        again = self.confirm(fixture, 2)
        self.assertEqual(again.returncode, 1)
        self.assertIn("フェーズ 2 はレビュー済み", again.stderr)

    # ---- 7. Draft を外す（ready）と、ユーザが早めに閉じる（close-early）

    def ready(self, fixture):
        return self.ccnavi("--cwd", self.parent_tree, "review", "ready", "--result", fixture)

    def close_early(self, fixture, reason="ここまでで十分", answer="y"):
        return self.ccnavi(
            "--cwd",
            self.parent_tree,
            "user",
            "close-early",
            "--reason",
            reason,
            "--result",
            fixture,
            stdin=answer + "\n",
        )

    def test_ready_needs_the_parent_to_be_closable(self):
        """Draft を外せるのは、親を閉じられる状態と同じ。閉じたあとでも打てる。"""
        self.family(plan=["design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        fixture = self.remote()
        # 子が開いている間は外せない。
        refused = self.ready(fixture)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("Draft を解除できません", refused.stderr)
        self.assertIn("close-early", refused.stderr)
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        requested = self.request(fixture, 1)
        self.assertEqual(requested.returncode, 0, requested.stderr)
        self.assertEqual(self.confirm(fixture, 1).returncode, 0)
        # フィードバック計画が無い間も外せない。
        refused = self.ready(fixture)
        self.assertIn("フィードバック計画", refused.stderr)
        self.start_parent()
        self.propose("i0001", parent_text("i0001", ["design"], feedback=[]))
        self.assertEqual(self.approve().returncode, 0)
        # 閉じられる状態になった。wip/ が追跡されていても、それは ready が消すので止めない。
        refused = self.ready(fixture)
        self.assertNotEqual(refused.returncode, 0)
        self.assertNotIn("`wip/` に追跡されているファイル", refused.stderr)
        tracked = git(self.parent_tree, "ls-files", "--", "wip").split()
        self.assertTrue(tracked)
        # 親を閉じる。案内は「push して ready」。wip/ を消す手順（rm -r wip）は案内しない。
        closed = self.ccnavi("ticket", "finish", "i0001")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertNotIn("rm -r wip", closed.stdout)
        self.assertIn("ready", closed.stdout)
        self.assertIn("`wip/` の追跡済みのファイルを削除して", closed.stdout)
        self.assertIn("ready --parent i0001", closed.stdout)
        self.assertIn("合意した範囲に入る", closed.stdout)
        self.assertIn("確認を取り直さずに進める", closed.stdout)
        self.assertIn("squash", closed.stdout)
        # 閉じた記録は進め方によらず置く。
        record = read_json(os.path.join(self.approved, "phases", "i0001", "closed.json"))
        self.assertEqual(record["reviews"], {"1": "mr"})
        self.commit_parent("状態の移動")
        # push していなければまだ外せない。
        refused = self.ready(fixture)
        self.assertIn("push されていない", refused.stderr)
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        # wip/ に未追跡のファイルがあれば、消さずに名指しして止める（履歴から戻せないため）。
        write(os.path.join(self.parent_tree, "wip", "memo.txt"), "メモ\n")
        refused = self.ready(fixture)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("未追跡のファイルが 1 件", refused.stderr)
        self.assertIn("wip/memo.txt", refused.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.parent_tree, "wip", "memo.txt")))
        os.remove(os.path.join(self.parent_tree, "wip", "memo.txt"))
        # push 済み。ready が通り、マーカーと note の下書きができる。
        # wip/ の追跡済みのファイルは消える。
        head = git(self.parent_tree, "rev-parse", "HEAD").strip()
        # write-and-push と同じく書いたパスを集めて打つ。wip/ の削除は write-and-push
        # がコミットしてよいもの
        listed = os.path.join(self.root, "logs", "state", "write-and-push", "ready.writes")
        passed = self.ccnavi(
            "--record-writes",
            listed,
            "--record-tree",
            self.parent_tree,
            "--cwd",
            self.parent_tree,
            "review",
            "ready",
            "--result",
            fixture,
        )
        self.assertEqual(passed.returncode, 0, passed.stderr)
        with open(listed, encoding="utf-8") as f:
            written = f.read().splitlines()
        for rel in tracked:
            self.assertIn(rel, written)
        lines = passed.stdout.splitlines()
        with open(lines[0], encoding="utf-8") as f:
            note = f.read()
        self.assertIn("ccnavi:ready", note)
        self.assertIn("squash", note)
        self.assertIn(f"wip-from {head}", lines)
        self.assertEqual(sorted(x[len("wip ") :] for x in lines if x.startswith("wip ")), tracked)
        for rel in tracked:
            self.assertFalse(os.path.exists(os.path.join(self.parent_tree, *rel.split("/"))))
        self.assertFalse(os.path.exists(os.path.join(self.parent_tree, "wip")))
        # マーカーは閉じたチケットと一緒に手元の退避（logs/archive/）へ移る
        archived = os.path.join(self.root, "logs", "archive", "self")
        mark = read_json(os.path.join(archived, "phases", "i0001", "ready.json"))
        self.assertEqual(mark["mr"], 7)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "done", "i0001.md")))
        # 同じ親にもう 1 度打っても通る（sh が外し損ねたときの打ち直し。MR がまだ Draft のとき）。
        again = self.ready(fixture)
        self.assertEqual(again.returncode, 0, again.stderr)

    def ready_recorded(self, fixture):
        """write-and-push と同じ形（書いたパスを集める・置き場の外を止める判定つき）で ready
        を打つ。"""
        listed = os.path.join(self.root, "logs", "state", "write-and-push", "ready.writes")
        done = self.ccnavi(
            "--record-writes",
            listed,
            "--record-tree",
            self.parent_tree,
            "--cwd",
            self.parent_tree,
            "review",
            "ready",
            "--result",
            fixture,
        )
        written = []
        if os.path.exists(listed):
            with open(listed, encoding="utf-8") as f:
                written = f.read().splitlines()
        return done, written

    def closed_family(self):
        """親を閉じて push し、ready を打てる形にする。返すのは取得した結果の見本。"""
        self.family(plan=["design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        fixture = self.remote()
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        self.assertEqual(self.confirm(fixture, 1).returncode, 0)
        self.start_parent()
        self.propose("i0001", parent_text("i0001", ["design"], feedback=[]))
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(self.ccnavi("ticket", "finish", "i0001").returncode, 0)
        self.commit_parent("状態の移動")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        return fixture

    @unittest.skipIf(os.name == "nt", "大文字違いの並存と名前の \\ は Windows では作れない")
    def test_write_and_push_lets_ready_remove_wip_in_any_case_but_nothing_else(self):
        """write-and-push の置き場の外の検査は、ready が消す wip/ の判定（大文字小文字と \\
        を問わない）と
        同じ判定で、消したものだけを通す。"""
        if not can_case_sensitive():
            self.skipTest("大文字小文字を区別しないファイルシステムでは大文字違いを並べられない")
        fixture = self.closed_family()
        odd = ["WIP/eli5/a.html", "wip\\eli5\\b.html"]
        for rel in odd:
            write(os.path.join(self.parent_tree, *rel.split("/")), "<p>x</p>\n")
            git(self.parent_tree, "add", "--", rel)
        git(self.parent_tree, "commit", "--quiet", "-m", "odd wip")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        passed, written = self.ready_recorded(fixture)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        for rel in [*odd, "wip/design/plan.md"]:
            self.assertIn(rel, written)
            self.assertFalse(os.path.lexists(os.path.join(self.parent_tree, *rel.split("/"))))

    def test_write_and_push_still_stops_writes_outside_the_places_for_ready(self):
        """wip/ の例外は、今は無い wip/ の下のものだけ。

        在るもの・wip/ の外・ツリーの外（別の親）は通さない。
        """
        from ccnavi.entry import cli

        base = os.path.realpath(self.parent_tree)
        write(os.path.join(base, "wip", "kept.md"), "x\n")
        write(os.path.join(base, "src", "gone.py"), "x\n")
        os.remove(os.path.join(base, "src", "gone.py"))
        cases = {
            os.path.join(base, "wip", "gone.md"): True,
            os.path.join(base, "WIP", "eli5", "gone.html"): True,
            os.path.join(base, "wip", "kept.md"): False,
            os.path.join(base, "src", "gone.py"): False,
            os.path.join(base, "wipx", "gone.md"): False,
            os.path.join(base, "wip"): False,
            base: False,
            os.path.join(self.root, "wip", "gone.md"): False,
            os.path.join(self.root, ".claude", "worktrees", "i0002", "wip", "gone.md"): False,
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(cli._ready_removed_wip(os.path.realpath(path), base), expected)

    def test_close_early_closes_early_and_files_the_rest(self):
        """ユーザが「キリの良いところ」と早めに閉じる。残りは取り消し・省略・受け入れになり、
        issue に書き出される。"""
        self.family(plan=["research", "design", "acceptance", "implement"])
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], review=False)
        )
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/research/summary.md", "s\n")])
        # 作業中の子がいる間は早めに閉じられない。
        fixture = self.remote()
        refused = self.close_early(fixture)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("作業中の子", refused.stderr)
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        # 2 番目の子を提案して承認するが、着手はしない（todo/ に残る）。
        self.propose("i0001-02-02", child_text("i0001-02-02", "i0001", 2, ["wip/design/*"]))
        self.commit_parent("propose 02")
        self.assertEqual(self.approve().returncode, 0)
        self.start_parent()
        data = read_json(fixture)
        data["threads"] = [{"id": "t0", "resolved": False, "url": "u/7#t0", "body": "気になる"}]
        write(fixture, json.dumps(data))
        # n なら何も変わらない。
        declined = self.close_early(fixture, answer="n")
        self.assertNotEqual(declined.returncode, 0)
        self.assertIn("i0001-02-02", declined.stdout)
        self.assertIn("実装とテスト", declined.stdout)
        self.assertIn("フィードバック計画", declined.stdout)
        self.assertIn("u/7#t0", declined.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "doing", "i0001-02-02.md")))
        # y で早めに閉じる。
        done = self.close_early(fixture, reason="今期はここまで")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("早めに閉じた", done.stdout)
        cancelled = os.path.join(self.approved, "done", "i0001-02-02.md")
        self.assertTrue(os.path.exists(cancelled))
        with open(cancelled, encoding="utf-8") as f:
            self.assertIn("cancelled_at:", f.read())
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001-02-02.md")))
        mark = read_json(os.path.join(self.approved, "phases", "i0001", "close-early.json"))
        self.assertEqual(mark["reason"], "今期はここまで")
        self.assertEqual(mark["cancelled"], ["i0001-02-02"])
        # 手を付けていないフェーズ(2・3・4)には省略のマーカーを置かない。
        self.assertEqual(mark["skipped"], [])
        for number in (2, 3, 4):
            self.assertFalse(
                os.path.exists(os.path.join(self.approved, "phases", "i0001", f"{number}.skipped")),
                number,
            )
        self.assertEqual(mark["accepted"], ["u/7#t0"])
        with open(
            os.path.join(self.state, "review-close-early-issue-i0001.md"), encoding="utf-8"
        ) as f:
            issue = f.read()
        self.assertTrue(issue.startswith("親 の残り\n\n"))
        self.assertIn("i0001-02-02", issue)
        self.assertIn("実装とテスト", issue)
        self.assertIn("u/7#t0", issue)
        self.assertIn("今期はここまで", issue)
        # 打ち直しても、取り消し済みの子だけになったフェーズ(2)に省略のマーカーを置かない。
        again = self.close_early(fixture, reason="今期はここまで")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        for number in (2, 3, 4):
            self.assertFalse(
                os.path.exists(os.path.join(self.approved, "phases", "i0001", f"{number}.skipped")),
                number,
            )
        # 早めに閉じたので、フィードバック計画が無くても親を閉じられる。
        explained = self.ccnavi("--explain")
        self.assertIn("ユーザが早めにクローズした", explained.stdout)
        closed = self.ccnavi("ticket", "finish", "i0001")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        # Draft はまだ外れていない。片付けて push してから ready で外す。
        self.assertFalse(
            os.path.exists(os.path.join(self.approved, "phases", "i0001", "ready.json"))
        )
        self.commit_parent("状態の移動")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        passed = self.ready(fixture)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertIn("wip wip/research/summary.md", passed.stdout.splitlines())

    def test_close_early_accepts_threads_in_three_stages(self):
        """受け入れの返信と解決は sh がする。置く前に済ませるため、3 回に分けて打つ。

        1 回目（`--accept-plan`）は y/N を聞いて計画だけを書く。何も置かない。
        2 回目（`--accept-check`）は、取り直した結果の未解決が計画と同じかだけを確かめる。
        3 回目（`--accept-done`）は、返信と解決のあとで取り直した結果を、ホストの実状態で確かめて
        聞き直さずに置く。
        """
        self.family(plan=["design"])
        self.commit_parent()
        fixture = self.remote()
        data = read_json(fixture)
        data["threads"] = [{"id": "t0", "resolved": False, "url": "u/7#t0", "body": "気になる"}]
        write(fixture, json.dumps(data))
        plan = os.path.join(self.state, "accept-plan.json")
        base = ("--cwd", self.parent_tree, "user", "close-early", "--reason", "ここまで")
        marker = os.path.join(self.approved, "phases", "i0001", "close-early.json")

        # 計画が無いまま 2 回目・3 回目を打っても、置かない
        for flag in ("--accept-check", "--accept-done"):
            early = self.ccnavi(*base, "--result", fixture, flag, plan)
            self.assertNotEqual(early.returncode, 0)
            self.assertIn("受け入れの計画", early.stderr)

        # n なら計画も書かない
        declined = self.ccnavi(*base, "--result", fixture, "--accept-plan", plan, stdin="n\n")
        self.assertNotEqual(declined.returncode, 0)
        self.assertFalse(os.path.exists(plan))

        planned = self.ccnavi(*base, "--result", fixture, "--accept-plan", plan, stdin="y\n")
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
        body = read_json(plan)
        self.assertEqual([t["id"] for t in body["threads"]], ["t0"])
        self.assertIn("早めに閉じた", body["threads"][0]["reply"])
        self.assertIn("issue の下書き", body["threads"][0]["reply"])
        self.assertFalse(os.path.exists(marker))

        # y/N の間に指摘が増えていたら、ホストに触る前に止まる
        grown = read_json(fixture)
        grown["threads"].append({"id": "t1", "resolved": False, "url": "u/7#t1", "body": "追加"})
        grown_fixture = os.path.join(self.state, "grown.json")
        write(grown_fixture, json.dumps(grown))
        stale = self.ccnavi(*base, "--result", grown_fixture, "--accept-check", plan)
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn("未解決の指摘が変わった", stale.stderr)
        checked = self.ccnavi(*base, "--result", fixture, "--accept-check", plan)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertFalse(os.path.exists(marker))

        # sh が解決する前（ホストは未解決のまま）に置こうとしても置かない
        unresolved = self.ccnavi(*base, "--result", fixture, "--accept-done", plan)
        self.assertNotEqual(unresolved.returncode, 0)
        self.assertIn("ホストで解決済みになっていない", unresolved.stderr)
        self.assertFalse(os.path.exists(marker))

        # 解決したあとに、見せていない未解決が増えていたら置かない
        data["threads"][0]["resolved"] = True
        extra = json.loads(json.dumps(data))
        extra["threads"].append({"id": "t1", "resolved": False, "url": "u/7#t1", "body": "追加"})
        extra_fixture = os.path.join(self.state, "extra.json")
        write(extra_fixture, json.dumps(extra))
        more = self.ccnavi(*base, "--result", extra_fixture, "--accept-done", plan)
        self.assertNotEqual(more.returncode, 0)
        self.assertIn("計画のほかに未解決の指摘が残っている", more.stderr)
        self.assertFalse(os.path.exists(marker))

        # 解決済みで、ほかに増えていなければ、聞き直さずに置く（stdin は空）
        write(fixture, json.dumps(data))
        done = self.ccnavi(*base, "--result", fixture, "--accept-done", plan)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        mark = read_json(marker)
        self.assertEqual(mark["accepted"], ["u/7#t0"])
        self.assertFalse(
            os.path.exists(os.path.join(self.approved, "phases", "i0001", "accepted.json"))
        )

    def test_close_early_reply_does_not_promise_an_issue_with_no_issue(self):
        """`--no-issue` で残りを issue に書き出さないときは、返信に issue に回すと書かない。"""
        self.family(plan=["design"])
        self.commit_parent()
        fixture = self.remote()
        data = read_json(fixture)
        data["threads"] = [{"id": "t0", "resolved": False, "url": "u/7#t0", "body": "気になる"}]
        write(fixture, json.dumps(data))
        plan = os.path.join(self.state, "accept-plan.json")
        base = ("--cwd", self.parent_tree, "user", "close-early", "--reason", "ここまで")
        planned = self.ccnavi(
            *base, "--result", fixture, "--accept-plan", plan, "--no-issue", stdin="y\n"
        )
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
        self.assertNotIn("issue", read_json(plan)["threads"][0]["reply"])

    def test_close_early_marks_a_touched_phase_so_the_hook_stays_quiet(self):
        """未着手の子のほかにレビュー待ちの子が居るフェーズは、取り消すと終わる。省略のマーカーが
        無いと、次の hook の告知が pending を書いて「レビューを頼め」と言い出すので、置き続ける。"""
        self.family(plan=["design", "implement"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")
        # フェーズ 1 に 2 つ目の子を足して承認するが、着手はしない。
        self.propose("i0001-01-02", child_text("i0001-01-02", "i0001", 1, ["wip/design/*"]))
        self.commit_parent("propose 02")
        self.assertEqual(self.approve().returncode, 0)
        self.start_parent()
        fixture = self.remote()
        done = self.close_early(fixture)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        phases_dir = os.path.join(self.approved, "phases", "i0001")
        # 手を付けたフェーズ 1 には置く。手を付けていないフェーズ 2 には置かない。
        self.assertTrue(os.path.exists(os.path.join(phases_dir, "1.skipped")))
        self.assertFalse(os.path.exists(os.path.join(phases_dir, "2.skipped")))
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        self.assertFalse(os.path.exists(os.path.join(phases_dir, "1.pending")))

    def test_close_early_is_a_human_path(self):
        """close-early はターミナルを求める。サブエージェントと直接の exe 呼び出しは止まる。"""
        self.family(plan=["design"])
        fixture = self.remote()
        result = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--entry",
            "sh",
            "user",
            "close-early",
            "--reason",
            "r",
            "--result",
            fixture,
            "--guard-ticket-approval",
            "enable",
            stdin="y\n",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ターミナル", result.stderr)
        for command in (
            "sh .ccnavi/scripts/ccnavi-review.sh decide 1",
            "sh .ccnavi/scripts/ccnavi-review.sh ready",
        ):
            payload = {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "cwd": self.parent_tree,
                "session_id": "s1",
                "agent_id": "sub-1",
                "tool_input": {"command": command},
            }
            denied = self.ccnavi("--mode", "enable", stdin=json.dumps(payload))
            self.assertIn("DENY_SUBAGENT_TICKET_OP", self.reason(denied), command)

    # ---- 8. 計画の無い親は承認できるが、着手は止まる

    def test_parent_without_plan_is_approved_but_cannot_start(self):
        from tests.ticket.test_ticket import ticket_text

        self.propose("i0001", ticket_text("i0001", allow=("src/*", "wip/*"), plan=0))
        self.propose(
            "i0001-01-01",
            ticket_text("i0001-01-01", parent="i0001", phase=1, allow=("src/a/*",)),
        )
        self.commit_parent()
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "doing", "i0001-01-01.md")))
        started = self.ccnavi("ticket", "start", "i0001")
        self.assertEqual(started.returncode, 1, started.stdout)
        self.assertIn("に plan が無いので着手できない", started.stderr)
        self.assertIn("子や子の提案があっても同じ", started.stderr)
        self.assertIn("phases.yml", started.stderr)
        # 子の着手は親が着手済みであることが条件のまま。親が止まれば子も着手できない。
        self.worktree("i0001-01-01", "i0001")
        child = self.ccnavi("ticket", "start", "i0001-01-01")
        self.assertEqual(child.returncode, 1, child.stdout)


def scoped_child_text(name, parent, phase, allow=(), ask=(), regex=()):
    """`child_text` に ask と regex の項を足せる版。regex は allow の項として書く。"""
    lines = [
        "---",
        "version: 1",
        f"ticket: {name}",
        f"parent: {parent}",
        f"phase: {phase}",
        "human_review:",
        "  required: true",
        "  reason: t",
        f"title: 子 {name}",
        "rationale: r",
    ]
    if allow or regex:
        lines.append("allow:")
        for g in allow:
            lines += ["  - match: Write|Edit", f'    glob: "{g}"']
        for r in regex:
            lines += ["  - match: Write|Edit", f"    regex: '{r}'"]
    if ask:
        lines.append("ask:")
        for g in ask:
            lines += ["  - match: Write|Edit", f'    glob: "{g}"']
    lines += ['started_at: ""', 'completed_at: ""', 'base_sha: ""', "---", "", "本文"]
    return "\n".join(lines) + "\n"


# 定義 research を消した phases.yml。親の計画の 1 番目の定義が引けなくなる。
PHASES_WITHOUT_RESEARCH = PHASES.split("  research:", 1)[0] + (
    "  design:" + PHASES.split("  design:", 1)[1]
)

# scope を inherit にした作業の定義だけを持つ phases.yml。
PHASES_INHERIT = """
version: 1
phases:
  open:
    kind: work
    title: 自由
    review: none
    scope: inherit
"""


class WrapperFlagsComeOnceTest(PhaseHarness):
    """sh が計算して渡すパス（`--root` / `--cwd`）は 2 度渡せない（issue #65）。

    `ccnavi-review.sh` は `"$bin" --root "$root" --cwd "$here" "$@"` の形で呼ぶ。
    どちらも「いまどこで動いているか」で、エージェントが宣言するものではない。後ろに
    1 本足すと argparse が後勝ちで読むので、2 本目が在ること自体を断る。
    """

    def test_a_second_cwd_is_refused(self):
        refused = self.ccnavi("--cwd", self.parent_tree, "--cwd", self.root, "--explain", "--json")

        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("--cwd は 1 度しか渡せない", refused.stderr)

    def test_one_of_each_still_goes_through(self):
        """1 本ずつなら通る。断る側だけを見ると、全部断る実装でもテストが通ってしまう。"""
        passed = self.ccnavi("--cwd", self.parent_tree, "--explain", "--json")

        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertTrue(json.loads(passed.stdout))


class PhasesFlagIsDiagnosisOnlyTest(PhaseHarness):
    """`ticket` のサブコマンドに `--phases` を足しても、定義は共通レイヤーのまま（issue #65）。

    `.ccnavi/scripts/ccnavi-ticket.sh` が引数をそのまま渡すので、この形はエージェントが
    Bash で打てる。通していた頃は、`review: mr` の定義を `review: none` と宣言する
    ファイルに差し替えて、レビュー待ちを飛ばせた。
    """

    def test_a_phases_flag_on_ticket_done_does_not_drop_the_review(self):
        # 同じ `design` を `review: none` と宣言するファイル。
        loose = write(
            os.path.join(self.root, "loose.yml"),
            "version: 1\nphases:\n  design:\n    kind: work\n    title: 設計\n"
            '    review: none\n    scope: ["wip/design/*"]\n',
        )
        self.family(plan=["design"])
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"], review=False)
        )
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])

        closed = self.ccnavi("ticket", "finish", "i0001-01-01", "--phases", loose)

        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertIn("--phases は診断", closed.stderr, "無視したことを言っていない")
        # `review: mr` のまま。閉じた先はレビュー待ちで、`.ccnavi/approved/done/` ではない。
        self.assertTrue(
            os.path.exists(
                os.path.join(self.parent_tree, "wip", "proposals", "review", "i0001-01-01.md")
            ),
            "レビュー待ちへ動いていない（渡した定義で判定された）",
        )
        self.assertFalse(
            os.path.exists(os.path.join(self.approved, "done", "i0001-01-01.md")),
            "レビューを飛ばして閉じた",
        )


class ScopeLimitTest(PhaseHarness):
    """範囲の上限（設計 wip/design/approve-carry.md 3・4、6.1〜6.2）。

    承認は範囲の超過を拒まず「編集対象としているが」として見せる。判定は子 → 親 → 定義の
    厳しい側で切り詰め、外へ出した上限を `limit:` 行で名指しする。
    親の範囲は既定で `src/*`, `wip/*`, `tests/*`、フェーズ 1 の定義 research は `wip/research/*`。
    """

    def approved_child(self, text, name="i0001-01-01", plan=("research", "design")):
        """親を承認し、子を提案して承認し、子のワークツリーを作って着手する。ワークツリーを返す。"""
        self.family(plan=plan)
        self.propose(name, text)
        self.commit_parent("propose child")
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "doing", name + ".md")))
        self.last_approval = approved
        return self.run_child(name)

    def write_to(self, tree, rel, mode="enable"):
        return self.hook(
            "PreToolUse",
            "Write",
            tree,
            mode=mode,
            file_path=os.path.join(tree, *rel.split("/")),
            content="x\n",
        )

    def decision(self, result):
        if not result.stdout.strip():
            return ""
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        return out.get("permissionDecision", "")

    # ---- 6.1 承認

    def test_regex_child_is_approved_and_shown_as_stopped_by_the_judge(self):
        """4. regex の子は承認でき、「編集対象としているが」に出る。"""
        self.family(plan=["research", "design"])
        self.propose(
            "i0001-01-01",
            scoped_child_text("i0001-01-01", "i0001", 1, regex=("^(wip/research|src/a|docs)/",)),
        )
        self.commit_parent("propose child")
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "doing", "i0001-01-01.md")))
        self.assertIn("編集対象としているが", approved.stdout)
        self.assertIn("regex", approved.stdout.split("編集対象としているが", 1)[1])
        self.assertNotIn("承認の対象にしない", approved.stderr)

    # ---- 6.2 判定

    def test_write_beyond_the_phase_type_is_denied_and_names_the_type(self):
        """7. enable: 定義の上限の外で子の範囲の中への Write は DENY_TICKET_SCOPE。"""
        tree = self.approved_child(
            child_text("i0001-01-01", "i0001", 1, ["wip/research/*", "src/a/*"])
        )
        inside = self.write_to(tree, "wip/research/note.md")
        self.assertNotEqual(self.decision(inside), "deny", self.reason(inside))
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(inside))

        denied = self.write_to(tree, "src/a/x.py")
        self.assertEqual(denied.returncode, 0, denied.stderr)
        self.assertEqual(self.decision(denied), "deny", denied.stdout)
        reason = self.reason(denied)
        self.assertIn("DENY_TICKET_SCOPE", reason)
        self.assertIn("limit: phase type 調査 (research): wip/research/*", reason)

    def test_dry_run_still_stops_the_write_and_says_which_limit(self):
        """8. dry-run: 範囲の外の Write は関門なので止まり、`limit: phase type` が出る。"""
        tree = self.approved_child(
            child_text("i0001-01-01", "i0001", 1, ["wip/research/*", "src/a/*"])
        )
        result = self.write_to(tree, "src/a/x.py", mode="dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.decision(result), "deny", result.stdout)
        text = self.reason(result)
        self.assertIn("still applies", text)
        self.assertIn("DENY_TICKET_SCOPE", text)
        self.assertIn("limit: phase type", text)

    def test_ask_inside_the_phase_type_stays_ask(self):
        """9. 定義の上限の中で子が `ask` と書いた場所は TICKET_ASK のまま。外なら止まる。"""
        tree = self.approved_child(
            scoped_child_text(
                "i0001-01-01",
                "i0001",
                1,
                allow=("wip/research/a/*",),
                ask=("wip/research/b/*", "src/b/*"),
            )
        )
        asked = self.write_to(tree, "wip/research/b/x.md")
        self.assertEqual(self.decision(asked), "ask", asked.stdout)
        self.assertIn("TICKET_ASK", self.reason(asked))
        # 定義は allow か外しか言わない。ask と書いた場所でも定義の外なら外を採る。
        beyond = self.write_to(tree, "src/b/x.py")
        self.assertEqual(self.decision(beyond), "deny", beyond.stdout)
        self.assertIn("limit: phase type", self.reason(beyond))

    def test_inherit_scope_does_not_cut_by_the_type(self):
        """10. 定義の scope が inherit なら、定義では切り詰めない。"""
        write(self.phases, PHASES_INHERIT)
        self.phases_text = PHASES_INHERIT
        tree = self.approved_child(
            child_text("i0001-01-01", "i0001", 1, ["src/a/*"]), plan=["open"]
        )
        result = self.write_to(tree, "src/a/x.py")
        self.assertNotEqual(self.decision(result), "deny", result.stdout)
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(result))
        self.assertNotIn("limit:", self.reason(result))

    def test_no_phases_file_anywhere_stops_a_parent_without_plan(self):
        """11. どのレイヤーにも phases.yml が無いと plan を持てず、計画の無い親は着手が止まる。"""
        from tests.ticket.test_ticket import ticket_text

        os.remove(self.phases)
        self.propose("i0001", ticket_text("i0001", allow=("src/*", "wip/*"), plan=0))
        self.commit_parent()
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        started = self.ccnavi("ticket", "start", "i0001")
        self.assertEqual(started.returncode, 1, started.stdout)
        self.assertIn("phases.yml に最小のフェーズ定義", started.stderr)
        self.assertIn("書くのはユーザ", started.stderr)

    def test_editing_phases_yml_does_not_change_the_cut_of_an_approved_child(self):
        """12. 承認のあとの判定は親に固定したコピー（`phases:`）の定義で切り詰める。

        `phases.yml` から定義を消しても、壊しても、判定はコピーを読むので上限は変わらない。
        """
        tree = self.approved_child(
            child_text("i0001-01-01", "i0001", 1, ["wip/research/*", "src/a/*", "docs/*"])
        )
        for label, text in (
            ("定義を消した", PHASES_WITHOUT_RESEARCH),
            ("phases.yml が不正", "version: 1\nphases: [\n"),
        ):
            with self.subTest(label):
                write(self.phases, text)
                beyond_type = self.write_to(tree, "src/a/x.py")
                self.assertEqual(self.decision(beyond_type), "deny", beyond_type.stdout)
                self.assertIn("limit: phase type 調査 (research)", self.reason(beyond_type))
                self.assertNotIn("定義の上限では切り詰めていない", self.reason(beyond_type))
                inside = self.write_to(tree, "wip/research/x.md")
                self.assertNotEqual(self.decision(inside), "deny", self.reason(inside))
                beyond_parent = self.write_to(tree, "docs/x.md")
                self.assertEqual(self.decision(beyond_parent), "deny", beyond_parent.stdout)
                self.assertIn("DENY_TICKET_SCOPE", self.reason(beyond_parent))
                self.assertIn("limit: parent i0001", self.reason(beyond_parent))

    # ---- 判定が失敗しない（チケット approve-carry-04 の 6〜8）

    def assert_answered(self, result):
        """hook が例外で終わらず、判定の答えとして読める形で返したこと。"""
        self.assertIn(result.returncode, (0, 2), result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        if result.stdout.strip():
            json.loads(result.stdout)

    def break_phases_encoding(self):
        """共通レイヤーの phases.yml に、UTF-8 として読めないバイト列を混ぜる。"""
        with open(self.phases, "wb") as f:
            f.write(PHASES.encode("utf-8").replace("調査".encode(), b"\xff\xfe\x80"))

    def test_undecodable_phases_file_does_not_crash_the_write_judge(self):
        """6. phases.yml が UTF-8 として読めなくても、Write の判定は例外で終わらない。"""
        tree = self.approved_child(
            child_text("i0001-01-01", "i0001", 1, ["wip/research/*", "src/a/*"])
        )
        self.break_phases_encoding()
        result = self.write_to(tree, "src/a/x.py")
        self.assert_answered(result)
        # 判定は親に固定したコピー（`phases:`）の定義で切り詰め、phases.yml は読まない。
        self.assertEqual(self.decision(result), "deny", result.stdout)
        self.assertIn("limit: phase type 調査 (research)", self.reason(result))
        self.assertNotIn("切り詰めていない", self.reason(result))

    def test_undecodable_phases_file_does_not_crash_the_bash_judge(self):
        """7. 同じ状態で、Bash の実行前チェック（止めるかどうかの経路）も例外で終わらない。"""
        tree = self.approved_child(
            child_text("i0001-01-01", "i0001", 1, ["wip/research/*", "src/a/*"])
        )
        self.break_phases_encoding()
        for command in (
            "ls",
            "echo x > src/a/x.py",
            "sh ../../../.ccnavi/scripts/ccnavi-ticket.sh finish i0001-01-01",
        ):
            with self.subTest(command):
                self.assert_answered(self.hook("PreToolUse", "Bash", tree, command=command))

    def test_no_phases_file_with_a_plan_says_nothing_about_the_type(self):
        """8. 承認のあとに phases.yml を消しても、判定は親に固定したコピーの定義で切り詰める。

        判定は phases.yml を読まない。注記も出さない。
        """
        tree = self.approved_child(
            child_text("i0001-01-01", "i0001", 1, ["wip/research/*", "src/a/*"])
        )
        os.remove(self.phases)
        self.assertFalse(os.path.exists(self.phases))
        inside = self.write_to(tree, "wip/research/note.md")
        self.assert_answered(inside)
        self.assertNotEqual(self.decision(inside), "deny", inside.stdout)
        self.assertNotIn("読めない", self.reason(inside))
        beyond = self.write_to(tree, "src/a/x.py")
        self.assert_answered(beyond)
        self.assertEqual(self.decision(beyond), "deny", beyond.stdout)
        self.assertIn("limit: phase type 調査 (research)", self.reason(beyond))
        self.assertNotIn("切り詰めていない", self.reason(beyond))

    def test_regex_child_is_judged_by_the_real_path(self):
        """14. regex の子: 実際のパスで親と定義に適用する。"""
        tree = self.approved_child(
            scoped_child_text("i0001-01-01", "i0001", 1, regex=("^(wip/research|src/a|docs)/",))
        )
        inside = self.write_to(tree, "wip/research/x.md")
        self.assertNotEqual(self.decision(inside), "deny", inside.stdout)
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(inside))

        beyond_type = self.write_to(tree, "src/a/x.py")
        self.assertEqual(self.decision(beyond_type), "deny", beyond_type.stdout)
        self.assertIn("limit: phase type 調査 (research)", self.reason(beyond_type))

        beyond_parent = self.write_to(tree, "docs/x.md")
        self.assertEqual(self.decision(beyond_parent), "deny", beyond_parent.stdout)
        self.assertIn("limit: parent i0001", self.reason(beyond_parent))

    def test_regex_child_ignores_case_like_the_glob_child(self):
        """14. regex の子: 範囲の表記は glob の子と同じく大文字小文字を区別しない。

        範囲はユーザが宣言する意図なので、`regex` で書いても同じ場所を指す。
        区別が要るなら `(?-i:...)` で囲む。
        """
        tree = self.approved_child(
            scoped_child_text("i0001-01-01", "i0001", 1, regex=("^wip/research/",))
        )
        inside = self.write_to(tree, "wip/Research/x.md")
        self.assertNotEqual(self.decision(inside), "deny", inside.stdout)
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(inside))

    def test_regex_child_can_keep_the_distinction_with_an_inline_flag(self):
        """14. regex の子: `(?-i:...)` で囲んだ範囲は書いた表記のとおりにヒットする。

        ルールの側（tests/config/test_config_union_holes.py）と同じ書き方が、
        チケットの範囲でも書けることを見る。
        """
        tree = self.approved_child(
            scoped_child_text("i0001-01-01", "i0001", 1, regex=("^wip/(?-i:research)/",))
        )
        inside = self.write_to(tree, "wip/research/x.md")
        self.assertNotEqual(self.decision(inside), "deny", inside.stdout)
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(inside))

        # 定義の上限（glob）にはヒットする表記だが、子の範囲が区別するので止まる。
        outside = self.write_to(tree, "wip/Research/x.md")
        self.assertEqual(self.decision(outside), "deny", outside.stdout)
        self.assertIn("DENY_TICKET_SCOPE", self.reason(outside))

    def test_post_monitoring_reports_a_shell_write_beyond_the_type(self):
        """15. 実行後チェック: Bash が定義の上限の外に書くと POST_TICKET_SCOPE。"""
        tree = self.approved_child(
            child_text("i0001-01-01", "i0001", 1, ["wip/research/*", "src/a/*"])
        )
        self.hook("UserPromptSubmit", "", tree)
        first = self.hook("PostToolUse", "Bash", tree, command="python gen.py")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        # 定義の中への書き込みは言わない。
        write(os.path.join(tree, "wip", "research", "gen.md"), "ok\n")
        quiet = self.hook("PostToolUse", "Bash", tree, command="python gen.py")
        self.assertEqual(quiet.returncode, 0, quiet.stdout + quiet.stderr)
        self.assertNotIn("POST_TICKET_SCOPE", quiet.stdout + quiet.stderr)
        # 子の範囲の中だが定義の外。
        write(os.path.join(tree, "src", "a", "gen.py"), "x\n")
        after = self.hook("PostToolUse", "Bash", tree, command="python gen.py")
        self.assertEqual(after.returncode, 2, after.stdout + after.stderr)
        self.assertIn("POST_TICKET_SCOPE", after.stderr)
        self.assertIn("src/a/gen.py", after.stderr.replace("\\", "/"))
        self.assertIn("調査", after.stderr)

    def test_subagent_stop_bounces_a_change_beyond_the_type_and_names_it(self):
        """16. SubagentStop: 定義の上限の外の変更で差し戻し、上限の名指しが付く。"""
        tree = self.approved_child(
            child_text("i0001-01-01", "i0001", 1, ["wip/research/*", "src/a/*"])
        )
        write(os.path.join(tree, "wip", "research", "ok.md"), "ok\n")
        write(os.path.join(tree, "src", "a", "stray.py"), "x\n")
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "stray")
        result = self.hook("SubagentStop", "", tree, agent_id="sub-1")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("POST_TICKET_SCOPE", result.stderr)
        self.assertIn("src/a/stray.py", result.stderr)
        self.assertIn("（定義 調査 の上限の外）", result.stderr)
        self.assertNotIn("wip/research/ok.md", result.stderr)


if __name__ == "__main__":
    unittest.main()
