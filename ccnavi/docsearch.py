"""md の frontmatter の索引を組み、それを引く（`ccnavi --docs`）。

ドキュメントを探すエージェントは、ふだん grep や Glob で本文を舐める。当たるのは行で、
そのファイルが何の文書かは開くまで分からず、よそからの言及も同じ重みで混ざる。
頭の frontmatter（`type` `title` `description` `tags` `keywords`）を索引にしておけば、
「何の文書か」で引ける。形は参考にした運用（`参考/MR-driven-workflow` の
`extract-frontmatter.sh` / `search-frontmatter.sh`）に合わせてある。

## 索引

- 組むのはワークスペースルートと、プロジェクトの置き場の直下の各プロジェクト（`tree.projects`。
  それぞれ別の git）。引くときは全部を合わせ、`concept_id` はワークスペースルートからの相対
  （`projects/<名前>/docs/x`）にする。index.jsonl に書く行はそのツリーのルートからの相対
- 対象は各ツリーの `git ls-files --cached --others --exclude-standard` の `*.md`。消したがまだ
  ステージしていないもの（実体が無いもの）と、シンボリックリンク・ふつうのファイルでない
  ものは飛ばす。ccnavi ディレクトリ（`.ccnavi/`）の下はチケットとマーカーの置き場で、
  文書ではないので載せない
- md が直下にあるディレクトリごとに `index.jsonl`。1 行
  `{"concept_id":…,"directory":…,"frontmatter":{…}|null,"mtime":"YYYY-MM-DDTHH:MM:SS"}`。
  `concept_id` は基準のディレクトリからの相対パスから `.md` を落としたもの
- `concept_id` と `mtime` が既存の行と同じなら、その行を使い回す（読み直さない）。
  書くのは一時ファイルに書いて置き換える形で、中身が同じなら書かない
- **書くのは、git がそこの `index.jsonl` を無視しているときだけ。** 作業ツリーに追跡されて
  いないファイルを撒くと、`git status`・実行後の監視・`worktree remove` のどれにも出る。
  md を持つディレクトリのどれでも無視されていないツリーは、索引の対象外にして引かない
  （案内と標準エラーで名指しする。`.gitignore` は書き換えない）。一部のディレクトリだけが
  無視されていないなら（追跡されている index.jsonl など）、そこは書かずに行だけを組む
- md が全部消えたディレクトリ（追跡はされているが実体が無い）の `index.jsonl` は消す。
  それ以外の経路で残った古い `index.jsonl` は読まない（引くときは、いま md を持つ
  ディレクトリの分だけを見る）

frontmatter は PyYAML の SafeLoader（別名を拒む `flow._Loader`）で読む。読めないもの・
キーと値の並びでないものは `null` にして、索引づくりは止めない。日付などの JSON に
載らない値は文字列にする。

## 引く

同じオプションの繰り返しは OR、違うオプションどうしは AND。大文字小文字は区別しない。
`--type` `--tag` `--keyword` は完全一致（`tags` がスカラーでも並びとして扱う）、
`--path` は `concept_id` への部分一致、`--text` は `concept_id`・`mtime`・frontmatter の
すべてのスカラーの値（キー名は含まない）への部分一致。`--since` / `--until` は `mtime` と
文字列で比べ、日付だけの `--until` は `T23:59:59` を補う。0 件でも終了コードは 0。
"""

from __future__ import annotations

import contextlib
import datetime
import json
import math
import os
import re
import stat
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, TextIO

import yaml

from . import flow, gitcmd, settings, tree

INDEX_NAME = "index.jsonl"
# frontmatter を探すのは頭のこの長さだけ。本文は読まない。
HEAD_LIMIT = 64 * 1024
# これより大きい index.jsonl は読まない（使い回さずに作り直す）。
INDEX_FILE_LIMIT = 16 * 1024 * 1024
# git に与える時間。ls-files と check-ignore の 1 回ずつ。
GIT_TIMEOUT_SECONDS = 10.0

SORTS = ("path", "mtime", "type", "title")
FORMATS = ("table", "path", "detail", "json", "jsonl", "count")
# `--since` / `--until` に受ける形。綴りを誤った値が黙って 0 件になるのを避ける。
_WHEN = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}(:\d{2}(:\d{2})?)?)?$")
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 規約の詳しい文書。ワークスペースに在るときだけ案内に名前を出す。
CONVENTION_DOC = "docs/claude/frontmatter.md"


