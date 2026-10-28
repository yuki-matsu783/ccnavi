"""レビューの依頼と確認。`ccnavi review prepare|requested|confirm` と `ccnavi --reviewed`。

sh が渡す JSON の形と投稿の目印は `review_host`、残った指摘の決め方（`--reviewed` と `decide`）は
`review_decide`、親を閉じる操作（`ready` と `close-early`）は `review_close` に分けてある。
review_decide と review_close は review を読む末端で、review からは読まない。

## exe が見るのはワークツリーの中だけ

ここはネットワークに出ない。フェーズが終わっているか、子のブランチが親に入っているか、
未コミットが無いか、push 済みか、マーカーがどうなっているか。分かるのはそこまでで、
マージリクエストの中身は `.ccnavi/scripts/ccnavi-review.sh` が取ってきて JSON で渡す
（`--result <path>`）。その JSON の形が sh と exe の契約で、テストも同じ経路を通る。

API のパス、トークンの権限、ページング、セルフホストの差は実物に適用しないと決まらない。
exe が API を直接呼ぶと、その呼び出しが配布物に組み込まれ、動かなくなったときに exe を
作り直すしかない。
sh ならプロジェクトごとに直せる。

## 依頼は prepare と requested の 2 段

投稿の前に前提を全部確かめ（`prepare`）、投稿は sh がして、その結果でマーカーを置く
（`requested`）。段の名前が違えば、どちらで止まったかが exit code を見なくても分かる。
`prepare` はマーカー付きの本文を state の置き場に書き出し、sh はそれを投稿する。

## 変更要求はユーザのターミナルでも通せない

未解決スレッドはユーザが `--reviewed --accept-unresolved` で受け入れて進めるが、
変更要求（changes requested）のレビューが残っている間はマーカーを置かない。
「このままではマージしない」の意思表示を、別の人がターミナルから上書きできるようにはしない。

## 未解決の指摘は、付いた時刻で絞らない

数えるのは「いま解決されていない指摘」全部。依頼より後のものだけを数えると、
指摘が残ったまま「子をもう 1 本足して承認してもらい、依頼をやり直す」だけで前回の
指摘が数から消える。ユーザが解決も受け入れもしていないのに通ってしまう。
除くのは機構自身の投稿と、ユーザが受け入れたものだけ。受け入れたものは、decide・close-early では
ホスト上で解決済みのスレッド、手で動かした子の確定では受け入れ台帳（accepted.json）に版つきで足したもの。
台帳の指摘は、版と最後のコメントの id が同じ間だけ数えない。移動の後に付いた指摘は数える。

レビューの状態（変更要求）はレビュアーごとの最新だけを見る。こちらは時刻で
比べるので、ホストの `Z` と手元のオフセットをエポック秒に直してから並べる。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
from dataclasses import dataclass
from typing import TextIO

from ..infra import fsio, gitcmd, pathcase, pathsafe, settings, tree
from ..policy import texts
from . import (
    approval,
    approval_checks,
    approval_marks,
    approval_ops,
    configsync,
    handoff,
    ops_close,
    phase,
    phase_forms,
    review_host,
    skillsync,
    ticket_fields,
    ticket_model,
    ticket_places,
    via,
)

TIMEOUT_SECONDS = 15.0

# state の置き場に書く、投稿待ちの本文の名前。sh がこれを投稿する。
REQUEST_FILE = "review-request-{parent}-{phase}.md"
# まだマージリクエストが無いときに、sh がこれで作る。
# 依頼の本文とマージリクエストの下書きの末尾に必ず書く一文。下書きの置き場
# （ticket_places.HANDOFF）の変更を、レビュアーが読む対象に数えないため。
HANDOFF_NOTE = (
    f"{ticket_places.HANDOFF}/ 配下はユーザに渡すコピーで、レビューの対象外。integrate で消える。"
)

# 依頼の本文の末尾に、`HANDOFF_NOTE` のあとに足す固定の 1 文。fix で直した指摘のスレッドは、ホストの
# 上で未解決のまま残る（仕様）。直ったかを見て resolve するのはユーザで、エージェントは fix を理由に
# resolve しない。ホストを問わない書き方。keep・issue を選んだときに sh が返信して resolve する
# 仕組み（受け入れの返信と解決）はこの文の対象外。
# 依頼の目印のキーは、この文を足す前の本文と親のブランチの先頭から作る（文を足してもキーは
# 変わらないので、足す前に投稿した依頼を打ち直しても、二重に投稿されない）。
RESOLVE_NOTE = (
    "修正を確認したら、ユーザがホスト上で該当スレッドを resolve する。"
    "エージェントは fix を理由に resolve しない。"
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
    """前提を全部確かめ、投稿する本文を state の置き場に書き出す。標準出力はその置き場。"""
    found = _parent_phase(stderr, root, conf, cwd, phase_no)
    if found is None:
        return 1
    parent, ph = found
    if not conf.state:
        stderr.write("ccnavi: state の置き場が空。投稿する本文を置く場所がありません。\n")
        return 1
    tree_root = tree.worktree_path(root, parent.ticket)
    unmet = _unmet(root, tree_root, conf, ph)
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
            f"--phase {ph.review_at} で依頼してください"
        )
    if unmet:
        stderr.write(f"ccnavi: 依頼の前提が {len(unmet)} 件満たされていない\n")
        for line in unmet:
            stderr.write(f"  - {line}\n")
        return 1
    # 計画があれば、このレビューが含むフェーズを機械が先頭に書く。延期した分を
    # ユーザが読み落とさないように。
    body = _covered_header(root, conf, parent, ph) + body
    body = body.rstrip("\n") + "\n\n" + HANDOFF_NOTE + "\n"
    # 目印に、本文と親のブランチの先頭から作ったキーを入れる。sh は同じ目印の投稿が MR に
    # 既にあれば投稿し直さない（打ち直し・write-and-push のやり直しで依頼を二重にしない）。
    # 子を足してやり直した依頼は先頭が違うので、別のキーになる。
    head = gitcmd.run(tree_root, ["rev-parse", "HEAD"])
    key = hashlib.sha256(
        (body + "\n" + (head.out.strip() if head.ok else "")).encode("utf-8")
    ).hexdigest()[:16]
    marker = f"{review_host.MARKER_REQUEST}{parent.ticket}:{phase_no} key={key} -->\n"
    path = os.path.join(conf.state, REQUEST_FILE.format(parent=parent.ticket, phase=phase_no))
    failed = fsio.write_text(path, marker + body + "\n" + RESOLVE_NOTE + "\n", newline="\n")
    if failed:
        stderr.write(f"ccnavi: 本文を書き出せない ({failed})\n")
        return 1
    # 1 行が依頼の本文のパス。マージリクエストは作らない（無ければ sh が止める）。
    stdout.write(path + "\n")
    return 0


def requested(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    phase_no: int,
    result_path: str,
) -> int:
    """投稿の結果を受けて、依頼のマーカーを置く。

    処理フロー:
    1. 親とフェーズを `_parent_phase` で得る。依頼済みで出し直せないなら
       （`_already_requested`）止める
    2. 投稿の結果（`_result_with_mr`）を読む。投稿の url が無ければ、マーカーは置かず止める
    3. 投稿の後に前提が崩れていないか（`_unmet`）を見る。崩れていれば、マーカーは置かず止める
    4. HEAD と、投稿の情報（MR・url・ホスト・時刻・投稿者）を `_mark` で依頼のマーカーに書く
    5. 依頼の待ちのファイルを消し、MR に注記を残す（`_note_mr`）
    6. 依頼した（または依頼し直した）ことを 1 行で言う
    """
    # 親のフェーズと、依頼済みでないことを確かめる。
    found = _parent_phase(stderr, root, conf, cwd, phase_no)
    if found is None:
        return 1
    parent, ph = found
    tree_root = tree.worktree_path(root, parent.ticket)
    already = _already_requested(tree_root, conf, ph, phase_no)
    if already:
        stderr.write(f"ccnavi: {already}\n")
        return 1
    again = approval_marks.MARK_REQUESTED in ph.marks
    # 投稿の結果（url を含む）を読む。url が無ければ、投稿されていないとしてマーカーは置かない。
    result = _result_with_mr(stderr, result_path)
    if result is None:
        return 1
    if not result.url:
        stderr.write(
            "ccnavi: 結果に投稿の url がありません。投稿されていないならマーカーは置かない\n"
        )
        return 1
    # 投稿の後も、依頼の前提（子の合流・未コミット・未 push）が崩れていないか見る。
    unmet = _unmet(root, tree_root, conf, ph)
    if unmet:
        stderr.write("ccnavi: 投稿の後に前提が成り立たなくなった。マーカーは置かない\n")
        for line in unmet:
            stderr.write(f"  - {line}\n")
        return 1
    # 依頼のマーカーを置く。HEAD と、投稿の情報を書く。
    rc, head = _git(tree_root, ["rev-parse", "HEAD"])
    # since はホストの時計。手元の時計と比べると、依頼直後の指摘が「依頼より前」と
    # 判定され、気づかないうちに除かれる。
    if not _mark(
        stderr,
        approval.home_dir(conf, root, parent.ticket, "", project=parent.project),
        parent.ticket,
        phase_no,
        approval_marks.MARK_REQUESTED,
        {
            "head": head.strip(),
            "mr": result.mr.number,
            "url": result.url,
            "host": result.host,
            "since": result.created_at,
            # 依頼を投稿したアカウント。GitLab で ccnavi の投稿のスレッドを見分けるのに使う
            # （分からなければ書かず、見分けずに数える。`_unresolved`）
            **({"poster": result.author} if result.author else {}),
        },
    ):
        return 1
    # 依頼の待ちのファイルを消し、MR に注記を残す。
    if conf.state:
        fsio.remove(
            os.path.join(conf.state, REQUEST_FILE.format(parent=parent.ticket, phase=phase_no))
        )
    _note_mr(stderr, root, conf, parent, result.mr.number)
    # 依頼した（または依頼し直した）ことを 1 行で言う。
    done = "依頼し直した" if again else "依頼した"
    stdout.write(
        f"OK: レビューを{done}（{result.mr.url or result.url}）。"
        f"{phase_forms.TURN_DEFINED}を終えてユーザを待ってください\n"
        f"{phase.PARALLEL_REQUEST_NOTE}\n"
    )
    return 0


def _note_mr(
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    parent: ticket_model.Ticket,
    number: int,
) -> None:
    """親の承認済みチケットの `mr:` が空なら、この MR の番号を書き足す。

    書くのは空のときだけ。同じ値なら何もせず、違う値なら上書きせず警告する。
    書き足しに失敗してもレビューの依頼は進む（マーカーは置けている）。
    書いたパスは `fsio` が記録するので、write-and-push のコミットに入る。
    """
    path = approval.copy_path(
        approval.home_dir(conf, root, parent.ticket, "", project=parent.project), parent.ticket
    )
    failed, result = approval_ops.note_mr(path, number)
    if failed:
        stderr.write(
            f"ccnavi: 警告: {parent.ticket} の `mr:` に MR !{number} を保存できませんでした:"
            f" {failed}\n"
        )
    elif result == ticket_fields.MR_SET:
        stderr.write(f"ccnavi: {parent.ticket} の `mr:` に MR !{number} を追記しました\n")
    elif result == ticket_fields.MR_DIFFERS:
        stderr.write(
            f"ccnavi: 警告: {parent.ticket} の `mr:` に別の値が既にある。上書きしない。"
            f"今回のレビューの MR は !{number}。\n{texts.say(root, 'NEXT_REVIEW_MR_DIFFERS')}\n"
        )
    elif result == ticket_fields.MR_UNREADABLE:
        stderr.write(
            f"ccnavi: 警告: {parent.ticket} の frontmatter を読めず、`mr:` を保存できませんでした\n"
        )


def reviewed_mark(
    mr: int, accepted: list[str], account: str = "", via: str = "", stamp: str = ""
) -> dict:
    """レビュー済みのマーカーの中身（`phases/<親>/<N>.reviewed`）。

    `confirm`（コア）と `decide` が使う。欄の順序は `{mr, accepted, actor, via, at}` で、
    Chrome のレビュー済みも同じ形で書く。
    `actor` は付けたユーザ（アカウント）が分かるときだけ、`via` は経路が分かるときだけ書く
    （Chrome は `via: chrome`。手元の `confirm` は `--actor` があるときだけ `actor` と `via: cli` を
    渡し、無ければマーカーは前と同じバイト列）。`stamp` が空なら `at` を
    書かず、`approval_marks.write_mark` が今の時刻を入れる。
    `accepted` 欄は受け入れた指摘のキーの記録。受け入れた指摘はホストで解決済みにしてあるので、
    確認はホストの状態だけを数える。
    """
    mark: dict = {"mr": mr, "accepted": list(accepted)}
    if account:
        mark["actor"] = account
    if via:
        mark["via"] = via
    if stamp:
        mark["at"] = stamp
    return mark


def matching_problems(result: review_host.Result | None, requested_mark: dict) -> list[str]:
    """依頼したのと同じマージリクエストを取得した結果か。違えばその説明（標準エラーの行）。"""
    if result is None or result.mr is None:
        return ["ccnavi: 結果にマージリクエストがありません。"]
    if not requested_mark.get("host") or not requested_mark.get("mr"):
        # 照合できない依頼の記録で通すと、別の MR のスレッドでレビュー済みにできる
        return [
            "ccnavi: 依頼の記録にホストかマージリクエストの番号がありません。依頼し直してください"
        ]
    if result.host != requested_mark.get("host"):
        return [
            f"ccnavi: 依頼したホスト（{requested_mark.get('host')}）と"
            f"結果のホスト（{result.host}）が違う"
        ]
    if str(requested_mark["mr"]) != str(result.mr.number):
        return [
            f"ccnavi: 依頼したマージリクエスト（{requested_mark['mr']}）と"
            f"結果のもの（{result.mr.number}）が違う"
        ]
    return []


def review_problems(
    root: str,
    conf: settings.Settings,
    parent: ticket_model.Ticket,
    phase_no: int,
    result: review_host.Result,
) -> list[str]:
    """レビュー済みにできない理由（変更要求のレビュー、未解決のスレッド）。標準エラーの行。"""
    changes = [
        r
        for r in review_host.effective(result.reviews)
        if r.state.upper() == review_host.CHANGES_REQUESTED
    ]
    if changes:
        return [
            f"ccnavi: {review_host.changes_requested_note(result.host)}。"
            "decide でも通せない。変更要求があるため、レビュー済みにはできません。"
            "修正する場合は同じフェーズに新しい子を提案し、ユーザの承認を受けてください",
            *[f"  - {r.url}" for r in changes],
        ]
    unresolved = review_host._unresolved(
        result.threads,
        result.host,
        _poster(parent, phase_no, root, conf),
        ledger_of(root, conf, parent),
    )
    if not unresolved:
        return []
    lines = [f"ccnavi: 未解決のスレッドが {len(unresolved)} 件残っている"]
    lines += [f"  - {thread_label(t)}" for t in unresolved]
    review_sh = settings.script_command(root, "ccnavi-review.sh")
    if _is_last_feedback_review(parent, phase_no):
        # フィードバック対応の最後のレビュー。新しいフィードバック作業フェーズは
        # 足せない。同じフェーズでやり直すか、別の issue に切り出すか。
        lines += [
            "フィードバック対応の最後のレビューです。選べるのは 2 つ。",
            f"  - 同じフェーズ {phase_no} に子を追加して承認を受け、やり直す（差し戻し）",
            f"  - ユーザが '{review_sh} decide {phase_no}'（ボードの「決める」）で"
            "残りを受け入れるか、別の issue に回す",
            "新しいフィードバック作業フェーズは足せません。",
        ]
    else:
        lines.append(
            "ユーザに解決してもらって再実行する。または、ユーザがターミナルで "
            f"'{review_sh} decide {phase_no}'（ボードの「決める」）を使い、指摘ごとに"
            "受け入れて進むか、続きの子チケットで直すかを選ぶ"
        )
    return lines


def changes_requested(result: review_host.Result) -> list[review_host.Review]:
    """有効な変更要求のレビュー（レビュアーごとの最新）。"""
    return [
        r
        for r in review_host.effective(result.reviews)
        if r.state.upper() == review_host.CHANGES_REQUESTED
    ]


def unresolved_for(
    root: str,
    conf: settings.Settings,
    parent: ticket_model.Ticket,
    phase_no: int,
    result: review_host.Result,
) -> list[review_host.Thread]:
    """数える未解決の指摘（機構の投稿と、受け入れ台帳が受け入れ済みとするものを除く）。"""
    return review_host._unresolved(
        result.threads,
        result.host,
        _poster(parent, phase_no, root, conf),
        ledger_of(root, conf, parent),
    )


def ledger_of(root: str, conf: settings.Settings, parent: ticket_model.Ticket) -> list[dict]:
    """親の受け入れ台帳の項目。読めなければ空（読めない台帳で未解決を隠さない）。"""
    home = approval.home_dir_read(conf, root, parent.ticket, "", project=parent.project)
    entries, problem = approval_marks.read_ledger(home, parent.ticket)
    return [] if problem else entries


def _settled_by_confirm() -> str:
    """confirm が子を動かした経路。Chrome のレビュー済みも同じコアを通るので、経路で見分ける。"""
    return "chrome" if via.via() == via.VIA_CHROME else "confirm"


def settle_and_mark(
    stage,
    root: str,
    conf: settings.Settings,
    parent: ticket_model.Ticket,
    ph: phase.Phase,
    mark: dict,
    ledger: list[dict] | None = None,
) -> tuple[str, str] | None:
    """レビュー済みで書くもの（子を `done/` へ、
    レビュー済みのマーカー）を fsio の書き込みを溜める段に並べる。

    順は `_settle_children` → `_mark` と同じ（子を先に動かす。理由は `_settle_children`）。
    止まったら（"", 理由）。Writer(FS) は `ccnavi: <理由>` と言って止まる（前と同じ文面）。
    """
    with fsio.policy(
        on_fail=fsio.FAIL_STOP, ticket="", message="{reason}", prefix="", undo=(), places=""
    ):
        moved, failed = approval_ops.settle_review(
            conf, root, parent.ticket, [*ph.covers, ph.number], by=_settled_by_confirm()
        )
        if failed:
            return "", failed
        if moved:
            stage.line(
                f"レビュー待ちの子を {conf.approved}/{ticket_model.DONE}/ へ動かした: "
                f"{', '.join(moved)}"
            )
        home = approval.home_dir(conf, root, parent.ticket, "", project=parent.project)
        if ledger:
            # 台帳を先に書く（マーカーだけが置かれて、受け入れた指摘が数えられる状態を作らない）
            failed = approval_marks.add_ledger(home, parent.ticket, ledger)
            if failed:
                return "", f"受け入れ台帳を書けない: {failed}"
        with fsio.policy(prefix="マーカーを置けない: "):
            failed = approval_marks.write_mark(
                home, parent.ticket, ph.number, approval_marks.MARK_REVIEWED, mark
            )
        if failed:
            return "", f"マーカーを置けない: {failed}"
    stage.line(f"OK: フェーズ {ph.number} はレビュー済み。先へ進める")
    return None


def _settle_children(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    parent: ticket_model.Ticket,
    ph: phase.Phase,
    by: str,
) -> bool:
    """レビューが済んだフェーズ（延期を引き受けた分を含む）のレビュー待ちの子を `done/` へ動かす。

    `by` は動かした経路（confirm・decide・close-early・chrome）。子の `settled_by` に書く。

    ユーザが見たことの記録はマーカーで、場所の移動はそれに合わせたもの。
    動かせなければ言って False。

    呼ぶ側はこれをレビュー済みのマーカーより先に呼ぶ。マーカーを先に置くと、動かせなかった
    ときに「レビュー済みなのに子が `review/` に残る」状態になり、`confirm` は「レビュー済み」で
    拒むので、取り出す操作が無くなる。
    逆順なら、動いたのにマーカーが置けなくても、次の `confirm` が置き直す（動かす分は空）。
    """
    moved, failed = approval_ops.settle_review(
        conf, root, parent.ticket, [*ph.covers, ph.number], by=by
    )
    if failed:
        stderr.write(f"ccnavi: {failed}\n")
        return False
    if moved:
        stdout.write(
            f"レビュー待ちの子を {conf.approved}/{ticket_model.DONE}/ へ動かした: "
            f"{', '.join(moved)}\n"
        )
    return True


def thread_key(t: review_host.Thread) -> str:
    """選択をスレッドに対応づけるキー。URL を先に使い、無ければスレッドの id。"""
    return review_host.thread_key(t)


def thread_version(t: review_host.Thread) -> str:
    """指摘の版。SHA-256(キー + 改行 + 正規化した最初のコメントの本文) の先頭 32 桁。

    正規化するのは改行コード（CRLF・CR を LF に）と Unicode の NFC だけ。本文が変わるか、キーが
    変わると版が変わる。見せた指摘と、適用の直前に取り直した指摘が同じかを、この版で照合する
    （CLI と Chrome が同じ Python のコアでこの 1 か所を使う）。
    """
    return review_host.thread_version(t)


def _approved_rel(conf: settings.Settings) -> str:
    """ccnavi 自身の置き場（ツリーのルートからの相対）。末尾の `/` は付けない。"""
    return (conf.approved or settings.DEFAULT_APPROVED).strip("/")


def _own_places(conf: settings.Settings) -> tuple[str, ...]:
    """ccnavi 自身が動かす置き場。承認済みチケットと提案の 2 つ。

    チケットは 2 つの置き場を行き来するので、片方だけを外すと `finish` の移動
    （`doing/` → `review/`）が「ユーザが見るものが動いた」に数えられる。
    """
    return (_approved_rel(conf), (conf.tickets or settings.DEFAULT_TICKETS).strip("/"))


def _is_own_place(conf: settings.Settings, path: str) -> bool:
    return any(path.startswith(place + "/") for place in _own_places(conf))


def _unseen_by_review(conf: settings.Settings, path: str) -> bool:
    """依頼の後に変わっても「ユーザが見るものが動いた」に数えない場所か。

    ccnavi 自身の置き場と、ELI5 の HTML の置き場（`wip/eli5/`）。ELI5 は依頼に添える
    説明で、成果物ではない（`ready` の前に消える）。直すたびに依頼し直させると、指摘を受けて
    説明を直すたびに依頼のコメントが増え、レビュー済みにしにくくなる。代わりに、
    直した ELI5 をユーザが見直す保証は無くなる。

    未コミットの検査（`_dirty_paths`）と push の検査（`_unpushed`）はこれを使わない。
    ELI5 の未コミットは sh が依頼のときに止め、push していない ELI5 はマージリクエストの差分に無い
    （crit push が届かない）。
    """
    # git の `-z` の表記をそのまま見る（`\` を `/` に直さない。`_outside_approved` と同じ理由）。
    return _is_own_place(conf, path) or path.startswith(ticket_places.ELI5 + "/")


# 依頼のマーカーに記録された HEAD。git の revision として使う前に、この形であることを求める。
_SHA = re.compile(r"^[0-9a-f]{7,64}$")


def is_sha(value: str) -> bool:
    """マーカーの `head` が sha の形をしているか。

    マーカーは親のブランチに乗って他の機械から届くファイルなので、中身を
    git の revision としてそのまま渡さない。`HEAD` や `@` のような「今」を指す値は
    `head..HEAD` を空差分にして「変更が無い」と判定させ、`-` で始まる値は git のオプションに
    なってしまう。
    """
    return bool(_SHA.match(value))


def _outside_approved(
    tree_root: str, conf: settings.Settings, ref: str, eli5: bool = False
) -> tuple[list[str], str]:
    """`ref..HEAD` の差分のうち、ccnavi 自身の置き場の外にあるパス。2 つめは読めなかった理由。

    NUL 区切りで読む理由は phase_scope.scope_findings と同じ。既定の出力は非 ASCII を
    引用符で囲んで 8 進でエスケープするので、そのまま照らし合わせると日本語のファイルが
    置き場の外か中かを読み違える。`--no-renames` を付けるのは、改名を 1 行にまとめられると
    移動元のパスが出力に出ず、置き場の外から中へ動かしたファイルが「置き場の中だけ」に見えるため。
    `--ignore-submodules=none` は、`.gitmodules` の `ignore = all` で submodule の
    進みが差分にまったく出なくなるのを防ぐ（`.gitmodules` は追跡されるので、外から届く）。

    `-z` が返すパスはもう正規化されているので、こちらでは何も直さない。空白を除いたり
    `\\` を `/` に直したりすると、`.ccnavi\\tickets\\x.py` という名前のファイル 1 個が
    置き場の中のパスになってしまい、除外の側に入る。

    `eli5` なら ELI5 の置き場も外す（`_unseen_by_review`。依頼済みの見分けだけが使う）。
    """
    paths, failed = _diff_paths(tree_root, ref)
    keep = _unseen_by_review if eli5 else _is_own_place
    return [p for p in paths if not keep(conf, p)], failed


def _diff_paths(tree_root: str, ref: str) -> tuple[list[str], str]:
    """`ref..HEAD` で変わったパス（置き場で絞らない）。2 つめは読めなかった理由。"""
    if not ref:
        return [], "比べる相手がありません。"
    rc, out = _git(
        tree_root,
        ["diff", "--name-only", "--no-renames", "--ignore-submodules=none", "-z", f"{ref}..HEAD"],
    )
    if rc != 0:
        return [], f"{ref[:12]} からの差分を読めない"
    return [path for path in out.split("\0") if path], ""


def _dirty_paths(tree_root: str, conf: settings.Settings, wip_removals: bool = False) -> list[str]:
    """ワークツリーの、ccnavi 自身の置き場を除いた未コミットの変更のパス。読めなければ `[""]`。

    承認済みチケットとマーカーはこのワークツリーの `.ccnavi/` に置かれ、git が追跡する。
    マーカーはフェーズの終わりに hook が書くので、ここを数えると「レビューを頼む前に
    マーカーをコミットしろ」と言い続けることになる。
    マーカーと承認済みチケットをコミットして push するのは `ccnavi-review.sh` と
    `ccnavi-user-agree.sh` の仕事で、ユーザの作業による未コミットの変更とは別に扱う。
    """
    rc, status = _git(
        tree_root, ["status", "--porcelain", "-z", "--untracked-files=no", "--no-renames"]
    )
    if rc != 0:
        return [""]
    # `-z` で読む。既定の出力は非 ASCII を引用符で囲んで 8 進でエスケープするので、置き場の中の
    # 日本語のファイルが置き場の外と判定され、「未コミットがある」で依頼が止まる。
    # `--no-renames` は、改名のときに出る 2 つめのパス（移動元）が XY を持たない形で
    # 入り込むのを避けるため。phase_scope.scope_findings と同じ読み方。
    paths: list[str] = []
    for entry in status.split("\0"):
        if len(entry) > 3 and entry[2] == " " and not _is_own_place(conf, entry[3:]):
            if wip_removals and "D" in entry[:2] and in_wip(entry[3:]):
                # `ready` が消した（追跡済みの）`wip/` のファイル。write-and-push がコミットするか、
                # write-and-push の外では sh がコミットと push を求めて止める。打ち直しの ready
                # は止めない。
                continue
            paths.append(entry[3:])
    return paths


def _mirror_prefixes(conf: settings.Settings) -> tuple[str, ...]:
    """プロジェクトのツリーで ccnavi がコピーを置く場所。ルートからの相対パスで、末尾に `/` を付け、
    大文字小文字は畳んである。共通レイヤーのミラーと工程スキルの 2 つ。"""
    mirror = configsync.mirror_dir(conf, "").replace(os.sep, "/").strip("/")
    return (pathcase.fold(mirror + "/"), pathcase.fold(skillsync.SKILLS_DIR + "/"))


def _dirty_problem(conf: settings.Settings, paths: list[str]) -> str:
    """未コミットの変更があるために止めるときの文面。

    変更がミラーのコピー先（共通レイヤー・工程スキル）にあるなら、作業の変更ではなく、
    共通レイヤーとのずれである。承認の範囲を超えるコミットは促さず、
    揃える手順（ユーザの操作）を案内する。
    """
    base = "親のワークツリーに未コミットの変更がある"
    prefixes = _mirror_prefixes(conf)
    mirrored = [p for p in paths if pathcase.fold(p).startswith(prefixes)]
    if not mirrored:
        return base
    shown = "、".join(mirrored[:10]) + (
        f" ほか {len(mirrored) - 10} 件" if len(mirrored) > 10 else ""
    )
    others = len(paths) - len(mirrored)
    lead = (
        f"{base}（共通レイヤーのコピーとのずれ: {shown}）。ずれの分は作業による変更ではないため、"
        "コミットも取り消しもしない。"
    )
    if others:
        lead += f"ずれ以外の変更が {others} 件ある。そちらは作業の変更として扱う。"
    return (
        lead + "ユーザには、`ccnavi-user-mirror.sh <プロジェクト名>` で差分と値を確かめ、"
        "`--yes --expect <値>` で共通レイヤーのミラーを揃えて、統合先にコミットしてもらうよう頼む。"
        "`skills/` のずれはその操作では揃わないため、統合先で揃えてコミットしてもらう"
    )


def in_wip(path: str) -> bool:
    """git が出したパスが、途中の作業の置き場（`wip`）の下か。
    大文字小文字と `\\` の区切りを問わない。"""
    folded = path.lower()
    return folded == ticket_places.WIP_ROOT or folded.startswith(
        (ticket_places.WIP_ROOT + "/", ticket_places.WIP_ROOT + "\\")
    )


def wip_tracked(tree_root: str) -> list[str] | None:
    """途中の作業の置き場（`wip`）の下で追跡されているファイル（git の表記）。読めなければ None。

    大文字小文字を区別せずに見る。区別しない FS で `WIP/eli5/` を先に作ると、範囲の判定
    （`tree.relative` は書いたパスを返す）は `wip/eli5/` として通すのに、
    git には `WIP/...` で入る。
    区別して見ると、それが既定のブランチまで残る。名前に `\\` を含む 1 ファイル（`wip\\eli5\\x`。
    Linux / macOS では作れる）も `wip` の下と見なす。pathspec の `:(icase)` に
    頼らず全部を `-z` で読んで絞るのは、`\\` の名前を pathspec で拾えないのと、
    git の版に依らないため。
    """
    rc, tracked = _git(tree_root, ["ls-files", "-z"])
    if rc != 0:
        return None
    return [p for p in tracked.split("\0") if p and in_wip(p)]


def _wip_untracked(tree_root: str) -> list[str] | None:
    """`wip` の下の未追跡のファイル（無視したものを除く）。読めなければ None。"""
    rc, out = _git(tree_root, ["ls-files", "-z", "--others", "--exclude-standard"])
    if rc != 0:
        return None
    return [p for p in out.split("\0") if p and in_wip(p)]


def _merge_problems(tree_root: str, conf: settings.Settings, root: str) -> list[str]:
    """マージに進む前にワークツリーの側で満たしていること。root は文面の sh のパスに使う。

    未コミットが無いこと、push 済みであること。ユーザがマージするときに見るのはリモートの HEAD
    なので、手元にだけあるものは無いのと同じ。

    途中の作業の置き場（`wip/`）の追跡済みのファイルは、`ready` が消してコミットに含める
    （`remove_wip`）ので、ここでは止めない。消すのは追跡済みのものだけで、履歴から戻せる。
    未追跡のファイルは履歴に無く、消すと戻せないので、残っていれば名指しして止める。
    """
    problems: list[str] = []
    if not os.path.isdir(tree_root):
        return [f"親のワークツリーが無い ({tree_root})"]
    wip = ticket_places.WIP_ROOT
    tracked = wip_tracked(tree_root)
    if tracked is None:
        problems.append(f"`{wip}/` が追跡されているかを読めない")
    else:
        # 退避より前に止める（remove_wip も同じ確かめをするが、そこでは退避が済んでいる）。
        odd = [r for r in tracked if "\n" in r or "\r" in r or _escapes(tree_root, r)]
        if odd:
            named = "、".join(repr(r) if ("\n" in r or "\r" in r) else r for r in odd[:10])
            problems.append(
                f"`{wip}/` の追跡済みのファイルのうち、途中のディレクトリがリンクで"
                f"ツリーの外を指すか、名前に改行を含むものがある（{named}）。削除するとツリーの外のファイルまで削除するおそれがあるので止めた。"
                "リンクを元のディレクトリに戻すか、ユーザに確かめてから再実行してください"
            )
    untracked = _wip_untracked(tree_root)
    if untracked is None:
        problems.append(f"`{wip}/` に未追跡のファイルがあるかを読めない")
    elif untracked:
        shown = "、".join(untracked[:10]) + (
            f" ほか {len(untracked) - 10} 件" if len(untracked) > 10 else ""
        )
        problems.append(
            f"`{wip}/` に未追跡のファイルが {len(untracked)} 件ある（{shown}）。"
            f"ready が削除するのは `{wip}/` の追跡済みのファイルだけ（履歴から戻せるもの）で、"
            "未追跡のものは戻せないので削除せずに中止しました。必要なものは別の場所へ移し、"
            "要らないものはユーザに確かめて削除してから再実行してください"
        )
    # 保護対象の下書き（`wip/handoff/`）のうち、取り込み先と中身が違う（取り込んでいない）もの。
    # ready は `wip/` の追跡済みのファイルを消すので、黙って消さずに止める。未追跡のものは上で言う。
    told = {pathcase.fold(p) for p in untracked or []}
    waiting = [p for p in handoff.pending(tree_root) if pathcase.fold(p) not in told]
    if waiting:
        shown = "、".join(waiting[:10]) + (
            f" ほか {len(waiting) - 10} 件" if len(waiting) > 10 else ""
        )
        problems.append(
            f"`{handoff.HANDOFF_REL}/` に取り込んでいない保護対象の下書きが {len(waiting)} 件ある"
            f"（{shown}）。ready は `{wip}/` を削除するので中止しました。ユーザに"
            "`ccnavi-user-handoff.sh` で取り込んでもらうか、要らない下書きを削除してから"
            "再実行してください"
        )
    dirty = _dirty_paths(tree_root, conf, wip_removals=True)
    if dirty:
        problems.append(_dirty_problem(conf, dirty))
    branch = _branch(tree_root)
    if not branch:
        problems.append("親ブランチの名前を読めない")
    else:
        rc, ahead = _git(tree_root, ["rev-list", f"origin/{branch}..HEAD"])
        if rc != 0 or ahead.strip():
            problems.append("親ブランチの HEAD が push されていない")
    return problems


def remove_wip(tree_root: str) -> tuple[list[str], str, str]:
    """`wip` の下の追跡済みのファイルを消す（`ready` が退避と同じ流れで使う）。

    返すのは（消したパス、消す前の HEAD、止まった理由）。消すのは fsio を通すので、write-and-push
    の一覧に載り、
    write-and-push がコミットして push する。未追跡のものは `_merge_problems` が先に止めているので、
    ここでは
    追跡済みのものだけを見る。消す前の HEAD は、消したものを履歴から戻すときの版（案内に使う）。
    空になったディレクトリは下から消す（git は空のディレクトリを持たない）。
    """
    names = wip_tracked(tree_root)
    if names is None:
        return [], "", f"`{ticket_places.WIP_ROOT}/` が追跡されているかを読めない"
    if not names:
        return [], "", ""
    # 消す前に全部を確かめ、1 本でもツリーの外へ出るものがあれば何も消さない。追跡済みのファイルの
    # 途中のディレクトリがリンク（無視されていると未追跡にも出ない）に置き換わっていると、
    # そのまま消せばリンクの先（ツリーの外）のファイルを消す。改行を含む名前は、sh へ渡す
    # 1 行 1 本の答え（`wip <パス>`）を壊すので、これも消さずに止める。
    outside = [rel for rel in names if "\n" in rel or "\r" in rel or _escapes(tree_root, rel)]
    if outside:
        named = "、".join(repr(r) if ("\n" in r or "\r" in r) else r for r in outside[:10])
        return (
            [],
            "",
            "途中のディレクトリがリンクでツリーの外を指すか、名前に改行を含むので、"
            f"何も削除しませんでした（{named}）。"
            "リンクを元のディレクトリに戻すか、ユーザに確かめてから再実行してください",
        )
    rc, head = _git(tree_root, ["rev-parse", "HEAD"])
    head = head.strip() if rc == 0 else ""
    removed: list[str] = []
    for rel in names:
        full = os.path.join(tree_root, *rel.split("/"))
        if not os.path.lexists(full):
            # ワークツリーから先に消えていた（削除が未コミット）。消したものとして一覧に載せ、
            # write-and-push のコミットに含める。
            fsio.note_removed(full)
            removed.append(rel)
            continue
        failed = fsio.unlink(full)
        if failed:
            return removed, head, f"{rel} を削除できません（{failed}）"
        removed.append(rel)
    for top in sorted({p.split("/", 1)[0] for p in names}):
        _prune_empty(os.path.join(tree_root, top))
    return removed, head, ""


def _escapes(tree_root: str, rel: str) -> bool:
    """`rel`（git の表記）の途中のディレクトリがリンクか、親ディレクトリを解くとツリーの外か。

    最後の名前そのものがリンクなのは構わない（`os.remove` はリンクだけを消す）。
    """
    parts = rel.split("/")
    if pathsafe.first_link(tree_root, parts, include_last=False):
        return True
    return pathsafe.escapes(tree_root, os.path.dirname(os.path.join(tree_root, *parts)))


def _prune_empty(directory: str) -> None:
    """空になったディレクトリを下から消す。リンクはたどらない。"""
    if os.path.islink(directory) or not os.path.isdir(directory):
        return
    for here, _dirs, _files in sorted(os.walk(directory), key=lambda w: -len(w[0])):
        with contextlib.suppress(OSError):
            os.rmdir(here)


def _parent_any(
    stderr: TextIO, root: str, conf: settings.Settings, cwd: str
) -> ticket_model.Ticket | None:
    """cwd の親。閉じた承認済みチケットも引く（親を閉じたあとに Draft を外す `ready` のため）。"""
    parent = phase.parent_for_cwd(root, conf, cwd)
    if parent is not None:
        return None if ops_close.family_stopped(stderr, root, conf, parent) else parent
    t = tree.tree_of(root, cwd or os.getcwd(), conf.projects)
    if t is not None and not t.is_main:
        closed, _ = approval.scan(conf, root, closed=True)
        found = tree.lookup(approval_checks.by_id(closed), t.name)
        if found is not None and not found.is_child:
            return None if ops_close.family_stopped(stderr, root, conf, found) else found
    stderr.write("ccnavi: ここは親チケットのワークツリーではない（cwd から親を引けない）\n")
    return None


def _write_text(path: str, text: str) -> str:
    failed = fsio.write_text(path, text, newline="\n")
    return f"下書きを書き出せない ({path}: {failed})" if failed else ""


def _covered_header(
    root: str, conf: settings.Settings, parent: ticket_model.Ticket, ph: phase.Phase
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
            if p.risk_body:
                risks.append(f"{p.label}: {p.risk_body}" if len(numbers) > 1 else p.risk_body)
    head = ""
    if parent.has_plan:
        if len(labels) <= 1 and not ph.covers:
            head = f"このレビューが含むフェーズ: {ph.label}\n"
        else:
            head = "このレビューが含むフェーズ: " + "、".join(labels) + "\n"
    if risks:
        head += "このレビューのリスク: " + " / ".join(risks) + "\n"
        if ph.risk_escalates:
            head += "（実績のリスクが高いので、宣言に関わらずレビュー必須の扱い）\n"
    return head + "\n" if head else ""


def _is_last_feedback_review(parent: ticket_model.Ticket, phase_no: int) -> bool:
    """フィードバック作業フェーズの最後のレビューか。"""
    if not parent.has_plan or not parent.feedback:
        return False
    last = len(parent.plan) + len(parent.feedback)
    return parent.review_at(last) == phase_no


# ---- 内部


def _parent(
    stderr: TextIO, root: str, conf: settings.Settings, cwd: str
) -> ticket_model.Ticket | None:
    parent = phase.parent_for_cwd(root, conf, cwd)
    if parent is None:
        stderr.write("ccnavi: ここは親チケットのワークツリーではない（cwd から親を引けない）\n")
        return None
    # 取り込み済みの親子のチケットが決まらない・閉じているなら、
    # 依頼・確認・行き先・早めに閉じたことのマーカーも置かない。
    if ops_close.family_stopped(stderr, root, conf, parent):
        return None
    return parent


def _phase(
    root: str, conf: settings.Settings, parent: ticket_model.Ticket, number: int
) -> phase.Phase | None:
    for ph in phase.phases_of(root, conf, parent.ticket):
        if ph.number == number:
            return ph
    return None


def _parent_phase(
    stderr: TextIO, root: str, conf: settings.Settings, cwd: str, number: int
) -> tuple[ticket_model.Ticket, phase.Phase] | None:
    """cwd の親と、その番号のフェーズ。どちらか無ければ言って None。"""
    parent = _parent(stderr, root, conf, cwd)
    if parent is None:
        return None
    ph = _phase(root, conf, parent, number)
    if ph is None:
        stderr.write(f"ccnavi: {parent.ticket} にフェーズ {number} の子がありません。\n")
        return None
    return parent, ph


def _result_with_mr(stderr: TextIO, path: str) -> review_host.Result | None:
    """取得した結果を読み、マージリクエストが入っていることまで確かめる。無ければ言って None。"""
    result = _result(stderr, path)
    if result is None or result.mr is None:
        stderr.write("ccnavi: 結果にマージリクエストがありません。\n")
        return None
    return result


def _mark(
    stderr: TextIO, approved_dir: str, parent: str, number: int, kind: str, data: dict
) -> bool:
    """フェーズのマーカーを置く。置けなければ言って False。"""
    failed = approval_marks.write_mark(approved_dir, parent, number, kind, data)
    if failed:
        stderr.write(f"ccnavi: マーカーを置けない: {failed}\n")
        return False
    return True


def _parent_mark(stderr: TextIO, approved_dir: str, parent: str, name: str, data: dict) -> bool:
    """親のマーカーを置く。置けなければ言って False。"""
    failed = approval_marks.write_parent_mark(approved_dir, parent, name, data)
    if failed:
        stderr.write(f"ccnavi: マーカーを置けない: {failed}\n")
        return False
    return True


def _unmet(root: str, tree_root: str, conf: settings.Settings, ph: phase.Phase) -> list[str]:
    """依頼の前提のうち、ワークツリーの中で分かるもの。"""
    unmet: list[str] = []
    if not ph.ended:
        unmet.append("フェーズが終わっていない（doing/ に子が残っている）")
    for child in ph.tickets:
        if ph.states.get(child.ticket) not in (ticket_model.REVIEW, ticket_model.DONE):
            continue
        # 取り消した子（CANCELLED）は上で外れる。cancel は子のブランチを親へ入れない。
        # すべての子が親のブランチに合流済みか。子のブランチは見ない（ローカルの子のブランチを
        # 消しても、別の PC で finish した子でも変わらない）。finish が書いた `merged_sha`
        # （合流した子の先頭）が、いまの親の HEAD の祖先かで確かめる。
        if not child.merged_sha:
            unmet.append(
                f"子 {child.ticket} が親のブランチに合流していない"
                "（finish が合流した記録 merged_sha が無い）"
            )
            continue
        rc, _ = _git(tree_root, ["merge-base", "--is-ancestor", child.merged_sha, "HEAD"])
        if rc != 0:
            unmet.append(
                f"子 {child.ticket} の合流した先頭（{child.merged_sha[:12]}）が親の HEAD に無い"
            )
            continue
        late = _unmerged_fix(root, tree_root, child)
        if late:
            unmet.append(
                f"子 {child.ticket} の手元のブランチに、合流されていない修正がある"
                f"（先頭 {late[:12]}。合流した先頭は {child.merged_sha[:12]}）。"
                "finish の後に積んだコミットは親のブランチに入らない。直すときは、同じフェーズに"
                "子を足してユーザの承認を受け、その子で直す"
            )
    # 未追跡は数えない。依頼文そのものをワークツリーに置く使い方が普通にあり、未追跡で
    # 前提を満たさないと判定すると依頼文を書く場所が無くなる。未追跡はマージリクエストに載らない。
    dirty = _dirty_paths(tree_root, conf)
    if dirty:
        unmet.append(_dirty_problem(conf, dirty))
    branch = _branch(tree_root)
    if not branch:
        unmet.append("親ブランチの名前を読めない")
    elif _unpushed(tree_root, conf, branch):
        unmet.append("親ブランチの HEAD が push されていない")
    return unmet


def _unmerged_fix(root: str, tree_root: str, child: ticket_model.Ticket) -> str:
    """finish の後に子のブランチへ積まれ、親に入っていないコミットの先頭。

    中身は `ops_close.unmerged_fix`。Stop の促しも同じ関数で確かめる。
    """
    return ops_close.unmerged_fix(root, tree_root, child, TIMEOUT_SECONDS)


def _unpushed(tree_root: str, conf: settings.Settings, branch: str) -> bool:
    """ユーザが見るものが、まだリモートに届いていないか。

    ccnavi 自身の置き場だけが手元に残っている形は、届いていると数える。ユーザがレビューで
    見るのはコードで、置き場をコミットして push するのは `ccnavi-user-push-approved.sh` の仕事
    （push が失敗してもコミットは残す）。数えると、レビュー待ちの間に失敗した push が
    次の依頼を止める。`confirm` の側（`_moved_since_request`）と同じ基準。

    `ready` の前提（`_merge_problems`）はこれを使わない。あちらはユーザがリモートを見て
    マージするところで、マーカーも本当に届いていないと他の機械へ渡らない。
    """
    rc, ahead = _git(tree_root, ["rev-list", f"origin/{branch}..HEAD"])
    if rc != 0:
        return True
    if not ahead.strip():
        return False
    changed, failed = _outside_approved(tree_root, conf, f"origin/{branch}")
    return bool(failed or changed)


def _result(stderr: TextIO, path: str) -> review_host.Result | None:
    if not path:
        stderr.write(
            "ccnavi: --result <json> を指定してください。リモートから取得した結果は sh が渡します。"
            "\n"
        )
        return None
    result = review_host.Result.load(path)
    if result.error:
        stderr.write(f"ccnavi: リモートを読めていない: {result.error}\n")
        return None
    return result


def _matching(stderr: TextIO, path: str, requested_mark: dict) -> review_host.Result | None:
    """依頼したのと同じマージリクエストを取得した結果か。違うもので先へ進めない。"""
    result = _result(stderr, path)
    if result is None:
        return None
    problems = matching_problems(result, requested_mark)
    for line in problems:
        stderr.write(line + "\n")
    return None if problems else result


def _poster(parent: ticket_model.Ticket, phase_no: int, root: str, conf: settings.Settings) -> str:
    """そのフェーズの依頼を投稿したアカウント（依頼の記録の `poster`）。無ければ空。"""
    ph = _phase(root, conf, parent, phase_no)
    mark = ph.marks.get(approval_marks.MARK_REQUESTED) if ph is not None else None
    return str((mark or {}).get("poster") or "")


def _branch(tree_root: str) -> str:
    rc, out = _git(tree_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    return out.strip() if rc == 0 else ""


def _moved_since_request(tree_root: str, conf: settings.Settings, requested_mark: dict) -> str:
    """依頼の後に、ユーザが見るものが動いたか。動いていれば、その説明（判定の文。理由の説明は
    `moved_explanation`）。`_moved_detail` の `text`。"""
    return _moved_detail(tree_root, conf, requested_mark).text


def _moved_detail(tree_root: str, conf: settings.Settings, requested_mark: dict) -> Moved:
    """依頼の後に、ユーザが見るものが動いたか。動いていれば、その説明と動いた理由の種類。

    ユーザが見たのは依頼時の HEAD。その後に積んだコミットは誰も見ていないので、
    それを「レビュー済み」に含めない。

    ただし ccnavi 自身の置き場（`.ccnavi/approved/` と `wip/proposals/`）だけを変えた
    コミットは、動いたと数えない。
    依頼のマーカーはそこに置かれ、親のブランチにコミットして他の PC に届ける前提のもの。
    数えると「依頼 → マーカー → コミット」の順のせいで、依頼の直後に必ず自分のマーカーの
    コミットで「動いた」と判定され、
    承認の push（`ccnavi-user-push-approved.sh`）が置き場をまとめてコミットするので、レビューを
    待っている間の承認でも依頼が無効になる。未コミットの側は `_dirty_paths` が同じ理由で外しており、
    基準をそこに揃える。ユーザがレビューで見るものは 1 バイトも変わらない。

    push の判定も同じ基準で見る。置き場だけが手元に残っていても、リモートにあるものと
    ユーザが見るものは同じ。

    判定（`text` が空かどうか）は理由の種類（`kind`）や一覧（`commits`）に左右されない。
    それらは止めたあとに足す説明だけ。
    """
    rc, head = _git(tree_root, ["rev-parse", "HEAD"])
    head = head.strip()
    if rc != 0:
        return Moved("親の HEAD を読めない", KIND_UNVERIFIED)
    recorded = str(requested_mark.get("head") or "")
    paths: list[str] | None = None
    ancestor = True
    if recorded and head != recorded and is_sha(recorded):
        found, failed = _diff_paths(tree_root, recorded)
        paths = None if failed else found
        ancestor = _git(tree_root, ["merge-base", "--is-ancestor", recorded, head])[0] == 0
    moved = moved_detail(conf, requested_mark, head, paths, ancestor)
    if moved.text:
        if moved.kind == KIND_SEEN:
            moved.commits = _unseen_commits(tree_root, conf, recorded)
        return moved
    branch = _branch(tree_root)
    if not branch or _unpushed(tree_root, conf, branch):
        return Moved("親ブランチの HEAD が push されていない")
    return Moved()


# 動いたと数えた理由の種類。説明（`moved_explanation`）の出し分けにだけ使い、判定には使わない。
KIND_SEEN = "seen"  # 依頼時の HEAD からの差分を確かめられ、レビューの対象に入るパスが変わっている
KIND_UNVERIFIED = "unverified"  # 差分を確かめられない（読めない・祖先でない・一覧が打ち切られた）


@dataclass
class Moved:
    """依頼の後に動いたかの答え。`text` が空なら動いていない（判定）。

    `kind` は動いた理由の種類（`KIND_*`。止めたあとの説明の出し分けだけに使う）、`commits` は
    手元の git で取れたときの、依頼時の HEAD から今の HEAD までのコミットの表示行
    （置き場と `wip/eli5/` だけを変えたものとマージコミットは除く。新しい順で最大
    `MAX_COMMITS` 件。取れなければ None）。
    """

    text: str = ""
    kind: str = ""
    commits: list[str] | None = None


def moved_since(
    conf: settings.Settings,
    requested_mark: dict,
    head: str,
    changed: list[str] | None,
    ancestor: bool = True,
) -> str:
    """依頼の後に、ユーザが見るものが動いたか（`_moved_since_request` の読みを除いた判定）。

    手元の confirm と Chrome のレビュー済みが同じこの関数で決める。
    `head` は親のブランチの今の先頭。`changed` は依頼時の先頭（マーカーの `head`）から `head` までに
    変わったパスで、置き場で絞る前のもの。読めなかった（差分を取れない、ホストの一覧が
    打ち切られた、依頼時のコミットが祖先でない）なら None で、動いたと数える。
    手元は git の差分、Chrome は compare API の変更の一覧から作る。
    `ancestor` は説明の出し分けだけに使う（判定は変えない）。
    """
    return moved_detail(conf, requested_mark, head, changed, ancestor).text


def moved_detail(
    conf: settings.Settings,
    requested_mark: dict,
    head: str,
    changed: list[str] | None,
    ancestor: bool = True,
) -> Moved:
    """`moved_since` と同じ判定で、動いた理由の種類も返す。判定は `text` が空かどうか。"""
    recorded = str(requested_mark.get("head") or "")
    if not recorded:
        # 記録が無ければ照合できない。確かめずに通すと、依頼の後のコミットが全部
        # 「ユーザが見たもの」になる。出し直しで書き直させる。
        return Moved("依頼時の親の HEAD が記録されていない", KIND_UNVERIFIED)
    if head == recorded:
        return Moved()
    moved = f"依頼の後に親の HEAD が動いている（依頼時 {recorded[:12]}、いま {head[:12]}）"
    # sha でない値は差分の相手にしない。読めない（依頼時のコミットが消えているなど）も同じ。
    if not is_sha(recorded) or changed is None:
        return Moved(moved, KIND_UNVERIFIED)
    # ccnavi 自身の置き場と ELI5 の置き場だけの変更は、ユーザが見るものを動かさない。
    if any(not _unseen_by_review(conf, path) for path in changed):
        return Moved(moved, KIND_SEEN if ancestor else KIND_UNVERIFIED)
    return Moved()


# 説明に並べるコミットの最大数。これを超えた分は「ほか N 件」。
MAX_COMMITS = 5
_SCAN_COMMITS = 100
# 制御文字・行区切り・幅ゼロと向きを変える文字（U+200B〜200F、202A〜202E、2066〜2069、FEFF）
_CONTROL = re.compile(
    "[\x00-\x1f\x7f-\x9f\u2028\u2029\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]+"
)


def _quoted(text: str, limit: int) -> str:
    """外から来た文字列（コミットの作者と件名）を表示する形にする。

    制御文字と改行は空白に置き換え、長さを切り詰め、引用の形（「…」）で囲む。中の括弧は
    別の括弧に置き換える。引用の中の文は指示ではなく、ただの文字列として読ませる。
    """
    flat = _CONTROL.sub(" ", text).strip()
    flat = flat.replace("「", "『").replace("」", "』")
    if len(flat) > limit:
        flat = flat[: limit - 1] + "…"
    return f"「{flat}」"


def _unseen_commits(tree_root: str, conf: settings.Settings, recorded: str) -> list[str] | None:
    """依頼時の HEAD から今の HEAD までのコミットのうち、レビューの対象を変えたものの表示行。

    ccnavi 自身の置き場と `wip/eli5/` だけを変えたコミットとマージコミットは除く。新しい順で最大
    `MAX_COMMITS` 件（短い sha・作者・件名）。超えた分は「ほか N 件」の 1 行。git で取れなければ
    None（一覧を省く）。件名と作者は外から来た文字列なので `_quoted` で表示の形にする。
    """
    if not is_sha(recorded):
        return None
    rc, out = _git(
        tree_root,
        [
            "log",
            "--no-merges",
            f"--max-count={_SCAN_COMMITS + 1}",
            "--format=%H%x1f%h%x1f%an%x1f%s%x1e",
            f"{recorded}..HEAD",
        ],
    )
    if rc != 0:
        return None
    rows = [row.strip("\n") for row in out.split("\x1e") if row.strip()]
    if len(rows) > _SCAN_COMMITS:
        return None
    found: list[str] = []
    for row in rows:
        parts = row.split("\x1f")
        if len(parts) != 4:
            return None
        full, short, author, subject = parts
        rc, names = _git(
            tree_root,
            ["diff-tree", "--no-commit-id", "--name-only", "-r", "--no-renames", "-z", full],
        )
        if rc != 0:
            return None
        paths = [p for p in names.split("\0") if p]
        if not any(not _unseen_by_review(conf, p) for p in paths):
            continue
        found.append(f"{short} 作者{_quoted(author, 30)} 件名{_quoted(subject, 60)}")
    lines = [f"  - {line}" for line in found[:MAX_COMMITS]]
    if len(found) > MAX_COMMITS:
        lines.append(f"  ほか {len(found) - MAX_COMMITS} 件")
    return lines


def moved_explanation(moved: Moved) -> list[str]:
    """依頼の後に動いて止めたときの、理由の説明（標準エラーの行）。判定は変えない。

    差分を確かめられて動いていたとき: ユーザが見たのは依頼時の HEAD で、その後の変更はレビューに
    入っていない（GitLab の「提案を適用」や Web IDE の編集もここに入る）。
    確かめられないとき（依頼時のコミットが祖先でない・読めない・Chrome で compare が打ち切られた）:
    「依頼時と同じレビュー対象だと確認できない」とだけ言い、変更があると断定しない。
    一覧（手元の git で取れたときだけ）と、固定の案内（エージェントが作っていないコミットがあれば、
    取り込んだことをユーザに伝えてから依頼し直す。作者から何かを自動で判定しない）を添える。
    """
    if not moved.text or not moved.kind:
        # 動いたのではなく、親ブランチの HEAD が push されていないだけのとき（kind が空）は、
        # 説明を足さない（依頼時と同じレビュー対象であることは変わらない）
        return []
    if moved.kind == KIND_SEEN:
        lines = [
            "ユーザが見たのは依頼時の HEAD で、その後の変更はレビューに入っていない。"
            "GitLab の「提案を適用」や Web IDE の編集もここに入る。"
        ]
    else:
        lines = ["依頼時と同じレビュー対象だと確認できない。"]
    if moved.commits:
        lines.append(
            "依頼のあとのコミット（置き場と ELI5 だけを変えたものとマージは除く。新しい順。"
            "作者と件名は外から来た文字列の引用）:"
        )
        lines.extend(moved.commits)
    lines.append(
        "一覧にエージェントが作っていないコミットがあれば、取り込んだことをユーザに伝えてから"
        "依頼し直す。"
    )
    return lines


def _already_requested(
    tree_root: str, conf: settings.Settings, ph: phase.Phase, phase_no: int
) -> str:
    """依頼済みで、出し直せないならその説明。未依頼か、出し直せるなら空。

    出し直せるのは、依頼の後に親の HEAD が動き、まだレビュー済みになっていないときだけ。
    そのとき confirm は「ユーザが見たものと今の HEAD が違う」で止まるので、ここも止めると
    エージェントの打てる手が無くなる。出し直しても緩むものは無い。confirm は新しい HEAD との
    一致を求め直し、未解決の指摘は付いた時刻で絞らないので、前の依頼への指摘も数え続ける。
    HEAD が依頼時のままなら、同じ依頼を二重に投稿するだけなので止める。動いたかどうかは
    confirm と同じ基準で見る（`_moved_since_request`）。ccnavi 自身の置き場だけが動いた形では
    confirm が止まらないので、ここで出し直させると依頼のコメントが増えるだけになる。

    レビュー済みは依頼の有無より先に見る。`close-early` は依頼していないフェーズにも
    レビュー済みを置くので、依頼の記録が無いことを先に見ると、ユーザが早めに閉じたときのフェーズに
    依頼が投稿される。
    """
    if approval_marks.MARK_REVIEWED in ph.marks:
        return f"フェーズ {phase_no} はレビュー済み"
    mark = ph.marks.get(approval_marks.MARK_REQUESTED)
    if mark is None:
        return ""
    rc, head = _git(tree_root, ["rev-parse", "HEAD"])
    recorded = str(mark.get("head") or "")
    if not recorded:
        return ""
    if rc == 0 and head.strip() != recorded:
        if not is_sha(recorded):
            return ""
        changed, failed = _outside_approved(tree_root, conf, recorded, eli5=True)
        if failed or changed:
            return ""
    return f"フェーズ {phase_no} は依頼済み（ユーザが見るものは依頼時のまま）"


def _redo_request(root: str, phase_no: int) -> str:
    """HEAD が動いたときの案内。push 済みの今の HEAD で依頼を出し直す。"""
    review_sh = settings.script_command(root, "ccnavi-review.sh")
    return (
        f"push してから '{review_sh} request --phase {phase_no} --body-file <依頼文> "
        f"--eli5 wip/eli5/phase-{phase_no}.html' で"
        "依頼を出し直してください"
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


def _first_line(body: str) -> str:
    line = body.strip().splitlines()[0] if body.strip() else ""
    return line[:120]


def thread_label(t: review_host.Thread) -> str:
    """一覧に出す 1 スレッドの表示。`<url> <ファイル>:<行> <最初の行>`。

    位置の無いスレッド（PR 全体へのコメントなど）は path が空で line が 0。そのときは
    ` :0 ` と出さず、位置を省く。行だけ無いときはファイル名だけを出す。
    """
    where = ""
    if t.path:
        where = f"{t.path}:{t.line}" if t.line else t.path
    return " ".join(part for part in (t.url, where, _first_line(t.body)) if part)
