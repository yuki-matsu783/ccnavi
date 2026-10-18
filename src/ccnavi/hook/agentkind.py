"""サブエージェントが、ファイルを編集するものかの見分け。

同じ作業ツリーで別の編集するサブエージェントが動いていることを、起動したサブエージェントに
案内するために使う（`subagent.at_start`）。編集する（`Write` / `Edit` / `NotebookEdit` を使える）
ものだけが案内の相手と対象になる。

判定は広めにする。読み取り専用と確かめられたものだけを `READ` にし、定義が無い・読めない・
`agent_type` が空など、見分けられないものは `EDIT` に倒す。案内は止めないので、読み取りの
サブエージェントに出てしまうノイズより、編集するサブエージェントへ出し損ねるほうが害が大きい。
"""

from __future__ import annotations

import os

import yaml

from ..infra import pathcase, yamlread

EDIT = "edit"
READ = "read"

# 組み込みで、読み取り専用のサブエージェント。
READ_ONLY_BUILTIN = ("explore", "plan", "claude-code-guide")

# 書き込み系のツール。`Bash` は数えない（コードから書く経路は読み切れない）。
EDIT_TOOLS = ("write", "edit", "multiedit", "notebookedit")

# 定義の frontmatter を読む長さの上限（バイト）。
HEAD_LIMIT = 16384


def kind_of(roots: list[str], agent_type: str, tools: object = None) -> str:
    """`EDIT` か `READ`。読み取りと確かめられなければ `EDIT`。

    `tools` は payload に一覧があるときだけ渡す。あれば定義より優先する。
    `roots` は定義（`.claude/agents/*.md`）を探すルート。近いほうから。
    """
    if isinstance(tools, list) and all(isinstance(t, str) for t in tools):
        return EDIT if _writes(tools) else READ
    name = agent_type.strip().casefold()
    if name in READ_ONLY_BUILTIN:
        return READ
    if not name:
        return EDIT
    meta = _definition(roots, name)
    if meta is None or "tools" not in meta and "disallowedTools" not in meta:
        return EDIT
    allowed = _names(meta.get("tools")) if "tools" in meta else None
    if allowed is not None and not _writes(allowed):
        return READ
    denied = _names(meta.get("disallowedTools"))
    if denied is not None and all(t in _lower(denied) for t in ("write", "edit", "notebookedit")):
        return READ
    return EDIT


def _lower(names: list[str]) -> list[str]:
    return [n.strip().casefold() for n in names]


def _writes(names: list[str]) -> bool:
    return any(n in EDIT_TOOLS for n in _lower(names))


def _names(value: object) -> list[str] | None:
    """`tools` の値（カンマ区切りの文字列か、文字列のリスト）。読めなければ None。"""
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    return None


def _definition(roots: list[str], name: str) -> dict | None:
    """`name` の定義の frontmatter。見つからない・読めないなら None。リンクは辿らない。"""
    for root in roots:
        folder = os.path.join(root, ".claude", "agents")
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except (OSError, ValueError):
            continue
        for entry in entries:
            if not entry.name.endswith(".md") or entry.is_symlink() or not entry.is_file():
                continue
            meta = _front(entry.path)
            if meta is None:
                continue
            named = meta.get("name") if isinstance(meta.get("name"), str) else entry.name[:-3]
            if pathcase.fold(named.strip()) == pathcase.fold(name):
                return meta
    return None


def _front(path: str) -> dict | None:
    """frontmatter を辞書で返す。無い・読めない・辞書でないなら None。"""
    try:
        with open(path, "rb") as f:
            head = f.read(HEAD_LIMIT).decode("utf-8-sig", errors="replace")
    except (OSError, ValueError):
        return None
    lines = head.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    body: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        body.append(line)
    else:
        return None
    try:
        meta = yamlread.safe_load("\n".join(body))
    except yaml.YAMLError:
        return None
    return meta if isinstance(meta, dict) else None
