"""フローを文に描く。子に渡す手順の一覧と、枝の行き先の書き方。

flow から分けた。flow を読まない。
"""

from __future__ import annotations

from collections import deque

from . import flow_shape, flow_text

# サブエージェントに並べる手順の上限。多ければファイルを読ませる。
RENDER_LIMIT = 40
# 子 1 本の手順の文の上限（文字）と、SubagentStart 全体でフローに使う上限。
CHILD_TEXT_LIMIT = 4000


def _labels(items, key: str) -> list[str]:
    entries = [i for i in flow_text._list(items) if isinstance(i, dict)]
    return flow_text._capped(
        [flow_text._line(i.get(key)) for i in entries[: flow_shape.ITEM_LIMIT]], len(entries)
    )


def _summary(node: dict, flows: dict) -> str:
    """ノード 1 つの中身を 1 行で。知らない種類は空。"""
    kind = flow_text._text(node.get("type"))
    data = flow_text._dict(node.get("data"))
    if kind == "prompt":
        return flow_text._line(data.get("prompt"))
    if kind == "subAgent":
        parts = [flow_text._line(data.get("description"))]
        if flow_text._line(data.get("prompt")):
            parts.append(f"プロンプト: {flow_text._line(data.get('prompt'))}")
        if flow_text._line(data.get("builtInType")):
            parts.append(f"種類: {flow_text._line(data.get('builtInType'))}")
        return " / ".join(p for p in parts if p)
    if kind == flow_shape.ASK:
        options = " | ".join(_labels(data.get("options"), "label"))
        multi = "（複数選択）" if data.get("multiSelect") is True else ""
        return f"問い: {flow_text._line(data.get('questionText'))} 選択肢{multi}: {options}"
    if kind in ("ifElse", "switch", "branch"):
        branches = [b for b in flow_text._list(data.get("branches")) if isinstance(b, dict)]
        parts = [
            f"{flow_text._line(b.get('label'))}={flow_text._line(b.get('condition'))}"
            for b in branches[: flow_shape.ITEM_LIMIT]
        ]
        parts = flow_text._capped(parts, len(branches))
        target = flow_text._line(data.get("evaluationTarget"))
        return (f"{target}: " if target else "") + " | ".join(parts)
    if kind == flow_shape.LOOP:
        limit = data.get("maxIterations")
        parts = [f"条件: {flow_text._line(data.get('condition'))}"]
        parts.append(
            f"最大 {flow_text._text(limit)} 回"
            if flow_shape.positive_int(limit)
            else "最大回数が書かれていない（1 以上の整数でない）"
        )
        return " / ".join(parts)
    if kind == "skill":
        return (
            f"スキル {flow_text._line(data.get('name'))}: "
            f"{flow_text._line(data.get('description'))}"
        )
    if kind == "mcp":
        tool = flow_text._line(data.get("toolName"))
        return f"MCP {flow_text._line(data.get('serverId'))}" + (f" / {tool}" if tool else "")
    if kind == "subAgentFlow":
        ref = flows.get(flow_text._text(data.get("subAgentFlowId")))
        name = flow_text._line(ref.get("name")) if isinstance(ref, dict) else ""
        return (flow_text._line(data.get("label")) or name) + (
            f"（サブフロー {name}）" if name else ""
        )
    if kind == "codex":
        return f"Codex: {flow_text._line(data.get('prompt'))}"
    if kind in ("branchSession", "start", "end"):
        return flow_text._line(data.get("label"))
    return ""


def _port_key(connection: dict) -> tuple:
    """出口の並べ順。`branch-<番号>` は番号の順、それ以外は文字列の順で後ろ。"""
    port = flow_text._text(connection.get("fromPort"))
    index = flow_shape._branch_index(port)
    return (0, index, "") if index is not None else (1, 0, port)


def _port_label(node: dict, port: str) -> str:
    """分岐の出口の名前。

    出口が項目の `id` とちょうど同じか、`branch-<番号>` の番号が項目の位置なら、その `label`。
    部分一致は採らない（`b` が `branch-1` に当たる）。項目は種類で決まる欄（分岐は `branches`、
    問いは `options`）の辞書だけを数える。ボードの線の言葉（`flow-doc.ts` の
    `connectionLabel`）と同じ読み方。
    """
    key = flow_shape.BRANCH_KEYS.get(flow_text._text(node.get("type")))
    if not port or key is None:
        return ""
    items = [
        i
        for i in flow_text._list(flow_text._dict(node.get("data")).get(key))
        if isinstance(i, dict)
    ]
    for item in items:
        if flow_text._text(item.get("id")) and flow_text._text(item.get("id")) == port:
            return flow_text._line(item.get("label"))
    index = flow_shape._branch_index(port)
    if index is not None and index < len(items):
        return flow_text._line(items[index].get("label"))
    return ""


