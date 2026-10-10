"""判定のコアを通す手元の承認の入口（`--agree` の 4 つの枝）。

端末・ボードの `--yes`・`--preview`・`--verify` と、対話の承認。入力・判定・書き込みは
`core_base`、再エクスポートは `core`。コアを通す入口をコアより下の段（agree）に置くと、
import が循環する（tests/core/test_module_layers.py）。

"""

from __future__ import annotations

import io
import json
from typing import TextIO

from ..infra import fsio, modes, settings
from ..tickets import (
    agree,
    agree_candidates,
    agree_screen,
    approval,
)
from . import core_base

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
) -> int:
    """未承認の提案をまとめてユーザに見せ、承認されたら承認済みチケットを置く。

    エージェントではなくユーザが端末から打つ経路。提案を書き直す方法は用意しない。
    チケットを書くのはエージェントの仕事で、承認する場所で書き替えられると、
    承認したユーザが承認したものの作者になる。

    承認の対象は「いま承認待ちのもの全部」。親が 1 本、その下の子が複数、という形が普通。
    子は親の部分集合なので、新たに書けるようになる領域は親の分だけ。

    親の改版（計画の変更）も一緒に承認の対象に入る。承認済みチケットは動かないのが原則で、改版はその
    唯一の例外（設計 9.7）。変えられるのは `plan` と `feedback` だけ。

    `only` は承認の対象を識別子で絞る（`ccnavi --agree <識別子>...`）。VS Code 拡張の
    ボードが絞り込みで見えている分だけを渡す。絞りは対象を狭めるだけで、絞らないときに
    落ちるものを通してはいけない。だから、承認待ちに無い識別子が入っていたら
    何も承認しない（ボードが古いときに、見せた以外のものを通さないため）。親の
    改版が承認待ちなのに対象から外した子も何も承認しない（外すと旧計画で検証される）。
    絞った対象に入らない親を持つ子は「親が承認されていない」で落ちる。

    判定と書き込みはコア（`judge_approval` → `plan` → `write_fs`）。
    """
    snapshot = core_base.read_fs(conf, root)
    verdict = core_base.judge_approval(snapshot, only)
    stderr.write(verdict.messages)
    core_base.say_elsewhere(stderr, verdict.gathered)
    gathered = verdict.gathered
    if gathered.refused:
        return 1
    if gathered.nothing_pending:
        stdout.write("承認待ちのチケットは無い。\n")
        # 読めない提案があったなら、その旨は標準エラーに出ている。承認するものが
        # 無いのは正常だが、壊れた提案を「何も無い」で通す形にはしない。
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
    code = core_base.write_fs(stdout, stderr, core_base.plan(snapshot, verdict).planned).code
    if code == 0 and gathered.rejected:
        # 承認の対象の一部が落ちたときは、通ったぶんを置いてから失敗で終わる。置いたので
        # 繰り返してよく、落ちたものは上で名指ししてある。成功で終わると、
        # 端末を見ていない側（スクリプト、CI）は全部通ったと読む。
        stderr.write(
            f"ccnavi: {len(gathered.rejected)} 件は承認の対象にしなかった。"
            "直して出し直してください（通ったぶんの承認済みチケットは置いた）\n"
        )
        return 1
    return code


def preview(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    as_json: bool,
    only: list[str] | None = None,
) -> int:
    """`--agree --preview`。一覧を見せるだけで、承認済みチケットは置かない。端末の壁は要らない。

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

    エージェントが提案を書いたあと、ユーザに承認を頼む前に自分で確かめるための枝
    （REQ-APV-13）。置かないところも端末を求めないところも `--preview` と同じで、
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

    落とさないものが 2 つある。どちらも `--agree` が落とさないもので、ここで落とすと
    「確かめは『いいえ』なのに承認は通る」という食い違いになる。

    - 範囲の超過（`overflow`）。承認は止まらず、判定が切り詰めるだけ。承認しても
      書けない場所が残るのは伝える値打ちがあるから、その行につけて見せる
    - 読めない提案（`problems`）。走査は絞る前の全ツリーを見るので、他のセッションの
      書きかけ 1 本で、自分の提案が通るのに「直せ」と言われることになる。
      出さずに済ませることはせず、件数と文面を本文に出す（自分が書いた 1 本かもしれないので）

    返すのは「はい」（0）か「いいえ」（`modes.EXIT_ANSWER_NO`）。使い方と設定の誤りで返す
    1 とは分ける。同じ値にすると、打ち方を間違えた回と提案が落ちる回が読む側から
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
            stdout.write("\n既にあるブランチと同じ名前の親（warn。承認は止めない）:\n")
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

    端末の壁は通らない。代わりに、見せた一覧と今の一覧が同じであることを求める。
    拡張が見せたあとに提案が増えていれば承認せず、食い違いを返す。見ていない
    ものを承認する経路を使えなくするため。識別子に加えて、見せた承認画面の本文・判定が読んだ中身・
    承認済みチケットに書き込む中身のダイジェスト（`digest`）も比べる。識別子が同じでも、見せたあとに提案の範囲や計画、
    画面に出ない欄（`issue` など）が書き換われば承認しない。
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
            "ccnavi: --yes には --digest（見せた承認画面の本文・判定が読んだ中身・"
            "承認済みチケットに書き込む中身のダイジェスト）が要る\n"
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
        if wanted != current:
            stderr.write(
                "ccnavi: 見せた一覧と今の一覧が違う（見せた: "
                f"{', '.join(wanted) or '(無し)'} / 今: {', '.join(current) or '(無し)'}）。"
                "見直してから承認してください\n"
            )
        else:
            stderr.write(
                "ccnavi: 見せた承認画面の本文・判定が読んだ中身・承認済みチケットに書き込む中身が、"
                "今のものと違う（識別子は同じで、提案か、判定が読んだ承認済みチケット・マーカーなどの"
                "中身が変わった）。見直してから承認してください\n"
            )
        return 1
    if not gathered.batch:
        stderr.write("ccnavi: 承認するものが無い\n")
        return 1

    lines = io.StringIO()
    applied = core_base.write_fs(lines, stderr, core_base.plan(snapshot, verdict).planned)
    if applied.code != 0:
        # 途中で止まった。置いたものはそのまま残るので、どこまで置いたかを返す。それを言わずに
        # 失敗を返すと、ユーザは「何も起きていない」と読む（README「承認の JSON」の `partial`）。
        if as_json:
            body = {
                "version": agree.AGREE_VERSION,
                "partial": {
                    "placed": applied.placed,
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
    prompt = agree_screen.approved_text(tickets, revisions, root)
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
        "prompt": prompt,
    }
    stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
    return 0
