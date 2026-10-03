"""取り込みの控えを読む（ADR-0093 の 3.3・3.6。段階 2c）。

控えは `ccnavi-sync.sh` と、親のブランチを最初に push したときの `ccnavi-git.sh push` が書き、
判定はここで読むだけ。
判定は git もネットワークも起こさない（`tree.py` の前提）ので、統合先と親のブランチの
リモートの姿は、sh が控えに書き出したものしか知らない。

    <控えの置き場>/sync/<リポジトリ>/families/<P>   家族の控え（1 行 1 項目。D33）
    <控えの置き場>/sync/<リポジトリ>/integration/   統合先の控え（done/・層・設定の写しと head）

`<リポジトリ>` はワークスペース自身なら `self`、プロジェクトならその名前。

## 取り込み済みの家族

家族の控えがある家族を「取り込み済みの家族」と呼び、権威を親のブランチ `P`（手元では
`.claude/worktrees/<P>` で HEAD が `P` を指すツリー）に固定する（3.3）。控えの無い家族
（2b より前に送った、origin が無い、一度も push していない）は今の動きのまま（D11）。

家族の控えは墓標として残る（親のワークツリーを片付けても消えない。消すのは人が打つ
`ccnavi-sync.sh --forget <P>` だけ）。控えと統合先の控えから、家族の立ち位置（`Standing`）を決める。

- 閉じた: 統合先の控えの `done/` に親の写しがある（親のワークツリーが無いか、あれば親の写しの
  承認の時刻が同じ）、または控えが `closed`。統合先の `done/` が権威（3.6 の正常系）
- `gone`・`blocked`・控えが壊れている・`present` なのに親のワークツリーが無い: **決まらない**。
  その家族の承認も状態の操作も止める（3.3 の 3、3.6）
- `present` で親のワークツリーがある: 親のブランチの写しだけが本物

## 統合先の控え

`sync/<リポジトリ>/` が無ければ、そのリポジトリは一度も取り込んでいない（`integration` は None で、
今どおり作業ツリーを読む）。在るのに統合先の控えが無い・`head` が無い・壊れている・入れ替えが
終わらないときは `broken` に理由を入れて返す。呼び手は `done/` の検査を何も出さずに通すことはしない
（識別子の再利用を確かめられないので「決まらない」として止める）。

## リンクは辿らない

控えの途中（`sync`・`<リポジトリ>`・`families`・`integration` と、その下の読むファイル）に
シンボリックリンクがあれば読まず、「控えが壊れている」とする（段階 2b のレビューの決定 B4）。
sh は写すときにリンクを落としているが、読む側でも辿らない。

## 入れ替えの一瞬

統合先の控えは `mv` 2 回で入れ替わるので、その間の一瞬だけ `integration/` が無い
（11.4.2 の 10）。入れ替えの途中（`integration.tmp.*`・`integration.old.*` が並んでいる、
`integration/` はあるのに `head` が無い）と分かるときだけ、少し待って読み直す。控えを
一度も書いていないリポジトリでは待たない（hook のたびに待つことになるため）。
"""

from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass

from . import fsio, settings, tree
from . import ticket as ticket_mod

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
_MISSING = "統合先の控えが無い（まだ取り込んでいないか、書けなかった）"
_NO_HEAD = "統合先の控えに head が無い"
_SWAPPING = "統合先の控えの入れ替えが終わらない"
# 入れ替えの一瞬を待つ回数と間隔（秒）。合わせて 0.25 秒ほど。
_RETRIES = 5
_RETRY_WAIT = 0.05
# 控え 1 つ（家族の控え・head）の大きさの上限。超えたら切らずに「壊れている」とする。
_RECORD_LIMIT = 64 * 1024


def repo_key(project: str) -> str:
    """控えを分ける名前。ワークスペース自身は `self`、プロジェクトはその名前。"""
    return project or SELF


def project_of_key(repo: str) -> str:
    """控えの名前から、プロジェクトの名前（ワークスペース自身なら空）。"""
    return "" if repo == SELF else repo


