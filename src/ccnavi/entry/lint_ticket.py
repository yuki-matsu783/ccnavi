"""`--lint` のうち、チケットの検査。承認済みチケットの写し・承認・提案・古い提案と、
親子の運用に要る hook の登録・スクリプト・リモートを読み書きする手段を見る。
ブランチとワークツリーの検査は `lint_branch` に分けてある。
"""

from __future__ import annotations

import contextlib
import os

from ..infra import gitcmd, hookio, settings, tree
from ..policy import rules
from ..policy.rules import SEVERITY_ERROR, SEVERITY_WARN, Problem
from ..tickets import (
    agree,
    agree_candidates,
    approval,
    approval_checks,
    approval_marks,
    history,
    ops_stop,
    phase,
    review_host,
    ticket_fold,
    ticket_model,
)
from . import lint_branch, lint_project


def _copy_problems(
    root: str,
    conf: settings.Settings,
    copies: list[ticket_model.Ticket],
    index: dict[str, ticket_model.Ticket],
    closed: list[ticket_model.Ticket],
    raw: approval.Raw | None = None,
) -> list[Problem]:
    """作業中の承認済みチケットを検査する。

    承認は置き場で決まり、チケットの中の欄（`ccnavi_approved` など）では決まらない。

    置き場を動かして承認する進め方では `--agree` を通らないので、承認のときにしか
    当たらなかった検査が誰にも当たらない。判定は `blocked` の分だけを止めるが、
    止まる場所は書き込みのときで、そこで初めて知るのは遅い。ここで全部言う。

    severity は 3 通りに分かれる。

    - `blocked` は判定が止める理由なので error
    - フェーズの順序は warn。狂っていても範囲の決まり方には影響せず、判定も止めない
      （`approval_checks.blocking_problems`）。承認のときは error だが、承認済みのものに当てるのは
      「その順で始めた」という記録で、いま止める根拠にはならない
    - 計画の形と、種類の定義が読めないことは `validate` が付けた severity のまま（error）。
      判定は止めないが、承認の画面を通っていれば起きない形なので、置き場を動かして
      承認した分の不備を CI で止める。範囲の超過だけは `validate` も warn
    - 再開（`done/` から `doing/` へ手で戻す）で残った閉じるときの欄は warn。ユーザの再開を
      止めないため、判定も止めない
    - 着手済みのチケットで、状態の履歴に着手の行が無いことと、基準点（`base_sha`）がワークツリーの
      HEAD の祖先でないことは warn。提案の段階で書かれた着手の欄かもしれないが、履歴は ccnavi の外で
      動かした分を持たず、判定は git を読まないので止めない

    `raw` は呼び手が `approval.read_raw` で読んだ置き場。フェーズの順序（`phase.order_problems`）
    を子ごとに見るときに、置き場を読み直さないために渡す。
    """
    problems: list[Problem] = []
    pool = dict(index)
    for t in closed:
        pool.setdefault(t.ticket, t)
    resolve = _types_resolver(conf, root)
    for t in copies:
        left = approval_checks.resumed_fields(t)
        if left:
            names = ", ".join(f"`{name}`" for name in left)
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"{t.ticket}: 作業中（doing/）なのに {names} に値が残っている。"
                    "done/ から手で戻した再開なら、ユーザがその欄を空にしてください"
                    "（残っていると、フローの着手中の扱いなど、着手中として数えない箇所がある）",
                )
            )
        unrecorded = _record_unrecorded(conf, t)
        if unrecorded:
            problems.append(Problem(SEVERITY_WARN, "(ticket)", f"{t.ticket}: {unrecorded}"))
        if t.workflow_record_differs:
            warned = f"{t.ticket}: {OLD_WORKFLOW_DIFFERS}"
            problems.append(Problem(SEVERITY_WARN, "(ticket)", warned))
        unrecorded = _start_unrecorded(conf, t)
        if unrecorded:
            problems.append(Problem(SEVERITY_WARN, "(ticket)", f"{t.ticket}: {unrecorded}"))
        off = ops_stop.base_off_head(root, conf, t) if t.started_at else ""
        if off:
            # 判定には入れない（判定は git を読まない）。`start` も止めない（通れば基準点を
            # HEAD で書き直すので、止めても防げる形が無い）。ここと status が warn で知らせる。
            problems.append(Problem(SEVERITY_WARN, "(ticket)", f"{t.ticket}: {off}"))
        if t.blocked:
            problems.append(Problem(SEVERITY_ERROR, "(ticket)", f"{t.ticket}: {t.blocked}"))
            continue
        types = resolve(agree_candidates.project_of(t, pool))
        complaints, overflow = agree_candidates.validate(t, pool, types)
        for p in complaints + overflow:
            problems.append(Problem(p.severity, "(ticket)", f"{t.ticket}: {p.detail}"))
        parent = pool.get(t.parent) if t.is_child else None
        if parent is not None:
            for p in phase.order_problems(root, conf, t, parent, types, raw=raw):
                # 承認のときは error。承認済みのものに当てるのは「その順で始めた」という
                # 記録で、いま止める根拠にはならない。
                problems.append(Problem(SEVERITY_WARN, "(ticket)", f"{t.ticket}: {p.detail}"))
    return problems


