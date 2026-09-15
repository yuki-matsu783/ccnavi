"""`.ccnavi/` の組み込み deny と層の名前を、綴りを変えて回避できないことの受入テスト。

どれも、塞がないと組み込み deny が「守っている」と言いながら回避できる形。
`.claude/` の側も同じ当て方で守られる。

- A-2 大文字小文字: `glob` で書いたルールと組み込みの守りを、どの機械でも区別せずに当てる
- A-3 区切りが続かない綴り: `rm -rf .ccnavi` / `mv .ccnavi .ccnavi.bak` / `rm -rf .claude`
- A-4 生の `id` のコロン: 層の名前を添えた形と見分けが付かないものを error にする
- A-5 `self` の予約: `projects/Self/` も `self` と同じに扱って数えない
- A-6 確認だけ: 既に参照されている `.ccnavi/scripts/` のスクリプトを、綴りを変えた形でも
  区切りの無い形でも、Write / Edit とシェルの両方で書き換えられない

層の名札（`self` / `common`）とプロジェクト名が同じときも、予約は両側に掛かる。

- `projects/self/` への Write / Edit は、そのプロジェクトの deny で判定され、
  ワークスペース自身の層のルールに落ちない（ReservedLayerNameTest）
- `projects/common/` の層の 3 本は、控えの key が共通層と衝突せず、
  控えと復元の対象に入る（ReservedLayerRestoreTest）

道具は外から動かす（`tests/inproc.py` の `run_ccnavi`）。fixture は
tests/test_config_union.py の ConfigUnionHarness と tests/test_config_union_guard.py の
GuardHarness を継ぐ。
"""

from __future__ import annotations

import json
import os
import unittest

from ccnavi import settings
from tests.test_config_union import (
    COMMON_RISK,
    HOME,
    ConfigUnionHarness,
    git,
    layer_path,
    read,
    ticket_text,
    write,
    write_layer,
)
from tests.test_config_union_guard import GuardHarness

# A-2 の的。`glob` と `regex` を 1 本ずつ持つ。綴りの扱いがこの 2 つで分かれる。
# `glob` と組み込みの守りは、どの機械でも大文字小文字を区別せずに当たる。機械で変えると、
# 同じルールが Windows では当たり Linux では当たらず、区別しないチケットの範囲とも食い違う。
# `regex` は書いた人が `(?i:...)` で選べるので、書いたとおりに区別する。
CASE_RULES = {
    "version": 1,
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
    "version": 1,
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
    "version": 1,
    "deny": [
        {
            "id": "kube",
            "match": "Bash",
            "glob": "*kubectl*",
            "message": "kubectl は人が叩く。",
        }
    ],
}

# 穴 1 の的。ワークスペース自身の層に置く、広い allow と 1 本の deny。
# 予約名のプロジェクトの層を名札で引くとこの層に当たるので、そのプロジェクトへの
# Write がここの allow で通り、ここの deny で止まる。どちらも起きてはいけない。
WIDE_OWN_RULES = {
    "version": 1,
    "deny": [
        {
            "id": "generated",
            "match": "Write|Edit",
            "glob": "*/generated/*",
            "message": "generated files are rebuilt, not edited.",
        }
    ],
    "allow": [{"id": "wide", "match": "Write|Edit", "glob": "*"}],
}

# 穴 1 の的。予約名のプロジェクトの層に置く deny。層として数えないので当たらない。
# 当たらないことは「緩い」のではない。共通層だけで判定するので、allow も無い。
RESERVED_PROJECT_RULES = {
    "version": 1,
    "deny": [
        {
            "id": "secret",
            "match": "Write|Edit",
            "glob": "*/secret/*",
            "message": "secret は人が置く。",
        }
    ],
}

# 穴 2 の的。予約名のプロジェクトの層の phases / risk。中身は何でもよく、
# 控えと復元の対象に入るかだけを見る。
RESERVED_PROJECT_PHASES = """\
version: 1
phases:
  reserved:
    kind: work
    title: 予約名の層
    review: none
    scope: ["src/*"]
"""

