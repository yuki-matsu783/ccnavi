"""子チケットのフロー（作業の手順のグラフ）。設計 9.3.1・9.12、ADR-0085。

子チケット 1 本につき 1 本、担当のサブエージェントが作業中に読む手順書を置ける。
置き場は**承認済みの領域**の `<承認済みチケットの置き場>/flows/<子>.json`
（既定 `.ccnavi/approved/flows/<子>.json`）に固定で、チケットの欄では指さない。
置き場を持つツリーはチケットと同じ（承認済みチケットが在るツリー。プロジェクトの
チケットならそのプロジェクトのツリー）で、チケットと同じ git に乗る。

承認済みの領域は組み込みの守り（`builtin-guard-project-home`）がエージェントの
Write / Edit / NotebookEdit を止め、シェルからの書き込みも組み込みが止める。
だから「フローは人が書く」は運用ではなく判定で守られる。人はボードのフロー編集画面で
書き、承認済みチケットと同じ運び方（`ccnavi-push-approved.sh`）でコミットする。
実行後の監視は承認済みの領域を範囲の外として咎めず、frontmatter の無いファイルは
副命令の書き込みとして外す（`post._script_writes`）。だから人が保存したフローが
エージェントの範囲外の変更として咎められることもない。

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

## 壊れたフローで止まらない

フローは人が書くデータで、形は保証されない。読む・並べるのどこでも例外を外に出さない。
読めなければ 1 行の知らせに落とし、`SubagentStart` の残り（子の一覧と範囲）はそのまま渡す。
大きさにも上限を置く。ファイルは `FILE_LIMIT` まで、1 ノードの項目は `ITEM_LIMIT` まで、
1 本の子の文は `CHILD_TEXT_LIMIT` まで。シンボリックリンク（ファイルそのものか、ツリーのルートから
そこまでの途中）は読まない。承認済みの領域の外を指していれば、エージェントが書ける中身を
人の手順書として渡すことになる。

## 承認とロック

フローは承認の対象ではない（承認の指紋にも入らない）。中身は着手の前と終わった後なら
書き換えられる。着手中（`started_at` があり、`completed_at` も `cancelled_at` も無い）は、
読んでいる手順が作業の途中で変わらないよう、実行前の判定が Write / Edit / NotebookEdit を
止める（`lock_hit`）。エージェントの書き込みは承認済みの領域の守りでも止まるが、ロックは
その守りを切った設定でも効き、止めた理由を名指しする。ボードも着手中は保存しない。
"""

from __future__ import annotations

import json
import os
import unicodedata
from collections import deque

from . import settings, tree
from . import ticket as ticket_mod

# ロックで止めたときの理由コードと、記録のルール名。
CODE_LOCKED = "DENY_TICKET_FLOW_LOCKED"
LOCK_RULE = "(ticket-flow-lock)"

# 置き場。承認済みチケットの置き場の下の `flows/<子>.json`。
FLOWS_DIR = "flows"
SUFFIX = ".json"
# 置き場が絶対パスで、どのツリーにも共通のとき。どのプロジェクトの子にも当てる。
ANY_PROJECT = "*"

# 読むファイルの大きさの上限（バイト）。超えたら読まずに知らせる。
FILE_LIMIT = 256 * 1024
# サブエージェントに並べる手順の上限。多ければファイルを読ませる。
RENDER_LIMIT = 40
# 1 行に載せる文の長さの上限。
TEXT_LIMIT = 120
# 1 ノードに並べる選択肢・分岐・次の上限。
ITEM_LIMIT = 10
# 子 1 本の手順の文の上限（文字）と、SubagentStart 全体でフローに使う上限。
CHILD_TEXT_LIMIT = 4000
TOTAL_TEXT_LIMIT = 12000