def _orphan_workflows(conf: settings.Settings, root: str, raw: approval.Raw) -> list[Problem]:
    """親の承認済みチケットがどこにも無いのに残った待ち方のファイル（`phases/<親>/workflow.yml`）。

    待ち方のファイルは `--agree` が書き、取り下げが一緒に消す。親を手で動かして消した・取り下げの
    途中で止まった、などで残ると、同じ識別子で後から承認した親（手で動かした承認は待ち方を書かない）
    の待ち方として読まれる。判定には入れず warn で言う。
    """
    known = {t.ticket for t in raw.everything}
    problems: list[Problem] = []
    for t in approval.trees(conf, root):
        phases_dir = os.path.join(settings.approved_dir(conf, t.root), approval_marks.PHASES_DIR)
        try:
            names = sorted(os.listdir(phases_dir))
        except OSError:
            continue
        for name in names:
            held = approval.workflow_path(settings.approved_dir(conf, t.root), name)
            if name in known or not os.path.lexists(held):
                continue
            shown = os.path.relpath(held, root).replace(os.sep, "/")
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"{shown}: 待ち方のファイルがあるのに、親 {name} の承認済みチケットがどの置き場"
                    "（作業中・レビュー待ち・閉じた）にも無い。同じ識別子で後から承認した親の待ち方として"
                    "読まれるので、取り下げや手での移動で残ったものなら、ユーザが消してください",
                )
            )
    return problems


OLD_WORKFLOW_DIFFERS = (
    "古い形（承認の記録 ccnavi_approved を持つ）の workflow: 欄の待ち方が、今の phases.yml から"
    "計算した待ち方と違うので、欄を使わず全体計画を一直線（前の番号を全部待つ）で読んでいる。"
    "欄は手で書けるので、計算と合わない待ち方は効かせない。並行にしたければ、改版でユーザに"
    " --agree を通してもらう"
)


def _record_unrecorded(conf: settings.Settings, t) -> str:
    """古い形（承認の記録 `ccnavi_approved` を持つ）なのに、状態の履歴に承認の行が無いときの文。

    古い形は取り下げを記録の欄で決め、`workflow:` の欄も待ち方として読む。今の承認は記録を
    書かないので、新しく置かれた古い形は手で書かれた記録かもしれない。承認の行は `approved`
    （続きの子は `raised`）。履歴は ccnavi の外で動かした分と、履歴を書く前の版の承認を持たない
    ので、無いことだけでは止めない（warn）。
    """
    if not approval_checks.has_record(t) or not t.tree_root:
        return ""
    where = settings.approved_dir(conf, t.tree_root)
    entries, why = history.read(where, t.ticket, limit=0)
    kinds = (history.KIND_APPROVED, history.KIND_RAISED)
    if why or any(e.get("kind") in kinds for e in entries):
        return ""
    return (
        "承認の記録（ccnavi_approved）を持つ古い形なのに、状態の履歴に承認（approved / raised）の"
        "行が無い。今の承認は記録を書かないので、手で書かれた記録かもしれない。"
        "ユーザに承認済みチケットを確かめてもらってください（履歴を書く前の版で承認したものなら問題ない）"
    )


