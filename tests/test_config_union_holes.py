"""敵対的レビューが見つけた守りの穴の受入テスト（子チケット config-union-07）。

どれも「判定が広がる側」の変更で、塞がないと `.ccnavi/` の組み込み deny が
「守っている」と言いながら回避できる。4 件とも既存の穴でもあり、`.claude/` の側も
同時に直る。

- A-2 大文字小文字: `glob` で書いたルールを、その機械がパスを見るのと同じ見方で当てる
- A-3 区切りが続かない綴り: `rm -rf .ccnavi` / `mv .ccnavi .ccnavi.bak` / `rm -rf .claude`
- A-4 生の `id` のコロン: 層の名前を添えた形と見分けが付かないものを error にする
- A-5 `self` の予約: `projects/Self/` も `self` と同じに扱って数えない
- A-6 確認だけ: 既に参照されている `.ccnavi/scripts/` のスクリプトを、綴りを変えた形でも
  区切りの無い形でも、Write / Edit とシェルの両方で書き換えられない

道具は外から動かす（`tests/inproc.py` の `run_ccnavi`）。fixture は
tests/test_config_union.py の ConfigUnionHarness と tests/test_config_union_guard.py の
GuardHarness を継ぐ。
"""

from __future__ import annotations

import json
import os
import unittest

from tests.test_config_union import (
    HOME,
    ConfigUnionHarness,
    git,
    write,
    write_layer,
)
from tests.test_config_union_guard import GuardHarness

# その機械がパスの綴りの大文字小文字を区別するか（ccnavi/tree.py の CASE_INSENSITIVE と
# 同じ問い）。区別しない機械では `.Ccnavi/scripts/count.sh` は本物そのものなので、
# 判定もそう見なければ守りが外れる。区別する機械では別のファイルなので、当たらないのが正しい。
CASE_INSENSITIVE = os.path.normcase("A") == "a"

# A-2 の的。`glob` と `regex` を 1 本ずつ持つ。綴りの扱いがこの 2 つで分かれる。
CASE_RULES = {
    "version": 3,
    "deny": [
        {
            "id": "glob-secret",
            "match": "Write|Edit",
            "glob": "*/secret/*",
            "message": "secret は人が置く。",
        },
        {
            "id": "regex-token",
            "match": "Write|Edit",
            "regex": r"[\\/]token[\\/]",
            "message": "token は人が置く。",
        },
    ],
    "allow": [{"id": "anything-read", "match": "Read", "regex": "."}],
}

# A-4 の的。裸の `id` にコロンがある。共通層に置けば lib の定義に見える。
COLON_RULES = {
    "version": 3,
    "deny": [
        {
            "id": "lib:custom",
            "match": "Write|Edit",
            "glob": "*/custom/*",
            "message": "custom は人が置く。",
        }
    ],
    "allow": [{"id": "anything-read", "match": "Read", "regex": "."}],
}

COLON_PHASES = """\
version: 1
phases:
  "lib:build":
    kind: work
    title: ビルド（コロン）
    review: none
    scope: ["src/*"]
"""

COLON_RISK = """\
version: 1
factors:
  - {id: "lib:schema", points: 5, glob: "schema/**", message: スキーマに触った}
"""

# A-5 の的。`projects/Self/` の層に置く deny。数えないので、当たってはいけない。
SELF_PROJECT_RULES = {
    "version": 3,
    "deny": [
        {
            "id": "kube",
            "match": "Bash",
            "glob": "*kubectl*",
            "message": "kubectl は人が叩く。",
        }
    ],
}

# A-6 の的。層の risk が呼ぶスクリプト。これが書き換わると配点が 0 を返せる。
COUNT_SCRIPT = 'printf \'{"points": 30, "message": "%s"}\' "$CCNAVI_TICKET"\n'
COUNT_RISK = (
    "version: 1\nfactors:\n"
    "  - {id: counted, points: 5, script: .ccnavi/scripts/count.sh, message: 数えた}\n"
)


