"""md の frontmatter の索引を組み、それを引く（`ccnavi --docs`）。

ドキュメントを探すエージェントは、ふだん grep や Glob で本文を端から探す。当たるのは行で、
そのファイルが何の文書かは開くまで分からず、よそからの言及も同じ重みで混ざる。
頭の frontmatter（`type` `title` `description` `tags` `keywords`）を索引にしておけば、
「何の文書か」で引ける。形は参考にした運用（`参考/MR-driven-workflow` の
`extract-frontmatter.sh` / `search-frontmatter.sh`）に合わせてある。

## 索引

- 組むのはワークスペースルートと、プロジェクトの置き場の直下の各プロジェクト（`tree.projects`。
  それぞれ別の git）。どれも `.git` をルートに持つものだけ。引くときは全部を合わせ、
  `concept_id` はワークスペースルートからの相対（`projects/<名前>/docs/x`）にする。
  index.jsonl に書く行はそのツリーのルートからの相対
- 対象は各ツリーの `git ls-files --cached --others --exclude-standard` の `*.md`。消したがまだ
  ステージしていないもの（実体が無いもの）と、シンボリックリンク・ふつうのファイルでない
  ものは飛ばす。ccnavi ディレクトリ（`.ccnavi/`）の下はチケットとマーカーの置き場で、
  文書ではないので載せない。実体のパスがツリーの外に出るディレクトリ（ジャンクションや
  リンク越し）は読みも書きもしない
- md が直下にあるディレクトリごとに `index.jsonl`。1 行
  `{"concept_id":…,"directory":…,"frontmatter":{…}|null,"mtime":"YYYY-MM-DDTHH:MM:SS"}`。
  `concept_id` はツリーのルートからの相対パスから `.md` を落としたもの。ルート直下の
  `directory` は `.`
- `concept_id` と `mtime` が既存の行と同じなら、その行を使い回す（読み直さない）。
  書くのは一時ファイルに書いて置き換える形で、中身が同じなら書かない。一時ファイルは
  git のディレクトリ（`.git/`）の中に排他で作る（作業ツリーの `git status` に出さない）。
  `.git` が別のファイルシステムなら、そのディレクトリの下の `.ccnavi-tmp-*/index.jsonl`
  （git に無視されることを確かめてから）に作る
- 期限（SessionStart）を過ぎたら md を読むのをやめるが、どのディレクトリも読めた分までは書く
- **書くのは、git がそこの `index.jsonl` を無視しているときだけ。** 作業ツリーに追跡されて
  いないファイルを置くと、`git status`・実行後の監視・`worktree remove` のどれにも出る。
  md を持つディレクトリのどれでも無視されていないツリーは、索引の対象外にして引かない
  （案内と標準エラーで名指しする。`.gitignore` は書き換えない）。一部のディレクトリだけが
  無視されていないなら（追跡されている index.jsonl など）、そこは書かずに行だけを組む
- **ccnavi が書いた形でない index.jsonl は、上書きも削除もしない。** 空か、空でない行が
  全部この 4 つの鍵を持つ行として読めるときだけを ccnavi のものとみなす。よその道具が
  同じ名前で置いたファイルを壊さない
- md が全部消えたディレクトリ（追跡はされているが実体が無い）の `index.jsonl` は、上の条件で
  消す。それ以外の経路で残った古い `index.jsonl` は読まない
- git への問い合わせの失敗（git が無い・期限切れ・壊れたリポジトリ）は「git の外」とも
  「無視されていない」とも別に扱う

frontmatter は PyYAML の SafeLoader（別名を拒む `flow._Loader`）で読む。読めないもの・
マッピングでないもの・JSON に書けないものは `null` にして、索引づくりは止めない。
日付などの JSON に載らない値は文字列にする。読むのはファイルの頭の 64 KiB まで（UTF-8）。

## 引く

同じオプションの繰り返しは OR、違うオプションどうしは AND。大文字小文字は区別せず、
文字列は NFC に揃えてから比べる。`--type` `--tag` `--keyword` は完全一致（`tags` が
スカラーでもリストとして扱う）、`--path` は `concept_id` への部分一致、`--text` は
`concept_id`・`mtime`・frontmatter のすべてのスカラーの値（キー名は含まない）への部分一致。
`--since` / `--until` は `mtime` と文字列で比べ、`--until` は書いた桁の終わりまで延ばす
（日付だけなら `T23:59:59`、`THH` なら `:59:59`、`THH:MM` なら `:59`）。0 件でも終了コードは 0。
"""

from __future__ import annotations

