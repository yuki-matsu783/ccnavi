"""残った指摘の行き先を決める。`ccnavi --reviewed` の決め方と `ccnavi review decide`。

レビューで残った指摘を、ユーザが指摘ごとに「このまま・直す・issue に回す」から選ぶ（設計 9.10）。
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
    history,
    phase,
    phasetypes,
    review,
    review_host,
    ticket_ids,
    ticket_model,
)

DECIDE_FILE = "review-decide-{parent}-{phase}.md"
# 残った指摘のうち、ユーザが issue に回すと選んだ分の下書き。sh がこれで issue を作る。
DECIDE_ISSUE_FILE = "review-issue-{parent}-{phase}.md"


# 残った指摘の行き先。ユーザが指摘ごとに選ぶ（設計 9.10）。
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
    ident, failed = approval_ops.followup(conf, root, parent, ph.number, children, items)
    if failed:
        stderr.write(f"ccnavi: 続きの子チケットを起こせない: {failed}\n")
        return None
    stdout.write(
        f"続きの子チケット {ident} を {conf.approved}/{ticket_model.DOING}/ に起こした"
        f"（フェーズ {ph.number}、範囲は見た子の和、本文に指摘 {len(items)} 件）。"
        "フェーズは開き直り、マーカーは消えた。\n"
        f"次は{_followup_next(root, parent, ident)}\n"
    )
    return ident


def _followup_next(root: str, parent: ticket_model.Ticket, ident: str) -> str:
    """続きの子を起こしたあと、エージェントが打つ 2 手。"""
    git_sh = settings.script_command(root, "ccnavi-git.sh")
    ticket_sh = settings.script_command(root, "ccnavi-ticket.sh")
    return (
        f"エージェントが '{git_sh} worktree add .claude/worktrees/{ident} -b {ident} "
        f"{ticket_ids.branch_name(parent)}' でワークツリーを切り、'{ticket_sh} start {ident}' で"
        "着手する"
    )


@dataclass
class Decision:
    """残った指摘を決める前の、見せる材料。preview と適用が同じものから組む。"""

    parent: ticket_model.Ticket
    ph: phase.Phase
    result: review_host.Result
    unresolved: list[review_host.Thread]
    # issue に回せるか。回せるのはフィードバック計画が承認されたあと（設計 9.11）。
    can_issue: bool


def decision_digest(d: Decision) -> str:
    """見せた指摘のダイジェスト。見せてから押すまでに指摘が増えた・変わったら、適用を止める。

    承認のダイジェスト（`agree_digest.approval_digest`）と同じ組み方。部分ごとの SHA-256 を
    件数と一緒に並べ、その全体の SHA-256。区切りでつなぐと、本文に区切りを書いてつなぎ目をずらせる。
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
) -> Decision | None:
    """残った指摘を決められる状態かを確かめ、見せる材料を組む。決められなければ None。"""
    found = review._parent_phase(stderr, root, conf, cwd, phase_no)
    if found is None:
        return None
    parent, ph = found
    requested_mark = ph.marks.get(approval_marks.MARK_REQUESTED)
    if requested_mark is None:
        stderr.write("ccnavi: 依頼の記録が無い。先に request してください\n")
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
            "レビュアーの approve / dismiss を待ってください\n"
        )
        return None
    unresolved = review_host._unresolved(
        result.threads,
        approval_marks.accepted_threads(
            approval.home_dir(conf, root, parent.ticket, "", project=parent.project),
            parent.ticket,
            phase_no,
            parent,
        ),
        result.host,
        str(requested_mark.get("poster") or ""),
    )
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
    chat: bool = False,
) -> int:
    """ユーザが端末で打つ。残った指摘を見せ、1 件ずつ行き先を選ばせる。変更要求は通せない。

    選んだ結果は `apply_decision` が置き、MR に書き込むコメントと issue の下書きを state の置き場に
    書き出す。sh がそれを投稿する。

    `chat` はこのセッションで見たフェーズ（`review: chat`）を通す枝。取得した結果も依頼の記録も
    要らない代わりに、定義が chat と宣言しているフェーズにしか使えない。
    """
    if chat:
        found = review._parent_phase(stderr, root, conf, cwd, phase_no)
        if found is None:
            return 1
        parent, ph = found
        return _reviewed_in_chat(stdin, stdout, stderr, conf, root, parent, ph, accept_unresolved)
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
    何も置かない。置くのは sh が C1 の中で `--yes <選択> --digest <ダイジェスト>` で打つ
    （選ぶのを C1 のロックの外で済ませ、ロックを持ったままユーザを待たない）。
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
        stderr.write(f"ccnavi: 選んだものを {out_path} に書けない（{failed}）\n")
        return 1
    return 0


