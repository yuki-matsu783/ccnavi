"""承認済みの親の改版の雛形を作る。

`ccnavi ticket revise <識別子> [--feedback]`。

承認済みの親（`doing/`）の計画・範囲・`phases:` を変えるには、承認済みチケットをコピーした提案を
同じ識別子で `todo/` に置き、改版として承認を受ける。手で写すと、スクリプトだけが書く欄
（`started_at`・`base_sha` など）を残したり消し損ねたりして、承認の検査が断る。
この雛形は、そのコピーと欄の始末まで済ませて作る。

## 決まり

- 対象は親だけ。cwd が識別子と同名の親のワークツリーの中でなければ止まる
  （`scaffold.parent_worktree`）
- 元は、そのワークツリーの承認済みの置き場の `doing/<識別子>.md`。無ければ止まる
- 出力先は `<提案の置き場>/todo/<識別子>.md`。既にあれば上書きせず止まる
- 写すときに、スクリプトだけが書く欄（`ticket_model.SCRIPT_FIELDS`）と続きの子の目印を外す。
  計画・`phases:`・範囲・本文はそのまま残る
- `--feedback` は `feedback: []`（対応なし）の改版を作る。全体計画のフェーズが全部閉じて
  レビューが済んだ（局面が「フィードバック計画待ち」）ときだけ。フィードバック計画が既にあれば止まる。
  対応のフェーズを並べる改版は、雛形の `feedback: []` を `feedback:` に手で直す

ネットワークにも git にも触らない。書いたあとの検査（`--agree --preview --verify`）と、
送る手順は出力が言う。
"""

from __future__ import annotations

import os
from typing import TextIO

from ...infra import fsio, settings
from ...infra.modes import EXIT_ERROR, EXIT_OK
from ..approval import checks as approval_checks
from ..approval import store as approval
from ..model import ticket_fields, ticket_ids, ticket_model
from ..phase import gates as phase
from . import scaffold

FEEDBACK_NONE = "feedback: []"


def _stripped(text: str) -> str:
    """承認済みチケットのコピーから、改版に残さない欄を外した本文。"""
    drop = tuple(ticket_model.SCRIPT_FIELDS) + (approval.FOLLOWUP_KEY,)
    kept = ticket_fields.script_shape(text, drop=drop)
    return (text if kept is None else kept).rstrip("\n") + "\n"


def _with_feedback_none(text: str) -> str | None:
    """frontmatter の閉じの前に `feedback: []` を足す。閉じが見つからなければ None。"""
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == ticket_model.FENCE), None)
    if not lines or end is None:
        return None
    lines.insert(end, FEEDBACK_NONE)
    return "\n".join(lines) + "\n"


def run(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    cwd: str,
    ident: str,
    feedback: bool = False,
) -> int:
    """`ticket revise` の本体。書けたら 0、書かなければ 1（理由は標準エラー）。"""

    def refuse(why: str) -> int:
        stderr.write(f"ccnavi: ticket revise: {why}。何も反映しません。\n")
        return EXIT_ERROR

    problem = ticket_ids.id_problem(ident)
    if problem:
        return refuse(f"識別子 {ident!r} が指定できない（{problem}）")
    if ticket_ids.child_pattern().fullmatch(ident):
        return refuse(f"{ident} は子の識別子の形。改版できるのは親だけ")
    where, why = scaffold.parent_worktree(conf, root, cwd, ident)
    if where is None:
        return refuse(why)

    source = os.path.join(
        settings.approved_dir(conf, where.root), ticket_model.DOING, ident + ".md"
    )
    try:
        with open(source, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return refuse(
            f"承認済みの親が {source} に無い。承認前の提案は、改版ではなく提案そのものを直す"
        )
    path = os.path.join(
        where.root,
        (conf.tickets or settings.DEFAULT_TICKETS).replace("/", os.sep),
        ticket_model.TODO,
        ident + ".md",
    )
    if os.path.lexists(path):
        return refuse(f"{path} が既にある。書きかけの改版は上書きしないので、それを直す")

    if feedback:
        copies, _ = approval.scan(conf, root)
        current = approval_checks.by_id(copies).get(ident)
        if current is None:
            return refuse(f"{ident} が承認済みチケットとして読めない")
        if current.feedback is not None:
            return refuse("フィードバック計画は 1 回だけ作れる。この親には既にある")
        if phase.stage(root, conf, current) != phase.STAGE_FEEDBACK_WAITING:
            return refuse(
                "全体計画のフェーズが全部閉じてレビューが済んでから出す"
                f"（今の局面: {phase.stage(root, conf, current)}）"
            )

    body = _stripped(text)
    if feedback:
        added = _with_feedback_none(body)
        if added is None:
            return refuse(
                f"{source} の frontmatter の閉じ（---）が見つからない。承認済みの親を確かめる"
            )
        body = added
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError as exc:
        return refuse(f"{os.path.dirname(path)} を作れない（{exc}）")
    failed = fsio.write_new_durable(path, body.encode("utf-8"))
    if failed:
        return refuse(f"{path} に保存できませんでした（{failed}）")

    ticket_sh = settings.script_command(root, "ccnavi-ticket.sh")
    stdout.write(
        f"{ident} の改版の雛形を {path} に書いた"
        "（承認済みチケットのコピー。スクリプトだけが書く欄は外した）\n"
    )
    verify = f"'{ticket_sh} approval-request {ident} --check' が通ったら、"
    send = (
        f"'{ticket_sh} approval-request {ident}' で親のブランチへ送り、"
        "承認をユーザに頼んで返事を待つ"
    )
    if feedback:
        stdout.write("フィードバック計画は `feedback: []`（対応なし）にした\n")
        # 対応のフェーズを並べる場合は、確かめる前に直す。先に言ってから次の一手を言う
        stdout.write(
            "案内: 全体計画のレビューで残った指摘を対応のフェーズにするときは、"
            f"{path} の `feedback: []` を、対応のフェーズを並べた `feedback:` に直してから"
            "確かめる\n"
        )
        stdout.write(f"次: {verify}{send}\n")
    else:
        cancel = f"{ticket_sh} cancel <子> --reason <理由>"
        stdout.write(f"次: {path} の plan・phases・allow などを直す\n")
        stdout.write(f"案内: 範囲を変えるときは、作業中の子を先に '{cancel}' で取り消す\n")
        stdout.write(f"案内: 直したら、{verify}{send}\n")
    return EXIT_OK
