"""閉じた親子のチケットの退避（`logs/archive/`）。

`ccnavi-review.sh ready` が Draft を外す直前に、親のツリーの承認済みの領域から、閉じた親
（`done/` に在る親。今回の親と、統合先にたまっていた過去の親）のファイルを手元の退避の置き場へ移す。
移すのは次の 4 つで、親のツリーの git の上では削除になる。削除は C1 がコミットして push してから
Draft を外すので、squash でマージすると既定のブランチにはチケットが残らない。

- `done/` の親と子のチケット
- `phases/<親>/` の下（マーカー・子の記録・Draft を外した印も含む）
- `events/` の親と子の跡
- `flows/` の子のフロー

置き場はワークスペースルートの `logs/archive/<リポジトリ>/` で、`<リポジトリ>` は
ワークスペース自身なら `self`、プロジェクトならその名前（取り込みの控えと同じ分け方）。
その下は承認済みの領域と同じ並び
（`done/<識別子>.md`・`phases/<親>/...`・`events/<識別子>.ndjson`・`flows/<子>.yml`）にする。
`logs/` は git が追跡しないので、退避は手元の機械にだけ残る。

退避は補助の記録で、判定の正ではない。読むのは次のところだけで、どれも「閉じた」側に締める向き。

- 閉じた識別子の使い回しの検査（`approval.integration_problems` / `integration_closed`）と、子の連番
  （`approval.next_child_id`）。退避にある識別子は閉じたものとして数える（同じリポジトリのものだけ）
- 先行の池（`approval.predecessor_pool_of`）。置き場のどこにも無い先行を、同じリポジトリの退避の
  `done/` から引く
- 判定の走査（`approval.scan`）。子のワークツリーに残った古い写しを、退避に同じ承認の写しがあれば
  作業中に戻さない（`drop_archived`）
- 取り込み（`ccnavi-sync.sh`・`syncstate`）。マージ済みかを確かめられないときと、親のワークツリーを
  片付けた後に、退避の親を閉じた証拠として補う。親のワークツリーの見分け（`ccnavi_parent_tree`）も見る
- C1 の見分けと実行後チェック。ready の印（`ready/<親>.json`）に載っている削除だけを
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

from ..infra import fsio
from . import history, syncstate
from . import ticket as ticket_mod

# ワークスペースルートからの退避の置き場。`logs/` は .gitignore に入っている。
ARCHIVE_DIR = os.path.join("logs", "archive")
# 承認済みの領域の下で移すもの。
PHASES_DIR = "phases"
FLOWS_DIR = "flows"
FLOW_SUFFIXES = (".yml", ".yaml")
# 退避した印の跡に書く置き場の名前（`from: done` → `to: archive`）。
PLACE = "archive"
# 書きかけの一時ファイル（fsio の `.<名前>.<一意>.part`）。移さない。
_TEMP = re.compile(r"^\..*\.part(\.[^/]*)?$")


def top(root: str) -> str:
    """退避の置き場の一番上（`<ワークスペースルート>/logs/archive`）。"""
    return os.path.join(root, ARCHIVE_DIR)


def base_dir(root: str, project: str) -> str:
    """そのリポジトリの退避の置き場（承認済みの領域と同じ並びを持つ）。"""
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
    """退避のあるリポジトリの名前（`self` とプロジェクトの名前）。リンクは落とす。"""
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
    parts = (*ARCHIVE_DIR.split(os.sep), repo, ticket_mod.DONE)
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


def find(root: str, ident: str, project: str | None = None) -> list[ticket_mod.Ticket]:
    """退避の `done/<識別子>.md` を読む。読めないものは落とす。

    `project` を渡せばそのリポジトリ（ワークスペース自身なら空文字）だけ、None なら全リポジトリ。
    状態は `done`（取り消しの欄があれば `cancelled`）。ツリーは持たない。
    """
    if not ticket_mod.is_valid_id(ident):
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


def closed_tickets(root: str) -> list[ticket_mod.Ticket]:
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


def _load(path: str, ident: str, repo: str) -> ticket_mod.Ticket | None:
    t, _ = ticket_mod.load(path)
    if t is None or t.ticket != ident:
        return None
    t.state = ticket_mod.CANCELLED if t.cancelled_at else ticket_mod.DONE
    t.project = syncstate.project_of_key(repo)
    t.tree, t.tree_root = "", ""
    return t


def archived_parent(root: str, project: str, ident: str) -> ticket_mod.Ticket | None:
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
    """ディレクトリの `*.md` のふつうのファイルの識別子（拡張子を落とした名前）。リンクは落とす。"""
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
    """チケットの識別子・親・承認の時刻（チケットとしての検査は掛けない）。"""
    if data is None:
        return None
    try:
        return syncstate._copy_fields(data.decode("utf-8"))
    except UnicodeDecodeError:
        return None


def archived_fields(root: str, project: str, ident: str) -> syncstate.DoneCopy | None:
    """退避の `done/<識別子>.md` の識別子・親・承認の時刻。無い・読めない・リンクなら None。"""
    if not ticket_mod.is_valid_id(ident):
        return None
    copy = _fields(archived_bytes(root, project, f"{ticket_mod.DONE}/{ident}.md"))
    return copy if copy is not None and copy.ticket == ident else None


def drop_archived(root: str, tickets: list[ticket_mod.Ticket]) -> list[ticket_mod.Ticket]:
    """手元の退避に、同じリポジトリで承認の時刻も同じ写しがあるチケットを落とす。

    `ready` の後も子のワークツリーには切ったときの `doing/` の写しが残る。親のツリーから消えた
    識別子は、その古い写しが権威として読まれ、作業中に戻ってしまう。閉じて退避したものは閉じた
    ものとして扱う（判定の scan から外す）。承認の時刻が違えば同じ識別子の別のチケットなので残す。
    """
    if not root or not tickets:
        return tickets
    out = []
    for t in tickets:
        copy = archived_fields(root, t.project, t.ticket)
        if copy is not None and copy.approved_at and copy.approved_at == t.approved_at:
            continue
        out.append(t)
    return out


# ---- 移す


@dataclass
class Plan:
    """移すもの。`files` は承認済みの領域からの相対（"/" 区切り）で、移す順に並ぶ。

    順は、マーカー（`phases/`）・跡・フロー・子のチケット・親のチケットの順。止まったときに
    `done/<親>.md` がツリーに残り、次の `ready` が同じ親を拾い直せるようにするため。
    """

    parents: list[str] = field(default_factory=list)
    tickets: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


def _done_copies(approved_dir: str) -> dict[str, syncstate.DoneCopy]:
    """`done/` のふつうのファイルの、識別子 → 欄。読めない・名前と違うものは落とす。"""
    directory = os.path.join(approved_dir, ticket_mod.DONE)
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
    """閉じた親の並びから、移すファイルを決める。

    親として拾うのは、`done/` に `parent:` を持たないチケットとして在るものと、手元の退避に親として
    在るもの（前の回が途中で止まった残り。`root` を渡したときだけ）。子は名前の形ではなくチケットの
    `parent:` 欄で親に結ぶ（`rel-01` という親を `rel` の子と取り違えない）。跡とフローは、その親か、
    `done/`（または退避）に在る子のものだけ。`phases/<親>/` は下を丸ごと。
    """
    done = _done_copies(approved_dir)
    family = set()
    for p in parents:
        here = done.get(p)
        if here is not None and not here.parent:
            family.add(p)
        elif here is None and root and not _lexists(approved_dir, ticket_mod.DOING, f"{p}.md"):
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
    out = Plan(parents=sorted(family))
    for p in out.parents:
        out.files += _walk(approved_dir, f"{PHASES_DIR}/{p}")
    for name in _names(os.path.join(approved_dir, history.EVENTS_DIR)):
        ident = name[: -len(history.SUFFIX)] if name.endswith(history.SUFFIX) else ""
        if ident and (ident in family or ident in children):
            out.files.append(f"{history.EVENTS_DIR}/{name}")
    for name in _names(os.path.join(approved_dir, FLOWS_DIR)):
        stem, ext = os.path.splitext(name)
        if ext in FLOW_SUFFIXES and stem in children:
            out.files.append(f"{FLOWS_DIR}/{name}")
    kids = sorted(i for i in done if i in children)
    elders = sorted(i for i in done if i in family)
    out.tickets = kids + elders
    out.files += [f"{ticket_mod.DONE}/{i}.md" for i in out.tickets]
    return out


def _lexists(*parts: str) -> bool:
    return os.path.lexists(os.path.join(*parts))


def _names(directory: str) -> list[str]:
    """ディレクトリのふつうのファイルの名前（リンクは落とす）。"""
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
    """退避の行き先の綴り。"""
    return os.path.join(base_dir(root, project), *rel.split("/"))


def same_bytes(a: bytes | None, b: bytes | None) -> bool:
    """改行を LF に揃えて同じ中身か（autocrlf で作業ツリーだけ CRLF になったものも同じと読む）。"""
    if a is None or b is None:
        return False
    return a.replace(b"\r\n", b"\n") == b.replace(b"\r\n", b"\n")


def holds(rel: str, held: bytes | None, data: bytes | None) -> bool:
    """退避の写し `held` が、ツリーの中身 `data` を写したものか。

    跡（`events/`）は、`data` が前置きで、足した部分が ready の流れの行（「退避した」と、ready が
    同じ回に置く Draft を外した印の行）だけなら同じと読む。C1 はコミット済みの中身と比べるが、
    ready は印を置いてから写すので、写しには印の 1 行が足されている。それ以外は中身が同じこと。
    """
    if same_bytes(held, data):
        return True
    if held is None or data is None or not rel.startswith(f"{history.EVENTS_DIR}/"):
        return False
    head, body = data.replace(b"\r\n", b"\n"), held.replace(b"\r\n", b"\n")
    if not body.startswith(head) or (head and not head.endswith(b"\n")):
        return False
    try:
        lines = body[len(head) :].decode("utf-8").split("\n")
    except UnicodeDecodeError:
        return False
    if lines and lines[-1] == "":
        lines = lines[:-1]
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            return False
        if not isinstance(row, dict) or not _ready_row(row):
            return False
    return bool(lines)


def _ready_row(row: dict) -> bool:
    """ready の流れが跡に足す行か（「退避した」か、Draft を外した印）。"""
    kind = row.get("kind")
    if kind == history.KIND_ARCHIVED:
        return True
    # "ready" は approval.PARENT_MARK_READY（approval はこのモジュールを読むので、綴りで持つ）。
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


# ---- ready が退避した印（どの親のワークツリーから、どのファイルを移したか）
#
# C1 の見分けと実行後チェックは、ready の流れで消したものだけを ccnavi の書き込みとして外す。
# 「退避に同じ中身がある」だけで外すと、ready を経ない削除も黙って運ばれる。印は退避の置き場の
# `ready/<親>.json` で、`logs/archive/` は記録の守りがエージェントの書き込みを止める。

READY_DIR = "ready"


def _tree_key(tree_root: str) -> str:
    return os.path.normcase(os.path.realpath(tree_root)) if tree_root else ""


def ready_files(root: str, project: str, tree_root: str) -> set[str]:
    """そのツリーから ready が移したファイル（承認済みの領域からの相対）の全部。"""
    directory = os.path.join(base_dir(root, project), READY_DIR)
    if (
        not root
        or _linked(root, (*ARCHIVE_DIR.split(os.sep), syncstate.repo_key(project), READY_DIR))
        is not False
    ):
        return set()
    key = _tree_key(tree_root)
    out: set[str] = set()
    for name in _names(directory):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, name), "rb") as f:
                data = json.loads(f.read().decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if not isinstance(data, dict) or data.get("tree") != key:
            continue
        files = data.get("files")
        if isinstance(files, list):
            out |= {f for f in files if isinstance(f, str)}
    return out


def ready_started(root: str, project: str, parent: str, tree_root: str) -> bool:
    """そのツリーで、その親の ready が退避を始めたか（印 `ready/<親>.json` が在るか）。

    印は条件を確かめた後にだけ書くので、在れば条件は前の回で確かめてある。
    """
    held = archived_bytes(root, project, f"{READY_DIR}/{parent}.json")
    if held is None:
        return False
    try:
        data = json.loads(held.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    return isinstance(data, dict) and data.get("tree") == _tree_key(tree_root)


def _note_ready(root: str, project: str, parent: str, tree_root: str, rels: list[str]) -> str:
    """ready の印に、これから移すファイルを足す（消す前に書く）。"""
    rel = f"{READY_DIR}/{parent}.json"
    held = archived_bytes(root, project, rel)
    files: list[str] = []
    if held is not None:
        try:
            data = json.loads(held.decode("utf-8"))
            if isinstance(data, dict) and data.get("tree") == _tree_key(tree_root):
                files = [f for f in data.get("files", []) if isinstance(f, str)]
        except (ValueError, UnicodeDecodeError):
            files = []
    merged = sorted(set(files) | set(rels))
    payload = {"parent": parent, "tree": _tree_key(tree_root), "files": merged}
    return _write(root, project, rel, json.dumps(payload, ensure_ascii=False, indent=1).encode())


def move(
    root: str,
    project: str,
    approved_dir: str,
    todo: Plan,
    ready_parent: str = "",
    tree_root: str = "",
) -> tuple[list[str], str]:
    """`todo` のファイルを退避へ移す。移した相対の並びと、止まった理由（無ければ空）。

    1 本ずつ、退避の側へ一時ファイルから書いて読み戻して確かめ、それから元を消す（元の消去は
    fsio を通すので、C1 の一覧に載る）。退避の側の書き込みは一覧に載せない（`logs/` は
    リポジトリに入らない）。退避の置き場の途中（`logs` から行き先のディレクトリまで）にリンクが
    あれば書かずに止める。

    跡（`events/`）はツリーの中身をそのまま写し、「退避した」の 1 行は退避の側にだけ足す。足すのは
    そのチケット（`done/`）を移したときで、途中で止まっても移していないチケットに印は残らない。
    行き先に写しが既に在るとき、ツリーの中身を写したものと読めれば（前の回の残り）写さずに元だけ
    消す。違えば、チケット（`done/`）なら上書きせずに止め（閉じた記録を書き換えない）、マーカー・
    跡・フローなら今の中身で置き換える（push が通らずに C1 が戻した後の打ち直し）。

    `ready_parent` と `tree_root` を渡せば、消す前に ready の印（`ready/<親>.json`）へ移すファイルを
    足す。途中で止まっても、そこまでに移した分は戻さない（元は git に残っていて、C1 が戻す）。
    """
    base = base_dir(root, project)
    if _linked(root, tuple(os.path.relpath(base, root).split(os.sep))):
        return [], f"退避の置き場（{base}）の途中にシンボリックリンクがある（辿らない）"
    if ready_parent:
        failed = _note_ready(root, project, ready_parent, tree_root, todo.files)
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
            if held is not None and rel.startswith(f"{ticket_mod.DONE}/"):
                return moved, (
                    f"退避の行き先 {target} に違う中身が既に在る。閉じたチケットを上書きしないので"
                    "止めた。ユーザが中身を確かめてください"
                )
            failed = _write(root, project, rel, data)
            if failed:
                return moved, failed
        if rel.startswith(f"{ticket_mod.DONE}/"):
            failed = _note_archived(root, project, rel[len(ticket_mod.DONE) + 1 : -len(".md")])
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
    """退避の側の跡に「退避した」の 1 行を足す（ツリーの跡には書かない）。"""
    entry: dict = {
        "at": history.stamp(),
        "ticket": ident,
        "kind": history.KIND_ARCHIVED,
        "from": ticket_mod.DONE,
        "to": PLACE,
        "via": history.via(),
    }
    entry.update(history.extra())
    rel = f"{history.EVENTS_DIR}/{ident}{history.SUFFIX}"
    held = archived_bytes(root, project, rel)
    if held is None and os.path.lexists(destination(root, project, rel)):
        return f"退避の跡 {destination(root, project, rel)} を読めない"
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
        return f"退避の綴りが読めない（{rel}）"
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
