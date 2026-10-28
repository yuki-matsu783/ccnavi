"""判定のコアを通すレビュー済み（`review confirm`）の判定と手元の入口。

`confirm` が検査と書くものの並べ方、`confirm_local` が手元の入力を読んで書く入口。
`reviewable` / `requested_head` / `moved_detail_on_host` は Chrome のボードが引く。
`decide_preview` と `decide` は、残った指摘の行き先を決める操作（`--reviewed` の決め方）のうち、
書くもの（Changes）とホストへの書き込みの計画（host_plan）を並べる部分で、Chrome が引く。
決め方の部品（見せる材料・ダイジェスト・転記）は review_decide のものをそのまま使う。
親を閉じる操作（review_close）は review を読む末端で、コアは読まない。

"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import TextIO

from ..infra import fsio, settings, tree
from . import core_base
from .approval import checks as approval_checks
from .approval import marks as approval_marks
from .approval import ops as approval_ops
from .approval import store as approval
from .lifecycle import close as ops_close
from .lifecycle import commands as ops
from .model import oplog, review_host, ticket_model, ticket_places, via
from .phase import gates as phase
from .phase import manual_settle
from .proposal import agree
from .review import decide as review_decide
from .review import request as review

# レビュー済み（`confirm`）が通らなかった根拠の種別。付録 A の `CORE_CONFIRM_*`。
# 親の承認済みチケットが作業中に無い。
CODE_CONFIRM_NO_PARENT = "CORE_CONFIRM_NO_PARENT"
# 親にそのフェーズの子が無い。
CODE_CONFIRM_NO_PHASE = "CORE_CONFIRM_NO_PHASE"
# そのフェーズはもうレビュー済み。
CODE_CONFIRM_ALREADY_REVIEWED = "CORE_CONFIRM_ALREADY_REVIEWED"
# 依頼の記録が無い。
CODE_CONFIRM_NOT_REQUESTED = "CORE_CONFIRM_NOT_REQUESTED"
# 依頼の後に、ユーザが見るものが動いた。
CODE_CONFIRM_MOVED = "CORE_CONFIRM_MOVED"
# 取得した結果が、依頼したマージリクエストのものと照合できない。
CODE_CONFIRM_RESULT_MISMATCH = "CORE_CONFIRM_RESULT_MISMATCH"
# 変更要求のレビューか、未解決のスレッドが残っている。
CODE_CONFIRM_REVIEW_OPEN = "CORE_CONFIRM_REVIEW_OPEN"


def confirm(
    snapshot: core_base.Snapshot,
    parent_id: str,
    phase_no: int,
    result,
    changed_since_request: str,
    manual: manual_settle.Manual | None = None,
    moved: review.Moved | None = None,
) -> core_base.Checked:
    """レビュー済みにしてよいかを見て、通れば書くもの（子を `done/` へ、
    レビュー済みのマーカー）を並べる。

    `result` はホストから取得した結果（`review_host.Result`、`ccnavi-review.sh` が組む形）。
    読めなかったときの扱い（`--result` が無い、読めない）は読む側（手元は `confirm_local`）が持つ。
    `changed_since_request` は依頼の後にユーザが見るものが動いたかの説明（空なら動いていない）。
    手元は git の差分、Chrome は compare API から作る。
    `moved` は動いた理由の種類と（手元だけ）コミットの一覧。止めたあとの説明にだけ使い、判定は
    `changed_since_request` で決まる。

    処理フロー:
    1. 親の承認済みチケットと、フェーズの子を引く（`_open_parent`・`review._phase`）。無ければ止める
    2. レビュー済みでないこと、依頼の記録が在ること、依頼のあとに見たものが動いていないことを見る
       （`approval_marks.MARK_REVIEWED`・`MARK_REQUESTED`・`changed_since_request`）
    3. 結果が依頼の記録と合うかを見る（`review.matching_problems`）
    4. 手で動かした子なら `_manual_refusal` を通し、未解決の指摘は版つきで受け入れ台帳に足す
       （`review.unresolved_for`）。そうでなければ指摘の有無を見る（`review.review_problems`）
    5. 通ったら、レビュー済みのマーカーと子の移動をステージに積む（`review.settle_and_mark`）。
       書き込みはここでは行わず、呼び手が `core_base.Changes` を使って書く
    """
    conf, root = snapshot.conf, snapshot.root
    parent = _open_parent(root, conf, parent_id)
    if parent is None:
        return core_base.refused(
            CODE_CONFIRM_NO_PARENT,
            [f"ccnavi: 親 {parent_id} の承認済みチケットが作業中にありません。"],
        )
    # フェーズの引き方と「依頼し直す」案内の文面は review の非公開の関数を使う（手元の
    # confirm と同じ文面にするため。review の中だけの約束なので公開名にはしない）。
    ph = review._phase(root, conf, parent, phase_no)
    if ph is None:
        return core_base.refused(
            CODE_CONFIRM_NO_PHASE,
            [f"ccnavi: {parent.ticket} にフェーズ {phase_no} の子がありません。"],
        )
    if approval_marks.MARK_REVIEWED in ph.marks:
        # 重ね打ちでマーカーを書き直さない（`_already_requested` と同じ文面の形）
        return core_base.refused(
            CODE_CONFIRM_ALREADY_REVIEWED, [f"ccnavi: フェーズ {phase_no} はレビュー済み"]
        )
    requested_mark = ph.marks.get(approval_marks.MARK_REQUESTED)
    if requested_mark is None:
        return core_base.refused(
            CODE_CONFIRM_NOT_REQUESTED,
            ["ccnavi: 依頼の記録がありません。先に request してください"],
        )
    if changed_since_request:
        return core_base.refused(
            CODE_CONFIRM_MOVED,
            [
                f"ccnavi: {changed_since_request}。ユーザが見たものと今の HEAD が違う。"
                f"{review._redo_request(root, phase_no)}",
                *(review.moved_explanation(moved) if moved is not None else []),
            ],
        )
    problems = review.matching_problems(result, requested_mark)
    if problems:
        return core_base.refused(CODE_CONFIRM_RESULT_MISMATCH, problems)
    mark_extra: dict = {}
    ledger: list[dict] = []
    if manual is not None and manual.state == manual_settle.READY:
        # 手で動かした子の確定。未解決の指摘は解決せず、版つきで受け入れ台帳に足す。
        refused = _manual_refusal(root, conf, parent, phase_no, result, manual)
        if refused is not None:
            return refused
        threads = review.unresolved_for(root, conf, parent, phase_no, result)
        ledger = [_ledger_entry(t, phase_no) for t in threads]
        mark_extra = {"via": "manual", "moved": list(manual.children)}
    else:
        problems = review.review_problems(root, conf, parent, phase_no, result)
        if problems:
            return core_base.refused(CODE_CONFIRM_REVIEW_OPEN, problems)
    stamp = snapshot.stamp or fsio.stamp()
    # 経路は Snapshot の経路（Chrome なら chrome）。
    actor = snapshot.actor
    with (
        fsio.staging() as stage,
        fsio.clock(stamp),
        via.session(actor.via or via.via(), actor.account),
    ):
        stopped = review.settle_and_mark(
            stage,
            root,
            conf,
            parent,
            ph,
            {**core_base.reviewed_mark(result.mr.number, [], snapshot.actor), **mark_extra},
            ledger,
        )
    return core_base.Checked([], core_base.Changes(agree.Planned(stage, stopped), root, conf))


def _manual_refusal(root, conf, parent, phase_no, result, manual) -> core_base.Checked | None:
    """手動移動の確定を止める理由。通れば None。

    変更要求のレビューがあれば保留（移動は残す）。受け入れるのは、移動のコミットの時刻より余裕幅
    （`manual_settle.MARGIN_SECONDS`）以上前にホストで作られたスレッドだけで、後に付いた指摘や、時刻が
    読めないスレッドが残っていれば確定しない（その分は decide へ）。
    """
    if review.changes_requested(result):
        return core_base.refused(
            CODE_CONFIRM_REVIEW_OPEN,
            [
                f"ccnavi: {review_host.changes_requested_note(result.host)}。"
                "手で動かした移動は保持するが、変更要求があるためレビュー済みにはできません（保留）"
            ],
        )
    late = []
    for t in review.unresolved_for(root, conf, parent, phase_no, result):
        # 最初のコメントだけでなく最後のコメントの時刻も見る（移動の後に付いた返信を受け入れない）
        made = max(review_host.epoch(t.created_at), review_host.epoch(t.last_comment_at))
        if (
            review_host.epoch(t.created_at) <= 0
            or made > manual.moved_at - manual_settle.MARGIN_SECONDS
        ):
            late.append(t)
    if not late:
        return None
    return core_base.refused(
        CODE_CONFIRM_REVIEW_OPEN,
        [
            f"ccnavi: 移動の後に付いた指摘（または時刻を読めない指摘）が {len(late)} 件残っていて、"
            "手で動かした移動を確定しません。decide で行き先を決めてください",
            *[f"  - {review.thread_label(t)}" for t in late],
        ],
    )


def _ledger_entry(t, phase_no: int) -> dict:
    """受け入れ台帳の項目（版つき。受け入れたときの最後のコメントを覚える）。"""
    return {
        "key": review_host.thread_key(t),
        "version": review_host.thread_version(t),
        "last_comment_id": t.last_comment_id,
        "accepted_by": "manual",
        "phase": phase_no,
    }


# `confirm_local` は前の `review.confirm` を移したもの。手元の入力の読み方（cwd の親、git の
# 差分、`--result` で取得した結果）は review の非公開の関数をそのまま使う。
# review だけが持つ読み方で、外に出す値打ちが無いので公開名にはしない。
def confirm_local(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    phase_no: int,
    result_path: str,
    actor: str = "",
) -> int:
    """依頼の後を見る。通ればマーカーを置いて先へ進めるようになる。

    検査と書くものの並べ方はコア（`confirm`）。ここは手元の入力
    （cwd の親、git の差分、`--result` で取得した結果）を読んで渡し、
    並べたものを書く（Writer(FS)）。Chrome の「レビュー済み」も同じコアを通る。
    前は review.py にあった（コアより下の段に置くと review → core → review の循環になる）。

    `actor` は `ccnavi-review.sh` がトークンの持ち主を引いて `--actor` で渡すアカウント。
    あればマーカーに `actor` と `via: cli` を書く。空（引けなかった）
    ならマーカーは前と同じ。
    """
    found = review._parent_phase(stderr, root, conf, cwd, phase_no)
    if found is None:
        return 1
    parent, ph = found
    requested_mark = ph.marks.get(approval_marks.MARK_REQUESTED)
    moved = review.Moved()
    result = None
    # 読む順は前と同じ。依頼の記録が無ければ差分も取得した結果も見ない。
    # 動いていれば取得した結果を読まない。レビュー済みなら差分も取得した結果も読まない
    # （コアが「レビュー済み」で止める。Chrome と同じ文面）
    if requested_mark is not None and approval_marks.MARK_REVIEWED not in ph.marks:
        moved = review._moved_detail(
            tree.worktree_path(root, parent.ticket), conf, requested_mark, bool(parent.project)
        )
        if not moved.text:
            result = review._result(stderr, result_path)
            if result is None:
                return 1
    who = core_base.Actor(actor, via.VIA_CLI) if actor else core_base.Actor()
    manual = manual_settle.assess(root, conf, parent, ph) if result is not None else None
    for line in _manual_notes(manual):
        stdout.write(line + "\n")
    checked = confirm(
        core_base.read_fs(conf, root, actor=who),
        parent.ticket,
        phase_no,
        result,
        moved.text,
        manual,
        moved,
    )
    for line in checked.problems:
        stderr.write(line + "\n")
    if checked.changes is None:
        return 1
    code = core_base.write_fs(stdout, stderr, checked.changes.planned).code
    if code == 0:
        after = after_last_review(root, conf, parent.ticket)
        if after:
            stdout.write(after + "\n")
    return code


def manual_for(
    snapshot: core_base.Snapshot, parent_id: str, phase_no: int, moved_at: float | None = None
) -> manual_settle.Manual | None:
    """git を持たない呼び手（Chrome）向けの、手で動かした子の判定。親かフェーズが無ければ None。

    移動の時刻は呼び手がホストの履歴から読んで渡す。`moved_at` が None のうちは `need_time` が真で、
    `paths`（動かした子の done/ のファイル）の時刻を読んでから渡し直す。
    """
    parent = _open_parent(snapshot.root, snapshot.conf, parent_id)
    if parent is None:
        return None
    ph = review._phase(snapshot.root, snapshot.conf, parent, phase_no)
    if ph is None:
        return None
    return manual_settle.assess(
        snapshot.root, snapshot.conf, parent, ph, external=True, moved_at=moved_at
    )


def _manual_notes(manual: manual_settle.Manual | None) -> list[str]:
    """手で動かした移動が、確定できない形のときの知らせ（確定できるときは出さない）。"""
    if manual is None or manual.state in (manual_settle.NONE, manual_settle.READY):
        return []
    line = manual.line()
    return [f"ccnavi: {line}"] if line else []


def after_last_review(root: str, conf: settings.Settings, parent_id: str) -> str:
    """最後のレビューが済んだときに、親を閉じてから Draft を外すまでの流れ。まだ先があれば空。

    「最後」は、親を閉じられる状態（`ops_close.close_problems` が空）になったこと。
    `confirm` の知らせが「次のフェーズへ進める」だけだと、最後のフェーズのあとに何をするか
    （親の finish、push、ready）が、親の finish の出力で初めて出てくる。
    """
    if ops_close.close_problems(root, conf, parent_id):
        return ""
    ticket_sh = settings.script_command(root, "ccnavi-ticket.sh")
    return "\n".join(
        [
            f"全部のフェーズのレビューが済み、親 {parent_id} を閉じられる。"
            "ここから Draft を解除するまでの流れ:",
            f"  1. '{ticket_sh} finish {parent_id}' で親を閉じる"
            "（取り込み済みの親子なら、finish が移動をコミットして push する。"
            "そうでなければ、移動をコミットして push する）。"
            "残っている子のワークツリーも片付く",
            f"  2. 親のワークツリーの外（ワークスペースルートなど）に出て、"
            f"'{ops.ready_command(root, parent_id)}' で Draft を解除する。"
            "ready はクローズ済みのチケットの記録と"
            f" `{ticket_places.WIP_ROOT}/` の追跡済みのファイルを削除してコミットし、push してから"
            " Draft を解除し、最後に親のワークツリーを削除します"
            "（中から --parent なしで実行すると消さず、外に出てから実行するコマンドを表示する）",
            "  マージはユーザが squash で行う",
            f"  {ops.READY_FLOW_AGREED}",
        ]
    )


def reviewable(snapshot: core_base.Snapshot, parent_id: str) -> list[dict]:
    """依頼済みで、まだレビュー済みでないフェーズ。Chrome のボードが出す候補。

    並べるだけ（通るかは `confirm` がホストから取得した結果で決める）。
    答えは `{phase, mr, host, children}` のリスト。`mr` と `host` は依頼のマーカーの値。
    """
    parent = _open_parent(snapshot.root, snapshot.conf, parent_id)
    if parent is None:
        return []
    out = []
    for ph in phase.phases_of(snapshot.root, snapshot.conf, parent.ticket):
        mark = ph.marks.get(approval_marks.MARK_REQUESTED)
        if mark is None or approval_marks.MARK_REVIEWED in ph.marks:
            continue
        try:
            mr = int(mark.get("mr") or 0)
        except (TypeError, ValueError):
            mr = 0
        out.append(
            {
                "phase": ph.number,
                "mr": mr,
                "host": str(mark.get("host") or ""),
                "children": [t.ticket for t in ph.tickets],
            }
        )
    return out


def _requested_mark(snapshot: core_base.Snapshot, parent_id: str, phase_no: int) -> dict | None:
    """依頼のマーカー。親・フェーズ・依頼の記録のどれかが無ければ None。"""
    parent = _open_parent(snapshot.root, snapshot.conf, parent_id)
    ph = review._phase(snapshot.root, snapshot.conf, parent, phase_no) if parent else None
    return ph.marks.get(approval_marks.MARK_REQUESTED) if ph is not None else None


def requested_head(snapshot: core_base.Snapshot, parent_id: str, phase_no: int) -> str | None:
    """依頼のマーカーに記録された親の先頭。依頼の記録（か親・フェーズ）が無ければ None。

    Chrome は依頼の後に親のブランチが動いたかを compare API で確かめる。比べる相手は
    TS に読ませず、ここが出す。
    """
    mark = _requested_mark(snapshot, parent_id, phase_no)
    return None if mark is None else str(mark.get("head") or "")


def moved_on_host(
    snapshot: core_base.Snapshot,
    parent_id: str,
    phase_no: int,
    head: str,
    changed: list[str] | None,
) -> str:
    """`moved_detail_on_host` の判定の文だけ（空なら動いていない）。"""
    return moved_detail_on_host(snapshot, parent_id, phase_no, head, changed).text


def moved_detail_on_host(
    snapshot: core_base.Snapshot,
    parent_id: str,
    phase_no: int,
    head: str,
    changed: list[str] | None,
) -> review.Moved:
    """ホストで読んだ親の先頭 `head` と変更の一覧から、依頼の後にユーザが見るものが動いたかを言う。

    手元の confirm と同じ関数（`review.moved_since`）で決める。依頼の記録が無ければ空を返し、
    `confirm` が「依頼の記録が無い」で止める。
    """
    mark = _requested_mark(snapshot, parent_id, phase_no)
    # Chrome は祖先かどうかを compare API の一覧に任せる（読めなければ changed が None）。
    # コミットの一覧は手元の git が無いので出さない
    return (
        review.Moved() if mark is None else review.moved_detail(snapshot.conf, mark, head, changed)
    )


# ---- decide（残った指摘の行き先を決める） ---------------------------------------------------

# decide が通らなかった根拠の種別。付録 A の `CORE_DECIDE_*`。
# 見せた指摘のダイジェストが無い・読めない。
CODE_DECIDE_NO_DIGEST = "CORE_DECIDE_NO_DIGEST"
# 見せた指摘と今の指摘が違う。
CODE_DECIDE_MISMATCH = "CORE_DECIDE_MISMATCH"
# 選択（か受け入れ済みのキー）の形が、見せた指摘と合わない。
CODE_DECIDE_CHOICES = "CORE_DECIDE_CHOICES"
# 返信と解決をするスレッドの id が無いか、API の経路に使えない。
CODE_DECIDE_THREAD_ID = "CORE_DECIDE_THREAD_ID"
# 続きの子を起こせない。
CODE_DECIDE_FOLLOWUP = "CORE_DECIDE_FOLLOWUP"
# 書くものを並べる途中で止まった（何も積まない）。
CODE_DECIDE_STOPPED = "CORE_DECIDE_STOPPED"

# 返信の本文の、issue の URL を入れる枠。issue を作った呼び手が置き換える。
ISSUE_URL_SLOT = "{issue_url}"
# 決めた内容のコメントの、作った issue の一覧を入れる枠。issue に回す指摘があるときだけ入る。
ISSUE_LINKS_SLOT = "{issue_links}"
# 1 回の apply で issue にできる指摘の上限（ホストへの書き込みを 1 回の操作に収める）。
MAX_ISSUES = 40
# issue のタイトルの字数の上限（GitHub は 256 字）。
_ISSUE_TITLE_LIMIT = 200


@dataclass(frozen=True)
class Accepted:
    """ホストで受け入れる（返信して解決する）指摘。

    返信と解決の計画はこの型からだけ組む。`fix`（このフェーズで直す）は受け入れないので、
    作れない。直す指摘に返信も解決も付かないことを、型で固める。
    """

    key: str
    thread_id: str
    choice: str

    def __post_init__(self) -> None:
        if self.choice not in (review_decide.CHOICE_KEEP, review_decide.CHOICE_ISSUE):
            raise ValueError(f"受け入れられない行き先: {self.choice}")


@dataclass
class Decided(core_base.Checked):
    """`decide` の答え。通らなければ `changes` と `host_plan` は None で、`code` に種別が入る。

    `host_plan` はホストへの書き込みの計画（JSON にできる辞書）。形は `decide` の docstring。
    `reviewed` はレビュー済みのマーカーを置くか、`followup` は起こす続きの子の識別子（無ければ空）、
    `pending_extra` は見せたあとに増えた指摘のキー（今回は扱わず、未対応として残す）、
    `mismatch` は見せた指摘と今の指摘が違うときの中身。
    """

    host_plan: dict | None = None
    reviewed: bool = False
    followup: str = ""
    pending_extra: list[str] = field(default_factory=list)
    mismatch: dict | None = None


@dataclass
class DecidePreview:
    """`decide_preview` の答え。通らなければ `preview` は None。

    `preview` の形は `review_decide.decision_json`（ボードの `DecidePreview` と同じ）。
    """

    problems: list[str]
    code: str = ""
    preview: dict | None = None


def _decision(
    snapshot: core_base.Snapshot,
    parent_id: str,
    phase_no: int,
    result,
    changed_since_request: str,
    moved: review.Moved | None,
    carry: frozenset[str],
) -> review_decide.Decision | core_base.Checked:
    """残った指摘を決められる状態かを見て、見せる材料を組む。決められなければ止める答え。

    止める条件と並びは `confirm` と同じ（親・フェーズ・レビュー済み・依頼の記録・動いたか・
    結果の照合）に、変更要求のレビューを足したもの。未解決のスレッドが残っていても止めない
    （それを決めるのが decide）。
    `carry` は受け入れ済み（ホストで解決済み）のキー。解決済みでも、見せた指摘の一部として数える。
    """
    conf, root = snapshot.conf, snapshot.root
    parent = _open_parent(root, conf, parent_id)
    if parent is None:
        return core_base.refused(
            CODE_CONFIRM_NO_PARENT,
            [f"ccnavi: 親 {parent_id} の承認済みチケットが作業中にありません。"],
        )
    ph = review._phase(root, conf, parent, phase_no)
    if ph is None:
        return core_base.refused(
            CODE_CONFIRM_NO_PHASE,
            [f"ccnavi: {parent.ticket} にフェーズ {phase_no} の子がありません。"],
        )
    if approval_marks.MARK_REVIEWED in ph.marks:
        return core_base.refused(
            CODE_CONFIRM_ALREADY_REVIEWED, [f"ccnavi: フェーズ {phase_no} はレビュー済み"]
        )
    requested_mark = ph.marks.get(approval_marks.MARK_REQUESTED)
    if requested_mark is None:
        return core_base.refused(
            CODE_CONFIRM_NOT_REQUESTED,
            ["ccnavi: 依頼の記録がありません。先に request してください"],
        )
    if changed_since_request:
        return core_base.refused(
            CODE_CONFIRM_MOVED,
            [
                f"ccnavi: {changed_since_request}。ユーザが見たものと今の HEAD が違う。"
                f"{review._redo_request(root, phase_no)}",
                *(review.moved_explanation(moved) if moved is not None else []),
            ],
        )
    problems = review.matching_problems(result, requested_mark)
    if problems:
        return core_base.refused(CODE_CONFIRM_RESULT_MISMATCH, problems)
    if review.changes_requested(result):
        # decide でも通せない（文面は手元の decide と同じ）
        return core_base.refused(
            CODE_CONFIRM_REVIEW_OPEN, review.review_problems(root, conf, parent, phase_no, result)
        )
    open_now = {id(t) for t in review.unresolved_for(root, conf, parent, phase_no, result)}
    unresolved = [
        t for t in result.threads if id(t) in open_now or review_host.thread_key(t) in carry
    ]
    return review_decide.Decision(parent, ph, result, unresolved, True)


def decide_preview(
    snapshot: core_base.Snapshot,
    parent_id: str,
    phase_no: int,
    result,
    changed_since_request: str,
    moved: review.Moved | None = None,
    carry: frozenset[str] = frozenset(),
) -> DecidePreview:
    """見せる指摘とダイジェストを返す（何も積まない）。

    形とダイジェストは手元の `--reviewed N --accept-unresolved --preview --json` と同じ。
    止める条件は `decide` と同じ。
    """
    found = _decision(snapshot, parent_id, phase_no, result, changed_since_request, moved, carry)
    if isinstance(found, core_base.Checked):
        return DecidePreview(found.problems, found.code)
    return DecidePreview([], "", review_decide.decision_json(found))


def _refused_decided(code: str, problems: list[str], mismatch: dict | None = None) -> Decided:
    out = core_base.refused(code, problems)
    return Decided(out.problems, None, out.code, mismatch=mismatch)


def decide(
    snapshot: core_base.Snapshot,
    parent_id: str,
    phase_no: int,
    result,
    changed_since_request: str,
    moved: review.Moved | None = None,
    choices: dict[str, str] | None = None,
    shown_digest: str | None = None,
    carry: frozenset[str] = frozenset(),
) -> Decided:
    """選んだ行き先を、書くもの（Changes）とホストへの書き込みの計画（host_plan）に並べる。

    手元の `apply_decision` と同じ意味で、書き込みは行わない。ホストにも触れない（実行ファイルは
    ネットワークに出ない）。呼び手が `host_plan` をホストに書いてから、`changes` を親のブランチへ
    1 コミットにする。

    - `choices`: 見せた指摘のキー → `keep`（対応しない）/ `fix`（このフェーズで直す）/
      `issue`（issue に回す）。見せた指摘の全部に選択が要る
    - `shown_digest`: `decide_preview` が返したダイジェスト。無い・読めない・今の指摘と違うなら
      何も積まない。見せたあとに増えた指摘は食い違いに数えず、未対応として残す（`pending_extra`）
    - `carry`: ホストで受け入れ済み（返信して解決済み）と確かめたキー。見せた指摘から外れていても
      食い違いに数えない。結果ではホストで解決済みでなければならず、`fix` のキーは入れられない。
      `host_plan` からは外れる（もう書かない）

    `host_plan` は JSON にできる辞書で、次の形をとる。

        {"issues":   [{"key", "op", "title", "body", "comments": [...]}],
         "replies":  [{"key", "thread_id", "body"}],
         "resolves": [{"key", "thread_id"}],
         "comment":  "<決めた内容のコメント>"}

    issue は `issue` を選んだ指摘 1 件につき 1 つ。`op` は親・フェーズ・キー・指摘の版から決まる
    操作ID（本文の頭の隠しコメント `MARKER_ISSUE` にも入る）。`comments` は本文が長いときの
    続きのコメント。
    `replies` と `resolves` は `keep` と `issue` だけ。`issue` の返信の本文は `ISSUE_URL_SLOT`
    を含み、同じキーの issue を作った URL に置き換える。`comment` の `ISSUE_LINKS_SLOT` は作った
    issue の URL の一覧に置き換える（issue に回す指摘が無ければ枠は入らない）。

    続きの子（`fix`）を起こすときは、親のブランチ上の既存のチケットだけを見て識別子を決める
    （手元の退避は見ない）。直す指摘が無く、追加分も無ければレビュー済みのマーカーを置く。
    続きの子に書く操作ID は、親・フェーズ・直す指摘のキーと版から決まる（MR の番号は入らない）。

    決まりは次のとおり。
    - `carry` の確認は「取り直した結果で、今ホストで解決済み」だけを見る。ccnavi の受け入れ返信が
      付いているかは見ない。issue に回した指摘の `carry` は、呼び手が 1 回目の結果（作った issue の
      URL）を持って渡す前提で、2 回目の `host_plan` はその指摘の issue を作り直さない
    - 1 回の呼び出しで issue に回せる指摘は `MAX_ISSUES` 件まで。超えたら何も積まずに止める
    - レビュー済みになった（`reviewed`）あとは、同じフェーズを `CORE_CONFIRM_ALREADY_REVIEWED` で
      断る。コミットが成功して MR へのコメントだけが失敗したときの再送は、この入口では受けない
      （コメントの再送は呼び手が独立に扱う）
    - 直す指摘が無く追加分があるときは、`changes` が空（書くものが無い）になる。呼び手は空の
      コミットを作らない
    """
    found = _decision(snapshot, parent_id, phase_no, result, changed_since_request, moved, carry)
    if isinstance(found, core_base.Checked):
        return Decided(found.problems, None, found.code)
    d = found
    want = review_decide.parse_digest((shown_digest or "").strip())
    if want is None:
        return _refused_decided(
            CODE_DECIDE_NO_DIGEST,
            [
                "ccnavi: 見せた指摘のダイジェストが無いか、読めません。指摘を見せ直してください。"
                "何も積んでいません"
            ],
        )
    base, versions = want
    if not isinstance(choices, dict) or not all(
        isinstance(k, str) and v in review_decide.CHOICES for k, v in choices.items()
    ):
        return _refused_decided(
            CODE_DECIDE_CHOICES,
            [f"ccnavi: 選択は {{キー: {' / '.join(review_decide.CHOICES)}}} の形で渡してください"],
        )
    # 受け入れ済みのキーは、ホストの結果で解決済みでなければならない（取り直した実状態で確かめる）
    by_key = {review_host.thread_key(t): t for t in result.threads}
    for key in sorted(carry):
        thread = by_key.get(key)
        if thread is None or not thread.resolved or choices.get(key) == review_decide.CHOICE_FIX:
            return _refused_decided(
                CODE_DECIDE_CHOICES,
                [f"ccnavi: 受け入れ済みとされたスレッド {key} が、ホストで解決済みになっていない"],
            )
    current = review_decide.decision_digest(d)
    differs = review_decide.changed_since_shown(d, base, versions)
    if differs:
        return _refused_decided(
            CODE_DECIDE_MISMATCH,
            ["ccnavi: 見せた指摘と今の指摘が違います。何も積んでいません"],
            {"digest": {"expected": shown_digest, "current": current}, "changed": differs},
        )
    shown = set(versions)
    d.extra = [t for t in d.unresolved if review_host.thread_key(t) not in shown and not t.resolved]
    d.unresolved = [t for t in d.unresolved if review_host.thread_key(t) in shown]
    if set(choices) != shown:
        return _refused_decided(
            CODE_DECIDE_CHOICES,
            ["ccnavi: 選択が見せた指摘と揃っていません（足りないか、余分があります）"],
        )
    picked = {
        c: [t for t in d.unresolved if choices.get(review_host.thread_key(t)) == c]
        for c in review_decide.CHOICES
    }
    if len(picked[review_decide.CHOICE_ISSUE]) > MAX_ISSUES:
        return _refused_decided(
            CODE_DECIDE_CHOICES,
            [
                f"ccnavi: issue に回す指摘が {len(picked[review_decide.CHOICE_ISSUE])} 件あります。"
                f"1 回で回せるのは {MAX_ISSUES} 件まで。選び直してください。何も積んでいません"
            ],
        )
    fix = picked[review_decide.CHOICE_FIX]
    accepted_threads = picked[review_decide.CHOICE_KEEP] + picked[review_decide.CHOICE_ISSUE]
    to_write = [t for t in accepted_threads if review_host.thread_key(t) not in carry]
    problem = review_host.accept_plan_problem(to_write)
    if problem:
        return _refused_decided(
            CODE_DECIDE_THREAD_ID, [f"ccnavi: 受け入れを始められません: {problem}"]
        )
    fix_op = oplog.id_for(
        "chrome-decide-fix",
        d.parent.ticket,
        str(d.ph.number),
        *sorted(f"{review_host.thread_key(t)}:{review_host.thread_version(t)}" for t in fix),
    )
    items = review_decide.transcribe(d, fix, fix_op)
    children = [t for t in d.ph.tickets if d.ph.states.get(t.ticket) in ticket_model.FINISHED]
    if fix:
        problem = approval_ops.followup_problem(
            snapshot.conf,
            snapshot.root,
            d.parent,
            d.ph.number,
            children or list(d.ph.tickets),
            review_decide.transcribe(d, fix),
        )
        if problem:
            return _refused_decided(
                CODE_DECIDE_FOLLOWUP, [f"ccnavi: 続きの子チケットを起こせない: {problem}"]
            )
    stamp = snapshot.stamp or fsio.stamp()
    actor = snapshot.actor
    with (
        fsio.staging() as stage,
        fsio.clock(stamp),
        via.session(actor.via or via.via(), actor.account),
    ):
        try:
            stopped, followup = _stage_decision(
                stage, snapshot, d, picked, children, items, fix_op, actor
            )
        except approval.NoParentWorktree as exc:
            stopped, followup = ("", f"親のワークツリーが決まらない（{exc}）"), ""
    if stopped is not None:
        return _refused_decided(CODE_DECIDE_STOPPED, [f"ccnavi: {stopped[1]}"])
    accepted = [
        Accepted(review_host.thread_key(t), t.id, choices[review_host.thread_key(t)])
        for t in to_write
    ]
    return Decided(
        [],
        core_base.Changes(agree.Planned(stage, None), snapshot.root, snapshot.conf),
        "",
        _host_plan(d, accepted, picked, followup),
        not followup and not d.extra,
        followup,
        [review_host.thread_key(t) for t in d.extra],
    )


def _stage_decision(
    stage,
    snapshot: core_base.Snapshot,
    d: review_decide.Decision,
    picked: dict[str, list[review_host.Thread]],
    children: list[ticket_model.Ticket],
    items: list[str],
    fix_op: str,
    actor: core_base.Actor,
) -> tuple[tuple[str, str] | None, str]:
    """親のブランチへ書くもの（子を `done/` へ、続きの子、レビュー済みのマーカー）を並べる。

    答えは（止まったら（"", 理由）、起こした続きの子の識別子）。順は手元の `apply_decision` と同じ。
    直す指摘があるか、見せたあとに増えた指摘が無いときだけ、レビュー待ちの子を動かす。
    直す指摘があれば続きの子を起こし（マーカーは置かない。フェーズは再び開く）、無くて追加分も
    無ければレビュー済みのマーカーを置く。
    """
    conf, root, parent, ph = snapshot.conf, snapshot.root, d.parent, d.ph
    fix = picked[review_decide.CHOICE_FIX]
    accepted = [
        review_host.thread_key(t)
        for t in picked[review_decide.CHOICE_KEEP] + picked[review_decide.CHOICE_ISSUE]
    ]
    followup = ""
    with fsio.policy(
        on_fail=fsio.FAIL_STOP, ticket="", message="{reason}", prefix="", undo=(), places=""
    ):
        if fix or not d.extra:
            by = "chrome" if via.via() == via.VIA_CHROME else "decide"
            moved, failed = approval_ops.settle_review(
                conf, root, parent.ticket, [*ph.covers, ph.number], by=by
            )
            if failed:
                return ("", failed), ""
            if moved:
                stage.line(
                    f"レビュー待ちの子を {conf.approved}/{ticket_model.DONE}/ へ動かした: "
                    f"{', '.join(moved)}"
                )
        if fix:
            followup, failed = approval_ops.followup(
                conf, root, parent, ph.number, children, items, io.StringIO(), fix_op
            )
            if failed:
                return ("", f"続きの子チケットを起こせない: {failed}"), ""
            stage.line(
                f"続きの子チケット {followup} を {conf.approved}/{ticket_model.DOING}/ に起こした"
                f"（フェーズ {ph.number}、範囲は見た子の和、本文に指摘 {len(items)} 件）。"
                "フェーズは再び開かれ、マーカーは消えた。"
            )
        elif not d.extra:
            try:
                home = approval.home_dir(conf, root, parent.ticket, "", project=parent.project)
            except approval.NoParentWorktree as exc:
                return ("", f"マーカーを置く親のワークツリーが決まらない（{exc}）"), ""
            assert d.result.mr is not None
            with fsio.policy(prefix="マーカーを置けない: "):
                failed = approval_marks.write_mark(
                    home,
                    parent.ticket,
                    ph.number,
                    approval_marks.MARK_REVIEWED,
                    core_base.reviewed_mark(d.result.mr.number, accepted, actor),
                )
            if failed:
                return ("", f"マーカーを置けない: {failed}"), ""
            stage.line(f"OK: フェーズ {ph.number} はレビュー済み。先へ進める")
    return None, followup


def _issue_title(d: review_decide.Decision, head: str, t: review_host.Thread) -> str:
    """指摘 1 件の issue のタイトル。出どころのタイトルに、指摘の位置（無ければキー）を添える。"""
    where = ""
    if t.path:
        where = f"{t.path}:{t.line}" if t.line else t.path
    inner = review_decide.span(where or review_host.thread_key(t))[1:-1]
    room = max(0, _ISSUE_TITLE_LIMIT - len(head) - 4)
    return f"{head}: `{inner[:room]}`"


def _issue_plan(d: review_decide.Decision, t: review_host.Thread) -> dict:
    """指摘 1 件の issue の計画。本文の頭に、操作ID の隠しコメントを入れる。"""
    key = review_host.thread_key(t)
    op = oplog.id_for(
        "chrome-decide-issue", d.parent.ticket, str(d.ph.number), key, review_host.thread_version(t)
    )
    main, parts = review_decide.issue_texts(d, [t], op)
    head, _, rest = main.partition("\n")
    return {
        "key": key,
        "op": op,
        "title": _issue_title(d, head, t),
        "body": f"{review_host.MARKER_ISSUE.format(op=op)}\n\n{rest.lstrip(chr(10))}",
        "comments": parts,
    }


def _host_plan(
    d: review_decide.Decision,
    accepted: list[Accepted],
    picked: dict[str, list[review_host.Thread]],
    followup: str,
) -> dict:
    """ホストへの書き込みの計画。返信と解決は `accepted`（受け入れる指摘）からだけ組む。

    順は issue の作成、返信、解決。`accepted` は、ホストにまだ書いていない分
    （受け入れ済みのキーを除いた分）。issue は、`issue` を選んだうちその分だけ作る。
    """
    why = f"フェーズ {d.ph.number} の未解決の指摘を、ユーザが decide で決めた"
    by_key = {review_host.thread_key(t): t for t in d.unresolved}
    issues = [
        _issue_plan(d, by_key[a.key]) for a in accepted if a.choice == review_decide.CHOICE_ISSUE
    ]
    replies = [
        {
            "key": a.key,
            "thread_id": a.thread_id,
            "body": review_host.accept_reply(
                why,
                issue=a.choice == review_decide.CHOICE_ISSUE,
                issue_url=ISSUE_URL_SLOT if a.choice == review_decide.CHOICE_ISSUE else "",
            ),
        }
        for a in accepted
    ]
    resolves = [{"key": a.key, "thread_id": a.thread_id} for a in accepted]
    comment = review_decide.decide_comment_text(d, picked, followup)
    if issues:
        comment += f"\nissue に回した分:\n{ISSUE_LINKS_SLOT}\n"
    return {"issues": issues, "replies": replies, "resolves": resolves, "comment": comment}


def _open_parent(root: str, conf: settings.Settings, parent_id: str) -> ticket_model.Ticket | None:
    """作業中の親の承認済みチケット（正とする側）。`phase.parent_for_cwd` と同じ引き方。"""
    open_copies, _ = approval.scan(conf, root)
    found = tree.lookup(approval_checks.by_id(open_copies), parent_id)
    if found is None or found.is_child:
        return None
    return found
