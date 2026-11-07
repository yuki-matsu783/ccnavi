"""共通レイヤーのミラーを調べ、ずれを同期する（`ccnavi user mirror`）。

着手済みの親のワークツリーのミラーは発言のたびに揃うが、ユーザのチェックアウトは対象外なので、
間が空くとミラーが古いまま残る。`user mirror` は、プロジェクトのユーザのチェックアウトの
ミラーを、見たいときに調べ（書かない）、押したときに揃える（ワークツリーに書き、コミットはしない）。

fixture は tests/config/test_config_union.py の ConfigUnionHarness を継ぐ。

見るのは次のとおり。

1. 調べる: ミラーが無ければずれ（追加）、共通レイヤーと同じなら同期済み。共通レイヤーに無い
   ファイルは削除として出る。名前を渡せばその分だけ。無い名前は全体の誤り
2. 共通レイヤーにコピーするものが無いときは、ずれにせず、ミラーを消さない
3. 揃える: 見せた差分の照合の値（expect）が今の差分と一致するときだけ書く。書くのはミラーだけで、
   プロジェクトの config には触れない。一致しなければ何も書かない
4. 調べられないプロジェクト（ミラーがリンク）は error にして、まとめて揃える対象から外す
5. 工程スキルのコピー（`skills/`）も同じ入口で調べて揃える。書く順と、片方の失敗
6. 入口: JSON の形、組み合わせの誤り、ターミナルなしの `--yes` の拒否

1〜5 の調べ方と揃え方の細部は Go のテスト（go/internal/tickets/sync/mirror）が確かめる。
ここでは 6 の入口を外から動かす。
"""

from __future__ import annotations

import json
import os

from tests.config.test_config_union import ConfigUnionHarness, write

MIRROR = (".ccnavi", "config", "common")


def mirror_of(project_root, name):
    return os.path.join(project_root, *MIRROR, *name.split("/"))


class MirrorSyncTest(ConfigUnionHarness):
    def view(self, *names):
        """`user mirror --json` の答え（書かずに調べる）。"""
        result = self.user_mirror(*names, "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def sync_all(self):
        """全プロジェクトのミラーを、共通レイヤーに揃える（ボードの経路）。"""
        expect = self.view()["expect"]
        result = self.user_mirror("--yes", "--expect", expect, "--via", "board", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"], result.stdout)

    # ---- 6. 入口

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
        self.assertTrue(lib["expect"])
        self.assertEqual(answer["expect"], self.view()["expect"])

    def test_the_view_writes_nothing(self):
        self.user_mirror("--json")
        self.assertFalse(os.path.exists(mirror_of(self.lib, "rules.yml")))

    def test_the_view_in_text_names_the_command_to_run(self):
        result = self.user_mirror()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ずれ", result.stdout)
        self.assertIn(
            f"ccnavi-user-mirror.sh --yes --expect {self.view()['expect']}", result.stdout
        )

    def test_the_text_view_quotes_names_and_shows_how_to_see_the_diff(self):
        self.project("odd name")
        result = self.user_mirror("odd name")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ccnavi-user-mirror.sh 'odd name' --yes --expect", result.stdout)
        self.assertIn("diff -ru", result.stdout)

    def test_the_text_view_warns_when_an_update_overwrites_the_mirror(self):
        self.sync_all()
        write(mirror_of(self.lib, "risks.yml"), "version: 1\nlevels: {}\nfactors: []\n")
        result = self.user_mirror()
        self.assertIn("上書き・削除します", result.stdout)
        fresh = self.user_mirror("app")
        self.assertNotIn("上書き・削除します", fresh.stdout)

    def test_the_board_route_syncs_with_a_matching_expect(self):
        expect = self.view("lib")["expect"]
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
