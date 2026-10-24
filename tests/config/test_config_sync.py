"""着手の前に共通レイヤーをプロジェクトの `.ccnavi/config/common/` へミラーする。

共通レイヤー（ワークスペースルートの `.ccnavi/config/common/`）はワークスペースの git にあり、
プロジェクトだけを clone したユーザからは見えない。プロジェクト向けの親の `ticket start` で、
共通レイヤーの中身を、親のワークツリーにあるプロジェクトの `.ccnavi/config/common/` へコピーする。
プロジェクトの `.ccnavi/config/project/` には触れない。

fixture は tests/config/test_config_union.py の ConfigUnionHarness を継ぐ。共通レイヤーは
rules / risks の 2 本（と、期待とスクリプト）を持ち、lib の config は 3 本とも別の中身を持つ。
app は `.ccnavi/` を持たない。

見るのは 5 つ。

1. 親の `ticket start` が、共通レイヤーの中身を親のワークツリーのミラーへコピーし、
config には触れない。
   共通レイヤーから無くなったファイルはミラーからも消す。`script:` の値は書き換えない
2. 子の着手とワークスペース自身の作業ではコピーしない
3. コピーできない形（読めない・リンク・書き込みの失敗）では何もコピーせず、着手しない。
   error のあるテンプレートだけは1本ずつコピーせずに飛ばし、着手は止めない。
   上書きの記録・知らせ・閉じるのを止める処理は無い
4. ミラーの書き込みを、実行後チェックとバックアップと復元が戻さず、報告もしない。条件は 3 つ
   （承認済みの親のワークツリーの
   `.ccnavi/config/common/`・途中にリンクが無い・内容が共通レイヤーと同一）
5. 条件を外れるもの（config、中身の違うミラー、他のワークツリー、リンク）は今までどおり扱う
"""

from __future__ import annotations

import json
import os
import re
from unittest import mock

from ccnavi.infra import settings
from ccnavi.tickets import configsync, risk
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


