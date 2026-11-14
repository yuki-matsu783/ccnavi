"""claude -p の契約の場面（本物と代役で同じもの）。仕様は `.agents/docs/claude-contract.md`。

GitLab の `fakeside.py` と同じ役回り。使い捨ての木、hook の入力を書き出すだけの hook、
打たせる手を持つ。hook が受けた JSON と stream-json を、形（`shape.py`）と
振る舞い（`observations`）にまとめる。

- 本物の側: `tests/manual/claude/record_hooks.py` が `PROMPT` を `claude -p` に渡し、
  `recorded.json` に録る
- 代役の側: `run_fake` が `SCRIPT`（`PROMPT` と同じ手の台本）を `tests/fake_claude.py` に渡す
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys

from tests import ROOT
from tests.contract import shape

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude")
RECORDED = os.path.join(DATA, "recorded.json")

EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "SubagentStart",
    "SubagentStop",
    "Stop",
)

# hook の本体。受けた JSON と環境の名前を記録し、tool_input の目印に合わせて止め方を変える。
HOOK = r"""
import json, os, sys, time
rec = os.path.dirname(os.path.abspath(__file__))
tag = sys.argv[1] if len(sys.argv) > 1 else ""
raw = sys.stdin.read()
t0 = time.time()
try:
    p = json.loads(raw)
except ValueError:
    p = {"_unparsed": raw}
ev = p.get("hook_event_name", "")
if ev == "UserPromptSubmit":
    time.sleep(1.0)  # 2 本の hook が並行に起きるかを見る
env = {k: v for k, v in os.environ.items() if k.startswith("CLAUDE")}
line = {"tag": tag, "payload": p, "t0": t0, "t1": time.time(),
        "env_names": sorted(env), "project_dir": env.get("CLAUDE_PROJECT_DIR"),
        "hook_cwd": os.getcwd()}
