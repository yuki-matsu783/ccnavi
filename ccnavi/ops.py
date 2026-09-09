"""チケットの状態を動かす操作。`ccnavi ticket start|done|cancel <識別子>`。

親が `.claude/scripts/ccnavi-ticket.sh` から呼ぶ。スクリプトは薄く、ここが本体。
サブエージェントからの呼び出しは cli.py が止める（`agent_id` が付いていたら拒む）。

やることは置き場を動かして欄を書くことだけ。作業ツリーの削除は親のマージ手順に
任せる。順序は「子の成果をマージ → done → 作業ツリーを消す」で、done の前に
作業ツリーを消すと base_sha の検査ができなくなる。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TextIO

from . import approval, settings, tree
from . import ticket as ticket_mod

TIMEOUT_SECONDS = 5.0


def start(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, ticket_id: str
) -> int:
    """todo/ → doing/。着手の時刻と基準点を書く。"""
    found = _find(stderr, root, conf, ticket_id)
    if found is None:
        return 1
    if found.state != ticket_mod.TODO:
        stderr.write(f"ccnavi: {ticket_id} は未着手ではない（いまは {found.state}/）\n")
        return 1
    worktree = tree.worktree_path(root, ticket_id)
    if not tree.is_worktree_of(root, worktree):
        stderr.write(
            f"ccnavi: {ticket_id} の作業ツリー {worktree} が無い。"
            "先に 'sh .claude/scripts/ccnavi-git.sh worktree add .claude/worktrees/"
            f"{ticket_id} -b {ticket_id}' で作ること\n"
        )
        return 1
    sha = _head(worktree)
    if not sha:
        stderr.write(f"ccnavi: {worktree} の HEAD を読めない\n")
        return 1
    fields = {"started_at": approval.now(), "base_sha": sha}
    return _move(
        stdout,
        stderr,
        conf,
        found,
        ticket_mod.DOING,
        fields,
        f"着手 {fields['started_at']} / 基準点 {sha[:12]}",
    )


def done(stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, ticket_id: str) -> int:
    """doing/ → done/。完了の時刻を書く。写しは次の hook が closed/ へ動かす。"""
    found = _find(stderr, root, conf, ticket_id)
    if found is None:
        return 1
    if found.state != ticket_mod.DOING:
        stderr.write(f"ccnavi: {ticket_id} は作業中ではない（いまは {found.state}/）\n")
        return 1
    fields = {"completed_at": approval.now()}
    return _move(
        stdout, stderr, conf, found, ticket_mod.DONE, fields, f"完了 {fields['completed_at']}"
    )


def cancel(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, ticket_id: str, reason: str
) -> int:
    """todo/ か doing/ → cancelled/。理由が要る。"""
    if not reason.strip():
        stderr.write("ccnavi: 取り消しには --reason <理由> が要る\n")
        return 1
    found = _find(stderr, root, conf, ticket_id)
    if found is None:
        return 1
    if found.state not in (ticket_mod.TODO, ticket_mod.DOING):
        stderr.write(f"ccnavi: {ticket_id} は未着手でも作業中でもない（いまは {found.state}/）\n")
        return 1
    fields = {"cancelled_at": approval.now(), "cancel_reason": reason.strip()}
    return _move(
        stdout, stderr, conf, found, ticket_mod.CANCELLED, fields, f"取り消し: {reason.strip()}"
    )


def _find(
    stderr: TextIO, root: str, conf: settings.Settings, ticket_id: str
) -> ticket_mod.Ticket | None:
    proposals, problems = ticket_mod.scan(root, conf.tickets)
    hits = [t for t in proposals if t.ticket == ticket_id]
    if not hits:
        stderr.write(
            f"ccnavi: 提案 {ticket_id} が見つからない"
            f"（{conf.tickets}/ の下を全作業ツリーで探した）\n"
        )
        for p in problems:
            stderr.write(f"  {p}\n")
        return None
    if len(hits) > 1:
        places = ", ".join(f"{t.tree or '(main)'}:{t.state}" for t in hits)
        stderr.write(f"ccnavi: {ticket_id} が複数の場所にある: {places}。1 つにしてから\n")
        return None
    return hits[0]


def _move(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    found: ticket_mod.Ticket,
    state: str,
    fields: dict[str, str],
    said: str,
) -> int:
    base = os.path.dirname(os.path.dirname(found.path))
    target = os.path.join(base, state, found.ticket + ".md")
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(ticket_mod.render(found, fields))
        os.remove(found.path)
    except OSError as exc:
        stderr.write(f"ccnavi: {found.ticket} を {state}/ へ動かせない ({exc})\n")
        return 1
    # 写しがあれば、欄をすぐ写す。次の hook でも写るが、ここで写しておくと
    # スクリプトの直後に走る検査が古い基準点を見ない。
    copies, _ = approval.copies(conf.approved)
    copy = approval.by_id(copies).get(found.ticket)
    if copy is not None:
        if state in ticket_mod.CLOSED:
            approval.update_copy(conf.approved, copy, fields)
            approval.close_copy(conf.approved, found.ticket)
        else:
            approval.update_copy(conf.approved, copy, fields)
    stdout.write(f"OK: {found.ticket} を {state}/ へ動かした（{said}）\n")
    return 0


def _head(worktree: str) -> str:
    try:
        done_ = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=worktree,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done_.stdout.strip() if done_.returncode == 0 else ""


def worktree_exists(root: str, ticket_id: str) -> bool:
    return tree.is_worktree_of(root, tree.worktree_path(root, ticket_id))


def remove_tree(path: str) -> None:
    """テストの片付け用。判定の経路では使わない。"""
    shutil.rmtree(path, ignore_errors=True)
