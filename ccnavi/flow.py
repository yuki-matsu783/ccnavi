"""子チケットのフロー（作業の手順のグラフ）。設計 9.3・9.12、ADR-0085。

子チケットは `flow:` の欄で、担当のサブエージェントが作業中に読む手順書を指せる。
書いていなければ既定の置き場 `references/<子>/flow.json` を見る。置き場はチケットと同じ
ツリー（承認済みチケットが在るツリー。プロジェクトのチケットならそのプロジェクトのツリー）の
ルートからの相対で、チケットと同じ git に乗る。

## 形

CC Workflow Studio（breaking-brake/cc-wf-studio）が `.vscode/workflows/*.json` に保存する
`workflow.json` と同じ形を読む。形が合うだけで、そのコードは使っていない（あちらは AGPL）。

    {"id", "name", "description"?, "version", "nodes": [...], "connections": [...],
     "subAgentFlows"?: [{"id", "name", "nodes", "connections"}], ...}
    node       = {"id", "type", "name", "position": {x, y}, "data": {...}}
    connection = {"id", "from", "to", "fromPort", "toPort", "condition"?}

ノードの種類（`type`）のうち、ここが中身を読むのは `start` `end` `prompt` `subAgent`
`askUserQuestion` `ifElse` `switch` `branch` `skill` `mcp` `subAgentFlow` `codex`
`branchSession` `group`。知らない種類は落とさず、種類の名前と `name` だけで並べる。

## 承認とロック

フローは承認の対象ではない（承認の指紋にも入らない）。承認されるのは `flow:` の欄の値
（どのファイルを読むか）までで、中身は着手の前と終わった後なら書き換えられる。
着手中（`started_at` があり、`completed_at` も `cancelled_at` も無い）は、読んでいる
手順が作業の途中で変わらないよう、実行前の判定が Write / Edit / NotebookEdit を止める
（`lock_hit`）。
"""

from __future__ import annotations

import json
import os

from . import ticket as ticket_mod
from . import tree

# ロックで止めたときの理由コードと、記録のルール名。
CODE_LOCKED = "DENY_TICKET_FLOW_LOCKED"
LOCK_RULE = "(ticket-flow-lock)"

# サブエージェントに並べる手順の上限。多ければファイルを読ませる。
RENDER_LIMIT = 40
# 1 行に載せる文の長さの上限。
TEXT_LIMIT = 120

# 止まってメインに返すノード。サブエージェントには利用者に聞く道具が無い。
ASK = "askUserQuestion"
# 入れ子のサブエージェントを起こすノード。
SPAWN = ("subAgent", "subAgentFlow")


def _fold(path: str) -> str:
    return path.replace("\\", "/").strip("/").lower()


def lock_dir(ticket_id: str) -> str:
    """ロックが覆う置き場。`references/<子>`。"""
    return f"{ticket_mod.FLOW_DIR}/{ticket_id}"


def covers(child: ticket_mod.Ticket, rel: str) -> bool:
    """ツリーのルートからの相対パスが、この子のフローの置き場か。

    覆うのは `references/<子>/` の下の全部と、`flow:` が指すファイル。大文字小文字は
    範囲の照合と同じく区別しない。
    """
    if not child.is_child:
        return False
    here = _fold(rel)
    base = _fold(lock_dir(child.ticket))
    return here == base or here.startswith(base + "/") or here == _fold(child.flow_rel())


def might_be_flow(rel: str) -> bool:
    """`references/` の下か。ロックの検査で承認済みチケットを読むかどうかを安く決める。"""
    here = _fold(rel)
    return here == _fold(ticket_mod.FLOW_DIR) or here.startswith(_fold(ticket_mod.FLOW_DIR) + "/")


def lock_hit(copies: list[ticket_mod.Ticket], project: str, rel: str) -> ticket_mod.Ticket | None:
    """この書き込みを止める、着手中の子。無ければ None。

    `copies` は開いている承認済みチケット。`project` は書き込み先のツリーのプロジェクト
    （ワークスペースなら空）。チケットと同じリポジトリのツリーだけを見る。ワークスペース
    ルート・プロジェクト・どのワークツリーでも、同じ相対パスなら止める（子のワークツリーに
    写っている版も、親のツリーの版も同じ手順書）。
    """
    for child in copies:
        if child.is_child and child.in_progress and child.project == project and covers(child, rel):
            return child
    return None


