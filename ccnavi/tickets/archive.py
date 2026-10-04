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

退避は補助の記録で、判定の正ではない。読むのは次の 3 つだけで、どれも「閉じた」側に締める向き。

- 閉じた識別子の使い回しの検査（`approval.integration_problems` / `integration_closed`）と、子の連番
  （`approval.next_child_id`）。退避にある識別子は閉じたものとして数える
- 先行の池（`approval.predecessor_pool_of`）。置き場のどこにも無い先行を、退避の `done/` から引く
- 取り込み（`ccnavi-sync.sh`）。親のブランチがリモートから消えたとき、退避に親があれば
  閉じた証拠にする

退避の置き場は手元にしか無いので、別の機械ではこれらの検査に使えない（ユーザが受け入れた）。

リンクは辿らない。置き場の途中（`logs`・`archive`・`<リポジトリ>`・その下）がシンボリックリンクなら
読まず、書きもしない。
"""

from __future__ import annotations

import contextlib
import os
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
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        out |= {n[: -len(".md")] for n in names if n.endswith(".md")}
    return out


def find(root: str, ident: str) -> list[ticket_mod.Ticket]:
    """退避の `done/<識別子>.md` を全リポジトリから読む。読めないものは落とす。

    状態は `done`（取り消しの欄があれば `cancelled`）。ツリーは持たない。
    """
    if not ticket_mod.is_valid_id(ident):
        return []
    out = []
    for repo in _repo_names(root):
        directory = _done_dir(root, repo)
        if not directory:
            continue
        path = os.path.join(directory, f"{ident}.md")
        if os.path.islink(path) or not os.path.isfile(path):
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
            if os.path.islink(path) or not os.path.isfile(path):
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
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    t = _load(path, ident, syncstate.repo_key(project))
    return t if t is not None and not t.parent else None


# ---- 移す


@dataclass
class Plan:
    """移すもの。`files` は承認済みの領域からの相対（"/" 区切り）。"""

    parents: list[str] = field(default_factory=list)
    tickets: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


def closed_parents(approved_dir: str) -> list[str]:
    """承認済みの領域の `done/` に在る親（子の形でない識別子）。"""
    return sorted(
        ident for ident in _done_ids(approved_dir) if not ticket_mod.child_pattern().match(ident)
    )


def _done_ids(approved_dir: str) -> list[str]:
    try:
        names = os.listdir(os.path.join(approved_dir, ticket_mod.DONE))
    except OSError:
        return []
    return [n[: -len(".md")] for n in names if n.endswith(".md")]


def _family_of(ident: str) -> str:
    matched = ticket_mod.child_pattern().match(ident)
    return matched.group("parent") if matched else ident


def plan(approved_dir: str, parents: list[str]) -> Plan:
    """閉じた親の並びから、移すファイルを決める。親が `done/` に無いものは入れない。

    子は `<親>-<連番>` の形で親に結ぶ。`done/` の子・`events/` の跡・`flows/` のフローのうち、
    親が `parents` にあるものだけ。`phases/<親>/` は下を丸ごと。
    """
    done = set(_done_ids(approved_dir))
    wanted = [p for p in parents if p in done and not ticket_mod.child_pattern().match(p)]
    family = set(wanted)
    out = Plan(parents=sorted(family))
    for ident in sorted(done):
        if _family_of(ident) in family:
            out.tickets.append(ident)
            out.files.append(f"{ticket_mod.DONE}/{ident}.md")
    for p in out.parents:
        out.files += _walk(approved_dir, f"{PHASES_DIR}/{p}")
    for name in _names(os.path.join(approved_dir, history.EVENTS_DIR)):
        if name.endswith(history.SUFFIX) and _family_of(name[: -len(history.SUFFIX)]) in family:
            out.files.append(f"{history.EVENTS_DIR}/{name}")
    for name in _names(os.path.join(approved_dir, FLOWS_DIR)):
        stem, ext = os.path.splitext(name)
        if ext in FLOW_SUFFIXES and _family_of(stem) in family and stem not in family:
            out.files.append(f"{FLOWS_DIR}/{name}")
    return out


def _names(directory: str) -> list[str]:
    """ディレクトリのふつうのファイルの名前（リンクは落とす）。"""
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    out = []
    for name in names:
        try:
            mode = os.lstat(os.path.join(directory, name)).st_mode
        except OSError:
            continue
        if stat.S_ISREG(mode):
            out.append(name)
    return out


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
            if os.path.islink(full) or not os.path.isfile(full):
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


def archived_bytes(root: str, project: str, rel: str) -> bytes | None:
    """退避にあるファイルの中身。無い・読めない・途中にリンクがあれば None。"""
    parts = (*ARCHIVE_DIR.split(os.sep), syncstate.repo_key(project), *rel.split("/"))
    if any(p in ("", ".", "..") for p in rel.split("/")):
        return None
    if _linked(root, parts) is not False:
        return None
    try:
        with open(os.path.join(root, *parts), "rb") as f:
            return f.read()
    except OSError:
        return None


def move(root: str, project: str, approved_dir: str, todo: Plan) -> tuple[list[str], str]:
    """`todo` のファイルを退避へ移す。移した相対の並びと、止まった理由（無ければ空）。

    退避へは写してから元を消す（元の消去は fsio を通すので、C1 の一覧に載る）。退避の側の書き込みは
    一覧に載せない（`logs/` はリポジトリに入らない）。行き先に同じ中身が既に在れば（前の回の残り）
    写さずに元だけ消す。違う中身が在るとき、チケット（`done/`）なら上書きせずに止め（閉じた記録を
    書き換えない）、マーカー・跡・フローなら今の中身で置き換える（push が通らずに C1 が戻した後の
    打ち直しでは、跡の 1 行や印の時刻が前の回と違う）。
    途中で止まっても、そこまでに移した分は戻さない（元は git に残っていて、C1 が戻す）。
    """
    base = base_dir(root, project)
    linked = _linked(root, tuple(os.path.relpath(base, root).split(os.sep)))
    if linked:
        return [], f"退避の置き場（{base}）の途中にシンボリックリンクがある（辿らない）"
    files = list(todo.files)
    for ident in todo.tickets:
        history.note(approved_dir, ident, history.KIND_ARCHIVED, ticket_mod.DONE, PLACE)
        # 跡の無かったチケットは、いま書いた 1 行でファイルができる。それも一緒に移す。
        rel = f"{history.EVENTS_DIR}/{ident}{history.SUFFIX}"
        if rel not in files and os.path.isfile(os.path.join(approved_dir, *rel.split("/"))):
            files.append(rel)
    moved: list[str] = []
    for rel in files:
        source = os.path.join(approved_dir, *rel.split("/"))
        data = fsio.read_bytes(source)
        if data is None:
            return moved, f"{source} を読めない"
        target = destination(root, project, rel)
        held = archived_bytes(root, project, rel)
        if held is None and os.path.lexists(target):
            return moved, f"退避の行き先 {target} を読めない（ふつうのファイルでないかリンク）"
        if held is not None and not same_bytes(held, data):
            if rel.startswith(f"{ticket_mod.DONE}/"):
                return moved, (
                    f"退避の行き先 {target} に違う中身が既に在る。閉じたチケットを上書きしないので"
                    "止めた。ユーザが中身を確かめてください"
                )
            # マーカー・跡・フローは、前の回（push が通らず戻された）の残りを今の中身で置き換える。
            # 前の回の跡の 1 行や Draft を外した印の時刻だけが違う、がふつう。
            failed = _replace(target, data)
            if failed:
                return moved, f"{target} を置き換えられない（{failed}）"
        elif held is None:
            failed = _copy(target, data)
            if failed:
                return moved, f"{target} に写せない（{failed}）"
        failed = fsio.unlink(source)
        if failed:
            return moved, f"{source} を消せない（{failed}）"
        moved.append(rel)
    for p in todo.parents:
        _prune_empty(os.path.join(approved_dir, PHASES_DIR, p))
    return moved, ""


def _copy(target: str, data: bytes) -> str:
    """退避の側へ書く。C1 の一覧には載せない（リポジトリの外）。"""
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "xb") as f:
            f.write(data)
    except OSError as exc:
        return f"{exc}"
    return ""


def _replace(target: str, data: bytes) -> str:
    """退避の側の 1 本を置き換える（同じディレクトリの一時ファイルから）。リンクは辿らない。"""
    temp = f"{target}.{os.getpid()}.tmp"
    try:
        with open(temp, "xb") as f:
            f.write(data)
        os.replace(temp, target)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.remove(temp)
        return f"{exc}"
    return ""


def _prune_empty(directory: str) -> None:
    """空になったディレクトリを下から消す（git は空のディレクトリを持たない）。"""
    if os.path.islink(directory) or not os.path.isdir(directory):
        return
    for here, _dirs, _files in sorted(os.walk(directory), key=lambda w: -len(w[0])):
        with contextlib.suppress(OSError):
            os.rmdir(here)
