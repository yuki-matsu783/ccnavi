"""フローの形の検査。ノード・枝・名前の食い違いを、読み手が直せる文で並べる。

flow から分けた。flow を読まない。
"""

from __future__ import annotations

from collections import deque

from . import flow_text

# 1 ノードに並べる選択肢・分岐・次の上限。
ITEM_LIMIT = 10

# 止まってメインに返すノード。サブエージェントにはユーザに聞く道具が無い。
ASK = "askUserQuestion"
# 図の上の囲み（ボードの枠）。手順ではないので並べない
GROUP = "group"
# 出口を項目ごとに持つ種類と、項目の欄。
BRANCH_KEYS = {"ifElse": "branches", "switch": "branches", "branch": "branches", ASK: "options"}


def shape_problem(data) -> str:
    """読めた中身の形の誤り（最初の 1 つ）。無ければ空。例外は外に出さない。

    SubagentStart の読み（`load`）と `--lint --flow` が同じここを通る。見るのは手順として
    並べるのに要る形だけ。最上位がマッピング、`nodes` がマッピングのリストで、どれも空でない
    文字列の `id` を持ち、`id` が重ならない。`connections` は在れば、マッピングのリスト。
    `id` が無い・重なるノードは並べるときに落ちるので、気づかないうちに手順が欠けることのないよう、
    読まない扱いにする。
    """
    if not isinstance(data, dict):
        return "最上位がマッピングではない"
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        return "`nodes` のリストが無い"
    seen: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            return f"nodes[{index}] がマッピングではない"
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            return f"nodes[{index}] に文字列の id が無い"
        if node_id in seen:
            return f"ノードの id が重なっている（{flow_text._line(node_id)}）"
        seen.add(node_id)
    if "connections" in data:
        connections = data.get("connections")
        if not isinstance(connections, list):
            return "`connections` がリストではない"
        for index, connection in enumerate(connections):
            if not isinstance(connection, dict):
                return f"connections[{index}] がマッピングではない"
    return ""


# ---- 線の構造と名前（`--lint --flow` の warn）
#
# 読めるか・形（`shape_problem`）の外にある、手順として怪しいところ。読むのは止めない（warn）。
# `SubagentStart` は見ない（並べ方は `render` のまま）。巡回は意図して書くことがあるので言わない。

# 並べない（手順でない）種類。構造の検査からも外す。
_NOT_STEP = (GROUP,)


def _step_nodes(data) -> list[dict]:
    """構造を見るノード。辞書で `id` を持ち、グループでないもの（重なった `id` は最初の 1 つ）。"""
    nodes: list[dict] = []
    seen: set[str] = set()
    for n in flow_text._list(flow_text._dict(data).get("nodes")):
        if not isinstance(n, dict) or flow_text._text(n.get("type")) in _NOT_STEP:
            continue
        nid = _node_id(n)
        if nid and nid not in seen:
            seen.add(nid)
            nodes.append(n)
    return nodes


def _named(node: dict) -> str:
    """苦情で名指しするノード。`<id>（<名前>）`。"""
    nid, name = flow_text._line(_node_id(node)), flow_text._line(node.get("name"))
    return f"{nid}（{name}）" if name and name != nid else nid


def _names(nodes: list[dict]) -> str:
    return ", ".join(flow_text._capped([_named(n) for n in nodes[:ITEM_LIMIT]], len(nodes)))


def _port_taken(node: dict, index: int, item: dict, ports: set[str]) -> bool:
    """項目（分岐・選択肢）の出口に線があるか。

    読み方は `_port_label` と同じ（項目の `id` か `branch-<番号>`）。
    """
    item_id = flow_text._text(item.get("id"))
    if item_id and item_id in ports:
        return True
    return any(_branch_index(port) == index for port in ports)


def structure_problems(data) -> list[str]:
    """線の構造の怪しいところ（1 件 1 行）。無ければ空。例外は外に出さない。

    見るのは、線の `from` / `to` が無いノードを指す、`start` から届かないノード、`start` に入る線、
    `end` から出る線、分岐・問いの出口に線が無い、`end` が無い、`start` が無い。グループは外す
    （手順ではない）。巡回は言わない。サブフロー（`subAgentFlows`）の中は見ない。

    出口は画面（`flow-doc.ts` の `portsOf`）と同じに読む。複数選択（`multiSelect: true`）の問いは
    選択肢ごとに出口を分けず、`output` の 1 本だけ。グループへ出る線も、出る側の出口は使っている
    （線は手順に数えないが、「出口に線が無い」とは言わない）。無いノードを指す線は
    `ITEM_LIMIT` 件まで言い、残りは数だけつける。
    """
    try:
        return _structure_problems(data)
    except Exception:  # noqa: BLE001  壊れたデータで lint を落とさない
        return ["線の構造を確かめられない（中身の型が崩れている）"]


