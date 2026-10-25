"""気づきの記録の読み取り（共有）と、処理すべきものの判定。

設計は docs/21_design/tickets/learning-wiki.md。収集・親の `finish`・`ticket consolidate`・
`--learnings` が同じ読み方をする。読むのはワークツリーの HEAD にある通常のファイルで、
未コミットの変更は見ない（届くのはコミットしたものだけ）。

書式: frontmatter の後の `## ` で始まる行を 1 件の気づきの見出しとする。処理の行は、その節の中の
`- 処理: 反映 <パターン名>` か `- 処理: 対象外 <理由>` の 1 行。

2 段の検査:
- 読み取りの検査: 読めない・通常のファイルでない・frontmatter が無い／壊れている・`learnings` が
  無い／不正 → 「読み取れない記録」。節が無いから処理済み、とは見なさない
- 整合の検査（読み取れた記録に適用する）: `none` なのに節がある・数と節の数が合わない・1 つの節に
  処理の行が 2 つ以上、または形の分からない処理の行がある
どちらも統合では直せないので、記録を直すよう案内する。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..infra import gitcmd, pathcase, yamlread
from ..records import wiki

LEARNING_DIR = "wip/handoff/learnings"
INBOX_DIR = "docs/wiki/inbox"
PATTERN_DIR = "docs/wiki/patterns"
# 親の finish が未コミットの変更を見る置き場。
WATCHED = (LEARNING_DIR, PATTERN_DIR, INBOX_DIR)

PROCESS_REFLECT = "反映"
PROCESS_SKIP = "対象外"

TIMEOUT = 5.0
_REGULAR = ("100644", "100755")
_PROCESS_LINE = re.compile(r"^- 処理:")
# 区切りには、日本語入力で入りやすい全角空白（U+3000）も使える。
_PROCESS_FORM = re.compile(r"^- 処理:[ \t\u3000]+(反映|対象外)(?:[ \t\u3000]+(.*\S))?[ \t\u3000]*$")


@dataclass
class Section:
    """気づき 1 件の節。"""

    title: str
    # 処理の行（`- 処理:` で始まる行）の原文。
    process_lines: list[str] = field(default_factory=list)

    @property
    def process(self) -> tuple[str, str] | None:
        """`(反映|対象外, 後ろ)`。行が 1 つで形が合うときだけ。"""
        if len(self.process_lines) != 1:
            return None
        found = _PROCESS_FORM.match(self.process_lines[0])
        if found is None:
            return None
        return found.group(1), (found.group(2) or "").strip()


@dataclass
class Record:
    path: str
    # 観測元の ID（ファイル名から .md を除いたもの）。
    ident: str
    learnings: int | None  # None は `learnings: none`
    sections: list[Section]


@dataclass
class Item:
    """処理すべきもの 1 件。"""

    path: str
    # 節の見出し。受信箱のファイルでは空。
    title: str
    why: str

    def describe(self) -> str:
        where = f"{self.path} の「{self.title}」" if self.title else self.path
        return f"{where}（{self.why}）"


@dataclass
class Pending:
    items: list[Item] = field(default_factory=list)
    # 読み取れない記録 `(パス, 理由)`。
    unreadable: list[tuple[str, str]] = field(default_factory=list)
    # 整合の検査にヒットする記録 `(パス, ヒットした条件)`。
    inconsistent: list[tuple[str, str]] = field(default_factory=list)

    @property
    def broken(self) -> bool:
        return bool(self.unreadable or self.inconsistent)


def parse(path: str, ident: str, text: str) -> tuple[Record | None, str]:
    """記録の文面を読む。読み取りの検査にヒットすれば `(None, 理由)`。"""
    text = text.lstrip("﻿").replace("\r\n", "\n")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, "frontmatter が無い"
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return None, "frontmatter が閉じていない"
    try:
        front = yamlread.safe_load("\n".join(lines[1:end]))
    except Exception as exc:  # YAML の誤りは種類を問わず「読めない」として扱う
        return None, f"frontmatter が YAML として読めない（{str(exc).splitlines()[0]}）"
    if not isinstance(front, dict):
        return None, "frontmatter が YAML のマッピングでない"
    if "learnings" not in front:
        return None, "`learnings` が無い"
    raw = front["learnings"]
    if raw == "none":
        count = None
    elif isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
        count = raw
    else:
        return None, f"`learnings: {raw}` は `none` か 0 以上の整数でなければならない"
    sections: list[Section] = []
    for line in lines[end + 1 :]:
        if line.startswith("## "):
            sections.append(Section(title=line[3:].strip()))
        elif sections and _PROCESS_LINE.match(line):
            sections[-1].process_lines.append(line)
    return Record(path, ident, count, sections), ""


def consistency_problem(record: Record) -> str:
    """整合の検査。ヒットすれば条件、無ければ空。"""
    n = len(record.sections)
    if record.learnings is None and n:
        return f"`learnings: none` なのに気づきの節が {n} 件ある"
    if record.learnings is not None and record.learnings != n:
        return f"`learnings: {record.learnings}` と節の数（{n}）が合わない"
    for sec in record.sections:
        if len(sec.process_lines) > 1:
            return f"「{sec.title}」に処理の行が {len(sec.process_lines)} つある"
        if sec.process_lines and sec.process is None:
            return (
                f"「{sec.title}」の処理の行の形が分からない"
                "（`- 処理: 反映 <パターン名>` か `- 処理: 対象外 <理由>`）"
            )
    return ""


def _listing(tree_root: str, rel: str) -> tuple[list[tuple[str, str]] | None, str]:
    """HEAD の `rel` 直下の `(名前, モード)`。読めなければ `(None, 理由)`。無ければ空。"""
    done = gitcmd.run(tree_root, ["ls-tree", "-z", "HEAD", f"{rel}/"], TIMEOUT, raw_paths=True)
    if not done.ok:
        return None, f"HEAD の {rel}/ を読めない"
    found: list[tuple[str, str]] = []
    for entry in done.out.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) < 3:
            continue
        found.append((path.rsplit("/", 1)[-1], parts[0] if parts[1] == "blob" else "tree"))
    return found, ""


def _text(tree_root: str, rel: str) -> str | None:
    data, ok = gitcmd.blob(tree_root, "HEAD", rel, TIMEOUT)
    if not ok or data is None:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def pattern_sources(tree_root: str, name: str) -> list[str] | None:
    """HEAD のパターンの `sources`。無いか、壊れている（`--learnings` が数えない）なら None。

    ファイル名は大文字小文字を区別せずに探す（`Foo` と `foo.md` は同じパターン）。
    """
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    names, _ = _listing(tree_root, PATTERN_DIR)
    want = pathcase.fold(name + ".md")
    found = next(
        (n for n, mode in names or [] if pathcase.fold(n) == want and mode in _REGULAR), ""
    )
    if not found:
        return None
    text = _text(tree_root, f"{PATTERN_DIR}/{found}")
    if text is None:
        return None
    parsed = wiki.parse_pattern(found[:-3], text)
    return None if isinstance(parsed, str) else list(parsed.sources)


def inbox_files(tree_root: str) -> tuple[list[str], str]:
    """HEAD の受信箱のファイル名。読めなければ `([], 理由)`。"""
    names, why = _listing(tree_root, INBOX_DIR)
    if names is None:
        return [], why
    # `.gitkeep` 以外は全部、判定するファイルとして数える（`.md` 以外も、大文字小文字違いも）。
    return sorted(n for n, _ in names if n != ".gitkeep"), ""


def pending(tree_root: str) -> Pending:
    """`tree_root` の HEAD で、統合の子が処理すべきものを列挙する。

    読み取れない記録と整合の検査にヒットする記録は列挙せず、別に返す（統合では直せない）。
    """
    out = Pending()
    names, why = _listing(tree_root, LEARNING_DIR)
    if names is None:
        out.unreadable.append((f"{LEARNING_DIR}/", why))
        names = []
    for name, mode in sorted(names):
        if not name.endswith(".md"):
            continue
        rel = f"{LEARNING_DIR}/{name}"
        if mode not in _REGULAR:
            out.unreadable.append((rel, "通常のファイルでない（リンクかディレクトリ）"))
            continue
        text = _text(tree_root, rel)
        if text is None:
            out.unreadable.append((rel, "読めない（UTF-8 でない、または HEAD に無い）"))
            continue
        record, why = parse(rel, name[: -len(".md")], text)
        if record is None:
            out.unreadable.append((rel, why))
            continue
        bad = consistency_problem(record)
        if bad:
            out.inconsistent.append((rel, bad))
            continue
        for sec in record.sections:
            reason = _section_problem(tree_root, record.ident, sec)
            if reason:
                out.items.append(Item(rel, sec.title, reason))
    inbox, why = inbox_files(tree_root)
    if why:
        out.unreadable.append((f"{INBOX_DIR}/", why))
    for name in inbox:
        out.items.append(Item(f"{INBOX_DIR}/{name}", "", "受信箱のファイル（判定して消す）"))
    return out


def _section_problem(tree_root: str, ident: str, sec: Section) -> str:
    """節が処理済みでない理由。処理済みなら空。"""
    got = sec.process
    if got is None:
        return "処理の行が無い"
    kind, rest = got
    if kind == PROCESS_SKIP:
        return "" if rest else "`処理: 対象外` の後ろに理由が無い"
    if not rest:
        return "`処理: 反映` の後ろにパターン名が無い"
    sources = pattern_sources(tree_root, rest)
    if sources is None:
        return f"`処理: 反映 {rest}` が指すパターンが無い（{PATTERN_DIR}/{rest}.md）"
    if pathcase.fold(ident) not in {pathcase.fold(s) for s in sources}:
        return f"パターン {rest} の `sources` に観測元の ID（{ident}）が無い"
    return ""


def uncommitted(tree_root: str) -> list[str]:
    """記録・パターン・受信箱の置き場にある、未コミットか未追跡の変更のパス。"""
    done = gitcmd.run(
        tree_root,
        ["status", "--porcelain", "-z", "--untracked-files=all", "--no-renames", "--", *WATCHED],
        TIMEOUT,
        raw_paths=True,
    )
    if not done.ok:
        return [f"（git の状態を読めない: {(done.err or done.failure).strip()}）"]
    return sorted(e[3:] for e in done.out.split("\0") if len(e) > 3)