# --- 索引 ---------------------------------------------------------------------------


@dataclass
class Built:
    """索引を組んだ結果。rows は concept_id の順。"""

    rows: list[dict] = field(default_factory=list)
    # 書いた・消した index.jsonl の数（相対パス）。
    written: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    # 読み直した md の数と、使い回した数。
    parsed: int = 0
    reused: int = 0
    # 書けなかった理由（あれば）。索引そのものは rows に揃っている。
    problems: list[str] = field(default_factory=list)
    # md を持つディレクトリの index.jsonl がどれも無視されていない（索引の対象外）。
    unignored: bool = False
    # 期限を過ぎて、残りのディレクトリを見なかった。
    timed_out: bool = False


class NotARepository(Exception):
    """基準のディレクトリで git の一覧が取れない。"""


def _listed(base: str, timeout: float = GIT_TIMEOUT_SECONDS) -> list[str]:
    """基準のディレクトリの下の、git が挙げる md（基準からの相対、`/` 区切り）。"""
    done = gitcmd.run(
        base,
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.md"],
        timeout=timeout,
    )
    if not done.ok:
        raise NotARepository(done.failure or done.err.strip() or f"git が {done.code} で終わった")
    seen: set[str] = set()
    found = []
    for path in done.out.split("\0"):
        if not path or not path.endswith(".md") or path in seen:
            continue
        seen.add(path)
        found.append(path)
    return found


def _ignored(base: str, paths: list[str], timeout: float = GIT_TIMEOUT_SECONDS) -> set[str]:
    """git が無視する相対パス。問い合わせられなければ空（どれも書かない側に倒れる）。"""
    if not paths:
        return set()
    done = gitcmd.run(
        base,
        ["check-ignore", "-z", "--stdin"],
        timeout=timeout,
        input="\0".join(paths) + "\0",
    )
    # 0 は 1 つ以上が無視、1 はどれも無視されない。それ以外は問い合わせの失敗。
    if done.failure or done.code not in (0, 1):
        return set()
    return {p for p in done.out.split("\0") if p}


def _excluded(rel: str, excluded_dirs: tuple[str, ...]) -> bool:
    return any(rel == d or rel.startswith(d + "/") for d in excluded_dirs)


def _mtime(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(seconds))


def _jsonable(value: Any) -> Any:
    """YAML から読んだ値を JSON に載る形へ。載らないものは文字列にする。"""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def front_matter(raw: bytes) -> dict | None:
    """頭の frontmatter（`---` で始まる YAML）。無い・読めない・並びでないなら None。"""
    text = raw[:HEAD_LIMIT].decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return None
    end = next((i for i, line in enumerate(lines[1:], 1) if line.rstrip() in ("---", "...")), 0)
    if not end:
        return None
    try:
        data = yaml.load("\n".join(lines[1:end]), Loader=flow._Loader)  # noqa: S506 - 別名を拒む SafeLoader
    except Exception:  # noqa: BLE001 - 壊れた frontmatter は null にして索引づくりを止めない
        return None
    if not isinstance(data, dict):
        return None
    return _jsonable(data)


def _read_head(path: str) -> bytes | None:
    """ふつうのファイルの頭。シンボリックリンク・ふつうでないもの・読めないものは None。"""
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags)
        try:
            return os.read(fd, HEAD_LIMIT)
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return None


def _dumps(row: dict) -> str:
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"))


def _read_index(path: str) -> tuple[dict[str, dict], str | None]:
    """既存の index.jsonl の行（concept_id → 行）と、ファイルの中身。無ければ ({}, None)。"""
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return {}, None
        if os.path.getsize(path) > INDEX_FILE_LIMIT:
            return {}, None
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            text = f.read()
    except OSError:
        return {}, None
    rows: dict[str, dict] = {}
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("concept_id"), str):
            rows[row["concept_id"]] = row
    return rows, text


def _write_atomic(path: str, text: str) -> None:
    temporary = f"{path}.tmp.{os.getpid()}"
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            with contextlib.suppress(OSError):
                os.remove(temporary)


