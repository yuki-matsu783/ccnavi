"""判定のコアを通す手元の承認の入口（`--agree` の 4 つの枝）。

端末・ボードの `--yes`・`--preview`・`--verify` と、対話の承認。入力・判定・書き込みは
`core_base`、再エクスポートは `core`。コアを通す入口をコアより下の段（agree）に置くと、
import が循環する（tests/core/test_module_layers.py）。

"""

from __future__ import annotations

import io
import json
import os
from typing import TextIO

from ..infra import fsio, modes, settings
from . import (
    agree,
    agree_candidates,
    agree_screen,
    approval,
    core_base,
    worktrees,
)

# ボードの承認（`--agree --yes`）が通らなかった根拠の種別。付録 A の `CORE_APPROVE_*`。
# 確認（`CORE_CONFIRM_*`）と同じく、文面の頭に
# `[<コード>]` で載せる。
# `--digest` が無い。
CODE_APPROVE_NO_DIGEST = "CORE_APPROVE_NO_DIGEST"
# 見せた一覧（識別子）と今の一覧が違う。
CODE_APPROVE_LIST_CHANGED = "CORE_APPROVE_LIST_CHANGED"
# 識別子は同じで、見せた中身（ダイジェスト）が今のものと違う。
CODE_APPROVE_DIGEST_CHANGED = "CORE_APPROVE_DIGEST_CHANGED"
# 承認するものが無い。
CODE_APPROVE_NOTHING = "CORE_APPROVE_NOTHING"
# 書く途中で止まった（置いたものは残る）。
CODE_APPROVE_STOPPED = "CORE_APPROVE_STOPPED"

# 見せたあとに承認待ちの提案が更新されたときに、利用者へ出す知らせ（端末・ボード・Chrome で共通）。
UPDATED_NOTICE = "内容が更新されました。"


def refusal_code(verdict: core_base.Verdict) -> str:
    """見せたものを比べた判定（`judge_approval`）で承認が通らない根拠の種別。通るなら空。

    手元の `--agree --yes`（`approve_yes`）と Chrome の `plan` が同じ答えを出すよう、
    1 か所で決める。
    見せたものと違えば、識別子の違い（`CORE_APPROVE_LIST_CHANGED`）を中身の違いより先に見る。
    違わなくても、承認する対象が無いか、絞りが通らなければ `CORE_APPROVE_NOTHING`。
    """
    mismatch = verdict.mismatch
    if mismatch is not None:
        if mismatch["expected"] != mismatch["current"]:
            return CODE_APPROVE_LIST_CHANGED
        return CODE_APPROVE_DIGEST_CHANGED
    if not verdict.batch or verdict.gathered.refused:
        return CODE_APPROVE_NOTHING
    return ""


# ---- 手元の入口（`--agree`。端末・ボードの `--yes`・`--preview`・`--verify`） ------------------
#
# 前は approval.py にあった。コアを通す入口なので、コアより下の段（agree）には置かない
# （agree → core → agree の循環になる。tests/core/test_module_layers.py）。


