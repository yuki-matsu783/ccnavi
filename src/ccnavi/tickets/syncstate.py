"""取り込み状態を読む。

取り込み状態は `ccnavi-sync.sh` と、
親のブランチを最初に push したときの `ccnavi-git.sh push` が書き、判定はここで読むだけ。
判定は git もネットワークも起こさない（`tree.py` の前提）ので、統合先と親のブランチの
リモートの状態は、sh が取り込み状態に書き出したものしか知らない。

    <state の置き場>/sync/<リポジトリ>/families/<P>
        親子のチケットの取り込み状態
        （1 行 1 項目。sh は jq を使わない）
    <state の置き場>/sync/<リポジトリ>/integration/
        統合先の取り込み結果（done/・レイヤー・設定のコピーと head）

`<リポジトリ>` はワークスペース自身なら `self`、プロジェクトならその名前。

## 親子のチケットの立ち位置

正とする側は、全部の親子のチケットで同じく親のブランチに固定する。手元では
`.claude/worktrees/<P>`（HEAD のブランチ名の `/` を `-` に置き換えたものが `P` になるツリー）で、
ほかのツリーのコピーでは代用しない。取り込み状態（記録）はキャッシュ
（取得した sha・同期結果・MR 番号）で、記録の有無では判定を変えない。
記録がなくても親のワークツリーがあれば決まる。

- 閉じた: 統合先の取り込み結果の `done/` に親のチケットがある（親のワークツリーが無いか、
  あれば親のチケットの承認の時刻が同じ）。閉じたかの第一の根拠は MR がマージ済みかで、
  それは `ccnavi-sync.sh` がホストに聞いて親の片付けまで行う。取り込み状態の `closed` や
  手元の退避（`logs/archive/`）からは推定しない
- `gone`・`blocked`・取り込み状態が破損している・親のワークツリーが無い・HEAD のブランチ名が
  フォルダ名と合わない: 決まらない。その親子のチケットの承認も状態の操作も止める
- それ以外で親のワークツリーがある: 親のブランチ上のチケットだけが正

## 統合先の取り込み結果

`sync/<リポジトリ>/` が無ければ、そのリポジトリは一度も取り込んでいない（`integration` は None で、
今どおり作業ツリーを読む）。在るのに統合先の取り込み結果が無い・`head` が無い・破損している・入れ替
えが終わらないときは `broken` に理由を入れて返す。
呼び手は `done/` の検査を何も出さずに通すことはしない
（識別子の再利用を確かめられないので「決まらない」として止める）。

## リンクは辿らない

取り込み状態の途中（`sync`・`<リポジトリ>`・`families`・`integration` と、その下の読むファイル）に
シンボリックリンクがあれば読まず、「取り込み状態が不正」とする。sh はコピーするときに
リンクを除いているが、読む側でも辿らない。

## 入れ替えの一瞬

統合先の取り込み結果は `mv` 2 回で入れ替わるので、その間の一瞬だけ `integration/` が無い。
入れ替えの途中（`integration.tmp.*`・`integration.old.*` が並んでいる、
`integration/` はあるのに `head` が無い）と分かるときだけ、少し待って読み直す。取り込み結果を
一度も書いていないリポジトリでは待たない（hook のたびに待つことになるため）。
"""

from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass

from ..infra import fsio, settings, tree
from . import ticket as ticket_mod
from . import ticket_ids, ticket_model

SYNC_DIR = "sync"
FAMILIES_DIR = "families"
INTEGRATION_DIR = "integration"
HEAD_FILE = "head"
# ワークスペース自身の取り込み状態の名前。
SELF = "self"

STATE_PRESENT = "present"
STATE_CLOSED = "closed"
STATE_GONE = "gone"
STATE_BLOCKED = "blocked"
STATES = (STATE_PRESENT, STATE_CLOSED, STATE_GONE, STATE_BLOCKED)