def _order(ids: list[str], kinds: dict[str, str], out: dict[str, list[dict]]) -> list[str]:
    """並べる順。`start` から辿り、辿れなかったものは後ろに元の順で足す。O(ノード + 線)。"""
    known = set(ids)
    starts = [i for i in ids if kinds.get(i) == "start"]
    seen: dict[str, None] = {}
    queue = deque(starts or ids[:1])
    while queue:
        current = queue.popleft()
        if current in seen or current not in known:
            continue
        seen[current] = None
        for c in out.get(current, []):
            queue.append(flow_text._text(c.get("to")))
    return list(seen) + [i for i in ids if i not in seen]


def render(
    data: dict, limit: int = RENDER_LIMIT, text_limit: int = CHILD_TEXT_LIMIT
) -> tuple[list[str], set[str]]:
    """フローを順に並べた行と、出てきたノードの種類の集合。例外は外に出さない。

    YAML を読まなくても手順が追えるよう、1 ノード 1 行で `<番号>. [<種類>] <名前>: <中身>`
    と次の番号を並べる。知らない種類は種類の名前と `name` だけ。ノードは `limit` 件まで、
    文は `text_limit` 文字まで。
    """
    try:
        return _render(data, limit, text_limit)
    except Exception:  # noqa: BLE001  破損したデータで SubagentStart を止めない
        return ["（フローを並べられない。ファイルを直接読んで判断してください）"], set()


def _render(data, limit: int, text_limit: int) -> tuple[list[str], set[str]]:
    data = flow_text._dict(data)
    nodes: list[dict] = []
    seen_ids: set[str] = set()
    for n in flow_text._list(data.get("nodes")):
        # グループは図の上の囲みで手順ではない。並べない（線が繋がっていても辿らない）
        if isinstance(n, dict) and flow_text._text(n.get("type")) == flow_shape.GROUP:
            continue
        if (
            isinstance(n, dict)
            and flow_shape._node_id(n)
            and flow_shape._node_id(n) not in seen_ids
        ):
            seen_ids.add(flow_shape._node_id(n))
            nodes.append(n)
    connections = [c for c in flow_text._list(data.get("connections")) if isinstance(c, dict)]
    flows = {
        flow_text._text(f.get("id")): f
        for f in flow_text._list(data.get("subAgentFlows"))
        if isinstance(f, dict) and flow_text._text(f.get("id"))
    }
    by_id = {flow_shape._node_id(n): n for n in nodes}
    ids = list(by_id)
    kinds_by = {i: flow_text._text(n.get("type")) for i, n in by_id.items()}
    out: dict[str, list[dict]] = {}
    for c in connections:
        out.setdefault(flow_text._text(c.get("from")), []).append(c)
    for edges in out.values():
        edges.sort(key=_port_key)
    order = _order(ids, kinds_by, out)
    number = {nid: i + 1 for i, nid in enumerate(order)}
    kinds = set(kinds_by.values())
    lines: list[str] = []
    used = 0
    shown = 0
    for nid in order[:limit]:
        node = by_id[nid]
        kind = flow_text._line(kinds_by[nid]) or "?"
        name = flow_text._line(node.get("name")) or flow_text._line(nid)
        body = _summary(node, flows)
        text = f"{number[nid]}. [{kind}] {name}" + (f": {body}" if body else "")
        edges = [c for c in out.get(nid, []) if flow_text._text(c.get("to")) in number]
        nexts = []
        for c in edges[: flow_shape.ITEM_LIMIT]:
            label = flow_text._line(c.get("condition")) or _port_label(
                node, flow_text._text(c.get("fromPort"))
            )
            nexts.append(
                f"{number[flow_text._text(c.get('to'))]}" + (f"（{label}）" if label else "")
            )
        nexts = flow_text._capped(nexts, len(edges))
        if nexts:
            text += " → " + ", ".join(nexts)
        # 組み立てた行でも見る。種類の名前が `ccnavi…` だと、こちらの `[<種類>]` が接頭辞になる。
        text = flow_text._neutral(text)
        if used + len(text) > text_limit and lines:
            break
        lines.append(text)
        used += len(text)
        shown += 1
    if len(order) > shown:
        lines.append(f"…ほか {len(order) - shown} 件。続きはファイルを読んでください")
    return lines, kinds
