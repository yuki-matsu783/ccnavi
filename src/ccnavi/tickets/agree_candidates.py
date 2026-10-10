"""承認の候補と、その検査。提案を候補に組み、計画・改版・欄・ブランチの食い違いを並べる。

agree から分けた。agree を読まない。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..infra import gitstate, settings, tree
from ..policy import rules
from . import (
    approval,
    approval_checks,
    approval_marks,
    approval_ops,
    phase,
    phasetypes,
    syncstate,
    ticket_ids,
    ticket_model,
    workflow,
)
from . import ticket as ticket_mod


@dataclass
class Candidate:
    """承認の対象の 1 件。新規の提案か、親の改版か。

    リスクの点はここに無い。点は子を閉じるときに実績（差分）で数え、宣言の広さでは
    数えない（risk.py）。宣言の広さは、親が `human_review.reason` で言う。
    """

    ticket: ticket_model.Ticket
    complaints: list[rules.Problem] = field(default_factory=list)
    # 範囲の超過（親の範囲・定義の上限を超えた項、regex の項）。承認は止めず、判定が
    # 切り詰める。判定に影響する（止まる）ので、判定に影響しない記述の注意（complaints の warn）
    # とは分けて持つ。
    overflow: list[rules.Problem] = field(default_factory=list)
    # 改版なら、いま使われている承認済みチケット。
    current: ticket_model.Ticket | None = None
    # このチケットに使うフェーズ定義。親の写し（`phases:`）を読んだもの。親ならその提案の写し、
    # 子なら親（承認済みか、同じ承認で通る提案）の写し。承認画面はこれで題と見る場所を出す。
    types: dict | None = None
    # 承認したときの `phases.yml`（親の `project:` が指すレイヤーの 1 本、設計 11.4.1）。写しが
    # これと同じかを確かめ、承認画面に「この親では使わない定義」を出すのに使う。
    config: dict | None = None
    # 承認画面に足す 1 行ずつの注記（フィードバック計画の証跡など）。
    notes: list[str] = field(default_factory=list)
    # 親の `branch:` が既にあるブランチ（手元か origin、または提案がそのブランチの上）を指すか
    # 。承認画面に「既存のブランチ <名前> を使う」と出す。
    existing_branch: bool = False

    @property
    def is_revision(self) -> bool:
        return self.current is not None

    @property
    def plans_feedback(self) -> bool:
        return (
            self.is_revision
            and self.current is not None
            and self.current.feedback is None
            and self.ticket.feedback is not None
        )


def candidates(
    root: str,
    conf: settings.Settings,
    pending: list[ticket_model.Ticket],
    revisions: list[ticket_model.Ticket],
    approved: list[ticket_model.Ticket],
) -> tuple[list[Candidate], list[tuple[ticket_model.Ticket, list[rules.Problem]]], dict]:
    """承認の対象に入れるものと、落とすものに分ける。3 つめは親子を引くための対応表。

    承認（`agree`）・見せる（`preview`）・確かめる（`verify`）に加えて、`--lint` も
    ここを通る。承認で落ちるものを数える経路が 2 本あると、片方が気づかないうちに弱くなる
    （実際に `--lint` は `validate` だけを当てていて、順序で落ちる子に何も言わなかった）。
    """
    open_index = approval_checks.by_id(approved)
    # 親子を引く対応表は、承認済みチケットと、今回の承認で通ったものだけ。落ちた親を対応表に残すと、
    # 承認されない親の範囲で子が検証され、親の承認を経ずに子の承認済みチケットができる。
    # pending は親が子より前に並ぶ（並べ替えの鍵が親の識別子）ので、子が引くときには
    # 親の通過が決まっている。
    pool = approval_checks.by_id(approved)
    batch: list[Candidate] = []
    rejected: list[tuple[ticket_model.Ticket, list[rules.Problem]]] = []
    # レイヤーごとの `phases.yml` の読み込みは 1 プロジェクト 1 回。承認の対象に同じレイヤーの
    # チケットが何件あっても、ファイルを読むのはそのレイヤーにつき 1 度で足りる。読むのは写しを
    # 確かめるためで、子の定義は親の写しから引く（`phases.yml` からは引かない）。
    cache: dict[str, dict | None] = {}
    # 先行を引く対応表。先行を書いた子が居るときだけ、最初の 1 回で組む。
    preds: dict[str, list[ticket_model.Ticket]] | None = None
    # 親子のチケットの立ち位置と統合先の取り込み結果。
    # 1 回の承認で 1 度ずつだけ読む。
    fams = syncstate.Families(conf, root)

    def config_for(t: ticket_model.Ticket) -> dict | None:
        name = project_of(t, pool)
        if name not in cache:
            cache[name] = phase.load_types(conf, root, name)
        return cache[name]

    for t in sorted(revisions, key=lambda x: x.ticket):
        current = open_index[t.ticket]
        config = config_for(t)
        # 提案の待ち方と写しの定義も承認済みと同じ関数で入れる（計画の項の `after` と `phases:`）。
        t.workflow = workflow.compute(t)
        types = phasetypes.types_of(t)
        complaints = _workflow_field(t) + script_field_problems(t) + record_field_problems(t)
        complaints += revision_problems(root, conf, t, current, config)
        complaints += approval_checks.family_problems(conf, root, t, fams)
        if any(p.severity == rules.SEVERITY_ERROR for p in complaints):
            rejected.append((t, complaints))
            continue
        cand = Candidate(
            ticket=t, complaints=complaints, current=current, types=types, config=config
        )
        if cand.plans_feedback:
            cand.notes = feedback_notes(root, conf, t)
        batch.append(cand)
        # 通った改版だけ、一緒に承認する子から見える親にする。落ちた改版の計画で子を
        # 通すと、承認されない番号の子が承認済みチケットになる。
        pool[t.ticket] = t

    # 今回の承認で通った子を親ごとに。後に続く子の順序の検査が、そのフェーズを
    # 開き直したものとして読む。
    # 子はフェーズの番号の順に並べる。識別子の順だと、後のフェーズの子（-02）が前のフェーズに
    # 足す子（-03）より先に検査され、開き直す前のマーカーで通ってしまう。
    added: dict[str, list[ticket_model.Ticket]] = {}
    for t in sorted(
        pending, key=lambda x: (x.parent or x.ticket, x.is_child, x.phase or 0, x.ticket)
    ):
        config = config_for(t)
        if not t.is_child and t.has_plan:
            t.workflow = workflow.compute(t)
        types = types_from(t, pool)
        complaints, overflow = validate(t, pool, config)
        complaints += _workflow_field(t) + script_field_problems(t) + record_field_problems(t)
        complaints += approval_checks.project_problems(t, pool, conf)
        complaints += approval_checks.family_problems(conf, root, t, fams)
        complaints += approval_checks.integration_problems(conf, root, t, fams)
        complaints += approval_checks.branch_problems(
            conf, root, t, fams, list(approved) + list(pending) + list(revisions)
        )
        if t.is_child and not any(p.severity == rules.SEVERITY_ERROR for p in complaints):
            parent = pool.get(t.parent)
            if parent is not None:
                complaints += phase.order_problems(
                    root, conf, t, parent, types, added.get(t.parent)
                )
        if t.is_child and t.predecessors:
            # 先行は `done/` に在って取り消しでないことを求める。同じ承認で通る
            # 先行も、まだ `todo/` に在るので満たさない。
            if preds is None:
                preds = approval_ops.predecessor_pool(conf, root)
            complaints += approval_checks.predecessor_problems(t, preds, conf.approved)
        if any(p.severity == rules.SEVERITY_ERROR for p in complaints):
            rejected.append((t, complaints))
            continue
        batch.append(
            Candidate(
                ticket=t,
                complaints=complaints,
                overflow=overflow,
                types=types,
                config=config,
                existing_branch=bool(t.branch) and branch_exists(root, conf, t),
            )
        )
        pool[t.ticket] = t
        if t.is_child:
            added.setdefault(t.parent, []).append(t)
    return batch, rejected, pool


def _workflow_field(t: ticket_model.Ticket) -> list[rules.Problem]:
    """提案に待ち方の欄（`workflow:`）が書いてあれば拒む。欄は読まないので、書いてあると
    効くと読み手に思わせる。待ち方は計画の項の `after` から計算する。"""
    if ticket_model.WORKFLOW_KEY not in t.raw:
        return []
    return [
        rules.Problem(
            rules.SEVERITY_ERROR,
            t.ticket,
            f"`{ticket_model.WORKFLOW_KEY}` の欄は提案に書かない（読まない）。"
            "待ち方は計画の項の `after` から決まる",
        )
    ]


def script_field_problems(t: ticket_model.Ticket) -> list[rules.Problem]:
    """提案にスクリプトだけが書く欄（`ticket_model.SCRIPT_FIELDS`）の空でない値があれば拒む。

    承認は提案の中身を変えずに動かすので、提案に書いた値はそのまま承認済みチケットの値になる。
    `review/` の 2 つめの保護（`completed_at`）や着手の基準点（`base_sha`）は、これらの欄を
    スクリプトだけが書くことを前提にしている。
    """
    found = [
        name
        for name in ticket_model.SCRIPT_FIELDS
        if t.raw.get(name) not in (None, "") and str(t.raw.get(name)).strip()
    ]
    if not found:
        return []
    names = ", ".join(f"`{name}`" for name in found)
    return [
        rules.Problem(
            rules.SEVERITY_ERROR,
            t.ticket,
            f"{names} はスクリプト（ccnavi-ticket.sh）だけが書く欄。"
            "提案には値を書かない（空にするか消す）。改版の提案では、承認済みチケットを写したときに"
            "入る started_at・completed_at・base_sha・cancelled_at・cancel_reason を空にする"
            "（改版は承認済みチケットの側の値を残し、計画だけを差し替える）",
        )
    ]


def record_field_problems(t: ticket_model.Ticket) -> list[rules.Problem]:
    """提案に承認の記録（`ccnavi_approved`）か続きの子の目印（`followup_of`）があれば拒む。

    承認は中身を変えないので、提案に書いた欄はそのまま承認済みチケットに入る。前の版の承認の
    記録を持つ古い形は取り下げを特別に扱う（子は記録の欄で決め、親は取り下げない）。続きの子の
    目印は取り下げを止める。どちらもユーザの判断（承認・レビューの行き先）だけが残すもので、
    提案に書かせると古い形や続きの子を装える。
    """
    found = [name for name in (ticket_model.APPROVAL_KEY, approval.FOLLOWUP_KEY) if name in t.raw]
    if not found:
        return []
    names = ", ".join(f"`{name}`" for name in found)
    return [
        rules.Problem(
            rules.SEVERITY_ERROR,
            t.ticket,
            f"{names} は承認の記録で、ユーザの承認（と続きの子を起こすレビューの行き先）だけが"
            "残すもの。提案には書かない（改版の提案で承認済みチケットを写したときも消す）",
        )
    ]


def types_from(t: ticket_model.Ticket, pool: dict[str, ticket_model.Ticket]) -> dict:
    """このチケットに使う定義。親ならその写し、子なら親の写し（親が引けなければ空）。

    親は承認済みならその承認済みチケット、同じ承認で通る提案ならその提案（`pool` に入っている）。
    `phases.yml` からは引かない。承認のあとに `phases.yml` から定義を消す・名前を変えても、
    進行中の親の子の承認が落ちないように。
    """
    if t.is_child:
        parent = pool.get(t.parent)
        return phasetypes.types_of(parent) if parent is not None else {}
    return phasetypes.types_of(t)


def project_of(t: ticket_model.Ticket, pool: dict[str, ticket_model.Ticket]) -> str:
    """このチケットのレイヤーを決める `project:`（設計 11.4.1）。

    子は親と同じ置き場に並ぶので、定義を引くには親のプロジェクトを使う。食い違えば
    `project_problems` が落とす。親が対応表に居ないときだけ、子の置き場の値をそのまま読む。
    """
    if t.is_child:
        parent = pool.get(t.parent)
        if parent is not None:
            return parent.project
    return t.project


def feedback_notes(root: str, conf: settings.Settings, parent: ticket_model.Ticket) -> list[str]:
    """フィードバック計画の承認に添える証跡。何を見たうえでの合意かを残す。"""
    accepted = approval_marks.accepted_threads(
        approval.home_dir(conf, root, parent.ticket, "", project=parent.project), parent.ticket
    )
    notes = [f"受け入れ済みの未解決スレッド: {len(accepted)} 件"]
    if not parent.feedback:
        notes.append("対応なし。見たうえで対応しない、という記録になる")
        if accepted:
            notes.append("別に追うものは issue に回したか（ccnavi-review.sh decide）")
    return notes


def plan_problems(
    t: ticket_model.Ticket,
    config: dict | None,
    held: dict[str, phasetypes.PhaseType] | None = None,
    approved: bool = False,
) -> list[rules.Problem]:
    """親の計画と写し（`phases:`）が組めるか（設計 9.7）。

    写しだけを読んで済むもの（写しが無い、項の定義が写しに無い、定義の形と `kind`、使われて
    いない定義）は `phasetypes.copy_problems`、順序（`after` の形・終端・最後の項の延期・延期の
    引き受け手とそのレビュー）は `workflow.problems` が当てる。どちらも判定の側が同じものを当てる。
    延期できる定義か（レビュー不要の定義は延期するものが無い）はここで見る。

    `config` は承認したときの `phases.yml`。写しの各定義がその同じ名前の定義と読んだ形で同じかを
    確かめる（`copy_config_problems`）。`held` は改版で、子が承認された番号が使う定義
    （承認済みチケットの写しの値）。`approved` は承認済みチケットに当てる（`--lint`）ときで、
    `phases.yml` との食い違いは warn にする（進行中の親には効かないのが決まり）。
    """
    problems = list(phasetypes.copy_problems(t))
    if not t.has_plan:
        return problems
    types = phasetypes.types_of(t)
    for key, items in (("plan", t.plan), ("feedback", t.feedback or [])):
        for i, item in enumerate(items):
            pt = types.get(item.type)
            if pt is not None and item.deferred and pt.review == phasetypes.REVIEW_NONE:
                problems.append(
                    rules.Problem(
                        rules.SEVERITY_ERROR,
                        t.ticket,
                        f"`{key}[{i}]` の `{item.type}` はレビュー不要の定義。延期するものが無い",
                    )
                )
    problems.extend(workflow.problems(t, types))
    problems.extend(copy_config_problems(t, config, held, approved))
    return problems


def copy_config_problems(
    t: ticket_model.Ticket,
    config: dict | None,
    held: dict[str, phasetypes.PhaseType] | None = None,
    approved: bool = False,
) -> list[rules.Problem]:
    """写し（`phases:`）の各定義が、承認したときの `phases.yml` の同じ名前の定義と同じか。

    読んだ形で比べる（`phasetypes.same`）。`phases.yml` にあって写しに無い定義は何も言わない
    （この親では使わないだけ）。`held` に入っている定義（改版で、子が承認された番号が使う定義）は
    `phases.yml` ではなく承認済みチケットの写しの値と比べる。進んでいるフェーズの範囲・見る場所・
    成果物を改版で変えないため。

    `approved` なら承認済みチケットの食い違いとして warn で言う（`--lint`）。進行中の親には
    効かない（判定は写しを読む）ので、止めない。
    """
    if not phasetypes.has_copy(t) or not t.has_plan:
        return []
    severity = rules.SEVERITY_WARN if approved else rules.SEVERITY_ERROR
    fill = f"`ccnavi --plan-order {t.ticket} --fill-phases`"
    if config is None:
        return [
            rules.Problem(
                severity,
                t.ticket,
                "`plan` があるのにフェーズ定義（phases.yml）が読めない。"
                "写し（`phases:`）が phases.yml と同じかを確かめられない",
            )
        ]
    problems: list[rules.Problem] = []
    for ident, pt in phasetypes.types_of(t).items():
        if held is not None and ident in held:
            if not phasetypes.same(pt, held[ident]):
                fields = ", ".join(phasetypes.differing(pt, held[ident]))
                problems.append(
                    rules.Problem(
                        severity,
                        t.ticket,
                        f"`phases:` の `{ident}` は子が承認された番号が使う定義なので、承認済み"
                        f"チケットの値から変えられない（違う欄: {fields}）。{fill} で差し込み直す。"
                        "直したいときは、ユーザが承認済みチケットの `phases:` を直す",
                    )
                )
            continue
        ref = config.get(ident)
        if approved:
            if ref is None or not phasetypes.same(pt, ref):
                what = (
                    "今の phases.yml に無い"
                    if ref is None
                    else "今の phases.yml と違う（違う欄: "
                    + ", ".join(phasetypes.differing(pt, ref))
                    + "）"
                )
                problems.append(
                    rules.Problem(
                        severity,
                        t.ticket,
                        f"`phases:` の `{ident}` が{what}。進行中の親には効かない（判定は"
                        "承認済みチケットの写しを読む）。反映するには、子の無い番号の定義なら"
                        f" {fill} を打った改版を出す（計画が同じでも改版になる）。子が承認された"
                        "番号の定義なら、ユーザが承認済みチケットの `phases:` を直す",
                    )
                )
            continue
        if ref is None:
            problems.append(
                rules.Problem(severity, t.ticket, f"`phases:` の `{ident}` は phases.yml に無い")
            )
        elif not phasetypes.same(pt, ref):
            problems.append(
                rules.Problem(
                    severity,
                    t.ticket,
                    f"`phases:` の `{ident}` が phases.yml の定義と違う"
                    f"（違う欄: {', '.join(phasetypes.differing(pt, ref))}）。"
                    f"{fill} で写し直す",
                )
            )
    return problems


def revision_problems(
    root: str,
    conf: settings.Settings,
    revised: ticket_model.Ticket,
    current: ticket_model.Ticket,
    config: dict | None,
) -> list[rules.Problem]:
    """親の改版を受けてよいか（設計 9.7）。

    変えてよいのは計画（`plan:` / `feedback:`）と写し（`phases:`）。写しは、子が承認された番号
    （固定した番号）が使う定義だけ承認済みチケットの値のまま（`phases.yml` と違っていてよい）、
    ほかは今の `phases.yml` と同じ。足すのも外すのも自由。
    """
    fixed = fixed_numbers(conf, root, current.ticket)
    held_types = phasetypes.types_of(current)
    held: dict[str, phasetypes.PhaseType] = {}
    for n in fixed:
        item = current.item_at(n)
        if item is not None and item.type in held_types:
            held[item.type] = held_types[item.type]
    problems = plan_problems(revised, config, held)
    if any(p.severity == rules.SEVERITY_ERROR for p in problems):
        return problems
    # 変えられるのは計画と写しだけ。
    if _scope_signature(revised) != _scope_signature(current):
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                revised.ticket,
                "改版で変えられるのは plan・feedback・phases だけ。範囲が承認済みチケットと違う",
            )
        )
    if (
        revised.title != current.title
        or revised.issue != current.issue
        or revised.issue_repo != current.issue_repo
    ):
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                revised.ticket,
                "改版で変えられるのは plan・feedback・phases だけ。"
                "題か課題番号が承認済みチケットと違う",
            )
        )
    if revised.branch != current.branch:
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                revised.ticket,
                "改版で変えられるのは plan・feedback・phases だけ。"
                "`branch:` が承認済みチケットと違う（親のブランチは承認で決まる）",
            )
        )
    # 錠は番号ごと。子が承認された番号（固定した番号）の項だけを変えさせない。
    problems += lock_problems(revised, current, fixed)
    # フィードバック計画の番号は全体計画の続きなので、フィードバック計画を立てたあとに全体計画を
    # 変えると番号と `after` がずれる。比べるのは定義・`review`・推移的な待ちの並び。
    # フィードバック計画を立てる改版でも同じ。全体計画の最後のレビューを済んだ扱いにするので、
    # 一緒に足した全体計画の項までレビュー済みになる。
    plan_changed = approval.plan_signature(revised, "plan") != approval.plan_signature(
        current, "plan"
    )
    if plan_changed and (current.feedback is not None or revised.feedback is not None):
        when = "立てたあと" if current.feedback is not None else "立てる改版で"
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                revised.ticket,
                f"フィードバック計画を{when}は全体計画を変えられない"
                "（フィードバック計画の番号は全体計画の続きで、項を足すと番号がずれる。"
                "全体計画を変えるなら、フィードバック計画を出す前に改版する）",
            )
        )
    # フィードバック計画: 無い状態から 1 回だけ、全体計画の最後のレビューが済んでから。
    # 比べるのは定義・`review`・推移的な待ちの並び（`after` だけの違いも違いとして拾う）。
    if approval.plan_signature(revised, "feedback") != approval.plan_signature(current, "feedback"):
        if current.feedback is not None:
            # 残りの切り出し先は進め方で違う。マージリクエストがあれば issue に切り出せるが、
            # chat で回した親はホストに何も無いので、新しい親チケットの提案にする。
            elsewhere = (
                "残りは新しい親チケットの提案として wip/proposals/todo/ に書いてください"
                if phase.chat_only(root, conf, current.ticket)
                else "残りは別 issue に回してください（ccnavi-review.sh decide）"
            )
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    revised.ticket,
                    "フィードバック計画は 1 回だけ。承認済みのフィードバック作業フェーズに"
                    f"子を足してやり直すか、{elsewhere}",
                )
            )
        elif not phase.plan_finished(root, conf, current):
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    revised.ticket,
                    "フィードバック計画は、全体計画のフェーズが全部閉じてレビューが済んでから出す。"
                    "作業が終わるまで対応は計画できない",
                )
            )
    return problems


def _scope_signature(t: ticket_model.Ticket) -> tuple:
    return tuple((e.decision, e.glob, e.regex) for e in t.entries)


def fixed_numbers(conf: settings.Settings, root: str, parent_id: str) -> set[int]:
    """この親で、子が承認された（開いていても閉じていても）番号の集合。改版の錠が固定する番号。"""
    return {
        t.phase
        for t in approval.all_tickets(conf, root)
        if t.parent == parent_id and t.phase is not None
    }


def lock_problems(
    revised: ticket_model.Ticket,
    current: ticket_model.Ticket,
    fixed: set[int],
    moved: dict[int, int] | None = None,
) -> list[rules.Problem]:
    """改版の番号ごとの錠（設計 9.7）。`fixed` は子が承認された番号。

    固定した番号の項は、定義・`review`・番号・推移的な待ち（`workflow.compute` の `waits`）を
    変えさせない。待ちを推移的な形で比べるのは、エージェントが `--explain` の「待つ: …」を写しても、
    直接の先行だけを書いても、同じ意味なら通すため。固定した番号が固定していない番号を待つ形
    （子を手で動かして承認した親）では、その先行も項の同一性（定義と `review`）で比べる。
    番号だけで比べると、先行の番号に別の項を入れ替える改版が通ってしまう。

    延期の引き受け手は、承認済みか提案の引き受け手が固定した番号のときだけ比べる。まだ始まって
    いない項の間で引き受け手が入れ替わるだけなら、済んだレビューが延期した作業を引き受けたことに
    はならない。

    `moved` は番号を振り直したとき（`--plan-order`）の、元の番号 → 振り直した番号。延期した項は
    元の項の同一性で比べる（承認済みの n 番の延期を、振り直した先の番号の延期と比べる）。固定した
    番号は振り直しで動かないので、ほかの比べ方は変わらない。
    """
    problems: list[rules.Problem] = []

    def error(text: str) -> None:
        problems.append(rules.Problem(rules.SEVERITY_ERROR, revised.ticket, text))

    held_items = dict(current.numbered())
    new_items = dict(revised.numbered())
    held = workflow.compute(current)
    fresh = workflow.compute(revised)
    for n in sorted(fixed):
        before, after = held_items.get(n), new_items.get(n)
        if before is None:
            continue
        if after is None or (after.type, after.review) != (before.type, before.review):
            shown = f"{after.type}" if after is not None else "無い"
            error(
                f"{n} 番目（{before.type}）は子が承認されているので、"
                f"定義・review・番号を変えられない（改版では {shown}）。子の phase が指す先が変わる"
            )
            continue
        if held.waits.get(n, []) != fresh.waits.get(n, []):
            error(
                f"{n} 番目（{before.type}）は子が承認されているので、待ちを変えられない"
                f"（いま待つ: {_numbers(held.waits.get(n, []))}、"
                f"改版: {_numbers(fresh.waits.get(n, []))}）"
            )
            continue
        for m in held.waits.get(n, []):
            if m in fixed:
                continue
            was, now = held_items.get(m), new_items.get(m)
            if now is None or was is None or (now.type, now.review) != (was.type, was.review):
                error(
                    f"{n} 番目は子の無い {m} 番目を待っているので、{m} 番目の項"
                    f"（{was.type if was is not None else '?'}）を入れ替えられない"
                )
    moved = moved or {}
    back = {new: old for old, new in moved.items()}
    for n in sorted(set(held.review_at) | {back.get(m, m) for m in fresh.review_at}):
        before_at, after_at = held.review_at.get(n), fresh.review_at.get(moved.get(n, n))
        if before_at == after_at:
            continue
        if before_at in fixed or after_at in fixed:
            error(
                f"延期の引き受け手が変わる（{n} 番目の延期: {before_at or '無し'} → "
                f"{after_at or '無し'}）。"
                "子が承認された番号が引き受ける延期は、行き先を変えられない"
            )
    return problems


def _numbers(found: list[int]) -> str:
    return ", ".join(map(str, found)) if found else "何も待たない"


def validate(
    t: ticket_model.Ticket,
    pool: dict[str, ticket_model.Ticket],
    config: dict | None = None,
    approved: bool = False,
) -> tuple[list[rules.Problem], list[rules.Problem]]:
    """承認の対象にしてよいかを見る。親子の制約はここでしか見られない。

    返すのは 2 つのリスト。1 つめはチケットの形の苦情で、error があれば承認しない。
    2 つめは範囲の超過（親の範囲・定義の上限を超えた項、regex の項）で、承認は止めない。
    判定が親と定義の上限で切り詰めるので、承認で止める理由が無い。

    形の検査を error に残すのは、**まとめて 1 度で見せて直させるため**。判定の側も同じ
    検査を当てる（`blocking_problems`）ので「判定では補えない」わけではないが、
    判定に任せると、承認の画面では通って、あとで書き込みが止まってから気づくことになる。
    承認はユーザがまとめて見て決める場所なので、そこで落ちるものはそこで言う。

    `config` は承認したときの `phases.yml`（親の写しを確かめる。`plan_problems`）。子の定義は
    親の写しから引き、`phases.yml` からは引かない。`approved` は承認済みチケットに当てる
    `--lint` で、写しと `phases.yml` の食い違いを warn にする。
    """
    problems: list[rules.Problem] = []
    overflow: list[rules.Problem] = []
    if not t.is_child:
        return plan_problems(t, config, approved=approved), overflow
    problems.extend(phasetypes.copy_problems(t))
    parent = pool.get(t.parent)
    problems.extend(approval_checks.child_problems(t, parent))
    if parent is None or parent.is_child:
        return problems, overflow
    overflow.extend(ticket_mod.subset_problems(t, parent))
    if problems:
        # 番号が親の計画に無い。定義を引けないので、ここから先は見ても意味が無い。
        return problems, overflow
    if parent.has_plan and t.phase is not None:
        item = parent.item_at(t.phase)
        pt = phasetypes.types_of(parent).get(item.type)
        if pt is None:
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    t.ticket,
                    f"{t.phase} 番目の定義 `{item.type}` が親 {parent.ticket} の `phases:` に無い",
                )
            )
            return problems, overflow
        overflow.extend(phasetypes.scope_problems(t, pt))
    # regex の項は親の検査と定義の検査が同じ文で言う。画面に 2 度並べない。
    seen: set[str] = set()
    distinct = []
    for p in overflow:
        if p.detail not in seen:
            seen.add(p.detail)
            distinct.append(p)
    return problems, distinct


def existing_branch_warnings(
    root: str, conf: settings.Settings, proposals: list, copies: list, closed: list, review: list
) -> list[str]:
    """新規の提案（親）の親のブランチ名が、手元か origin に既にあるときの文。

    `--lint` と `--agree --preview --verify` が warn として出す。承認は止めない。
    提案がそのブランチの上で書かれている（`.claude/worktrees/<識別子>` がそのブランチを
    チェックアウトしていて、提案がその中にある）ときは、そのブランチが親のブランチなので言わない。
    """
    settled = {t.ticket for t in list(copies) + list(closed) + list(review)}
    found: list[str] = []
    cache: dict[str, set[str]] = {}
    said: set[str] = set()
    for t in proposals:
        if t.state != ticket_model.TODO or t.is_child or t.ticket in settled or t.ticket in said:
            continue
        said.add(t.ticket)
        if t.branch:
            # `branch:` で既にあるブランチを指すのは、そのブランチで作業するという宣言。warn は
            # 出さず、承認画面に「既存のブランチ <名前> を使う」と出す。
            continue
        if _written_on_own_branch(root, conf, t):
            continue
        repo = tree.project_root(conf.projects, t.project) if t.project else root
        if repo not in cache:
            cache[repo] = gitstate.branch_names(repo)
        folded = t.ticket.casefold()
        hits = sorted(n for n in cache[repo] if n.casefold() == folded)
        if hits:
            found.append(
                f"{t.ticket}: 親のブランチ名と同じブランチ（{', '.join(hits)}）が既にある。"
                "承認すると、そのブランチを親のブランチとして取り込み・送る。"
                "別の作業なら識別子を変える"
            )
    return found


def _written_on_own_branch(root: str, conf: settings.Settings, t) -> bool:
    """提案が、自分の識別子の名前のワークツリーの中にあり、そのワークツリーが親のブランチ
    （`branch:`、無ければ識別子）の上か。"""
    if not t.path:
        return False
    here = tree.tree_of(root, t.path, conf.projects)
    if here is None or here.is_main or here.name != t.ticket:
        return False
    return tree.branch_of(here.root) == ticket_ids.branch_name(t)


def branch_exists(root: str, conf: settings.Settings, t) -> bool:
    """親の `branch:` のブランチが既にあるか。

    提案がそのブランチの上で書かれている（`_written_on_own_branch`）か、そのリポジトリの手元か
    origin にそのブランチがある（`tree.has_branch`。ファイルだけを読む）とき。Chrome の仮の
    ツリーでは提案はいつも親のブランチの上にある。
    """
    if not t.branch:
        return False
    if _written_on_own_branch(root, conf, t):
        return True
    repo = tree.project_root(conf.projects, t.project) if t.project else root
    return tree.has_branch(repo, t.branch)