def build(
    base: str,
    excluded_dirs: tuple[str, ...] = (),
    refresh: bool = True,
    deadline: float | None = None,
) -> Built:
    """1 つの git の作業ツリー（base）の md の索引を組み、無視されている index.jsonl を差分で書く。

    md を持つディレクトリの `index.jsonl` がどれも無視されていなければ、そのツリーは索引の
    対象外（`unignored`）で、行を返さない。一部だけ無視されていないディレクトリは、行を
    組むが書かない。refresh が偽なら mtime を見ずに既存の行をそのまま使い、何も書かない
    （在る分だけ。行の無い md は読む）。deadline（`time.monotonic` の値）を過ぎたら、残りの
    ディレクトリを見ずに `timed_out` で返す。書いたディレクトリの分は次の回に使い回せる。
    git の一覧が取れなければ `NotARepository`。
    """
    result = Built()
    listed = [p for p in _listed(base, _left(deadline)) if not _excluded(p, excluded_dirs)]
    by_dir: dict[str, list[str]] = {}
    for rel in listed:
        by_dir.setdefault(rel.rpartition("/")[0], []).append(rel)
    if not by_dir:
        return result
    index_rel = {d: (f"{d}/{INDEX_NAME}" if d else INDEX_NAME) for d in by_dir}
    writable = _ignored(base, sorted(index_rel.values()), _left(deadline))
    if not writable:
        result.unignored = True
        return result

    for directory in sorted(by_dir):
        if deadline is not None and time.monotonic() > deadline:
            result.timed_out = True
            break
        index_path = os.path.join(base, *index_rel[directory].split("/"))
        old_rows, old_text = _read_index(index_path)
        rows = _rows_of(base, directory, by_dir[directory], old_rows, refresh, result)
        result.rows.extend(rows)
        if not refresh or index_rel[directory] not in writable:
            continue
        try:
            if not rows:
                # md が全部消えたディレクトリ。無視されている索引だけを消す。
                if old_text is not None:
                    os.remove(index_path)
                    result.removed.append(index_rel[directory])
                continue
            text = "".join(_dumps(r) + "\n" for r in rows)
            if text == old_text:
                continue
            _write_atomic(index_path, text)
            result.written.append(index_rel[directory])
        except OSError as exc:
            result.problems.append(f"{index_rel[directory]} を書けない: {exc}")
    result.rows.sort(key=lambda r: str(r.get("concept_id", "")))
    return result


def _rows_of(
    base: str,
    directory: str,
    names: list[str],
    old_rows: dict[str, dict],
    refresh: bool,
    result: Built,
) -> list[dict]:
    """1 つのディレクトリの行。mtime が既存の行と同じなら使い回す。"""
    rows = []
    for rel in sorted(names):
        full = os.path.join(base, *rel.split("/"))
        concept_id = rel[: -len(".md")]
        if not refresh and concept_id in old_rows:
            rows.append(old_rows[concept_id])
            result.reused += 1
            continue
        try:
            info = os.lstat(full)
        except OSError:
            continue  # 消したがまだステージしていない
        if not stat.S_ISREG(info.st_mode):
            continue
        mtime = _mtime(info.st_mtime)
        old = old_rows.get(concept_id)
        if (
            old is not None
            and old.get("mtime") == mtime
            and old.get("directory") == directory
            and "frontmatter" in old
        ):
            rows.append(old)
            result.reused += 1
            continue
        raw = _read_head(full)
        if raw is None:
            continue
        result.parsed += 1
        rows.append(
            {
                "concept_id": concept_id,
                "directory": directory,
                "frontmatter": front_matter(raw),
                "mtime": mtime,
            }
        )
    return rows


def _left(deadline: float | None) -> float:
    """git に与える時間。期限があれば残りの時間で切る（0 にはしない）。"""
    if deadline is None:
        return GIT_TIMEOUT_SECONDS
    return max(0.1, min(GIT_TIMEOUT_SECONDS, deadline - time.monotonic()))


def _rel(path: str, root: str) -> str:
    """root からの相対（`/` 区切り）。root の外なら空。"""
    try:
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(root))
    except ValueError:
        return ""
    rel = rel.replace(os.sep, "/")
    return "" if rel == "." or rel == ".." or rel.startswith("../") else rel