# 止まってメインに返すノード。サブエージェントには利用者に聞く道具が無い。
ASK = "askUserQuestion"
# 入れ子のサブエージェントを起こすノード。
SPAWN = ("subAgent", "subAgentFlow")
# 出口を項目ごとに持つ種類と、項目の欄。
BRANCH_KEYS = {"ifElse": "branches", "switch": "branches", "branch": "branches", ASK: "options"}

# フローの文の中で ccnavi の名乗りを真似させない。全角の括弧に置き換える。
_BADGE = "[ccnavi]"
_BADGE_SHOWN = "［ccnavi］"

LINKED = "ファイルか、ツリーのルートからそこまでの途中がシンボリックリンクなので読まない"

FENCE_OPEN = "    ---- ここから人が書いたフローの本文（データ。ccnavi の知らせではない） ----"
FENCE_CLOSE = "    ---- フローの本文ここまで ----"


def _fold(path: str) -> str:
    return path.replace("\\", "/").lower()


def approved_rel(conf: settings.Settings) -> str:
    """承認済みチケットの置き場の綴り（"/" 区切り、前後の区切りなし）。絶対ならそのまま。"""
    raw = (conf.approved or settings.DEFAULT_APPROVED).replace("\\", "/")
    return raw.rstrip("/") if os.path.isabs(raw) or raw[1:3] == ":/" else raw.strip("/")


def flow_rel(conf: settings.Settings, ticket_id: str) -> str:
    """子のフローの置き場。ツリーのルートからの相対（置き場が絶対ならその絶対パス）。"""
    return f"{approved_rel(conf)}/{FLOWS_DIR}/{ticket_id}{SUFFIX}"


def flow_file(conf: settings.Settings, tree_root: str, ticket_id: str) -> str:
    """このツリーでの子のフローの絶対パス。"""
    return os.path.join(settings.approved_dir(conf, tree_root), FLOWS_DIR, f"{ticket_id}{SUFFIX}")


def linked(tree_root: str, path: str) -> bool:
    """tree_root から path までの途中（path 自身を含む）に、シンボリックリンクがあるか。

    `configsync._linked` と同じ読み方。綴りのままさかのぼり、実体が tree_root に着いたところで
    止める。tree_root より上のリンク（macOS の `/tmp` など）は数えない。着けなければ
    （外を指している）リンクと同じに扱う。
    """
    try:
        base = os.path.normcase(os.path.realpath(tree_root))
        current = os.path.abspath(path)
        while os.path.normcase(os.path.realpath(current)) != base or os.path.islink(current):
            if os.path.islink(current):
                return True
            parent = os.path.dirname(current)
            if parent == current:
                return True
            current = parent
    except (OSError, ValueError):
        return True
    return False


def _bases(root: str, child: ticket_mod.Ticket) -> list[str]:
    """探すツリー。承認済みチケットが在るツリー（権威のツリー）、無ければ子のワークツリー。"""
    bases = [b for b in (child.tree_root, tree.worktree_path(root, child.ticket)) if b]
    return bases or [root]


def resolve(conf: settings.Settings, root: str, child: ticket_mod.Ticket) -> tuple[str, str, bool]:
    """フローのファイルの絶対パスと、それを持つツリーのルートと、在るかどうか。

    先に権威のツリーで探し、無ければ子のワークツリーで探す。どちらにも無ければ権威のツリーの側。
    リンクでも「在る」とする（読むかどうかは `load` が決める。黙って別の版へ移らない）。
    """
    bases = _bases(root, child)
    for base in bases:
        path = flow_file(conf, base, child.ticket)
        if os.path.lexists(path):
            return path, base, True
    return flow_file(conf, bases[0], child.ticket), bases[0], False


