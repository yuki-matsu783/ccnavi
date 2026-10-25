"""閉じた親子のチケットの退避（`logs/archive/`）。

`ccnavi-review.sh ready` が Draft を外す直前に、親のツリーの承認済みの領域から、閉じた親
（`done/` に在る親。今回の親と、統合先にたまっていた過去の親）のファイルを手元の退避の置き場へ移す。
移すのは次の 3 つで、親のツリーでは git 上の削除になる。削除は write-and-push がコミットして push
してから
Draft を外すので、squash でマージすると既定のブランチにはチケットが残らない。

- `done/` の親と子のチケット
- `phases/<親>/` の下（マーカー・子の記録・Draft を外したマーカーも含む）
- `flows/` の子のフロー

置き場はワークスペースルートの `logs/archive/<リポジトリ>/` で、`<リポジトリ>` は
ワークスペース自身なら `self`、プロジェクトならその名前（`tree.repo_key`）。
その下は承認済みの領域と同じ構成
（`done/<識別子>.md`・`phases/<親>/...`・`flows/<子>.yml`）にする。
`logs/` は git が追跡しないので、退避は手元の機械にだけ残る。

退避はバックアップ。閉じたかは MR がマージ済みかで決める
（`ccnavi-sync.sh` がホストに聞く）。読むのは次のところだけ。

- 子の連番（`approval_ops.next_child_id`）。退避にある識別子は使い済みとして数える
  （同じリポジトリのものだけ）
- `ready` の途中の打ち直しで、前の回が移し残したものを拾う（`plan`）
- ボードの表示と、親のワークツリーの見分け（`ccnavi_parent_tree`。`ready` から
  親のワークツリーを片付けるまでの間）
- write-and-push の見分けと実行後チェック。ready のマーカー（`ready/<親>.json`）
に載っている削除だけを
  ccnavi の書き込みと読む

退避の置き場は手元にしか無いので、別の機械ではこれらに使えない（ユーザが受け入れた）。

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

from ..infra import fsio, gitcmd, pathcase, pathsafe, tree
from . import donecopy, ticket_ids, ticket_model
from . import ticket as ticket_mod

# ワークスペースルートからの退避の置き場。`logs/` は .gitignore に入っている。
ARCHIVE_DIR = os.path.join("logs", "archive")
# 承認済みの領域の下で移すもの。
PHASES_DIR = "phases"
FLOWS_DIR = "flows"
FLOW_SUFFIXES = (".yml", ".yaml")
PLACE = "archive"
# 書きかけの一時ファイル（fsio の `.<名前>.<一意>.part`）。移さない。
_TEMP = re.compile(r"^\..*\.part(\.[^/]*)?$")


def top(root: str) -> str:
    """退避の置き場の一番上（`<ワークスペースルート>/logs/archive`）。"""
    return os.path.join(root, ARCHIVE_DIR)


def base_dir(root: str, project: str) -> str:
    """そのリポジトリの退避の置き場（承認済みの領域と同じ構成を持つ）。"""
    return os.path.join(top(root), tree.repo_key(project))


def _linked(root: str, parts: tuple[str, ...]) -> bool | None:
    """ルートの下の parts にリンクか junction があれば True。無ければ None、在れば False。"""
    if pathsafe.first_link(root, parts):
        return True
    return False if os.path.lexists(os.path.join(root, *parts)) else None


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
    repos = _repo_names(root) if project is None else [tree.repo_key(project)]
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
    repos = _repo_names(root) if project is None else [tree.repo_key(project)]
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
    """退避にある閉じたチケットの全部（ボードの表示用）。"""
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
    t.project = tree.project_of_key(repo)
    t.tree, t.tree_root = "", ""
    return t


def archived_parent(root: str, project: str, ident: str) -> ticket_model.Ticket | None:
    """退避にある親（識別子が同じで、子でない）。無ければ None。"""
    directory = _done_dir(root, tree.repo_key(project))
    if not directory:
        return None
    path = os.path.join(directory, f"{ident}.md")
    if not _regular(path):
        return None
    t = _load(path, ident, tree.repo_key(project))
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


def _fields(data: bytes | None) -> donecopy.DoneCopy | None:
    """チケットの識別子・親・着手と取り消しの欄（チケットとしての検査は掛けない）。"""
    if data is None:
        return None
    try:
        return donecopy.copy_fields(data.decode("utf-8"))
    except UnicodeDecodeError:
        return None


def archived_name(root: str, project: str, ident: str) -> str | None:
    """退避の `done/` にある、その識別子のファイルの実際の綴り（幹）。無ければ None。

    大文字小文字だけが違う識別子も同じものとして引く。
    """
    if not ticket_ids.is_valid_id(ident):
        return None
    if archived_bytes(root, project, f"{ticket_model.DONE}/{ident}.md") is not None:
        return ident
    directory = _done_dir(root, tree.repo_key(project))
    folded = [i for i in (_regular_md(directory) if directory else []) if same_id(i, ident)]
    return folded[0] if folded else None


def archived_fields(root: str, project: str, ident: str) -> donecopy.DoneCopy | None:
    """退避の `done/<識別子>.md` の識別子・親・着手と取り消しの欄。無い・読めない・リンクなら None。

    大文字小文字だけが違う識別子も同じものとして引く（区別しないファイルシステムでは、ブランチと
    ワークツリーの名前がぶつかるため。使い回しの検査を緩めない向き）。
    """
    name = archived_name(root, project, ident)
    if name is None:
        return None
    copy = _fields(archived_bytes(root, project, f"{ticket_model.DONE}/{name}.md"))
    # 区別しないファイルシステムでは、`I0001.md` で `i0001.md` が直に読める（folded を通らない）。
    # 区別するファイルシステムと同じ答えにするため、ここも大文字小文字を問わず突き合わせる。
    return copy if copy is not None and same_id(copy.ticket, name) else None


def same_id(a: str, b: str) -> bool:
    """大文字小文字だけが違う識別子を同じと読む。"""
    return a.casefold() == b.casefold()


# ---- 移す


@dataclass
class Plan:
    """移すもの。`files` は承認済みの領域からの相対（"/" 区切り）で、移す順に並ぶ。

    順は、マーカー（`phases/`）・フロー・子のチケット・親のチケットの順。
    止まったときに
    `done/<親>.md` がツリーに残り、次の `ready` が同じ親を拾い直せるようにするため。
    """

    parents: list[str] = field(default_factory=list)
    tickets: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


def _done_copies(approved_dir: str) -> dict[str, donecopy.DoneCopy]:
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
    directory = _done_dir(root, tree.repo_key(project))
    for ident in _regular_md(directory) if directory else []:
        copy = archived_fields(root, project, ident)
        if copy is not None and not copy.parent:
            out.append(ident)
    return out


def plan(approved_dir: str, parents: list[str], root: str = "", project: str = "") -> Plan:
    """閉じた親のリストから、移すファイルを決める。

    親として拾うのは、`done/` に `parent:` を持たないチケットとして在るものと、手元の退避に親として
    在るもの（前の回が途中で止まった残り。`root` を渡したときだけ）。子は名前の形ではなくチケットの
    `parent:` 欄で親に結ぶ（`rel-01` という親を `rel` の子と取り違えない）。
    フローは、その親か、`done/`（または退避）に在る子のもの、どこにもチケットの無い子のもの。
    `phases/<親>/` は下をすべて。

    処理フロー:
    1. `done/` を読み、親にできるものを `family` に入れる（退避の親は `root` を渡したときだけ）
    2. 子を `parent:` で `family` に結ぶ（`done/` と退避の両方を見る）
    3. 承認済みの `done/`・`doing/` にも退避にも無い識別子は、子の名前の形
       （`ticket_ids.child_pattern`）で結ぶ
    4. 親ごとに `phases/<親>/` の下を全部、フローのファイルは `belongs` が真のものを足す
    5. 移すチケットは子を先に、親を後に並べ、その `done/` のファイルを足す
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
        directory = _done_dir(root, tree.repo_key(project))
        for ident in _regular_md(directory) if directory else []:
            held = archived_fields(root, project, ident)
            if held is not None and held.parent in family:
                children.add(ident)
    # 承認済みの `done/`・`doing/` にも退避にも無い識別子（チケットが消えた子など）。
    # フローしか残っていないので、
    # このときだけ子の形（`<親>-<フェーズ番号>-<連番>`）で親に結ぶ。承認済みに在る識別子は
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
    """改行を LF に揃えて同じ中身か
    （autocrlf でワークツリーだけ CRLF になったものも同じと読む）。"""
    if a is None or b is None:
        return False
    return a.replace(b"\r\n", b"\n") == b.replace(b"\r\n", b"\n")


