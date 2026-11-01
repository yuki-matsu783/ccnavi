"""証拠の読み取り（段 1）。

hook の応答から、渡った文脈・拒否の傾向・迂回の疑い・文面の一覧の元を取り出す。
"""

import json
import re

from ccnavi.infra import pathcase
from tests.scenarios import stream
from tests.scenarios.common import clip

EVIDENCE_LIMIT = 1000


CONTEXT_LIMIT = 2000


def _hook_payload(ev):
    """hook_response の stdout / output が JSON オブジェクトならその dict を返す。違えば {}。"""
    for key in ("stdout", "output"):
        raw = ev.get(key)
        if isinstance(raw, str) and raw.lstrip().startswith("{"):
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
    return {}


def summarize_hooks(hook_events, where):
    """hook の応答を、判定に要るものの行と、省いたものの件数に分ける。
    残すのは、拒否・確認（permissionDecision が deny / ask、または decision が block）、
    案内文（additionalContext。SessionStart のものを除く）、中身のある Stop の応答、
    終了コードが 0 でない応答、停止（continue: false / stopReason）、
    systemMessage（SessionStart を除く）も残す。
    hook_started / hook_progress は数えない。SessionStart と出力が空の応答は件数だけにする。"""
    lines, omitted = [], {}
    for ev in hook_events:
        if ev.get("subtype") != "hook_response":
            continue
        name = ev.get("hook_event") or ev.get("hook_event_name") or "?"
        data = _hook_payload(ev)
        exit_code = ev.get("exit_code")
        spec = data.get("hookSpecificOutput")
        spec = spec if isinstance(spec, dict) else {}
        decision = spec.get("permissionDecision") or data.get("decision")
        reason = spec.get("permissionDecisionReason") or data.get("reason")
        context = spec.get("additionalContext")
        label = f"{name} {ev.get('hook_name', '')}".strip()
        if decision in ("deny", "ask", "block"):
            # 拒否の文面は切らない
            lines.append(f"- [{label}] {decision}: {reason or ''}")
        elif isinstance(exit_code, int) and exit_code != 0:
            # 終了コードが 0 でない応答（exit 2 のブロックなど）は、文面を切らない
            body = (
                (ev.get("stderr") or "").strip()
                or (ev.get("stdout") or "").strip()
                or str(ev.get("output") or "")
            )
            lines.append(f"- [{label}] 終了コード {exit_code}: {body}")
        elif data.get("continue") is False or data.get("stopReason"):
            lines.append(f"- [{label}] 停止: {data.get('stopReason') or ''}")
        elif name == "Stop" and (data or ev.get("stdout") or ev.get("stderr")):
            body = (
                json.dumps(data, ensure_ascii=False)
                if data
                else ev.get("stdout") or ev.get("stderr")
            )
            lines.append(f"- [{label}] {clip(body, EVIDENCE_LIMIT, where)}")
        elif context and name != "SessionStart":
            lines.append(f"- [{label}] 案内: {clip(context, CONTEXT_LIMIT, where)}")
        elif data.get("systemMessage") and name != "SessionStart":
            lines.append(f"- [{label}] 通知: {clip(data['systemMessage'], CONTEXT_LIMIT, where)}")
        else:
            omitted[name] = omitted.get(name, 0) + 1
    return lines, omitted


def hook_denial_texts(hook_events):
    """拒否・確認・ブロックの hook 応答にある理由の文面を集める。
    ツール結果のエラーとの重複判定に使う。"""
    texts = []
    for ev in hook_events:
        if ev.get("subtype") != "hook_response":
            continue
        data = _hook_payload(ev)
        spec = data.get("hookSpecificOutput")
        spec = spec if isinstance(spec, dict) else {}
        decision = spec.get("permissionDecision") or data.get("decision")
        reason = spec.get("permissionDecisionReason") or data.get("reason")
        text = str(reason).strip() if reason else ""
        if decision in ("deny", "ask", "block") and text:
            texts.append(text)
    return texts


def duplicates_hook(text, denial_texts):
    """エラーの文面が、hook の拒否理由と（前後の空白を除いて）まったく同じなら True。"""
    text = text.strip()
    if not text:
        return False
    return text in denial_texts


CONTEXT_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse")

GUIDE_HEADS = ("次:", "案内:", "必須:")

REPEAT_MIN = 3

AGENT_TOOL = "Agent"