class GlobCaseTest(ConfigUnionHarness):
    """A-2: `glob` で書いたルールの綴りの扱い。

    `rules.py` だけが区別する側にいて、`risk.py` / `phasetypes.py` / `ticket.py` は
    畳んでいた。`.ccnavi/` を最初に `.Ccnavi/` で作れば組み込み deny を素通りできる、
    という穴の土台がこれ。
    """

    def setUp(self):
        super().setUp()
        write(self.rules, json.dumps(CASE_RULES))

    def test_glob_matches_the_way_the_machine_reads_paths(self):
        """§25.4: `glob` の deny は、その機械がパスを見るのと同じ見方で当たる。"""
        # 実体があると、区別しない機械では `os.path.realpath` が綴りをディスクの側へ
        # 補正してしまい、この問い自体が消える（フラグ無しでも当たる）。
        self.assertFalse(os.path.exists(os.path.join(self.ws, "secret")))
        exact = self.hook("Write", self.ws, file_path=os.path.join(self.ws, "secret", "x.txt"))
        self.assert_denied(exact, "glob-secret")

        swapped = self.hook("Write", self.ws, file_path=os.path.join(self.ws, "SECRET", "x.txt"))
        if CASE_INSENSITIVE:
            self.assert_denied(swapped, "glob-secret")
        else:
            # 区別する機械では本当に別のディレクトリ。当たらないのが正しい。
            self.assert_not_denied(swapped)

    def test_regex_keeps_the_distinction(self):
        """§25.4: `regex` で書いた範囲は今までどおり区別する（書いた人が意図を持てる）。"""
        exact = self.hook("Write", self.ws, file_path=os.path.join(self.ws, "token", "x.txt"))
        self.assert_denied(exact, "regex-token")

        swapped = self.hook("Write", self.ws, file_path=os.path.join(self.ws, "TOKEN", "x.txt"))
        self.assert_not_denied(swapped)


class BuiltinGlobCaseTest(GuardHarness):
    """A-2: 組み込みの `*/.ccnavi/*` も同じ見方で当たる（穴そのもの）。

    的は app の層。fixture の app は `.ccnavi/` を持たないので、`.Ccnavi/` は
    ディスクに無く、`os.path.realpath` が綴りを補正しない。「まだ無いところを
    綴り違いで作る」という、ちょうど回避が成り立つ形になる。
    """

    def test_the_builtin_project_home_deny_matches_a_swapped_spelling(self):
        """§25.6: `.Ccnavi/config/rules.yml` への Write も組み込みの deny で止まる。"""
        # 傘がディスクに無いことが前提。あると realpath が綴りを補正して、問いが消える。
        self.assertFalse(os.path.exists(os.path.join(self.app, HOME)))
        exact = os.path.join(self.app, HOME, "config", "rules.yml")
        self.assert_denied(
            self.guarded_hook("Write", self.ws, file_path=exact), "builtin-guard-project-home"
        )

        swapped = os.path.join(self.app, ".Ccnavi", "config", "rules.yml")
        result = self.guarded_hook("Write", self.ws, file_path=swapped)
        if CASE_INSENSITIVE:
            self.assert_denied(result, "builtin-guard-project-home")
        else:
            self.assert_not_denied(result)


