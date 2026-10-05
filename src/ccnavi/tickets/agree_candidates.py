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
    # 範囲の超過（親の範囲・種類の上限を超えた項、regex の項）。承認は止めず、判定が
    # 切り詰める。判定に影響する（止まる）ので、判定に影響しない記述の注意（complaints の warn）
    # とは分けて持つ。
    overflow: list[rules.Problem] = field(default_factory=list)
    # 改版なら、いま使われている承認済みチケット。
    current: ticket_model.Ticket | None = None
    # このチケットに使うフェーズの種類（共通レイヤー + `project:` が指すレイヤー、設計 11.4.1）。
    # 承認の対象の中でもチケットごとに違いうるので、候補が引いたものを持っておく。
    types: dict | None = None
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
    # レイヤーごとの読み込みは 1 プロジェクト 1 回。承認の対象に同じレイヤーのチケットが
    # 何件あっても、ファイルを読むのはそのレイヤーにつき 1 度で足りる。
    cache: dict[str, dict | None] = {}
    # 先行を引く対応表。先行を書いた子が居るときだけ、最初の 1 回で組む。
    preds: dict[str, list[ticket_model.Ticket]] | None = None
    # 親子のチケットの立ち位置と統合先の取り込み結果。
    # 1 回の承認で 1 度ずつだけ読む。
    fams = syncstate.Families(conf, root)

    def types_for(t: ticket_model.Ticket) -> dict | None:
        name = project_of(t, pool)
        if name not in cache:
            cache[name] = phase.load_types(conf, root, name)
        return cache[name]

    for t in sorted(revisions, key=lambda x: x.ticket):
        current = open_index[t.ticket]
        types = types_for(t)
        complaints = _workflow_field(t) + script_field_problems(t) + record_field_problems(t)
        complaints += revision_problems(root, conf, t, current, types)
        complaints += approval_checks.family_problems(conf, root, t, fams)
        if any(p.severity == rules.SEVERITY_ERROR for p in complaints):
            rejected.append((t, complaints))
            continue
        cand = Candidate(ticket=t, complaints=complaints, current=current, types=types)
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
        types = types_for(t)
        complaints, overflow = validate(t, pool, types)
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
                preds = approval.predecessor_pool(conf, root)
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
                existing_branch=bool(t.branch) and branch_exists(root, conf, t),
            )
        )
        pool[t.ticket] = t
        if t.is_child:
            added.setdefault(t.parent, []).append(t)
    return batch, rejected, pool


