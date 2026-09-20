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

import contextlib
import os
import shutil
import subprocess
import tempfile
import time
import unittest

from tests import ROOT

BOARD = os.path.join(ROOT, "vscode-extension", "ccnavi-board")
RUNNER = os.path.join(BOARD, "scripts", "test-groups.js")
MARK = os.path.join(ROOT, ".claude", "hooks", "mark-ext.sh")
TEST_EXT = os.path.join(ROOT, ".claude", "hooks", "test-ext.sh")

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


# `--plan` が読むもの。写しを作って試すときはこれだけあればよい（node_modules も out も読まない）。
PLAN_INPUTS = ("scripts", "src", "test", "package.json", "tsconfig.test.json")


def plan(*paths, root=BOARD):
    """`--plan` の 1 行を読み、辞書にする。`root` を渡すとその写しの runner を回す。"""
    result = subprocess.run(
        [NODE, os.path.join(root, "scripts", "test-groups.js"), "--plan", "--for", *paths],
        capture_output=True,
        text=True,
        cwd=root,
        check=True,
    )
    fields = dict(part.split("=", 1) for part in result.stdout.split())
    return {
        "groups": sorted(filter(None, fields["groups"].split(","))),
        "compile": fields["compile"] == "yes",
        "webview": fields["webview"] == "yes",
    }


def write(path, text, mode="w"):
    with open(path, mode, encoding="utf-8") as handle:
        handle.write(text)


@contextlib.contextmanager
def copied_board():
    """`--plan` が読むものだけを写した拡張のルート。中で好きに壊してよい。"""
    with tempfile.TemporaryDirectory() as temp:
        root = os.path.join(temp, "board")
        os.makedirs(root)
        for name in PLAN_INPUTS:
            source = os.path.join(BOARD, name)
            target = os.path.join(root, name)
            if os.path.isdir(source):
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        yield root


@unittest.skipIf(NODE is None, "node が無い")
class ExtTestPlanTest(unittest.TestCase):
    def test_a_source_file_calls_the_groups_that_read_it(self):
        # リスク管理画面の文書。risk のテストが直接読み、shared も辿って読む。
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

    def test_one_screen_does_not_call_the_other_screens_group(self):
        # 画面が 2 つ以上あるとき、1 つ直しただけで全部の画面のテストを回さない。
        # 画面ごとの置き場を runner が知っている（BUNDLE_ENTRIES の screen）。
        self.assertNotIn("projects", plan("src/webview/board/App.tsx")["groups"])
        got = plan("src/webview/projects/App.tsx")
        self.assertIn("projects", got["groups"])
        self.assertNotIn("board", got["groups"])
        self.assertTrue(got["webview"])

    def test_an_import_across_screens_still_calls_the_other_screens_group(self):
        # 置き場の綴りだけで絞ると、画面をまたぐ import が 1 本入っただけで
        # 「直したのに回らない」側に外れる。閉包で見ているので外れない。
        with copied_board() as root:
            crossing = os.path.join(root, "src", "webview", "projects", "App.tsx")
            write(crossing, 'import "../board/state.js";\n', mode="a")
            got = plan("src/webview/board/state.ts", root=root)
        self.assertIn("board", got["groups"])
        self.assertIn("projects", got["groups"], "projects の束ねにも入るので、projects も回る")

    def test_a_new_screen_calls_its_own_group_even_without_a_test_helper(self):
        # 画面を足して `test/helpers/<名前>.ts` を作り忘れても、同じ名前のグループは回す。
        # ここが無いと、その画面のテストだけが黙って回らない（pnpm test でしか気づけない）。
        with copied_board() as root:
            screen = os.path.join(root, "src", "webview", "fakescreen")
            group = os.path.join(root, "test", "fakescreen")
            os.makedirs(screen)
            os.makedirs(group)
            write(os.path.join(screen, "main.tsx"), "export const x = 1;\n")
            write(
                os.path.join(group, "fake.test.ts"),
                'import { test } from "node:test";\ntest("f", () => {});\n',
            )
            got = plan("src/webview/fakescreen/main.tsx", root=root)
        self.assertIn("fakescreen", got["groups"])
        self.assertTrue(got["webview"])

    def test_every_screen_on_disk_has_a_group_and_a_test_helper(self):
        # 綴りの約束（画面 `src/webview/<名前>/main.tsx` ↔ グループ `test/<名前>/` と
        # 入口 `test/helpers/<名前>.ts`）が守られていること。破ると絞り込みが粗くなる。
        webview = os.path.join(BOARD, "src", "webview")
        names = sorted(
            name
            for name in os.listdir(webview)
            if os.path.isfile(os.path.join(webview, name, "main.tsx"))
        )
        self.assertTrue(names, "画面が 1 つも見つからない")
        for name in names:
            with self.subTest(screen=name):
                self.assertIn(name, groups_on_disk())
                helper = os.path.join(BOARD, "test", "helpers", f"{name}.ts")
                self.assertTrue(os.path.isfile(helper), helper)

    def test_a_part_shared_by_every_screen_calls_them_all(self):
        # どの画面にも属さない部品（acquireVsCodeApi の窓口など）は、全部の画面に効く。
        got = plan("src/webview/vscode.ts")
        for group in ("board", "projects"):
            self.assertIn(group, got["groups"])
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

    def test_a_path_is_normalized_before_the_spelling_is_read(self):
        # `./` が付いただけで別のファイルに見えると、画面を直したのに束ね直さない、
        # という取りこぼしになる（回すものが減る側に外れる）。
        for given in ("src/webview/board/App.tsx", "./src/webview/board/App.tsx"):
            with self.subTest(given=given):
                got = plan(given)
                self.assertIn("board", got["groups"])
                self.assertTrue(got["webview"])
        self.assertEqual(plan("package.json"), plan("./package.json"))

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


