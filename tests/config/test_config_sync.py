"""ユーザの発言のたびに、共通レイヤーをプロジェクトの `.ccnavi/config/common/` へミラーする。

共通レイヤー（ワークスペースルートの `.ccnavi/config/common/`）はワークスペースの git にあり、
プロジェクトだけを clone したユーザからは見えない。ユーザの発言（UserPromptSubmit）で、
共通レイヤーの中身を、着手済みの親のワークツリーにある
プロジェクトの `.ccnavi/config/common/` へ写す。
プロジェクトの `.ccnavi/config/project/` には触れない。`ticket start` では写さない。

fixture は tests/config/test_config_union.py の ConfigUnionHarness を継ぐ。共通レイヤーは
rules / risks の 2 本（と、期待とスクリプト）を持ち、lib の config は 3 本とも別の中身を持つ。
app は `.ccnavi/` を持たない。

見るのは 5 つ。

1. 発言が、共通レイヤーの中身を着手済みの親のワークツリーのミラーへ写し、config には触れない。
   共通レイヤーから無くなったファイルはミラーからも消す。`script:` の値は書き換えない
2. 子とワークスペース自身の作業、未着手の親には写さない。`ticket start` 自身は写さない
3. 写せない形（読めない・リンク・書き込みの失敗）では何も写さず、発言と着手は止めずに理由を言う。
   error のあるテンプレートだけは1本ずつ写さずに飛ばす。上書きの記録・知らせ・閉じるのを
   止める処理は無い
4. 写した分は発言の受け入れに入るので、実行後チェックとバックアップと復元が戻さず、報告もしない。
   ツール呼び出しの中でミラーが書き換わったら、今までどおり戻す
5. 差分が無いときは何も読み書きしない（指紋が前と同じ）。元か先が動けば、検証つきで比べ直す
"""

from __future__ import annotations

import json
import os
from unittest import mock

from ccnavi.infra import settings
from ccnavi.tickets import configsync, promptsync, risk
from tests import ROOT, phasecopy
from tests.config.test_config_union import (
    COMMON_PHASES,
    COMMON_RISK,
    COMMON_RULES,
    LIB_PHASES,
    ConfigUnionHarness,
    git,
    layer_path,
    read,
    ticket_text,
    write,
)
from tests.inproc import run_ccnavi
from tests.ticketsample import PLAIN_PHASES

SCOPE = ("src/*",)

COMMON_SCRIPT_RISK = (
    COMMON_RISK
    + "  - {id: counted, points: 5, script: .ccnavi/config/common/scripts/count.sh, message: 数えた}\n"  # noqa: E501
)
COUNT_SH = "printf '{\"points\": 1}'\n"
FEATURE_TEMPLATE = (
    "name: feature\ndescription: 機能を設計する\nwhen: 新しい機能を足すとき\nplan: [design]\n"
)
EXPECTATIONS = "version: 1\nexpectations: []\n"


def mirror_of(tree_root, name):
    return os.path.join(tree_root, ".ccnavi", "config", "common", *name.split("/"))


def config_of(tree_root, kind):
    return os.path.join(tree_root, ".ccnavi", "config", "project", settings.LAYER_FILE_NAMES[kind])


class Started:
    """`ticket start` と、その後の発言（ミラーを写す）の結果をまとめたもの。"""

    def __init__(self, start, prompt, said):
        self.returncode = start.returncode
        self.stdout = start.stdout + said
        self.stderr = start.stderr + prompt.stderr


