"""レビューの依頼と確認。`ccnavi review prepare|requested|confirm` と `ccnavi --reviewed`。

sh が渡す JSON の形と投稿の目印は `review_host`、残った指摘の決め方（`--reviewed` と `decide`）は
`review_decide`、親を閉じる操作（`ready` と `close-early`）は `review_close` に分けてある。
review_decide と review_close は review を読む末端で、review からは読まない。

## exe が見るのは作業ツリーの中だけ

ここはネットワークに出ない。フェーズが終わっているか、子のブランチが親に入っているか、
未コミットが無いか、push 済みか、マーカーがどうなっているか。分かるのはそこまでで、
マージリクエストの中身は `.ccnavi/scripts/ccnavi-review.sh` が取ってきて JSON で渡す
（`--result <path>`）。その JSON の形が sh と exe の契約で、テストも同じ経路を通る。

API のパス、トークンの権限、ページング、セルフホストの差は実物に当てないと決まらない。
exe が API を直接呼ぶと、その呼び出しが配布物に組み込まれ、動かなくなったときに exe を
作り直すしかない。
sh ならプロジェクトごとに直せる。

## 依頼は prepare と requested の 2 段

投稿の前に前提を全部確かめ（`prepare`）、投稿は sh がして、その結果でマーカーを置く
（`requested`）。段の名前が違えば、どちらで止まったかが exit code を見なくても分かる。
`prepare` はマーカー付きの本文を state の置き場に書き出し、sh はそれを投稿する。

## 変更要求はユーザの端末でも通せない

未解決スレッドはユーザが `--reviewed --accept-unresolved` で受け入れて進めるが、
変更要求（changes requested）のレビューが残っている間はマーカーを置かない。
「このままではマージしない」の意思表示を、別の人が端末から上書きできるようにはしない。

## 未解決の指摘は、付いた時刻で絞らない

数えるのは「いま解決されていない指摘」全部。依頼より後のものだけを数えると、
指摘が残ったまま「子をもう 1 本足して承認してもらい、依頼をやり直す」だけで前回の
指摘が数から消える。ユーザが解決も受け入れもしていないのに通ってしまう。
除くのは機構自身の投稿と、ユーザが受け入れたものだけ。

レビューの状態（変更要求）はレビュアーごとの最新だけを見る。こちらは時刻で
比べるので、ホストの `Z` と手元のオフセットをエポック秒に直してから並べる。
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import TextIO

from ..infra import fsio, gitcmd, settings, tree
from . import (
    approval,
    approval_checks,
    approval_marks,
    approval_ops,
    ops_close,
    phase,
    phase_forms,
    review_host,
    ticket_model,
    ticket_places,
)
from . import ticket as ticket_mod

TIMEOUT_SECONDS = 15.0

# state の置き場に書く、投稿待ちの本文の名前。sh がこれを投稿する。
REQUEST_FILE = "review-request-{parent}-{phase}.md"
# まだマージリクエストが無いときに、sh がこれで作る。
MR_FILE = "review-mr-{parent}.md"


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
        stderr.write("ccnavi: state の置き場が空。投稿する本文を置く場所が無い\n")
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
    # 目印に、本文と親のブランチの先頭から作った鍵を入れる。sh は同じ目印の投稿が MR に
    # 既にあれば投稿し直さない（打ち直し・C1 のやり直しで依頼を二重にしない）。
    # 子を足してやり直した依頼は先頭が違うので、別の鍵になる。
    head = gitcmd.run(tree_root, ["rev-parse", "HEAD"])
    key = hashlib.sha256(
        (body + "\n" + (head.out.strip() if head.ok else "")).encode("utf-8")
    ).hexdigest()[:16]
    marker = f"{review_host.MARKER_REQUEST}{parent.ticket}:{phase_no} key={key} -->\n"
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


def mr_draft(parent: ticket_model.Ticket) -> str:
    """マージリクエストの下書き。1 行目が題、空行のあとが本文。

    まだ無ければ sh がこれで作る。ユーザがレビューのときに見るのはこの入れ物なので、
    親チケットが持っている材料（題・理由・本文・元の課題）をそのまま書き写す。
    下書き（Draft）で作るのは、統合を決めるのがユーザだから。題から Draft を外して
    マージするところまでがユーザの手に残る。
    """
    title = parent.title.strip() or parent.ticket
    lines = [f"Draft: {title}", ""]
    if parent.issue:
        # 別のリポジトリの課題は `Closes owner/repo#N`（`issue:` に `owner/repo#N` を許している）
        lines += [f"Closes {ticket_mod.issue_label(parent)}", ""]
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
    again = approval_marks.MARK_REQUESTED in ph.marks
    result = _result_with_mr(stderr, result_path)
    if result is None:
        return 1
    if not result.url:
        stderr.write("ccnavi: 結果に投稿の url が無い。投稿されていないならマーカーは置かない\n")
        return 1
    # 投稿とマーカーの間に HEAD が動いていないか。
    # 動いていれば、ユーザが見るものとマーカーが食い違う。
    unmet = _unmet(tree_root, conf, ph)
    if unmet:
        stderr.write("ccnavi: 投稿の後に前提が崩れた。マーカーは置かない\n")
        for line in unmet:
            stderr.write(f"  - {line}\n")
        return 1
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
    if conf.state:
        fsio.remove(
            os.path.join(conf.state, REQUEST_FILE.format(parent=parent.ticket, phase=phase_no))
        )
    done = "依頼し直した" if again else "依頼した"
    stdout.write(
        f"OK: レビューを{done}（{result.mr.url or result.url}）。"
        f"{phase_forms.TURN_DEFINED}を終えてユーザを待ってください\n"
    )
    return 0


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
        return ["ccnavi: 結果にマージリクエストが無い"]
    if not requested_mark.get("host") or not requested_mark.get("mr"):
        # 照合できない依頼の記録で通すと、別の MR のスレッドでレビュー済みにできる
        return ["ccnavi: 依頼の記録にホストかマージリクエストの番号が無い。依頼し直してください"]
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
            "ccnavi: 変更要求のレビューが立っている。decide でも通せない。"
            "レビュアーの approve / dismiss を待ってください",
            *[f"  - {r.url}" for r in changes],
        ]
    unresolved = review_host._unresolved(
        result.threads,
        approval_marks.accepted_threads(
            approval.home_dir(conf, root, parent.ticket, "", project=parent.project),
            parent.ticket,
            phase_no,
            parent,
        ),
        result.host,
        _poster(parent, phase_no, root, conf),
    )
    if not unresolved:
        return []
    lines = [f"ccnavi: 未解決のスレッドが {len(unresolved)} 件残っている"]
    lines += [f"  - {thread_label(t)}" for t in unresolved]
    review_sh = settings.script_command(root, "ccnavi-review.sh")
    if _is_last_feedback_review(parent, phase_no):
        # フィードバック対応の最後のレビュー。新しいフィードバック作業フェーズは
        # 足せない。同じフェーズでやり直すか、別の issue に切り出すか（設計 9.11）。
        lines += [
            "フィードバック対応の最後のレビューです。道は 2 つ。",
            f"  - 同じフェーズ {phase_no} に子を足して承認を受け、やり直す（差し戻し）",
            f"  - ユーザが '{review_sh} decide {phase_no}'（ボードの「決める」）で"
            "残りを受け入れるか、別の issue に回す",
            "新しいフィードバック作業フェーズは足せません。",
        ]
    else:
        lines.append(
            "解決してもらって再実行するか、ユーザが端末で "
            f"'{review_sh} decide {phase_no}'（ボードの「決める」）で、指摘ごとに"
            "受け入れて進むか、続きの子チケットで直すかを選ぶ"
        )
    return lines


def settle_and_mark(
    stage,
    root: str,
    conf: settings.Settings,
    parent: ticket_model.Ticket,
    ph: phase.Phase,
    mark: dict,
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
            conf, root, parent.ticket, [*ph.covers, ph.number]
        )
        if failed:
            return "", failed
        if moved:
            stage.line(
                f"レビュー待ちの子を {conf.approved}/{ticket_model.DONE}/ へ動かした: "
                f"{', '.join(moved)}"
            )
        home = approval.home_dir(conf, root, parent.ticket, "", project=parent.project)
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
) -> bool:
    """レビューが済んだフェーズ（延期を引き受けた分を含む）のレビュー待ちの子を `done/` へ動かす。

    ユーザが見たことの記録はマーカーで、場所の移動はそれに合わせたもの。
    動かせなければ言って False。

    呼ぶ側はこれをレビュー済みのマーカーより先に呼ぶ。マーカーを先に置くと、動かせなかった
    ときに「レビュー済みなのに子が `review/` に残る」状態になり、`confirm` は「レビュー済み」で
    拒み、`--reviewed --chat` も「すでにレビュー済み」で戻るので、取り出す操作が無くなる。
    逆順なら、動いたのにマーカーが置けなくても、次の `confirm` が置き直す（動かす分は空）。
    """
    moved, failed = approval_ops.settle_review(conf, root, parent.ticket, [*ph.covers, ph.number])
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
    """選択をスレッドに対応づける鍵。受け入れの記録と同じく、URL を先に使う。"""
    return t.url or t.id


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

    未コミットの検査（`_dirty`）と push の検査（`_unpushed`）はこれを使わない。ELI5 の未コミットは
    sh が依頼のときに止め、push していない ELI5 はマージリクエストの差分に無い
    （crit push が届かない）。
    """
    # git の `-z` の表記をそのまま見る（`\` を `/` に直さない。`_outside_approved` と同じ理由）。
    return _is_own_place(conf, path) or path.startswith(ticket_places.ELI5 + "/")


