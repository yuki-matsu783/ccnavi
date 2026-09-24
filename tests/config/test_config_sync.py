"""着手の前に共通層でプロジェクトの層を上書きする（設計 §11.12、ADR-0084）。

fixture は tests/config/test_config_union.py の ConfigUnionHarness を継ぐ。共通層は
rules / phases / risks の 3 本を持ち、lib はそれぞれ別の中身を持つ。app は層を持たない。

見るのは 4 つ。

1. 親の `ticket start` が、共通層にあるファイルだけを親のワークツリーへ写し、印を置く
2. 子の着手とワークスペース自身の作業では写さない
3. 最初のレビューの依頼の頭に載り、2 回目からは載らない
4. 写した書き込みを、実行後の監視と控えと復元が戻さず、報告もしない。写した後に
   手を入れたものは今までどおり扱う
"""

from __future__ import annotations

import json
import os

from ccnavi import configsync, settings
from tests.config.test_config_union import (
    COMMON_PHASES,
    COMMON_RISK,
    COMMON_RULES,
    ConfigUnionHarness,
    git,
    read,
    ticket_text,
    write,
)

SCOPE = ("src/*",)


def config_of(tree_root, kind):
    return os.path.join(tree_root, ".ccnavi", "config", settings.LAYER_FILE_NAMES[kind])