def _workflow_field(t: ticket_model.Ticket) -> list[rules.Problem]:
    """提案に待ち方の欄（`workflow:`）が書いてあれば拒む。待ち方を書くのは `--agree` だけで、
    置き場は `phases/<親>/workflow.yml`（承認はチケットの中身を変えない）。"""
    if ticket_model.WORKFLOW_KEY not in t.raw:
        return []
    return [
        rules.Problem(
            rules.SEVERITY_ERROR,
            t.ticket,
            f"`{ticket_model.WORKFLOW_KEY}` の欄は提案に書かない。待ち方は --agree が"
            f" {approval_marks.PHASES_DIR}/<親>/{approval_marks.WORKFLOW_FILE} に書く",
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
    記録を持つ古い形は取り下げを記録の欄で決め、`workflow:` の欄も待ち方として読む。続きの子の
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


def project_of(t: ticket_model.Ticket, pool: dict[str, ticket_model.Ticket]) -> str:
    """このチケットのレイヤーを決める `project:`（設計 11.4.1）。

    子は親と同じ置き場に並ぶので、種類を引くには親のプロジェクトを使う。食い違えば
    `project_problems` が落とす。親が対応表に居ないときだけ、子の置き場の値をそのまま読む。
    """
    if t.is_child:
        parent = pool.get(t.parent)
        if parent is not None:
            return parent.project
    return t.project


def types_resolver(conf: settings.Settings, root: str, approved: list[ticket_model.Ticket]):
    """チケットに使う種類を引く関数。レイヤーごとの読み込みは 1 プロジェクト 1 回。"""
    pool = approval_checks.by_id(approved)
    cache: dict[str, dict | None] = {}

    def types_for(t: ticket_model.Ticket) -> dict | None:
        name = project_of(t, pool)
        if name not in cache:
            cache[name] = phase.load_types(conf, root, name)
        return cache[name]

    return types_for


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


def plan_problems(t: ticket_model.Ticket, types: dict | None) -> list[rules.Problem]:
    """親の計画が種類の定義と合っているか（設計 9.7）。"""
    problems: list[rules.Problem] = []
    if not t.has_plan:
        return problems
    if types is None:
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                "`plan` があるのにフェーズの種類の定義（phases.yml）が読めない",
            )
        )
        return problems
    for key, items, kind in (
        ("plan", t.plan, phasetypes.KIND_WORK),
        ("feedback", t.feedback or [], phasetypes.KIND_FEEDBACK),
    ):
        seen_types = {item.type for item in items}
        for i, item in enumerate(items):
            pt = types.get(item.type)
            if pt is None:
                problems.append(
                    rules.Problem(
                        rules.SEVERITY_ERROR, t.ticket, f"`{key}[{i}]` の種類 `{item.type}` は無い"
                    )
                )
                continue
            if pt.kind != kind:
                where = "全体計画" if key == "plan" else "フィードバック計画"
                problems.append(
                    rules.Problem(
                        rules.SEVERITY_ERROR,
                        t.ticket,
                        f"`{key}[{i}]` の `{item.type}` は kind `{pt.kind}`。{where}には置けない",
                    )
                )
            if item.deferred and pt.review == phasetypes.REVIEW_NONE:
                problems.append(
                    rules.Problem(
                        rules.SEVERITY_ERROR,
                        t.ticket,
                        f"`{key}[{i}]` の `{item.type}` はレビュー不要の種類。延期するものが無い",
                    )
                )
            for need in pt.requires:
                if need not in seen_types:
                    problems.append(
                        rules.Problem(
                            rules.SEVERITY_ERROR,
                            t.ticket,
                            f"`{item.type}` を置くなら `{need}` も {key} に要る（requires）",
                        )
                    )
        # 延期の先は、レビューがある項でなければならない。全体計画は待ち方（`workflow`）が
        # 引き受け手を決めるので、そちらで見る。
        if key == "plan":
            continue
        for i, item in enumerate(items):
            if not item.deferred:
                continue
            target = next((x for x in items[i + 1 :] if not x.deferred), None)
            if target is None:
                continue
            tpt = types.get(target.type)
            if tpt is not None and tpt.review == phasetypes.REVIEW_NONE and target.review != "mr":
                problems.append(
                    rules.Problem(
                        rules.SEVERITY_ERROR,
                        t.ticket,
                        f"`{key}[{i}]` を延期した先の `{target.type}` にレビューが無い",
                    )
                )
    if not any(p.severity == rules.SEVERITY_ERROR for p in problems):
        problems.extend(workflow.problems(t, types))
    return problems


