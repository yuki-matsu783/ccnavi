"""拡張のテストを「触ったファイルに関わるグループだけ」回す仕掛けの検査。

見るのは 2 つ。

1. `vscode-extension/ccnavi-board/scripts/test-groups.js --plan` が、触ったファイルから
   回すグループを決める。中の関数は呼ばず、出る 1 行だけを見る（`groups=... compile=...
   webview=...`）。この行が sh（`.claude/hooks/test-ext.sh`）との契約
2. `.claude/hooks/mark-ext.sh` が、拡張のファイルを触ったときだけ書き残す

`--plan` は node_modules が無くても動く（読むのはソースの綴りだけ）。組み立ても
テストもしないので、このグループの時間の中に収まる。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT

BOARD = os.path.join(ROOT, "vscode-extension", "ccnavi-board")
RUNNER = os.path.join(BOARD, "scripts", "test-groups.js")
MARK = os.path.join(ROOT, ".claude", "hooks", "mark-ext.sh")

NODE = shutil.which("node")
SH = shutil.which("sh")

# test/ の下でグループではないもの。runner 側と同じ決まり。
NOT_GROUPS = {"helpers", "fixtures"}


def groups_on_disk():
    base = os.path.join(BOARD, "test")
    found = [
        name
        for name in os.listdir(base)
        if os.path.isdir(os.path.join(base, name)) and name not in NOT_GROUPS
    ]
    return sorted(found)


def plan(*paths):
    """`--plan` の 1 行を読み、辞書にする。"""
    result = subprocess.run(
        [NODE, RUNNER, "--plan", "--for", *paths],
        capture_output=True,
        text=True,
        cwd=BOARD,
        check=True,
    )
    fields = dict(part.split("=", 1) for part in result.stdout.split())
    return {
        "groups": sorted(filter(None, fields["groups"].split(","))),
        "compile": fields["compile"] == "yes",
        "webview": fields["webview"] == "yes",
    }


@unittest.skipIf(NODE is None, "node が無い")
class ExtTestPlanTest(unittest.TestCase):
    def test_a_source_file_calls_the_groups_that_read_it(self):
        # rules 画面の文書。rules のテストが直接読み、projects と shared も辿って読む。
        # 辿り方を表で持っていないので、import が変われば期待も変わる。ここでは
        # 「読むグループが挙がり、読まないグループは挙がらない」ことだけを見る。
        got = plan("src/core/risk-doc.ts")
        self.assertIn("risk", got["groups"])
        self.assertNotIn("rules", got["groups"])
        self.assertNotIn("board", got["groups"])
        self.assertTrue(got["compile"])

    def test_a_test_file_calls_its_own_group_only(self):
        got = plan("test/risk/risk-doc.test.ts")
        self.assertEqual(["risk"], got["groups"])

    def test_the_screen_calls_the_groups_that_read_the_bundle(self):
        # 画面（React）は esbuild が束ね、テストは束ねたものを読む。import では
        # 辿れないので、束ねたものを読むグループが挙がり、束ねる工程も入る。
        got = plan("src/webview/board/App.tsx")
        self.assertIn("board", got["groups"])
        self.assertTrue(got["webview"])

    def test_the_shared_helper_calls_every_group(self):
        # どのグループも読む部品。触ったら全部回る。
        self.assertEqual(groups_on_disk(), plan("test/helpers/dom.ts")["groups"])

    def test_the_build_ground_calls_every_group(self):
        for path in ("package.json", "tsconfig.test.json", "scripts/bundle-webview.js"):
            with self.subTest(path=path):
                self.assertEqual(groups_on_disk(), plan(path)["groups"])

    def test_fixtures_call_every_group(self):
        # 固定データは実行時に名前で開くので import では辿れない。全部に効くと見る。
        self.assertEqual(groups_on_disk(), plan("test/fixtures/board.json")["groups"])

    def test_a_file_no_test_reads_is_type_checked_only(self):
        # 拡張ホスト側（VS Code の API を呼ぶ入口）はテストが読まない。型だけ見る。
        got = plan("src/extension.ts")
        self.assertEqual([], got["groups"])
        self.assertTrue(got["compile"])

    def test_documents_call_nothing(self):
        got = plan("README.md")
        self.assertEqual([], got["groups"])
        self.assertFalse(got["compile"])

    def test_paths_outside_the_extension_call_nothing(self):
        got = plan(os.path.join(ROOT, "ccnavi", "main.py"))
        self.assertEqual([], got["groups"])
        self.assertFalse(got["compile"])

    def test_paths_arrive_as_the_hook_writes_them(self):
        # hook が渡すのは絶対パス。ワークツリーで作業していると、その途中に
        # .claude/worktrees/<名前>/ が挟まる。どちらの綴りでも同じ結論になる。
        absolute = os.path.join(BOARD, "src", "core", "risk-doc.ts")
        worktree = "/tmp/ws/.claude/worktrees/w1/vscode-extension/ccnavi-board/src/core/risk-doc.ts"
        expected = plan("src/core/risk-doc.ts")
        self.assertEqual(expected, plan(absolute))
        self.assertEqual(expected, plan(worktree))

    def test_several_paths_are_joined(self):
        got = plan("src/core/risk-doc.ts", "test/rules/hooks.test.ts", "README.md")
        self.assertIn("risk", got["groups"])
        self.assertIn("rules", got["groups"])

    def test_every_group_on_disk_is_known_to_the_runner(self):
        # グループを増やしたのに runner が知らない、を防ぐ。
        self.assertEqual(groups_on_disk(), plan("package.json")["groups"])
        self.assertGreater(len(groups_on_disk()), 1)


@unittest.skipIf(SH is None, "sh が無い")
class MarkExtHookTest(unittest.TestCase):
    """PostToolUse の hook が、拡張のファイルだけを書き残すこと。"""

    def mark(self, workspace, file_path, session="s1"):
        payload = (
            f'{{"session_id":"{session}","hook_event_name":"PostToolUse",'
            f'"tool_input":{{"file_path":"{file_path}"}}}}'
        )
        result = subprocess.run(
            [SH, MARK],
            input=payload,
            capture_output=True,
            text=True,
            cwd=workspace,
            env={**os.environ, "CLAUDE_PROJECT_DIR": workspace},
        )
        self.assertEqual(0, result.returncode, result.stderr)
        marker = os.path.join(workspace, "logs", "session", f"{session}.ext-files")
        if not os.path.exists(marker):
            return []
        with open(marker, encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]

    def test_an_extension_file_is_written_down(self):
        with tempfile.TemporaryDirectory() as ws:
            path = "/repo/vscode-extension/ccnavi-board/src/core/board.ts"
            self.assertEqual([path], self.mark(ws, path))

    def test_a_worktree_path_is_written_down(self):
        with tempfile.TemporaryDirectory() as ws:
            path = "/repo/.claude/worktrees/w1/vscode-extension/ccnavi-board/test/board/b.test.ts"
            self.assertEqual([path], self.mark(ws, path))

    def test_a_windows_path_is_written_down_with_slashes(self):
        # JSON の中でバックスラッシュは 2 個に増えている。
        with tempfile.TemporaryDirectory() as ws:
            given = "C:\\\\repo\\\\vscode-extension\\\\ccnavi-board\\\\src\\\\core\\\\board.ts"
            self.assertEqual(
                ["C:/repo/vscode-extension/ccnavi-board/src/core/board.ts"],
                self.mark(ws, given),
            )

    def test_files_outside_the_extension_are_not_written_down(self):
        with tempfile.TemporaryDirectory() as ws:
            self.assertEqual([], self.mark(ws, "/repo/ccnavi/main.py"))
            self.assertEqual([], self.mark(ws, "/repo/docs/adr/0036-tests-at-stop.md"))

    def test_the_same_file_twice_is_harmless(self):
        # 同じターンで 2 回直しても、回すグループは変わらない（Stop 側で sort -u する）。
        with tempfile.TemporaryDirectory() as ws:
            path = "/repo/vscode-extension/ccnavi-board/src/core/board.ts"
            self.mark(ws, path)
            self.assertEqual([path, path], self.mark(ws, path))


if __name__ == "__main__":
    unittest.main()