@dataclass
class Place:
    """索引を組む 1 つの git の作業ツリー。prefix は concept_id の頭に付ける綴り。"""

    name: str
    base: str
    prefix: str
    excluded: tuple[str, ...]


# 案内と標準エラーで、ワークスペース自身を指す名前。
WORKSPACE = "ワークスペース"


def places(conf: settings.Settings, root: str) -> list[Place]:
    """ワークスペースルートと、プロジェクトの置き場の直下のプロジェクト（`tree.projects`）。

    ワークスペースの一覧からは ccnavi ディレクトリとプロジェクトの置き場を外す。
    プロジェクトは別の git なので、そこの md はそのプロジェクトの一覧で拾う。
    プロジェクトの concept_id はワークスペースルートからの相対（`projects/<名前>/…`）にする。
    """
    home = (conf.project_home or settings.DEFAULT_PROJECT_HOME).strip("/")
    own = (home,) if home else ()
    workspace_excluded = list(own)
    projects_rel = _rel(conf.projects, root) if conf.projects else ""
    if projects_rel:
        workspace_excluded.append(projects_rel)
    found = [Place(WORKSPACE, root, "", tuple(workspace_excluded))]
    for project in tree.projects(conf.projects):
        prefix = _rel(project.root, root)
        if not prefix:
            prefix = f"{os.path.basename(os.path.normpath(conf.projects))}/{project.project}"
        found.append(Place(project.project, project.root, prefix, own))
    return found


def _prefixed(row: dict, prefix: str) -> dict:
    if not prefix:
        return row
    moved = dict(row)
    moved["concept_id"] = f"{prefix}/{row.get('concept_id', '')}"
    directory = row.get("directory") or ""
    moved["directory"] = f"{prefix}/{directory}" if directory else prefix
    return moved


@dataclass
class Collected:
    """ワークスペースとプロジェクトを合わせた索引。concept_id はワークスペースルートから。"""

    rows: list[dict] = field(default_factory=list)
    # md を持つが index.jsonl を無視していないので対象外にしたツリーの名前。
    unignored: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    timed_out: bool = False


def collect(
    conf: settings.Settings,
    root: str,
    refresh: bool = True,
    deadline: float | None = None,
) -> Collected:
    """ワークスペースとプロジェクトの索引を新しくして集める。git でないツリーは黙って飛ばす。"""
    result = Collected()
    for place in places(conf, root):
        if deadline is not None and time.monotonic() > deadline:
            result.timed_out = True
            break
        try:
            built = build(place.base, place.excluded, refresh=refresh, deadline=deadline)
        except NotARepository:
            continue
        if built.unignored:
            result.unignored.append(place.name)
        result.timed_out = result.timed_out or built.timed_out
        result.problems.extend(f"{place.prefix}/{p}" if place.prefix else p for p in built.problems)
        result.rows.extend(_prefixed(r, place.prefix) for r in built.rows)
    result.rows.sort(key=lambda r: str(r.get("concept_id", "")))
    return result


UNIGNORED = "は .gitignore に `**/index.jsonl` が無いので索引の対象外"


# --- 引く ---------------------------------------------------------------------------


@dataclass
class Query:
    types: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    since: str = ""
    until: str = ""
    sort: str = "path"
    reverse: bool = False
    limit: int = 0
    format: str = "table"


def problems_of(query: Query) -> list[str]:
    """使い方の誤り。空なら引ける。"""
    found = []
    if query.sort not in SORTS:
        found.append(f"--sort は {' / '.join(SORTS)} のどれか（{query.sort!r} は読めない）")
    if query.format not in FORMATS:
        found.append(f"--format は {' / '.join(FORMATS)} のどれか（{query.format!r} は読めない）")
    for flag, value in (("--since", query.since), ("--until", query.until)):
        if value and not _WHEN.match(value):
            found.append(f"{flag} は YYYY-MM-DD か YYYY-MM-DDTHH:MM:SS で書く（{value!r}）")
    return found


