"""チケットの状態を動かす操作。`ccnavi ticket start|finish|cancel <識別子>`。

親が `.ccnavi/scripts/ccnavi-ticket.sh` から呼ぶ。スクリプトは薄く、ここが本体。
サブエージェントからの呼び出しは hook の判定が止める（`hook/judge.py` が `phase_forms.forbidden` で
`DENY_SUBAGENT_TICKET_OP` を返す）。実行ファイルは呼び手がサブエージェントかを知らない。

やることは置き場を動かして欄を書くことだけ（状態は置き場が表す）。`start` は置き場を動かさず
`doing/` の欄を書く。`finish` は `doing/` から `wip/proposals/review/`（レビュー要）か
`.ccnavi/approved/done/`（不要）へ、`cancel` は `doing/` から `done/` へ動かす。
ワークツリーはここでは消さない。子のワークツリーは、`confirm` が子を `done/` へ動かしたあとと、
親の `finish` のあとに sh が `worktree tidy` で消す（worktrees.py）。finish の前にワークツリーを
消すと base_sha の検査ができなくなり、レビュー待ちの間に指摘を直す場所も無くなる。

ここに置くのは操作の本体（`start` / `finish` / `cancel` / `record_risk`）と、`finish` が閉じるときに
実績のリスクを数える手順。チケットを引いて閉じてよいかを検査する部分は `ops_close.py`、Stop で
`finish` の打ち忘れを促す部分と git の読み取りは `ops_stop.py` に分けてある。
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import TextIO

from ..infra import fsio, settings, tree
from . import (
    approval,
    approval_checks,
    approval_marks,
    approval_ops,
    configsync,
    flow,
    history,
    ops_close,
    ops_stop,
    phase,
    risk,
    ticket_ids,
    ticket_model,
    ticket_places,
    worktrees,
)

# 最後のレビューのあと、Draft を外すまでの手順がユーザの合意の範囲に入ることの言い切り。
# `confirm` の知らせ（core_review.after_last_review）と親の `finish` の出力が同じ文を使う。
# ユーザがレビュー済みにした時点で、ここまでは合意済み。外へ出る操作（push・Draft を外す）の前に
# 確認を取り直すと、同じ流れの中で何度も許可を求めることになる。
READY_FLOW_AGREED = (
    "ここから Draft を外すまで（finish → push → ready。`wip/` の削除は ready に入っている）は、"
    "ユーザのレビュー済みの合意の範囲に入る。"
    "push と Draft を外す前に、ユーザへ確認を取り直さずに進める"
    "（Claude Code の自動モードの分類器に止められたときだけ、ユーザに許可を頼む）"
)


def ready_command(root: str, parent_id: str) -> str:
    """Draft を外す 1 本。親のワークツリーの外から打つ形（`--parent`）。

    この形なら、Draft を外した最後に親のワークツリーも消える。
    """
    return f"{settings.script_command(root, 'ccnavi-review.sh')} ready --parent {parent_id}"


def start(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, ticket_id: str
) -> int:
    """`doing/` の承認済みチケットに、着手の時刻と基準点を書く。置き場は動かない。"""
    found = ops_close.find(stderr, root, conf, ticket_id)
    if found is None:
        return 1
    if found.state != ticket_model.DOING:
        stderr.write(
            f"ccnavi: {ticket_id} は承認済みの作業中ではない（いまは {found.state}/）。"
            + (
                "先にユーザに 'ccnavi --agree' を通してもらってください"
                if found.state == ticket_model.TODO
                else ""
            )
            + "\n"
        )
        return 1
    if found.started_at:
        stderr.write(f"ccnavi: {ticket_id} は着手済み（{found.started_at}）\n")
        return 1
    if ops_close.parent_not_started(stderr, root, conf, found):
        return 1
    if ops_close.predecessors_unmet(stderr, root, conf, found):
        return 1
    # ワークツリーは承認済みチケットの `project` が指すリポジトリから
    # 切られていること。
    # 元リポジトリが違えば、判定はそのツリーの元リポジトリで行われ、チケットと食い違う。
    owner = tree.project_root(conf.projects, found.project) or root
    worktree = tree.worktree_path(root, ticket_id)
    if not tree.is_worktree_of(owner, worktree) or not tree.exact_name(root, ticket_id):
        where = f"projects/{found.project} の中で " if found.project else ""
        stderr.write(
            f"ccnavi: {ticket_id} のワークツリー {worktree} が無いか、"
            "元リポジトリが承認済みチケットの project"
            f"（{found.project or 'ワークスペース'}）と違う（大文字小文字まで同じ表記であること）。"
            f"先に {where}'{settings.script_command(root, 'ccnavi-git.sh')} "
            f'worktree add "{worktree}" '
            f"{_branch_words(found)}' で作ってください\n"
        )
        return 1
    sha = ops_stop.worktree_head_sha(worktree)
    if not sha:
        stderr.write(f"ccnavi: {worktree} の HEAD を読めない\n")
        return 1
    synced = _sync_config(stderr, root, conf, found, worktree)
    if synced is None:
        return 1
    fields = {"started_at": approval_marks.now(), "base_sha": sha}
    failed = approval_ops.update_fields(found.path, fields)
    if failed:
        stderr.write(f"ccnavi: {ticket_id} に着手の欄を書けない: {failed}\n")
        return 1
    history.note(
        os.path.dirname(os.path.dirname(found.path)),
        found.ticket,
        history.KIND_STARTED,
        ticket_model.DOING,
        ticket_model.DOING,
        base_sha=sha,
    )
    stdout.write(
        f"OK: {found.ticket} に着手した（{fields['started_at']} / 基準点 {sha[:12]}）。"
        f"置き場は {ticket_model.DOING}/ のまま\n"
    )
    if found.is_child:
        # 着手のときのフローのハッシュを記録する。SubagentStart / SubagentStop が、着手のあとに
        # 書き換わったら知らせる（止めない）。
        started = replace(found, started_at=fields["started_at"])
        where, failed = flow.record_digest(conf, root, started)
        if failed:
            stderr.write(
                f"ccnavi: {ticket_id} のフローのハッシュを記録できない（{failed}）。"
                "着手のあとの書き換えは知らせられない\n"
            )
        else:
            stdout.write(
                f"フローのハッシュを {where} に記録した。"
                "承認済みチケットと同じくユーザがコミットする\n"
            )
    for line in synced:
        stdout.write(line + "\n")
    return 0


def _sync_config(
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    found: ticket_model.Ticket,
    worktree: str,
) -> list[str] | None:
    """親の着手の前に、共通レイヤーをプロジェクトの `.ccnavi/common/` へミラーする。

    返すのは着手の出力に足す行。ミラーできなければ None（着手しない）。子は親のブランチに
    含まれるので配らない。ワークスペース自身の作業は、共通レイヤーと同じリポジトリにあるので配らない。
    プロジェクトの `.ccnavi/config/` には触れない。
    """
    if found.is_child or not found.project:
        return []
    changes, why = configsync.plan(conf, root, worktree)
    if why:
        stderr.write(f"ccnavi: {found.ticket} の共通レイヤーをミラーできない: {why}\n")
        return None
    if not changes:
        return []
    failed = configsync.apply(changes, configsync.mirror_dir(conf, worktree))
    if failed:
        stderr.write(f"ccnavi: {found.ticket} の共通レイヤーをミラーできない: {failed}\n")
        return None
    git_sh = settings.script_command(root, "ccnavi-git.sh")
    lines = [
        f"共通レイヤーとプロジェクト {found.project} のミラー（.ccnavi/common/）が違っていたので、"
        "共通レイヤーの中身に揃えた（プロジェクトの .ccnavi/config/ には触れていない）:"
    ]
    for c in changes:
        state = "消した" if c.deletes else ("上書き" if c.before is not None else "新しく置いた")
        lines.append(f"  - {c.rel}（{state}）")
    lines.append(
        f"  作業を始める前に、{worktree} で {', '.join(c.rel for c in changes)} を"
        f" '{git_sh} add' してコミットしてください"
    )
    return lines


def finish(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, ticket_id: str
) -> int:
    """`doing/` → `review/`（レビュー要）か `done/`（不要）。完了の時刻を書く。"""
    found = ops_close.find(stderr, root, conf, ticket_id)
    if found is None:
        return 1
    if found.state != ticket_model.DOING:
        stderr.write(f"ccnavi: {ticket_id} は作業中ではない（いまは {found.state}/）\n")
        return 1
    if not found.started_at:
        stderr.write(
            f"ccnavi: {ticket_id} は未着手。先に "
            f"'{settings.script_command(root, 'ccnavi-ticket.sh')} start {ticket_id}' "
            "を通してください\n"
        )
        return 1
    if ops_close.parent_still_busy(stderr, root, conf, found):
        return 1
    if ops_close.deliverables_missing(stderr, root, conf, found):
        return 1
    # 子は、閉じる前に実績のリスクを数える（risk.py）。定性項目の判定が揃わなければ閉じない。
    scored = _score_child(stdout, stderr, root, conf, found)
    if scored is None:
        return 1
    fields = {"completed_at": approval_marks.now()}
    state = ticket_model.REVIEW if _needs_review(root, conf, found) else ticket_model.DONE
    code = _move(stdout, stderr, root, conf, found, state, fields, f"完了 {fields['completed_at']}")
    if code == 0 and scored:
        for line in scored:
            stdout.write(line + "\n")
    if code == 0 and not found.is_child:
        _close_parent(stdout, stderr, root, conf, found)
    return code


def _needs_review(root: str, conf: settings.Settings, found: ticket_model.Ticket) -> bool:
    """この子を閉じたとき、ユーザが見る対象になるか。親は見ない。

    フェーズの「見る場所」を、この子を閉じたものとして数え直す。延期したフェーズの子も
    レビュー待ちに置く。見るのは次にレビューがあるフェーズの番だが、ユーザが見るまでは
    見られていない。実績のリスクは `_score_child` が記録した直後なので、ここで数え直すと
    宣言に関わらず上がった分も入る。
    """
    if not found.is_child or found.phase is None:
        return False
    for ph in phase.phases_of(root, conf, found.parent):
        if ph.number != found.phase:
            continue
        ph.states[found.ticket] = ticket_model.DONE
        return ph.deferred or ph.review_required
    return found.review_required


def _close_parent(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, found: ticket_model.Ticket
) -> None:
    """親を閉じたあとの記録と案内。

    記録（`closed.json`）は、どのフェーズをどこで見たかを親のブランチに残す。提案は
    統合先に取り込む前に `wip/` ごと消えるので、マージリクエストを作らない進め方では
    閉じた事実の残る先がここしか無い。

    案内は進め方で分かれる。マージリクエストがあるなら Draft を外す合図まで、
    無いなら統合先に取り込むところまで。ccnavi はどちらでもマージしない。
    Draft を外す `ready` は、閉じたチケットとその記録を手元の `logs/archive/` へ退避してから外す
    （`review_close.ready`・archive.py）。この記録（`closed.json`）も一緒に退避される。
    """
    where = approval.home_dir(conf, root, found.ticket, "", project=found.project)
    venues = phase.review_venues(root, conf, found.ticket)
    failed = approval_marks.write_parent_mark(
        where,
        found.ticket,
        approval_marks.PARENT_MARK_CLOSED,
        {"reviews": {str(n): venues[n] for n in sorted(venues)}},
    )
    if failed:
        stderr.write(f"ccnavi: 閉じた記録を書けない: {failed}\n")
    git_sh = settings.script_command(root, "ccnavi-git.sh")
    wip = ticket_places.WIP_ROOT
    if phase.chat_only(root, conf, found.ticket, venues):
        stdout.write(
            f"次は、この移動をコミットし、`{wip}/` を消して"
            f"（'{git_sh} rm -r {wip}'）コミットし、統合先のブランチに取り込んでください。"
            "このチケットにはマージリクエストで見るフェーズが無いので、"
            "Draft を外す手順は無い。途中の作業は既定のブランチに残さない。"
            f"`{wip}/` で消えるのは追跡済みのファイルだけで、消す前のコミットから戻せる。"
            f"{worktrees.classifier_note(root, f'{git_sh} rm -r {wip}')}\n"
        )
        return
    if approval_marks.read_parent_mark(where, found.ticket, approval_marks.PARENT_MARK_READY):
        stdout.write("Draft は外してある。マージはユーザが行う\n")
        return
    stdout.write(
        "次は、この移動をコミットして push し"
        "（取り込み済みの親子では、この finish が済ませている）、"
        f"'{ready_command(root, found.ticket)}' で Draft を外してください"
        "（「マージに進んでよい」の合図）。"
        "ready は Draft を外す前に、閉じたチケットとその記録"
        f"（`{conf.approved}/` の done/・phases/・events/・flows/）を手元の logs/archive/ へ移し、"
        f"`{wip}/` の追跡済みのファイルを消して、その削除をコミットして push する"
        f"（`{wip}/` に未追跡のファイルがあれば消さずに止まる。消したものは履歴から戻せる）。"
        "Draft を外したあと、ready は親のワークツリーを消す（親のワークツリーの外から --parent で"
        "打てば消える。中から --parent なしで打つと消さず、外に出てから打つ 1 本を出す）。"
        "途中の作業もチケットも既定のブランチに残さない。マージはユーザが squash で行う。"
        f"{READY_FLOW_AGREED}\n"
    )


def cancel(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, ticket_id: str, reason: str
) -> int:
    """`doing/` → `done/`。取り消しの時刻と理由を書く。理由が要る。"""
    if not reason.strip():
        stderr.write("ccnavi: 取り消しには --reason <理由> が要る\n")
        return 1
    found = ops_close.find(stderr, root, conf, ticket_id)
    if found is None:
        return 1
    if found.state == ticket_model.TODO:
        stderr.write(
            f"ccnavi: {ticket_id} は未承認の提案（todo/）。取り消しの記録は要らないので、"
            "ファイルを消せばよい\n"
        )
        return 1
    if found.state != ticket_model.DOING:
        stderr.write(f"ccnavi: {ticket_id} は作業中ではない（いまは {found.state}/）\n")
        return 1
    if ops_close.parent_still_busy(stderr, root, conf, found):
        return 1
    fields = {"cancelled_at": approval_marks.now(), "cancel_reason": reason.strip()}
    return _move(
        stdout,
        stderr,
        root,
        conf,
        found,
        ticket_model.DONE,
        fields,
        f"取り消し: {reason.strip()}",
    )


def record_risk(
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
    answer = answer.strip().lower()
    if answer not in ("yes", "no"):
        stderr.write("ccnavi: 判定は yes か no\n")
        return 1
    if not reason.strip():
        stderr.write("ccnavi: record-risk には --reason <根拠> が要る\n")
        return 1
    found = ops_close.find(stderr, root, conf, ticket_id)
    if found is None:
        return 1
    if not found.is_child:
        stderr.write(f"ccnavi: {ticket_id} は子ではない。判定は子の差分に付ける\n")
        return 1
    definition = risk.load_definition(conf, root, _project_of(conf, root, found))
    factor = definition.factor(factor_id)
    if factor is None or factor.kind != risk.KIND_JUDGE:
        names = ", ".join(f.id for f in definition.judges) or "(無い)"
        stderr.write(
            f"ccnavi: {factor_id} は定性の項目ではない（{definition.source}）。あるのは: {names}\n"
        )
        return 1
    worktree = tree.worktree_path(root, ticket_id)
    head = ops_stop.worktree_head_sha(worktree)
    if not head:
        stderr.write(f"ccnavi: {worktree} の HEAD を読めない\n")
        return 1
    where = approval.home_dir(conf, root, ticket_id, found.parent, project=found.project)
    record = (
        approval_marks.read_child_record(
            where, found.parent, ticket_id, approval_marks.CHILD_RECORD_JUDGE
        )
        or {}
    )
    record[factor_id] = {
        "hit": answer == "yes",
        "reason": reason.strip(),
        "head": head,
        "at": approval_marks.now(),
        # その項目がどのレイヤーに書いてあるか。
        "source": factor.source,
    }
    failed = approval_marks.write_child_record(
        where, found.parent, ticket_id, approval_marks.CHILD_RECORD_JUDGE, record
    )
    if failed:
        stderr.write(f"ccnavi: 判定を記録できない: {failed}\n")
        return 1
    stdout.write(
        f"OK: {ticket_id} の {factor_id} を {answer} と記録した"
        f"（{'+' + str(factor.points) if answer == 'yes' else '加点なし'}、HEAD {head[:12]}）\n"
    )
    return 0


def _project_of(conf: settings.Settings, root: str, found: ticket_model.Ticket) -> str:
    """このチケットのレイヤーを決める `project:`。

    正とするのは承認済みチケットの側。子は親から継ぐので、親の承認済みチケットを引く。提案の側に
    書いてある値はユーザが承認していないので、判定の根拠にしない。
    """
    if not found.is_child:
        return found.project
    copies, _ = approval.scan(conf, root)
    parent = approval_checks.by_id(copies).get(found.parent)
    if parent is None:
        closed, _ = approval.scan(conf, root, closed=True)
        parent = approval_checks.by_id(closed).get(found.parent)
    return parent.project if parent is not None else found.project


def _score_child(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, found: ticket_model.Ticket
) -> list[str] | None:
    """子の実績のリスクを数えて記録する。閉じられなければ None。

    返すのは、閉じたあとに出す行（点と内訳）。親は数えない。
    """
    if not found.is_child:
        return []
    worktree = tree.worktree_path(root, found.ticket)
    definition = risk.load_definition(conf, root, _project_of(conf, root, found))
    if definition.fallback:
        stderr.write(f"ccnavi: {definition.fallback}\n")
    diff, why = risk.measure(worktree, found.base_sha)
    if diff is None:
        stderr.write(f"ccnavi: {found.ticket} のリスクを測れない: {why}\n")
        return None
    where = approval.home_dir(conf, root, found.ticket, found.parent, project=found.project)
    judgements = (
        approval_marks.read_child_record(
            where, found.parent, found.ticket, approval_marks.CHILD_RECORD_JUDGE
        )
        or {}
    )
    # record-risk の記録は C1 にしない。この終了が読んだ入力として一覧に載せ、
    # この C1 でコミットする。
    fsio.note_input(
        approval_marks.child_record_path(
            where, found.parent, found.ticket, approval_marks.CHILD_RECORD_JUDGE
        )
    )
    env = {
        "CCNAVI_BASE_SHA": diff.base,
        "CCNAVI_HEAD": diff.head,
        "CCNAVI_TICKET": found.ticket,
        "CCNAVI_PARENT": found.parent,
    }
    score = risk.evaluate(definition, diff, root, worktree, env, judgements)
    if score.pending:
        prompt = risk.judge_prompt(found.ticket, found.parent, diff, score.pending, worktree, root)
        where = ""
        if conf.state:
            path = os.path.join(conf.state, f"risk-judge-{found.ticket}.md")
            failed = fsio.write_text(path, prompt, newline="\n")
            if failed:
                stderr.write(f"ccnavi: 問いを書き出せない ({failed})\n")
            else:
                where = path
        names = ", ".join(f.id for f in score.pending)
        stderr.write(
            f"ccnavi: {found.ticket} を閉じる前に、定性のリスク項目の判定が要る: {names}\n"
            "  問いと差分の要約を渡してサブエージェントに判断させ、報告を "
            f"'{settings.script_command(root, 'ccnavi-ticket.sh')} record-risk {found.ticket} "
            "<項目> yes|no "
            "--reason <根拠>' で記録してから閉じ直してください\n"
        )
        if where:
            stderr.write(f"  問い: {where}\n")
        return None
    record = score.as_dict()
    record.update({"head": diff.head, "base": diff.base, "at": approval_marks.now()})
    record["summary"] = diff.summary()
    if definition.dropped:
        # 空として扱ったレイヤーの名前を残す。共通レイヤーだけで測ったことが、
        # あとから記録を読んだユーザに分かる。
        record["fallback"] = ",".join(definition.dropped)
    failed = approval_marks.write_child_record(
        where, found.parent, found.ticket, approval_marks.CHILD_RECORD_RISK, record
    )
    if failed:
        stderr.write(f"ccnavi: リスクを記録できない: {failed}\n")
        return None
    lines = score.lines()
    lines[0] += f"（{diff.summary()}、配点 {definition.source}）"
    if score.level in risk.ESCALATE_FROM:
        lines.append("  実績のリスクが高いので、宣言に関わらずこのフェーズは人間レビューが要る")
    return lines


def _move(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    found: ticket_model.Ticket,
    state: str,
    fields: dict[str, str],
    said: str,
) -> int:
    """`doing/` の承認済みチケットに欄を書き、`review/` か `done/` へ動かす。"""
    failed = approval_ops.update_fields(found.path, fields)
    if failed:
        stderr.write(f"ccnavi: {found.ticket} に欄を書けない: {failed}\n")
        return 1
    where = os.path.dirname(os.path.dirname(found.path))
    if state == ticket_model.REVIEW:
        failed = approval_ops.to_review(where, found.tree_root, conf.tickets, found.ticket)
        place = f"{conf.tickets}/{ticket_model.REVIEW}/"
    else:
        failed = approval_ops.close_copy(where, found.ticket)
        place = f"{conf.approved}/{ticket_model.DONE}/"
    if failed:
        stderr.write(f"ccnavi: {found.ticket}: {failed}\n")
        return 1
    cancelled = bool(fields.get("cancelled_at"))
    history.note(
        where,
        found.ticket,
        history.KIND_CANCELLED if cancelled else history.KIND_FINISHED,
        ticket_model.DOING,
        state,
        reason=fields.get("cancel_reason") if cancelled else None,
    )
    stdout.write(f"OK: {found.ticket} を {place} へ動かした（{said}）\n")
    if state == ticket_model.REVIEW:
        # 依頼の前に「ユーザのレビューを待つ」と言わない。次に誰が何をするかを、
        # 依頼の有無で言い分ける（phase.review_next）。
        moved = replace(found, completed_at=fields.get("completed_at", ""))
        # 誰がいつ done/ へ動かすかは review_next の文が言う（重ねて言わない）。
        stdout.write(
            (
                phase.review_next(root, conf, moved)
                or "まだレビュー準備中。親がレビューを依頼するまで、ユーザには回らない。"
                "依頼の後、ユーザがレビューを終えて confirm / decide / --reviewed を打つと "
                f"{conf.approved}/{ticket_model.DONE}/ へ動く"
            )
            + "\n"
        )
    return 0


def _branch_words(t: ticket_model.Ticket) -> str:
    """ワークツリーを作る案内の、ブランチの語。

    親の `branch:` が識別子と違えば、そのブランチを出す（既にあればそのまま、無ければ `-b` で
    切る。`ccnavi-git.sh worktree add` は `branch:` と一致する名前だけを通す）。ほかは今どおり
    `-b <識別子>`。
    """
    branch = ticket_ids.branch_name(t)
    if branch == t.ticket:
        return f"-b {t.ticket}"
    return f"{branch}'（無ければ '-b {branch} <起点>'）"