def _start_unrecorded(conf: settings.Settings, t) -> str:
    """着手の欄があるのに、状態の履歴に `started` の行が無いときの文。あれば空。

    着手の欄（`started_at`・`base_sha`）は `ticket start` だけが書き、書くときに履歴にも残す。
    承認はチケットの中身を変えないので、提案の段階で書かれた欄は、手で動かした承認では
    そのまま承認済みチケットに入り、中身だけでは道具が書いたものと見分けられない。
    履歴は ccnavi の外で動かした分を持たないので、無いことだけでは止めない
    （warn。判定にも入れない）。
    """
    if not t.started_at or not t.tree_root:
        return ""
    where = settings.approved_dir(conf, t.tree_root)
    entries, why = history.read(where, t.ticket, limit=0)
    if why or any(e.get("kind") == history.KIND_STARTED for e in entries):
        return ""
    return (
        "着手の欄（started_at）があるのに、状態の履歴に着手（started）の行が無い。"
        "ccnavi-ticket.sh start を通さずに書かれた欄かもしれない"
        "（提案の段階で書いて手で動かした承認など）。"
        "ユーザに started_at と base_sha を確かめてもらってください"
    )


def _types_resolver(conf: settings.Settings, root: str):
    """`project:` から、そのチケットに使う種類を引く（設計 11.4.1）。

    承認の対象の中でもチケットごとにレイヤーが違いうるので、1 つに決めずに引く形で渡す。
    読み込みは 1 レイヤー 1 回。
    """
    cache: dict[str, dict | None] = {}

    def resolve(project: str = ""):
        if project not in cache:
            cache[project] = phase.load_types(conf, root, project)
        return cache[project]

    return resolve


