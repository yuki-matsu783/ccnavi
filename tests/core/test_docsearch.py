"""md の frontmatter の索引と `ccnavi --docs`（docsearch）。

見るのは次のとおり。

1. 索引: git が挙げる md をディレクトリごとの `index.jsonl` に書く。無視されていなければ書かない。
   実体の無い md と ccnavi ディレクトリの下は載せない。md が全部消えたディレクトリの索引は消す
2. 差分: `concept_id` と `mtime` が同じ行は読み直さない。中身が同じなら書かない
3. frontmatter: 壊れたもの・別名・並びでないものは null。スカラーの tags は 1 要素。日付は文字列
4. 引く: 完全一致・部分一致・日時、同じものは OR・違うものは AND、並べ方、出力の形、桁揃え
5. CLI: `--docs` の外の絞り込みは言って落とす。誤った値は 1、0 件は 0
   どこから打ってもワークスペースを引く
6. プロジェクト: 置き場の直下の各 git も引く。パスはワークスペースルートから。
   index.jsonl を無視していなければ対象外
7. SessionStart: 索引を新しくして案内を添える。対象外は名指し。サブエージェントと git の外では黙る
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import time
import unittest

from ccnavi import docsearch, settings
from tests.inproc import run_ccnavi


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
        write(os.path.join(self.root, ".gitignore"), "**/index.jsonl\n")

    def put(self, rel: str, text: str, mtime: float | None = NOON) -> str:
        return write(os.path.join(self.root, *rel.split("/")), text, mtime)

    def index_of(self, rel_dir: str) -> str:
        return os.path.join(self.root, *[p for p in rel_dir.split("/") if p], "index.jsonl")

    def rows_of(self, rel_dir: str) -> list[dict]:
        with open(self.index_of(rel_dir), encoding="utf-8") as f:
            return [json.loads(line) for line in f]


# --- 1. 索引 ---------------------------------------------------------------------------


class IndexTest(Repo):
    def test_writes_one_index_per_directory_with_the_row_shape(self):
        self.put("README.md", doc(type="guide", title="はじめに"))
        self.put("docs/adr/0001-a.md", doc(type="adr", tags="[git, worktree]"))
        built = docsearch.build(self.root)
        self.assertEqual(sorted(built.written), ["docs/adr/index.jsonl", "index.jsonl"])
        (row,) = self.rows_of("docs/adr")
        self.assertEqual(
            row,
            {
                "concept_id": "docs/adr/0001-a",
                "directory": "docs/adr",
                "frontmatter": {"type": "adr", "tags": ["git", "worktree"]},
                "mtime": "2026-08-05T12:00:00",
            },
        )
        self.assertEqual(self.rows_of("")[0]["directory"], "")
        with open(self.index_of("docs/adr"), encoding="utf-8") as f:
            self.assertEqual(f.read().count("\n"), 1)
            f.seek(0)
            self.assertNotIn(", ", f.read())  # 1 行は詰めた JSON

    def test_a_tree_that_ignores_no_index_is_left_out(self):
        write(os.path.join(self.root, ".gitignore"), "")
        self.put("a.md", doc(type="guide"))
        built = docsearch.build(self.root)
        self.assertTrue(built.unignored)
        self.assertEqual((built.rows, built.written), ([], []))
        self.assertFalse(os.path.exists(self.index_of("")))

    def test_a_tracked_index_is_not_rewritten_but_its_rows_are_built(self):
        self.put("a.md", doc(type="guide"))
        self.put("docs/b.md", doc(type="adr"))
        write(self.index_of(""), "tracked\n")
        git(self.root, "add", "-f", "index.jsonl")
        built = docsearch.build(self.root)
        self.assertFalse(built.unignored)
        self.assertEqual([r["concept_id"] for r in built.rows], ["a", "docs/b"])
        self.assertEqual(built.written, ["docs/index.jsonl"])
        with open(self.index_of(""), encoding="utf-8") as f:
            self.assertEqual(f.read(), "tracked\n")

    def test_lists_untracked_but_not_ignored_files_and_skips_ignored_ones(self):
        write(os.path.join(self.root, ".gitignore"), "**/index.jsonl\nbuild/\n")
        self.put("new.md", doc(type="guide"))
        self.put("build/out.md", doc(type="guide"))
        self.put("notes.txt", "not md")
        built = docsearch.build(self.root)
        self.assertEqual([r["concept_id"] for r in built.rows], ["new"])

    def test_skips_deleted_files_and_removes_the_index_of_an_emptied_directory(self):
        self.put("keep/a.md", doc(type="guide"))
        self.put("gone/b.md", doc(type="guide"))
        git(self.root, "add", "-A")
        docsearch.build(self.root)
        self.assertTrue(os.path.exists(self.index_of("gone")))
        os.remove(os.path.join(self.root, "gone", "b.md"))
        built = docsearch.build(self.root)
        self.assertEqual([r["concept_id"] for r in built.rows], ["keep/a"])
        self.assertFalse(os.path.exists(self.index_of("gone")))
        self.assertEqual(built.removed, ["gone/index.jsonl"])

    def test_leaves_out_the_ccnavi_directory(self):
        self.put(".ccnavi/approved/done/i0001.md", "---\nversion: 1\nticket: i0001\n---\n")
        self.put("a.md", doc(type="guide"))
        built = docsearch.build(self.root, (".ccnavi",))
        self.assertEqual([r["concept_id"] for r in built.rows], ["a"])
        self.assertFalse(os.path.exists(self.index_of(".ccnavi/approved/done")))

    def test_not_a_repository_raises(self):
        with tempfile.TemporaryDirectory() as bare, self.assertRaises(docsearch.NotARepository):
            docsearch.build(bare)


# --- 2. 差分 ---------------------------------------------------------------------------


class IncrementalTest(Repo):
    def test_reuses_rows_whose_mtime_did_not_move_and_does_not_rewrite(self):
        self.put("a.md", doc(type="guide"))
        self.put("b.md", doc(type="adr"))
        first = docsearch.build(self.root)
        self.assertEqual((first.parsed, first.reused), (2, 0))
        second = docsearch.build(self.root)
        self.assertEqual((second.parsed, second.reused), (0, 2))
        self.assertEqual(second.written, [])

    def test_a_row_is_reused_even_if_the_file_changed_within_the_same_mtime(self):
        # 使い回すかは concept_id と mtime だけで決める（読み直さない証拠）。
        self.put("a.md", doc(type="guide"))
        docsearch.build(self.root)
        self.put("a.md", doc(type="changed"))  # 同じ mtime に戻す
        self.assertEqual(docsearch.build(self.root).rows[0]["frontmatter"]["type"], "guide")

    def test_a_moved_mtime_is_read_again(self):
        self.put("a.md", doc(type="guide"))
        docsearch.build(self.root)
        self.put("a.md", doc(type="changed"), mtime=NOON + 60)
        built = docsearch.build(self.root)
        self.assertEqual(built.parsed, 1)
        self.assertEqual(self.rows_of("")[0]["frontmatter"]["type"], "changed")
        self.assertEqual(built.written, ["index.jsonl"])

    def test_no_refresh_uses_existing_rows_and_writes_nothing(self):
        self.put("a.md", doc(type="guide"))
        docsearch.build(self.root)
        self.put("a.md", doc(type="changed"), mtime=NOON + 60)
        self.put("b.md", doc(type="adr"))
        built = docsearch.build(self.root, refresh=False)
        types = {r["concept_id"]: r["frontmatter"]["type"] for r in built.rows}
        self.assertEqual(types, {"a": "guide", "b": "adr"})
        self.assertEqual(built.written, [])

    def test_a_broken_index_line_is_ignored(self):
        self.put("a.md", doc(type="guide"))
        write(self.index_of(""), "{not json\n")
        built = docsearch.build(self.root)
        self.assertEqual(built.parsed, 1)
        self.assertEqual(self.rows_of("")[0]["concept_id"], "a")


# --- 3. frontmatter --------------------------------------------------------------------


class FrontMatterTest(unittest.TestCase):
    def test_none_when_absent_unclosed_broken_aliased_or_not_a_mapping(self):
        for raw in (
            b"# no front\n",
            b"---\ntype: guide\n",
            b"---\ntype: [unclosed\n---\n",
            b"---\na: &x 1\nb: *x\n---\n",
            b"---\n- a\n- b\n---\n",
            b"---\n!!python/object:os.system x\n---\n",
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(docsearch.front_matter(raw))

    def test_values_that_json_lacks_become_strings(self):
        front = docsearch.front_matter(b"---\ndate: 2026-08-05\nn: .nan\nok: true\n---\n")
        self.assertEqual(front, {"date": "2026-08-05", "n": "nan", "ok": True})

    def test_bom_and_dots_end(self):
        self.assertEqual(docsearch.front_matter("﻿---\ntype: a\n...\n".encode()), {"type": "a"})


# --- 4. 引く ---------------------------------------------------------------------------


def row(cid: str, mtime: str = "2026-08-05T12:00:00", **front) -> dict:
    return {
        "concept_id": cid,
        "directory": cid.rpartition("/")[0],
        "frontmatter": front or None,
        "mtime": mtime,
    }


ROWS = [
    row(
        "docs/adr/0001-git",
        "2026-08-01T09:00:00",
        type="adr",
        title="B 題",
        tags=["git", "Worktree"],
    ),
    row("docs/adr/0002-ticket", "2026-08-05T23:30:00", type="ADR", title="A 題", keywords=["承認"]),
    row("docs/claude/worktree", "2026-08-06T00:00:00", type="guide", tags="worktree"),
    row("README", "2026-07-01T00:00:00", type="guide", description="使い方 description"),
    row("notes/plain"),
]


def ids(query: docsearch.Query) -> list[str]:
    hits, _ = docsearch.search(ROWS, query)
    return [h["concept_id"] for h in hits]


class SearchTest(unittest.TestCase):
    def test_type_tag_keyword_are_whole_values_ignoring_case(self):
        self.assertEqual(
            ids(docsearch.Query(types=["adr"])), ["docs/adr/0001-git", "docs/adr/0002-ticket"]
        )
        self.assertEqual(ids(docsearch.Query(types=["ad"])), [])
        # スカラーの tags も 1 要素の並びとして当たる。
        self.assertEqual(
            ids(docsearch.Query(tags=["WORKTREE"])), ["docs/adr/0001-git", "docs/claude/worktree"]
        )
        self.assertEqual(ids(docsearch.Query(keywords=["承認"])), ["docs/adr/0002-ticket"])

    def test_same_option_is_or_and_different_options_are_and(self):
        self.assertEqual(
            ids(docsearch.Query(types=["guide", "adr"])),
            ["README", "docs/adr/0001-git", "docs/adr/0002-ticket", "docs/claude/worktree"],
        )
        self.assertEqual(
            ids(docsearch.Query(types=["guide"], tags=["worktree"])), ["docs/claude/worktree"]
        )

    def test_path_is_part_of_the_concept_id(self):
        self.assertEqual(ids(docsearch.Query(paths=["CLAUDE/"])), ["docs/claude/worktree"])

    def test_text_looks_at_values_not_keys(self):
        # キー名（title・tags・description）には当たらない。当たると全件になる。
        self.assertEqual(ids(docsearch.Query(texts=["title"])), [])
        self.assertEqual(ids(docsearch.Query(texts=["tags"])), [])
        self.assertEqual(ids(docsearch.Query(texts=["description"])), ["README"])
        self.assertEqual(ids(docsearch.Query(texts=["使い方"])), ["README"])
        self.assertEqual(ids(docsearch.Query(texts=["2026-07"])), ["README"])
        self.assertEqual(ids(docsearch.Query(texts=["plain"])), ["notes/plain"])

    def test_since_and_date_only_until(self):
        self.assertEqual(
            ids(docsearch.Query(since="2026-08-05", until="2026-08-05")),
            ["docs/adr/0002-ticket", "notes/plain"],
        )
        self.assertEqual(
            ids(docsearch.Query(until="2026-08-05T12:00")),
            ["README", "docs/adr/0001-git", "notes/plain"],
        )

    def test_sort_reverse_and_limit(self):
        self.assertEqual(
            ids(docsearch.Query(sort="mtime", reverse=True, limit=2)),
            ["docs/claude/worktree", "docs/adr/0002-ticket"],
        )
        # 同じ値は concept_id で並ぶ。frontmatter が無いものは空として先頭に来る。
        self.assertEqual(
            ids(docsearch.Query(sort="type")),
            [
                "notes/plain",
                "docs/adr/0002-ticket",
                "docs/adr/0001-git",
                "README",
                "docs/claude/worktree",
            ],
        )
        self.assertEqual(
            ids(docsearch.Query(sort="title"))[-2:], ["docs/adr/0002-ticket", "docs/adr/0001-git"]
        )
        hits, matched = docsearch.search(ROWS, docsearch.Query(limit=2))
        self.assertEqual((len(hits), matched), (2, 5))

    def test_problems_name_bad_values(self):
        said = docsearch.problems_of(
            docsearch.Query(sort="size", format="xml", since="yesterday", until="2026-8-5")
        )
        self.assertEqual(len(said), 4)


def render(query: docsearch.Query, rows=ROWS) -> str:
    out = io.StringIO()
    hits, matched = docsearch.search(rows, query)
    docsearch.render(out, hits, matched, len(rows), query.format)
    return out.getvalue()


class RenderTest(unittest.TestCase):
    def test_count_says_shown_only_when_cut(self):
        self.assertEqual(render(docsearch.Query(format="count")), "matched=5 total=5\n")
        self.assertEqual(
            render(docsearch.Query(format="count", limit=1)), "matched=5 shown=1 total=5\n"
        )

    def test_path_jsonl_json(self):
        self.assertEqual(
            render(docsearch.Query(format="path", types=["guide"])),
            "README\ndocs/claude/worktree\n",
        )
        lines = render(docsearch.Query(format="jsonl", types=["guide"])).splitlines()
        self.assertEqual(
            [json.loads(line)["concept_id"] for line in lines], ["README", "docs/claude/worktree"]
        )
        self.assertEqual(len(json.loads(render(docsearch.Query(format="json")))), 5)

    def test_detail_folds_lines(self):
        rows = [row("a", type="guide", description="1 行目\n2 行目", tags=["x", "y"])]
        text = render(docsearch.Query(format="detail"), rows)
        self.assertIn("  description: 1 行目 2 行目\n", text)
        self.assertIn("  tags       : x, y\n", text)

    def test_table_aligns_wide_characters_as_two_columns(self):
        rows = [
            row("日本語/文書", type="ガイド", title="題"),
            row("ab", type="adr", title="t"),
        ]
        lines = render(docsearch.Query(format="table"), rows).splitlines()
        # タイトルの列が始まる見た目の位置が揃う。
        starts = [docsearch.dwidth(line[: line.rindex(" ") + 1]) for line in lines]
        self.assertEqual(starts[0], starts[1])
        self.assertEqual(lines[0], "adr     ab           t")

    def test_broken_rows_do_not_break_any_format(self):
        rows = [
            {"concept_id": "x", "frontmatter": {"tags": {"a": 1}, "type": ["odd"]}, "mtime": None},
            {"concept_id": "y", "frontmatter": "not a mapping"},
        ]
        for fmt in docsearch.FORMATS:
            with self.subTest(fmt=fmt):
                render(docsearch.Query(format=fmt, texts=[""], tags=["q"]), rows)
                render(docsearch.Query(format=fmt, sort="title"), rows)


# --- 5. CLI ----------------------------------------------------------------------------


def clean_env(**extra: str) -> dict[str, str]:
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    environment.pop("CLAUDE_PROJECT_DIR", None)
    environment.update(extra)
    return environment


class CliTest(Repo):
    def ccnavi(self, *args: str, cwd: str | None = None):
        return run_ccnavi(
            ["--root", self.root, "--log", "", "--state", "", *args],
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
            done = run_ccnavi(
                ["--root", bare, "--log", "", "--docs", "--format", "count"], env=clean_env()
            )
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

    def test_filters_outside_docs_are_dropped_and_said(self):
        done = self.ccnavi("--type", "adr", "--no-refresh", "-r", "--version")
        self.assertEqual(done.returncode, 0)
        done = self.ccnavi("--lint", "--type", "adr", "--limit", "3")
        self.assertIn("ccnavi: --type は --docs でだけ効く", done.stderr)
        self.assertIn("ccnavi: --limit は --docs でだけ効く", done.stderr)


# --- 6. プロジェクト -------------------------------------------------------------------


class ProjectsTest(Repo):
    """プロジェクトの置き場（`projects/`）の直下の各 git も索引に入れる。"""

    def setUp(self):
        super().setUp()
        write(os.path.join(self.root, ".gitignore"), "**/index.jsonl\n/projects/\n")
        self.lib = self.project("lib", "**/index.jsonl\n")
        self.bare = self.project("bare", "")
        self.put("docs/w.md", doc(type="guide", title="ワークスペース"))
        self.put("projects/lib/docs/x.md", doc(type="adr", title="lib の決定"))
        self.put("projects/bare/y.md", doc(type="guide"))

    def project(self, name: str, ignore: str) -> str:
        home = os.path.join(self.root, "projects", name)
        os.makedirs(home)
        git(home, "init", "-q")
        write(os.path.join(home, ".gitignore"), ignore)
        return home

    def ccnavi(self, *args: str):
        return run_ccnavi(
            ["--root", self.root, "--log", "", "--state", "", "--docs", *args],
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
        with open(os.path.join(self.lib, "docs", "index.jsonl"), encoding="utf-8") as f:
            self.assertEqual(json.loads(f.readline())["concept_id"], "docs/x")

    def test_path_narrows_to_one_project(self):
        done = self.ccnavi("--path", "projects/lib", "--format", "path")
        self.assertEqual(done.stdout, "projects/lib/docs/x\n")

    def test_a_project_that_ignores_no_index_is_left_out_and_named(self):
        done = self.ccnavi("--format", "count")
        self.assertEqual(done.stdout, "matched=2 total=2\n")
        self.assertIn(f"bare {docsearch.UNIGNORED}", done.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.bare, "index.jsonl")))
        with open(os.path.join(self.bare, ".gitignore"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "")  # .gitignore は書き換えない

    def test_the_ccnavi_directory_of_a_project_is_left_out(self):
        self.put("projects/lib/.ccnavi/approved/doing/i0001.md", "---\nversion: 1\n---\n")
        done = self.ccnavi("--path", ".ccnavi", "--format", "count")
        self.assertEqual(done.stdout, "matched=0 total=2\n")

    def test_a_past_deadline_stops_before_any_tree(self):
        conf = settings.load(self.root)[0]
        found = docsearch.collect(conf, self.root, deadline=time.monotonic() - 1)
        self.assertTrue(found.timed_out)
        self.assertEqual(found.rows, [])


# --- 7. SessionStart -------------------------------------------------------------------


class SessionStartTest(Repo):
    def context(self, root: str, **extra) -> str:
        payload = {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": root, **extra}
        done = run_ccnavi(
            ["--root", root, "--log", "", "--state", "", "--approved", "", "--mode", "enable"],
            input=json.dumps(payload),
            cwd=root,
            env=clean_env(CCNAVI_BIN_PATH=".ccnavi/scripts/ccnavi-launcher.sh"),
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        if not done.stdout.strip():
            return ""
        return json.loads(done.stdout)["hookSpecificOutput"].get("additionalContext", "")

    def test_refreshes_the_index_and_tells_how_to_search(self):
        self.put("docs/a.md", doc(type="guide"))
        said = self.context(self.root)
        self.assertTrue(os.path.exists(self.index_of("docs")))
        launcher = f"sh {self.root.replace(os.sep, '/')}/.ccnavi/scripts/ccnavi-launcher.sh --docs"
        self.assertIn(launcher, said)
        self.assertIn("grep・Glob より先に", said)
        self.assertIn("type は必須", said)
        self.assertNotIn(docsearch.CONVENTION_DOC, said)
        self.assertNotIn(docsearch.UNIGNORED, said)

    def test_names_the_convention_document_when_present(self):
        self.put("docs/a.md", doc(type="guide"))
        self.put(docsearch.CONVENTION_DOC, doc(type="guide"))
        self.assertIn(docsearch.CONVENTION_DOC, self.context(self.root))

    def test_names_a_project_left_out(self):
        write(os.path.join(self.root, ".gitignore"), "**/index.jsonl\n/projects/\n")
        self.put("docs/a.md", doc(type="guide"))
        home = os.path.join(self.root, "projects", "bare")
        os.makedirs(home)
        git(home, "init", "-q")
        self.put("projects/bare/y.md", doc(type="guide"))
        said = self.context(self.root)
        self.assertIn("--docs", said)
        self.assertIn(f"bare {docsearch.UNIGNORED}", said)

    def test_only_the_left_out_line_when_nothing_is_indexed(self):
        write(os.path.join(self.root, ".gitignore"), "")
        self.put("docs/a.md", doc(type="guide"))
        said = self.context(self.root)
        self.assertIn(f"{docsearch.WORKSPACE} {docsearch.UNIGNORED}", said)
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


if __name__ == "__main__":
    unittest.main()
