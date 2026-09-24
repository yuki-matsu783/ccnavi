"""レビューの依頼と確認。`ccnavi review prepare|requested|confirm` と `ccnavi --reviewed`。

## exe が見るのは作業ツリーの中だけ

ここはネットワークに出ない。フェーズが終わっているか、子のブランチが親に入っているか、
未コミットが無いか、push 済みか、マーカーがどうなっているか。分かるのはそこまでで、
マージリクエストの中身は `.ccnavi/scripts/ccnavi-review.sh` が取ってきて JSON で渡す
（`--result <path>`）。その JSON の形が sh と exe の契約で、テストも同じ経路を通る。

API のパス、トークンの権限、ページング、セルフホストの差は実物に当てないと決まらない。
exe が API を直接叩くと、それが配布物の中に閉じて、壊れたときに exe を作り直すしかない。
sh ならプロジェクトごとに直せる。

## 依頼は prepare と requested の 2 段

投稿の前に前提を全部確かめ（`prepare`）、投稿は sh がして、その結果でマーカーを置く
（`requested`）。段の名前が違えば、どちらで止まったかが exit code を見なくても分かる。
`prepare` はマーカー付きの本文を控えの置き場に書き出し、sh はそれを投稿する。

## 変更要求は人の端末でも通せない

未解決スレッドは人が `--reviewed --accept-unresolved` で受け入れて進めるが、
変更要求（changes requested）のレビューが立っている間はマーカーを置かない。
「このままではマージしない」の意思表示を、別の人が端末から上書きする形は残さない。

## 未解決の指摘は、付いた時刻で絞らない

数えるのは「いま解決されていない指摘」全部。依頼より後のものだけを数えると、
指摘が残ったまま「子をもう 1 本足して承認してもらい、依頼をやり直す」だけで前回の
指摘が数から消える。人が解決も受け入れもしていないのに通る形になる。
除くのは機構自身の投稿と、人が受け入れたものだけ。

レビューの状態（変更要求）はレビュアーごとの最新だけを見る。こちらは時刻で
比べるので、ホストの `Z` と手元のオフセットをエポック秒に直してから並べる。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from typing import TextIO

from . import approval, configsync, fsio, gitcmd, ops, phase, phasetypes, settings, tree
from . import ticket as ticket_mod

# 投稿に付けるマーカー。機構自身の投稿を、確認のときに除くため。
MARKER_REQUEST = "<!-- ccnavi:request "
MARKER_COMMENT = "<!-- ccnavi:comment -->"
MARKER_DECIDE = "<!-- ccnavi:decide -->"
MARKER_PREFIX = "<!-- ccnavi:"

GITHUB_TOKEN = "GITHUB_TOKEN"
GITLAB_TOKEN = "GITLAB_TOKEN"

TIMEOUT_SECONDS = 15.0

# 控えの置き場に書く、投稿待ちの本文の名前。sh がこれを投稿する。
REQUEST_FILE = "review-request-{parent}-{phase}.md"
DECIDE_FILE = "review-decide-{parent}-{phase}.md"
# 残った指摘のうち、人が issue に回すと選んだ分の下書き。sh がこれで issue を作る。
DECIDE_ISSUE_FILE = "review-issue-{parent}-{phase}.md"
# まだマージリクエストが無いときに、sh がこれで作る。
MR_FILE = "review-mr-{parent}.md"
# Draft を外すときにマージリクエストへ残すコメント。sh が Draft を外してから投稿する。
READY_FILE = "review-ready-{parent}.md"
# 人が締めたときの、残りを写す issue の下書きと、マージリクエストへ残すコメント。
CLOSE_EARLY_ISSUE_FILE = "review-close-early-issue-{parent}.md"
CLOSE_EARLY_NOTE_FILE = "review-close-early-note-{parent}.md"
MARKER_READY = "<!-- ccnavi:ready -->"
MARKER_CLOSE_EARLY = "<!-- ccnavi:close-early -->"


@dataclass
class Thread:
    id: str = ""
    resolved: bool = False
    url: str = ""
    path: str = ""
    line: int = 0
    body: str = ""
    created_at: str = ""


@dataclass
class Review:
    state: str = ""
    url: str = ""
    submitted_at: str = ""
    # author はレビュアーの識別。同じ人の最新のレビューだけを数えるために要る。
    # GitHub は過去の全レビューを返すので、後で approve しても古い変更要求が残る。
    author: str = ""


# 変更要求の状態。GitHub の CHANGES_REQUESTED と、GitLab の requested_changes をここに寄せる。
CHANGES_REQUESTED = "CHANGES_REQUESTED"
DISMISSED = "DISMISSED"


def effective(reviews: list[Review]) -> list[Review]:
    """レビュアーごとに最新の 1 件。取り下げられたものは無い扱い。"""
    latest: dict[str, Review] = {}
    for r in reviews:
        key = r.author or r.url or id(r)
        current = latest.get(key)
        if current is None or _epoch(r.submitted_at) >= _epoch(current.submitted_at):
            latest[key] = r
    return [r for r in latest.values() if r.state.upper() != DISMISSED]


@dataclass
class MergeRequest:
    number: int = 0
    url: str = ""


@dataclass
class Result:
    """sh が取ってきたリモートの写し。`--result <json>` で渡る。

    形は 1 つ。`host` と `mr{number,url}` は常に要る。`confirm` と `--reviewed` は
    `threads[]` と `reviews[]`、`requested` は投稿の `url` と `created_at` を見る。
    """

    host: str = ""
    mr: MergeRequest | None = None
    threads: list[Thread] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)
    url: str = ""
    created_at: str = ""
    error: str = ""

    @classmethod
    def load(cls, path: str) -> Result:
        data, failed = fsio.read_json(path)
        if isinstance(failed, OSError):
            return cls(error=f"結果を読めない ({failed})")
        if failed is not None:
            return cls(error=f"結果が JSON として読めない ({failed})")
        if not isinstance(data, dict):
            return cls(error="結果の最上位が辞書ではない")
        if data.get("error"):
            return cls(error=str(data["error"]))
        mr = data.get("mr")
        found = None
        if isinstance(mr, dict) and mr.get("number"):
            found = MergeRequest(int(mr.get("number") or 0), str(mr.get("url") or ""))
        return cls(
            host=str(data.get("host") or ""),
            mr=found,
            threads=[
                Thread(
                    id=str(t.get("id") or ""),
                    resolved=bool(t.get("resolved")),
                    url=str(t.get("url") or ""),
                    path=str(t.get("path") or ""),
                    line=int(t.get("line") or 0),
                    body=str(t.get("body") or ""),
                    created_at=str(t.get("created_at") or ""),
                )
                for t in data.get("threads") or []
                if isinstance(t, dict)
            ],
            reviews=[
                Review(
                    str(r.get("state") or ""),
                    str(r.get("url") or ""),
                    str(r.get("submitted_at") or ""),
                    str(r.get("author") or ""),
                )
                for r in data.get("reviews") or []
                if isinstance(r, dict)
            ],
            url=str(data.get("url") or ""),
            created_at=str(data.get("created_at") or ""),
        )


def prepare(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    phase_no: int,
    body_file: str,
) -> int:
    """前提を全部確かめ、投稿する本文を控えの置き場に書き出す。標準出力はその置き場。"""
    found = _parent_phase(stderr, root, conf, cwd, phase_no)
    if found is None:
        return 1
    parent, ph = found
    if not conf.state:
        stderr.write("ccnavi: 控えの置き場が空。投稿する本文を置く場所が無い\n")
        return 1
    tree_root = tree.worktree_path(root, parent.ticket)
    unmet = _unmet(tree_root, conf, ph)
    body = _read_body(_resolve(cwd, body_file))
    if body is None:
        unmet.append(f"依頼文を読めない ({body_file})")
        body = ""
    if not body.strip():
        unmet.append("依頼文が空")
    already = _already_requested(tree_root, conf, ph, phase_no)
    if already:
        unmet.append(already)
    if ph.deferred:
        unmet.append(
            f"フェーズ {ph.label} のレビューは {ph.review_at} 番目と一緒に見る計画。"
            f"--phase {ph.review_at} で依頼する"
        )
    if unmet:
        stderr.write(f"ccnavi: 依頼の前提が {len(unmet)} 件満たされていない\n")
        for line in unmet:
            stderr.write(f"  - {line}\n")
        return 1
    marker = f"{MARKER_REQUEST}{parent.ticket}:{phase_no} -->\n"
    # 計画があれば、このレビューが含むフェーズを機械が先頭に書く。延期した分を
    # 人が読み落とさないように。
    body = _covered_header(root, conf, parent, ph) + body
    # 着手のときに共通層でプロジェクトの設定を上書きしていれば、最初の依頼の頭に載せる
    # （設計 §11.12）。知らせたことは、投稿が済んでから `requested` が印に残す。
    home = approval.home_dir(conf, root, parent.ticket, "")
    synced = configsync.pending(home, parent.ticket)
    if synced:
        body = configsync.notice(synced) + body
    path = os.path.join(conf.state, REQUEST_FILE.format(parent=parent.ticket, phase=phase_no))
    draft = os.path.join(conf.state, MR_FILE.format(parent=parent.ticket))
    failed = fsio.write_text(path, marker + body, newline="\n") or fsio.write_text(
        draft, mr_draft(parent), newline="\n"
    )
    if failed:
        stderr.write(f"ccnavi: 本文を書き出せない ({failed})\n")
        return 1
    if synced:
        # 本文に載せたことを印に残す。`requested` はこれを見て知らせ済みにする。載せていない
        # 投稿で知らせ済みにすると、人が一度も見ないまま知らせが消える。
        failed = configsync.mark_prepared(home, parent.ticket, phase_no)
        if failed:
            stderr.write(f"ccnavi: 設定の上書きを本文に載せた印を書けない ({failed})\n")
            return 1
    # 1 行目が依頼の本文、2 行目がマージリクエストの下書き。sh はこの順で読む。
    stdout.write(path + "\n" + draft + "\n")
    return 0


def _note_synced(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    parent: str,
    where: str,
    phase_no: int | None,
) -> None:
    """設定を上書きしたことを知らせた、と印に残す。書けなくても依頼は済んでいるので止めない。

    `phase_no` を渡したら、その番号の依頼の本文に載せたとき（`prepare` が印に残した）だけ残す。
    """
    home = approval.home_dir(conf, root, parent, "")
    if configsync.pending(home, parent) is None:
        return
    if phase_no is not None and not configsync.prepared_for(home, parent, phase_no):
        return
    failed = configsync.mark_notified(home, parent, where)
    if failed:
        stderr.write(f"ccnavi: 設定を上書きしたことを知らせた印を書けない: {failed}\n")


def mr_draft(parent: ticket_mod.Ticket) -> str:
    """マージリクエストの下書き。1 行目が題、空行のあとが本文。

    まだ無ければ sh がこれで作る。人がレビューのときに見るのはこの入れ物なので、
    親チケットが持っている材料（題・理由・本文・元の課題）をそのまま写す。
    下書き（Draft）で作るのは、統合を決めるのが人だから。題から Draft を外して
    マージするところまでが人の手に残る。
    """
    title = parent.title.strip() or parent.ticket
    lines = [f"Draft: {title}", ""]
    if parent.issue:
        lines += [f"Closes #{parent.issue}", ""]
    lines += [
        f"チケット `{parent.ticket}`。ワークツリーは `.claude/worktrees/{parent.ticket}`。",
        "",
    ]
    if parent.rationale.strip():
        lines += ["## なぜやるか", "", parent.rationale.strip(), ""]
    if parent.body.strip():
        lines += [parent.body.strip(), ""]
    lines += [
        "---",
        "",
        "フェーズが終わるたびに ccnavi がレビューの依頼をこのマージリクエストに投稿する。",
        "未解決のスレッドが残っている間、次のフェーズへは進めない。",
        "",
    ]
    return "\n".join(lines)


def requested(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    phase_no: int,
    result_path: str,
) -> int:
    """投稿の結果を受けて、依頼のマーカーを置く。"""
    found = _parent_phase(stderr, root, conf, cwd, phase_no)
    if found is None:
        return 1
    parent, ph = found
    tree_root = tree.worktree_path(root, parent.ticket)
    already = _already_requested(tree_root, conf, ph, phase_no)
    if already:
        stderr.write(f"ccnavi: {already}\n")
        return 1
    again = approval.MARK_REQUESTED in ph.marks
    result = _result_with_mr(stderr, result_path)
    if result is None:
        return 1
    if not result.url:
        stderr.write("ccnavi: 結果に投稿の url が無い。投稿されていないならマーカーは置かない\n")
        return 1
    # 投稿とマーカーの間に HEAD が動いていないか。動いていれば、人が見るものとマーカーが食い違う。
    unmet = _unmet(tree_root, conf, ph)
    if unmet:
        stderr.write("ccnavi: 投稿の後に前提が崩れた。マーカーは置かない\n")
        for line in unmet:
            stderr.write(f"  - {line}\n")
        return 1
    rc, head = _git(tree_root, ["rev-parse", "HEAD"])
    # since はホストの時計。手元の時計と比べると、依頼直後の指摘が「依頼より前」に
    # 落ちて黙って除かれる。
    if not _mark(
        stderr,
        approval.home_dir(conf, root, parent.ticket, ""),
        parent.ticket,
        phase_no,
        approval.MARK_REQUESTED,
        {
            "head": head.strip(),
            "mr": result.mr.number,
            "url": result.url,
            "host": result.host,
            "since": result.created_at,
        },
    ):
        return 1
    if conf.state:
        fsio.remove(
            os.path.join(conf.state, REQUEST_FILE.format(parent=parent.ticket, phase=phase_no))
        )
    _note_synced(stderr, conf, root, parent.ticket, result.url, phase_no)
    done = "依頼し直した" if again else "依頼した"
    stdout.write(
        f"OK: レビューを{done}（{result.mr.url or result.url}）。ターンを終えて利用者を待つこと\n"
    )
    return 0


def confirm(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    phase_no: int,
    result_path: str,
) -> int:
    """依頼の後を見る。通ればマーカーを置いて先へ進めるようになる。"""
    found = _parent_phase(stderr, root, conf, cwd, phase_no)
    if found is None:
        return 1
    parent, ph = found
    requested_mark = ph.marks.get(approval.MARK_REQUESTED)
    if requested_mark is None:
        stderr.write("ccnavi: 依頼の記録が無い。先に request すること\n")
        return 1
    tree_root = tree.worktree_path(root, parent.ticket)
    moved = _moved_since_request(tree_root, conf, requested_mark)
    if moved:
        stderr.write(
            f"ccnavi: {moved}。人が見たものと今の HEAD が違う。{_redo_request(root, phase_no)}\n"
        )
        return 1
    result = _matching(stderr, result_path, requested_mark)
    if result is None:
        return 1
    changes = [r for r in effective(result.reviews) if r.state.upper() == CHANGES_REQUESTED]
    if changes:
        stderr.write(
            "ccnavi: 変更要求のレビューが立っている。decide でも通せない。"
            "レビュアーの approve / dismiss を待つこと\n"
        )
        for r in changes:
            stderr.write(f"  - {r.url}\n")
        return 1
    unresolved = _unresolved(
        result.threads,
        approval.accepted_threads(
            approval.home_dir(conf, root, parent.ticket, ""), parent.ticket, phase_no, parent
        ),
    )
    if unresolved:
        stderr.write(f"ccnavi: 未解決のスレッドが {len(unresolved)} 件残っている\n")
        for t in unresolved:
            stderr.write(f"  - {t.url} {t.path}:{t.line} {_first_line(t.body)}\n")
        review_sh = settings.script_command(root, "ccnavi-review.sh")
        if _is_last_feedback_review(parent, phase_no):
            # フィードバック対応の最後のレビュー。新しいフィードバック作業フェーズは
            # 足せない。同じフェーズでやり直すか、別の issue に切り出すか（設計 §9.11）。
            stderr.write(
                "フィードバック対応の最後のレビューです。道は 2 つ。\n"
                f"  - 同じフェーズ {phase_no} に子を足して承認を受け、やり直す（差し戻し）\n"
                f"  - 利用者が '{review_sh} decide {phase_no}'（ボードの「決める」）で"
                "残りを受け入れるか、別の issue に回す\n"
                "新しいフィードバック作業フェーズは足せません。\n"
            )
        else:
            stderr.write(
                "解決してもらって再実行するか、利用者が端末で "
                f"'{review_sh} decide {phase_no}'（ボードの「決める」）で、指摘ごとに"
                "受け入れて進むか、続きの子チケットで直すかを選ぶ\n"
            )
        return 1
    assert result.mr is not None
    if not _settle_children(stdout, stderr, root, conf, parent, ph):
        return 1
    if not _mark(
        stderr,
        approval.home_dir(conf, root, parent.ticket, ""),
        parent.ticket,
        phase_no,
        approval.MARK_REVIEWED,
        {"mr": result.mr.number, "accepted": []},
    ):
        return 1
    stdout.write(f"OK: フェーズ {phase_no} はレビュー済み。先へ進める\n")
    return 0


def _settle_children(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    parent: ticket_mod.Ticket,
    ph: phase.Phase,
) -> bool:
    """レビューが済んだフェーズ（延期を引き受けた分を含む）のレビュー待ちの子を `done/` へ動かす。

    人が見たことの記録はマーカーで、場所の移動はその写し（ADR-0055）。動かせなければ言って False。

    呼ぶ側はこれをレビュー済みのマーカーより先に呼ぶ。マーカーを先に置くと、動かせなかった
    ときに「レビュー済みなのに子が `review/` に残る」形になり、`confirm` は「レビュー済み」で
    拒み、`--reviewed --chat` も「すでにレビュー済み」で戻るので、取り出す操作が無くなる。
    逆順なら、動いたのにマーカーが置けなくても、次の `confirm` が置き直す（動かす分は空）。
    """
    moved, failed = approval.settle_review(conf, root, parent.ticket, [*ph.covers, ph.number])
    if failed:
        stderr.write(f"ccnavi: {failed}\n")
        return False
    if moved:
        stdout.write(
            f"レビュー待ちの子を {conf.approved}/{ticket_mod.DONE}/ へ動かした: "
            f"{', '.join(moved)}\n"
        )
    return True


# 残った指摘の行き先。人が指摘ごとに選ぶ（設計 §9.10）。
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
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    parent: ticket_mod.Ticket,
    ph: phase.Phase,
    items: list[str],
    stamp: str,
) -> str | None:
    """人が選んだ続きの子を `doing/` に起こし、識別子を返す。起こせなければ None。"""
    children = [t for t in ph.tickets if ph.states.get(t.ticket) in ticket_mod.FINISHED]
    ident, failed = approval.followup(conf, root, parent, ph.number, children, items, stamp)
    if failed:
        stderr.write(f"ccnavi: 続きの子チケットを起こせない: {failed}\n")
        return None
    stdout.write(
        f"続きの子チケット {ident} を {conf.approved}/{ticket_mod.DOING}/ に起こした"
        f"（フェーズ {ph.number}、範囲は見た子の和、本文に指摘 {len(items)} 件）。"
        "フェーズは開き直り、マーカーは消えた。\n"
        f"次は{_followup_next(root, parent, ident)}\n"
    )
    return ident


def _followup_next(root: str, parent: ticket_mod.Ticket, ident: str) -> str:
    """続きの子を起こしたあと、エージェントが打つ 2 手。"""
    git_sh = settings.script_command(root, "ccnavi-git.sh")
    ticket_sh = settings.script_command(root, "ccnavi-ticket.sh")
    return (
        f"エージェントが '{git_sh} worktree add .claude/worktrees/{ident} -b {ident} "
        f"{parent.ticket}' でワークツリーを切り、'{ticket_sh} start {ident}' で着手する"
    )


@dataclass
class Decision:
    """残った指摘を決める前の、見せる材料。preview と適用が同じものから組む。"""

    parent: ticket_mod.Ticket
    ph: phase.Phase
    result: Result
    unresolved: list[Thread]
    # issue に回せるか。回せるのはフィードバック計画が承認されたあと（設計 §9.11）。
    can_issue: bool


def thread_key(t: Thread) -> str:
    """選択を結ぶ鍵。受け入れの控えと同じく、URL を先に使う。"""
    return t.url or t.id


def decision_digest(d: Decision) -> str:
    """見せた指摘の指紋。見せてから押すまでに指摘が増えた・変わったら、適用を止める。

    承認の指紋（`approval.approval_digest`）と同じ組み方。部分ごとの SHA-256 を件数と一緒に
    並べ、その全体の SHA-256。区切りでつなぐと、本文に区切りを書いてつなぎ目をずらせる。
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
        parts.extend([thread_key(t), t.path, str(t.line), t.body])
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
    found = _parent_phase(stderr, root, conf, cwd, phase_no)
    if found is None:
        return None
    parent, ph = found
    requested_mark = ph.marks.get(approval.MARK_REQUESTED)
    if requested_mark is None:
        stderr.write("ccnavi: 依頼の記録が無い。先に request すること\n")
        return None
    tree_root = tree.worktree_path(root, parent.ticket)
    moved = _moved_since_request(tree_root, conf, requested_mark)
    if moved:
        stderr.write(f"ccnavi: {moved}。{_redo_request(root, phase_no)}\n")
        return None
    result = _matching(stderr, result_path, requested_mark)
    if result is None:
        return None
    if any(r.state.upper() == CHANGES_REQUESTED for r in effective(result.reviews)):
        stderr.write(
            "ccnavi: 変更要求のレビューが立っている。decide でも通せない。"
            "レビュアーの approve / dismiss を待つこと\n"
        )
        return None
    unresolved = _unresolved(
        result.threads,
        approval.accepted_threads(
            approval.home_dir(conf, root, parent.ticket, ""), parent.ticket, phase_no, parent
        ),
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
                "key": thread_key(t),
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
    """人が端末で打つ。残った指摘を見せ、1 件ずつ行き先を選ばせる。変更要求は通せない。

    選んだ結果は `apply_decision` が置き、MR に写すコメントと issue の下書きを控えの置き場に
    書き出す。sh がそれを投稿する。

    `chat` はこのセッションで見たフェーズ（`review: chat`）を通す枝。写しも依頼の記録も
    要らない代わりに、種類が chat と宣言しているフェーズにしか当たらない。
    """
    if chat:
        found = _parent_phase(stderr, root, conf, cwd, phase_no)
        if found is None:
            return 1
        parent, ph = found
        return _reviewed_in_chat(stdin, stdout, stderr, conf, root, parent, ph, accept_unresolved)
    if not accept_unresolved:
        stderr.write(
            "ccnavi: 未解決を受け入れるなら --accept-unresolved を付ける。"
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
    keys = "k / f / i" if d.can_issue else "k / f"
    stdout.write(
        "未解決（Unresolved）の指摘の行き先を 1 件ずつ選びます。"
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
        stdout.write(f"[{i}/{len(d.unresolved)}] {t.url} {t.path}:{t.line} {_first_line(t.body)}\n")
        stdout.write(f"選択（{keys}）: ")
        stdout.flush()
        picked = _CHOICE_KEYS.get(fsio.read_line(stdin).strip().lower(), "")
        if not picked or (picked == CHOICE_ISSUE and not d.can_issue):
            stderr.write("ccnavi: 決めなかった。何も置いていない\n")
            return 1
        choices[thread_key(t)] = picked
    summary = apply_decision(stdout, stderr, root, conf, d, choices)
    return 0 if summary is not None else 1


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
    """オーバーレイで人が押した選択を置く（`--yes <選択の JSON> --digest <指紋> --json`）。

    端末の壁の代わりに、見せた指摘と今の指摘の指紋が一致することを求める。エージェントが
    これをシェルで打つ形は、組み込みの deny（`phase.ticket_approval_rule`）が止める。
    結果は JSON で返す。違えば何も置かず `mismatch` を返す。
    """
    if not digest.strip():
        stderr.write("ccnavi: --yes には --digest（見せた指摘の指紋）が要る\n")
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
    shown = {thread_key(t) for t in d.unresolved}
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


def apply_decision(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    d: Decision,
    choices: dict[str, str],
) -> dict | None:
    """選んだ行き先を置く。端末とオーバーレイが同じここを通る。置けなければ None。

    - 対応しない・issue に回す: 受け入れの控え（`accepted.json`）に足す。次の confirm は数えない
    - このフェーズで直す: 続きの子を同じ番号で `doing/` に起こす。受け入れないので、次の
      confirm がまた数える。1 件でもあればフェーズは開き直り、レビュー済みのマーカーは置かない
    - どれも直さないなら、レビュー済みのマーカーを置く

    MR に写すコメントと issue の下書きは控えの置き場に書き、sh が投稿する。
    """
    assert d.result.mr is not None
    parent, ph = d.parent, d.ph
    stamp = approval.now()
    home = approval.home_dir(conf, root, parent.ticket, "")
    picked = {c: [t for t in d.unresolved if choices.get(thread_key(t)) == c] for c in CHOICES}
    accepted = [thread_key(t) for t in picked[CHOICE_KEEP] + picked[CHOICE_ISSUE]]
    fix = picked[CHOICE_FIX]
    # issue に回す分は、控えの置き場に下書きを書いて sh に渡す。置き場が無ければ回せないので、
    # 何も置く前に断る（受け入れだけが残り、issue は作られない、にしない）
    if picked[CHOICE_ISSUE] and not conf.state:
        stderr.write("ccnavi: 控えの置き場が空。issue に回す下書きを置けない\n")
        return None
    # 前の回の下書き（投稿に失敗して残ったもの）は捨てる。残すと、この回に選んでいない
    # issue やコメントを sh が投稿する
    if conf.state:
        for name in (DECIDE_FILE, DECIDE_ISSUE_FILE):
            stale = os.path.join(conf.state, name.format(parent=parent.ticket, phase=ph.number))
            if os.path.exists(stale):
                os.remove(stale)
    # 受け入れはマーカーより先に控えへ。マーカーは上書きも一括の消去もされるので、人が 1 度言った
    # 「これは承知で進める」はそちらに置かない。
    if accepted:
        failed = approval.remember_accepted(home, parent.ticket, accepted, ph.number)
        if failed:
            stderr.write(f"ccnavi: 受け入れを控えられない: {failed}\n")
            return None
    if not _settle_children(stdout, stderr, root, conf, parent, ph):
        return None
    followup = ""
    if fix:
        items = [_thread_line(t) for t in fix]
        ident = _followup_from_choice(
            io.StringIO(), stdout, stderr, root, conf, parent, ph, items, stamp
        )
        if ident is None:
            return None
        followup = ident
    elif not _mark(
        stderr,
        home,
        parent.ticket,
        ph.number,
        approval.MARK_REVIEWED,
        {"mr": d.result.mr.number, "accepted": accepted},
    ):
        return None
    issue_draft = ""
    if picked[CHOICE_ISSUE] and conf.state:
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
        "kept": [thread_key(t) for t in picked[CHOICE_KEEP]],
        "fix": [thread_key(t) for t in fix],
        "issue": [thread_key(t) for t in picked[CHOICE_ISSUE]],
        "issue_draft": issue_draft,
        "prompt": _decided_prompt(root, d, picked, followup),
    }


def _thread_line(t: Thread) -> str:
    return f"{t.url} {t.path}:{t.line} {_first_line(t.body)}"


def _write_decide_issue(
    stderr: TextIO, conf: settings.Settings, d: Decision, threads: list[Thread]
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
    picked: dict[str, list[Thread]],
    followup: str,
) -> None:
    """MR に写す、決めた内容のコメント。issue の綴りは sh が作ったあとに書き足す。"""
    lines = [MARKER_DECIDE, f"フェーズ {d.ph.number} の未解決（Unresolved）の指摘の行き先:"]
    if picked[CHOICE_KEEP]:
        lines += [
            "",
            "対応しない（受け入れて進む）:",
            *[f"- {thread_key(t)}" for t in picked[CHOICE_KEEP]],
        ]
    if picked[CHOICE_FIX]:
        lines += [
            "",
            f"このフェーズで直す（続きの子チケット `{followup}`）:",
            *[f"- {thread_key(t)}" for t in picked[CHOICE_FIX]],
        ]
    if picked[CHOICE_ISSUE]:
        lines += ["", "issue に回す:", *[f"- {thread_key(t)}" for t in picked[CHOICE_ISSUE]]]
    if not any(picked.values()):
        lines = [
            MARKER_DECIDE,
            f"フェーズ {d.ph.number} で未解決（Unresolved）の指摘なし。レビュー済みにした。",
        ]
    path = os.path.join(conf.state, DECIDE_FILE.format(parent=d.parent.ticket, phase=d.ph.number))
    failed = fsio.write_text(path, "\n".join(lines) + "\n", newline="\n")
    if failed:
        stderr.write(f"ccnavi: 記録を書き出せない ({failed})\n")


def _decided_prompt(root: str, d: Decision, picked: dict[str, list[Thread]], followup: str) -> str:
    """決めたことを Claude Code に渡す文。ボードがコピーか新しいセッションで渡す。"""
    if not any(picked.values()):
        # 選ぶものが無かった。0 件の内訳は並べず、レビュー済みになったことだけ言う
        return (
            f"[ccnavi] 利用者が親 {d.parent.ticket} のフェーズ {d.ph.number} をレビュー済みにした"
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
        f"[ccnavi] 利用者が親 {d.parent.ticket} のフェーズ {d.ph.number} で"
        f"未解決（Unresolved）の指摘の行き先を決めた（{counts}）。"
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
    parent: ticket_mod.Ticket,
    ph: phase.Phase,
    accept_unresolved: bool,
) -> int:
    """このセッションで見たフェーズを、人が端末で通す（設計 §9.8）。

    ホストへ出ないので写しも依頼の記録も無い。代わりに見るのは 3 つ。宣言が `chat` で
    あること（`mr` と宣言したフェーズを安い経路で通させない）と、フェーズが終わって
    いること、そして依頼が出ていないこと。依頼を出した先には指摘が付いているかもしれず、
    それを数えずに通す道はここには置かない（数えるのは `confirm`、受け入れるのは `decide`）。
    実績のリスクが高ければマージリクエストを勧めるが、止めはしない（ADR-0065）。
    """
    if accept_unresolved:
        stderr.write(
            "ccnavi: --chat に未解決スレッドは無い（ホストへ出ていない）。"
            "--accept-unresolved は外すこと\n"
        )
        return 1
    review_sh = settings.script_command(root, "ccnavi-review.sh")
    if ph.review_kind != phasetypes.REVIEW_CHAT:
        if ph.review_kind == phasetypes.REVIEW_MR:
            stderr.write(
                f"ccnavi: フェーズ {ph.label} はマージリクエストで見るフェーズ。"
                "--chat では通せない。"
                f"'{review_sh} request --phase {ph.number} --body-file <依頼文>' から\n"
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
    if approval.MARK_REQUESTED in ph.marks:
        # 依頼を出したあとに --chat で通すと、マージリクエストに付いた指摘を数えずに
        # 止まっていたのが解ける。数える道（confirm）と、数えたうえで受け入れる道（decide）がある。
        stderr.write(
            f"ccnavi: フェーズ {ph.label} はマージリクエストに依頼済み。--chat では通せない。"
            f"'{review_sh} confirm --phase {ph.number}'（指摘が残っていれば "
            f"'{review_sh} decide {ph.number}'）から\n"
        )
        return 1
    if approval.MARK_REVIEWED in ph.marks:
        stdout.write(f"OK: フェーズ {ph.number} はすでにレビュー済み\n")
        return 0
    synced = configsync.pending(approval.home_dir(conf, root, parent.ticket, ""), parent.ticket)
    if synced:
        stdout.write(configsync.notice(synced))
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
    if not _settle_children(stdout, stderr, root, conf, parent, ph):
        return 1
    if not _mark(
        stderr,
        approval.home_dir(conf, root, parent.ticket, ""),
        parent.ticket,
        ph.number,
        approval.MARK_REVIEWED,
        data,
    ):
        return 1
    if synced:
        _note_synced(stderr, conf, root, parent.ticket, phasetypes.REVIEW_CHAT, None)
    stdout.write(f"OK: フェーズ {ph.number} はレビュー済み（このセッションで見た）\n")
    # 残した指摘があれば、続きの子を起こす。ホストに写しが無いので、指摘は人が打つ。
    stdout.write(
        "残した指摘があれば、続きの子チケットを起こす。指摘を 1 行ずつ入れ、空行で終える"
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
    ident = _followup_from_choice(
        stdin, stdout, stderr, root, conf, parent, ph, items, approval.now()
    )
    return 0 if ident is not None else 1


def ready(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    result_path: str,
) -> int:
    """Draft を外してよいかを確かめ、マーカーとコメントの下書きを置く。外すのは sh。

    条件は「親を閉じられる」と同じ（ops.close_problems）。閉じてよい状態と、
    マージに進んでよい状態は同じもの。親を閉じたあとでも打てる（閉じた承認済みチケットも引く）。
    同じ親に 2 度打っても通る。sh が Draft を外し損ねたときに打ち直せるように。
    マージそのものは人が行う。
    """
    parent = _parent_any(stderr, root, conf, cwd)
    if parent is None:
        return 1
    problems = ops.close_problems(root, conf, parent.ticket)
    problems += _merge_problems(tree.worktree_path(root, parent.ticket), conf, root)
    if problems:
        stderr.write("ccnavi: まだ Draft を外せない:\n")
        for p in problems:
            stderr.write(f"  - {p}\n")
        stderr.write(
            "全部片付けてから打ち直す。まだ残るものを承知で締めるなら、利用者が端末で "
            f"'{settings.script_command(root, 'ccnavi-review.sh')} close-early --reason <理由>' "
            "を打つ\n"
        )
        return 1
    result = _result_with_mr(stderr, result_path)
    if result is None:
        return 1
    if not conf.state:
        stderr.write("ccnavi: 控えの置き場が空。コメントの下書きを置く場所が無い\n")
        return 1
    wrapped = approval.read_parent_mark(
        approval.home_dir(conf, root, parent.ticket, ""),
        parent.ticket,
        approval.PARENT_MARK_CLOSE_EARLY,
    )
    text = [MARKER_READY, f"チケット `{parent.ticket}` の作業は終わり、Draft を外した。"]
    text.append(
        f"`{WIP_ROOT}/` は片付けてある。マージするかどうかは利用者が決める。"
        "取り込むときは squash で、途中のコミットを既定のブランチに残さない。"
    )
    if wrapped:
        text.append(f"（利用者が締めた: {wrapped.get('reason', '')}）")
    path = os.path.join(conf.state, READY_FILE.format(parent=parent.ticket))
    failed = _write_text(path, "\n".join(text) + "\n")
    if failed:
        stderr.write(f"ccnavi: {failed}\n")
        return 1
    if not _parent_mark(
        stderr,
        approval.home_dir(conf, root, parent.ticket, ""),
        parent.ticket,
        approval.PARENT_MARK_READY,
        {"mr": result.mr.number, "url": result.mr.url},
    ):
        return 1
    stdout.write(path + "\n")
    return 0


def close_early(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    reason: str,
    result_path: str,
) -> int:
    """人が端末で打つ。「まだ残っているが、キリの良いところまでやった」と締める。

    残っているもの（未着手の子、子の無いフェーズ、終わっていないレビュー、
    未計画のフィードバック、未解決のスレッド）を全部見せてから y/N。y なら、
    未着手の子を取り消し、フェーズに省略とレビュー済みのマーカーを置き、未解決を受け入れ、
    親のマーカー `close-early.json` を置く。残りは別の issue に写す下書きを書き、sh がそれで
    issue を作る。黙って消えるものは作らない。

    Draft を外すのはここではなく `ready`。締めたあとに親が状態の移動をコミットし、
    途中の作業の置き場を消して push する。それが済んだことを `ready` が確かめて外す。
    外す道を 1 本にしておくと、外れたマージリクエストは必ず「片付いて push 済み」になる。

    作業中の子がいる間は打てない。締めるのは、手が止まっているときだけ。
    """
    if not reason.strip():
        stderr.write("ccnavi: --close-early には --reason <理由> が要る\n")
        return 1
    parent = _parent(stderr, root, conf, cwd)
    if parent is None:
        return 1
    if not conf.state:
        stderr.write("ccnavi: 控えの置き場が空。下書きを置く場所が無い\n")
        return 1
    result = _result_with_mr(stderr, result_path)
    if result is None:
        return 1
    assert result.mr is not None
    if any(r.state.upper() == CHANGES_REQUESTED for r in effective(result.reviews)):
        stderr.write(
            "ccnavi: 変更要求のレビューが立っている。端末からも通せない。"
            "レビュアーの approve / dismiss を待つこと\n"
        )
        return 1
    phases = phase.phases_of(root, conf, parent.ticket)
    doing = [
        t.ticket
        for ph in phases
        for t in ph.tickets
        if ph.states.get(t.ticket) == ticket_mod.DOING and t.started_at
    ]
    if doing:
        stderr.write(
            f"ccnavi: 作業中の子がいる（{', '.join(doing)}）。閉じるか取り消してから締めること\n"
        )
        return 1
    home = approval.home_dir(conf, root, parent.ticket, "")
    left = _leftovers(home, parent, phases, result)
    # 着手で共通層を写したことをまだ知らせていなければ、締める前にここで見せる。y で締めたら
    # 見たものとして残す。見せないと、締めたあとの finish でもう 1 度端末を求めることになる。
    synced = configsync.pending(home, parent.ticket)
    if synced:
        stdout.write(configsync.notice(synced))
    _show_leftovers(stdout, parent, left)
    if fsio.read_line(stdin).strip().lower() not in ("y", "yes"):
        stderr.write("ccnavi: 締めなかった\n")
        return 1
    if synced:
        failed = configsync.mark_notified(home, parent.ticket, configsync.NOTIFIED_TERMINAL)
        if failed:
            stderr.write(f"ccnavi: 設定の上書きを見たと残せない: {failed}\n")
            return 1
    stamp = approval.now()
    settled = _settle(
        stdout, stderr, root, conf, parent, phases, left, result.mr.number, stamp, reason
    )
    if settled is None:
        return 1
    cancelled, skipped, reviewed_now = settled
    accepted = [t.url or t.id for t in left.unresolved]
    failed = approval.remember_accepted(
        approval.home_dir(conf, root, parent.ticket, ""), parent.ticket, accepted
    )
    if failed:
        stderr.write(f"ccnavi: 受け入れを控えられない: {failed}\n")
        return 1
    if not _parent_mark(
        stderr,
        approval.home_dir(conf, root, parent.ticket, ""),
        parent.ticket,
        approval.PARENT_MARK_CLOSE_EARLY,
        {
            "reason": reason.strip(),
            "at": stamp,
            "mr": result.mr.number,
            "cancelled": cancelled,
            "skipped": skipped,
            "settled": reviewed_now,
            "accepted": accepted,
        },
    ):
        return 1
    failed = _close_early_drafts(
        conf, parent, result, reason, stamp, left, cancelled, skipped, accepted
    )
    if failed:
        stderr.write(f"ccnavi: {failed}\n")
        return 1
    # 下書きの綴りは標準出力に出さない。ここは人の端末に向いていて、sh は
    # 控えの置き場の決まった名前（親の識別子 = ブランチ名）で拾う。
    stdout.write(
        f"OK: {parent.ticket} を締めた。あとは親に、状態の移動をコミットし、"
        f"'ticket finish {parent.ticket}' で閉じ、`{WIP_ROOT}/` を消して push し、"
        "'ccnavi-review.sh ready' で Draft を外させる\n"
    )
    return 0


@dataclass
class Leftovers:
    """締めるときに残っているもの。見せるものと、締めたあとに issue へ写すもの。"""

    # 未着手の子。取り消す。
    todo: list[ticket_mod.Ticket]
    # 終わっていないフェーズ。省略のマーカーを置く。
    not_ended: list[phase.Phase]
    # 終わっていないフェーズのうち、手を付けていないもの（子が無いか全部未着手）。
    untouched: list[phase.Phase]
    # 終わったがレビューが済んでいないフェーズ。済んだ扱いにする。
    unreviewed: list[phase.Phase]
    # フィードバック計画がまだ無い。対応なしの扱いにする。
    unplanned: bool
    # 未解決のスレッド。受け入れる。
    unresolved: list[Thread]

    @property
    def nothing(self) -> bool:
        return not (
            self.todo or self.not_ended or self.unreviewed or self.unresolved or self.unplanned
        )


def _leftovers(
    approved_dir: str, parent: ticket_mod.Ticket, phases: list[phase.Phase], result: Result
) -> Leftovers:
    not_ended = [ph for ph in phases if not ph.ended]

    def untouched(t: ticket_mod.Ticket, ph: phase.Phase) -> bool:
        # 承認されたが着手していない子。`doing/` に在って着手の欄が空。
        return ph.states.get(t.ticket) == ticket_mod.DOING and not t.started_at

    return Leftovers(
        todo=[t for ph in phases for t in ph.tickets if untouched(t, ph)],
        not_ended=not_ended,
        untouched=[
            ph for ph in not_ended if not ph.tickets or all(untouched(t, ph) for t in ph.tickets)
        ],
        unreviewed=[ph for ph in phases if ph.ended and not phase.reviewed_or_skipped(ph)],
        unplanned=parent.has_plan and parent.feedback is None,
        unresolved=_unresolved(
            result.threads, approval.accepted_threads(approved_dir, parent.ticket)
        ),
    )


def _show_leftovers(stdout: TextIO, parent: ticket_mod.Ticket, left: Leftovers) -> None:
    """残っているものと、締めたら何が起きるかを人に見せて、y/N を促す。"""
    stdout.write(f"{parent.ticket}（{parent.title}）を締める。残っているもの:\n")
    for t in left.todo:
        stdout.write(f"  - 未着手の子 {t.ticket}（{t.title}）→ 取り消す\n")
    for ph in left.untouched:
        stdout.write(f"  - フェーズ {ph.label}: 手を付けていない → 省略のマーカー\n")
    for ph in left.unreviewed:
        stdout.write(f"  - フェーズ {ph.label}: レビューが済んでいない → 済んだ扱い\n")
    if left.unplanned:
        stdout.write("  - フィードバック計画: 未計画 → 対応なしの扱い\n")
    for t in left.unresolved:
        stdout.write(f"  - 未解決 {t.url} {t.path}:{t.line} {_first_line(t.body)} → 受け入れる\n")
    if left.nothing:
        stdout.write("  （何も残っていない。ready で足りる）\n")
    stdout.write("残りは別の issue に写す。締めてよいなら y、やめるならそれ以外: ")
    stdout.flush()


def _settle(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    parent: ticket_mod.Ticket,
    phases: list[phase.Phase],
    left: Leftovers,
    mr_number: int,
    stamp: str,
    reason: str,
) -> tuple[list[str], list[int], list[int]] | None:
    """未着手の子を取り消し、フェーズにマーカーを置く。

    返すのは取り消した子、省略のマーカーを置いた番号、済んだ扱いにした番号。
    途中で失敗したら None。そこまでの変更は戻さない（マーカーは次に打てば重ねられる）。
    """
    cancelled: list[str] = []
    for t in left.todo:
        if ops.cancel(stdout, stderr, root, conf, t.ticket, f"close-early: {reason.strip()}") != 0:
            return None
        cancelled.append(t.ticket)
    skipped: list[int] = []
    settled: list[int] = []
    pending = {ph.number for ph in left.not_ended}
    for ph in phases:
        if ph.number in pending:
            if approval.MARK_SKIPPED in ph.marks:
                continue
            if not _mark(
                stderr,
                approval.home_dir(conf, root, parent.ticket, ""),
                parent.ticket,
                ph.number,
                approval.MARK_SKIPPED,
                {"by": "close-early", "at": stamp},
            ):
                return None
            skipped.append(ph.number)
        elif ph in left.unreviewed:
            if not _settle_children(stdout, stderr, root, conf, parent, ph):
                return None
            if not _mark(
                stderr,
                approval.home_dir(conf, root, parent.ticket, ""),
                parent.ticket,
                ph.number,
                approval.MARK_REVIEWED,
                {"by": "close-early", "at": stamp, "mr": mr_number, "accepted": []},
            ):
                return None
            settled.append(ph.number)
    return cancelled, skipped, settled


def _close_early_drafts(
    conf: settings.Settings,
    parent: ticket_mod.Ticket,
    result: Result,
    reason: str,
    stamp: str,
    left: Leftovers,
    cancelled: list[str],
    skipped: list[int],
    accepted: list[str],
) -> str:
    """残りを写す issue の下書きと、マージリクエストへ残すコメント の下書き。書けなければ理由。"""
    assert result.mr is not None
    # 1 行目が題、空行のあとが本文。
    issue = [f"{parent.title} の残り", ""]
    issue += [
        f"元のマージリクエスト: {result.mr.url}（チケット `{parent.ticket}`）",
        f"締めた理由: {reason.strip()}",
        "",
        "## 残した作業",
        "",
    ]
    rest = [f"- {t.ticket}: {t.title}" for t in left.todo]
    rest += [f"- フェーズ {ph.label}: 手を付けていない" for ph in left.untouched]
    if left.unplanned:
        rest.append("- フィードバック計画は立てていない")
    issue += rest or ["（残した作業は無い）"]
    issue += ["", "## 引き継ぐ指摘", ""]
    issue += [f"- {t.url} {t.path}:{t.line} {_first_line(t.body)}" for t in left.unresolved] or [
        "（未解決のスレッドは残っていない）"
    ]
    issue.append("")
    issue_path = os.path.join(conf.state, CLOSE_EARLY_ISSUE_FILE.format(parent=parent.ticket))
    failed = _write_text(issue_path, "\n".join(issue))
    if failed:
        return failed
    note = [
        MARKER_CLOSE_EARLY,
        f"利用者が締めた（{stamp}）: {reason.strip()}",
        f"取り消した子: {', '.join(cancelled) or '無し'} / 省略したフェーズ: "
        f"{', '.join(str(n) for n in skipped) or '無し'} / 受け入れた指摘: {len(accepted)} 件",
        "残りは別の issue に写す。親が片付けて ready を打てば Draft が外れる。"
        "マージは利用者が行う。",
        "",
    ]
    note_path = os.path.join(conf.state, CLOSE_EARLY_NOTE_FILE.format(parent=parent.ticket))
    return _write_text(note_path, "\n".join(note))


# 途中の作業の置き場。調査や設計の下書きを置く場所で、マージの前に丸ごと消す。
# 既定のブランチに残す場所はマージリクエストと issue。綴りは設定から導かず固定する。
WIP_ROOT = "wip"


def _approved_rel(conf: settings.Settings) -> str:
    """ccnavi 自身の置き場（ツリーのルートからの相対）。末尾の `/` は付けない。"""
    return (conf.approved or settings.DEFAULT_APPROVED).strip("/")


def _own_places(conf: settings.Settings) -> tuple[str, ...]:
    """ccnavi 自身が動かす置き場。承認済みチケットと提案の 2 つ（ADR-0055）。

    チケットは 2 つの置き場を行き来するので、片方だけを外すと `finish` の移動
    （`doing/` → `review/`）が「人が見るものが動いた」に数えられる。
    """
    return (_approved_rel(conf), (conf.tickets or settings.DEFAULT_TICKETS).strip("/"))


def _is_own_place(conf: settings.Settings, path: str) -> bool:
    return any(path.startswith(place + "/") for place in _own_places(conf))


# 依頼のマーカーに記録された HEAD。git の revision として使う前に、この形であることを求める。
_SHA = re.compile(r"^[0-9a-f]{7,64}$")


def _is_sha(value: str) -> bool:
    """マーカーの `head` が sha の形をしているか。

    マーカーは親のブランチに乗って他の機械から届くファイル（設計 §9.2）なので、中身を
    git の revision としてそのまま渡さない。`HEAD` や `@` のような「今」を指す値は
    `head..HEAD` を空差分にして判定を素通りさせ、`-` で始まる値は git のオプションに化ける。
    """
    return bool(_SHA.match(value))


def _outside_approved(tree_root: str, conf: settings.Settings, ref: str) -> tuple[list[str], str]:
    """`ref..HEAD` の差分のうち、ccnavi 自身の置き場の外にあるパス。2 つめは読めなかった理由。

    NUL 区切りで読む理由は phase.scope_findings と同じ。既定の出力は非 ASCII を
    引用して 8 進に逃がすので、そのまま当てると日本語のファイルが置き場の外か中かで
    読み違える。`--no-renames` を付けるのは、改名を 1 行にまとめられると移動元が消え、
    置き場の外から中へ動かしたファイルが「置き場の中だけ」に見えるため。
    `--ignore-submodules=none` は、`.gitmodules` の `ignore = all` で submodule の
    進みが差分から丸ごと消えるのを止める（`.gitmodules` は追跡されるので、外から届く）。

    `-z` が返すパスはもう正規化されているので、こちらでは何も直さない。空白を落としたり
    `\\` を `/` に直したりすると、`.ccnavi\\tickets\\x.py` という名前のファイル 1 個が
    置き場の中のパスに化けて、除外の側に落ちる。
    """
    if not ref:
        return [], "比べる相手が無い"
    rc, out = _git(
        tree_root,
        ["diff", "--name-only", "--no-renames", "--ignore-submodules=none", "-z", f"{ref}..HEAD"],
    )
    if rc != 0:
        return [], f"{ref[:12]} からの差分を読めない"
    changed = []
    for path in out.split("\0"):
        if path and not _is_own_place(conf, path):
            changed.append(path)
    return changed, ""


def _dirty(tree_root: str, conf: settings.Settings) -> bool:
    """ワークツリーに未コミットの変更があるか。ccnavi 自身の置き場は数えない。

    写しとマーカーはこのワークツリーの `.ccnavi/` に置かれ、git が追跡する（設計 §9.2）。
    マーカーはフェーズの終わりに hook が書くので、ここを数えると「レビューを頼む前に
    マーカーをコミットしろ」と言い続けることになる。マーカーと写しをコミットして push するのは
    `ccnavi-review.sh` と `ccnavi-approve.sh` の仕事で、人の作業の汚れとは別に扱う。
    """
    rc, status = _git(
        tree_root, ["status", "--porcelain", "-z", "--untracked-files=no", "--no-renames"]
    )
    if rc != 0:
        return True
    # `-z` で読む。既定の出力は非 ASCII を引用して 8 進に逃がすので、置き場の中の
    # 日本語のファイルが置き場の外に見え、「未コミットがある」で依頼が止まる。
    # `--no-renames` は、改名のときに出る 2 つめの綴り（移動元）が XY を持たない形で
    # 混ざるのを避けるため。phase.scope_findings と同じ読み方。
    for entry in status.split("\0"):
        if len(entry) > 3 and entry[2] == " " and not _is_own_place(conf, entry[3:]):
            return True
    return False


def _merge_problems(tree_root: str, conf: settings.Settings, root: str) -> list[str]:
    """マージに進む前にワークツリーの側で満たしていること。root は文面の sh の綴りに使う。

    途中の作業の置き場が追跡から消えていること、未コミットが無いこと、push 済みであること。
    人がマージするときに見るのはリモートの HEAD なので、手元にだけあるものは無いのと同じ。
    """
    problems: list[str] = []
    if not os.path.isdir(tree_root):
        return [f"親のワークツリーが無い ({tree_root})"]
    wip = WIP_ROOT
    rc, tracked = _git(tree_root, ["ls-files", "--", wip])
    if rc == 0 and tracked.strip():
        n = len(tracked.strip().splitlines())
        problems.append(
            f"`{wip}/` に追跡されているファイルが {n} 件ある。"
            "途中の作業は既定のブランチに残さない。"
            f"'{settings.script_command(root, 'ccnavi-git.sh')} rm -r {wip}' で消してコミットする"
        )
    if _dirty(tree_root, conf):
        problems.append("親のワークツリーに未コミットの変更がある")
    branch = _branch(tree_root)
    if not branch:
        problems.append("親ブランチの名前を読めない")
    else:
        rc, ahead = _git(tree_root, ["rev-list", f"origin/{branch}..HEAD"])
        if rc != 0 or ahead.strip():
            problems.append("親ブランチの HEAD が push されていない")
    return problems


def _parent_any(
    stderr: TextIO, root: str, conf: settings.Settings, cwd: str
) -> ticket_mod.Ticket | None:
    """cwd の親。閉じた承認済みチケットも引く（親を閉じたあとに Draft を外す道のため）。"""
    parent = phase.parent_for_cwd(root, conf, cwd)
    if parent is not None:
        return parent
    t = tree.tree_of(root, cwd or os.getcwd(), conf.projects)
    if t is not None and not t.is_main:
        closed, _ = approval.scan(conf, root, closed=True)
        found = tree.lookup(approval.by_id(closed), t.name)
        if found is not None and not found.is_child:
            return found
    stderr.write("ccnavi: ここは親チケットのワークツリーではない（cwd から親を引けない）\n")
    return None


def _write_text(path: str, text: str) -> str:
    failed = fsio.write_text(path, text, newline="\n")
    return f"下書きを書き出せない ({path}: {failed})" if failed else ""


def _covered_header(
    root: str, conf: settings.Settings, parent: ticket_mod.Ticket, ph: phase.Phase
) -> str:
    """依頼文の先頭に置く「このレビューが含むフェーズ」と「このレビューのリスク」。

    リスクの行は、レビュアーが「なぜこのフェーズにレビューが要ることになったか」を
    依頼文で読めるように、実績の点と加点した理由を機械が書く（risk.py）。
    """
    numbers = [*ph.covers, ph.number]
    labels = []
    risks = []
    for p in phase.phases_of(root, conf, parent.ticket):
        if p.number in numbers:
            labels.append(p.label)
            if p.risk_line:
                risks.append(f"{p.label}: {p.risk_line}" if len(numbers) > 1 else p.risk_line)
    head = ""
    if parent.has_plan:
        if len(labels) <= 1 and not ph.covers:
            head = f"このレビューが含むフェーズ: {ph.label}\n"
        else:
            head = "このレビューが含むフェーズ: " + "、".join(labels) + "\n"
    if risks:
        head += "このレビューのリスク: " + " / ".join(risks) + "\n"
        if ph.risk_escalates:
            head += "（実績のリスクが高いので、宣言に関わらずレビューが要る扱い）\n"
    return head + "\n" if head else ""


def _is_last_feedback_review(parent: ticket_mod.Ticket, phase_no: int) -> bool:
    """フィードバック作業フェーズの最後のレビューか。"""
    if not parent.has_plan or not parent.feedback:
        return False
    last = len(parent.plan) + len(parent.feedback)
    return parent.review_at(last) == phase_no


# ---- リモートに要る道具の有無。exe は使わないが、--lint が言う。

# ホストにはポートが付く（`localhost:8929`）。落とすと、手元や社内に立てた
# GitLab を GitHub と見分ける手掛かりまで狂う。ssh の `git@host:group/proj` の
# `:` はパスの区切りなので、数字だけのときにポートと見なす。
# `https://oauth2:token@host/` のユーザ情報は読み飛ばす。sh と同じく、authority の
# 最後の `@` までをユーザ情報と見る（git がそう切る。トークンに `@` が入る形がある）。
# IPv6 は `[::1]` の形。sh が読めない綴り（`ssh://user@host/`、大文字の scheme）は
# ここでも読めない扱いにして、--lint と sh の言うことを揃える。
_REMOTE = re.compile(
    r"^(?:https?://|ssh://git@|git@)(?:[^/]*@)?"
    r"(?P<host>\[[^\]/]+\]|[^/:@]+)(?::\d+)?[/:]+(?P<path>.+?)(?:\.git)?/?$"
)


def remote_kind(url: str) -> str:
    """origin の URL から、GitHub か GitLab か。読めない綴りなら空。"""
    m = _REMOTE.match(url)
    if m is None:
        return ""
    host = m.group("host")
    return "github" if host == "github.com" else "gitlab"


def transport_problem(url: str) -> str:
    """sh がリモートを読み書きできる形になっているか。なっていなければ、その説明。

    sh と同じ順で探す。`gh` / `glab` があればそれ、無ければ `curl` とトークン。
    どちらも無ければ、レビューの依頼と確認は動かない。
    """
    kind = remote_kind(url)
    if not kind:
        return f"origin ({url}) が GitHub でも GitLab でもない"
    if shutil.which("jq") is None:
        return "jq が無い。結果の JSON を組み立てられない"
    cli = "gh" if kind == "github" else "glab"
    if shutil.which(cli) is not None:
        return ""
    token = GITHUB_TOKEN if kind == "github" else GITLAB_TOKEN
    if shutil.which("curl") is None:
        return f"{cli} も curl も無い。MCP などで人がリモートを読む形にするか、どちらかを入れる"
    if not os.environ.get(token, ""):
        return f"{cli} が無く、curl に付ける {token} も無い"
    return ""


# ---- 内部


def _parent(
    stderr: TextIO, root: str, conf: settings.Settings, cwd: str
) -> ticket_mod.Ticket | None:
    parent = phase.parent_for_cwd(root, conf, cwd)
    if parent is None:
        stderr.write("ccnavi: ここは親チケットのワークツリーではない（cwd から親を引けない）\n")
    return parent


def _phase(
    root: str, conf: settings.Settings, parent: ticket_mod.Ticket, number: int
) -> phase.Phase | None:
    for ph in phase.phases_of(root, conf, parent.ticket):
        if ph.number == number:
            return ph
    return None


def _parent_phase(
    stderr: TextIO, root: str, conf: settings.Settings, cwd: str, number: int
) -> tuple[ticket_mod.Ticket, phase.Phase] | None:
    """cwd の親と、その番号のフェーズ。どちらか無ければ言って None。"""
    parent = _parent(stderr, root, conf, cwd)
    if parent is None:
        return None
    ph = _phase(root, conf, parent, number)
    if ph is None:
        stderr.write(f"ccnavi: {parent.ticket} にフェーズ {number} の子が無い\n")
        return None
    return parent, ph


def _result_with_mr(stderr: TextIO, path: str) -> Result | None:
    """写しを読み、マージリクエストが入っていることまで確かめる。無ければ言って None。"""
    result = _result(stderr, path)
    if result is None or result.mr is None:
        stderr.write("ccnavi: 結果にマージリクエストが無い\n")
        return None
    return result


def _mark(
    stderr: TextIO, approved_dir: str, parent: str, number: int, kind: str, data: dict
) -> bool:
    """フェーズのマーカーを置く。置けなければ言って False。"""
    failed = approval.write_mark(approved_dir, parent, number, kind, data)
    if failed:
        stderr.write(f"ccnavi: マーカーを置けない: {failed}\n")
        return False
    return True


def _parent_mark(stderr: TextIO, approved_dir: str, parent: str, name: str, data: dict) -> bool:
    """親のマーカーを置く。置けなければ言って False。"""
    failed = approval.write_parent_mark(approved_dir, parent, name, data)
    if failed:
        stderr.write(f"ccnavi: マーカーを置けない: {failed}\n")
        return False
    return True


def _unmet(tree_root: str, conf: settings.Settings, ph: phase.Phase) -> list[str]:
    """依頼の前提のうち、ワークツリーの中で分かるもの。"""
    unmet: list[str] = []
    if not ph.ended:
        unmet.append("フェーズが終わっていない（doing/ に子が残っている）")
    for child in ph.tickets:
        if ph.states.get(child.ticket) not in (ticket_mod.REVIEW, ticket_mod.DONE):
            continue
        # 子のブランチは識別子と同じ名前。ワークツリーではなくブランチを引くので、
        # ワークツリーを消しても検査から外れない。引けなければ前提の未充足。
        rc, sha = _git(tree_root, ["rev-parse", "--verify", f"{child.ticket}^{{commit}}"])
        if rc != 0 or not sha.strip():
            unmet.append(f"子 {child.ticket} のブランチを確かめられない（消えている）")
            continue
        rc, _ = _git(tree_root, ["merge-base", "--is-ancestor", sha.strip(), "HEAD"])
        if rc != 0:
            unmet.append(f"子 {child.ticket} のブランチが親に取り込まれていない")
    # 未追跡は数えない。依頼文そのものをワークツリーに置く形が普通にあり、それが
    # 前提を落とすと依頼文を書く場所が無くなる。未追跡はマージリクエストに載らない。
    if _dirty(tree_root, conf):
        unmet.append("親のワークツリーに未コミットの変更がある")
    branch = _branch(tree_root)
    if not branch:
        unmet.append("親ブランチの名前を読めない")
    elif _unpushed(tree_root, conf, branch):
        unmet.append("親ブランチの HEAD が push されていない")
    return unmet


def _unpushed(tree_root: str, conf: settings.Settings, branch: str) -> bool:
    """人が見るものが、まだリモートに届いていないか。

    ccnavi 自身の置き場だけが手元に残っている形は、届いていると数える。人がレビューで
    見るのはコードで、置き場を運ぶのは `ccnavi-push-approved.sh` の仕事（push が落ちても
    コミットは残す）。数えると、レビュー待ちの間に落ちた push が次の依頼を止める。
    `confirm` の側（`_moved_since_request`）と同じ基準。

    `ready` の前提（`_merge_problems`）はこれを使わない。あちらは人がリモートを見て
    マージするところで、マーカーも本当に届いていないと他の機械へ渡らない。
    """
    rc, ahead = _git(tree_root, ["rev-list", f"origin/{branch}..HEAD"])
    if rc != 0:
        return True
    if not ahead.strip():
        return False
    changed, failed = _outside_approved(tree_root, conf, f"origin/{branch}")
    return bool(failed or changed)


def _result(stderr: TextIO, path: str) -> Result | None:
    if not path:
        stderr.write("ccnavi: --result <json> が要る。リモートの写しは sh が渡す\n")
        return None
    result = Result.load(path)
    if result.error:
        stderr.write(f"ccnavi: リモートを読めていない: {result.error}\n")
        return None
    return result


def _matching(stderr: TextIO, path: str, requested_mark: dict) -> Result | None:
    """依頼したのと同じマージリクエストの写しか。違うもので先へ進めない。"""
    result = _result(stderr, path)
    if result is None:
        return None
    if result.mr is None:
        stderr.write("ccnavi: 結果にマージリクエストが無い\n")
        return None
    if requested_mark.get("host") and result.host != requested_mark.get("host"):
        stderr.write(
            f"ccnavi: 依頼したホスト（{requested_mark.get('host')}）と"
            f"結果のホスト（{result.host}）が違う\n"
        )
        return None
    if requested_mark.get("mr") and int(requested_mark["mr"]) != result.mr.number:
        stderr.write(
            f"ccnavi: 依頼したマージリクエスト（{requested_mark['mr']}）と"
            f"結果のもの（{result.mr.number}）が違う\n"
        )
        return None
    return result


def _unresolved(threads: list[Thread], accepted: set[str]) -> list[Thread]:
    """まだ解決されていない指摘。

    付いた時刻では絞らない。依頼より後のものだけを数えると、
    指摘が残ったまま「子をもう 1 本足して承認してもらい、依頼をやり直す」だけで
    前回の指摘が数から消える。人が解決も受け入れもしていないのに通る形になる。

    数えないのは 2 つだけ。機構自身が置いた投稿と、人が「未解決のまま進める」と
    受け入れたもの。受け入れた分を数え続けると、その親が二度と通らなくなる。
    """
    return [
        t
        for t in threads
        if not t.resolved
        and not t.body.startswith(MARKER_PREFIX)
        and t.url not in accepted
        and t.id not in accepted
    ]


def _branch(tree_root: str) -> str:
    rc, out = _git(tree_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    return out.strip() if rc == 0 else ""


def _moved_since_request(tree_root: str, conf: settings.Settings, requested_mark: dict) -> str:
    """依頼の後に、人が見るものが動いたか。動いていれば、その説明。

    人が見たのは依頼時の HEAD。その後に積んだコミットは誰も見ていないので、
    それを「レビュー済み」に含めない。

    ただし ccnavi 自身の置き場（`.ccnavi/approved/` と `wip/proposals/`）だけを変えた
    コミットは、動いたと数えない。
    依頼のマーカーはそこに置かれ、親のブランチにコミットして他の機械へ運ぶ前提のもの（設計 §9.2）。
    数えると「依頼 → マーカー → コミット」の順のせいで依頼の直後に必ず自分の足を踏み、
    承認を運ぶ `ccnavi-push-approved.sh` が置き場をまとめてコミットするので、レビューを
    待っている間の承認も依頼を壊す。未コミットの側は `_dirty` が同じ理由で外しており、
    基準をそこに揃える。人がレビューで見るものは 1 バイトも変わらない。

    push の判定も同じ基準で見る。置き場だけが手元に残っていても、リモートにあるものと
    人が見るものは同じ。
    """
    rc, head = _git(tree_root, ["rev-parse", "HEAD"])
    head = head.strip()
    if rc != 0:
        return "親の HEAD を読めない"
    recorded = str(requested_mark.get("head") or "")
    if not recorded:
        # 記録が無ければ照合できない。素通りさせると、依頼の後のコミットが全部
        # 「人が見たもの」になる。出し直しで書き直させる。
        return "依頼時の親の HEAD が記録されていない"
    if head != recorded:
        moved = f"依頼の後に親の HEAD が動いている（依頼時 {recorded[:12]}、いま {head[:12]}）"
        # sha でない値は差分の相手にしない。読めない（依頼時のコミットが消えているなど）も同じ。
        if not _is_sha(recorded):
            return moved
        changed, failed = _outside_approved(tree_root, conf, recorded)
        if failed or changed:
            return moved
    branch = _branch(tree_root)
    if not branch or _unpushed(tree_root, conf, branch):
        return "親ブランチの HEAD が push されていない"
    return ""


def _already_requested(
    tree_root: str, conf: settings.Settings, ph: phase.Phase, phase_no: int
) -> str:
    """依頼済みで、出し直せないならその説明。未依頼か、出し直せるなら空。

    出し直せるのは、依頼の後に親の HEAD が動き、まだレビュー済みになっていないときだけ。
    そのとき confirm は「人が見たものと今の HEAD が違う」で止まるので、ここも止めると
    エージェントの打てる手が無くなる。出し直しても緩むものは無い。confirm は新しい HEAD との
    一致を求め直し、未解決の指摘は付いた時刻で絞らないので、前の依頼への指摘も数え続ける。
    HEAD が依頼時のままなら、同じ依頼を二重に投稿するだけなので止める。動いたかどうかは
    confirm と同じ基準で見る（`_moved_since_request`）。ccnavi 自身の置き場だけが動いた形では
    confirm が止まらないので、ここで出し直させると依頼のコメントが増えるだけになる。

    レビュー済みは依頼の有無より先に見る。`close-early` は依頼していないフェーズにも
    レビュー済みを置くので、依頼の記録が無いことを先に見ると、人が締めたフェーズに
    依頼が投稿される。
    """
    if approval.MARK_REVIEWED in ph.marks:
        return f"フェーズ {phase_no} はレビュー済み"
    mark = ph.marks.get(approval.MARK_REQUESTED)
    if mark is None:
        return ""
    rc, head = _git(tree_root, ["rev-parse", "HEAD"])
    recorded = str(mark.get("head") or "")
    if not recorded:
        return ""
    if rc == 0 and head.strip() != recorded:
        if not _is_sha(recorded):
            return ""
        changed, failed = _outside_approved(tree_root, conf, recorded)
        if failed or changed:
            return ""
    return f"フェーズ {phase_no} は依頼済み（人が見るものは依頼時のまま）"


def _redo_request(root: str, phase_no: int) -> str:
    """HEAD が動いたときの案内。push 済みの今の HEAD で依頼を出し直す。"""
    review_sh = settings.script_command(root, "ccnavi-review.sh")
    return (
        f"push してから '{review_sh} request --phase {phase_no} --body-file <依頼文>' で"
        "依頼を出し直すこと"
    )


def _resolve(cwd: str, path: str) -> str:
    """相対パスは、スクリプトを打った場所（--cwd）からの相対。"""
    if not path or os.path.isabs(path):
        return path
    return os.path.join(cwd or os.getcwd(), path)


def _read_body(path: str) -> str | None:
    return fsio.read_text(path)


def _git(cwd: str, args: list[str]) -> tuple[int, str]:
    return gitcmd.output(cwd, args, TIMEOUT_SECONDS)


def _epoch(text: str) -> float:
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _first_line(body: str) -> str:
    line = body.strip().splitlines()[0] if body.strip() else ""
    return line[:120]