RESERVED_PROJECT_RISK = """\
version: 1
factors:
  - {id: reserved, points: 5, glob: "src/**", message: 予約名の層で数えた}
"""

# A-6 の的。層の risk が呼ぶスクリプト。これが書き換わると配点が 0 を返せる。
COUNT_SCRIPT = 'printf \'{"points": 30, "message": "%s"}\' "$CCNAVI_TICKET"\n'
COUNT_RISK = (
    "version: 1\nfactors:\n"
    "  - {id: counted, points: 5, script: .ccnavi/scripts/count.sh, message: 数えた}\n"
)


class GlobCaseTest(ConfigUnionHarness):
    """A-2: `glob` で書いたルールの綴りの扱い。

    `glob` は、`risk.py` / `phasetypes.py` / `ticket.py` の glob と同じく、どの機械でも
    大文字小文字を区別せずに当たる。区別すると、`.ccnavi/` を `.Ccnavi/` の綴りで作って
    組み込み deny を素通りできる。
    """

    def setUp(self):
        super().setUp()
        write(self.rules, json.dumps(CASE_RULES))

    def test_glob_ignores_case_on_every_machine(self):
        """§11.4: `glob` の deny は、どの機械でも大文字小文字を区別せずに当たる。"""
        # 実体があると、区別しない機械では `os.path.realpath` が綴りをディスクの側へ
        # 補正してしまい、この問い自体が消える（フラグ無しでも当たる）。
        self.assertFalse(os.path.exists(os.path.join(self.ws, "secret")))
        exact = self.hook("Write", self.ws, file_path=os.path.join(self.ws, "secret", "x.txt"))
        self.assert_denied(exact, "glob-secret")

        # 区別する機械では `SECRET/` は本当に別のディレクトリだが、それでも当てる。
        # 同じルールが機械によって当たったり当たらなかったりしないことを優先する。
        swapped = self.hook("Write", self.ws, file_path=os.path.join(self.ws, "SECRET", "x.txt"))
        self.assert_denied(swapped, "glob-secret")

    def test_regex_keeps_the_distinction(self):
        """§11.4: `regex` で書いた範囲は書いたとおりに区別する（書いた人が意図を持てる）。"""
        exact = self.hook("Write", self.ws, file_path=os.path.join(self.ws, "token", "x.txt"))
        self.assert_denied(exact, "regex-token")

        swapped = self.hook("Write", self.ws, file_path=os.path.join(self.ws, "TOKEN", "x.txt"))
        self.assert_not_denied(swapped)


class BuiltinGlobCaseTest(GuardHarness):
    """A-2: 組み込みの `*/.ccnavi/*` も、どの機械でも大文字小文字を区別せずに当たる（穴そのもの）。

    的は app の層。fixture の app は `.ccnavi/` を持たないので、`.Ccnavi/` は
    ディスクに無く、`os.path.realpath` が綴りを補正しない。「まだ無いところを
    綴り違いで作る」という、ちょうど回避が成り立つ形になる。
    """

    def test_the_builtin_project_home_deny_matches_a_swapped_spelling(self):
        """§11.6: `.Ccnavi/config/rules.yml` への Write も組み込みの deny で止まる。"""
        # ccnavi ディレクトリがディスクに無いことが前提。あると realpath が綴りを補正して、
        # 問いが消える。
        self.assertFalse(os.path.exists(os.path.join(self.app, HOME)))
        exact = os.path.join(self.app, HOME, "config", "rules.yml")
        self.assert_denied(
            self.guarded_hook("Write", self.ws, file_path=exact), "builtin-guard-project-home"
        )

        # 区別する機械でも止める。組み込みの守りだけが機械で当たり方を変えると、
        # どの機械でも区別しないルールの glob やチケットの範囲と食い違う。
        swapped = os.path.join(self.app, ".Ccnavi", "config", "rules.yml")
        result = self.guarded_hook("Write", self.ws, file_path=swapped)
        self.assert_denied(result, "builtin-guard-project-home")


