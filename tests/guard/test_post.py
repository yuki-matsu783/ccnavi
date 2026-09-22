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
import tempfile
import unittest

from tests import ROOT, common_path
from tests.inproc import run_ccnavi

RULES = {
    "version": 1,
    "deny": [
        {
            "id": "protected",
            "match": "Write|Edit",
            "glob": "*/protected/*",
            "message": "protected/ is declared write-deny. Ask the user before changing it.",
        },
        {
            "id": "push",
            "match": "Bash",
            "glob": "*git push*",
            "message": "git push is not run by the agent.",
        },
        {
            # 承認済みチケットの置き場。人が承認してコミットする場所でもあるので、
            # コミット済みのぶんは報告から外れる（post._committed_findings）。
            "id": "approved",
            "match": "Write|Edit",
            "glob": "*/.ccnavi/approved/*",
            "message": "approved tickets are moved by the user.",
        },
        {
            # 提案の置き場。`ticket done` はここへチケットを動かすので、移動の
            # 両側が保護領域に入る。片側だけを守ると、外れ方を見たことにならない。
            "id": "proposals",
            "match": "Write|Edit",
            "glob": "*/wip/proposals/*",
            "message": "proposals are moved by the scripts.",
        },
    ],
    # `ask` も保護領域（post._guarding）。報告はされるが、戻す対象ではない
    # （post._restorable）。文面は書かない――ask に message を書くと lint が error。
    "ask": [
        {
            "id": "watched",
            "match": "Write|Edit",
            "glob": "*/watched/*",
        }
    ],
    # 実行後の監視を見るテストなので、実行前の判定で確認を出させない。
    # 出すと、監視が何を言ったかを見たいテストが ask の話になる。
    "allow": [
        {
            "id": "anything-else",
            "match": "Bash|Read|Write|Edit|NotebookEdit",
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


class Harness:
    """payload を 1 回渡して、返ってきたものだけを読む。リポジトリは各 setUp が作る。

    2 つのテストが同じ渡し方をするので、渡し方は 1 か所に持つ。分けて書くと、
    片方だけがフラグの増減に追いつかなくなる。
    """

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
        return run_ccnavi(
            [
                "--root",
                self.repo,
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
            cwd=ROOT,
            env=environment,
        )


class PostToolUseTest(Harness, unittest.TestCase):
    """保護領域を持つリポジトリを 1 つ作り、それを汚しながら見る。"""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="ccnavi-post-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)

        git(self.repo, "init", "--quiet")
        write(os.path.join(self.repo, "protected", "keep.txt"), "committed\n")
        write(os.path.join(self.repo, "watched", "deps.txt"), "committed\n")
        write(os.path.join(self.repo, "src", "app.py"), "print(1)\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "--quiet", "-m", "init")

        # 共通層は既定の置き場へ。`--rules` は診断でだけ効く（ADR-0067）。
        self.rules = common_path(self.repo, "rules")
        write(self.rules, json.dumps(RULES))
        self.state = os.path.join(self.repo, "state")
        self.log = os.path.join(self.repo, "log.jsonl")

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
        self.assertIn("rule: protected", result.stderr)
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

    def test_自動復元の予行は戻さずに戻すはずだったと言う(self):
        # 判定は本番で、戻しだけ予行。何が戻るのかを、戻される前に見せる面。
        self.run_hook(command="ls")
        self.dirty()

        result = self.run_hook(restore="dry-run", command="python build.py")

        with open(os.path.join(self.repo, "protected", "keep.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "changed by a build\n", "予行はファイルに触らない")
        self.assertIn("would-restore:", result.stderr)
        self.assertIn("put it back to its committed content", result.stderr)
        # 誰も戻していないので、戻す手順は落とさない。
        self.assertIn('git restore --staged --worktree -- "protected/keep.txt"', result.stderr)
        self.assertIn("would-restore 1", self.records()[-1]["detail"])

    def test_自動復元の予行は現れたファイルにも触らない(self):
        self.run_hook(command="ls")
        write(os.path.join(self.repo, "protected", "generated.env"), "SECRET=1\n")

        result = self.run_hook(restore="dry-run", command="npm run build")

        self.assertTrue(os.path.exists(os.path.join(self.repo, "protected", "generated.env")))
        self.assertIn("would have moved this file aside", result.stderr)

    def test_自動復元を切ったときは予行の文を出さない(self):
        # 切った形と予行の形が同じ見た目になると、3 つの値が 2 つに戻る。
        self.run_hook(command="ls")
        self.dirty()

        result = self.run_hook(restore="disable", command="python build.py")

        self.assertIn("POST_VIOLATION", result.stderr)
        self.assertNotIn("would-restore", result.stderr)
        self.assertNotIn("would-restore", self.records()[-1].get("detail", ""))

    def test_モードが予行なら戻しの宣言によらず予行として言う(self):
        # CCNAVI_MODE=dry-run は戻しの側も予行に落とす（cli.effective_setting）。
        self.run_hook(mode="dry-run", command="ls")
        self.dirty()

        result = self.run_hook(mode="dry-run", restore="enable", command="python build.py")

        with open(os.path.join(self.repo, "protected", "keep.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "changed by a build\n")
        self.assertIn("would-restore:", self.context(result))

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
        self.rules = write(common_path(outside, "rules"), json.dumps(RULES))
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
        # 記録と控えを保護領域の中に置く。置き場を設定でルールが守る場所の中へ
        # 指したときの形。
        self.state = os.path.join(self.repo, "protected", "state")
        self.log = os.path.join(self.repo, "protected", "log.jsonl")

        self.run_hook(command="ls")
        result = self.run_hook(command="ls -la")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    # 戻す対象は deny だけ

    def test_askと宣言した場所は報告するが戻さない(self):
        # `ask` は「人が 1 度見る場所」の宣言で、「書くな」ではない。戻すと、
        # 人が確認に「はい」と答えた編集をあとから無かったことにする。
        self.run_hook(command="ls")
        write(os.path.join(self.repo, "watched", "deps.txt"), "changed by a build\n")

        result = self.run_hook(restore="enable", command="python build.py")

        with open(os.path.join(self.repo, "watched", "deps.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "changed by a build\n", "ask の場所は戻さない")
        self.assertIn("POST_VIOLATION", result.stderr, "戻さなくても報告はする")
        self.assertIn("watched/deps.txt", result.stderr)
        self.assertNotIn("restored: ccnavi", result.stderr)
        # 戻していないので、戻す手順は載せたままにする。
        self.assertIn('git restore --staged --worktree -- "watched/deps.txt"', result.stderr)
        # 手順だけだと「自分で戻せ」としか読めない。戻さなかった理由を添える。
        self.assertIn("not-restored:", result.stderr)
        self.assertIn("`ask`, not `deny`", result.stderr)

    def test_askと宣言した場所は予行でも戻すはずだったと言わない(self):
        # 本番で戻さないものについて「enable なら戻していた」と言うと、
        # 設定を上げたときに起きることを読み違えさせる。
        self.run_hook(command="ls")
        write(os.path.join(self.repo, "watched", "deps.txt"), "changed by a build\n")

        result = self.run_hook(restore="dry-run", command="python build.py")

        self.assertIn("POST_VIOLATION", result.stderr)
        self.assertNotIn("would-restore", result.stderr)
        self.assertNotIn("would-restore", self.records()[-1].get("detail", ""))

    # コミットに入った変更

    def test_ターンの終わりはコミットに入った変更も言う(self):
        # `git status` はコミットを見せない。ここを足さないと、保護領域を汚して
        # からコミットした回が「何も起きなかった」と同じ見た目になる。
        self.run_hook(event="UserPromptSubmit")
        self.dirty()
        git(self.repo, "add", "--", "protected/keep.txt")
        git(self.repo, "commit", "--quiet", "-m", "汚してからコミットする")

        result = self.run_hook(event="Stop")

        self.assertTrue(result.stdout, "コミットに入った変更が報告されていない")
        message = json.loads(result.stdout)["systemMessage"]
        self.assertIn("protected/keep.txt", message)
        self.assertIn("committed", message)
        # 戻す手順は書かない。履歴は書き換えないので、案内できる 1 つが無い。
        self.assertNotIn("git restore", message)
        self.assertIn("コミットに入っている", message)

    def test_ターンが始まる前のコミットは言わない(self):
        # 前のターンや他のセッションが積んだコミットを、このターンの成果として
        # 並べない。基準はターンの始まりに控えた HEAD。
        #
        # 「何も出ない」だけを見ると、コミットを一切見ない実装でも通ってしまう。
        # 同じターンで 1 件だけ積んで、そちらは出ることも一緒に見る。
        self.dirty()
        git(self.repo, "add", "--", "protected/keep.txt")
        git(self.repo, "commit", "--quiet", "-m", "前のターンのコミット")
        self.run_hook(event="UserPromptSubmit")
        write(os.path.join(self.repo, "protected", "later.txt"), "このターンのコミット\n")
        git(self.repo, "add", "--", "protected/later.txt")
        git(self.repo, "commit", "--quiet", "-m", "このターンのコミット")

        result = self.run_hook(event="Stop")

        message = json.loads(result.stdout)["systemMessage"]
        self.assertIn("protected/later.txt", message)
        self.assertNotIn("protected/keep.txt", message, "前のターンのコミットは並べない")

    def test_承認のコミットはターンの報告に並べない(self):
        # 承認は人が提案を .ccnavi/approved/ へ動かしてコミットする運び。
        # そこは deny でもあるので、外さないと承認のたびに違反として並ぶ。
        self.run_hook(event="UserPromptSubmit")
        write(os.path.join(self.repo, ".ccnavi", "approved", "doing", "i0001.md"), "x\n")
        self.dirty()
        git(self.repo, "add", "--", ".ccnavi/approved/doing/i0001.md", "protected/keep.txt")
        git(self.repo, "commit", "--quiet", "-m", "承認済みチケットと、保護領域の変更")

        result = self.run_hook(event="Stop")

        # 同じコミットに載っていても、外れるのは置き場のほうだけ。
        message = json.loads(result.stdout)["systemMessage"]
        self.assertIn("protected/keep.txt", message)
        self.assertNotIn(".ccnavi/approved", message)

    def test_統合先を取り込んだマージの持ち込みは並べない(self):
        # ワークツリーを切って作業し、`merge <統合先>` で取り込んでから戻すのが
        # このリポジトリの手順。二点の差分で数えると、人が統合先で直した保護領域が
        # 打つたびに並ぶ。数えるのはこのツリーが積んだコミットだけ。
        git(self.repo, "checkout", "--quiet", "-b", "feature")
        self.run_hook(event="UserPromptSubmit")
        write(os.path.join(self.repo, "src", "app.py"), "print(2)\n")
        git(self.repo, "add", "--", "src/app.py")
        git(self.repo, "commit", "--quiet", "-m", "自分の作業")
        git(self.repo, "checkout", "--quiet", "master")
        self.dirty("人が統合先で直した\n")
        git(self.repo, "add", "--", "protected/keep.txt")
        git(self.repo, "commit", "--quiet", "-m", "統合先の変更")
        git(self.repo, "checkout", "--quiet", "feature")
        git(self.repo, "merge", "--quiet", "--no-edit", "master")

        result = self.run_hook(event="Stop")

        self.assertEqual(result.stdout, "", "取り込んだぶんを自分のターンの成果にしない")

    def test_ターンの途中で切ったツリーも触った時点から数える(self):
        # ワークツリーを切ってから手を付けるのがこのリポジトリの手順。切ったターンが
        # まるごと数えられないと、その手順を踏むほど穴が大きくなる。
        self.run_hook(event="UserPromptSubmit")
        later = os.path.join(self.repo, ".claude", "worktrees", "wt1")
        git(self.repo, "worktree", "add", "--quiet", "-b", "wt1", later)
        # 切ったあとの 1 回で基準が付く（この呼び出しの行き先がそのツリー）。
        self.run_hook(tool="Write", file_path=os.path.join(later, "src", "app.py"))
        write(os.path.join(later, "protected", "keep.txt"), "切ったあとに汚してコミット\n")
        git(later, "add", "--", "protected/keep.txt")
        git(later, "commit", "--quiet", "-m", "切ったあとのコミット")

        result = self.run_hook(event="Stop")

        message = json.loads(result.stdout)["systemMessage"]
        self.assertIn("protected/keep.txt", message)
        self.assertIn("wt1", message)
        self.assertNotIn("数えていないツリー", message, "基準が付いたツリーは数える")

    def test_基準を持たないツリーは数えていないと言う(self):
        # ターンの途中で切ったワークツリーは、プロンプトのときに無いので基準を
        # 持たない。黙って飛ばすと、そのツリーで何も起きなかったのと見分けが付かない。
        self.run_hook(event="UserPromptSubmit")
        later = os.path.join(self.repo, ".claude", "worktrees", "wt1")
        git(self.repo, "worktree", "add", "--quiet", "-b", "wt1", later)

        result = self.run_hook(event="Stop")

        message = json.loads(result.stdout)["systemMessage"]
        self.assertIn("数えていないツリー", message)
        self.assertIn("wt1", message)
        self.assertIn("uncounted", self.records()[-1].get("detail", ""))

    def test_戻さなかった1件は控えに入りターンの終わりに人へ出る(self):
        # 戻していないのでファイルは汚れたまま。呼び出しごとに言えば同じ文が
        # 呼び出しの数だけ積まれるので、報告はセッションで 1 度きりにする。
        # 人が見るのはターンの終わりの報告（Stop）。
        # 控え（セッション）とターンの基準の両方を、汚す前に置く。
        self.run_hook(command="ls")
        self.run_hook(event="UserPromptSubmit")
        write(os.path.join(self.repo, "watched", "deps.txt"), "first\n")
        first = self.run_hook(restore="enable", command="python build.py")
        self.assertIn("watched/deps.txt", first.stderr)

        write(os.path.join(self.repo, "watched", "deps.txt"), "second and different\n")
        again = self.run_hook(restore="enable", command="python build.py")

        self.assertEqual(again.stderr, "", "同じ汚れを呼び出しごとには言わない")
        self.assertIn("known", self.records()[-1].get("detail", ""))
        # ターンの終わりには出る。人はここで「結局どこが変わったのか」を 1 度で見る。
        stop = self.run_hook(event="Stop", restore="enable")
        self.assertIn("watched/deps.txt", json.loads(stop.stdout)["systemMessage"])

    def test_denyとaskが同じ回に出たらdenyだけ戻る(self):
        self.run_hook(command="ls")
        self.dirty()
        write(os.path.join(self.repo, "watched", "deps.txt"), "changed by a build\n")

        result = self.run_hook(restore="enable", command="python build.py")

        with open(os.path.join(self.repo, "protected", "keep.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "committed\n", "deny の場所は戻る")
        with open(os.path.join(self.repo, "watched", "deps.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "changed by a build\n", "ask の場所は残る")
        # 報告はどちらも出る。数えるのは戻した 1 件だけ。
        self.assertIn("protected/keep.txt", result.stderr)
        self.assertIn("watched/deps.txt", result.stderr)
        self.assertIn("restored 1", self.records()[-1]["detail"])


TICKET = """---
id: i0001
title: "something"
allow:
  - match: Write|Edit
    glob: "src/*"
---
body
"""

DOING = ".ccnavi/approved/doing/i0001.md"


def ticket_repo(committed=True):
    """承認済みチケットを 1 本持つリポジトリ。`committed` が偽ならコミットを作らない。"""
    repo = tempfile.mkdtemp(prefix="ccnavi-place-")
    git(repo, "init", "--quiet")
    write(os.path.join(repo, *DOING.split("/")), TICKET)
    write(os.path.join(repo, "src", "app.py"), "print(1)\n")
    if committed:
        git(repo, "add", "-A")
        git(repo, "commit", "--quiet", "-m", "init")
    write(common_path(repo, "rules"), json.dumps(RULES))
    return repo


class TicketPlaceTest(Harness, unittest.TestCase):
    """チケットの置き場の変更を、誰が書いたかではなく何が変わったかで見分ける。

    ここは ccnavi の副命令（`ticket start` / `done`、`review request` / `check` / `ready`）が
    書く場所で、同時にプロジェクトが `deny` と宣言した場所でもある。外さないと、自分の
    手順を自分で違反として報告し、戻す設定では自分で戻して手順が進まなくなる。外しすぎると、
    承認済みチケットが宣言する範囲を、引数に現れない書き込みで広げる道ができる。
    """

    def setUp(self):
        self.repo = ticket_repo()
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.state = os.path.join(self.repo, "state")
        self.log = os.path.join(self.repo, "log.jsonl")
        # 基準を取る。以降に現れたものが、この呼び出しの結果として見られる。
        self.run_hook(command="ls")

    def path(self, rel):
        return os.path.join(self.repo, *rel.split("/"))

    def said(self, result):
        return f"{result.stdout}{result.stderr}"

    def ran_script(self, restore="disable"):
        return self.run_hook(
            restore=restore, command="sh .ccnavi/scripts/ccnavi-ticket.sh start i0001"
        )

    # 外れるもの

    def test_スクリプトが書く欄だけの変更は言わず戻しもしない(self):
        started = TICKET.replace("---\nbody", 'started_at: "2026-09-22T00:00:00Z"\n---\nbody')
        write(self.path(DOING), started)

        result = self.ran_script(restore="enable")

        self.assertEqual(result.returncode, 0, self.said(result))
        self.assertEqual(self.said(result), "")
        with open(self.path(DOING), encoding="utf-8") as f:
            self.assertIn("started_at", f.read(), "戻されると着手の時刻が消え、手順が進まない")

    def test_マーカーの設置は言わず戻しもしない(self):
        marker = ".ccnavi/approved/phases/i0001/1.requested"
        write(self.path(marker), '{"mr": 1}\n')

        result = self.ran_script(restore="enable")

        self.assertEqual(result.returncode, 0, self.said(result))
        self.assertTrue(os.path.exists(self.path(marker)), "退避されるとレビューの依頼が消える")

    def test_レビュー待ちへの移動は言わない(self):
        # `ticket done`。`doing/` から消えて、同じ姿が `review/` に現れる。
        os.remove(self.path(DOING))
        write(
            self.path("wip/proposals/review/i0001.md"),
            TICKET.replace("---\nbody", 'completed_at: "2026-09-22T01:00:00Z"\n---\nbody'),
        )

        result = self.ran_script()

        self.assertEqual(result.returncode, 0, self.said(result))

    # 外れないもの

    def test_範囲を広げる編集は言う(self):
        write(self.path(DOING), TICKET.replace('glob: "src/*"', 'glob: "*"'))

        result = self.ran_script()

        self.assertEqual(result.returncode, 2)
        self.assertIn("POST_VIOLATION", result.stderr)
        self.assertIn(DOING, result.stderr.replace(os.sep, "/"))

    def test_承認済みチケットを消すだけなら言う(self):
        # 承認済みチケットの無いワークツリーは範囲を持たない。消すことは範囲を外すこと。
        os.remove(self.path(DOING))

        result = self.ran_script()

        self.assertEqual(result.returncode, 2)
        self.assertIn("POST_VIOLATION", result.stderr)

    def test_承認待ちへ逃がす移動は言う(self):
        # 行き先が `review/` でも閉じた置き場でもない。`doing/` から出しただけ。
        os.remove(self.path(DOING))
        write(self.path("wip/proposals/todo/i0001.md"), TICKET)

        result = self.ran_script()

        self.assertEqual(result.returncode, 2)
        self.assertIn("POST_VIOLATION", result.stderr)

    def test_承認済みチケットを新しく置いたら言う(self):
        write(self.path(".ccnavi/approved/doing/i0002.md"), TICKET.replace("i0001", "i0002"))

        result = self.ran_script()

        self.assertEqual(result.returncode, 2)
        self.assertIn("POST_VIOLATION", result.stderr)

    def test_コミット済みの版を読めなければ言う(self):
        # HEAD が無いリポジトリ。突き合わせる相手が読めないので、外す側には倒さない。
        # 読めていれば外れるはずのマーカーで見る。外れたら、突き合わせを飛ばしたということ。
        self.repo = ticket_repo(committed=False)
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.state = os.path.join(self.repo, "state")
        self.log = os.path.join(self.repo, "log.jsonl")
        self.run_hook(command="ls")
        write(self.path(".ccnavi/approved/phases/i0001/1.requested"), '{"mr": 1}\n')

        result = self.ran_script()

        self.assertEqual(result.returncode, 2)
        self.assertIn("POST_VIOLATION", result.stderr)


if __name__ == "__main__":
    unittest.main()