def _ticket(conf: settings.Settings, root: str) -> list[Problem]:
    """チケットと承認済みチケットとワークツリーが合っているかを見る（REQ-TKT-25）。

    判定に使われるのは承認済みチケットの側だけなので、ここで問うのは「使われている範囲は何か」と
    「ワークツリーと提案がそれと一致しているか」。一致していない状態は誤りでは
    ないが、書いたユーザは書いたとおりに使われていると思っている。
    検証はその思い違いを名指しする場所になる。
    """
    problems: list[Problem] = []
    if not conf.tickets_enabled:
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(ticket)",
                f"{settings.TICKET_CONTROL_ENV}=disable。チケットの範囲・フェーズの HITL ポイント・"
                "サブエージェントの制限は効かない",
            )
        )
        return problems
    root = root or os.getcwd()
    if not _any_copies(conf, root) and not tree_has_tickets(root, conf.tickets):
        # 承認済みチケットも提案も無い状態は不備ではない。チケットによる制御は任意で、
        # 使っていないプロジェクトにここで苦情を返すと、その 1 行が常態になって
        # 他の報告ごと読まれなくなる。
        return problems

    problems.extend(_approved_guarded(conf, root))

    raw = approval.read_raw(conf, root)
    copies, notes = approval.scan(conf, root, raw=raw)
    for note in notes:
        problems.append(Problem(SEVERITY_ERROR, "(ticket)", note))
    # 閉じた承認済みチケットの苦情も拾う。判定は閉じたものを読まないが、読めないファイルが
    # 置き場に残っていること自体は書いたユーザの思い違いで、言わないと他の機械へそのまま届く。
    closed, notes = approval.scan(conf, root, closed=True, raw=raw)
    for note in notes:
        problems.append(Problem(SEVERITY_ERROR, "(ticket)", note))
    review, notes = approval.scan_review(conf, root, raw=raw)
    for note in notes:
        problems.append(Problem(SEVERITY_ERROR, "(ticket)", note))
    index = approval_checks.by_id(copies)
    done = {t.ticket for t in closed + review}

    # 承認済みの識別子の提案は、承認済みチケットと合わせて本物とするツリーを決める
    # （`approval.scan_proposals` と同じまとめ方）。外に残った古い提案は別に名指しする。
    proposals, complaints, stale = approval.read_proposals(conf, root, raw.everything)
    problems.extend(complaints)
    problems.extend(_stale_problems(root, conf, stale, index, done))

    problems.extend(_copy_problems(root, conf, copies, index, closed, raw))
    problems.extend(_orphan_workflows(conf, root, raw))

    resolve = _types_resolver(conf, root)
    for t in copies:
        if not t.is_child and t.has_plan and resolve(t.project) is None:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    "(phases)",
                    f"{t.ticket} は計画を持つのにフェーズの種類の定義"
                    f"（{conf.phases} と {t.project or '自身'} のレイヤー）が読めない",
                )
            )

    # ツリーの名前から、そのツリーがどのリポジトリのものかを引く表。ワークスペースなら空、
    # プロジェクトならその名前で、ワークツリーは元リポジトリのほうに付く（tree.Tree）。
    # チケットは自分の置かれたツリーの名前しか持たないので、ここで作って渡す。
    repo_of = {
        t.name or "(ワークスペースルート)": t.project for t in tree.all_trees(root, conf.projects)
    }
    preds = approval_checks.predecessor_pool_of(copies, review, closed, proposals, root)
    approval_checks.align_imported(conf, root, preds)
    # 統合先の done/ で閉じた識別子も閉じたものに数える（開いた親子のチケットでも統合先の
    # done/ は常に読む）。承認の対象から外れる（`agree.waiting`）ので、何も言わずに済ませず
    # 名指しする。
    done |= approval_checks.integration_closed(conf, root, proposals)
    problems.extend(
        _proposal_problems(proposals, copies, index, closed, done, repo_of, preds, root=root)
    )
    problems.extend(
        lint_branch._branch_name_problems(
            proposals,
            copies,
            closed,
            review,
            conf.integration_branch or settings.integration_recorded(conf.state),
            conf.branch_prefixes,
        )
    )
    problems.extend(lint_branch._prefix_setting_problems(conf))
    problems.extend(
        lint_branch._existing_branch_problems(root, conf, proposals, copies, closed, review)
    )
    problems.extend(_approval_problems(root, conf, proposals, copies, closed, review))

    worktrees = tree.worktrees(root, conf.projects)
    problems.extend(lint_branch._worktree_problems(root, conf, worktrees, index, copies))
    names = {t.name for t in worktrees}
    if any(not t.is_child for t in copies):
        problems.extend(_review_token(root))
    problems.extend(_ticket_hooks(root))
    for stray in _stray_claude_dirs(root, names):
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(ticket)",
                f"{stray} はワークツリーでもワークスペースルートでもないのに .claude/ を持つ。"
                "そこへ cd するだけで、別のワークスペースルートのように見える",
            )
        )
    return problems


def _any_copies(conf: settings.Settings, root: str) -> bool:
    """どこかのツリーに承認済みチケットの置き場があるか。"""
    return any(
        os.path.isdir(settings.approved_dir(conf, t.root)) for t in approval.trees(conf, root)
    )


