"""取り込みの控えを読む（ADR-0093 の 3.3・3.6。段階 2c）。

控えは `ccnavi-sync.sh`（と `ccnavi-git.sh push` の移り目）が書き、判定はここで読むだけ。
判定は git もネットワークも起こさない（`tree.py` の前提）ので、統合先と親のブランチの
リモートの姿は、sh が控えに書き出したものしか知らない。

    <控えの置き場>/sync/<リポジトリ>/families/<P>   家族の控え（1 行 1 項目。D33）
    <控えの置き場>/sync/<リポジトリ>/integration/   統合先の控え（done/・層・設定の写しと head）

`<リポジトリ>` はワークスペース自身なら `self`、プロジェクトならその名前。

## 取り込み済みの家族

家族の控えがある家族を「取り込み済みの家族」と呼び、権威を親のブランチ `P`（手元では
`.claude/worktrees/<P>` で HEAD が `P` を指すツリー）に固定する（3.3）。控えの無い家族
（2b より前に送った、origin が無い、一度も push していない）は今の動きのまま（D11）。

控えの `state` と親のワークツリーから、家族の立ち位置（`Standing`）を決める。

- `present` で親のワークツリーがある: 親のブランチの写しだけが本物
- `closed`: 家族は閉じている（統合先の `done/` に親の写しがある。3.6 の正常系）
- `gone`・`blocked`・控えが壊れている・`present` なのに親のワークツリーが無い: **決まらない**。
  その家族の承認も状態の操作も止める（3.3 の 3、3.6）

## リンクは辿らない

控えの途中（`sync`・`<リポジトリ>`・`families`・`integration` と、その下の読むファイル）に
シンボリックリンクがあれば読まず、「控えが壊れている」とする（段階 2b のレビューの決定 B4）。
sh は写すときにリンクを落としているが、読む側でも辿らない。

## 入れ替えの一瞬

統合先の控えは `mv` 2 回で入れ替わるので、その間の一瞬だけ `integration/` が無い
（11.4.2 の 10）。入れ替えの途中（`integration.tmp.*`・`integration.old.*` が並んでいる、
`integration/` はあるのに `head` が無い）と分かるときだけ、少し待って読み直す。控えを
一度も書いていないワークスペースでは待たない（hook のたびに待つことになるため）。
"""

from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass

from . import fsio, settings, tree

SYNC_DIR = "sync"
FAMILIES_DIR = "families"
INTEGRATION_DIR = "integration"
HEAD_FILE = "head"
# ワークスペース自身の控えの名前。
SELF = "self"

STATE_PRESENT = "present"
STATE_CLOSED = "closed"
STATE_GONE = "gone"
STATE_BLOCKED = "blocked"
STATES = (STATE_PRESENT, STATE_CLOSED, STATE_GONE, STATE_BLOCKED)

_LINKED = "控えの途中にシンボリックリンクがある（辿らない）"
_NOT_DIR = "統合先の控えがディレクトリでない（リンクは辿らない）"
# 入れ替えの一瞬を待つ回数と間隔（秒）。合わせて 0.25 秒ほど。
_RETRIES = 5
_RETRY_WAIT = 0.05


def repo_key(project: str) -> str:
    """控えを分ける名前。ワークスペース自身は `self`、プロジェクトはその名前。"""
    return project or SELF


def any_records(state_dir: str) -> bool:
    """取り込みの控えが 1 つでもありうるか（`sync/` が在るか）。無ければ判定は前のまま。"""
    return bool(state_dir) and os.path.lexists(os.path.join(state_dir, SYNC_DIR))


# ---- 家族の控え


@dataclass(frozen=True)
class Family:
    """家族の控え 1 つ。`broken` が空でなければ読めなかった理由（`state` は空）。"""

    name: str
    repo: str
    state: str = ""
    reason: str = ""
    sha: str = ""
    broken: str = ""
    path: str = ""


def family_path(state_dir: str, repo: str, name: str) -> str:
    return os.path.join(state_dir, SYNC_DIR, repo, FAMILIES_DIR, name)


def family(state_dir: str, repo: str, name: str) -> Family | None:
    """家族の控え。無ければ None（取り込み済みでない家族）。"""
    if not state_dir or not name or not any_records(state_dir):
        return None
    path = family_path(state_dir, repo, name)
    linked = _linked_below(state_dir, (SYNC_DIR, repo, FAMILIES_DIR, name))
    if linked is None:
        return None
    if linked:
        return Family(name, repo, broken=_LINKED, path=path)
    record, why = _read_record(path)
    if record is None:
        return Family(name, repo, broken=why, path=path)
    state = record.get("state", "")
    if state not in STATES:
        return Family(name, repo, broken=f"控えの state を読めない（{state or '空'}）", path=path)
    return Family(
        name,
        repo,
        state=state,
        reason=record.get("reason", ""),
        sha=record.get("sha", ""),
        path=path,
    )


# ---- 統合先の控え


