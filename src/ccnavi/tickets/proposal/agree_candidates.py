"""承認の候補と、その検査。提案を候補に組み、計画・改版・欄・ブランチの食い違いを並べる。

agree から分けた。agree を読まない。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...infra import gitstate, settings, tree
from ...policy import rules
from ..approval import checks as approval_checks
from ..approval import ops as approval_ops
from ..approval import store as approval
from ..config import phasetypes, workflow
from ..model import proposal_form, standing, ticket_ids, ticket_model
from ..model import ticket as ticket_mod
from ..phase import gates as phase


@dataclass
class Candidate:
    """承認の対象の 1 件。新規の提案か、親の改版か。

    リスクの点はここに無い。点は子を閉じるときに実績（差分）で数え、宣言の広さでは
    数えない（tickets/config/risk.py）。宣言の広さは、親が `human_review.reason` で言う。
    """

    ticket: ticket_model.Ticket
    complaints: list[rules.Problem] = field(default_factory=list)
    # 範囲の超過（親の範囲・定義の上限を超えた項、regex の項）。判定が
    # 切り詰める。判定に影響する（止まる）ので、判定に影響しない記述の注意（complaints の warn）
    # とは分けて持つ。
    overflow: list[rules.Problem] = field(default_factory=list)
    # 改版なら、いま使われている承認済みチケット。
    current: ticket_model.Ticket | None = None
    # このチケットに使うフェーズ定義。親のコピー（`phases:`）を読んだもの。
    # 親ならその提案のコピー、
    # 子なら親（承認済みか、同じ承認で通る提案）のコピー。承認画面はこれでタイトルと見る場所を出す。
    types: dict | None = None
    # 承認したときの `phases.yml`（共通レイヤー + 親の `project:` が指すレイヤーの和）。コピーが
    # これと同じかを確かめ、承認画面に「この親では使わない定義」を出すのに使う。
    config: dict | None = None
    # 承認画面に足す 1 行ずつの注記（フィードバック計画の証跡など）。
    notes: list[str] = field(default_factory=list)

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
    """承認の対象に入れるものと、外すものに分ける。3 つめは親子を引くための対応表。

    承認（`agree`）・見せる（`preview`）・確かめる（`verify`）に加えて、`--lint` も
    ここを通る。承認で外れるものを数える経路が 2 本あると、片方が気づかないうちに弱くなる
    （実際に `--lint` は `validate` だけを適用していて、順序で外れる子に何も言わなかった）。

    処理フロー:
    1. 承認済みを対応表（`pool`）に入れ、設定と先行の対応表の入れ物を用意する
    2. 改版（`revisions`）を 1 件ずつ検める（`revision_problems` など）。
       通った改版だけを `pool` に入れる
    3. 承認待ち（`pending`）を親・子の順に検める（`validate` など）。
       子の順序の検査には、同じ親で今回通った前の子を渡す
    4. 先行を書いた子は、`done/` に在る先行だけを満たすものとして検める
       （`approval_checks.predecessor_problems`）
    5. 外れなかったものを `batch`、外れたものを `rejected` に入れて返す
    """
    open_index = approval_checks.by_id(approved)
    # 親子を引く対応表は、承認済みチケットと、今回の承認で通ったものだけ。外れた親を対応表に残すと、
    # 承認されない親の範囲で子が検証され、親の承認を経ずに子の承認済みチケットができる。
    # pending は親が子より前に並ぶ（並べ替えのキーが親の識別子）ので、子が引くときには
    # 親の通過が決まっている。
    pool = approval_checks.by_id(approved)
    batch: list[Candidate] = []
    rejected: list[tuple[ticket_model.Ticket, list[rules.Problem]]] = []
    # レイヤーごとの `phases.yml` の読み込みは 1 プロジェクト 1 回。承認の対象に同じレイヤーの
    # チケットが何件あっても、ファイルを読むのはそのレイヤーにつき 1 度で足りる。読むのはコピーを
    # 確かめるためで、子の定義は親のコピーから引く（`phases.yml` からは引かない）。
    cache: dict[str, dict | None] = {}
    # 先行を引く対応表。先行を書いた子が居るときだけ、最初の 1 回で組む。
    preds: dict[str, list[ticket_model.Ticket]] | None = None
    # 親子のチケットの立ち位置。
    # 1 回の承認で 1 度だけ読む。
    fams = standing.Families(conf, root)
    # フィードバック計画を初めて足す改版と、同じ親の全体計画の子が同じ承認に入っているもの。
    # 子を足すとそのフェーズが開き直り、レビュー済みが前提の改版が成り立たない。両方を拒む。
    mixed = _feedback_with_plan_children(revisions, pending, open_index)

    def config_for(t: ticket_model.Ticket) -> dict | None:
        name = project_of(t, pool)
        if name not in cache:
            cache[name] = phase.load_types(conf, root, name)
        return cache[name]

    for t in sorted(revisions, key=lambda x: x.ticket):
        current = open_index[t.ticket]
        config = config_for(t)
        # 提案の先行関係とコピーの定義も承認済みと同じ関数で入れる（計画のステップの `after` と
        # `phases:`）。
        t.workflow = workflow.compute(t)
        types = phasetypes.types_of(t)
        complaints = _workflow_field(t) + script_field_problems(t) + record_field_problems(t)
        complaints += shorthand_problems(t)
        complaints += proposal_form.form_problems(t, revision=True)
        complaints += revision_problems(root, conf, t, current, config)
        complaints += approval_checks.family_problems(conf, root, t, fams)
        complaints += approval_checks.mr_problems(t)
        if t.ticket in mixed:
            complaints.append(_mixed_problem(t, mixed[t.ticket], is_revision=True))
        if any(p.severity == rules.SEVERITY_ERROR for p in complaints):
            rejected.append((t, complaints))
            continue
        cand = Candidate(
            ticket=t, complaints=complaints, current=current, types=types, config=config
        )
        if cand.plans_feedback:
            cand.notes = feedback_notes(t)
        batch.append(cand)
        # 通った改版だけ、一緒に承認する子から見える親にする。外れた改版の計画で子を
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
        missing = ""
        if t.is_child and pool.get(t.parent) is None:
            missing = parent_elsewhere_text(root, conf, t)
        complaints, overflow = validate(t, pool, config, missing=missing)
        complaints += _workflow_field(t) + script_field_problems(t) + record_field_problems(t)
        complaints += shorthand_problems(t)
        complaints += proposal_form.form_problems(t)
        complaints += approval_checks.project_problems(t, pool, conf)
        complaints += approval_checks.place_problems(root, conf, t)
        complaints += approval_checks.family_problems(conf, root, t, fams)
        complaints += approval_checks.mr_problems(t)
        if t.is_child and t.parent in mixed and t in mixed[t.parent]:
            complaints.append(_mixed_problem(t, mixed[t.parent], is_revision=False))
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
            )
        )
        pool[t.ticket] = t
        if t.is_child:
            added.setdefault(t.parent, []).append(t)
    return batch, rejected, pool


def _feedback_with_plan_children(
    revisions: list[ticket_model.Ticket],
    pending: list[ticket_model.Ticket],
    open_index: dict[str, ticket_model.Ticket],
) -> dict[str, list[ticket_model.Ticket]]:
    """親の識別子 → 同じ承認に入る、その親の全体計画（`len(plan)` 番以下）の子。

    対象はフィードバック計画を初めて足す改版（`Candidate.plans_feedback` と同じ条件）だけ。
    絞り込みの有無・入口（CLI・ボード・Chrome）に関わらず、`pending` / `revisions` が
    同じなら同じ答えになる。フィードバック計画のフェーズの子は数えない（同時に承認してよい）。
    """
    out: dict[str, list[ticket_model.Ticket]] = {}
    for rev in revisions:
        current = open_index.get(rev.ticket)
        if current is None or current.feedback is not None or rev.feedback is None:
            continue
        kids = [
            c
            for c in pending
            if c.is_child
            and c.parent == rev.ticket
            and c.phase is not None
            and c.phase <= len(rev.plan)
        ]
        if kids:
            out[rev.ticket] = kids
    return out


def _mixed_problem(
    t: ticket_model.Ticket, kids: list[ticket_model.Ticket], is_revision: bool
) -> rules.Problem:
    names = ", ".join(k.ticket for k in kids)
    who = (
        f"フィードバック計画の改版は、同じ承認で全体計画の子（{names}）を足すあいだは承認しない"
        if is_revision
        else f"親 {t.parent} のフィードバック計画の改版と同じ承認には入れない（{names}）"
    )
    return rules.Problem(
        rules.SEVERITY_ERROR,
        t.ticket,
        f"{who}。全体計画に足す子を先に承認し、その作業と再レビュー（レビュー済み）を済ませてから、"
        "フィードバック計画の改版を承認する",
    )


def _workflow_field(t: ticket_model.Ticket) -> list[rules.Problem]:
    """提案に先行関係の欄（`workflow:`）が書いてあれば拒む。欄は読まないので、書いてあると
    効くと読み手に思わせる。先行関係は計画のステップの `after` から計算する。"""
    if ticket_model.WORKFLOW_KEY not in t.raw:
        return []
    return [
        rules.Problem(
            rules.SEVERITY_ERROR,
            t.ticket,
            f"`{ticket_model.WORKFLOW_KEY}` の欄は提案に書かない（読まない）。"
            "先行関係は計画のステップの `after` から決まる",
        )
    ]


def shorthand_problems(t: ticket_model.Ticket) -> list[rules.Problem]:
    """提案に欄の短縮形（`issue: "#12"`・`human_review: true/false`）があれば拒む。

    読み取り（`ticket.parse`）は承認済みチケットのために寛容なまま。書き直せる提案だけを正す。
    """
    out: list[rules.Problem] = []
    issue = t.raw.get("issue")
    if isinstance(issue, str) and issue.strip().startswith("#"):
        out.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"`issue: {issue.strip()}` は短縮形。番号だけを書く"
                f"（`issue: {issue.strip().lstrip('#').strip()}`）",
            )
        )
    if isinstance(t.raw.get("human_review"), bool):
        out.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                "`human_review` は {required: true か false, reason: 理由} で書く"
                "（true/false だけは書かない）",
            )
        )
    return out


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
            "提案には値を書かない（空にするか削除します）。改版の提案では、承認済みチケットをコピーしたときに"
            "入る started_at・completed_at・base_sha・merged_sha・cancelled_at・cancel_reason・"
            "finished_to・settled_by を"
            "空にする"
            "（改版は承認済みチケットの側の値を残し、計画・`phases:`・範囲だけを差し替える）",
        )
    ]


def record_field_problems(t: ticket_model.Ticket) -> list[rules.Problem]:
    """提案に続きの子の目印（`followup_of`）があれば拒む。

    承認は中身を変えないので、提案に書いた欄はそのまま承認済みチケットに入る。続きの子の
    目印は、ユーザの判断（レビューの行き先）だけが残すもので、
    提案に書かせると続きの子を装える。
    """
    if approval.FOLLOWUP_KEY not in t.raw:
        return []
    names = f"`{approval.FOLLOWUP_KEY}`"
    return [
        rules.Problem(
            rules.SEVERITY_ERROR,
            t.ticket,
            f"{names} は続きの子を起こすレビューの行き先だけが残すもの。"
            "提案には書かない（改版の提案で承認済みチケットをコピーしたときも削除します）",
        )
    ]


def types_from(t: ticket_model.Ticket, pool: dict[str, ticket_model.Ticket]) -> dict:
    """このチケットに使う定義。親ならそのコピー、子なら親のコピー（親が引けなければ空）。

    親は承認済みならその承認済みチケット、同じ承認で通る提案ならその提案（`pool` に入っている）。
    `phases.yml` からは引かない。承認のあとに `phases.yml` から定義を消す・名前を変えても、
    進行中の親の子の承認が落ちないように。
    """
    if t.is_child:
        parent = pool.get(t.parent)
        return phasetypes.types_of(parent) if parent is not None else {}
    return phasetypes.types_of(t)


def project_of(t: ticket_model.Ticket, pool: dict[str, ticket_model.Ticket]) -> str:
    """このチケットのレイヤーを決める `project:`。

    子は親と同じ置き場に並ぶので、定義を引くには親のプロジェクトを使う。食い違えば
    `project_problems` が外す。親が対応表に居ないときだけ、子の置き場の値をそのまま読む。
    """
    if t.is_child:
        parent = pool.get(t.parent)
        if parent is not None:
            return parent.project
    return t.project


def feedback_notes(parent: ticket_model.Ticket) -> list[str]:
    """フィードバック計画の承認に添える証跡。何を見たうえでの合意かを残す。

    受け入れた未解決スレッドの記録は持たない（ホスト側で返信を残して解決済みにする）ので、
    件数は添えない。
    """
    notes: list[str] = []
    if not parent.feedback:
        notes.append("対応なし。見たうえで対応しない、という記録になる")
        notes.append("別に追うものは issue に回したか（ccnavi-user-decide.sh）")
    return notes


def plan_problems(
    t: ticket_model.Ticket,
    config: dict | None,
    held: dict[str, phasetypes.PhaseType] | None = None,
    approved: bool = False,
) -> list[rules.Problem]:
    """親の計画とコピー（`phases:`）が組めるか。

    コピーだけを読んで済むもの（コピーが無い、ステップの定義がコピーに無い、定義の形と `kind`、
    使われて
    いない定義）は `phasetypes.copy_problems`、順序（`after` の形・終端・最後のステップの延期・
    延期の引き受け手とそのレビュー）は `workflow.problems` が適用する。
    どちらも判定の側が同じものを適用する。延期できる定義か（レビュー不要の定義は延期するものが無い）はここで見る。

    `config` は承認したときの `phases.yml`。コピーの各定義がその同じ名前の定義と読んだ形で同じかを
    確かめる（`copy_config_problems`）。`held` は改版で、子が承認された番号が使う定義
    （承認済みチケットのコピーの値）。`approved` は承認済みチケットに適用する（`--lint`）ときで、
    `phases.yml` との食い違いは warn にする（進行中の親には効かないのが決まり）。
    """
    problems = list(phasetypes.copy_problems(t))
    if not t.has_plan:
        if not t.is_child and not approved and held is None:
            # 改版（held あり）の親は着手済みなので言わない。止まるのは `ticket start` で、
            # 承認依頼の前に気づけるよう、先に言う
            problems.append(
                rules.Problem(
                    rules.SEVERITY_WARN,
                    t.ticket,
                    "親の提案に計画（`plan:`）がありません。計画の無い親は承認しても"
                    "`ticket start` が止まります。"
                    "`ccnavi ticket new <識別子> --title <タイトル> --plan <型,…>"
                    " --issue <値> --mr <番号>` の雛形に揃えてください",
                )
            )
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
                        f"`{key}[{i}]` の `{item.type}` はレビュー不要の定義。"
                        "延期するものがありません。",
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
    """コピー（`phases:`）の各定義が、承認したときの `phases.yml` の同じ名前の定義と同じか。

    読んだ形で比べる（`phasetypes.same`）。`phases.yml` にあってコピーに無い定義は何も言わない
    （この親では使わないだけ）。`held` に入っている定義（改版で、子が承認された番号が使う定義）は
    `phases.yml` ではなく承認済みチケットのコピーの値と比べる。進んでいるフェーズの範囲・見る場所・
    成果物を改版で変えないため。

    `approved` なら承認済みチケットの食い違いとして warn で言う（`--lint`）。進行中の親には
    効かない（判定はコピーを読む）。
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
                "コピー（`phases:`）が phases.yml と同じかを確かめられない",
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
                    "今の phases.yml にありません。"
                    if ref is None
                    else "今の phases.yml と違う（違う欄: "
                    + ", ".join(phasetypes.differing(pt, ref))
                    + "）"
                )
                problems.append(
                    rules.Problem(
                        severity,
                        t.ticket,
                        f"`phases:` の `{ident}` が{what}。進行中の親には反映されない（判定は"
                        "承認済みチケットのコピーを読む）。反映するには、子の無い番号の定義なら"
                        f" {fill} を実行した改版を出す（計画が同じでも改版になる）。子が承認された"
                        "番号の定義なら、ユーザが承認済みチケットの `phases:` を直す",
                    )
                )
            continue
        if ref is None:
            problems.append(
                rules.Problem(
                    severity, t.ticket, f"`phases:` の `{ident}` は phases.yml にありません。"
                )
            )
        elif not phasetypes.same(pt, ref):
            problems.append(
                rules.Problem(
                    severity,
                    t.ticket,
                    f"`phases:` の `{ident}` が phases.yml の定義と違う"
                    f"（違う欄: {', '.join(phasetypes.differing(pt, ref))}）。"
                    f"{fill} でコピーし直す",
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
    """親の改版を受けてよいか。

    変えてよいのは計画（`plan:` / `feedback:`）・コピー（`phases:`）・範囲。コピーは、
    子が承認された番号
    （固定した番号）が使う定義だけ承認済みチケットの値のまま（`phases.yml` と違っていてよい）、
    ほかは今の `phases.yml` と同じ。足すのも外すのも自由。

    処理フロー:
    1. 子が承認された番号（`fixed_numbers`）のステップが持つ型の定義を、承認済みのコピーから集める
    2. 計画を検める（`plan_problems`）。error があれば、ここで返す
    3. 範囲の改版なら、読めない項（`unreadable_scope`）と作業中の子（`open_children`）を見る
    4. タイトル・課題・`mr:` を比べる（`mr:` は片方が空なら比べない）
    5. 固定した番号のステップを変えていないかを見る（`lock_problems`）
    6. フィードバック計画の追加・変更を、全体計画の状態に照らして見る（`plan_signature`）
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
    # 範囲を変える改版は、作業中の子が居ないときだけ。子の範囲は親の範囲の中に収まる前提で
    # 承認されているので、親の範囲を変えるなら、子は一度取り消して出し直す。
    # 書かれる節（生の値）が違うなら、読めない項が混ざっていないかを見る。読めない項は
    # `entries` に入らないので、署名では違いに出ない。
    if any(revised.raw.get(s) != current.raw.get(s) for s in rules.SECTIONS):
        problems += unreadable_scope(revised)
    if approval.scope_signature(revised) != approval.scope_signature(current):
        busy = open_children(conf, root, current.ticket)
        if busy:
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    revised.ticket,
                    "範囲を変える改版は、作業中の子が居ないときだけ出せる"
                    f"（作業中の子: {', '.join(busy)}）。"
                    "`ccnavi-ticket.sh cancel <子> --reason <理由>` で子を取り消してから、"
                    "改版を出し直す。範囲を変えたあとの子は、新しい子として提案する",
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
                "改版で変えられるのは plan・feedback・phases・範囲だけ。"
                "タイトルか課題番号が承認済みチケットと違う",
            )
        )
    # `mr:` は承認のあとに ccnavi が書き足すことがある（`review request`）。改版の提案は
    # 書き足す前のコピーかもしれないので、片方が空なら比べない。両方に値があって違えば error。
    if revised.mr is not None and current.mr is not None and revised.mr != current.mr:
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                revised.ticket,
                "改版で変えられるのは plan・feedback・phases・範囲だけ。"
                "`mr:` が承認済みチケットと違う",
            )
        )
    # 錠は番号ごと。子が承認された番号（固定した番号）のステップだけを変えさせない。
    problems += lock_problems(revised, current, fixed)
    # フィードバック計画の番号は全体計画の続きなので、フィードバック計画を立てたあとに全体計画を
    # 変えると番号と `after` がずれる。比べるのは定義・`review`・推移的な先行の並び。
    # フィードバック計画を立てる改版でも同じ。
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
                "（フィードバック計画の番号は全体計画の続きで、ステップを追加すると番号がずれる。"
                "全体計画を変えるなら、フィードバック計画を出す前に改版する）",
            )
        )
    # フィードバック計画: 無い状態から 1 回だけ、全体計画の最後のレビューが済んでから。
    # 比べるのは定義・`review`・推移的な先行の並び（`after` だけの違いも違いとして拾う）。
    if approval.plan_signature(revised, "feedback") != approval.plan_signature(current, "feedback"):
        if current.feedback is not None:
            # 残りの切り出し先は進め方で違う。マージリクエストがあれば issue に切り出せるが、
            # マージリクエストを持たない親はホストに何も無いので、新しい親チケットの提案にする。
            elsewhere = (
                "残りは新しい親チケットの提案として wip/proposals/todo/ に書いてください"
                if phase.without_mr(root, conf, current.ticket)
                else "残りは別 issue に回してください（ccnavi-user-decide.sh）"
            )
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    revised.ticket,
                    "フィードバック計画は 1 回だけ作成でき、変更できません。ただし、承認済みの"
                    "フィードバック作業フェーズの定義の上限と親の範囲に収まる修正は、その"
                    "フェーズに子チケットを追加して対応できます（設計の修正でも、定義が親の範囲の"
                    "設計書を持つ場合は同様です）。収まらない場合は、ユーザによる定義の変更か、"
                    f"別作業への切り出しが必要です。切り出す場合は、{elsewhere}",
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


def unreadable_scope(t: ticket_model.Ticket) -> list[rules.Problem]:
    """範囲の節に、判定が読めない項が混ざっていないか。

    改版は提案の節をそのまま承認済みチケットに書く。承認画面に出る差分は読めた項
    （`entries`）から組むので、読めなかった項（書き込みのツールを含まない `match`、絶対パス、
    型違いの節）は画面に出ないまま残る。数が合わなければ拒む。
    """
    raw = 0
    for section in rules.SECTIONS:
        value = t.raw.get(section)
        if value is None:
            continue
        if not isinstance(value, list):
            return [_unreadable(t, f"`{section}` がリストではない")]
        raw += len(value)
    if raw != len(t.entries):
        return [
            _unreadable(
                t, f"判定が読めない項が {raw - len(t.entries)} 件ある（`ccnavi --lint` で確かめる）"
            )
        ]
    return []


def _unreadable(t: ticket_model.Ticket, why: str) -> rules.Problem:
    return rules.Problem(
        rules.SEVERITY_ERROR,
        t.ticket,
        f"範囲の節に、判定が読めない項がある改版は出せない: {why}",
    )


def open_children(conf: settings.Settings, root: str, parent_id: str) -> list[str]:
    """この親の子で、作業中（`doing/`）のものの識別子。範囲を変える改版を止める。

    閉じた子（終わった・取り消した）とレビュー待ちの子は数えない。仕事が済んでいて、
    親の範囲が変わっても動かす先が無いため。
    """
    # `all_tickets` は子のワークツリーに checkout されたままの古い版も拾う。親のツリーで
    # 取り消したあとも `doing/` のコピーが残るので、数えると、取り消しても改版が通らなくなる。
    # 判定と同じく、正とするツリーの側だけを数える（`approval.scan`）。
    opened, _ = approval.scan(conf, root)
    return sorted(
        t.ticket for t in opened if t.parent == parent_id and t.state == ticket_model.DOING
    )


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
    """改版の番号ごとの錠。`fixed` は子が承認された番号。

    固定した番号のステップは、定義・`review`・番号・推移的な先行（`workflow.compute` の `waits`）を
    変えさせない。先行を推移的な形で比べるのは、
    エージェントが `--explain` の「先行 …」をコピーしても、
    直接の先行だけを書いても、同じ意味なら通すため。固定した番号に固定していない番号が先行する形
    （子を手で動かして承認した親）では、その先行もステップの同一性（定義と `review`）で比べる。
    番号だけで比べると、先行の番号に別のステップを入れ替える改版が通ってしまう。

    延期の引き受け手は、承認済みか提案の引き受け手が固定した番号のときだけ比べる。まだ始まって
    いないステップの間で引き受け手が入れ替わるだけなら、済んだレビューが延期した作業を引き受けたことに
    はならない。

    `moved` は番号を振り直したとき（`--plan-order`）の、元の番号 → 振り直した番号。
    延期したステップは元のステップの同一性で比べる（承認済みの n 番の延期を、
    振り直した先の番号の延期と比べる）。固定した番号は振り直しで動かないので、ほかの比べ方は変わらない。
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
                f"{n} 番目（{before.type}）は子が承認されているので、先行を変えられない"
                f"（いま先行: {_numbers(held.waits.get(n, []))}、"
                f"改版: {_numbers(fresh.waits.get(n, []))}）"
            )
            continue
        for m in held.waits.get(n, []):
            if m in fixed:
                continue
            was, now = held_items.get(m), new_items.get(m)
            if now is None or was is None or (now.type, now.review) != (was.type, was.review):
                error(
                    f"{n} 番目に子の無い {m} 番目が先行しているので、{m} 番目のステップ"
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
                "子が承認された番号が引き受ける延期は、延期先を変えられない"
            )
    return problems


def _numbers(found: list[int]) -> str:
    return ", ".join(map(str, found)) if found else "先行なし"


def parent_elsewhere_text(
    root: str,
    conf: settings.Settings,
    t: ticket_model.Ticket,
    everything: list[ticket_model.Ticket] | None = None,
) -> str:
    """子の親が承認済みの一覧に無いのに、別のツリーに同じ識別子が在るときの案内。

    親の識別子の承認済みチケットか提案が、子と違うツリーに在る形（親のワークツリーを識別子と
    違う名前で切った、など）を名指しする。`validate` の `missing` に渡す。無ければ空。
    判定は変えない（親が無いので error のまま）。
    `everything` は承認済みチケットの全部（読んであれば渡す。無ければここで読む）。
    """
    if not t.is_child or not t.parent:
        return ""
    if everything is None:
        everything = approval.all_tickets(conf, root)
    found, _ = ticket_mod.scan_all(root, conf.tickets, conf.projects)
    places: list[str] = []
    for u in list(everything) + list(found):
        if u.ticket != t.parent or u.is_child or u.tree == t.tree:
            continue
        place = f"{u.tree or '(ワークスペースルート)'} の {u.state or ticket_model.DOING}/"
        if place not in places:
            places.append(place)
    if not places:
        return ""
    return (
        f"親 {t.parent} が承認されていない。親 {t.parent} は {', '.join(places)} に在る。"
        f"親のワークツリーは {t.parent} と同名にして、その中に承認済みチケットを置く"
    )


def validate(
    t: ticket_model.Ticket,
    pool: dict[str, ticket_model.Ticket],
    config: dict | None = None,
    approved: bool = False,
    missing: str = "",
) -> tuple[list[rules.Problem], list[rules.Problem]]:
    """承認の対象にしてよいかを見る。親子の制約はここでしか見られない。

    返すのは 2 つのリスト。1 つめはチケットの形の苦情で、error があれば承認しない。
    2 つめは範囲の超過（親の範囲・定義の上限を超えた項、regex の項）で、
    判定が親と定義の上限で切り詰める。

    形の検査を error に残すのは、まとめて 1 度で見せて直させるため。判定の側も同じ
    検査を適用する（`blocking_problems`）ので「判定では補えない」わけではないが、
    判定に任せると、承認の画面では通って、あとで書き込みが止まってから気づくことになる。
    承認はユーザがまとめて見て決める場所なので、そこで外れるものはそこで言う。

    `config` は承認したときの `phases.yml`（親のコピーを確かめる。`plan_problems`）。子の定義は
    親のコピーから引き、`phases.yml` からは引かない。`approved` は承認済みチケットに適用する
    `--lint` で、コピーと `phases.yml` の食い違いを warn にする。
    """
    problems: list[rules.Problem] = []
    overflow: list[rules.Problem] = []
    if t.has_maintenance and not approved:
        # 保守用の子の印は、通常の提案・承認の経路では使えない（`ticket consolidate` が
        # 承認済みの置き場へ直に作る）。印を持つ提案は承認しない。
        return [
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"`{ticket_model.MAINTENANCE_KEY}:` は通常の提案・承認の経路では指定できない印。"
                "提案から欄を消す（統合の子は `ccnavi-ticket.sh consolidate <親>` が作る）",
            )
        ], overflow
    if not t.is_child:
        mark = t.maintenance_problem()
        if mark:
            return [rules.Problem(rules.SEVERITY_ERROR, t.ticket, mark)], overflow
        # 承認済みの親はコピーが固定で、言われても直せない（改版が要る）。承認の前に言えば足りる
        if t.has_plan and not approved:
            overflow.extend(phasetypes.parent_scope_problems(t))
        return plan_problems(t, config, approved=approved), overflow
    problems.extend(phasetypes.copy_problems(t))
    parent = pool.get(t.parent)
    problems.extend(approval_checks.child_problems(t, parent, missing))
    if parent is None or parent.is_child:
        return problems, overflow
    if t.is_consolidate:
        # 統合の子の範囲の上限は、ccnavi の中に固定した定義（親の範囲は適用しない）。
        if not problems:
            overflow.extend(phasetypes.scope_problems(t, phasetypes.consolidate_type()))
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
                    f"{t.phase} 番目の定義 `{item.type}` が親 {parent.ticket} の `phases:`"
                    " にありません。",
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
    """新規の提案（親）の識別子になるブランチ（`/` を `-` に置き換えて識別子と一致するもの）が、
    手元か origin に既にあるときの文。

    `--lint` と `--agree --preview --verify` が warn として出す。
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
        if own_branch(root, conf, t):
            continue
        repo = tree.project_root(conf.projects, t.project) if t.project else root
        if repo not in cache:
            cache[repo] = gitstate.branch_names(repo)
        folded = t.ticket.casefold()
        hits = sorted(n for n in cache[repo] if ticket_ids.branch_to_id(n).casefold() == folded)
        if hits:
            found.append(
                f"{t.ticket}: 親のブランチ名と同じブランチ（{', '.join(hits)}）が既にある。"
                "承認すると、そのブランチを親のブランチとして取り込み、push する。"
                "別の作業なら識別子を変える"
            )
    return found


def own_branch(root: str, conf: settings.Settings, t) -> str:
    """提案が、自分の識別子の名前のワークツリーの中にあるとき、そのワークツリーの HEAD のブランチ。

    HEAD のブランチ名を変換（`ticket_ids.branch_to_id`）したものが識別子と一致するときだけ。
    そうでなければ空。Chrome の仮のツリーでは提案はいつも親のブランチの上にある。
    """
    if not t.path:
        return ""
    here = tree.tree_of(root, t.path, conf.projects)
    if here is None or here.is_main or here.name != t.ticket:
        return ""
    return standing.head_branch(here.root, t.ticket)