def approve(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    only: list[str] | None = None,
    placed_out: str = "",
) -> int:
    """未承認の提案をまとめてユーザに見せ、承認されたら承認済みチケットを置く。

    エージェントではなくユーザが端末から打つ経路。承認は提案の中身を書き換えない。
    チケットを書くのはエージェントの仕事で、承認する場所で書き替えられると、
    承認したユーザが承認したものの作者になる。

    承認の対象は「いま承認待ちのもの全部」。親が 1 本、その下の子が複数、という形が普通。
    子は親の部分集合なので、新たに書けるようになる領域は親の分だけ。

    親の改版（計画の変更）も一緒に承認の対象に入る。承認済みチケットは動かないのが原則で、改版はその
    唯一の例外。変えられるのは `plan`・`feedback`・`phases:`・範囲。
    範囲を変える改版は、作業中の子が居ないときだけ通る。

    `only` は承認の対象を識別子で絞る（`ccnavi --agree <識別子>...`）。VS Code 拡張の
    ボードが絞り込みで見えている分だけを渡す。絞りは対象を狭めるだけで、絞らないときに
    失敗するものを通してはいけない。だから、承認待ちに無い識別子が入っていたら
    何も承認しない（ボードが古いときに、見せた以外のものを通さないため）。親の
    改版が承認待ちなのに対象から外した子も何も承認しない（外すと旧計画で検証される）。
    絞った対象に入らない親を持つ子は「親が承認されていない」で失敗する。

    判定と書き込みはコア（`judge_approval` → `plan` → `write_fs`）。

    `placed_out` があれば、書き込みに入ったあと、実際に書けた承認済みチケットのパスと、
    書けなかったもの・書かなかったものを JSON で書き出す（`placed_report`）。呼び手
    （`ccnavi-user-agree.sh`）が、書けた分だけをコミット・push し、残りを名指しするため。
    """
    snapshot = core_base.read_fs(conf, root)
    verdict = core_base.judge_approval(snapshot, only)
    while True:
        stderr.write(verdict.messages)
        core_base.say_elsewhere(stderr, verdict.gathered)
        gathered = verdict.gathered
        if gathered.refused:
            return 1
        if gathered.nothing_pending:
            stdout.write("承認待ちのチケットはありません。\n")
            # 読めない提案があったなら、その旨は標準エラーに出ている。承認するものが
            # 無いのは正常だが、不正な提案を「何も無い」で通す形にはしない。
            return 1 if gathered.broken else 0
        if not gathered.batch:
            return 1

        if gathered.note:
            stdout.write(gathered.note + "\n\n")
        stdout.write(gathered.text + "\n\n")
        stdout.write(f"この {len(gathered.batch)} 件を承認する場合は y、やめる場合はそれ以外: ")
        stdout.flush()
        if fsio.read_line(stdin).strip().lower() not in ("y", "yes"):
            stderr.write("ccnavi: 承認しなかった\n")
            return 1
        # 確認を待つ間に提案が更新されていないか、承認の直前に最新の状態から照合する。
        # 違えば承認せず、最新を表示し直してもう一度確認を取る（ボード・Chrome と同じ強さ）。
        latest_snapshot = core_base.read_fs(conf, root)
        latest = core_base.judge_approval(
            latest_snapshot, only, gathered.identifiers, verdict.digest
        )
        if latest.mismatch is None:
            snapshot, verdict = latest_snapshot, latest
            gathered = latest.gathered
            break
        stdout.write(f"\n{UPDATED_NOTICE}最新の承認待ちを表示し直します。\n\n")
        snapshot, verdict = latest_snapshot, core_base.judge_approval(latest_snapshot, only)
    applied, written = write_recorded(
        stdout, stderr, root, core_base.plan(snapshot, verdict).planned
    )
    if placed_out:
        _write_report(placed_out, placed_report(conf, root, gathered, applied, written), stderr)
    if applied.code == 0 and gathered.rejected:
        # 承認の対象の一部が失敗したときは、通ったぶんを置いてから失敗で終わる。置いたので
        # 繰り返してよく、失敗したものは上で名指ししてある。成功で終わると、
        # 端末を見ていない側（スクリプト、CI）は全部通ったと読む。
        stderr.write(
            f"ccnavi: {len(gathered.rejected)} 件は承認の対象にしなかった。"
            "直して出し直してください（通ったぶんの承認済みチケットは置いた）\n"
        )
        return modes.EXIT_PARTIAL
    if applied.code != 0 and applied.placed:
        # 途中で止まったが、そこまでに置いたものは残る。
        return modes.EXIT_PARTIAL
    return applied.code


def copy_paths(conf: settings.Settings, root: str, batch: list, ids: list[str]) -> list[str]:
    """承認済みチケットの置き場でのパス（識別子 `ids` の分）。承認の対象 `batch` から引く。"""
    by_id = {c.ticket.ticket: c.ticket for c in batch}
    out = []
    for ident in ids:
        t = by_id.get(ident)
        if t is None:
            continue
        home = approval.home_dir(conf, root, t.ticket, t.parent, t.tree_root, project=t.project)
        out.append(approval.copy_path(home, t.ticket))
    return out


def write_recorded(
    stdout: TextIO, stderr: TextIO, root: str, planned: agree.Planned
) -> tuple[agree.Applied, list[str]]:
    """並べた書き込みを書き（`core_base.write_fs`）、実際に書けた・消せたパスも返す。

    パスはワークスペースルートからの相対（区切りは "/"）。承認済みチケット・マーカーと、
    承認で消した提案が入る。ルートの外は入れない。`ccnavi-user-push-approved.sh --path` に渡すと、
    この承認で書いたものだけをコミットして push する（同じ置き場のほかの変更を巻き込まない）。
    """
    with fsio.recording() as written:
        applied = core_base.write_fs(stdout, stderr, planned)
    return applied, written_paths(root, written)