WRITE_TOOLS = ("Edit", "Write")

# decisions.jsonl の subject が上限で切られた目印（src/ccnavi/records/audit.py の _limited）
CUT_MARK = "…(+"


def _hook_name(ev):
    return ev.get("hook_event") or ev.get("hook_event_name") or "?"


def hook_items(hook_events):
    """hook の応答から、エージェントかユーザに渡る文面を取り出す。
    kind は 拒否（deny・block）・確認（ask）・案内（additionalContext）。
    1 つの応答が理由と案内の両方を持てば 2 件になる。"""
    items = []
    for ev in hook_events:
        if ev.get("subtype") != "hook_response":
            continue
        data = _hook_payload(ev)
        spec = data.get("hookSpecificOutput")
        spec = spec if isinstance(spec, dict) else {}
        decision = spec.get("permissionDecision") or data.get("decision")
        reason = spec.get("permissionDecisionReason") or data.get("reason")
        base = {"event": _hook_name(ev), "label": ev.get("hook_name") or _hook_name(ev)}
        if decision in ("deny", "ask", "block") and reason:
            kind = "確認" if decision == "ask" else "拒否"
            items.append({**base, "kind": kind, "text": str(reason).strip()})
        context = spec.get("additionalContext")
        if context:
            items.append({**base, "kind": "案内", "text": str(context).strip()})
    return items


def fence(text):
    """text を囲むコードフェンスの行。text の中のバッククォートの列より長くする。"""
    bar = "`" * (max([len(m) for m in re.findall(r"`+", text)] + [2]) + 1)
    return [f"{bar}text", text, bar]


def step_streams(out):
    """出力ディレクトリの step-N.jsonl を parse_stream して {N: 結果} にする（N の昇順）。"""
    streams = {}
    for p in sorted(out.glob("step-*.jsonl"), key=lambda q: int(q.stem.split("-")[1])):
        streams[int(p.stem.split("-")[1])] = stream.parse_stream(p.read_text(encoding="utf-8"))
    return streams


def context_lines(parsed, num, out):
    """step に渡った文脈の節（evidence.md）の行を返し、全文を context-<num>.md に書く。
    親とサブエージェントは分ける。何も無ければ [] を返し、ファイルも書かない。"""
    where = f"context-{num}.md"
    items = hook_items(parsed["hook_events"])
    main = [i for i in items if i["kind"] != "案内" or i["event"] in CONTEXT_EVENTS]
    sub_ctx = [i for i in items if i["kind"] == "案内" and i["event"] == "SubagentStart"]
    calls = parsed["tool_calls"]
    agents = [c for c in calls if c["name"] == AGENT_TOOL and c.get("id")]
    if not (main or agents or sub_ctx):
        return []
    lines = [
        "",
        f"### 渡った文脈（全文は {where}）",
        "SessionStart・UserPromptSubmit・PreToolUse の案内（additionalContext）と、"
        "拒否・確認の理由。先頭だけを出す。",
    ]
    full = [f"# step {num} に渡った文脈（全文）"]

    def add(heading, text, limit):
        n = len(text)
        lines.append(f"- {heading}（{n} 文字）")
        lines.extend(fence(clip(text, limit, where)))
        full.extend(["", f"## {heading}（{n} 文字）", *fence(text)])

    for i in main:
        limit = CONTEXT_LIMIT if i["kind"] == "案内" else EVIDENCE_LIMIT
        add(f"{i['event']} [{i['label']}] {i['kind']}", i["text"], limit)
    if not main:
        lines.append("- なし")
    if agents or sub_ctx:
        lines += ["", "#### サブエージェント（親の呼び出しと分けて出す）"]
        full += ["", "# サブエージェント"]
    for a in agents:
        spec = a["input"] if isinstance(a["input"], dict) else {}
        add(
            f"Agent 呼び出し {a['id']}（subagent_type: {spec.get('subagent_type', '?')}）"
            "に親が渡した prompt",
            str(spec.get("prompt") or ""),
            EVIDENCE_LIMIT,
        )
        kids = [c for c in calls if c.get("parent") == a["id"]]
        lines.append(f"- この Agent 呼び出しの中のツール呼び出し: {len(kids)} 件")
        lines += [
            f"  - {c['name']}: {clip(json.dumps(c['input'], ensure_ascii=False), 500, where)}"
            for c in kids
        ]
        full += ["", f"## Agent 呼び出し {a['id']} の中のツール呼び出し（{len(kids)} 件）"]
        full += [f"- {c['name']}: {json.dumps(c['input'], ensure_ascii=False)}" for c in kids]
    for i in sub_ctx:
        add(
            f"SubagentStart [{i['label']}] 案内（どの Agent 呼び出しのものかは取れない）",
            i["text"],
            CONTEXT_LIMIT,
        )
    if agents or sub_ctx:
        lines.append(
            "- 取れない: サブエージェントが受け取った SessionStart の文脈・スキル・CLAUDE.md。"
            "stream-json に出るのは上の分だけで、渡っていないものは推測しない"
        )
    (out / where).write_text("\n".join(full) + "\n", encoding="utf-8")
    return lines