_LINKED = "取り込み状態の途中にシンボリックリンクがある（辿らない）"
_NOT_DIR = "統合先の取り込み結果がディレクトリでない（リンクは辿らない）"
_MISSING = "統合先の取り込み結果がありません（まだ取り込んでいないか、保存できませんでした）"
_NO_HEAD = "統合先の取り込み結果に head がありません。"
_SWAPPING = "統合先の取り込み結果の入れ替えが終わらない"
# 入れ替えの一瞬を待つ回数と間隔（秒）。合わせて 0.25 秒ほど。
_RETRIES = 5
_RETRY_WAIT = 0.05
# 取り込み状態のファイル 1 つ（親子のチケットの取り込み状態・head）の大きさの上限。
# 超えたら切らずに「不正」とする。
_RECORD_LIMIT = 64 * 1024


def repo_key(project: str) -> str:
    """取り込み状態を分ける名前。ワークスペース自身は `self`、プロジェクトはその名前。"""
    return project or SELF


def project_of_key(repo: str) -> str:
    """取り込み状態の名前から、プロジェクトの名前（ワークスペース自身なら空）。"""
    return "" if repo == SELF else repo


def any_records(state_dir: str) -> bool:
    """取り込み状態が 1 つでもありうるか（`sync/` が在るか）。無ければ判定は前のまま。"""
    return bool(state_dir) and os.path.lexists(os.path.join(state_dir, SYNC_DIR))


def repo_seen(state_dir: str, repo: str) -> bool:
    """そのリポジトリを取り込んだ形跡（`sync/<リポジトリ>/`）が在るか。"""
    return any_records(state_dir) and os.path.lexists(os.path.join(state_dir, SYNC_DIR, repo))


# ---- 親子のチケットの取り込み状態


@dataclass(frozen=True)
class Family:
    """親子のチケットの取り込み状態 1 つ。

    `broken` が空でなければ読めなかった理由（`state` は空）。
    """

    name: str
    repo: str
    state: str = ""
    reason: str = ""
    sha: str = ""
    broken: str = ""
    path: str = ""
    # 取り込み状態に書いた親のブランチ名。前の取り込み状態は識別子と同じ名前を書いている。
    branch: str = ""
    # 最後に取り込んだ（origin に合わせた）時刻。UNIX 秒の文字列。前の取り込み状態や読めない値は空。
    fetched_at: str = ""
    # 親を載せる MR の番号。ccnavi-start.sh が保存する。無ければ空。
    mr: str = ""


def family_path(state_dir: str, repo: str, name: str) -> str:
    return os.path.join(state_dir, SYNC_DIR, repo, FAMILIES_DIR, name)


def family(state_dir: str, repo: str, name: str) -> Family | None:
    """親子のチケットの取り込み状態。無ければ None（取り込み済みでない親子のチケット）。"""
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
        return Family(
            name, repo, broken=f"取り込み状態の state を読めない（{state or '空'}）", path=path
        )
    return Family(
        name,
        repo,
        state=state,
        reason=record.get("reason", ""),
        sha=record.get("sha", ""),
        path=path,
        branch=record.get("branch", ""),
        fetched_at=_digits(record.get("fetched_at", "")),
        mr=_digits(record.get("mr", "")),
    )


def _digits(text: str) -> str:
    """数字だけの値。それ以外（空・読めない値）は空。"""
    return text if text.isascii() and text.isdigit() else ""


def family_names(state_dir: str) -> list[tuple[str, str]]:
    """親子のチケットの取り込み状態の (リポジトリ, 親の識別子) のリスト。
    書きかけ（`*.tmp.*`）は数えない。"""
    base = os.path.join(state_dir, SYNC_DIR)
    out: list[tuple[str, str]] = []
    for repo in _names(base):
        for name in _names(os.path.join(base, repo, FAMILIES_DIR)):
            if ".tmp." not in name:
                out.append((repo, name))
    return out


def repos(state_dir: str) -> list[str]:
    """取り込み状態のあるリポジトリの名前のリスト。"""
    return _names(os.path.join(state_dir, SYNC_DIR)) if state_dir else []


# ---- 統合先の取り込み結果


