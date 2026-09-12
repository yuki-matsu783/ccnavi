"""レビューの依頼と確認。`ccnavi review prepare|requested|check` と `ccnavi --reviewed`。

## exe が見るのは作業ツリーの中だけ

ここはネットワークに出ない。フェーズが終わっているか、子のブランチが親に入っているか、
未コミットが無いか、push 済みか、印がどうなっているか。分かるのはそこまでで、
マージリクエストの中身は `.claude/scripts/ccnavi-review.sh` が取ってきて JSON で渡す
（`--result <path>`）。その JSON の形が sh と exe の契約で、テストも同じ経路を通る。

以前は exe が GitHub / GitLab の API を直接叩いていた。実測できていない部分
（API のパス、トークンの権限、ページング、セルフホストの差）が配布物の中に閉じて、
壊れたときに exe を作り直すしかなかった。sh ならプロジェクトごとに直せる。

## 依頼は prepare と requested の 2 段

投稿の前に前提を全部確かめ（`prepare`）、投稿は sh がして、その結果で印を置く
（`requested`）。段の名前が違えば、どちらで止まったかが exit code を見なくても分かる。
`prepare` はマーカー付きの本文を控えの置き場に書き出し、sh はそれを投稿する。

## 変更要求は人の端末でも通せない

未解決スレッドは人が `--reviewed --accept-unresolved` で受け入れて進めるが、
変更要求（changes requested）のレビューが立っている間は印を置かない。
「このままではマージしない」の意思表示を、別の人が端末から上書きする形は残さない。

## 未解決の指摘は、付いた時刻で絞らない

数えるのは「いま解決されていない指摘」全部。依頼より後のものだけを数えていた版は、
指摘が残ったまま「子をもう 1 本足して承認してもらい、依頼をやり直す」だけで前回の
指摘が数から消えた。人が解決も受け入れもしていないのに通る形で、実物の GitLab で
流れを通したときに出た。除くのは機構自身の投稿と、人が受け入れたものだけ。

レビューの状態（変更要求）はレビュアーごとの最新だけを見る。こちらは時刻で
比べるので、ホストの `Z` と手元のオフセットをエポック秒に直してから並べる。
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from typing import TextIO

from . import approval, fsio, gitcmd, ops, phase, settings, tree
from . import ticket as ticket_mod

# 投稿に付ける印。機構自身の投稿を、確認のときに除くため。
MARKER_REQUEST = "<!-- ccnavi:request "
MARKER_NOTE = "<!-- ccnavi:note -->"
MARKER_ACCEPT = "<!-- ccnavi:accept -->"
MARKER_PREFIX = "<!-- ccnavi:"

GITHUB_TOKEN = "GITHUB_TOKEN"
GITLAB_TOKEN = "GITLAB_TOKEN"

TIMEOUT_SECONDS = 15.0

# 控えの置き場に書く、投稿待ちの本文の名前。sh がこれを投稿する。
REQUEST_FILE = "review-request-{parent}-{phase}.md"
ACCEPT_FILE = "review-accept-{parent}-{phase}.md"
# まだマージリクエストが無いときに、sh がこれで作る。
MR_FILE = "review-mr-{parent}.md"
# 残った指摘を別の issue に切り出すときの下書き。sh がこれで issue を作る。
HANDOFF_FILE = "review-handoff-{parent}.md"
# Draft を外すときに MR へ残す note。sh が Draft を外してから投稿する。
READY_FILE = "review-ready-{parent}.md"
# 人が締めたときの、残りを写す issue の下書きと、MR へ残す note。
WRAPUP_ISSUE_FILE = "review-wrapup-issue-{parent}.md"
WRAPUP_NOTE_FILE = "review-wrapup-note-{parent}.md"
MARKER_READY = "<!-- ccnavi:ready -->"
MARKER_WRAPUP = "<!-- ccnavi:wrapup -->"


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

    形は 1 つ。`host` と `mr{number,url}` は常に要る。`check` と `--reviewed` は
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
    unmet = _unmet(tree_root, ph)
    body = _read_body(_resolve(cwd, body_file))
    if body is None:
        unmet.append(f"依頼文を読めない ({body_file})")
        body = ""
    if not body.strip():
        unmet.append("依頼文が空")
    if approval.MARK_REQUESTED in ph.marks:
        unmet.append(f"フェーズ {phase_no} は依頼済み")
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
    path = os.path.join(conf.state, REQUEST_FILE.format(parent=parent.ticket, phase=phase_no))
    draft = os.path.join(conf.state, MR_FILE.format(parent=parent.ticket))
    failed = fsio.write_text(path, marker + body, newline="\n") or fsio.write_text(
        draft, mr_draft(parent), newline="\n"
    )
    if failed:
        stderr.write(f"ccnavi: 本文を書き出せない ({failed})\n")
        return 1
    # 1 行目が依頼の本文、2 行目がマージリクエストの下書き。sh はこの順で読む。
    stdout.write(path + "\n" + draft + "\n")
    return 0


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
    lines += [f"チケット `{parent.ticket}`。作業ツリーは `.claude/worktrees/{parent.ticket}`。", ""]
    if parent.rationale.strip():
        lines += ["## なぜやるか", "", parent.rationale.strip(), ""]
    if parent.body.strip():
        lines += [parent.body.strip(), ""]
    lines += [
        "---",
        "",
        "フェーズが終わるたびに ccnavi がレビューの依頼をこの MR に投稿する。",
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
    """投稿の結果を受けて、依頼の印を置く。"""
    found = _parent_phase(stderr, root, conf, cwd, phase_no)
    if found is None:
        return 1
    parent, ph = found
    if approval.MARK_REQUESTED in ph.marks:
        stderr.write(f"ccnavi: フェーズ {phase_no} は依頼済み\n")
        return 1
    result = _result_with_mr(stderr, result_path)
    if result is None:
        return 1
    if not result.url:
        stderr.write("ccnavi: 結果に投稿の url が無い。投稿されていないなら印は置かない\n")
        return 1
    tree_root = tree.worktree_path(root, parent.ticket)
    # 投稿と印の間に HEAD が動いていないか。動いていれば、人が見るものと印が食い違う。
    unmet = _unmet(tree_root, ph)
    if unmet:
        stderr.write("ccnavi: 投稿の後に前提が崩れた。印は置かない\n")
        for line in unmet:
            stderr.write(f"  - {line}\n")
        return 1
    rc, head = _git(tree_root, ["rev-parse", "HEAD"])
    # since はホストの時計。手元の時計と比べると、依頼直後の指摘が「依頼より前」に
    # 落ちて黙って除かれる。
    if not _mark(
        stderr,
        conf.approved,
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
    stdout.write(
        f"OK: レビューを依頼した（{result.mr.url or result.url}）。ターンを終えて利用者を待つこと\n"
    )
    return 0


def check(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    phase_no: int,
    result_path: str,
) -> int:
    """依頼の後を見る。通れば印を置いてゲートが開く。"""
    found = _parent_phase(stderr, root, conf, cwd, phase_no)
    if found is None:
        return 1
    parent, ph = found
    requested_mark = ph.marks.get(approval.MARK_REQUESTED)
    if requested_mark is None:
        stderr.write("ccnavi: 依頼の記録が無い。先に request すること\n")
        return 1
    tree_root = tree.worktree_path(root, parent.ticket)
    moved = _moved_since_request(tree_root, requested_mark)
    if moved:
        stderr.write(f"ccnavi: {moved}。人が見たものと今の HEAD が違う。request をやり直すこと\n")
        return 1
    result = _matching(stderr, result_path, requested_mark)
    if result is None:
        return 1
    changes = [r for r in effective(result.reviews) if r.state.upper() == CHANGES_REQUESTED]
    if changes:
        stderr.write(
            "ccnavi: 変更要求のレビューが立っている。--accept-unresolved でも通せない。"
            "レビュアーの approve / dismiss を待つこと\n"
        )
        for r in changes:
            stderr.write(f"  - {r.url}\n")
        return 1
    unresolved = _unresolved(
        result.threads, approval.accepted_threads(conf.approved, parent.ticket)
    )
    if unresolved:
        stderr.write(f"ccnavi: 未解決のスレッドが {len(unresolved)} 件残っている\n")
        for t in unresolved:
            stderr.write(f"  - {t.url} {t.path}:{t.line} {_first_line(t.body)}\n")
        if _is_last_feedback_review(parent, phase_no):
            # フィードバック対応の最後のレビュー。新しいフィードバック作業フェーズは
            # 足せない。同じフェーズでやり直すか、別の issue に切り出すか（設計 §24.15.7）。
            stderr.write(
                "フィードバック対応の最後のレビューです。道は 2 つ。\n"
                f"  - 同じフェーズ {phase_no} に子を足して承認を受け、やり直す（差し戻し）\n"
                "  - 'sh .claude/scripts/ccnavi-review.sh handoff --body-file <題と本文>' で"
                "別の issue に切り出し、利用者が端末で "
                f"'sh .claude/scripts/ccnavi-review.sh accept {phase_no}' を打って"
                "残りを受け入れる\n"
                "新しいフィードバック作業フェーズは足せません。\n"
            )
        else:
            stderr.write(
                "解決してもらって再実行するか、同じフェーズに子を足してやり直すか、利用者が端末で "
                f"'sh .claude/scripts/ccnavi-review.sh accept {phase_no}' を打つ\n"
            )
        return 1
    assert result.mr is not None
    if not _mark(
        stderr,
        conf.approved,
        parent.ticket,
        phase_no,
        approval.MARK_REVIEWED,
        {"mr": result.mr.number, "accepted": []},
    ):
        return 1
    stdout.write(f"OK: フェーズ {phase_no} はレビュー済み。ゲートが開いた\n")
    return 0


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
    """人が端末で打つ。未解決を見せてから y/N。変更要求は通せない。

    受け入れたスレッドの一覧は控えの置き場に書き出す。sh がそれを MR のコメントに写す。
    """
    found = _parent_phase(stderr, root, conf, cwd, phase_no)
    if found is None:
        return 1
    parent, ph = found
    requested_mark = ph.marks.get(approval.MARK_REQUESTED)
    if requested_mark is None:
        stderr.write("ccnavi: 依頼の記録が無い。先に request すること\n")
        return 1
    if not accept_unresolved:
        stderr.write(
            "ccnavi: 未解決を受け入れるなら --accept-unresolved を付ける。"
            "受け入れないなら 'sh .claude/scripts/ccnavi-review.sh check' で足りる\n"
        )
        return 1
    tree_root = tree.worktree_path(root, parent.ticket)
    moved = _moved_since_request(tree_root, requested_mark)
    if moved:
        stderr.write(f"ccnavi: {moved}。request をやり直すこと\n")
        return 1
    result = _matching(stderr, result_path, requested_mark)
    if result is None:
        return 1
    if any(r.state.upper() == CHANGES_REQUESTED for r in effective(result.reviews)):
        stderr.write(
            "ccnavi: 変更要求のレビューが立っている。端末からも通せない。"
            "レビュアーの approve / dismiss を待つこと\n"
        )
        return 1
    unresolved = _unresolved(
        result.threads, approval.accepted_threads(conf.approved, parent.ticket)
    )
    stdout.write(
        f"フェーズ {phase_no}（親 {parent.ticket}）の未解決スレッド: {len(unresolved)} 件\n"
    )
    for t in unresolved:
        stdout.write(f"  - {t.url} {t.path}:{t.line} {_first_line(t.body)}\n")
    stdout.write("これらを残したまま次のフェーズへ進めてよいなら y、やめるならそれ以外: ")
    stdout.flush()
    if fsio.read_line(stdin).strip().lower() not in ("y", "yes"):
        stderr.write("ccnavi: 受け入れなかった\n")
        return 1
    accepted = [t.url or t.id for t in unresolved]
    assert result.mr is not None
    # 印より先に控えへ。印は上書きも一括の消去もされるので、人が 1 度言った
    # 「これは承知で進める」はそちらに置かない。
    failed = approval.remember_accepted(conf.approved, parent.ticket, accepted)
    if failed:
        stderr.write(f"ccnavi: 受け入れを控えられない: {failed}\n")
        return 1
    if not _mark(
        stderr,
        conf.approved,
        parent.ticket,
        phase_no,
        approval.MARK_REVIEWED,
        {"mr": result.mr.number, "accepted": accepted},
    ):
        return 1
    if accepted and conf.state:
        path = os.path.join(conf.state, ACCEPT_FILE.format(parent=parent.ticket, phase=phase_no))
        note = (
            MARKER_ACCEPT
            + "\n未解決のまま次のフェーズへ進める:\n"
            + "\n".join(f"- {a}" for a in accepted)
            + "\n"
        )
        failed = fsio.write_text(path, note, newline="\n")
        if failed:
            stderr.write(f"ccnavi: 受け入れの記録を書き出せない ({failed})\n")
    stdout.write(
        f"OK: フェーズ {phase_no} はレビュー済み（未解決 {len(accepted)} 件を受け入れた）\n"
    )
    return 0


def handoff(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    body_file: str,
    result_path: str,
) -> int:
    """残った指摘を別の issue に切り出す下書きを書き出す（設計 §24.15.7）。

    作るのは sh。ここは、切り出してよい段階か（フィードバック計画が承認済み）を確かめ、
    親が書いた題と本文に、写しの中の未解決スレッドの URL を添えて、控えの置き場に置く。
    標準出力はその綴り。1 行目が題、空行のあとが本文。
    """
    parent = _parent(stderr, root, conf, cwd)
    if parent is None:
        return 1
    if not parent.has_plan or parent.feedback is None:
        stderr.write(
            "ccnavi: 切り出せるのはフィードバック計画が承認されたあと。"
            "それまでは同じフェーズに子を足して応える\n"
        )
        return 1
    if not conf.state:
        stderr.write("ccnavi: 控えの置き場が空。下書きを置く場所が無い\n")
        return 1
    body = _read_body(_resolve(cwd, body_file))
    if body is None or not body.strip():
        stderr.write(f"ccnavi: 題と本文を読めない ({body_file})。1 行目が題\n")
        return 1
    result = _result_with_mr(stderr, result_path)
    if result is None:
        return 1
    accepted = approval.accepted_threads(conf.approved, parent.ticket)
    remaining = _unresolved(result.threads, accepted)
    lines = body.rstrip("\n").splitlines()
    title = lines[0].strip() if lines else ""
    rest = "\n".join(lines[1:]).strip()
    if not title:
        stderr.write("ccnavi: 1 行目が題。空にしない\n")
        return 1
    text = [title, "", rest, ""] if rest else [title, ""]
    text += [
        f"元のマージリクエスト: {result.mr.url}（チケット `{parent.ticket}`）",
        "",
        "## 引き継ぐ指摘",
        "",
    ]
    if remaining:
        text += [f"- {t.url} {t.path}:{t.line} {_first_line(t.body)}" for t in remaining]
    else:
        text.append("（未解決のスレッドは残っていない）")
    text.append("")
    path = os.path.join(conf.state, HANDOFF_FILE.format(parent=parent.ticket))
    failed = fsio.write_text(path, "\n".join(text), newline="\n")
    if failed:
        stderr.write(f"ccnavi: 下書きを書き出せない ({failed})\n")
        return 1
    stdout.write(path + "\n")
    return 0


def ready(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    result_path: str,
) -> int:
    """Draft を外してよいかを確かめ、印と note の下書きを置く。外すのは sh。

    条件は「親を閉じられる」と同じ（ops.close_problems）。閉じてよい状態と、
    マージに進んでよい状態は同じもの。親を閉じたあとでも打てる（閉じた承認済みチケットも引く）。
    同じ親に 2 度打っても通る。sh が Draft を外し損ねたときに打ち直せるように。
    マージそのものは人が行う。
    """
    parent = _parent_any(stderr, root, conf, cwd)
    if parent is None:
        return 1
    problems = ops.close_problems(root, conf, parent.ticket)
    problems += _merge_problems(tree.worktree_path(root, parent.ticket), conf)
    if problems:
        stderr.write("ccnavi: まだ Draft を外せない:\n")
        for p in problems:
            stderr.write(f"  - {p}\n")
        stderr.write(
            "全部片付けてから打ち直す。まだ残るものを承知で締めるなら、利用者が端末で "
            "'sh .claude/scripts/ccnavi-review.sh wrapup --reason <理由>' を打つ\n"
        )
        return 1
    result = _result_with_mr(stderr, result_path)
    if result is None:
        return 1
    if not conf.state:
        stderr.write("ccnavi: 控えの置き場が空。note の下書きを置く場所が無い\n")
        return 1
    wrapped = approval.read_parent_mark(conf.approved, parent.ticket, approval.PARENT_MARK_WRAPUP)
    text = [MARKER_READY, f"チケット `{parent.ticket}` の作業は終わり、Draft を外した。"]
    text.append(
        f"`{wip_root(conf)}/` は片付けてある。マージするかどうかは利用者が決める。"
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
        conf.approved,
        parent.ticket,
        approval.PARENT_MARK_READY,
        {"mr": result.mr.number, "url": result.mr.url},
    ):
        return 1
    stdout.write(path + "\n")
    return 0


def wrapup(
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
    未着手の子を取り消し、フェーズに省略とレビュー済みの印を置き、未解決を受け入れ、
    親の印 `wrapup.json` を置く。残りは別の issue に写す下書きを書き、sh がそれで
    issue を作る。黙って消えるものは作らない。

    Draft を外すのはここではなく `ready`。締めたあとに親が状態の移動をコミットし、
    途中の作業の置き場を消して push する。それが済んだことを `ready` が確かめて外す。
    外す道を 1 本にしておくと、外れた MR は必ず「片付いて push 済み」になる。

    作業中の子がいる間は打てない。締めるのは、手が止まっているときだけ。
    """
    if not reason.strip():
        stderr.write("ccnavi: wrapup には --reason <理由> が要る\n")
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
        t.ticket for ph in phases for t in ph.tickets if ph.states.get(t.ticket) == ticket_mod.DOING
    ]
    if doing:
        stderr.write(
            f"ccnavi: 作業中の子がいる（{', '.join(doing)}）。閉じるか取り消してから締めること\n"
        )
        return 1
    left = _leftovers(conf, parent, phases, result)
    _show_leftovers(stdout, parent, left)
    if fsio.read_line(stdin).strip().lower() not in ("y", "yes"):
        stderr.write("ccnavi: 締めなかった\n")
        return 1
    stamp = approval.now()
    settled = _settle(
        stdout, stderr, root, conf, parent, phases, left, result.mr.number, stamp, reason
    )
    if settled is None:
        return 1
    cancelled, skipped, reviewed_now = settled
    accepted = [t.url or t.id for t in left.unresolved]
    failed = approval.remember_accepted(conf.approved, parent.ticket, accepted)
    if failed:
        stderr.write(f"ccnavi: 受け入れを控えられない: {failed}\n")
        return 1
    if not _parent_mark(
        stderr,
        conf.approved,
        parent.ticket,
        approval.PARENT_MARK_WRAPUP,
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
    failed = _wrapup_drafts(conf, parent, result, reason, stamp, left, cancelled, skipped, accepted)
    if failed:
        stderr.write(f"ccnavi: {failed}\n")
        return 1
    # 下書きの綴りは標準出力に出さない。ここは人の端末に向いていて、sh は
    # 控えの置き場の決まった名前（親の識別子 = ブランチ名）で拾う。
    stdout.write(
        f"OK: {parent.ticket} を締めた。あとは親に、状態の移動をコミットし、"
        f"'ticket done {parent.ticket}' で閉じ、`{wip_root(conf)}/` を消して push し、"
        "'ccnavi-review.sh ready' で Draft を外させる\n"
    )
    return 0


@dataclass
class Leftovers:
    """締めるときに残っているもの。見せるものと、締めたあとに issue へ写すもの。"""

    # 未着手の子。取り消す。
    todo: list[ticket_mod.Ticket]
    # 終わっていないフェーズ。省略の印を置く。
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
    conf: settings.Settings, parent: ticket_mod.Ticket, phases: list[phase.Phase], result: Result
) -> Leftovers:
    not_ended = [ph for ph in phases if not ph.ended]
    return Leftovers(
        todo=[t for ph in phases for t in ph.tickets if ph.states.get(t.ticket) == ticket_mod.TODO],
        not_ended=not_ended,
        untouched=[
            ph
            for ph in not_ended
            if not ph.tickets or all(ph.states.get(t.ticket) == ticket_mod.TODO for t in ph.tickets)
        ],
        unreviewed=[ph for ph in phases if ph.ended and not phase.reviewed_or_skipped(ph)],
        unplanned=parent.has_plan and parent.feedback is None,
        unresolved=_unresolved(
            result.threads, approval.accepted_threads(conf.approved, parent.ticket)
        ),
    )