def _approved_guarded(conf: settings.Settings, root: str) -> list[Problem]:
    """承認済みチケットの置き場が守られているか。

    置き場は ccnavi ディレクトリ（`.ccnavi/`）の下にあり、守るのは組み込みの 1 本
    （`builtin-guard-project-home`）。ルールファイルには書かせない（書かせると消せる）。
    ここで見るのは、置き場が本当に ccnavi ディレクトリの下にあるか。外に向けると、
    その 1 本が当たらず、エージェントが承認済みチケットを書けて承認の意味が無くなる。

    置き場は既定に固定なので、外に向くのは診断のフラグ（`--approved` /
    `--project-home`）で動かしたときだけ。フラグが残る間は見ておく。
    """
    home = (conf.project_home or settings.DEFAULT_PROJECT_HOME).replace("\\", "/").strip("/")
    approved = (conf.approved or settings.DEFAULT_APPROVED).replace("\\", "/").strip("/")
    if home and (approved == home or approved.startswith(home + "/")):
        return []
    return [
        Problem(
            SEVERITY_ERROR,
            "(ticket)",
            f"承認済みチケットの置き場（{conf.approved}）が"
            f"ccnavi ディレクトリ（{conf.project_home}）の外にある。"
            "組み込みの保護が及ばないので、エージェントが承認済みチケットを書き換えられ、"
            "承認が意味を持たない",
        )
    ]


def _approval_problems(
    root: str,
    conf: settings.Settings,
    proposals: list,
    copies: list,
    closed: list,
    review: list,
) -> list[Problem]:
    """承認で落ちるものを、承認の前に名指しする。ユーザが端末で初めて知るより早く。

    **承認と同じ関数を通す**（`agree_candidates.candidates`）。ここだけ
    `agree_candidates.validate` を当てる形にすると、順序で落ちる子（前のフェーズが閉じていない）・
    計画に無い番号・`project:` の食い違い・改版の検査が抜ける。同じ事実を数える経路が 2 本あると、
    片方が気づかないうちに弱くなる。`--agree --preview --verify` と同じ答えをここでも言う。

    範囲の超過は承認では落ちないが、判定で止まるので同じく名指しする（warn）。

    **「まだ承認できない」だけは warn にする。** 前のフェーズが閉じていない子
    （`rules.KIND_NOT_YET`）は、書いた側に直すものが無く、前が閉じれば同じ提案が通る。
    `--lint` はワークスペース全体を見る道具で、その終了コードは VS Code の設定画面が
    保存してよいかの判断にも使われる（`phases-panel.ts`）。ここを error にすると、
    編集と関わりのない提案 1 本で、設定の保存も CI も止まる。承認そのものは落とす
    （`agree_candidates.candidates` の側は error のまま）ので、緩むのは報告の重さだけ。
    """
    pending, revisions = agree.waiting(
        proposals,
        copies,
        closed,
        review,
        agree_candidates.types_resolver(conf, root, copies),
    )
    if not pending and not revisions:
        return []
    batch, rejected, _pool = agree_candidates.candidates(root, conf, pending, revisions, copies)
    problems: list[Problem] = []
    for cand in batch:
        for p in cand.complaints + cand.overflow:
            problems.append(_said(cand.ticket.ticket, p))
    for t, complaints in rejected:
        for p in complaints:
            problems.append(_said(t.ticket, p))
    return problems


def _said(ticket: str, problem) -> Problem:
    """承認の苦情 1 件を、`--lint` の言い方に直す。「まだ承認できない」は warn にする。"""
    severity = SEVERITY_WARN if problem.kind == rules.KIND_NOT_YET else problem.severity
    return Problem(severity, "(ticket)", f"{ticket}: {problem.detail}")