@dataclass(frozen=True)
class Integration:
    """統合先の控え。`broken` が空でなければ読めなかった理由。"""

    repo: str
    dir: str
    branch: str = ""
    sha: str = ""
    source: str = ""
    broken: str = ""

    def file(self, rel: str) -> tuple[bytes | None, str]:
        """控えの中のファイル（"/" 区切りの相対）の中身。

        無ければ (None, "")、読めなければ (None, 理由)。途中とファイルそのもののリンクは辿らない。
        """
        parts = tuple(p for p in rel.split("/") if p)
        if not parts or any(p in (".", "..") for p in parts):
            return None, f"読めない綴り（{rel}）"
        linked = _linked_below(self.dir, parts)
        if linked is None:
            return None, ""
        if linked:
            return None, f"{rel} の途中にシンボリックリンクがある（辿らない）"
        path = os.path.join(self.dir, *parts)
        try:
            with open(path, "rb") as f:
                data = f.read()
            fsio.note_read(path, data)
            return data, ""
        except OSError as exc:
            return None, f"{rel} を読めない（{exc.strerror or type(exc).__name__}）"

    def names(self, rel: str) -> tuple[list[str], str]:
        """控えの中のディレクトリのファイルの名前（リンクは落とす）。無ければ空。"""
        parts = tuple(p for p in rel.split("/") if p)
        linked = _linked_below(self.dir, parts)
        if linked is None:
            return [], ""
        if linked:
            return [], f"{rel} の途中にシンボリックリンクがある（辿らない）"
        directory = os.path.join(self.dir, *parts)
        try:
            entries = sorted(os.listdir(directory))
        except OSError as exc:
            return [], f"{rel} を読めない（{exc.strerror or type(exc).__name__}）"
        out = []
        for name in entries:
            try:
                mode = os.lstat(os.path.join(directory, name)).st_mode
            except OSError:
                continue
            if stat.S_ISREG(mode):
                out.append(name)
        # 名前の並びも判定の入力（承認の指紋の read_set に入れる）。
        fsio.note_read(directory, "\n".join(out))
        return out, ""


def integration(state_dir: str, repo: str) -> Integration | None:
    """統合先の控え。無ければ None（一度も取り込んでいない。今どおり作業ツリーを読む）。"""
    if not state_dir or not any_records(state_dir):
        return None
    base = os.path.join(state_dir, SYNC_DIR, repo)
    directory = os.path.join(base, INTEGRATION_DIR)
    linked = _linked_below(state_dir, (SYNC_DIR, repo))
    if linked is None:
        return None
    if linked:
        return Integration(repo, directory, broken=_LINKED)
    for attempt in range(_RETRIES + 1):
        found = _integration_once(repo, directory)
        if found is not None:
            return found
        if attempt == _RETRIES or not _swapping(base, directory):
            return None
        time.sleep(_RETRY_WAIT)
    return None


def _integration_once(repo: str, directory: str) -> Integration | None:
    try:
        mode = os.lstat(directory).st_mode
    except OSError:
        return None
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        return Integration(repo, directory, broken=_NOT_DIR)
    head = os.path.join(directory, HEAD_FILE)
    if not os.path.lexists(head):
        return None
    record, why = _read_record(head)
    if record is None:
        return Integration(repo, directory, broken=why)
    return Integration(
        repo,
        directory,
        branch=record.get("branch", ""),
        sha=record.get("sha", ""),
        source=record.get("source", ""),
    )


def _swapping(base: str, directory: str) -> bool:
    """入れ替えの途中に見えるか。途中の名前が並んでいるか、`integration/` に `head` が無い。"""
    if os.path.isdir(directory) and not os.path.lexists(os.path.join(directory, HEAD_FILE)):
        return True
    try:
        names = os.listdir(base)
    except OSError:
        return False
    prefixes = (INTEGRATION_DIR + ".tmp.", INTEGRATION_DIR + ".old.")
    return any(n.startswith(prefixes) for n in names)


def done_ids(integ: Integration | None, approved_rel: str) -> set[str]:
    """統合先の `done/` にある識別子（ファイル名から。閉じた・取り消し済みの両方）。"""
    if integ is None or integ.broken:
        return set()
    names, _ = integ.names(f"{approved_rel.strip('/')}/done")
    return {n[: -len(".md")] for n in names if n.endswith(".md")}


# ---- 家族の立ち位置


@dataclass(frozen=True)
class Standing:
    """家族の立ち位置。`record` が None なら取り込み済みでない（今の動きのまま）。

    `stop` は決まらない・閉じているので止める理由（空なら止めない）。`closed` は閉じた家族。
    `home` は親のワークツリー（`.claude/worktrees/<P>` で HEAD が `P` を指すもの）。
    """

    family: str
    repo: str
    record: Family | None = None
    home: tree.Tree | None = None
    stop: str = ""
    closed: bool = False

    @property
    def imported(self) -> bool:
        return self.record is not None