@dataclass(frozen=True)
class Integration:
    """統合先の取り込み結果。`broken` が空でなければ読めなかった理由。"""

    repo: str
    dir: str
    branch: str = ""
    sha: str = ""
    source: str = ""
    broken: str = ""

    def file(self, rel: str) -> tuple[bytes | None, str]:
        """取り込み結果の中のファイル（"/" 区切りの相対）の中身。

        無ければ (None, "")、読めなければ (None, 理由)。途中とファイルそのもののリンクは辿らない。
        """
        parts, why = _parts(rel)
        if why:
            return None, why
        linked = _linked_below(self.dir, parts)
        if linked is None:
            return None, ""
        if linked:
            return None, f"{rel} の途中にシンボリックリンクがある（辿らない）"
        path = os.path.join(self.dir, *parts)
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            fd = os.open(path, flags)
            with os.fdopen(fd, "rb") as f:
                data = f.read()
            return data, ""
        except OSError as exc:
            return None, f"{rel} を読めない（{exc.strerror or type(exc).__name__}）"

    def names(self, rel: str) -> tuple[list[str], str]:
        """取り込み結果の中のディレクトリのファイルの名前（リンクは除く）。無ければ空。"""
        parts, why = _parts(rel)
        if why:
            return [], why
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
        return out, ""


def integration(state_dir: str, repo: str) -> Integration | None:
    """統合先の取り込み結果。そのリポジトリを一度も取り込んでいなければ None
    （今どおり作業ツリーを読む）。

    取り込んだ形跡（`sync/<リポジトリ>/`）が在るのに読めなければ、`broken` に理由を入れて返す。
    """
    if not state_dir or not repo_seen(state_dir, repo):
        return None
    base = os.path.join(state_dir, SYNC_DIR, repo)
    directory = os.path.join(base, INTEGRATION_DIR)
    linked = _linked_below(state_dir, (SYNC_DIR, repo))
    if linked is None:
        return None
    if linked:
        return Integration(repo, directory, broken=_LINKED)
    found = Integration(repo, directory, broken=_MISSING)
    for attempt in range(_RETRIES + 1):
        found = _integration_once(repo, directory)
        if not found.broken:
            return found
        if attempt == _RETRIES or not _swapping(base, directory):
            break
        time.sleep(_RETRY_WAIT)
    if found.broken in (_MISSING, _NO_HEAD) and _swapping(base, directory):
        return Integration(repo, directory, broken=_SWAPPING)
    return found


def _integration_once(repo: str, directory: str) -> Integration:
    try:
        mode = os.lstat(directory).st_mode
    except OSError:
        return Integration(repo, directory, broken=_MISSING)
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        return Integration(repo, directory, broken=_NOT_DIR)
    head = os.path.join(directory, HEAD_FILE)
    if not os.path.lexists(head):
        return Integration(repo, directory, broken=_NO_HEAD)
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


def done_ids(integ: Integration | None, approved_rel: str) -> tuple[set[str], str]:
    """統合先の `done/` にある識別子（ファイル名から。閉じた・取り消し済みの両方）と、読めない理由。

    取り込み結果が無ければ（None）空で理由も空。破損していれば空と理由（呼び手は何も出さずに通すことはしない）。
    """
    if integ is None:
        return set(), ""
    if integ.broken:
        return set(), integ.broken
    names, why = integ.names(f"{approved_rel.strip('/')}/done")
    return {n[: -len(".md")] for n in names if n.endswith(".md")}, why


@dataclass(frozen=True)
class DoneCopy:
    """統合先の `done/` 上のチケットの、閉じたかを決めるのに要る欄だけ。

    照合に使うのは、スクリプトだけが書く着手と取り消しの欄（`base_sha`・`started_at`・
    `cancelled_at`）。
    """

    ticket: str
    parent: str
    base_sha: str = ""
    started_at: str = ""
    cancelled_at: str = ""


# 同じ親かを比べる欄。最初に両方が値を持つ欄で決める。
MATCH_FIELDS = ("base_sha", "started_at", "cancelled_at")