def _proposal_problems(
    proposals: list,
    copies: list,
    index: dict,
    closed: list,
    done: set[str],
    repo_of: dict[str, str],
    preds: dict | None = None,
    root: str = "",
) -> list[Problem]:
    """提案の側。承認待ち、先行が閉じていない着手済み、同じ識別子の重複。

    承認で落ちるものは `_approval_problems` が言う（承認と同じ関数を通す）。

    `todo/` に在るものは全部承認待ち。同じ識別子がどこかの置き場（作業中・レビュー待ち・
    閉じた）に在れば、親の改版でない限り書き損じなので名指しする。

    `closed` は `done/` の承認済みチケット。`proposals` は `todo/` と `review/`。作業中と
    レビュー待ちと閉じたの 3 つを横断して数えないと、`doing/` と `done/` に同じ識別子が
    在る形（動かす途中で止まった形跡）を CI が見逃し、状態の操作が「複数の場所にある」で
    止まって初めて知ることになる。

    **同じリポジトリの中ではツリーごとに数える。** 承認済みチケットは git に入れて共有するので、
    切ったワークツリーの数だけ同じチケットができる。しかもワークツリーはそれぞれ別のコミットを指すので、
    古いほうで `doing`・新しいほうで `done` になるのも普通の形。ツリーをまたいだ食い違いまで
    error にすると、ワークツリーを 2 本持つだけで閉じたチケットが全部 error になり、`--lint` が
    常に非ゼロで終わる。捕まえたいのは 1 つのツリーの中で 2 つの状態に在る形だけ。

    **リポジトリをまたいだら、状態が何であれ error にする。** プロジェクトは自分の git を持つので
    （設計 11）、そこに同じ識別子が在るのは同じチケットではなく違うチケットどうしの衝突。
    識別子はユーザが選ぶ短い連番で、プロジェクトが独立に振れば重なる。コミットの遅れでは説明が付かないから、
    ツリーごとの免除を当ててはいけない。
    """
    problems: list[Problem] = []

    # ツリーと状態は分けて持つ。同じ識別子が別のツリーに在るのは普通で（承認済みチケットは
    # git に入れて共有するので、切ったワークツリーの数だけ同じチケットができる）、
    # しかもワークツリーは
    # それぞれ別のコミットを指すから、古いほうが doing・新しいほうが done になるのも普通。
    # error にするのは 1 つのツリーの中で 2 つの状態に在る形だけ。
    def place(t, state: str) -> tuple[str, str, str]:
        at = t.tree or "(ワークスペースルート)"
        return (repo_of.get(at, at), at, state)

    seen: dict[str, list[tuple[str, str, str]]] = {}
    # チケットそのものも識別子ごとに持つ。どれが本物か決まるかの判断は `ticket_fold.collisions` が
    # 決め、ボードの `scattered` と状態の操作が止まる条件に揃える（同じ答えを 2 か所で
    # 出さない）。数えるのは `index`（識別子ごとに 1 つ）ではなく全部。同じ識別子が 2 つ
    # 残っているのがまさに言いたい形なので、引き当ての表で数えると自分でまとめてしまう。
    held: dict[str, list] = {}
    for t in copies:
        seen.setdefault(t.ticket, []).append(place(t, t.state or ticket_model.DOING))
        held.setdefault(t.ticket, []).append(t)
    for t in closed:
        seen.setdefault(t.ticket, []).append(place(t, t.state or ticket_model.DONE))
        held.setdefault(t.ticket, []).append(t)
    for t in proposals:
        seen.setdefault(t.ticket, []).append(place(t, t.state))
        held.setdefault(t.ticket, []).append(t)
        if t.state != ticket_model.TODO:
            continue
        if t.ticket in index:
            current = index[t.ticket]
            if not t.is_child and t.has_plan and approval.plan_differs(t, current):
                continue  # 親の改版。承認待ちに入る
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"{t.ticket} は承認済み（{current.tree or '(ワークスペースルート)'} の "
                    f"{current.state or ticket_model.DOING}/）なのに todo/ にも在る"
                    f"（{_rel(root, t.path)}）。"
                    "計画の改版でなければ todo/ の側を消してください",
                )
            )
            continue
        if t.ticket in done:
            problems.append(
                Problem(SEVERITY_WARN, "(ticket)", _closed_todo_text(t.ticket, _rel(root, t.path)))
            )
            continue
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(ticket)",
                f"{t.ticket} は承認待ち（{t.tree or '(ワークスペースルート)'} の todo/）。"
                "'ccnavi --agree' を通すまで範囲は効かない",
            )
        )
    for t in index.values():
        if t.started_at:
            # 先行の数え方は承認と着手と同じ（`done/` に在って取り消しでないものだけ満たす）。
            # 着手はこれで止まるので、ここに出るのは
            # 着手のあとに先行が動いたか、止める前の版で着手したもの。
            unmet = approval_checks.unmet_predecessors(t, preds or {})
            if unmet:
                names = ", ".join(f"{p.ticket}（{p.label}）" for p in unmet)
                problems.append(
                    Problem(
                        SEVERITY_WARN,
                        "(ticket)",
                        f"{t.ticket} は先行 {names} を満たしていないのに着手している",
                    )
                )
    for ticket_id, places in seen.items():
        # 別のリポジトリに同じ識別子が在るのは、同じチケットではなく**違うチケットどうしの衝突**。
        # 識別子はユーザが選ぶ短い連番なので、プロジェクトが独立に振れば普通に重なる。
        # コミットの遅れでは説明できないので、状態が何であれ error にする。
        repos = sorted({repo for repo, _, _ in places})
        if len(repos) > 1:
            where = ", ".join(
                # ツリーの名前がリポジトリの名前と同じなら（プロジェクトの元ツリー）、
                # 2 度書かない。切ったワークツリーなら「どのリポジトリのどのツリーか」を出す。
                f"{repo or '(ワークスペース)'}:{state}"
                if at == repo
                else f"{repo or '(ワークスペース)'}/{at}:{state}"
                for repo, at, state in sorted(places)
            )
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    "(ticket)",
                    f"{ticket_id} が複数のリポジトリにある: {where}。"
                    "識別子はリポジトリごとに一意にしてください",
                )
            )
            continue
        # 本物とするツリーでまとめて 2 つ以上残る形（状態の操作が止まる）と、その中で 2 つの
        # 置き場に在る形（動かす途中で止まった形跡）。`todo/` に在るのは親の改版の途中なので
        # error にしない。ツリーをまたいだチケットはまとめれば 1 つに決まるので、
        # ここには出てこない。
        caught = ticket_fold.collisions(held[ticket_id])
        if not caught:
            continue
        where = ", ".join(
            f"{t.tree or '(ワークスペースルート)'}:{t.state}"
            for t in sorted(caught, key=lambda t: (t.tree, t.state))
        )
        problems.append(
            Problem(SEVERITY_ERROR, "(ticket)", f"{ticket_id} が複数の場所にある: {where}")
        )
    return problems