def any_records(state_dir: str) -> bool:
    """取り込みの控えが 1 つでもありうるか（`sync/` が在るか）。無ければ判定は前のまま。"""
    return bool(state_dir) and os.path.lexists(os.path.join(state_dir, SYNC_DIR))


def repo_seen(state_dir: str, repo: str) -> bool:
    """そのリポジトリを取り込んだ跡（`sync/<リポジトリ>/`）が在るか。"""
    return any_records(state_dir) and os.path.lexists(os.path.join(state_dir, SYNC_DIR, repo))


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


def family_names(state_dir: str) -> list[tuple[str, str]]:
    """家族の控えの (リポジトリ, 親の識別子) の並び。書きかけ（`*.tmp.*`）は数えない。"""
    base = os.path.join(state_dir, SYNC_DIR)
    out: list[tuple[str, str]] = []
    for repo in _names(base):
        for name in _names(os.path.join(base, repo, FAMILIES_DIR)):
            if ".tmp." not in name:
                out.append((repo, name))
    return out


def repos(state_dir: str) -> list[str]:
    """控えのあるリポジトリの名前の並び。"""
    return _names(os.path.join(state_dir, SYNC_DIR)) if state_dir else []


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
            fsio.note_read(path, data)
            return data, ""
        except OSError as exc:
            return None, f"{rel} を読めない（{exc.strerror or type(exc).__name__}）"

    def names(self, rel: str) -> tuple[list[str], str]:
        """控えの中のディレクトリのファイルの名前（リンクは落とす）。無ければ空。"""
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
        # 名前の並びも判定の入力（承認の指紋の read_set に入れる）。
        fsio.note_read(directory, "\n".join(out))
        return out, ""


def integration(state_dir: str, repo: str) -> Integration | None:
    """統合先の控え。そのリポジトリを一度も取り込んでいなければ None（今どおり作業ツリーを読む）。

    取り込んだ跡（`sync/<リポジトリ>/`）が在るのに読めなければ、`broken` に理由を入れて返す。
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

    控えが無ければ（None）空で理由も空。壊れていれば空と理由（呼び手は何も出さずに通すことはしない）。
    """
    if integ is None:
        return set(), ""
    if integ.broken:
        return set(), integ.broken
    names, why = integ.names(f"{approved_rel.strip('/')}/done")
    return {n[: -len(".md")] for n in names if n.endswith(".md")}, why


@dataclass(frozen=True)
class DoneCopy:
    """統合先の `done/` の写しの、閉じたかを決めるのに要る欄だけ。"""

    ticket: str
    parent: str
    approved_at: str


def done_copy(integ: Integration, approved_rel: str, ident: str) -> DoneCopy | None:
    """統合先の `done/<識別子>.md` の frontmatter の欄。無い・読めなければ None。

    閉じたかを決めるのに要るのは識別子・親・承認の時刻だけなので、チケットとしての検査
    （範囲の欄など）は掛けない（検査に落ちる古い写しでも、閉じた記録として読む）。
    """
    data, why = integ.file(f"{approved_rel.strip('/')}/done/{ident}.md")
    if data is None or why:
        return None
    try:
        return _copy_fields(data.decode("utf-8"))
    except UnicodeDecodeError:
        return None


def _copy_fields(text: str) -> DoneCopy | None:
    # 同じパッケージの frontmatter の読み方を使う（チケットの検査は掛けない）。
    front, _, problems = ticket_mod._frontmatter(text)
    if not isinstance(front, dict) or problems:
        return None
    record = front.get("ccnavi_approved")
    approved_at = record.get("approved_at") if isinstance(record, dict) else ""
    return DoneCopy(
        ticket=str(front.get("ticket") or ""),
        parent=str(front.get("parent") or ""),
        approved_at=str(approved_at or ""),
    )


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