def written_paths(root: str, written: list[str]) -> list[str]:
    """書いたパス（絶対パス）を、ワークスペースルートからの相対（"/" 区切り）にする。外は除く。"""
    base = os.path.realpath(root)
    out = []
    for path in written:
        real = fsio.parent_resolved(path)
        try:
            rel = os.path.relpath(real, base)
        except ValueError:
            continue
        if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
            continue
        out.append(rel.replace(os.sep, "/"))
    return out


def placed_report(
    conf: settings.Settings,
    root: str,
    gathered: agree.Gathered,
    applied: agree.Applied,
    written: list[str] | None = None,
) -> dict:
    """承認の書き込みの結果。実際に書けたパス（`placed`）と、書けなかったもの・書かなかったもの。

    - `placed`: 書けた承認済みチケットのパス
    - `written`: この承認で書いた・消したパス（ワークスペースルートからの相対。`write_recorded`）
    - `failed`: 書き込みで止まった提案（識別子と理由）。止まらなければ空
    - `unwritten`: 止まったために書かなかった提案の識別子
    - `rejected`: 検査で承認の対象にしなかった提案（識別子と理由）
    """
    ids = [c.ticket.ticket for c in gathered.batch]
    failed = (
        [{"ticket": applied.stopped_at, "reason": applied.reason}]
        if applied.code != 0 and applied.stopped_at
        else []
    )
    stopped = {f["ticket"] for f in failed}
    unwritten = (
        [i for i in ids if i not in applied.placed and i not in stopped]
        if applied.code != 0
        else []
    )
    return {
        "version": agree.AGREE_VERSION,
        "placed": copy_paths(conf, root, gathered.batch, applied.placed),
        "written": list(written or []),
        "failed": failed,
        "unwritten": unwritten,
        "rejected": [
            {"ticket": t.ticket, "problems": [str(p) for p in complaints]}
            for t, complaints in gathered.rejected
        ],
    }


def _write_report(path: str, body: dict, stderr: TextIO) -> None:
    failed = fsio.write_text_atomic(path, json.dumps(body, ensure_ascii=False) + "\n")
    if failed:
        stderr.write(f"ccnavi: 書けたパスの一覧を {path} に書けなかった（{failed}）\n")


def preview(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    as_json: bool,
    only: list[str] | None = None,
) -> int:
    """`--agree --preview`。一覧を見せるだけで、承認済みチケットは置かない。端末の確認は要らない。

    JSON の形は README「承認の JSON」。一覧が空でも 0 で返す。拡張は `batch` が空なら
    「承認待ちは無い」と出す。`only` はボードの絞り込みで見えている分（`--agree` と
    同じ意味）。見せる一覧と承認する対象が同じ絞りを通るようにする。
    """
    verdict = core_base.judge_approval(core_base.read_fs(conf, root), only)
    stderr.write(verdict.messages)
    core_base.say_elsewhere(stderr, verdict.gathered)
    gathered = verdict.gathered
    if gathered.refused:
        return 1
    if not as_json:
        if gathered.note:
            stdout.write(gathered.note + "\n\n")
        stdout.write(gathered.text + "\n")
        return 0
    stdout.write(
        json.dumps(agree.preview_body(conf, root, gathered, verdict.digest), ensure_ascii=False)
        + "\n"
    )
    return 0