def holds(rel: str, held: bytes | None, data: bytes | None) -> bool:
    """退避したコピー `held` が、ツリーの中身 `data` をコピーしたものか（中身が同じか）。"""
    return same_bytes(held, data)


def archived_bytes(root: str, project: str, rel: str) -> bytes | None:
    """退避にあるファイルの中身。無い・読めない・途中（`logs` を含む）にリンクがあれば None。"""
    if any(p in ("", ".", "..") for p in rel.split("/")):
        return None
    parts = (*ARCHIVE_DIR.split(os.sep), tree.repo_key(project), *rel.split("/"))
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
# write-and-push の見分けと実行後チェックは、ready の流れで消したものだけを ccnavi
# の書き込みとして外す。
# 「退避に同じ中身がある」だけで外すと、ready を経ない削除も気づかないうちにコミットされる。
# ready のマーカーは退避の置き場の `ready/<親>.json` で、`logs/archive/` は記録の保護が
# エージェントの書き込みを止める。

READY_DIR = "ready"


def _tree_key(tree_root: str) -> str:
    return pathcase.fold(os.path.realpath(tree_root)) if tree_root else ""


# ready のマーカーに載せてよいのは、承認済みの領域のこの 3 つの下だけ。
READY_PLACES = (
    f"{ticket_model.DONE}/",
    f"{PHASES_DIR}/",
    f"{FLOWS_DIR}/",
)


