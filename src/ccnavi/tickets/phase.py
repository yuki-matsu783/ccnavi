"""フェーズの終わりと HITL ポイント（Human In The Loop。ユーザの手が入るところ）。

ユーザだけが打つコマンドの形の見分けは `phase_forms`（phase を読まない）、子チケットの範囲の
当て方は `phase_scope`（phase を読む）に分けてある。

子チケットのまとまりが終わったときに何をするかと、ユーザを待たずに進もうとしたら止めること。

## フェーズの終わり

同じ親の同じ `phase` の子が `todo/` にも `doing/` にも無く、`done/` に 1 枚以上あるとき、
そのフェーズは終わり。`cancelled/` だけのフェーズは終わりではない（何も成果が無い）。

終わりの扱いは、`done/` の子に `human_review.required: true` が 1 枚でもあるかで分かれる。
あれば HITL ポイントに来たということで、レビュー済みのマーカーが置かれるまで
サブエージェントの起動とシェルを止める。
無ければ省略のマーカーを置いて進ませる。

## 止めるときの鍵は cwd

書き込みは書き込み先のパスで親を決めるが、起動とシェルには書き込み先が無い。止めるかどうかは
呼び出しの `cwd` がどの親のワークツリーにあるかで親を引く。`cd` を 1 回打てば判定から外れるが、
外れた先で起動したサブエージェントの書き込みは書き込み先で止まるので、致命傷にならない。

## 提案から承認済みチケットへコピーするもの

スクリプトが書く欄（着手・完了の時刻と基準点）だけを、提案から承認済みチケットへコピーする。
範囲に触らない欄なので、コピーしても承認の意味は変わらない。提案が `done/` か
`cancelled/` に動いていたら、承認済みチケットを `closed/` へ動かす。
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass, field, replace
from typing import TextIO

from ..infra import settings, tree
from ..policy import rules
from . import (
    approval,
    approval_checks,
    approval_marks,
    history,
    phase_forms,
    phasetypes,
    risk,
    ticket_model,
    workflow,
)

# 止めている間の呼び名。依頼のマーカーが境目で、未依頼はエージェントの番、
# 依頼済みはユーザの番（設計 9.8）。
LABEL_PREPARING = "レビュー準備中"
LABEL_WAITING = "レビュー待ち"

# 返す文の理由コード。
CODE_REVIEW = "DENY_PHASE_REVIEW"
CODE_SUBAGENT = "DENY_SUBAGENT_TICKET_OP"

# git に与える時間。
TIMEOUT_SECONDS = 2.0


@dataclass
class Phase:
    """1 つの親の 1 つのフェーズ。

    親が計画を持てば、番号に定義と計画の項が付く（設計 9.7）。持たなければ
    番号だけで、今までどおり子の `human_review` からレビューの要否を決める。
    """

    parent: str
    number: int
    tickets: list[ticket_model.Ticket] = field(default_factory=list)
    states: dict[str, str] = field(default_factory=dict)
    marks: dict[str, dict] = field(default_factory=dict)
    # 計画があるときだけ。item は計画の項、type は定義、owner は親の承認済みチケット。
    item: ticket_model.PlanItem | None = None
    type: phasetypes.PhaseType | None = None
    owner: ticket_model.Ticket | None = None
    # 子ごとの実績のリスク（閉じるときに数えた記録）。子の識別子 → 記録。
    risks: dict[str, dict] = field(default_factory=dict)
    # 延期を引き受けた前のフェーズが、定義として宣言している「見る場所」。引き受けた側は
    # 厳しい側で見る（`review_kind`）。組むのは `phases_of`。
    covered_reviews: list[str] = field(default_factory=list)

    @property
    def risk(self) -> dict | None:
        """このフェーズの実績のリスク。子の最大値。無ければ None。"""
        best: dict | None = None
        for record in self.risks.values():
            if best is None or int(record.get("points") or 0) > int(best.get("points") or 0):
                best = record
        return best

    @property
    def risk_escalates(self) -> bool:
        """実績のリスクが、宣言に関わらずレビューが要る扱いにするリスクレベルか。"""
        record = self.risk
        return record is not None and str(record.get("level") or "") in risk.ESCALATE_FROM

    @property
    def risk_line(self) -> str:
        """ユーザ向けの 1 行。`リスク: 58 (HIGH) — 行数が多い（…）、…`。無ければ空。"""
        body = self.risk_body
        return f"リスク: {body}" if body else ""

    @property
    def risk_body(self) -> str:
        """`risk_line` から頭の `リスク: ` を除いたもの。見出しを自分で付ける側が使う。"""
        record = self.risk
        if record is None:
            return ""
        hits = [str(h.get("detail") or "") for h in record.get("hits") or [] if isinstance(h, dict)]
        text = f"{record.get('points', 0)} ({record.get('level', '')})"
        return text + (" — " + "、".join(hits) if hits else "")

    @property
    def planned(self) -> bool:
        return self.item is not None

    @property
    def title(self) -> str:
        """ユーザ向けの名前。定義が無ければ番号だけ。"""
        if self.type is not None:
            return self.type.title
        if self.item is not None:
            return self.item.type
        return ""

    @property
    def label(self) -> str:
        return f"{self.number}（{self.title}）" if self.title else str(self.number)

    @property
    def deferred(self) -> bool:
        return self.item is not None and self.item.deferred

    @property
    def review_at(self) -> int | None:
        """このフェーズのレビューが行われる番号。延期なら次にレビューがある番号。"""
        if self.owner is None or not self.planned:
            return self.number
        return self.owner.review_at(self.number)

    @property
    def covers(self) -> list[int]:
        """このフェーズのレビューが含む、延期した前のフェーズの番号。"""
        if self.owner is None or not self.planned:
            return []
        return self.owner.covered_by(self.number)

    @property
    def ended(self) -> bool:
        """終わったか。作業中（`doing/`）の子が無く、レビュー待ちか閉じた子が 1 枚以上。

        取り消しだけのフェーズは終わらない。レビュー待ちは作業としては終わっていて、
        ユーザが見るのを待っている段（設計 9.8）。
        """
        states = list(self.states.values())
        if not states or any(s in (ticket_model.TODO, ticket_model.DOING, "") for s in states):
            return False
        return any(s in (ticket_model.REVIEW, ticket_model.DONE) for s in states)

    @property
    def declared_review(self) -> str | None:
        """このフェーズ定義と計画の項が言う「見る場所」。宣言が無ければ None。

        計画の項は `mr` にだけ強められる（`ticket_model.PLAN_REVIEWS`）ので、項が `mr` なら
        定義より優先する。定義の無い番号（計画が無い、定義のファイルが無い、その名前の
        定義が読めない）は、言っている者が居ないので None。
        """
        if self.item is not None and self.item.review == ticket_model.PLAN_REVIEW_MR:
            return phasetypes.REVIEW_MR
        if self.type is not None:
            return self.type.review
        return None

    @property
    def review_kind(self) -> str:
        """このフェーズの終わりにユーザがどこで見るか。`none` / `chat` / `mr`（設計 9.8）。

        見る場所を言えるのは、定義と計画の項と、引き受けた延期だけ。そのうち厳しい側を
        採る。子の宣言（`human_review.required`）と実績のリスクは「要る」とだけ言い、
        場所は言わないので、宣言が「見ない」だったフェーズを `chat` へ上げるにとどまる。
        どちらも止める向きにしか働かず、宣言された `mr` を `chat` に下げることはない。

        場所を言う者が 1 人も居なければ（計画が無い、定義が読めない）今までどおりで、
        子が「ユーザが見る」と言うか実績が高ければ `mr`。緩い側を採ると、定義のファイルが
        読めないときにレビューの行き先が消える。延期を引き受けている番号は、覆っている分の
        宣言が読めなくても `mr` を受け取る（`_covered_review`）ので、この扱いにはならない。

        延期したフェーズは自分では見る場所を持たず、次に見るフェーズが引き受ける。
        """
        if self.deferred:
            return phasetypes.REVIEW_NONE
        from_children = any(
            t.review_required
            for t in self.tickets
            if self.states.get(t.ticket) in (ticket_model.REVIEW, ticket_model.DONE)
        )
        # 実績のリスクは、宣言を厳しい側にだけ上書きする（risk.py）。
        needed = from_children or self.risk_escalates
        declared = [d for d in [self.declared_review, *self.covered_reviews] if d is not None]
        if not declared:
            return phasetypes.REVIEW_MR if needed else phasetypes.REVIEW_NONE
        where = declared[0]
        for covered in declared[1:]:
            where = phasetypes.stricter(where, covered)
        # 「要る」としか言われていないフェーズは、いちばん手間の少ない見る場所（`chat`）まで
        # 上げる。マージリクエストを勧めるのは文の側の仕事で、強制はしない。
        if needed and where == phasetypes.REVIEW_NONE:
            where = phasetypes.REVIEW_CHAT
        return where

    @property
    def review_required(self) -> bool:
        """このフェーズの終わりにユーザのレビューが要るか。見る場所が `none` でなければ要る。"""
        return self.review_kind != phasetypes.REVIEW_NONE

    @property
    def review_in_chat(self) -> bool:
        """このセッションでユーザが見るフェーズか。ホストへは出ない。"""
        return self.review_kind == phasetypes.REVIEW_CHAT

    @property
    def gate_closed(self) -> bool:
        """レビューが済むまで止めているか。

        欄の名前は JSON の表記（`gate_closed`）に合わせてある。ユーザに見せる名前は
        `review_label` が出す「レビュー準備中」「レビュー待ち」で、この表記は
        判定とボードの間の契約としてだけ残っている（設計 9.8）。
        """
        return (
            self.ended and self.review_required and approval_marks.MARK_REVIEWED not in self.marks
        )

    @property
    def review_waiting(self) -> bool:
        """依頼を出したのにまだ止まっている。ユーザのレビュー待ち。

        ボードはこれをそのまま使うだけで、止まっているかとマーカーから組み直さない。依頼していない
        フェーズは止まっていても待ちではなく（レビュー準備中）、先に親が request を打つ。

        これはマージリクエストの待ちだけを言う。`review: chat` のフェーズは普段 `request` を
        打たないので False のまま。ボードの「受け入れ」（未解決スレッドを受け入れて進む）が
        この欄で出し分けられており、ホストから取得する結果の無い
        chat のフェーズに出すと打てない操作を見せることになる。
        打ったときは（実績のリスクが高いときに勧める向き。設計 9.10）
        ホストから取得する結果があるので、`mr` と同じに True でよい。
        このセッションで見る待ちは `review_kind` と `gate_closed` で読む（設計 9.8）。
        """
        return self.gate_closed and approval_marks.MARK_REQUESTED in self.marks

    @property
    def review_label(self) -> str:
        """止めている間の呼び名。次に動く者で分かれる（設計 9.8）。

        まだ依頼していなければ動くのはエージェント（合流・push・依頼）なので
        「レビュー準備中」、依頼が出ていれば動くのはユーザなので「レビュー待ち」。
        `review: chat` のフェーズは普段この段を持たないので、ユーザが端末で
        `--reviewed --chat` を打つまで「レビュー準備中」のまま。`request` を通した
        ときだけ（設計 9.10）`mr` と同じに「レビュー待ち」へ移る。
        """
        return LABEL_WAITING if self.review_waiting else LABEL_PREPARING


# レイヤーごとの定義の読み込みは phasetypes に置く
# （approval も読むため。approval は phase を読めない）。
types_path = phasetypes.types_path
layer_types = phasetypes.layer_types
load_types = phasetypes.load_types


def _covered_review(phase: Phase | None) -> str:
    """延期を引き受けた側に渡す「覆っている分の見る場所」。読めなければ `mr`（設計 9.8）。"""
    if phase is None or phase.declared_review is None:
        return phasetypes.REVIEW_MR
    return phase.declared_review


def phases_of(
    root: str,
    conf: settings.Settings,
    parent_id: str,
    proposed: ticket_model.Ticket | None = None,
    raw: approval.Raw | None = None,
) -> list[Phase]:
    """この親のフェーズを番号順に。開いている承認済みチケットと閉じた承認済みチケットの両方から組む。

    親が計画を持てば、まだ子の無い番号も並ぶ（計画が言っている番号は全部フェーズ）。

    `proposed` は、承認済みチケットがまだ無いときに計画を読む親。承認で同じときに通った親の
    提案を渡す（`order_problems`）。渡さないと、親と後のフェーズの子を一緒に承認したとき
    計画が読めずフェーズが 1 つも並ばず、順序の検査が何も見ないまま通る。承認済みチケットが
    あればそちらを使う（改版の計画は承認されるまで使われない）。

    `raw` は呼び手が `approval.read_raw` で読んだ置き場。読んでから置き場のファイルを
    動かしていないときだけ渡す。渡せば 3 つの `scan` が置き場を読み直さない。無ければここで読む。
    """
    open_copies, _ = approval.scan(conf, root, raw=raw)
    closed_copies, _ = approval.scan(conf, root, closed=True, raw=raw)
    by_number: dict[int, Phase] = {}
    owner = approval_checks.by_id(open_copies + closed_copies).get(parent_id)
    if owner is None and proposed is not None and proposed.ticket == parent_id:
        owner = proposed
    if owner is not None and owner.has_plan:
        # レイヤーは親の承認済みチケットの `project:` が決める（設計 11.4.1）。
        # ユーザが承認した値で、
        # 子は親から継ぐので、判定が申告に依存する形にはならない。
        types = load_types(conf, root, owner.project) or {}
        if owner.workflow is None:
            # 承認前の提案など、待ち方を入れていない親。承認済みと同じ計算（計画の `after`）で読む。
            owner = replace(owner, workflow=workflow.compute(owner))
        for n, item in owner.numbered():
            by_number[n] = Phase(parent_id, n, item=item, type=types.get(item.type), owner=owner)
        # 延期を引き受けた側に、引き受けた分の「見る場所」を渡す。厳しい側を採るのは
        # `review_kind`。ここで渡さないと、chat の計画に mr の延期が入っているときに
        # 引き受けた側が chat のままになり、宣言した mr が消える。
        #
        # 覆っている分の宣言が読めない番号は `mr` として扱う。延期できるのはレビューのある
        # 定義だけ（承認が確かめる）なので、そこには必ず見る場所を言った者が居た。
        # 読めなくなったことを理由に、その番号のレビューが消えてはいけない。
        for phase in by_number.values():
            phase.covered_reviews = [_covered_review(by_number.get(c)) for c in phase.covers]
    # 状態は置き場そのもの。閉じた（`done/`）、レビュー待ち（`review/`）、
    # 作業中（`doing/`）の順に読み、同じ識別子が 2 つの置き場に在れば閉じた側を採る。
    # 閉じたかどうかを決めるのは承認済みチケットの側で、エージェントが書ける `todo/` に同じ識別子を
    # 書いてもフェーズは開き直らない（そちらは承認待ちにもならない。agree.waiting）。
    review_copies, _ = approval.scan_review(conf, root, raw=raw)
    seen: set[str] = set()
    for pool in (closed_copies, review_copies, open_copies):
        for t in approval_checks.children_of(pool, parent_id):
            if t.phase is None or t.ticket in seen:
                continue
            seen.add(t.ticket)
            phase = by_number.setdefault(t.phase, Phase(parent_id, t.phase))
            phase.tickets.append(t)
            phase.states[t.ticket] = t.state
    # マーカーと記録は親のツリーに置く。子のワークツリーにも承認済みチケットは checkout されるが、
    # マーカーを子の側に書くと、同じフェーズのマーカーが複数のツリーに分かれて置かれる。
    where = approval.home_dir(conf, root, parent_id, "")
    for phase in by_number.values():
        phase.marks = approval_marks.marks(where, parent_id, phase.number)
        for t in phase.tickets:
            record = approval_marks.read_child_record(
                where, parent_id, t.ticket, approval_marks.CHILD_RECORD_RISK
            )
            if record:
                phase.risks[t.ticket] = record
    return [by_number[n] for n in sorted(by_number)]


def held_phase(
    root: str, conf: settings.Settings, parent_id: str, raw: approval.Raw | None = None
) -> Phase | None:
    """レビューが済むまで止めているフェーズ。無ければ None。`raw` は `phases_of` と同じ。"""
    for phase in phases_of(root, conf, parent_id, raw=raw):
        if phase.gate_closed:
            return phase
    return None


def worktree_at(root: str, conf: settings.Settings, cwd: str) -> tree.Tree | None:
    """cwd が入っているワークツリーかプロジェクト。ワークスペースルートか外なら None。

    `parent_at` が置き場を読む前に見る条件。これが None でないときだけ置き場を読み
    （`approval.read_raw`）、`parent_in` に渡す。
    """
    t = tree.tree_of(root, cwd or os.getcwd(), conf.projects)
    if t is None or t.is_main:
        return None
    return t


def parent_in(
    root: str, conf: settings.Settings, here: tree.Tree, raw: approval.Raw | None = None
) -> ticket_model.Ticket | None:
    """ツリー `here`（`worktree_at` の答え）が親のワークツリーなら、その親の承認済みチケット。

    `raw` は `phases_of` と同じ。
    """
    # 承認済みチケットの側を読む。承認は親のワークツリーを作る前にも打てるので、そのときの
    # 承認済みチケットは提案があったツリー（プロジェクトのルート）に在る。
    open_copies, _ = approval.scan(conf, root, raw=raw)
    found = tree.lookup(approval_checks.by_id(open_copies), here.name)
    if found is None or found.is_child:
        return None
    return found


def parent_at(
    root: str, conf: settings.Settings, cwd: str, raw: approval.Raw | None = None
) -> tuple[ticket_model.Ticket | None, approval.Raw | None]:
    """（cwd が親のワークツリーの中ならその親の承認済みチケット, 引くのに使った置き場）。

    置き場を読むのは cwd がワークツリーかプロジェクトの中で、`raw` が None のときだけ。
    読んだ置き場を返すので、呼び手は同じ hook の残りの処理に持ち回れる。
    cwd がワークスペースルートか外なら、`raw` をそのまま返す。
    """
    here = worktree_at(root, conf, cwd)
    if here is None:
        return None, raw
    if raw is None:
        raw = approval.read_raw(conf, root)
    return parent_in(root, conf, here, raw), raw


def parent_for_cwd(root: str, conf: settings.Settings, cwd: str) -> ticket_model.Ticket | None:
    """cwd が親のワークツリーの中なら、その親の承認済みチケット。"""
    return parent_at(root, conf, cwd)[0]


def hold_reason(phase: Phase, tool: str, root: str) -> str:
    """止めたときに返す文。いまどの段にいて、次に何をすればよいかを言う。

    段の名前（レビュー準備中／レビュー待ち）を見出しに置く。止まっている事実だけを
    言っても次に何をすればよいかが分からないので、段ごとにやることを書き分ける（設計 9.8）。
    """
    review_sh = settings.script_command(root, "ccnavi-review.sh")
    what = "サブエージェントの起動" if tool == "Agent" else "このシェル実行"
    n = phase.number
    why = "人間レビューが要る子を含みます" if not phase.planned else "レビューが要るフェーズです"
    if phase.risk_escalates:
        why = f"実績のリスクが高い（{phase.risk_line}）ので、宣言に関わらずレビューが要ります"
    later = "次のフェーズの計画（wip/proposals/todo/ への提案）はレビュー前に進めて構いません。"
    if phase.review_waiting:
        # 依頼は出してある。ここで動くのはユーザで、エージェントは待つほうに回る。
        todo = (
            "やること: ユーザのレビューを待ってください。レビューが終わったら "
            f"'{review_sh} confirm --phase {n}' で確かめます。指摘が付いていたら、"
            f"同じフェーズに子を足してやり直せます。{later}"
        )
    elif phase.review_in_chat:
        todo = (
            "やること: 子の成果を親ブランチへ合流し、ユーザに差分を見てもらって、"
            f"{phase_forms.TURN_DEFINED}を終えてユーザを待ってください。このフェーズはこのセッションで見る計画"
            "（review: chat）なので、マージリクエストは要りません。先へ進めるのは、"
            f"ユーザが親のワークツリーの端末で打つ '{review_sh} chat {n}' です"
            f"（エージェントからは打てません）。{later}"
        )
    else:
        todo = (
            "やること: 子の成果を親ブランチへ合流し、ELI5 の HTML を "
            f"wip/eli5/phase-{n}.html に書いてコミットして push し、"
            f"'{review_sh} request --phase {n} --body-file <依頼文> "
            f"--eli5 wip/eli5/phase-{n}.html' "
            f"でレビューを頼み、{phase_forms.TURN_DEFINED}を終えてユーザを待ってください。"
            f"ユーザがレビューを終えたら '{review_sh} confirm --phase {n}' "
            f"で確かめます。{later}"
        )
    return "\n".join(
        [
            f"[ccnavi] {CODE_REVIEW} (parent: {phase.parent}, phase: {n}, {phase.review_label})",
            f"{phase.parent} のフェーズ {phase.label} は{phase.review_label}です。"
            f"フェーズは終わっていて、{why}。"
            f"レビュー済みのマーカーが置かれるまで、{what}を止めます。",
            todo,
            *([] if tool == "Agent" else [phase_forms.EXEMPT_NOTE]),
        ]
    )


def _type_source(phase: Phase) -> dict:
    """定義を根拠に置くマーカーに足す、その定義のレイヤー（設計 11.9）。

    `review:` が絡むマーカー（省略と保留）にだけ足す。他のマーカーは定義を見ずに置くので、
    レイヤーを書いても根拠にならない。定義の無いフェーズでは欄そのものを置かない。
    """
    return {"source": phase.type.source} if phase.type is not None else {}


def announce(
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    parent: ticket_model.Ticket,
    raw: approval.Raw | None = None,
) -> str:
    """終わったばかりのフェーズについて 1 度だけ言う文。無ければ空文字。

    `raw` は `phases_of` と同じ。ここが書くのはマーカーだけで、置き場のチケットは動かさない。
    """
    texts = []
    where = approval.home_dir(conf, root, parent.ticket, "", project=parent.project)
    phases = phases_of(root, conf, parent.ticket, raw=raw)
    for phase in phases:
        if not phase.ended or phase.marks:
            continue
        n = phase.number
        if phase.deferred:
            at = phase.review_at
            failed = approval_marks.write_mark(
                where, parent.ticket, n, approval_marks.MARK_SKIPPED, {"deferred_to": at}
            )
            if failed:
                stderr.write(f"ccnavi: フェーズのマーカーを書けない: {failed}\n")
            texts.append(
                f"[ccnavi] {parent.ticket} のフェーズ {phase.label} が終わりました。"
                f"レビューは {at} 番目と一緒に見る計画なので、ここでは止めません。"
                + _next_hint(parent, phases, n)
            )
        elif phase.review_required:
            failed = approval_marks.write_mark(
                where,
                parent.ticket,
                n,
                approval_marks.MARK_PENDING,
                {"review": phase.review_kind, **_type_source(phase)},
            )
            if failed:
                stderr.write(f"ccnavi: フェーズのマーカーを書けない: {failed}\n")
            required = [t.ticket for t in phase.tickets if t.review_required]
            covers = (
                f"（{', '.join(str(c) for c in phase.covers)} 番目の分も含めて）"
                if phase.covers
                else ""
            )
            who = f"人間レビュー要の子: {', '.join(required)}。" if required else ""
            if phase.risk_escalates:
                who += "実績のリスクが高いので、宣言に関わらずレビューが要ります"
                who += f"（{phase.risk_line}）。"
            elif phase.risk_line:
                who += f"{phase.risk_line}。"
            hold_note = (
                "指摘があれば同じフェーズに子を足せます。レビュー済みになるまで、"
                f"サブエージェントの起動とシェル実行は止まります。{phase_forms.EXEMPT_NOTE}"
            )
            if phase.review_in_chat:
                # このセッションでユーザが見る。ホストへは出ないので push もしない。
                # 先へ進めるのは端末のユーザで、エージェントには打てない。
                advise = (
                    "実績のリスクが高いので、マージリクエストで見てもらうことを勧めます"
                    "（定義の `review` を mr にするか、ユーザに相談）。それでも chat で"
                    "通すかはユーザが決めます。"
                    if phase.risk_escalates
                    else ""
                )
                texts.append(
                    f"[ccnavi] {parent.ticket} のフェーズ {phase.label} が終わりました"
                    f"（{LABEL_PREPARING}）。{who}"
                    "このフェーズはこのセッションで見る計画（review: chat）です。"
                    "子の成果を親ブランチへ合流し、ユーザに差分を見てもらってください。"
                    f"{advise}"
                    "レビュー"
                    f"{covers}が済んだら、ユーザが親のワークツリーの端末で "
                    f"'{settings.script_command(root, 'ccnavi-review.sh')} chat {n}' を"
                    "打つと先へ進めます"
                    "（この経路はエージェントには打てません）。"
                    f"{phase_forms.TURN_DEFINED}を終えてユーザを待ってください。{hold_note}"
                )
            else:
                texts.append(
                    f"[ccnavi] {parent.ticket} のフェーズ {phase.label} が終わりました"
                    f"（{LABEL_PREPARING}）。{who}"
                    "子の成果を親ブランチへ合流し、ELI5 の HTML を "
                    f"wip/eli5/phase-{n}.html に書いてコミットして push し、"
                    f"'{settings.script_command(root, 'ccnavi-review.sh')} request --phase {n} "
                    f"--body-file <依頼文> --eli5 wip/eli5/phase-{n}.html' "
                    f"でレビュー{covers}を頼み、{phase_forms.TURN_DEFINED}を終えてユーザを待ってください。"
                    f"{hold_note}"
                )
        else:
            failed = approval_marks.write_mark(
                where,
                parent.ticket,
                n,
                approval_marks.MARK_SKIPPED,
                {"tickets": [t.ticket for t in phase.tickets], **_type_source(phase)},
            )
            if failed:
                stderr.write(f"ccnavi: フェーズのマーカーを書けない: {failed}\n")
            risk_note = f"{phase.risk_line}。" if phase.risk_line else ""
            texts.append(
                f"[ccnavi] {parent.ticket} のフェーズ {phase.label} が終わりました。{risk_note}"
                "レビューは不要なので、省略して次へ進めます。省略した事実は記録に残しました。"
                + _next_hint(parent, phases, n)
            )
    return "\n\n".join(texts)


def _next_hint(parent: ticket_model.Ticket, phases: list[Phase], number: int) -> str:
    """次のフェーズの計画を促す 1 文。計画が無ければ空。"""
    if not parent.has_plan:
        return ""
    following = [p for p in phases if p.number > number]
    if not following:
        if parent.feedback is None:
            return (
                "全体計画のフェーズは全部終わりました。レビューが済んだら、次はフィードバック計画です。"
                "親チケットに `feedback:` を足して（対応が無くても `[]` で）改版を提案し、"
                "承認を受けてください。改版の提案では `started_at`・`completed_at`・`base_sha`・"
                "`cancelled_at`・`cancel_reason` は空にします。"
            )
        return "計画のフェーズは全部終わりました。親チケットを閉じられます。"
    # 並行して始められるものを全部挙げる。番号が前でも、まだ子の無い枝は拾う。
    # 待ちが済んでいない番号は案内しない。
    ready = [p for p in phases if not p.tickets and waits_done(parent, phases, p.number)]
    if not ready:
        return "次に始められるフェーズは、待っているフェーズが済むまでありません。"
    names = "、".join(p.label for p in ready)
    hints = "".join(
        f"（{p.label} の案内: {p.type.when}）" for p in ready if p.type is not None and p.type.when
    )
    return f"次に始められるのは {names} です。子チケットを提案して承認を受けてください。{hints}"


def waits_done(parent: ticket_model.Ticket, phases: list[Phase], number: int) -> bool:
    """N 番目が待つフェーズが、全部閉じてレビューが済んでいるか。"""
    by_number = {p.number: p for p in phases}
    for m in workflow.waits_of(parent, number):
        phase = by_number.get(m)
        if phase is None or not phase.ended or not reviewed_or_skipped(phase):
            return False
    return True


def reviewed_or_skipped(phase: Phase) -> bool:
    """このフェーズのレビューが済んでいるか、要らないか。延期は次の番号に委ねる。"""
    if phase.deferred:
        return True
    if not phase.review_required:
        return True
    return approval_marks.MARK_REVIEWED in phase.marks


def review_next(
    root: str,
    conf: settings.Settings,
    child: ticket_model.Ticket,
    raw: approval.Raw | None = None,
) -> str:
    """レビュー待ち（`review/`）の子について、ユーザに回るまでに誰が何をするかの 1 文。

    `finish` の出力と `status` の「次の一手」が使う。子を `review/` に置いただけでは、ユーザには
    回らない。親が合流・ELI5・`request` を済ませて初めて「レビュー待ち」になる（それまでは
    「レビュー準備中」。設計 9.8）。依頼の前に「ユーザのレビューを待つ」と言うと、依頼を打ち忘れる。

    言い分けるのは次のとおり。見るのは子のフェーズのレビューを引き受けるフェーズ（延期なら
    `review_at`）のマーカー。

    - レビュー済み: 済んでいる（ユーザの操作で done/ へ動く）
    - このセッションで見る（`review: chat`）: 親が見せて、ユーザが端末で `chat` を打つ
    - 依頼済みで、この子が依頼より前に閉じた: ユーザのレビューを待つ
    - 依頼済みで、この子が依頼より後に閉じた: 依頼し直しが要る
    - 未依頼: レビュー準備中。子がまだ残っていればそれを言い、前に依頼していた（続きの子を足した
      ときに依頼の記録が外れた）なら、依頼し直しが要ることも言う
    """
    review_sh = settings.script_command(root, "ccnavi-review.sh")
    if not child.is_child or child.phase is None:
        return ""
    phases = phases_of(root, conf, child.parent, raw=raw)
    ph = next((p for p in phases if p.number == child.phase), None)
    if ph is None:
        return ""
    owner = ph
    if ph.deferred and ph.review_at is not None:
        owner = next((p for p in phases if p.number == ph.review_at), ph)
    n = owner.number
    lead = ""
    if owner is not ph:
        lead = f"フェーズ {ph.label} のレビューは {owner.label} と一緒に見る計画。"
    if approval_marks.MARK_REVIEWED in owner.marks:
        return (
            f"{lead}フェーズ {owner.label} はレビュー済み。ユーザの操作（confirm / decide）で "
            f"{conf.approved}/{ticket_model.DONE}/ へ動く"
        )
    if owner.review_in_chat and approval_marks.MARK_REQUESTED not in owner.marks:
        return (
            f"{lead}まだ{LABEL_PREPARING}。このフェーズはこのセッションで見る計画（review: chat）。"
            "親が子の成果を合流してユーザに差分を見てもらい、ユーザが親のワークツリーの端末で "
            f"'{review_sh} chat {n}' を打つまで、先へは進まない"
            f"（打てば {conf.approved}/{ticket_model.DONE}/ へ動く）"
        )
    request = (
        f"'{review_sh} request --phase {n} --body-file <依頼文> --eli5 wip/eli5/phase-{n}.html'"
    )
    requested = owner.marks.get(approval_marks.MARK_REQUESTED)
    if requested is not None:
        if _later(child.completed_at, str(requested.get("at") or "")):
            return (
                f"{lead}フェーズ {owner.label} は依頼済みだが、この子は前回の依頼より後に閉じた。"
                "依頼し直すまで、この子の分はユーザに回らない。親が合流・ELI5 を済ませて push し、"
                f"{request} で依頼し直す"
            ) + _AFTER_REQUEST.format(done=f"{conf.approved}/{ticket_model.DONE}/")
        return (
            f"{lead}フェーズ {owner.label} は依頼済み（{LABEL_WAITING}）。ユーザのレビューを待つ"
            "（ユーザが confirm / decide で done/ へ動かす）"
        )
    if not ph.ended or not owner.ended:
        left = "このフェーズ" if owner is ph else f"フェーズ {owner.label}"
        text = (
            f"{lead}まだ{LABEL_PREPARING}。{left}にはまだ閉じていない子がある。全部閉じてから、"
            f"親が合流・ELI5・{request} を済ませるまで、ユーザには回らない"
        )
    else:
        text = (
            f"{lead}まだ{LABEL_PREPARING}。親が合流・ELI5・{request} を済ませるまで、"
            "ユーザには回らない"
        )
    if _was_requested(conf, root, child.parent, n):
        text += (
            f"。フェーズ {owner.label} は前に依頼したが、続きの子を足したときに依頼の記録が外れた。"
            "前回の依頼より後に閉じた子があるので、依頼し直しが要る"
        )
    return text + _AFTER_REQUEST.format(done=f"{conf.approved}/{ticket_model.DONE}/")


# 依頼の前の文の結び。依頼の後に誰が done/ へ動かすか
# （依頼済み・レビュー済みの文はそれぞれ言っている）。
_AFTER_REQUEST = "。依頼の後、ユーザがレビューを終えて confirm / decide を打つと {done} へ動く"


def _later(a: str, b: str) -> bool:
    """時刻 `a` が `b` より後か。どちらかが読めなければ False（依頼し直しを言わない側）。"""
    try:
        return datetime.datetime.fromisoformat(a) > datetime.datetime.fromisoformat(b)
    except (TypeError, ValueError):
        return False


def _was_requested(conf: settings.Settings, root: str, parent: str, number: int) -> bool:
    """このフェーズの依頼の記録が、続きの子を足したときに消されたことがあるか（親の状態の履歴）。"""
    where = approval.home_dir(conf, root, parent, "")
    entries, _ = history.read(where, parent, limit=0)
    for entry in entries:
        if (
            entry.get("kind") == history.KIND_PHASE_REOPENED
            and entry.get("phase") == number
            and approval_marks.MARK_REQUESTED in (entry.get("cleared") or [])
        ):
            return True
    return False


def resumed_review(
    root: str,
    conf: settings.Settings,
    child: ticket_model.Ticket,
    raw: approval.Raw | None = None,
) -> str:
    """作業中（`doing/`）の子のフェーズに、レビュー済みのマーカーが残っている形の警告文。無ければ空。

    運用の基本は、閉じたチケットを戻さず新しいチケットを作り直すこと。ユーザが `done/` から
    `doing/` へ手で戻す再開は想定しておらず、されるとそのフェーズの `reviewed` が残って、
    もう一度 `finish` しても止まらず、レビューの告知も出ない。機構はマーカーを消さない
    （消すかはレビューをやり直すかどうかで、ユーザが決める）。判定も `start` も止めず、
    `--lint` と `status` がこの文を warn で言う。

    延期したフェーズの子は、引き受けた側（`review_at`）の `reviewed` が残っているときに言う
    （消すのはその引き受けた側のマーカー。延期の `skipped` は消さない）。引き受けた側がまだ
    レビュー前なら、これからのレビューが再開後の作業も覆うので言わない。
    閉じ直したときにレビューが要らないフェーズ（定義が `review: none` で、この子も
    `human_review` を求めていない）と、レビューが要らない引き受け手は、マーカーが残っていても
    止める条件に入らないので言わない。
    引き受け手 M は、ゲートと同じく計画から計算した待ち方で引く `review_at`（`N.skipped` の
    `deferred_to` ではない。書いたあとに改版で引き受け手が動けば、skipped は古くなる）。
    文には子の識別子を入れない（呼び手が前に付ける）。
    """
    if child.state != ticket_model.DOING or not child.is_child or child.phase is None:
        return ""
    phases = phases_of(root, conf, child.parent, raw=raw)
    for ph in phases:
        if ph.number != child.phase:
            continue
        if ph.deferred:
            # 延期したフェーズは、引き受けた側 M のレビューが覆う。M が済んでいれば、再開後の作業は
            # そのレビューに含まれない。M がまだなら、これからのレビューが再開後の作業も見る。
            owner = next((p for p in phases if p.number == ph.review_at), None)
            if owner is None or owner is ph or not owner.review_required:
                return ""
            if approval_marks.MARK_REVIEWED not in owner.marks:
                return ""
            lead = (
                f"作業中（doing/）ですが、このフェーズ {ph.label} は {ph.number} から "
                f"{owner.number} へ延期されていて、フェーズ {owner.label} の reviewed マーカーが"
                "残っています。再開後の作業は、そのレビューに含まれていません。"
            )
            return lead + _erase_steps(root, conf, child, owner.number)
        # `review_kind` は子の要求（`human_review`）を `review/` と `done/` の子からしか数えない。
        # 再開された子は `doing/` なので、閉じ直したときに要る分（この子の要求）も足して見る。
        # ここだけで足し、`review_kind` は変えない（ゲートと告知とボードの判定を動かさないため）。
        if not (ph.review_required or child.review_required):
            return ""
        if approval_marks.MARK_REVIEWED not in ph.marks:
            return ""
        lead = f"作業中（doing/）ですが、フェーズ {ph.label} の reviewed マーカーが残っています。"
        return lead + _erase_steps(root, conf, child, ph.number)
    return ""


def _erase_steps(
    root: str, conf: settings.Settings, child: ticket_model.Ticket, number: int
) -> str:
    """`number` 番の reviewed マーカーを消す手順と、残してよい場合の言い添え。"""
    mark = "/".join(
        (
            conf.approved or settings.DEFAULT_APPROVED,
            approval_marks.PHASES_DIR,
            child.parent,
            f"{number}.{approval_marks.MARK_REVIEWED}",
        )
    )
    git = settings.script_command(root, "ccnavi-git.sh")
    push = settings.script_command(root, "ccnavi-push-approved.sh")
    return (
        "レビューをやり直すならマーカーを消してください"
        f"（消し方: ユーザが{_marker_tree(root, conf, child)}で '{git} rm {mark}' を打ち、"
        "削除をコミットする。取り込み済みで origin があり、chat だけでない親子なら "
        f"'{push} {child.parent}' が取り込んでから送る。それ以外は何もしないので、"
        "ユーザが自分でコミットする）。"
        "続きの作業だけなら、そのままで構いません。"
        "運用の基本は、新しいチケットを作り直すことです"
    )


def _marker_tree(root: str, conf: settings.Settings, child: ticket_model.Ticket) -> str:
    """マーカーを置くツリー（`approval.home_dir` が決める）を、ルートからの相対で言う。

    `phases_of` がマーカーを読むときと同じ引数（project なし）で引く。

    置き場の設定が絶対パスなどで、ツリーのルートを引けないときは「マーカーがあるツリー」。
    """
    where = approval.home_dir(conf, root, child.parent, "")
    rel = (conf.approved or settings.DEFAULT_APPROVED).replace("/", os.sep)
    suffix = os.sep + rel
    if not where.endswith(suffix):
        return "マーカーがあるツリー"
    tree_root = where[: -len(suffix)]
    shown = os.path.relpath(tree_root, root).replace(os.sep, "/")
    if shown == ".":
        return "ワークスペースルート"
    if shown.startswith(".."):
        return f"ツリー {tree_root}"
    return f"ツリー {shown}"


def order_problems(
    root: str,
    conf: settings.Settings,
    child: ticket_model.Ticket,
    parent: ticket_model.Ticket,
    types: dict[str, phasetypes.PhaseType] | None,
    adding: list[ticket_model.Ticket] | None = None,
    raw: approval.Raw | None = None,
) -> list[rules.Problem]:
    """N 番目の子を承認してよいか。待つフェーズが閉じてレビューが済んでいるか（設計 9.7）。

    待つ番号は親の待ち方（`workflow`。計画の項の `after` を推移的に辿ったもの）が決める。
    親の計画が壊れていれば（`workflow.errors`）、壊れた待ちで順序を数えずに落とす。

    ここで出す苦情は `rules.KIND_NOT_YET`。承認は落とすが、書いた側に直すものは無く、
    前のフェーズが閉じれば同じ提案がそのまま通る。全体を見る `--lint` はこの定義を見て
    warn にする（`lint_ticket._approval_problems`）。

    `adding` は同じ承認で先に通った、同じ親の子。承認されればそのフェーズには開いた子が
    増え、マーカーも消える（`_apply` の `clear_marks`）。ディスクの上では閉じていても、開いた
    フェーズとして読む。読まないと、前のフェーズに足す子と、そのフェーズが済んだ前提の
    次の子が一緒に承認され、1 本ずつ承認したときに落ちるものが、まとめて承認すると通る。

    `raw` は `phases_of` と同じ。
    """
    if not parent.has_plan or child.phase is None:
        return []
    mine = parent.item_at(child.phase)
    if mine is None:
        return []
    broken = workflow.errors(parent)
    if broken:
        return [
            rules.Problem(
                rules.SEVERITY_ERROR,
                child.ticket,
                f"親 {parent.ticket} の計画が壊れているので、順序を数えない（{broken[0].detail}）",
            )
        ]
    waits = set(workflow.waits_of(parent, child.phase))
    reopened: dict[int, list[str]] = {}
    for t in adding or []:
        if t.phase is not None:
            reopened.setdefault(t.phase, []).append(t.ticket)
    # 同じ承認でフィードバック計画を出しているなら、全体計画の最後のレビューはその承認で
    # 済む（settle_last_review）。承認の前にマーカーは無いので、ここでは計画の側から読む。
    settled = len(parent.plan) if parent.feedback is not None else 0
    problems: list[rules.Problem] = []
    for phase in phases_of(root, conf, parent.ticket, parent, raw=raw):
        if phase.number >= child.phase:
            break
        if phase.number not in waits:
            continue
        ended = phase.ended and phase.number not in reopened
        if phase.number == settled and ended:
            continue
        if not ended:
            if phase.number in reopened:
                names = ", ".join(reopened[phase.number])
                state = (
                    f"同じ承認で {names} を足すので開き直る"
                    if phase.ended
                    else f"同じ承認で {names} を足すが、まだ閉じていない"
                )
            else:
                state = "子がまだ無い" if not phase.tickets else "子が開いている"
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    child.ticket,
                    f"{child.phase} 番目の子は、{phase.label} が閉じるまで承認しない（{state}）。"
                    "作業が終わるまで次の計画は立てない",
                    rules.KIND_NOT_YET,
                )
            )
            break
        if not reviewed_or_skipped(phase):
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    child.ticket,
                    f"{child.phase} 番目の子は、{phase.label} のレビューが済むまで承認しない",
                    rules.KIND_NOT_YET,
                )
            )
            break
    return problems


def plan_finished(root: str, conf: settings.Settings, parent: ticket_model.Ticket) -> bool:
    """全体計画のフェーズが全部閉じ、最後のレビューを頼んであるか。フィードバック計画を出せる条件。

    レビューが「通った」ことは求めない。差し戻し（未解決の指摘）を受けたあとに出すのが
    フィードバック計画なので、通っていないのが普通。求めるのは、最後のフェーズまで閉じて
    レビューを依頼したこと。その依頼へのユーザの応えを見て、親が計画を書く。
    """
    if not parent.has_plan:
        return False
    last = len(parent.plan)
    for phase in phases_of(root, conf, parent.ticket):
        if phase.number > last:
            break
        if not phase.ended:
            return False
        if phase.number == last:
            asked = {
                approval_marks.MARK_REQUESTED,
                approval_marks.MARK_REVIEWED,
                approval_marks.MARK_SKIPPED,
            }
            return reviewed_or_skipped(phase) or bool(asked & set(phase.marks))
        if not reviewed_or_skipped(phase):
            return False
    return False


def review_venues(root: str, conf: settings.Settings, parent_id: str) -> dict[int, str]:
    """この親のフェーズ番号 → ユーザがどこで見るか（`none` / `chat` / `mr`）。"""
    return {p.number: p.review_kind for p in phases_of(root, conf, parent_id)}


def chat_only(
    root: str, conf: settings.Settings, parent_id: str, venues: dict[int, str] | None = None
) -> bool:
    """マージリクエストに出さない進め方か。フェーズが 1 つも無ければ False。

    1 つでも `mr` で見るフェーズがあれば、その親にはマージリクエストが在る（レビューの依頼が作る）
    ので、早めに閉じたあとも Draft を外す手順を通る。`venues` は数え直しを省くために呼ぶ側が渡す値。
    """
    if venues is None:
        venues = review_venues(root, conf, parent_id)
    return bool(venues) and phasetypes.REVIEW_MR not in venues.values()


def settle_last_review(approved_dir: str, parent: ticket_model.Ticket, stamp: str) -> str:
    """フィードバック計画の承認で、全体計画の最後のレビューを済んだ扱いにする。

    ユーザがレビューの結果を見たうえで対応を計画したので、その計画の承認がレビューの
    合意になる。残った指摘は消えない。フィードバック作業フェーズの `confirm` が、
    解決されていない指摘を全部数える。
    """
    if not parent.has_plan:
        return ""
    last = len(parent.plan)
    if (
        approval_marks.read_mark(approved_dir, parent.ticket, last, approval_marks.MARK_REVIEWED)
        is not None
    ):
        return ""
    return approval_marks.write_mark(
        approved_dir,
        parent.ticket,
        last,
        approval_marks.MARK_REVIEWED,
        {"by": "feedback-plan", "at": stamp, "accepted": []},
    )


def stage(
    root: str,
    conf: settings.Settings,
    parent: ticket_model.Ticket,
    raw: approval.Raw | None = None,
) -> str:
    """親がいまどの局面にいるか（設計 9.7）。計画が無ければ空文字。`raw` は `phases_of` と同じ。"""
    if not parent.has_plan:
        return ""
    phases = phases_of(root, conf, parent.ticket, raw=raw)
    held_all = [p for p in phases if p.gate_closed]
    if held_all:
        # 並行した枝が同時に止まっていることがある。呼び名ごとに番号を並べる。
        groups: dict[str, list[str]] = {}
        for p in held_all:
            groups.setdefault(p.review_label, []).append(p.label)
        return "、".join(f"{label}（{'、'.join(names)}）" for label, names in groups.items())
    if approval_marks.read_parent_mark(
        approval.home_dir(conf, root, parent.ticket, "", project=parent.project),
        parent.ticket,
        approval_marks.PARENT_MARK_CLOSE_EARLY,
    ):
        # ユーザが早めに閉じた。残りは別の issue に書き出してあるので、閉じられる。
        return "閉じられる（ユーザが早めに閉じた）"
    in_feedback = parent.feedback is not None and len(parent.feedback) > 0
    open_phases = [p for p in phases if not p.ended]
    if open_phases and open_phases[0].number <= len(parent.plan):
        # 子がまだ無く、待ちが済んでいないフェーズは作業中に数えない。
        working = [
            p
            for p in open_phases
            if p.number <= len(parent.plan) and (p.tickets or waits_done(parent, phases, p.number))
        ]
        return f"作業中（{'、'.join(p.label for p in working or open_phases[:1])}）"
    for phase in open_phases:
        where = "フィードバック対応中" if phase.number > len(parent.plan) else "作業中"
        return f"{where}（{phase.label}）"
    if parent.feedback is None:
        return "フィードバック計画待ち"
    return "クローズ可" if not in_feedback else "クローズ可（フィードバック対応済み）"