def same_parent(mine: DoneCopy | None, closed: DoneCopy) -> bool:
    """手元の親（`mine`）と統合先の `done/` の親（`closed`）が同じ親チケットか。

    `base_sha` → `started_at` → `cancelled_at` の順に、最初に両方が値を持つ欄が同じなら同じとする。
    空どうしは一致としない（空文字は無いとみなす）。どの欄でも照合できないとき、手元に親が無いときは
    同じとしない（閉じていない側に倒し、止めて戻し方を出す）。
    """
    if mine is None:
        return False
    for name in MATCH_FIELDS:
        a, b = getattr(mine, name), getattr(closed, name)
        if a and b:
            return a == b
    return False


def done_copy(integ: Integration, approved_rel: str, ident: str) -> DoneCopy | None:
    """統合先の `done/<識別子>.md` の frontmatter の欄。無い・読めなければ None。

    閉じたかを決めるのに要るのは識別子・親・着手と取り消しの欄だけなので、チケットとしての検査
    （範囲の欄など）は掛けない（検査に失敗する古いチケットでも、閉じた記録として読む）。
    """
    data, why = integ.file(f"{approved_rel.strip('/')}/done/{ident}.md")
    if data is None or why:
        return None
    try:
        return _copy_fields(data.decode("utf-8"))
    except UnicodeDecodeError:
        return None


def _text(value: object) -> str:
    # 欄の値を文字列で。None と空白だけの値は無いとみなす。
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _copy_fields(text: str) -> DoneCopy | None:
    # 同じパッケージの frontmatter の読み方を使う（チケットの検査は掛けない）。
    front, _, problems = ticket_mod._frontmatter(text)
    if not isinstance(front, dict) or problems:
        return None
    return DoneCopy(
        ticket=_text(front.get("ticket")),
        parent=_text(front.get("parent")),
        base_sha=_text(front.get("base_sha")),
        started_at=_text(front.get("started_at")),
        cancelled_at=_text(front.get("cancelled_at")),
    )


# ---- 親子のチケットの立ち位置


@dataclass(frozen=True)
class Standing:
    """親子のチケットの立ち位置。`record` は取り込み状態（キャッシュ）で、無くてもよい。

    `stop` は決まらない・閉じているので止める理由（空なら止めない）。`closed` は閉じた
    親子のチケット。
    `home` は親のワークツリー（`.claude/worktrees/<P>` で、HEAD のブランチ名を変換したものが
    フォルダ名と一致するもの）。
    `branch` は親のブランチ名。ワークツリーの HEAD から読んだもので、読めなければ空
    （識別子からの逆算はしない）。
    """

    family: str
    repo: str
    record: Family | None = None
    home: tree.Tree | None = None
    stop: str = ""
    closed: bool = False
    branch: str = ""

    @property
    def branch_name(self) -> str:
        """文面に出す親のブランチ名。HEAD から読めなければ取り込み記録（キャッシュ）の名前、
        それも無ければ `<ブランチ名>`。"""
        cached = self.record.branch if self.record is not None else ""
        return self.branch or cached or "<ブランチ名>"