@unittest.skipIf(SH is None or NODE is None, "sh か node が無い")
class TestExtHookTest(unittest.TestCase):
    """Stop の hook が、回すべきときに回し、落ちたときだけ差し戻すこと。

    本物のテストは回さない。触ったファイルから組み立てた綴りで入口（`scripts/test-groups.js`）を
    呼べているか、終了コードをどう読むかを見るので、入口は控えを置いて渡された引数を書き出す。
    sh の代わりに写しを渡すのと同じ考え方で、拡張の node_modules にも依存しない。
    """

    STUB = (
        "require('node:fs').writeFileSync(process.env.STUB_LOG,"
        " process.argv.slice(2).join('\\n'));\n"
        "process.exit(Number(process.env.STUB_RC || 0));\n"
    )

    def workspace(self, tree="tree", touched="src/core/board.ts"):
        """一時のワークスペースと、その中の拡張の写しを作る。

        `tree` に空白や記号を入れて、綴りの扱いを見る。
        """
        base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, base, True)
        workspace = os.path.join(base, "ws")
        board = os.path.join(base, tree, "vscode-extension", "ccnavi-board")
        os.makedirs(os.path.join(board, "scripts"))
        os.makedirs(os.path.join(workspace, "logs", "session"))
        with open(os.path.join(board, "scripts", "test-groups.js"), "w", encoding="utf-8") as f:
            f.write(self.STUB)
        marker = os.path.join(workspace, "logs", "session", "s1.ext-files")
        with open(marker, "w", encoding="utf-8") as f:
            f.write(f"{os.path.join(board, *touched.split('/'))}\n")
        return workspace, board, marker

    def stop(self, workspace, rc=0, active=False, log=None):
        payload = (
            '{"session_id":"s1","hook_event_name":"Stop",'
            f'"stop_hook_active":{"true" if active else "false"}}}'
        )
        log = log or os.path.join(workspace, "stub.log")
        env = {**os.environ, "CLAUDE_PROJECT_DIR": workspace, "STUB_LOG": log, "STUB_RC": str(rc)}
        result = subprocess.run(
            [SH, TEST_EXT], input=payload, capture_output=True, text=True, cwd=workspace, env=env
        )
        args = None
        if os.path.exists(log):
            with open(log, encoding="utf-8") as f:
                args = f.read().split("\n")
        return result, args

    def retries(self, workspace):
        path = os.path.join(workspace, "logs", "session", "s1.ext-retries")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return f.read().strip()

    def test_the_entry_point_is_called_with_the_touched_file(self):
        workspace, board, marker = self.workspace()
        result, args = self.stop(workspace)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["--for", os.path.join(board, "src", "core", "board.ts")], args)
        # 通ったら印を消す。次のターンで同じものを回し直さない。
        self.assertFalse(os.path.exists(marker))

    def test_a_path_with_spaces_arrives_as_one_argument(self):
        # 空白で語に割れると、入口が見つからず「何も回さずに通った」になる。
        # Windows の利用者名に空白は珍しくない。
        workspace, board, _ = self.workspace(tree="my dir")
        result, args = self.stop(workspace)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["--for", os.path.join(board, "src", "core", "board.ts")], args)

    def test_a_path_with_a_sed_delimiter_arrives_as_one_argument(self):
        # 絞り込みを sed の正規表現で書くと、`#` や `[` で構文エラーになって同じ穴が開く。
        workspace, board, _ = self.workspace(tree="a#b[c]")
        result, args = self.stop(workspace)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["--for", os.path.join(board, "src", "core", "board.ts")], args)

    def test_a_failing_run_is_pushed_back_and_counted(self):
        workspace, _, marker = self.workspace()
        result, _ = self.stop(workspace, rc=1)
        self.assertEqual(2, result.returncode)
        self.assertIn("落ちたテストを直して", result.stderr)
        self.assertEqual("1", self.retries(workspace))
        # 印は残す。直したあと同じグループを回し直すため。
        self.assertTrue(os.path.exists(marker))

    def test_a_missing_environment_is_not_counted_as_a_failing_test(self):
        # 終了コード 3 は「node_modules が無い」など環境が足りない側。テストは
        # 落ちていないので、「落ちたテストを直して」と差し戻すのは嘘になる。
        workspace, _, _ = self.workspace()
        result, _ = self.stop(workspace, rc=3)
        self.assertEqual(0, result.returncode)
        self.assertIsNone(self.retries(workspace))

    def test_pushing_back_stops_at_three(self):
        # 同じセッションで続けて落ちる形。印は差し戻しても残るので、同じ workspace で回す。
        workspace, _, _ = self.workspace()
        for expected in ("1", "2", "3"):
            result, _ = self.stop(workspace, rc=1, active=True)
            self.assertEqual(2, result.returncode)
            self.assertEqual(expected, self.retries(workspace))
        result, _ = self.stop(workspace, rc=1, active=True)
        self.assertEqual(0, result.returncode)
        self.assertIn("これ以上は止めません", result.stderr)
        self.assertIsNone(self.retries(workspace))

    def test_a_turn_that_touched_nothing_runs_nothing(self):
        workspace, _, marker = self.workspace()
        os.remove(marker)
        result, args = self.stop(workspace)
        self.assertEqual(0, result.returncode)
        self.assertIsNone(args)
        self.assertEqual("", result.stderr)

    def test_an_old_marker_is_still_read_in_the_same_session(self):
        # 1 時間の掃除が自分の印を消してしまうと、拡張を直したのに「触っていない
        # ターン」に見えて黙って何も回らない。1 つのターンは 1 時間を超えることがある。
        workspace, _, marker = self.workspace()
        old = time.time() - 2 * 60 * 60
        os.utime(marker, (old, old))
        result, args = self.stop(workspace)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIsNotNone(args)

    def test_a_marker_that_points_nowhere_is_reported_not_passed(self):
        # 入口が無いツリーの印だけが残った形。黙って exit 0 すると「テストが通った」と
        # 区別が付かない。
        workspace, board, _ = self.workspace()
        os.remove(os.path.join(board, "scripts", "test-groups.js"))
        result, args = self.stop(workspace)
        self.assertEqual(0, result.returncode)
        self.assertIsNone(args)
        self.assertIn("テストの入口がない", result.stderr)


if __name__ == "__main__":
    unittest.main()