def _step_label(row, step_sessions):
    steps = sorted(n for n, sids in step_sessions.items() if row.get("session") in sids)
    return ",".join(str(n) for n in steps) or "?"


def repeated_denials(rows, step_sessions):
    """同じ step で、同じ code の deny が、間に allow を挟まず REPEAT_MIN 回以上続いた箇所。
    間にある deny 以外の行（skip・handover など）は数えず、途切れにもしない。"""
    found, cur = [], {}

    def flush(step):
        run = cur.pop(step, None)
        if run and run["count"] >= REPEAT_MIN:
            found.append(run)

    for r in rows:
        step = _step_label(r, step_sessions)
        decision = r.get("decision")
        if decision == "allow":
            flush(step)
        elif decision == "deny":
            code = r.get("code") or ""
            run = cur.get(step)
            if run and run["code"] == code:
                run["count"] += 1
                run["subjects"].append(r.get("subject") or "")
            else:
                flush(step)
                cur[step] = {
                    "step": step,
                    "code": code,
                    "count": 1,
                    "subjects": [r.get("subject") or ""],
                }
    for step in list(cur):
        flush(step)
    return found


def _write_path(call):
    spec = call["input"] if isinstance(call["input"], dict) else {}
    path = spec.get("file_path")
    return path if call["name"] in WRITE_TOOLS and isinstance(path, str) and path else None


def bypass_suspects(rows, streams, step_sessions):
    """Edit / Write が deny されたあと、同じ step で同じパス（pathcase.fold で比べる）への
    Edit / Write がまだ打たれている箇所。deny の数より呼び出しの数が多いものを疑う。
    Bash の中身からはパスを取らない。切れた subject は比べない。"""
    denied = {}
    for r in rows:
        subject = r.get("subject") or ""
        if (
            r.get("decision") == "deny"
            and r.get("event") == "PreToolUse"
            and r.get("tool") in WRITE_TOOLS
            and subject
            and CUT_MARK not in subject
        ):
            key = (_step_label(r, step_sessions), pathcase.fold(subject))
            denied.setdefault(key, []).append(subject)
    found = []
    for (step, folded), subjects in denied.items():
        paths = []
        for n in (int(x) for x in step.split(",") if x.isdigit()):
            paths += [p for p in map(_write_path, streams.get(n, {}).get("tool_calls", [])) if p]
        later = [p for p in paths if pathcase.fold(p) == folded]
        if len(later) > len(subjects):
            found.append(
                {"step": step, "path": subjects[0], "denied": len(subjects), "calls": len(later)}
            )
    return found


# 次の手を示す語。hook の拒否文は、次の手をマーカーのない行頭の文で書くことが多いので、
# マーカーの行（GUIDE_HEADS）に加えて、依頼・指示の言い回しも見る（#570）。
# 「使う」「確かめ」のように、禁止の説明にも出る語は入れない。
GUIDE_PHRASES = (
    "ください",
    "頼んで",
    "依頼",
    "使って",
    "使います",
    "従って",
    "してから",
    "書き直",
    "打ち直",
    "伝えて",
    "報告して",
)

EMPTY_REASON = "(理由文が空)"

GUIDE_COMMAND = re.compile(r"`[^`\n]+`")  # 打つ形をコード片で示す文（取り込みの案内など）