class Families:
    """1 回の判定の中で、家族の立ち位置と統合先の控えを引く窓口。

    ワークツリーの一覧・家族の控え・統合先の控えは 1 度ずつだけ読む（hook のたびに
    何度も走る `scan` の中で、家族ごとにワークツリーを並べ直さないため）。
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
        """統合先の控えの `done/` の識別子と読めない理由（控えの無いリポジトリは空と空）。"""
        if repo not in self._done:
            self._done[repo] = done_ids(self.integration(repo), self.conf.approved)
        return self._done[repo]

    def standing(self, family_id: str, project: str = "") -> Standing:
        """この家族の立ち位置（3.3 の権威の規則）。"""
        key = (project or "", family_id)
        if key not in self._standings:
            self._standings[key] = self._standing(family_id, project or "")
        return self._standings[key]

    def standing_any(self, family_id: str, project: str | None = None) -> Standing:
        """リポジトリが分かれば `standing`。分からなければ控えのあるリポジトリを全部探す。

        同じ識別子の家族が 2 つ以上のリポジトリにあれば、どれとも決めずに止める。
        """
        if project is not None:
            return self.standing(family_id, project)
        if not self.active:
            return Standing(family_id, SELF)
        hits = [
            self.standing(family_id, project_of_key(repo))
            for repo in repos(self.conf.state)
            if family(self.conf.state, repo, family_id) is not None
        ]
        if not hits:
            return Standing(family_id, SELF)
        # 控えは 1 つでも、同じ名前の親のワークツリーが別のリポジトリにもあれば、
        # どちらの家族か決めない（ワークスペースの人の付けた名前 `web-i0012` と、
        # プロジェクト web の issue 12 の家族など。11.9.3 の 13）
        other = sorted(
            {repo_key(w.project) for w in self.worktrees() if w.name == family_id}
            - {h.repo for h in hits}
        )
        if len(hits) == 1 and not other:
            return hits[0]
        where = ", ".join([h.repo for h in hits] + other)
        return Standing(
            family_id,
            hits[0].repo,
            hits[0].record,
            stop=f"家族 {family_id} の控えが複数のリポジトリ（{where}）にある。どれか決まらない",
        )

    def home_tree(self, family_id: str, project: str) -> tree.Tree | None:
        """親のワークツリー。名前（大文字小文字まで）が家族の識別子で、元が同じリポジトリで、
        HEAD がブランチ `<P>` を指すもの。無ければ None。ファイルだけを読む（git は起こさない）。"""
        work = self._named_tree(family_id, project)
        if work is not None and tree.branch_of(work.root) == family_id:
            return work
        return None

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
        if record is None:
            return Standing(family_id, repo)
        home = self.home_tree(family_id, project)
        if record.state == STATE_CLOSED or self._closed_in_integration(repo, family_id, home):
            return Standing(
                family_id,
                repo,
                record,
                home,
                stop=f"家族 {family_id} は閉じている（統合先の done/ に親の写しがある）",
                closed=True,
            )
        if record.broken:
            stop = f"家族 {family_id} の控えが壊れている（{record.broken}）"
        elif record.state == STATE_GONE:
            stop = (
                f"親のブランチ {family_id} がリモートに無く、統合先にも閉じた記録が無い"
                "（家族の控えが gone）。この家族の状態を決められない"
            )
        elif record.state == STATE_BLOCKED:
            stop = f"取り込みの検査で家族 {family_id} を止めた（{record.reason or '理由なし'}）"
        elif home is None:
            named = self._named_tree(family_id, project)
            busy = tree.busy_of(named.root) if named is not None else ""
            where = f"{tree.WORKTREES_DIR.replace(os.sep, '/')}/{family_id}"
            if busy:
                stop = (
                    f"親のワークツリー（{where}）に途中の操作（{busy}）がある。"
                    "済ませるか取りやめるまで、取り込み済みの家族でどの写しを本物とするかが"
                    "決まらない"
                )
            elif named is not None:
                stop = (
                    f"親のワークツリー（{where}）の HEAD がブランチ {family_id} を指していない。"
                    "取り込み済みの家族でどの写しを本物とするかが決まらない"
                )
            else:
                stop = (
                    f"親のワークツリー（{where}）が無い。"
                    "取り込み済みの家族でどの写しを本物とするかが決まらない"
                    "（家族の控えは、親のワークツリーを片付けても残る）"
                )
        else:
            stop = ""
        return Standing(family_id, repo, record, home, stop=stop)

    def _closed_in_integration(self, repo: str, family_id: str, home: tree.Tree | None) -> bool:
        """統合先の控えの `done/` に、この家族の親の写しがあるか（家族の控えに頼らない）。

        親のワークツリーに承認済みの親の写しがあれば、承認の時刻が同じときだけ閉じたとする
        （同じ識別子の古い家族の写しを、この家族のものと読まない。sh の見方と同じ）。
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
        mine = _home_parent_copy(self.conf, home, family_id)
        if mine is None or not mine.approved_at:
            return True
        return mine.approved_at == closed.approved_at