def _structure_problems(data) -> list[str]:
    data = flow_text._dict(data)
    nodes = _step_nodes(data)
    by_id = {_node_id(n): n for n in nodes}
    groups = {
        _node_id(n)
        for n in flow_text._list(data.get("nodes"))
        if isinstance(n, dict) and flow_text._text(n.get("type")) in _NOT_STEP
    }
    kinds = {nid: flow_text._text(n.get("type")) for nid, n in by_id.items()}
    out: list[str] = []
    missing: list[str] = []
    edges: dict[str, list[str]] = {}
    ports: dict[str, set[str]] = {}
    for index, c in enumerate(flow_text._list(data.get("connections"))):
        if not isinstance(c, dict):
            continue
        cid = flow_text._line(c.get("id")) or f"connections[{index}]"
        src, dst = flow_text._text(c.get("from")), flow_text._text(c.get("to"))
        bad = False
        for end_name, ref in (("from", src), ("to", dst)):
            if ref in groups:
                continue
            if ref not in by_id:
                shown = flow_text._line(ref) or "(空)"
                missing.append(f"線 {cid} の {end_name} が無いノード（{shown}）を指している")
                bad = True
        if not bad and src in by_id:
            # グループへ出る線でも、出る側の出口は使っている
            ports.setdefault(src, set()).add(flow_text._text(c.get("fromPort")) or "output")
        if bad or src in groups or dst in groups:
            continue
        edges.setdefault(src, []).append(dst)
        if kinds.get(dst) == "start":
            out.append(f"線 {cid} が start（{_named(by_id[dst])}）に入っている")
        if kinds.get(src) == "end":
            out.append(f"線 {cid} が end（{_named(by_id[src])}）から出ている")
    out.extend(missing[:ITEM_LIMIT])
    if len(missing) > ITEM_LIMIT:
        out.append(f"無いノードを指す線は…ほか {len(missing) - ITEM_LIMIT} 件")
    starts = [nid for nid in by_id if kinds[nid] == "start"]
    if not starts:
        out.append("start が無い（どこから始めるかが決まらない）")
    if not any(kind == "end" for kind in kinds.values()):
        out.append("end が無い（どこで終わるかが決まらない）")
    if starts:
        reached: set[str] = set()
        queue = deque(starts)
        while queue:
            current = queue.popleft()
            if current in reached:
                continue
            reached.add(current)
            queue.extend(edges.get(current, []))
        lost = [n for nid, n in by_id.items() if nid not in reached]
        if lost:
            out.append(f"start から届かないノードがある: {_names(lost)}")
    for nid, node in by_id.items():
        key = BRANCH_KEYS.get(kinds[nid])
        if key is None:
            continue
        taken = ports.get(nid, set())
        if kinds[nid] == ASK and flow_text._dict(node.get("data")).get("multiSelect") is True:
            # 複数選択の問いは出口を分けない（`output` の 1 本。画面の `portsOf` と同じ）
            if "output" not in taken:
                out.append(f"問い {_named(node)} の出口に線が無い: output（複数選択）")
            continue
        items = [
            i
            for i in flow_text._list(flow_text._dict(node.get("data")).get(key))
            if isinstance(i, dict)
        ]
        empty = [
            flow_text._line(item.get("label")) or f"{index + 1} 番目"
            for index, item in enumerate(items)
            if not _port_taken(node, index, item, taken)
        ]
        if empty:
            what = "問い" if kinds[nid] == ASK else "分岐"
            shown = ", ".join(flow_text._capped(empty[:ITEM_LIMIT], len(empty)))
            out.append(f"{what} {_named(node)} の出口に線が無い: {shown}")
    return out


def _flow_nodes(data) -> list[dict]:
    """名前を確かめるノード。最上位とサブフローの中の両方。"""
    data = flow_text._dict(data)
    nodes = [n for n in flow_text._list(data.get("nodes")) if isinstance(n, dict)]
    for sub in flow_text._list(data.get("subAgentFlows")):
        nodes.extend(
            n for n in flow_text._list(flow_text._dict(sub).get("nodes")) if isinstance(n, dict)
        )
    return nodes


def name_problems(data, cat: dict[str, list[dict]]) -> list[str]:
    """`subAgent` の種類（`builtInType`）と `skill` の名前（`name`）が候補に無いもの（1 件 1 行）。

    書き誤りを見つけるため。空の欄は言わない（書きかけ）。スキルの `:` を含む名前
    （プラグインのスキル）は、ディレクトリの中から確かめられないので言わない。大文字小文字だけが
    違えば、正しい表記をつける。例外は外に出さない。
    """
    try:
        return _name_problems(data, cat)
    except Exception:  # noqa: BLE001  壊れたデータで lint を落とさない
        return []


def _name_problems(data, cat: dict[str, list[dict]]) -> list[str]:
    agents = [i["name"] for i in cat.get("agents", [])]
    skills = [i["name"] for i in cat.get("skills", [])]
    out: list[str] = []
    for node in _flow_nodes(data):
        kind = flow_text._text(node.get("type"))
        info = flow_text._dict(node.get("data"))
        if kind == "subAgent":
            what, value, known = (
                "サブエージェントの種類",
                flow_text._text(info.get("builtInType")),
                agents,
            )
        elif kind == "skill":
            what, value, known = "スキル", flow_text._text(info.get("name")), skills
            if ":" in value:
                continue
        else:
            continue
        value = value.strip()
        if not value or value in known:
            continue
        near = [k for k in known if k.casefold() == value.casefold()]
        hint = f"。大文字小文字が違う（{flow_text._line(near[0])}）" if near else ""
        out.append(
            f"ノード {_named(node)} の{what} {flow_text._line(value)} が候補に無い"
            "（組み込みと .claude/ の下に無い。書き誤りかもしれない。"
            f"ユーザ・プラグインのものなら気にしなくてよい）{hint}"
        )
    return out


def _branch_index(port: str) -> int | None:
    """`branch-<番号>` の番号。番号は ASCII の数字だけ。違う表記なら None。"""
    head, _, digits = port.rpartition("-")
    if head != "branch" or not digits or not (digits.isascii() and digits.isdigit()):
        return None
    if len(digits) > 6:
        return None
    return int(digits)


def _node_id(node: dict) -> str:
    return flow_text._text(node.get("id"))
