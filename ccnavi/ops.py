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
    # 承認の無いチケットは着手させない。写しが無ければ範囲は効かず、フェーズにも
    # 数えられないので、着手した子が「無いもの」として進んでしまう。
    open_copies, _ = approval.copies(conf.approved)
    if found.ticket not in approval.by_id(open_copies):
        stderr.write(
            f"ccnavi: {ticket_id} は承認されていない（写しが無い）。"
            "先に利用者が 'ccnavi --approve' を通すこと\n"
        )
        return 1
    worktree = tree.worktree_path(root, ticket_id)
    if not tree.is_worktree_of(root, worktree) or not tree.exact_name(root, ticket_id):
        stderr.write(
            f"ccnavi: {ticket_id} の作業ツリー {worktree} が無い（綴りは大文字小文字まで同じで）。"
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
    if _parent_still_busy(stderr, root, conf, found):
        return 1
    if _deliverables_missing(stderr, root, conf, found):
        return 1
    # 子は、閉じる前に実績のリスクを数える（risk.py）。定性項目の判定が揃わなければ閉じない。
    scored = _score_child(stdout, stderr, root, conf, found)
    if scored is None:
        return 1
    fields = {"completed_at": approval.now()}
    code = _move(
        stdout, stderr, conf, found, ticket_mod.DONE, fields, f"完了 {fields['completed_at']}"
    )
    if code == 0 and scored:
        for line in scored:
            stdout.write(line + "\n")
    if code == 0 and not found.is_child:
        # 親を閉じた。マージに進んでよいの合図（Draft を外す）は、まだなら親が出す。
        # マージそのものは人。
        if approval.read_parent_mark(conf.approved, found.ticket, approval.PARENT_MARK_READY):
            stdout.write("Draft は外してある。マージは利用者が行う\n")
        else:
            from .review import wip_root

            stdout.write(
                f"次は、この移動をコミットし、`{wip_root(conf)}/` を消して"
                f"（'sh .claude/scripts/ccnavi-git.sh rm -r {wip_root(conf)}'）コミットし、"
                "push してから 'sh .claude/scripts/ccnavi-review.sh ready' で Draft を外す"
                "（マージに進んでよいの合図）。途中の作業は既定のブランチに残さない。"
                "マージは利用者が squash で行う\n"
            )
    return code


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
    if _parent_still_busy(stderr, root, conf, found):
        return 1
    fields = {"cancelled_at": approval.now(), "cancel_reason": reason.strip()}
    return _move(
        stdout, stderr, conf, found, ticket_mod.CANCELLED, fields, f"取り消し: {reason.strip()}"
    )


def judge(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    ticket_id: str,
    factor_id: str,
    answer: str,
    reason: str,
) -> int:
    """定性のリスク項目の判定を記録する。判断したのはサブエージェント、記録するのは親。

    判定は子の HEAD に結ぶ。HEAD が動いたら判定は古く、閉じるときに取り直しになる。
    """
    from . import risk

    answer = answer.strip().lower()
    if answer not in ("yes", "no"):
        stderr.write("ccnavi: 判定は yes か no\n")
        return 1
    if not reason.strip():
        stderr.write("ccnavi: judge には --reason <根拠> が要る\n")
        return 1
    found = _find(stderr, root, conf, ticket_id)
    if found is None:
        return 1
    if not found.is_child:
        stderr.write(f"ccnavi: {ticket_id} は子ではない。判定は子の差分に付ける\n")
        return 1
    definition = risk.load_definition(conf)
    factor = definition.factor(factor_id)
    if factor is None or factor.kind != risk.KIND_JUDGE:
        names = ", ".join(f.id for f in definition.judges) or "(無い)"
        stderr.write(
            f"ccnavi: {factor_id} は定性の項目ではない（{definition.source}）。あるのは: {names}\n"
        )
        return 1
    worktree = tree.worktree_path(root, ticket_id)
    head = _head(worktree)
    if not head:
        stderr.write(f"ccnavi: {worktree} の HEAD を読めない\n")
        return 1
    record = (
        approval.read_child_record(
            conf.approved, found.parent, ticket_id, approval.CHILD_RECORD_JUDGE
        )
        or {}
    )
    record[factor_id] = {
        "hit": answer == "yes",
        "reason": reason.strip(),
        "head": head,
        "at": approval.now(),
    }
    failed = approval.write_child_record(
        conf.approved, found.parent, ticket_id, approval.CHILD_RECORD_JUDGE, record
    )
    if failed:
        stderr.write(f"ccnavi: 判定を記録できない: {failed}\n")
        return 1
    stdout.write(
        f"OK: {ticket_id} の {factor_id} を {answer} と記録した"
        f"（{'+' + str(factor.points) if answer == 'yes' else '加点なし'}、HEAD {head[:12]}）\n"
    )
    return 0


def _score_child(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, found: ticket_mod.Ticket
) -> list[str] | None:
    """子の実績のリスクを数えて記録する。閉じられなければ None。

    返すのは、閉じたあとに出す行（点と内訳）。親は数えない。
    """
    from . import risk

    if not found.is_child:
        return []
    worktree = tree.worktree_path(root, found.ticket)
    definition = risk.load_definition(conf)
    if definition.fallback:
        stderr.write(f"ccnavi: {definition.fallback}\n")
    diff, why = risk.measure(worktree, found.base_sha)
    if diff is None:
        stderr.write(f"ccnavi: {found.ticket} のリスクを測れない: {why}\n")
        return None
    judgements = (
        approval.read_child_record(
            conf.approved, found.parent, found.ticket, approval.CHILD_RECORD_JUDGE
        )
        or {}
    )
    env = {
        "CCNAVI_BASE_SHA": diff.base,
        "CCNAVI_HEAD": diff.head,
        "CCNAVI_TICKET": found.ticket,
        "CCNAVI_PARENT": found.parent,
    }
    score = risk.evaluate(definition, diff, root, worktree, env, judgements)
    if score.pending:
        prompt = risk.judge_prompt(found.ticket, found.parent, diff, score.pending, worktree)
        where = ""
        if conf.state:
            path = os.path.join(conf.state, f"risk-judge-{found.ticket}.md")
            try:
                os.makedirs(conf.state, exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(prompt)
                where = path
            except OSError as exc:
                stderr.write(f"ccnavi: 問いを書き出せない ({exc})\n")
        names = ", ".join(f.id for f in score.pending)
        stderr.write(
            f"ccnavi: {found.ticket} を閉じる前に、定性のリスク項目の判定が要る: {names}\n"
            "  問いと差分の要約を渡してサブエージェントに判断させ、報告を "
            f"'sh .claude/scripts/ccnavi-ticket.sh judge {found.ticket} <項目> yes|no "
            "--reason <根拠>' で記録してから閉じ直すこと\n"
        )
        if where:
            stderr.write(f"  問い: {where}\n")
        return None
    record = score.as_dict()
    record.update({"head": diff.head, "base": diff.base, "at": approval.now()})
    record["summary"] = diff.summary()
    failed = approval.write_child_record(
        conf.approved, found.parent, found.ticket, approval.CHILD_RECORD_RISK, record
    )
    if failed:
        stderr.write(f"ccnavi: リスクを記録できない: {failed}\n")
        return None
    lines = score.lines()
    lines[0] += f"（{diff.summary()}、配点 {definition.source}）"
    if score.level in risk.ESCALATE_FROM:
        lines.append("  実績のリスクが高いので、宣言に関わらずこのフェーズは人間レビューが要る")
    return lines


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


def _parent_still_busy(
    stderr: TextIO, root: str, conf: settings.Settings, found: ticket_mod.Ticket
) -> bool:
    """親を閉じてよいか。開いている子や閉じたゲートがある間は閉じさせない。

    親の写しが閉じるとゲートの鍵（cwd から引く親）が消え、レビュー要の
    フェーズが終わっていても誰も止めなくなる。
    """
    if found.is_child:
        return False
    problems = close_problems(root, conf, found.ticket)
    for p in problems:
        stderr.write(f"ccnavi: {p}\n")
    return bool(problems)


def close_problems(root: str, conf: settings.Settings, parent_id: str) -> list[str]:
    """親を閉じられない理由の一覧。空なら閉じてよい。

    `review ready`（Draft を外す）も同じ条件を見る。閉じてよい状態と、マージに
    進んでよい状態は同じもの。人が wrapup で締めていれば、開いている子以外は問わない。
    人が締めたあとに残っているものは、締めたときに別の issue へ写してある。
    """
    from . import phase

    copies, _ = approval.copies(conf.approved)
    open_children = [t.ticket for t in copies if t.parent == parent_id]
    if open_children:
        return [
            f"{parent_id} には開いている子がある（{', '.join(open_children)}）。子を先に閉じること"
        ]
    if approval.read_parent_mark(conf.approved, parent_id, approval.PARENT_MARK_WRAPUP):
        return []
    problems: list[str] = []
    closed = phase.gate(root, conf, parent_id)
    if closed is not None:
        problems.append(
            f"{parent_id} のフェーズ {closed.label} はレビュー待ち（ゲートが閉じている）。"
            "レビューを済ませてから"
        )
    closed_copies, _ = approval.copies(conf.approved, closed=True)
    copy = approval.by_id(copies + closed_copies).get(parent_id)
    if copy is not None and copy.has_plan:
        if copy.feedback is None:
            problems.append(
                f"{parent_id} はフィードバック計画がまだ。対応が無くても "
                "`feedback: []` を改版で出して承認を受けてから"
            )
        unfinished = [p.label for p in phase.phases_of(root, conf, parent_id) if not p.ended]
        if unfinished:
            problems.append(
                f"{parent_id} には終わっていないフェーズがある（{', '.join(unfinished)}）。"
                "全部閉じてから"
            )
    return problems


def _deliverables_missing(
    stderr: TextIO, root: str, conf: settings.Settings, found: ticket_mod.Ticket
) -> bool:
    """フェーズの最後の子を閉じる前に、種類の成果物が揃っているか（設計 §24.15.6）。

    在って追跡されていることだけを見る。中身は見ない。空でも在ることは分かるので、
    「調査したことにする」は塞げる。
    """
    from . import phase

    if not found.is_child or found.phase is None:
        return False
    copies, _ = approval.copies(conf.approved)
    parent = approval.by_id(copies).get(found.parent)
    if parent is None or not parent.has_plan:
        return False
    item = parent.item_at(found.phase)
    types = phase.load_types(conf) or {}
    pt = types.get(item.type) if item is not None else None
    if pt is None or not pt.deliverables:
        return False
    # 同じフェーズに、まだ開いている別の子があれば、成果物はその子が出すかもしれない。
    for ph in phase.phases_of(root, conf, found.parent):
        if ph.number != found.phase:
            continue
        others = [
            t.ticket
            for t in ph.tickets
            if t.ticket != found.ticket
            and ph.states.get(t.ticket) in (ticket_mod.TODO, ticket_mod.DOING)
        ]
        if others:
            return False
    worktree = tree.worktree_path(root, found.parent)
    child_tree = tree.worktree_path(root, found.ticket)
    missing = [
        g for g in pt.deliverables if not _tracked(worktree, g) and not _tracked(child_tree, g)
    ]
    if not missing:
        return False
    stderr.write(
        f"ccnavi: フェーズ {found.phase}（{pt.title}）の成果物が無い: {', '.join(missing)}。"
        "親か子の作業ツリーに置いて追跡（git add）してから閉じること\n"
    )
    return True


def _tracked(worktree: str, glob: str) -> bool:
    """この glob に当たる追跡済みのファイルが 1 つでもあるか。"""
    if not os.path.isdir(worktree):
        return False
    try:
        done_ = subprocess.run(
            ["git", "ls-files", "--", glob],
            cwd=worktree,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done_.returncode == 0 and bool(done_.stdout.strip())


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
        with open(found.path, encoding="utf-8") as f:
            original = f.read()
        os.makedirs(os.path.dirname(target), exist_ok=True)
        # 提案は人も読む。読み直して書き出さず、欄の行だけを書き換える。
        with open(target, "w", encoding="utf-8") as f:
            f.write(ticket_mod.set_fields(original, fields))
    except OSError as exc:
        stderr.write(f"ccnavi: {found.ticket} を {state}/ へ動かせない ({exc})\n")
        return 1
    try:
        os.remove(found.path)
    except OSError as exc:
        # 両方に残すと、以後どの操作も「複数の場所にある」で止まる。書いた側を消して戻す。
        try:
            os.remove(target)
            stderr.write(f"ccnavi: {found.path} を消せない ({exc})。元の置き場のままにした\n")
        except OSError:
            stderr.write(
                f"ccnavi: {found.ticket} が {found.state}/ と {state}/ の両方に残った ({exc})。"
                f"人が {found.state}/ の側を消すこと\n"
            )
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