# 依頼のマーカーに記録された HEAD。git の revision として使う前に、この形であることを求める。
_SHA = re.compile(r"^[0-9a-f]{7,64}$")


def _is_sha(value: str) -> bool:
    """マーカーの `head` が sha の形をしているか。

    マーカーは親のブランチに乗って他の機械から届くファイル（設計 9.2）なので、中身を
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

    `-z` が返すパスはもう正規化されているので、こちらでは何も直さない。空白を落としたり
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
        return [], "比べる相手が無い"
    rc, out = _git(
        tree_root,
        ["diff", "--name-only", "--no-renames", "--ignore-submodules=none", "-z", f"{ref}..HEAD"],
    )
    if rc != 0:
        return [], f"{ref[:12]} からの差分を読めない"
    return [path for path in out.split("\0") if path], ""


def _dirty(tree_root: str, conf: settings.Settings) -> bool:
    """ワークツリーに未コミットの変更があるか。ccnavi 自身の置き場は数えない。

    承認済みチケットとマーカーはこのワークツリーの `.ccnavi/` に置かれ、git が追跡する（設計 9.2）。
    マーカーはフェーズの終わりに hook が書くので、ここを数えると「レビューを頼む前に
    マーカーをコミットしろ」と言い続けることになる。
    マーカーと承認済みチケットをコミットして push するのは`ccnavi-review.sh` と
    `ccnavi-agree.sh` の仕事で、ユーザの作業による未コミットの変更とは別に扱う。
    """
    rc, status = _git(
        tree_root, ["status", "--porcelain", "-z", "--untracked-files=no", "--no-renames"]
    )
    if rc != 0:
        return True
    # `-z` で読む。既定の出力は非 ASCII を引用符で囲んで 8 進でエスケープするので、置き場の中の
    # 日本語のファイルが置き場の外と判定され、「未コミットがある」で依頼が止まる。
    # `--no-renames` は、改名のときに出る 2 つめのパス（移動元）が XY を持たない形で
    # 入り込むのを避けるため。phase_scope.scope_findings と同じ読み方。
    for entry in status.split("\0"):
        if len(entry) > 3 and entry[2] == " " and not _is_own_place(conf, entry[3:]):
            return True
    return False


def _in_wip(path: str) -> bool:
    """git が出したパスが、途中の作業の置き場（`wip`）の下か。
    大文字小文字と `\\` の区切りを問わない。"""
    folded = path.lower()
    return folded == ticket_places.WIP_ROOT or folded.startswith(
        (ticket_places.WIP_ROOT + "/", ticket_places.WIP_ROOT + "\\")
    )


def _merge_problems(tree_root: str, conf: settings.Settings, root: str) -> list[str]:
    """マージに進む前にワークツリーの側で満たしていること。root は文面の sh のパスに使う。

    途中の作業の置き場が追跡から消えていること、未コミットが無いこと、push 済みであること。
    ユーザがマージするときに見るのはリモートの HEAD なので、手元にだけあるものは無いのと同じ。
    """
    problems: list[str] = []
    if not os.path.isdir(tree_root):
        return [f"親のワークツリーが無い ({tree_root})"]
    wip = ticket_places.WIP_ROOT
    # 大文字小文字を区別せずに見る。区別しない FS で `WIP/eli5/` を先に作ると、範囲の判定
    # （`tree.relative` は書いたパスを返す）は `wip/eli5/` として通すのに、
    # git には `WIP/...` で入る。
    # 区別して見ると、それが既定のブランチまで残る。名前に `\` を含む 1 ファイル（`wip\eli5\x`。
    # Linux / macOS では作れる）も `wip` の下と見なして止める。pathspec の `:(icase)` に
    # 頼らず全部を `-z` で読んで絞るのは、`\` の名前を pathspec で拾えないのと、
    # git の版に依らないため。
    rc, tracked = _git(tree_root, ["ls-files", "-z"])
    names = [p for p in tracked.split("\0") if p and _in_wip(p)] if rc == 0 else []
    if rc != 0:
        problems.append(f"`{wip}/` が追跡されているかを読めない")
    elif names:
        tops = sorted({p.split("/", 1)[0] for p in names})
        git_sh = settings.script_command(root, "ccnavi-git.sh")
        removes = " と ".join(f"'{git_sh} rm -r {top}'" for top in tops)
        problems.append(
            f"`{wip}/` に追跡されているファイルが {len(names)} 件ある。"
            "途中の作業は既定のブランチに残さない。"
            f"{removes} で消してコミットしてください"
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
            head += "（実績のリスクが高いので、宣言に関わらずレビューが要る扱い）\n"
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
        stderr.write(f"ccnavi: {parent.ticket} にフェーズ {number} の子が無い\n")
        return None
    return parent, ph


def _result_with_mr(stderr: TextIO, path: str) -> review_host.Result | None:
    """取得した結果を読み、マージリクエストが入っていることまで確かめる。無ければ言って None。"""
    result = _result(stderr, path)
    if result is None or result.mr is None:
        stderr.write("ccnavi: 結果にマージリクエストが無い\n")
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


def _unmet(tree_root: str, conf: settings.Settings, ph: phase.Phase) -> list[str]:
    """依頼の前提のうち、ワークツリーの中で分かるもの。"""
    unmet: list[str] = []
    if not ph.ended:
        unmet.append("フェーズが終わっていない（doing/ に子が残っている）")
    for child in ph.tickets:
        if ph.states.get(child.ticket) not in (ticket_model.REVIEW, ticket_model.DONE):
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
    # 未追跡は数えない。依頼文そのものをワークツリーに置く使い方が普通にあり、未追跡で
    # 前提を満たさないと判定すると依頼文を書く場所が無くなる。未追跡はマージリクエストに載らない。
    if _dirty(tree_root, conf):
        unmet.append("親のワークツリーに未コミットの変更がある")
    branch = _branch(tree_root)
    if not branch:
        unmet.append("親ブランチの名前を読めない")
    elif _unpushed(tree_root, conf, branch):
        unmet.append("親ブランチの HEAD が push されていない")
    return unmet


def _unpushed(tree_root: str, conf: settings.Settings, branch: str) -> bool:
    """ユーザが見るものが、まだリモートに届いていないか。

    ccnavi 自身の置き場だけが手元に残っている形は、届いていると数える。ユーザがレビューで
    見るのはコードで、置き場をコミットして push するのは `ccnavi-push-approved.sh` の仕事
    （push が落ちてもコミットは残す）。数えると、レビュー待ちの間に落ちた push が次の依頼を止める。
    `confirm` の側（`_moved_since_request`）と同じ基準。

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
        stderr.write("ccnavi: --result <json> が要る。リモートから取得した結果は sh が渡す\n")
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
    """依頼の後に、ユーザが見るものが動いたか。動いていれば、その説明。

    ユーザが見たのは依頼時の HEAD。その後に積んだコミットは誰も見ていないので、
    それを「レビュー済み」に含めない。

    ただし ccnavi 自身の置き場（`.ccnavi/approved/` と `wip/proposals/`）だけを変えた
    コミットは、動いたと数えない。
    依頼のマーカーはそこに置かれ、親のブランチにコミットして他の PC に届ける前提のもの（設計 9.2）。
    数えると「依頼 → マーカー → コミット」の順のせいで、依頼の直後に必ず自分のマーカーの
    コミットで「動いた」と判定され、
    承認の push（`ccnavi-push-approved.sh`）が置き場をまとめてコミットするので、レビューを
    待っている間の承認でも依頼が無効になる。未コミットの側は `_dirty` が同じ理由で外しており、
    基準をそこに揃える。ユーザがレビューで見るものは 1 バイトも変わらない。

    push の判定も同じ基準で見る。置き場だけが手元に残っていても、リモートにあるものと
    ユーザが見るものは同じ。
    """
    rc, head = _git(tree_root, ["rev-parse", "HEAD"])
    head = head.strip()
    if rc != 0:
        return "親の HEAD を読めない"
    recorded = str(requested_mark.get("head") or "")
    paths: list[str] | None = None
    if recorded and head != recorded and _is_sha(recorded):
        found, failed = _diff_paths(tree_root, recorded)
        paths = None if failed else found
    moved = moved_since(conf, requested_mark, head, paths)
    if moved:
        return moved
    branch = _branch(tree_root)
    if not branch or _unpushed(tree_root, conf, branch):
        return "親ブランチの HEAD が push されていない"
    return ""