def _show_leftovers(stdout: TextIO, parent: ticket_mod.Ticket, left: Leftovers) -> None:
    """残っているものと、締めたら何が起きるかを人に見せて、y/N を促す。"""
    stdout.write(f"{parent.ticket}（{parent.title}）を締める。残っているもの:\n")
    for t in left.todo:
        stdout.write(f"  - 未着手の子 {t.ticket}（{t.title}）→ 取り消す\n")
    for ph in left.untouched:
        stdout.write(f"  - フェーズ {ph.label}: 手を付けていない → 省略の印\n")
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
    """未着手の子を取り消し、フェーズに印を置く。

    返すのは取り消した子、省略の印を置いた番号、済んだ扱いにした番号。
    途中で失敗したら None。そこまでの変更は戻さない（印は次に打てば重ねられる）。
    """
    cancelled: list[str] = []
    for t in left.todo:
        if ops.cancel(stdout, stderr, root, conf, t.ticket, f"wrapup: {reason.strip()}") != 0:
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
                conf.approved,
                parent.ticket,
                ph.number,
                approval.MARK_SKIPPED,
                {"by": "wrapup", "at": stamp},
            ):
                return None
            skipped.append(ph.number)
        elif ph in left.unreviewed:
            if not _mark(
                stderr,
                conf.approved,
                parent.ticket,
                ph.number,
                approval.MARK_REVIEWED,
                {"by": "wrapup", "at": stamp, "mr": mr_number, "accepted": []},
            ):
                return None
            settled.append(ph.number)
    return cancelled, skipped, settled


