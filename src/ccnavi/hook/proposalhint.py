"""親の提案を手書きしたときの書式の誤りを、書いた時点で 1 度だけ案内する（PostToolUse）。

提案の雛形は `ccnavi ticket new` が作る。手で書くと、子の例をコピーして親に `parent:` と `phase:` を
書いたり、`plan:`・`phases:`・`mr:` を落としたりする。承認の検査（`--agree --preview --verify`）は
後から言うが、書いた時点で言えば、直しが 1 手で済む。

判定は変えない。案内を出すのは次をすべて満たすときだけ。

- Write で新しく作った（`tool_response.type` が `create`）。同じ提案を直すたびには出さない
- 書いた先が、ツリーの提案の置き場の `todo/` の直下の md
- 次のどちらか
  - 親のつもりで `parent:` を書いた（識別子が子の形でない）。`parent:` と `phase:` を消すよう言う
  - 親（`parent:` が無い）なのに `plan:`・`phases:`・`mr:` のどれかが無い。欠けた欄を名指しする

書式の規則そのものは持たない。子の形は `ticket_ids.child_pattern` と同じものを引く。
"""

from __future__ import annotations

import os
import re

from ..infra import fsio, hookio, settings, tree
from ..tickets import ticket, ticket_ids

# 理由コード（設計 付録 A）。記録（`decisions.jsonl`）の `code` には残らない。
CODE_PARENT_KEY = "NOTICE_PROPOSAL_PARENT_KEY"
CODE_PARENT_FIELDS = "NOTICE_PROPOSAL_PARENT_FIELDS"

# 親の提案が持つ欄。`project:` は置き場が決めるので数えない。
PARENT_FIELDS = ("issue", "mr", "plan", "phases")


def hint(conf: settings.Settings, root: str, payload: hookio.Input) -> str:
    """案内の文。出さないときは空。何が起きても実行後チェックを止めない。"""
    if payload.tool_name != "Write" or payload.tool_response.get("type") != "create":
        return ""
    try:
        return _hint(conf, root, payload)
    except Exception:  # noqa: BLE001 - 案内の足しなので、何が起きても実行後チェックを止めない
        return ""


def _hint(conf: settings.Settings, root: str, payload: hookio.Input) -> str:
    """書いたのが親の提案（`todo/` の md）なら、書式の誤りを 1 行の案内にして返す。無ければ空。

    処理フロー:
    1. 書いたのが `.md` で、チケット制御が有効でなければ空を返す。
    2. 書いた先のツリーを求め、`todo/` の直下の md でなければ空を返す（`tree.relative`）。
    3. frontmatter を読む（`ticket.front_mapping`）。読めなければ空を返す。
    4. `parent:` が在るときは、識別子が子の形なら空を返す。子の形でなければ、
       `parent:` を消すよう案内する。
    5. `parent:` が無いときは、`PARENT_FIELDS` の欠けを案内する。欠けが無ければ空を返す。
    """
    raw = str(payload.tool_input.get("file_path") or "")
    if not raw.endswith(".md") or not conf.tickets:
        return ""
    written = os.path.abspath(os.path.join(payload.cwd or root, raw))
    where = tree.tree_of(root, fsio.full_path(raw, payload.cwd or root), conf.projects)
    if where is None:
        return ""
    rel = tree.relative(where, written)
    folder = conf.tickets.strip("/") + "/todo/"
    # ワークツリーが想定外の位置（`projects/<名前>/.claude/worktrees/<識別子>/`）にあると、
    # 書いた先はプロジェクトのツリーの中の相対になる。その形でも案内を出す
    inner = re.sub(r"^\.claude/worktrees/[^/]+/", "", rel)
    if not inner.startswith(folder) or "/" in inner[len(folder) :]:
        return ""
    with open(written, encoding="utf-8") as handle:
        front = ticket.front_mapping(handle.read())
    if front is None:
        return ""
    name = str(front.get("ticket") or "").strip() or os.path.splitext(os.path.basename(written))[0]
    parent = str(front.get("parent") or "").strip()
    if parent:
        matched = ticket_ids.child_pattern().match(name)
        if matched is not None:
            # 子の形。親の欄は求めない。別の親を指す誤りは読み込み（ticket.py）のエラー文が言う
            return ""
        return (
            f"[ccnavi] {CODE_PARENT_KEY} ({rel}) 識別子 {name} は子の形ではないのに `parent:` が"
            "あります。親の提案なら `parent:` と `phase:` の行を消してください"
            "（子だけの欄）。雛形は"
            " `ccnavi ticket new <識別子> --title <題> --plan <型,…> --issue <値> --mr <番号>`"
            " が作ります。"
        )
    missing = [key for key in PARENT_FIELDS if not front.get(key)]
    if not missing:
        return ""
    return (
        f"[ccnavi] {CODE_PARENT_FIELDS} ({rel}) 親の提案に {'・'.join(f'`{k}:`' for k in missing)}"
        " がありません。手で足さず、"
        "`ccnavi ticket new <識別子> --title <題> --plan <型,…> --issue <値> --mr <番号>`"
        " で雛形を作るか、`ccnavi --plan-order <親> --fill-phases` で"
        " `phases:` を差し込んでください。書式は docs/design/tickets/proposal-format.md。"
    )
