"""チケットの状態を動かす操作。`ccnavi ticket start|finish|cancel <識別子>`。

親が `.ccnavi/scripts/ccnavi-ticket.sh` から呼ぶ。スクリプトは薄く、ここが本体。
サブエージェントからの呼び出しは cli.py が止める（`agent_id` が付いていたら拒む）。

やることは置き場を動かして欄を書くことだけ（ADR-0055）。`start` は置き場を動かさず
`doing/` の欄を書く。`finish` は `doing/` から `wip/proposals/review/`（レビュー要）か
`.ccnavi/approved/done/`（不要）へ、`cancel` は `doing/` から `done/` へ動かす。
ワークツリーの削除は親のマージ手順に任せる。順序は「子の成果をマージ → finish →
ワークツリーを消す」で、finish の前にワークツリーを消すと base_sha の検査ができなくなる。
"""

from __future__ import annotations

import os
from typing import TextIO

from . import approval, configsync, fsio, gitcmd, phase, risk, settings, tree
from . import ticket as ticket_mod

TIMEOUT_SECONDS = 5.0


def start(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, ticket_id: str
) -> int:
    """`doing/` の承認済みチケットに、着手の時刻と基準点を書く。置き場は動かない。"""
    found = _find(stderr, root, conf, ticket_id)
    if found is None:
        return 1
    if found.state != ticket_mod.DOING:
        stderr.write(
            f"ccnavi: {ticket_id} は承認済みの作業中ではない（いまは {found.state}/）。"
            + (
                "先に利用者が 'ccnavi --approve' を通すこと"
                if found.state == ticket_mod.TODO
                else ""
            )
            + "\n"
        )
        return 1
    if found.started_at:
        stderr.write(f"ccnavi: {ticket_id} は着手済み（{found.started_at}）\n")
        return 1
    if _parent_not_started(stderr, root, conf, found):
        return 1
    # ワークツリーは承認済みチケットの `project` が指すリポジトリから
    # 切られていること（REQ-MLT-13）。
    # 元リポジトリが違えば、判定はそのツリーの元リポジトリで行われ、チケットと噛み合わない。
    owner = tree.project_root(conf.projects, found.project) or root
    worktree = tree.worktree_path(root, ticket_id)
    if not tree.is_worktree_of(owner, worktree) or not tree.exact_name(root, ticket_id):
        where = f"projects/{found.project} の中で " if found.project else ""
        stderr.write(
            f"ccnavi: {ticket_id} のワークツリー {worktree} が無いか、"
            "元リポジトリが承認済みチケットの project"
            f"（{found.project or 'ワークスペース'}）と違う（綴りは大文字小文字まで同じで）。"
            f"先に {where}'{settings.script_command(root, 'ccnavi-git.sh')} "
            f'worktree add "{worktree}" '
            f"-b {ticket_id}' で作ること\n"
        )
        return 1
    sha = _head(worktree)
    if not sha:
        stderr.write(f"ccnavi: {worktree} の HEAD を読めない\n")
        return 1
    synced = _sync_config(stderr, root, conf, found, worktree)
    if synced is None:
        return 1
    fields = {"started_at": approval.now(), "base_sha": sha}
    failed = approval.update_fields(found.path, fields)
    if failed:
        stderr.write(f"ccnavi: {ticket_id} に着手の欄を書けない: {failed}\n")
        return 1
    stdout.write(
        f"OK: {found.ticket} に着手した（{fields['started_at']} / 基準点 {sha[:12]}）。"
        f"置き場は {ticket_mod.DOING}/ のまま\n"
    )
    for line in synced:
        stdout.write(line + "\n")
    return 0


def _sync_config(
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    found: ticket_mod.Ticket,
    worktree: str,
) -> list[str] | None:
    """親の着手の前に、共通層でプロジェクトの層を上書きする（設計 §11.12）。

    返すのは着手の出力に足す行。写せなければ None（着手しない）。子は親のブランチに
    乗るので比べない。ワークスペース自身の作業は、共通層と同じリポジトリにあるので比べない。
    """
    if found.is_child or not found.project:
        return []
    copied, why = configsync.plan(conf, worktree)
    if why:
        stderr.write(f"ccnavi: {found.ticket} の設定を比べられない: {why}\n")
        return None
    if not copied:
        return []
    where = approval.home_dir(conf, root, found.ticket, "")
    failed = configsync.apply(conf, worktree, where, found.ticket, copied)
    if failed:
        stderr.write(f"ccnavi: {found.ticket} の設定を写せない: {failed}\n")
        return None
    git_sh = settings.script_command(root, "ccnavi-git.sh")
    lines = [
        f"共通層とプロジェクト {found.project} の設定が違っていたので、共通層で上書きした。"
        "最初のレビューの依頼の頭に載る:"
    ]
    for c in copied:
        line = f"  - {c.rel}（{'上書き' if c.existed else '新しく置いた'}）"
        if c.lost:
            line += "。消えた識別子: " + ", ".join(c.lost)
        lines.append(line)
    mark = approval.parent_mark_path(where, found.ticket, configsync.MARK)
    lines.append(
        f"  作業を始める前に、{worktree} で {', '.join(c.rel for c in copied)} を"
        f" '{git_sh} add' してコミットすること。印 {mark} も、それを持つツリーでコミットする"
    )
    return lines