def _ask_choices(
    stdin: TextIO, stdout: TextIO, stderr: TextIO, d: Decision
) -> dict[str, str] | None:
    """残った指摘の行き先を 1 件ずつ端末で選ばせる。決めなければ None。"""
    keys = "k / f / i" if d.can_issue else "k / f"
    stdout.write(
        "未解決（Unresolved）の指摘の対応方針を 1 件ずつ選びます。"
        "それ以外を打つと、何もせずにやめます。\n"
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
    """残った指摘を見せるだけ（`--reviewed N --accept-unresolved --preview --json`）。何も置かない。

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
) -> int:
    """オーバーレイでユーザが押した選択を置く。

    形は `--yes <選択の JSON> --digest <ダイジェスト> --json`。

    ユーザが端末で打つという制約の代わりに、見せた指摘と今の指摘のダイジェストが一致することを
    求める。エージェントがこれをシェルで打つ形は、組み込みの deny
    （`phase_forms.ticket_approval_rule`）が止める。
    結果は JSON で返す。違えば何も置かず `mismatch` を返す。
    """
    if not digest.strip():
        stderr.write("ccnavi: --yes には --digest（見せた指摘のダイジェスト）が要る\n")
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
    d = _decision(stderr, root, conf, cwd, phase_no, result_path)
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
    notes = io.StringIO()
    summary = apply_decision(notes, stderr, root, conf, d, raw)
    if summary is None:
        return 1
    stdout.write(
        json.dumps({"version": DECIDE_VERSION, "ok": True, **summary}, ensure_ascii=False) + "\n"
    )
    return 0


def _decide_actor() -> tuple[str, str]:
    """decide のマーカーに入れる (アカウント, 経路)。

    アカウントは ccnavi-review.sh がトークンの持ち主を引いて `--actor` で渡したもの（履歴の行の
    `actor`）。経路はこの起動の経路（端末は `terminal`、ボードは `board`）。アカウントが無ければ
    どちらも書かない（confirm と同じく、引けなければマーカーは前と同じバイト列）。
    """
    account = str(history.extra().get("actor") or "")
    return (account, history.via()) if account else ("", "")


def apply_decision(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    d: Decision,
    choices: dict[str, str],
) -> dict | None:
    """選んだ行き先を置く。端末とオーバーレイが同じここを通る。置けなければ None。

    - 対応しない・issue に回す: 受け入れの記録（`accepted.json`）に足す。次の confirm は数えない
    - このフェーズで直す: 続きの子を同じ番号で `doing/` に起こす。受け入れないので、次の
      confirm がまた数える。1 件でもあればフェーズは開き直り、レビュー済みのマーカーは置かない
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
    # issue に回す分は、state の置き場に下書きを書いて sh に渡す。置き場が無ければ回せないので、
    # 何も置く前に断る（受け入れだけが残り、issue は作られない、にしない）
    if picked[CHOICE_ISSUE] and not conf.state:
        stderr.write("ccnavi: state の置き場が空。issue に回す下書きを置けない\n")
        return None
    # 前の回の下書き（投稿に失敗して残ったもの）は捨てる。残すと、この回に選んでいない
    # issue やコメントを sh が投稿する
    if conf.state:
        for name in (DECIDE_FILE, DECIDE_ISSUE_FILE):
            stale = os.path.join(conf.state, name.format(parent=parent.ticket, phase=ph.number))
            if os.path.exists(stale):
                fsio.remove(stale)
    # 受け入れはマーカーより先に記録へ。マーカーは上書きも一括の消去もされるので、
    # ユーザが 1 度言った「これは承知で進める」はそちらに置かない。
    if accepted:
        failed = approval_marks.remember_accepted(home, parent.ticket, accepted, ph.number)
        if failed:
            stderr.write(f"ccnavi: 受け入れを記録できない: {failed}\n")
            return None
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
            "サブエージェントには渡さない。"
        )
    return f"{head}\nフェーズ {d.ph.number} はレビュー済みになった。次のフェーズへ進める。"