class ConfigMirrorTest(ConfigUnionHarness):
    def setUp(self):
        super().setUp()
        self.sessions = set()

    def session_start(self, session="s1"):
        """セッションの始まりを渡す。実行後チェックの基準が取れるので、以降の発言の受け入れが働く。"""
        payload = {"hook_event_name": "SessionStart", "session_id": session, "source": "startup"}
        payload["cwd"] = self.ws
        done = self.ccnavi("--mode", "enable", stdin=json.dumps(payload), guard="disable")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def prompt(self, session="s1", guard="disable"):
        """ユーザの発言を 1 件渡す（ミラーを写す）。モデルの文脈に足された文も返す。"""
        if session not in self.sessions:
            self.sessions.add(session)
            self.session_start(session)
        done = self.hook("", self.ws, event="UserPromptSubmit", session=session, guard=guard)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done

    def start_ticket(self, name):
        """親の `ticket start` を打ち、続けてユーザの発言を渡す。"""
        started = self.ccnavi("ticket", "start", name)
        prompted = self.prompt()
        return Started(started, prompted, self.reason(prompted))

    def start_parent(self, project="lib", name="i0001"):
        """project 向けの親を承認し、そのプロジェクトからワークツリーを切って着手する。"""
        owner = os.path.join(self.projects, project) if project else self.ws
        # 計画の無い親は着手できない。レイヤーに定義のあるプロジェクトはその定義を使い、
        # 定義の無いプロジェクト（app）には最小の定義を置く。
        plan = {"lib": "build", "": "docs"}.get(project, "work")
        if not os.path.exists(layer_path(owner, "phases")):
            write(layer_path(owner, "phases"), PLAIN_PHASES)
        self.propose(
            name,
            ticket_text(name, project=project, plan=[plan], allow=SCOPE),
            project=project,
        )
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        tree = self.worktree(owner, name)
        started = self.start_ticket(name)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        return tree, started

    def settings(self):
        conf, _ = settings.load(self.ws)
        return conf

    def add_script(self):
        write(self.risk, COMMON_SCRIPT_RISK)
        write(os.path.join(self.ws, ".ccnavi", "config", "common", "scripts", "count.sh"), COUNT_SH)

    def test_parent_start_mirrors_the_common_layer_and_leaves_config_alone(self):
        """1: 共通レイヤーの中身を親のワークツリーの `.ccnavi/config/common/` へ写す。config
        は触らない"""
        write(
            os.path.join(self.ws, ".ccnavi", "config", "common", "rule-expectations.yml"),
            EXPECTATIONS,
        )
        config_before = {k: read(config_of(self.lib, k)) for k in ("rules", "phases", "risk")}

        tree, started = self.start_parent()

        self.assertEqual(json.loads(read(mirror_of(tree, "rules.yml"))), COMMON_RULES)
        self.assertEqual(read(mirror_of(tree, "risks.yml")), COMMON_RISK)
        self.assertEqual(read(mirror_of(tree, "rule-expectations.yml")), EXPECTATIONS)
        for kind, text in config_before.items():
            self.assertEqual(read(config_of(tree, kind)), text, kind)
            self.assertEqual(read(config_of(self.lib, kind)), text, kind)
        # 元リポジトリのミラーは触らない（親のブランチに乗って届く）。
        self.assertFalse(os.path.exists(mirror_of(self.lib, "rules.yml")))
        self.assertIn("ミラー", started.stdout)
        self.assertIn(".ccnavi/config/common/rules.yml", started.stdout)
        # 上書きの記録は置かない。
        marks = self.approved_path("phases", "i0001", "config-sync.json", project="lib")
        self.assertFalse(os.path.exists(marks))

    def test_the_start_does_not_write_the_mirror(self):
        """2: `ticket start` 自身はミラーを書かない。書くのは次のユーザの発言。"""
        self.add_script()
        tree = self.worktree(os.path.join(self.projects, "lib"), "i0001")
        text = ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE)
        write(
            os.path.join(tree, "wip", "proposals", "todo", "i0001.md"),
            phasecopy.insert(text, LIB_PHASES),
        )
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        target = os.path.join(self.ws, "logs", "state", "write-and-push", "lib", "i0001.t.writes")

        started = self.ccnavi(
            "--record-writes", target, "--record-tree", tree, "ticket", "start", "i0001"
        )

        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertNotIn(".ccnavi/config/common", read(target) if os.path.exists(target) else "")
        self.assertFalse(os.path.exists(mirror_of(tree, "rules.yml")))
        self.assertIn("次のユーザの発言", started.stdout)
        self.prompt()
        self.assertTrue(os.path.isfile(mirror_of(tree, "rules.yml")))

    def test_the_risk_script_is_mirrored_without_rewriting_the_value(self):
        """1: 配点が指すスクリプトもミラーする。`script:` の値は共通レイヤーと同じ表記のまま。"""
        self.add_script()

        tree, started = self.start_parent()

        self.assertEqual(read(mirror_of(tree, "scripts/count.sh")), COUNT_SH)
        self.assertIn(
            "script: .ccnavi/config/common/scripts/count.sh", read(mirror_of(tree, "risks.yml"))
        )
        self.assertIn(".ccnavi/config/common/scripts/count.sh", started.stdout)

    def add_template(self, name="feature", text=FEATURE_TEMPLATE):
        directory = os.path.join(self.ws, ".ccnavi", "config", "common", "templates")
        return write(os.path.join(directory, name + ".yml"), text)

    def test_a_common_template_is_mirrored_and_kept_by_the_next_plan(self):
        """1: 共通のテンプレートも写す。ミラーに置いたものが次の着手で消されない。"""
        conf = self.settings()
        self.add_template()
        write(
            os.path.join(self.ws, ".ccnavi", "config", "common", "templates", ".hidden.yml"),
            "x: 1\n",
        )
        write(
            os.path.join(self.ws, ".ccnavi", "config", "common", "templates", "notes.txt"),
            "x\n",
        )

        tree, started = self.start_parent()

        self.assertEqual(read(mirror_of(tree, "templates/feature.yml")), FEATURE_TEMPLATE)
        self.assertIn(".ccnavi/config/common/templates/feature.yml", started.stdout)
        # `.yml` でないものと、`.` で始まるものは、テンプレートとして読まれないので写さない。
        self.assertFalse(os.path.exists(mirror_of(tree, "templates/notes.txt")))
        self.assertFalse(os.path.exists(mirror_of(tree, "templates/.hidden.yml")))
        # 写し終えたあとの次の計画は、何も変えない（ミラーに在るテンプレートを消さない）。
        changes, why = configsync.plan(conf, self.ws, tree)
        self.assertEqual((changes, why), ([], ""))

    def test_the_mirrored_template_is_read_as_the_common_one_in_a_single_clone(self):
        """1: 単体 clone では、ミラー（`config/common/`）が共通として `--templates` に読まれる。"""
        self.add_template()
        tree, _ = self.start_parent()

        shown = run_ccnavi(
            ["--root", tree, "--templates", "--json"],
            env={
                **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
                "CCNAVI_LEARNING": "disable",
            },
            cwd=ROOT,
        )

        self.assertEqual(shown.returncode, 0, shown.stdout + shown.stderr)
        answer = json.loads(shown.stdout)
        common = [layer for layer in answer["layers"] if layer["name"] == "common"][0]
        self.assertEqual([t["name"] for t in common["templates"]], ["feature"])
        self.assertEqual(
            common["templates"][0]["path"], ".ccnavi/config/common/templates/feature.yml"
        )
        self.assertEqual(
            [(t["name"], t["effective"]) for t in answer["templates"]], [("feature", True)]
        )

    def test_a_template_gone_from_the_common_layer_is_removed_from_the_mirror(self):
        """1: 共通のテンプレートが無くなれば、ミラーからも消す。空になった置き場も片付ける。"""
        write(mirror_of(self.lib, "templates/old.yml"), FEATURE_TEMPLATE.replace("feature", "old"))
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "--quiet", "-m", "old mirror")

        tree, started = self.start_parent()

        self.assertFalse(os.path.exists(mirror_of(tree, "templates/old.yml")))
        self.assertFalse(os.path.isdir(mirror_of(tree, "templates")))
        self.assertIn("削除した", started.stdout)

    def skipped_template_keeps_the_start(self, text):
        """3: error のあるテンプレートは写さずに飛ばす。そのために着手は止めない。"""
        self.add_template(text=text)

        tree, started = self.start_parent()

        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertTrue(os.path.isfile(mirror_of(tree, "rules.yml")))
        self.assertFalse(os.path.exists(mirror_of(tree, "templates/feature.yml")))

    def test_a_template_that_is_not_yaml_is_skipped(self):
        self.skipped_template_keeps_the_start("plan: [design\n")

    def test_a_template_with_a_broken_shape_is_skipped(self):
        self.skipped_template_keeps_the_start(
            "name: feature\ndescription: d\nplan: [design]\nflows: 3\n"
        )

    def test_a_template_with_only_warnings_is_mirrored(self):
        """3: 定義に無い型は warn にとどまる（定義は後から変わる）。止めずに写す。"""
        self.add_template(text=FEATURE_TEMPLATE.replace("design", "nothing-like-it"))

        tree, _ = self.start_parent()

        self.assertTrue(os.path.isfile(mirror_of(tree, "templates/feature.yml")))

    def test_a_symlinked_templates_place_skips_only_the_templates(self):
        """3: 共通のテンプレートの置き場がリンクなら、テンプレートだけ写さず、着手は止まらない。"""
        real = os.path.join(self.ws, "elsewhere")
        write(os.path.join(real, "feature.yml"), FEATURE_TEMPLATE)
        link = os.path.join(self.ws, ".ccnavi", "config", "common", "templates")
        self.symlink_or_skip(real, link)
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")

        started = self.start_ticket("i0001")

        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertFalse(os.path.exists(mirror_of(tree, "templates/feature.yml")))
        # ほかの設定のミラーは写る。
        self.assertTrue(os.path.isfile(mirror_of(tree, "rules.yml")))

    def test_a_script_shared_by_two_factors_is_mirrored_once(self):
        """1: 2 つの項目が同じスクリプトを指しても、写すのは 1 回。"""
        self.add_script()
        write(
            self.risk,
            COMMON_SCRIPT_RISK
            + "  - {id: again, points: 2, script: .ccnavi/config/common/scripts/count.sh,"
            " message: もう一度}\n",
        )
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")

        changes, why = configsync.plan(self.settings(), self.ws, tree)

        self.assertEqual(why, "")
        rels = [c.rel for c in changes]
        self.assertEqual(rels.count(".ccnavi/config/common/scripts/count.sh"), 1, rels)

    def test_files_gone_from_the_common_layer_are_removed_from_the_mirror(self):
        """1: 共通レイヤーから無くなったファイルは、ミラーからも消す。config は消さない。"""
        old = mirror_of(self.lib, "scripts/old.sh")
        write(old, "old\n")
        write(mirror_of(self.lib, "rule-expectations.yml"), EXPECTATIONS)
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "--quiet", "-m", "old mirror")

        tree, started = self.start_parent()

        self.assertFalse(os.path.exists(mirror_of(tree, "scripts/old.sh")))
        self.assertFalse(os.path.exists(mirror_of(tree, "rule-expectations.yml")))
        self.assertFalse(os.path.isdir(mirror_of(tree, "scripts")))
        self.assertIn("削除した", started.stdout)
        self.assertTrue(os.path.isfile(config_of(tree, "phases")))

    def test_identical_mirror_is_not_touched(self):
        """1: 中身が同じなら何も書かない。改行の違いは見ない。"""
        write(mirror_of(self.lib, "rules.yml"), json.dumps(COMMON_RULES))
        write(mirror_of(self.lib, "risks.yml"), COMMON_RISK.replace("\n", "\r\n"))
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "--quiet", "-m", "same")

        _, started = self.start_parent()

        self.assertNotIn("ミラー", started.stdout)

    def test_a_project_without_a_layer_gets_the_mirror(self):
        """1: `.ccnavi/` を持たないプロジェクトにも新しく置く。"""
        tree, started = self.start_parent(project="app")

        self.assertEqual(read(mirror_of(tree, "risks.yml")), COMMON_RISK)
        self.assertIn("新しく置いた", started.stdout)
        self.assertFalse(os.path.exists(config_of(tree, "rules")))

    def test_a_missing_or_empty_common_layer_leaves_the_mirror_alone(self):
        """1: 共通レイヤーが無い（空）なら配らず、ミラーを消さない。着手は止めない。"""
        common = os.path.join(self.ws, ".ccnavi", "config", "common")
        for name in os.listdir(common):
            os.remove(os.path.join(common, name))
        write(mirror_of(self.lib, "rules.yml"), json.dumps(COMMON_RULES))
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "--quiet", "-m", "old mirror")

        tree, started = self.start_parent()

        self.assertEqual(json.loads(read(mirror_of(tree, "rules.yml"))), COMMON_RULES)
        self.assertNotIn("ミラー", started.stdout)
        self.assertTrue(os.path.isfile(config_of(tree, "rules")))

    def test_workspace_tickets_are_not_mirrored(self):
        """2: ワークスペース自身の作業は、共通レイヤーと同じリポジトリにあるので写さない。"""
        tree, started = self.start_parent(project="")

        self.assertNotIn("ミラー", started.stdout)
        self.assertEqual(
            sorted(os.listdir(os.path.join(tree, ".ccnavi", "config", "common"))),
            sorted(os.listdir(os.path.join(self.ws, ".ccnavi", "config", "common"))),
        )

    def test_child_start_does_not_mirror(self):
        """2: 子は親のブランチに乗るので、子のワークツリーには写さない。"""
        self.propose(
            "i0001",
            ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE),
            project="lib",
        )
        self.propose(
            "i0001-01-01",
            ticket_text("i0001-01-01", project="lib", parent="i0001", phase=1, allow=SCOPE),
            project="lib",
        )
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        parent = self.worktree(self.lib, "i0001")
        self.assertEqual(self.ccnavi("ticket", "start", "i0001").returncode, 0)
        child = self.worktree(self.lib, "i0001-01-01")

        started = self.ccnavi("ticket", "start", "i0001-01-01")
        self.prompt()

        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertFalse(os.path.exists(mirror_of(child, "rules.yml")))
        # 親のワークツリーには、発言で写る。
        self.assertTrue(os.path.isfile(mirror_of(parent, "rules.yml")))

    def test_uncommitted_edits_in_the_mirror_are_overwritten(self):
        """3: ミラーは誰も編集しないので、未コミットの変更があっても止めない。"""
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")
        write(mirror_of(tree, "rules.yml"), "# 書きかけ\n")

        started = self.start_ticket("i0001")

        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertEqual(json.loads(read(mirror_of(tree, "rules.yml"))), COMMON_RULES)

    def test_no_notice_and_no_gate_at_close(self):
        """3: 上書きの知らせも、閉じるのを止める処理も無い。"""
        self.start_parent()

        # 計画のある親は `finish` が全フェーズの終了を求めるので、閉じられはしない。
        # 止める理由に、ミラーの上書きは出ない（計画の理由だけ）。
        closed = self.ccnavi("ticket", "finish", "i0001")

        self.assertEqual(closed.returncode, 1, closed.stdout)
        self.assertIn("終わっていないフェーズがある", closed.stderr)
        for word in ("ミラー", "上書き", "config-synced"):
            self.assertNotIn(word, closed.stdout + closed.stderr)
        refused = self.ccnavi("--config-synced", "i0001", stdin="y\n")
        self.assertNotEqual(refused.returncode, 0)

    def test_a_common_rule_using_root_is_mirrored(self):
        """2: `{root}` を使う共通ルールがあっても、親の着手が通りミラーされる。"""
        rooted = {"id": "workspace-root", "match": "Write|Edit", "regex": "^{root}[\\\\/]"}
        write(self.rules, json.dumps(dict(COMMON_RULES, allow=[rooted])))

        tree, _ = self.start_parent()

        self.assertEqual(
            json.loads(read(mirror_of(tree, "rules.yml"))),
            dict(COMMON_RULES, allow=[rooted]),
        )

    def test_a_common_layer_the_mirror_cannot_hold_is_reported_and_the_start_goes_on(self):
        """3: ミラーとして読めない共通レイヤーは写さず、理由を言う（着手は止めない）。"""
        write(self.risk, "version: 1\nfactors: [\n")
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")

        started = self.start_ticket("i0001")

        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertIn("ミラーとして読めない", started.stdout + started.stderr)
        self.assertFalse(os.path.exists(mirror_of(tree, "rules.yml")))

    def test_without_a_common_phases_file_the_mirror_has_none(self):
        """1: 共通レイヤーに phases.yml が無く config にだけあるとき、

        ミラーに phases.yml は置かず、着手は通る。

        前に配った phases.yml がミラーに残っていれば消す。config の phases.yml には触れない。
        """
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")
        write(mirror_of(tree, "phases.yml"), COMMON_PHASES)

        started = self.start_ticket("i0001")

        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertTrue(os.path.exists(mirror_of(tree, "rules.yml")))
        self.assertFalse(os.path.exists(mirror_of(tree, "phases.yml")))
        self.assertEqual(read(config_of(tree, settings.KIND_PHASES)), LIB_PHASES)

    def test_a_common_phases_file_is_mirrored(self):
        """1: 共通レイヤーの phases.yml もミラーへ写す。config の phases.yml には触れない。"""
        write(self.phases, COMMON_PHASES)
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")

        started = self.start_ticket("i0001")

        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertEqual(read(mirror_of(tree, "phases.yml")), COMMON_PHASES)
        self.assertEqual(read(config_of(tree, settings.KIND_PHASES)), LIB_PHASES)

    def test_a_common_phases_file_the_mirror_cannot_hold_is_reported_and_the_start_goes_on(self):
        """3: フェーズ定義として読めない共通レイヤーの phases.yml は写さず、
        理由を言う（着手は止めない）。"""
        write(self.phases, "version: 1\nphases: [\n")
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")

        started = self.start_ticket("i0001")

        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertIn("phases.yml", started.stdout + started.stderr)
        self.assertFalse(os.path.exists(mirror_of(tree, "rules.yml")))

    def test_a_script_the_risk_points_at_that_is_missing_is_reported_and_the_start_goes_on(self):
        """3: 配点が指すスクリプトが無ければ、配らず、理由を言う。"""
        write(self.risk, COMMON_SCRIPT_RISK)
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")

        started = self.start_ticket("i0001")

        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertIn("count.sh", started.stdout + started.stderr)
        self.assertFalse(os.path.exists(mirror_of(tree, "rules.yml")))

    def test_a_common_file_that_vanishes_after_listing_stops_the_plan(self):
        """3: 読む前に消えた共通レイヤーのスクリプトは、空として写さずに止める。"""
        conf = self.settings()
        self.add_script()
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")
        real = configsync._read_strict

        def vanished(path):
            if path.endswith("count.sh"):
                return None, ""
            return real(path)

        with mock.patch.object(configsync, "_read_strict", vanished):
            changes, why = configsync.plan(conf, self.ws, tree)

        self.assertEqual(changes, [])
        self.assertIn("を読めない (無い)", why)

    def symlink_or_skip(self, source, link):
        """リンクを張る。Windows で権限が無いなど、張れない環境ではテストを飛ばす。"""
        try:
            os.symlink(source, link)
        except (OSError, NotImplementedError) as error:
            privilege_not_held = getattr(error, "winerror", None) == 1314
            if not (privilege_not_held or isinstance(error, NotImplementedError)):
                raise
            self.skipTest(f"シンボリックリンクが作れない: {error}")

    def test_a_symlinked_target_is_reported_and_the_start_goes_on(self):
        """3: コピー先がシンボリックリンクなら、写さず、理由を言う（着手は止めない）。"""
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")
        target = mirror_of(tree, "rules.yml")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        self.symlink_or_skip(config_of(tree, "phases"), target)

        started = self.start_ticket("i0001")

        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertIn("シンボリックリンク", started.stdout + started.stderr)
        self.assertTrue(os.path.islink(target))

    def test_a_symlinked_mirror_directory_is_reported_and_the_start_goes_on(self):
        """3: ミラーの置き場そのものがリンクでも写さず、理由を言う。リンク先には何も書かない。"""
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")
        elsewhere = os.path.join(os.path.dirname(tree), "elsewhere")
        os.makedirs(elsewhere)
        os.makedirs(os.path.join(tree, ".ccnavi"), exist_ok=True)
        self.symlink_or_skip(elsewhere, os.path.join(tree, ".ccnavi", "config", "common"))

        started = self.start_ticket("i0001")

        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertEqual(os.listdir(elsewhere), [])

    def test_a_failed_write_puts_everything_back(self):
        """3: 1 本でも書けなければ、した分を元に戻す（書いた分も、消した分も）。"""
        conf = self.settings()
        write(mirror_of(self.lib, "scripts/old.sh"), "old\n")
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "--quiet", "-m", "old mirror")
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")
        changes, why = configsync.plan(conf, self.ws, tree)
        self.assertEqual(why, "")
        deleted = [c for c in changes if c.deletes]
        written = [c for c in changes if not c.deletes]
        self.assertTrue(deleted and len(written) >= 2)
        # 最後に書く 1 本の行き先をディレクトリにして、置き換えを失敗させる。
        ordered = [*written, *deleted]
        last = written[-1]
        os.makedirs(os.path.join(last.target, "x"))
        run = [*[c for c in changes if c is not last], last]

        failed = configsync.apply(run)

        self.assertIn("戻した", failed)
        self.assertEqual(read(mirror_of(tree, "scripts/old.sh")), "old\n")
        for c in written[:-1]:
            self.assertFalse(os.path.exists(c.target), c.rel)
        self.assertEqual(len(ordered), len(changes))

    def test_post_monitor_does_not_report_the_mirror(self):
        """4: 発言で写した分は受け入れの対象になり、繰り返し報告しない。

        セッションの最初の実行後チェックは、基準を取ったあとにあった変更を 1 度だけ伝える
        （ユーザが発言の前に直した保護領域と同じ扱い）。2 回目からは伝えない。
        """
        tree, _ = self.start_parent()
        self.hook("Bash", tree, event="PostToolUse", command="ls")

        said = self.hook("Bash", tree, event="PostToolUse", command="ls")

        self.assertNotIn(".ccnavi/config/common", said.stdout + said.stderr)

    def test_post_monitor_reports_an_edit_after_the_mirror(self):
        """5: ミラーの後に手を入れて共通レイヤーと違う中身にしたものは、今までどおり報告する。"""
        tree, _ = self.start_parent()
        write(mirror_of(tree, "risks.yml"), COMMON_RISK + "# edited\n")

        said = self.hook("Bash", tree, event="PostToolUse", command="ls")

        self.assertIn("risks.yml", said.stdout + said.stderr)

    def test_post_monitor_reports_a_config_edit(self):
        """5: config の書き込みは、ミラーと中身が同じでも外さない。"""
        tree, _ = self.start_parent()
        write(config_of(tree, "rules"), read(self.rules))

        said = self.hook("Bash", tree, event="PostToolUse", command="ls")

        self.assertIn("rules.yml", said.stdout + said.stderr)

    def test_core_file_guard_keeps_the_mirror(self):
        """4: 発言で写した分は戻さない。そのあと、ツール呼び出しの中で書き換わったら戻す。"""
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        tree = self.worktree(self.lib, "i0001")
        git(tree, "add", "-A")
        command = "sh .ccnavi/scripts/ccnavi-ticket.sh start i0001"

        self.hook("Bash", tree, command=command, guard="enable")
        started = self.ccnavi("ticket", "start", "i0001")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.hook("Bash", tree, event="PostToolUse", command=command, guard="enable")
        self.assertFalse(os.path.exists(mirror_of(tree, "risks.yml")))

        self.prompt(guard="enable")
        self.assertEqual(read(mirror_of(tree, "risks.yml")), COMMON_RISK)
        self.hook("Bash", tree, command="echo y", guard="enable")
        self.hook("Bash", tree, event="PostToolUse", command="echo y", guard="enable")
        self.assertEqual(read(mirror_of(tree, "risks.yml")), COMMON_RISK)

        self.hook("Bash", tree, command="echo x", guard="enable")
        write(mirror_of(tree, "risks.yml"), "version: 1\n")
        self.hook("Bash", tree, event="PostToolUse", command="echo x", guard="enable")
        self.assertEqual(read(mirror_of(tree, "risks.yml")), COMMON_RISK)

    def test_core_file_guard_keeps_a_removed_mirror_file(self):
        """4: 共通レイヤーから無くなって発言で消したミラーも、戻さない。"""
        write(mirror_of(self.lib, "rules.yml"), json.dumps(COMMON_RULES))
        write(mirror_of(self.lib, "phases.yml"), COMMON_PHASES)
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "--quiet", "-m", "old mirror")
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")
        command = "sh .ccnavi/scripts/ccnavi-ticket.sh start i0001"

        self.hook("Bash", tree, command=command, guard="enable")
        started = self.ccnavi("ticket", "start", "i0001")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.hook("Bash", tree, event="PostToolUse", command=command, guard="enable")
        self.prompt(guard="enable")
        self.assertFalse(os.path.exists(mirror_of(tree, "phases.yml")))

        self.hook("Bash", tree, command="echo x", guard="enable")
        self.hook("Bash", tree, event="PostToolUse", command="echo x", guard="enable")

        self.assertFalse(os.path.exists(mirror_of(tree, "phases.yml")))

    def test_a_non_utf8_script_is_mirrored_byte_for_byte(self):
        """1: UTF-8 でないスクリプトも、バイト列のまま写す（改行や文字の読み替えをしない）。"""
        write(self.risk, COMMON_SCRIPT_RISK)
        script = os.path.join(self.ws, ".ccnavi", "config", "common", "scripts", "count.sh")
        os.makedirs(os.path.dirname(script), exist_ok=True)
        raw = b"# \x82\xa0\rprintf 1\n"
        with open(script, "wb") as f:
            f.write(raw)

        tree, _ = self.start_parent()

        with open(mirror_of(tree, "scripts/count.sh"), "rb") as f:
            self.assertEqual(f.read(), raw)

    def test_a_mirrored_script_factor_is_not_counted_twice(self):
        """ミラーが統合先に入っても、共通レイヤーの同じ項目と 2 重に数えない。

        ワークスペースの中ではミラーを読まない（共通レイヤーは 1 本）ので、そもそも数が増えない。
        """
        self.add_script()
        tree, _ = self.start_parent()
        for rel in (".ccnavi/config/common/risks.yml", ".ccnavi/config/common/scripts/count.sh"):
            write(
                os.path.join(self.lib, *rel.split("/")), read(os.path.join(tree, *rel.split("/")))
            )

        definition, problems = risk.layer_definition(self.settings(), self.ws, "lib")

        ids = [f.id for f in definition.factors]
        self.assertEqual(ids.count("counted"), 1, ids)
        self.assertFalse([p for p in problems if p.severity == "warn"], problems)

    # 発言ごとの同期（差分が無ければ読み書きしない）

    def test_a_prompt_with_nothing_to_sync_neither_reads_nor_writes(self):
        """5: 元も先も動いていなければ、検証つきの比べ直しも書き込みもしない。"""
        tree, _ = self.start_parent()
        target = mirror_of(tree, "rules.yml")
        before = os.stat(target).st_mtime_ns

        with mock.patch.object(configsync, "plan", side_effect=AssertionError("比べ直した")):
            lines = promptsync.sync(self.settings(), self.ws)

        self.assertEqual(lines, [])
        self.assertEqual(os.stat(target).st_mtime_ns, before)

    def test_a_change_in_the_common_layer_reaches_the_mirror_at_the_next_prompt(self):
        """5: ターンの間にユーザが共通レイヤーを直すと、次の発言で写り、戻されない。"""
        tree, _ = self.start_parent()
        self.prompt(guard="enable")
        write(self.risk, COMMON_RISK + "# 直した\n")

        said = self.prompt(guard="enable")

        self.assertEqual(read(mirror_of(tree, "risks.yml")), COMMON_RISK + "# 直した\n")
        self.assertIn("risks.yml", self.reason(said))
        self.hook("Bash", tree, command="echo x", guard="enable")
        self.hook("Bash", tree, event="PostToolUse", command="echo x", guard="enable")
        self.assertEqual(read(mirror_of(tree, "risks.yml")), COMMON_RISK + "# 直した\n")

    def test_an_edit_of_the_mirror_between_turns_is_overwritten_at_the_next_prompt(self):
        """5: ターンの間にミラーが手で直されていれば、元が動いていなくても比べ直して揃える。"""
        tree, _ = self.start_parent()
        write(mirror_of(tree, "risks.yml"), "version: 1\n")

        self.prompt()

        self.assertEqual(read(mirror_of(tree, "risks.yml")), COMMON_RISK)

    def test_a_failed_sync_is_said_every_time_and_not_remembered(self):
        """3: 写せなかった理由は発言のたびに言い、直ったらその発言で写す。"""
        write(self.risk, "version: 1\nfactors: [\n")
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")
        self.assertEqual(self.ccnavi("ticket", "start", "i0001").returncode, 0)

        first = self.reason(self.prompt())
        second = self.reason(self.prompt())

        self.assertIn("ミラーとして読めない", first)
        self.assertIn("ミラーとして読めない", second)
        self.assertFalse(os.path.exists(mirror_of(tree, "rules.yml")))
        write(self.risk, COMMON_RISK)
        self.prompt()
        self.assertEqual(read(mirror_of(tree, "risks.yml")), COMMON_RISK)

    def test_an_unstarted_parent_is_not_mirrored(self):
        """2: 着手していない親のワークツリーには写さない。"""
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")

        self.prompt()

        self.assertFalse(os.path.exists(mirror_of(tree, "rules.yml")))

    def test_a_worktree_without_an_approved_parent_is_not_mirrored(self):
        """2: 承認済みの親の無いワークツリーには写さない。"""
        tree = self.worktree(self.lib, "scratchy")

        self.prompt()

        self.assertFalse(os.path.exists(mirror_of(tree, "rules.yml")))

    def test_parallel_sessions_do_not_disturb_each_other(self):
        """5: 並行するセッションの発言は、同じ内容を書くだけで、互いの結果を壊さない。"""
        tree, _ = self.start_parent()
        write(self.risk, COMMON_RISK + "# 直した\n")

        self.prompt(session="s2")
        again = self.prompt(session="s1")

        self.assertEqual(read(mirror_of(tree, "risks.yml")), COMMON_RISK + "# 直した\n")
        self.assertNotIn("risks.yml", self.reason(again))

    def test_lint_does_not_name_the_mirror_as_a_worktree_only_file(self):
        """ミラーはワークスペースの中では読まれないので、「ワークツリーにしかない」とは言わない。"""
        self.start_parent()

        warns = self.problems("warn")

        self.assertFalse([p for p in warns if ".ccnavi/config/common" in p["detail"]], warns)

    def test_the_mirror_in_the_original_project_is_restored(self):
        """元のプロジェクトの `.ccnavi/config/common/` のミラーも、守る対象（書き換えは戻す）。"""
        mirror = mirror_of(self.lib, "rules.yml")
        write(mirror, json.dumps(COMMON_RULES))
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "--quiet", "-m", "mirror")

        self.hook("Bash", self.ws, command="echo x", guard="enable")
        write(mirror, "version: 1\n")
        self.hook("Bash", self.ws, event="PostToolUse", command="echo x", guard="enable")

        self.assertEqual(json.loads(read(mirror)), COMMON_RULES)
