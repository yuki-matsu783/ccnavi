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
# 戻す働きの 2 つ（settings.py の RESTORE_IF_DENY_ENV / GUARD_CORE_FILES_ENV）。
# 書かなければ enable で動くので、dry-run で導入したときにここだけ本気で動くと、
# 様子を見ている人の手元でファイルが勝手に戻る。
GUARD_ENV = ("CCNAVI_RESTORE_IF_DENY", "CCNAVI_GUARD_CORE_FILES")
# チケットの承認の経路。enable か disable しか取らないので、モードには合わせない。
TICKET_APPROVAL_ENV = "CCNAVI_GUARD_TICKET_APPROVAL"
# チケット制御を使うか（settings.py の TICKET_CONTROL_ENV）。プロジェクトが導入のときに決める。
TICKET_CONTROL_ENV = "CCNAVI_TICKET_CONTROL"
# hook に登録される 1 行。README「設定」の見本と対になる。綴りが変わると、
# ccnavi 自身が守る対象（CCNAVI_BIN_PATH）と実際に起動するものがずれる。
HOOK_COMMAND = '"${CLAUDE_PROJECT_DIR}/${CCNAVI_BIN_PATH}"'
# もう効かない環境変数（settings.py の RETIRED_ENVS）。書かれていたら
# ccnavi の --lint が苦情を言う。導入スクリプトが作ってはいけない。
RETIRED_ENV = ("CCNAVI_TICKET", "CCNAVI_LEDGER", "CCNAVI_PROJECT_RULES")
# --deploy が配るゲートの sh。拒否の文面が案内する「代わりに通る形」で、
# 無いと止められた側に逃げ道がない。
GATE_SCRIPTS = ("ccnavi-ticket.sh", "ccnavi-review.sh", "ccnavi-git.sh")
# 実際に配る sh。3 本が起動して最初に読む共通部（ccnavi-common.sh）も要る。
# 配らないと、配った先で 3 本とも「共通部が読めない」で落ちる。
DEPLOY_SCRIPTS = (*GATE_SCRIPTS, "ccnavi-common.sh")
RULES_PARTS = (".claude", "ccnavi", "rules.yml")
# --deploy が配る残りの設定 2 本（設計 §25.9）。リスクの配点は共通層、
# フェーズの種類は自身の層（scope がワークスペースのレイアウトに付くため）。
RISK_PARTS = (".claude", "ccnavi", "risk.yml")
PHASES_PARTS = (".ccnavi", "config", "phases.yml")


def quiet_deploy(args):
    """配布元を名指ししていない呼び出しから、配布を外す。

    配布は既定で走る。設定の話をするテストでそれを許すと、テストが「コードの
    こと」ではなく「走った機械に dist/ が組み立ててあるかどうか」を報告する。
    配布そのものは DeploysWhatTheProjectNeeds が、偽の配布元を作って見る。
    """
    if any(a in ("--deploy", "--no-deploy") for a in args):
        return list(args)
    return ["--no-deploy", *args]