class ShellPlaceTest(GuardHarness):
    """A-3: 区切りが続かない綴り。傘ごと消す・退かす形（`_PLACES` / `_COPY_PLACES`）。"""

    def test_removing_or_moving_the_umbrella_itself_is_denied(self):
        """§25.6: 傘の名前で終わる綴りも `builtin-guard-setting-files` で止まる。"""
        for command in (
            "rm -rf .ccnavi",
            "rm -rf projects/lib/.ccnavi",
            "mv .ccnavi .ccnavi.bak",
            "mv projects/lib/.ccnavi /tmp/keep",
            "cp /tmp/x .ccnavi",
            "rm -rf .ccnavi && echo done",
        ):
            with self.subTest(command=command):
                result = self.guarded_hook("Bash", self.ws, command=command)
                self.assert_denied(result, "builtin-guard-setting-files")

    def test_the_claude_side_is_closed_the_same_way(self):
        """§25.6: `.claude` も同じ。中身のあるディレクトリだが、丸ごと消す道は塞ぐ。"""
        for command in ("rm -rf .claude", "mv .claude .claude.bak", "cp /tmp/x .claude"):
            with self.subTest(command=command):
                result = self.guarded_hook("Bash", self.ws, command=command)
                self.assert_denied(result, "builtin-guard-setting-files")

    def test_other_names_are_not_denied(self):
        """§25.6: 当たる範囲が広がっても、別名には誤爆しない。

        `.claudexyz` や `.ccnavi-notes.md` は別のファイル。`.claude/worktrees/` は
        守る対象ではないので、片付けは通る（`.claude` の側を傘と同じ `_END` で
        閉じると、ここが止まる）。傘から外へ写すだけの読みも通る。
        """
        for command in (
            "rm -rf .claudexyz",
            "rm -rf .ccnavixyz",
            "mv .ccnavi-notes.md notes.md",
            "rm -rf .claude-backup",
            "rm -rf .claude/worktrees/w1",
            "cp .ccnavi/config/rules.yml /tmp/x",
            "cat .ccnavi/config/rules.yml",
        ):
            with self.subTest(command=command):
                self.assert_not_denied(self.guarded_hook("Bash", self.ws, command=command))

    def test_the_moved_umbrella_is_closed_the_same_way(self):
        """§25.6: 傘の名前を動かしてあるときも、名前で終わる綴りで止まる。"""
        result = self.hook(
            "Bash",
            self.ws,
            guard="enable",
            env={"CCNAVI_PROJECT_HOME": ".navi"},
            command="rm -rf projects/lib/.navi",
        )
        self.assert_denied(result, "builtin-guard-setting-files")


class ColonIdTest(ConfigUnionHarness):
    """A-4: 生の `id` のコロン。層の名前を添えた形（`lib:custom`）と見分けが付かない。"""

    def test_a_colon_in_a_rule_id_is_named_by_lint(self):
        """§25.4: 共通層でも層でも、コロンを含む `id` は error で名指しする。"""
        write(self.rules, json.dumps(COLON_RULES))
        errors = self.problems("error")
        self.assertTrue(any("lib:custom" in p["where"] for p in errors), errors)
        self.assertTrue(any("`:`" in p["detail"] for p in errors), errors)

        write(self.rules, json.dumps(CASE_RULES))
        write_layer(self.lib, rules=COLON_RULES)
        errors = self.problems("error", where=self.project_where("lib"))
        self.assertTrue(any("lib:custom" in p["where"] for p in errors), errors)

    def test_a_rule_with_a_colon_in_its_id_does_not_judge(self):
        """§25.4: 落として名指しする側に倒す。黙って効かせると、どのファイルを直すのか決まらない。

        1 件の不備でガード全体は落とさない（rules.load）ので、他のルールは効いたまま。
        代償は、その 1 本が効かなくなること。`--lint` が error で言うのがその受け皿。
        """
        write(self.rules, json.dumps(COLON_RULES))
        self.assert_not_denied(
            self.hook("Write", self.ws, file_path=os.path.join(self.ws, "custom", "x.txt"))
        )

    def test_a_colon_in_a_phase_type_id_is_named_by_lint(self):
        """§25.4.1: フェーズの種類の識別子も同じ。"""
        write_layer(self.lib, phases=COLON_PHASES)
        errors = self.problems("error", where=self.project_where("lib"))
        self.assertTrue(any("lib:build" in p["where"] for p in errors), errors)
        self.assertTrue(any("`:`" in p["detail"] for p in errors), errors)

    def test_a_colon_in_a_risk_factor_id_is_named_by_lint(self):
        """§25.4.2: リスクの項目の id も同じ。"""
        write_layer(self.lib, risk=COLON_RISK)
        errors = self.problems("error", where=self.project_where("lib"))
        self.assertTrue(any("`:`" in p["detail"] for p in errors), errors)