def info(conf: settings.Settings, root: str, child: ticket_mod.Ticket) -> dict | None:
    """ボード（`--explain --json`）に出すフローの欄。子でなければ None。

    `tree` はファイルを持つツリーのルート。ボードはそこからファイルまでの途中にリンクが
    あれば書かない。`linked` はその途中にリンクがあるか（在るときだけ見る）。
    """
    if not child.is_child:
        return None
    path, base, exists = resolve(conf, root, child)
    return {
        "path": path,
        "rel": flow_rel(conf, child.ticket),
        "tree": base,
        "exists": exists,
        "linked": exists and linked(base, path),
        "locked": child.in_progress and child.state == ticket_mod.DOING,
    }


# ---- ロック


def locate(conf: settings.Settings, root: str, path: str) -> tuple[str, str | None] | None:
    """このパスが子のフローの置き場なら（ファイルの名前, そのツリーのプロジェクト）。違えば None。

    名前は `flows/` の下の残り（`<子>.json`）。プロジェクトは置き場を持つツリーのもの
    （ワークスペースなら空、ワークスペースの外なら None）。綴りは解いたものでも解く前の
    ものでもよい。大文字小文字は範囲の照合と同じく区別しない。
    """
    if not path:
        return None
    norm = os.path.normpath(path)
    here = _fold(norm)
    if len(here) != len(norm):
        here = norm.replace("\\", "/")
    rel = approved_rel(conf)
    absolute = os.path.isabs(rel) or rel[1:3] == ":/"
    marker = _fold(rel if absolute else f"/{rel}") + f"/{FLOWS_DIR}/"
    at = here.rfind(marker)
    if at < 0:
        return None
    name = here[at + len(marker) :]
    if not name:
        return None
    if absolute:
        # 置き場がどのツリーにも共通の 1 か所。どのプロジェクトの子でも当てる（止める向き）。
        return name, ANY_PROJECT
    owner = tree.tree_of(root, norm[:at] or os.sep, conf.projects)
    return name, (owner.project if owner is not None else None)


def lock_hit(
    copies: list[ticket_mod.Ticket], project: str | None, name: str
) -> ticket_mod.Ticket | None:
    """この書き込みを止める、着手中の子。無ければ None。

    `copies` はどのツリーの写しも並べたもの（識別子で 1 本に畳む前）。どれか 1 本でも
    着手中なら止める（止める向きだけ）。`project` は置き場を持つツリーのプロジェクト
    （ワークスペースなら空）。
    ワークスペースルート・プロジェクト・どのワークツリーでも、同じ子の置き場なら止める。
    """
    if project is None:
        return None
    for child in copies:
        if (
            child.is_child
            and child.in_progress
            and project in (child.project, ANY_PROJECT)
            and _fold(f"{child.ticket}{SUFFIX}") == name
        ):
            return child
    return None


def locked_message(conf: settings.Settings, child: ticket_mod.Ticket, path: str) -> str:
    """ロックで止めたときの文面。"""
    ticket = clean(child.ticket)
    return "\n".join(
        [
            f"[ccnavi] {CODE_LOCKED} (source: {clean(child.path)})",
            f"ticket: {ticket} ({clean(child.title)}), started {clean(child.started_at)}",
            f"path: {clean(path)}",
            f"子チケット {ticket} は着手中なので、そのフロー"
            f"（`{flow_rel(conf, child.ticket)}`）は書き換えられません。"
            "担当のサブエージェントが読んでいる手順が作業の途中で変わるのを防ぐためです。"
            "フローは人がボードのフロー編集画面で書くもので、エージェントは書きません。",
            f"ロックは {ticket} が `ccnavi-ticket.sh finish` で終わるか "
            "`cancel` で取り消されると外れます。手順を直したいなら、終わってから"
            "人に書き換えてもらうか、次の子チケットのフローに書いてもらってください。",
        ]
    )


# ---- 読む