def _fm(row: dict) -> dict:
    front = row.get("frontmatter")
    return front if isinstance(front, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _str(row: dict, key: str) -> str:
    return _text(_fm(row).get(key))


def _arr(row: dict, key: str) -> list[str]:
    value = _fm(row).get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return [_text(v) for v in value]
    return [_text(value)]


def _scalars(value: Any):
    if isinstance(value, dict):
        for v in value.values():
            yield from _scalars(v)
    elif isinstance(value, list):
        for v in value:
            yield from _scalars(v)
    elif value is not None:
        yield _text(value)


def _searchable(row: dict) -> str:
    parts = [_text(row.get("concept_id")), _text(row.get("mtime"))]
    parts.extend(_scalars(_fm(row)))
    return "\n".join(parts).lower()


def _needles(values: list[str]) -> list[str]:
    return [v.lower() for v in values if v]


def _until_end(until: str) -> str:
    """`--until` を、書いた桁の終わりまで延ばす。

    mtime は `YYYY-MM-DDTHH:MM:SS` で、文字列で比べる。`2026-08-05` のままだと
    `2026-08-05T00:00:00` より小さく、その日に書いたものが 1 本も残らない。
    """
    if _DATE_ONLY.match(until):
        return until + "T23:59:59"
    tail = {13: ":59:59", 16: ":59"}.get(len(until), "")
    return until + tail


def matches(row: dict, query: Query) -> bool:
    def exact(needles: list[str], hay: list[str]) -> bool:
        if not needles:
            return True
        lowered = {h.lower() for h in hay}
        return any(n in lowered for n in needles)

    def partial(needles: list[str], hay: str) -> bool:
        return not needles or any(n in hay for n in needles)

    mtime = _text(row.get("mtime"))
    until = _until_end(query.until)
    return (
        exact(_needles(query.types), [_str(row, "type")])
        and exact(_needles(query.tags), _arr(row, "tags"))
        and exact(_needles(query.keywords), _arr(row, "keywords"))
        and partial(_needles(query.paths), _text(row.get("concept_id")).lower())
        and partial(_needles(query.texts), _searchable(row) if query.texts else "")
        and (not query.since or mtime >= query.since)
        and (not until or mtime <= until)
    )


def _sort_key(query: Query):
    def key(row: dict):
        cid = _text(row.get("concept_id"))
        if query.sort == "mtime":
            return (_text(row.get("mtime")), cid)
        if query.sort in ("type", "title"):
            return (_str(row, query.sort), cid)
        return (cid,)

    return key


def dwidth(text: str) -> int:
    """見た目の幅。東アジアの全角（W / F）を 2 と数える。"""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - dwidth(text))


def _one_line(text: str) -> str:
    """表と詳しい形に出す値を 1 行に畳む（改行で行が割れないように）。"""
    return " ".join(text.split()) if ("\n" in text or "\r" in text) else text


def search(rows: list[dict], query: Query) -> tuple[list[dict], int]:
    """(出す行, 絞った後で --limit で切る前の数)。"""
    hits = sorted((r for r in rows if matches(r, query)), key=_sort_key(query))
    if query.reverse:
        hits.reverse()
    matched = len(hits)
    if query.limit > 0:
        hits = hits[: query.limit]
    return hits, matched


def render(out: TextIO, hits: list[dict], matched: int, total: int, fmt: str) -> None:
    if fmt == "count":
        shown = f" shown={len(hits)}" if len(hits) != matched else ""
        out.write(f"matched={matched}{shown} total={total}\n")
    elif fmt == "path":
        for row in hits:
            out.write(_text(row.get("concept_id")) + "\n")
    elif fmt == "jsonl":
        for row in hits:
            out.write(_dumps(row) + "\n")
    elif fmt == "json":
        out.write(json.dumps(hits, ensure_ascii=False, indent=2) + "\n")
    elif fmt == "detail":
        for row in hits:
            out.write(
                f"- {_text(row.get('concept_id'))}\n"
                f"  type       : {_one_line(_str(row, 'type'))}\n"
                f"  title      : {_one_line(_str(row, 'title'))}\n"
                f"  description: {_one_line(_str(row, 'description'))}\n"
                f"  tags       : {_one_line(', '.join(_arr(row, 'tags')))}\n"
                f"  keywords   : {_one_line(', '.join(_arr(row, 'keywords')))}\n"
                f"  mtime      : {_text(row.get('mtime'))}\n"
            )
    else:
        types = [_one_line(_str(r, "type")) for r in hits]
        ids = [_text(r.get("concept_id")) for r in hits]
        tw = max((dwidth(t) for t in types), default=0)
        cw = max((dwidth(c) for c in ids), default=0)
        for row, kind, cid in zip(hits, types, ids, strict=True):
            line = f"{_pad(kind, tw)}  {_pad(cid, cw)}  {_one_line(_str(row, 'title'))}"
            out.write(line.rstrip() + "\n")


