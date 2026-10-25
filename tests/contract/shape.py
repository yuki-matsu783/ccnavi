"""GitLab の応答の「形」を作り、比べる。仕様は `docs/42_claude/gitlab-contract.md`。

形（node）は JSON で表せる辞書で、TypeScript 側（#553）も同じ表で読む。

    {"t": ["integer", "null"],       型の和集合（並べ替え済み）
     "keys": {名前: node},            object のとき。キーの順は持たない
     "opt": [名前, ...],              keys のうち任意（一部の要素にだけある）のもの
     "items": node}                   array のとき。要素の形の和集合。空の配列は持たない
"""

from __future__ import annotations

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "gitlab")

# これより深い入れ物は object / array に丸める
MAX_DEPTH = 5
# 環境ごとに変わる欄。値だけでなく有無も比べない
DROPPED_KEYS = frozenset(
    {"avatar_url", "web_url", "username", "email", "public_email", "commit_email", "token"}
)
DATETIME = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)$")

KINDS = ("missing", "extra", "type", "presence", "status")


def load_data(name: str):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def type_of(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "datetime" if DATETIME.match(value) else "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    raise TypeError(f"JSON の値ではない: {type(value).__name__}")


def normalize(value, depth: int = 0) -> dict:
    kind = type_of(value)
    node: dict = {"t": [kind]}
    if depth >= MAX_DEPTH:
        return node
    if kind == "object":
        node["keys"] = {
            k: normalize(v, depth + 1) for k, v in sorted(value.items()) if k not in DROPPED_KEYS
        }
    elif kind == "array":
        items = None
        for element in value:
            shape = normalize(element, depth + 1)
            items = shape if items is None else merge(items, shape)
        if items is not None:
            node["items"] = items
    return node


def merge(a: dict, b: dict) -> dict:
    """同じ場所の 2 つの形を 1 つにする（配列の要素の和集合）。"""
    out: dict = {"t": sorted(set(a["t"]) | set(b["t"]))}
    if "keys" in a or "keys" in b:
        ka, kb = a.get("keys", {}), b.get("keys", {})
        # 片方の object にしかないキーは任意。もう片方が object でない（null など）ときは、
        # そのキーが「無い」のではなく object ごと無いので、任意にしない
        a_obj, b_obj = "keys" in a, "keys" in b
        opt = set(a.get("opt", [])) | set(b.get("opt", []))
        if a_obj and b_obj:
            opt |= set(ka) ^ set(kb)
        out["keys"] = {
            k: merge(ka[k], kb[k]) if k in ka and k in kb else ka.get(k) or kb[k]
            for k in sorted(set(ka) | set(kb))
        }
        if opt:
            out["opt"] = sorted(opt)
    if "items" in a or "items" in b:
        ia, ib = a.get("items"), b.get("items")
        out["items"] = merge(ia, ib) if ia and ib else ia or ib
    return out


def compare(expected: dict, actual: dict, path: str = "") -> list[tuple[str, str]]:
    """(キー, 違いの種類) の一覧。キーは `author.id`・`notes[].system`。根は `$`。"""
    here = path or "$"
    if expected["t"] != actual["t"]:
        return [(here, "type")]
    diffs: list[tuple[str, str]] = []
    if "keys" in expected and "keys" in actual:
        ek, ak = expected["keys"], actual["keys"]
        for k in sorted(set(ek) | set(ak)):
            sub = f"{path}.{k}" if path else k
            if k not in ak:
                diffs.append((sub, "missing"))
            elif k not in ek:
                diffs.append((sub, "extra"))
            else:
                if (k in expected.get("opt", [])) != (k in actual.get("opt", [])):
                    diffs.append((sub, "presence"))
                diffs += compare(ek[k], ak[k], sub)
    # 片方が空の配列（items が無い）なら、比べる材料が無い
    if "items" in expected and "items" in actual:
        diffs += compare(expected["items"], actual["items"], f"{path}[]")
    return diffs


def compare_all(recorded: dict, actual: dict) -> dict[str, list[tuple[str, str]]]:
    """要求の名前ごとの違い。recorded / actual は `{名前: {"status", "shape"}}`。"""
    found: dict[str, list[tuple[str, str]]] = {}
    for name, want in recorded.items():
        got = actual.get(name)
        if got is None:
            found[name] = [("$", "missing")]
            continue
        diffs: list[tuple[str, str]] = []
        if want["status"] != got["status"]:
            diffs.append(("$status", "status"))
        diffs += compare(want["shape"], got["shape"])
        if diffs:
            found[name] = diffs
    for name in sorted(set(actual) - set(recorded)):
        found[name] = [("$", "extra")]
    return found


def apply_allow(
    found: dict[str, list[tuple[str, str]]], entries: list[dict]
) -> tuple[dict[str, list[tuple[str, str]]], list[str]]:
    """許可リストを当てる。(残った違い, 許可リストの問題)。

    問題: 理由が空、書式が違う、実際は差が出ていない（古い許可）。
    """
    problems: list[str] = []
    used: set[int] = set()
    left: dict[str, list[tuple[str, str]]] = {}
    for i, entry in enumerate(entries):
        label = f"許可リスト[{i}] {entry.get('request')}/{entry.get('key')}/{entry.get('kind')}"
        if not str(entry.get("reason", "")).strip():
            problems.append(f"{label}: 理由が空")
        if entry.get("kind") not in KINDS:
            problems.append(f"{label}: 違いの種類が {KINDS} のどれでもない")
    index = {(e.get("request"), e.get("key"), e.get("kind")): i for i, e in enumerate(entries)}
    for name, diffs in found.items():
        for key, kind in diffs:
            hit = index.get((name, key, kind))
            if hit is None:
                left.setdefault(name, []).append((key, kind))
            else:
                used.add(hit)
    for i, entry in enumerate(entries):
        if i not in used:
            problems.append(
                f"許可リスト[{i}] {entry.get('request')}/{entry.get('key')}/{entry.get('kind')}:"
                " 実際は差が出ていない（古い許可）"
            )
    return left, problems


def scan_forbidden(text: str, forbidden: list[dict]) -> list[str]:
    """禁止する文字列に当たった名前の一覧。当たりが無ければ空。"""
    return [f["name"] for f in forbidden if re.search(f["regex"], text)]


PLACEHOLDER = re.compile(r"\$\{([a-z_]+(?:\.[a-z_]+)*)\}")


def substitute(value, context: dict[str, str]):
    """要求の文字列の `${名前}` を実体に置き換える（入れ子の辞書・配列も）。"""
    if isinstance(value, str):
        return PLACEHOLDER.sub(lambda m: str(context[m.group(1)]), value)
    if isinstance(value, dict):
        return {k: substitute(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, context) for v in value]
    return value