def load(path: str, tree_root: str = "") -> tuple[dict | None, str]:
    """フローを読む。(中身, 読めない理由)。例外は外に出さない。

    tree_root を渡せば、そこからファイルまでの途中にリンクがあるものは読まない。ファイルそのものが
    リンクなら、tree_root が無くても読まない。
    """
    try:
        if tree_root and linked(tree_root, path):
            return None, LINKED
        size = os.lstat(path).st_size
        if size > FILE_LIMIT:
            return None, f"大きすぎるので読まない（{size} バイト、上限 {FILE_LIMIT}）"
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as f:
            raw = f.read(FILE_LIMIT + 1)
        if len(raw) > FILE_LIMIT:
            return None, f"大きすぎるので読まない（上限 {FILE_LIMIT} バイト）"
        data = json.loads(raw.decode("utf-8-sig"))
    except OSError as exc:
        return None, f"読めない ({clean(exc.strerror or type(exc).__name__)})"
    except RecursionError:
        return None, "入れ子が深すぎて JSON として読めない"
    except (ValueError, TypeError) as exc:
        return None, f"JSON として読めない ({_line(exc)})"
    except Exception as exc:  # noqa: BLE001  壊れたデータで SubagentStart を落とさない
        return None, f"読めない ({type(exc).__name__})"
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        return None, "`nodes` の並びが無い"
    return data, ""


# ---- 並べる


def clean(text) -> str:
    """文脈に載せる値から、改行と制御文字（書式の制御も）を除く。"""
    shown = str(text if text is not None else "")
    return "".join(
        " " if ch in "\r\n\t\v\f\x85  " else ch
        for ch in shown
        if ch in "\r\n\t\v\f\x85  " or unicodedata.category(ch) not in ("Cc", "Cf")
    )


def _text(value) -> str:
    """文字列か数だけを文にする。並びや辞書は中身を辿らない（深い入れ子で落ちない）。"""
    if isinstance(value, bool):
        return ""
    if isinstance(value, (str, int, float)):
        return str(value)
    return ""


def _line(value) -> str:
    """1 行に畳んで切る。ccnavi の名乗りは真似させない。"""
    if isinstance(value, Exception):
        value = str(value)
    text = value if isinstance(value, str) else _text(value)
    shown = " ".join(clean(text[: TEXT_LIMIT * 8]).split())
    shown = _neutral(shown)
    if len(shown) > TEXT_LIMIT:
        shown = shown[:TEXT_LIMIT] + "…"
    return shown


def _neutral(text: str) -> str:
    lowered = text.lower()
    if _BADGE not in lowered:
        return text
    out, i = [], 0
    while True:
        at = lowered.find(_BADGE, i)
        if at < 0:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:at] + _BADGE_SHOWN)
        i = at + len(_BADGE)


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _capped(parts: list[str], total: int) -> list[str]:
    """並びを ITEM_LIMIT で切り、残りの数を添える。"""
    if total > len(parts):
        return parts + [f"…ほか {total - len(parts)} 件"]
    return parts


def _labels(items, key: str) -> list[str]:
    entries = [i for i in _list(items) if isinstance(i, dict)]
    return _capped([_line(i.get(key)) for i in entries[:ITEM_LIMIT]], len(entries))


