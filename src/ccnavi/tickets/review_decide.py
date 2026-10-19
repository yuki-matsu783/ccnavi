"""残った指摘の行き先を決める。`ccnavi --reviewed` の決め方と `ccnavi review decide`。

レビューで残った指摘を、ユーザが指摘ごとに「このまま・直す・issue に回す」から選ぶ。
選んだ結果の下書き（コメントと issue）を state の置き場に書き、sh がそれを投稿する。
下書きのファイル名・目印・`--json` の形は sh とボードとの契約で、値と形を変えない。
review から分けた。review を読む末端で、review からは読まれない。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from dataclasses import dataclass
from typing import TextIO

from ..infra import fsio, settings, tree
from . import (
    approval,
    approval_marks,
    approval_ops,
    phase,
    review,
    review_host,
    standing,
    ticket_model,
    via,
)

DECIDE_FILE = "review-decide-{parent}-{phase}.md"
# 残った指摘のうち、ユーザが issue に回すと選んだ分の下書き。sh がこれで issue を作る。
DECIDE_ISSUE_FILE = "review-issue-{parent}-{phase}.md"


# 残った指摘の行き先。ユーザが指摘ごとに選ぶ。
CHOICE_KEEP = "keep"
CHOICE_FIX = "fix"
CHOICE_ISSUE = "issue"
CHOICES = (CHOICE_KEEP, CHOICE_FIX, CHOICE_ISSUE)
# ボードと取り交わす JSON の版。形を変えたら上げる（拡張の `decidemodel.ts` と揃える）。
DECIDE_VERSION = 1
# 端末で打つ 1 文字と、画面に出す呼び名。
_CHOICE_KEYS = {"k": CHOICE_KEEP, "f": CHOICE_FIX, "i": CHOICE_ISSUE}
CHOICE_LABELS = {
    CHOICE_KEEP: "対応しない（受け入れて進む）",
    CHOICE_FIX: "このフェーズで直す（続きの子チケットを起こす）",
    CHOICE_ISSUE: "issue に回す（受け入れて、別の issue に切り出す）",
}


def _followup_from_choice(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    parent: ticket_model.Ticket,
    ph: phase.Phase,
    items: list[str],
) -> str | None:
    """ユーザが選んだ続きの子を `doing/` に起こし、識別子を返す。起こせなければ None。"""
    children = [t for t in ph.tickets if ph.states.get(t.ticket) in ticket_model.FINISHED]
    ident, failed = approval_ops.followup(conf, root, parent, ph.number, children, items, stderr)
    if failed:
        stderr.write(f"ccnavi: 続きの子チケットを起こせない: {failed}\n")
        return None
    stdout.write(
        f"続きの子チケット {ident} を {conf.approved}/{ticket_model.DOING}/ に起こした"
        f"（フェーズ {ph.number}、範囲は見た子の和、本文に指摘 {len(items)} 件）。"
        "フェーズは再び開かれ、マーカーは消えた。\n"
        f"次は{_followup_next(root, parent, ident)}\n"
    )
    for line in phase.reopen_impact(root, conf, parent.ticket, ph.number):
        stdout.write(line + "\n")
    return ident


def _status_line(root: str, parent: ticket_model.Ticket) -> str:
    """前の会話が無い新しいセッションでも、状況の確かめ方が分かるようにする 1 文。"""
    ticket_sh = settings.script_command(root, "ccnavi-ticket.sh")
    return (
        f"前の会話が無くても、状況は '{ticket_sh} status {parent.ticket}' で確かめる"
        "（置き場のファイルを自分で探さない）。"
    )


def _followup_next(root: str, parent: ticket_model.Ticket, ident: str) -> str:
    """続きの子を起こしたあと、エージェントが打つ 2 手。"""
    git_sh = settings.script_command(root, "ccnavi-git.sh")
    ticket_sh = settings.script_command(root, "ccnavi-ticket.sh")
    if tree.has_origin(root):
        # origin があれば、start がワークツリーを切る。手で切る手順は添えない。
        return f"エージェントが '{ticket_sh} start {ident}' で着手する"
    base = standing.head_branch(tree.worktree_path(root, parent.ticket), parent.ticket)
    return (
        f"エージェントが '{git_sh} worktree add .claude/worktrees/{ident} -b {ident} "
        f"{base or '<親のブランチ>'}' でワークツリーを切り、'{ticket_sh} start {ident}' で"
        "着手する"
    )


@dataclass
class Decision:
    """残った指摘を決める前の、見せる材料。preview と適用が同じものから組む。"""

    parent: ticket_model.Ticket
    ph: phase.Phase
    result: review_host.Result
    unresolved: list[review_host.Thread]
    # issue に回せるか。回せるのはフィードバック計画が承認されたあと。
    can_issue: bool


def decision_digest(d: Decision) -> str:
    """見せた指摘のダイジェスト。見せてから押すまでに指摘が増えた・変わったら、適用を止める。

    部分ごとの SHA-256 を件数と一緒に並べ、その全体の SHA-256。
    区切りでつなぐと、本文に区切りを書いてつなぎ目をずらせる。
    """
    assert d.result.mr is not None
    parts = [
        d.parent.ticket,
        str(d.ph.number),
        str(d.result.mr.number),
        d.result.mr.url,
        str(d.can_issue),
    ]
    for t in d.unresolved:
        parts.extend([review.thread_key(t), t.path, str(t.line), t.body])
    lines = [str(len(parts))] + [hashlib.sha256(p.encode("utf-8")).hexdigest() for p in parts]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _decision(
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    phase_no: int,
    result_path: str,
    carry: frozenset[str] = frozenset(),
) -> Decision | None:
    """残った指摘を決められる状態かを確かめ、見せる材料を組む。決められなければ None。

    `carry` は受け入れ済みのスレッドの id（`--accept-done`）。sh が返信と解決をしたあとの結果では
    解決済みになっているが、見せた指摘の一部として数え直す（見せたときと同じ並びでダイジェストを
    組み直し、見せたあとに指摘が増えたり変わったりしていないことを確かめるため）。
    """
    found = review._parent_phase(stderr, root, conf, cwd, phase_no)
    if found is None:
        return None
    parent, ph = found
    requested_mark = ph.marks.get(approval_marks.MARK_REQUESTED)
    if requested_mark is None:
        stderr.write("ccnavi: 依頼の記録がありません。先に request してください\n")
        return None
    tree_root = tree.worktree_path(root, parent.ticket)
    moved = review._moved_since_request(tree_root, conf, requested_mark)
    if moved:
        stderr.write(f"ccnavi: {moved}。{review._redo_request(root, phase_no)}\n")
        return None
    result = review._matching(stderr, result_path, requested_mark)
    if result is None:
        return None
    if any(
        r.state.upper() == review_host.CHANGES_REQUESTED
        for r in review_host.effective(result.reviews)
    ):
        stderr.write(
            "ccnavi: 変更要求のレビューが立っている。decide でも通せない。"
            "変更要求があるため、レビュー済みにはできません。修正する場合は同じフェーズに"
            "新しい子を提案し、ユーザの承認を受けてください。修正・再依頼のあと、"
            "レビュアーの approve（または dismiss）を待ちます\n"
        )
        return None
    open_now = {
        id(t)
        for t in review_host._unresolved(
            result.threads, result.host, str(requested_mark.get("poster") or "")
        )
    }
    unresolved = [t for t in result.threads if id(t) in open_now or (t.id and t.id in carry)]
    can_issue = parent.has_plan and parent.feedback is not None
    return Decision(parent, ph, result, unresolved, can_issue)


def _decision_json(d: Decision) -> dict:
    assert d.result.mr is not None
    return {
        "version": DECIDE_VERSION,
        "parent": d.parent.ticket,
        "phase": d.ph.number,
        "mr": {"number": d.result.mr.number, "url": d.result.mr.url},
        "can_issue": d.can_issue,
        "threads": [
            {
                "key": review.thread_key(t),
                "url": t.url,
                "path": t.path,
                "line": t.line,
                "body": t.body,
            }
            for t in d.unresolved
        ],
        "digest": decision_digest(d),
    }


def reviewed(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    phase_no: int,
    accept_unresolved: bool,
    result_path: str,
) -> int:
    """ユーザが端末か VS Code のボードで行う。

    残った指摘を見せ、1 件ずつ行き先を選ばせる。変更要求は通せない。

    選んだ結果は `apply_decision` が置き、MR に書き込むコメントと issue の下書きを state の置き場に
    書き出す。sh がそれを投稿する。
    """
    if not accept_unresolved:
        stderr.write(
            "ccnavi: 未解決を受け入れるなら --accept-unresolved を付けてください。"
            "受け入れないなら "
            f"'{settings.script_command(root, 'ccnavi-review.sh')} confirm' で足りる\n"
        )
        return 1
    d = _decision(stderr, root, conf, cwd, phase_no, result_path)
    if d is None:
        return 1
    if not d.unresolved:
        # 選ぶものが無い。選び方の案内は出さず、レビュー済みにするだけ
        stdout.write(
            f"フェーズ {phase_no}（親 {d.parent.ticket}）で未解決（Unresolved）の指摘なし\n"
        )
        summary = apply_decision(stdout, stderr, root, conf, d, {})
        return 0 if summary is not None else 1
    stdout.write(
        f"フェーズ {phase_no}（親 {d.parent.ticket}）で未解決（Unresolved）の指摘: "
        f"{len(d.unresolved)} 件\n"
    )
    choices = _ask_choices(stdin, stdout, stderr, d)
    if choices is None:
        return 1
    summary = apply_decision(stdout, stderr, root, conf, d, choices)
    return 0 if summary is not None else 1


def choose(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    phase_no: int,
    result_path: str,
    out_path: str,
) -> int:
    """対話の decide の前半（`--reviewed N --accept-unresolved --choose-out <ファイル>`）。

    残った指摘を見せて 1 件ずつ選ばせ、
    選択とダイジェストを `{"choices": …, "digest": …}` で書くだけ。
    置くのは sh が write-and-push の中で `--yes <選択> --digest <ダイジェスト>` で打つ
    （選ぶのを write-and-push のロックの外で済ませ、ロックを持ったままユーザを待たない）。
    """
    d = _decision(stderr, root, conf, cwd, phase_no, result_path)
    if d is None:
        return 1
    choices: dict[str, str] = {}
    if d.unresolved:
        stdout.write(
            f"フェーズ {phase_no}（親 {d.parent.ticket}）で未解決（Unresolved）の指摘: "
            f"{len(d.unresolved)} 件\n"
        )
        picked_all = _ask_choices(stdin, stdout, stderr, d)
        if picked_all is None:
            return 1
        choices = picked_all
    else:
        stdout.write(
            f"フェーズ {phase_no}（親 {d.parent.ticket}）で未解決（Unresolved）の指摘なし\n"
        )
    failed = fsio.write_text(
        out_path,
        json.dumps({"choices": choices, "digest": decision_digest(d)}, ensure_ascii=False) + "\n",
    )
    if failed:
        stderr.write(f"ccnavi: 選んだものを {out_path} に保存できませんでした（{failed}）\n")
        return 1
    return 0


def _ask_choices(
    stdin: TextIO, stdout: TextIO, stderr: TextIO, d: Decision
) -> dict[str, str] | None:
    """残った指摘の行き先を 1 件ずつ端末で選ばせる。決めなければ None。"""
    keys = "k / f / i" if d.can_issue else "k / f"
    stdout.write(
        "未解決（Unresolved）の指摘の対応方針を 1 件ずつ選びます。"
        "選択肢以外を入力すると、何もせずにやめます。\n"
    )
    for letter, choice in _CHOICE_KEYS.items():
        if choice == CHOICE_ISSUE and not d.can_issue:
            continue
        stdout.write(f"  {letter}  {CHOICE_LABELS[choice]}\n")
    if not d.can_issue:
        stdout.write("  （issue に回せるのは、フィードバック計画が承認されたあとです）\n")
    choices: dict[str, str] = {}
    for i, t in enumerate(d.unresolved, 1):
        stdout.write(f"[{i}/{len(d.unresolved)}] {review.thread_label(t)}\n")
        stdout.write(f"選択（{keys}）: ")
        stdout.flush()
        picked = _CHOICE_KEYS.get(fsio.read_line(stdin).strip().lower(), "")
        if not picked or (picked == CHOICE_ISSUE and not d.can_issue):
            stderr.write("ccnavi: 決めなかった。何も置いていない\n")
            return None
        choices[review.thread_key(t)] = picked
    return choices


def decide_preview(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    phase_no: int,
    result_path: str,
) -> int:
    """残った指摘を見せる（`--reviewed N --accept-unresolved --preview --json`）。

    ボードのオーバーレイが読む。適用はこの `digest` を `--digest` で返したときだけ通る。
    """
    d = _decision(stderr, root, conf, cwd, phase_no, result_path)
    if d is None:
        return 1
    stdout.write(json.dumps(_decision_json(d), ensure_ascii=False) + "\n")
    return 0


def decide_yes(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    phase_no: int,
    result_path: str,
    choices_text: str,
    digest: str,
    plan_out: str = "",
    plan_done: str = "",
) -> int:
    """オーバーレイでユーザが押した選択を置く。

    形は `--yes <選択の JSON> --digest <ダイジェスト> --json`。

    受け入れる指摘（対応しない・issue に回す）には、置く前に sh が返信を残して解決する。
    実行ファイルはネットワークに出ないので、3 回に分けて呼ばれる。

    1. `plan_out`（`--accept-plan <ファイル>`）あり: 何も置かずに受け入れの計画だけを書く。
       検査は置くときと同じ（ダイジェスト、選択、置く側の事前検査）。失敗なら計画を書かない
    2. sh が計画のスレッドに返信を残して解決し、結果を取り直す
    3. `plan_done`（`--accept-done <ファイル>`）あり: 取り直した結果を読み、受け入れたスレッドが
       全部ホストで解決済みであることを確かめ、受け入れたスレッドを見せた指摘に数え直して
       ダイジェストを照合し（見せたあとに指摘が変われば止まる）、置く

    ユーザが端末で打つという制約の代わりに、見せた指摘と今の指摘のダイジェストが一致することを
    求める。エージェントがこれをシェルで打つ形は、組み込みの deny
    （`phase_forms.ticket_approval_rule`）が止める。
    結果は JSON で返す。違えば何も置かず `mismatch` を返す。
    """
    if not digest.strip():
        stderr.write("ccnavi: --yes には --digest（見せた指摘のダイジェスト）が必要です。\n")
        return 1
    try:
        raw = json.loads(choices_text)
    except ValueError:
        stderr.write("ccnavi: --yes の選択を JSON として読めない\n")
        return 1
    if not isinstance(raw, dict) or not all(
        isinstance(k, str) and v in CHOICES for k, v in raw.items()
    ):
        stderr.write(f"ccnavi: --yes の選択は {{鍵: {' / '.join(CHOICES)}}} の形で渡す\n")
        return 1
    planned = None
    if plan_done:
        planned = review_host.read_accept_plan(plan_done)
        if planned is None:
            stderr.write(f"ccnavi: 受け入れの計画 {plan_done} を読めない。何も置いていない\n")
            return 1
    d = _decision(
        stderr,
        root,
        conf,
        cwd,
        phase_no,
        result_path,
        frozenset(planned.ids) if planned is not None else frozenset(),
    )
    if d is None:
        return 1
    current = decision_digest(d)
    if digest.strip().lower() != current:
        stdout.write(
            json.dumps(
                {
                    "version": DECIDE_VERSION,
                    "ok": False,
                    "mismatch": True,
                    "digest": {"expected": digest, "current": current},
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        stderr.write("ccnavi: 見せた指摘と今の指摘が違う。何も置いていない\n")
        return 1
    shown = {review.thread_key(t) for t in d.unresolved}
    if set(raw) != shown:
        stderr.write("ccnavi: 選択が見せた指摘と揃っていない（足りないか、余分がある）\n")
        return 1
    if not d.can_issue and CHOICE_ISSUE in raw.values():
        stderr.write("ccnavi: issue に回せるのは、フィードバック計画が承認されたあと\n")
        return 1
    picked = {c: [t for t in d.unresolved if raw.get(review.thread_key(t)) == c] for c in CHOICES}
    if not _precheck(stderr, root, conf, d, picked):
        return 1
    if plan_out:
        return _write_plan(stderr, d, raw, plan_out, current)
    if planned is not None:
        problem = _plan_problem(d, raw, planned)
        if problem:
            stderr.write(f"ccnavi: 受け入れが済んでいない: {problem}。何も置いていない\n")
            return 1
    notes = io.StringIO()
    summary = apply_decision(notes, stderr, root, conf, d, raw)
    if summary is None:
        return 1
    stdout.write(
        json.dumps({"version": DECIDE_VERSION, "ok": True, **summary}, ensure_ascii=False) + "\n"
    )
    return 0


def _accepted_threads(d: Decision, choices: dict[str, str]) -> list[tuple[review_host.Thread, str]]:
    """受け入れる指摘（対応しない・issue に回す）と、スレッドに残す返信の本文。"""
    why = f"フェーズ {d.ph.number} の未解決の指摘を、ユーザが decide で決めた"
    out = []
    for t in d.unresolved:
        choice = choices.get(review.thread_key(t))
        if choice in (CHOICE_KEEP, CHOICE_ISSUE):
            out.append((t, review_host.accept_reply(why, issue=choice == CHOICE_ISSUE)))
    return out


def _plan_problem(d: Decision, choices: dict[str, str], planned: review_host.AcceptPlan) -> str:
    """計画と、sh が取り直した結果が合うか。合わなければ説明。

    受け入れる指摘（選択が対応しない・issue に回す）と計画のスレッドが同じで、そのスレッドが
    全部ホストで解決済みであること（ホストの実状態で確かめる）。
    """
    want = sorted(review.thread_key(t) for t, _ in _accepted_threads(d, choices))
    if sorted(planned.keys) != want:
        return "計画のスレッドと選択が合わない"
    by_id = {t.id: t for t in d.result.threads if t.id}
    for ident in planned.ids:
        t = by_id.get(ident)
        if t is None:
            return f"スレッド {ident} が結果に無い"
        if not t.resolved:
            return f"スレッド {review.thread_key(t)} がホストで解決済みになっていない"
    return ""


def _write_plan(
    stderr: TextIO, d: Decision, choices: dict[str, str], out_path: str, digest: str
) -> int:
    """受け入れの計画を書く。受け入れる指摘が無くても、空の計画を書く（sh は何もしない）。"""
    items = _accepted_threads(d, choices)
    problem = review_host.accept_plan_problem([t for t, _ in items])
    if problem:
        stderr.write(f"ccnavi: 受け入れを始められない: {problem}\n")
        return 1
    failed = review_host.write_accept_plan(out_path, items, digest)
    if failed:
        stderr.write(f"ccnavi: 受け入れの計画を {out_path} に保存できませんでした（{failed}）\n")
        return 1
    return 0


def _decide_actor() -> tuple[str, str]:
    """decide のマーカーに入れる (アカウント, 経路)。

    アカウントは ccnavi-review.sh がトークンの持ち主を引いて `--actor` で渡したもの。
    経路はこの起動の経路（端末は `terminal`、ボードは `board`）。アカウントが無ければ
    どちらも書かない（confirm と同じく、引けなければマーカーは前と同じバイト列）。
    """
    account = via.actor()
    return (account, via.via()) if account else ("", "")


def _precheck(
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    d: Decision,
    picked: dict[str, list[review_host.Thread]],
) -> bool:
    """置く前に、ローカルで失敗しうることを確かめる。通れば True。

    ホストに触る前（`--accept-plan` の段）にも呼ぶ。受け入れの返信と解決を済ませてから
    「置けない」と分かると、解決済みのスレッドだけが残るため。
    """
    parent, ph = d.parent, d.ph
    fix = picked[CHOICE_FIX]
    # issue に回す分は、state の置き場に下書きを書いて sh に渡す。置き場が無ければ回せないので、
    # 何も置く前に断る（受け入れだけが残り、issue は作られない、にしない）
    if picked[CHOICE_ISSUE] and not conf.state:
        stderr.write("ccnavi: state の置き場が空。issue に回す下書きを置けない\n")
        return False
    # 直す指摘があるなら、起こす続きの子がチケットとして読めるかを、何も置く前に確かめる。
    # 子が多いと範囲の和が上限（20 件）を超え、「起こした」のに後で読み込めない子が残る。
    if fix:
        settled = [t for t in ph.tickets if ph.states.get(t.ticket) in ticket_model.FINISHED]
        problem = approval_ops.followup_problem(
            conf,
            root,
            parent,
            ph.number,
            settled or list(ph.tickets),
            [_thread_line(t) for t in fix],
        )
        if problem:
            stderr.write(f"ccnavi: 続きの子チケットを起こせない: {problem}\n")
            return False
    return True


def apply_decision(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    d: Decision,
    choices: dict[str, str],
) -> dict | None:
    """選んだ行き先を置く。端末とオーバーレイが同じここを通る。置けなければ None。

    - 対応しない・issue に回す: 受け入れる。スレッドへの返信と解決は、置く前に sh が済ませてある
      （`--accept-plan`）。次の confirm は、ホストで解決済みのスレッドを数えない
    - このフェーズで直す: 続きの子を同じ番号で `doing/` に起こす。受け入れないので、次の
      confirm がまた数える。1 件でもあればフェーズは再び開かれ、レビュー済みのマーカーは置かない
    - どれも直さないなら、レビュー済みのマーカーを置く

    MR に書き込むコメントと issue の下書きは state の置き場に書き、sh が投稿する。
    """
    assert d.result.mr is not None
    parent, ph = d.parent, d.ph
    home = approval.home_dir(conf, root, parent.ticket, "", project=parent.project)
    picked = {
        c: [t for t in d.unresolved if choices.get(review.thread_key(t)) == c] for c in CHOICES
    }
    accepted = [review.thread_key(t) for t in picked[CHOICE_KEEP] + picked[CHOICE_ISSUE]]
    fix = picked[CHOICE_FIX]
    if not _precheck(stderr, root, conf, d, picked):
        return None
    # 前の回の下書き（投稿に失敗して残ったもの）は捨てる。残すと、この回に選んでいない
    # issue やコメントを sh が投稿する
    if conf.state:
        for name in (DECIDE_FILE, DECIDE_ISSUE_FILE):
            stale = os.path.join(conf.state, name.format(parent=parent.ticket, phase=ph.number))
            if os.path.exists(stale):
                fsio.remove(stale)
    if not review._settle_children(stdout, stderr, root, conf, parent, ph):
        return None
    followup = ""
    if fix:
        items = [_thread_line(t) for t in fix]
        ident = _followup_from_choice(stdout, stderr, root, conf, parent, ph, items)
        if ident is None:
            return None
        followup = ident
    elif not review._mark(
        stderr,
        home,
        parent.ticket,
        ph.number,
        approval_marks.MARK_REVIEWED,
        review.reviewed_mark(d.result.mr.number, accepted, *_decide_actor()),
    ):
        return None
    issue_draft = ""
    if picked[CHOICE_ISSUE]:
        issue_draft = _write_decide_issue(stderr, conf, d, picked[CHOICE_ISSUE])
    if conf.state:
        _write_decide_comment(stderr, conf, d, picked, followup)
    if followup:
        stdout.write(f"OK: フェーズ {ph.number} は続きの子 {followup} で応える\n")
    else:
        stdout.write(
            f"OK: フェーズ {ph.number} はレビュー済み（未解決 {len(accepted)} 件を受け入れた）\n"
            if accepted
            else f"OK: フェーズ {ph.number} はレビュー済み（未解決（Unresolved）の指摘なし）\n"
        )
    return {
        "parent": parent.ticket,
        "phase": ph.number,
        "reviewed": not followup,
        "followup": followup,
        "kept": [review.thread_key(t) for t in picked[CHOICE_KEEP]],
        "fix": [review.thread_key(t) for t in fix],
        "issue": [review.thread_key(t) for t in picked[CHOICE_ISSUE]],
        "issue_draft": issue_draft,
        "prompt": _decided_prompt(root, d, picked, followup),
    }


def _thread_line(t: review_host.Thread) -> str:
    return review.thread_label(t)


def _write_decide_issue(
    stderr: TextIO, conf: settings.Settings, d: Decision, threads: list[review_host.Thread]
) -> str:
    """issue に回す指摘の下書き。1 行目が題、空行のあとが本文。書けなければ空。"""
    assert d.result.mr is not None
    text = [
        f"レビューで残った指摘（{d.parent.ticket} のフェーズ {d.ph.number}）",
        "",
        f"元のマージリクエスト: {d.result.mr.url}（チケット `{d.parent.ticket}`）",
        "",
        "## 引き継ぐ指摘",
        "",
        *[f"- {_thread_line(t)}" for t in threads],
        "",
    ]
    path = os.path.join(
        conf.state, DECIDE_ISSUE_FILE.format(parent=d.parent.ticket, phase=d.ph.number)
    )
    failed = fsio.write_text(path, "\n".join(text), newline="\n")
    if failed:
        stderr.write(f"ccnavi: issue の下書きを書き出せない ({failed})\n")
        return ""
    return path


def _write_decide_comment(
    stderr: TextIO,
    conf: settings.Settings,
    d: Decision,
    picked: dict[str, list[review_host.Thread]],
    followup: str,
) -> None:
    """MR に書き込む、決めた内容のコメント。issue の表記は sh が作ったあとに書き足す。"""
    lines = [
        review_host.MARKER_DECIDE,
        f"フェーズ {d.ph.number} の未解決（Unresolved）指摘の対応方針:",
    ]
    if picked[CHOICE_KEEP]:
        lines += [
            "",
            "対応しない（受け入れて進む）:",
            *[f"- {review.thread_key(t)}" for t in picked[CHOICE_KEEP]],
        ]
    if picked[CHOICE_FIX]:
        lines += [
            "",
            f"このフェーズで直す（続きの子チケット `{followup}`）:",
            *[f"- {review.thread_key(t)}" for t in picked[CHOICE_FIX]],
        ]
    if picked[CHOICE_ISSUE]:
        lines += ["", "issue に回す:", *[f"- {review.thread_key(t)}" for t in picked[CHOICE_ISSUE]]]
    if not any(picked.values()):
        lines = [
            review_host.MARKER_DECIDE,
            f"フェーズ {d.ph.number} で未解決（Unresolved）の指摘なし。レビュー済みにした。",
        ]
    path = os.path.join(conf.state, DECIDE_FILE.format(parent=d.parent.ticket, phase=d.ph.number))
    failed = fsio.write_text(path, "\n".join(lines) + "\n", newline="\n")
    if failed:
        stderr.write(f"ccnavi: 記録を書き出せない ({failed})\n")


def _decided_prompt(
    root: str, d: Decision, picked: dict[str, list[review_host.Thread]], followup: str
) -> str:
    """決めたことを Claude Code に渡す文。ボードがコピーか新しいセッションで渡す。"""
    if not any(picked.values()):
        # 選ぶものが無かった。0 件の内訳は並べず、レビュー済みになったことだけ言う
        return (
            f"[ccnavi] ユーザが親 {d.parent.ticket} のフェーズ {d.ph.number} をレビュー済みにした"
            "（未解決（Unresolved）の指摘なし）。次のフェーズへ進める。"
            + "\n"
            + _status_line(root, d.parent)
        )
    # 選ばれなかった行き先（0 件）は並べない
    counts = "、".join(
        f"{label} {len(picked[c])} 件"
        for c, label in (
            (CHOICE_KEEP, "対応しない"),
            (CHOICE_FIX, "このフェーズで直す"),
            (CHOICE_ISSUE, "issue に回す"),
        )
        if picked[c]
    )
    head = (
        f"[ccnavi] ユーザが親 {d.parent.ticket} のフェーズ {d.ph.number} で"
        f"未解決（Unresolved）の指摘の対応方針を決めた（{counts}）。"
    )
    if followup:
        items = "\n".join(f"- {_thread_line(t)}" for t in picked[CHOICE_FIX])
        return (
            f"{head}\n直す指摘は続きの子チケット {followup} に載せ、承認済みにしてある。"
            f"{_followup_next(root, d.parent, followup)}。指摘:\n{items}\n"
            "状態の操作（start・finish）はメインが打つ。実装はサブエージェントに任せてよい。\n"
            f"{_status_line(root, d.parent)}"
        )
    return (
        f"{head}\nフェーズ {d.ph.number} はレビュー済みになった。次のフェーズへ進める。\n"
        f"{_status_line(root, d.parent)}"
    )
