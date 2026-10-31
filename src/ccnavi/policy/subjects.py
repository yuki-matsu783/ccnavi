"""ツールの呼び出しのうち、ルールを適用する文字列（subject）を選ぶ。

判定（`hook.judge`）と、ルールファイルの検査（`policy.rule_lint`。適用する対象の無いツール名を言う）が
同じ表を読む。検査は判定の下の段から呼ばれる（保護対象の取り込みも同じ検査を掛ける）ので、
表と選び方を判定の外のここに置く。
"""

from __future__ import annotations

from ..infra import fsio, hookio, toolnames

# ツールごとに、ルールを適用する欄。ここに無いツールは対象を持たず、判定に届かない
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


def subject_of(payload: hookio.Input) -> str:
    """このツールでルールを適用する欄を選ぶ。欄は SUBJECT_FIELDS が持つ。"""
    tool = payload.tool_name
    field = SUBJECT_FIELDS.get(tool)
    if field is None:
        return ""
    if tool in toolnames.AGENT_TOOLS:
        # 起動には対象の文字列が無い。レビューが済むまで止める対象なので、見出しを subject に
        # して判定に入れる。
        return payload.field_value(field) or payload.field_value("prompt") or "(agent)"
    value = payload.field_value(field)
    if tool == "NotebookEdit":
        value = value or payload.field_value("file_path")
    if tool in toolnames.SEARCH_TOOLS:
        value = value or "."
    if tool in toolnames.PATH_TOOLS:
        return fsio.full_path(value, payload.cwd)
    return value
