"""実行後チェックの受入テスト。

道具を外から動かす。実際の git リポジトリを一時ディレクトリに作り、そこを
汚してから payload を渡し、返ってきた文と終了コードと記録だけを読む。
作業ツリーの実物を見るのがこのチェックの要点なので、git を差し替えると、
テストが通ることとチェックが動くことが別の話になる。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from ccnavi.hook import events, post, post_findings
from ccnavi.infra import gitstate, settings
from ccnavi.records import audit
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
            # 承認済みチケットの置き場。ユーザが承認してコミットする場所でもあるので、
            # コミット済みのぶんは報告から外れる（post._committed_findings）。
            "id": "approved",
            "match": "Write|Edit",
            "glob": "*/.ccnavi/approved/*",
            "message": "approved tickets are moved by the user.",
        },
        {
            # 提案の置き場。`ticket finish` はここへチケットを動かすので、移動の
            # 両側が保護領域に入る。片側だけを守ると、外れ方を見たことにならない。
            "id": "proposals",
            "match": "Write|Edit",
            "glob": "*/wip/proposals/*",
            "message": "proposals are moved by the scripts.",
        },
    ],
    # `ask` も保護領域（post_findings._guarding）。報告はされるが、戻す対象ではない
    # （post._restorable）。文面は書かない。ask に message を書くと lint が error。
    "ask": [
        {
            "id": "watched",
            "match": "Write|Edit",
            "glob": "*/watched/*",
        }
    ],
    # 実行後チェックを見るテストなので、実行前チェックで確認を出させない。
    # 出すと、チェックが何を言ったかを見たいテストが ask の話になる。
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


# squash の取り込みを確かめるテストは `merge-tree --write-tree`（git 2.38+）を使う。
MERGE_TREE = (
    subprocess.run(
        ["git", "merge-tree", "--write-tree", "HEAD", "HEAD"], capture_output=True, cwd=ROOT
    ).returncode
    == 0
)


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
        source=None,
        prompt=None,
        **tool_input,
    ):
        body = {
            "hook_event_name": event,
            "tool_name": tool,
            "tool_input": tool_input,
            "session_id": session,
        }
        # UserPromptSubmit だけが prompt を持つ。
        if prompt is not None:
            body["prompt"] = prompt
        # SessionStart だけが source を持つ（startup・resume・clear・compact）。
        if source is not None:
            body["source"] = source
        payload = json.dumps(body)
        # モードは環境ではなくフラグで固定する。このリポジトリは ccnavi を
        # 自分自身に仕掛けているので、テストを走らせるセッションが既に
        # モードを持っている。それを読むテストは、コードではなく走った
        # 機械のことを報告してしまう。
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
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

        # 共通レイヤーは既定の置き場へ。`--rules` は診断でだけ有効。
        self.rules = common_path(self.repo, "rules")
        write(self.rules, json.dumps(RULES))
        # 戻す手順はラッパーの打ち方で書く。ラッパーが無いときの文は下のテストで見る。
        write(os.path.join(self.repo, ".ccnavi", "scripts", "ccnavi-git.sh"), "exit 0\n")
        self.state = os.path.join(self.repo, "logs", "state")
        self.log = os.path.join(self.repo, "logs", "decisions.jsonl")

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
        self.assertEqual(result.stderr, "", "守ると宣言していない場所の変更は対象外")

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
        self.assertIn("rule: .ccnavi/config/common/rules.yml:protected", result.stderr)
        # 原因となった直前の実行。
        self.assertIn("Bash(python build.py)", result.stderr)
        # 戻す手順。
        self.assertIn(
            'ccnavi-git.sh restore --staged --worktree -- "protected/keep.txt"', result.stderr
        )
        # ルールが持つ文面。止めるだけでは足りないという道具の目的そのもの。
        self.assertIn("Ask the user", result.stderr)

    def test_ラッパーが無ければ戻す手順は書かずユーザに頼む(self):
        """生の git は組み込みで止まるので、戻す `git` の形を案内すると、案内した行が止まる。"""
        os.remove(os.path.join(self.repo, ".ccnavi", "scripts", "ccnavi-git.sh"))
        self.run_hook(command="ls")
        self.dirty()

        result = self.run_hook(command="python build.py")

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotIn("git restore", result.stderr)
        self.assertIn("ユーザに頼んで", result.stderr)

    def test_新しく現れたファイルは消す手順を返す(self):
        self.run_hook(command="ls")
        write(os.path.join(self.repo, "protected", "generated.env"), "SECRET=1\n")

        result = self.run_hook(command="npm run build")

        self.assertEqual(result.returncode, 2)
        self.assertIn("protected/generated.env", result.stderr)
        # 現れたファイルは restore では戻せない。git のラッパーは clean を通さないので、
        # シェルの rm で消す。
        self.assertIn('rm -- "protected/generated.env"', result.stderr)
        self.assertNotIn("git clean", result.stderr)

    def test_同じ変更を毎回は言わない(self):
        self.run_hook(command="ls")
        self.dirty()
        self.run_hook(command="python build.py")

        result = self.run_hook(command="ls -la")

        self.assertEqual(result.returncode, 0, "1 度伝えた変更を呼び出しのたびに繰り返さない")
        self.assertEqual(result.stderr, "")

    def test_同じ場所が別の変わり方をしたらもう一度言う(self):
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
        self.assertEqual(line["rules"], [".ccnavi/config/common/rules.yml:protected"])
        # 文面の先頭の行と同じ理由コードを記録にも残す。
        self.assertEqual(line["code"], "POST_VIOLATION")

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
        # 判定は allow のまま、文面で知らせた根拠を記録に残す。
        self.assertEqual(self.records()[-1]["code"], "POST_PREEXISTING")
        # 次の回は何も知らせないので code も無い。
        self.run_hook(command="ls")
        self.assertNotIn("code", self.records()[-1])

    def test_起動時に在った変更は初回の呼び出しで1度だけ伝え戻さない(self):
        self.dirty()
        self.run_hook(event="SessionStart", source="startup")

        first = self.run_hook(command="ls", restore="enable")
        second = self.run_hook(command="ls -la", restore="enable")

        self.assertEqual(first.returncode, 0, "起動時の変更は直前の実行の結果ではない")
        self.assertIn("POST_PREEXISTING", self.context(first))
        self.assertEqual(second.stdout, "", "1 度伝えたものは繰り返さない")
        with open(os.path.join(self.repo, "protected", "keep.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "changed by a build\n", "他人の書きかけを戻してはいけない")

    def test_起動後に現れた変更は起動時の基準に関わらず差し戻す(self):
        self.run_hook(event="SessionStart", source="startup")
        self.dirty()

        result = self.run_hook(command="python build.py")

        self.assertEqual(result.returncode, 2)
        self.assertIn("POST_VIOLATION", result.stderr)
        self.assertIn("protected/keep.txt", result.stderr)

    def test_ターンの間にユーザが直した保護領域は次の呼び出しで戻さない(self):
        # 起動時の基準は 1 度しか取らない。ユーザの発言で今ある変更を記録に足さないと、
        # セッションの途中でユーザが直したぶんが次のツール呼び出しの変更に見えて、
        # コミット済みの内容へ戻される。
        self.run_hook(event="SessionStart", source="startup")
        self.dirty("edited by the user\n")

        self.run_hook(event="UserPromptSubmit", prompt="続きをお願いします")
        result = self.run_hook(command="ls", restore="enable")

        self.assertEqual(result.returncode, 0)
        with open(os.path.join(self.repo, "protected", "keep.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "edited by the user\n", "ユーザの編集を戻してはいけない")

    def test_ユーザの編集を受け入れたあとの呼び出しの変更は戻す(self):
        self.run_hook(event="SessionStart", source="startup")
        self.dirty("edited by the user\n")
        self.run_hook(event="UserPromptSubmit", prompt="続きをお願いします")
        write(os.path.join(self.repo, "protected", "new.txt"), "built\n")

        result = self.run_hook(command="python build.py", restore="enable")

        self.assertIn("protected/new.txt", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "protected", "new.txt")))
        with open(os.path.join(self.repo, "protected", "keep.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "edited by the user\n")

    def test_バックグラウンドの完了通知ではユーザの編集として受け入れない(self):
        # 通知も UserPromptSubmit を起こすが、その間に動いたプロセスの変更は、
        # ユーザの変更と区別できない。受け入れずに、これまでどおり戻す。
        self.run_hook(event="SessionStart", source="startup")
        self.dirty("written by a background process\n")

        self.run_hook(
            event="UserPromptSubmit",
            prompt="<task-notification>\n<status>completed</status>\n</task-notification>",
        )
        result = self.run_hook(command="ls", restore="enable")

        self.assertEqual(result.returncode, 2)
        with open(os.path.join(self.repo, "protected", "keep.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "committed\n")

    def test_受け入れたユーザの編集のうえにエージェントが書いたら言うが戻さない(self):
        # 報告済みの記録の鍵は種類とパスだけ。指紋で見分けないと、受け入れたファイルへの
        # 以降の書き込みが全部素通しになる。一方、戻す先のコミット済みの内容はユーザの編集より
        # 古いので、戻すとその編集まで消える。だから言うだけにする。
        self.run_hook(event="SessionStart", source="startup")
        self.dirty("edited by the user\n")
        self.run_hook(event="UserPromptSubmit", prompt="続きをお願いします")

        self.dirty("overwritten by the agent\n")
        result = self.run_hook(command="python build.py", restore="enable")

        self.assertEqual(result.returncode, 2)
        self.assertIn("protected/keep.txt", result.stderr)
        self.assertIn("already held a change the user made", result.stderr)
        with open(os.path.join(self.repo, "protected", "keep.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "overwritten by the agent\n", "ユーザの編集まで消さない")

    def test_受け入れたあとの書き込みは1度言えば繰り返さない(self):
        self.run_hook(event="SessionStart", source="startup")
        self.dirty("edited by the user\n")
        self.run_hook(event="UserPromptSubmit", prompt="続きをお願いします")
        self.dirty("overwritten by the agent\n")
        self.run_hook(command="python build.py", restore="enable")

        again = self.run_hook(command="ls", restore="enable")

        self.assertEqual(again.returncode, 0)
        self.assertEqual(again.stdout, "")

    def test_起動時の変更が件数の上限を超えても戻さない(self):
        # 基準を件数で切ると、切った先の在る汚れが「新しい変更」になり、自動復元で消される。
        names = [os.path.join(self.repo, "protected", f"gen{i:03}.txt") for i in range(520)]
        for path in names:
            write(path, "generated\n")
        self.run_hook(event="SessionStart", source="startup")

        self.run_hook(command="ls", restore="enable")

        self.assertTrue(all(os.path.exists(path) for path in names), "他人の書きかけを消さない")

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

    def test_戻そうとしたかを呼び手に返す(self):
        """`post.check` の 2 つ目は、戻しを試みた回だけ真。呼び手はこれで置き場を読み直す。"""
        seen = []

        def spy(*args, **kwargs):
            out = real(*args, **kwargs)
            seen.append(out[1])
            return out

        real = post.check
        with mock.patch.object(post, "check", side_effect=spy):
            self.run_hook(command="ls")
            self.dirty()
            self.run_hook(restore="enable", command="python build.py")
            # 戻しが無効なら、差し戻す変更があっても戻そうとしない
            self.run_hook(session="s2", command="ls")
            self.dirty()
            self.run_hook(session="s2", restore="disable", command="python build.py")
        self.assertEqual(seen, [False, True, False, False])

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
        # 判定は本番で、戻しだけ予行。何が戻るのかを、戻される前に見せる設定。
        self.run_hook(command="ls")
        self.dirty()

        result = self.run_hook(restore="dry-run", command="python build.py")

        with open(os.path.join(self.repo, "protected", "keep.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "changed by a build\n", "予行はファイルに触らない")
        self.assertIn("would-restore:", result.stderr)
        self.assertIn("put it back to its committed content", result.stderr)
        # 誰も戻していないので、戻す手順は省かない。
        self.assertIn(
            'ccnavi-git.sh restore --staged --worktree -- "protected/keep.txt"', result.stderr
        )
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
        # CCNAVI_MODE=dry-run は戻しの側も予行にする（modes.effective_setting）。
        self.run_hook(mode="dry-run", command="ls")
        self.dirty()

        result = self.run_hook(mode="dry-run", restore="enable", command="python build.py")

        with open(os.path.join(self.repo, "protected", "keep.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "changed by a build\n")
        self.assertIn("would-restore:", self.context(result))

    def test_読めない自動復元の値は守る側になる(self):
        # 読めない値の行き先を off から enable に変えてある。書き損じた 1 語で保護が
        # 消えるより、書き損じた 1 語で保護が残るほうがよい、という向き。
        # 戻す先はコミット済みの内容なので、失われるのは「宣言した保護領域を
        # 汚した未コミットの変更」だけになる。
        self.run_hook(command="ls")
        self.dirty()

        result = self.run_hook(restore="yes", command="python build.py")

        with open(os.path.join(self.repo, "protected", "keep.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "committed\n")
        self.assertIn("は設定ではありません", result.stderr)

    # ターンの終わり

    def test_ターンの終わりに保護領域の変更をユーザへ報告する(self):
        self.run_hook(event="UserPromptSubmit")
        self.dirty()

        result = self.run_hook(event="Stop")

        self.assertEqual(result.returncode, 0, "報告のためにターンを続けさせない")
        message = json.loads(result.stdout)["systemMessage"]
        self.assertIn("protected/keep.txt", message)
        self.assertIn("protected", message, "どのルールが言っているか")
        self.assertIn("ccnavi-git.sh restore", message, "戻す手順")
        line = self.records()[-1]
        self.assertEqual(line["event"], "Stop")
        self.assertEqual(line["decision"], "deny")
        self.assertEqual(line["code"], "POST_VIOLATION")

    def test_ターンの終わりも変わっていなければ何も言わない(self):
        self.run_hook(event="UserPromptSubmit")

        result = self.run_hook(event="Stop")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_ターンが始まる前から在った変更は報告しない(self):
        # ユーザの書きかけ、他のセッションが置いたもの、前のターンで片付け
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

    def test_HEADを持たない記録は基準なしとして扱う(self):
        # heads の無い記録は、記録が無いときと同じ。コミットのぶんを数えられない
        # 基準で報告すると、このターンに入ったコミットを知らないうちに取りこぼす。
        self.run_hook(event="UserPromptSubmit")
        turn = os.path.join(self.state, "s1.turn.json")
        with open(turn, encoding="utf-8") as f:
            data = json.load(f)
        del data["heads"]
        write(turn, json.dumps(data))
        self.dirty()

        result = self.run_hook(event="Stop")

        self.assertEqual(result.stdout, "")
        self.assertEqual(self.records()[-1]["reason"], "no-turn-baseline")

    def test_ターンの終わりの報告は一度伝えた変更も含む(self):
        # 呼び出しごとの報告は記録を見て繰り返さないが、ユーザはまだ 1 度も
        # 見ていないことがある。宛先が違うので、記録を共有しない。
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

    def test_git_の作業ツリーでなければ何も言わずに通す(self):
        outside = tempfile.mkdtemp(prefix="ccnavi-plain-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        self.repo = outside
        self.rules = write(common_path(outside, "rules"), json.dumps(RULES))
        self.state = os.path.join(outside, "logs", "state")
        self.log = os.path.join(outside, "logs", "decisions.jsonl")

        result = self.run_hook(command="ls")

        self.assertEqual(result.returncode, 0, "見えないことでツールを止めない")
        # 見えなかったことは記録に残す。残さないと、違反が無かったのか
        # 見ていなかったのかが同じ見た目になる。
        line = self.records()[-1]
        self.assertEqual(line["reason"], "worktree-unreadable")
        self.assertEqual(line["detail"], "not-a-git-worktree")

    def test_自分が書く場所は自分の違反にしない(self):
        # 記録と state の置き場（既定の `logs/`）をルールが守る形。置き場を動かすフラグは
        # 診断でだけ効くので、ルールの側で既定の置き場を保護領域に入れる。
        rules = dict(RULES)
        rules["deny"] = [
            *RULES["deny"],
            {
                "id": "records",
                "match": "Write|Edit",
                "glob": "*/logs/*",
                "message": "records are written by ccnavi.",
            },
        ]
        write(self.rules, json.dumps(rules))

        self.run_hook(command="ls")
        result = self.run_hook(command="ls -la")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    # 戻す対象は deny だけ

    def test_askと宣言した場所は報告するが戻さない(self):
        # `ask` は「ユーザが 1 度見る場所」の宣言で、「書くな」ではない。戻すと、
        # ユーザが確認に「はい」と答えた編集をあとから無かったことにする。
        self.run_hook(command="ls")
        write(os.path.join(self.repo, "watched", "deps.txt"), "changed by a build\n")

        result = self.run_hook(restore="enable", command="python build.py")

        with open(os.path.join(self.repo, "watched", "deps.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "changed by a build\n", "ask の場所は戻さない")
        self.assertIn("POST_VIOLATION", result.stderr, "戻さなくても報告はする")
        self.assertIn("watched/deps.txt", result.stderr)
        self.assertNotIn("restored: ccnavi", result.stderr)
        # 戻していないので、戻す手順は載せたままにする。
        self.assertIn(
            'ccnavi-git.sh restore --staged --worktree -- "watched/deps.txt"', result.stderr
        )
        # 手順だけだと「自分で戻せ」としか読めない。戻さなかった理由をつける。
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
        self.assertIn("コミット済み", message)
        # 戻す手順は書かない。履歴は書き換えないので、案内できる 1 つが無い。
        self.assertNotIn("git restore", message)
        self.assertIn("はすでにコミット済みなので", message)
        self.assertIn("不要な変更なら取り消してください", message)

    def test_ターンが始まる前のコミットは言わない(self):
        # 前のターンや他のセッションが積んだコミットを、このターンの成果として
        # 並べない。基準はターンの始まりに記録した HEAD。
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
        # 承認はユーザが提案を .ccnavi/approved/ へ動かしてコミットする進め方。
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
        # ワークツリーを切って作業し、`merge <統合先>` を打ってから統合先へ取り込むのが
        # このリポジトリの手順。二点の差分で数えると、ユーザが統合先で直した保護領域が
        # 打つたびに並ぶ。数えるのはこのツリーが積んだコミットだけ。
        git(self.repo, "checkout", "--quiet", "-b", "feature")
        self.run_hook(event="UserPromptSubmit")
        write(os.path.join(self.repo, "src", "app.py"), "print(2)\n")
        git(self.repo, "add", "--", "src/app.py")
        git(self.repo, "commit", "--quiet", "-m", "自分の作業")
        git(self.repo, "checkout", "--quiet", "master")
        self.dirty("ユーザが統合先で直した\n")
        git(self.repo, "add", "--", "protected/keep.txt")
        git(self.repo, "commit", "--quiet", "-m", "統合先の変更")
        git(self.repo, "checkout", "--quiet", "feature")
        git(self.repo, "merge", "--quiet", "--no-edit", "master")

        result = self.run_hook(event="Stop")

        self.assertEqual(result.stdout, "", "取り込んだぶんを自分のターンの成果にしない")

    def integrate_and_finish(self):
        """`integrate` と `integrate-done` の後の形を作る。

        squash のコミット、片付け（ブランチを消す）、記録の写し。
        """
        home = git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        git(self.repo, "checkout", "--quiet", "-b", "feat")
        self.dirty("ワークツリーで直した\n")
        git(self.repo, "add", "--", "protected/keep.txt")
        git(self.repo, "commit", "--quiet", "-m", "ワークツリーの作業")
        tip = git(self.repo, "rev-parse", "HEAD").strip()
        git(self.repo, "checkout", "--quiet", home)
        base = git(self.repo, "rev-parse", "HEAD").strip()
        git(self.repo, "merge", "--quiet", "--squash", "feat")
        tree = git(self.repo, "write-tree").strip()
        git(self.repo, "commit", "--quiet", "-m", "取り込み")
        git(self.repo, "branch", "--quiet", "-D", "feat")
        ddir = os.path.join(self.repo, ".git", post_findings.INTEGRATE_DONE_DIR)
        write(
            os.path.join(ddir, "feat"),
            f"state staged\nbranch feat\nbranch_sha {tip}\nbase {base}\ntree {tree}\n",
        )

    @unittest.skipUnless(MERGE_TREE, "git merge-tree --write-tree (git 2.38+) is needed")
    def test_integrate_done_の取り込みはターンの報告に並べない(self):
        # 片付けの後なので staged の除外は効かない。squash と git で確かめたコミットだけを外す。
        self.run_hook(event="UserPromptSubmit")
        self.integrate_and_finish()

        result = self.run_hook(event="Stop")

        self.assertEqual(result.stdout, "", "正規の取り込みを違反として並べない")

    @unittest.skipUnless(MERGE_TREE, "git merge-tree --write-tree (git 2.38+) is needed")
    def test_取り込みと同じターンの直接のコミットは並べる(self):
        self.run_hook(event="UserPromptSubmit")
        self.integrate_and_finish()
        write(os.path.join(self.repo, "protected", "later.txt"), "直接書いた\n")
        git(self.repo, "add", "--", "protected/later.txt")
        git(self.repo, "commit", "--quiet", "-m", "直接のコミット")

        result = self.run_hook(event="Stop")

        message = json.loads(result.stdout)["systemMessage"]
        self.assertIn("protected/later.txt", message)
        self.assertNotIn("protected/keep.txt", message, "取り込みの分は外す")

    def test_記録の写しが無い取り込みのコミットは並べる(self):
        # 記録が無ければ squash と確かめられないので、今までどおり報告する。
        self.run_hook(event="UserPromptSubmit")
        self.integrate_and_finish()
        os.remove(os.path.join(self.repo, ".git", post_findings.INTEGRATE_DONE_DIR, "feat"))

        result = self.run_hook(event="Stop")

        message = json.loads(result.stdout)["systemMessage"]
        self.assertIn("protected/keep.txt", message)

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
        self.assertNotIn(
            "今回のターンでこの確認ができませんでした", message, "基準が付いたツリーは数える"
        )

    def test_ターンの途中で切ったツリーのコミットは分岐点から数える(self):
        # 記録済みの HEAD との分岐点を基準にするので、確認不能の警告は出ず、保護領域の変更は並ぶ。
        self.run_hook(event="UserPromptSubmit")
        later = os.path.join(self.repo, ".claude", "worktrees", "wt1")
        git(self.repo, "worktree", "add", "--quiet", "-b", "wt1", later)
        write(os.path.join(later, "protected", "keep.txt"), "切ったあとに汚してコミット\n")
        git(later, "add", "--", "protected/keep.txt")
        git(later, "commit", "--quiet", "-m", "切ったあとのコミット")

        result = self.run_hook(event="Stop")

        message = json.loads(result.stdout)["systemMessage"]
        self.assertIn("protected/keep.txt", message)
        self.assertNotIn("今回のターンでこの確認ができませんでした", message)

    def test_基準を持たないツリーは数えていないと言う(self):
        # ターンの途中で切ったワークツリーは、プロンプトのときに無いので基準を
        # 持たない。何も言わずに飛ばすと、そのツリーで何も起きなかったのと見分けが付かない。
        self.run_hook(event="UserPromptSubmit")
        later = os.path.join(self.repo, ".claude", "worktrees", "wt1")
        git(self.repo, "worktree", "add", "--quiet", "--orphan", "-b", "wt1", later)

        result = self.run_hook(event="Stop")

        message = json.loads(result.stdout)["systemMessage"]
        self.assertIn("ワークツリー wt1 では、今回のターンでこの確認ができませんでした", message)
        self.assertIn("uncounted", self.records()[-1].get("detail", ""))

    def test_ターンの終わりの記録は数えていないツリーと違反の両方を残す(self):
        # detail を上書きすると、先に入った断りが消える。足していく。
        self.run_hook(event="UserPromptSubmit")
        later = os.path.join(self.repo, ".claude", "worktrees", "wt1")
        git(self.repo, "worktree", "add", "--quiet", "--orphan", "-b", "wt1", later)
        self.dirty()

        self.run_hook(event="Stop")

        line = self.records()[-1]
        self.assertEqual(line["code"], "POST_VIOLATION")
        self.assertIn("uncounted 1", line.get("detail", ""))

    def test_戻さなかった1件は記録に入りターンの終わりにユーザへ出る(self):
        # 戻していないのでファイルは汚れたまま。呼び出しごとに言えば同じ文が
        # 呼び出しの数だけ積まれるので、報告はセッションで 1 度きりにする。
        # ユーザが見るのはターンの終わりの報告（Stop）。
        # 記録（セッション）とターンの基準の両方を、汚す前に置く。
        self.run_hook(command="ls")
        self.run_hook(event="UserPromptSubmit")
        write(os.path.join(self.repo, "watched", "deps.txt"), "first\n")
        first = self.run_hook(restore="enable", command="python build.py")
        self.assertIn("watched/deps.txt", first.stderr)

        write(os.path.join(self.repo, "watched", "deps.txt"), "second and different\n")
        again = self.run_hook(restore="enable", command="python build.py")

        self.assertEqual(again.stderr, "", "同じ汚れを呼び出しごとには言わない")
        self.assertIn("known", self.records()[-1].get("detail", ""))
        # ターンの終わりには出る。ユーザはここで「結局どこが変わったのか」を 1 度で見る。
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

# 着手済みの版。`ticket start` が書いたあと、ユーザがコミットして親のブランチに乗った状態。
STARTED = TICKET.replace(
    "---\nbody", 'started_at: "2026-09-21T00:00:00Z"\nbase_sha: "1111111111111111"\n---\nbody'
)

DOING = ".ccnavi/approved/doing/i0001.md"


def ticket_repo(committed=True, text=TICKET):
    """承認済みチケットを 1 本持つリポジトリ。`committed` が偽ならコミットを作らない。"""
    repo = tempfile.mkdtemp(prefix="ccnavi-place-")
    git(repo, "init", "--quiet")
    write(os.path.join(repo, *DOING.split("/")), text)
    write(os.path.join(repo, "src", "app.py"), "print(1)\n")
    if committed:
        git(repo, "add", "-A")
        git(repo, "commit", "--quiet", "-m", "init")
    write(common_path(repo, "rules"), json.dumps(RULES))
    return repo


class RecordCodeTest(unittest.TestCase):
    """記録の code は 1 つ。件の code が混ざった回は強いほうを載せ、内訳を detail に書く。"""

    @staticmethod
    def found(*codes):
        return [
            post_findings.Finding(gitstate.Change(path=f"f{i}"), [], "", code)
            for i, code in enumerate(codes)
        ]

    def test_ルールの違反はチケットの範囲外より強い(self):
        found = self.found("POST_TICKET_SCOPE", "POST_VIOLATION", "POST_TICKET_SCOPE")
        self.assertEqual(post_findings.record_code(found), "POST_VIOLATION")
        self.assertEqual(
            post_findings.codes_note(found), "codes POST_VIOLATION 1, POST_TICKET_SCOPE 2"
        )

    def test_1種類だけなら内訳を書かない(self):
        found = self.found("POST_TICKET_SCOPE", "POST_TICKET_SCOPE")
        self.assertEqual(post_findings.record_code(found), "POST_TICKET_SCOPE")
        self.assertEqual(post_findings.codes_note(found), "")

    def test_件が無ければ空(self):
        self.assertEqual(post_findings.record_code([]), "")
        self.assertEqual(post_findings.codes_note([]), "")


class StopNudgeRecordTest(unittest.TestCase):
    """同じ Stop で報告と促しが重なった回の記録。止めた促しが勝ち、報告は detail に残る。"""

    def test_報告のcodeとrulesをdetailへ移す(self):
        record = audit.Record(
            decision=audit.DENY,
            code="POST_VIOLATION",
            rules=["guard-a", "guard-b"],
            detail="uncounted 1",
        )
        events._over_report(record)
        self.assertEqual(record.detail, "uncounted 1; report POST_VIOLATION guard-a,guard-b")
        self.assertEqual(record.rules, [])

    def test_報告が無ければ何もしない(self):
        record = audit.Record(decision=audit.ALLOW, detail="uncounted 1")
        events._over_report(record)
        self.assertEqual(record.detail, "uncounted 1")


class TicketPlaceTest(Harness, unittest.TestCase):
    """チケットの置き場の変更を、誰が書いたかではなく何が変わったかで見分ける。

    ここは ccnavi のサブコマンド（`ticket start` / `finish`、`review request` / `confirm` /
    `ready`）が書く場所で、同時にプロジェクトが `deny` と宣言した場所でもある。外さないと、自分の
    手順を自分で違反として報告し、戻す設定では自分で戻して手順が進まなくなる。外しすぎると、
    承認済みチケットが宣言する範囲を、引数に現れない書き込みで広げる経路ができる。
    """

    def setUp(self):
        self.repo = ticket_repo()
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.state = os.path.join(self.repo, "logs", "state")
        self.log = os.path.join(self.repo, "logs", "decisions.jsonl")
        # 基準を取る。以降に現れたものが、この呼び出しの結果として見られる。
        self.run_hook(command="ls")

    def use(self, repo):
        """別のリポジトリに差し替えて、そこで基準を取り直す。"""
        self.repo = repo
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        self.state = os.path.join(repo, "logs", "state")
        self.log = os.path.join(repo, "logs", "decisions.jsonl")
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
        # `ticket finish`。`doing/` から消えて、正規化した内容が同じものが `review/` に現れる。
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

    def test_承認済みの置き場の報告には_ccnavi_自身の書き込みの可能性を添える(self):
        write(self.path(DOING), TICKET.replace('glob: "src/*"', 'glob: "*"'))

        result = self.ran_script()

        self.assertEqual(result.returncode, 2)
        self.assertIn("ccnavi itself most likely wrote this", result.stderr)
        self.assertIn("Do not run undo:", result.stderr)
        # 判定は変えない。報告も undo も出る。
        self.assertIn("undo:", result.stderr)

    def test_承認済みの置き場の外の報告には添えない(self):
        from ccnavi.hook import post_report

        self.assertTrue(post_report._in_approved_area(".ccnavi/approved/doing/a.md"))
        self.assertTrue(post_report._in_approved_area("projects/x/.ccnavi/approved/done/a.md"))
        self.assertFalse(post_report._in_approved_area("wip/proposals/todo/a.md"))
        self.assertFalse(post_report._in_approved_area("src/.ccnavi/approvedX/a.md"))

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

    def test_着手済みのチケットを閉じて動かしても言わない(self):
        # `ticket finish`。着手の欄はコミット済みの版と同じまま、完了の時刻だけが足される。
        self.use(ticket_repo(text=STARTED))
        os.remove(self.path(DOING))
        write(
            self.path("wip/proposals/review/i0001.md"),
            STARTED.replace("---\nbody", 'completed_at: "2026-09-22T01:00:00Z"\n---\nbody'),
        )

        result = self.ran_script()

        self.assertEqual(result.returncode, 0, self.said(result))

    def test_空の欄を持つチケットを閉じて動かしても言わない(self):
        # 承認は `started_at: ""` の形で欄を置く（approval_ops）。`""` を値と数えると、
        # `finish` が書いた時刻を既にある値の書き換えと読み、正規の移動を報告していた。
        placed = TICKET.replace(
            "---\nbody", 'started_at: ""\ncompleted_at: ""\nbase_sha: ""\n---\nbody'
        )
        self.use(ticket_repo(text=placed))
        os.remove(self.path(DOING))
        write(
            self.path("wip/proposals/review/i0001.md"),
            placed.replace('started_at: ""', 'started_at: "2026-09-22T00:00:00Z"')
            .replace('completed_at: ""', 'completed_at: "2026-09-22T01:00:00Z"')
            .replace('base_sha: ""', 'base_sha: "1111111111111111"'),
        )

        result = self.ran_script()

        self.assertEqual(result.returncode, 0, self.said(result))

    def test_未コミットのレビュー待ちを閉じた置き場へ動かしても言わない(self):
        # `finish` で `review/` に置いたものをコミットせずに `confirm` した形。`review/` の側は
        # HEAD に無いので git status から消え、`doing/` の削除と `done/` の新規が組になる。
        placed = TICKET.replace(
            "---\nbody", 'started_at: ""\ncompleted_at: ""\nbase_sha: ""\n---\nbody'
        )
        self.use(ticket_repo(text=placed))
        os.remove(self.path(DOING))
        write(
            self.path(".ccnavi/approved/done/i0001.md"),
            placed.replace('started_at: ""', 'started_at: "2026-09-22T00:00:00Z"')
            .replace('completed_at: ""', 'completed_at: "2026-09-22T01:00:00Z"')
            .replace('base_sha: ""', 'base_sha: "1111111111111111"'),
        )

        result = self.ran_script()

        self.assertEqual(result.returncode, 0, self.said(result))

    def test_基準点の書き換えは言う(self):
        # `base_sha` はサブエージェント終了時チェックと実績リスクの基準点。書き換えられると
        # コミット済みの範囲外の変更が検査から消えるので、正規化で除いてはいけない。
        self.use(ticket_repo(text=STARTED))
        write(self.path(DOING), STARTED.replace("1111111111111111", "2222222222222222"))

        result = self.ran_script()

        self.assertEqual(result.returncode, 2)
        self.assertIn("POST_VIOLATION", result.stderr)

    def test_mrが空から整数になっただけなら言わない(self):
        # `review request` が MR を作った後に、承認済みチケットの空の `mr:` へ番号を書き足す。
        # 欄が無い版・値なしの版のどちらからでも、ほかが 1 文字も変わらなければ ccnavi の書き込み。
        for before in (TICKET, TICKET.replace("---\nbody", 'mr: ""\n---\nbody')):
            with self.subTest(before=before):
                self.use(ticket_repo(text=before))
                write(self.path(DOING), TICKET.replace("---\nbody", "mr: 12\n---\nbody"))

                result = self.ran_script()

                self.assertEqual(result.returncode, 0, self.said(result))

    def test_mrの整数が別の整数や空になるのは言う(self):
        held = TICKET.replace("---\nbody", "mr: 12\n---\nbody")
        for now in (
            TICKET.replace("---\nbody", "mr: 13\n---\nbody"),
            TICKET.replace("---\nbody", 'mr: ""\n---\nbody'),
            TICKET,
        ):
            with self.subTest(now=now):
                self.use(ticket_repo(text=held))
                write(self.path(DOING), now)

                result = self.ran_script()

                self.assertEqual(result.returncode, 2)
                self.assertIn("POST_VIOLATION", result.stderr)

    def test_mrを足すと同時にほかが変わっていれば言う(self):
        self.use(ticket_repo(text=TICKET))
        changed = TICKET.replace("---\nbody", "mr: 12\n---\nbody").replace("src/*", "*")
        write(self.path(DOING), changed)
        self.assertEqual(self.ran_script().returncode, 2)

        self.use(ticket_repo(text=TICKET))
        write(self.path(DOING), TICKET.replace("---\nbody", "mr: 12\n---\nbody2"))
        self.assertEqual(self.ran_script().returncode, 2)

    def test_mrが整数以外の書き方なら言う(self):
        for raw in ('"12"', "12 # c", "[12]", "0", "-1", "x"):
            with self.subTest(raw=raw):
                self.use(ticket_repo(text=TICKET))
                write(self.path(DOING), TICKET.replace("---\nbody", f"mr: {raw}\n---\nbody"))

                result = self.ran_script()

                self.assertEqual(result.returncode, 2, raw)

    def test_mrの足し込みと基準点の書き換えが一緒なら言う(self):
        self.use(ticket_repo(text=STARTED))
        now = STARTED.replace("1111111111111111", "2222222222222222").replace(
            "---\nbody", "mr: 12\n---\nbody"
        )
        write(self.path(DOING), now)

        self.assertEqual(self.ran_script().returncode, 2)

    def test_続きの行に置いた基準点の書き換えは言う(self):
        # 1 行目だけを読むと空に見える書き方（`base_sha: |` の次の行に値）。値なしと読むと、
        # 欄ごと比較から落ちて書き換えが報告されない。
        block = TICKET.replace("---\nbody", "base_sha: |\n  1111111111111111\n---\nbody")
        self.use(ticket_repo(text=block))
        write(self.path(DOING), block.replace("1111111111111111", "2222222222222222"))

        result = self.ran_script()

        self.assertEqual(result.returncode, 2)
        self.assertIn("POST_VIOLATION", result.stderr)

    def test_着手の時刻の書き換えは言う(self):
        self.use(ticket_repo(text=STARTED))
        write(self.path(DOING), STARTED.replace("2026-09-21", "2026-09-20"))

        result = self.ran_script()

        self.assertEqual(result.returncode, 2)
        self.assertIn("POST_VIOLATION", result.stderr)

    def test_承認待ちからレビュー待ちへの移動は言う(self):
        # ユーザの承認を通っていない提案を、レビュー待ちに見せる形。移動の元は
        # 作業中かレビュー待ちに限る。
        write(self.path("wip/proposals/todo/i0009.md"), TICKET.replace("i0001", "i0009"))
        git(self.repo, "add", "--", "wip/proposals/todo/i0009.md")
        git(self.repo, "commit", "--quiet", "-m", "propose")
        self.run_hook(command="ls")
        os.remove(self.path("wip/proposals/todo/i0009.md"))
        write(self.path("wip/proposals/review/i0009.md"), TICKET.replace("i0001", "i0009"))

        result = self.ran_script()

        self.assertEqual(result.returncode, 2)
        self.assertIn("POST_VIOLATION", result.stderr)

    def test_同じ内容が2つ動くときは移動として外さない(self):
        # 正規の移動 1 件に、同じ内容のチケットのただの削除が一緒に通ってはいけない。
        # 内容が同じなら識別子も同じなので、揃うのは普通の手順では起きない。
        write(self.path(".ccnavi/approved/doing/i0002.md"), TICKET)
        git(self.repo, "add", "--", ".ccnavi/approved/doing/i0002.md")
        git(self.repo, "commit", "--quiet", "-m", "twin")
        self.run_hook(command="ls")
        os.remove(self.path(DOING))
        write(
            self.path("wip/proposals/review/i0001.md"),
            TICKET.replace("---\nbody", 'completed_at: "2026-09-22T01:00:00Z"\n---\nbody'),
        )
        os.remove(self.path(".ccnavi/approved/doing/i0002.md"))

        result = self.ran_script()

        self.assertEqual(result.returncode, 2)
        self.assertIn("POST_VIOLATION", result.stderr)
        self.assertIn("i0002", result.stderr)

    def test_行き先に同じ内容が2つあるときも外さない(self):
        # 正規の移動に、行き先へ直接置いた偽物が一緒に通る形。
        self.run_hook(command="ls")
        os.remove(self.path(DOING))
        moved = TICKET.replace("---\nbody", 'completed_at: "2026-09-22T01:00:00Z"\n---\nbody')
        write(self.path("wip/proposals/review/i0001.md"), moved)
        write(self.path("wip/proposals/review/i0001-copy.md"), moved)

        result = self.ran_script()

        self.assertEqual(result.returncode, 2)
        self.assertIn("POST_VIOLATION", result.stderr)

    def test_コミット済みの版を読めなければ言う(self):
        # HEAD が無いリポジトリ。突き合わせる相手が読めないので、外す側にはしない。
        # 読めていれば外れるはずのマーカーで見る。外れたら、突き合わせを飛ばしたということ。
        self.use(ticket_repo(committed=False))
        write(self.path(".ccnavi/approved/phases/i0001/1.requested"), '{"mr": 1}\n')

        result = self.ran_script()

        self.assertEqual(result.returncode, 2)
        self.assertIn("POST_VIOLATION", result.stderr)


class UndoTextTest(unittest.TestCase):
    """戻す手順の文（`gitstate.undo`）は、git のラッパーで打てる形だけを書く。"""

    def change(self, kind, staged=False):
        return gitstate.Change(kind=kind, path="p/x.txt", full="", status="", staged=staged)

    def test_wrapper_forms(self):
        git = "sh /w/.ccnavi/scripts/ccnavi-git.sh"
        self.assertEqual(
            gitstate.undo(self.change(gitstate.KIND_CHANGED), git),
            f'{git} restore --staged --worktree -- "p/x.txt"',
        )
        # ラッパーは rm -f と clean を通さない。索引から外してシェルの rm で消す。
        self.assertEqual(
            gitstate.undo(self.change(gitstate.KIND_NEW, staged=True), git),
            f'{git} restore --staged -- "p/x.txt" && rm -- "p/x.txt"',
        )
        self.assertEqual(gitstate.undo(self.change(gitstate.KIND_NEW), git), 'rm -- "p/x.txt"')

    def test_git_command_follows_the_wrapper(self):
        root = tempfile.mkdtemp(prefix="ccnavi-gitcmd-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        # ラッパーが無ければ、生の git は組み込みで止まらないので git を案内する。
        self.assertEqual(settings.git_command(root), "git")
        write(os.path.join(root, ".ccnavi", "scripts", "ccnavi-git.sh"), "")
        self.assertEqual(settings.git_command(root), settings.script_command(root, "ccnavi-git.sh"))

    def test_report_uses_the_wrapper(self):
        # 実行後チェックの報告は、ワークスペースのラッパーで打てる形を案内する。
        harness = PostToolUseTest("test_変更を検知したら差し戻し戻し方と直前の実行を返す")
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        write(os.path.join(harness.repo, ".ccnavi", "scripts", "ccnavi-git.sh"), "")
        harness.run_hook(command="ls")
        harness.dirty()
        result = harness.run_hook(command="python build.py")
        git = settings.script_command(harness.repo, "ccnavi-git.sh")
        self.assertIn(
            f'undo: {git} restore --staged --worktree -- "protected/keep.txt"', result.stderr
        )


if __name__ == "__main__":
    unittest.main()