class ConfigSyncTest(ConfigUnionHarness):
    def start_parent(self, project="lib", name="i0001"):
        """project 向けの親を承認し、そのプロジェクトからワークツリーを切って着手する。"""
        owner = os.path.join(self.projects, project) if project else self.ws
        self.propose(
            name,
            ticket_text(name, project=project, allow=SCOPE),
            project=project,
        )
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        tree = self.worktree(owner, name)
        started = self.ccnavi("ticket", "start", name)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        return tree, started

    def mark(self, name="i0001", project="lib"):
        path = self.approved_path("phases", name, "config-sync.json", project=project)
        return json.loads(read(path)) if os.path.exists(path) else None

    def test_parent_start_overwrites_the_project_layer_with_the_common_layer(self):
        """1: 共通層の 3 本で、親のワークツリーの 3 本を上書きする。消えた識別子を名指しする。"""
        tree, started = self.start_parent()

        self.assertEqual(json.loads(read(config_of(tree, "rules"))), COMMON_RULES)
        self.assertEqual(read(config_of(tree, "phases")), COMMON_PHASES)
        self.assertEqual(read(config_of(tree, "risk")), COMMON_RISK)
        # 元リポジトリは触らない。判定が読むのはそちらで、上書きは親のブランチに乗って届く。
        self.assertNotEqual(read(config_of(self.lib, "phases")), COMMON_PHASES)

        self.assertIn("共通層で上書きした", started.stdout)
        # lib の phases の build / release は共通層に無いので、上書きで消える。
        self.assertIn("build", started.stdout)
        self.assertIn("release", started.stdout)
        mark = self.mark()
        by_path = {f["path"]: f for f in mark["files"]}
        self.assertEqual(
            sorted(by_path),
            [".ccnavi/config/phases.yml", ".ccnavi/config/risks.yml", ".ccnavi/config/rules.yml"],
        )
        self.assertEqual(by_path[".ccnavi/config/phases.yml"]["lost"], ["build", "release"])
        self.assertEqual(mark["notified"], "")

    def test_files_missing_from_the_common_layer_are_left_alone(self):
        """1: 共通層に無いファイルは、プロジェクトの側を消さずに残す。"""
        os.remove(self.phases)
        before = read(config_of(self.lib, "phases"))

        tree, _ = self.start_parent()

        self.assertEqual(read(config_of(tree, "phases")), before)
        paths = [f["path"] for f in self.mark()["files"]]
        self.assertNotIn(".ccnavi/config/phases.yml", paths)

    def test_a_project_without_a_layer_gets_new_files(self):
        """1: 層を持たないプロジェクトには、新しく置く。"""
        tree, started = self.start_parent(project="app")

        self.assertEqual(read(config_of(tree, "risk")), COMMON_RISK)
        self.assertIn("新しく置いた", started.stdout)
        self.assertFalse(any(f["existed"] for f in self.mark(project="app")["files"]))

    def test_identical_layers_are_not_touched(self):
        """1: 中身が同じなら何も書かず、印も置かない。改行の違いは見ない。"""
        for kind, text in (
            ("rules", json.dumps(COMMON_RULES)),
            ("phases", COMMON_PHASES.replace("\n", "\r\n")),
            ("risk", COMMON_RISK),
        ):
            write(config_of(self.lib, kind), text)
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "--quiet", "-m", "same")

        _, started = self.start_parent()

        self.assertNotIn("共通層で上書きした", started.stdout)
        self.assertIsNone(self.mark())

    def test_workspace_tickets_are_not_synced(self):
        """2: ワークスペース自身の作業は、共通層と同じリポジトリにあるので比べない。"""
        tree, started = self.start_parent(project="")

        self.assertNotIn("共通層で上書きした", started.stdout)
        self.assertIsNone(self.mark(project=""))
        self.assertNotEqual(read(config_of(tree, "phases")), COMMON_PHASES)

    def test_child_start_does_not_sync(self):
        """2: 子は親のブランチに乗るので、子の着手では比べない。"""
        self.propose(
            "i0001",
            ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE),
            project="lib",
        )
        self.propose(
            "i0001-01",
            ticket_text("i0001-01", project="lib", parent="i0001", phase=1, allow=SCOPE),
            project="lib",
        )
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.worktree(self.lib, "i0001")
        self.assertEqual(self.ccnavi("ticket", "start", "i0001").returncode, 0)
        child = self.worktree(self.lib, "i0001-01")

        started = self.ccnavi("ticket", "start", "i0001-01")

        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertNotIn("共通層で上書きした", started.stdout)
        self.assertNotEqual(read(config_of(child, "phases")), COMMON_PHASES)

    def test_the_first_review_carries_the_notice_once(self):
        """3: 最初の依頼の頭に載り、知らせたことを印に残すと、以後は載らない。"""
        self.start_parent()
        home = self.approved_dir_of("lib")

        mark = configsync.pending(home, "i0001")
        self.assertIsNotNone(mark)
        text = configsync.notice(mark)
        self.assertIn("プロジェクトの設定が変わった", text)
        self.assertIn(".ccnavi/config/rules.yml", text)
        self.assertIn("`build`", text)

        self.assertEqual(configsync.mark_notified(home, "i0001", "https://example/mr/1"), "")
        self.assertIsNone(configsync.pending(home, "i0001"))
        self.assertEqual(self.mark()["notified"], "https://example/mr/1")

    def test_post_monitor_does_not_report_the_synced_files(self):
        """4: 範囲の外（`.ccnavi/config/`）への書き込みでも、写した分は報告しない。"""
        tree, _ = self.start_parent()

        said = self.hook("Bash", tree, event="PostToolUse", command="ls")

        self.assertNotIn(".ccnavi/config", said.stdout + said.stderr)

    def test_post_monitor_reports_an_edit_after_the_sync(self):
        """4: 写した後に手を入れたものは、印の指紋と合わないので今までどおり報告する。"""
        tree, _ = self.start_parent()
        write(
            config_of(tree, "risk"),
            COMMON_RISK + "  - {id: x, points: 1, files_over: 0, message: x}\n",
        )

        said = self.hook("Bash", tree, event="PostToolUse", command="ls")

        self.assertIn("risks.yml", said.stdout + said.stderr)

    def test_is_synced_write_needs_both_the_content_and_the_mark(self):
        """4: 共通層と同じ中身でも、印が名指ししていなければ外さない。"""
        conf = self.settings()
        tree, _ = self.start_parent()
        target = config_of(tree, "rules")
        self.assertTrue(configsync.is_synced_write(conf, self.ws, target))

        # 元リポジトリの設定は判定が読む版なので、同じ中身と印が揃っても外さない。
        main = config_of(self.lib, "rules")
        write(main, read(target))
        self.assertFalse(configsync.is_synced_write(conf, self.ws, main))

        os.remove(self.approved_path("phases", "i0001", "config-sync.json", project="lib"))
        self.assertFalse(configsync.is_synced_write(conf, self.ws, target))

    def test_core_file_guard_keeps_the_synced_files(self):
        """4: 控えと復元は、写した分を戻さない。写した後の書き換えは戻す。"""
        self.propose("i0001", ticket_text("i0001", project="lib", allow=SCOPE), project="lib")
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        tree = self.worktree(self.lib, "i0001")
        git(tree, "add", "-A")
        command = "sh .ccnavi/scripts/ccnavi-ticket.sh start i0001"

        self.hook("Bash", tree, command=command, guard="enable")
        started = self.ccnavi("ticket", "start", "i0001")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.hook("Bash", tree, event="PostToolUse", command=command, guard="enable")
        self.assertEqual(read(config_of(tree, "risk")), COMMON_RISK)

        self.hook("Bash", tree, command="echo x", guard="enable")
        write(config_of(tree, "risk"), "version: 1\n")
        self.hook("Bash", tree, event="PostToolUse", command="echo x", guard="enable")
        self.assertEqual(read(config_of(tree, "risk")), COMMON_RISK)

    def settings(self):
        conf, _ = settings.load(self.ws)
        return conf