import contextlib
import datetime
import errno
import itertools
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
# 最初に読む長さ。frontmatter が無いか、ここで閉じていれば続きは読まない。
FIRST_READ = 4 * 1024
# これより大きい index.jsonl は読まない（ccnavi のものとみなさず、触らない）。
INDEX_FILE_LIMIT = 16 * 1024 * 1024
# git に与える時間。ls-files と check-ignore の 1 回ずつ。
GIT_TIMEOUT_SECONDS = 10.0
# 一時ファイルの名前（git のディレクトリの中）。打ち切りの残骸はこの古さを過ぎたら消す。
TMP_PREFIX = "ccnavi-index-"
TMP_SUFFIX = ".tmp"
TMP_STALE_SECONDS = 10 * 60
# 作業ツリーの側に置く一時ファイルのディレクトリの名前（`.git` が別のファイルシステムのとき）。
# 中の `index.jsonl` は `**/index.jsonl` で無視される。
LOCAL_TMP_PREFIX = ".ccnavi-tmp-"
# frontmatter と索引の行の入れ子の深さの上限。`--format json` の字下げは再帰で書くので、
# これより深いものは書けないものとして扱う。
MAX_DEPTH = 32
# 索引の 1 行が持つ鍵。
ROW_KEYS = ("concept_id", "directory", "frontmatter", "mtime")
# ルート直下のファイルの `directory`（参考と同じ）。
ROOT_DIRECTORY = "."

SORTS = ("path", "mtime", "type", "title")
FORMATS = ("table", "path", "detail", "json", "jsonl", "count")
# `--since` / `--until` に受ける形と、その形の読み方。書き方を誤った値が気づかないうちに
# 0 件になるのを避ける。
_WHEN_FORMATS = {
    10: "%Y-%m-%d",
    13: "%Y-%m-%dT%H",
    16: "%Y-%m-%dT%H:%M",
    19: "%Y-%m-%dT%H:%M:%S",
}
_WHEN = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}(:\d{2}(:\d{2})?)?)?$")
# frontmatter を閉じる行。
_CLOSING = re.compile(rb"\n(?:---|\.\.\.)[ \t]*\r?(?:\n|$)")

# 規約の詳しい文書。ワークスペースに在るときだけ案内に名前を出す。
CONVENTION_DOC = "docs/claude/frontmatter.md"

_counter = itertools.count()


# --- 索引 ---------------------------------------------------------------------------


@dataclass
class Built:
    """索引を組んだ結果。rows は concept_id の順。"""

    rows: list[dict] = field(default_factory=list)
    # 書いた・消した index.jsonl（ツリーのルートからの相対）。
    written: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    # ccnavi が書いた形でないので触らなかった index.jsonl（ツリーのルートからの相対）。
    foreign: list[str] = field(default_factory=list)
    # 読み直した md の数と、使い回した数。
    parsed: int = 0
    reused: int = 0
    # 書けなかった・読まなかった理由（あれば）。索引は組めた分だけ rows にある。
    problems: list[str] = field(default_factory=list)
    # md を持つディレクトリの index.jsonl がどれも無視されていない（索引の対象外）。
    unignored: bool = False
    # md を持ち、索引の対象になるツリーだった（打ち切りで rows が空でも、引けば当たるもの）。
    indexable: bool = False
    # 期限を過ぎて、読み直しの要る md を読み残した。
    timed_out: bool = False


class NotARepository(Exception):
    """基準のディレクトリが git のルートではない（`.git` が無い）。"""


class GitFailed(Exception):
    """git への問い合わせに失敗した（git が無い・期限切れ・壊れたリポジトリ）。"""


class _TimeUp(Exception):
    pass


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _failure(done: gitcmd.Done) -> str:
    if done.missing:
        return "git が見つからない"
    if done.timed_out:
        return "git が期限までに終わらなかった"
    if done.failure:
        return done.failure
    return (done.err.strip().splitlines() or [f"git が {done.code} で終わった"])[-1]


def _listed(base: str, timeout: float = GIT_TIMEOUT_SECONDS) -> list[str]:
    """基準のディレクトリの下の、git が挙げる md（基準からの相対、`/` 区切り）。"""
    if not os.path.exists(os.path.join(base, ".git")):
        raise NotARepository(base)
    # pathspec（`*.md`）は渡さず、ここで `.md` を選ぶ。利用者の環境に `GIT_LITERAL_PATHSPECS`
    # などがあると `*.md` の読み方が変わる。
    done = gitcmd.run(
        base,
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        timeout=timeout,
        plain_pathspecs=True,
    )
    if not done.ok:
        raise GitFailed(_failure(done))
    seen: set[str] = set()
    found = []
    for path in done.out.split("\0"):
        if not path or not path.endswith(".md") or path in seen:
            continue
        seen.add(path)
        found.append(path)
    return found


def _ignored(base: str, paths: list[str], timeout: float = GIT_TIMEOUT_SECONDS) -> set[str]:
    """git が無視する相対パス。問い合わせに失敗したら `GitFailed`。"""
    if not paths:
        return set()
    # check-ignore は渡したパスを pathspec として読み、`:` で始まるもの（`:(exclude)x/` という
    # 名前のディレクトリなど）を magic として 128 で止まる。`--literal-pathspecs` も受けないので、
    # 頭に `./` を付けて magic と読ませない。出てくるパスも `./` 付きなので外して返す。
    done = gitcmd.run(
        base,
        ["check-ignore", "-z", "--stdin"],
        timeout=timeout,
        input="".join(f"./{p}\0" for p in paths),
        plain_pathspecs=True,
    )
    # 0 は 1 つ以上が無視、1 はどれも無視されない。それ以外は問い合わせの失敗。
    if done.failure or done.code not in (0, 1):
        raise GitFailed(_failure(done))
    return {p.removeprefix("./") for p in done.out.split("\0") if p}