# --- 入口 ---------------------------------------------------------------------------

# SessionStart で索引を新しくするのに使ってよい時間（秒）。過ぎたら残りは次の回に回す。
# 使い回しが効いていれば数百本の md でも 1 秒に届かない。初めての大きなプロジェクトだけが
# ここに当たり、書けたディレクトリの分は次のセッションで使い回される。
START_SECONDS = 3.0


def run(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    query: Query,
    refresh: bool = True,
) -> int:
    """`ccnavi --docs`。ワークスペースと、索引の対象になるプロジェクトの md を引く。0 件でも 0。"""
    problems = problems_of(query)
    if problems:
        for problem in problems:
            stderr.write(f"ccnavi: {problem}\n")
        return 1
    found = collect(conf, root, refresh=refresh)
    for problem in found.problems:
        stderr.write(f"ccnavi: 索引を残せなかった（引くのは続ける）: {problem}\n")
    if found.unignored:
        stderr.write(f"ccnavi: {', '.join(found.unignored)} {UNIGNORED}\n")
    hits, matched = search(found.rows, query)
    render(stdout, hits, matched, len(found.rows), query.format)
    if query.format in ("table", "detail"):
        shown = f" shown={len(hits)}" if len(hits) != matched else ""
        stderr.write(f"matched={matched}{shown} total={len(found.rows)}\n")
    return 0


def _command(conf: settings.Settings, root: str) -> str:
    """案内に書く ccnavi の綴り。設定が相対ならワークスペースルートから書く。"""
    path = conf.bin
    if path and not os.path.isabs(path):
        path = os.path.realpath(os.path.join(root, path))
    return settings.bin_command(path)


def at_start(conf: settings.Settings, root: str) -> str:
    """SessionStart。ワークスペースとプロジェクトの索引を差分で新しくし、引き方の案内を返す。

    何が起きても開始は止めない。md が 1 本も無い・何かが壊れたときは黙る（空を返す）。
    索引の対象外にしたツリーがあれば 1 行添える。期限（START_SECONDS）を過ぎたら残りは
    新しくしないが、案内は出す（`--docs` は引く前に自分で新しくする）。
    """
    try:
        found = collect(conf, root, deadline=time.monotonic() + START_SECONDS)
        return notice(conf, root, found)
    except Exception:  # noqa: BLE001 - 索引は案内の足しで、セッションの開始を止めない
        return ""


def notice(conf: settings.Settings, root: str, found: Collected) -> str:
    unignored = (
        f"{', '.join(found.unignored)} {UNIGNORED}（ccnavi は .gitignore を書き換えない）"
        if found.unignored
        else ""
    )
    if not found.rows:
        return f"[ccnavi] md の索引（--docs）: {unignored}" if unignored else ""
    command = _command(conf, root)
    lines = [
        f"[ccnavi] ドキュメント（*.md）を探すときは、grep・Glob より先に '{command} --docs' で"
        " frontmatter の索引を引く（ワークスペースとプロジェクトを横断。パスはワークスペースルート"
        "から）。grep は本文中の文字列を探すときか、0 件だったときに使う。",
        "  絞り込み: --type / --tag / --keyword <値>（完全一致）、--path <部分>（パス）、"
        "--text <部分>（パス・更新日時・frontmatter の値）、--since / --until <YYYY-MM-DD>。"
        "同じものの繰り返しは OR、違うものどうしは AND。大文字小文字は区別しない",
        "  並べ方と形: --sort path|mtime|type|title、-r（逆順）、--limit <N>、"
        "--format table|path|detail|json|jsonl|count",
        "  md を書くときは頭に frontmatter を付ける。type は必須、title・description・"
        "tags（kebab-case で 2〜4 個）・keywords は推奨。",
    ]
    if os.path.isfile(os.path.join(root, *CONVENTION_DOC.split("/"))):
        lines[-1] += f"type の値と詳しい決まりは {CONVENTION_DOC}"
    if unignored:
        lines.append(f"  {unignored}")
    return "\n".join(lines)
