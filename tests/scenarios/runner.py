"""受入シナリオを claude -p で回す実行器。手順と決まりは docs/claude/acceptance-scenarios.md。

普段のテストには含めない。人間に確認を取ってから、明確な指示があったときだけ回す。

    uv run python -m tests.scenarios.runner run <シナリオ>
    uv run python -m tests.scenarios.runner run <シナリオ> --confirmed
    uv run python -m tests.scenarios.runner next <出力の置き場>

next は、人間の操作で止まったあと、人間が「次へ」と言ってから打つ。

--confirmed を付けるのは、人間が「状態がきれいで最新か」「test-repo にブランチや MR が
できてよいか」に答えたあとだけ。付けずに打つと、確認に使う事実を出して止まる（終了コード 3）。

終了コード: 0 全部通った / 1 不合格がある / 2 未成立か意味の判定待ちがある /
3 人間の確認が要る / 10 人間の操作で止まった（「次へ」のあと next で続ける）。

判定は 3 つ。機械で比べられるもの（ccnavi の判定の記録、ファイルの最終状態、
コマンドの出力）は pass / fail。期待した呼び出しをエージェントが打たなかったものは
inconclusive（未成立。fail にしない）。意味で読むもの（judge）は、evidence.md に証拠を
抜き出し、別のエージェントが基準に照らして読む。
"""

import argparse
import hashlib
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
GIT_SH = "{root}/.ccnavi/scripts/ccnavi-git.sh"
STEP_TIMEOUT_SEC = 900

PASS, FAIL, INCONCLUSIVE, REVIEW = "pass", "fail", "inconclusive", "needs-review"

# claude を呼ぶ道は、`python -m tests.scenarios.runner` で起動されたときだけ開く。
# ほかのモジュール（全量テストに入るテストを含む）が import して呼んでも、ここで止まる。
# 全量テストに入らないのは「test_ で始まるモジュールが無いから」だけでなく、この門でも守る。
_ENTRY = False


def _require_entry():
    if not _ENTRY:
        raise RuntimeError(
            "受入シナリオは `python -m tests.scenarios.runner` から起動したときだけ回せる"
            "（テストや他のモジュールから呼ばない）"
        )


# ---- シナリオの読み込み ----


def load_scenario(path):
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    for key in ("id", "steps"):
        if key not in data:
            raise ValueError(f"{path}: {key} がありません")
    data.setdefault("model", "haiku")
    data.setdefault("budget_usd", 1.0)
    data.setdefault("permission_mode", "auto")
    data.setdefault("expect", {})
    return data


# ---- claude -p の出力（stream-json）を読む ----


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
                    out["tool_calls"].append({"name": b.get("name"), "input": b.get("input")})
        elif kind == "user":
            for b in _blocks(ev.get("message")):
                if b.get("type") == "tool_result":
                    out["tool_results"].append(
                        {"is_error": bool(b.get("is_error")), "content": b.get("content")}
                    )
        elif kind == "result":
            out["final_text"] = ev.get("result") or ""
            out["cost_usd"] += float(ev.get("total_cost_usd") or 0)
        elif "hook" in str(ev.get("subtype", "")) or "hook" in str(kind):
            out["hook_events"].append(ev)
    return out


# ---- ccnavi の判定の記録（logs/decisions.jsonl）を比べる ----


def read_decisions(log_path, session_ids):
    """判定の記録から、回したセッションの行だけを取り出す（他のセッションの行は混ざる）。"""
    rows = []
    sessions = set(session_ids)
    p = Path(log_path)
    if not p.exists():
        return rows
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("session") in sessions:
                rows.append(rec)
    return rows


def _selected(rec, cond):
    """どの呼び出しの話かを決める欄（event・tool・subject_contains）。"""
    if "event" in cond and rec.get("event") != cond["event"]:
        return False
    if "tool" in cond and rec.get("tool") != cond["tool"]:
        return False
    return not (
        "subject_contains" in cond and cond["subject_contains"] not in str(rec.get("subject", ""))
    )


def _satisfies(rec, cond):
    """その呼び出しがどう判定されたかを決める欄（decision・code・rules_include・decision_not）。"""
    if "decision" in cond and rec.get("decision") != cond["decision"]:
        return False
    if "decision_not" in cond and rec.get("decision") == cond["decision_not"]:
        return False
    if "code" in cond and rec.get("code") != cond["code"]:
        return False
    rules = rec.get("rules") or []
    return all(r in rules for r in cond.get("rules_include", []))