def revision_problems(
    root: str,
    conf: settings.Settings,
    revised: ticket_model.Ticket,
    current: ticket_model.Ticket,
    types: dict | None,
) -> list[rules.Problem]:
    """親の改版を受けてよいか（設計 9.7）。"""
    problems = plan_problems(revised, types)
    if any(p.severity == rules.SEVERITY_ERROR for p in problems):
        return problems
    # 変えられるのは計画だけ。
    if _scope_signature(revised) != _scope_signature(current):
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                revised.ticket,
                "改版で変えられるのは plan と feedback だけ。範囲が承認済みチケットと違う",
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
                "改版で変えられるのは plan と feedback だけ。題か課題番号が承認済みチケットと違う",
            )
        )
    if revised.branch != current.branch:
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                revised.ticket,
                "改版で変えられるのは plan と feedback だけ。`branch:` が承認済みチケットと違う"
                "（親のブランチは承認で決まる）",
            )
        )
    # 全体計画: 子がある番号までは同じ順序でなければならない。
    if revised.plan != current.plan:
        frozen = _last_phase_with_children(conf, root, current.ticket)
        if frozen > len(revised.plan) or revised.plan[:frozen] != current.plan[:frozen]:
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    revised.ticket,
                    f"全体計画の {frozen} 番目までは子が承認されているので変えられない。"
                    "番号がずれると子の phase が指す先が変わる",
                )
            )
    # 延期の引き受け手: 子がある番号までは変えない。引き受け手が前へ動くと、延期した作業を
    # 済んだレビューが引き受けたことになり、誰にも見られずに終わる。
    frozen = _last_phase_with_children(conf, root, current.ticket)
    held = workflow.effective(current, types).review_at
    fresh = workflow.compute(revised, types).review_at
    moved = [
        n
        for n in sorted(set(held) | set(fresh))
        if held.get(n) != fresh.get(n)
        and (
            n <= frozen
            or any(at is not None and at <= frozen for at in (held.get(n), fresh.get(n)))
        )
    ]
    if moved:
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                revised.ticket,
                f"延期の引き受け手が変わる（{', '.join(map(str, moved))} 番目）。"
                f"{frozen} 番目までは子が承認されているので、延期の行き先は変えられない",
            )
        )
    # フィードバック計画: 無い状態から 1 回だけ、全体計画の最後のレビューが済んでから。
    if revised.feedback != current.feedback:
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


def _last_phase_with_children(conf: settings.Settings, root: str, parent_id: str) -> int:
    """この親で、子が承認された（開いていても閉じていても）いちばん後ろの番号。"""
    numbers = [
        t.phase
        for t in approval._everything(conf, root)
        if t.parent == parent_id and t.phase is not None
    ]
    return max(numbers) if numbers else 0


def validate(
    t: ticket_model.Ticket, pool: dict[str, ticket_model.Ticket], types: dict | None = None
) -> tuple[list[rules.Problem], list[rules.Problem]]:
    """承認の対象にしてよいかを見る。親子の制約はここでしか見られない。

    返すのは 2 つのリスト。1 つめはチケットの形の苦情で、error があれば承認しない。
    2 つめは範囲の超過（親の範囲・種類の上限を超えた項、regex の項）で、承認は止めない。
    判定が親と種類の上限で切り詰めるので、承認で止める理由が無い。

    形の検査を error に残すのは、**まとめて 1 度で見せて直させるため**。判定の側も同じ
    検査を当てる（`blocking_problems`）ので「判定では補えない」わけではないが、
    判定に任せると、承認の画面では通って、あとで書き込みが止まってから気づくことになる。
    承認はユーザがまとめて見て決める場所なので、そこで落ちるものはそこで言う。
    """
    problems: list[rules.Problem] = []
    overflow: list[rules.Problem] = []
    if not t.is_child:
        return plan_problems(t, types), overflow
    parent = pool.get(t.parent)
    problems.extend(approval_checks.child_problems(t, parent))
    if parent is None or parent.is_child:
        return problems, overflow
    overflow.extend(ticket_mod.subset_problems(t, parent))
    if problems:
        # 番号が親の計画に無い。種類を引けないので、ここから先は見ても意味が無い。
        return problems, overflow
    if parent.has_plan and t.phase is not None:
        item = parent.item_at(t.phase)
        pt = (types or {}).get(item.type)
        if pt is None:
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    t.ticket,
                    f"{t.phase} 番目の種類 `{item.type}` の定義が読めない",
                )
            )
            return problems, overflow
        overflow.extend(phasetypes.scope_problems(t, pt))
    # regex の項は親の検査と種類の検査が同じ文で言う。画面に 2 度並べない。
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
