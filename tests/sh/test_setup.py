"""導入スクリプトの受入テスト。外からスクリプトを動かす。

読み返すのは、書かれた `.claude/settings.json` と標準出力・終了コードだけ。
中の関数も変数も見ない。

使い捨てのディレクトリを毎回作る。このリポジトリ自身に打つと、テストが
「コードのこと」ではなく「走った機械の設定ファイルのこと」を報告するし、
走らせるたびに自分の hook の登録が書き換わる。
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT

SCRIPT = os.path.join(ROOT, "scripts", "ccnavi-setup.sh")


def _launcher_source():
    """偽の配布元に置く振り分けの sh の中身。

    原本は `.ccnavi/scripts/ccnavi-launcher.sh`（設計 launcher-scripts）。人が写す前の
    ツリーにはまだ無いので、`test_launcher.py` と同じく環境変数 `CCNAVI_TEST_LAUNCHER` で
    名指しできる（相対ならリポジトリのルートから）。名指しが無ければ前の原本
    `scripts/ccnavi-launcher.sh` を読む。
    ここで見たいのは「どこへ配るか」と「中身が同じまま届くか」で、中身の版は問わない。
    """
    named = os.environ.get("CCNAVI_TEST_LAUNCHER")
    if named:
        return os.path.join(ROOT, named)
    for parts in ((".ccnavi", "scripts", "ccnavi-launcher.sh"), ("scripts", "ccnavi-launcher.sh")):
        path = os.path.join(ROOT, *parts)
        if os.path.isfile(path):
            return path
    return os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-launcher.sh")


LAUNCHER = _launcher_source()


def _load_build():
    """build.py を名前でなく場所で読む。`import build` は PyInstaller の作業場所
    （build/）や同名のパッケージを掴みうる。"""
    spec = importlib.util.spec_from_file_location("ccnavi_build", os.path.join(ROOT, "build.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# この機械で組み立てたときに build.py が書く目印。導入スクリプトはこれを自分の uname と
# 比べる。ここを本物の build_target から取るので、2 つの語がずれればテストが落ちる。
THIS_MACHINE = _load_build().build_target()
# どの機械とも一致しない目印。
ANOTHER_MACHINE = "haiku-riscv64"
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
REQUIRED_ENV = ("CCNAVI_MODE", "CCNAVI_LOG", "CCNAVI_BIN_PATH")
# 戻す働きの 2 つ（settings.py の RESTORE_IF_DENY_ENV / GUARD_CORE_FILES_ENV）。
# 書かなければ enable で動くので、dry-run で導入したときにここだけ本気で動くと、
# 様子を見ている人の手元でファイルが勝手に戻る。
GUARD_ENV = ("CCNAVI_RESTORE_IF_DENY", "CCNAVI_GUARD_CORE_FILES")
# チケットの承認の経路。enable か disable しか取らないので、モードには合わせない。
TICKET_APPROVAL_ENV = "CCNAVI_GUARD_TICKET_APPROVAL"
UNWATCHED_ENV = "CCNAVI_GUARD_UNWATCHED"
# チケット制御を使うか（settings.py の TICKET_CONTROL_ENV）。プロジェクトが導入のときに決める。
TICKET_CONTROL_ENV = "CCNAVI_TICKET_CONTROL"
# hook に登録される 1 行。README「設定」の見本と対になる。綴りが変わると、
# ccnavi 自身が守る対象（CCNAVI_BIN_PATH）と実際に起動するものがずれる。
HOOK_COMMAND = '"${CLAUDE_PROJECT_DIR}/${CCNAVI_BIN_PATH}"'
# --deploy が配るゲートの sh。拒否の文面が案内する「代わりに通る形」で、
# 無いと止められた側に逃げ道がない。
GATE_SCRIPTS = ("ccnavi-ticket.sh", "ccnavi-review.sh", "ccnavi-git.sh")
# 実際に配る sh。3 本が起動して最初に読む共通部（ccnavi-common.sh）も要る。
# 配らないと、配った先で 3 本とも「共通部が読めない」で落ちる。
# 承認済みチケットを運ぶ sh（ccnavi-push-approved.sh）も配る。ボードは承認のあとこれを
# 端末に送るので、配らないと配布先のボードは運べない（設計 approve-carry §1.5）。
DEPLOY_SCRIPTS = (*GATE_SCRIPTS, "ccnavi-common.sh", "ccnavi-push-approved.sh")
RULES_PARTS = (".ccnavi", "common", "rules.yml")
# --deploy が配る残りの設定 2 本（設計 §11.9）。リスクの配点は共通層、
# フェーズの種類は自身の層（scope がワークスペースのレイアウトに付くため）。
RISK_PARTS = (".ccnavi", "common", "risks.yml")
PHASES_PARTS = (".ccnavi", "config", "phases.yml")
# 置き場は 2 つに分けて固定する（設計 launcher-scripts §1）。hook が起動する振り分けの sh は
# ゲートの sh と同じ .ccnavi/scripts/、機械ごとの組み立ては .ccnavi/bin/<os>-<arch>/。
BIN_DIR_PARTS = (".ccnavi", "bin")
LAUNCHER_NAME = "ccnavi-launcher.sh"
LAUNCHER_PARTS = (".ccnavi", "scripts", LAUNCHER_NAME)
# CCNAVI_BIN_PATH に書く綴り。固定。
BIN_PATH = "/".join(LAUNCHER_PARTS)


def section(stdout, heading):
    """見出しで始まる塊の、字下げされた行を並べる。

    文面の中に綴りが 1 回出るだけを見ると、別の塊（置き換える env など）に出た綴りでも
    通ってしまう。どの塊に並んだかまで見る。
    """
    out = []
    inside = False
    for line in stdout.splitlines():
        if inside and line.startswith("  "):
            out.append(line.strip())
            continue
        inside = line.startswith(heading)
    return out


def names(lines, path):
    """行のどれかが、その綴りそのもので始まるか。長い綴りの頭と取り違えない。"""
    pattern = re.compile(re.escape(path) + r"(?![\w./-])")
    return any(pattern.match(line) for line in lines)


def clean_env(**extra):
    """テストを走らせているセッションの CCNAVI_* を持ち込まない。

    テストを回すのは ccnavi が入ったセッションで、その env には CCNAVI_* が並んでいる。
    持ち込むと、テストが「コードのこと」ではなく「走らせた人のセッション」を報告しうる。
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    env.update(extra)
    return env


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

    def run_setup(self, *args, env=None):
        return subprocess.run(
            [SHELL, SCRIPT, self.dir, *quiet_deploy(args)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env or clean_env(),
        )

    def run_raw(self, *args):
        """対象ディレクトリを自分で並べる形。引数の扱いを試すときに使う。"""
        return subprocess.run(
            [SHELL, SCRIPT, *quiet_deploy(args)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=clean_env(),
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
        self.assertEqual(env["CCNAVI_LOG"], "logs/log.jsonl")

    def test_does_not_write_the_common_layer_paths(self):
        """ADR-0052: 共通層の 3 本は `.ccnavi/common/` 固定なので、env には書かない。

        既定と同じ値を書いても動きは変わらないが、読まれない語が設定に残ると、
        そこを直せば置き場が動くと読める。`--all` の一覧にも書かない。
        """
        for args in (("--mode", "enable"), ("--mode", "enable", "--all")):
            self.run_setup(*args, "--force")
            env = self.read_settings()["env"]
            for name in ("CCNAVI_RULES", "CCNAVI_PHASES", "CCNAVI_RISK"):
                with self.subTest(args=args, name=name):
                    self.assertNotIn(name, env)

    def test_bin_path_points_at_the_launcher(self):
        """指すのは .ccnavi/scripts/ の振り分けの sh で、どの環境でも 1 行のまま（S1）。

        実行ファイルは機械ごとに .ccnavi/bin/<os>-<arch>/ に入り、どれを起動するかは sh が選ぶ。
        設定に機械の語や `.exe` が入ると、その 1 行が 1 つの環境でしか当たらなくなる。
        """
        self.run_setup()
        self.assertEqual(self.read_settings()["env"]["CCNAVI_BIN_PATH"], BIN_PATH)

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

    def test_the_unwatched_gate_is_written_as_enable(self):
        """確認できる者が居ないモードの門も、モードに合わせず enable で書く。

        この門も enable か disable しか取らない。止めずに報告する段は
        CCNAVI_MODE=dry-run が持つので、dry-run で導入したプロジェクトでも
        ここは enable のまま書く。切るプロジェクトは自分で 1 行書き換える。
        """
        self.run_setup()
        self.assertEqual(self.read_settings()["env"][UNWATCHED_ENV], "enable")

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

    def test_all_writes_the_settings_that_have_defaults(self):
        """--all は、既定と同じ値のつまみも設定ファイルに並べる。"""
        self.run_setup("--all")
        env = self.read_settings()["env"]
        self.assertEqual(env["CCNAVI_TICKETS_PROPOSAL"], "wip/tickets")
        self.assertEqual(env["CCNAVI_TICKETS_APPROVED"], ".ccnavi/tickets")
        self.assertEqual(env["CCNAVI_STATE"], "logs/state")
        self.assertEqual(env["CCNAVI_PROJECT_HOME"], ".ccnavi")

    def test_says_what_is_still_missing(self):
        """登録しただけでは動かないので、人が置くものを挙げる（S12）。

        振り分けの sh はゲートの sh と同じ並びに出る。
        """
        result = self.run_setup()
        missing = section(result.stdout, "まだ無いもの")
        self.assertTrue(names(missing, BIN_PATH), result.stdout)
        self.assertTrue(names(missing, f".ccnavi/bin/{THIS_MACHINE}/ccnavi"), result.stdout)
        self.assertTrue(names(missing, "/".join(RULES_PARTS)), result.stdout)
        for name in DEPLOY_SCRIPTS:
            self.assertTrue(names(missing, f".ccnavi/scripts/{name}"), name)


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
        for option in ("--mode",):
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

    def make_source(self, built=True, parts=True, target=THIS_MACHINE, launcher=True):
        """配布元のふりをするディレクトリを作る。

        本物を組み立てない。PyInstaller に 11 秒かかるし、ここで見たいのは
        「どこから何を配るか」であって、実行ファイルの中身ではない。

        target は build.py が dist/ccnavi.target に書く目印。None なら書かない
        （目印の無い配布元）。

        launcher が偽なら、.ccnavi/scripts/ccnavi-launcher.sh だけを置かない。
        """
        src = tempfile.mkdtemp(prefix="ccnavi-source-")
        self.addCleanup(shutil.rmtree, src, ignore_errors=True)
        if built:
            if target is not None:
                os.makedirs(os.path.join(src, "dist"), exist_ok=True)
                with open(os.path.join(src, "dist", "ccnavi.target"), "w", encoding="utf-8") as f:
                    f.write(target + "\n")
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
            os.makedirs(os.path.dirname(os.path.join(src, *RULES_PARTS)))
            with open(
                os.path.join(src, *RULES_PARTS),
                "w",
                encoding="utf-8",
            ) as f:
                f.write("deny: []\n")
            # 設定 3 本のひな形。risk は共通層、phases は自身の層（設計 §11.9）。
            with open(os.path.join(src, *RISK_PARTS), "w", encoding="utf-8") as f:
                f.write("version: 1\nlevels: {}\nfactors: []\n")
            os.makedirs(os.path.join(src, ".ccnavi", "config"))
            with open(os.path.join(src, *PHASES_PARTS), "w", encoding="utf-8") as f:
                f.write("version: 1\nphases: {}\n")
            os.makedirs(os.path.join(src, ".ccnavi", "scripts"))
            for name in DEPLOY_SCRIPTS:
                with open(
                    os.path.join(src, ".ccnavi", "scripts", name),
                    "w",
                    encoding="utf-8",
                ) as f:
                    f.write(f"# {name}\n")
            if launcher:
                # 振り分けの sh は本物を写し、ゲートの sh と同じ置き場に置く。配布先で
                # 中身が同じであることを見るため。モードは落として置く。配布元の置き方に
                # 依らず、配った先で実行ビットが付くことを見るため。
                shutil.copy(LAUNCHER, os.path.join(src, *LAUNCHER_PARTS))
                os.chmod(os.path.join(src, *LAUNCHER_PARTS), 0o644)
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
            env=clean_env(),
        )

    def built(self, target=THIS_MACHINE, *rest):
        """配った組み立ての置き場。rest を足すとその下。"""
        return self.deployed(*BIN_DIR_PARTS, target, *rest)

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
        self.assertTrue(os.path.isfile(self.built(THIS_MACHINE, "ccnavi")))
        self.assertTrue(os.path.isfile(self.built(THIS_MACHINE, "_internal", "base_library.zip")))
        with (
            open(self.deployed(*LAUNCHER_PARTS), encoding="utf-8") as f,
            open(LAUNCHER, encoding="utf-8") as g,
        ):
            self.assertEqual(f.read(), g.read())
        self.assertTrue(os.path.isfile(self.deployed(*RULES_PARTS)))
        self.assertTrue(os.path.isfile(self.deployed(*RISK_PARTS)))
        self.assertTrue(os.path.isfile(self.deployed(*PHASES_PARTS)))
        for name in DEPLOY_SCRIPTS:
            self.assertTrue(os.path.isfile(self.deployed(".ccnavi", "scripts", name)))

    def test_says_nothing_is_missing_after_it_copied(self):
        """配ったあとは「まだ無いもの」が出ない。

        ここが出たままだと、配ったのか配れなかったのかを人が読み取れない。
        """
        src = self.make_source()
        result = self.run_setup("--mode", "enable", "--deploy", src)
        self.assertNotIn("まだ無いもの", result.stdout)
        self.assertIn("配った", result.stdout)

    def test_deploys_the_launcher_next_to_the_gate_scripts(self):
        """振り分けの sh は .ccnavi/scripts/ へ、中身を変えずに配り、実行ビットを付ける（S2）。

        hook は sh を `sh` 経由でなく直に起動する。実行ビットが無いと 126 で起動せず、
        判定が 1 行も走らない。
        """
        src = self.make_source()
        result = self.run_setup("--deploy", src)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        with (
            open(self.deployed(*LAUNCHER_PARTS), encoding="utf-8") as f,
            open(LAUNCHER, encoding="utf-8") as g,
        ):
            self.assertEqual(f.read(), g.read())
        if os.name != "nt":
            self.assertTrue(os.access(self.deployed(*LAUNCHER_PARTS), os.X_OK))
        # sh の隣に機械ごとの置き場を作らない。sh はそこを探さない。
        self.assertFalse(os.path.exists(self.deployed(".ccnavi", "scripts", THIS_MACHINE)))

    def test_puts_the_executable_in_the_fixed_place(self):
        """実行ファイルは .ccnavi/bin/<built>/ に置く。置き場は固定（S3）。

        設定に書く綴りと実体の置き場が割れると、設定は書けているのに hook が
        どこにも無いものを起動する形になる。sh は自分の隣でなく ../bin/ を探すので、
        置き場がこの 1 か所に決まっていれば割れない。
        """
        src = self.make_source()
        result = self.run_setup("--deploy", src)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        self.assertEqual(self.read_settings()["env"]["CCNAVI_BIN_PATH"], BIN_PATH)
        self.assertTrue(os.path.isfile(self.built(THIS_MACHINE, "ccnavi")))
        self.assertTrue(os.path.isfile(self.built(THIS_MACHINE, "_internal", "base_library.zip")))
        self.assertEqual(sorted(os.listdir(self.deployed(*BIN_DIR_PARTS))), [THIS_MACHINE])
        self.assertNotIn("まだ無いもの", result.stdout)

    def test_refuses_a_bin_option_as_unknown_and_writes_nothing(self):
        """`--bin` は持たない。知らないオプションとして 2 で断り、何も書かない（S4）。

        置き場は固定なので、置き場を名指しするオプションは無い。
        """
        src = self.make_source()
        for args in (
            ("--bin", BIN_PATH),
            ("--bin",),
        ):
            with self.subTest(args=args):
                self.dir = tempfile.mkdtemp(prefix="ccnavi-setup-")
                self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
                self.make_git()
                result = self.run_setup("--mode", "enable", "--deploy", src, *args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("--bin は知らないオプションです", result.stderr)
                self.assertEqual(os.listdir(self.dir), [".git"])

    def test_names_the_push_script_when_the_source_lacks_it(self):
        """15. 配布元に ccnavi-push-approved.sh が無ければ、最後の「まだ無いもの」に挙げる。

        ボードは承認のあとこの sh を端末に送る。配れなかったことが最後の一覧に出ないと、
        配布先で運べない理由を人が読み取れない。
        """
        src = self.make_source()
        os.remove(os.path.join(src, ".ccnavi", "scripts", "ccnavi-push-approved.sh"))
        result = self.run_setup("--mode", "enable", "--deploy", src)
        self.assertIn("まだ無いもの", result.stdout, result.stdout + result.stderr)
        missing = result.stdout.split("まだ無いもの", 1)[1]
        self.assertIn("ccnavi-push-approved.sh", missing)
        self.assertFalse(
            os.path.exists(self.deployed(".ccnavi", "scripts", "ccnavi-push-approved.sh"))
        )
        # 他の sh は配ったので、一覧には出ない。
        self.assertNotIn("ccnavi-ticket.sh", missing)

    def test_no_deploy_writes_only_the_settings(self):
        """`--no-deploy` は配布物を置かない。何が無いかと、戻し方を見せる。"""
        result = self.run_setup("--mode", "enable", "--no-deploy")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_settings()["env"]["CCNAVI_MODE"], "enable")
        self.assertFalse(os.path.exists(self.deployed(*BIN_DIR_PARTS)))
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
        self.assertTrue(os.path.isfile(self.built(THIS_MACHINE, "ccnavi")))
        self.assertTrue(os.path.isfile(self.deployed(*LAUNCHER_PARTS)))
        self.assertTrue(os.path.isfile(self.deployed(*RULES_PARTS)))
        for name in DEPLOY_SCRIPTS:
            self.assertTrue(os.path.isfile(self.deployed(".ccnavi", "scripts", name)))

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
        self.assertTrue(os.access(self.built(THIS_MACHINE, "ccnavi"), os.X_OK))
        self.assertTrue(os.access(self.deployed(*LAUNCHER_PARTS), os.X_OK))

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
        stale = self.built(THIS_MACHINE, "_internal", "古い同梱物.so")
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
        self.assertIn("ccnavi-launcher.sh", result.stdout)
        self.assertTrue(os.path.isfile(self.built(THIS_MACHINE, "ccnavi")))

    def test_names_the_launcher_the_source_does_not_have(self):
        """配布元の .ccnavi/scripts/ に sh が無ければ、配れないものに挙げる（S13）。

        前の原本の置き場（scripts/ccnavi-launcher.sh）に在っても、そこからは配らない。
        拾うと、配布元の版が混ざったまま「配った」に見える。
        """
        src = self.make_source(launcher=False)
        os.makedirs(os.path.join(src, "scripts"), exist_ok=True)
        shutil.copy(LAUNCHER, os.path.join(src, "scripts", LAUNCHER_NAME))

        result = self.run_setup("--deploy", src)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        absent = section(result.stdout, "配布元に無くて配れないもの")
        self.assertTrue(names(absent, BIN_PATH), result.stdout)
        self.assertFalse(os.path.exists(self.deployed(*LAUNCHER_PARTS)))
        self.assertTrue(names(section(result.stdout, "まだ無いもの"), BIN_PATH), result.stdout)

    @unittest.skipIf(os.name == "nt", "Windows の実行の許しは別の仕組み")
    def test_puts_the_run_bit_back_on_a_launcher_it_keeps(self):
        """配布先の sh の実行ビットが落ちていれば、配らない回でも付け直す（S14）。

        sh は追跡するので、Windows で足した sh は 100644 で入り、別の機械で clone した
        直後は実行ビットが無い。配布先に既に在るからと触らずにいると、打ち直しても
        hook は 126 で起動しないままになる。中身は入れ替えない。
        """
        src = self.make_source()
        self.run_setup("--deploy", src)
        launcher = self.deployed(*LAUNCHER_PARTS)
        with open(launcher, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n# このプロジェクトで直した sh\n")
        os.chmod(launcher, 0o644)

        result = self.run_setup("--deploy", src)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.access(launcher, os.X_OK))
        with open(launcher, encoding="utf-8") as f:
            self.assertIn("このプロジェクトで直した", f.read())

    def test_check_does_not_copy(self):
        """`--check` は配らない。揃っていないので 1 を返す。"""
        src = self.make_source()
        result = self.run_setup("--deploy", src, "--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("配る", result.stdout)
        self.assertFalse(os.path.exists(self.deployed(*BIN_DIR_PARTS)))
        self.assertFalse(os.path.exists(self.deployed(*LAUNCHER_PARTS)))

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


class ChecksWhereTheExecutableRuns(DeploysWhatTheProjectNeeds):
    """実行ファイルを、組み立てた機械の置き場（`<os>-<arch>/`）に入れる。

    PyInstaller の実行ファイルは組み立てた機械の OS と CPU でしか動かない。機械ごとに
    置き場を分けるので、別の機械向けを配ってもこの機械の実行ファイルは上書きされない。
    ただしこの機械で動くものが無いことは、黙らずに言う。
    """

    def test_places_a_named_source_for_another_machine_in_its_own_place(self):
        src = self.make_source(target=ANOTHER_MACHINE)
        result = self.run_setup("--deploy", src)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.path.isfile(self.built(ANOTHER_MACHINE, "ccnavi")))
        self.assertFalse(os.path.exists(self.built(THIS_MACHINE)))
        self.assertIn(f"{ANOTHER_MACHINE} 向け", result.stdout)
        self.assertIn("まだ無いもの", result.stdout)
        self.assertIn(f"{THIS_MACHINE}/ccnavi", result.stdout)

    def test_default_source_for_another_machine_is_placed_too(self):
        src = self.make_source(target=ANOTHER_MACHINE)
        result = self.run_copied(self.install_script(src))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.path.isfile(self.built(ANOTHER_MACHINE, "ccnavi")))
        self.assertTrue(os.path.exists(self.settings_path()))

    def test_keeps_builds_for_two_machines_side_by_side(self):
        """Windows と WSL で同じフォルダを開く形。片方を配っても、もう片方は残る。"""
        self.run_setup("--deploy", self.make_source(target=ANOTHER_MACHINE))
        result = self.run_setup("--deploy", self.make_source())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.path.isfile(self.built(ANOTHER_MACHINE, "ccnavi")))
        self.assertTrue(os.path.isfile(self.built(THIS_MACHINE, "ccnavi")))
        self.assertNotIn("まだ無いもの", result.stdout)

    def test_deploys_a_build_for_this_machine_without_a_note(self):
        src = self.make_source()
        result = self.run_setup("--deploy", src)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.path.isfile(self.built(THIS_MACHINE, "ccnavi")))
        self.assertNotIn("確かめていません", result.stdout)
        self.assertNotIn("向けで", result.stdout)
        # 目印は dist/ccnavi/ の外にあるので、配布先へは写らない。
        for parts in (
            (".ccnavi", "ccnavi.target"),
            (*BIN_DIR_PARTS, "ccnavi.target"),
            (*BIN_DIR_PARTS, THIS_MACHINE, "ccnavi.target"),
        ):
            self.assertFalse(os.path.exists(self.deployed(*parts)), parts)

    def test_refuses_a_named_source_without_the_mark(self):
        """目印が無いと置き場を決められない。推測で置くと、別の機械向けを入れうる。"""
        src = self.make_source(target=None)
        result = self.run_setup("--deploy", src)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("ccnavi.target", result.stderr)
        self.assertFalse(os.path.exists(self.settings_path()))

    def test_default_source_without_the_mark_writes_only_the_settings(self):
        src = self.make_source(target=None)
        result = self.run_copied(self.install_script(src))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("配っていません", result.stdout)
        self.assertFalse(os.path.exists(self.deployed(*BIN_DIR_PARTS)))
        self.assertTrue(os.path.exists(self.settings_path()))

    def test_refuses_a_mark_that_would_leave_the_place(self):
        """目印はそのままディレクトリ名になる。区切りを含む値で置き場の外へ書かせない。"""
        for mark in ("../escape", "linux/x86_64", "linux"):
            with self.subTest(mark=mark):
                src = self.make_source(target=mark)
                result = self.run_setup("--deploy", src)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertFalse(os.path.exists(self.deployed(".ccnavi", "escape")))
                self.assertFalse(os.path.exists(self.deployed("escape")))