def _summary(node: dict, flows: dict) -> str:
    """ノード 1 つの中身を 1 行で。知らない種類は空。"""
    kind = _text(node.get("type"))
    data = _dict(node.get("data"))
    if kind == "prompt":
        return _line(data.get("prompt"))
    if kind == "subAgent":
        head = data.get("description") or data.get("agentDefinition")
        prompt = _line(data.get("prompt"))
        text = _line(head)
        if prompt:
            text += f" / プロンプト: {prompt}"
        if _line(data.get("builtInType")):
            text += f" / 種類: {_line(data.get('builtInType'))}"
        return text
    if kind == ASK:
        options = " | ".join(_labels(data.get("options"), "label"))
        multi = "（複数選択）" if data.get("multiSelect") is True else ""
        return f"問い: {_line(data.get('questionText'))} 選択肢{multi}: {options}"
    if kind in ("ifElse", "switch", "branch"):
        branches = [b for b in _list(data.get("branches")) if isinstance(b, dict)]
        parts = [
            f"{_line(b.get('label'))}={_line(b.get('condition'))}" for b in branches[:ITEM_LIMIT]
        ]
        parts = _capped(parts, len(branches))
        target = _line(data.get("evaluationTarget"))
        return (f"{target}: " if target else "") + " | ".join(parts)
    if kind == "skill":
        return f"スキル {_line(data.get('name'))}: {_line(data.get('description'))}"
    if kind == "mcp":
        tool = _line(data.get("toolName"))
        return f"MCP {_line(data.get('serverId'))}" + (f" / {tool}" if tool else "")
    if kind == "subAgentFlow":
        ref = flows.get(_text(data.get("subAgentFlowId")))
        name = _line(ref.get("name")) if isinstance(ref, dict) else ""
        return (_line(data.get("label")) or name) + (f"（サブフロー {name}）" if name else "")
    if kind == "codex":
        return f"Codex: {_line(data.get('prompt'))}"
    if kind in ("group", "branchSession", "start", "end"):
        return _line(data.get("label") or data.get("workDescription"))
    return ""


def _branch_index(port: str) -> int | None:
    """`branch-<番号>` の番号。番号は ASCII の数字だけ。違う綴りなら None。"""
    head, _, digits = port.rpartition("-")
    if head != "branch" or not digits or not (digits.isascii() and digits.isdigit()):
        return None
    if len(digits) > 6:
        return None
    return int(digits)


def _port_key(connection: dict) -> tuple:
    """出口の並べ順。`branch-<番号>` は番号の順、それ以外は綴りの順で後ろ。"""
    port = _text(connection.get("fromPort"))
    index = _branch_index(port)
    return (0, index, "") if index is not None else (1, 0, port)


def _port_label(node: dict, port: str) -> str:
    """分岐の出口の名前。

    出口が項目の `id` とちょうど同じか、`branch-<番号>` の番号が項目の位置なら、その `label`。
    部分一致は採らない（`b` が `branch-1` に当たる）。項目は種類で決まる欄（分岐は `branches`、
    問いは `options`）の辞書だけを数える。ボードの線の言葉（`flow-doc.ts` の
    `connectionLabel`）と同じ読み方。
    """
    key = BRANCH_KEYS.get(_text(node.get("type")))
    if not port or key is None:
        return ""
    items = [i for i in _list(_dict(node.get("data")).get(key)) if isinstance(i, dict)]
    for item in items:
        if _text(item.get("id")) and _text(item.get("id")) == port:
            return _line(item.get("label"))
    index = _branch_index(port)
    if index is not None and index < len(items):
        return _line(items[index].get("label"))
    return ""


def _node_id(node: dict) -> str:
    return _text(node.get("id"))


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
            queue.append(_text(c.get("to")))
    return list(seen) + [i for i in ids if i not in seen]


def render(
    data: dict, limit: int = RENDER_LIMIT, text_limit: int = CHILD_TEXT_LIMIT
) -> tuple[list[str], set[str]]:
    """フローを順に並べた行と、出てきたノードの種類の集合。例外は外に出さない。

    JSON を読まなくても手順が追えるよう、1 ノード 1 行で `<番号>. [<種類>] <名前>: <中身>`
    と次の番号を並べる。知らない種類は種類の名前と `name` だけ。ノードは `limit` 件まで、
    文は `text_limit` 文字まで。
    """
    try:
        return _render(data, limit, text_limit)
    except Exception:  # noqa: BLE001  壊れたデータで SubagentStart を落とさない
        return ["（フローを並べられない。ファイルを直接読んで判断する）"], set()


