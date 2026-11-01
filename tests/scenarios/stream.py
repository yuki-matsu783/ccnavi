"""claude -p の出力（stream-json）を読む（段 0）。外を呼ばない。"""

import json


def _blocks(message):
    content = (message or {}).get("content")
    return content if isinstance(content, list) else []


def parse_stream(text):
    """stream-json の行から、証拠に使うものを抜き出す。形が違う行は読み飛ばす。"""
    out = {
        "session_ids": [],
        "tool_calls": [],
        "tool_results": [],
        "hook_events": [],
        "final_text": "",
        "cost_usd": 0.0,
    }
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        sid = ev.get("session_id")
        if sid and sid not in out["session_ids"]:
            out["session_ids"].append(sid)
        kind = ev.get("type")
        if kind == "assistant":
            for b in _blocks(ev.get("message")):
                if b.get("type") == "tool_use":
                    out["tool_calls"].append(
                        {
                            "name": b.get("name"),
                            "input": b.get("input"),
                            "id": b.get("id"),
                            "parent": ev.get("parent_tool_use_id"),
                        }
                    )
        elif kind == "user":
            for b in _blocks(ev.get("message")):
                if b.get("type") == "tool_result":
                    out["tool_results"].append(
                        {
                            "is_error": bool(b.get("is_error")),
                            "content": b.get("content"),
                            "tool_use_id": b.get("tool_use_id"),
                            "parent": ev.get("parent_tool_use_id"),
                        }
                    )
        elif kind == "result":
            out["final_text"] = ev.get("result") or ""
            out["cost_usd"] += float(ev.get("total_cost_usd") or 0)
        elif "hook" in str(ev.get("subtype", "")) or "hook" in str(kind):
            out["hook_events"].append(ev)
    return out


def text_of(content):
    """ツール結果の content（文字列かブロックの列）を 1 つの文字列にする。"""
    if content is None:
        return ""
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                parts.append(b.get("text") or f"[{b.get('type', '?')} ブロック]")
            else:
                parts.append(str(b))
        return "\n".join(parts)
    return str(content)