def _home_parent_copy(
    conf: settings.Settings, home: tree.Tree | None, family_id: str
) -> DoneCopy | None:
    if home is None:
        return None
    base = settings.approved_dir(conf, home.root)
    for state in (ticket_mod.DOING, ticket_mod.DONE):
        text = fsio.read_text(os.path.join(base, state, f"{family_id}.md"))
        if text is not None:
            return _copy_fields(text)
    return None


def standing(conf: settings.Settings, root: str, family_id: str, project: str = "") -> Standing:
    """この家族の立ち位置（3.3 の権威の規則）。1 回だけ引くときの形。"""
    return Families(conf, root).standing(family_id, project)


def standing_any(
    conf: settings.Settings, root: str, family_id: str, project: str | None = None
) -> Standing:
    """リポジトリの分からない家族の立ち位置（`Families.standing_any`）。"""
    return Families(conf, root).standing_any(family_id, project)


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
        return [
            f"控え（{record.path}）の中身を人が確かめ、壊れていれば人が '{sync} --forget {name}' で"
            f"消してから、オンラインで '{sync} {name}' を打ち直してください",
        ]
    if record is not None and record.state == STATE_GONE:
        return [
            f"オンラインで '{sync} {name}' を打つと戻し方が出る。改名・消し間違いなら利用者に"
            f"元の名前 {name} でブランチを戻してもらい、オンラインで '{sync} {name}' を"
            "打ち直してください",
            f"家族を捨てたなら、親のワークツリーを片付けて（'{git} worktree remove "
            f".claude/worktrees/{name}'）、人に '{sync} --forget {name}' で家族の控えを"
            "消してもらってください"
            "（エージェントは打たない）",
        ]
    if record is not None and record.state == STATE_BLOCKED:
        return [
            f"理由を直してから、オンラインで '{sync} {name}' を打ち直してください"
            "（検査し直して通れば present に戻る）"
        ]
    if st.home is None and "途中の操作" in st.stop:
        return [
            "親のワークツリーの途中の操作（merge・rebase など）を済ませるか取りやめてから"
            "打ち直してください"
        ]
    return [
        f"親のワークツリーを切り直してください（'{git} fetch origin {name}' のあと "
        f"'{git} worktree add .claude/worktrees/{name} -b {name} origin/{name}'）。"
        f"別のブランチに居るなら {name} に戻してください。"
        f"閉じた家族なら、オンラインで '{sync}' を打って"
        "統合先を取り込み直してください。捨てた家族なら、人に "
        f"'{sync} --forget {name}' で家族の控えを消してもらってください",
    ]


# ---- 下請け


def _names(directory: str) -> list[str]:
    try:
        return sorted(os.listdir(directory))
    except OSError:
        return []


def _parts(rel: str) -> tuple[tuple[str, ...], str]:
    """相対の綴り（"/" 区切り）を部品に分ける。`.`・`..` と空は受け付けない。"""
    parts = tuple(p for p in rel.split("/") if p)
    if not parts or any(p in (".", "..") for p in parts):
        return (), f"読めない綴り（{rel}）"
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
    """1 行 1 項目（`<鍵> <値>`）の控え。

    リンク・ふつうのファイルでないもの・大きすぎるものは読まない（切って読まない）。
    """
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
            raw = f.read(_RECORD_LIMIT + 1)
        if len(raw) > _RECORD_LIMIT:
            return None, f"控えが大きすぎる（{_RECORD_LIMIT} バイトを超える）"
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
