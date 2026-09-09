"""実行後の監視の受入テスト。

道具を外から動かす。本物の git リポジトリを一時ディレクトリに作り、そこを
汚してから payload を渡し、返ってきた文と終了コードと記録だけを読む。
作業ツリーの実物を見るのがこの面の要点なので、git を差し替えると、
テストが通ることと監視が動くことが別の話になる。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RULES = {
    "version": 3,
    "deny": [
        {
            "id": "protected",
            "match": "Write|Edit|MultiEdit",
            "glob": "*/protected/*",
            "message": "protected/ is declared write-deny. Ask the user before changing it.",
        },
        {
            "id": "push",
            "match": "Bash",
            "glob": "*git push*",
            "message": "git push is not run by the agent.",
        },
    ],
    # 実行後の監視を見るテストなので、実行前の判定で確認を出させない。
    # 出すと、監視が何を言ったかを見たいテストが ask の話になる。
    "allow": [
        {
            "id": "anything-else",
            "match": "Bash|Read|Write|Edit|MultiEdit|NotebookEdit",
            "regex": ".",
        }
    ],
}


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


class PostToolUseTest(unittest.TestCase):
    """保護領域を持つリポジトリを 1 つ作り、それを汚しながら見る。"""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="ccnavi-post-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)

        git(self.repo, "init", "--quiet")
        write(os.path.join(self.repo, "protected", "keep.txt"), "committed\n")
        write(os.path.join(self.repo, "src", "app.py"), "print(1)\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "--quiet", "-m", "init")

        self.rules = os.path.join(self.repo, "rules.yml")
        write(self.rules, json.dumps(RULES))
        self.state = os.path.join(self.repo, "state")
        self.log = os.path.join(self.repo, "log.jsonl")

    def run_hook(
        self,
        mode="enable",
        restore="disable",
        session="s1",
        tool="Bash",
        event="PostToolUse",
        **tool_input,
    ):
        payload = json.dumps(
            {
                "hook_event_name": event,
                "tool_name": tool,
                "tool_input": tool_input,
                "session_id": session,
            }
        )
        # モードは環境ではなくフラグで固定する。このリポジトリは ccnavi を
        # 自分自身に仕掛けているので、テストを走らせるセッションが既に
        # モードを持っている。それを読むテストは、コードではなく走った
        # 機械のことを報告してしまう。
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        args = ["--mode", mode]
        if mode == "disable":
            # disable だけはフラグから言えない。作業ツリーの中から来た disable は
            # 名指しで無視される仕様なので、環境から渡す。
            environment["CCNAVI_MODE"] = "disable"
            args = []
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "ccnavi",
                "--root",
                self.repo,
                "--rules",
                self.rules,
                "--state",
                self.state,
                "--log",
                self.log,
                "--restore-if-deny",
                restore,
                # ここで見るのはルール由来の保護だけ。ccnavi 自身の設定ファイルを
                # 守る側は対象も経路も別なので、混ぜると失敗したときにどちらの
                # 話なのかが分からなくなる。あちらは test_selfguard が見る。
                "--guard-core-files",
                "disable",
                *args,
            ],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
            env=environment,
        )

    def dirty(self, text="changed by a build\n"):
        write(os.path.join(self.repo, "protected", "keep.txt"), text)

    def records(self):
        with open(self.log, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def context(self, result):
        try:
            return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        except (ValueError, KeyError) as exc:
            self.fail(f"標準出力が期待した JSON ではない: {exc}\nstdout: {result.stdout!r}")

    # 何も起きていないとき

    def test_保護領域が変わっていなければ何も言わない(self):
        result = self.run_hook(command="ls")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.records()[-1]["decision"], "allow")

    def test_保護領域の外の変更は言わない(self):
        self.run_hook(command="ls")
        write(os.path.join(self.repo, "src", "app.py"), "print(2)\n")

        result = self.run_hook(command="python build.py")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "", "守ると宣言していない場所の変更は監視の対象ではない")

    # 検知したとき

    def test_変更を検知したら差し戻し戻し方と直前の実行を返す(self):
        self.run_hook(command="ls")
        self.dirty()

        result = self.run_hook(command="python build.py")

        # このイベントでツールは取り消せないので、返せるのは差し戻しの文だけ。
        self.assertEqual(result.returncode, 2, "exit 2 と標準エラーがモデルに届く経路")
        self.assertIn("POST_VIOLATION", result.stderr)
        self.assertIn("protected/keep.txt", result.stderr)
        # どの設定が言っているか。名指ししないと直しに行く先が決まらない。
        self.assertIn("rules.yml#protected", result.stderr)
        # 原因となった直前の実行（REQ-PST-02）。
        self.assertIn("Bash(python build.py)", result.stderr)
        # 戻す手順（REQ-PST-03）。
        self.assertIn('git restore --staged --worktree -- "protected/keep.txt"', result.stderr)
        # ルールが持つ文面。止めるだけでは足りないという道具の目的そのもの。
        self.assertIn("Ask the user", result.stderr)

    def test_新しく現れたファイルは消す手順を返す(self):
        self.run_hook(command="ls")
        write(os.path.join(self.repo, "protected", "generated.env"), "SECRET=1\n")

        result = self.run_hook(command="npm run build")

        self.assertEqual(result.returncode, 2)
        self.assertIn("protected/generated.env", result.stderr)
        self.assertIn(
            "git clean -f", result.stderr, "現れたファイルを restore で戻すことはできない"
        )

    def test_同じ変更を毎回は言わない(self):
        self.run_hook(command="ls")
        self.dirty()
        self.run_hook(command="python build.py")

        result = self.run_hook(command="ls -la")

        self.assertEqual(result.returncode, 0, "1 度伝えた変更を呼び出しのたびに繰り返さない")
        self.assertEqual(result.stderr, "")

    def test_同じ場所が別の壊れ方をしたらもう一度言う(self):
        self.run_hook(command="ls")
        self.dirty()
        self.run_hook(command="python build.py")
        os.remove(os.path.join(self.repo, "protected", "keep.txt"))

        result = self.run_hook(command="python clean.py")

        self.assertEqual(result.returncode, 2, "中身が変わったことと消えたことは別の出来事")
        self.assertIn("gone", result.stderr)

    def test_記録に変更したパスと当たったルールが残る(self):
        self.run_hook(command="ls")
        self.dirty()
        self.run_hook(command="python build.py")

        line = self.records()[-1]
        self.assertEqual(line["event"], "PostToolUse")
        self.assertEqual(line["decision"], "deny")
        self.assertTrue(line["enforced"])
        self.assertEqual(line["paths"], ["changed protected/keep.txt"])
        self.assertEqual(line["rules"], ["protected"])

    # セッションが始まる前から在った変更

    def test_前から在った変更は原因を付けずに戻すなと言う(self):
        self.dirty()

        result = self.run_hook(command="ls")

        self.assertEqual(result.returncode, 0, "直前の実行の結果ではないので差し戻さない")
        text = self.context(result)
        self.assertIn("POST_PREEXISTING", text)
        self.assertIn("Do not undo it", text)
        self.assertNotIn("POST_VIOLATION", text)
        # この呼び出しは何も汚していない。判定は呼び出しについてのもの。
        self.assertEqual(self.records()[-1]["decision"], "allow")

    def test_前から在った変更は自動復元でも触らない(self):
        self.dirty()

        self.run_hook(command="ls", restore="enable")

        with open(os.path.join(self.repo, "protected", "keep.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "changed by a build\n", "他人の書きかけを戻してはいけない")

    # モード

    def test_dry_run_は差し戻さず報告だけする(self):
        self.run_hook(mode="dry-run", command="ls")
        self.dirty()

        result = self.run_hook(mode="dry-run", command="python build.py")

        self.assertEqual(result.returncode, 0, "呼び出しを止めないのが warn の約束")
        text = self.context(result)
        self.assertIn("POST_VIOLATION", text)
        self.assertIn("protected/keep.txt", text)
        self.assertFalse(self.records()[-1]["enforced"])

    def test_dry_run_は自動復元も行わない(self):
        self.run_hook(mode="dry-run", command="ls")
        self.dirty()

        self.run_hook(mode="dry-run", restore="enable", command="python build.py")

        with open(os.path.join(self.repo, "protected", "keep.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "changed by a build\n")

    def test_disable_は何も見ない(self):
        self.dirty()

        result = self.run_hook(mode="disable", command="python build.py")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.records()[-1]["reason"], "mode-disabled")

    # 自動復元

    def test_自動復元は変更を戻して戻したことを言う(self):
        self.run_hook(command="ls")
        self.dirty()

        result = self.run_hook(restore="enable", command="python build.py")

        with open(os.path.join(self.repo, "protected", "keep.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "committed\n")
        self.assertIn("restored:", result.stderr)

    def test_自動復元は現れたファイルを消さずに退避する(self):
        self.run_hook(command="ls")
        write(os.path.join(self.repo, "protected", "generated.env"), "SECRET=1\n")

        result = self.run_hook(restore="enable", command="npm run build")

        self.assertFalse(os.path.exists(os.path.join(self.repo, "protected", "generated.env")))
        self.assertIn("moved this file to", result.stderr)
        # 退避先が報告に載るので、要るものだったなら拾い直せる。
        aside = os.path.join(self.state, "aside")
        found = [f for _, _, files in os.walk(aside) for f in files]
        self.assertEqual(found, ["generated.env"])

    def test_読めない自動復元の値は守る側に落ちる(self):
        # 倒れる先を off から enable に変えてある。書き損じた 1 語で守りが
        # 消えるより、書き損じた 1 語で守りが残るほうがよい、という向き。
        # 戻す先はコミット済みの内容なので、失われるのは「宣言した保護領域を
        # 汚した未コミットの変更」だけになる。
        self.run_hook(command="ls")
        self.dirty()

        result = self.run_hook(restore="yes", command="python build.py")

        with open(os.path.join(self.repo, "protected", "keep.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "committed\n")
        self.assertIn("is not a setting", result.stderr)

    # ターンの終わり

    def test_ターンの終わりに保護領域の変更を人へ報告する(self):
        self.run_hook(event="UserPromptSubmit")
        self.dirty()

        result = self.run_hook(event="Stop")

        self.assertEqual(result.returncode, 0, "報告のためにターンを続けさせない")
        message = json.loads(result.stdout)["systemMessage"]
        self.assertIn("protected/keep.txt", message)
        self.assertIn("protected", message, "どのルールが言っているか")
        self.assertIn("git restore", message, "戻す手順")

    def test_ターンの終わりも変わっていなければ何も言わない(self):
        self.run_hook(event="UserPromptSubmit")

        result = self.run_hook(event="Stop")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_ターンが始まる前から在った変更は報告しない(self):
        # 利用者の書きかけ、他のセッションが置いたもの、前のターンで片付け
        # なかったもの。全部このターンの成果として並べると、次から読まれなくなる。
        self.dirty()
        self.run_hook(event="UserPromptSubmit")

        result = self.run_hook(event="Stop")

        self.assertEqual(result.stdout, "", "基準に入っているものは、このターンの出来事ではない")

    def test_ターンの始まりを見ていなければ何も言わない(self):
        # 登録されていない、あるいは基準を読めなかった。見えていない期間を
        # 見えたことにしない。
        self.dirty()

        result = self.run_hook(event="Stop")

        self.assertEqual(result.stdout, "")
        self.assertEqual(self.records()[-1]["reason"], "no-turn-baseline")

    def test_ターンの終わりの報告は一度伝えた変更も含む(self):
        # 呼び出しごとの報告は控えを見て繰り返さないが、人はまだ 1 度も
        # 見ていないことがある。宛先が違うので、控えを共有しない。
        self.run_hook(event="UserPromptSubmit")
        self.run_hook(command="ls")
        self.dirty()
        self.run_hook(command="python build.py")

        result = self.run_hook(event="Stop")

        self.assertIn("protected/keep.txt", json.loads(result.stdout)["systemMessage"])

    def test_dry_run_でもターンの終わりには報告する(self):
        # 見えたことを言うだけで、呼び出しにも作業ツリーにも手を出さない。
        self.run_hook(mode="dry-run", event="UserPromptSubmit")
        self.dirty()

        result = self.run_hook(mode="dry-run", event="Stop")

        self.assertIn("protected/keep.txt", json.loads(result.stdout)["systemMessage"])

    # 見えないとき

    def test_ツールが作業ツリーを変えようがないときは見ない(self):
        self.run_hook(command="ls")
        self.dirty()

        result = self.run_hook(tool="Read", file_path="src/app.py")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.records()[-1]["reason"], "tool-cannot-write")

    def test_git_の作業ツリーでなければ黙って通す(self):
        outside = tempfile.mkdtemp(prefix="ccnavi-plain-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        self.repo = outside
        write(os.path.join(outside, "rules.yml"), json.dumps(RULES))
        self.rules = os.path.join(outside, "rules.yml")
        self.state = os.path.join(outside, "state")
        self.log = os.path.join(outside, "log.jsonl")

        result = self.run_hook(command="ls")

        self.assertEqual(result.returncode, 0, "見えないことでツールを止めない")
        # 見えなかったことは記録に残す。残さないと、違反が無かったのか
        # 見ていなかったのかが同じ見た目になる。
        line = self.records()[-1]
        self.assertEqual(line["reason"], "worktree-unreadable")
        self.assertEqual(line["detail"], "not-a-git-worktree")

    def test_自分が書く場所は自分の違反にしない(self):
        # 記録と控えを保護領域の中に置く。このリポジトリのルールが
        # `.claude/ccnavi/*` を守っていて、記録も控えもそこにあるのと同じ形。
        self.state = os.path.join(self.repo, "protected", "state")
        self.log = os.path.join(self.repo, "protected", "log.jsonl")

        self.run_hook(command="ls")
        result = self.run_hook(command="ls -la")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