class Families:
    """1 回の判定の中で、親子のチケットの立ち位置と統合先の取り込み結果を引く窓口。

    ワークツリーの一覧・親子のチケットの取り込み状態・統合先の取り込み結果は 1 度ずつだけ読む
    （hook のたびに何度も走る `scan` の中で、親子のチケットごとにワークツリーを並べ直さないため）。
    """

    def __init__(self, conf: settings.Settings, root: str):
        self.conf = conf
        self.root = root
        self.active = any_records(conf.state)
        self._worktrees: list[tree.Tree] | None = None
        self._standings: dict[tuple[str, str], Standing] = {}
        self._integrations: dict[str, Integration | None] = {}
        self._done: dict[str, tuple[set[str], str]] = {}

    def worktrees(self) -> list[tree.Tree]:
        if self._worktrees is None:
            self._worktrees = tree.worktrees(self.root, self.conf.projects)
        return self._worktrees

    def integration(self, repo: str) -> Integration | None:
        if repo not in self._integrations:
            self._integrations[repo] = integration(self.conf.state, repo) if self.active else None
        return self._integrations[repo]

    def done(self, repo: str) -> tuple[set[str], str]:
        """統合先の取り込み結果の `done/` の識別子と読めない理由
        （取り込み結果の無いリポジトリは空と空）。"""
        if repo not in self._done:
            self._done[repo] = done_ids(self.integration(repo), self.conf.approved)
        return self._done[repo]

    def standing(self, family_id: str, project: str = "") -> Standing:
        """この親子のチケットの立ち位置（正とする側の規則）。"""
        key = (project or "", family_id)
        if key not in self._standings:
            self._standings[key] = self._standing(family_id, project or "")
        return self._standings[key]

    def standing_any(self, family_id: str, project: str | None = None) -> Standing:
        """リポジトリが分かれば `standing`。分からなければ、取り込み状態か同じ名前の親の
        ワークツリーのあるリポジトリを全部探す。

        同じ識別子の親子のチケットが 2 つ以上のリポジトリにあれば、どれとも決めずに止める
        （ワークスペースのユーザの付けた名前 `web-i0012` と、プロジェクト web の issue 12 の
        親子のチケットなど）。どこにも無ければワークスペース自身として引く（親のワークツリーが
        無いので止まる）。
        """
        if project is not None:
            return self.standing(family_id, project)
        repos_found: list[str] = []
        if self.active:
            repos_found = [
                r for r in repos(self.conf.state) if family(self.conf.state, r, family_id)
            ]
        for w in self.worktrees():
            if w.name == family_id and repo_key(w.project) not in repos_found:
                repos_found.append(repo_key(w.project))
        if not repos_found:
            return self.standing(family_id, "")
        if len(repos_found) == 1:
            return self.standing(family_id, project_of_key(repos_found[0]))
        hits = [self.standing(family_id, project_of_key(r)) for r in repos_found]
        where = ", ".join(repos_found)
        return Standing(
            family_id,
            hits[0].repo,
            hits[0].record,
            stop=(
                f"親子のチケット {family_id} が複数のリポジトリ（{where}）にある。どれか決まらない"
            ),
        )

    def home_tree(self, family_id: str, project: str) -> tree.Tree | None:
        """親のワークツリー。名前（大文字小文字まで）が親の識別子で、元が同じリポジトリで、
        HEAD のブランチ名を変換したものが名前と一致するもの。無ければ None。ファイルだけを読む
        （git は起こさない）。"""
        work = self._named_tree(family_id, project)
        if work is not None and head_branch(work.root, family_id):
            return work
        return None

    def branch(self, family_id: str, project: str = "") -> str:
        """親のブランチ名。親のワークツリー（`.claude/worktrees/<P>`）の HEAD から読む。
        読めない・変換した識別子が名前と合わないときは空。ファイルだけを読む（git は起こさない）。

        識別子から逆算しない。取り込み記録の `branch` はキャッシュで、ここでは使わない。
        """
        home = self.home_tree(family_id, project)
        return head_branch(home.root, family_id) if home is not None else ""

    def branch_any(self, family_id: str, project: str | None = None) -> str:
        """リポジトリの分からない親子のチケットの親のブランチ名。

        立ち位置（`standing_any`）の親のブランチ名。決まらなければ空。
        """
        return self.standing_any(family_id, project).branch

    def _all_trees(self) -> list[tree.Tree]:
        return [tree.main_tree(self.root), *tree.projects(self.conf.projects), *self.worktrees()]

    def _named_tree(self, family_id: str, project: str) -> tree.Tree | None:
        if not tree.exact_name(self.root, family_id):
            return None
        for work in self.worktrees():
            if work.name == family_id and work.project == project:
                return work
        return None

    def _standing(self, family_id: str, project: str) -> Standing:
        repo = repo_key(project)
        record = family(self.conf.state, repo, family_id) if self.active else None
        branch = self.branch(family_id, project)
        home = self.home_tree(family_id, project)
        if self._closed_in_integration(repo, family_id, home):
            return Standing(
                family_id,
                repo,
                record,
                home,
                stop=(
                    f"親子のチケット {family_id} は閉じている"
                    "（統合先の done/ に親のチケットがある）"
                ),
                closed=True,
                branch=branch,
            )
        if record is not None and record.broken:
            stop = f"親子のチケット {family_id} の取り込み状態が不正（{record.broken}）"
        elif record is not None and record.state == STATE_GONE:
            stop = (
                f"親のブランチ {record.branch or branch or family_id} がリモートに無く、"
                "統合先にもクローズ済みの記録がありません（取り込み状態が gone）。"
                "この親子のチケットの状態を決められない"
            )
        elif record is not None and record.state == STATE_BLOCKED:
            why = record.reason or "理由なし"
            stop = f"取り込みの検査で親子のチケット {family_id} を止めた（{why}）"
        elif home is None:
            named = self._named_tree(family_id, project)
            busy = tree.busy_of(named.root) if named is not None else ""
            where = f"{tree.WORKTREES_DIR.replace(os.sep, '/')}/{family_id}"
            if busy:
                stop = (
                    f"親のワークツリー（{where}）に途中の操作（{busy}）がある。"
                    "済ませるか取りやめるまで、どのチケットを正とするかが決まらない"
                )
            elif named is not None:
                stop = (
                    f"親のワークツリー（{where}）の HEAD のブランチ名を変換した識別子が"
                    f"フォルダ名（{family_id}）と合わない。"
                    "どのチケットを正とするかが決まらない"
                )
            else:
                stop = (
                    f"親のワークツリー（{where}）がありません。"
                    "ほかのツリーのコピーでは代用しない（どのチケットを正とするかが決まらない）"
                )
        else:
            stop = ""
        return Standing(family_id, repo, record, home, stop=stop, branch=branch)

    def _closed_in_integration(self, repo: str, family_id: str, home: tree.Tree | None) -> bool:
        """統合先の取り込み結果の `done/` に、この親子のチケットの親チケットがあるか。

        取り込み状態には頼らない。

        親のワークツリーの承認済みの親のチケットと、着手と取り消しの欄（`same_parent`）で同じ親だと
        言えるときだけ閉じたとする。同じ識別子の古い親子のチケットを、今のものと読まない。手元に親が
        無いときと、どの欄でも照合できないときは閉じていない側に倒す（sh の見方と同じ）。
        """
        ids, why = self.done(repo)
        if why or family_id not in ids:
            return False
        integ = self.integration(repo)
        if integ is None:
            return False
        closed = done_copy(integ, self.conf.approved, family_id)
        if closed is None or closed.ticket != family_id or closed.parent:
            return False
        return same_parent(_home_parent_copy(self.conf, home, family_id), closed)


