"""承認の対象の指紋。承認の画面に出したものと、置き場へ書くものが同じであることを確かめる材料。

承認で書く本文（改版の写し）と、指紋に入れる読みの範囲（チケットと設定）を組む。
agree から分けた。agree を読まない。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import replace

from ..infra import fsio, settings, tree
from . import (
    agree_candidates,
    approval,
    approval_marks,
    history,
    syncstate,
    ticket_model,
    workflow,
)
from . import ticket as ticket_mod


def approval_digest(
    text: str,
    batch: list[agree_candidates.Candidate],
    read: dict[str, str] | None = None,
    carried_parts: list[bytes] | None = None,
) -> str:
    """承認のダイジェスト。承認画面の本文・判定が読んだ中身（`read_set`）・承認済みチケットに書き込む中身の
    SHA-256 の 16 進（小文字）。

    ボードは preview のダイジェストを `--yes` に `--digest` で返す。識別子だけを比べると、
    見せたあとに提案の範囲や計画が書き換わっても、同じ識別子なら承認が通る。
    本文だけを比べても、画面に出ないのに承認済みチケットへ書き込む欄（`issue`、Markdown の
    本文、知らない frontmatter の欄）は見せたあとに書き換えられる。

    **判定が読んだ中身（`read_set`）をダイジェストに含める。** 提案だけでなく、
    判定が読んだ承認済みチケット・マーカー・フェーズの種類・統合先の取り込み結果のどれかが見せたあとに
    変われば、ダイジェストが変わる（読んだ先が増えた・減ったも同じ）。全ブランチの先頭（`head_sha`）は
    入れない（無関係なコミットで承認が通らなくならないように）。`read` は `read_set` の返す形
    （`<ブランチ>:<相対パス>` → 中身のハッシュ）。

    一括のチケットの書き込む中身（`carried`）も残してダイジェストに含める。読んだ中身から決まるものだが、読みの
    記録（`fsio.reading`）を通らない読みが紛れても、書き込む中身の変化は取りこぼさないように。
    中身はバイト列で入れる。新規の承認は提案のバイト列をそのまま動かすので、改行や BOM だけの
    書き換えもダイジェストを変える。`carried` は呼び手が判定の読みの中で組んで渡す
    （`core.judge_approval`）。
    渡さなければここで組む。

    部分をそのままつながず、部分ごとのハッシュを件数と一緒に並べて、そのリストのハッシュを取る。
    区切りの文字でつなぐと、その文字が部分の中に出たときにつなぎ目をずらせる。Markdown の
    本文は生の制御文字（`\\x00` も）をそのまま通すので、どの文字も「中身に出ない」とは言えない。
    読んだ中身の部分は `<鍵>\\n<中身のハッシュ>`（ハッシュは 16 進の固定長なので、最後の改行で
    切れる）。
    """
    if carried_parts is None:
        carried_parts = [carried(cand) for cand in batch]
    parts = [text.encode("utf-8")] + list(carried_parts)
    if read is not None:
        parts += [f"{key}\n{digest}".encode() for key, digest in sorted(read.items())]
    lines = [str(len(parts))] + [hashlib.sha256(p).hexdigest() for p in parts]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def read_set(conf: settings.Settings, root: str, seen: dict[str, str]) -> dict[str, str]:
    """判定が読んだ中身（`fsio.reading` に溜めた内容）を、
    機械に依らない鍵に直す。

    鍵は `<リポジトリ>:<ブランチ>:<ツリーからの相対パス>`（"/" 区切り）。リポジトリは
    ワークスペース自身なら `self`、プロジェクトならその名前（取り込み状態の名前と同じ。ブランチ名が
    リポジトリをまたいで重なっても鍵は同じにならない。git の ref に `:` は使えない）。
    ブランチはそのツリーの HEAD が指すブランチ名（読めなければツリーの名前。ワークスペースルートの
    名前は空）で、`Changes.per_branch` と同じ決め方。ツリーは承認済みチケットを持ちうるもの全部
    （`trees`）で、最長一致。鍵に使ったツリーの HEAD の中身も `<リポジトリ>:<ブランチ>:(HEAD)` で
    入れる（切り離した HEAD の sha が変わればツリーの名前の鍵は同じでもダイジェストが変わる）。
    state の置き場の下は、取り込み状態（`sync/`）だけを `(控え):<相対パス>` で
    入れ、ほかの記録（セッションごとの一時の状態）は入れない（判定の入力ではなく、読むたびに
    変わりうる）。ワークスペースの外は絶対パスのまま。
    """
    # 溜めた読みのパスはリンクをたどった先（`fsio.note_read`）なので、比べる側も
    # たどった先に揃えてから大文字小文字をそろえる（macOS の /tmp のようなリンクを経たルート、
    # Windows の表記の揺れ）。
    held = sorted(
        ((_real(t.root), t) for t in approval.trees(conf, root)),
        key=lambda x: len(x[0]),
        reverse=True,
    )
    folded_roots = [(_folded(real), real, t) for real, t in held]
    state_real = _real(conf.state) if conf.state else ""
    state = _folded(state_real) if state_real else ""
    names: dict[str, str] = {}
    out: dict[str, str] = {}
    for path, digest in seen.items():
        folded = _folded(path)
        if state and (folded == state or folded.startswith(state + os.sep)):
            rel = os.path.relpath(path, state_real).replace(os.sep, "/")
            if rel.split("/", 1)[0] == syncstate.SYNC_DIR:
                out[f"(控え):{rel}"] = digest
            continue
        owner = next(
            (
                (real, t)
                for key, real, t in folded_roots
                if folded == key or folded.startswith(key + os.sep)
            ),
            None,
        )
        if owner is None:
            out[f"(外):{fsio.slashed(path)}"] = digest
            continue
        real, t = owner
        if real not in names:
            prefix = f"{syncstate.repo_key(t.project)}:{tree.branch_of(t.root) or t.name}"
            names[real] = prefix
            head = tree.head_text(t.root)
            out[f"{prefix}:(HEAD)"] = fsio.content_digest(head) if head is not None else "-"
        rel = os.path.relpath(path, real).replace(os.sep, "/")
        out[f"{names[real]}:{rel}"] = digest
    return out


# 判定の置き場と働きを決める設定（`settings.load` と Claude Code の設定）の読み先。
_SETTINGS_FILES = (
    "pyproject.toml",
    settings.LOCAL_FILE,
    os.path.join(".claude", "settings.json"),
    settings.LOCAL_CLAUDE_SETTINGS,
)
# 承認の判定に影響する設定の値（環境変数からも来るので、ファイルの中身とは別に値そのものを入れる）。
# 置き場のパスとチケット制御だけ。state の置き場（`state`）は入れない（取り込み状態は中身で
# `(控え):` に入る。置き場のパスだけが違う起動で見せ直しにならないように）。モードと承認の保護は
# 承認の答えを変えない（端末を求めるかどうか）ので入れない。
_SETTINGS_VALUES = ("tickets", "approved", "projects", "project_home")


def settings_read_set(conf: settings.Settings, root: str) -> dict[str, str]:
    """判定が読んだ設定（read_set に足す）。

    `settings.load` が読むファイル（`pyproject.toml`・`ccnavi.settings.local.json`）と
    Claude Code の設定（`.claude/settings.json`・`.claude/settings.local.json`）の中身を
    `(設定):<相対パス>` で、判定に影響する値（置き場のパス・チケット制御など。
    環境変数から来るもの）を `(設定値):<名前>` で入れる。
    見せたあとに置き場のパスや設定が変われば、同じ画面でもダイジェストが変わる。
    """
    out: dict[str, str] = {}
    for rel in _SETTINGS_FILES:
        try:
            with open(os.path.join(root, rel), "rb") as f:
                out[f"(設定):{rel.replace(os.sep, '/')}"] = fsio.content_digest(f.read())
        except OSError:
            out[f"(設定):{rel.replace(os.sep, '/')}"] = fsio.READ_ABSENT
    for name in _SETTINGS_VALUES:
        value = str(getattr(conf, name, "") or "")
        if value and os.path.isabs(value):
            inside = os.path.relpath(value, root)
            if not inside.startswith(".."):
                value = inside.replace(os.sep, "/")
        out[f"(設定値):{name}"] = fsio.content_digest(value)
    # チケット制御は書かれた値（空・enable）ではなく有効かどうかで入れる。
    out["(設定値):ticket_control"] = fsio.content_digest(str(conf.tickets_enabled))
    return out


def _real(path: str) -> str:
    try:
        return os.path.realpath(path)
    except OSError:
        return os.path.abspath(path)


def _folded(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def carried(cand: agree_candidates.Candidate) -> bytes:
    """承認で書き込む中身のバイト列。承認済みチケットと、親なら待ち方のファイル。

    新規は提案のバイト列そのもの（`approval.admit` が動かす中身）。読んだバイト列のダイジェストを
    判定の読み（`read_set`）にも入れる。読みの記録は改行を揃えた本文でダイジェストを取るので、それだけでは
    改行や BOM だけの書き換えを覆わない。改版は承認済みチケットの frontmatter の計画だけを差し替え、
    本文は承認済みチケットのものを残す（`revise_copy`）。待ち方は `phases/<親>/workflow.yml` に
    書く中身（`approval.workflow_bytes`）を後ろに足す。区切りは件数つきのダイジェストの並びで決まる
    （`approval_digest`）ので、ここでは長さを頭に付けてつなぐ。
    """
    t = cand.ticket
    if cand.is_revision and cand.current is not None:
        front, body = revised_front(cand.current, t), cand.current.body
        ticket_bytes = ticket_mod.render(replace(cand.current, raw=front, body=body)).encode(
            "utf-8"
        )
    else:
        ticket_bytes = fsio.read_bytes(t.path) or b""
        fsio.note_exact(t.path, ticket_bytes)
    wf = _workflow_to_write(cand)
    workflow_bytes = approval.workflow_bytes(wf) if wf is not None else b""
    return b"%d\n" % len(ticket_bytes) + ticket_bytes + workflow_bytes


def _workflow_to_write(cand: agree_candidates.Candidate) -> ticket_model.Workflow | None:
    """この承認で `phases/<親>/workflow.yml` に書く待ち方。書かないなら None。

    新規は計画を持つ親だけ。改版はいつも書く（計画か待ち方が変わったから改版になる）。
    """
    t = cand.ticket
    if t.is_child:
        return None
    if cand.is_revision or t.has_plan:
        return workflow.compute(t, cand.types)
    return None


def revise_copy(
    approved_dir: str,
    current: ticket_model.Ticket,
    revised: ticket_model.Ticket,
    feedback_planned: bool,
    types: dict | None = None,
) -> str:
    """承認済みチケットの計画を差し替え、待ち方を `phases/<親>/workflow.yml` に書き直す。

    範囲と本文はそのまま。改版の時刻はチケットに書かない（状態の履歴の `revised` に残る。
    フィードバック計画の改版は `feedback: true` を添える）。

    待ち方を先に書き、チケットを書く段で落ちたら待ち方を前の中身へ戻す。片方だけが新しい形
    （新しい計画に古い待ち方、古い計画に新しい待ち方）を残さないため。
    """
    held = approval.workflow_path(approved_dir, current.ticket)
    restore = ((held, fsio.read_bytes(held)),)
    failed = approval.write_workflow(approved_dir, current.ticket, workflow.compute(revised, types))
    if failed:
        return failed
    current.raw = revised_front(current, revised)
    with fsio.policy(restore=restore):
        failed = approval_marks.write_ticket(
            approval.copy_path(approved_dir, current.ticket), ticket_mod.render(current)
        )
    if failed:
        fsio.put_back(restore)
        return failed
    history.note(
        approved_dir,
        current.ticket,
        history.KIND_REVISED,
        ticket_model.DOING,
        ticket_model.DOING,
        feedback=True if feedback_planned else None,
    )
    return ""


def revised_front(current: ticket_model.Ticket, revised: ticket_model.Ticket) -> dict:
    """改版で書く frontmatter。承認済みチケットの frontmatter の計画を差し替えたコピー。

    `current` は書き換えない。承認のダイジェスト（`digest`）も同じものから組むので、見せた
    中身と書く中身が食い違わない。待ち方は書かない（`phases/<親>/workflow.yml` に書く）。
    前の版の承認済みチケットに残る `workflow:` 欄はそのまま残す（ファイルが在ればファイルを読む）。
    """
    front = dict(current.raw)
    front["plan"] = [item.as_raw() for item in revised.plan]
    if revised.feedback is not None:
        front["feedback"] = [item.as_raw() for item in revised.feedback]
    return front