def _rel(root: str, path: str) -> str:
    """名指しに使う、ワークスペースルートからの相対パス（`/` 区切り）。"""
    if not root or not path:
        return path
    return os.path.relpath(path, root).replace(os.sep, "/")


def _stale_problems(
    root: str, conf: settings.Settings, stale: list, index: dict, done: set[str]
) -> list[Problem]:
    """本物とするツリーの外に残った、承認済みの識別子の `todo/` の提案。

    `approval.stale_proposals` が返すもの。

    承認の対象にもボードにも入らない（本物とするツリーの側だけを読む）。名指しするのは 2 つだけ。

    - 計画の違う親の版。本物としないツリーに書いた改版か、改版の前に切ったワークツリーに
      残った古い版（巻き戻しの元）。書く場所を案内する（改版は本物とするツリーの `todo/` に
      置く）。文面は `--agree` と同じ（`approval.revision_elsewhere_text`）
    - 閉じたかレビュー待ちの識別子の `todo/`。前から出していた再開の案内を、本物とするツリーの
      外に在っても同じく出す

    計画の同じ古い提案は言わない。承認の前に切ったワークツリーに残るのは普通の形で、言うと
    ワークツリーを持つだけで WARN が並ぶ。
    """
    problems: list[Problem] = []
    for t, where in stale:
        current = index.get(t.ticket)
        if approval.revision_elsewhere(t, current):
            text = approval.revision_elsewhere_text(conf, root, t, where)
        elif current is None and t.ticket in done:
            text = _closed_todo_text(t.ticket, _rel(root, t.path))
        else:
            continue
        problems.append(Problem(SEVERITY_WARN, "(ticket)", text))
    return problems