class ConfigMirrorTest(ConfigUnionHarness):
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
        started = self.ccnavi("ticket", "start", name)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        return tree, started

    def settings(self):
        conf, _ = settings.load(self.ws)
        return conf

    def add_script(self):
        write(self.risk, COMMON_SCRIPT_RISK)
        write(os.path.join(self.ws, ".ccnavi", "config", "common", "scripts", "count.sh"), COUNT_SH)

    def test_parent_start_mirrors_the_common_layer_and_leaves_config_alone(self):
        """1: 共通レイヤーの中身を親のワークツリーの `.ccnavi/config/common/` へコピーする。config
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

    def test_the_start_write_and_push_carries_the_mirror(self):
        """write-and-push の書いたパスの一覧（基点は親のワークツリー）で、
        ミラーの書き込みは外でも通す。"""
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
        listed = read(target).splitlines()
        for rel in (
            ".ccnavi/config/common/rules.yml",
            ".ccnavi/config/common/risks.yml",
            ".ccnavi/config/common/scripts/count.sh",
        ):
            self.assertIn(rel, listed)
        self.assertNotIn("置き場の外", started.stderr)

    def test_the_risk_script_is_mirrored_without_rewriting_the_value(self):
        """1: 配点が指すスクリプトもミラーする。`script:` の値は共通レイヤーと同じ表記のまま。"""
        conf = self.settings()
        self.add_script()

        tree, started = self.start_parent()

        self.assertEqual(read(mirror_of(tree, "scripts/count.sh")), COUNT_SH)
        self.assertIn(
            "script: .ccnavi/config/common/scripts/count.sh", read(mirror_of(tree, "risks.yml"))
        )
        self.assertIn(".ccnavi/config/common/scripts/count.sh", started.stdout)
        self.assertTrue(
            configsync.is_synced_write(conf, self.ws, mirror_of(tree, "scripts/count.sh"))
        )

    def add_template(self, name="feature", text=FEATURE_TEMPLATE):
        directory = os.path.join(self.ws, ".ccnavi", "config", "common", "templates")
        return write(os.path.join(directory, name + ".yml"), text)

    def test_a_common_template_is_mirrored_and_kept_by_the_next_plan(self):
        """1: 共通のテンプレートもコピーする。ミラーに置いたものが次の着手で消されない。"""
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
        # `.yml` でないものと、`.` で始まるものは、テンプレートとして読まれないのでコピーしない。
        self.assertFalse(os.path.exists(mirror_of(tree, "templates/notes.txt")))
        self.assertFalse(os.path.exists(mirror_of(tree, "templates/.hidden.yml")))
        self.assertTrue(
            configsync.is_synced_write(conf, self.ws, mirror_of(tree, "templates/feature.yml"))
        )
        # 中身が違えば外さない（サブディレクトリでも判定は変わらない）。
        self.assertFalse(
            configsync.is_synced_write(
                conf, self.ws, mirror_of(tree, "templates/feature.yml"), b"name: feature\n"
            )
        )
        # コピー終えたあとの次の計画は、何も変えない（ミラーに在るテンプレートを消さない）。
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
        """3: error のあるテンプレートはコピーせずに飛ばす。そのために着手は止めない。"""
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
        """3: 定義に無い型は warn にとどまる（定義は後から変わる）。止めずにコピーする。"""
        self.add_template(text=FEATURE_TEMPLATE.replace("design", "nothing-like-it"))

        tree, _ = self.start_parent()

        self.assertTrue(os.path.isfile(mirror_of(tree, "templates/feature.yml")))

    def test_a_symlinked_templates_place_skips_only_the_templates(self):
        """3: 共通のテンプレートの置き場がリンクなら、テンプレートだけコピーせず、
        着手は止まらない。"""
        real = os.path.join(self.ws, "elsewhere")
        write(os.path.join(real, "feature.yml"), FEATURE_TEMPLATE)
        link = os.path.join(self.ws, ".ccnavi", "config", "common", "templates")
        self.symlink_or_skip(real, link)
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")

        started = self.ccnavi("ticket", "start", "i0001")

        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertFalse(os.path.exists(mirror_of(tree, "templates/feature.yml")))
        # ほかの設定のミラーは写る。
        self.assertTrue(os.path.isfile(mirror_of(tree, "rules.yml")))

    def test_a_script_shared_by_two_factors_is_mirrored_once(self):
        """1: 2 つの項目が同じスクリプトを指しても、コピーするのは 1 回。"""
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
        """2: ワークスペース自身の作業は、共通レイヤーと同じリポジトリにあるのでコピーしない。"""
        tree, started = self.start_parent(project="")

        self.assertNotIn("ミラー", started.stdout)
        self.assertEqual(
            sorted(os.listdir(os.path.join(tree, ".ccnavi", "config", "common"))),
            sorted(os.listdir(os.path.join(self.ws, ".ccnavi", "config", "common"))),
        )

    def test_child_start_does_not_mirror(self):
        """2: 子は親のブランチに乗るので、子の着手ではコピーしない。"""
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
        self.worktree(self.lib, "i0001")
        self.assertEqual(self.ccnavi("ticket", "start", "i0001").returncode, 0)
        child = self.worktree(self.lib, "i0001-01-01")

        started = self.ccnavi("ticket", "start", "i0001-01-01")

        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertNotIn("ミラー", started.stdout)
        self.assertFalse(os.path.exists(mirror_of(child, "rules.yml")))

    def test_uncommitted_edits_in_the_mirror_are_overwritten(self):
        """3: ミラーは誰も編集しないので、未コミットの変更があっても止めない。"""
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")
        write(mirror_of(tree, "rules.yml"), "# 書きかけ\n")

        started = self.ccnavi("ticket", "start", "i0001")

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

    def test_a_common_layer_the_mirror_cannot_hold_stops_the_start(self):
        """3: ミラーとして読めない共通レイヤーはコピーせず、着手しない。"""
        write(self.risk, "version: 1\nfactors: [\n")
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")

        started = self.ccnavi("ticket", "start", "i0001")

        self.assertNotEqual(started.returncode, 0, started.stdout)
        self.assertIn("ミラーとして読めない", started.stderr)
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

        started = self.ccnavi("ticket", "start", "i0001")

        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertTrue(os.path.exists(mirror_of(tree, "rules.yml")))
        self.assertFalse(os.path.exists(mirror_of(tree, "phases.yml")))
        self.assertEqual(read(config_of(tree, settings.KIND_PHASES)), LIB_PHASES)

    def test_a_common_phases_file_is_mirrored(self):
        """1: 共通レイヤーの phases.yml もミラーへコピーする。config の phases.yml には触れない。"""
        write(self.phases, COMMON_PHASES)
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")

        started = self.ccnavi("ticket", "start", "i0001")

        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertEqual(read(mirror_of(tree, "phases.yml")), COMMON_PHASES)
        self.assertEqual(read(config_of(tree, settings.KIND_PHASES)), LIB_PHASES)

    def test_a_common_phases_file_the_mirror_cannot_hold_stops_the_start(self):
        """3: フェーズ定義として読めない共通レイヤーの phases.yml はコピーせず、着手しない。"""
        write(self.phases, "version: 1\nphases: [\n")
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")

        started = self.ccnavi("ticket", "start", "i0001")

        self.assertNotEqual(started.returncode, 0, started.stdout)
        self.assertIn("phases.yml", started.stderr)
        self.assertFalse(os.path.exists(mirror_of(tree, "rules.yml")))

    def test_a_script_the_risk_points_at_that_is_missing_stops_the_start(self):
        """3: 配点が指すスクリプトが無ければ、配らずに止める。"""
        write(self.risk, COMMON_SCRIPT_RISK)
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")

        started = self.ccnavi("ticket", "start", "i0001")

        self.assertNotEqual(started.returncode, 0, started.stdout)
        self.assertIn("count.sh", started.stderr)
        self.assertFalse(os.path.exists(mirror_of(tree, "rules.yml")))

    def test_a_common_file_that_vanishes_after_listing_stops_the_plan(self):
        """3: 読む前に消えた共通レイヤーのスクリプトは、空としてコピーせずに止める。"""
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

    def test_a_symlinked_target_stops_the_start(self):
        """3: コピー先がシンボリックリンクなら、コピーせずに着手を止める。"""
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")
        target = mirror_of(tree, "rules.yml")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        self.symlink_or_skip(config_of(tree, "phases"), target)

        started = self.ccnavi("ticket", "start", "i0001")

        self.assertNotEqual(started.returncode, 0, started.stdout)
        self.assertIn("シンボリックリンク", started.stderr)
        self.assertTrue(os.path.islink(target))

    def test_a_symlinked_mirror_directory_stops_the_start(self):
        """3: ミラーの置き場そのものがリンクでも止める。リンク先には何も書かない。"""
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")
        elsewhere = os.path.join(os.path.dirname(tree), "elsewhere")
        os.makedirs(elsewhere)
        os.makedirs(os.path.join(tree, ".ccnavi"), exist_ok=True)
        self.symlink_or_skip(elsewhere, os.path.join(tree, ".ccnavi", "config", "common"))

        started = self.ccnavi("ticket", "start", "i0001")

        self.assertNotEqual(started.returncode, 0, started.stdout)
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
        """4: 範囲の外（`.ccnavi/config/common/`）への書き込みでも、ミラーした分は報告しない。"""
        tree, _ = self.start_parent()

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

    def test_is_synced_write_needs_the_place_and_the_content(self):
        """4・5: 3 つの条件のうち、置き場と中身を見る。"""
        conf = self.settings()
        tree, _ = self.start_parent()
        target = mirror_of(tree, "rules.yml")
        self.assertTrue(configsync.is_synced_write(conf, self.ws, target))

        # 中身が共通レイヤーと違えば外さない。
        self.assertFalse(configsync.is_synced_write(conf, self.ws, target, b"x: 1\n"))
        # 消えたことは、共通レイヤーにも無いときだけ外す。
        self.assertFalse(configsync.is_synced_write(conf, self.ws, target, None))
        self.assertTrue(
            configsync.is_synced_write(conf, self.ws, mirror_of(tree, "phases.yml"), None)
        )
        # config は同じ中身でも外さない。
        config = config_of(tree, "rules")
        write(config, read(target))
        self.assertFalse(configsync.is_synced_write(conf, self.ws, config))
        # 元リポジトリのミラーは判定が読む版（単体 clone では共通レイヤー）なので、外さない。
        main = mirror_of(self.lib, "rules.yml")
        write(main, read(target))
        self.assertFalse(configsync.is_synced_write(conf, self.ws, main))

    def test_a_worktree_without_an_approved_parent_is_not_exempt(self):
        """4: 承認済みの親チケットの無いワークツリーは、共通レイヤーと同じ中身でも外さない。"""
        conf = self.settings()
        tree = self.worktree(self.lib, "scratchy")
        target = mirror_of(tree, "rules.yml")
        write(target, read(self.rules))

        self.assertFalse(configsync.is_synced_write(conf, self.ws, target))

    def test_another_project_parent_is_not_exempt(self):
        """4: 承認済みの親でも、その `project:` が違うプロジェクトのワークツリーは外さない。"""
        conf = self.settings()
        self.start_parent()
        self.propose("i0002", ticket_text("i0002", project="app", allow=SCOPE), project="app")
        self.assertEqual(self.approve().returncode, 0)
        # 親の i0002 は app のワークツリー。同じ名前で lib から切ったツリーに置き換える。
        git(self.app, "worktree", "remove", "--force", self.worktree(self.app, "i0002"))
        other = self.worktree(self.lib, "i0002")
        target = mirror_of(other, "rules.yml")
        write(target, read(self.rules))

        self.assertFalse(configsync.is_synced_write(conf, self.ws, target))

    def test_a_child_worktree_is_not_exempt(self):
        """4: 子のワークツリーはミラーしないので、共通レイヤーの中身を書く書き込みも外さない。"""
        conf = self.settings()
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
        self.assertEqual(self.approve().returncode, 0)
        self.worktree(self.lib, "i0001")
        self.assertEqual(self.ccnavi("ticket", "start", "i0001").returncode, 0)
        child = self.worktree(self.lib, "i0001-01-01")
        target = mirror_of(child, "rules.yml")
        write(target, read(self.rules))

        self.assertFalse(configsync.is_synced_write(conf, self.ws, target))

    def test_a_symlink_is_not_exempt(self):
        """4: ミラーのファイルを別のコピーへのシンボリックリンクに差し替えても外さない。"""
        conf = self.settings()
        tree, _ = self.start_parent()
        target = mirror_of(tree, "rules.yml")
        os.remove(target)
        self.symlink_or_skip("risks.yml", target)

        self.assertFalse(configsync.is_synced_write(conf, self.ws, target))
        said = self.hook("Bash", tree, event="PostToolUse", command="ls")
        self.assertIn("rules.yml", said.stdout + said.stderr)

    def test_core_file_guard_keeps_the_mirror(self):
        """4: バックアップと復元は、ミラーした分を戻さない。ミラーの後の書き換えは戻す。"""
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
        self.assertEqual(read(mirror_of(tree, "risks.yml")), COMMON_RISK)

        self.hook("Bash", tree, command="echo x", guard="enable")
        write(mirror_of(tree, "risks.yml"), "version: 1\n")
        self.hook("Bash", tree, event="PostToolUse", command="echo x", guard="enable")
        self.assertEqual(read(mirror_of(tree, "risks.yml")), COMMON_RISK)

    def test_core_file_guard_keeps_a_removed_mirror_file(self):
        """4: 共通レイヤーから無くなって消えたミラーも、戻さない。"""
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

        self.assertFalse(os.path.exists(mirror_of(tree, "phases.yml")))

    def test_committed_content_decides_at_the_end_of_the_turn(self):
        """4: コミットした中身で答える。好きな中身でコミットしてディスクだけ戻しても外れない。"""
        tree, _ = self.start_parent()
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "mirror")
        self.assertEqual(self.hook("", self.ws, event="UserPromptSubmit").returncode, 0)
        target = mirror_of(tree, "rules.yml")
        synced = read(target)
        write(target, "version: 1\nallow:\n  - {id: all, match: Write, glob: '*'}\n")
        git(tree, "commit", "--quiet", "-am", "loosen")
        write(target, synced)

        stopped = self.hook("", self.ws, event="Stop")

        self.assertIn(".ccnavi/config/common/rules.yml", self.system_message(stopped))

    def test_the_mirror_commit_itself_is_not_reported_at_the_end_of_the_turn(self):
        """4: ミラーした分をコミットしても、ターンの終わりに違反として並ばない。"""
        self.assertEqual(self.hook("", self.ws, event="UserPromptSubmit").returncode, 0)
        tree, _ = self.start_parent()
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "mirror")

        stopped = self.hook("", self.ws, event="Stop")

        # 違反の一覧は「<ツリー>: <パス>」の行で並ぶ。文面の `対象のルール` には
        # 共通レイヤーの記録名が出るので、行の頭で分ける。
        self.assertIsNone(
            re.search(r"(?m)^\s*\S+: \.ccnavi/config/common/", self.system_message(stopped))
        )

    def test_a_non_utf8_script_is_not_reported_after_it_is_committed(self):
        """4: コミット分はバイト列のまま比べる。UTF-8 でないスクリプトもミラーした分に数える。"""
        write(self.risk, COMMON_SCRIPT_RISK)
        script = os.path.join(self.ws, ".ccnavi", "config", "common", "scripts", "count.sh")
        os.makedirs(os.path.dirname(script), exist_ok=True)
        with open(script, "wb") as f:
            f.write(b"# \x82\xa0\rprintf 1\n")
        self.assertEqual(self.hook("", self.ws, event="UserPromptSubmit").returncode, 0)
        tree, _ = self.start_parent()
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "mirror")

        stopped = self.hook("", self.ws, event="Stop")

        self.assertNotIn("count.sh", self.system_message(stopped))

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
