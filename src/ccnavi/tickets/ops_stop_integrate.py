"""Stop で、直接作業が `integrate` まで進んでいないときの促し。

チケットに結び付かない直接作業のワークツリー（`.claude/worktrees/<名前>`。ブランチも同じ名前）で
終えようとしたとき、作業が残っていて `integrate <名前>` の記録も無ければ、1 回だけ止めて
`integrate` の打ち方と、staged で止めてユーザに中身を確かめてもらう手順を返す。hook 自身は
`integrate` を打たない。促すだけ。

## 促す条件

調べる対象は、cwd のツリーと、このセッションが書き込んだ直接作業のツリー（候補の記録。
`direct_candidates`）。チケット制御の有無は問わない。1 本のツリーが次の全部を満たすとき。

- `.claude/worktrees/<名前>` のワークツリーで、今のブランチが名前と同じ
- チケットに結び付かない。`ccnavi-git.sh integrate` と同じ判定（`bound_reason`。全ツリーの
  承認済みチケット・提案の `<名前>.md`、手元の退避、origin に在る親のブランチのワークツリー）を
  使うので、促したのに `integrate` が断る食い違いは起きない
- 作業が残っている。ワークツリーに未コミットの変更がある（追跡していないファイルも数える）か、
  統合先に入っていないコミットがある
- `integrate <名前>` の記録が無い。統合先のツリーの git ディレクトリの `ccnavi-integrate/<名前>/` が
  無く、`ccnavi-integrate-done/<名前>`（片付けた記録の写し）も、写しの `branch_sha` が今の HEAD と
  同じで未コミットの変更が無い、という形では無い（名前を使い直した作業を、前の写しで黙らせない）

## 3 つの結果

1 本を調べた結果は `HIT`（当たる）・`CLEAR`（当たらない。確認済み）・
`UNKNOWN`（期限不足・git の失敗・決められない）のどれか。外してよい `CLEAR`（`drop`）は、
ディレクトリが無い・HEAD のブランチが名前と違う・チケットに結び付く・`integrate` が受けない名前、
のときだけ。`UNKNOWN` は候補を外さず、促した記録も
残さない。git は、期限（`time.monotonic` の絶対時刻）を過ぎてから起こさない（`gitcmd.run_until`）。

## 統合先

`ccnavi_integration`（`ccnavi-common-state.sh`）と同じ。そのリポジトリの手元の `origin/HEAD`、
無ければ `origin/main`・`origin/master`。origin の無いリポジトリでは決まらない。
決まらなければ `integrate` が断るので、促さずに理由を言う。

## 促さずに理由を言う

`integrate` の前提を満たせないとき。統合先をチェックアウトしているツリーが無い、そのツリーに
未コミットの変更や途中の操作がある。`blocked` に理由を入れて返し、呼び手が止めずに報告する。

## 1 回だけ

鍵は `integrate:<名前>`、値は状態の要約（促すか理由か・未コミットの有無・統合先に入っていない
コミットの数・HEAD）。同じ要約では 1 回だけで、進めばまた言う。記録は `finish` の促しと同じ
`nudged-<セッション>.json`。

git とファイルだけを読む。ネットワークには出ない（`docs/claude/exe-boundary.md`）。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from ..infra import fsio, gitcmd, pathcase, settings, tree
from . import archive, direct_candidates, ops_close, ops_stop, ticket_ids, ticket_model

# Stop で直接作業の `integrate` を促すときの理由コード。止めるのは 1 回だけの促しで、
# 判定の deny ではない。
CODE_INTEGRATE_NUDGE = "NUDGE_INTEGRATE"

# 記録の鍵の頭。チケットの識別子・`request:` の鍵と分ける。
_KEY_HEAD = "integrate:"
# `ccnavi-git.sh integrate` が記録を置く、統合先のツリーの git ディレクトリの中の名前。
_RECORD_DIR = "ccnavi-integrate"
_DONE_DIR = "ccnavi-integrate-done"
# 統合先のツリーで、あれば `integrate` が断る途中の操作の目印のうち、`tree.BUSY_MARKS` に無いもの。
_SQUASH_MSG = "SQUASH_MSG"
# 理由に並べる未コミットのパスの数。
_LISTED = 5


@dataclass
class Direct:
    """Stop で `integrate` を促す（か、促せない理由を言う）相手。

    `blocked` が空でなければ、`integrate` の前提を満たせないので促さず、理由を言う。
    `integ_tree` は統合先をチェックアウトしているツリー（見つからなければ空）。
    """

    name: str
    worktree: str
    head: str
    dirty: bool
    ahead: int
    integration: str
    integ_tree: str = ""
    blocked: str = ""

    @property
    def key(self) -> str:
        return f"{_KEY_HEAD}{self.name}"

    @property
    def digest(self) -> str:
        kind = "blocked" if self.blocked else "nudge"
        return f"{kind}:{'dirty' if self.dirty else 'clean'}:{self.ahead}@{self.head}"


# 1 本のツリーを調べた結果。
HIT = "hit"  # 促すか、理由を言う相手になる
CLEAR = "clear"  # 今は促す必要が無いと確認できた
UNKNOWN = "unknown"  # 期限不足・git の失敗・決められないものがあり、確認できない


@dataclass
class Probe:
    """1 本のツリーを調べた結果。

    `drop` は `CLEAR` のうち「候補から外してよい」と確認できたもの（ディレクトリが無い、HEAD の
    ブランチが名前と違う、チケットに結び付く、`integrate` が受けない名前）。`UNKNOWN` のときは
    候補を外さず、促した記録も残さない。
    """

    state: str
    found: Direct | None = None
    drop: bool = False


def _expired(deadline: float | None) -> bool:
    """期限（`time.monotonic` の絶対時刻）の手前で、git を起こすのをやめる時点か。"""
    if deadline is None:
        return False
    return deadline - time.monotonic() - gitcmd.DEADLINE_MARGIN <= gitcmd.DEADLINE_FLOOR


def _run(
    cwd: str,
    args: list[str],
    deadline: float | None = None,
    timeout: float = ops_close.TIMEOUT_SECONDS,
) -> gitcmd.Done:
    """git を起こす。期限があれば、期限を過ぎてから起こさない（`gitcmd.run_until`）。"""
    if deadline is None:
        return gitcmd.run(cwd, args, timeout)
    return gitcmd.run_until(cwd, args, deadline, timeout)


def _out(cwd: str, args: list[str], deadline: float | None = None) -> tuple[int, str]:
    done = _run(cwd, args, deadline)
    if done.failure:
        return 1, ""
    return done.code, done.out


def direct_at_stop(
    root: str,
    conf: settings.Settings,
    cwd: str,
    session: str = "",
    deadline: float | None = None,
) -> Direct | None:
    """メインエージェントが終わろうとしたとき、`integrate` を促す（か理由を言う）直接作業。

    1. cwd のツリーを調べる。当たり、まだ促していなければ返す。
    2. 記録の候補（`direct_candidates`）を、`checked` の小さい順（未調査が先、同じなら `seen` の
       新しい順）に調べる。cwd と同じものは除く。調べ始めた候補は、結果が「不明」でも `checked` を
       今にする。「外してよい」と確認できた候補は記録から外す。当たり、まだ促していないものが
       見つかったらそこで止めて返す。
    3. 期限が足りなくなったら打ち切る。それまでの `checked` と外す候補は保存する。

    `session` が空なら候補は見ない。`deadline` は `time.monotonic` の絶対時刻で、省けば期限なし。
    git かファイルを読めなければ None（促しは保護ではないので、読めないときは何も出さずに通す）。
    """
    here = tree.tree_of(root, cwd or os.getcwd(), conf.projects)
    skip = ""
    if here is not None and not here.is_main:
        got = probe(root, conf, here, deadline)
        skip = here.name
        if got.found is not None and not _already(conf, session, got.found):
            return got.found
    if not session or not conf.state:
        return None
    entries = direct_candidates.load(conf, root, session)
    entries = [e for e in entries if not (skip and pathcase.fold(e["name"]) == pathcase.fold(skip))]
    entries.sort(key=lambda e: (e["checked"], -e["seen"]))
    checked: dict[str, float] = {}
    dropped: set[str] = set()
    result: Direct | None = None
    for item in entries:
        if _expired(deadline):
            break
        checked[item["name"]] = time.time()
        work = item["path"]
        if not os.path.isdir(work):
            dropped.add(item["name"])
            continue
        cand = tree.tree_of(root, work, conf.projects)
        if cand is None or cand.is_main or pathcase.fold(cand.name) != pathcase.fold(item["name"]):
            dropped.add(item["name"])
            continue
        got = probe(root, conf, cand, deadline)
        if got.drop:
            dropped.add(item["name"])
        if got.found is not None and not _already(conf, session, got.found):
            result = got.found
            break
    direct_candidates.update(conf, session, checked, dropped)
    return result


def _already(conf: settings.Settings, session: str, found: Direct) -> bool:
    return bool(session) and nudged_before(conf.state, session, found)


def probe(
    root: str, conf: settings.Settings, here: tree.Tree, deadline: float | None = None
) -> Probe:
    """1 本のワークツリーを 3 つの結果のどれかで調べる（モジュールの説明の「促す条件」）。"""
    branch = tree.branch_of(here.root)
    if branch is None:
        return Probe(UNKNOWN)
    if branch != here.name:
        return Probe(CLEAR, drop=True)
    if not _integrable_name(here.name):
        return Probe(CLEAR, drop=True)
    reason, unsure = bound_check(root, conf, here, deadline)
    if reason:
        return Probe(CLEAR, drop=True)
    if unsure:
        return Probe(UNKNOWN)
    head = ops_stop.worktree_head_sha(here.root, deadline)
    if not head:
        return Probe(UNKNOWN)
    owner = tree.project_root(conf.projects, here.project) or root
    integration = _integration(owner, deadline)
    if integration is None:
        return Probe(UNKNOWN)
    dirty = _dirty(here.root, deadline)
    if dirty is None:
        return Probe(UNKNOWN)
    ahead = (
        _ahead(here.root, integration, deadline)
        if integration
        else _unique(here.root, here.name, deadline)
    )
    if ahead is None:
        if not (dirty and integration) or not _ref_missing(here.root, integration, deadline):
            return Probe(UNKNOWN)
        # 未コミットの変更があるのに統合先の ref を引けない。
        found = Direct(here.name, here.root, head, dirty, 0, integration)
        found.blocked = (
            f"統合先（{integration}）の ref が無く、引けません。統合先の名前が合っているか、"
            "ブランチが手元か origin にあるかをユーザに確かめてください"
        )
        return Probe(HIT, found)
    if not (dirty or ahead):
        return Probe(CLEAR)
    found = Direct(here.name, here.root, head, dirty, ahead, integration)
    if not integration:
        if gitcmd.run(owner, ["remote", "get-url", "origin"], ops_close.TIMEOUT_SECONDS).ok:
            found.blocked = (
                "統合先 (origin/HEAD) が決まらない。ユーザに `git fetch origin` のあと "
                "`git remote set-head origin -a` を頼んでください"
            )
        else:
            found.blocked = (
                "origin が無いので統合先が決まらない。ユーザに origin の追加を頼んでください"
            )
        return Probe(HIT, found)
    integ_tree = _integration_tree(owner, integration, deadline)
    if integ_tree is None:
        return Probe(UNKNOWN)
    found.integ_tree = integ_tree
    if not found.integ_tree:
        found.blocked = (
            f"統合先（{integration}）をチェックアウトしているツリーが見当たりません。"
            "どのツリーで取り込むかをユーザに確かめてください"
        )
        return Probe(HIT, found)
    recorded = _recorded(found.integ_tree, here.name, head, dirty, deadline)
    if recorded is None:
        return Probe(UNKNOWN)
    if recorded:
        return Probe(CLEAR)
    busy = _integration_busy(found.integ_tree, deadline)
    if busy is None:
        return Probe(UNKNOWN)
    found.blocked = busy
    return Probe(HIT, found)


def _integrable_name(name: str) -> bool:
    """`integrate` が受ける名前か（`direct_candidates.integrable_name`）。"""
    return direct_candidates.integrable_name(name)


def integration_name(owner: str) -> str:
    """そのリポジトリ（`owner` はそのチェックアウト）の統合先の名前。決まらなければ空。

    手元の `origin/HEAD`、無ければ `origin/main`、`origin/master` の順。sh の
    `ccnavi_integration`（`ccnavi_default_branch`）と同じ。
    origin の無いリポジトリでは決まらない。
    統合先の解決は、hook・`--lint`・レイヤーの比べ方がこの関数を共通に使う。
    """
    return _integration(owner) or ""


def _integration(owner: str, deadline: float | None = None) -> str | None:
    """`integration_name` の期限つきの版。期限不足・git の失敗で決められなければ None。"""
    head = tree.origin_head(owner)
    if head:
        return head
    for name in ("main", "master"):
        done = _run(
            owner,
            ["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{name}"],
            deadline,
        )
        if done.failure:
            return None
        if done.ok:
            return name
    return ""


def _tree_roots(root: str, conf: settings.Settings) -> list[str]:
    """チケットの置き場を探すツリーの根。登録されたツリーに加え、`.claude/worktrees/` と
    プロジェクトの置き場の直下のディレクトリも、登録の有無によらず入れる。

    外れたワークツリーの残骸や、まだ git リポジトリにしていないプロジェクトにチケットがあっても、
    `integrate` は断る（広めに当てて断る向き）。
    """
    roots = [t.root for t in tree.all_trees(root, conf.projects)]
    homes = [os.path.join(root, tree.WORKTREES_DIR)]
    if conf.projects:
        homes.append(
            conf.projects if os.path.isabs(conf.projects) else os.path.join(root, conf.projects)
        )
    for home in homes:
        try:
            entries = sorted(os.listdir(home))
        except OSError:
            continue
        roots.extend(os.path.join(home, e) for e in entries if os.path.isdir(os.path.join(home, e)))
    return list(dict.fromkeys(roots))


def _named_file(directory: str, name: str) -> str:
    """ディレクトリの中の `<名前>.md`（実際の表記のパス）。大文字小文字と NFC は畳んで比べる。"""
    want = pathcase.fold(tree.nfc(f"{name}.md"))
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        return ""
    for entry in entries:
        if pathcase.fold(tree.nfc(entry)) == want:
            return os.path.join(directory, entry)
    return ""


def bound_reason(root: str, conf: settings.Settings, here: tree.Tree) -> str:
    """この名前のワークツリーがチケットに結び付く理由。結び付かなければ空。

    `integrate`（`ccnavi tree bound`）の判定。決められないものは、これまでどおり結び付かないと
    読む。Stop の判定は、決められないことを区別する `bound_check` を使う。
    """
    return bound_check(root, conf, here)[0]


def bound_check(
    root: str, conf: settings.Settings, here: tree.Tree, deadline: float | None = None
) -> tuple[str, bool]:
    """`bound_reason` の本体。`(理由, 決められなかったか)`。

    理由が空で決められなかったときは、Stop の判定が「不明」として受ける（候補を外さない）。
    決められないのは、origin の追跡ブランチの有無を引く git が期限不足か失敗で終わったとき。

    Stop の促し（`direct_at_stop`）と `ccnavi-git.sh integrate`（`ccnavi tree bound`）が同じ判定を
    使う。材料は次の 3 つで、広めに当てて断る向き。

    - 全ツリー（ワークスペースルート・プロジェクト・ワークツリー）の承認済みチケット
      （`doing/`・`done/`）と提案（`todo/`・`review/`）に、同じ名前の `<名前>.md` がある
      （子のものも含む）
    - 手元の退避（`ready` 済み）に、同じ名前の親がある
    - `.claude/worktrees/<名前>`（か、名前の `/` を `-` にした識別子）のワークツリーのブランチが、
      識別子と一致し、`origin/<ブランチ>` が手元にある。`ccnavi-start.sh` は親のブランチを origin へ
      送ってから始めるので、チケットも提案もまだ無い親は、これで見分ける。直接作業のブランチは
      送らず統合先へ直接取り込むので、origin にあるかで見分けられる

    """
    name = here.name
    for base_root in _tree_roots(root, conf):
        approved = settings.approved_dir(conf, base_root)
        proposals = os.path.join(base_root, conf.tickets.replace("/", os.sep))
        for base, state in (
            (approved, ticket_model.DOING),
            (approved, ticket_model.DONE),
            (proposals, ticket_model.TODO),
            (proposals, ticket_model.REVIEW),
        ):
            found = _named_file(os.path.join(base, state), name)
            if found:
                rel = os.path.relpath(found, root).replace(os.sep, "/")
                return f"チケットか提案（{rel}）がある", False
    if archive.archived_fields(root, here.project, name) is not None:
        return "手元の退避（ready 済み）に同じ名前の親がある", False
    unsure = False
    ident = ticket_ids.branch_to_id(name)
    for cand in dict.fromkeys((name, ident)):
        work = tree.worktree_path(root, cand)
        if not os.path.isdir(work) or os.path.islink(work):
            continue
        branch = tree.branch_of(work)
        if not branch or pathcase.fold(ticket_ids.branch_to_id(branch)) != pathcase.fold(cand):
            continue
        done = _run(
            work,
            ["show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
            deadline,
            gitcmd.TIMEOUT_SECONDS,
        )
        if done.failure or done.code not in (0, 1):
            unsure = True
        elif done.ok:
            return (
                f"親のブランチ（origin/{branch}）のワークツリー"
                f"（{tree.WORKTREES_DIR.replace(os.sep, '/')}/{cand}）がある",
                False,
            )
    return "", unsure


def _dirty(worktree: str, deadline: float | None = None) -> bool | None:
    """ワークツリーに未コミットの変更があるか（追跡していないものも数える）。読めなければ None。"""
    done = _run(worktree, ["status", "--porcelain", "--untracked-files=all"], deadline)
    if not done.ok:
        return None
    return bool(done.out.strip())


def _ahead(worktree: str, integration: str, deadline: float | None = None) -> int | None:
    """統合先に入っていないコミットの数。統合先の ref を引けない・数えられないときは None。

    手元のブランチを先に見て、無ければ `origin/<統合先>`。
    """
    for ref in (f"refs/heads/{integration}", f"refs/remotes/origin/{integration}"):
        done = _run(worktree, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], deadline)
        if done.failure:
            return None
        if not done.ok:
            continue
        counted = _run(worktree, ["rev-list", "--count", f"{ref}..HEAD"], deadline)
        out = counted.out.strip()
        return int(out) if counted.ok and out.isdigit() else None
    return None


def _ref_missing(worktree: str, integration: str, deadline: float | None = None) -> bool:
    """統合先の ref が、手元にも origin にも「無い」と確かめられたか。

    期限不足・git の失敗は「確かめられていない」ので偽（呼び手は不明として扱う）。
    """
    for ref in (f"refs/heads/{integration}", f"refs/remotes/origin/{integration}"):
        done = _run(worktree, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], deadline)
        if done.failure or done.ok:
            return False
    return True


def _unique(worktree: str, name: str, deadline: float | None = None) -> int | None:
    """統合先が決まらないときの目安。ほかのどのブランチにも入っていないコミットの数。"""
    done = _run(
        worktree,
        [
            "rev-list",
            "--count",
            "HEAD",
            "--not",
            f"--exclude={name}",
            "--branches",
            "--remotes",
        ],
        deadline,
    )
    out = done.out.strip()
    return int(out) if done.ok and out.isdigit() else None


def _integration_tree(owner: str, integration: str, deadline: float | None = None) -> str | None:
    """統合先のブランチをチェックアウトしているツリー。見つからなければ空、読めなければ None。"""
    done = _run(owner, ["worktree", "list", "--porcelain"], deadline)
    if not done.ok:
        return None
    path = ""
    for line in done.out.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree ") :]
        elif line.strip() == f"branch refs/heads/{integration}":
            return path
    return ""


def _git_path(top: str, name: str, deadline: float | None = None) -> str | None:
    """`git rev-parse --git-path`。引けなければ None（期限不足・git の失敗）。"""
    done = _run(top, ["rev-parse", "--git-path", name], deadline)
    path = done.out.strip() if done.ok else ""
    if not path:
        return None
    return path if os.path.isabs(path) else os.path.join(top, path)


def _read_record(path: str) -> dict[str, str]:
    """`ccnavi_record_write` が書いた「鍵 値」の行の記録。読めなければ空。"""
    text = fsio.read_text(path, errors="replace")
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        key, _, value = line.partition(" ")
        if key:
            out[key] = value.strip()
    return out


def _recorded(
    integ_tree: str,
    name: str,
    head: str = "",
    dirty: bool | None = False,
    deadline: float | None = None,
) -> bool | None:
    """`integrate <名前>` の記録があるか。調べられなければ None（判定全体を「不明」にする）。

    - `ccnavi-integrate/<名前>/`（取り込みの途中）があれば「記録あり」。
    - `ccnavi-integrate-done/<名前>`（片付けた記録の写し）は、写しの `branch_sha` が今の HEAD と
      同じで、`dirty` が「変更無し」と確認できたときだけ「記録あり」。名前を使い直した作業
      （新しいコミットを積んだ、同じ HEAD で未コミットの変更がある）を、前の写しで黙らせない。
    - `dirty` が不明なら、取り込み済みとみなさず、判定全体を不明にする。
    - 写しを読めないときは「記録なし」（促す側に倒す）。
    """
    marks = _git_path(integ_tree, _RECORD_DIR, deadline)
    if marks is None:
        return None
    if os.path.lexists(os.path.join(marks, name)):
        return True
    done = _git_path(integ_tree, _DONE_DIR, deadline)
    if done is None:
        return None
    copy = os.path.join(done, name)
    if not os.path.lexists(copy):
        return False
    sha = _read_record(copy).get("branch_sha", "")
    if not sha or not head or sha != head:
        return False
    if dirty is None:
        return None
    return not dirty


def _integration_busy(integ_tree: str, deadline: float | None = None) -> str | None:
    """統合先のツリーが `integrate` の前提（綺麗で、途中の操作が無い）を満たさない理由。

    満たすなら空。期限不足で確かめられなければ None（判定全体を「不明」にする）。
    """
    mark = tree.busy_of(integ_tree)
    squash = _git_path(integ_tree, _SQUASH_MSG, deadline)
    if not mark and squash and os.path.lexists(squash):
        mark = _SQUASH_MSG
    if mark:
        return (
            f"統合先のツリー（{integ_tree}）に途中の操作（{mark}）があります。"
            "持ち主に確かめて済ませてもらってください"
        )
    done = _run(integ_tree, ["status", "--porcelain", "--untracked-files=all"], deadline)
    if done.timed_out:
        return None
    if not done.ok:
        return f"統合先のツリー（{integ_tree}）の状態を読めません"
    lines = [ln for ln in done.out.splitlines() if ln.strip()]
    if lines:
        listed = " ".join(ln.strip() for ln in lines[:_LISTED])
        more = f" ほか {len(lines) - _LISTED} 件" if len(lines) > _LISTED else ""
        return (
            f"統合先のツリー（{integ_tree}）に未コミットの変更があります（{listed}{more}）。"
            "取り込みと混ざるので、持ち主に確かめ、コミットか退避を済ませてもらってください"
        )
    return ""


def nudged_before(state_dir: str, session: str, found: Direct) -> bool:
    """このセッションで、同じ状態のまま言ったことがあるか。言うのは 1 回だけ。"""
    data = fsio.read_dict(ops_stop.nudge_path(state_dir, session)) or {}
    return data.get(found.key) == found.digest


def remember_nudge(state_dir: str, session: str, found: Direct) -> str:
    """言った (名前, 状態) を記録する。書けなければ理由。git で共有する履歴には入れない。"""
    path = ops_stop.nudge_path(state_dir, session)
    data = fsio.read_dict(path) or {}
    data[found.key] = found.digest
    return fsio.write_json_atomic(path, dict(sorted(data.items())))


def _left(found: Direct) -> str:
    parts = []
    if found.dirty:
        parts.append("未コミットの変更")
    if found.ahead:
        parts.append(f"統合先（{found.integration}）に入っていないコミット {found.ahead} 件")
    return "と".join(parts)


def integrate_nudge(root: str, found: Direct) -> str:
    """Stop を止めて渡す文。`integrate` の打ち方と、staged で止めて確かめてもらう手順を言う。

    コマンドは 1 行で完結させ、別のツリーで打つものは `cd <引用したパス> && …` で始める
    （Bash の cwd が次の呼び出しに残るとは限らない）。ラッパーを通らない `git` は書かない。
    """
    n = found.name
    git_sh = settings.script_command(root, "ccnavi-git.sh")
    work = settings.shell_path(found.worktree)
    integ = settings.shell_path(found.integ_tree)
    lines = [
        f"[ccnavi] {CODE_INTEGRATE_NUDGE} (worktree: {n})",
        f"ワークツリー .claude/worktrees/{n} はチケットに結び付かない直接作業で、"
        f"{_left(found)}があり、integrate {n} の記録がありません。",
    ]
    if found.dirty:
        lines.append(
            "integrate が取り込むのはコミットした分だけです。先にコミットしてください: "
            f"`cd {work} && {git_sh} add -- <パス>` と "
            f"`cd {work} && {git_sh} commit -m '<メッセージ>'`。"
        )
    lines += [
        f"作業が終わったなら `cd {integ} && {git_sh} integrate {n}` を実行して staged にし、"
        "そこで止めてください。hook は integrate を実行しません。",
        f"続き: `cd {integ} && {git_sh} status` と `cd {integ} && {git_sh} diff --staged` の結果を"
        "ユーザに見せます。了承を得たら "
        f"`cd {integ} && {git_sh} integrate-done {n} -m '<メッセージ>'` でコミットして片付けます。"
        f"やめるなら `cd {integ} && {git_sh} integrate --abort {n}`。",
        "まだ続けるなら、続ける理由（何が残っているか）をユーザに向けて書いてから終えてください。"
        "この案内は同じ状態では 1 回だけです。",
    ]
    return "\n".join(lines)


def blocked_notice(found: Direct) -> str:
    """促せないときにユーザへ言う文（`systemMessage`）。"""
    return (
        f"[ccnavi] 直接作業 {found.name} は {_left(found)}があり、integrate の記録もありませんが、"
        f"今は取り込みを促せません。{found.blocked}"
    )