def _closed_todo_text(ticket_id: str, rel: str) -> str:
    """閉じたかレビュー待ちの識別子が `todo/` にも在るときの案内。"""
    return (
        f"{ticket_id} は閉じたかレビュー待ちなのに todo/ にも在る（{rel}）。"
        "todo/ の側は承認の対象にならない。再開するには、"
        "ユーザが承認済みチケットを戻す"
    )


def _review_token(root: str) -> list[Problem]:
    """sh がリモートを読み書きできる形か。gh / glab か、curl とホストに合うトークン。"""
    rc, out = gitcmd.output(root, ["remote", "get-url", "origin"], 5)
    url = out.strip() if rc == 0 else ""
    if not url:
        return [Problem(SEVERITY_WARN, "(ticket)", "origin が無い。レビューの依頼と確認は動かない")]
    problem = review_host.transport_problem(url)
    if problem:
        return [Problem(SEVERITY_WARN, "(ticket)", f"{problem}。レビューの依頼と確認は動かない")]
    return []


TICKET_SCRIPTS = ("ccnavi-ticket.sh", "ccnavi-review.sh", "ccnavi-git.sh")


def _ticket_hooks(root: str) -> list[Problem]:
    """親子の運用に要る hook の登録とスクリプトが揃っているか。"""
    problems = []
    for event, what in (
        (hookio.SUBAGENT_START, "サブエージェントに開いている子の一覧を渡せない"),
        (hookio.SUBAGENT_STOP, "範囲外の変更を残したサブエージェントを差し戻せない"),
    ):
        if lint_project._registered(root, event) is False:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(project)",
                    f"{lint_project.PROJECT_SETTINGS} の {event} に"
                    f" ccnavi が登録されていない。{what}",
                )
            )
    for name in TICKET_SCRIPTS:
        path = os.path.join(root, ".ccnavi", "scripts", name)
        if not os.path.isfile(path):
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(project)",
                    f".ccnavi/scripts/{name} が無い。レビュー済みのマーカーが置かれるまで"
                    "シェル実行を止めている間、例外として通るはずのこの sh の操作もできなくなる",
                )
            )
    return problems


def tree_has_tickets(root: str, tickets_rel: str) -> bool:
    """ワークスペースルートかワークツリーのどこかに提案の置き場があるか。"""
    for t in [tree.main_tree(root), *tree.worktrees(root)]:
        if os.path.isdir(os.path.join(t.root, tickets_rel.replace("/", os.sep))):
            return True
    return False


def _stray_claude_dirs(root: str, worktree_names: set[str]) -> list[str]:
    """リポジトリの中で `.claude/` を持つ、ワークツリーでもワークスペースルートでもない
    ディレクトリ。

    浅くしか見ない。2 段まで。深くたどると大きなリポジトリで検証が待たされる。
    """
    found = []
    skip = {".git", ".claude", "node_modules", ".venv", "dist", "build"}
    try:
        first = sorted(os.listdir(root))
    except OSError:
        return found
    for name in first:
        top = os.path.join(root, name)
        if name in skip or not os.path.isdir(top):
            continue
        candidates = [top]
        with contextlib.suppress(OSError):
            candidates += [os.path.join(top, n) for n in sorted(os.listdir(top))]
        for candidate in candidates:
            if os.path.isdir(os.path.join(candidate, ".claude")):
                found.append(os.path.relpath(candidate, root))
    return found
