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
7. SessionStart: 索引を新しくして案内をつける。対象外は名指し。
   サブエージェントと git の外では案内を出さない
"""

from __future__ import annotations

import errno
import io
import json
import os
import subprocess
import tempfile
import time
import unicodedata
import unittest
from unittest import mock

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
        self.assertEqual(self.rows_of("")[0]["directory"], ".")
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

    def test_an_index_with_a_broken_line_is_not_ours_and_left_alone(self):
        self.put("a.md", doc(type="guide"))
        write(self.index_of(""), "{not json\n")
        built = docsearch.build(self.root)
        self.assertEqual(built.parsed, 1)
        self.assertEqual([r["concept_id"] for r in built.rows], ["a"])
        self.assertEqual((built.written, built.foreign), ([], ["index.jsonl"]))
        with open(self.index_of(""), encoding="utf-8") as f:
            self.assertEqual(f.read(), "{not json\n")


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
        # 大文字小文字を区別せず、同じ値は concept_id で並ぶ（`adr` と `ADR` は同じ値）。
        # frontmatter が無いものは空として先頭に来る。
        self.assertEqual(
            ids(docsearch.Query(sort="type")),
            [
                "notes/plain",
                "docs/adr/0001-git",
                "docs/adr/0002-ticket",
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

    def test_filters_outside_docs_stop_the_run(self):
        done = self.ccnavi("--lint", "--type", "adr", "--limit", "3")
        self.assertEqual(done.returncode, 1)
        self.assertIn("ccnavi: --type は --docs でだけ使える", done.stderr)
        self.assertIn("ccnavi: --limit は --docs でだけ使える", done.stderr)
        # 以前は argparse が知らないフラグとして止めていた打ち間違い。落として進めない。
        done = self.ccnavi("ticket", "start", "i0001", "--limit", "3")
        self.assertEqual(done.returncode, 1)
        self.assertIn("--limit は --docs でだけ使える", done.stderr)
        self.assertNotIn("チケット", done.stdout)

    def test_flags_of_other_runs_stop_docs(self):
        self.put("a.md", doc(type="guide"))
        for args in (
            ("--yes", "i0001"),
            ("--preview",),
            ("--verify",),
            ("--result", "{}"),
            ("--tickets", "wip"),
            ("--reason", "x"),
            ("--flow", "f.yml"),
            ("--reviewed", "0"),
        ):
            with self.subTest(args=args):
                done = self.ccnavi("--docs", *args)
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn(f"--docs は {args[0]} と一緒に使えない", done.stderr)

    def test_settings_and_wrapper_flags_stop_docs_too(self):
        """設定や sh の綴りを差し替えるフラグも、黙って無視せずに止める。"""
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
            ("--integration-branch", "main"),
            ("--choose-out", "x"),
        ):
            with self.subTest(args=args):
                done = self.ccnavi("--docs", *args)
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn(f"--docs は {args[0]} と一緒に使えない", done.stderr)
                self.assertEqual(done.stdout, "")
                # 層の置き場の「診断でだけ効く」の文で先へ進まない。
                self.assertNotIn("診断", done.stderr)

    def test_root_log_and_state_are_accepted(self):
        self.put("a.md", doc(type="guide"))
        done = self.ccnavi("--docs", "--state", "", "--format", "path")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "a\n")

    def test_impossible_dates_are_refused(self):
        for value in ("2026-13-45", "2026-02-30", "2026-08-05T25", "2026-08-05T10:61"):
            with self.subTest(value=value):
                done = self.ccnavi("--docs", "--since", value)
                self.assertEqual(done.returncode, 1)
        self.assertEqual(self.ccnavi("--docs", "--until", "2026-08-05T10:59").returncode, 0)


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


# --- 8. 敵対的な入力と失敗 --------------------------------------------------------------


def can_symlink_dirs() -> bool:
    with tempfile.TemporaryDirectory() as d:
        try:
            os.symlink(d, os.path.join(d, "link"), target_is_directory=True)
        except (OSError, NotImplementedError):
            return False
    return True


class ForeignIndexTest(Repo):
    """A: ccnavi の形でない index.jsonl は上書きも削除もしない。"""

    def test_a_foreign_index_is_not_overwritten(self):
        self.put("a.md", doc(type="guide"))
        foreign = '{"name": "other tool"}\n'
        write(self.index_of(""), foreign)
        built = docsearch.build(self.root)
        self.assertEqual(built.foreign, ["index.jsonl"])
        self.assertIn("ccnavi の索引ではない", built.problems[0])
        with open(self.index_of(""), encoding="utf-8") as f:
            self.assertEqual(f.read(), foreign)

    def test_a_foreign_index_of_an_emptied_directory_is_not_removed(self):
        self.put("gone/b.md", doc(type="guide"))
        self.put("keep.md", doc(type="guide"))
        git(self.root, "add", "gone/b.md")
        write(self.index_of("gone"), "not ours\n")
        os.remove(os.path.join(self.root, "gone", "b.md"))
        built = docsearch.build(self.root)
        self.assertTrue(os.path.exists(self.index_of("gone")))
        self.assertEqual(built.removed, [])

    def test_an_empty_index_is_ours(self):
        self.put("a.md", doc(type="guide"))
        write(self.index_of(""), "\n")
        self.assertEqual(docsearch.build(self.root).written, ["index.jsonl"])

    def test_cli_and_session_start_name_it(self):
        self.put("a.md", doc(type="guide"))
        write(self.index_of(""), "x\n")
        done = run_ccnavi(
            ["--root", self.root, "--log", "", "--docs", "--format", "count"], env=clean_env()
        )
        self.assertIn("index.jsonl は ccnavi の索引ではないので書き換えない", done.stderr)
        conf = settings.load(self.root)[0]
        self.assertIn("書き換えなかった: index.jsonl", docsearch.at_start(conf, self.root))


class OutsideTest(Repo):
    """B: リンク越しにツリーの外を読まない・書かない。"""

    def outside(self) -> str:
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        path = os.path.realpath(other.name)
        write(os.path.join(path, "x.md"), doc(type="guide"))
        return path

    def assert_nothing_outside(self, outside: str, built: docsearch.Built) -> None:
        self.assertFalse(os.path.exists(os.path.join(outside, "index.jsonl")))
        self.assertEqual([r for r in built.rows if r["concept_id"].startswith("linked")], [])

    def test_under_refuses_a_path_outside(self):
        base = os.path.normcase(os.path.realpath(self.root))
        self.assertTrue(docsearch._under(os.path.join(self.root, "docs"), base))
        self.assertFalse(docsearch._under(self.outside(), base))

    @unittest.skipUnless(os.name == "nt", "ジャンクションは Windows にしか無い")
    def test_a_junction_is_neither_read_nor_written(self):
        import _winapi

        outside = self.outside()
        link = os.path.join(self.root, "linked")
        _winapi.CreateJunction(outside, link)
        self.addCleanup(os.rmdir, link)
        self.assertFalse(docsearch._under(link, os.path.normcase(os.path.realpath(self.root))))
        self.put("a.md", doc(type="guide"))
        self.assert_nothing_outside(outside, docsearch.build(self.root))

    @unittest.skipUnless(can_symlink_dirs(), "ディレクトリのシンボリックリンクを作れない")
    def test_a_symlinked_directory_is_neither_read_nor_written(self):
        outside = self.outside()
        os.symlink(outside, os.path.join(self.root, "linked"), target_is_directory=True)
        self.put("a.md", doc(type="guide"))
        self.assert_nothing_outside(outside, docsearch.build(self.root))


class RobustTest(Repo):
    """C: 壊れた入力で落ちない。"""

    def test_front_matter_that_json_cannot_write_is_none(self):
        huge = b"---\nn: 1" + b"9" * 5000 + b"\n---\n"
        self.assertIsNone(docsearch.front_matter(huge))

    def test_a_deeply_nested_index_is_foreign_not_a_crash(self):
        self.put("a.md", doc(type="guide"))
        write(self.index_of(""), "[" * 100000 + "\n")
        built = docsearch.build(self.root)
        self.assertEqual(built.foreign, ["index.jsonl"])

    def test_one_broken_directory_does_not_stop_the_others(self):
        self.put("bad/a.md", doc(type="guide"))
        self.put("good/b.md", doc(type="adr"))
        real = docsearch._scan

        def broken(base, base_real, directory, *args):
            if directory == "bad":
                raise RuntimeError("boom")
            return real(base, base_real, directory, *args)

        with mock.patch.object(docsearch, "_scan", broken):
            built = docsearch.build(self.root)
        self.assertEqual([r["concept_id"] for r in built.rows], ["good/b"])
        self.assertIn("bad/ を読めない", built.problems[0])

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
                    ["--root", self.root, "--log", "", "--docs", "--no-refresh", "--format", fmt],
                    env=clean_env(),
                )
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertIn("ccnavi の索引ではない", done.stderr)
                self.assertIn('"a"' if fmt == "json" else "guide", done.stdout)


class DeadlineTest(Repo):
    """D: 期限は md 1 本ごとに見る。打ち切ったディレクトリも読めた分は書き、回を重ねれば埋まる。"""

    def test_parse_stops_between_files_and_keeps_the_rest_waiting(self):
        self.put("a.md", doc(type="guide"))
        one = docsearch._Dir("", "index.jsonl", self.index_of(""), True, docsearch.ABSENT, {}, None)
        one.order = ["a"]
        one.pending = [("a.md", "a", "2026-01-01T00:00:00")]
        built = docsearch.Built()
        docsearch._parse_pending(self.root, [one], time.monotonic() - 1, built)
        self.assertTrue(built.timed_out)
        self.assertEqual(built.parsed, 0)
        self.assertEqual(len(one.pending), 1)

    def test_a_directory_cut_short_is_written_up_to_where_it_got(self):
        """読めた md の新しい行と、読めなかった md の既存の行で書く。"""
        self.put("a.md", doc(type="guide"))
        self.put("b.md", doc(type="guide"))
        old_b = {"concept_id": "b", "directory": ".", "frontmatter": None, "mtime": "x"}
        write(self.index_of(""), json.dumps(old_b) + "\n")
        real = docsearch._read_head
        reads = []

        def once(path):
            # 1 本読んだら期限が切れたことにする。
            reads.append(path)
            return real(path)

        with (
            mock.patch.object(docsearch, "_read_head", once),
            mock.patch.object(docsearch, "_past", lambda deadline: bool(reads)),
        ):
            built = docsearch.build(self.root, deadline=time.monotonic() + 600)
        self.assertTrue(built.timed_out)
        self.assertEqual(built.written, ["index.jsonl"])
        rows = {r["concept_id"]: r for r in map(json.loads, read(self.index_of("")).splitlines())}
        self.assertEqual(rows["a"]["frontmatter"], {"type": "guide"})
        self.assertEqual(rows["b"], old_b)

        # 次の回に残りを読む。
        built = docsearch.build(self.root)
        self.assertFalse(built.timed_out)
        rows = {r["concept_id"]: r for r in map(json.loads, read(self.index_of("")).splitlines())}
        self.assertEqual(rows["b"]["frontmatter"], {"type": "guide"})

    def test_a_big_directory_fills_up_over_runs_and_does_not_starve_the_rest(self):
        """大きなディレクトリが毎回打ち切られても、回を重ねれば埋まり、後ろのディレクトリも読む。"""
        for i in range(30):
            self.put(f"docs/d{i:03}.md", doc(type="guide"))
        self.put("zz/late.md", doc(type="adr"))
        budget = {"left": 0}
        real = docsearch._read_head

        def counted(path):
            budget["left"] -= 1
            return real(path)

        def past(deadline):
            return deadline is not None and budget["left"] <= 0

        seen = []
        with (
            mock.patch.object(docsearch, "_read_head", counted),
            mock.patch.object(docsearch, "_past", past),
        ):
            for _ in range(3):
                budget["left"] = 12  # 1 回に読めるのは 12 本まで
                built = docsearch.build(self.root, deadline=time.monotonic() + 600)
                seen.append(len(built.rows))
                self.assertIn("zz/late", [r["concept_id"] for r in built.rows])
        # 1 回目は zz/late と docs の 11 本、2 回目は docs の続き 12 本、3 回目で残りの 7 本。
        self.assertEqual(seen, [12, 24, 31])
        built = docsearch.build(self.root)
        self.assertFalse(built.timed_out)
        self.assertEqual(built.parsed, 0)

    def test_session_start_without_time_does_not_refresh_but_still_guides(self):
        """期限が切れていれば新しくしないが、既存の索引と md があれば案内は出す。"""
        self.put("a.md", doc(type="guide"))
        conf = settings.load(self.root)[0]
        said = docsearch.at_start(conf, self.root, time.monotonic() - 1)
        self.assertIn("--docs", said)
        self.assertFalse(os.path.exists(self.index_of("")))

        docsearch.build(self.root)
        before = read(self.index_of(""))
        self.put("a.md", doc(type="adr"))
        os.utime(self.root + "/a.md", (time.time() + 5, time.time() + 5))
        said = docsearch.at_start(conf, self.root, time.monotonic() - 1)
        self.assertIn("--docs", said)
        self.assertEqual(read(self.index_of("")), before)

    def test_session_start_without_time_and_without_md_says_nothing(self):
        self.put("docs/x.txt", "x")
        conf = settings.load(self.root)[0]
        self.assertEqual(docsearch.at_start(conf, self.root, time.monotonic() - 1), "")

    def test_read_head_stops_early_without_front_matter(self):
        path = self.put("big.md", "x" * 20000)
        self.assertEqual(len(docsearch._read_head(path)), docsearch.FIRST_READ)
        path = self.put("closed.md", "---\ntype: a\n---\n" + "y" * 20000)
        self.assertEqual(len(docsearch._read_head(path)), docsearch.FIRST_READ)
        path = self.put("long.md", "---\nd: " + "z" * 10000 + "\n---\nbody")
        self.assertEqual(docsearch.front_matter(docsearch._read_head(path))["d"], "z" * 10000)


class GitFailureTest(Repo):
    """E: git に聞けなかったことを、git の外・無視されていないと取り違えない。"""

    def failing(self, verb: str):
        real = docsearch.gitcmd.run

        def run(cwd, args, *rest, **kwargs):
            if args[0] == verb:
                return docsearch.gitcmd.Done(failure="timed out", timed_out=True)
            return real(cwd, args, *rest, **kwargs)

        return mock.patch.object(docsearch.gitcmd, "run", run)

    def test_ls_files_failure_is_failed_not_outside_git(self):
        self.put("a.md", doc(type="guide"))
        conf = settings.load(self.root)[0]
        with self.failing("ls-files"):
            found = docsearch.collect(conf, self.root)
        self.assertEqual(found.failed, [f"{docsearch.WORKSPACE}: git が期限までに終わらなかった"])
        self.assertEqual(found.unignored, [])

    def test_check_ignore_failure_is_not_unignored(self):
        self.put("a.md", doc(type="guide"))
        conf = settings.load(self.root)[0]
        with self.failing("check-ignore"):
            found = docsearch.collect(conf, self.root)
            said = docsearch.at_start(conf, self.root)
        self.assertEqual(found.unignored, [])
        self.assertEqual(len(found.failed), 1)
        self.assertNotIn(docsearch.UNIGNORED, said)

    def test_cli_says_the_query_failed(self):
        self.put("a.md", doc(type="guide"))
        with self.failing("ls-files"):
            done = run_ccnavi(["--root", self.root, "--log", "", "--docs"], env=clean_env())
        self.assertEqual(done.returncode, 0)
        self.assertIn("git への問い合わせに失敗したので引かない", done.stderr)


class NormalizationTest(unittest.TestCase):
    """F: NFC に揃えて比べ、結合文字は幅 0。"""

    NFD = unicodedata.normalize("NFD", "が")

    def test_nfd_rows_match_nfc_queries(self):
        rows = [row(f"docs/{self.NFD}", tags=[self.NFD], title=self.NFD)]
        for query in (
            docsearch.Query(paths=["が"]),
            docsearch.Query(tags=["が"]),
            docsearch.Query(texts=["が"]),
        ):
            with self.subTest(query=query):
                self.assertEqual(len(docsearch.search(rows, query)[0]), 1)

    def test_combining_marks_have_no_width(self):
        self.assertEqual(docsearch.dwidth(self.NFD), 2)
        self.assertEqual(docsearch.dwidth("é"), 1)


class TempFileTest(Repo):
    """G: 一時ファイルは .git の中に排他で作り、残骸は次の回に消す。"""

    def test_nothing_new_shows_in_git_status(self):
        git(self.root, "add", ".gitignore")
        git(self.root, "commit", "-q", "-m", "i")
        self.put("a.md", doc(type="guide"))
        git(self.root, "add", "a.md")
        git(self.root, "commit", "-q", "-m", "a")
        docsearch.build(self.root)
        self.assertTrue(os.path.exists(self.index_of("")))
        self.assertEqual(git(self.root, "status", "--porcelain", "-uall"), "")

    def test_stale_leftovers_are_swept(self):
        self.put("a.md", doc(type="guide"))
        git_dir = os.path.join(self.root, ".git")
        stale = write(os.path.join(git_dir, "ccnavi-index-1-0.tmp"), "x", mtime=NOON)
        fresh = write(os.path.join(git_dir, "ccnavi-index-1-1.tmp"), "x")
        docsearch.build(self.root)
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.exists(fresh))
        leftovers = [n for n in os.listdir(git_dir) if n.startswith("ccnavi-index-")]
        self.assertEqual(leftovers, ["ccnavi-index-1-1.tmp"])


class DedupeAndProjectsTest(Repo):
    """H: 同じ concept_id は 1 本に。`.git` の無いプロジェクトは CLI で名指し。"""

    def test_nfc_duplicates_are_folded(self):
        nfc, nfd = "が", unicodedata.normalize("NFD", "が")
        self.put(f"{nfc}.md", doc(type="a"))
        try:
            self.put(f"{nfd}.md", doc(type="b"))
        except OSError:
            self.skipTest("NFD の名前を作れない")
        names = os.listdir(self.root)
        if not (nfc + ".md" in names and nfd + ".md" in names):
            self.skipTest("このファイルシステムは名前を正規化する")
        conf = settings.load(self.root)[0]
        self.assertEqual(len(docsearch.collect(conf, self.root).rows), 1)

    def test_a_project_without_git_is_named_by_the_cli(self):
        self.put("a.md", doc(type="guide"))
        self.put("projects/plain/x.md", doc(type="guide"))
        done = run_ccnavi(["--root", self.root, "--log", "", "--docs"], env=clean_env())
        self.assertIn("プロジェクト plain は .git を持たないので引かない", done.stderr)
        conf = settings.load(self.root)[0]
        self.assertNotIn("plain", docsearch.at_start(conf, self.root))


# --- 敵対的レビューの指摘 -------------------------------------------------------------------


class LineSeparatorTest(Repo):
    """自分の index.jsonl を、値の中の U+2028・U+2029・U+0085 で割って foreign にしない。"""

    def test_a_line_separator_in_a_value_keeps_the_index_ours(self):
        for escape, char in (("\\L", " "), ("\\P", " "), ("\\N", "\u0085")):
            with self.subTest(char=hex(ord(char))):
                self.put("a.md", f'---\ntype: guide\ntitle: "x{escape}y"\n---\n')
                docsearch.build(self.root)
                self.assertIn(char, read(self.index_of("")))
                built = docsearch.build(self.root)
                self.assertEqual(built.foreign, [])
                self.assertEqual(built.reused, 1)
                self.assertEqual(built.rows[0]["frontmatter"]["title"], f"x{char}y")
                os.remove(self.index_of(""))

    def test_crlf_lines_are_still_read(self):
        self.put("a.md", doc(type="guide"))
        docsearch.build(self.root)
        text = read(self.index_of(""))
        with open(self.index_of(""), "w", encoding="utf-8", newline="") as f:
            f.write(text.replace("\n", "\r\n"))
        built = docsearch.build(self.root)
        self.assertEqual(built.foreign, [])
        self.assertEqual(built.reused, 1)


class CrossDeviceTest(Repo):
    """`.git` が別のファイルシステムでも、作業ツリーの側の一時ファイルで書く。"""

    def exdev(self):
        real = os.replace
        git_dir = os.path.join(self.root, ".git") + os.sep

        def replace(src, dst):
            if str(src).startswith(git_dir):
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            return real(src, dst)

        return mock.patch.object(docsearch.os, "replace", replace)

    def leftovers(self, rel_dir: str) -> list[str]:
        where = os.path.join(self.root, *[p for p in rel_dir.split("/") if p])
        return [n for n in os.listdir(where) if n.startswith(docsearch.LOCAL_TMP_PREFIX)]

    def test_writes_through_a_temporary_beside_the_index(self):
        self.put("docs/a.md", doc(type="guide"))
        self.put("b.md", doc(type="adr"))
        with self.exdev():
            built = docsearch.build(self.root)
        self.assertEqual(built.problems, [])
        self.assertEqual(sorted(built.written), ["docs/index.jsonl", "index.jsonl"])
        self.assertEqual(self.rows_of("docs")[0]["frontmatter"], {"type": "guide"})
        self.assertEqual(self.leftovers("docs"), [])
        self.assertEqual(self.leftovers(""), [])
        status = git(self.root, "status", "--porcelain", "--untracked-files=all")
        self.assertNotIn("index.jsonl", status)
        self.assertNotIn(docsearch.LOCAL_TMP_PREFIX, status)

    def test_does_not_write_when_the_temporary_would_not_be_ignored(self):
        write(os.path.join(self.root, ".gitignore"), "/docs/index.jsonl\n")
        self.put("docs/a.md", doc(type="guide"))
        with self.exdev():
            built = docsearch.build(self.root)
        self.assertEqual(built.written, [])
        self.assertIn("git に無視されない", " ".join(built.problems))
        self.assertFalse(os.path.exists(self.index_of("docs")))
        self.assertEqual(self.leftovers("docs"), [])
        self.assertEqual([r["concept_id"] for r in built.rows], ["docs/a"])

    def test_sweeps_only_its_own_stale_leftovers(self):
        self.put("docs/a.md", doc(type="guide"))
        old = time.time() - docsearch.TMP_STALE_SECONDS - 60
        mine = write(os.path.join(self.root, "docs", ".ccnavi-tmp-1-1", "index.jsonl"), "x")
        theirs = write(os.path.join(self.root, "docs", ".ccnavi-tmp-2-2", "notes.txt"), "keep")
        fresh = write(os.path.join(self.root, "docs", ".ccnavi-tmp-3-3", "index.jsonl"), "x")
        for path in (mine, theirs):
            os.utime(os.path.dirname(path), (old, old))
        with self.exdev():
            docsearch.build(self.root)
        self.assertFalse(os.path.exists(os.path.dirname(mine)))
        self.assertTrue(os.path.exists(theirs))
        self.assertTrue(os.path.exists(fresh))

    def test_a_different_device_goes_beside_from_the_start(self):
        self.put("a.md", doc(type="guide"))
        real = os.stat
        git_dir = os.path.join(self.root, ".git")

        class Other:
            def __init__(self, info):
                self.info = info

            def __getattr__(self, name):
                return self.info.st_dev + 1 if name == "st_dev" else getattr(self.info, name)

        def stat(path, *args, **kwargs):
            info = real(path, *args, **kwargs)
            return Other(info) if os.fspath(path) == git_dir else info

        writer = None
        with mock.patch.object(docsearch.os, "stat", stat):
            writer = docsearch._Writer(self.root, git_dir, None)
        self.assertTrue(writer.beside)


class PathspecMagicTest(Repo):
    """パスやユーザの環境を pathspec の magic として読ませない。"""

    def test_check_ignore_takes_a_path_that_looks_like_magic(self):
        paths = [":(exclude)x/index.jsonl", ":!y/index.jsonl", "*/index.jsonl"]
        self.assertEqual(docsearch._ignored(self.root, paths), set(paths))

    @unittest.skipIf(os.name == "nt", "Windows では `:` を名前に使えない")
    def test_a_directory_named_like_magic_is_indexed(self):
        self.put(":(exclude)x/a.md", doc(type="guide"))
        built = docsearch.build(self.root)
        self.assertEqual(built.written, [":(exclude)x/index.jsonl"])

    def test_pathspec_environment_of_the_user_does_not_break_the_index(self):
        self.put("docs/a.md", doc(type="guide"))
        for name in ("GIT_LITERAL_PATHSPECS", "GIT_ICASE_PATHSPECS", "GIT_GLOB_PATHSPECS"):
            with self.subTest(name=name), mock.patch.dict(os.environ, {name: "1"}):
                built = docsearch.build(self.root)
                self.assertEqual([r["concept_id"] for r in built.rows], ["docs/a"])


class JsonOutputTest(Repo):
    """どの出力の形でも落ちず、正しい JSON を出す。"""

    def test_deep_front_matter_is_null_and_json_output_works(self):
        nested = "[" * 40 + "]" * 40
        self.put("a.md", f"---\ntype: guide\nn: {nested}\n---\n")
        done = run_ccnavi(
            ["--root", self.root, "--log", "", "--docs", "--format", "json"], env=clean_env()
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIsNone(json.loads(done.stdout)[0]["frontmatter"])

    def test_nan_and_infinity_rows_are_foreign(self):
        self.put("a.md", doc(type="guide"))
        for bad in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(bad=bad):
                front = f'"frontmatter":{{"n":{bad}}}'
                text = f'{{"concept_id":"a","directory":".",{front},"mtime":"x"}}\n'
                write(self.index_of(""), text)
                args = ["--root", self.root, "--log", "", "--docs", "--no-refresh"]
                done = run_ccnavi([*args, "--format", "json"], env=clean_env())
                self.assertEqual(done.returncode, 0, done.stderr)

                def refuse(constant):
                    raise ValueError(constant)

                rows = json.loads(done.stdout, parse_constant=refuse)
                self.assertEqual(rows[0]["frontmatter"], {"type": "guide"})


if __name__ == "__main__":
    unittest.main()
