"""共通レイヤーのミラーを調べ、ずれを同期する（`ccnavi user mirror`）。

親チケットの着手がミラーを揃えるのは、そのプロジェクトの親チケットに着手するときだけなので、
間が空くとミラーが古いまま残る。`user mirror` は、プロジェクトのメインのチェックアウトの
ミラーを、見たいときに調べ（書かない）、押したときに揃える（ワークツリーに書き、コミットはしない）。

fixture は tests/config/test_config_union.py の ConfigUnionHarness を継ぐ。

見るのは次のとおり。

1. 調べる: ミラーが無ければずれ（追加）、共通レイヤーと同じなら同期済み。共通レイヤーに無い
   ファイルは削除として出る。名前を渡せばその分だけ。無い名前は全体の誤り
2. 共通レイヤーにコピーするものが無いときは、ずれにせず、ミラーを消さない
3. 揃える: 見せた差分の照合の値（expect）が今の差分と一致するときだけ書く。書くのはミラーだけで、
   プロジェクトの config には触れない。一致しなければ何も書かない
4. 調べられないプロジェクト（ミラーがリンク）は error にして、まとめて揃える対象から外す
5. 入口: JSON の形、組み合わせの誤り、ターミナルなしの `--yes` の拒否
"""

from __future__ import annotations

import json
import os

from ccnavi.infra import settings
from ccnavi.tickets import mirrorsync
from tests.config.test_config_union import (
    COMMON_RISK,
    COMMON_RULES,
    ConfigUnionHarness,
    git,
    read,
    write,
)

MIRROR = (".ccnavi", "config", "common")
# 共通レイヤーの中身を変えるための、配点の項目を 1 つ追加する行（読める形）。
EXTRA_FACTOR = "  - {id: extra, points: 1, lines_over: 99, message: 足した}\n"


def mirror_of(project_root, name):
    return os.path.join(project_root, *MIRROR, *name.split("/"))