def finish(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, ticket_id: str
) -> int:
    """`doing/` → `review/`（レビュー要）か `done/`（不要）。完了の時刻を書く。"""
    found = _find(stderr, root, conf, ticket_id)
    if found is None:
        return 1
    if found.state != ticket_mod.DOING:
        stderr.write(f"ccnavi: {ticket_id} は作業中ではない（いまは {found.state}/）\n")
        return 1
    if not found.started_at:
        stderr.write(
            f"ccnavi: {ticket_id} は未着手。先に "
            f"'{settings.script_command(root, 'ccnavi-ticket.sh')} start {ticket_id}' を通す\n"
        )
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
    state = ticket_mod.REVIEW if _needs_review(root, conf, found) else ticket_mod.DONE
    code = _move(stdout, stderr, root, conf, found, state, fields, f"完了 {fields['completed_at']}")
    if code == 0 and scored:
        for line in scored:
            stdout.write(line + "\n")
    if code == 0 and not found.is_child:
        _close_parent(stdout, stderr, root, conf, found)
    return code


def _needs_review(root: str, conf: settings.Settings, found: ticket_mod.Ticket) -> bool:
    """この子を閉じたとき、人が見る対象になるか（設計 §9.8）。親は見ない。

    フェーズの「見る場所」を、この子を閉じたものとして数え直す。延期したフェーズの子も
    レビュー待ちに置く。見るのは次にレビューがあるフェーズの番だが、人が見るまでは
    見られていない。実績のリスクは `_score_child` が記録した直後なので、ここで数え直すと
    宣言に関わらず上がった分も入る。
    """
    if not found.is_child or found.phase is None:
        return False
    for ph in phase.phases_of(root, conf, found.parent):
        if ph.number != found.phase:
            continue
        ph.states[found.ticket] = ticket_mod.DONE
        return ph.deferred or ph.review_required
    return found.review_required


def _close_parent(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, found: ticket_mod.Ticket
) -> None:
    """親を閉じたあとの記録と案内。

    記録（`closed.json`）は、どのフェーズをどこで見たかを親のブランチに残す。提案は
    統合先へ戻す前に `wip/` ごと消えるので、マージリクエストを作らない運び方では
    締めた事実の残る先がここしか無い（設計 §9.8）。

    案内は運び方で分かれる。マージリクエストがあるなら Draft を外す合図まで、無いなら統合先へ戻す
    ところまで。ccnavi はどちらでもマージしない。
    """
    from .review import WIP_ROOT

    where = approval.home_dir(conf, root, found.ticket, "")
    venues = phase.review_venues(root, conf, found.ticket)
    failed = approval.write_parent_mark(
        where,
        found.ticket,
        approval.PARENT_MARK_CLOSED,
        {"reviews": {str(n): venues[n] for n in sorted(venues)}},
    )
    if failed:
        stderr.write(f"ccnavi: 締めた記録を書けない: {failed}\n")
    git_sh = settings.script_command(root, "ccnavi-git.sh")
    if phase.chat_only(root, conf, found.ticket, venues):
        stdout.write(
            f"次は、この移動をコミットし、`{WIP_ROOT}/` を消して"
            f"（'{git_sh} rm -r {WIP_ROOT}'）コミットし、統合先のブランチへ戻す。"
            "このチケットにはマージリクエストで見るフェーズが無いので、"
            "Draft を外す手順は無い。途中の作業は既定のブランチに残さない\n"
        )
        return
    if approval.read_parent_mark(where, found.ticket, approval.PARENT_MARK_READY):
        stdout.write("Draft は外してある。マージは利用者が行う\n")
        return
    review_sh = settings.script_command(root, "ccnavi-review.sh")
    stdout.write(
        f"次は、この移動をコミットし、`{WIP_ROOT}/` を消して"
        f"（'{git_sh} rm -r {WIP_ROOT}'）コミットし、"
        f"push してから '{review_sh} ready' で Draft を外す"
        "（「マージに進んでよい」の合図）。途中の作業は既定のブランチに残さない。"
        "マージは利用者が squash で行う\n"
    )