with open(os.path.join(rec, "log.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps(line, ensure_ascii=False) + "\n")
if tag != "A":
    sys.exit(0)
text = json.dumps(p.get("tool_input") or {}, ensure_ascii=False)
out = None
if ev == "SessionStart":
    out = {"hookSpecificOutput": {"hookEventName": ev, "additionalContext": "REC-SESSION-CTX"}}
elif ev == "PreToolUse" and "deny-me" in text:
    out = {"hookSpecificOutput": {"hookEventName": ev, "permissionDecision": "deny",
                                  "permissionDecisionReason": "REC-DENY"}}
elif ev == "PreToolUse" and "ask-me" in text:
    out = {"hookSpecificOutput": {"hookEventName": ev, "permissionDecision": "ask",
                                  "permissionDecisionReason": "REC-ASK"}}
elif ev == "PreToolUse" and "exit2-me" in text:
    sys.stderr.write("REC-EXIT2")
    sys.exit(2)
elif ev == "PostToolUse" and "block-post" in text:
    out = {"decision": "block", "reason": "REC-POSTBLOCK"}
elif ev == "PostToolUse" and "ctx-me" in text:
    out = {"hookSpecificOutput": {"hookEventName": ev, "additionalContext": "REC-POSTCTX"}}
elif ev == "Stop" and not p.get("stop_hook_active"):
    mark = os.path.join(rec, "stop-blocked")
    if not os.path.exists(mark):
        open(mark, "w").close()
        out = {"decision": "block",
               "reason": "REC-STOP: Write ツールで stop.txt に done と書いてから終わってください"}
if out is not None:
    print(json.dumps(out, ensure_ascii=False))
"""

PROMPT = """これはツールの動きを録るテストです。
次の手を、上から順に 1 つずつ、書いたとおりのツールで打ってください。
止められたり失敗したりしても、迂回せず、言い換えず、次の手へ進んでください。説明は要りません。

1. Read ツールで a.txt を読む
2. Write ツールで deny-me.txt に x と書く
3. Edit ツールで a.txt の hello を hi に置き換える
4. Bash ツールで `echo exit2-me` を打つ
5. Write ツールで ask-me.txt に x と書く
6. Bash ツールで `sh -c 'exit 3'` を打つ
7. Bash ツールで `echo block-post` を打つ
8. Read ツールで ctx-me.txt を読む
9. サブエージェント（general-purpose）を 1 つ起動し、次のとおり頼む。
   「Read ツールで a.txt を読み、Write ツールで sub.txt に ok と書いて終わる」
10. 最後に「おわり」とだけ答える
"""

PROMPT_RESUME = "Read ツールで a.txt を読み、「おわり」とだけ答えてください。"

# 機械・人・モデルに結び付く文字列。記録に入っていたら置かない
FORBIDDEN = [
    {"name": "メールの形", "regex": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+"},
    {
        "name": "モデルの名前",
        "regex": r"claude-(opus|sonnet|haiku|fable)|\bhaiku\b|\bsonnet\b|\bopus\b",
    },
    {"name": "機械のパス", "regex": r"/home/|/root\b|/tmp/|[A-Za-z]:\\\\"},
    {"name": "呼び出しの ID", "regex": r"toolu_"},
    {"name": "REC の目印", "regex": r"REC-"},
]

MODEL_KEY = re.compile(r"claude|haiku|sonnet|opus|fable", re.IGNORECASE)
# 呼び出しの ID（`toolu_…`・UUID・16 桁以上の 16 進）をキーにした辞書のキー
ID_KEY = re.compile(r"^toolu_|^[0-9a-f]{8}-[0-9a-f]{4}-|^[0-9a-f]{16,}$")


def settings(rec_dir: str) -> dict:
    hook = os.path.join(rec_dir, "hook.py")
    hooks = {}
    for ev in EVENTS:
        py = sys.executable
        entries = [{"type": "command", "command": f'"{py}" "{hook}" A', "timeout": 30}]
        if ev == "UserPromptSubmit":
            entries.append({"type": "command", "command": f'"{py}" "{hook}" B', "timeout": 30})
        hooks[ev] = [{"hooks": entries}]
    return {"hooks": hooks}


def build_tree(base: str) -> str:
    tree = os.path.join(base, "tree")
    rec = os.path.join(tree, ".rec")
    os.makedirs(os.path.join(tree, ".claude"))
    os.makedirs(rec)
    with open(os.path.join(rec, "hook.py"), "w", encoding="utf-8") as f:
        f.write(HOOK)
    with open(os.path.join(tree, ".claude", "settings.json"), "w", encoding="utf-8") as f:
        json.dump(settings(rec), f, ensure_ascii=False, indent=2)
    for name, body in (("a.txt", "hello\n"), ("ctx-me.txt", "ctx\n")):
        with open(os.path.join(tree, name), "w", encoding="utf-8") as f:
            f.write(body)
    subprocess.run(["git", "init", "-q", tree], check=True)
    return tree


# PROMPT と同じ手の台本（代役用）。1 回目と、--resume でつなぐ 2 回目
SCRIPT = (
    {
        "script": [
            {"tool": "Read", "input": {"file_path": "a.txt"}},
            {"tool": "Write", "input": {"file_path": "deny-me.txt", "content": "x\n"}},
            {
                "tool": "Edit",
                "input": {"file_path": "a.txt", "old_string": "hello", "new_string": "hi"},
            },
            {"tool": "Bash", "input": {"command": "echo exit2-me"}},
            {"tool": "Write", "input": {"file_path": "ask-me.txt", "content": "x\n"}},
            {"tool": "Bash", "input": {"command": "sh -c 'exit 3'"}},
            {"tool": "Bash", "input": {"command": "echo block-post"}},
            {"tool": "Read", "input": {"file_path": "ctx-me.txt"}},
            {
                "agent": {
                    "type": "general-purpose",
                    "description": "Read a.txt and write sub.txt",
                    "script": [
                        {"tool": "Read", "input": {"file_path": "a.txt"}},
                        {"tool": "Write", "input": {"file_path": "sub.txt", "content": "ok"}},
                    ],
                }
            },
            {"say": "おわり"},
        ],
        "after_stop_block": [
            {"tool": "Write", "input": {"file_path": "stop.txt", "content": "done\n"}}
        ],
    },
    {"script": [{"tool": "Read", "input": {"file_path": "a.txt"}}, {"say": "おわり"}]},
)


def read_jsonl(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            with contextlib.suppress(ValueError):
                out.append(json.loads(line))
    return out


def collapse(node: dict) -> dict:
    """形の中で、キーにモデルの名前や呼び出しの ID が入る辞書（modelUsage・wire_tool_inputs など）の
    キーを `<model>`・`<id>` にまとめる。まとめたキーどうしの形は合わせる。"""
    out = dict(node)
    if "keys" in node:
        keys: dict = {}
        renamed = False
        for k, v in node["keys"].items():
            name = "<model>" if MODEL_KEY.search(k) else "<id>" if ID_KEY.search(k) else k
            renamed |= name != k
            v = collapse(v)
            keys[name] = shape.merge(keys[name], v) if name in keys else v
        out["keys"] = dict(sorted(keys.items()))
        if renamed:
            out.pop("opt", None)
    if "items" in node:
        out["items"] = collapse(node["items"])
    return out


def add(table: dict, key: str, value) -> None:
    node = collapse(shape.normalize(value))
    table[key] = shape.merge(table[key], node) if key in table else node


def tool_key(payload: dict) -> str:
    ev = payload.get("hook_event_name", "?")
    tool = payload.get("tool_name")
    return f"{ev}:{tool}" if tool else ev


def text_in(events: list[dict], needle: str) -> list[str]:
    """needle が出た stream-json の行の type/subtype の一覧。"""
    return sorted(
        {
            f"{e.get('type')}/{e.get('subtype', '')}"
            for e in events
            if needle in json.dumps(e, ensure_ascii=False)
        }
    )


def tool_result_text(events: list[dict], tool_use_id: str) -> tuple[bool, str] | None:
    for e in events:
        if e.get("type") != "user":
            continue
        for b in (e.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("tool_use_id") == tool_use_id:
                return bool(b.get("is_error")), json.dumps(b.get("content"), ensure_ascii=False)
    return None


def observe(tree: str, logs: list[dict], streams: list[list[dict]]) -> dict:
    first = [entry for entry in logs if entry["run"] == 0]
    a_hooks = [entry for entry in first if entry["tag"] == "A"]
    s1 = streams[0]

    def pre(marker: str) -> dict | None:
        for entry in a_hooks:
            p = entry["payload"]
            if p.get("hook_event_name") == "PreToolUse" and marker in json.dumps(
                p.get("tool_input") or {}, ensure_ascii=False
            ):
                return p
        return None

    def result_of(marker: str) -> dict:
        p = pre(marker)
        if not p:
            return {"called": False}
        got = tool_result_text(s1, p.get("tool_use_id", ""))
        posts = [
            e["payload"]["hook_event_name"]
            for e in a_hooks
            if e["payload"].get("tool_use_id") == p.get("tool_use_id")
            and e["payload"].get("hook_event_name") != "PreToolUse"
        ]
        out = {"called": True, "tool": p.get("tool_name"), "later_events": posts}
        if got:
            out["is_error"], content = got
            out["reason_in_result"] = {
                m: m in content for m in ("REC-DENY", "REC-ASK", "REC-EXIT2", "REC-POSTBLOCK")
            }
        return out

    ups = [e for e in first if e["payload"].get("hook_event_name") == "UserPromptSubmit"]
    parallel = None
    if len(ups) >= 2:
        a, b = ups[0], ups[1]
        parallel = a["t0"] < b["t1"] and b["t0"] < a["t1"]
    stops = [e["payload"] for e in a_hooks if e["payload"].get("hook_event_name") == "Stop"]
    agent_payloads = [e["payload"] for e in a_hooks if e["payload"].get("agent_id")]
    sub_start = [
        e["payload"] for e in a_hooks if e["payload"].get("hook_event_name") == "SubagentStart"
    ]
    resume_starts = [
        e["payload"].get("source")
        for e in logs
        if e["run"] == 1
        and e["tag"] == "A"
        and e["payload"].get("hook_event_name") == "SessionStart"
    ]
    nested = [e for e in s1 if e.get("type") == "assistant" and e.get("parent_tool_use_id")]
    return {
        "sequence_run1": [tool_key(e["payload"]) for e in a_hooks],
        "sequence_run2": [
            tool_key(e["payload"]) for e in logs if e["run"] == 1 and e["tag"] == "A"
        ],
        "deny": {
            **result_of("deny-me"),
            "file_written": os.path.exists(os.path.join(tree, "deny-me.txt")),
        },
        "ask": {
            **result_of("ask-me"),
            "file_written": os.path.exists(os.path.join(tree, "ask-me.txt")),
        },
        "exit2": result_of("exit2-me"),
        "bash_exit3": result_of("exit 3"),
        "post_block": {**result_of("block-post"), "seen_in": text_in(s1, "REC-POSTBLOCK")},
        "post_context_seen_in": text_in(s1, "REC-POSTCTX"),
        "session_context_seen_in": text_in(s1, "REC-SESSION-CTX"),
        "user_prompt_hooks_parallel": parallel,
        "stop": {
            "count": len(stops),
            "stop_hook_active": [bool(s.get("stop_hook_active")) for s in stops],
            "continued_after_block": os.path.exists(os.path.join(tree, "stop.txt")),
            "reason_seen_in": text_in(s1, "REC-STOP"),
        },
        "subagent": {
            "started": len(sub_start),
            "agent_types": sorted({str(p.get("agent_type")) for p in sub_start}),
            "nested_tool_events": sorted({tool_key(p) for p in agent_payloads}),
            "stream_nested_assistant_lines": len(nested),
            "launcher_tools": sorted(
                {
                    p.get("tool_name")
                    for p in (e["payload"] for e in a_hooks)
                    if p.get("hook_event_name") == "PreToolUse"
                    and not p.get("agent_id")
                    and p.get("tool_name") not in ("Read", "Write", "Edit", "Bash")
                }
            ),
        },
        "resume_session_start_source": resume_starts,
        "project_dir_is_cwd": sorted(
            {
                e["project_dir"] == e["payload"].get("cwd")
                for e in a_hooks
                if e["payload"].get("cwd")
            }
        ),
        "hook_cwd_is_project_dir": sorted({e["hook_cwd"] == e["project_dir"] for e in a_hooks}),
        "env_names": sorted(
            {
                n
                for e in a_hooks
                for n in e["env_names"]
                if n.startswith("CLAUDE_PROJECT")
                or n.startswith("CLAUDE_CODE_ENTRY")
                or n == "CLAUDE_ENV_FILE"
            }
        ),
    }


def collect(tree: str, run) -> dict:
    """run(回の番号, prompt) で 2 回回し、hook と stream-json を形と振る舞いにまとめる。
    run は stream-json の文字列を返す。"""
    log_path = os.path.join(tree, ".rec", "log.jsonl")
    streams_text = [run(0, PROMPT)]
    with open(log_path, encoding="utf-8") as f:
        n_first = len(f.readlines())
    streams_text.append(run(1, PROMPT_RESUME))
    with open(log_path, encoding="utf-8") as f:
        logs = [dict(json.loads(line), run=0 if i < n_first else 1) for i, line in enumerate(f)]
    streams = [read_jsonl(t) for t in streams_text]
    hooks: dict = {}
    for entry in logs:
        if entry["tag"] == "A":
            add(hooks, tool_key(entry["payload"]), entry["payload"])
    stream: dict = {}
    for events in streams:
        for e in events:
            add(stream, f"{e.get('type')}/{e.get('subtype', '')}", e)
    return {
        "hooks": dict(sorted(hooks.items())),
        "stream": dict(sorted(stream.items())),
        "observations": observe(tree, logs, streams),
        "_raw": {"streams": streams_text, "log_path": log_path, "streams_parsed": streams},
    }


def run_fake(base: str) -> dict:
    """使い捨ての木で代役を 2 回回し、collect の結果を返す。"""
    tree = build_tree(base)
    session = "00000000-0000-4000-8000-000000000000"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "CCNAVI_"))}
    env["PYTHONPATH"] = ROOT
    env["CCNAVI_FAKE_CLAUDE_HOME"] = os.path.join(base, "home")

    def run(n: int, prompt: str) -> str:
        script = os.path.join(base, f"script-{n}.json")
        with open(script, "w", encoding="utf-8") as f:
            json.dump(SCRIPT[n], f, ensure_ascii=False)
        cmd = [
            sys.executable,
            "-m",
            "tests.fake_claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-hook-events",
            "--permission-mode",
            "auto",
        ]
        cmd += ["--resume", session] if n else ["--session-id", session]
        r = subprocess.run(
            cmd,
            cwd=tree,
            capture_output=True,
            text=True,
            timeout=120,
            env={**env, "CCNAVI_FAKE_CLAUDE_SCRIPT": script},
        )
        if r.returncode != 0:
            raise RuntimeError(f"代役が終了コード {r.returncode} で終わった: {r.stderr}")
        return r.stdout

    return collect(tree, run)