def head_branch(tree_root: str, family_id: str) -> str:
    """そのツリーの HEAD が指すブランチ名。変換（`ticket_ids.branch_to_id`）した識別子が
    `family_id` と一致するときだけ返し、そうでなければ空。ファイルだけを読む。"""
    head = tree.branch_of(tree_root)
    if head and tree.nfc(ticket_ids.branch_to_id(head)) == family_id:
        return head
    return ""


def _home_parent_copy(
    conf: settings.Settings, home: tree.Tree | None, family_id: str
) -> DoneCopy | None:
    if home is None:
        return None
    base = settings.approved_dir(conf, home.root)
    for state in (ticket_model.DOING, ticket_model.DONE):
        text = fsio.read_text(os.path.join(base, state, f"{family_id}.md"))
        if text is not None:
            return _copy_fields(text)
    return None


def standing(conf: settings.Settings, root: str, family_id: str, project: str = "") -> Standing:
    """この親子のチケットの立ち位置（正とする側の規則）。1 回だけ引くときの形。"""
    return Families(conf, root).standing(family_id, project)


def standing_any(
    conf: settings.Settings, root: str, family_id: str, project: str | None = None
) -> Standing:
    """リポジトリの分からない親子のチケットの立ち位置（`Families.standing_any`）。"""
    return Families(conf, root).standing_any(family_id, project)