def check_decision(rows, cond):
    """期待する判定が出ているか。呼び出し自体が無ければ inconclusive（fail にしない）。"""
    picked = [r for r in rows if _selected(r, cond)]
    if not picked:
        return INCONCLUSIVE, "該当する呼び出しが記録に無い（エージェントが打たなかった）"
    n = sum(1 for r in picked if _satisfies(r, cond))
    need = cond.get("min_count", 1)
    if n >= need:
        return PASS, f"{n} 件が一致"
    seen = sorted({(r.get("decision"), r.get("code")) for r in picked}, key=str)
    return FAIL, f"一致 {n} 件（{need} 件が要る）。記録にあった判定: {seen}"


def check_decision_none(rows, cond):
    """出ていてはいけない判定が出ていないか。"""
    hit = [
        r
        for r in rows
        if _selected(r, cond)
        and _satisfies(
            r, {k: v for k, v in cond.items() if k not in ("event", "tool", "subject_contains")}
        )
    ]
    if hit:
        return FAIL, f"出ていた: {hit[0].get('subject', '')[:80]}"
    return PASS, "出ていない"


# ---- 最終状態 ----


def file_digest(path):
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def check_state(item, root):
    cwd = Path(root) / item["cwd"] if item.get("cwd") else Path(root)
    r = subprocess.run(
        item["run"], shell=True, cwd=cwd, capture_output=True, text=True, timeout=120
    )
    out = (r.stdout or "") + (r.stderr or "")
    if "expect_contains" in item and item["expect_contains"] not in out:
        return FAIL, f"出力に「{item['expect_contains']}」が無い: {out.strip()[:120]}"
    if "expect_not_contains" in item and item["expect_not_contains"] in out:
        return FAIL, f"出力に「{item['expect_not_contains']}」がある"
    return PASS, "一致"


# ---- 期待をまとめて評価する ----


def evaluate(expect, rows, raw_text, root, pre_digests):
    """期待ごとの結果の一覧 [(種類, 内容, 判定, 理由)] を返す。"""
    results = []
    for cond in expect.get("decisions", []):
        v, why = check_decision(rows, cond)
        results.append(("decisions", cond, v, why))
    for cond in expect.get("decisions_none", []):
        v, why = check_decision_none(rows, cond)
        results.append(("decisions_none", cond, v, why))
    for rel in expect.get("files_unchanged", []):
        now = file_digest(Path(root) / rel)
        v, why = (
            (PASS, "変わっていない")
            if now == pre_digests.get(rel)
            else (FAIL, "回す前と変わっている")
        )
        results.append(("files_unchanged", rel, v, why))
    for item in expect.get("state", []):
        v, why = check_state(item, root)
        results.append(("state", item, v, why))
    for s in expect.get("transcript_forbidden", []):
        v, why = (FAIL, "出力に出ている") if s in raw_text else (PASS, "出ていない")
        results.append(("transcript_forbidden", s, v, why))
    for criterion in expect.get("judge", []):
        results.append(("judge", criterion, REVIEW, "証拠を読んで判定する"))
    return results


def overall(results):
    kinds = [r[2] for r in results]
    for v in (FAIL, INCONCLUSIVE, REVIEW):
        if v in kinds:
            return v
    return PASS


# ---- 人間への確認に使う事実 ----


def preflight_facts(root):
    git = GIT_SH.format(root=root)
    lines = []
    for label, cwd in (
        ("ワークスペースルート", root),
        ("projects/test-repo", Path(root) / "projects/test-repo"),
    ):
        if not Path(cwd).is_dir():
            lines.append(f"## {label}: 置き場が無い（{cwd}）\n")
            continue
        for sub in ("status", "worktree list", "branch"):
            r = subprocess.run(
                f"sh {git} {sub}", shell=True, cwd=cwd, capture_output=True, text=True, timeout=120
            )
            lines.append(f"## {label}: git {sub}\n{(r.stdout or r.stderr).strip()}\n")
    return "\n".join(lines)


# ---- 実行 ----


def run_step(scn, step, out, idx, session_id, resume_id, persist, root):
    _require_entry()
    cmd = [
        "claude",
        "-p",
        step["prompt"].strip(),
        "--model",
        scn["model"],
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-hook-events",
        "--max-budget-usd",
        str(round(scn["budget_usd"] / max(1, scn["_prompt_steps"]), 2)),
        "--permission-mode",
        scn["permission_mode"],
    ]
    if resume_id:
        cmd += ["--resume", resume_id]
    else:
        cmd += ["--session-id", session_id]
    if not persist:
        cmd += ["--no-session-persistence"]
    r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=STEP_TIMEOUT_SEC)
    (out / f"step-{idx}.jsonl").write_text(r.stdout, encoding="utf-8")
    if r.stderr.strip():
        (out / f"step-{idx}.stderr").write_text(r.stderr, encoding="utf-8")
    return parse_stream(r.stdout)


def _persist_needed(steps, idx):
    """続きの step が --resume でつなぐなら、セッションを残す。"""
    for nxt in steps[idx + 1 :]:
        if nxt.get("human"):
            continue
        return bool(nxt.get("resume"))
    return False


