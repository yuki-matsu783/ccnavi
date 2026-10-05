"""Stop で `finish` の打ち忘れを促す判定と、git の読み取り。

メインエージェントが終わろうとしたとき、着手済みのまま `finish` されていないチケットを見つけ
（`unfinished_at_stop`）、促しを 1 回だけ出す
（`nudged_before` / `remember_nudge` / `finish_nudge`）。
`base_off_head` は基準点がワークツリーの HEAD の祖先かを見る。`_head` は `ops.py` も使う。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..infra import fsio, gitcmd, settings, tree
from . import approval, approval_checks, ops_close, syncstate, ticket_model

# Stop で `finish` の打ち忘れを促すときの理由コード。止めるのは 1 回だけの促しで、
# 判定の deny ではない。
CODE_FINISH_NUDGE = "NUDGE_TICKET_FINISH"


@dataclass
class Unfinished:
    """Stop で `finish` を促す相手。`ahead` は基準点より先のコミットの数。"""

    ticket: ticket_model.Ticket
    worktree: str
    ahead: int
    head: str = ""


def unfinished_at_stop(
    root: str, conf: settings.Settings, cwd: str, raw: approval.Raw | None = None
) -> Unfinished | None:
    """メインエージェントが終わろうとしたとき、`finish` を打ち忘れていそうなチケット。

    促すのは、cwd のワークツリーに結び付いた承認済みチケットが次を全部満たすときだけ。

    - 作業中（`doing/`）で着手済み。終わっても取り消されてもいない（`in_progress`）
    - 信頼できない理由（`blocked`）が無い。範囲が判定に使われていないチケットに終わりを勧めない
    - 基準点（`base_sha`）を持つ
    - 親なら `close_problems` が空。開いている子・レビュー準備中／レビュー待ちのフェーズ・
      フィードバック計画待ち・終わっていないフェーズがあれば `finish` は通らないので促さない
    - ワークツリーに未コミットの変更が無い（追跡していないファイルも数える）
    - 基準点より先に、自分で作ったコミットが 1 件以上ある（`_own_commits`）

    git を読めなければ促さない。促しは保護ではないので、読めないときは今までどおり何も出さずに通す。

    `raw` は `close_problems` と同じ。ここは読むだけで置き場を動かさない。
    """
    here = tree.tree_of(root, cwd or os.getcwd(), conf.projects)
    if here is None or here.is_main:
        return None
    copies, _ = approval.scan(conf, root, raw=raw)
    bound = tree.lookup(approval_checks.by_id(copies), here.name)
    if bound is None or not bound.in_progress or bound.blocked or not bound.base_sha:
        return None
    if not bound.is_child and ops_close.close_problems(root, conf, bound.ticket, raw):
        return None
    rc, out = gitcmd.output(
        here.root, ["status", "--porcelain", "--untracked-files=all"], ops_close.TIMEOUT_SECONDS
    )
    if rc != 0 or out.strip():
        return None
    parent_branch = (
        syncstate.Families(conf, root).branch(bound.parent, bound.project) if bound.is_child else ""
    )
    ahead = _own_commits(here.root, bound, parent_branch)
    head = _head(here.root)
    if ahead <= 0 or not head:
        return None
    return Unfinished(bound, here.root, ahead, head)


def _own_commits(worktree: str, t: ticket_model.Ticket, parent_branch: str = "") -> int:
    """基準点より先の、このチケットが自分で作ったコミットの数。数えられなければ 0（促さない側）。

    取り込んだだけのコミットは数えない。子なら親のブランチ（`parent_branch`。親チケットの
    `branch:`、無ければ親の識別子）の先にあるもの、親ならワークツリーの起点のデフォルトブランチ
    （`origin/HEAD`。セッションの頭で進める）の先にあるものを除く。取り込みで生まれたマージのコミットも除く
    （`--no-merges`）。子で親のブランチを引けなければ、自分のものか決まらないので 0 を返す。
    親で `origin/HEAD` が無い（リモートの無いリポジトリ）ときは、除くものが無いので基準点の先を
    全部数える。
    """
    exclude: list[str] = []
    if t.is_child:
        rc, sha = gitcmd.output(
            worktree,
            ["rev-parse", "--verify", "--quiet", f"{parent_branch or t.parent}^{{commit}}"],
            ops_close.TIMEOUT_SECONDS,
        )
        if rc != 0 or not sha.strip():
            return 0
        exclude.append(f"^{sha.strip()}")
    else:
        rc, sha = gitcmd.output(
            worktree,
            ["rev-parse", "--verify", "--quiet", "refs/remotes/origin/HEAD^{commit}"],
            ops_close.TIMEOUT_SECONDS,
        )
        if rc == 0 and sha.strip():
            exclude.append(f"^{sha.strip()}")
    rc, out = gitcmd.output(
        worktree,
        ["rev-list", "--count", "--no-merges", f"{t.base_sha}..HEAD", *exclude],
        ops_close.TIMEOUT_SECONDS,
    )
    if rc != 0 or not out.strip().isdigit():
        return 0
    return int(out.strip())


def _nudge_path(state_dir: str, session: str) -> str:
    """促した (チケット, HEAD) の記録。セッションごとに置く（差し戻しの記録と同じ）。"""
    where = fsio.safe_name(session) or "unknown"
    return os.path.join(state_dir, f"nudged-{where}.json")


def nudged_before(state_dir: str, session: str, found: Unfinished) -> bool:
    """このセッションで、同じチケットを同じ HEAD のまま促したことがあるか。促すのは 1 回だけ。"""
    data = fsio.read_dict(_nudge_path(state_dir, session)) or {}
    return data.get(found.ticket.ticket) == found.head


def remember_nudge(state_dir: str, session: str, found: Unfinished) -> str:
    """促した (チケット, HEAD) を記録する。書けなければ理由。
    git で共有する履歴（history）には入れない。"""
    path = _nudge_path(state_dir, session)
    data = fsio.read_dict(path) or {}
    data[found.ticket.ticket] = found.head
    return fsio.write_json_atomic(path, dict(sorted(data.items())))


def finish_nudge(root: str, found: Unfinished) -> str:
    """Stop を止めて渡す文。打つ sh のパスと、続けるならどうするかを言う。"""
    t = found.ticket
    ticket_sh = settings.script_command(root, "ccnavi-ticket.sh")
    kind = "子チケット" if t.is_child else "親チケット"
    return "\n".join(
        [
            f"[ccnavi] {CODE_FINISH_NUDGE} (ticket: {t.ticket})",
            f"{kind} {t.ticket} は着手済みのまま作業中（{ticket_model.DOING}/）です。ワークツリー "
            f"{found.worktree} に未コミットの変更が無く、基準点 {t.base_sha[:12]} より先に"
            f"コミットが {found.ahead} 件あります。",
            f"作業が終わったなら '{ticket_sh} finish {t.ticket}' を実行してから終えてください。"
            "まだ続けるなら、続ける理由（何が残っているか）をユーザに向けて書いてから終えてください。"
            "この案内は同じ HEAD では 1 回だけで、コミットを足すまで次に終えるときは止めません。",
        ]
    )


def base_off_head(root: str, conf: settings.Settings, t: ticket_model.Ticket) -> str:
    """基準点（`base_sha`）がチケットのワークツリーの HEAD の祖先でないときの文。でなければ空。

    着手の欄はスクリプトだけが書く。承認はチケットの中身を変えないので、手で動かした承認では
    提案の段階で書かれた基準点がそのまま入りうる。`--lint` と `ccnavi ticket status` が warn で
    言う。`start` では止めない（`started_at` も仕込まれていれば「着手済み」で先に返り、`base_sha`
    だけなら判定が `blocked` で止め、`start` は基準点を HEAD で書き直す）。ワークツリーが無い・
    HEAD を読めないときは確かめられないので空を返す（言わない）。判定には入れない
    （判定は git を読まない）。
    """
    if not t.base_sha:
        return ""
    owner = tree.project_root(conf.projects, t.project) or root
    worktree = tree.worktree_path(root, t.ticket)
    if not tree.is_worktree_of(owner, worktree):
        return ""
    head = _head(worktree)
    if not head or _is_ancestor(worktree, t.base_sha, head):
        return ""
    return (
        f"基準点（base_sha: {t.base_sha}）がワークツリー {worktree} の HEAD の祖先でない。"
        "着手の欄はスクリプトだけが書くもので、提案の段階で書かれた値か、ワークツリーの履歴を"
        "書き換えたあとかもしれない。ユーザに承認済みチケットの base_sha を確かめてもらってください"
    )


def _is_ancestor(worktree: str, base: str, head: str) -> bool:
    """`base` が `head` の祖先（か同じ）か。確かめられなければ偽（warn で言う側）。"""
    done = gitcmd.run(
        worktree, ["merge-base", "--is-ancestor", base, head], ops_close.TIMEOUT_SECONDS
    )
    return done.ok


def _head(worktree: str) -> str:
    rc, out = gitcmd.output(worktree, ["rev-parse", "HEAD"], ops_close.TIMEOUT_SECONDS)
    return out.strip() if rc == 0 else ""
