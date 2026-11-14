"""`claude -p` の代役。台本どおりにツールを呼び、Claude Code と同じ形で hook を呼び、
stream-json を出す。

受入シナリオを `--agent script` で回すとき、実行器（`tests/scenarios/drive.py`）が
`claude` の代わりに起こす。仕様は `.agents/docs/claude-contract.md`。形が本物と合っていることは
`tests/contract/test_claude_contract.py` が、本物から録った
`tests/contract/claude/recorded.json` と比べて確かめる。

    python -m tests.fake_claude -p <prompt> --output-format stream-json --verbose \\
        --include-hook-events --session-id <id>      （実行器が渡す引数と同じ）

台本は環境変数 `CCNAVI_FAKE_CLAUDE_SCRIPT` が指す JSON
（`{"script": [...], "after_stop_block": [...]}`）。
1 手は次のどれか。分岐しない（止められても次の手へ進む）。

- `{"tool": "Read" | "Write" | "Edit" | "Bash", "input": {...}}`
  （`file_path` は cwd からの相対で書く）
- `{"say": "<文>"}`（最後の文。`result` に入る）
- `{"agent": {"type": ..., "description": ..., "script": [...]}}`
  （サブエージェント。`Agent` ツールとして出す）

モデル・費用・Claude Code 自身の権限の仕組みは真似ない。hook が止めなければツールを実行する。
同じイベントに当たった hook は、本物と同じく並行に起こす。
セッションの記録は `CCNAVI_FAKE_CLAUDE_HOME`（既定は一時ディレクトリの下）に置く。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor

SCRIPT_ENV = "CCNAVI_FAKE_CLAUDE_SCRIPT"
HOME_ENV = "CCNAVI_FAKE_CLAUDE_HOME"
TOOLS = ("Read", "Write", "Edit", "Bash")
HOOK_TIMEOUT_SEC = 60
SUBAGENT_DONE_PROMPT = "<task-notification>サブエージェントが終わった</task-notification>"


class Blocked(Exception):
    """hook に止められたことを表す例外。`text` はモデルに渡る文。"""

    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


class Session:
    def __init__(self, args, root: str):
        self.args = args
        self.root = root
        self.id = args.resume or args.session_id or str(uuid.uuid4())
        home = os.environ.get(HOME_ENV) or os.path.join(tempfile.gettempdir(), "ccnavi-fake-claude")
        os.makedirs(home, exist_ok=True)
        self.home = home
        self.transcript = os.path.join(home, f"{self.id}.jsonl")
        self.prompt_id = str(uuid.uuid4())
        self.hooks = _load_hooks(root)
        self.out = sys.stdout
        self.last_text = ""
        self.ran_agent = False
        self.current_call = None

    # 出力 ------------------------------------------------------------------

    def emit(self, line: dict, transcript: bool = False) -> None:
        line = {**line, "session_id": self.id, "uuid": str(uuid.uuid4())}
        text = json.dumps(line, ensure_ascii=False)
        self.out.write(text + "\n")
        self.out.flush()
        if transcript and not self.args.no_session_persistence:
            with open(self.transcript, "a", encoding="utf-8") as f:
                f.write(text + "\n")

    # hook ------------------------------------------------------------------

    def base(self, event: str, agent: dict | None = None) -> dict:
        p = {
            "session_id": self.id,
            "transcript_path": self.transcript,
            "cwd": self.root,
            "hook_event_name": event,
        }
        if event != "SessionStart":
            p["prompt_id"] = self.prompt_id
        if event not in ("SessionStart", "SubagentStart"):
            p["permission_mode"] = self.args.permission_mode
        if agent:
            p["agent_id"], p["agent_type"] = agent["id"], agent["type"]
        return p

    def fire(self, payload: dict) -> Blocked | None:
        """payload のイベントに当たる hook を起こし、止めたら Blocked を返す（最初の 1 本）。"""
        event, tool = payload["hook_event_name"], payload.get("tool_name")
        name = f"{event}:{tool}" if tool else event
        if event == "SessionStart":
            name = f"{event}:{payload['source']}"
        hooks = [
            h
            for h in _matching(self.hooks.get(event, []), tool)
            if not h.get("asyncRewake") and h.get("type", "command") == "command"
        ]
        ids = [str(uuid.uuid4()) for _ in hooks]
        for hook_id in ids:
            self.hook_line("hook_started", hook_id, name, event)
        # 本物と同じく、同じイベントに当たった hook は並行に起こす
        with ThreadPoolExecutor(max_workers=max(1, len(hooks))) as pool:
            results = list(pool.map(lambda h: self.run_hook(h, payload), hooks))
        blocked = None
        for hook_id, (code, stdout, stderr) in zip(ids, results, strict=True):
            self.hook_line(
                "hook_response",
                hook_id,
                name,
                event,
                output=stdout + stderr,
                stdout=stdout,
                stderr=stderr,
                exit_code=code,
                outcome="success" if code == 0 else "error",
            )
            blocked = blocked or _blocking(event, tool, code, stdout, stderr)
        return blocked

    def hook_line(self, subtype: str, hook_id: str, name: str, event: str, **extra) -> None:
        if self.args.include_hook_events:
            self.emit(
                {
                    "type": "system",
                    "subtype": subtype,
                    "hook_id": hook_id,
                    "hook_name": name,
                    "hook_event": event,
                    **extra,
                }
            )

    def run_hook(self, hook: dict, payload: dict) -> tuple[int, str, str]:
        env = {
            **os.environ,
            "CLAUDE_PROJECT_DIR": self.root,
            "CLAUDE_ENV_FILE": os.path.join(self.home, f"{self.id}.env"),
        }
        # 本物の -p は、外から渡されていなければ sdk-cli を入れる。
        # ccnavi は、この値で非対話かどうかを見分ける
        env.setdefault("CLAUDE_CODE_ENTRYPOINT", "sdk-cli")
        env.pop(SCRIPT_ENV, None)
        try:
            r = subprocess.run(
                ["sh", "-c", hook["command"]],
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                cwd=self.root,
                env=env,
                timeout=hook.get("timeout", HOOK_TIMEOUT_SEC),
            )
        except subprocess.TimeoutExpired:
            return 1, "", "hook がタイムアウトした"
        return r.returncode, r.stdout, r.stderr

    # 手 --------------------------------------------------------------------

    def play(self, moves: list, agent: dict | None = None) -> None:
        for move in moves:
            if "say" in move:
                self.last_text = move["say"]
                self.emit(_assistant([{"type": "text", "text": move["say"]}], agent), True)
            elif "agent" in move:
                self.run_agent(move["agent"])
            else:
                self.use_tool(move["tool"], dict(move.get("input") or {}), agent)

    def use_tool(self, tool: str, tool_input: dict, agent: dict | None, runner=None):
        if "file_path" in tool_input:
            tool_input["file_path"] = os.path.join(self.root, tool_input["file_path"])
        if tool == "Bash":
            tool_input.setdefault("description", "")
        call = f"toolu_{uuid.uuid4().hex[:24]}"
        self.emit(
            _assistant(
                [{"type": "tool_use", "id": call, "name": tool, "input": tool_input}], agent
            ),
            True,
        )
        pre = {
            **self.base("PreToolUse", agent),
            "tool_name": tool,
            "tool_input": tool_input,
            "tool_use_id": call,
        }
        blocked = self.fire(pre)
        if blocked:
            self.emit(_result(call, blocked.text, True, agent), True)
            return None
        self.current_call = call
        try:
            text, response = (runner or _TOOLS[tool])(tool_input)
        except ToolFailed as exc:
            self.fire(
                {
                    **self.base("PostToolUseFailure", agent),
                    "tool_name": tool,
                    "tool_input": tool_input,
                    "tool_use_id": call,
                    "error": exc.text,
                    "is_interrupt": False,
                }
            )
            self.emit(_result(call, exc.text, True, agent), True)
            return None
        self.fire(
            {
                **self.base("PostToolUse", agent),
                "tool_name": tool,
                "tool_input": tool_input,
                "tool_response": response,
                "tool_use_id": call,
            }
        )
        self.emit(_result(call, text, False, agent), True)
        return response

    def run_agent(self, spec: dict) -> None:
        agent = {"id": uuid.uuid4().hex[:17], "type": spec.get("type", "general-purpose")}
        tool_input = {
            "description": spec.get("description", ""),
            "prompt": spec.get("prompt", ""),
            "subagent_type": agent["type"],
            "run_in_background": False,
        }

        def run(_input):
            agent["call"] = self.current_call
            self.fire(self.base("SubagentStart", agent))
            self.play(spec.get("script") or [], agent)
            report = spec.get("report", "完了")
            self.use_tool(
                "SubagentHandback",
                {"message": report},
                agent,
                runner=lambda _i: (
                    json.dumps({"success": True}),
                    {"success": True, "message": "Report delivered"},
                ),
            )
            self.fire(
                {
                    **self.base("SubagentStop", agent),
                    "stop_hook_active": False,
                    "agent_transcript_path": self.transcript,
                    "background_tasks": [],
                    "session_crons": [],
                }
            )
            return report, {
                "status": "completed",
                "prompt": tool_input["prompt"],
                "agentId": agent["id"],
                "agentType": agent["type"],
                "content": [{"type": "text", "text": report}],
            }

        self.use_tool("Agent", tool_input, None, runner=run)
        self.ran_agent = True

    def stop(self, active: bool) -> Blocked | None:
        return self.fire(
            {
                **self.base("Stop"),
                "stop_hook_active": active,
                "last_assistant_message": self.last_text,
                "background_tasks": [],
                "session_crons": [],
            }
        )

    def prompt(self, text: str) -> None:
        self.fire({**self.base("UserPromptSubmit"), "prompt": text})


class ToolFailed(Exception):
    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


def _read(ti):
    try:
        with open(ti["file_path"], encoding="utf-8") as f:
            content = f.read()
    except OSError as exc:
        raise ToolFailed(f"File does not exist. {exc.strerror}") from None
    lines = content.split("\n")
    shown = "\n".join(f"{n}\t{line}" for n, line in enumerate(lines, 1))
    return shown, {
        "type": "text",
        "file": {
            "filePath": ti["file_path"],
            "content": content,
            "numLines": len(lines),
            "startLine": 1,
            "totalLines": len(lines),
        },
    }


def _write(ti):
    path = ti["file_path"]
    original = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            original = f.read()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(ti.get("content", ""))
    kind = "create" if original is None else "update"
    text = (
        f"File created successfully at: {path}"
        if original is None
        else f"The file {path} has been updated successfully."
    )
    return text, {
        "type": kind,
        "filePath": path,
        "content": ti.get("content", ""),
        "structuredPatch": [],
        "originalFile": original,
        "userModified": False,
    }


def _edit(ti):
    path, old, new = ti["file_path"], ti.get("old_string", ""), ti.get("new_string", "")
    try:
        with open(path, encoding="utf-8") as f:
            original = f.read()
    except OSError:
        raise ToolFailed("File does not exist.") from None
    count = original.count(old) if old else 0
    if count == 0:
        raise ToolFailed(f"String to replace not found in file.\nString: {old}")
    if count > 1 and not ti.get("replace_all"):
        raise ToolFailed(
            f"Found {count} matches of the string to replace, but replace_all is false."
        )
    ti.setdefault("replace_all", False)
    with open(path, "w", encoding="utf-8") as f:
        f.write(original.replace(old, new) if ti["replace_all"] else original.replace(old, new, 1))
    return f"The file {path} has been updated successfully.", {
        "filePath": path,
        "oldString": old,
        "newString": new,
        "originalFile": original,
        "structuredPatch": [],
        "userModified": False,
        "replaceAll": ti["replace_all"],
    }


def _bash(ti):
    r = subprocess.run(["sh", "-c", ti["command"]], capture_output=True, text=True)
    out = (r.stdout + r.stderr).rstrip("\n")
    if r.returncode != 0:
        raise ToolFailed(f"Exit code {r.returncode}" + (f"\n{out}" if out else ""))
    return out, {
        "stdout": r.stdout.rstrip("\n"),
        "stderr": r.stderr.rstrip("\n"),
        "interrupted": False,
        "isImage": False,
        "noOutputExpected": False,
    }


_TOOLS = {"Read": _read, "Write": _write, "Edit": _edit, "Bash": _bash}


def _assistant(content: list, agent: dict | None) -> dict:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": content},
        "parent_tool_use_id": agent and agent.get("call"),
    }


def _result(call: str, text: str, is_error: bool, agent: dict | None) -> dict:
    block = {"type": "tool_result", "tool_use_id": call, "content": text}
    if is_error:
        block["is_error"] = True
    return {
        "type": "user",
        "message": {"role": "user", "content": [block]},
        "parent_tool_use_id": agent and agent.get("call"),
    }


def _load_hooks(root: str) -> dict:
    try:
        with open(os.path.join(root, ".claude", "settings.json"), encoding="utf-8") as f:
            return json.load(f).get("hooks") or {}
    except FileNotFoundError:
        return {}


def _matching(groups: list, tool: str | None):
    for group in groups:
        matcher = group.get("matcher") or ""
        if matcher and matcher != "*" and not (tool and re.fullmatch(matcher, tool)):
            continue
        yield from group.get("hooks") or []


def _blocking(event: str, tool: str | None, code: int, stdout: str, stderr: str) -> Blocked | None:
    """hook の応答のうち、止めるものを Blocked にする。
    文は、本物の tool_result と差し戻しの文に合わせる。"""
    label = f"{event}:{tool}" if tool else event
    if code == 2:
        reason = f"[command hook]: {stderr.strip()}"
        return Blocked(
            f"Stop hook feedback:\n{reason}" if event == "Stop" else f"{label} hook error: {reason}"
        )
    if code != 0:
        return None
    try:
        out = json.loads(stdout) if stdout.strip() else {}
    except ValueError:
        return None
    if not isinstance(out, dict):
        return None
    spec = out.get("hookSpecificOutput") or {}
    decision = spec.get("permissionDecision")
    if event == "PreToolUse" and decision == "deny":
        return Blocked(f"{label} hook error: {spec.get('permissionDecisionReason', '')}")
    if event == "PreToolUse" and decision == "ask":  # -p では止まる（本物で録った）
        return Blocked(spec.get("permissionDecisionReason", ""))
    if event == "Stop" and out.get("decision") == "block":
        return Blocked(f"Stop hook feedback:\n{out.get('reason', '')}")
    return None


def _check_moves(moves, where="script") -> None:
    for i, move in enumerate(moves or [], 1):
        if not isinstance(move, dict) or len(set(move) & {"tool", "say", "agent"}) != 1:
            raise SystemExit(f"{where} の {i} 手目: tool・say・agent のどれか 1 つを書く")
        if "tool" in move and move["tool"] not in TOOLS:
            raise SystemExit(f"{where} の {i} 手目: tool は {' / '.join(TOOLS)} のどれか")
        if "agent" in move:
            _check_moves(move["agent"].get("script"), f"{where} の {i} 手目の agent")


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="fake_claude")
    ap.add_argument("-p", "--print", dest="prompt", required=True)
    ap.add_argument("--model")
    ap.add_argument("--output-format", choices=["stream-json"], required=True)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--include-hook-events", action="store_true")
    ap.add_argument("--max-budget-usd")
    ap.add_argument("--permission-mode", default="default")
    ap.add_argument("--setting-sources")
    ids = ap.add_mutually_exclusive_group()
    ids.add_argument("--session-id")
    ids.add_argument("--resume")
    ap.add_argument("--no-session-persistence", action="store_true")
    return ap.parse_args(argv)  # 知らない引数は終了コード 2（実行器の追加に追従させる）


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    path = os.environ.get(SCRIPT_ENV)
    if not path:
        raise SystemExit(f"{SCRIPT_ENV} に台本の JSON のパスを入れる")
    with open(path, encoding="utf-8") as f:
        plan = json.load(f)
    _check_moves(plan.get("script"))
    _check_moves(plan.get("after_stop_block"), "after_stop_block")

    s = Session(args, os.getcwd())
    s.emit(
        {
            "type": "system",
            "subtype": "init",
            "cwd": s.root,
            "tools": [*TOOLS, "Agent"],
            "permissionMode": args.permission_mode,
        }
    )
    s.fire({**s.base("SessionStart"), "source": "resume" if args.resume else "startup"})
    s.prompt(args.prompt)
    s.play(plan.get("script") or [])
    if s.ran_agent:  # 本物はサブエージェントが終わった知らせで、もう一度 UserPromptSubmit を起こす
        s.prompt(SUBAGENT_DONE_PROMPT)
    blocked = s.stop(False)
    stop_reason = "end_turn"
    if blocked:
        s.emit(
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "text", "text": blocked.text}]},
                "parent_tool_use_id": None,
                "isSynthetic": True,
            },
            True,
        )
        s.play(plan.get("after_stop_block") or [])
        if s.stop(True):
            stop_reason = "stop_hook_blocked_twice"
    s.emit(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": s.last_text,
            "stop_reason": stop_reason,
            "total_cost_usd": 0.0,
            "num_turns": 1,
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