def same_tree(a: str, b: str) -> bool:
    """同じツリーのルートか（表記を揃えて比べる）。"""
    return (
        bool(a)
        and bool(b)
        and os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))
    )


def guidance(root: str, st: Standing) -> list[str]:
    """止めたときの解き方。1 行ずつ。"""
    sync = settings.script_command(root, "ccnavi-sync.sh")
    git = settings.script_command(root, "ccnavi-git.sh")
    name = st.family
    branch = st.branch_name
    if st.closed:
        return [
            f"親子のチケット {name} は閉じている。状態の操作はありません。"
            "親のワークツリーは片付けてよい"
        ]
    record = st.record
    if record is not None and record.broken:
        return [
            f"取り込み状態（{record.path}）の中身をユーザが確かめてください。"
            f"破損していればユーザが '{sync} --forget {name}' で"
            f"削除してから、オンラインで '{sync} {name}' を再実行してください",
        ]
    if record is not None and record.state == STATE_GONE:
        return [
            f"オンラインで '{sync} {name}' を実行すると戻し方が出る。改名・消し間違いならユーザに"
            f"元の名前 {branch} でブランチを戻してもらい、オンラインで '{sync} {name}' を"
            "再実行してください",
            f"親子のチケットを捨てたなら、親のワークツリーを片付けて（'{git} worktree remove "
            f".claude/worktrees/{name}'）、ユーザに '{sync} --forget {name}' で"
            "親子のチケットの取り込み状態を削除してもらってください"
            "（エージェントは実行しません）",
        ]
    if record is not None and record.state == STATE_BLOCKED:
        return [
            f"理由を直してから、オンラインで '{sync} {name}' を再実行してください"
            "（検査し直して通れば present に戻る）"
        ]
    if st.home is None and "途中の操作" in st.stop:
        return [
            "親のワークツリーの途中の操作（merge・rebase など）を済ませるか取りやめてから"
            "再実行してください"
        ]
    if st.home is None and "合わない" in st.stop:
        return [
            f"親のワークツリー（.claude/worktrees/{name}）の HEAD を、ブランチ名の `/` を `-` に"
            f"置き換えると {name} になるブランチ（{branch}）に戻してください"
        ]
    return [
        f"親のワークツリーを切り直してください（'{git} fetch origin {branch}' のあと "
        f"'{git} worktree add .claude/worktrees/{name} {branch}'。ブランチ名の `/` を `-` に"
        "置き換えたものがフォルダ名になる）。"
        f"クローズ済みの親子のチケットなら、オンラインで '{sync}' を実行して。"
        "統合先を取り込み直してください。捨てた親子のチケットなら、ユーザに "
        f"'{sync} --forget {name}' で取り込み状態を削除してもらってください",
    ]


# ---- 下請け


def _names(directory: str) -> list[str]:
    try:
        return sorted(os.listdir(directory))
    except OSError:
        return []


def _parts(rel: str) -> tuple[tuple[str, ...], str]:
    """相対パス（"/" 区切り）を部品に分ける。`.`・`..` と空は受け付けない。"""
    parts = tuple(p for p in rel.split("/") if p)
    if not parts or any(p in (".", "..") for p in parts):
        return (), f"読めないパス（{rel}）"
    return parts, ""


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
    """1 行 1 項目（`<鍵> <値>`）の取り込み状態。

    リンク・ふつうのファイルでないもの・大きすぎるものは読まない（切って読まない）。
    """
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        return None, f"取り込み状態を読めない（{exc.strerror or type(exc).__name__}）"
    if stat.S_ISLNK(mode):
        return None, "取り込み状態がシンボリックリンク（辿らない）"
    if not stat.S_ISREG(mode):
        return None, "取り込み状態がふつうのファイルでない"
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as f:
            raw = f.read(_RECORD_LIMIT + 1)
        if len(raw) > _RECORD_LIMIT:
            return None, f"取り込み状態が大きすぎる（{_RECORD_LIMIT} バイトを超える）"
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"取り込み状態を読めない（{type(exc).__name__}）"
    record: dict[str, str] = {}
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        if key and key not in record:
            record[key] = value.strip()
    return record, ""