class KeepsTheExecutableOutOfGit(DeploysWhatTheProjectNeeds):
    """配った実行ファイルを .gitignore に足す。

    配布先は git で持ち回るのが普通なので、書かないと次のコミットで実行ファイルと
    _internal がまるごと履歴に入る。入ってしまうと、消すには履歴を書き換えるしか
    ない。
    """

    def test_adds_only_the_place_of_the_build(self):
        """足すのは /.ccnavi/bin/<built>/ の 1 行だけ（S5）。

        振り分けの sh はゲートの sh と同じく追跡する側に置く。無視すると、clone した先に
        sh が届かず hook が起動しない。置き場ごと（.ccnavi/ や .ccnavi/bin/）も無視しない。
        同じ .ccnavi/ の下にある rules.yml まで git から消え、そのプロジェクトが何を
        止めるかが渡らなくなる。
        """
        src = self.make_source()
        self.make_git()
        result = self.run_setup("--deploy", src)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        written = self.gitignore()
        lines = [line for line in written.splitlines() if line.strip() and not line.startswith("#")]
        self.assertEqual(lines, [f"/.ccnavi/bin/{THIS_MACHINE}/"])
        for wide in (
            "/.ccnavi/",
            "/.ccnavi/bin/",
            "/.ccnavi/common/",
            "/.ccnavi/scripts/",
            f"/{BIN_PATH}",
        ):
            self.assertNotIn(wide + "\n", written, wide)
        self.assertNotIn("rules.yml", written)
        self.assertIn(".gitignore に足した", result.stdout)

    def test_adds_the_place_of_each_machine_that_was_deployed(self):
        """別の機械の組み立てを配れば、その置き場も足す。"""
        self.make_git()
        self.run_setup("--deploy", self.make_source())
        self.run_setup("--deploy", self.make_source(target=ANOTHER_MACHINE))
        written = self.gitignore()
        self.assertEqual(written.count(f"/.ccnavi/bin/{THIS_MACHINE}/\n"), 1)
        self.assertEqual(written.count(f"/.ccnavi/bin/{ANOTHER_MACHINE}/\n"), 1)

    def test_keeps_the_lines_that_were_already_there(self):
        src = self.make_source()
        self.make_git()
        with open(os.path.join(self.dir, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("node_modules/\n")

        self.run_setup("--deploy", src)
        written = self.gitignore()
        self.assertIn("node_modules/\n", written)
        self.assertIn(f"/.ccnavi/bin/{THIS_MACHINE}/\n", written)

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


class LeavesAPathItDidNotWrite(DeploysWhatTheProjectNeeds):
    """既定でない CCNAVI_BIN_PATH（人が決めた綴り）は書き換えず、名指しする。"""

    def test_leaves_a_path_it_did_not_write_and_names_it(self):
        """既定でない綴りは書き換えず、名指しで 1 行出す。終了コードは 0（S10）。

        人が決めた綴りの先で何が使われているかを、このスクリプトは決められない。
        案内は settings.json の値の直し方。
        """
        custom = "dist/ccnavi/ccnavi"
        self.write_settings({"env": {"CCNAVI_BIN_PATH": custom}})
        result = self.run_setup("--deploy", self.make_source())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        self.assertEqual(self.read_settings()["env"]["CCNAVI_BIN_PATH"], custom)
        self.assertTrue(
            any(custom in line and BIN_PATH in line for line in result.stdout.splitlines()),
            result.stdout,
        )
        self.assertTrue(os.path.isfile(self.built(THIS_MACHINE, "ccnavi")))

    def test_check_is_not_settled_by_a_path_it_did_not_write(self):
        """既定でない綴りは、--check では「揃っていない」に数える（S10）。"""
        src = self.make_source()
        self.run_setup("--deploy", src)
        settled = self.run_setup("--deploy", src, "--check")
        self.assertEqual(settled.returncode, 0, settled.stdout)

        data = self.read_settings()
        data["env"]["CCNAVI_BIN_PATH"] = "dist/ccnavi/ccnavi"
        self.write_settings(data)
        result = self.run_setup("--deploy", src, "--check")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("dist/ccnavi/ccnavi", result.stdout)
        self.assertEqual(self.read_settings()["env"]["CCNAVI_BIN_PATH"], "dist/ccnavi/ccnavi")


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
