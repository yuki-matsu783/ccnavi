"""ccnavi 自身の設定ファイルを守る面の受入テスト。

道具を外から動かす。本物の git リポジトリを一時ディレクトリに作り、実行前の
payload で控えを取らせ、設定ファイルを壊してから実行後の payload を渡し、
ファイルが実際にどうなったかを読む。

ここで確かめたいのは 1 つに尽きる。ルールファイルを壊す道と、壊れたことに
気づく道が、同じファイルに乗っていないこと。ルール由来の保護は、ルールを
空にされると保護領域ごと消える。この面はそこを埋めるために在る。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

from ccnavi import selfguard, shellread
from tests.inproc import run_ccnavi

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RULES = {
    "version": 3,
    "deny": [
        {
            "id": "push",
            "match": "Bash",
            "glob": "*git push*",
            "message": "git push is not run by the agent.",
        }
    ],
    "allow": [{"id": "anything-else", "match": "Bash|Read|Write|Edit", "regex": "."}],
}

SETTINGS = {"hooks": {"PreToolUse": [], "PostToolUse": []}, "env": {"CCNAVI_MODE": "enable"}}


def git(repo, *args):
    done = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if done.returncode != 0:
        raise AssertionError(f"git {args}: {done.stderr}")
    return done.stdout


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class SelfGuardTest(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="ccnavi-selfguard-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)

        git(self.repo, "init", "--quiet")
        self.settings = os.path.join(self.repo, ".claude", "settings.json")
        self.rules = os.path.join(self.repo, ".claude", "ccnavi", "rules.yml")
        write(self.settings, json.dumps(SETTINGS, indent=2) + "\n")
        write(self.rules, json.dumps(RULES))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "--quiet", "-m", "init")

        self.state = os.path.join(self.repo, "state")
        self.log = os.path.join(self.repo, "log.jsonl")

    def binary(self, text="MZ fake executable\n"):
        """実行ファイルの代わりを置く。`.gitignore` の中に在る想定なので、
        コミットしない。git から戻せない対象がこれにあたる。"""
        path = os.path.join(self.repo, "dist", "ccnavi", "ccnavi.exe")
        write(path, text)
        return path

    def worktree(self, name="w1"):
        """本物の作業ツリーを `.claude/worktrees/<名前>` に作る。

        git に作らせる。守る側は `.git` ファイルと main の登録の相互参照が
        両向きに揃ったものだけを作業ツリーと呼ぶので、手でディレクトリを
        置いただけでは対象にならない。
        """
        path = os.path.join(self.repo, ".claude", "worktrees", name)
        git(self.repo, "worktree", "add", "--quiet", "-b", name, path)
        return path

    def copy_in(self, work, *parts):
        """作業ツリーの中の写しの綴り。"""
        return os.path.join(work, ".claude", *parts)

    def run_hook(
        self,
        event,
        mode="enable",
        setting="enable",
        session="s1",
        tool="Bash",
        bin="",
        **tool_input,
    ):
        payload = json.dumps(
            {
                "hook_event_name": event,
                "tool_name": tool,
                "tool_input": tool_input or {"command": "ls"},
                "session_id": session,
            }
        )
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        if bin:
            environment["CCNAVI_BIN_PATH"] = bin
        return run_ccnavi(
            [
                "--root",
                self.repo,
                "--rules",
                self.rules,
                "--state",
                self.state,
                "--log",
                self.log,
                "--mode",
                mode,
                "--guard-core-files",
                setting,
                # ルール由来の保護は切っておく。両方が同じファイルについて
                # 別々に口を出すと、どちらが戻したのかがテストから見えない。
                "--restore-if-deny",
                "disable",
            ],
            input=payload,
            cwd=ROOT,
            env=environment,
        )

    def store(self):
        """実体の置き場に在るものの中身。セッションをまたいで共有する側。"""
        found = os.path.join(self.state, "selfguard", "store")
        if not os.path.isdir(found):
            return []
        return sorted(read(os.path.join(found, name)) for name in os.listdir(found))

    def sessions(self):
        """控えを持っているセッションの名前。"""
        found = os.path.join(self.state, "selfguard")
        return sorted(
            name for name in os.listdir(found) if os.path.isdir(os.path.join(found, name))
        )

    def backdate(self, *paths, days=10):
        """置き場ごと更新時刻を古くする。掃除の日付をまたがせるため。"""
        old = time.time() - days * 86400
        for path in paths:
            for name in os.listdir(path):
                os.utime(os.path.join(path, name), (old, old))
            os.utime(path, (old, old))

    def records(self):
        with open(self.log, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    # 控えを取って戻す

    def test_書き換えられたルールファイルは直前の内容に戻る(self):
        self.run_hook("PreToolUse")
        write(self.rules, json.dumps({"version": 3, "deny": []}))

        result = self.run_hook("PostToolUse")

        self.assertEqual(json.loads(read(self.rules)), RULES)
        self.assertIn("rules.yml", result.stdout)
        self.assertIn("restored", result.stdout)

    def test_ルールを空にされても保護は消えない(self):
        # ルール由来の保護は、保護領域をルールファイルから導く。deny を空に
        # されるとその一覧ごと消えるので、実行後の監視は何も検知しない。
        # この面はルールを読まずに対象を決めるので、そこで止まらない。
        self.run_hook("PreToolUse")
        write(self.rules, json.dumps({"version": 3, "deny": [], "ask": [], "allow": []}))
        write(self.settings, "{}\n")

        self.run_hook("PostToolUse")

        self.assertEqual(json.loads(read(self.rules)), RULES)
        self.assertEqual(json.loads(read(self.settings)), SETTINGS)

    def test_hook_の登録を消された設定ファイルも戻る(self):
        self.run_hook("PreToolUse")
        broken = dict(SETTINGS)
        broken["hooks"] = {"PreToolUse": []}
        write(self.settings, json.dumps(broken))

        self.run_hook("PostToolUse")

        self.assertEqual(json.loads(read(self.settings))["hooks"], SETTINGS["hooks"])

    def test_直前の断面に戻すのでコミットしていない編集は残る(self):
        # git から戻すとコミット済みの内容まで巻き戻り、人の書きかけが消える。
        # 控えから戻せば、戻る先はこのツール呼び出しの直前になる。
        edited = json.dumps(
            RULES
            | {"version": 3, "ask": [{"id": "x", "match": "Bash", "regex": "y", "message": "z"}]}
        )
        write(self.rules, edited)
        self.run_hook("PreToolUse")
        write(self.rules, "{}")

        self.run_hook("PostToolUse")

        self.assertEqual(json.loads(read(self.rules)), json.loads(edited))

    # 消されたとき

    def test_消された設定ファイルは実行前に戻る(self):
        self.run_hook("PreToolUse")
        os.remove(self.settings)

        result = self.run_hook("PreToolUse")

        self.assertTrue(os.path.exists(self.settings))
        self.assertEqual(json.loads(read(self.settings)), SETTINGS)
        self.assertIn("settings.json", result.stdout)

    def test_控えが無ければ_git_から戻る(self):
        # 実行前を通らずに消された場合。控えの置き場ごと消えた形も同じ。
        os.remove(self.rules)

        self.run_hook("PreToolUse")

        self.assertTrue(os.path.exists(self.rules))
        self.assertEqual(json.loads(read(self.rules)), RULES)

    # 置いていないファイル

    def test_置いていない設定ファイルについては何も言わない(self):
        # settings.local.json は置かないのが普通。無いことを毎回報告すると、
        # 呼び出しのたびに 1 行増えて、本当に言うべき 1 行が埋もれる。
        result = self.run_hook("PreToolUse")

        self.assertNotIn("settings.local.json", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_置いていない設定ファイルが現れたら言うが消さない(self):
        self.run_hook("PreToolUse")
        local = os.path.join(self.repo, ".claude", "settings.local.json")
        write(local, '{"hooks": {}}\n')

        result = self.run_hook("PostToolUse")

        self.assertTrue(os.path.exists(local), "人が置くこともあるファイルを消さない")
        self.assertIn("settings.local.json", result.stdout)

    # 作業ツリーの中の写し

    def test_作業ツリーの中の設定ファイルも戻る(self):
        # その場では誰も読まないファイルだが、統合すれば main の hook の
        # 登録になる。止める側も気づく側も無い道なので、ここで戻す。
        work = self.worktree()
        copy = self.copy_in(work, "settings.json")
        self.run_hook("PreToolUse")
        write(copy, "{}\n")

        result = self.run_hook("PostToolUse")

        self.assertEqual(json.loads(read(copy)), SETTINGS)
        self.assertIn("restored", result.stdout)
        self.assertIn("統合すれば", result.stdout)

    def test_作業ツリーの中のルールファイルも戻る(self):
        work = self.worktree()
        copy = self.copy_in(work, "ccnavi", "rules.yml")
        self.run_hook("PreToolUse")
        write(copy, json.dumps({"version": 3, "deny": []}))

        self.run_hook("PostToolUse")

        self.assertEqual(json.loads(read(copy)), RULES)

    def test_作業ツリーでないディレクトリは守らない(self):
        # `.claude/worktrees/` の下に在るだけのディレクトリ。参考実装の写しを
        # 置いた形がこれで、守りに行くと人のファイルを勝手に戻すことになる。
        fake = os.path.join(self.repo, ".claude", "worktrees", "not-a-tree")
        copy = self.copy_in(fake, "settings.json")
        write(copy, "{}\n")
        self.run_hook("PreToolUse")
        write(copy, '{"changed": true}\n')

        self.run_hook("PostToolUse")

        self.assertEqual(json.loads(read(copy)), {"changed": True})

    def test_消された写しは実行前に戻る(self):
        work = self.worktree()
        copy = self.copy_in(work, "settings.json")
        self.run_hook("PreToolUse")
        os.remove(copy)

        self.run_hook("PreToolUse")

        self.assertEqual(json.loads(read(copy)), SETTINGS)

    def test_控えが無ければ作業ツリーの側の_git_から戻る(self):
        # 控えを取る前に書き換えられた回。main の git は
        # `.claude/worktrees/` を無視しているので、写しのコミット済みの内容を
        # 持っているのは、その作業ツリー自身の git のほうになる。
        work = self.worktree()
        copy = self.copy_in(work, "settings.json")
        write(copy, "{}\n")

        self.run_hook("PostToolUse")

        self.assertEqual(json.loads(read(copy)), SETTINGS)

    def test_写しの控えは作業ツリーごとに分かれる(self):
        # 取り違えると、片方の作業ツリーの内容がもう片方に書き戻される。
        one = self.copy_in(self.worktree("w1"), "settings.json")
        two = self.copy_in(self.worktree("w2"), "settings.json")
        write(one, json.dumps({"env": {"A": "1"}}))
        write(two, json.dumps({"env": {"B": "2"}}))
        self.run_hook("PreToolUse")
        write(one, "{}\n")
        write(two, "{}\n")

        self.run_hook("PostToolUse")

        self.assertEqual(json.loads(read(one)), {"env": {"A": "1"}})
        self.assertEqual(json.loads(read(two)), {"env": {"B": "2"}})

    def test_プロジェクトのルールファイルの写しも対象になる(self):
        # 置き場に並ぶプロジェクトのルールファイルも、root の下に在れば
        # 作業ツリーに写しが入り、統合で main へ届く道は同じ。
        self.worktree()
        project = os.path.join(self.repo, "projects", "lib", "config", "rules.yml")
        write(project, json.dumps(RULES))

        found = selfguard.targets(self.repo, self.rules, "", [("lib", project)])

        copies = {t.label for t in found if t.top}
        self.assertIn(
            os.path.join(".claude", "worktrees", "w1", "projects", "lib", "config", "rules.yml"),
            copies,
        )

    def test_root_の外を指すルールファイルには写しが無い(self):
        # 置き場がワークスペースルートの外にあるなら、作業ツリーの中に対応する
        # 写しは無い。無い場所を守りに行っても、報告に死んだ 1 行が増えるだけ。
        self.worktree()
        outside = os.path.join(os.path.dirname(self.repo), "elsewhere", "rules.yml")

        found = selfguard.targets(self.repo, outside, "")

        self.assertEqual([t.label for t in found if t.top], self.settings_copies())

    def settings_copies(self):
        """作業ツリー w1 の中の、設定ファイル 2 つの綴り。"""
        return [
            os.path.join(".claude", "worktrees", "w1", ".claude", "settings.json"),
            os.path.join(".claude", "worktrees", "w1", ".claude", "settings.local.json"),
        ]

    # 戻す前に止める

    def test_ルールに書かなくてもシェルからの書き込みは止まる(self):
        # RULES にこの場所を守るルールは 1 件も無い。それでも止まるのが要点で、
        # 止める側もルールファイルの外に置いてあることを確かめている。
        result = self.run_hook("PreToolUse", command="echo x > .claude/ccnavi/rules.yml")

        self.assertIn("deny", result.stdout)
        self.assertIn("builtin-guard-setting-files", result.stdout)

    def test_設定ファイルを読むだけなら通る(self):
        # 場所の名前が出たかどうかでは止めない。ここは読むほうが普通の場所で、
        # 名前で止めると、いちばんガードを直したいときにいちばん強く効く。
        result = self.run_hook("PreToolUse", command="cat .claude/ccnavi/rules.yml")

        self.assertNotIn("deny", result.stdout)

    def test_disable_なら止める側も足さない(self):
        result = self.run_hook(
            "PreToolUse", setting="disable", command="echo x > .claude/ccnavi/rules.yml"
        )

        self.assertNotIn("builtin-guard-setting-files", result.stdout)

    # 引用に空白を含む形（wip/design/shellread-sep.md §3）。shellread が語の中の
    # 切れ目をコマンドの区切りと別の印で渡すようになると、`[^\x00]*` が引用の
    # 空白をまたいで行き先まで届く。止める側はそれで穴が塞がり、リダイレクトの
    # 行き先の式は語の中の印を食わないように直す。

    def rules_in_shell(self):
        """シェルに書く綴りのルールファイル。引用の外に置くので区切りは `/`。"""
        return self.rules.replace("\\", "/")

    @unittest.skipUnless(hasattr(shellread, "WORD_SEP"), "shellread-sep の実装待ち")
    def test_引用の中に書いたリダイレクトの行き先は書き込みではない(self):
        # `> 場所` が引用の中にある。grep の引数であって、書き込み先を連れてこない。
        # 語の中の印がリダイレクトの行き先として読まれると、ここが止まる。
        result = self.run_hook("PreToolUse", command=f'grep -n "> {self.rules_in_shell()}" f')

        self.assertNotIn("deny", result.stdout)
        self.assertNotIn("builtin-guard-setting-files", result.stdout)

    @unittest.skipUnless(hasattr(shellread, "WORD_SEP"), "shellread-sep の実装待ち")
    def test_引用に空白を含む書き換えも止まる(self):
        # 動詞と行き先の間に引用の空白があっても、同じコマンドの中なら届く。
        # 引用の空白がコマンドの区切りと同じ印だった間は、ここが穴だった。
        rules = self.rules_in_shell()
        for command in [
            f'sed -i "s/a b/c/" {rules}',
            f'tee "a b" {rules}',
            f'cp "a b" {rules}',
        ]:
            with self.subTest(command=command):
                result = self.run_hook("PreToolUse", command=command)

                self.assertIn("deny", result.stdout)
                self.assertIn("builtin-guard-setting-files", result.stdout)

    # 実行ファイル

    def test_実行ファイルはシェルからの書き込みで止まる(self):
        path = self.binary()

        result = self.run_hook(
            "PreToolUse", bin=path, command="cp /tmp/other.exe dist/ccnavi/ccnavi.exe"
        )

        self.assertIn("deny", result.stdout)
        self.assertIn("builtin-guard-setting-files", result.stdout)

    def test_実行ファイルは名指しのツールからも止まる(self):
        path = self.binary()

        result = self.run_hook("PreToolUse", bin=path, tool="Write", file_path=path)

        self.assertIn("deny", result.stdout)
        self.assertIn("builtin-guard-binary", result.stdout)

    def test_指していなければ実行ファイルの綴りは当たらない(self):
        # CCNAVI_BIN_PATH が空なら、そこは守る対象ではない。綴りを推測して
        # 守ると、そこに在る別のファイルを実体として扱うことになる。
        self.binary()

        result = self.run_hook("PreToolUse", command="cp /tmp/other.exe dist/ccnavi/ccnavi.exe")

        self.assertNotIn("deny", result.stdout)

    def test_セッション開始で控えを取り実行後に戻す(self):
        path = self.binary()
        self.run_hook("SessionStart", bin=path)
        write(path, "MZ replaced\n")

        result = self.run_hook("PostToolUse", bin=path)

        self.assertEqual(read(path), "MZ fake executable\n")
        self.assertIn("ccnavi.exe", result.stdout)

    def test_セッション開始を通らなければ実行ファイルは黙って通る(self):
        # 控えが無い状態。セッション開始のイベントに登録していないか、
        # 実行ファイルを指していない設定がこれで、事件ではない。
        path = self.binary()
        write(path, "MZ replaced\n")

        result = self.run_hook("PostToolUse", bin=path)

        self.assertEqual(read(path), "MZ replaced\n")
        self.assertEqual(result.stdout, "")

    def test_実行ファイルが見つからなければセッション開始で言う(self):
        missing = os.path.join(self.repo, "dist", "ccnavi", "ccnavi.exe")

        result = self.run_hook("SessionStart", bin=missing)

        self.assertIn("CCNAVI_BIN_PATH", result.stdout)

    def test_拡張子を書かない綴りでも実行ファイルに当たる(self):
        # hook の登録は 3 つの環境で同じ 1 行を使う。PyInstaller が Windows で
        # だけ `.exe` を付けるので、設定に書いた綴りと在るファイルの綴りがずれる。
        # 書いた側を直させるのではなく、在るほうを選ぶ。
        path = self.binary()
        spelled = os.path.join(self.repo, "dist", "ccnavi", "ccnavi")

        self.run_hook("SessionStart", bin=spelled)
        write(path, "MZ replaced\n")
        self.run_hook("PostToolUse", bin=spelled)

        self.assertEqual(read(path), "MZ fake executable\n")

    def test_拡張子なしの実行ファイルはそのまま当たる(self):
        # Linux の置き場がこれ。継ぎ足して探すのは書いた綴りが無いときだけで、
        # 在るならそれを使う。Windows で `.exe` まで書いた設定も、同じ理由で
        # 継ぎ足しに回らず、書いたとおりに当たる。
        path = os.path.join(self.repo, "dist", "ccnavi", "ccnavi")
        write(path, "ELF fake executable\n")

        self.run_hook("SessionStart", bin=path)
        write(path, "ELF replaced\n")
        self.run_hook("PostToolUse", bin=path)

        self.assertEqual(read(path), "ELF fake executable\n")

    # セッション開始の控え

    def test_dry_run_でもセッション開始で控えを取る(self):
        # 控えることは誰の書きかけも消さない。ここを enable に限ると、
        # 切り替えた最初のセッションが戻す先を持たないまま走る。
        path = self.binary()

        self.run_hook("SessionStart", bin=path, mode="dry-run")

        self.assertEqual(self.store(), ["MZ fake executable\n"])

    def test_セッション開始では設定ファイルも控える(self):
        # 実行前の控えが始まるのは最初のツール呼び出しから。それより前に
        # 設定ファイルを消されると、控えを持たないまま実行後の監視に入る。
        self.run_hook("SessionStart")

        saved = os.path.join(self.state, "selfguard", "s1", "rules")
        self.assertEqual(read(saved), read(self.rules))

    # 控えを溜めない

    def test_実行ファイルの控えはセッションをまたいで_1_本(self):
        # 同じビルドのまま何セッション走っても、写しは 1 本で済むこと。
        path = self.binary()
        self.run_hook("SessionStart", bin=path, session="s1")
        self.run_hook("SessionStart", bin=path, session="s2")

        self.assertEqual(self.store(), ["MZ fake executable\n"])
        self.assertFalse(os.path.exists(os.path.join(self.state, "selfguard", "s1", "bin")))

    def test_古い控えはセッション開始で落ちる(self):
        self.run_hook("SessionStart", session="old")
        self.backdate(os.path.join(self.state, "selfguard", "old"))

        self.run_hook("SessionStart", session="s1")

        self.assertEqual(self.sessions(), ["s1"])

    def test_動いているセッションの控えは巻き添えにしない(self):
        # 実行前の控えは呼び出しのたびに書き直される。日付で切るのは
        # そこに乗るため。並行しているセッションの戻す先を消さない。
        self.run_hook("PreToolUse", session="other")

        self.run_hook("SessionStart", session="s1")

        self.assertIn("other", self.sessions())

    def test_自分の控えは日付を見ずに残る(self):
        # 時計がずれている環境で、自分が戻す先を自分で消さないこと。
        self.run_hook("SessionStart", session="s1")
        self.backdate(os.path.join(self.state, "selfguard", "s1"))

        self.run_hook("SessionStart", session="s1")

        self.assertIn("s1", self.sessions())

    def test_参照されなくなった実体も落ちる(self):
        path = self.binary()
        self.run_hook("SessionStart", bin=path, session="old")
        self.backdate(
            os.path.join(self.state, "selfguard", "old"),
            os.path.join(self.state, "selfguard", "store"),
        )

        self.run_hook("SessionStart", session="s1")

        self.assertEqual(self.store(), [])

    def test_使われている実体は古くても残る(self):
        # 何日も続いているセッションが、自分が戻す先を失わないこと。
        path = self.binary()
        self.run_hook("SessionStart", bin=path, session="s1")
        self.backdate(os.path.join(self.state, "selfguard", "store"))

        self.run_hook("SessionStart", session="s2")

        self.assertEqual(self.store(), ["MZ fake executable\n"])

    # 設定で切る

    def test_disable_は控えも取らず戻しもしない(self):
        self.run_hook("PreToolUse", setting="disable")
        write(self.rules, "{}")

        result = self.run_hook("PostToolUse", setting="disable")

        self.assertEqual(read(self.rules), "{}")
        self.assertEqual(result.stdout, "")

    def test_dry_run_は戻さず戻すはずだったと言う(self):
        self.run_hook("PreToolUse", setting="dry-run")
        write(self.rules, "{}")

        result = self.run_hook("PostToolUse", setting="dry-run")

        self.assertEqual(read(self.rules), "{}", "言うだけで触らない")
        self.assertIn("would-restore", result.stdout)

    def test_モードが_dry_run_なら設定が_enable_でも触らない(self):
        # dry-run は「呼び出しにも作業ツリーにも手を出さない」が約束。
        # 守る側だけがその外に出ると、試している最中に誰も頼んでいない
        # ファイル操作が起きる。
        self.run_hook("PreToolUse", mode="dry-run")
        write(self.rules, "{}")

        result = self.run_hook("PostToolUse", mode="dry-run")

        self.assertEqual(read(self.rules), "{}")
        self.assertIn("would-restore", result.stdout)

    # 記録

    def test_記録に何をしたかが残る(self):
        self.run_hook("PreToolUse")
        write(self.rules, "{}")
        self.run_hook("PostToolUse")

        line = self.records()[-1]
        self.assertIn("guarded", line)
        self.assertIn("rules:restored", line["guarded"])


if __name__ == "__main__":
    unittest.main()