def verify(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    as_json: bool,
    only: list[str] | None = None,
) -> int:
    """`--agree --preview --verify`。いま `--agree` を打てば通るかを、置かずに返す。

    エージェントが提案を書いたあと、ユーザに承認を頼む前に自分で確かめるための枝。
    置かないところも端末を求めないところも `--preview` と同じで、
    違うのは「通るかどうか」を終了コードと本文で言うこと。

    答えを `--preview` 自身に持たせない理由。読む側（VS Code のボード拡張）は 0 以外を
    失敗として扱うので、承認の対象にしない提案が 1 件あるだけで一覧を出せなくなる。
    見せる枝と確かめる枝を分け、判定そのものは `gather` の 1 か所に置く。

    通る = 指定したもの（指定が無ければ承認待ち全部）が、そのまま承認の対象に入る。
    次のどれかがあれば通らない。

    - 絞りが通らない（承認待ちに無い識別子、親の改版を外した子）
    - 承認待ちが 1 件も無い。承認を頼む前の確認としては失敗で、
      提案の置き場を間違えた回がここに出る
    - 承認の対象にしない提案がある（`rejected`）

    失敗扱いにしないものが 2 つある。どちらも `--agree` が失敗させないもので、ここで失敗扱いにすると
    「確かめは『いいえ』なのに承認は通る」という食い違いになる。

    - 範囲の超過（`overflow`）。承認は止まらず、判定が切り詰めるだけ。承認しても
      書けない場所が残るのは伝える値打ちがあるから、その行につけて見せる
    - 読めない提案（`problems`）。走査は絞る前の全ツリーを見るので、他のセッションの
      書きかけ 1 本で、自分の提案が通るのに「直せ」と言われることになる。
      出さずに済ませることはせず、件数と文面を本文に出す（自分が書いた 1 本かもしれないので）

    返すのは「はい」（0）か「いいえ」（`modes.EXIT_ANSWER_NO`）。使い方と設定の誤りで返す
    1 とは分ける。同じ値にすると、打ち方を間違えた回と提案が失敗する回が読む側から
    区別できず、直すものが無いのに提案を直しに行くことになる。
    """
    judged = core_base.judge_approval(core_base.read_fs(conf, root), only)
    stderr.write(judged.messages)
    if as_json:
        # テキストの本文には `verify_verdict` が名指しの段を入れる。JSON の本体には無いので
        # 標準エラーへ出す。
        core_base.say_elsewhere(stderr, judged.gathered)
    gathered = judged.gathered
    verdict = agree.verify_verdict(gathered, conf.tickets)
    # 新規の親のブランチ名が既にあるブランチと同じか。warn なので答えは変えない。
    fresh = [c.ticket for c in gathered.batch if not c.is_revision and not c.ticket.is_child]
    branches = agree_candidates.existing_branch_warnings(root, conf, fresh, [], [], [])
    if as_json:
        body = agree.preview_body(conf, root, gathered, judged.digest)
        body["verify"] = {"ok": verdict.ok, "reason": verdict.reason}
        body["branch_warnings"] = branches
        stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
    else:
        stdout.write(verdict.text)
        if branches:
            stdout.write("\n既にあるブランチと同じ名前の親（warn）:\n")
            for line in branches:
                stdout.write(f"  - {line}\n")
    return modes.EXIT_OK if verdict.ok else modes.EXIT_ANSWER_NO