class ShellPlaceTest(GuardHarness):
    """A-3: 区切りが続かない綴り。ディレクトリごと消す・退かす形（`_PLACES` / `_COPY_PLACES`）。"""

    def test_removing_or_moving_the_umbrella_itself_is_denied(self):
        """§11.6: `.ccnavi` で終わる綴りも `builtin-guard-setting-files` で止まる。"""
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
        """§11.6: `.claude` も同じ。中身のあるディレクトリだが、丸ごと消す道は塞ぐ。"""
        for command in ("rm -rf .claude", "mv .claude .claude.bak", "cp /tmp/x .claude"):
            with self.subTest(command=command):
                result = self.guarded_hook("Bash", self.ws, command=command)
                self.assert_denied(result, "builtin-guard-setting-files")

    def test_other_names_are_not_denied(self):
        """§11.6: 当たる範囲が広がっても、別名には誤爆しない。

        `.claudexyz` や `.ccnavi-notes.md` は別のファイル。`.claude/worktrees/` は
        守る対象ではないので、片付けは通る（`.claude` の側を ccnavi ディレクトリと同じ `_END` で
        閉じると、ここが止まる）。ccnavi ディレクトリから外へ写すだけの読みも通る。
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
        """§11.6: ccnavi ディレクトリの名前を動かしてあるときも、名前で終わる綴りで止まる。"""
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
        """§11.4: 共通層でも層でも、コロンを含む `id` は error で名指しする。"""
        write(self.rules, json.dumps(COLON_RULES))
        errors = self.problems("error")
        self.assertTrue(any("lib:custom" in p["where"] for p in errors), errors)
        self.assertTrue(any("`:`" in p["detail"] for p in errors), errors)

        write(self.rules, json.dumps(CASE_RULES))
        write_layer(self.lib, rules=COLON_RULES)
        errors = self.problems("error", where=self.project_where("lib"))
        self.assertTrue(any("lib:custom" in p["where"] for p in errors), errors)

    def test_a_rule_with_a_colon_in_its_id_does_not_judge(self):
        """§11.4: 落として名指しする側に倒す。黙って効かせると、どのファイルを直すのか決まらない。

        1 件の不備でガード全体は落とさない（rules.load）ので、他のルールは効いたまま。
        代償は、その 1 本が効かなくなること。`--lint` が error で言うのがその受け皿。
        """
        write(self.rules, json.dumps(COLON_RULES))
        self.assert_not_denied(
            self.hook("Write", self.ws, file_path=os.path.join(self.ws, "custom", "x.txt"))
        )

    def test_a_colon_in_a_phase_type_id_is_named_by_lint(self):
        """§11.4.1: フェーズの種類の識別子も同じ。"""
        write_layer(self.lib, phases=COLON_PHASES)
        errors = self.problems("error", where=self.project_where("lib"))
        self.assertTrue(any("lib:build" in p["where"] for p in errors), errors)
        self.assertTrue(any("`:`" in p["detail"] for p in errors), errors)

    def test_a_colon_in_a_risk_factor_id_is_named_by_lint(self):
        """§11.4.2: リスクの項目の id も同じ。"""
        write_layer(self.lib, risk=COLON_RISK)
        errors = self.problems("error", where=self.project_where("lib"))
        self.assertTrue(any("`:`" in p["detail"] for p in errors), errors)


class ReservedSelfTest(ConfigUnionHarness):
    """A-5: `self` は予約。`projects/Self/` のような綴り違いも同じに扱う。"""

    def setUp(self):
        super().setUp()
        self.self_project = self.project("Self", rules=SELF_PROJECT_RULES)

    def test_lint_names_the_reserved_name_whatever_the_spelling(self):
        """§11.4: `projects/Self/` も error で名指しする。"""
        errors = self.problems("error", where=self.project_where("Self"))
        self.assertTrue(any("self" in p["detail"] for p in errors), errors)

    def test_the_layer_is_not_counted(self):
        """§11.4: 数えないので、その層の deny は Bash の和に入らない。"""
        self.assert_not_denied(self.hook("Bash", self.ws, command="kubectl get pods"))
        # 普通の名前の層は和に入る。「そもそも和が効いていない」ではないことを見る。
        self.assert_denied(self.hook("Bash", self.ws, command="psql -c 'select 1'"), "lib:raw-psql")


class ReservedLayerNameTest(ConfigUnionHarness):
    """穴 1: 予約名のプロジェクトへの Write が、ワークスペース自身の層で判定される。

    `layers` は `projects/self/` を数えないのに、`layer_for` は
    `target.project or LAYER_SELF` を名札で引いていた。`target.project` が `"self"`
    なら名札と一致するので、ワークスペース自身の層が返る。**そのプロジェクトへの
    Write / Edit が、プロジェクト自身の deny を一度も読まずに、ワークスペースの層の
    ルールで判定される。**

    穴が再現する形に組む。ワークスペース自身の層に広い `allow`（`self:wide`）と
    deny（`self:generated`）を置き、プロジェクトの層に deny（`secret`）を置く。
    名札で層を引くと `self:wide` が勝って通り、`self:generated` で止まる。予約名の
    プロジェクトは層無しなので共通層だけで判定し、どちらも記録に現れない。
    """

    def setUp(self):
        super().setUp()
        write_layer(self.ws, rules=WIDE_OWN_RULES)

    def assert_the_wide_allow_is_alive(self):
        """前提の確認。広い allow は実在して、ワークスペースのツリーには当たる。

        これが無いと、あとの「当たらない」が「そもそもルールが効いていない」と
        区別できない。
        """
        allowed = self.hook("Write", self.ws, file_path=os.path.join(self.ws, "secret", "x.txt"))
        self.assert_not_denied(allowed)
        record = self.last_record()
        self.assertEqual(record["decision"], "allow", record)
        self.assertIn("self:wide", record["rules"], record)

    def check_no_layer_is_borrowed(self, name):
        """`projects/<name>/` への Write が、どの層の rules でも判定されないこと。"""
        project = self.project(name, rules=RESERVED_PROJECT_RULES)
        self.assert_the_wide_allow_is_alive()

        result = self.hook("Write", self.ws, file_path=os.path.join(project, "secret", "x.txt"))

        record = self.last_record()
        # 穴の本体。名札で層を引くと、ここに `self:wide` が入り、decision が allow になる。
        self.assertNotIn("self:wide", record.get("rules", []), record)
        self.assertNotEqual(record["decision"], "allow", record)
        # プロジェクトの層も足さない（層無し）。共通層に `*/secret/*` は無いので deny でもない。
        self.assert_not_denied(result)
        self.assertNotIn(f"{name}:secret", record.get("rules", []), record)

    def check_the_workspace_deny_does_not_reach(self, name):
        """ワークスペース自身の層の deny も、予約名のプロジェクトには届かないこと。"""
        project = self.project(name, rules=RESERVED_PROJECT_RULES)
        # 前提。同じ綴りはワークスペースのツリーでは止まる。
        self.assert_denied(
            self.hook("Write", self.ws, file_path=os.path.join(self.ws, "generated", "x.py")),
            "self:generated",
        )

        # 名札で層を引くと、ここも `self:generated` で止まる。層無しなら共通層だけ。
        self.assert_not_denied(
            self.hook("Write", self.ws, file_path=os.path.join(project, "generated", "x.py"))
        )
        self.assertNotIn("self:generated", self.last_record().get("rules", []))

    def test_a_write_into_projects_self_is_not_judged_by_the_workspace_layer(self):
        """§11.4: `projects/self/` は層無し。ワークスペースの層の allow では通らない。"""
        self.check_no_layer_is_borrowed(settings.LAYER_SELF)

    def test_the_spelling_does_not_change_it(self):
        """§11.4: `projects/Self/` も同じ（綴りの大文字小文字は問わない）。"""
        self.check_no_layer_is_borrowed("Self")

    def test_a_write_into_projects_common_is_not_judged_by_the_workspace_layer(self):
        """§11.4: `common` も予約。`projects/common/` も層無し。"""
        self.check_no_layer_is_borrowed(settings.LAYER_COMMON)

    def test_the_workspace_layer_deny_does_not_reach_projects_self(self):
        """§11.4: 層を借りないので、ワークスペースの層の deny も届かない。"""
        self.check_the_workspace_deny_does_not_reach(settings.LAYER_SELF)

    def test_a_project_whose_name_is_not_reserved_still_works(self):
        """§11.4 対照: 予約名でない `lib` は今までどおり自分の層で判定される。"""
        self.assert_denied(
            self.hook("Write", self.ws, file_path=os.path.join(self.lib, "schema", "x.sql")),
            "lib:schema",
        )
        # 広い allow は行き先の 1 層にしか足さないので、lib には漏れない。
        self.assertNotIn("self:wide", self.last_record().get("rules", []))
        self.assert_denied(self.hook("Bash", self.ws, command="psql -c 'select 1'"), "lib:raw-psql")

    def test_lint_names_both_reserved_names(self):
        """§11.4: `projects/common/` と `projects/self/` の両方を error で名指しする。"""
        self.project(settings.LAYER_COMMON, rules=RESERVED_PROJECT_RULES)
        self.project(settings.LAYER_SELF, rules=RESERVED_PROJECT_RULES)
        for name in settings.RESERVED_LAYER_NAMES:
            with self.subTest(name=name):
                errors = self.problems("error", where=self.project_where(name))
                self.assertTrue(any("予約" in p["detail"] for p in errors), errors)
                # 文面から「どちらの綴りが予約か」が読めること。
                self.assertTrue(
                    any(
                        f"`{settings.LAYER_COMMON}`" in p["detail"]
                        and f"`{settings.LAYER_SELF}`" in p["detail"]
                        for p in errors
                    ),
                    errors,
                )

    def test_a_ticket_cannot_name_a_reserved_layer_name(self):
        """§11.4: `project: self` / `project: common` のチケットは `--approve` で通らない。"""
        for name in settings.RESERVED_LAYER_NAMES:
            self.project(name, rules=RESERVED_PROJECT_RULES)
        # 前提。同じ本文で `project: lib` なら通る。止まる理由が予約名であることを固定する。
        self.propose(
            "i0001", ticket_text("i0001", project="lib", plan=["design"], allow=("src/*",)), "lib"
        )
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)

        numbered = zip(("i0002", "i0003"), settings.RESERVED_LAYER_NAMES, strict=True)
        for number, name in numbered:
            with self.subTest(project=name):
                proposal = self.propose(
                    number,
                    ticket_text(number, project=name, plan=["design"], allow=("src/*",)),
                    name,
                )
                refused = self.approve()
                self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
                self.assertIn("予約", refused.stderr, refused.stderr)
                self.assertFalse(os.path.exists(self.approved_copy(number)))
                # 次の 1 件と混ざらないように片付ける。
                os.remove(proposal)


class ReservedLayerRestoreTest(GuardHarness):
    """穴 2: `projects/common/` の層の 3 本が控えと復元の対象から落ちる。

    `_layer_key` が `layer == LAYER_COMMON` の文字列比較で key を決めていた。
    `projects/common/` があると、その層の key が `rules` / `phases` / `risk` になって
    共通層の key と完全に一致し、`_places` の重複の排除で先に積んだ共通層だけが
    残る。プロジェクトが名前を 1 つ選ぶだけで、その 3 本が守られなくなる。

    key が共通層と重なると、このクラスの最初の 2 つが落ちる（戻らないので中身が壊れたまま）。
    """

    def setUp(self):
        super().setUp()
        self.reserved = self.project(
            settings.LAYER_COMMON,
            rules=RESERVED_PROJECT_RULES,
            phases=RESERVED_PROJECT_PHASES,
            risk=RESERVED_PROJECT_RISK,
        )

    def test_the_layer_of_a_project_named_common_is_restored(self):
        """§11.6: 名札と同じ名前のプロジェクトでも、層の 3 本が控えと復元の対象。"""
        for kind in settings.LAYER_KINDS:
            with self.subTest(kind=kind):
                path = layer_path(self.reserved, kind)
                broken = "version: 1\ndeny: []\n" if kind == "rules" else "version: 1\n"
                before, after, result = self.break_and_restore(path, broken=broken)
                self.assertEqual(after, before, self.said(result, kind))
                self.assertIn("restored", result.stdout, self.said(result, kind))

    def test_the_restore_asks_the_project_git(self):
        """§11.6: 戻す先を聞く相手はそのプロジェクトの git。控えが無くても戻る。"""
        path = layer_path(self.reserved, "rules")
        expected = read(path)
        os.remove(path)

        result = self.run_hook("PreToolUse")

        self.assertTrue(os.path.exists(path), self.said(result))
        self.assertEqual(read(path), expected, self.said(result))

    def test_the_common_layer_is_still_restored_alongside_it(self):
        """§11.6: 共通層の phases / risk も同じ 1 回で戻る（key がぶつかっていない）。"""
        for path in (self.phases, self.risk):
            name = os.path.basename(path)
            with self.subTest(path=name):
                before, after, result = self.break_and_restore(path, broken="version: 1\n")
                self.assertEqual(after, before, self.said(result, name))
                self.assertIn("restored", result.stdout, self.said(result, name))

    def test_the_layer_of_a_project_named_self_is_restored_too(self):
        """§11.6: `projects/self/` の 3 本も、ワークスペース自身の層とは別に守る。"""
        project = self.project(
            settings.LAYER_SELF, rules=RESERVED_PROJECT_RULES, phases=RESERVED_PROJECT_PHASES
        )
        write(layer_path(self.ws, "risk"), COMMON_RISK)
        for home, kind in ((project, "rules"), (project, "phases"), (self.ws, "risk")):
            with self.subTest(home=os.path.basename(home), kind=kind):
                path = layer_path(home, kind)
                broken = "version: 1\ndeny: []\n" if kind == "rules" else "version: 1\n"
                before, after, result = self.break_and_restore(path, broken=broken)
                self.assertEqual(after, before, self.said(result, kind))


class ScriptTamperTest(GuardHarness):
    """A-6: 既に参照されている `.ccnavi/scripts/` のスクリプトを書き換える・消す道。

    コア（控えと復元）には入れない。止まることだけを確かめる（A-2 と A-3 の結果）。
    """

    def setUp(self):
        super().setUp()
        self.script = write(os.path.join(self.lib, HOME, "scripts", "count.sh"), COUNT_SCRIPT)
        write_layer(self.lib, risk=COUNT_RISK)
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "--quiet", "-m", "script")

    def test_the_reference_is_valid(self):
        """§11.4.2: 前提の確認。この `script:` は `--lint` に咎められない（参照が生きている）。"""
        errors = self.problems("error", where=self.project_where("lib"))
        self.assertEqual([p for p in errors if "count.sh" in p["detail"]], [], errors)

    def test_named_tools_cannot_rewrite_it(self):
        """§11.6: Write / Edit は、正しい綴りでも綴りを変えた形でも止まる。"""
        for tool in ("Write", "Edit"):
            with self.subTest(tool=tool):
                self.assert_denied(
                    self.guarded_hook(tool, self.ws, file_path=self.script),
                    "builtin-guard-project-home",
                )
        # どの機械でも止める。区別しない機械では、この綴りで書けば本物が書き換わる。
        # 区別する機械では別のファイルだが、組み込みの守りの当たり方を機械で変えない。
        # 補正が無い形（ccnavi ディレクトリがまだ無いところを綴り違いで作る）は
        # BuiltinGlobCaseTest。
        swapped = os.path.join(self.lib, ".Ccnavi", "scripts", "count.sh")
        result = self.guarded_hook("Write", self.ws, file_path=swapped)
        self.assert_denied(result, "builtin-guard-project-home")

    def test_the_shell_cannot_rewrite_or_delete_it(self):
        """§11.6: シェルも同じ。ccnavi ディレクトリごと消す形も、綴りを変えた形も止まる。"""
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
        # シェルの側も、場所の綴りはどの機械でも区別せずに当てる（selfguard._folded）。
        swapped = "rm -rf projects/lib/.Ccnavi/scripts/count.sh"
        result = self.guarded_hook("Bash", self.ws, command=swapped)
        self.assert_denied(result, "builtin-guard-setting-files")


if __name__ == "__main__":
    unittest.main()
