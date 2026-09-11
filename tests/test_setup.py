"""導入スクリプトの受入テスト。外からスクリプトを動かす。

読み返すのは、書かれた `.claude/settings.json` と標準出力・終了コードだけ。
中の関数も変数も見ない。

使い捨てのディレクトリを毎回作る。このリポジトリ自身に打つと、テストが
「コードのこと」ではなく「走った機械の設定ファイルのこと」を報告するし、
走らせるたびに自分の hook の登録が書き換わる。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "ccnavi-setup.sh")
SHELL = shutil.which("sh") or shutil.which("bash")
HAS_JQ = shutil.which("jq") is not None

# 登録されていることを確かめる 7 つ。README「設定」の表と対になる。
EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "SubagentStart",
    "SubagentStop",
)
REQUIRED_ENV = ("CCNAVI_MODE", "CCNAVI_RULES", "CCNAVI_LOG", "CCNAVI_BIN_PATH")
# hook に登録される 1 行。README「設定」の見本と対になる。綴りが変わると、
# ccnavi 自身が守る対象（CCNAVI_BIN_PATH）と実際に起動するものがずれる。
HOOK_COMMAND = '"${CLAUDE_PROJECT_DIR}/${CCNAVI_BIN_PATH}"'
# もう効かない環境変数（settings.py の RETIRED_ENVS）。書かれていたら
# ccnavi の --lint が苦情を言う。導入スクリプトが作ってはいけない。
RETIRED_ENV = ("CCNAVI_TICKET", "CCNAVI_LEDGER")
# --deploy が写すゲートの sh。拒否の文面が案内する「代わりに通る形」で、
# 無いと止められた側に逃げ道がない。
GATE_SCRIPTS = ("ccnavi-ticket.sh", "ccnavi-review.sh", "ccnavi-git.sh")
RULES_PARTS = (".claude", "ccnavi", "rules.yml")


@unittest.skipUnless(SHELL, "sh も bash も見つからない")
@unittest.skipUnless(HAS_JQ, "jq が見つからない")
class SetupTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-setup-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def run_setup(self, *args):
        return subprocess.run(
            [SHELL, SCRIPT, self.dir, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def run_raw(self, *args):
        """対象ディレクトリを自分で並べる形。引数の扱いを試すときに使う。"""
        return subprocess.run(
            [SHELL, SCRIPT, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def settings_path(self):
        return os.path.join(self.dir, ".claude", "settings.json")

    def write_settings(self, data):
        os.makedirs(os.path.dirname(self.settings_path()), exist_ok=True)
        with open(self.settings_path(), "w", encoding="utf-8") as f:
            if isinstance(data, str):
                f.write(data)
            else:
                json.dump(data, f)

    def read_settings(self):
        with open(self.settings_path(), encoding="utf-8") as f:
            return json.load(f)

    def commands_of(self, data, event):
        out = []
        for entry in data.get("hooks", {}).get(event, []):
            for hook in entry.get("hooks", []):
                out.append(hook.get("command", ""))
        return out

    def hooks_of(self, data, event):
        out = []
        for entry in data.get("hooks", {}).get(event, []):
            out.extend(entry.get("hooks", []))
        return out


class WritesTheExpectedShape(SetupTest):
    def test_writes_env_and_all_seven_hooks(self):
        """設定ファイルが無いところに、env と 7 つの hook を作る。"""
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)

        data = self.read_settings()
        for name in REQUIRED_ENV:
            self.assertIn(name, data["env"])
        for event in EVENTS:
            commands = self.commands_of(data, event)
            self.assertTrue(
                any("ccnavi" in c.lower() for c in commands),
                f"{event} に ccnavi が登録されていない",
            )

    def test_registers_the_exact_command_line_from_the_readme(self):
        """hook に書かれる 1 行そのものを見る。

        「ccnavi という字が入っている」だけを見ていると、綴りを取り違えても
        テストが緑のまま通る。この 1 行は、何を起動するかと、ccnavi が何を
        守るか（CCNAVI_BIN_PATH）を同時に決めている。
        """
        self.run_setup()
        data = self.read_settings()
        for event in EVENTS:
            hooks = self.hooks_of(data, event)
            self.assertEqual(len(hooks), 1, f"{event} の hook が 1 つでない")
            self.assertEqual(hooks[0]["command"], HOOK_COMMAND, f"{event} の command")
            self.assertEqual(hooks[0]["type"], "command", f"{event} の type")
            self.assertEqual(hooks[0]["timeout"], 10, f"{event} の timeout")

    def test_writes_the_paths_ccnavi_reads(self):
        """env の値そのものを見る。存在するだけでは、取り違えを見つけられない。"""
        self.run_setup("--mode", "enable")
        env = self.read_settings()["env"]
        self.assertEqual(env["CCNAVI_MODE"], "enable")
        self.assertEqual(env["CCNAVI_RULES"], ".claude/ccnavi/rules.yml")
        self.assertEqual(env["CCNAVI_LOG"], ".claude/ccnavi/log.jsonl")

    def test_bin_path_is_spelled_without_the_exe_suffix(self):
        """実行ファイルの綴りは 3 つの環境で 1 行のまま。

        `.exe` を書き足すのは PyInstaller のほうで、設定の側は書かない。
        設定に `.exe` が入ると、その 1 行が Windows でしか当たらなくなる。
        """
        self.run_setup()
        self.assertEqual(self.read_settings()["env"]["CCNAVI_BIN_PATH"], "dist/ccnavi/ccnavi")

    def test_does_not_write_retired_env(self):
        """もう効かない環境変数を書かない。書けば --lint が苦情を言う。"""
        self.run_setup("--all")
        env = self.read_settings()["env"]
        for name in RETIRED_ENV:
            self.assertNotIn(name, env)

    def test_all_writes_the_settings_that_have_defaults(self):
        """--all は、既定と同じ値のつまみも設定ファイルに並べる。"""
        self.run_setup("--all")
        env = self.read_settings()["env"]
        self.assertEqual(env["CCNAVI_TICKETS"], "wip/tickets")
        self.assertEqual(env["CCNAVI_GUARD_CLI"], "enable")

    def test_says_what_is_still_missing(self):
        """登録しただけでは動かないので、人が置くものを挙げる。"""
        result = self.run_setup()
        self.assertIn("dist/ccnavi/ccnavi", result.stdout)
        self.assertIn("rules.yml", result.stdout)
        self.assertIn("ccnavi-git.sh", result.stdout)


class KeepsWhatItFinds(SetupTest):
    def test_running_twice_changes_nothing(self):
        """2 回目は同じ形に落ち着き、hook が二重にならない。"""
        self.run_setup()
        first = self.read_settings()

        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("書き足すものはありません", result.stdout)
        self.assertEqual(self.read_settings(), first)
        self.assertEqual(len(self.commands_of(first, "PreToolUse")), 1)

    def test_keeps_settings_that_have_nothing_to_do_with_ccnavi(self):
        """ccnavi と関係のない hook・env・権限を消さない。

        導入は既にあるプロジェクトに対して打つものなので、消してしまうと
        入れた瞬間に、そのプロジェクトの他の道具が止まる。
        """
        self.write_settings(
            {
                "env": {"PYTHONUTF8": "1"},
                "permissions": {"deny": ["Read(**/.env*)"]},
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Write",
                            "hooks": [{"type": "command", "command": "sh lint.sh"}],
                        }
                    ]
                },
            }
        )
        self.run_setup()

        data = self.read_settings()
        self.assertEqual(data["env"]["PYTHONUTF8"], "1")
        self.assertEqual(data["permissions"]["deny"], ["Read(**/.env*)"])
        self.assertIn("sh lint.sh", self.commands_of(data, "PostToolUse"))
        self.assertEqual(len(self.commands_of(data, "PostToolUse")), 2)

    def test_keeps_the_copy_it_took_before_the_first_change(self):
        """写しは最初の 1 回だけ取る。

        毎回取り直すと、打ち直した数だけ写しが新しくなり、戻れるのは 1 手前
        ――そこには既に ccnavi が入っている――までになる。入れる前の姿へ
        戻す手立てが消える。
        """
        self.write_settings({"env": {"MY_IMPORTANT": "keep"}})
        self.run_setup("--mode", "enable")
        self.run_setup("--mode", "enable", "--all")

        with open(self.settings_path() + ".bak", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved, {"env": {"MY_IMPORTANT": "keep"}})


class DoesNotWeakenTheGuard(SetupTest):
    def test_refuses_disable(self):
        """`disable` は書かない。

        監視される側が書けるファイルから監視を止める形になる。設定lint が
        error として報告する記述を、導入の側が作ってはいけない。
        """
        result = self.run_setup("--mode", "disable")
        self.assertEqual(result.returncode, 2)
        self.assertIn("disable", result.stderr)
        self.assertFalse(os.path.exists(self.settings_path()))

    def test_refuses_a_newline_in_the_bin_path(self):
        """`--bin` に改行を入れて env を注ぎ込む形を止める。

        値を「名前=値」の行に組んでから割ると、値の中の改行がそのまま行の
        区切りになる。しかも実行ファイルの行は最後なので、注いだ分が後勝ちで
        効く。ここを通すと、上のテストが拒んだ disable を --bin から書ける。
        """
        result = self.run_setup(
            "--mode", "enable", "--bin", "dist/ccnavi/ccnavi\nCCNAVI_MODE=disable"
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse(os.path.exists(self.settings_path()))

    def test_refuses_an_empty_bin_path(self):
        """空の実行ファイルは、7 つの hook すべてを壊す。

        書かれるのはワークスペースルートのディレクトリを起動しようとする 1 行に
        なり、しかも「まだ無いもの」にも挙がらない（ディレクトリは在るので）。
        """
        result = self.run_setup("--bin", "")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(os.path.exists(self.settings_path()))

    def test_refuses_a_bin_path_that_climbs_out_of_the_project(self):
        """`..` はプロジェクトの外を指す。絶対パスを拒む理由がそのまま当たる。

        実行ファイルの綴りは判定器の実体そのもの（selfguard.py 冒頭）。
        差し替えられると、ルールを 1 行も変えずに判定を入れ替えられる。
        """
        result = self.run_setup("--bin", "../../elsewhere/fake")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(os.path.exists(self.settings_path()))

    def test_refuses_an_absolute_bin_path(self):
        """実行ファイルの綴りは相対だけ。

        `env` では `${CLAUDE_PROJECT_DIR}` が展開されないので、絶対で書くと
        3 つの環境で同じ 1 行が使えなくなる。
        """
        for path in ("/opt/ccnavi/ccnavi", "C:/opt/ccnavi", "//server/share/ccnavi"):
            with self.subTest(path=path):
                result = self.run_setup("--bin", path)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(os.path.exists(self.settings_path()))


class TellsWhatItDidNotChange(SetupTest):
    def test_reports_a_value_it_will_not_replace(self):
        """揃っているように見えて防御が消えている形を、黙って見逃さない。

        `CCNAVI_MODE=disable` が既に書かれた設定は、env も hook も欠けて
        いないので「変えるところがない」に見える。そこを黙って通ると、
        導入スクリプトが最も直接的な違反を素通りさせることになる。
        """
        self.run_setup("--mode", "dry-run")
        result = self.run_setup("--mode", "enable")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CCNAVI_MODE", result.stdout)
        self.assertIn("--force", result.stdout)
        self.assertEqual(self.read_settings()["env"]["CCNAVI_MODE"], "dry-run")

    def test_check_is_not_settled_when_a_value_differs(self):
        """--check は、値が違うだけのときも「揃っていない」と言う。

        不足の 2 つだけで終了コードを決めると、CI で --check を回す運用が、
        disable の書かれた設定に対して緑を出し続ける。
        """
        self.run_setup("--mode", "dry-run")
        result = self.run_setup("--mode", "enable", "--check")

        self.assertEqual(result.returncode, 1)
        self.assertIn("CCNAVI_MODE", result.stdout)

    def test_keeps_an_existing_value_unless_the_option_is_named(self):
        """置き換えるのは、この実行で名指しした値だけ。

        `--force` が既定値まで押し込むと、`--all` を足しに来た打ち直しが、
        その場で指定していない CCNAVI_MODE を既定の dry-run へ落とす。
        """
        self.run_setup("--mode", "enable")

        self.run_setup("--all", "--force")
        self.assertEqual(self.read_settings()["env"]["CCNAVI_MODE"], "enable")

        self.run_setup("--mode", "dry-run", "--force")
        self.assertEqual(self.read_settings()["env"]["CCNAVI_MODE"], "dry-run")

    def test_check_reports_without_writing(self):
        """--check は書かない。揃っていなければ終了コードで言う。"""
        result = self.run_setup("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("CCNAVI_BIN_PATH", result.stdout)
        self.assertIn("SubagentStop", result.stdout)
        self.assertFalse(os.path.exists(self.settings_path()))

        self.run_setup()
        result = self.run_setup("--check")
        self.assertEqual(result.returncode, 0, result.stdout)


class ReadsTheRegistrationCarefully(SetupTest):
    def test_does_not_add_a_second_registration_to_an_event_that_has_one(self):
        """すでに ccnavi が登録されているイベントには足さない。

        綴りは決め打ちにできないので、名前が入っているかどうかで見る。
        別の綴りで登録してあるプロジェクトに 2 本目を足すと、
        すべての呼び出しで判定が 2 回走る。
        """
        self.write_settings(
            {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "", "hooks": [{"type": "command", "command": "bin/CCNAVI"}]}
                    ]
                }
            }
        )
        self.run_setup()
        self.assertEqual(self.commands_of(self.read_settings(), "PreToolUse"), ["bin/CCNAVI"])

    def test_says_so_when_the_registration_is_spelled_differently(self):
        """別の綴りで登録されているイベントは、足さずに人へ見せる。

        どちらが正しいかをここで決められない。黙って足すと判定が 2 回走り、
        黙って飛ばすとそのイベントが落ちたままになる。
        """
        self.write_settings(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "",
                            "hooks": [{"type": "command", "command": "sh /src/ccnavi-app/lint.sh"}],
                        }
                    ]
                }
            }
        )
        result = self.run_setup()

        self.assertIn("PreToolUse", result.stdout)
        self.assertIn("別の綴り", result.stdout)
        self.assertEqual(len(self.commands_of(self.read_settings(), "PreToolUse")), 1)

    def test_registers_when_the_name_only_happens_to_be_a_substring(self):
        """無関係な hook にたまたま名前が混ざっているだけなら、登録する。

        部分一致だけで見ていると、この 1 件があるだけでイベントが丸ごと
        落ちる。しかも設定lint は PreToolUse の登録を検査していないので、
        判定が 1 行も走らない状態を誰も見つけられない。
        """
        self.write_settings(
            {
                "hooks": {
                    "Stop": [
                        {
                            "matcher": "",
                            "hooks": [{"type": "command", "command": "echo my-ccnavigation-tool"}],
                        }
                    ]
                }
            }
        )
        self.run_setup()
        self.assertIn(HOOK_COMMAND, self.commands_of(self.read_settings(), "Stop"))


class RefusesShapesItCannotHandle(SetupTest):
    def test_refuses_to_write_over_a_settings_file_it_cannot_read(self):
        """読めない設定ファイルには書かない。

        壊れた JSON を空のオブジェクトとして扱うと、書き損じたカンマ 1 つで
        そのプロジェクトの設定が丸ごと消える。
        """
        self.write_settings('{"env": {,}')

        result = self.run_setup()
        self.assertEqual(result.returncode, 2)
        with open(self.settings_path(), encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"env": {,}')

    def test_refuses_json_whose_shape_it_does_not_expect(self):
        """想定と違う型は、jq の生のエラーではなく 2 で断る。

        握りつぶして進むとどこかで必ず落ちる。落ちた先の終了コード（jq の 5）は
        このスクリプトが宣言していない値なので、呼んだ側は引数の誤りとも
        環境の不足とも区別が付かない。
        """
        for raw in (
            '{"env": ["A=1"]}',
            '{"env": "nope"}',
            '{"hooks": ["PreToolUse"]}',
            '{"hooks": {"PreToolUse": "run-me"}}',
            '{"hooks": {"Stop": ["oops"]}}',
            '{"hooks": {"Stop": [{"hooks": "x"}]}}',
            '{"hooks": {"Stop": [{"hooks": [{"command": 42}]}]}}',
            "null",
            "[]",
            '"just a string"',
        ):
            with self.subTest(raw=raw):
                self.write_settings(raw)
                result = self.run_setup()
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("ccnavi-setup:", result.stderr)
                with open(self.settings_path(), encoding="utf-8") as f:
                    self.assertEqual(f.read(), raw)

    def test_accepts_an_entry_without_a_hooks_key(self):
        """`hooks` を持たないエントリは、壊れてはいない。そのまま扱う。"""
        self.write_settings({"hooks": {"PreToolUse": [{"matcher": "Bash"}]}})
        result = self.run_setup()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(HOOK_COMMAND, self.commands_of(self.read_settings(), "PreToolUse"))

    def test_refuses_when_the_claude_directory_is_a_file(self):
        """`.claude` が普通のファイルなら、jq を回す前に断る。

        あとで mkdir が失敗すると、終了コード 1（--check の「揃っていない」）と
        衝突したうえ、生のエラーだけが出る。
        """
        with open(os.path.join(self.dir, ".claude"), "w", encoding="utf-8") as f:
            f.write("not a directory\n")

        result = self.run_setup()
        self.assertEqual(result.returncode, 2)
        self.assertIn("ccnavi-setup:", result.stderr)


class ReadsItsArguments(SetupTest):
    def test_help_exits_zero(self):
        result = self.run_raw("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--mode", result.stdout)

    def test_refuses_an_unknown_option(self):
        result = self.run_setup("--nope")
        self.assertEqual(result.returncode, 2)

    def test_refuses_an_option_without_its_value(self):
        for option in ("--mode", "--bin"):
            with self.subTest(option=option):
                result = self.run_raw(option)
                self.assertEqual(result.returncode, 2)

    def test_refuses_a_mode_it_does_not_know(self):
        for mode in ("off", "Enable", "yes"):
            with self.subTest(mode=mode):
                result = self.run_setup("--mode", mode)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(os.path.exists(self.settings_path()))

    def test_refuses_two_project_roots(self):
        """空文字を「まだ受け取っていない」と読むと、2 つ受け取れてしまう。"""
        result = self.run_raw("", self.dir, "--check")
        self.assertEqual(result.returncode, 2)

        result = self.run_raw(self.dir, self.dir)
        self.assertEqual(result.returncode, 2)

    def test_refuses_a_directory_that_is_not_there(self):
        result = self.run_raw(os.path.join(self.dir, "nope"))
        self.assertEqual(result.returncode, 2)

    def test_double_dash_ends_the_options(self):
        """`-` で始まる名前のディレクトリも対象にできる。"""
        odd = os.path.join(self.dir, "-weird")
        os.makedirs(odd)
        result = self.run_raw("--", odd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(os.path.join(odd, ".claude", "settings.json")))


class WritesWithoutLeavingTraces(SetupTest):
    def test_leaves_no_temporary_file(self):
        """設定ファイルの隣に残骸を置かない。

        見慣れないファイルがあると、それが設定なのか残骸なのかを人が
        判断できない。
        """
        self.run_setup()
        names = sorted(os.listdir(os.path.join(self.dir, ".claude")))
        self.assertEqual(names, ["settings.json"])

    def test_keeps_a_link_instead_of_replacing_it(self):
        """設定を 1 か所で持って張っている置き方を壊さない。

        `mv` はディレクトリエントリを差し替えるので、リンクが普通のファイルに
        なって実体には何も届かない。
        """
        shared = os.path.join(self.dir, "real.json")
        with open(shared, "w", encoding="utf-8") as f:
            json.dump({"env": {"KEEP": "yes"}}, f)
        os.makedirs(os.path.dirname(self.settings_path()), exist_ok=True)
        try:
            os.link(shared, self.settings_path())
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"ハードリンクを作れない: {exc}")

        result = self.run_setup("--mode", "enable")
        self.assertEqual(result.returncode, 0, result.stderr)

        with open(shared, encoding="utf-8") as f:
            landed = json.load(f)
        self.assertEqual(landed["env"]["CCNAVI_MODE"], "enable")
        self.assertEqual(landed["env"]["KEEP"], "yes")


class DeploysWhatTheProjectNeeds(SetupTest):
    """`--deploy` が、設定だけでは動かないものを配り元から写す。

    `dist/` は .gitignore に入っていて git では渡らず、`CCNAVI_BIN_PATH` は相対で
    しか書けない。よそで組んだ実行ファイルを指すこともできないので、対象
    プロジェクトの中に実体を置く経路がこのスクリプトに要る。
    """

    def make_source(self, built=True, parts=True):
        """配り元のふりをするディレクトリを作る。

        本物を組み立てない。PyInstaller に 11 秒かかるし、ここで見たいのは
        「どこから何を写すか」であって、実行ファイルの中身ではない。
        """
        src = tempfile.mkdtemp(prefix="ccnavi-source-")
        self.addCleanup(shutil.rmtree, src, ignore_errors=True)
        if built:
            binary = os.path.join(src, "dist", "ccnavi", "ccnavi")
            os.makedirs(os.path.join(src, "dist", "ccnavi", "_internal"))
            with open(binary, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\nexit 0\n")
            os.chmod(binary, 0o755)
            with open(
                os.path.join(src, "dist", "ccnavi", "_internal", "base_library.zip"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write("同梱物")
        if parts:
            os.makedirs(os.path.join(src, ".claude", "ccnavi"))
            with open(
                os.path.join(src, ".claude", "ccnavi", "rules.yml"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write("deny: []\n")
            os.makedirs(os.path.join(src, ".claude", "scripts"))
            for name in GATE_SCRIPTS:
                with open(
                    os.path.join(src, ".claude", "scripts", name),
                    "w",
                    encoding="utf-8",
                ) as f:
                    f.write(f"# {name}\n")
        return src

    def deployed(self, *parts):
        return os.path.join(self.dir, *parts)

    def test_writes_the_settings_and_copies_the_parts_in_one_run(self):
        """1 回で、設定を書き、実行ファイル・ルール・ゲートの sh を写す。"""
        src = self.make_source()
        result = self.run_setup("--mode", "enable", "--deploy", src)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertEqual(self.read_settings()["env"]["CCNAVI_MODE"], "enable")
        self.assertTrue(os.path.isfile(self.deployed("dist", "ccnavi", "ccnavi")))
        self.assertTrue(
            os.path.isfile(self.deployed("dist", "ccnavi", "_internal", "base_library.zip"))
        )
        self.assertTrue(os.path.isfile(self.deployed(*RULES_PARTS)))
        for name in GATE_SCRIPTS:
            self.assertTrue(os.path.isfile(self.deployed(".claude", "scripts", name)))

    def test_says_nothing_is_missing_after_it_copied(self):
        """写したあとは「まだ無いもの」が出ない。

        ここが出たままだと、配ったのか配れなかったのかを人が読み取れない。
        """
        src = self.make_source()
        result = self.run_setup("--mode", "enable", "--deploy", src)
        self.assertNotIn("まだ無いもの", result.stdout)
        self.assertIn("写した", result.stdout)

    def test_points_at_deploy_when_it_did_not_copy(self):
        """`--deploy` なしで打った人に、写す手立てがあることを見せる。"""
        result = self.run_setup("--mode", "enable")
        self.assertIn("まだ無いもの", result.stdout)
        self.assertIn("--deploy", result.stdout)

    @unittest.skipIf(os.name == "nt", "Windows の実行の許しは別の仕組み")
    def test_the_executable_can_still_be_run(self):
        """実行の許しを落とさない。

        落ちていると hook は「実行ファイルが無い」ではなく「起動できない」で
        黙って死ぬ。設定lint も実行ファイルは在ると言うので、誰も気付かない。
        """
        src = self.make_source()
        self.run_setup("--deploy", src)
        self.assertTrue(os.access(self.deployed("dist", "ccnavi", "ccnavi"), os.X_OK))

    def test_running_twice_does_not_copy_again(self):
        """2 回目は写さない。既にあるものとして並べる。"""
        src = self.make_source()
        self.run_setup("--deploy", src)
        with open(self.deployed(*RULES_PARTS), "w", encoding="utf-8") as f:
            f.write("# このプロジェクトで直したルール\n")

        result = self.run_setup("--deploy", src)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("配り先に既にあるので写していないもの", result.stdout)
        with open(self.deployed(*RULES_PARTS), encoding="utf-8") as f:
            self.assertIn("このプロジェクトで直した", f.read())

    def test_force_replaces_and_drops_the_previous_build(self):
        """`--force` なら入れ替える。前の組み立ての残りも持ち越さない。

        PyInstaller の同梱物は名前で引かれるので、前の版の同梱物が残ると
        新しい実行ファイルがそれを掴む。
        """
        src = self.make_source()
        self.run_setup("--deploy", src)
        stale = self.deployed("dist", "ccnavi", "_internal", "古い同梱物.so")
        with open(stale, "w", encoding="utf-8") as f:
            f.write("前の版")
        with open(self.deployed(*RULES_PARTS), "w", encoding="utf-8") as f:
            f.write("# 直したルール\n")

        result = self.run_setup("--deploy", src, "--force")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("入れ替えた", result.stdout)
        self.assertFalse(os.path.exists(stale))
        with open(self.deployed(*RULES_PARTS), encoding="utf-8") as f:
            self.assertEqual(f.read(), "deny: []\n")

    def test_follows_the_bin_option_for_where_it_puts_the_executable(self):
        """実行ファイルの行き先は `--bin` が決める。

        設定に書く綴りと実体の置き場が割れると、設定は書けているのに hook が
        どこにも無いものを起動する形になる。
        """
        src = self.make_source()
        result = self.run_setup("--bin", "tools/ccnavi/ccnavi", "--deploy", src)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertEqual(self.read_settings()["env"]["CCNAVI_BIN_PATH"], "tools/ccnavi/ccnavi")
        self.assertTrue(os.path.isfile(self.deployed("tools", "ccnavi", "ccnavi")))
        self.assertFalse(os.path.exists(self.deployed("dist")))

    def test_refuses_a_source_that_was_never_built(self):
        """組み立てていない配り元では、黙って進まない。

        報告だけにすると「配ったはずなのに実行ファイルが無い」が最後の一覧に
        しか出ず、打った人は配れたものとして先へ進む。
        """
        src = self.make_source(built=False)
        result = self.run_setup("--deploy", src)
        self.assertEqual(result.returncode, 2)
        self.assertIn("build.py", result.stderr)
        self.assertFalse(os.path.exists(self.settings_path()))

    def test_names_what_the_source_does_not_have(self):
        """配り元に無いものは、黙って飛ばさずに名前を挙げる。

        判定するものだけが入って何を止めるかが入らない形は、配った側の
        落ち度に見えないまま残る。
        """
        src = self.make_source(parts=False)
        result = self.run_setup("--deploy", src)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("配り元に無くて写せないもの", result.stdout)
        self.assertIn("rules.yml", result.stdout)
        self.assertTrue(os.path.isfile(self.deployed("dist", "ccnavi", "ccnavi")))

    def test_check_does_not_copy(self):
        """`--check` は写さない。揃っていないので 1 を返す。"""
        src = self.make_source()
        result = self.run_setup("--deploy", src, "--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("写す", result.stdout)
        self.assertFalse(os.path.exists(self.deployed("dist")))

    def test_check_is_settled_once_everything_is_there(self):
        src = self.make_source()
        self.run_setup("--deploy", src)
        result = self.run_setup("--deploy", src, "--check")
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_refuses_to_deploy_onto_itself(self):
        """配り元と配り先が同じなら断る。写す先が無い。"""
        src = self.make_source()
        result = self.run_raw(src, "--deploy", src)
        self.assertEqual(result.returncode, 2)
        self.assertIn("ccnavi-setup:", result.stderr)

    def test_refuses_a_source_that_is_not_there(self):
        result = self.run_setup("--deploy", os.path.join(self.dir, "どこにもない"))
        self.assertEqual(result.returncode, 2)

    def test_refuses_deploy_without_its_value(self):
        result = self.run_setup("--deploy")
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
