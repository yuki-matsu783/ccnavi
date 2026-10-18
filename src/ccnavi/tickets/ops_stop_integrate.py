"""Stop で、直接作業が `integrate` まで進んでいないときの促し。

チケットに結び付かない直接作業のワークツリー（`.claude/worktrees/<名前>`。ブランチも同じ名前）で
終えようとしたとき、作業が残っていて `integrate <名前>` の記録も無ければ、1 回だけ止めて
`integrate` の打ち方と、staged で止めてユーザに中身を確かめてもらう手順を返す。hook 自身は
`integrate` を打たない。促すだけ。

## 促す条件

cwd のツリーが次を全部満たすとき。

- `.claude/worktrees/<名前>` のワークツリーで、今のブランチが名前と同じ
- チケットに結び付かない。材料は `ccnavi-git.sh integrate` の `ig_bound` と同じ
  （全ツリーの承認済みチケット・提案の `<名前>.md`、手元の退避）。親のブランチのワークツリーは、
  ブランチ名が名前と違う（`/` を含む）ので、上の条件で外れている
- 作業が残っている。ワークツリーに未コミットの変更がある（追跡していないファイルも数える）か、
  統合先に入っていないコミットがある
- `integrate <名前>` の記録が無い。統合先のツリーの git ディレクトリの `ccnavi-integrate/<名前>/` も
  `ccnavi-integrate-done/<名前>` も無い

## 統合先

`ccnavi_integration`（`ccnavi-common-state.sh`）の順（環境変数、`settings.local.json` の統合先、
既定ブランチ）に、hook が設定から読む `--integration-branch` を足したもの。環境変数
`CCNAVI_INTEGRATION_BRANCH`、`.claude/settings.local.json` の `env`、`--integration-branch`
（sh 側には無い）、`origin/HEAD`、`origin/main`・`origin/master`。
決まらなければ `integrate` が断るので、
促さずに理由を言う。

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
from dataclasses import dataclass

from ..infra import fsio, gitcmd, settings, tree
from . import archive, ops_close, ops_stop, ticket_model

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


def direct_at_stop(root: str, conf: settings.Settings, cwd: str) -> Direct | None:
    """メインエージェントが終わろうとしたとき、`integrate` を促す（か理由を言う）直接作業。

    条件はモジュールの説明のとおり。git かファイルを読めなければ None（促しは保護ではないので、
    読めないときは今までどおり何も出さずに通す）。
    """
    here = tree.tree_of(root, cwd or os.getcwd(), conf.projects)
    if here is None or here.is_main or tree.branch_of(here.root) != here.name:
        return None
    if not _integrable_name(here.name):
        return None
    if _bound_to_ticket(root, conf, here):
        return None
    head = ops_stop.worktree_head_sha(here.root)
    if not head:
        return None
    owner = tree.project_root(conf.projects, here.project) or root
    integration = integration_name(root, conf, owner, here.project)
    dirty = _dirty(here.root)
    if dirty is None:
        return None
    ahead = _ahead(here.root, integration) if integration else _unique(here.root, here.name)
    if ahead is None:
        if not (dirty and integration):
            return None
        # 未コミットの変更があるのに統合先の ref を引けない。コミットだけなら黙る（下の説明）。
        found = Direct(here.name, here.root, head, dirty, 0, integration)
        found.blocked = (
            f"統合先（{integration}）の ref が無く、引けません。統合先の名前が合っているか、"
            "ブランチが手元か origin にあるかをユーザに確かめてください"
        )
        return found
    if not (dirty or ahead):
        return None
    found = Direct(here.name, here.root, head, dirty, ahead, integration)
    if not integration:
        found.blocked = (
            "統合先の名前が決まりません（CCNAVI_INTEGRATION_BRANCH・"
            "origin/HEAD のどれもありません）。統合先をユーザに確かめ、CCNAVI_INTEGRATION_BRANCH に"
            "入れてもらってください"
        )
        return found
    found.integ_tree = _integration_tree(owner, integration)
    if not found.integ_tree:
        found.blocked = (
            f"統合先（{integration}）をチェックアウトしているツリーが見当たりません。"
            "どのツリーで取り込むかをユーザに確かめてください"
        )
        return found
    if _recorded(found.integ_tree, here.name):
        return None
    found.blocked = _integration_busy(found.integ_tree)
    return found


def _integrable_name(name: str) -> bool:
    """`integrate` が受ける名前か。`ccnavi-git.sh` の `ig_name` と同じ検査。

    統合先に使う名前（main・master・develop・release・release-*）と、`ccnavi_is_ident` を通らない
    形は断られるので、促さない。
    """
    if name in ("main", "master", "develop", "release") or name.startswith("release-"):
        return False
    if not name or name[0] in "-." or ".." in name or "/" in name or "\\" in name:
        return False
    if not (name[0].isascii() and name[0].isalnum()):
        return False
    return all(c.isalnum() and c.isascii() or c in "._-" or ord(c) >= 0x80 for c in name)


def integration_name(root: str, conf: settings.Settings, owner: str, project: str) -> str:
    """そのリポジトリ（`owner` はそのチェックアウト）の統合先の名前。決まらなければ空。

    環境変数、`settings.local.json` の `env`、`--integration-branch`（これだけ
    `ccnavi_integration` には無い）、`origin/HEAD`、`origin/main`・`origin/master` の順。
    統合先の解決は、hook・`--lint`・レイヤーの比べ方がこの関数を共通に使う。
    `project` は呼び手の互換のために残した引数で、使わない。
    """
    for name in (
        os.environ.get(settings.INTEGRATION_ENV, ""),
        settings.integration_local(root),
        conf.integration_branch,
    ):
        if name and name.strip():
            return name.strip()
    head = tree.origin_head(owner)
    if head:
        return head
    for name in ("main", "master"):
        done = gitcmd.run(
            owner,
            ["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{name}"],
            ops_close.TIMEOUT_SECONDS,
        )
        if done.ok:
            return name
    return ""


def _bound_to_ticket(root: str, conf: settings.Settings, here: tree.Tree) -> bool:
    """この名前がチケットに結び付くか。`ccnavi-git.sh` の `ig_bound` と同じ材料。

    決められないものは結び付くとして扱う（促さない側。`integrate` も断る向きに倒す）。
    """
    name = here.name
    for t in tree.all_trees(root, conf.projects):
        approved = settings.approved_dir(conf, t.root)
        proposals = os.path.join(t.root, conf.tickets.replace("/", os.sep))
        for base, state in (
            (approved, ticket_model.DOING),
            (approved, ticket_model.DONE),
            (proposals, ticket_model.TODO),
            (proposals, ticket_model.REVIEW),
        ):
            if os.path.isfile(os.path.join(base, state, f"{name}.md")):
                return True
    return archive.archived_fields(root, here.project, name) is not None


def _dirty(worktree: str) -> bool | None:
    """ワークツリーに未コミットの変更があるか（追跡していないものも数える）。読めなければ None。"""
    rc, out = gitcmd.output(
        worktree, ["status", "--porcelain", "--untracked-files=all"], ops_close.TIMEOUT_SECONDS
    )
    if rc != 0:
        return None
    return bool(out.strip())


def _ahead(worktree: str, integration: str) -> int | None:
    """統合先に入っていないコミットの数。統合先の ref を引けない・数えられないときは None。

    手元のブランチを先に見て、無ければ `origin/<統合先>`。
    """
    for ref in (f"refs/heads/{integration}", f"refs/remotes/origin/{integration}"):
        done = gitcmd.run(
            worktree,
            ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            ops_close.TIMEOUT_SECONDS,
        )
        if not done.ok:
            continue
        rc, out = gitcmd.output(
            worktree, ["rev-list", "--count", f"{ref}..HEAD"], ops_close.TIMEOUT_SECONDS
        )
        return int(out.strip()) if rc == 0 and out.strip().isdigit() else None
    return None


def _unique(worktree: str, name: str) -> int | None:
    """統合先が決まらないときの目安。ほかのどのブランチにも入っていないコミットの数。"""
    rc, out = gitcmd.output(
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
        ops_close.TIMEOUT_SECONDS,
    )
    return int(out.strip()) if rc == 0 and out.strip().isdigit() else None


def _integration_tree(owner: str, integration: str) -> str:
    """統合先のブランチをチェックアウトしているツリー。見つからなければ空。"""
    rc, out = gitcmd.output(owner, ["worktree", "list", "--porcelain"], ops_close.TIMEOUT_SECONDS)
    if rc != 0:
        return ""
    path = ""
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree ") :]
        elif line.strip() == f"branch refs/heads/{integration}":
            return path
    return ""


def _git_path(top: str, name: str) -> str:
    rc, out = gitcmd.output(top, ["rev-parse", "--git-path", name], ops_close.TIMEOUT_SECONDS)
    path = out.strip() if rc == 0 else ""
    if path and not os.path.isabs(path):
        path = os.path.join(top, path)
    return path


def _recorded(integ_tree: str, name: str) -> bool:
    """`integrate <名前>` の記録（取り込み中の記録、片付けた記録の写し）が在るか。"""
    marks = _git_path(integ_tree, _RECORD_DIR)
    done = _git_path(integ_tree, _DONE_DIR)
    return bool(
        (marks and os.path.lexists(os.path.join(marks, name)))
        or (done and os.path.lexists(os.path.join(done, name)))
    )


def _integration_busy(integ_tree: str) -> str:
    """統合先のツリーが `integrate` の前提（綺麗で、途中の操作が無い）を満たさない理由。"""
    mark = tree.busy_of(integ_tree)
    squash = _git_path(integ_tree, _SQUASH_MSG)
    if not mark and squash and os.path.lexists(squash):
        mark = _SQUASH_MSG
    if mark:
        return (
            f"統合先のツリー（{integ_tree}）に途中の操作（{mark}）があります。"
            "持ち主に確かめて済ませてもらってください"
        )
    rc, out = gitcmd.output(
        integ_tree, ["status", "--porcelain", "--untracked-files=all"], ops_close.TIMEOUT_SECONDS
    )
    if rc != 0:
        return f"統合先のツリー（{integ_tree}）の状態を読めません"
    lines = [ln for ln in out.splitlines() if ln.strip()]
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
    """Stop を止めて渡す文。`integrate` の打ち方と、staged で止めて確かめてもらう手順を言う。"""
    n = found.name
    git_sh = settings.script_command(root, "ccnavi-git.sh")
    lines = [
        f"[ccnavi] {CODE_INTEGRATE_NUDGE} (worktree: {n})",
        f"ワークツリー .claude/worktrees/{n} はチケットに結び付かない直接作業で、"
        f"{_left(found)}があり、'integrate {n}' の記録がありません。",
    ]
    if found.dirty:
        lines.append(
            "integrate が取り込むのはコミットした分だけです。"
            "先にワークツリーの中でコミットしてください。"
        )
    lines += [
        f"作業が終わったなら、統合先のツリー（{found.integ_tree}）で '{git_sh} integrate {n}' を"
        "実行して staged にし、そこで止めてください。hook は integrate を実行しません。",
        "続き: ユーザに git status と git diff --staged で中身を確かめてもらい、"
        f"よければ '{git_sh} integrate-done {n} -m <メッセージ>' でコミットして片付けます。"
        f"やめるなら '{git_sh} integrate --abort {n}' で取り込む前に戻します。",
        "まだ続けるなら、続ける理由（何が残っているか）をユーザに向けて書いてから終えてください。"
        "この案内は同じ状態では 1 回だけで、コミットを追加するまで次に終えるときは止めません。",
    ]
    return "\n".join(lines)


def blocked_notice(found: Direct) -> str:
    """促せないときにユーザへ言う文（`systemMessage`）。"""
    return (
        f"[ccnavi] 直接作業 {found.name} は {_left(found)}があり、integrate の記録もありませんが、"
        f"今は取り込みを促せません。{found.blocked}"
    )
