"""閉じた親子のチケットの退避（`logs/archive/`）。

`ccnavi-review.sh ready` が Draft を外す直前に、親のツリーの承認済みの領域から、閉じた親
（`done/` に在る親。今回の親と、統合先にたまっていた過去の親）のファイルを手元の退避の置き場へ移す。
移すのは次の 4 つで、親のツリーの git の上では削除になる。削除は C1 がコミットして push してから
Draft を外すので、squash でマージすると既定のブランチにはチケットが残らない。

- `done/` の親と子のチケット
- `phases/<親>/` の下（マーカー・子の記録・Draft を外したマーカーも含む）
- `events/` の親と子の履歴
- `flows/` の子のフロー

置き場はワークスペースルートの `logs/archive/<リポジトリ>/` で、`<リポジトリ>` は
ワークスペース自身なら `self`、プロジェクトならその名前（取り込み状態と同じ分け方）。
その下は承認済みの領域と同じ構成
（`done/<識別子>.md`・`phases/<親>/...`・`events/<識別子>.ndjson`・`flows/<子>.yml`）にする。
`logs/` は git が追跡しないので、退避は手元の機械にだけ残る。

退避は補助の記録で、判定の正ではない。読むのは次のところだけで、どれも「閉じた」側に厳しくする向き。

- 閉じた識別子の使い回しの検査（`approval_checks.integration_problems` / `integration_closed`）と、
  子の連番（`approval_ops.next_child_id`）。退避にある識別子は閉じたものとして数える（同じリポジトリのものだけ）
- 先行を引く対応表（`approval_checks.predecessor_pool_of`）。置き場のどこにも無い先行を、
  同じリポジトリの退避の `done/` から引く
- 判定の走査（`approval.scan`）。子のワークツリーに残った古いチケットを、退避に同じ承認のチケットが
  あれば作業中に戻さない（`drop_archived`）
- 取り込み（`ccnavi-sync.sh`・`syncstate`）。マージ済みかを確かめられないときと、親のワークツリーを
  片付けた後に、退避の親を閉じた証拠として補う。親のワークツリーの見分け（`ccnavi_parent_tree`）も見る
- C1 の見分けと実行後チェック。ready のマーカー（`ready/<親>.json`）に載っている削除だけを
  ccnavi の書き込みと読む

退避の置き場は手元にしか無いので、別の機械ではこれらの検査に使えない（ユーザが受け入れた）。

リンクは辿らない。置き場の途中（`logs`・`archive`・`<リポジトリ>`・その下）がシンボリックリンクなら
読まず、書きもしない。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
from dataclasses import dataclass, field

from ..infra import fsio, gitcmd
from . import history, syncstate, ticket_ids, ticket_model
from . import ticket as ticket_mod

# ワークスペースルートからの退避の置き場。`logs/` は .gitignore に入っている。
ARCHIVE_DIR = os.path.join("logs", "archive")
# 承認済みの領域の下で移すもの。
PHASES_DIR = "phases"
FLOWS_DIR = "flows"
FLOW_SUFFIXES = (".yml", ".yaml")
# 退避したマーカーの履歴に書く置き場の名前（`from: done` → `to: archive`）。
PLACE = "archive"
# 書きかけの一時ファイル（fsio の `.<名前>.<一意>.part`）。移さない。
_TEMP = re.compile(r"^\..*\.part(\.[^/]*)?$")


def top(root: str) -> str:
    """退避の置き場の一番上（`<ワークスペースルート>/logs/archive`）。"""
    return os.path.join(root, ARCHIVE_DIR)


def base_dir(root: str, project: str) -> str:
    """そのリポジトリの退避の置き場（承認済みの領域と同じ構成を持つ）。"""
    return os.path.join(top(root), syncstate.repo_key(project))


def is_archived_path(root: str, path: str) -> bool:
    """パスが退避の置き場の下か。"""
    if not root or not path:
        return False
    here = os.path.normcase(os.path.abspath(top(root)))
    there = os.path.normcase(os.path.abspath(path))
    return there.startswith(here + os.sep)


def _linked(root: str, parts: tuple[str, ...]) -> bool | None:
    """ルートの下の parts を順に lstat して、リンクがあれば True。途中で無ければ None。"""
    path = root
    for part in parts:
        path = os.path.join(path, part)
        try:
            mode = os.lstat(path).st_mode
        except OSError:
            return None
        if stat.S_ISLNK(mode):
            return True
    return False


def _repo_names(root: str) -> list[str]:
    """退避のあるリポジトリの名前（`self` とプロジェクトの名前）。リンクは除く。"""
    if not root or _linked(root, tuple(ARCHIVE_DIR.split(os.sep))) is not False:
        return []
    try:
        names = sorted(os.listdir(top(root)))
    except OSError:
        return []
    out = []
    for name in names:
        try:
            mode = os.lstat(os.path.join(top(root), name)).st_mode
        except OSError:
            continue
        if stat.S_ISDIR(mode):
            out.append(name)
    return out


def _done_dir(root: str, repo: str) -> str:
    """退避の `done/`。途中にリンクがあれば空文字（読まない）。"""
    parts = (*ARCHIVE_DIR.split(os.sep), repo, ticket_model.DONE)
    if _linked(root, parts) is not False:
        return ""
    return os.path.join(root, *parts)


def ids(root: str, project: str | None = None) -> set[str]:
    """退避の `done/` にある識別子（ファイル名から）。`project` が None なら全リポジトリ。"""
    repos = _repo_names(root) if project is None else [syncstate.repo_key(project)]
    out: set[str] = set()
    for repo in repos:
        directory = _done_dir(root, repo)
        if not directory:
            continue
        out |= set(_regular_md(directory))
    return out


def find(root: str, ident: str, project: str | None = None) -> list[ticket_model.Ticket]:
    """退避の `done/<識別子>.md` を読む。読めないものは除く。

    `project` を渡せばそのリポジトリ（ワークスペース自身なら空文字）だけ、None なら全リポジトリ。
    状態は `done`（取り消しの欄があれば `cancelled`）。ツリーは持たない。
    """
    if not ticket_ids.is_valid_id(ident):
        return []
    out = []
    repos = _repo_names(root) if project is None else [syncstate.repo_key(project)]
    for repo in repos:
        directory = _done_dir(root, repo)
        if not directory:
            continue
        path = os.path.join(directory, f"{ident}.md")
        if not _regular(path):
            continue
        found = _load(path, ident, repo)
        if found is not None:
            out.append(found)
    return out


def closed_tickets(root: str) -> list[ticket_model.Ticket]:
    """退避にある閉じたチケットの全部（ボードの表示用。判定には混ぜない）。"""
    out = []
    for repo in _repo_names(root):
        directory = _done_dir(root, repo)
        if not directory:
            continue
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        for name in names:
            if not name.endswith(".md"):
                continue
            path = os.path.join(directory, name)
            if not _regular(path):
                continue
            found = _load(path, name[: -len(".md")], repo)
            if found is not None:
                out.append(found)
    return out


def _load(path: str, ident: str, repo: str) -> ticket_model.Ticket | None:
    t, _ = ticket_mod.load(path)
    if t is None or t.ticket != ident:
        return None
    t.state = ticket_model.CANCELLED if t.cancelled_at else ticket_model.DONE
    t.project = syncstate.project_of_key(repo)
    t.tree, t.tree_root = "", ""
    return t


def archived_parent(root: str, project: str, ident: str) -> ticket_model.Ticket | None:
    """退避にある親（識別子が同じで、子でない）。無ければ None。"""
    directory = _done_dir(root, syncstate.repo_key(project))
    if not directory:
        return None
    path = os.path.join(directory, f"{ident}.md")
    if not _regular(path):
        return None
    t = _load(path, ident, syncstate.repo_key(project))
    return t if t is not None and not t.parent else None


def _regular(path: str) -> bool:
    """ふつうのファイルか（リンクは辿らずに False）。"""
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _regular_md(directory: str) -> list[str]:
    """ディレクトリの `*.md` のふつうのファイルの識別子（拡張子を除いた名前）。リンクは除く。"""
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    return [
        n[: -len(".md")]
        for n in names
        if n.endswith(".md") and _regular(os.path.join(directory, n))
    ]


def _fields(data: bytes | None) -> syncstate.DoneCopy | None:
    """チケットの識別子・親・着手と取り消しの欄（チケットとしての検査は掛けない）。"""
    if data is None:
        return None
    try:
        return syncstate._copy_fields(data.decode("utf-8"))
    except UnicodeDecodeError:
        return None


def archived_fields(root: str, project: str, ident: str) -> syncstate.DoneCopy | None:
    """退避の `done/<識別子>.md` の識別子・親・着手と取り消しの欄。無い・読めない・リンクなら None。

    大文字小文字だけが違う識別子も同じものとして引く（区別しないファイルシステムでは、ブランチと
    ワークツリーの名前がぶつかるため。使い回しの検査を緩めない向き）。
    """
    if not ticket_ids.is_valid_id(ident):
        return None
    name = ident
    if archived_bytes(root, project, f"{ticket_model.DONE}/{ident}.md") is None:
        directory = _done_dir(root, syncstate.repo_key(project))
        folded = [i for i in (_regular_md(directory) if directory else []) if same_id(i, ident)]
        if not folded:
            return None
        name = folded[0]
    copy = _fields(archived_bytes(root, project, f"{ticket_model.DONE}/{name}.md"))
    return copy if copy is not None and copy.ticket == name else None


def same_id(a: str, b: str) -> bool:
    """大文字小文字だけが違う識別子を同じと読む。"""
    return a.casefold() == b.casefold()


def drop_archived(root: str, tickets: list[ticket_model.Ticket]) -> list[ticket_model.Ticket]:
    """手元の退避に、同じリポジトリで同じチケットと言えるコピーがあるチケットを除く。

    `ready` の後も子のワークツリーには切ったときの `doing/` のチケットが残る。親のツリーから消えた
    識別子は、その古いチケットが権威として読まれ、作業中に戻ってしまう。閉じて退避したものは閉じた
    ものとして扱う（判定の scan から外す）。

    同じチケットかは、承認の時刻ではなく着手と取り消しの欄で決める（承認はチケットの中身を
    変えないので、承認の時刻の欄は古い形にしか無い）。

    - 着手も取り消しもしていない写し（欄が 3 つとも空）は、子のワークツリー（親のツリーではない
      ツリー）で見つけたものに限り、退避より前の写しとして除く。子のワークツリーを切ったのは
      着手より前で、残る写しはふつうこの形
    - 親のツリーで見つけた、欄が 3 つとも空のチケットは除かない。`ready` は親のツリーから
      閉じた親子を消すので、そこに残る未着手のチケットは古い写しではなく、閉じた識別子を使い直した
      もの（手元の退避は別の機械には無いので、別の機械で承認し直せる）。気づかないうちに除くと、ユーザの
      承認がどこにも出ずに消える。残して `Ticket.archived_clash` に理由を入れ、判定で止める
      （`approval_checks.content_problems`）
    - 欄があれば、統合先の `done/` の親と同じく `syncstate.same_parent` で比べ、同じと言えるときだけ
      除く。違えば同じ識別子の別のチケットとみなして残す
    - 両方に古い形の承認の時刻があれば、違えばどちらでも残し、同じなら欄が空でも除く（前の版と
      同じ見方）
    """
    if not root or not tickets:
        return tickets
    out = []
    for t in tickets:
        copy = archived_fields(root, t.project, t.ticket)
        if copy is not None and copy.ticket == t.ticket:
            verdict = _same_ticket(_mine(t), copy, in_parent_tree=_in_parent_tree(t))
            if verdict == _SAME:
                continue
            if verdict == _REUSED:
                # レビュー待ちの走査は `mark_blocked` を通らないので、理由をここでも付ける。
                t.archived_clash = REUSED_REASON
                t.blocked = t.blocked or REUSED_REASON
        out.append(t)
    return out


# 退避の写しとの見比べの答え。
_SAME = "same"  # 同じチケット（除く）
_OTHER = "other"  # 別のチケット（残す）
_REUSED = "reused"  # 親のツリーの未着手のチケットで、退避と同じ識別子（残して止める）

REUSED_REASON = (
    "手元の退避（logs/archive/）に同じ識別子の閉じたチケットがある。閉じた識別子を別の機械で"
    "使い直した可能性がある。このチケットは着手も取り消しもしていないので、退避と同じものかを"
    "見分けられない。ユーザが識別子を確かめ、使い直したなら別の識別子で提案して承認し直してください"
)


def _in_parent_tree(t: ticket_model.Ticket) -> bool:
    # 親のツリー（親自身なら自分のツリー）で見つけたか。`ticket_fold.authority` と同じ見方。
    return t.tree == (t.parent or t.ticket)


def _same_ticket(
    mine: syncstate.DoneCopy, archived: syncstate.DoneCopy, in_parent_tree: bool
) -> str:
    # 退避の写しと同じチケットか（drop_archived の説明のとおり）。
    if mine.approved_at and archived.approved_at:
        if mine.approved_at != archived.approved_at:
            return _OTHER
        if not any(getattr(mine, name) for name in syncstate.MATCH_FIELDS):
            return _SAME
    if not any(getattr(mine, name) for name in syncstate.MATCH_FIELDS):
        return _REUSED if in_parent_tree else _SAME
    return _SAME if syncstate.same_parent(mine, archived) else _OTHER


def _mine(t: ticket_model.Ticket) -> syncstate.DoneCopy:
    # 照合に使う欄だけを、退避の欄と同じ形で。
    return syncstate.DoneCopy(
        ticket=t.ticket,
        parent=t.parent,
        base_sha=t.base_sha,
        started_at=t.started_at,
        cancelled_at=t.cancelled_at,
        approved_at=t.approved_at,
    )


# ---- 移す


@dataclass
class Plan:
    """移すもの。`files` は承認済みの領域からの相対（"/" 区切り）で、移す順に並ぶ。

    順は、マーカー（`phases/`）・履歴・フロー・子のチケット・親のチケットの順。止まったときに
    `done/<親>.md` がツリーに残り、次の `ready` が同じ親を拾い直せるようにするため。
    """

    parents: list[str] = field(default_factory=list)
    tickets: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


def _done_copies(approved_dir: str) -> dict[str, syncstate.DoneCopy]:
    """`done/` のふつうのファイルの、識別子 → 欄。読めない・名前と違うものは除く。"""
    directory = os.path.join(approved_dir, ticket_model.DONE)
    out = {}
    for ident in _regular_md(directory):
        copy = _fields(fsio.read_bytes(os.path.join(directory, f"{ident}.md")))
        if copy is not None and copy.ticket == ident:
            out[ident] = copy
    return out


def closed_parents(approved_dir: str) -> list[str]:
    """承認済みの領域の `done/` に在る親（`parent:` を持たないチケット）。"""
    return sorted(i for i, copy in _done_copies(approved_dir).items() if not copy.parent)


def archived_parents(root: str, project: str) -> list[str]:
    """手元の退避の `done/` に在る親（`parent:` を持たないチケット）。"""
    out = []
    directory = _done_dir(root, syncstate.repo_key(project))
    for ident in _regular_md(directory) if directory else []:
        copy = archived_fields(root, project, ident)
        if copy is not None and not copy.parent:
            out.append(ident)
    return out


def plan(approved_dir: str, parents: list[str], root: str = "", project: str = "") -> Plan:
    """閉じた親のリストから、移すファイルを決める。

    親として拾うのは、`done/` に `parent:` を持たないチケットとして在るものと、手元の退避に親として
    在るもの（前の回が途中で止まった残り。`root` を渡したときだけ）。子は名前の形ではなくチケットの
    `parent:` 欄で親に結ぶ（`rel-01` という親を `rel` の子と取り違えない）。履歴とフローは、
    その親か、`done/`（または退避）に在る子のもの、どこにもチケットの無い子（取り下げた子）のもの。
    `phases/<親>/` は下をすべて。
    """
    done = _done_copies(approved_dir)
    family = set()
    for p in parents:
        here = done.get(p)
        if here is not None and not here.parent:
            family.add(p)
        elif here is None and root and not _lexists(approved_dir, ticket_model.DOING, f"{p}.md"):
            # 作業中に同じ識別子が在れば、退避の親とは別の（開いた）親子のチケット。拾わない。
            held = archived_fields(root, project, p)
            if held is not None and not held.parent:
                family.add(p)
    children = {i for i, copy in done.items() if copy.parent in family}
    if root:
        directory = _done_dir(root, syncstate.repo_key(project))
        for ident in _regular_md(directory) if directory else []:
            held = archived_fields(root, project, ident)
            if held is not None and held.parent in family:
                children.add(ident)
    # どの置き場にもチケットの無い識別子（取り下げた子など）。履歴とフローしか残っていないので、
    # このときだけ子の形（`<親>-<フェーズ番号>-<連番>`）で親に結ぶ。チケットが在る識別子は
    # `parent:` 欄で決める。
    known = set(done) | set(_regular_md(os.path.join(approved_dir, ticket_model.DOING)))

    def belongs(ident: str) -> bool:
        if ident in family or ident in children:
            return True
        if ident in known or (root and archived_fields(root, project, ident) is not None):
            return False
        matched = ticket_ids.child_pattern().match(ident)
        return matched is not None and matched.group("parent") in family

    out = Plan(parents=sorted(family))
    for p in out.parents:
        out.files += _walk(approved_dir, f"{PHASES_DIR}/{p}")
    for name in _names(os.path.join(approved_dir, history.EVENTS_DIR)):
        ident = name[: -len(history.SUFFIX)] if name.endswith(history.SUFFIX) else ""
        if ident and belongs(ident):
            out.files.append(f"{history.EVENTS_DIR}/{name}")
    for name in _names(os.path.join(approved_dir, FLOWS_DIR)):
        stem, ext = os.path.splitext(name)
        if ext in FLOW_SUFFIXES and stem not in family and belongs(stem):
            out.files.append(f"{FLOWS_DIR}/{name}")
    kids = sorted(i for i in done if i in children)
    elders = sorted(i for i in done if i in family)
    out.tickets = kids + elders
    out.files += [f"{ticket_model.DONE}/{i}.md" for i in out.tickets]
    return out


def _lexists(*parts: str) -> bool:
    return os.path.lexists(os.path.join(*parts))


def _names(directory: str) -> list[str]:
    """ディレクトリのふつうのファイルの名前（リンクは除く）。"""
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    return [n for n in names if _regular(os.path.join(directory, n))]


def _walk(approved_dir: str, rel: str) -> list[str]:
    """承認済みの領域の下の rel の下のふつうのファイル（相対、"/" 区切り）。リンクは辿らない。"""
    start = os.path.join(approved_dir, *rel.split("/"))
    if os.path.islink(start) or not os.path.isdir(start):
        return []
    out = []
    for here, dirs, files in os.walk(start, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(here, d)))
        for name in sorted(files):
            full = os.path.join(here, name)
            if not _regular(full) or _TEMP.search(name):
                continue
            out.append(os.path.relpath(full, approved_dir).replace(os.sep, "/"))
    return out


def destination(root: str, project: str, rel: str) -> str:
    """退避の行き先のパス。"""
    return os.path.join(base_dir(root, project), *rel.split("/"))


def same_bytes(a: bytes | None, b: bytes | None) -> bool:
    """改行を LF に揃えて同じ中身か（autocrlf で作業ツリーだけ CRLF になったものも同じと読む）。"""
    if a is None or b is None:
        return False
    return a.replace(b"\r\n", b"\n") == b.replace(b"\r\n", b"\n")


def _lines(data: bytes) -> list[bytes] | None:
    """改行を LF に揃えた行のリスト（末尾の空行は除く）。"""
    body = data.replace(b"\r\n", b"\n")
    lines = body.split(b"\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    return lines


def _is_ready_line(line: bytes) -> bool:
    try:
        row = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False
    return isinstance(row, dict) and _ready_row(row)


def holds(rel: str, held: bytes | None, data: bytes | None) -> bool:
    """退避したコピー `held` が、ツリーの中身 `data` を写したものか。

    履歴（`events/`）は、`data` の行が全部 `held` に在り、`held` にだけ在る行が ready の流れの行
    （「退避した」と、ready が置く Draft を外したマーカーの行）だけなら同じと読む。C1 は
    コミット済みの中身と比べるが、ready はマーカーを置いてからコピーし、退避の側にだけ「退避した」を
    足すので、コピーにはその行が足されている。それ以外は中身が同じこと。
    """
    if same_bytes(held, data):
        return True
    if held is None or data is None or not rel.startswith(f"{history.EVENTS_DIR}/"):
        return False
    rest = list(_lines(held) or [])
    for line in _lines(data) or []:
        if line not in rest:
            return False
        rest.remove(line)
    return bool(rest) and all(_is_ready_line(line) for line in rest)


def _merged_history(held: bytes, data: bytes) -> bytes:
    """退避の履歴に、ツリーにあって退避に無い行だけを足す（退避の側にだけある行を消さない）。"""
    rest = list(_lines(held) or [])
    lines = list(rest)
    for line in _lines(data) or []:
        if line in rest:
            rest.remove(line)
        else:
            lines.append(line)
    return b"".join(line + b"\n" for line in lines)


def _ready_row(row: dict) -> bool:
    """ready の流れが履歴に足す行か（「退避した」か、Draft を外したマーカー）。"""
    kind = row.get("kind")
    if kind == history.KIND_ARCHIVED:
        return True
    # "ready" は approval_marks.PARENT_MARK_READY（approval_marks はこのモジュールより上の段なので、
    # 表記で持つ）。
    return kind == history.KIND_PARENT_MARK and row.get("mark") == "ready"


def archived_bytes(root: str, project: str, rel: str) -> bytes | None:
    """退避にあるファイルの中身。無い・読めない・途中（`logs` を含む）にリンクがあれば None。"""
    if any(p in ("", ".", "..") for p in rel.split("/")):
        return None
    parts = (*ARCHIVE_DIR.split(os.sep), syncstate.repo_key(project), *rel.split("/"))
    if not root or _linked(root, parts) is not False:
        return None
    path = os.path.join(root, *parts)
    if not _regular(path):
        return None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags), "rb") as f:
            return f.read()
    except OSError:
        return None


# ---- ready が退避したマーカー（どの親のワークツリーから、どのファイルを移したか）
#
# C1 の見分けと実行後チェックは、ready の流れで消したものだけを ccnavi の書き込みとして外す。
# 「退避に同じ中身がある」だけで外すと、ready を経ない削除も気づかないうちにコミットされる。
# ready のマーカーは退避の置き場の `ready/<親>.json` で、`logs/archive/` は記録の保護が
# エージェントの書き込みを止める。

READY_DIR = "ready"


def _tree_key(tree_root: str) -> str:
    return os.path.normcase(os.path.realpath(tree_root)) if tree_root else ""


# ready のマーカーに載せてよいのは、承認済みの領域のこの 4 つの下だけ。
READY_PLACES = (
    f"{ticket_model.DONE}/",
    f"{PHASES_DIR}/",
    f"{history.EVENTS_DIR}/",
    f"{FLOWS_DIR}/",
)


def in_ready_places(rel: str) -> bool:
    """承認済みの領域からの相対が、ready が移す 4 つの置き場の下か（`..` などは受けない）。"""
    parts = rel.split("/")
    return rel.startswith(READY_PLACES) and not any(p in ("", ".", "..") for p in parts)


def tree_head(tree_root: str, rev: str = "HEAD") -> str:
    """ツリーの版の sha。読めなければ空文字（git はローカルの読み取りだけ）。"""
    if not tree_root:
        return ""
    done = gitcmd.run(tree_root, ["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"])
    return done.out.strip() if done.ok else ""


def _read_ready(root: str, project: str, name: str) -> dict | None:
    held = archived_bytes(root, project, f"{READY_DIR}/{name}")
    if held is None:
        return None
    try:
        data = json.loads(held.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def ready_files(root: str, project: str, tree_root: str, head: str) -> set[str]:
    """その回の ready がそのツリーから移したファイル（承認済みの領域からの相対）。

    ready のマーカーが有効なのは、マーカーを書いたときのツリーの先頭（`head`）と、いま比べている版が
    同じ間だけ。
    ready の削除がコミットされてツリーが進めば、マーカーは以後の削除には効かない（ready の外の削除を
    ccnavi の書き込みとしてコミットしない）。`head` が空なら何も返さない。
    """
    if not root or not head:
        return set()
    if _linked(root, (*ARCHIVE_DIR.split(os.sep), syncstate.repo_key(project), READY_DIR)):
        return set()
    key = _tree_key(tree_root)
    out: set[str] = set()
    for name in _names(os.path.join(base_dir(root, project), READY_DIR)):
        if not name.endswith(".json"):
            continue
        data = _read_ready(root, project, name)
        if data is None or data.get("tree") != key or data.get("head") != head:
            continue
        files = data.get("files")
        if isinstance(files, list):
            out |= {f for f in files if isinstance(f, str) and in_ready_places(f)}
    return out


def ready_started(root: str, project: str, parent: str, tree_root: str) -> bool:
    """そのツリーで、その親の ready が退避を始めたか（マーカー `ready/<親>.json` が在るか）。

    ready のマーカーは条件を確かめた後にだけ書くので、在れば条件は前の回で確かめてある。
    """
    data = _read_ready(root, project, f"{parent}.json")
    return data is not None and data.get("tree") == _tree_key(tree_root)


def _note_ready(
    root: str, project: str, parent: str, tree_root: str, rels: list[str], head: str
) -> str:
    """ready のマーカーに、これから移すファイルとツリーの先頭を書く（消す前に書く）。

    同じ先頭の前の回（途中で止まった）の一覧には足し、先頭が進んでいれば今回の分で書き直す。
    """
    data = _read_ready(root, project, f"{parent}.json")
    files: list[str] = []
    if data is not None and data.get("tree") == _tree_key(tree_root) and data.get("head") == head:
        files = [f for f in data.get("files", []) if isinstance(f, str)]
    merged = sorted(set(files) | {r for r in rels if in_ready_places(r)})
    payload = {"parent": parent, "tree": _tree_key(tree_root), "head": head, "files": merged}
    body = json.dumps(payload, ensure_ascii=False, indent=1).encode()
    return _write(root, project, f"{READY_DIR}/{parent}.json", body)


def move(
    root: str,
    project: str,
    approved_dir: str,
    todo: Plan,
    ready_parent: str = "",
    tree_root: str = "",
) -> tuple[list[str], str]:
    """`todo` のファイルを退避へ移す。移した相対パスのリストと、止まった理由（無ければ空）。

    1 本ずつ、退避の側へ一時ファイルから書いて読み戻して確かめ、それから元を消す（元の消去は
    fsio を通すので、C1 の一覧に載る）。退避の側の書き込みは一覧に載せない（`logs/` は
    リポジトリに入らない）。退避の置き場の途中（`logs` から行き先のディレクトリまで）にリンクが
    あれば書かずに止める。

    履歴（`events/`）はツリーの中身をそのままコピーし、「退避した」の 1 行は退避の側にだけ足す。
    足すのはそのチケット（`done/`）を移したときで、途中で止まっても移していないチケットに「退避した」は残らない。
    行き先にコピーが既に在るとき、ツリーの中身をコピーしたものと読めれば（前の回の残り）コピーせずに元だけ
    消す。違えば、チケット（`done/`）なら上書きせずに止め（閉じた記録を書き換えない）、マーカー・
    履歴・フローなら今の中身で置き換える（push が通らずに C1 が戻した後の打ち直し）。

    `ready_parent` と `tree_root` を渡せば、消す前に ready のマーカー（`ready/<親>.json`）へ移す
    ファイルを足す。途中で止まっても、そこまでに移した分は戻さない（元は git に残っていて、
    C1 が戻す）。
    """
    base = base_dir(root, project)
    if _linked(root, tuple(os.path.relpath(base, root).split(os.sep))):
        return [], f"退避の置き場（{base}）の途中にシンボリックリンクがある（辿らない）"
    if ready_parent:
        head = tree_head(tree_root)
        if not head:
            return [], f"親のワークツリー（{tree_root}）の先頭を読めない。何も移していない"
        failed = _note_ready(root, project, ready_parent, tree_root, todo.files, head)
        if failed:
            return [], failed
    moved: list[str] = []
    for rel in todo.files:
        source = os.path.join(approved_dir, *rel.split("/"))
        data = fsio.read_bytes(source)
        if data is None:
            return moved, f"{source} を読めない"
        target = destination(root, project, rel)
        held = archived_bytes(root, project, rel)
        if held is None and os.path.lexists(target):
            return moved, f"退避の行き先 {target} を読めない（ふつうのファイルでないかリンク）"
        if not holds(rel, held, data):
            if held is not None and rel.startswith(f"{ticket_model.DONE}/"):
                return moved, (
                    f"退避の行き先 {target} に違う中身が既に在る。閉じたチケットを上書きしないので"
                    "止めた。ユーザが中身を確かめてください"
                )
            content = data
            if held is not None and rel.startswith(f"{history.EVENTS_DIR}/"):
                # 履歴は上書きしない。退避の側にだけある行（前の回の「退避した」など）を残す
                content = _merged_history(held, data)
            failed = _write(root, project, rel, content)
            if failed:
                return moved, failed
        if rel.startswith(f"{ticket_model.DONE}/"):
            failed = _note_archived(root, project, rel[len(ticket_model.DONE) + 1 : -len(".md")])
            if failed:
                return moved, failed
        failed = fsio.unlink(source)
        if failed:
            return moved, f"{source} を消せない（{failed}）"
        moved.append(rel)
    for p in todo.parents:
        _prune_empty(os.path.join(approved_dir, PHASES_DIR, p))
    return moved, ""


def _note_archived(root: str, project: str, ident: str) -> str:
    """退避の側の履歴に「退避した」の 1 行を足す（ツリーの履歴には書かない）。"""
    entry: dict = {
        "at": history.stamp(),
        "ticket": ident,
        "kind": history.KIND_ARCHIVED,
        "from": ticket_model.DONE,
        "to": PLACE,
        "via": history.via(),
    }
    entry.update(history.extra())
    rel = f"{history.EVENTS_DIR}/{ident}{history.SUFFIX}"
    held = archived_bytes(root, project, rel)
    if held is None and os.path.lexists(destination(root, project, rel)):
        return f"退避の履歴 {destination(root, project, rel)} を読めない"
    # 打ち直しで同じ行を重ねない（前の回が「退避した」を書いていれば足さない）
    for line in _lines(held or b"") or []:
        try:
            row = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(row, dict) and row.get("kind") == history.KIND_ARCHIVED:
            return ""
    body = held or b""
    if body and not body.endswith(b"\n"):
        body += b"\n"
    line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
    return _write(root, project, rel, body + line.encode("utf-8", errors="backslashreplace"))


def _safe_dir(root: str, project: str, rel: str) -> str:
    """行き先のディレクトリを、ルートから 1 段ずつ確かめながら作る。リンクがあれば理由を返す。"""
    parts = [*ARCHIVE_DIR.split(os.sep), syncstate.repo_key(project), *rel.split("/")[:-1]]
    path = root
    for part in parts:
        path = os.path.join(path, part)
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            try:
                os.mkdir(path)
            except FileExistsError:
                pass
            except OSError as exc:
                return f"{path} を作れない（{exc}）"
            mode = os.lstat(path).st_mode
        except OSError as exc:
            return f"{path} を確かめられない（{exc}）"
        if stat.S_ISLNK(mode):
            return f"退避の置き場の途中（{path}）がシンボリックリンク（辿らない）"
        if not stat.S_ISDIR(mode):
            return f"退避の置き場の途中（{path}）がディレクトリでない"
    return ""


def _write(root: str, project: str, rel: str, data: bytes) -> str:
    """退避の側の 1 本を書く。同じディレクトリの一時ファイルから os.replace し、読み戻して確かめる。

    C1 の一覧には載せない（リポジトリの外）。途中にリンクがあれば書かない。書けなかったときは
    理由を返す（一時ファイルは消す。半端なファイルを行き先に残さない）。
    """
    if any(p in ("", ".", "..") for p in rel.split("/")):
        return f"退避のパスが読めない（{rel}）"
    failed = _safe_dir(root, project, rel)
    if failed:
        return failed
    target = destination(root, project, rel)
    if os.path.islink(target):
        return f"退避の行き先 {target} がシンボリックリンク（辿らない）"
    temp = os.path.join(os.path.dirname(target), f".{os.path.basename(target)}.{os.getpid()}.part")
    try:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0)
        )
        with os.fdopen(os.open(temp, flags, 0o644), "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, target)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.remove(temp)
        return f"{target} に書けない（{exc}）"
    if archived_bytes(root, project, rel) != data:
        return f"{target} を読み戻すと中身が違う（書けていない）"
    return ""


def _prune_empty(directory: str) -> None:
    """空になったディレクトリを下から消す（git は空のディレクトリを持たない）。"""
    if os.path.islink(directory) or not os.path.isdir(directory):
        return
    for here, _dirs, _files in sorted(os.walk(directory), key=lambda w: -len(w[0])):
        with contextlib.suppress(OSError):
            os.rmdir(here)
