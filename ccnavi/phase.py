"""フェーズとゲート。子チケットの束が終わったときに何をするかと、進もうとしたら止めること。

## フェーズの終わり

同じ親の同じ `phase` の子が `todo/` にも `doing/` にも無く、`done/` に 1 枚以上あるとき、
そのフェーズは終わり。`cancelled/` だけのフェーズは終わりではない（何も成果が無い）。

終わりの扱いは、`done/` の子に `human_review.required: true` が 1 枚でもあるかで分かれる。
あればゲートが閉じ、レビュー済みの印が置かれるまでサブエージェントの起動とシェルを止める。
無ければ省略の印を置いて進ませる。

## ゲートの鍵は cwd

書き込みは行き先で結ぶが、起動とシェルには行き先が無い。ゲートは呼び出しの `cwd` が
どの親の作業ツリーにあるかで親を引く。`cd` 1 回で外れる鍵だが、外れた先で起動した
サブエージェントの書き込みは行き先で止まるので、致命傷にならない。

## 提案から写しへ写すもの

スクリプトが書く欄（着手・完了の時刻と基準点）だけを、提案から写しへ写す。
範囲に触らない欄なので、写しても承認の意味は変わらない。提案が `done/` か
`cancelled/` に動いていたら、写しを `closed/` へ動かす。
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import TextIO

from . import approval, rules, settings, tree
from . import ticket as ticket_mod

# ゲートの中でも通す Bash。状態を動かす・レビューを頼む・合流して片付ける、の 3 本。
EXEMPT = re.compile(r"ccnavi-(ticket|review|git)\.sh")

# サブエージェントに許さない操作。状態を動かすスクリプトとレビューのスクリプト。
SUBAGENT_FORBIDDEN = re.compile(r"ccnavi-(ticket|review)\.sh")

# ゲートが止めるツール。
GATED_TOOLS = ("Agent", "Bash")

# 返す文の理由コード。
CODE_GATE = "DENY_PHASE_GATE"
CODE_SUBAGENT = "DENY_SUBAGENT_TICKET_OP"

# git に与える時間。
TIMEOUT_SECONDS = 2.0


@dataclass
class Phase:
    """1 つの親の 1 つのフェーズ。"""

    parent: str
    number: int
    tickets: list[ticket_mod.Ticket] = field(default_factory=list)
    states: dict[str, str] = field(default_factory=dict)
    marks: dict[str, dict] = field(default_factory=dict)

    @property
    def ended(self) -> bool:
        states = list(self.states.values())
        if not states or any(s in (ticket_mod.TODO, ticket_mod.DOING, "") for s in states):
            return False
        return ticket_mod.DONE in states

    @property
    def review_required(self) -> bool:
        return any(
            t.review_required for t in self.tickets if self.states.get(t.ticket) == ticket_mod.DONE
        )

    @property
    def gate_closed(self) -> bool:
        return self.ended and self.review_required and approval.MARK_REVIEWED not in self.marks


def sync(stderr: TextIO, root: str, conf: settings.Settings) -> list[ticket_mod.Ticket]:
    """提案の状態を写しへ写し、閉じたものを閉じる。開いている写しを返す。"""
    open_copies, notes = approval.copies(conf.approved)
    for note in notes:
        stderr.write(f"ccnavi: {note}\n")
    remaining = []
    for copy in open_copies:
        state, path = _proposal(root, conf, copy)
        if state in ticket_mod.CLOSED:
            proposal, _ = ticket_mod.load(path)
            if proposal is not None:
                approval.update_copy(conf.approved, copy, _script_fields(proposal))
            failed = approval.close_copy(conf.approved, copy.ticket)
            if failed:
                stderr.write(f"ccnavi: {copy.ticket}: {failed}\n")
                remaining.append(copy)
            continue
        if path:
            proposal, _ = ticket_mod.load(path)
            if proposal is not None:
                fields = _script_fields(proposal)
                if any(getattr(copy, k) != v for k, v in fields.items()):
                    approval.update_copy(conf.approved, copy, fields)
                    for k, v in fields.items():
                        setattr(copy, k, v)
        remaining.append(copy)
    return remaining


def phases_of(root: str, conf: settings.Settings, parent_id: str) -> list[Phase]:
    """この親のフェーズを番号順に。開いている写しと閉じた写しの両方から組む。"""
    open_copies, _ = approval.copies(conf.approved)
    closed_copies, _ = approval.copies(conf.approved, closed=True)
    by_number: dict[int, Phase] = {}
    for t in approval.children_of(open_copies + closed_copies, parent_id):
        if t.phase is None:
            continue
        phase = by_number.setdefault(t.phase, Phase(parent_id, t.phase))
        phase.tickets.append(t)
        state, _ = _proposal(root, conf, t)
        phase.states[t.ticket] = state
    for phase in by_number.values():
        phase.marks = approval.marks(conf.approved, parent_id, phase.number)
    return [by_number[n] for n in sorted(by_number)]


def gate(root: str, conf: settings.Settings, parent_id: str) -> Phase | None:
    """閉じているゲート。無ければ None。"""
    for phase in phases_of(root, conf, parent_id):
        if phase.gate_closed:
            return phase
    return None


def parent_for_cwd(root: str, conf: settings.Settings, cwd: str) -> ticket_mod.Ticket | None:
    """cwd が親の作業ツリーの中なら、その親の写し。"""
    t = tree.tree_of(root, cwd or os.getcwd())
    if t is None or t.is_main:
        return None
    open_copies, _ = approval.copies(conf.approved)
    found = approval.by_id(open_copies).get(t.name)
    if found is None or found.is_child:
        return None
    return found


def gate_reason(phase: Phase, tool: str) -> str:
    """ゲートが止めたときに返す文。次に何をすればよいかを言う。"""
    what = "サブエージェントの起動" if tool == "Agent" else "このシェル実行"
    marks = "依頼済み" if approval.MARK_REQUESTED in phase.marks else "未依頼"
    n = phase.number
    return "\n".join(
        [
            f"[ccnavi] {CODE_GATE} (parent: {phase.parent}, phase: {n}, {marks})",
            f"{phase.parent} のフェーズ {n} は終わっていて、人間レビューが要る子を含みます。"
            f"レビュー済みの印が置かれるまで、ゲートが{what}を止めます。",
            "やること: 子の成果を親ブランチへ合流して push し、"
            f"'sh .claude/scripts/ccnavi-review.sh request --phase {n} --body-file <依頼文>' "
            "でレビューを頼み、ターンを終えて利用者を待ってください。"
            f"利用者がレビューを終えたら 'sh .claude/scripts/ccnavi-review.sh check --phase {n}' "
            "で確かめます。次のフェーズの計画（wip/tickets/todo/ への提案）は"
            "レビュー前に進めて構いません。",
        ]
    )


def announce(stderr: TextIO, root: str, conf: settings.Settings, parent: ticket_mod.Ticket) -> str:
    """終わったばかりのフェーズについて 1 度だけ言う文。無ければ空文字。"""
    texts = []
    for phase in phases_of(root, conf, parent.ticket):
        if not phase.ended or phase.marks:
            continue
        if phase.review_required:
            failed = approval.write_mark(
                conf.approved, parent.ticket, phase.number, approval.MARK_PENDING, {}
            )
            if failed:
                stderr.write(f"ccnavi: フェーズの印を書けない: {failed}\n")
            required = [t.ticket for t in phase.tickets if t.review_required]
            n = phase.number
            texts.append(
                f"[ccnavi] {parent.ticket} のフェーズ {n} が終わりました。"
                f"人間レビュー要の子: {', '.join(required)}。"
                "子の成果を親ブランチへ合流して push し、"
                f"'sh .claude/scripts/ccnavi-review.sh request --phase {n} --body-file <依頼文>' "
                "でレビューを頼み、ターンを終えて利用者を待ってください。指摘があれば同じフェーズに"
                "子を足せます。レビュー済みになるまで、ゲートがサブエージェントの起動とシェル実行を"
                "止めます。"
            )
        else:
            failed = approval.write_mark(
                conf.approved,
                parent.ticket,
                phase.number,
                approval.MARK_SKIPPED,
                {"tickets": [t.ticket for t in phase.tickets]},
            )
            if failed:
                stderr.write(f"ccnavi: フェーズの印を書けない: {failed}\n")
            texts.append(
                f"[ccnavi] {parent.ticket} のフェーズ {phase.number} が終わりました。"
                "子はどれも人間レビュー不要なので、レビューを省略して次のフェーズへ進めます。"
                "省略した事実は記録に残しました。"
            )
    return "\n\n".join(texts)


def scope_findings(
    root: str, conf: settings.Settings, child: ticket_mod.Ticket, parent: ticket_mod.Ticket | None
) -> tuple[list[str], str]:
    """子の作業ツリーに残っている範囲外の変更。2 つめは読めなかった理由。

    見るのは `base_sha..HEAD` のコミット済みの差分と、未コミットの変更の両方。
    未コミットだけ見る検査では、範囲外を書いてコミットしたものが映らない。
    """
    worktree = tree.worktree_path(root, child.ticket)
    if not os.path.isdir(worktree):
        return [], "作業ツリーが無い"
    paths: set[str] = set()
    if child.base_sha:
        rc, out = _git(worktree, ["diff", "--name-only", f"{child.base_sha}..HEAD"])
        if rc != 0:
            return [], "基準点からの差分を読めない"
        paths.update(line.strip() for line in out.splitlines() if line.strip())
    rc, out = _git(worktree, ["status", "--porcelain", "--untracked-files=all", "--no-renames"])
    if rc != 0:
        return [], "作業ツリーの状態を読めない"
    for line in out.splitlines():
        if len(line) > 3:
            paths.add(line[3:].strip())
    outside = []
    for rel in sorted(paths):
        rel = rel.replace("\\", "/")
        if rel.startswith(conf.tickets + "/"):
            continue
        verdict = child.decide(rel)
        if parent is not None:
            verdict = ticket_mod.combine(verdict, parent.decide(rel))
        if verdict in (ticket_mod.OUTSIDE, rules.DENY):
            outside.append(rel)
    return outside, ""


def _proposal(root: str, conf: settings.Settings, copy: ticket_mod.Ticket) -> tuple[str, str]:
    """写しの元になった提案が、いまどの状態にあるか。"""
    tree_root = _tree_root(root, copy.source_tree)
    if not tree_root:
        return "", ""
    return ticket_mod.locate(root, conf.tickets, tree_root, copy.ticket)


def _tree_root(root: str, name: str) -> str:
    if name == tree.MAIN:
        return tree.main_tree(root).root
    for t in tree.worktrees(root):
        if t.name == name:
            return t.root
    return ""


def _script_fields(proposal: ticket_mod.Ticket) -> dict[str, str]:
    return {k: getattr(proposal, k) for k in ticket_mod.SCRIPT_FIELDS}


def _git(cwd: str, args: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return done.returncode, done.stdout
