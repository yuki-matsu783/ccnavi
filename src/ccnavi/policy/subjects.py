"""ツールの呼び出しのうち、ルールを当てる文字列（subject）を選ぶ。

判定（`hook.judge`）と、ルールファイルの検査（`policy.rule_lint`。当てる対象の無いツール名を言う）が
同じ表を読む。検査は判定の下の段から呼ばれる（保護対象の取り込みも同じ検査を掛ける）ので、
表と選び方を判定の外のここに置く。
"""

from __future__ import annotations

from ..infra import fsio, hookio, modes
from . import ruleload

# サブエージェントの起動ツール（Agent・Task）。レビューが済むまで止める対象で、対象の文字列を
# 持たないので見出しだけを subject にする。一覧は infra.modes が持つ。
AGENT_TOOLS = modes.AGENT_TOOLS

# ツールごとに、ルールを当てる欄。ここに無いツールは対象を持たず、判定に届かない
# まま通る。名前は Claude Code の権限ルール `ToolName(指定子)` から括弧の中を
# 除いたものに揃えてある。WebSearch は指定子を持たないので載せない。順序は画面の
# 候補の順になる。
#
# 対象を差し込む側（試験の diagnose と lint の probe）はこの表を読む。欄の名前を
# subject_of の中にだけ持つと、ツールを足したときにそちらが気づかないうちに古くなる。
SUBJECT_FIELDS: dict[str, str] = {
    "Bash": "command",
    "PowerShell": "command",
    "Read": "file_path",
    "Grep": "path",
    "Glob": "path",
    "Edit": "file_path",
    "Write": "file_path",
    "NotebookEdit": "notebook_path",
    "Skill": "skill",
    "Agent": "description",
    "Task": "description",
    "WebFetch": "url",
}

# 行き着く先まで解いてから当てるツール（パスを対象にするもの）。行き先のレイヤーを選ぶ一覧と
# 同じものを使う。
# 別に持つと、判定はパスに当てるのにレイヤーは共通レイヤーだけ、という食い違いが起きる。
PATH_TOOLS = ruleload.PATH_TOOLS

# 探す場所を省略できるツール。省略は「いま居る場所」の意味なので、cwd を対象にする。
# 空のままにすると、場所を書かない呼び出しがどのルールにも当たらずに通る。
SEARCH_TOOLS = ("Grep", "Glob")


def subject_of(payload: hookio.Input) -> str:
    """このツールでルールを当てる欄を選ぶ。欄は SUBJECT_FIELDS が持つ。"""
    tool = payload.tool_name
    field = SUBJECT_FIELDS.get(tool)
    if field is None:
        return ""
    if tool in AGENT_TOOLS:
        # 起動には対象の文字列が無い。レビューが済むまで止める対象なので、見出しを subject に
        # して判定に入れる。
        return payload.field_value(field) or payload.field_value("prompt") or "(agent)"
    value = payload.field_value(field)
    if tool == "NotebookEdit":
        value = value or payload.field_value("file_path")
    if tool in SEARCH_TOOLS:
        value = value or "."
    if tool in PATH_TOOLS:
        return fsio.full_path(value, payload.cwd)
    return value