class ReservedSelfTest(ConfigUnionHarness):
    """A-5: `self` は予約。`projects/Self/` のような綴り違いも同じに扱う。"""

    def setUp(self):
        super().setUp()
        self.self_project = self.project("Self", rules=SELF_PROJECT_RULES)

    def test_lint_names_the_reserved_name_whatever_the_spelling(self):
        """§25.4: `projects/Self/` も error で名指しする。"""
        errors = self.problems("error", where=self.project_where("Self"))
        self.assertTrue(any("self" in p["detail"] for p in errors), errors)

    def test_the_layer_is_not_counted(self):
        """§25.4: 数えないので、その層の deny は Bash の和に入らない。"""
        self.assert_not_denied(self.hook("Bash", self.ws, command="kubectl get pods"))
        # 普通の名前の層は和に入る。「そもそも和が効いていない」ではないことを見る。
        self.assert_denied(self.hook("Bash", self.ws, command="psql -c 'select 1'"), "lib:raw-psql")


class ScriptTamperTest(GuardHarness):
    """A-6: 既に参照されている `.ccnavi/scripts/` のスクリプトを書き換える・消す道。

    中核（控えと復元）には入れない。止まることだけを確かめる（A-2 と A-3 の結果）。
    """

    def setUp(self):
        super().setUp()
        self.script = write(os.path.join(self.lib, HOME, "scripts", "count.sh"), COUNT_SCRIPT)
        write_layer(self.lib, risk=COUNT_RISK)
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "--quiet", "-m", "script")

    def test_the_reference_is_valid(self):
        """§25.4.2: 前提の確認。この `script:` は `--lint` に咎められない（参照が生きている）。"""
        errors = self.problems("error", where=self.project_where("lib"))
        self.assertEqual([p for p in errors if "count.sh" in p["detail"]], [], errors)

    def test_named_tools_cannot_rewrite_it(self):
        """§25.6: Write / Edit は、正しい綴りでも綴りを変えた形でも止まる。"""
        for tool in ("Write", "Edit"):
            with self.subTest(tool=tool):
                self.assert_denied(
                    self.guarded_hook(tool, self.ws, file_path=self.script),
                    "builtin-guard-project-home",
                )
        swapped = os.path.join(self.lib, ".Ccnavi", "scripts", "count.sh")
        result = self.guarded_hook("Write", self.ws, file_path=swapped)
        if CASE_INSENSITIVE:
            # 区別しない機械では、この綴りで書けば本物が書き換わる。ここは傘が実在する
            # 形なので `os.path.realpath` がディスクの綴りへ補正する道でも止まる。
            # 補正が無い形（傘がまだ無いところを綴り違いで作る）は BuiltinGlobCaseTest。
            self.assert_denied(result, "builtin-guard-project-home")
        else:
            self.assert_not_denied(result)

    def test_the_shell_cannot_rewrite_or_delete_it(self):
        """§25.6: シェルも同じ。傘ごと消す形も、綴りを変えた形も止まる。"""
        for command in (
            "rm -rf projects/lib/.ccnavi/scripts/count.sh",
            "echo x > projects/lib/.ccnavi/scripts/count.sh",
            "rm -rf projects/lib/.ccnavi",
            "mv projects/lib/.ccnavi /tmp/keep",
        ):
            with self.subTest(command=command):
                self.assert_denied(
                    self.guarded_hook("Bash", self.ws, command=command),
                    "builtin-guard-setting-files",
                )
        swapped = "rm -rf projects/lib/.Ccnavi/scripts/count.sh"
        result = self.guarded_hook("Bash", self.ws, command=swapped)
        if CASE_INSENSITIVE:
            self.assert_denied(result, "builtin-guard-setting-files")
        else:
            self.assert_not_denied(result)


if __name__ == "__main__":
    unittest.main()