def _git_dir(base: str) -> str:
    """一時ファイルを置く git のディレクトリ。`.git` がファイル（submodule）なら指す先。"""
    dot_git = os.path.join(base, ".git")
    if os.path.isdir(dot_git):
        return dot_git
    try:
        with open(dot_git, encoding="utf-8") as f:
            line = f.readline().strip()
    except (OSError, ValueError):
        return ""
    if not line.startswith("gitdir:"):
        return ""
    target = line[len("gitdir:") :].strip()
    if not os.path.isabs(target):
        target = os.path.join(base, target)
    return target if os.path.isdir(target) else ""


def _sweep(tmp_dir: str) -> None:
    """打ち切りで残った古い一時ファイルを消す。"""
    try:
        names = os.listdir(tmp_dir)
    except OSError:
        return
    now = time.time()
    for name in names:
        if not (name.startswith(TMP_PREFIX) and name.endswith(TMP_SUFFIX)):
            continue
        path = os.path.join(tmp_dir, name)
        with contextlib.suppress(OSError):
            info = os.lstat(path)
            if stat.S_ISREG(info.st_mode) and now - info.st_mtime > TMP_STALE_SECONDS:
                os.remove(path)


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
    """頭の frontmatter（`---` で始まる YAML）。

    無い・読めない・マッピングでない・書けないなら None。
    """
    try:
        text = raw[:HEAD_LIMIT].decode("utf-8-sig", errors="replace")
        lines = text.splitlines()
        if not lines or lines[0].rstrip() != "---":
            return None
        end = next((i for i, line in enumerate(lines[1:], 1) if line.rstrip() in ("---", "...")), 0)
        if not end:
            return None
        data = yaml.load("\n".join(lines[1:end]), Loader=flow._Loader)  # noqa: S506 - 別名を拒む SafeLoader
        if not isinstance(data, dict):
            return None
        data = _jsonable(data)
        _check_json(data)  # 書けるかを試す（桁の多すぎる整数・深すぎる入れ子など）
        return data
    except Exception:  # noqa: BLE001 - 壊れた frontmatter は null にして索引づくりを止めない
        return None


def _read_head(path: str) -> bytes | None:
    """ふつうのファイルの頭。シンボリックリンク・ふつうでないもの・読めないものは None。

    まず FIRST_READ だけ読み、frontmatter が無いか閉じていればそこで止める。続きが要る
    ときだけ HEAD_LIMIT まで読み足す。
    """
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags)
        try:
            data = os.read(fd, FIRST_READ)
            if not data.removeprefix(b"\xef\xbb\xbf").startswith(b"---"):
                return data
            while len(data) < HEAD_LIMIT and not _CLOSING.search(data, 3):
                chunk = os.read(fd, min(16 * 1024, HEAD_LIMIT - len(data)))
                if not chunk:
                    break
                data += chunk
            return data
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return None


def _dumps(row: Any) -> str:
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"))


def _deep(value: Any) -> bool:
    """入れ子が MAX_DEPTH より深いか。再帰しない。"""
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            children = list(item.values())
        elif isinstance(item, list):
            children = item
        else:
            continue
        if depth > MAX_DEPTH:
            return True
        stack.extend((child, depth + 1) for child in children)
    return False


def _check_json(value: Any) -> None:
    """どの出力の形（`--format json` の字下げを含む）でも書けるか。書けなければ ValueError。

    字下げのある `json.dumps` は C の速い経路を使わず再帰するので、詰めた形で書けた行でも
    深い入れ子で RecursionError になる。NaN・Infinity は JSON に無い表記を出すので弾く。
    """
    if _deep(value):
        raise ValueError("入れ子が深すぎる")
    json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _valid_row(row: Any) -> bool:
    """ccnavi が書いた形の 1 行か。"""
    if not (
        isinstance(row, dict)
        and all(key in row for key in ROW_KEYS)
        and isinstance(row["concept_id"], str)
        and isinstance(row["directory"], str)
        and isinstance(row["mtime"], str)
        and (row["frontmatter"] is None or isinstance(row["frontmatter"], dict))
    ):
        return False
    try:
        _check_json(row)
    except (ValueError, RecursionError):
        return False
    return True


ABSENT, OURS, FOREIGN = "absent", "ours", "foreign"


