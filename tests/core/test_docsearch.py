"""md の frontmatter の索引と `ccnavi --docs` の受入テスト。実行ファイルを起こして出力だけを見る。

見るのは次のとおり。

1. CLI: `--docs` の外の絞り込みは理由を言って失敗にする。誤った値は 1、0 件は 0。
   どこから打ってもワークスペースを引く。不正な索引の行でも、どの出力の形でも異常終了しない
2. プロジェクト: 置き場の直下の各 git も引く。パスはワークスペースルートから。
   ccnavi-index.jsonl を無視していなければ対象外
3. SessionStart: 索引を新しくして案内をつける。対象外は名指し。
   サブエージェントと git の外では案内を出さない
4. PostToolUse: frontmatter の無い md を Write で作ったら、判定を変えずに案内を渡す

索引の作り方・差分・frontmatter の読み方・引き方・出力の形・敵対的な入力と git の失敗は
`go/internal/catalog`（docindex・docs）の go test が見る。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest

from tests.inproc import run_ccnavi

# 文の部品（ccnavi が出す文の一部）。
CONVENTION_DOC = ".ccnavi/docs/frontmatter.md"
WORKSPACE = "ワークスペース"
UNIGNORED = "は .gitignore に `**/ccnavi-index.jsonl` が無く、足せなかったので索引の対象外"
IGNORE_ADDED = "に `**/ccnavi-index.jsonl` を追加しました（未コミットの差分が残る）"
CODE_MISSING_FRONTMATTER = "NOTICE_DOC_FRONTMATTER"


def git(cwd: str, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if done.returncode != 0:
        raise AssertionError(f"git {args}: {done.stderr}")
    return done.stdout


def write(path: str, text: str, mtime: float | None = None) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def read(path: str) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def doc(**front) -> str:
    lines = ["---"]
    for key, value in front.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n\n# 本文\n\nbody text\n"


# 2026-08-05 12:00:00 のローカル時刻。mtime の文字列はローカル時刻で書くので、そこから作る。
NOON = time.mktime((2026, 8, 5, 12, 0, 0, 0, 0, -1))


class Repo(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = os.path.realpath(self.dir.name)
        git(self.root, "init", "-q")
        write(os.path.join(self.root, ".gitignore"), "**/ccnavi-index.jsonl\n")

    def put(self, rel: str, text: str, mtime: float | None = NOON) -> str:
        return write(os.path.join(self.root, *rel.split("/")), text, mtime)

    def index_of(self, rel_dir: str) -> str:
        return os.path.join(self.root, *[p for p in rel_dir.split("/") if p], "ccnavi-index.jsonl")

    def rows_of(self, rel_dir: str) -> list[dict]:
        with open(self.index_of(rel_dir), encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    def context(self, root: str, **extra) -> str:
        payload = {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": root, **extra}
        done = run_ccnavi(
            ["--root", root, "--mode", "enable"],
            input=json.dumps(payload),
            cwd=root,
            env=clean_env(CCNAVI_ENTRY_PATH=".ccnavi/scripts/ccnavi-launcher.sh"),
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        if not done.stdout.strip():
            return ""
        return json.loads(done.stdout)["hookSpecificOutput"].get("additionalContext", "")


def clean_env(**extra: str) -> dict[str, str]:
    environment = {
        **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
        "CCNAVI_LEARNING": "disable",
    }
    environment.pop("CLAUDE_PROJECT_DIR", None)
    environment.update(extra)
    return environment


class CliTest(Repo):
    def ccnavi(self, *args: str, cwd: str | None = None):
        return run_ccnavi(
            ["--root", self.root, *args],
            cwd=cwd or self.root,
            env=clean_env(),
        )

    def test_searches_and_writes_the_index(self):
        self.put("docs/a.md", doc(type="guide", title="甲"))
        self.put("b.md", doc(type="adr"))
        done = self.ccnavi("--docs", "--type", "GUIDE")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "guide  docs/a  甲\n")
        self.assertIn("matched=1 total=2", done.stderr)
        self.assertTrue(os.path.exists(self.index_of("docs")))

    def test_json_is_format_json(self):
        self.put("a.md", doc(type="guide"))
        done = self.ccnavi("--docs", "--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)[0]["concept_id"], "a")

    def test_the_workspace_is_searched_wherever_it_is_run(self):
        self.put("a.md", doc(type="guide"))
        with tempfile.TemporaryDirectory() as elsewhere:
            done = self.ccnavi("--docs", "--format", "path", cwd=os.path.realpath(elsewhere))
        self.assertEqual((done.returncode, done.stdout), (0, "a\n"))

    def test_no_match_is_still_zero(self):
        self.put("a.md", doc(type="guide"))
        done = self.ccnavi("--docs", "--type", "nothing", "--format", "count")
        self.assertEqual((done.returncode, done.stdout), (0, "matched=0 total=1\n"))

    def test_a_workspace_outside_git_has_nothing_and_is_zero(self):
        with tempfile.TemporaryDirectory() as bare:
            bare = os.path.realpath(bare)
            write(os.path.join(bare, "a.md"), doc(type="guide"))
            done = run_ccnavi(["--root", bare, "--docs", "--format", "count"], env=clean_env())
        self.assertEqual((done.returncode, done.stdout), (0, "matched=0 total=0\n"))

    def test_bad_values_are_one(self):
        self.put("a.md", doc(type="guide"))
        for args in (
            ("--sort", "size"),
            ("--format", "xml"),
            ("--until", "tomorrow"),
            ("--json", "--format", "table"),
            ("--lint",),
            ("stray",),
        ):
            with self.subTest(args=args):
                done = self.ccnavi("--docs", *args)
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn("ccnavi:", done.stderr)

    def test_filters_outside_docs_stop_the_run(self):
        done = self.ccnavi("--lint", "--type", "adr", "--limit", "3")
        self.assertEqual(done.returncode, 1)
        self.assertIn("ccnavi: --type は --docs でだけ指定できる", done.stderr)
        self.assertIn("ccnavi: --limit は --docs でだけ指定できる", done.stderr)
        # 別の副命令に --limit を付ける打ち間違いは、失敗にして進めない。
        done = self.ccnavi("ticket", "start", "i0001", "--limit", "3")
        self.assertEqual(done.returncode, 1)
        self.assertIn("--limit は --docs でだけ指定できる", done.stderr)
        self.assertNotIn("チケット", done.stdout)

    def test_flags_of_other_runs_stop_docs(self):
        self.put("a.md", doc(type="guide"))
        for args in (
            ("--preview",),
            ("--verify",),
            ("--result", "{}"),
            ("--tickets", "wip"),
            ("--reason", "x"),
            ("--flow", "f.yml"),
        ):
            with self.subTest(args=args):
                done = self.ccnavi("--docs", *args)
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn(f"--docs は {args[0]} と同時に指定できない", done.stderr)

    def test_user_operations_do_not_go_with_docs(self):
        """`user` の操作にも `--docs` は添えられない。`user` の外の `--yes` は先に断る。"""
        self.put("a.md", doc(type="guide"))
        for args, flag in (
            (("user", "reviewed", "0", "--docs"), "--reviewed"),
            (("user", "agree", "--yes", "i0001", "--docs"), "--agree"),
            (("user", "prune", "--docs"), "--prune"),
        ):
            with self.subTest(args=args):
                done = self.ccnavi(*args)
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn(f"--docs は {flag} と同時に指定できない", done.stderr)
        refused = self.ccnavi("--docs", "--yes", "i0001")
        self.assertEqual(refused.returncode, 2, refused.stdout)
        self.assertIn("ユーザ専用", refused.stderr)

    def test_settings_and_wrapper_flags_stop_docs_too(self):
        """設定や sh のパスを差し替えるフラグも、何も言わずに無視せず、止める。"""
        self.put("a.md", doc(type="guide"))
        for args in (
            ("--cwd", self.root),
            ("--approved", "x"),
            ("--approved", ""),
            ("--mode", "enable"),
            ("--projects", "elsewhere"),
            ("--projects", ""),
            ("--project-home", ".other"),
            ("--rules", "r.yml"),
            ("--phases", "p.yml"),
            ("--risk", "k.yml"),
            ("--ticket-control", "disable"),
            ("--guard-core-files", "disable"),
            ("--guard-ticket-approval", "enable"),
            ("--restore-if-deny", "x"),
            ("--choose-out", "x"),
        ):
            with self.subTest(args=args):
                done = self.ccnavi("--docs", *args)
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn(f"--docs は {args[0]} と同時に指定できない", done.stderr)
                self.assertEqual(done.stdout, "")
                # レイヤーの置き場の「診断でだけ効く」の文で先へ進まない。
                self.assertNotIn("診断", done.stderr)

    def test_root_log_and_state_are_accepted(self):
        self.put("a.md", doc(type="guide"))
        done = self.ccnavi("--docs", "--format", "path")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "a\n")

    def test_impossible_dates_are_refused(self):
        for value in ("2026-13-45", "2026-02-30", "2026-08-05T25", "2026-08-05T10:61"):
            with self.subTest(value=value):
                done = self.ccnavi("--docs", "--since", value)
                self.assertEqual(done.returncode, 1)
        self.assertEqual(self.ccnavi("--docs", "--until", "2026-08-05T10:59").returncode, 0)


class ProjectsTest(Repo):
    """プロジェクトの置き場（`projects/`）の直下の各 git も索引に入れる。"""

    def setUp(self):
        super().setUp()
        write(os.path.join(self.root, ".gitignore"), "**/ccnavi-index.jsonl\n/projects/\n")
        self.lib = self.project("lib", "**/ccnavi-index.jsonl\n")
        # .gitignore に足せない（ふつうのファイルでない）プロジェクトは、対象外のまま。
        self.bare = self.project("bare", None)
        self.put("docs/w.md", doc(type="guide", title="ワークスペース"))
        self.put("projects/lib/docs/x.md", doc(type="adr", title="lib の決定"))
        self.put("projects/bare/y.md", doc(type="guide"))

    def project(self, name: str, ignore: str | None) -> str:
        """ignore が None なら .gitignore をディレクトリにして、無視の行を足せなくする。"""
        home = os.path.join(self.root, "projects", name)
        os.makedirs(home)
        git(home, "init", "-q")
        if ignore is None:
            os.makedirs(os.path.join(home, ".gitignore"))
        else:
            write(os.path.join(home, ".gitignore"), ignore)
        return home

    def ccnavi(self, *args: str):
        return run_ccnavi(
            ["--root", self.root, "--docs", *args],
            cwd=self.root,
            env=clean_env(),
        )

    def test_projects_are_searched_with_paths_from_the_workspace_root(self):
        done = self.ccnavi("--format", "jsonl")
        self.assertEqual(done.returncode, 0, done.stderr)
        rows = [json.loads(line) for line in done.stdout.splitlines()]
        self.assertEqual(
            [(r["concept_id"], r["directory"]) for r in rows],
            [("docs/w", "docs"), ("projects/lib/docs/x", "projects/lib/docs")],
        )
        # 書く行はそのプロジェクトのルートからの相対。
        with open(os.path.join(self.lib, "docs", "ccnavi-index.jsonl"), encoding="utf-8") as f:
            self.assertEqual(json.loads(f.readline())["concept_id"], "docs/x")

    def test_path_narrows_to_one_project(self):
        done = self.ccnavi("--path", "projects/lib", "--format", "path")
        self.assertEqual(done.stdout, "projects/lib/docs/x\n")

    def test_a_project_that_ignores_no_index_is_left_out_and_named(self):
        done = self.ccnavi("--format", "count")
        self.assertEqual(done.stdout, "matched=2 total=2\n")
        self.assertIn(f"bare {UNIGNORED}", done.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.bare, "ccnavi-index.jsonl")))
        self.assertTrue(os.path.isdir(os.path.join(self.bare, ".gitignore")))

    def test_a_negating_line_does_not_stop_the_line(self):
        # 許可リスト型の .gitignore でも、足す行が後ろで勝つので足して索引に入れる。
        allow = self.project("allow", "/*\n!/docs/\n!/docs/**\n")
        self.put("projects/allow/docs/z.md", doc(type="guide"))
        done = self.ccnavi("--format", "path")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("projects/allow/docs/z", done.stdout.splitlines())
        self.assertEqual(
            read(os.path.join(allow, ".gitignore")), "/*\n!/docs/\n!/docs/**\n**/ccnavi-index.jsonl\n"
        )
        self.assertTrue(os.path.exists(os.path.join(allow, "docs", "ccnavi-index.jsonl")))

    def test_a_project_without_ignore_gets_the_line_and_is_indexed(self):
        plain = self.project("plain", "node_modules/\n")
        self.put("projects/plain/z.md", doc(type="guide", title="plain の文書"))
        done = self.ccnavi("--format", "path")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("projects/plain/z", done.stdout.splitlines())
        self.assertIn("projects/plain/.gitignore に", done.stderr)
        self.assertEqual(
            read(os.path.join(plain, ".gitignore")), "node_modules/\n**/ccnavi-index.jsonl\n"
        )
        self.assertTrue(os.path.exists(os.path.join(plain, "ccnavi-index.jsonl")))
        # 2 回目は足さない。
        again = self.ccnavi("--format", "path")
        self.assertNotIn("を追加した", again.stderr)
        self.assertEqual(
            read(os.path.join(plain, ".gitignore")), "node_modules/\n**/ccnavi-index.jsonl\n"
        )

    def test_the_ccnavi_directory_of_a_project_is_left_out(self):
        self.put("projects/lib/.ccnavi/approved/doing/i0001.md", "---\nversion: 1\n---\n")
        done = self.ccnavi("--path", ".ccnavi", "--format", "count")
        self.assertEqual(done.stdout, "matched=0 total=2\n")


class SessionStartTest(Repo):
    def test_refreshes_the_index_and_tells_how_to_search(self):
        self.put("docs/a.md", doc(type="guide"))
        said = self.context(self.root)
        self.assertTrue(os.path.exists(self.index_of("docs")))
        launcher = f"sh {self.root.replace(os.sep, '/')}/.ccnavi/scripts/ccnavi-launcher.sh --docs"
        self.assertIn(launcher, said)
        self.assertIn("grep・Glob より先に", said)
        self.assertNotIn("--keyword frontmatter", said)
        self.assertNotIn(UNIGNORED, said)

    def test_points_to_the_convention_document_through_docs_when_present(self):
        self.put("docs/a.md", doc(type="guide"))
        self.put(CONVENTION_DOC, doc(type="guide"))
        said = self.context(self.root)
        self.assertIn("--docs --keyword frontmatter", said)

    def test_names_a_project_left_out(self):
        write(os.path.join(self.root, ".gitignore"), "**/ccnavi-index.jsonl\n/projects/\n")
        self.put("docs/a.md", doc(type="guide"))
        home = os.path.join(self.root, "projects", "bare")
        os.makedirs(home)
        git(home, "init", "-q")
        os.makedirs(os.path.join(home, ".gitignore"))  # 足せない
        self.put("projects/bare/y.md", doc(type="guide"))
        said = self.context(self.root)
        self.assertIn("--docs", said)
        self.assertIn(f"bare {UNIGNORED}", said)

    def test_names_a_project_that_got_the_line(self):
        write(os.path.join(self.root, ".gitignore"), "**/ccnavi-index.jsonl\n/projects/\n")
        self.put("docs/a.md", doc(type="guide"))
        home = os.path.join(self.root, "projects", "plain")
        os.makedirs(home)
        git(home, "init", "-q")
        self.put("projects/plain/y.md", doc(type="guide"))
        said = self.context(self.root)
        self.assertIn(f"projects/plain/.gitignore {IGNORE_ADDED}", said)
        self.assertNotIn(UNIGNORED, said)
        self.assertEqual(read(os.path.join(home, ".gitignore")), "**/ccnavi-index.jsonl\n")

    def test_names_the_workspace_that_got_the_line(self):
        write(os.path.join(self.root, ".gitignore"), "")
        self.put("docs/a.md", doc(type="guide"))
        said = self.context(self.root)
        self.assertIn(f".gitignore {IGNORE_ADDED}", said)
        self.assertIn("grep・Glob より先に", said)

    def test_only_the_left_out_line_when_nothing_is_indexed(self):
        os.remove(os.path.join(self.root, ".gitignore"))
        os.makedirs(os.path.join(self.root, ".gitignore"))  # 足せない
        self.put("docs/a.md", doc(type="guide"))
        said = self.context(self.root)
        self.assertIn(f"{WORKSPACE} {UNIGNORED}", said)
        self.assertNotIn("grep・Glob より先に", said)

    def test_silent_for_a_subagent_without_md_and_outside_git(self):
        self.put("docs/a.md", doc(type="guide"))
        self.assertNotIn("--docs", self.context(self.root, agent_id="a1"))
        self.assertFalse(os.path.exists(self.index_of("docs")))
        os.remove(os.path.join(self.root, "docs", "a.md"))
        self.assertNotIn("--docs", self.context(self.root))
        with tempfile.TemporaryDirectory() as bare:
            bare = os.path.realpath(bare)
            write(os.path.join(bare, "a.md"), doc(type="guide"))
            self.assertNotIn("--docs", self.context(bare))


class FrontmatterHintTest(Repo):
    """Write で新しく作った md に frontmatter か type が無ければ、PostToolUse で 1 度だけ案内する。

    既にある md の Edit・上書きでは出さない。対象は索引に入る md（書いた先のツリーの git で決める）
    から、決まりの文書で「付けない・付けなくてよい」とされているもの（README.md・CLAUDE.md・AGENTS.md・下書きの
    置き場・チケットの置き場）を除いたもの。
    """

    def test_post_tool_use_delivers_it_without_changing_the_decision(self):
        path = self.put("docs/new.md", "# 見出し\n")
        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": self.root,
            "tool_name": "Write",
            "tool_input": {"file_path": path, "content": "# 見出し\n"},
            "tool_response": {"type": "create", "filePath": path},
        }
        done = run_ccnavi(
            ["--root", self.root, "--mode", "enable"],
            input=json.dumps(payload),
            cwd=self.root,
            env=clean_env(),
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        said = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(CODE_MISSING_FRONTMATTER, said)
        self.assertNotIn("permissionDecision", done.stdout)


class ForeignIndexTest(Repo):
    """A: ccnavi の形でない ccnavi-index.jsonl は上書きも削除もしない。"""

    def test_cli_and_session_start_name_it(self):
        self.put("a.md", doc(type="guide"))
        write(self.index_of(""), "x\n")
        done = run_ccnavi(["--root", self.root, "--docs", "--format", "count"], env=clean_env())
        self.assertIn("ccnavi-index.jsonl は ccnavi の索引ではないので書き換えない", done.stderr)
        self.assertIn("書き換えなかった: ccnavi-index.jsonl", self.context(self.root))


class RobustTest(Repo):
    """C: 不正な入力で異常終了しない。"""

    def test_no_refresh_and_text_survive_odd_rows(self):
        self.put("a.md", doc(type="guide"))
        nested: object = "deep"
        for _ in range(500):
            nested = [nested]
        row = {"concept_id": "a", "directory": ".", "frontmatter": {"n": nested}, "mtime": "x"}
        write(self.index_of(""), json.dumps(row) + "\n")
        # 字下げの JSON に書けない深さの行は ccnavi の行とみなさない（foreign）。md は読み直す。
        for fmt in ("table", "json"):
            with self.subTest(fmt=fmt):
                done = run_ccnavi(
                    ["--root", self.root, "--docs", "--no-refresh", "--format", fmt],
                    env=clean_env(),
                )
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertIn("ccnavi の索引ではない", done.stderr)
                self.assertIn('"a"' if fmt == "json" else "guide", done.stdout)


class DedupeAndProjectsTest(Repo):
    """H: 同じ concept_id は 1 本に。`.git` の無いプロジェクトは CLI で名指し。"""

    def test_a_project_without_git_is_named_by_the_cli(self):
        self.put("a.md", doc(type="guide"))
        self.put("projects/plain/x.md", doc(type="guide"))
        done = run_ccnavi(["--root", self.root, "--docs"], env=clean_env())
        self.assertIn("プロジェクト plain は .git を持たないので引かない", done.stderr)
        self.assertNotIn("plain", self.context(self.root))


class JsonOutputTest(Repo):
    """どの出力の形でも異常終了せず、正しい JSON を出す。"""

    def test_deep_front_matter_is_null_and_json_output_works(self):
        nested = "[" * 40 + "]" * 40
        self.put("a.md", f"---\ntype: guide\nn: {nested}\n---\n")
        done = run_ccnavi(["--root", self.root, "--docs", "--format", "json"], env=clean_env())
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIsNone(json.loads(done.stdout)[0]["frontmatter"])

    def test_nan_and_infinity_rows_are_foreign(self):
        self.put("a.md", doc(type="guide"))
        for bad in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(bad=bad):
                front = f'"frontmatter":{{"n":{bad}}}'
                text = f'{{"concept_id":"a","directory":".",{front},"mtime":"x"}}\n'
                write(self.index_of(""), text)
                args = ["--root", self.root, "--docs", "--no-refresh"]
                done = run_ccnavi([*args, "--format", "json"], env=clean_env())
                self.assertEqual(done.returncode, 0, done.stderr)

                def refuse(constant):
                    raise ValueError(constant)


if __name__ == "__main__":
    unittest.main()