def cancel(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, ticket_id: str, reason: str
) -> int:
    """`doing/` → `done/`。取り消しの時刻と理由を書く。理由が要る。"""
    if not reason.strip():
        stderr.write("ccnavi: 取り消しには --reason <理由> が要る\n")
        return 1
    found = _find(stderr, root, conf, ticket_id)
    if found is None:
        return 1
    if found.state == ticket_mod.TODO:
        stderr.write(
            f"ccnavi: {ticket_id} は未承認の提案（todo/）。取り消しの記録は要らないので、"
            "ファイルを消せばよい\n"
        )
        return 1
    if found.state != ticket_mod.DOING:
        stderr.write(f"ccnavi: {ticket_id} は作業中ではない（いまは {found.state}/）\n")
        return 1
    if _parent_still_busy(stderr, root, conf, found):
        return 1
    fields = {"cancelled_at": approval.now(), "cancel_reason": reason.strip()}
    return _move(
        stdout,
        stderr,
        root,
        conf,
        found,
        ticket_mod.DONE,
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
    found = _find(stderr, root, conf, ticket_id)
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
    head = _head(worktree)
    if not head:
        stderr.write(f"ccnavi: {worktree} の HEAD を読めない\n")
        return 1
    where = approval.home_dir(conf, root, ticket_id, found.parent)
    record = (
        approval.read_child_record(where, found.parent, ticket_id, approval.CHILD_RECORD_JUDGE)
        or {}
    )
    record[factor_id] = {
        "hit": answer == "yes",
        "reason": reason.strip(),
        "head": head,
        "at": approval.now(),
        # その項目がどの層に書いてあるか（設計 §11.9）。
        "source": factor.source,
    }
    failed = approval.write_child_record(
        where, found.parent, ticket_id, approval.CHILD_RECORD_JUDGE, record
    )
    if failed:
        stderr.write(f"ccnavi: 判定を記録できない: {failed}\n")
        return 1
    stdout.write(
        f"OK: {ticket_id} の {factor_id} を {answer} と記録した"
        f"（{'+' + str(factor.points) if answer == 'yes' else '加点なし'}、HEAD {head[:12]}）\n"
    )
    return 0


def _project_of(conf: settings.Settings, root: str, found: ticket_mod.Ticket) -> str:
    """このチケットの層を決める `project:`（設計 §11.4.1、§11.4.2）。

    権威は承認済みチケットの側。子は親から継ぐので、親の承認済みチケットを引く。提案の側に
    書いてある値は人が承認していないので、判定の根拠にしない。
    """
    if not found.is_child:
        return found.project
    copies, _ = approval.scan(conf, root)
    parent = approval.by_id(copies).get(found.parent)
    if parent is None:
        closed, _ = approval.scan(conf, root, closed=True)
        parent = approval.by_id(closed).get(found.parent)
    return parent.project if parent is not None else found.project


def _score_child(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, found: ticket_mod.Ticket
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
    where = approval.home_dir(conf, root, found.ticket, found.parent)
    judgements = (
        approval.read_child_record(where, found.parent, found.ticket, approval.CHILD_RECORD_JUDGE)
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
            "--reason <根拠>' で記録してから閉じ直すこと\n"
        )
        if where:
            stderr.write(f"  問い: {where}\n")
        return None
    record = score.as_dict()
    record.update({"head": diff.head, "base": diff.base, "at": approval.now()})
    record["summary"] = diff.summary()
    if definition.dropped:
        # 空として扱った層の名前を残す（設計 §11.2）。共通層だけで測ったことが、
        # あとから記録を読んだ人に分かる。
        record["fallback"] = ",".join(definition.dropped)
    failed = approval.write_child_record(
        where, found.parent, found.ticket, approval.CHILD_RECORD_RISK, record
    )
    if failed:
        stderr.write(f"ccnavi: リスクを記録できない: {failed}\n")
        return None
    lines = score.lines()
    lines[0] += f"（{diff.summary()}、配点 {definition.source}）"
    if score.level in risk.ESCALATE_FROM:
        lines.append("  実績のリスクが高いので、宣言に関わらずこのフェーズは人間レビューが要る")
    return lines


def _places(
    root: str, conf: settings.Settings, ticket_id: str
) -> tuple[list[ticket_mod.Ticket], list[str], list[ticket_mod.Problem]]:
    """この識別子のチケットが在る置き場を全部引く。読めなかった理由と提案の不備も返す。

    権威のあるツリーの側だけを読む（approval.scan / ticket.scan の畳み）。
    """
    hits: list[ticket_mod.Ticket] = []
    copies, notes = approval.scan(conf, root)
    hits += [t for t in copies if t.ticket == ticket_id]
    review, more = approval.scan_review(conf, root)
    hits += [t for t in review if t.ticket == ticket_id]
    closed, _ = approval.scan(conf, root, closed=True)
    hits += [t for t in closed if t.ticket == ticket_id]
    proposals, problems = ticket_mod.scan(root, conf.tickets, conf.projects)
    # `todo/` は承認の前の姿。承認済みチケット（作業中・レビュー待ち・閉じた）が在れば、同じ
    # 識別子の `todo/` は改版の候補か書き損じで、状態の操作の相手ではない（`--lint` が言う）。
    if not hits:
        hits += [t for t in proposals if t.ticket == ticket_id and t.state == ticket_mod.TODO]
    return hits, notes + more, problems


def _where(hits: list[ticket_mod.Ticket]) -> str:
    """複数の置き場に在るときに、その在り処を並べた文言。"""
    return ", ".join(f"{t.tree or '(ワークスペースルート)'}:{t.state}" for t in hits)


def _undecided(stderr: TextIO, head: str, hits: list[ticket_mod.Ticket]) -> None:
    """どれが本物か決まらないときの文面。次の一手まで書く。

    「1 つにしてから」だけだと、写しはどれも追跡されたファイルなので、受け取った側に
    できることが読めない。権威の決まり方（親のツリー → 元ツリー）と、この場面で
    それが決まらない理由を名指しする。
    """
    home = hits[0].parent or hits[0].ticket
    stderr.write(head + f"が複数の場所にある: {_where(hits)}。1 つに決まるまで動かさない\n")
    stderr.write(
        f"  本物は、親 {home} のワークツリーの写し。無ければ元ツリー"
        "（ワークスペースルート、プロジェクトのチケットならそのプロジェクト）の写し\n"
    )
    stderr.write(
        "  どちらにも無いか、元ツリーより先の置き場に在る写しがあると決まらない。"
        "先に進んだ側を合流させるか、残ったワークツリーを畳んでから打ち直すこと\n"
    )


def _find(
    stderr: TextIO, root: str, conf: settings.Settings, ticket_id: str
) -> ticket_mod.Ticket | None:
    """この識別子のチケットを、どの置き場に在っても 1 つ引く。`state` に置き場が入る。

    2 つ以上残れば、どれが本物か決まらないので止める。
    """
    hits, notes, problems = _places(root, conf, ticket_id)
    if not hits:
        stderr.write(
            f"ccnavi: チケット {ticket_id} が見つからない"
            f"（{conf.approved}/ と {conf.tickets}/ の下を全ツリーで探した）\n"
        )
        for p in problems:
            stderr.write(f"  {p}\n")
        for note in notes:
            stderr.write(f"  {note}\n")
        return None
    if len(hits) > 1:
        _undecided(stderr, f"ccnavi: {ticket_id} ", hits)
        return None
    return hits[0]


def _parent_not_started(
    stderr: TextIO, root: str, conf: settings.Settings, found: ticket_mod.Ticket
) -> bool:
    """子に着手してよいか。親が作業中で着手済みでなければ止める（設計 §9.6、REQ-TKT-48）。

    親の `start` を飛ばしても途中では何も壊れず、親を閉じるときだけが通らない。壊れない
    ので気付けず、気付くのがいちばん遅い場所になる。親の作業が実際に始まる瞬間
    （最初の子の着手）で止めれば、いちばん早い場所で言える。

    親は別の置き場に在ることもある（未承認、閉じた）。どれも子に着手してよい状態では
    ないので、そのまま置き場を名指しして止める。
    """
    if not found.is_child:
        return False
    hits, notes, _ = _places(root, conf, found.parent)
    ticket_sh = settings.script_command(root, "ccnavi-ticket.sh")
    head = f"ccnavi: {found.ticket} の親 {found.parent} "
    if not hits:
        stderr.write(
            head + f"が見つからない（{conf.approved}/ と {conf.tickets}/ を全ツリーで探した）\n"
        )
        for note in notes:
            stderr.write(f"  {note}\n")
        return True
    if len(hits) > 1:
        _undecided(stderr, head, hits)
        return True
    parent = hits[0]
    if parent.state == ticket_mod.TODO:
        stderr.write(
            head + "がまだ承認されていない（todo/）。先に利用者が 'ccnavi --approve' を通し、"
            f"'{ticket_sh} start {found.parent}' で着手すること\n"
        )
        return True
    if parent.state != ticket_mod.DOING:
        # 置き場だけを言う。`review/` に親が居るのは壊れたデータのときだけだが、そこで
        # 「閉じた」と言うと、文面が事実と違う。
        stderr.write(head + f"は作業中ではない（いまは {parent.state}/）。子を足す相手ではない\n")
        return True
    if not parent.started_at:
        stderr.write(
            head + f"が未着手（{parent.state}/）。子より先に親に着手すること。\n"
            f"  '{ticket_sh} start {found.parent}'\n"
        )
        return True
    return False


def _parent_still_busy(
    stderr: TextIO, root: str, conf: settings.Settings, found: ticket_mod.Ticket
) -> bool:
    """親を閉じてよいか。開いている子やレビュー待ちのフェーズがある間は閉じさせない。

    親の承認済みチケットが閉じると止めるときの鍵（cwd から引く親）が消え、レビュー要の
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
    進んでよい状態は同じもの。人が close-early で締めていれば、開いている子以外は問わない。
    人が締めたあとに残っているものは、締めたときに別の issue へ写してある。
    """
    copies, _ = approval.scan(conf, root)
    open_children = [t.ticket for t in copies if t.parent == parent_id]
    if open_children:
        return [
            f"{parent_id} には開いている子がある（{', '.join(open_children)}）。子を先に閉じること"
        ]
    if approval.read_parent_mark(
        approval.home_dir(conf, root, parent_id, ""), parent_id, approval.PARENT_MARK_CLOSE_EARLY
    ):
        return []
    problems: list[str] = []
    held = phase.held_phase(root, conf, parent_id)
    if held is not None:
        problems.append(
            f"{parent_id} のフェーズ {held.label} は{held.review_label}。レビューを済ませてから"
        )
    closed_copies, _ = approval.scan(conf, root, closed=True)
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
    """フェーズの最後の子を閉じる前に、種類の成果物が揃っているか（設計 §9.8）。

    在って追跡されていることだけを見る。中身は見ない。空でも在ることは分かるので、
    「調査したことにする」は塞げる。
    """
    if not found.is_child or found.phase is None:
        return False
    copies, _ = approval.scan(conf, root)
    parent = approval.by_id(copies).get(found.parent)
    if parent is None or not parent.has_plan:
        return False
    item = parent.item_at(found.phase)
    types = phase.load_types(conf, root, parent.project) or {}
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
            if t.ticket != found.ticket and ph.states.get(t.ticket) == ticket_mod.DOING
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
        "親か子のワークツリーに置いて追跡（git add）してから閉じること\n"
    )
    return True


def _tracked(worktree: str, glob: str) -> bool:
    """この glob に当たる追跡済みのファイルが 1 つでもあるか。"""
    if not os.path.isdir(worktree):
        return False
    rc, out = gitcmd.output(worktree, ["ls-files", "--", glob], TIMEOUT_SECONDS)
    return rc == 0 and bool(out.strip())


def _move(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    found: ticket_mod.Ticket,
    state: str,
    fields: dict[str, str],
    said: str,
) -> int:
    """`doing/` の承認済みチケットに欄を書き、`review/` か `done/` へ動かす。"""
    failed = approval.update_fields(found.path, fields)
    if failed:
        stderr.write(f"ccnavi: {found.ticket} に欄を書けない: {failed}\n")
        return 1
    where = os.path.dirname(os.path.dirname(found.path))
    if state == ticket_mod.REVIEW:
        failed = approval.to_review(where, found.tree_root, conf.tickets, found.ticket)
        place = f"{conf.tickets}/{ticket_mod.REVIEW}/"
    else:
        failed = approval.close_copy(where, found.ticket)
        place = f"{conf.approved}/{ticket_mod.DONE}/"
    if failed:
        stderr.write(f"ccnavi: {found.ticket}: {failed}\n")
        return 1
    stdout.write(f"OK: {found.ticket} を {place} へ動かした（{said}）\n")
    if state == ticket_mod.REVIEW:
        stdout.write(
            "人のレビューを待つ。レビューが済むと利用者の操作（confirm / decide / --reviewed）で "
            f"{conf.approved}/{ticket_mod.DONE}/ へ動く\n"
        )
    return 0


def _head(worktree: str) -> str:
    rc, out = gitcmd.output(worktree, ["rev-parse", "HEAD"], TIMEOUT_SECONDS)
    return out.strip() if rc == 0 else ""
