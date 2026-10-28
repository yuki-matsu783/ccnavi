"""チケットの状態を動かす操作。`ccnavi ticket start|finish|cancel <識別子>`。

親が `.ccnavi/scripts/ccnavi-ticket.sh` から呼ぶ。スクリプトは薄く、ここが本体。
サブエージェントからの呼び出しは hook の判定が止める（`hook/judge.py` が `phase_forms.forbidden` で
`DENY_SUBAGENT_TICKET_OP` を返す）。実行ファイルは呼び手がサブエージェントかを知らない。

やることは置き場を動かして欄を書くことだけ（状態は置き場が表す）。`start` は置き場を動かさず
`doing/` の欄を書く。`finish` は `doing/` から `wip/proposals/review/`（レビュー要）か
`.ccnavi/approved/done/`（不要）へ、`cancel` は `doing/` から `done/` へ動かす。
子のワークツリーは、`confirm` が子を `done/` へ動かしたあとと、
親の `finish` のあとに sh が `worktree tidy` で消す（tickets/lifecycle/worktrees.py）。
finish の前にワークツリーを
消すと base_sha の検査ができなくなり、レビュー待ちの間に見返す場所も無くなる（そこで積んだ
コミットは親へ合流しないので、差し戻しは同じフェーズに子を足して直す）。

ここに置くのは操作の本体（`start` / `finish` / `cancel` / `record_risk`）と、`finish` が閉じるときに
実績のリスクを数える手順。
チケットを引いて閉じてよいかを検査する部分は `tickets/lifecycle/close.py`、Stop で
`finish` の打ち忘れを促す部分と git の読み取りは `tickets/lifecycle/stop.py` に分けてある。
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import TextIO

from ...infra import fsio, gitcmd, gitstate, settings, tree
from ...policy import texts
from ..approval import checks as approval_checks
from ..approval import marks as approval_marks
from ..approval import ops as approval_ops
from ..approval import store as approval
from ..config import risk
from ..flow import graph as flow
from ..learning import notes as learning
from ..model import ticket_model, ticket_places
from ..phase import gates as phase
from ..sync import template_drafts
from . import close as ops_close
from . import plan as ops_plan
from . import stop as ops_stop
from . import worktrees

# 最後のレビューのあと、Draft を外すまでの手順がユーザの合意の範囲に入ることの言い切り。
# `confirm` の知らせ（core_review.after_last_review）と親の `finish` の出力が同じ文を使う。
# ユーザがレビュー済みにした時点で、ここまでは合意済み。外へ出る操作（push・Draft を外す）の前に
# 確認を取り直すと、同じ流れの中で何度も許可を求めることになる。
READY_FLOW_AGREED = (
    "ここから Draft を解除するまで（finish → push → ready。`wip/` の削除は ready に入っている）は、"
    "ユーザがレビューして合意した範囲に入る。"
    "push と Draft を解除する前に、ユーザへ確認を取り直さずに進める"
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
    """`doing/` の承認済みチケットに、着手の時刻と基準点を書く。

    処理フロー:
    1. `ops_close.find` で承認済みを引き、`doing/` の未着手であることと、置き場・計画・
       親の着手・先行チケットを確かめる（`misplaced_home`・`plan_missing`・
       `parent_not_started`・`predecessors_unmet`）。
    2. ワークツリーが承認済みの `project` の元リポジトリから切られているかを
       `tree.is_worktree_of` で確かめる。
    3. ワークツリーの HEAD を基準点にする。
    4. `approval_ops.update_fields` で `started_at` と `base_sha` を欄に書く。
    5. 子ならフローのハッシュを `flow.record_digest` で記録し、次の一手を案内する。
    """
    found = ops_close.find(stderr, root, conf, ticket_id)
    if found is None:
        return 1
    if found.state != ticket_model.DOING:
        stderr.write(
            f"ccnavi: {ticket_id} は承認済みの作業中ではない（いまは {found.state}/）。"
            + (
                "先にユーザに 'ccnavi-user-agree.sh' を通してもらってください"
                if found.state == ticket_model.TODO
                else ""
            )
            + "\n"
        )
        return 1
    if found.started_at:
        stderr.write(f"ccnavi: {ticket_id} は着手済み（{found.started_at}）\n")
        return 1
    if ops_close.misplaced_home(stderr, root, conf, found):
        return 1
    if ops_plan.plan_missing(stderr, found):
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
    if not tree.is_worktree_of(owner, worktree):
        stderr.write(
            f"ccnavi: {ticket_id} のワークツリー {worktree} が無いか、"
            "元リポジトリが承認済みチケットの project"
            f"（{found.project or 'ワークスペース'}）と違う。"
            f"{_create_words(root, found, worktree)}\n"
        )
        return 1
    sha = ops_stop.worktree_head_sha(worktree)
    if not sha:
        stderr.write(f"ccnavi: {worktree} の HEAD を読めない\n")
        return 1
    fields = {"started_at": approval_marks.now(), "base_sha": sha}
    failed = approval_ops.update_fields(found.path, fields)
    if failed:
        stderr.write(f"ccnavi: {ticket_id} に着手の欄を保存できませんでした: {failed}\n")
        return 1
    stdout.write(
        f"OK: {found.ticket} に着手した（{fields['started_at']} / 基準点 {sha[:12]}）。"
        f"置き場は {ticket_model.DOING}/ のまま\n"
    )
    if found.is_child:
        # 着手のときのフローのハッシュを記録する。SubagentStart / SubagentStop が、着手のあとに
        # 書き換わったら知らせる。
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
    if found.project and not found.is_child:
        # 共通レイヤーと工程スキルのコピーは、ここでは書かない（`promptsync`）。
        stdout.write(
            "共通レイヤーと工程スキルのプロジェクトへのコピーは、次のユーザの発言で置かれる\n"
        )
    for line in ops_plan.childless_notice(root, conf, found):
        stdout.write(line + "\n")
    if found.is_child:
        stdout.write(texts.say(root, "NEXT_OPS_START_CHILD", {"ticket": found.ticket}) + "\n")
    if learning.applies(root, conf, found):
        stdout.write(
            "学びの蓄積: 閉じる前に "
            f"'{settings.script_command(root, 'ccnavi-ticket.sh')} learn' を実行し、"
            f"wip/handoff/learnings/{found.ticket}.md に気づきを書いて git add と git commit をする"
            "（無ければ learnings: none）\n"
        )
    return 0


def finish(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, ticket_id: str
) -> int:
    """`doing/` → `review/`（レビュー要）か `done/`（不要）。完了の時刻を書く。

    処理フロー:
    1. `ops_close.find` で承認済みを引き、着手済みであることと、置き場・親の状態・成果物を確かめる
       （`misplaced_home`・`parent_still_busy`・`deliverables_missing`）。
    2. 学びの記録（`learning.record_missing`・`learning.parent_blocked`）を確かめる。
    3. 子なら `_score_child` で実績のリスクを数える。判定が揃わなければ閉じない。
    4. 完了の時刻・親へ入った子の先頭（`_merged_sha`）・行き先（`_needs_review`）を決める。
       行き先は `review` か `done`。
    5. `_move` で欄を書いて動かす。成功したら、リスクの行を出し、親なら `_close_parent` を呼ぶ。
    """
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
    if ops_close.misplaced_home(stderr, root, conf, found):
        return 1
    if ops_close.parent_still_busy(stderr, root, conf, found):
        return 1
    if ops_close.deliverables_missing(stderr, root, conf, found):
        return 1
    if learning.record_missing(stderr, root, conf, found):
        return 1
    # 親は、学び以外の検査をすべて通したあとに、親の今の HEAD で気づきの処理の証跡を照合する。
    if learning.parent_blocked(stderr, root, conf, found):
        return 1
    # 子は、閉じる前に実績のリスクを数える（tickets/config/risk.py）。
    # 定性項目の判定が揃わなければ閉じない。
    scored = _score_child(stdout, stderr, root, conf, found)
    if scored is None:
        return 1
    fields = {"completed_at": approval_marks.now()}
    merged = _merged_sha(root, found)
    if merged:
        fields["merged_sha"] = merged
    state = ticket_model.REVIEW if _needs_review(root, conf, found) else ticket_model.DONE
    # 置き場の状態だけでは、あとから「review 行きだったか」は求まらない（review_kind は
    # 実績のリスクなどで変わる）。置いた先を残し、手で動かしたかどうかの判定
    # （settled_by の有無）の根拠にする。
    fields["finished_to"] = state
    code = _move(stdout, stderr, root, conf, found, state, fields, f"完了 {fields['completed_at']}")
    if code == 0 and scored:
        for line in scored:
            stdout.write(line + "\n")
    if code == 0 and not found.is_child:
        _close_parent(stdout, stderr, root, conf, found)
    return code


def _merged_sha(root: str, found: ticket_model.Ticket) -> str:
    """この finish で親のブランチに入る子の先頭。入らない（合流していない）なら空。

    親のワークツリーが合流の途中なら、その `MERGE_HEAD`（finish の検査が読んだ子の先頭）が
    このコミットで入る先頭（`_merging_head`）。検査のあとに子が積んだコミットは合流に載らない。
    合流の途中でなくても、子の先頭が親の先頭の祖先なら（子が先に親へ入っている）その先頭。
    どちらでもなければ、親のブランチに入らない子なので書かない（レビュー依頼の前提が言う）。
    """
    if not found.is_child:
        return ""
    merging = _merging_head(root, found)
    if merging:
        return merging
    child_tree = tree.worktree_path(root, found.ticket)
    parent_tree = tree.worktree_path(root, found.parent)
    head = gitstate.head(child_tree)
    if not head:
        return ""
    parent_head = gitstate.head(parent_tree)
    if not parent_head:
        return ""
    done = gitcmd.run(
        child_tree, ["merge-base", "--is-ancestor", head, parent_head], ops_close.TIMEOUT_SECONDS
    )
    return head if done.ok else ""


def _merging_head(root: str, found: ticket_model.Ticket) -> str:
    """親のワークツリーが合流している子の先頭（`MERGE_HEAD`）。合流の途中でなければ空。

    `MERGE_HEAD` が子のブランチの先頭か、その祖先のときだけ採る。親のワークツリーで
    別のものを合流している途中（write-and-push の外の手作業）を、この子の合流と取り違えないため。
    """
    if not found.is_child:
        return ""
    parent_tree = tree.worktree_path(root, found.parent)
    rc, out = gitcmd.output(
        parent_tree, ["rev-parse", "--verify", "--quiet", "MERGE_HEAD"], ops_close.TIMEOUT_SECONDS
    )
    merging = out.strip() if rc == 0 else ""
    head = gitstate.head(tree.worktree_path(root, found.ticket))
    if not merging or not head:
        return ""
    done = gitcmd.run(
        parent_tree, ["merge-base", "--is-ancestor", merging, head], ops_close.TIMEOUT_SECONDS
    )
    return merging if done.ok else ""


def _needs_review(root: str, conf: settings.Settings, found: ticket_model.Ticket) -> bool:
    """この子を閉じたとき、ユーザが見る対象になるか。

    フェーズの「見る場所」を、この子を閉じたものとして数え直す。延期したフェーズの子も
    レビュー待ちに置く。見るのは次にレビューがあるフェーズの番だが、ユーザが見るまでは
    見られていない。実績のリスクは `_score_child` が記録した直後なので、ここで数え直すと
    宣言に関わらず上がった分も入る。
    """
    if not found.is_child or found.phase is None:
        return False
    if found.is_consolidate:
        # 統合の子は必ずレビュー不要。done/ へ移す。
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
    無いなら統合先に取り込むところまで。
    Draft を外す `ready` は、閉じたチケットとその記録を手元の `logs/archive/` へ退避してから外す
    （`review_close.ready`・tickets/model/archive.py）。この記録（`closed.json`）も一緒に退避される。
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
        stderr.write(f"ccnavi: クローズ済みの記録を保存できませんでした: {failed}\n")
    git_sh = settings.script_command(root, "ccnavi-git.sh")
    wip = ticket_places.WIP_ROOT
    if phase.without_mr(root, conf, found.ticket, venues):
        stdout.write(
            texts.say(
                root,
                "NEXT_OPS_CLOSE_NO_MR",
                {
                    "wip": wip,
                    "classifier_note": worktrees.classifier_note(root, f"{git_sh} rm -r {wip}"),
                },
            )
            + "\n"
        )
        # 材料（`done/`）は取り込みまで残るので、`wip/` を消す前の今なら下書きが作れる。
        stdout.write(template_drafts.close_notice(conf, root, found.ticket, mr=False))
        return
    if approval_marks.read_parent_mark(where, found.ticket, approval_marks.PARENT_MARK_READY):
        # ready は印を置いたあとに `done/` を `logs/archive/` へ退避するので、材料は拾えず、
        # `close_notice` も空を返す。案内を足さないのはそのため。この分岐に来るのは、ready の印が
        # 残ったまま作業中の親を finish したときだけ。印は正常なら退避と一緒に消え、退避が途中で
        # 止まったときだけ残る。
        stdout.write("Draft は外してある。マージはユーザが行う\n")
        return
    stdout.write(
        texts.say(
            root,
            "NEXT_OPS_CLOSE_MR",
            {
                "ready_command": ready_command(root, found.ticket),
                "approved": conf.approved,
                "wip": wip,
                "flow_agreed": READY_FLOW_AGREED,
            },
        )
        + "\n"
    )
    # 材料は ready で退避されるので、ready の前の今しか拾えない。出力に足すだけで、判定は変えない。
    stdout.write(template_drafts.close_notice(conf, root, found.ticket))


def cancel(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, ticket_id: str, reason: str
) -> int:
    """`doing/` → `done/`。取り消しの時刻と理由を書く。理由が要る。

    処理フロー:
    1. 理由が空なら止める。
    2. `ops_close.find` で承認済みを引き、未承認（`todo/`）と作業中でないものを止める。
    3. 親が作業中（`ops_close.parent_still_busy`）なら止める。
    4. `cancelled_at` と `cancel_reason` を欄に書き、`_move` で `done/` へ動かす。
    """
    if not reason.strip():
        stderr.write("ccnavi: 取り消しには --reason <理由> が必要です。\n")
        return 1
    found = ops_close.find(stderr, root, conf, ticket_id)
    if found is None:
        return 1
    if found.state == ticket_model.TODO:
        stderr.write(
            f"ccnavi: {ticket_id} は未承認の提案（todo/）。取り消しの記録は不要なので、"
            "ファイルを削除すればよい\n"
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

    処理フロー:
    1. `answer` が yes か no か、`reason` が空でないかを見る。
    2. `ops_close.find` で子を引き、項目が定義の定性の項目（`KIND_JUDGE`）かを見る。
    3. 子のワークツリーの HEAD を求める（`ops_stop.worktree_head_sha`）。
    4. 子の判定の記録（`CHILD_RECORD_JUDGE`）を読み、この項目の欄（`hit`・`reason`・`head`・`at`・
       `source`）を置き換えて書き戻す。
    """
    answer = answer.strip().lower()
    if answer not in ("yes", "no"):
        stderr.write("ccnavi: 判定は yes か no\n")
        return 1
    if not reason.strip():
        stderr.write("ccnavi: record-risk には --reason <根拠> を指定してください。\n")
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

    返すのは、閉じたあとに出す行（点と内訳）。

    処理フロー:
    1. 子でないもの（親・単独）と統合の子は測らず、空を返す。
    2. `risk.load_definition` で配点を読む（プロジェクトの `project` のレイヤーを使う）。
    3. 差分の範囲を `gitstate.child_base` で求め、`risk.measure` で測る。
    4. 子の定性の判定（`CHILD_RECORD_JUDGE`）を読み、`risk.evaluate` で点を付ける。
    5. 判定が残っていれば、問いを書き出して止める（None）。
    6. 結果を `CHILD_RECORD_RISK` に書き、点と内訳の行を返す。
    """
    if not found.is_child or found.is_consolidate:
        # 統合の子は実績リスクを測らない（定性の判定も求めない）。
        return []
    worktree = tree.worktree_path(root, found.ticket)
    definition = risk.load_definition(conf, root, _project_of(conf, root, found))
    if definition.fallback:
        stderr.write(f"ccnavi: {definition.fallback}\n")
    # 数えるのは子の仕事の始まり（取り込み直後の親の先頭と子の共通の祖先）から子の先頭まで。
    # 着手の基準点から数えると、衝突を解くために子へ取り込んだ親の、ほかの子の変更が混ざる。
    # 合流の途中なら、子の先頭の代わりに合流する先頭（MERGE_HEAD）まで数える。検査のあとに
    # 子が積んだコミットは合流に載らないので数えない。
    head = _merging_head(root, found) or "HEAD"
    base, why = gitstate.child_base(
        worktree, tree.worktree_path(root, found.parent), child_head=head
    )
    diff, why = risk.measure(worktree, base, head) if base else (None, why)
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
    # record-risk の記録は、この終了が読んだ入力として一覧に載せ、
    # この write-and-push でコミットする。
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
            f"ccnavi: {found.ticket} を閉じる前に、定性のリスク項目の判定が必要です: {names}\n"
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
        lines.append("  実績のリスクが高いので、宣言に関わらずこのフェーズは人間レビューが必要です")
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
    """`doing/` の承認済みチケットに欄を書き、`review/` か `done/` へ動かす。

    処理フロー:
    1. `approval_ops.update_fields` で `fields` を欄に書く。
    2. `state` が `review` なら `approval_ops.to_review`、それ以外は
       `approval_ops.close_copy` で動かす。
    3. 動かした先を言い、`review` のときは `phase.review_next` の文（無ければ既定の文）を、
       子が `done` に入ったときは親の次の一手を案内する。
    """
    failed = approval_ops.update_fields(found.path, fields)
    if failed:
        stderr.write(f"ccnavi: {found.ticket} に欄を保存できませんでした: {failed}\n")
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
    stdout.write(f"OK: {found.ticket} を {place} へ動かした（{said}）\n")
    if state == ticket_model.REVIEW:
        # 依頼の前に「ユーザのレビューを待つ」と言わない。次に誰が何をするかを、
        # 依頼の有無で言い分ける（phase.review_next）。
        moved = replace(found, completed_at=fields.get("completed_at", ""))
        # 誰がいつ done/ へ動かすかは review_next の文が言う。
        stdout.write(
            (
                phase.review_next(root, conf, moved)
                or "まだレビュー準備中。親がレビューを依頼するまで、ユーザには回らない。"
                "依頼の後、ユーザがレビューを終えて confirm / decide / --reviewed か "
                "Chrome 拡張のレビュー済みを実行すると "
                f"{conf.approved}/{ticket_model.DONE}/ へ動く"
            )
            + "\n"
        )
    elif state == ticket_model.DONE and found.is_child and not fields.get("cancelled_at"):
        # 次の一手は status が計算する
        stdout.write(texts.say(root, "NEXT_OPS_CHILD_DONE", {"parent": found.parent}) + "\n")
    return 0


def _create_words(root: str, t: ticket_model.Ticket, worktree: str) -> str:
    """ワークツリーを作る案内。

    子は親のブランチから `-b <子の識別子>` で切る。親は識別子の名前のブランチを作らず、
    親のブランチ（MR の元ブランチ。`/` を `-` に置き換えると識別子になる名前）を
    直接チェックアウトする。
    作るのは `ccnavi-start.sh`（MR の採用の確認と取り込みも行う）。
    """
    if t.is_child:
        where = f"projects/{t.project} の中で " if t.project else ""
        git = settings.script_command(root, "ccnavi-git.sh")
        return f"先に {where}'{git} worktree add \"{worktree}\" -b {t.ticket}' で作ってください"
    start = settings.script_command(root, "ccnavi-start.sh")
    return (
        f"親のワークツリーは '{start} --mr <MR の番号>'"
        "（MR が無ければ '--issue <issue の番号>'）で、"
        "親のブランチを直接チェックアウトして作ってください（識別子の名前のブランチは作らない）"
    )