def approve_yes(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    expected: list[str],
    as_json: bool,
    only: list[str] | None = None,
    digest: str = "",
) -> int:
    """`--agree --yes <識別子,…> --digest <ダイジェスト> [<絞り>...]`。

    拡張のオーバーレイで押した承認。

    端末の確認は通らない。代わりに、見せた一覧と今の一覧が同じであることを求める。
    拡張が見せたあとに提案が増えていれば承認せず、食い違いを返す。見ていない
    ものを承認する経路を使えなくするため。識別子に加えて、見せた各提案ファイルの元のバイト列全体の
    ダイジェスト（`digest`）も比べる。識別子が同じでも、見せたあとに提案の本文・改行・BOM が
    書き換われば承認しない。判定が読んだ中身や書き込む中身は比べない（承認の可否は、ここで最新の
    状態から計算し直す）。違いが見つかったら何も置かずに `mismatch` を返し、
    呼び手が最新を表示し直す。
    ダイジェストが無ければ承認しない。

    引数は 2 つに分かれる。`--yes` は「オーバーレイに出ていた識別子」で、後ろに並べる語は
    「そのとき掛けていた絞り」（`--agree --preview` に渡したものと同じ）。分けないと検査が
    必ず通る。絞りだけで対象を狭めて、その狭めた対象と見せた識別子を比べると、いつでも一致する。
    絞りは preview と同じものを通し、比べるのは「その絞りで今できる一覧」と「見せた識別子」。
    """
    wanted = sorted({s.strip() for s in expected if s.strip()})
    narrowed = [s.strip() for s in (only or []) if s.strip()]
    shown = digest.strip()
    if not shown:
        stderr.write(
            core_base.tagged(
                CODE_APPROVE_NO_DIGEST,
                "ccnavi: --yes には --digest（見せた提案の識別子の一覧と提案ファイルの"
                "バイト列のダイジェスト。preview の digest）が必要です",
            )
            + "\n"
        )
        return 1
    # 絞りが通らなかった（承認待ちに無い識別子が入っている、親の改版を外した）ときは、
    # ボードが古い。拡張には食い違いとして返し、一覧を読み直させる（`judge_approval` が比べる）。
    snapshot = core_base.read_fs(conf, root)
    verdict = core_base.judge_approval(snapshot, narrowed, shown_ids=wanted, shown_digest=digest)
    stderr.write(verdict.messages)
    core_base.say_elsewhere(stderr, verdict.gathered)
    gathered = verdict.gathered
    if verdict.mismatch is not None:
        current = verdict.mismatch["current"]
        if as_json:
            body = {"version": agree.AGREE_VERSION, "mismatch": verdict.mismatch}
            stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
        if refusal_code(verdict) == CODE_APPROVE_LIST_CHANGED:
            stderr.write(
                core_base.tagged(
                    CODE_APPROVE_LIST_CHANGED,
                    f"ccnavi: {UPDATED_NOTICE}見せた一覧と今の一覧が違う（見せた: "
                    f"{', '.join(wanted) or '(無し)'} / 今: {', '.join(current) or '(無し)'}）。"
                    "何も承認していない。最新を見直してから承認してください",
                )
                + "\n"
            )
        else:
            stderr.write(
                core_base.tagged(
                    CODE_APPROVE_DIGEST_CHANGED,
                    f"ccnavi: {UPDATED_NOTICE}識別子は同じだが、提案ファイルの中身が"
                    "見せたときと違う。何も承認していない。最新を見直してから承認してください",
                )
                + "\n"
            )
        return 1
    if not gathered.batch:
        stderr.write(
            core_base.tagged(CODE_APPROVE_NOTHING, "ccnavi: 承認するものがありません。") + "\n"
        )
        return 1

    lines = io.StringIO()
    applied, written = write_recorded(
        lines, stderr, root, core_base.plan(snapshot, verdict).planned
    )
    if applied.code != 0:
        # 途中で止まった。置いたものはそのまま残るので、どこまで置いたかを返す。それを言わずに
        # 失敗を返すと、ユーザは「何も起きていない」と読む（README「承認の JSON」の `partial`）。
        stderr.write(
            core_base.tagged(
                CODE_APPROVE_STOPPED,
                "ccnavi: 承認を途中で止めた。置いたものはそのまま残る"
                f"（{', '.join(applied.placed) or '無し'}）",
            )
            + "\n"
        )
        if as_json:
            body = {
                "version": agree.AGREE_VERSION,
                "partial": {
                    "placed": applied.placed,
                    # 実際に書けた承認済みチケットのパス（`placed` の識別子の分）
                    "paths": copy_paths(conf, root, gathered.batch, applied.placed),
                    # この承認で書いた・消したパス（ワークスペースルートからの相対）。拡張は
                    # これだけを `ccnavi-user-push-approved.sh --path` でコミット・push させる。
                    "written": written,
                    "ticket": applied.stopped_at,
                    "reason": applied.reason,
                    # 止まるまでに出た行（マーカーを消した、改版した）。端末は stdout で見えるが、
                    # 拡張はこの JSON しか見ないので、同じものを渡す。
                    "lines": [line for line in lines.getvalue().splitlines() if line.strip()],
                },
            }
            stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
        else:
            stdout.write(lines.getvalue())
        return applied.code
    tickets = [c.ticket for c in gathered.batch]
    revisions = {c.ticket.ticket for c in gathered.batch if c.is_revision}
    # 子のワークツリーの計画。拡張が `create` の子を承認の後に作る（作れなければ案内に名指しで出る）
    plan = worktrees.child_worktrees(root, tickets)
    prompt = agree_screen.approved_text(tickets, revisions, root, plan)
    if not as_json:
        stdout.write(lines.getvalue())
        return 0
    body = {
        "version": agree.AGREE_VERSION,
        "approved": [t.ticket for t in tickets],
        "copies": [
            approval.copy_path(
                approval.home_dir(conf, root, t.ticket, t.parent, t.tree_root, project=t.project),
                t.ticket,
            )
            for t in tickets
        ],
        "lines": [line for line in lines.getvalue().splitlines() if line.strip()],
        # この承認で書いた・消したパス（ワークスペースルートからの相対。`write_recorded`）
        "written": written,
        "worktrees": plan,
        "prompt": prompt,
    }
    stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
    return 0