def _read_index(path: str) -> tuple[dict[str, dict], str | None, str]:
    """既存の index.jsonl の行（concept_id → 行）、中身、持ち主（ABSENT / OURS / FOREIGN）。

    空か、空でない行が全部 ccnavi の形の行なら OURS。リンク・ふつうでないもの・大きすぎる
    もの・UTF-8 でないもの・形の違う行を持つものは FOREIGN で、上書きも削除もしない。
    """
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return {}, None, ABSENT
    except OSError:
        return {}, None, FOREIGN
    if not stat.S_ISREG(info.st_mode) or info.st_size > INDEX_FILE_LIMIT:
        return {}, None, FOREIGN
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8")
    except (OSError, ValueError):
        return {}, None, FOREIGN
    rows: dict[str, dict] = {}
    # 割るのは `\n` だけ。`splitlines` は U+2028・U+2029・U+0085 でも割るが、JSON の文字列は
    # それらを生のまま持てる（ensure_ascii=False で書く）ので、自分の行を途中で割ってしまう。
    for line in text.split("\n"):
        line = line.removesuffix("\r")
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            return {}, text, FOREIGN
        if not _valid_row(row):
            return {}, text, FOREIGN
        rows[row["concept_id"]] = row
    return rows, text, OURS


def _write_atomic(path: str, text: str, temporary: str) -> None:
    """temporary に排他で一時ファイルを作って書き、path へ置き換える。"""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    fd = os.open(temporary, flags, 0o644)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8"))
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            with contextlib.suppress(OSError):
                os.remove(temporary)


def _sweep_beside(parent: str) -> None:
    """作業ツリーの側に残った古い一時ディレクトリを消す。

    消すのは名前が LOCAL_TMP_PREFIX で始まり、リンクでないディレクトリで、古く、中身が
    ふつうのファイルの `index.jsonl` だけ（か空）のものに限る。ほかのものは他人のものとして残す。
    """
    try:
        names = os.listdir(parent)
    except OSError:
        return
    now = time.time()
    for name in names:
        if not name.startswith(LOCAL_TMP_PREFIX):
            continue
        holder = os.path.join(parent, name)
        with contextlib.suppress(OSError):
            info = os.lstat(holder)
            if not stat.S_ISDIR(info.st_mode) or now - info.st_mtime <= TMP_STALE_SECONDS:
                continue
            inside = os.listdir(holder)
            if any(entry != INDEX_NAME for entry in inside):
                continue
            if inside:
                left = os.path.join(holder, INDEX_NAME)
                if not stat.S_ISREG(os.lstat(left).st_mode):
                    continue
                os.remove(left)
            os.rmdir(holder)


class _Writer:
    """index.jsonl を置き換える。一時ファイルはふだん git のディレクトリに作る。

    `.git` が作業ツリーと別のファイルシステムにあると `os.replace` が EXDEV で落ちるので、
    そのときは作業ツリーの同じディレクトリの下に `.ccnavi-tmp-*/index.jsonl` を作って置き換える。
    そのパスが git に無視されることを先に確かめ、`git status` を汚さない。
    """

    def __init__(self, base: str, tmp_dir: str, deadline: float | None) -> None:
        self.base = base
        self.tmp_dir = tmp_dir
        self.deadline = deadline
        self.beside = False
        with contextlib.suppress(OSError):
            self.beside = os.stat(tmp_dir).st_dev != os.stat(base).st_dev

    def write(self, index_path: str, index_rel: str, text: str) -> None:
        if not self.beside:
            name = f"{TMP_PREFIX}{os.getpid()}-{next(_counter)}{TMP_SUFFIX}"
            try:
                _write_atomic(index_path, text, os.path.join(self.tmp_dir, name))
                return
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    raise
                self.beside = True
        self._write_beside(index_path, index_rel, text)

    def _write_beside(self, index_path: str, index_rel: str, text: str) -> None:
        parent = os.path.dirname(index_path)
        _sweep_beside(parent)
        name = f"{LOCAL_TMP_PREFIX}{os.getpid()}-{next(_counter)}"
        directory = index_rel.rpartition("/")[0]
        probe = f"{directory}/{name}/{INDEX_NAME}" if directory else f"{name}/{INDEX_NAME}"
        try:
            ignored = probe in _ignored(self.base, [probe], _left(self.deadline))
        except GitFailed as exc:
            raise OSError(f"一時ファイルの置き場を git に確かめられない: {exc}") from exc
        if not ignored:
            raise OSError(f"一時ファイルの置き場 {probe} が git に無視されないので書かない")
        holder = os.path.join(parent, name)
        os.mkdir(holder, 0o700)  # 在れば落ちる（排他）
        try:
            _write_atomic(index_path, text, os.path.join(holder, INDEX_NAME))
        finally:
            with contextlib.suppress(OSError):
                os.rmdir(holder)


def _under(path: str, base_real: str) -> bool:
    """path の実体が base_real 自身かその下か。"""
    try:
        real = os.path.normcase(os.path.realpath(path))
        return os.path.commonpath([real, base_real]) == base_real
    except (OSError, ValueError):
        return False


