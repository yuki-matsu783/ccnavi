"""着手の前に共通層でプロジェクトの層を上書きする（設計 11.12、ADR-0084）。

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

from ccnavi import configsync, ops, phase, risk, settings
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


COMMON_SCRIPT_RISK = (
    COMMON_RISK
    + "  - {id: counted, points: 5, script: .ccnavi/common/scripts/count.sh, message: 数えた}\n"
)
COUNT_SH = "printf '{\"points\": 1}'\n"


class ConfigSyncBoundaryTest(ConfigSyncTest):
    """レビューで見つかった穴を塞いだことを見る。"""

    def test_a_mark_of_another_project_does_not_exempt(self):
        """印はその親のもの。lib で写したあとでも、app のワークツリーの書き込みは外さない。"""
        conf = self.settings()
        self.start_parent()
        self.propose("i0002", ticket_text("i0002", project="app", allow=SCOPE), project="app")
        self.assertEqual(self.approve().returncode, 0)
        other = self.worktree(self.app, "i0002")
        target = config_of(other, "rules")
        write(target, read(self.rules))

        self.assertFalse(configsync.is_synced_write(conf, self.ws, target))

    def test_a_child_worktree_is_not_exempt(self):
        """子のワークツリーは上書きしないので、共通層の中身へ戻す書き込みも外さない。"""
        conf = self.settings()
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
        self.assertEqual(self.approve().returncode, 0)
        self.worktree(self.lib, "i0001")
        self.assertEqual(self.ccnavi("ticket", "start", "i0001").returncode, 0)
        child = self.worktree(self.lib, "i0001-01")
        target = config_of(child, "rules")
        write(target, read(self.rules))

        self.assertFalse(configsync.is_synced_write(conf, self.ws, target))

    def test_committed_content_decides_at_the_end_of_the_turn(self):
        """コミットした中身で答える。好きな中身でコミットしてディスクだけ戻しても外れない。"""
        tree, _ = self.start_parent()
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "sync")
        self.assertEqual(self.hook("", self.ws, event="UserPromptSubmit").returncode, 0)
        target = config_of(tree, "rules")
        synced = read(target)
        write(target, "version: 1\nallow:\n  - {id: all, match: Write, glob: '*'}\n")
        git(tree, "commit", "--quiet", "-am", "loosen")
        write(target, synced)

        stopped = self.hook("", self.ws, event="Stop")

        self.assertIn(".ccnavi/config/rules.yml", self.system_message(stopped))

    def test_the_sync_commit_itself_is_not_reported_at_the_end_of_the_turn(self):
        """着手で写した分をコミットしても、ターンの終わりに違反として並ばない。"""
        self.assertEqual(self.hook("", self.ws, event="UserPromptSubmit").returncode, 0)
        tree, _ = self.start_parent()
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "sync")

        stopped = self.hook("", self.ws, event="Stop")

        self.assertNotIn(".ccnavi/config", self.system_message(stopped))

    def test_scripts_the_risk_points_at_are_copied_too(self):
        """配点が指す共通層のスクリプトも写し、指す先をプロジェクトの層の綴りに直す。"""
        conf = self.settings()
        write(self.risk, COMMON_SCRIPT_RISK)
        write(os.path.join(self.ws, ".ccnavi", "common", "scripts", "count.sh"), COUNT_SH)

        tree, started = self.start_parent()

        copied = os.path.join(tree, ".ccnavi", "scripts", "count.sh")
        self.assertEqual(read(copied), COUNT_SH)
        risks = read(config_of(tree, "risk"))
        self.assertIn("script: .ccnavi/scripts/count.sh", risks)
        self.assertNotIn(".ccnavi/common/scripts", risks)
        self.assertIn(".ccnavi/scripts/count.sh", started.stdout)
        self.assertTrue(configsync.is_synced_write(conf, self.ws, copied))
        self.assertTrue(configsync.is_synced_write(conf, self.ws, config_of(tree, "risk")))

    def test_a_common_layer_that_the_project_cannot_read_stops_the_start(self):
        """プロジェクトの層として読めない共通層は写さず、着手しない。"""
        write(self.risk, "version: 1\nfactors: [\n")
        self.propose("i0001", ticket_text("i0001", project="lib", allow=SCOPE), project="lib")
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")
        before = read(config_of(tree, "rules"))

        started = self.ccnavi("ticket", "start", "i0001")

        self.assertNotEqual(started.returncode, 0, started.stdout)
        self.assertIn("プロジェクトの層として読めない", started.stderr)
        self.assertEqual(read(config_of(tree, "rules")), before)
        self.assertIsNone(self.mark())

    def test_uncommitted_edits_stop_the_start(self):
        """写す先に未コミットの変更があれば、人の書きかけを踏まないよう止める。"""
        self.propose("i0001", ticket_text("i0001", project="lib", allow=SCOPE), project="lib")
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")
        write(config_of(tree, "phases"), read(config_of(tree, "phases")) + "# 書きかけ\n")

        started = self.ccnavi("ticket", "start", "i0001")

        self.assertNotEqual(started.returncode, 0, started.stdout)
        self.assertIn("未コミットの変更", started.stderr)
        self.assertIn("# 書きかけ", read(config_of(tree, "phases")))
        self.assertIsNone(self.mark())

    def test_a_failed_write_puts_everything_back(self):
        """1 本でも書けなければ、書いた分と印を元に戻す。"""
        conf = self.settings()
        self.propose("i0001", ticket_text("i0001", project="lib", allow=SCOPE), project="lib")
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")
        copied, why = configsync.plan(conf, self.ws, tree)
        self.assertEqual(why, "")
        first = copied[0]
        original = read(first.target)
        # 2 本目の行き先をディレクトリにして、置き換えを失敗させる。
        os.remove(copied[1].target)
        os.makedirs(os.path.join(copied[1].target, "x"))
        home = self.approved_dir_of("lib")

        failed = configsync.apply(home, "i0001", copied)

        self.assertIn("戻した", failed)
        self.assertEqual(read(first.target), original)
        self.assertIsNone(self.mark())

    def test_changed_ids_are_named_too(self):
        """同じ id のまま deny から allow へ移ったものも「中身が変わった」と名指しする。"""
        loosened = {
            "version": 1,
            "allow": [
                {"id": "credentials", "match": "Write|Edit", "glob": "*/.env*"},
            ],
        }
        write(config_of(self.lib, "rules"), json.dumps(loosened))
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "--quiet", "-m", "loosen")

        _, started = self.start_parent()

        by_path = {f["path"]: f for f in self.mark()["files"]}
        self.assertEqual(by_path[".ccnavi/config/rules.yml"]["changed"], ["credentials"])
        self.assertIn("中身が変わった識別子: credentials", started.stdout)

    def test_a_parent_without_review_cannot_close_until_a_human_saw_it(self):
        """レビューの無い親は、人が端末で見たと残すまで閉じられない。"""
        self.start_parent()

        refused = self.ccnavi("ticket", "finish", "i0001")
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIn("--config-synced i0001", refused.stderr)

        seen = self.ccnavi("--config-synced", "i0001", stdin="y\n")
        self.assertEqual(seen.returncode, 0, seen.stdout + seen.stderr)
        self.assertIn("プロジェクトの設定が変わった", seen.stdout)
        self.assertEqual(self.mark()["notified"], configsync.NOTIFIED_TERMINAL)

        closed = self.ccnavi("ticket", "finish", "i0001")
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)

    def test_config_synced_is_denied_to_the_agent(self):
        """エージェントが Bash で打つ形は、人の判断の経路と同じ組み込みの deny が止める。"""
        rule = phase.ticket_approval_rule("", self.ws)
        self.assertIsNotNone(rule.compiled.search("ccnavi --config-synced i0001"))

    def test_notified_only_after_the_notice_was_put_in_the_request(self):
        """依頼の本文に載せた番号でだけ、知らせ済みにできる。"""
        self.start_parent()
        home = self.approved_dir_of("lib")
        self.assertFalse(configsync.prepared_for(home, "i0001", 1))

        self.assertEqual(configsync.mark_prepared(home, "i0001", 1), "")

        self.assertTrue(configsync.prepared_for(home, "i0001", 1))
        self.assertFalse(configsync.prepared_for(home, "i0001", 2))


class ConfigSyncSecondReviewTest(ConfigSyncTest):
    """2 回目の敵対的レビューで見つかった穴を塞いだことを見る。"""

    def test_a_worktree_without_an_approved_parent_is_not_exempt(self):
        """承認済みの親チケットの無いワークツリーは、印を自作しても外さない。"""
        conf = self.settings()
        tree = self.worktree(self.lib, "scratchy")
        target = config_of(tree, "rules")
        content = read(self.rules).encode()
        write(target, read(self.rules))
        mark = os.path.join(tree, ".ccnavi", "approved", "phases", "scratchy", "config-sync.json")
        write(
            mark,
            json.dumps(
                {
                    "files": [
                        {
                            "path": ".ccnavi/config/rules.yml",
                            "sha256": configsync._digest(content),
                            "before_sha256": configsync._digest(
                                read(config_of(self.lib, "rules")).encode()
                            ),
                        }
                    ],
                    "notified": "x",
                }
            ),
        )

        self.assertFalse(configsync.is_synced_write(conf, self.ws, target))

    def symlink_or_skip(self, source, link):
        """リンクを張る。Windows で権限が無いなど、張れない環境ではテストを飛ばす。

        飛ばすのは権限が無いとき（Windows の 1314）と、未対応の環境だけ。ほかの失敗
        （リンク先がすでに在るなど）は準備の崩れなので、飛ばさずに落とす。
        """
        try:
            os.symlink(source, link)
        except (OSError, NotImplementedError) as error:
            privilege_not_held = getattr(error, "winerror", None) == 1314
            if not (privilege_not_held or isinstance(error, NotImplementedError)):
                raise
            self.skipTest(f"シンボリックリンクが作れない: {error}")

    def test_a_symlink_is_not_exempt(self):
        """設定を別の写しへのシンボリックリンクに差し替えても外さない。"""
        conf = self.settings()
        tree, _ = self.start_parent()
        target = config_of(tree, "rules")
        os.remove(target)
        self.symlink_or_skip("risks.yml", target)

        self.assertFalse(configsync.is_synced_write(conf, self.ws, target))
        said = self.hook("Bash", tree, event="PostToolUse", command="ls")
        self.assertIn("rules.yml", said.stdout + said.stderr)

    def test_a_symlinked_target_stops_the_start(self):
        """写す先がシンボリックリンクなら、写さずに着手を止める。"""
        self.propose("i0001", ticket_text("i0001", project="lib", allow=SCOPE), project="lib")
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")
        target = config_of(tree, "rules")
        os.remove(target)
        self.symlink_or_skip("phases.yml", target)
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "link")

        started = self.ccnavi("ticket", "start", "i0001")

        self.assertNotEqual(started.returncode, 0, started.stdout)
        self.assertIn("シンボリックリンク", started.stderr)
        self.assertTrue(os.path.islink(target))

    def test_writing_back_after_a_human_fix_is_not_exempt(self):
        """写した分をコミットしたあと人が直したら、共通層の中身へ戻す書き込みは外さない。"""
        conf = self.settings()
        tree, _ = self.start_parent()
        target = config_of(tree, "rules")
        synced = read(target)
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "sync")
        write(
            target,
            json.dumps({"version": 1, "deny": [{"id": "own", "match": "Bash", "glob": "*x*"}]}),
        )
        git(tree, "commit", "--quiet", "-am", "human fix")

        write(target, synced)

        self.assertFalse(configsync.is_synced_write(conf, self.ws, target))
        said = self.hook("Bash", tree, event="PostToolUse", command="ls")
        self.assertIn("rules.yml", said.stdout + said.stderr)

    def test_close_problems_hold_ready_until_a_human_saw_it(self):
        """Draft を外す `ready` も同じ門を通る。知らせていない上書きがあれば止める。"""
        conf = self.settings()
        self.start_parent()

        problems = ops.close_problems(self.ws, conf, "i0001")

        self.assertTrue(any("--config-synced i0001" in p for p in problems), problems)

    def test_a_synced_script_factor_is_counted_once_after_it_lands(self):
        """写した配点が統合先に入っても、共通層の同じ項目と 2 重に数えない。"""
        write(self.risk, COMMON_SCRIPT_RISK)
        write(os.path.join(self.ws, ".ccnavi", "common", "scripts", "count.sh"), COUNT_SH)
        tree, _ = self.start_parent()
        # 親のブランチが統合先に入ったのと同じ形にする。
        for rel in (".ccnavi/config/risks.yml", ".ccnavi/scripts/count.sh"):
            write(
                os.path.join(self.lib, *rel.split("/")), read(os.path.join(tree, *rel.split("/")))
            )

        definition, problems = risk.layer_definition(self.settings(), self.ws, "lib")

        ids = [f.id for f in definition.factors]
        self.assertEqual(ids.count("counted"), 1, ids)
        self.assertNotIn("lib:counted", ids)
        self.assertFalse([p for p in problems if p.severity == "warn"], problems)

    def test_projected_rewrites_only_script_values(self):
        """指す先を直すのは `script:` の値だけ。`glob` や `message` の同じ綴りは触らない。"""
        text = (
            b"version: 1\nfactors:\n"
            b"  - {id: a, points: 1, script: .ccnavi/common/scripts/a.sh, message: m}\n"
            b'  - {id: b, points: 1, glob: ".ccnavi/common/scripts/**",'
            b" message: .ccnavi/common/scripts/ changed}\n"
        )

        out = configsync.projected(self.settings(), settings.KIND_RISK, text)

        self.assertIn(b"script: .ccnavi/scripts/a.sh", out)
        self.assertIn(b'glob: ".ccnavi/common/scripts/**"', out)
        self.assertIn(b"message: .ccnavi/common/scripts/ changed", out)

    def test_a_script_shared_by_two_factors_is_copied_once(self):
        """2 つの項目が同じスクリプトを指しても、写すのは 1 回。"""
        write(
            self.risk,
            COMMON_SCRIPT_RISK
            + "  - {id: again, points: 2, script: .ccnavi/common/scripts/count.sh,"
            " message: もう一度}\n",
        )
        write(os.path.join(self.ws, ".ccnavi", "common", "scripts", "count.sh"), COUNT_SH)
        self.propose("i0001", ticket_text("i0001", project="lib", allow=SCOPE), project="lib")
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree(self.lib, "i0001")

        copied, why = configsync.plan(self.settings(), self.ws, tree)

        self.assertEqual(why, "")
        rels = [c.rel for c in copied]
        self.assertEqual(rels.count(".ccnavi/scripts/count.sh"), 1, rels)

    def test_a_non_utf8_script_is_not_reported_after_it_is_committed(self):
        """コミット分はバイト列のまま比べる。UTF-8 でないスクリプトも写した分として外れる。"""
        write(self.risk, COMMON_SCRIPT_RISK)
        script = os.path.join(self.ws, ".ccnavi", "common", "scripts", "count.sh")
        os.makedirs(os.path.dirname(script), exist_ok=True)
        with open(script, "wb") as f:
            f.write(b"# \x82\xa0\rprintf 1\n")
        self.assertEqual(self.hook("", self.ws, event="UserPromptSubmit").returncode, 0)
        tree, _ = self.start_parent()
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "sync")

        stopped = self.hook("", self.ws, event="Stop")

        self.assertNotIn("count.sh", self.system_message(stopped))