def _wrapup_drafts(
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
    """残りを写す issue の下書きと、MR へ残す note の下書きを書く。書けなければ理由。"""
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
    issue_path = os.path.join(conf.state, WRAPUP_ISSUE_FILE.format(parent=parent.ticket))
    failed = _write_text(issue_path, "\n".join(issue))
    if failed:
        return failed
    note = [
        MARKER_WRAPUP,
        f"利用者が締めた（{stamp}）: {reason.strip()}",
        f"取り消した子: {', '.join(cancelled) or '無し'} / 省略したフェーズ: "
        f"{', '.join(str(n) for n in skipped) or '無し'} / 受け入れた指摘: {len(accepted)} 件",
        "残りは別の issue に写す。親が片付けて ready を打てば Draft が外れる。"
        "マージは利用者が行う。",
        "",
    ]
    note_path = os.path.join(conf.state, WRAPUP_NOTE_FILE.format(parent=parent.ticket))
    return _write_text(note_path, "\n".join(note))


def wip_root(conf: settings.Settings) -> str:
    """途中の作業を置く場所。提案の置き場（`wip/tickets`）のいちばん上の階層。

    マージのときにはここを丸ごと消す。途中の記録（チケットの置き場、調査や設計の文書）は
    既定のブランチに残さない。残す場所はマージリクエストと issue。
    """
    rel = (conf.tickets or settings.DEFAULT_TICKETS).replace("\\", "/").strip("/")
    return rel.split("/")[0] if rel else "wip"


def _merge_problems(tree_root: str, conf: settings.Settings) -> list[str]:
    """マージに進む前に作業ツリーの側で満たしていること。

    途中の作業の置き場が追跡から消えていること、未コミットが無いこと、push 済みであること。
    人がマージするときに見るのはリモートの HEAD なので、手元にだけあるものは無いのと同じ。
    """
    problems: list[str] = []
    if not os.path.isdir(tree_root):
        return [f"親の作業ツリーが無い ({tree_root})"]
    wip = wip_root(conf)
    rc, tracked = _git(tree_root, ["ls-files", "--", wip])
    if rc == 0 and tracked.strip():
        n = len(tracked.strip().splitlines())
        problems.append(
            f"`{wip}/` に追跡されているファイルが {n} 件ある。"
            "途中の作業は既定のブランチに残さない。"
            f"'sh .claude/scripts/ccnavi-git.sh rm -r {wip}' で消してコミットする"
        )
    rc, status = _git(tree_root, ["status", "--porcelain", "--untracked-files=no"])
    if rc != 0 or status.strip():
        problems.append("親の作業ツリーに未コミットの変更がある")
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
        closed, _ = approval.copies(conf.approved, closed=True)
        found = tree.lookup(approval.by_id(closed), t.name)
        if found is not None and not found.is_child:
            return found
    stderr.write("ccnavi: ここは親チケットの作業ツリーではない（cwd から親を引けない）\n")
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
        stderr.write("ccnavi: ここは親チケットの作業ツリーではない（cwd から親を引けない）\n")
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
    """フェーズの印を置く。置けなければ言って False。"""
    failed = approval.write_mark(approved_dir, parent, number, kind, data)
    if failed:
        stderr.write(f"ccnavi: 印を置けない: {failed}\n")
        return False
    return True


def _parent_mark(stderr: TextIO, approved_dir: str, parent: str, name: str, data: dict) -> bool:
    """親の印を置く。置けなければ言って False。"""
    failed = approval.write_parent_mark(approved_dir, parent, name, data)
    if failed:
        stderr.write(f"ccnavi: 印を置けない: {failed}\n")
        return False
    return True


def _unmet(tree_root: str, ph: phase.Phase) -> list[str]:
    """依頼の前提のうち、作業ツリーの中で分かるもの。"""
    unmet: list[str] = []
    if not ph.ended:
        unmet.append("フェーズが終わっていない（todo/ か doing/ に子が残っている）")
    for child in ph.tickets:
        if ph.states.get(child.ticket) != ticket_mod.DONE:
            continue
        # 子のブランチは識別子と同じ名前。作業ツリーではなくブランチを引くので、
        # 作業ツリーを消しても検査から外れない。引けなければ前提の未充足。
        rc, sha = _git(tree_root, ["rev-parse", "--verify", f"{child.ticket}^{{commit}}"])
        if rc != 0 or not sha.strip():
            unmet.append(f"子 {child.ticket} のブランチを確かめられない（消えている）")
            continue
        rc, _ = _git(tree_root, ["merge-base", "--is-ancestor", sha.strip(), "HEAD"])
        if rc != 0:
            unmet.append(f"子 {child.ticket} のブランチが親に取り込まれていない")
    # 未追跡は数えない。依頼文そのものを作業ツリーに置く形が普通にあり、それが
    # 前提を落とすと依頼文を書く場所が無くなる。未追跡はマージリクエストに載らない。
    rc, status = _git(tree_root, ["status", "--porcelain", "--untracked-files=no"])
    if rc != 0 or status.strip():
        unmet.append("親の作業ツリーに未コミットの変更がある")
    branch = _branch(tree_root)
    if not branch:
        unmet.append("親ブランチの名前を読めない")
    else:
        rc, ahead = _git(tree_root, ["rev-list", f"origin/{branch}..HEAD"])
        if rc != 0 or ahead.strip():
            unmet.append("親ブランチの HEAD が push されていない")
    return unmet


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
    """依頼したのと同じマージリクエストの写しか。違うものでゲートを開けない。"""
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

    付いた時刻では絞らない。依頼より後のものだけを数えていた版は、
    指摘が残ったまま「子をもう 1 本足して承認してもらい、依頼をやり直す」だけで
    前回の指摘が数から消えた。人が解決も受け入れもしていないのに通る形になる。

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


def _moved_since_request(tree_root: str, requested_mark: dict) -> str:
    """依頼の後に親の HEAD が動いたか。動いていれば、その説明。

    人が見たのは依頼時の HEAD。その後に積んだコミットは誰も見ていないので、
    それを「レビュー済み」に含めない。
    """
    rc, head = _git(tree_root, ["rev-parse", "HEAD"])
    head = head.strip()
    if rc != 0:
        return "親の HEAD を読めない"
    recorded = str(requested_mark.get("head") or "")
    if recorded and head != recorded:
        return f"依頼の後に親の HEAD が動いている（依頼時 {recorded[:12]}、いま {head[:12]}）"
    branch = _branch(tree_root)
    rc, ahead = _git(tree_root, ["rev-list", f"origin/{branch}..HEAD"]) if branch else (1, "")
    if rc != 0 or ahead.strip():
        return "親ブランチの HEAD が push されていない"
    return ""


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