def locked_message(child: ticket_mod.Ticket, rel: str) -> str:
    """ロックで止めたときの文面。"""
    return "\n".join(
        [
            f"[ccnavi] {CODE_LOCKED} (source: {child.path})",
            f"ticket: {child.ticket} ({child.title}), started {child.started_at}",
            f"path: {rel}",
            f"子チケット {child.ticket} は着手中なので、そのフロー"
            f"（`{lock_dir(child.ticket)}/` の下と `{child.flow_rel()}`）は書き換えられません。"
            "担当のサブエージェントが読んでいる手順が作業の途中で変わるのを防ぐためです。",
            f"ロックは {child.ticket} が `ccnavi-ticket.sh finish` で終わるか "
            "`cancel` で取り消されると外れます。手順を直したいなら、終わってから書き換えるか、"
            "次の子チケットのフローに書いてください。",
        ]
    )


def resolve(root: str, child: ticket_mod.Ticket) -> tuple[str, bool]:
    """フローのファイルの絶対パスと、在るかどうか。

    先に承認済みチケットが在るツリー（権威のツリー）で探し、無ければ子のワークツリーで探す。
    どちらにも無ければ権威のツリーの側の綴りを返す。
    """
    rel = child.flow_rel()
    if not rel:
        return "", False
    bases = [b for b in (child.tree_root, tree.worktree_path(root, child.ticket)) if b]
    if not bases:
        bases = [root]
    candidates = [os.path.join(b, rel.replace("/", os.sep)) for b in bases]
    for path in candidates:
        if os.path.isfile(path):
            return path, True
    return candidates[0], False


def info(root: str, child: ticket_mod.Ticket) -> dict | None:
    """ボード（`--explain --json`）に出すフローの欄。子でなければ None。"""
    if not child.is_child:
        return None
    path, exists = resolve(root, child)
    return {
        "path": path,
        "rel": child.flow_rel(),
        "declared": bool(child.flow),
        "exists": exists,
        "locked": child.in_progress and child.state == ticket_mod.DOING,
    }


def load(path: str) -> tuple[dict | None, str]:
    """フローを読む。(中身, 読めない理由)。"""
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except OSError as exc:
        return None, f"読めない ({exc})"
    except ValueError as exc:
        return None, f"JSON として読めない ({exc})"
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        return None, "`nodes` の並びが無い"
    return data, ""


def _line(text) -> str:
    """1 行に畳んで切る。"""
    shown = " ".join(str(text or "").split())
    if len(shown) > TEXT_LIMIT:
        shown = shown[:TEXT_LIMIT] + "…"
    return shown


def _labels(items, key: str) -> list[str]:
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            out.append(_line(item.get(key)))
    return out


def _summary(node: dict, flows: dict) -> str:
    """ノード 1 つの中身を 1 行で。知らない種類は空。"""
    kind = str(node.get("type") or "")
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    if kind == "prompt":
        return _line(data.get("prompt"))
    if kind == "subAgent":
        head = data.get("description") or data.get("agentDefinition")
        prompt = data.get("prompt")
        text = _line(head)
        if prompt:
            text += f" / プロンプト: {_line(prompt)}"
        if data.get("builtInType"):
            text += f" / 種類: {_line(data.get('builtInType'))}"
        return text
    if kind == ASK:
        options = " | ".join(_labels(data.get("options"), "label"))
        multi = "（複数選択）" if data.get("multiSelect") else ""
        return f"問い: {_line(data.get('questionText'))} 選択肢{multi}: {options}"
    if kind in ("ifElse", "switch", "branch"):
        branches = data.get("branches") if isinstance(data.get("branches"), list) else []
        parts = []
        for b in branches:
            if isinstance(b, dict):
                parts.append(f"{_line(b.get('label'))}={_line(b.get('condition'))}")
        target = f"{_line(data.get('evaluationTarget'))}: " if data.get("evaluationTarget") else ""
        return target + " | ".join(parts)
    if kind == "skill":
        return f"スキル {_line(data.get('name'))}: {_line(data.get('description'))}"
    if kind == "mcp":
        tool = data.get("toolName") or ""
        return f"MCP {_line(data.get('serverId'))}" + (f" / {_line(tool)}" if tool else "")
    if kind == "subAgentFlow":
        ref = flows.get(str(data.get("subAgentFlowId") or ""))
        name = ref.get("name") if isinstance(ref, dict) else ""
        return _line(data.get("label") or name) + (f"（サブフロー {name}）" if name else "")
    if kind == "codex":
        return f"Codex: {_line(data.get('prompt'))}"
    if kind in ("group", "branchSession", "start", "end"):
        return _line(data.get("label") or data.get("workDescription"))
    return ""


def _order(nodes: list[dict], connections: list[dict]) -> list[str]:
    """並べる順。`start` から辿り、辿れなかったものは後ろに元の順で足す。"""
    ids = [str(n.get("id")) for n in nodes]
    out: dict[str, list[dict]] = {}
    for c in connections:
        out.setdefault(str(c.get("from")), []).append(c)
    starts = [str(n.get("id")) for n in nodes if n.get("type") == "start"]
    seen: list[str] = []
    queue = list(starts or ids[:1])
    while queue:
        current = queue.pop(0)
        if current in seen or current not in ids:
            continue
        seen.append(current)
        for c in sorted(out.get(current, []), key=lambda x: str(x.get("fromPort") or "")):
            queue.append(str(c.get("to")))
    return seen + [i for i in ids if i not in seen]