def moved_since(
    conf: settings.Settings, requested_mark: dict, head: str, changed: list[str] | None
) -> str:
    """依頼の後に、ユーザが見るものが動いたか（`_moved_since_request` の読みを除いた判定）。

    手元の confirm と Chrome のレビュー済みが同じこの関数で決める。
    `head` は親のブランチの今の先頭。`changed` は依頼時の先頭（マーカーの `head`）から `head` までに
    変わったパスで、置き場で絞る前のもの。読めなかった（差分を取れない、ホストの一覧が
    打ち切られた、依頼時のコミットが祖先でない）なら None で、動いたと数える。
    手元は git の差分、Chrome は compare API の変更の一覧から作る。
    """
    recorded = str(requested_mark.get("head") or "")
    if not recorded:
        # 記録が無ければ照合できない。確かめずに通すと、依頼の後のコミットが全部
        # 「ユーザが見たもの」になる。出し直しで書き直させる。
        return "依頼時の親の HEAD が記録されていない"
    if head == recorded:
        return ""
    moved = f"依頼の後に親の HEAD が動いている（依頼時 {recorded[:12]}、いま {head[:12]}）"
    # sha でない値は差分の相手にしない。読めない（依頼時のコミットが消えているなど）も同じ。
    if not _is_sha(recorded) or changed is None:
        return moved
    # ccnavi 自身の置き場と ELI5 の置き場だけの変更は、ユーザが見るものを動かさない。
    if any(not _unseen_by_review(conf, path) for path in changed):
        return moved
    return ""


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
        if not _is_sha(recorded):
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