def _write_state(out, st):
    (out / "state.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def _setup(scn, root):
    made = []
    for rel, body in (scn.get("setup") or {}).get("files", {}).items():
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        made.append(rel)
    return made


def _cleanup(made, root):
    for rel in made:
        p = Path(root) / rel
        if p.is_file():
            p.unlink()
    for rel in made:  # 空になった親も消す（setup で作ったものだけ）
        d = (Path(root) / rel).parent
        try:
            while d != Path(root) and not any(d.iterdir()):
                d.rmdir()
                d = d.parent
        except OSError:
            pass


def drive(scn, out, st, root):
    """st['next_step'] から、人間の操作の step か末尾まで進める。
    人間の操作で止まったら 10、末尾まで進んだら None を返す。"""
    steps = scn["steps"]
    while st["next_step"] < len(steps):
        idx = st["next_step"]
        step = steps[idx]
        if step.get("human"):
            print(f"\n== 人間の操作（step {idx + 1}）==")
            print(step.get("instruction", "").strip())
            for c in step.get("check", []):
                print(f"  確認: {c}")
            print(
                "\n済んだら人間が「次へ」と言う。続きは: python -m tests.scenarios.runner next", out
            )
            st["next_step"] = idx + 1
            _write_state(out, st)
            return 10
        resume_id = st["session_ids"][-1] if step.get("resume") and st["session_ids"] else None
        sid = str(uuid.uuid4())
        t0 = time.time()
        ev = run_step(scn, step, out, idx + 1, sid, resume_id, _persist_needed(steps, idx), root)
        print(f"step {idx + 1}: {time.time() - t0:.0f} 秒、費用 {ev['cost_usd']:.3f} USD")
        for s in ev["session_ids"] or [sid]:
            if s not in st["session_ids"]:
                st["session_ids"].append(s)
        st["cost_usd"] += ev["cost_usd"]
        st["next_step"] = idx + 1
        _write_state(out, st)
    return None


def finish(scn, out, st, root):
    _cleanup(st["setup_made"], root)
    raw = "".join(p.read_text(encoding="utf-8") for p in sorted(out.glob("step-*.jsonl")))
    rows = read_decisions(Path(root) / "logs/decisions.jsonl", st["session_ids"])
    results = evaluate(scn["expect"], rows, raw, root, st["pre_digests"])
    write_report(scn, out, st, results, rows, raw)
    final = overall(results)
    print(f"\n結果: {final}（レポート: {out}/report.md）")
    return {PASS: 0, FAIL: 1, INCONCLUSIVE: 2, REVIEW: 2}[final]


EVIDENCE_LIMIT = 1000
CONTEXT_LIMIT = 2000


def _clip(text, limit, where):
    """limit を超えたら切り、切ったことと全量の在りかを末尾に書く。"""
    text = str(text)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…（全 {len(text)} 文字のうち先頭 {limit} 文字。全量は {where}）"


def _text_of(content):
    """ツール結果の content（文字列かブロックの列）を 1 つの文字列にする。"""
    if isinstance(content, list):
        parts = [b.get("text", "") if isinstance(b, dict) else str(b) for b in content]
        return "\n".join(p for p in parts if p)
    return str(content)


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
    案内文（additionalContext。SessionStart のものを除く）、中身のある Stop の応答。
    hook_started / hook_progress は数えない。SessionStart と出力が空の応答は件数だけにする。"""
    lines, omitted = [], {}
    for ev in hook_events:
        if ev.get("subtype") != "hook_response":
            continue
        name = ev.get("hook_event") or ev.get("hook_event_name") or "?"
        data = _hook_payload(ev)
        spec = data.get("hookSpecificOutput")
        spec = spec if isinstance(spec, dict) else {}
        decision = spec.get("permissionDecision") or data.get("decision")
        reason = spec.get("permissionDecisionReason") or data.get("reason")
        context = spec.get("additionalContext")
        label = f"{name} {ev.get('hook_name', '')}".strip()
        if decision in ("deny", "ask", "block"):
            # 拒否の文面は切らない
            lines.append(f"- [{label}] {decision}: {reason or ''}")
        elif name == "Stop" and (data or ev.get("stdout") or ev.get("stderr")):
            body = (
                json.dumps(data, ensure_ascii=False)
                if data
                else ev.get("stdout") or ev.get("stderr")
            )
            lines.append(f"- [{label}] {_clip(body, EVIDENCE_LIMIT, where)}")
        elif context and name != "SessionStart":
            lines.append(f"- [{label}] 案内: {_clip(context, CONTEXT_LIMIT, where)}")
        else:
            omitted[name] = omitted.get(name, 0) + 1
    return lines, omitted


def _results_table(results):
    lines = ["| 種類 | 期待 | 判定 | 理由 |", "|---|---|---|---|"]
    for kind, cond, v, why in results:
        c = json.dumps(cond, ensure_ascii=False) if not isinstance(cond, str) else cond
        lines.append(f"| {kind} | {c[:100]} | {v} | {why[:100]} |")
    return lines


def write_report(scn, out, st, results, rows, raw):
    lines = [
        f"# {scn['id']} {scn['title']}",
        "",
        f"全体: {overall(results)}  費用: {st['cost_usd']:.3f} USD",
        "",
    ]
    lines += _results_table(results)
    if scn.get("unconfirmed"):
        lines += ["", "## 確かめる点（unconfirmed）"] + [f"- {u}" for u in scn["unconfirmed"]]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "report.json").write_text(
        json.dumps(
            [{"kind": k, "expect": c, "verdict": v, "why": w} for k, c, v, w in results],
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    ev = [
        "# 証拠（意味の判定用）",
        "",
        "## 機械判定の結果",
        "",
        f"全体: {overall(results)}",
        "",
        *_results_table(results),
        "",
        "judge の基準:",
        *[f"- {c}" for c in scn["expect"].get("judge", [])],
    ]
    for p in sorted(out.glob("step-*.jsonl")):
        parsed = parse_stream(p.read_text(encoding="utf-8"))
        ev += ["", f"## {p.stem}", "### ツール呼び出し"]
        ev += [
            f"- {t['name']}: {_clip(json.dumps(t['input'], ensure_ascii=False), 500, p.name)}"
            for t in parsed["tool_calls"]
        ]
        ev += ["### ツール結果"]
        ev += [
            f"- {'エラー' if r['is_error'] else '成功'}: "
            f"{_clip(_text_of(r['content']), EVIDENCE_LIMIT, p.name)}"
            for r in parsed["tool_results"]
        ]
        hook_lines, omitted = summarize_hooks(parsed["hook_events"], p.name)
        ev += ["### hook の応答（拒否・確認・案内文・Stop だけ）", *hook_lines]
        if omitted:
            counts = "、".join(f"{k} {v} 件" for k, v in sorted(omitted.items()))
            ev.append(f"- 省いた応答: {counts}（全量は {p.name}）")
        ev += ["### 最後の報告", _clip(parsed["final_text"], 3000, p.name)]
    ev += ["", "## ccnavi の判定の記録（このセッションの行）"]
    for r in rows:
        head = " ".join(str(r.get(k, "")) for k in ("event", "tool", "decision", "code", "rules"))
        ev.append(f"- {head} {str(r.get('subject', ''))[:100]}")
    (out / "evidence.md").write_text("\n".join(ev) + "\n", encoding="utf-8")


def cmd_run(args):
    _require_entry()
    root = Path(args.root).resolve()
    scn = load_scenario(args.scenario)
    scn["_prompt_steps"] = sum(1 for s in scn["steps"] if not s.get("human"))
    if not args.confirmed:
        print("回す前に、人間に次を確認する。確認が取れたら --confirmed を付けて打ち直す。\n")
        print(preflight_facts(root))
        print(
            "問い 1: 今の状態はきれいで、最新か（他のセッションの作業中や未コミットの変更が無いか）"
        )
        print("問い 2: projects/test-repo にブランチや MR ができてよいか")
        print("問い 3: ccnavi 本体にブランチ・コミット・MR が残らない前提でよいか")
        return 3
    out = Path(
        args.out or ROOT / "logs" / "scenarios" / f"{scn['id']}-{datetime.now():%Y%m%d-%H%M%S}"
    )
    out.mkdir(parents=True, exist_ok=True)
    pre = {rel: file_digest(root / rel) for rel in scn["expect"].get("files_unchanged", [])}
    st = {
        "scenario": str(Path(args.scenario).resolve()),
        "root": str(root),
        "next_step": 0,
        "session_ids": [],
        "cost_usd": 0.0,
        "pre_digests": pre,
        "setup_made": _setup(scn, root),
    }
    _write_state(out, st)
    return _continue(scn, out, st, root)


def _continue(scn, out, st, root):
    rc = drive(scn, out, st, Path(root))
    return rc if rc is not None else finish(scn, out, st, Path(root))


def cmd_next(args):
    out = Path(args.out)
    st = json.loads((out / "state.json").read_text(encoding="utf-8"))
    scn = load_scenario(st["scenario"])
    scn["_prompt_steps"] = sum(1 for s in scn["steps"] if not s.get("human"))
    return _continue(scn, out, st, st["root"])


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("scenario")
    r.add_argument("--confirmed", action="store_true")
    r.add_argument("--root", default=str(ROOT))
    r.add_argument("--out")
    r.set_defaults(fn=cmd_run)
    n = sub.add_parser("next")
    n.add_argument("out")
    n.set_defaults(fn=cmd_next)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    _ENTRY = True
    sys.exit(main())
