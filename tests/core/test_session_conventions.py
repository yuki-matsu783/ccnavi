"""セッション開始で渡す、ccnavi が前提にしている作業の決まりの受入テスト。

見るのは 6 つ。

1. 起動のたびに、ワークツリー・git の入口・下書きの置き場・サブエージェント・合意の要る変更が
   1 行ずつ届く。git の入口は打てる絶対パスで、詳しいことは --help と拒否の案内に聞く形
2. ワークツリーの行は `workspace-root` の deny がワークスペースのルール（共通レイヤーか自身の
   レイヤー）にあるときだけ載る。ルールが読めず既定に戻ったときは載らない。
   git の行は、生の git を組み込みがいつも止めるので常に載る。文は、ラッパーがあれば打つ形を
   名指しし、無ければユーザに頼む形にする
3. チケット制御が有効なときだけ、承認済みチケットの状態の確かめ方（`status`）が載る
4. projects/ にプロジェクトがあるときだけ、cd してから作業する行が載る
5. md の索引の案内が出る回だけ、詳しい決まりを --docs で引く行が載る
6. サブエージェントの起動（agent_id がある）には出さない。文は短い
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest

from tests import ROOT, put_ccnavi_md
from tests.core.test_lint import SOUND, rules_file
from tests.inproc import run_ccnavi

HEAD = "# ccnavi の作業の決まり"
WORKTREE_LINE = ".claude/worktrees/<名前>"
GIT_LINE = "git は直接呼ばず"
# ラッパーが無いワークスペースの git の行。誘導先が無いので、ユーザに頼む形になる。
NO_WRAPPER_GIT_LINE = "git は直接呼ばない。"
# ワークツリーと git の行を支える deny。中身は判定に関係しないので、最小限にしてある。
WORKSPACE_ROOT_RULE = {
    "id": "workspace-root",
    "match": "Write",
    "glob": "*/never/*",
    "message": "m",
}


def block(text: str) -> str:
    """additionalContext から作業の決まり（.ccnavi/CCNAVI.md の本文）だけを抜く。無ければ空。

    本文は空行を挟んで続く。次の `[ccnavi]` で始まる段落（索引の案内など）の手前までを返す。
    """
    start = text.find(HEAD)
    if start < 0:
        return ""
    end = text.find("\n\n[ccnavi] ", start)
    return text[start:] if end < 0 else text[start:end]


class SessionConventionsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = os.path.realpath(directory.name)
        self.rules = rules_file(self.root, SOUND, WORKSPACE_ROOT_RULE)
        put_ccnavi_md(self.root)
        self.wrapper = os.path.join(self.root, ".ccnavi", "scripts", "ccnavi-git.sh")
        os.makedirs(os.path.dirname(self.wrapper), exist_ok=True)
        with open(self.wrapper, "w", encoding="utf-8") as f:
            f.write("exit 0\n")

    def start(self, *extra, **payload_extra) -> str:
        payload = {"hook_event_name": "SessionStart", "session_id": "s1", **payload_extra}
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        result = run_ccnavi(
            ["--root", self.root, "--rules", self.rules, *extra],
            input=json.dumps(payload),
            cwd=ROOT,
            env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if not result.stdout.strip():
            return ""
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_作業の決まりが1行ずつ届く(self):
        text = block(self.start("--mode", "enable"))

        self.assertIn(".claude/worktrees/<名前>", text)
        self.assertIn(
            f"sh {self.root}/.ccnavi/scripts/ccnavi-worktree.sh <名前> でワークツリー", text
        )
        self.assertNotIn("--help（worktree add）", text)
        git_sh = f"sh {self.root}/.ccnavi/scripts/ccnavi-git.sh"
        self.assertIn(git_sh, text)
        self.assertIn("迂回せず", text)
        self.assertIn("wip/handoff/", text)
        self.assertIn("integrate が外し", text)
        # ルート直下の使い捨ての置き場は、ワークツリーの中から書き違えないよう絶対パスで届く。
        self.assertIn(f"{self.root}/scratchpad/", text)
        self.assertNotIn("追跡されず", text)
        self.assertIn("サブエージェントにバックグラウンドで任せ", text)
        self.assertIn("実装する前にユーザと合意する", text)

    def test_考え方と保護対象の直し方も届く(self):
        text = block(self.start("--mode", "enable"))

        self.assertIn("案内が出たらそれに従い、迂回しない", text)
        self.assertIn("他セッションのもの", text)
        self.assertIn(f"sh {self.root}/.ccnavi/scripts/ccnavi-stage.sh <元のパス>", text)
        # 写した下書きは、ユーザが `ccnavi user handoff` で取り込む（mv を案内しない）。
        self.assertIn("ユーザに `ccnavi user handoff` での取り込みを案内してターンを終える", text)
        self.assertNotIn("mv（フルパス）", text)

    def test_ccnavi_mdが無ければ無いことだけを言う(self):
        os.remove(os.path.join(self.root, ".ccnavi", "CCNAVI.md"))
        text = self.start("--mode", "enable")

        self.assertIn(".ccnavi/CCNAVI.md が無いか読めない", text)
        self.assertNotIn(HEAD, text)

    def test_workspace_rootのdenyが無ければワークツリーの行は出ない(self):
        rules_file(self.root, SOUND)
        text = block(self.start("--mode", "enable"))

        self.assertNotIn(WORKTREE_LINE, text)
        # git の行は組み込みが支えるので、ルールに無くても載る。
        self.assertIn(GIT_LINE, text)
        # ほかの行は残る。
        self.assertIn("wip/handoff/", text)
        self.assertIn("実装する前にユーザと合意する", text)

    def test_行ごとに支えるものを見る(self):
        """ワークツリーの行は deny を見る。git の行は組み込みが支えるので、ルールに依らない。"""
        rules_file(self.root, SOUND, WORKSPACE_ROOT_RULE)
        text = block(self.start("--mode", "enable"))
        self.assertIn(WORKTREE_LINE, text)
        self.assertIn(GIT_LINE, text)

        rules_file(self.root, SOUND)
        text = block(self.start("--mode", "enable"))
        self.assertNotIn(WORKTREE_LINE, text)
        self.assertIn(GIT_LINE, text)

    def test_ワークスペース自身のレイヤーのdenyも数える(self):
        rules_file(self.root, SOUND)
        own = os.path.join(self.root, ".ccnavi", "config", "project", "rules.yml")
        os.makedirs(os.path.dirname(own), exist_ok=True)
        with open(own, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "deny": [WORKSPACE_ROOT_RULE]}, f)
        text = block(self.start("--mode", "enable"))

        self.assertIn(WORKTREE_LINE, text)

    def test_allowやaskにある同じidは数えない(self):
        rules_file(self.root, SOUND)
        own = os.path.join(self.root, ".ccnavi", "config", "project", "rules.yml")
        os.makedirs(os.path.dirname(own), exist_ok=True)
        with open(own, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "ask": [WORKSPACE_ROOT_RULE], "allow": []}, f)
        text = block(self.start("--mode", "enable"))

        self.assertNotIn(WORKTREE_LINE, text)

    def test_ルールが読めず既定に戻ってもワークツリーの行は出ない(self):
        with open(self.rules, "w", encoding="utf-8") as f:
            f.write("version: [壊れている\n")
        text = block(self.start("--mode", "enable"))

        self.assertNotIn(WORKTREE_LINE, text)
        # 既定に戻っても組み込みの止めは足されるので、git の行は載る。
        self.assertIn(GIT_LINE, text)

    def test_ラッパーが無ければgitの行はユーザに頼む形になる(self):
        """生の git は常に止まるので、`git` を勧めない。入口が無いときは頼む文にする。"""
        os.remove(self.wrapper)
        text = block(self.start("--mode", "enable"))

        self.assertIn(NO_WRAPPER_GIT_LINE, text)
        self.assertNotIn(GIT_LINE, text)
        self.assertNotIn(f"sh {self.root}/.ccnavi/scripts/ccnavi-git.sh", text)

    def test_git行はチケット制御の可否に依らず載る(self):
        self.assertIn(GIT_LINE, block(self.start("--mode", "enable")))
        self.assertIn(
            GIT_LINE, block(self.start("--mode", "enable", "--ticket-control", "disable"))
        )
        with open(self.rules, "w", encoding="utf-8") as f:
            f.write("version: [壊れている\n")
        self.assertIn(GIT_LINE, block(self.start("--mode", "enable")))

    def test_チケット制御が有効ならstatusの確かめ方が載る(self):
        text = block(self.start("--mode", "enable"))

        self.assertIn(f"sh {self.root}/.ccnavi/scripts/ccnavi-ticket.sh status [<親>]", text)

    def test_チケット制御がdisableでも決まりは届きstatusは載らない(self):
        text = block(self.start("--mode", "enable", "--ticket-control", "disable"))

        self.assertIn("ccnavi-git.sh", text)
        self.assertNotIn("ccnavi-ticket.sh", text)

    def test_プロジェクトがあるときだけcdの行が載る(self):
        self.assertNotIn("cd してから", block(self.start("--mode", "enable")))

        os.makedirs(os.path.join(self.root, "projects", "lib", ".git"))
        text = block(self.start("--mode", "enable"))

        self.assertIn("projects/<名前>/ を直すときは、そこへ cd してから作業する", text)

    def test_索引の案内が出る回だけdocsの行が載る(self):
        self.assertNotIn("--docs", block(self.start("--mode", "enable")))

        subprocess.run(["git", "init", "-q", self.root], check=True)
        with open(os.path.join(self.root, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("**/ccnavi-index.jsonl\n")
        with open(os.path.join(self.root, "README.md"), "w", encoding="utf-8") as f:
            f.write("---\ntype: guide\n---\n# x\n")
        text = self.start("--mode", "enable")

        self.assertIn("--docs で、--keyword <語> を付けて引く", block(text))
        # 「下の案内」は索引の案内を指すので、その案内は決まりより後ろに出る。
        self.assertLess(text.index(HEAD), text.index("[ccnavi] ドキュメント（*.md）"))

    def test_dry_runの注記は進め方の最後の行のまま(self):
        text = self.start("--mode", "dry-run")

        self.assertTrue(text.splitlines()[-1].startswith("（現状: CCNAVI_MODE=dry-run"), text)
        self.assertLess(text.index(HEAD), text.index("チケット制御を使っている"))

    def test_チケット制御がdisableならdry_runの注記も出ない(self):
        text = self.start("--mode", "dry-run", "--ticket-control", "disable")

        self.assertIn(HEAD, text)
        self.assertNotIn("（現状:", text)

    def test_サブエージェントには出さない(self):
        self.assertEqual(block(self.start("--mode", "enable", agent_id="a1")), "")

    def test_毎回届くので短い(self):
        # 毎回の起動・再開・compact に届く。考え方・作業の決まり・詳細の表・進め方を合わせて
        # 3000 字未満に収める（詳細は .ccnavi/docs/ に分け、ここには入口だけ置く）。
        os.makedirs(os.path.join(self.root, "projects", "lib", ".git"))
        text = block(self.start("--mode", "enable"))
        body = text.replace(self.root, "<root>")

        self.assertLess(len(body), 3000, body)

    def test_dry_runの注記を足しても短い(self):
        os.makedirs(os.path.join(self.root, "projects", "lib", ".git"))
        text = block(self.start("--mode", "dry-run"))
        body = text.replace(self.root, "<root>")

        self.assertLess(len(body), 3000, body)


if __name__ == "__main__":
    unittest.main()