def _render(data, limit: int, text_limit: int) -> tuple[list[str], set[str]]:
    data = _dict(data)
    nodes: list[dict] = []
    seen_ids: set[str] = set()
    for n in _list(data.get("nodes")):
        if isinstance(n, dict) and _node_id(n) and _node_id(n) not in seen_ids:
            seen_ids.add(_node_id(n))
            nodes.append(n)
    connections = [c for c in _list(data.get("connections")) if isinstance(c, dict)]
    flows = {
        _text(f.get("id")): f
        for f in _list(data.get("subAgentFlows"))
        if isinstance(f, dict) and _text(f.get("id"))
    }
    by_id = {_node_id(n): n for n in nodes}
    ids = list(by_id)
    kinds_by = {i: _text(n.get("type")) for i, n in by_id.items()}
    out: dict[str, list[dict]] = {}
    for c in connections:
        out.setdefault(_text(c.get("from")), []).append(c)
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
        kind = _line(kinds_by[nid]) or "?"
        name = _line(node.get("name")) or _line(nid)
        body = _summary(node, flows)
        text = f"{number[nid]}. [{kind}] {name}" + (f": {body}" if body else "")
        edges = [c for c in out.get(nid, []) if _text(c.get("to")) in number]
        nexts = []
        for c in edges[:ITEM_LIMIT]:
            label = _line(c.get("condition")) or _port_label(node, _text(c.get("fromPort")))
            nexts.append(f"{number[_text(c.get('to'))]}" + (f"（{label}）" if label else ""))
        nexts = _capped(nexts, len(edges))
        if nexts:
            text += " → " + ", ".join(nexts)
        if used + len(text) > text_limit and lines:
            break
        lines.append(text)
        used += len(text)
        shown += 1
    if len(order) > shown:
        lines.append(f"…ほか {len(order) - shown} 件。続きはファイルを読む")
    return lines, kinds


def briefing(
    conf: settings.Settings,
    root: str,
    child: ticket_mod.Ticket,
    scope: str,
    budget: int = TOTAL_TEXT_LIMIT,
) -> list[str]:
    """SubagentStart でこの子について渡す行。フローが無ければ空。例外は外に出さない。

    サブエージェントの自動 compact で消えても辿り直せるよう、ファイルの絶対パスを毎回名指しする。
    手順の文は `budget` 文字（と子 1 本の上限）まで。
    """
    if not child.is_child:
        return []
    path, base, exists = resolve(conf, root, child)
    if not exists:
        return []
    lock = (
        "着手中なので、終わるまで書き換えられない（ロック）。"
        if child.in_progress
        else "着手すると、終わるまで書き換えられなくなる（ロック）。"
    )
    lines = [
        f"    フロー: {clean(path)}（人がボードで書いた {clean(child.ticket)} の手順。"
        "承認済みの領域にあり、エージェントは書けない）。作業の前にこのファイルを読み、"
        "その順に進める。文脈が要約されて見失ったら、このパスを読み直す。" + lock
    ]
    data, why = load(path, base)
    if data is None:
        lines.append(f"    フローを読めない: {why}。人に確かめる")
        return lines
    room = max(0, min(budget, CHILD_TEXT_LIMIT))
    if room == 0:
        lines.append("    手順は SubagentStart の文の上限に達したので並べない。ファイルを読む")
        return lines
    steps, kinds = render(data, text_limit=room)
    lines.append(FENCE_OPEN)
    lines.extend(f"      {s}" for s in steps)
    lines.append(FENCE_CLOSE)
    worktree = clean(tree.worktree_path(root, child.ticket))
    ticket, scope = clean(child.ticket), clean(scope) or "(空)"
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
            f"{ticket}、ワークツリー {worktree}、範囲 {scope} を書き、"
            "他の子のワークツリーには触れないことを書く。Agent ツールが無ければ（入れ子の上限）、"
            "そのノードで手を止め、起動してほしいエージェントの種類・プロンプト・担当の子チケットの"
            "ワークツリーと範囲を添えてメインに返す。メインがそのとおり起動し、結果を持って"
            "同じサブエージェントを再開させる。"
        )
    return lines
