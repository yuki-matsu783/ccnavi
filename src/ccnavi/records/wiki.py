"""学びの蓄積の wiki（`docs/wiki/`）のパターンと採否の記録を読み、パターンの状態を決める。

読むのは 2 種類。

- パターン（`docs/wiki/patterns/<名前>.md`）。frontmatter の `target`（反映先の種類）と、
  `sources`（観測元の ID の一覧）を持つ。重複を除いた `sources` の数が「観測元の数」
- 採否の記録（`docs/wiki/impact/<提案>.md`）。ある提案がどのパターンを動機にし（`patterns`。
  パターン名から、そのときの観測元の数）、どう決まったか（`status`）を持つ

frontmatter が壊れたファイルは、その名前と理由を `Wiki.broken` に積み、数えない。
壊れた採否の記録は、無いものとして状態の決定に入れない。

パターンの状態（`pattern_status`）は純粋な関数で、ファイルも時計も見ない。

1. そのパターンを `patterns` に含む採否の記録に `承認待ち` が 1 つでもあれば「提案中」
2. それ以外は、`decided_at` が最も新しい採否の記録で決める。同じ日時なら `proposal` の名前順で
   後のもの（日時はタイムゾーンを揃えて比べる）。`承認`・`却下` なら「決着」。
   `延期` は、今の観測元の数がその記録の `patterns` にある数より多ければ「候補」、
   多くなければ「決着」
3. 採否の記録が 1 つも無ければ「候補」

承認待ちの記録は規則 2 の「最も新しい記録」の選び方に入れない（規則 1 が先に処理する）。
名前は大文字小文字を区別せず比べる。ファイルは読むだけで、書かない。
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from ..infra import yamlread

PATTERNS_DIR = "docs/wiki/patterns"
IMPACT_DIR = "docs/wiki/impact"

TARGETS = ("rule", "risk", "phase", "template", "skill", "docs")
TYPE = "learning-wiki"

PENDING = "承認待ち"
APPROVED = "承認"
REJECTED = "却下"
DEFERRED = "延期"
DECISIONS = (PENDING, APPROVED, REJECTED, DEFERRED)

PROPOSING = "提案中"
CANDIDATE = "候補"
SETTLED = "決着"

# frontmatter を探すのは、頭のこの長さの中だけ。
HEAD_LIMIT = 64 * 1024


@dataclass(frozen=True)
class Pattern:
    """パターン 1 本。`name` はファイル名から `.md` を除いたもの。"""

    name: str
    target: str
    # 重複を除いた観測元の ID（大文字小文字だけが違うものは同じ ID とみなす。残すのは最初の綴り）。
    sources: tuple[str, ...]
    title: str = ""

    @property
    def count(self) -> int:
        return len(self.sources)


@dataclass(frozen=True)
class Impact:
    """採否の記録 1 本。"""

    proposal: str
    target: str
    files: tuple[str, ...]
    # 動機のパターン名（比べるための鍵ではなく書かれた綴り）から、そのときの観測元の数。
    patterns: Mapping[str, int]
    status: str
    reason: str
    # タイムゾーン付きの日時。承認待ちは空（None）のことがある。
    decided_at: datetime.datetime | None


@dataclass(frozen=True)
class Broken:
    """frontmatter が壊れたファイル。`file` は wiki を含むツリーからの相対パス。"""

    file: str
    kind: str
    problem: str


@dataclass
class Wiki:
    patterns: list[Pattern] = field(default_factory=list)
    impacts: list[Impact] = field(default_factory=list)
    broken: list[Broken] = field(default_factory=list)
    # `docs/wiki/` の下にパターンか採否の記録のディレクトリが在ったか。
    present: bool = False


def key(name: str) -> str:
    """名前を比べるための鍵。大文字小文字を区別しない。"""
    return name.strip().casefold()


# --- frontmatter ---


def front_matter(text: str) -> dict | str:
    """頭の frontmatter（`---` で囲んだ YAML）の辞書。読めなければ理由の文字列。"""
    lines = text[:HEAD_LIMIT].lstrip("﻿").splitlines()
    if not lines or lines[0].rstrip() != "---":
        return "frontmatter が無い"
    end = next((i for i, line in enumerate(lines[1:], 1) if line.rstrip() in ("---", "...")), 0)
    if not end:
        return "frontmatter が閉じていない"
    try:
        data = yamlread.safe_load("\n".join(lines[1:end]))
    except Exception as exc:  # noqa: BLE001 - 壊れた YAML は理由にして名指しする
        return f"frontmatter を YAML として読めない ({type(exc).__name__})"
    if not isinstance(data, dict):
        return "frontmatter が対応表（key: value）でない"
    return data


def _text(data: dict, name: str, required: bool = False) -> tuple[str, str]:
    """文字列の欄。`(値, 問題)`。無い・None は空文字。"""
    value = data.get(name)
    if value is None:
        return "", f"{name} が無い" if required else ""
    if not isinstance(value, str):
        return "", f"{name} が文字列でない"
    if required and not value.strip():
        return "", f"{name} が空"
    return value.strip(), ""


def _strings(data: dict, name: str) -> tuple[list[str], str]:
    """文字列の一覧の欄。`(値, 問題)`。無い・None は空の一覧。"""
    value = data.get(name)
    if value is None:
        return [], ""
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        return [], f"{name} が空でない文字列の一覧でない"
    return [v.strip() for v in value], ""


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if key(value) not in seen:
            seen.add(key(value))
            out.append(value)
    return tuple(out)


def parse_pattern(name: str, text: str) -> Pattern | str:
    """パターンの frontmatter を読む。壊れていれば理由の文字列。"""
    data = front_matter(text)
    if isinstance(data, str):
        return data
    if data.get("type") != TYPE:
        return f"type が {TYPE} でない"
    target = data.get("target")
    if target not in TARGETS:
        return f"target が {' '.join(TARGETS)} のどれでもない"
    sources, problem = _strings(data, "sources")
    if problem:
        return problem
    if not sources:
        return "sources が空（観測元が無い）"
    title, problem = _text(data, "title")
    for other in ("description",):
        problem = problem or _text(data, other)[1]
    for other in ("tags", "keywords"):
        problem = problem or _strings(data, other)[1]
    if problem:
        return problem
    return Pattern(name=name, target=target, sources=_unique(sources), title=title)


def _when(value: object) -> datetime.datetime | str:
    """`decided_at`。タイムゾーン付きの日時か、読めない理由。"""
    if isinstance(value, datetime.datetime):
        when = value
    elif isinstance(value, str):
        try:
            when = datetime.datetime.fromisoformat(value.strip())
        except ValueError:
            return "decided_at が日時として読めない"
    else:
        return "decided_at が日時でない"
    if when.tzinfo is None or when.utcoffset() is None:
        return "decided_at にタイムゾーンが無い"
    return when


def _counts(value: object) -> tuple[dict[str, int], str]:
    if value is None:
        return {}, ""
    if not isinstance(value, dict):
        return {}, "patterns がパターン名から観測元の数への対応表でない"
    out: dict[str, int] = {}
    for name, count in value.items():
        if not isinstance(name, str) or not name.strip():
            return {}, "patterns のパターン名が文字列でない"
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return {}, f"patterns の {name} の数が 0 以上の整数でない"
        out[name.strip()] = count
    return out, ""


def parse_impact(text: str) -> Impact | str:
    """採否の記録の frontmatter を読む。壊れていれば理由の文字列。"""
    data = front_matter(text)
    if isinstance(data, str):
        return data
    proposal, problem = _text(data, "proposal", required=True)
    if problem:
        return problem
    target = data.get("target")
    if target not in TARGETS:
        return f"target が {' '.join(TARGETS)} のどれでもない"
    files, problem = _strings(data, "files")
    if problem:
        return problem
    patterns, problem = _counts(data.get("patterns"))
    if problem:
        return problem
    status = data.get("status")
    if status not in DECISIONS:
        return f"status が {' '.join(DECISIONS)} のどれでもない"
    reason, problem = _text(data, "reason")
    if problem:
        return problem
    raw = data.get("decided_at")
    decided: datetime.datetime | None = None
    if raw is not None and raw != "":
        when = _when(raw)
        if isinstance(when, str):
            return when
        decided = when
    elif status != PENDING:
        return "decided_at が無い（承認待ちのほかは日時が要る）"
    return Impact(
        proposal=proposal,
        target=target,
        files=tuple(files),
        patterns=patterns,
        status=status,
        reason=reason,
        decided_at=decided,
    )


def build(
    pattern_texts: Mapping[str, str],
    impact_texts: Mapping[str, str],
    present: bool = True,
) -> Wiki:
    """ファイル名（`.md` を除く）から本文への対応を作り、`Wiki` を組む。

    壊れたファイルは `broken` に積む。並びは名前順で、読み順によらず同じ答えにする。
    """
    wiki = Wiki(present=present)
    seen: dict[str, str] = {}
    for name in sorted(pattern_texts):
        where = f"{PATTERNS_DIR}/{name}.md"
        if key(name) in seen:
            wiki.broken.append(
                Broken(where, "pattern", f"{seen[key(name)]} と大文字小文字だけが違う同名")
            )
            continue
        seen[key(name)] = name
        parsed = parse_pattern(name, pattern_texts[name])
        if isinstance(parsed, str):
            wiki.broken.append(Broken(where, "pattern", parsed))
        else:
            wiki.patterns.append(parsed)
    for name in sorted(impact_texts):
        parsed = parse_impact(impact_texts[name])
        if isinstance(parsed, str):
            wiki.broken.append(Broken(f"{IMPACT_DIR}/{name}.md", "impact", parsed))
        else:
            wiki.impacts.append(parsed)
    return wiki


# --- パターンの状態 ---


def _mentions(impact: Impact, name: str) -> bool:
    return any(key(n) == key(name) for n in impact.patterns)


def _count_in(impact: Impact, name: str) -> int:
    return max(c for n, c in impact.patterns.items() if key(n) == key(name))


def _order(impact: Impact) -> tuple[datetime.datetime, str]:
    # 承認待ち以外は decided_at を持つ。比べるときはタイムゾーンを揃える。
    when = impact.decided_at or datetime.datetime.min.replace(tzinfo=datetime.UTC)
    return when.astimezone(datetime.UTC), key(impact.proposal)


def pattern_status(name: str, count: int, impacts: Iterable[Impact]) -> str:
    """パターンの状態（`提案中`・`候補`・`決着`）。

    `count` は今の観測元の数。`impacts` は壊れていない採否の記録の全部（このパターンに関係の無い
    ものが混ざっていてよい）。
    """
    mine = [i for i in impacts if _mentions(i, name)]
    if any(i.status == PENDING for i in mine):
        return PROPOSING
    decided = [i for i in mine if i.decided_at is not None]
    if not decided:
        return CANDIDATE
    last = max(decided, key=_order)
    if last.status == DEFERRED:
        return CANDIDATE if count > _count_in(last, name) else SETTLED
    return SETTLED


@dataclass(frozen=True)
class Row:
    name: str
    target: str
    status: str
    count: int
    title: str = ""

    def reached(self, threshold: int) -> bool:
        """観測元の数が閾値以上か。"""
        return self.count >= threshold


def rows(wiki: Wiki) -> list[Row]:
    """パターンごとの状態と観測元の数（名前順）。"""
    return [
        Row(p.name, p.target, pattern_status(p.name, p.count, wiki.impacts), p.count, p.title)
        for p in sorted(wiki.patterns, key=lambda p: key(p.name))
    ]


def candidates(wiki: Wiki) -> list[Row]:
    return [r for r in rows(wiki) if r.status == CANDIDATE]


def pending(wiki: Wiki) -> list[Impact]:
    """`承認待ち` の採否の記録（提案の名前順）。"""
    return sorted((i for i in wiki.impacts if i.status == PENDING), key=lambda i: key(i.proposal))


# 観測元の数の閾値と、通知する候補の件数の既定。
# 書かれた値は、settings の LEARNING_SOURCES_ENV と LEARNING_NOTICE_COUNT_ENV から読む。
DEFAULT_SOURCES = 3
DEFAULT_NOTICE_COUNT = 5


def _positive(written: str, default: int) -> int:
    try:
        n = int(written.strip())
    except ValueError:
        return default
    return n if n >= 1 else default


def sources_threshold(written: str) -> int:
    """書かれた閾値。読めない値と 1 未満は既定（3）に戻す。"""
    return _positive(written, DEFAULT_SOURCES)


def notice_count(written: str) -> int:
    """書かれた通知の件数。読めない値と 1 未満は既定（5）に戻す。"""
    return _positive(written, DEFAULT_NOTICE_COUNT)