def _reviewed_in_chat(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    parent: ticket_model.Ticket,
    ph: phase.Phase,
    accept_unresolved: bool,
) -> int:
    """このセッションで見たフェーズを、ユーザが端末で通す（設計 9.8）。

    ホストへ出ないので取得した結果も依頼の記録も無い。代わりに見るのは 3 つ。宣言が `chat` で
    あること（`mr` と宣言したフェーズを手軽な経路で通させない）と、フェーズが終わって
    いること、そして依頼が出ていないこと。依頼を出した先には指摘が付いているかもしれず、
    それを数えずに通す手段はここには用意しない（数えるのは `confirm`、受け入れるのは `decide`）。
    実績のリスクが高ければマージリクエストを勧めるが、止めはしない（このセッションで受ける
    レビューもマージリクエストと並ぶ道として残す）。
    """
    if accept_unresolved:
        stderr.write(
            "ccnavi: --chat に未解決スレッドは無い（ホストへ出ていない）。"
            "--accept-unresolved は外してください\n"
        )
        return 1
    review_sh = settings.script_command(root, "ccnavi-review.sh")
    if ph.review_kind != phasetypes.REVIEW_CHAT:
        if ph.review_kind == phasetypes.REVIEW_MR:
            stderr.write(
                f"ccnavi: フェーズ {ph.label} はマージリクエストで見るフェーズ。"
                "--chat では通せない。"
                f"'{review_sh} request --phase {ph.number} --body-file <依頼文> "
                f"--eli5 wip/eli5/phase-{ph.number}.html' から\n"
            )
        else:
            stderr.write(
                f"ccnavi: フェーズ {ph.label} はレビューの要らないフェーズ。通すものが無い\n"
            )
        return 1
    if not ph.ended:
        stderr.write(
            f"ccnavi: フェーズ {ph.label} はまだ終わっていない（開いている子がある）。"
            "子を全部閉じてから\n"
        )
        return 1
    if approval_marks.MARK_REQUESTED in ph.marks:
        # 依頼を出したあとに --chat で通すと、マージリクエストに付いた指摘を数えずに
        # 止めていた判定を外せてしまう。数える手段（confirm）と、数えたうえで受け入れる手段
        # （decide）がある。
        stderr.write(
            f"ccnavi: フェーズ {ph.label} はマージリクエストに依頼済み。--chat では通せない。"
            f"'{review_sh} confirm --phase {ph.number}'（指摘が残っていれば "
            f"'{review_sh} decide {ph.number}'）から\n"
        )
        return 1
    if approval_marks.MARK_REVIEWED in ph.marks:
        stdout.write(f"OK: フェーズ {ph.number} はすでにレビュー済み\n")
        return 0
    stdout.write(f"フェーズ {ph.label}（親 {parent.ticket}）の子:\n")
    for t in ph.tickets:
        stdout.write(f"  - {t.ticket} {t.title}\n")
    if ph.risk_line:
        stdout.write(f"{ph.risk_line}\n")
    if ph.risk_escalates:
        stdout.write(
            "実績のリスクが高い。マージリクエストで見ることを勧める"
            "（このまま chat で通すこともできる）。\n"
        )
    stdout.write("このセッションで見たものとしてレビュー済みにしてよいなら y、やめるならそれ以外: ")
    stdout.flush()
    if fsio.read_line(stdin).strip().lower() not in ("y", "yes"):
        stderr.write("ccnavi: レビュー済みにしなかった\n")
        return 1
    data = {
        "by": phasetypes.REVIEW_CHAT,
        "tickets": [t.ticket for t in ph.tickets],
        "accepted": [],
    }
    if ph.risk_escalates:
        record = ph.risk or {}
        data["risk"] = str(record.get("level") or "")
        data["recommended"] = phasetypes.REVIEW_MR
    if not review._settle_children(stdout, stderr, root, conf, parent, ph):
        return 1
    if not review._mark(
        stderr,
        approval.home_dir(conf, root, parent.ticket, "", project=parent.project),
        parent.ticket,
        ph.number,
        approval_marks.MARK_REVIEWED,
        data,
    ):
        return 1
    stdout.write(f"OK: フェーズ {ph.number} はレビュー済み（このセッションで見た）\n")
    # 残した指摘があれば、続きの子を起こす。ホストから取得した結果が無いので、指摘はユーザが打つ。
    stdout.write(
        "残した指摘があれば、続きの子チケットを起こす。指摘を 1 行ずつ入れ、空行で終えてください"
        "（何も入れなければ起こさない）:\n"
    )
    stdout.flush()
    items: list[str] = []
    while True:
        line = fsio.read_line(stdin)
        if not line.strip():
            break
        items.append(line.strip())
    if not items:
        return 0
    ident = _followup_from_choice(stdout, stderr, root, conf, parent, ph, items)
    return 0 if ident is not None else 1