@unittest.skipUnless(SHELL, "sh も bash も見つからない")
@unittest.skipUnless(HAS_JQ, "jq が見つからない")
class SetupTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-setup-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def run_setup(self, *args):
        return subprocess.run(
            [SHELL, SCRIPT, self.dir, *quiet_deploy(args)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def run_raw(self, *args):
        """対象ディレクトリを自分で並べる形。引数の扱いを試すときに使う。"""
        return subprocess.run(
            [SHELL, SCRIPT, *quiet_deploy(args)],
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

    def vscode_path(self):
        return os.path.join(self.dir, ".vscode", "settings.json")

    def write_vscode(self, data):
        os.makedirs(os.path.dirname(self.vscode_path()), exist_ok=True)
        with open(self.vscode_path(), "w", encoding="utf-8") as f:
            if isinstance(data, str):
                f.write(data)
            else:
                json.dump(data, f)

    def read_vscode(self):
        with open(self.vscode_path(), encoding="utf-8") as f:
            return json.load(f)

    def read_vscode_text(self):
        with open(self.vscode_path(), encoding="utf-8") as f:
            return f.read()

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
        self.assertEqual(self.read_settings()["env"]["CCNAVI_BIN_PATH"], ".claude/ccnavi/ccnavi")

    def test_writes_the_guards_at_the_same_strength_as_the_mode(self):
        """戻す働きの 2 つは CCNAVI_MODE と同じ値で並ぶ。

        書かなければ 2 つとも enable で動く。dry-run で導入したつもりの
        プロジェクトで、判定は止めないのに戻す働きだけが本気で動く形になり、
        様子を見ている人の手元でファイルが勝手に戻る。
        """
        self.run_setup()
        env = self.read_settings()["env"]
        for name in GUARD_ENV:
            self.assertEqual(env[name], "dry-run", name)

    def test_the_guards_follow_an_enable_mode_too(self):
        """`--mode enable` なら 2 つとも enable。片方だけが残らない。"""
        self.run_setup("--mode", "enable")
        env = self.read_settings()["env"]
        for name in GUARD_ENV:
            self.assertEqual(env[name], "enable", name)

    def test_the_ticket_approval_gate_does_not_follow_a_dry_run_mode(self):
        """承認の門は dry-run で導入しても enable で書く。

        この門は enable か disable しか取らない。dry-run と書くと、ccnavi の
        `--lint` が error にするし、書いた人は止まらないつもりでいるのに
        実際は止まる。導入スクリプトがその食い違いを作らない。
        """
        self.run_setup()
        self.assertEqual(self.read_settings()["env"][TICKET_APPROVAL_ENV], "enable")

    def test_the_ticket_approval_gate_is_enable_under_an_enable_mode(self):
        self.run_setup("--mode", "enable")
        self.assertEqual(self.read_settings()["env"][TICKET_APPROVAL_ENV], "enable")

    def test_ticket_control_is_written_as_enable_by_default(self):
        """チケット制御は既定の enable でも常に書く。切るつまみを設定ファイルの中で
        見つけられるように。"""
        self.run_setup()
        self.assertEqual(self.read_settings()["env"][TICKET_CONTROL_ENV], "enable")

    def test_ticket_control_can_be_disabled_at_setup(self):
        """全体ルールだけで足りるプロジェクトは、導入のときに disable を選ぶ。"""
        self.run_setup("--ticket-control", "disable")
        self.assertEqual(self.read_settings()["env"][TICKET_CONTROL_ENV], "disable")

    def test_refuses_a_ticket_control_it_does_not_know(self):
        for value in ("off", "dry-run", "Enable"):
            with self.subTest(value=value):
                result = self.run_setup("--ticket-control", value)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(os.path.exists(self.settings_path()))

    def test_force_replaces_ticket_control_only_when_named(self):
        self.write_settings({"env": {TICKET_CONTROL_ENV: "disable"}})
        self.run_setup("--force", "--mode", "enable")
        self.assertEqual(self.read_settings()["env"][TICKET_CONTROL_ENV], "disable")
        self.run_setup("--force", "--ticket-control", "enable")
        self.assertEqual(self.read_settings()["env"][TICKET_CONTROL_ENV], "enable")

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
        self.assertEqual(env["CCNAVI_PHASES"], ".claude/ccnavi/phases.yml")
        self.assertEqual(env["CCNAVI_PROJECT_HOME"], ".ccnavi")

    def test_says_what_is_still_missing(self):
        """登録しただけでは動かないので、人が置くものを挙げる。"""
        result = self.run_setup()
        self.assertIn(".claude/ccnavi/ccnavi", result.stdout)
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
        """控えは最初の 1 回だけ取る。

        毎回取り直すと、打ち直した数だけ控えが新しくなり、戻れるのは 1 手前
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
    """`--deploy` が、設定だけでは動かないものを配布元から配る。

    `dist/` は .gitignore に入っていて git では渡らず、`CCNAVI_BIN_PATH` は相対で
    しか書けない。よそで組んだ実行ファイルを指すこともできないので、対象
    プロジェクトの中に実体を置く経路がこのスクリプトに要る。
    """

    def make_source(self, built=True, parts=True):
        """配布元のふりをするディレクトリを作る。

        本物を組み立てない。PyInstaller に 11 秒かかるし、ここで見たいのは
        「どこから何を配るか」であって、実行ファイルの中身ではない。
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
            # 設定 3 本のひな形。risk は共通層、phases は自身の層（設計 §25.9）。
            with open(os.path.join(src, *RISK_PARTS), "w", encoding="utf-8") as f:
                f.write("version: 1\nlevels: {}\nfactors: []\n")
            os.makedirs(os.path.join(src, ".ccnavi", "config"))
            with open(os.path.join(src, *PHASES_PARTS), "w", encoding="utf-8") as f:
                f.write("version: 1\nphases: {}\n")
            os.makedirs(os.path.join(src, ".claude", "scripts"))
            for name in DEPLOY_SCRIPTS:
                with open(
                    os.path.join(src, ".claude", "scripts", name),
                    "w",
                    encoding="utf-8",
                ) as f:
                    f.write(f"# {name}\n")
        return src

    def install_script(self, src):
        """配布元のふりをするディレクトリに、このスクリプト自身を置く。

        既定の配布元は「打ったスクリプトの置き場の 1 つ上」。本物の ccnavi の
        根をそのまま配布元にすると、テストが走った機械に dist/ が組み立てて
        あるかどうかで結果が変わる。
        """
        scripts = os.path.join(src, "scripts")
        os.makedirs(scripts, exist_ok=True)
        copied = os.path.join(scripts, os.path.basename(SCRIPT))
        shutil.copy(SCRIPT, copied)
        return copied

    def run_copied(self, script, *args):
        """配布を既定のまま走らせる。quiet_deploy を通さない。"""
        return subprocess.run(
            [SHELL, script, self.dir, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def make_git(self):
        """配布先を git のリポジトリに見せる。.gitignore を書くのはここだけ。"""
        os.makedirs(os.path.join(self.dir, ".git"), exist_ok=True)

    def gitignore(self):
        path = os.path.join(self.dir, ".gitignore")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()

    def deployed(self, *parts):
        return os.path.join(self.dir, *parts)

    def test_writes_the_settings_and_copies_the_parts_in_one_run(self):
        """1 回で、設定を書き、実行ファイル・ルール・ゲートの sh を配る。"""
        src = self.make_source()
        result = self.run_setup("--mode", "enable", "--deploy", src)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertEqual(self.read_settings()["env"]["CCNAVI_MODE"], "enable")
        self.assertTrue(os.path.isfile(self.deployed(".claude", "ccnavi", "ccnavi")))
        self.assertTrue(
            os.path.isfile(self.deployed(".claude", "ccnavi", "_internal", "base_library.zip"))
        )
        self.assertTrue(os.path.isfile(self.deployed(*RULES_PARTS)))
        self.assertTrue(os.path.isfile(self.deployed(*RISK_PARTS)))
        self.assertTrue(os.path.isfile(self.deployed(*PHASES_PARTS)))
        for name in DEPLOY_SCRIPTS:
            self.assertTrue(os.path.isfile(self.deployed(".claude", "scripts", name)))

    def test_says_nothing_is_missing_after_it_copied(self):
        """配ったあとは「まだ無いもの」が出ない。

        ここが出たままだと、配ったのか配れなかったのかを人が読み取れない。
        """
        src = self.make_source()
        result = self.run_setup("--mode", "enable", "--deploy", src)
        self.assertNotIn("まだ無いもの", result.stdout)
        self.assertIn("配った", result.stdout)

    def test_no_deploy_writes_only_the_settings(self):
        """`--no-deploy` は配布物を置かない。何が無いかと、戻し方を見せる。"""
        result = self.run_setup("--mode", "enable", "--no-deploy")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_settings()["env"]["CCNAVI_MODE"], "enable")
        self.assertFalse(os.path.exists(self.deployed(".claude", "ccnavi")))
        self.assertIn("まだ無いもの", result.stdout)
        self.assertIn("--no-deploy を外すと", result.stdout)

    def test_refuses_deploy_together_with_no_deploy(self):
        """どちらを通すかを、スクリプトの側で決めない。"""
        src = self.make_source()
        result = self.run_setup("--deploy", src, "--no-deploy")
        self.assertEqual(result.returncode, 2)
        self.assertIn("ccnavi-setup:", result.stderr)
        self.assertFalse(os.path.exists(self.settings_path()))

    def test_copies_without_being_told_where_from(self):
        """配布元を名指ししなくても配る。既定はスクリプト自身の置き場の 1 つ上。

        設定だけ書かれて実行ファイルが無い形は、hook が 7 つ登録されているのに
        何も起動しない、という一番分かりにくい壊れ方になる。そこが、打った人が
        `--deploy` を知っているかどうかで分かれない。
        """
        src = self.make_source()
        script = self.install_script(src)
        result = self.run_copied(script, "--mode", "enable")
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertEqual(self.read_settings()["env"]["CCNAVI_MODE"], "enable")
        self.assertTrue(os.path.isfile(self.deployed(".claude", "ccnavi", "ccnavi")))
        self.assertTrue(os.path.isfile(self.deployed(*RULES_PARTS)))
        for name in DEPLOY_SCRIPTS:
            self.assertTrue(os.path.isfile(self.deployed(".claude", "scripts", name)))

    def test_writes_the_settings_when_the_default_source_is_not_built(self):
        """既定の配布元が組み立てられていなくても、設定は書く。

        名指しされていない配布元が空なのは、打った人の誤りではない。ここで
        断ると、組み立てていない機械では設定すら書けなくなる。
        """
        src = self.make_source(built=False)
        script = self.install_script(src)
        result = self.run_copied(script, "--mode", "enable")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_settings()["env"]["CCNAVI_MODE"], "enable")
        self.assertIn("配っていません", result.stdout)
        self.assertIn("build.py", result.stdout)

    def test_does_not_refuse_when_the_default_source_is_the_target(self):
        """ccnavi 自身に打っても止まらない。配る先が無いので、配布だけ諦める。"""
        src = self.make_source()
        script = self.install_script(src)
        result = subprocess.run(
            [SHELL, script, src, "--mode", "enable"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("配布元と配布先が同じ", result.stdout)
        with open(os.path.join(src, ".claude", "settings.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["env"]["CCNAVI_MODE"], "enable")

    @unittest.skipIf(os.name == "nt", "Windows の実行の許しは別の仕組み")
    def test_the_executable_can_still_be_run(self):
        """実行の許しを落とさない。

        落ちていると hook は「実行ファイルが無い」ではなく「起動できない」で
        黙って死ぬ。設定lint も実行ファイルは在ると言うので、誰も気付かない。
        """
        src = self.make_source()
        self.run_setup("--deploy", src)
        self.assertTrue(os.access(self.deployed(".claude", "ccnavi", "ccnavi"), os.X_OK))

    def test_running_twice_does_not_copy_again(self):
        """2 回目は配らない。既にあるものとして並べる。"""
        src = self.make_source()
        self.run_setup("--deploy", src)
        with open(self.deployed(*RULES_PARTS), "w", encoding="utf-8") as f:
            f.write("# このプロジェクトで直したルール\n")

        result = self.run_setup("--deploy", src)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("配布先に既にあるので配っていないもの", result.stdout)
        with open(self.deployed(*RULES_PARTS), encoding="utf-8") as f:
            self.assertIn("このプロジェクトで直した", f.read())

    def test_force_replaces_and_drops_the_previous_build(self):
        """`--force` なら入れ替える。前の組み立ての残りも持ち越さない。

        PyInstaller の同梱物は名前で引かれるので、前の版の同梱物が残ると
        新しい実行ファイルがそれを掴む。
        """
        src = self.make_source()
        self.run_setup("--deploy", src)
        stale = self.deployed(".claude", "ccnavi", "_internal", "古い同梱物.so")
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
        # 既定の置き場には置かれない。rules.yml は同じディレクトリへ配られるので、
        # 見るのはディレクトリではなく実行ファイルの有無。
        self.assertFalse(os.path.exists(self.deployed(".claude", "ccnavi", "ccnavi")))

    def test_refuses_a_source_that_was_never_built(self):
        """組み立てていない配布元では、黙って進まない。

        報告だけにすると「配ったはずなのに実行ファイルが無い」が最後の一覧に
        しか出ず、打った人は配れたものとして先へ進む。
        """
        src = self.make_source(built=False)
        result = self.run_setup("--deploy", src)
        self.assertEqual(result.returncode, 2)
        self.assertIn("build.py", result.stderr)
        self.assertFalse(os.path.exists(self.settings_path()))

    def test_names_what_the_source_does_not_have(self):
        """配布元に無いものは、黙って飛ばさずに名前を挙げる。

        判定するものだけが入って何を止めるかが入らない形は、配った側の
        落ち度に見えないまま残る。
        """
        src = self.make_source(parts=False)
        result = self.run_setup("--deploy", src)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("配布元に無くて配れないもの", result.stdout)
        self.assertIn("rules.yml", result.stdout)
        self.assertTrue(os.path.isfile(self.deployed(".claude", "ccnavi", "ccnavi")))

    def test_check_does_not_copy(self):
        """`--check` は配らない。揃っていないので 1 を返す。"""
        src = self.make_source()
        result = self.run_setup("--deploy", src, "--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("配る", result.stdout)
        self.assertFalse(os.path.exists(self.deployed(".claude", "ccnavi")))

    def test_check_is_settled_once_everything_is_there(self):
        src = self.make_source()
        self.run_setup("--deploy", src)
        result = self.run_setup("--deploy", src, "--check")
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_refuses_to_deploy_onto_itself(self):
        """配布元と配布先が同じなら断る。配る先が無い。"""
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


class KeepsTheExecutableOutOfGit(DeploysWhatTheProjectNeeds):
    """配った実行ファイルを .gitignore に足す。

    配布先は git で持ち回るのが普通なので、書かないと次のコミットで実行ファイルと
    _internal がまるごと履歴に入る。入ってしまうと、消すには履歴を書き換えるしか
    ない。
    """

    def test_adds_the_executable_and_its_bundle(self):
        src = self.make_source()
        self.make_git()
        result = self.run_setup("--deploy", src)
        self.assertEqual(result.returncode, 0, result.stderr)

        written = self.gitignore()
        self.assertIn("/.claude/ccnavi/ccnavi\n", written)
        # 3 つの環境で同じリポジトリを開くので、両方の綴りを書く。PyInstaller が
        # Windows でだけ .exe を付ける。
        self.assertIn("/.claude/ccnavi/ccnavi.exe\n", written)
        self.assertIn("/.claude/ccnavi/_internal/\n", written)
        self.assertIn(".gitignore に足した", result.stdout)

    def test_keeps_the_lines_that_were_already_there(self):
        src = self.make_source()
        self.make_git()
        with open(os.path.join(self.dir, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("node_modules/\n")

        self.run_setup("--deploy", src)
        written = self.gitignore()
        self.assertIn("node_modules/\n", written)
        self.assertIn("/.claude/ccnavi/_internal/\n", written)

    def test_does_not_lose_the_last_line_without_a_newline(self):
        """末尾に改行が無いファイルへ足しても、最後の行と繋がらない。"""
        src = self.make_source()
        self.make_git()
        with open(os.path.join(self.dir, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("*.log")

        self.run_setup("--deploy", src)
        self.assertIn("*.log\n", self.gitignore())

    def test_running_twice_does_not_write_the_same_line_again(self):
        src = self.make_source()
        self.make_git()
        self.run_setup("--deploy", src)
        first = self.gitignore()

        result = self.run_setup("--deploy", src)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.gitignore(), first)
        self.assertNotIn(".gitignore に足した", result.stdout)

    def test_does_not_ignore_the_rules_that_sit_next_to_the_executable(self):
        """置き場ごと無視しない。

        `--bin` の綴りによっては、実行ファイルの置き場が rules.yml と同じ
        ディレクトリになる。そこを丸ごと無視すると、そのプロジェクトが何を
        止めるかまで git から消える。
        """
        src = self.make_source()
        self.make_git()
        result = self.run_setup("--bin", ".claude/ccnavi/ccnavi", "--deploy", src)
        self.assertEqual(result.returncode, 0, result.stderr)

        written = self.gitignore()
        self.assertIn("/.claude/ccnavi/ccnavi\n", written)
        self.assertIn("/.claude/ccnavi/_internal/\n", written)
        self.assertNotIn("/.claude/ccnavi/\n", written)
        self.assertNotIn("rules.yml", written)

    def test_writes_nothing_when_the_target_is_not_a_git_repository(self):
        """git で持っていない配布先に .gitignore を置いても、誰も読まない。"""
        src = self.make_source()
        result = self.run_setup("--deploy", src)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(self.gitignore())

    def test_check_does_not_write_the_lines(self):
        src = self.make_source()
        self.make_git()
        result = self.run_setup("--deploy", src, "--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn(".gitignore に足す", result.stdout)
        self.assertIsNone(self.gitignore())

    def test_is_not_settled_while_the_lines_are_missing(self):
        """配布が済んでいても、.gitignore が欠けていれば揃っていない。

        --check を門にしている手順がそこを通すと、次のコミットで実行ファイルが
        履歴に入る。
        """
        src = self.make_source()
        self.make_git()
        self.run_setup("--deploy", src)
        with open(os.path.join(self.dir, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("node_modules/\n")

        result = self.run_setup("--deploy", src, "--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn(".gitignore に足す", result.stdout)


class WritesTheVscodeSettings(SetupTest):
    """VS Code へ渡す設定。README「worktreeをVSCODEで見えるようにする」と対になる。

    ccnavi は作業を .claude/worktrees/ の中でさせる。この 1 行が無いと、
    エディタからは main の作業ツリーしか見えないまま作業が進む。
    """

    def test_creates_the_file_when_it_is_not_there(self):
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_vscode(), {"git.detectWorktrees": True})

    def test_keeps_settings_that_have_nothing_to_do_with_ccnavi(self):
        """VS Code の他の設定は残す。控えも 1 つ取る。"""
        self.write_vscode({"editor.tabSize": 2})
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)

        landed = self.read_vscode()
        self.assertEqual(landed["editor.tabSize"], 2)
        self.assertIs(landed["git.detectWorktrees"], True)
        self.assertTrue(os.path.exists(self.vscode_path() + ".bak"))

    def test_running_twice_changes_nothing(self):
        self.run_setup()
        before = self.read_vscode_text()
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_vscode_text(), before)
        self.assertFalse(os.path.exists(self.vscode_path() + ".bak"))

    def test_keeps_a_value_the_person_wrote(self):
        """`false` と書いた人の判断を消さない。並べて見せるだけ。"""
        self.write_vscode({"git.detectWorktrees": False})
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(self.read_vscode()["git.detectWorktrees"], False)
        self.assertIn("git.detectWorktrees", result.stdout)

        check = self.run_setup("--check")
        self.assertEqual(check.returncode, 1, check.stdout)

    def test_does_not_touch_a_file_it_cannot_read(self):
        """VS Code の設定ファイルはコメントを書ける（JSONC）。jq は読めない。

        ここで死ぬと、ccnavi と関係のない書き方のせいで .claude/settings.json
        まで書けなくなる。触らずに人へ渡して、残りは進める。
        """
        jsonc = '{\n  // worktree は見せない\n  "git.detectWorktrees": false\n}\n'
        self.write_vscode(jsonc)
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_vscode_text(), jsonc)
        self.assertIn(".vscode/settings.json", result.stdout)
        self.assertIn("--no-vscode", result.stdout)
        # 肝心の登録は済んでいる。
        self.assertIn("CCNAVI_BIN_PATH", self.read_settings()["env"])

    def test_no_vscode_leaves_it_alone(self):
        result = self.run_setup("--no-vscode")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(self.vscode_path()))

        check = self.run_setup("--check", "--no-vscode")
        self.assertEqual(check.returncode, 0, check.stdout)

    def test_check_is_not_settled_when_the_line_is_missing(self):
        self.run_setup("--no-vscode")
        check = self.run_setup("--check")
        self.assertEqual(check.returncode, 1, check.stdout)
        self.assertIn("git.detectWorktrees", check.stdout)
        self.assertFalse(os.path.exists(self.vscode_path()), "--check は書かない")


if __name__ == "__main__":
    unittest.main()