def standing(conf: settings.Settings, root: str, family_id: str, project: str = "") -> Standing:
    """この家族の立ち位置（3.3 の権威の規則）。"""
    repo = repo_key(project)
    record = family(conf.state, repo, family_id)
    if record is None:
        return Standing(family_id, repo)
    home = home_tree(conf, root, family_id, project)
    if record.broken:
        stop = f"家族 {family_id} の控えが壊れている（{record.broken}）"
    elif record.state == STATE_GONE:
        stop = (
            f"親のブランチ {family_id} がリモートに無く、統合先にも閉じた記録が無い"
            "（家族の控えが gone）。この家族の状態を決められない"
        )
    elif record.state == STATE_BLOCKED:
        stop = f"取り込みの検査で家族 {family_id} を止めた（{record.reason or '理由なし'}）"
    elif record.state == STATE_CLOSED:
        return Standing(
            family_id,
            repo,
            record,
            home,
            stop=f"家族 {family_id} は閉じている（統合先の done/ に親の写しがある）",
            closed=True,
        )
    elif home is None:
        stop = (
            f"親のワークツリー（{tree.WORKTREES_DIR.replace(os.sep, '/')}/{family_id}）が無いか、"
            f"その HEAD がブランチ {family_id} を指していない。取り込み済みの家族の権威が決まらない"
        )
    else:
        stop = ""
    return Standing(family_id, repo, record, home, stop=stop)


def standing_any(conf: settings.Settings, root: str, family_id: str) -> Standing:
    """リポジトリの分からない家族の立ち位置。ワークスペースとプロジェクトの控えを順に探す。"""
    if not any_records(conf.state):
        return Standing(family_id, SELF)
    for project in ("", *(p.project for p in tree.projects(conf.projects))):
        found = standing(conf, root, family_id, project)
        if found.imported:
            return found
    return Standing(family_id, SELF)


def home_tree(conf: settings.Settings, root: str, family_id: str, project: str) -> tree.Tree | None:
    """親のワークツリー。名前（大文字小文字まで）が家族の識別子で、元が同じリポジトリで、
    HEAD がブランチ `<P>` を指すもの。無ければ None。ファイルだけを読む（git は起こさない）。"""
    if not tree.exact_name(root, family_id):
        return None
    for work in tree.worktrees(root, conf.projects):
        if work.name != family_id or work.project != project:
            continue
        if tree.branch_of(work.root) == family_id:
            return work
    return None


def same_tree(a: str, b: str) -> bool:
    """同じツリーのルートか（綴りを揃えて比べる）。"""
    return (
        bool(a)
        and bool(b)
        and os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))
    )


def guidance(root: str, st: Standing) -> list[str]:
    """止めたときの解き方（3.6 の案内）。1 行ずつ。"""
    sync = settings.script_command(root, "ccnavi-sync.sh")
    git = settings.script_command(root, "ccnavi-git.sh")
    name = st.family
    if st.closed:
        return [f"家族 {name} は閉じている。状態の操作は無い。親のワークツリーは片付けてよい"]
    record = st.record
    if record is not None and record.broken:
        return [f"控え（{record.path}）を消して '{sync} {name}' を打ち直す"]
    if record is not None and record.state == STATE_GONE:
        return [
            f"'{sync} {name}' を打つと戻し方が出る。改名・消し間違いなら利用者に元の名前 {name} で"
            f"ブランチを戻してもらい、'{sync} {name}' を打ち直す",
            f"家族を捨てたなら、親のワークツリーを片付けて（'{git} worktree remove "
            f".claude/worktrees/{name}'）'{sync}' を打つ",
        ]
    if record is not None and record.state == STATE_BLOCKED:
        return [f"理由を直してから '{sync} {name}' を打ち直す（検査し直して通れば present に戻る）"]
    return [
        f"親のワークツリーを切り直す（'{git} fetch origin {name}' のあと "
        f"'{git} worktree add .claude/worktrees/{name} -b {name} origin/{name}'）。"
        f"別のブランチに居るなら {name} に戻す",
    ]


# ---- 下請け


def _linked_below(base: str, parts: tuple[str, ...]) -> bool | None:
    """base の下の parts を順に lstat して、リンクがあれば True。途中で無ければ None。"""
    path = base
    for part in parts:
        path = os.path.join(path, part)
        try:
            mode = os.lstat(path).st_mode
        except OSError:
            return None
        if stat.S_ISLNK(mode):
            return True
    return False


def _read_record(path: str) -> tuple[dict[str, str] | None, str]:
    """1 行 1 項目（`<鍵> <値>`）の控え。リンク・ふつうのファイルでないものは読まない。"""
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        return None, f"控えを読めない（{exc.strerror or type(exc).__name__}）"
    if stat.S_ISLNK(mode):
        return None, "控えがシンボリックリンク（辿らない）"
    if not stat.S_ISREG(mode):
        return None, "控えがふつうのファイルでない"
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as f:
            raw = f.read(64 * 1024)
        text = raw.decode("utf-8")
        fsio.note_read(path, raw)
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"控えを読めない（{type(exc).__name__}）"
    record: dict[str, str] = {}
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        if key and key not in record:
            record[key] = value.strip()
    return record, ""