def has_guide(text):
    """理由文が次の手を示しているか。次のどれかなら True。
    - 行頭（前の空白を除く）が `次:`・`案内:`・`必須:`
    - 依頼・指示の語（GUIDE_PHRASES）がある
    - 打つ形を、バッククォートで囲んだコード片で示している
    語の有無で見るので、誤検知と見逃しがある（限界は acceptance-scenarios.md）。"""
    if any(line.strip().startswith(GUIDE_HEADS) for line in text.splitlines()):
        return True
    return any(p in text for p in GUIDE_PHRASES) or bool(GUIDE_COMMAND.search(text))


def _empty_denials(hook_events):
    """理由文が空の拒否（deny・block）の hook 名。hook_items は文面があるものだけなので別に拾う。"""
    labels = []
    for ev in hook_events:
        if ev.get("subtype") != "hook_response":
            continue
        data = _hook_payload(ev)
        spec = data.get("hookSpecificOutput")
        spec = spec if isinstance(spec, dict) else {}
        decision = spec.get("permissionDecision") or data.get("decision")
        reason = spec.get("permissionDecisionReason") or data.get("reason")
        if decision in ("deny", "block") and not (reason and str(reason).strip()):
            labels.append(ev.get("hook_name") or _hook_name(ev))
    return labels


def unguided_denials(streams):
    """次の手を示していない拒否の理由文（has_guide が偽、または理由文が空）。
    (step 番号, hook 名, 理由文) の列。理由文が空のものは EMPTY_REASON を入れる。"""
    found = []
    for num, parsed in streams.items():
        for i in hook_items(parsed["hook_events"]):
            if i["kind"] == "拒否" and not has_guide(i["text"]):
                found.append((num, i["label"], i["text"]))
        found += [(num, label, EMPTY_REASON) for label in _empty_denials(parsed["hook_events"])]
    return found


def normalize_message(text):
    """文面を、パスと数字の違いを除いた形にする。置き換えの順は、コミットの ID → パス → 数字
    （数字を先にすると ID が壊れる）。"""
    text = re.sub(r"\b[0-9a-f]{7,40}\b", "<SHA>", text)
    path = r"(?:[A-Za-z0-9_.\-~:@%+=/\\]|<SHA>)*[/\\](?:[A-Za-z0-9_.\-~:@%+=/\\]|<SHA>)*"
    text = re.sub(path, "<PATH>", text)
    return re.sub(r"\d+", "<N>", text)


def _sources(items, rows):
    """hook の応答に出どころ（code・rules）を付ける。応答には無いので、decisions.jsonl の行と
    順に突き合わせる。数が合わなければ付けない。items は (step, item) の列。"""

    def pre(i):
        return i["event"] == "PreToolUse"

    row_sets = {
        "reason": [
            r
            for r in rows
            if r.get("event") == "PreToolUse"
            and r.get("decision") in ("deny", "ask")
            and r.get("enforced") is not False
        ],
        "hint": [r for r in rows if r.get("event") == "PreToolUse" and r.get("hinted")],
    }
    item_sets = {
        "reason": [x for x in items if x[1]["kind"] != "案内" and pre(x[1])],
        "hint": [x for x in items if x[1]["kind"] == "案内" and pre(x[1])],
    }
    found = {}
    for key, its in item_sets.items():
        if len(its) != len(row_sets[key]):
            continue
        for (_, item), row in zip(its, row_sets[key], strict=True):
            rules = row.get("rules") or []
            rules = ",".join(map(str, rules)) if isinstance(rules, list) else str(rules)
            found[id(item)] = f"{row.get('code') or '-'} / {rules or '-'}"
    return found


def collect_messages(streams, rows):
    """hook の応答（拒否・確認・案内）の文面を、正規化した文面で重複を除いて集める。
    案内は SessionStart のものを除く（CCNAVI.md の全文なので、文脈の節で見る）。"""
    items = []
    for num, parsed in streams.items():
        for i in hook_items(parsed["hook_events"]):
            if i["kind"] != "案内" or i["event"] != "SessionStart":
                items.append((num, i))
    sources = _sources(items, rows)
    merged = {}
    for num, i in items:
        key = (i["kind"], normalize_message(i["text"]))
        m = merged.setdefault(
            key,
            {
                "kind": i["kind"],
                "text": i["text"],
                "count": 0,
                "events": [],
                "steps": [],
                "src": [],
            },
        )
        m["count"] += 1
        for field, value in (("events", i["event"]), ("steps", num), ("src", sources.get(id(i)))):
            if value is not None and value not in m[field]:
                m[field].append(value)
    return list(merged.values())