def _past(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() > deadline


@dataclass
class _Dir:
    """1 つのディレクトリの途中の状態。"""

    directory: str
    index_rel: str
    index_path: str
    writable: bool
    owner: str
    old_rows: dict[str, dict]
    old_text: str | None
    # md の concept_id を、名前の順に。
    order: list[str] = field(default_factory=list)
    # 決まった行（使い回し・読み直し）。
    rows: dict[str, dict] = field(default_factory=dict)
    # 読み直しを待つ md（相対パス, concept_id, mtime）。mtime が None は見る前に打ち切ったもの。
    pending: list[tuple[str, str, str | None]] = field(default_factory=list)


def build(
    base: str,
    excluded_dirs: tuple[str, ...] = (),
    refresh: bool = True,
    deadline: float | None = None,
    parse: bool = True,
) -> Built:
    """1 つの git の作業ツリー（base）の md の索引を組み、無視されている index.jsonl を差分で書く。

    md を持つディレクトリの `index.jsonl` がどれも無視されていなければ、そのツリーは索引の
    対象外（`unignored`）で、行を返さない。一部だけ無視されていないディレクトリと、ccnavi の
    ものでない index.jsonl があるディレクトリは、行を組むが書かない。refresh が偽なら mtime を
    見ずに既存の行をそのまま使い、何も書かない（行の無い md は parse が真なら読む）。

    順は 3 段。まず全部のディレクトリで既存の行を使い回せるかを見（lstat だけ）、次に
    読み直しの要る md を、待つ数の少ないディレクトリから読み、最後にディレクトリごとに書く。
    deadline（`time.monotonic` の値）を過ぎたら読むのをやめて `timed_out` にするが、そこまでに
    読めた分は書く。読めなかった md は既存の行があればそれを残す（次の回に mtime の違いで読み
    直す）。大きなディレクトリも回を重ねれば埋まり、小さなディレクトリはその後ろに並ばない。
    `.git` が無ければ `NotARepository`、git に聞けなければ `GitFailed`。
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
    result.indexable = True
    tmp_dir = _git_dir(base) if refresh else ""
    if refresh and not tmp_dir:
        result.problems.append("git のディレクトリが見つからないので索引を書かない")
    if tmp_dir:
        _sweep(tmp_dir)
    writer = _Writer(base, tmp_dir, deadline) if tmp_dir else None
    base_real = os.path.normcase(os.path.realpath(base))

    dirs: list[_Dir] = []
    for directory in sorted(by_dir):
        if _past(deadline):
            result.timed_out = True
            break
        try:
            scanned = _scan(
                base,
                base_real,
                directory,
                by_dir[directory],
                index_rel[directory],
                index_rel[directory] in writable and writer is not None,
                refresh,
                deadline,
                result,
            )
        except Exception as exc:  # noqa: BLE001 - 1 つのディレクトリの壊れで全体を止めない
            result.problems.append(f"{directory or ROOT_DIRECTORY}/ を読めない: {exc!r}")
            continue
        if scanned is not None:
            dirs.append(scanned)
    if parse and not result.timed_out:
        _parse_pending(base, dirs, deadline, result)
    for one in dirs:
        try:
            _settle(one, refresh, writer, result)
        except Exception as exc:  # noqa: BLE001 - 1 つのディレクトリの壊れで全体を止めない
            result.problems.append(f"{one.directory or ROOT_DIRECTORY}/ を読めない: {exc!r}")
    result.rows.sort(key=lambda r: str(r.get("concept_id", "")))
    return result


def _scan(
    base: str,
    base_real: str,
    directory: str,
    names: list[str],
    index_rel: str,
    writable: bool,
    refresh: bool,
    deadline: float | None,
    result: Built,
) -> _Dir | None:
    """既存の index.jsonl を読み、使い回せる行と読み直しの要る md に分ける。"""
    full_dir = os.path.join(base, *directory.split("/")) if directory else base
    if not _under(full_dir, base_real):
        result.problems.append(f"{directory}/ の実体がツリーの外にあるので読まない")
        return None
    index_path = os.path.join(base, *index_rel.split("/"))
    old_rows, old_text, owner = _read_index(index_path)
    if owner == FOREIGN:
        result.foreign.append(index_rel)
        result.problems.append(f"{index_rel} は ccnavi の索引ではないので書き換えない")
        writable = False
    one = _Dir(directory, index_rel, index_path, writable, owner, old_rows, old_text)
    shown_dir = directory or ROOT_DIRECTORY
    for rel in sorted(names):
        concept_id = rel[: -len(".md")]
        one.order.append(concept_id)
        if result.timed_out or _past(deadline):
            result.timed_out = True
            one.pending.append((rel, concept_id, None))
            continue
        old = old_rows.get(concept_id)
        if not refresh and old is not None:
            one.rows[concept_id] = old
            result.reused += 1
            continue
        try:
            info = os.lstat(os.path.join(base, *rel.split("/")))
        except OSError:
            continue  # 消したがまだステージしていない
        if not stat.S_ISREG(info.st_mode):
            continue
        mtime = _mtime(info.st_mtime)
        if old is not None and old["mtime"] == mtime and old["directory"] == shown_dir:
            one.rows[concept_id] = old
            result.reused += 1
            continue
        one.pending.append((rel, concept_id, mtime))
    return one


def _parse_pending(base: str, dirs: list[_Dir], deadline: float | None, result: Built) -> None:
    """読み直しの要る md を読む。待つ数の少ないディレクトリから。期限を過ぎたらやめる。"""
    for one in sorted(dirs, key=lambda d: (len(d.pending), d.directory)):
        for at, (rel, concept_id, mtime) in enumerate(one.pending):
            if mtime is None:
                continue
            if _past(deadline):
                result.timed_out = True
                one.pending = one.pending[at:]
                return
            raw = _read_head(os.path.join(base, *rel.split("/")))
            if raw is None:
                continue
            result.parsed += 1
            one.rows[concept_id] = {
                "concept_id": concept_id,
                "directory": one.directory or ROOT_DIRECTORY,
                "frontmatter": front_matter(raw),
                "mtime": mtime,
            }
        one.pending = [p for p in one.pending if p[2] is None]


def _settle(one: _Dir, refresh: bool, writer: _Writer | None, result: Built) -> None:
    """1 つのディレクトリの行を決め、書けるなら書く（打ち切ったディレクトリも読めた分まで）。"""
    waiting = {concept_id for _, concept_id, _ in one.pending}
    rows = []
    for concept_id in one.order:
        row = one.rows.get(concept_id)
        if row is None and concept_id in waiting:
            row = one.old_rows.get(concept_id)
        if row is not None:
            rows.append(row)
    result.rows.extend(rows)
    if not refresh or not one.writable or writer is None:
        return
    try:
        if not rows:
            # md が全部消えたディレクトリ。ccnavi の索引で、無視されているものだけを消す。
            # 打ち切りで行が空のときは消さない。
            if one.owner == OURS and not waiting:
                os.remove(one.index_path)
                result.removed.append(one.index_rel)
            return
        text = "".join(_dumps(r) + "\n" for r in rows)
        if text == one.old_text:
            return
        writer.write(one.index_path, one.index_rel, text)
        result.written.append(one.index_rel)
    except OSError as exc:
        result.problems.append(f"{one.index_rel} を書けない: {exc}")


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
    return "" if rel in (".", "..") or rel.startswith("../") else rel


@dataclass
class Place:
    """索引を組む 1 つの git の作業ツリー。prefix は concept_id の頭に付ける文字列。"""

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


def without_git(conf: settings.Settings) -> list[str]:
    """プロジェクトの置き場の直下にあって `.git` を持たないディレクトリの名前。"""
    if not conf.projects:
        return []
    try:
        names = sorted(os.listdir(conf.projects))
    except OSError:
        return []
    return [
        name
        for name in names
        if os.path.isdir(os.path.join(conf.projects, name))
        and not os.path.exists(os.path.join(conf.projects, name, ".git"))
    ]


def _prefixed(row: dict, prefix: str) -> dict:
    if not prefix:
        return row
    moved = dict(row)
    moved["concept_id"] = f"{prefix}/{row.get('concept_id', '')}"
    directory = row.get("directory") or ROOT_DIRECTORY
    moved["directory"] = prefix if directory == ROOT_DIRECTORY else f"{prefix}/{directory}"
    return moved


@dataclass
class Collected:
    """ワークスペースとプロジェクトを合わせた索引。concept_id はワークスペースルートから。"""

    rows: list[dict] = field(default_factory=list)
    # md を持つが index.jsonl を無視していないので対象外にしたツリーの名前。
    unignored: list[str] = field(default_factory=list)
    # git への問い合わせに失敗したツリー（名前: 理由）。対象外とも git の外とも別。
    failed: list[str] = field(default_factory=list)
    # ccnavi のものでないので触らなかった index.jsonl（ワークスペースルートから）。
    foreign: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    # どれかのツリーが md を持ち、索引の対象だった。
    indexable: bool = False
    timed_out: bool = False


def collect(
    conf: settings.Settings,
    root: str,
    refresh: bool = True,
    deadline: float | None = None,
    parse: bool = True,
) -> Collected:
    """ワークスペースとプロジェクトの索引を新しくして集める。

    `.git` の無いツリーは何も出さずに飛ばす。
    """
    result = Collected()
    seen: set[str] = set()
    for place in places(conf, root):
        if deadline is not None and time.monotonic() > deadline:
            result.timed_out = True
            break
        try:
            built = build(
                place.base, place.excluded, refresh=refresh, deadline=deadline, parse=parse
            )
        except NotARepository:
            continue
        except GitFailed as exc:
            result.failed.append(f"{place.name}: {exc}")
            result.timed_out = result.timed_out or _past(deadline)
            continue
        if built.unignored:
            result.unignored.append(place.name)
        result.timed_out = result.timed_out or built.timed_out
        result.indexable = result.indexable or built.indexable

        def at(rel: str, place: Place = place) -> str:
            return f"{place.prefix}/{rel}" if place.prefix else rel

        result.foreign.extend(at(p) for p in built.foreign)
        result.problems.extend(at(p) for p in built.problems)
        for row in built.rows:
            moved = _prefixed(row, place.prefix)
            key = _norm(str(moved.get("concept_id", "")))
            if key in seen:
                continue
            seen.add(key)
            result.rows.append(moved)
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


def _valid_when(value: str) -> bool:
    if not _WHEN.match(value):
        return False
    try:
        datetime.datetime.strptime(value, _WHEN_FORMATS[len(value)])
    except (KeyError, ValueError):
        return False
    return True


def problems_of(query: Query) -> list[str]:
    """使い方の誤り。空なら引ける。"""
    found = []
    if query.sort not in SORTS:
        found.append(f"--sort は {' / '.join(SORTS)} のどれか（{query.sort!r} は使えない）")
    if query.format not in FORMATS:
        found.append(f"--format は {' / '.join(FORMATS)} のどれか（{query.format!r} は使えない）")
    for flag, value in (("--since", query.since), ("--until", query.until)):
        if value and not _valid_when(value):
            found.append(
                f"{flag} には日時を YYYY-MM-DD[THH[:MM[:SS]]] の形で書く（{value!r} は読めない）"
            )
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
        try:
            return _dumps(value)
        except (ValueError, RecursionError):
            return ""
    try:
        return str(value)
    except ValueError:  # 桁の多すぎる整数
        return ""


def _str(row: dict, key: str) -> str:
    return _text(_fm(row).get(key))


def _arr(row: dict, key: str) -> list[str]:
    value = _fm(row).get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return [_text(v) for v in value]
    return [_text(value)]


def _scalars(value: Any) -> list[str]:
    """入れ子のスカラーの値。再帰しない（深い入れ子で落ちないように）。"""
    found = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            stack.extend(reversed(item))
        elif item is not None:
            found.append(_text(item))
    return found


def _fold(text: str) -> str:
    """比べる形。NFC に揃えて小文字にする。"""
    return _norm(text).lower()


def _searchable(row: dict) -> str:
    parts = [_text(row.get("concept_id")), _text(row.get("mtime")), *_scalars(_fm(row))]
    return _fold("\n".join(parts))


def _needles(values: list[str]) -> list[str]:
    return [_fold(v) for v in values if v]


def _until_end(until: str) -> str:
    """`--until` を、書いた桁の終わりまで延ばす。

    mtime は `YYYY-MM-DDTHH:MM:SS` で、文字列で比べる。`2026-08-05` のままだと
    `2026-08-05T00:00:00` より小さく、その日に書いたものが 1 本も残らない。
    """
    tail = {10: "T23:59:59", 13: ":59:59", 16: ":59"}.get(len(until), "")
    return until + tail if until else until


def matches(row: dict, query: Query) -> bool:
    def exact(needles: list[str], hay: list[str]) -> bool:
        if not needles:
            return True
        folded = {_fold(h) for h in hay}
        return any(n in folded for n in needles)

    def partial(needles: list[str], hay: str) -> bool:
        return not needles or any(n in hay for n in needles)

    mtime = _text(row.get("mtime"))
    until = _until_end(query.until)
    return (
        exact(_needles(query.types), [_str(row, "type")])
        and exact(_needles(query.tags), _arr(row, "tags"))
        and exact(_needles(query.keywords), _arr(row, "keywords"))
        and partial(_needles(query.paths), _fold(_text(row.get("concept_id"))))
        and partial(_needles(query.texts), _searchable(row) if query.texts else "")
        and (not query.since or mtime >= query.since)
        and (not until or mtime <= until)
    )


def _sort_key(query: Query):
    def key(row: dict):
        cid = _norm(_text(row.get("concept_id")))
        if query.sort == "mtime":
            return (_text(row.get("mtime")), cid)
        if query.sort in ("type", "title"):
            return (_fold(_str(row, query.sort)), cid)
        return (cid,)

    return key


def _char_width(c: str) -> int:
    if unicodedata.combining(c) or unicodedata.category(c) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1


def dwidth(text: str) -> int:
    """見た目の幅。東アジアの全角（W / F）を 2、結合文字と書式文字を 0 と数える。"""
    return sum(_char_width(c) for c in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - dwidth(text))


def _one_line(text: str) -> str:
    """表と詳しい形に出す値を 1 行にまとめる（改行で行が分かれないように）。"""
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

# SessionStart で索引を新しくするのに使ってよい時間（秒）の上限。実際には hook の判定の
# 期限（events.decide が持つ deadline）の残りと小さいほう。過ぎたら残りは次の回に回す。
# 使い回せていれば数百本の md でも 1 秒に届かない。初めての大きなプロジェクトだけが
# ここに当たり、書けたディレクトリの分は次のセッションで使い回される。
START_SECONDS = 3.0
# 残りがこれに満たなければ新しくしない（既存の index.jsonl だけで案内する）。
MIN_REFRESH_SECONDS = 0.5
# 新しくしないときに git に与える時間（ls-files と check-ignore）。
STALE_SECONDS = 1.0


def run(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    query: Query,
    refresh: bool = True,
) -> int:
    """`ccnavi --docs`。ワークスペースと、索引の対象になるプロジェクトの md を引く。0 件でも 0。

    期限は無い。初めての回は md を全部読む（1 本につき頭の数 KiB）ので、大きなツリーでは
    数秒かかることがある。2 回目からは mtime の変わった md だけを読む。
    """
    problems = problems_of(query)
    if problems:
        for problem in problems:
            stderr.write(f"ccnavi: {problem}\n")
        return 1
    found = collect(conf, root, refresh=refresh)
    for name in without_git(conf):
        stderr.write(f"ccnavi: プロジェクト {name} は .git を持たないので引かない\n")
    for failure in found.failed:
        stderr.write(f"ccnavi: git への問い合わせに失敗したので引かない: {failure}\n")
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
    """案内に書く ccnavi のパスの表記。設定が相対ならワークスペースルートから書く。"""
    path = conf.bin
    if path and not os.path.isabs(path):
        path = os.path.realpath(os.path.join(root, path))
    return settings.bin_command(path)


def at_start(conf: settings.Settings, root: str, deadline: float | None = None) -> str:
    """SessionStart。ワークスペースとプロジェクトの索引を差分で新しくし、引き方の案内を返す。

    使う時間は START_SECONDS と、渡された期限（hook の判定の期限）の残りの小さいほう。
    残りが MIN_REFRESH_SECONDS に満たない（期限が既に切れているときも）なら新しくせず、
    md を読まず書かずに既存の index.jsonl の行だけを集めて案内する（git には STALE_SECONDS
    だけ与える）。何が起きても開始は止めない。md が 1 本も無い・何かが壊れたときは何も出さない
    （空を返す）。索引の対象外にしたツリーと、触らなかった index.jsonl があれば短くつける。
    git への問い合わせに失敗したツリーは何も言わない（対象外と取り違えさせない）。
    """
    try:
        now = time.monotonic()
        limit = now + START_SECONDS
        if deadline is not None:
            limit = min(limit, deadline)
        if limit - now >= MIN_REFRESH_SECONDS:
            found = collect(conf, root, deadline=limit)
        else:
            found = collect(conf, root, refresh=False, parse=False, deadline=now + STALE_SECONDS)
            found.timed_out = True
        return notice(conf, root, found)
    except Exception:  # noqa: BLE001 - 索引は案内の足しで、セッションの開始を止めない
        return ""


def _has_md(root: str) -> bool:
    """ワークスペースルートの直下に md があるか（git に聞けないときの目安）。"""
    try:
        with os.scandir(root) as entries:
            return any(e.name.endswith(".md") and e.is_file() for e in entries)
    except OSError:
        return False


# 案内で名指しする、触らなかった index.jsonl の数の上限。
FOREIGN_SHOWN = 3


def notice(conf: settings.Settings, root: str, found: Collected) -> str:
    extra = []
    if found.unignored:
        extra.append(
            f"{', '.join(found.unignored)} {UNIGNORED}（ccnavi は .gitignore を書き換えない）"
        )
    if found.foreign:
        names = ", ".join(found.foreign[:FOREIGN_SHOWN])
        rest = len(found.foreign) - FOREIGN_SHOWN
        more = f" ほか {rest} 本" if rest > 0 else ""
        extra.append(f"ccnavi の索引ではないので書き換えなかった: {names}{more}")
    # 行が無くても、索引の対象のツリーに md がある（打ち切りで読めなかった）か、時間切れで
    # git に聞けなかったがワークスペースに md があるなら、引き方の案内は出す。
    guide = bool(found.rows) or found.indexable
    if not guide and found.timed_out and not found.unignored:
        guide = _has_md(root)
    if not guide:
        return "\n".join(f"[ccnavi] md の索引（--docs）: {e}" for e in extra)
    command = _command(conf, root)
    lines = [
        f"[ccnavi] ドキュメント（*.md）を探すときは、grep・Glob より先に '{command} --docs' で"
        " frontmatter の索引を引いてください（ワークスペースとプロジェクトを横断。パスは"
        "ワークスペースルートから）。grep は本文中の文字列を探すときか、0 件だったときに"
        "使ってください。",
        "  絞り込み: --type / --tag / --keyword <値>（完全一致）、--path <部分>（パス）、"
        "--text <部分>（パス・更新日時・frontmatter の値）、--since / --until <YYYY-MM-DD>。"
        "同じものの繰り返しは OR、違うものどうしは AND。大文字小文字は区別しない",
        "  並べ方と形: --sort path|mtime|type|title、-r（逆順）、--limit <N>、"
        "--format table|path|detail|json|jsonl|count",
        "  md を書くときは頭に frontmatter を付けてください。type は必須、title・description・"
        "tags（kebab-case で 2〜4 個）・keywords は推奨。",
    ]
    if os.path.isfile(os.path.join(root, *CONVENTION_DOC.split("/"))):
        lines[-1] += f"type の値と詳しい決まりは {CONVENTION_DOC}"
    lines.extend(f"  {e}" for e in extra)
    return "\n".join(lines)