def _port_label(node: dict, port: str) -> str:
    """分岐の出口の名前。出口の綴りの末尾の番号を、分岐か選択肢の並びに当てる。"""
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    items = data.get("branches") or data.get("options")
    if not isinstance(items, list):
        return ""
    digits = ""
    for ch in reversed(port):
        if not ch.isdigit():
            break
        digits = ch + digits
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        if (digits and int(digits) == i) or (item.get("id") and str(item.get("id")) in port):
            return _line(item.get("label"))
    return ""


def render(data: dict, limit: int = RENDER_LIMIT) -> tuple[list[str], set[str]]:
    """フローを順に並べた行と、出てきたノードの種類の集合。

    JSON を読まなくても手順が追えるよう、1 ノード 1 行で `<番号>. [<種類>] <名前>: <中身>`
    と次の番号を並べる。知らない種類は種類の名前と `name` だけ。
    """
    nodes = [n for n in data.get("nodes") or [] if isinstance(n, dict) and n.get("id")]
    connections = [c for c in data.get("connections") or [] if isinstance(c, dict)]
    flows = {str(f.get("id")): f for f in data.get("subAgentFlows") or [] if isinstance(f, dict)}
    by_id = {str(n.get("id")): n for n in nodes}
    order = _order(nodes, connections)
    number = {nid: i + 1 for i, nid in enumerate(order)}
    kinds = {str(n.get("type") or "") for n in nodes}
    lines: list[str] = []
    for nid in order[:limit]:
        node = by_id[nid]
        kind = str(node.get("type") or "?")
        name = _line(node.get("name")) or nid
        body = _summary(node, flows)
        text = f"{number[nid]}. [{kind}] {name}" + (f": {body}" if body else "")
        nexts = []
        for c in connections:
            if str(c.get("from")) != nid or str(c.get("to")) not in number:
                continue
            label = _line(c.get("condition")) or _port_label(node, str(c.get("fromPort") or ""))
            nexts.append(f"{number[str(c.get('to'))]}" + (f"（{label}）" if label else ""))
        if nexts:
            text += " → " + ", ".join(nexts)
        lines.append(text)
    if len(order) > limit:
        lines.append(f"…ほか {len(order) - limit} 件。続きはファイルを読む")
    return lines, kinds


def briefing(root: str, child: ticket_mod.Ticket, scope: str) -> list[str]:
    """SubagentStart でこの子について渡す行。フローが無ければ空。

    サブエージェントの自動 compact で消えても辿り直せるよう、ファイルの絶対パスと、それを
    指すチケットの欄（`flow:`）を毎回名指しする。
    """
    if not child.is_child:
        return []
    path, exists = resolve(root, child)
    if not exists:
        if child.flow:
            return [f"    フロー: `flow:` が指す {path} が無い。親に確かめる"]
        return []
    field = "`flow:` の欄" if child.flow else f"`flow:` の欄が無いので既定の {child.flow_rel()}"
    lines = [
        f"    フロー: {path}（チケット {child.path} の {field}）。作業の前にこのファイルを読み、"
        "その順に進める。文脈が要約されて見失ったら、このパスかチケットの `flow:` を読み直す。"
        "着手中は書き換えられない（ロック）。"
    ]
    data, why = load(path)
    if data is None:
        lines.append(f"    フローを読めない: {why}。ファイルを直接読んで判断する")
        return lines
    steps, kinds = render(data)
    lines.extend(f"      {s}" for s in steps)
    worktree = tree.worktree_path(root, child.ticket)
    if ASK in kinds:
        lines.append(
            f"    {ASK} のノード: サブエージェントは利用者に聞けない"
            "（AskUserQuestion は渡されない）。そのノードで手を止め、問いと選択肢を添えて"
            "メインに返す。メインが利用者に聞き、答えを持って同じサブエージェントを再開させる。"
        )
    if kinds & set(SPAWN):
        lines.append(
            "    subAgent / subAgentFlow のノード: Agent ツールがあれば入れ子の"
            "サブエージェントとして起動する。そのプロンプトには必ず、担当の子チケット "
            f"{child.ticket}、ワークツリー {worktree}、範囲 {scope or '(空)'} を書き、"
            "他の子のワークツリーには触れないことを書く。Agent ツールが無ければ（入れ子の上限）、"
            "そのノードで手を止め、起動してほしいエージェントの種類・プロンプト・担当の子チケットの"
            "ワークツリーと範囲を添えてメインに返す。メインがそのとおり起動し、結果を持って"
            "同じサブエージェントを再開させる。"
        )
    return lines
