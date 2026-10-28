"""Stop で `finish` の打ち忘れを促す判定と、git の読み取り。

メインエージェントが終わろうとしたとき、着手済みのまま `finish` されていないチケットを見つけ
（`unfinished_at_stop`）、促しを 1 回だけ出す
（`nudged_before` / `remember_nudge` / `finish_nudge`）。
`base_off_head` は基準点がワークツリーの HEAD の祖先かを見る。
`worktree_head_sha` は `tickets/lifecycle/commands.py` も使う。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ...infra import fsio, gitcmd, settings, tree
from ...policy import texts
from ..approval import checks as approval_checks
from ..approval import store as approval
from ..model import standing, ticket_model
from . import close as ops_close

# Stop で `finish` の打ち忘れを促すときの理由コード。止めるのは 1 回だけの促しで、
# 判定の deny ではない。
CODE_FINISH_NUDGE = "NUDGE_TICKET_FINISH"

# Stop の促しの共通の 1 文（`finish` の促しと、レビューの依頼までの道の促し）。状態ごとの次の一手は
# 変えず、直前にユーザが止めた・待たせた・相談したいと言ったときは、促しに従わずに終えさせる。
# 次の一手がコマンドでなく、作業を続けることやユーザへの相談なら、無理にコマンドの形にしない。
USER_STOP_NOTE = (
    "直前にユーザが「止めて」「待って」「相談したい」と言った場合は、このまま進めずにターンを終えてください。"
    "止めた理由と、再開するときの次の一手を1行で書きます。"
    "ユーザの判断が本当に要る問いがある場合は、その問いを最後に書き直して終えてください。"
)


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
    - 自分で作ったコミットが 1 件以上ある（`_own_commits`。親は基準点より先、
      子は親のブランチに無いもの）

    git を読めなければ促さない。促しは保護ではないので、読めないときは今までどおり何も出さずに通す。

    `raw` は `close_problems` と同じ。
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
        standing.Families(conf, root).branch(bound.parent, bound.project) if bound.is_child else ""
    )
    ahead = _own_commits(here.root, bound, parent_branch)
    head = worktree_head_sha(here.root)
    if ahead <= 0 or not head:
        return None
    return Unfinished(bound, here.root, ahead, head)


def _own_commits(worktree: str, t: ticket_model.Ticket, parent_branch: str = "") -> int:
    """基準点より先の、このチケットが自分で作ったコミットの数。数えられなければ 0（促さない側）。

    子なら親のブランチ（`parent_branch`。親のワークツリーの
    HEAD から読んだ名前）の先にあるもの、親ならワークツリーの起点のデフォルトブランチ
    （`origin/HEAD`。セッションの頭で進める）の先にあるものを除く。取り込みで生まれたマージのコミットも除く
    （`--no-merges`）。子で親のブランチを引けなければ、自分のものか決まらないので 0 を返す。
    親で `origin/HEAD` が無い（リモートの無いリポジトリ）ときは、除くものが無いので基準点の先を
    全部数える。
    """
    exclude: list[str] = []
    if t.is_child:
        if not parent_branch:
            return 0
        rc, sha = gitcmd.output(
            worktree,
            ["rev-parse", "--verify", "--quiet", f"{parent_branch}^{{commit}}"],
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
    # 子は、親のブランチに入っていないコミットがそのまま自分の仕事。基準点から数えない
    # （衝突を解くために親を取り込んでも、親にあるほかの子のコミットは除かれる）。
    # 親は基準点（着手のときの親のワークツリーの HEAD）から数える。親のブランチは親自身の線で、
    # 取り込みに混ざるのは統合先（上で除く `origin/HEAD`）と同じ親のブランチ（ほかの PC が送った
    # 親の仕事）だけなので、子のような共通の祖先の取り直しは要らない。合流した子のコミットも
    # 親の仕事として数える（ここは「閉じられる親に、送っていない仕事が残っているか」を見る）。
    start = [] if t.is_child else [f"{t.base_sha}..HEAD"]
    rc, out = gitcmd.output(
        worktree,
        ["rev-list", "--count", "--no-merges", *(start or ["HEAD"]), *exclude],
        ops_close.TIMEOUT_SECONDS,
    )
    if rc != 0 or not out.strip().isdigit():
        return 0
    return int(out.strip())


def nudge_path(state_dir: str, session: str) -> str:
    """促した (チケット, HEAD) の記録。セッションごとに置く（差し戻しの記録と同じ）。"""
    where = fsio.safe_name(session) or "unknown"
    return os.path.join(state_dir, f"nudged-{where}.json")


def nudged_before(state_dir: str, session: str, found: Unfinished) -> bool:
    """このセッションで、同じチケットを同じ HEAD のまま促したことがあるか。促すのは 1 回だけ。"""
    data = fsio.read_dict(nudge_path(state_dir, session)) or {}
    return data.get(found.ticket.ticket) == found.head


def remember_nudge(state_dir: str, session: str, found: Unfinished) -> str:
    """促した (チケット, HEAD) を記録する。書けなければ理由。"""
    path = nudge_path(state_dir, session)
    data = fsio.read_dict(path) or {}
    data[found.ticket.ticket] = found.head
    return fsio.write_json_atomic(path, dict(sorted(data.items())))


def finish_nudge(root: str, found: Unfinished) -> str:
    """Stop を止めて渡す文。続けるならどうするかを言う。文は文面ファイルが持つ。"""
    t = found.ticket
    values = {
        "ticket": t.ticket,
        "worktree": found.worktree,
        "ahead": str(found.ahead),
        "base": t.base_sha[:12],
        "user_stop_note": USER_STOP_NOTE,
    }
    said = texts.say(
        root, "NUDGE_TICKET_FINISH", values, {"what": "child" if t.is_child else "parent"}
    )
    return "\n".join([f"[ccnavi] {CODE_FINISH_NUDGE} (ticket: {t.ticket})", said])


def base_off_head(root: str, conf: settings.Settings, t: ticket_model.Ticket) -> str:
    """基準点（`base_sha`）がチケットのワークツリーの HEAD の祖先でないときの文。でなければ空。

    着手の欄はスクリプトだけが書く。承認はチケットの中身を変えないので、手で動かした承認では
    提案の段階で書かれた基準点がそのまま入りうる。`--lint` と `ccnavi ticket status` が warn で
    言う。`start` は、`started_at` が無ければ基準点を HEAD で書き直す
    （`started_at` も仕込まれていれば「着手済み」で先に返り、`base_sha` だけなら判定が
    `blocked` で止める）。ワークツリーが無い・
    HEAD を読めないときは確かめられないので空を返す（言わない）。判定には入れない
    （判定は git を読まない）。
    """
    if not t.base_sha:
        return ""
    owner = tree.project_root(conf.projects, t.project) or root
    worktree = tree.worktree_path(root, t.ticket)
    if not tree.is_worktree_of(owner, worktree):
        return ""
    head = worktree_head_sha(worktree)
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


def worktree_head_sha(worktree: str, deadline: float | None = None) -> str:
    """ワークツリーの HEAD の sha。読めなければ空。

    `deadline`（`time.monotonic` の絶対時刻）があれば、過ぎてから git を起こさない。
    """
    if deadline is None:
        rc, out = gitcmd.output(worktree, ["rev-parse", "HEAD"], ops_close.TIMEOUT_SECONDS)
    else:
        rc, out, _ = gitcmd.output_until(
            worktree, ["rev-parse", "HEAD"], deadline, ops_close.TIMEOUT_SECONDS
        )
    return out.strip() if rc == 0 else ""