class MirrorSyncTest(ConfigUnionHarness):
    def conf(self):
        conf, _ = settings.load(self.ws)
        return conf

    def plan(self, wanted=None):
        return mirrorsync.prepare(self.conf(), self.ws, wanted)

    def by_name(self, plan):
        return {p.name: p for p in plan.projects}

    def sync_all(self):
        """全プロジェクトのミラーを、共通レイヤーに揃える。"""
        plan = self.plan()
        done = mirrorsync.apply(self.conf(), plan, plan.expect)
        self.assertTrue(done.ok, done.error)
        return done

    # ---- 1. 調べる

    def test_a_project_without_a_mirror_differs_by_creating_the_files(self):
        plan = self.plan()
        self.assertEqual(plan.error, "")
        found = self.by_name(plan)
        self.assertEqual(sorted(found), ["app", "lib"])
        for project in found.values():
            self.assertEqual(project.state, mirrorsync.STATE_DIFFERS, project.name)
            actions = {c.rel: mirrorsync.action_of(c) for c in project.changes}
            self.assertEqual(actions[".ccnavi/config/common/rules.yml"], "create")
            self.assertEqual(actions[".ccnavi/config/common/risks.yml"], "create")
            self.assertNotEqual(project.expect, "")

    def test_a_synced_project_has_no_changes_and_no_expect(self):
        self.sync_all()
        plan = self.plan()
        for project in plan.projects:
            self.assertEqual(project.state, mirrorsync.STATE_SYNCED, project.name)
            self.assertEqual(project.changes, [])
            self.assertEqual(project.expect, "")
        self.assertEqual(plan.expect, "")

    def test_a_file_the_common_layer_does_not_have_is_a_delete(self):
        self.sync_all()
        write(mirror_of(self.lib, "old.yml"), "version: 1\n")
        project = self.by_name(self.plan())["lib"]
        self.assertEqual(project.state, mirrorsync.STATE_DIFFERS)
        self.assertEqual(
            [(c.rel, mirrorsync.action_of(c)) for c in project.changes],
            [(".ccnavi/config/common/old.yml", "delete")],
        )

    def test_a_changed_file_is_an_update(self):
        self.sync_all()
        write(mirror_of(self.lib, "risks.yml"), "version: 1\nlevels: {}\nfactors: []\n")
        project = self.by_name(self.plan())["lib"]
        self.assertEqual(
            [(c.rel, mirrorsync.action_of(c)) for c in project.changes],
            [(".ccnavi/config/common/risks.yml", "update")],
        )

    def test_a_line_ending_difference_is_not_a_difference(self):
        self.sync_all()
        raw = read(mirror_of(self.lib, "risks.yml")).replace("\n", "\r\n")
        with open(mirror_of(self.lib, "risks.yml"), "w", encoding="utf-8", newline="") as f:
            f.write(raw)
        self.assertEqual(self.by_name(self.plan())["lib"].state, mirrorsync.STATE_SYNCED)

    def test_names_pick_the_projects_to_show_and_unknown_names_are_an_error(self):
        plan = self.plan(["lib"])
        self.assertEqual([p.name for p in plan.projects if p.selected], ["lib"])
        self.assertEqual(plan.expect, self.by_name(plan)["lib"].expect)
        bad = self.plan(["nothing"])
        self.assertIn("nothing", bad.error)
        self.assertEqual(bad.projects, [])

    def test_names_are_matched_without_regard_to_case(self):
        plan = self.plan(["LIB"])
        self.assertEqual([p.name for p in plan.projects if p.selected], ["lib"])

    def test_the_expect_changes_when_the_common_layer_changes(self):
        before = self.plan().expect
        write(self.risk, COMMON_RISK + EXTRA_FACTOR)
        after = self.plan().expect
        self.assertNotEqual(before, after)

    def test_an_unreadable_common_layer_is_an_error_for_the_whole_plan(self):
        write(self.rules, "version: 99\n")
        plan = self.plan()
        self.assertNotEqual(plan.error, "")
        self.assertEqual(plan.projects, [])

    # ---- 2. 共通レイヤーが空

    def test_an_empty_common_layer_is_not_a_difference_and_deletes_nothing(self):
        self.sync_all()
        for path in (self.rules, self.phases, self.risk):
            if os.path.exists(path):
                os.remove(path)
        plan = self.plan()
        self.assertTrue(plan.common_empty)
        for project in plan.projects:
            self.assertEqual(project.state, mirrorsync.STATE_SYNCED, project.name)
        done = mirrorsync.apply(self.conf(), plan, plan.expect)
        self.assertFalse(done.ok)
        self.assertTrue(os.path.exists(mirror_of(self.lib, "rules.yml")))

    # ---- 3. 揃える

    def test_apply_writes_the_mirror_only_and_does_not_commit(self):
        config_file = os.path.join(self.lib, ".ccnavi", "config", "project", "rules.yml")
        config_before = read(config_file) if os.path.exists(config_file) else None
        head = git(self.lib, "rev-parse", "HEAD")

        plan = self.plan(["lib"])
        done = mirrorsync.apply(self.conf(), plan, plan.expect)

        self.assertTrue(done.ok, done.error)
        self.assertEqual([name for name, _ in done.synced], ["lib"])
        self.assertEqual(json.loads(read(mirror_of(self.lib, "rules.yml"))), COMMON_RULES)
        self.assertEqual(read(mirror_of(self.lib, "risks.yml")), COMMON_RISK)
        self.assertFalse(os.path.exists(mirror_of(self.app, "rules.yml")))
        if config_before is not None:
            self.assertEqual(read(config_file), config_before)
        self.assertEqual(git(self.lib, "rev-parse", "HEAD"), head)

    def test_apply_with_a_stale_expect_writes_nothing(self):
        plan = self.plan()
        done = mirrorsync.apply(self.conf(), plan, "0" * 64)
        self.assertFalse(done.ok)
        self.assertEqual(done.error, mirrorsync.STALE)
        self.assertFalse(os.path.exists(mirror_of(self.lib, "rules.yml")))

    def test_apply_with_an_empty_expect_writes_nothing(self):
        plan = self.plan()
        done = mirrorsync.apply(self.conf(), plan, "")
        self.assertFalse(done.ok)
        self.assertFalse(os.path.exists(mirror_of(self.lib, "rules.yml")))

    def test_apply_refuses_when_the_common_layer_changed_after_it_was_shown(self):
        shown = self.plan().expect
        write(self.risk, COMMON_RISK + EXTRA_FACTOR)
        done = mirrorsync.apply(self.conf(), self.plan(), shown)
        self.assertFalse(done.ok)
        self.assertEqual(done.error, mirrorsync.STALE)
        self.assertFalse(os.path.exists(mirror_of(self.lib, "risks.yml")))

    def test_apply_with_nothing_to_sync_says_so(self):
        self.sync_all()
        plan = self.plan()
        done = mirrorsync.apply(self.conf(), plan, plan.expect)
        self.assertFalse(done.ok)
        self.assertIn("揃えるミラーはありません", done.error)

    def test_apply_by_name_leaves_the_other_projects_alone(self):
        plan = self.plan(["lib"])
        self.assertTrue(mirrorsync.apply(self.conf(), plan, plan.expect).ok)
        again = self.by_name(self.plan())
        self.assertEqual(again["lib"].state, mirrorsync.STATE_SYNCED)
        self.assertEqual(again["app"].state, mirrorsync.STATE_DIFFERS)

    # ---- 4. 調べられないプロジェクト

    def test_a_linked_mirror_is_an_error_and_is_left_out_of_the_bulk_sync(self):
        outside = os.path.join(os.path.dirname(self.ws), "elsewhere")
        os.makedirs(outside)
        os.makedirs(os.path.join(self.app, ".ccnavi", "config"), exist_ok=True)
        os.symlink(outside, os.path.join(self.app, ".ccnavi", "config", "common"))

        plan = self.plan()
        found = self.by_name(plan)
        self.assertEqual(found["app"].state, mirrorsync.STATE_ERROR)
        self.assertNotEqual(found["app"].error, "")
        self.assertEqual(found["lib"].state, mirrorsync.STATE_DIFFERS)
        self.assertEqual([p.name for p in plan.chosen], ["lib"])
        done = mirrorsync.apply(self.conf(), plan, plan.expect)
        self.assertTrue(done.ok, done.error)
        self.assertEqual(os.listdir(outside), [])

    # ---- 5. 入口

    def user_mirror(self, *args):
        return self.ccnavi("user", "mirror", *args)

    def test_the_view_prints_json_with_the_agreed_shape(self):
        result = self.user_mirror("--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        answer = json.loads(result.stdout)
        self.assertEqual(answer["version"], 1)
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["error"], "")
        self.assertFalse(answer["common_empty"])
        self.assertEqual(sorted(p["name"] for p in answer["projects"]), ["app", "lib"])
        lib = next(p for p in answer["projects"] if p["name"] == "lib")
        self.assertEqual(lib["state"], "differs")
        self.assertIn(
            {"rel": ".ccnavi/config/common/rules.yml", "action": "create"}, lib["changes"]
        )
        self.assertEqual(lib["expect"], self.by_name(self.plan())["lib"].expect)
        self.assertEqual(answer["expect"], self.plan().expect)

    def test_the_view_writes_nothing(self):
        self.user_mirror("--json")
        self.assertFalse(os.path.exists(mirror_of(self.lib, "rules.yml")))

    def test_the_view_in_text_names_the_command_to_run(self):
        result = self.user_mirror()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ずれ", result.stdout)
        self.assertIn(f"user mirror --yes --expect {self.plan().expect}", result.stdout)

    def test_the_text_view_quotes_names_and_shows_how_to_see_the_diff(self):
        self.project("odd name")
        result = self.user_mirror("odd name")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("user mirror 'odd name' --yes --expect", result.stdout)
        self.assertIn("diff -ru", result.stdout)

    def test_the_text_view_warns_when_an_update_overwrites_the_mirror(self):
        self.sync_all()
        write(mirror_of(self.lib, "risks.yml"), "version: 1\nlevels: {}\nfactors: []\n")
        result = self.user_mirror()
        self.assertIn("上書き・削除します", result.stdout)
        fresh = self.user_mirror("app")
        self.assertNotIn("上書き・削除します", fresh.stdout)

    def test_the_board_route_syncs_with_a_matching_expect(self):
        expect = self.plan(["lib"]).expect
        result = self.user_mirror("lib", "--yes", "--expect", expect, "--via", "board", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        answer = json.loads(result.stdout)
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["synced"][0]["name"], "lib")
        self.assertIn(".ccnavi/config/common/rules.yml", answer["synced"][0]["files"])
        self.assertTrue(os.path.exists(mirror_of(self.lib, "rules.yml")))

    def test_the_board_route_with_a_stale_expect_writes_nothing(self):
        result = self.user_mirror("--yes", "--expect", "0" * 64, "--via", "board", "--json")
        self.assertNotEqual(result.returncode, 0)
        answer = json.loads(result.stdout)
        self.assertFalse(answer["ok"])
        self.assertIn("内容が更新されました", answer["error"])
        self.assertFalse(os.path.exists(mirror_of(self.lib, "rules.yml")))

    def test_combinations_that_make_no_sense_are_refused(self):
        for args in (
            ("--expect", "x"),
            ("--yes",),
            ("--via", "board", "--json"),
            ("--yes", "--via", "board", "--json"),
            ("--via", "terminal", "--yes", "--expect", "x", "--json"),
        ):
            with self.subTest(args=args):
                result = self.user_mirror(*args)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse(os.path.exists(mirror_of(self.lib, "rules.yml")))

    def test_the_internal_flag_is_not_accepted_without_the_user_entry(self):
        result = self.ccnavi("--mirror", "--json")
        self.assertNotEqual(result.returncode, 0)