def in_ready_places(rel: str) -> bool:
    """承認済みの領域からの相対が、ready が移す 3 つの置き場の下か（`..` などは受けない）。"""
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
    if _linked(root, (*ARCHIVE_DIR.split(os.sep), tree.repo_key(project), READY_DIR)):
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
    fsio を通すので、write-and-push の一覧に載る）。退避の側の書き込みは一覧に載せない（`logs/` は
    リポジトリに入らない）。退避の置き場の途中（`logs` から行き先のディレクトリまで）にリンクが
    あれば書かずに止める。

    行き先にコピーが既に在るとき、ツリーの中身をコピーしたものと読めれば（前の回の残り）コピーせずに元だけ
    消す。違えば、チケット（`done/`）なら上書きせずに止め（閉じた記録を書き換えない）、マーカー・
    フローなら今の中身で置き換える（push が通らずに write-and-push
    が戻した後の打ち直し）。

    `ready_parent` と `tree_root` を渡せば、消す前に ready のマーカー（`ready/<親>.json`）へ移す
    ファイルを足す。途中で止まっても、そこまでに移した分は戻さない（元は git に残っていて、
    write-and-push が戻す）。
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
            return moved, f"退避先 {target} を読めない（ふつうのファイルでないかリンク）"
        if not holds(rel, held, data):
            if held is not None and rel.startswith(f"{ticket_model.DONE}/"):
                return moved, (
                    f"退避先 {target} に違う中身が既に在る。"
                    "クローズ済みのチケットを上書きしないので"
                    "止めた。ユーザが中身を確かめてください"
                )
            failed = _write(root, project, rel, data)
            if failed:
                return moved, failed
        failed = fsio.unlink(source)
        if failed:
            return moved, f"{source} を削除できません（{failed}）"
        moved.append(rel)
    for p in todo.parents:
        _prune_empty(os.path.join(approved_dir, PHASES_DIR, p))
    return moved, ""


def _safe_dir(root: str, project: str, rel: str) -> str:
    """行き先のディレクトリを、ルートから 1 段ずつ確かめながら作る。リンクがあれば理由を返す。"""
    parts = [*ARCHIVE_DIR.split(os.sep), tree.repo_key(project), *rel.split("/")[:-1]]
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

    write-and-push の一覧には載せない（リポジトリの外）。途中にリンクがあれば書かない。
    書けなかったときは
    理由を返す（一時ファイルは消す。半端なファイルを行き先に残さない）。
    """
    if any(p in ("", ".", "..") for p in rel.split("/")):
        return f"退避のパスが読めない（{rel}）"
    failed = _safe_dir(root, project, rel)
    if failed:
        return failed
    target = destination(root, project, rel)
    if os.path.islink(target):
        return f"退避先 {target} がシンボリックリンク（辿らない）"
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
        return f"{target} に保存できませんでした（{exc}）"
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
