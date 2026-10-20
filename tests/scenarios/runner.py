"""受入シナリオを claude -p で回す実行器。手順と決まりは docs/claude/acceptance-scenarios.md。

普段のテストには含めない。人間に確認を取ってから、明確な指示があったときだけ回す。

    uv run python -m tests.scenarios.runner run <シナリオ>
    uv run python -m tests.scenarios.runner run <シナリオ> --confirmed
    uv run python -m tests.scenarios.runner next <出力の置き場>

next は、人間の操作で止まったあと、人間が「次へ」と言ってから打つ。

--confirmed を付けるのは、人間が「状態がきれいで最新か」（と、test_repo のシナリオでは
「test-repo にブランチや MR ができてよいか」）に答えたあとだけ。
付けずに打つと、確認に使う事実を出して止まる（終了コード 3）。
ccnavi 本体にブランチ・コミット・ワークツリーが残らないことは、
回す前後の状態を比べて確かめる（repo）。

終了コード: 0 全部通った / 1 不合格がある / 2 未成立か意味の判定待ちがある /
3 人間の確認が要る / 10 人間の操作で止まった（「次へ」のあと next で続ける）。

判定は 3 つ。機械で比べられるもの（ccnavi の判定の記録、ファイルの最終状態、
コマンドの出力）は pass / fail。期待した呼び出しをエージェントが打たなかったものは
inconclusive（未成立。fail にしない）。意味で読むもの（judge）は、evidence.md に証拠を
抜き出し、別のエージェントが基準に照らして読む。
"""

import argparse
import contextlib
import hashlib
import json
import os
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
    for n, step in enumerate(data["steps"], 1):
        after = step.get("expect_after")
        if after is None:
            continue
        if step.get("human"):
            raise ValueError(
                f"{path}: step {n} 人間の操作の step には expect_after を付けられません"
            )
        extra = set(after) - {"decisions", "state"} if isinstance(after, dict) else None
        if extra is None:
            raise ValueError(
                f"{path}: step {n} expect_after は decisions と state を持つ dict です"
            )
        if extra:
            raise ValueError(
                f"{path}: step {n} expect_after に書けるのは decisions と state だけです: "
                + ", ".join(sorted(extra))
            )
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
    """その呼び出しがどう判定されたかを決める欄（decision・code・rules_include・decision_not・hinted）。"""
    if "decision" in cond and rec.get("decision") != cond["decision"]:
        return False
    if "decision_not" in cond and rec.get("decision") == cond["decision_not"]:
        return False
    if "code" in cond and rec.get("code") != cond["code"]:
        return False
    rules = rec.get("rules") or []
    # hinted は、その呼び出しで once の案内を渡した行にだけ付く。欄が無い行は false 扱い。
    if "hinted" in cond and bool(rec.get("hinted")) != bool(cond["hinted"]):
        return False
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
        return FAIL, f"出ていた: {_clip(hit[0].get('subject', ''), 200, 'logs/decisions.jsonl')}"
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
        tail = _clip(out.strip(), 300, "state の run を手で打ち直して確かめる")
        return FAIL, f"出力に「{item['expect_contains']}」が無い: {tail}"
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


def evaluate_after(step_expect, st, root):
    """step の expect_after を評価する。判定の行は、ここまでに回した全 step のセッションから探す。
    結果は evaluate と同じ形の [(種類, 内容, 判定, 理由)]。"""
    rows = read_decisions(Path(root) / "logs/decisions.jsonl", st["session_ids"])
    results = []
    for cond in step_expect.get("decisions", []):
        v, why = check_decision(rows, cond)
        results.append(("decisions", cond, v, why))
    for item in step_expect.get("state", []):
        v, why = check_state(item, root)
        results.append(("state", item, v, why))
    return results


def overall(results):
    kinds = [r[2] for r in results]
    for v in (FAIL, INCONCLUSIVE, REVIEW):
        if v in kinds:
            return v
    return PASS


# ---- ccnavi 本体の変化を確かめる（回す前と後で比べる） ----

REPO_CHANGED = "回す前と変わっている（HEAD・ブランチ・ワークツリーのどれか）"


def repo_snapshot(root):
    """ccnavi 本体の HEAD・ブランチの一覧・ワークツリーの一覧。回す前と後で比べる。"""
    parts = []
    for args in (
        ["rev-parse", "HEAD"],
        ["for-each-ref", "--format=%(refname)", "refs/heads"],
        ["worktree", "list", "--porcelain"],
    ):
        r = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=120
        )
        parts.append(r.stdout if r.returncode == 0 else f"エラー {r.returncode}: {r.stderr}")
    return "\n".join(parts)


def repo_result(pre, now):
    """回す前と後の ccnavi 本体の状態を比べる。(判定, 理由)。
    pre が無いとき（この決まりより前に作られた状態ファイル）は、確かめられないので未成立にする。"""
    if pre is None:
        return INCONCLUSIVE, "回す前の状態が無い（この実行より前に作られた状態ファイル）"
    if pre == now:
        return PASS, "変わっていない"
    return FAIL, REPO_CHANGED


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


MODE_ENV = "CCNAVI_MODE"


def mode_check(root):
    """判定のモードが enable かを確かめる。(enable か, 人間に見せる文)。

    dry-run では、hook は拒否を報告するだけで止めない（enforced が false の行になる）。
    シナリオは「止まる」ことを確かめるので、enable 以外では回さない。値は、プロジェクトの
    .claude/settings.json の env と、実行の環境変数を見る。どちらかに enable 以外の値が
    あれば止める。値が無ければ enable（hook の既定と同じ）。
    """
    settings_path = Path(root) / ".claude/settings.json"
    try:
        declared = json.loads(settings_path.read_text(encoding="utf-8")).get("env", {})
    except FileNotFoundError:
        declared = {}
    except (OSError, ValueError) as exc:
        return (
            False,
            f"## 判定のモード: {settings_path} を読めない（{exc}）。確かめられないので回さない",
        )
    sources = [
        (".claude/settings.json の env", declared.get(MODE_ENV)),
        ("実行の環境変数", os.environ.get(MODE_ENV)),
    ]
    lines = ["## 判定のモード（CCNAVI_MODE）"]
    ok = True
    for label, value in sources:
        if value is None:
            lines.append(f"- {label}: 指定なし")
            continue
        lines.append(f"- {label}: {value}")
        if value.strip().lower() not in ("", "enable"):
            ok = False
    if ok:
        lines.append("enable で判定している。回してよい")
    else:
        lines.append(
            "enable ではない。dry-run などでは止まらない判定が混じるので回さない。"
            "モードを enable にしてから打ち直す（設定の変更はユーザが行う）"
        )
    return ok, "\n".join(lines)


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
    if r.returncode != 0:
        # 失敗した step の出力は、ここでは判定に使わない（drive が止める）
        raise RuntimeError(f"claude が終了コード {r.returncode} で終わった")
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


def setup_clashes(scn, root):
    """setup のファイルのうち、もう有るもの。有れば回さない（後片付けで消してしまうため）。"""
    return [rel for rel in (scn.get("setup") or {}).get("files", {}) if (Path(root) / rel).exists()]


def _setup(scn, root):
    """setup のファイルを作る。(作ったファイル, 作ったディレクトリ) を返す。
    既にあるパスには触らない（setup_clashes で先に確かめてある）。
    ディレクトリは、この関数が新しく作ったものだけ。"""
    files, dirs = [], []
    for rel, body in (scn.get("setup") or {}).get("files", {}).items():
        p = Path(root) / rel
        missing = []
        d = p.parent
        while d != Path(root) and not d.exists():
            missing.append(d)
            d = d.parent
        for d in reversed(missing):
            d.mkdir()
            dirs.append(str(d.relative_to(root)))
        p.write_text(body, encoding="utf-8")
        files.append(rel)
    return files, dirs


def _cleanup(made, made_dirs, root):
    for rel in made:
        p = Path(root) / rel
        if p.is_file():
            p.unlink()
    for rel in sorted(made_dirs, key=len, reverse=True):  # 深いものから。空のときだけ消す
        with contextlib.suppress(OSError):
            (Path(root) / rel).rmdir()


def drive(scn, out, st, root):
    """st['next_step'] から、人間の操作の step か末尾まで進める。
    人間の操作で止まったら 10、末尾まで進んだら None を返す。
    expect_after を満たさなければ 11 を返す。"""
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
        persist = _persist_needed(steps, idx)
        try:
            ev = run_step(scn, step, out, idx + 1, sid, resume_id, persist, root)
        except Exception as e:  # 起動できない・時間切れ・終了コードが 0 でない
            # 後続は回さず止める。finish に回して、後片付けを通す
            st["halt"] = {
                "step": idx + 1,
                "kind": "run",
                "reasons": [f"step {idx + 1} が正常に終わらなかった: {e}"],
            }
            _write_state(out, st)
            print(f"step {idx + 1}: 正常に終わらなかった（{e}）。後続の step には進まず止める")
            return 11
        print(f"step {idx + 1}: {time.time() - t0:.0f} 秒、費用 {ev['cost_usd']:.3f} USD")
        for s in ev["session_ids"] or [sid]:
            if s not in st["session_ids"]:
                st["session_ids"].append(s)
        st["cost_usd"] += ev["cost_usd"]
        st["next_step"] = idx + 1
        _write_state(out, st)
        after = step.get("expect_after")
        if after:
            try:
                bad = [r for r in evaluate_after(after, st, root) if r[2] != PASS]
            except Exception as e:  # check_state の timeout・cwd 不在など。止めて finish に回す
                bad = [("expect_after", after, INCONCLUSIVE, f"評価で例外: {e!r}")]
            if bad:
                st["halt"] = {
                    "step": idx + 1,
                    "reasons": [
                        f"{r[0]} {json.dumps(r[1], ensure_ascii=False)}: {r[3]}" for r in bad
                    ],
                }
                _write_state(out, st)
                print(f"step {idx + 1}: expect_after を満たさない。後続の step には進まず止める")
                return 11
    return None


def finish(scn, out, st, root):
    try:
        # state の run は setup のファイルが残っているうちに回す。消すのは判定のあと
        raw = "".join(p.read_text(encoding="utf-8") for p in sorted(out.glob("step-*.jsonl")))
        rows = read_decisions(Path(root) / "logs/decisions.jsonl", st["session_ids"])
        results = evaluate(scn["expect"], rows, raw, root, st["pre_digests"])
        halt = st.get("halt")
        if halt:
            # 止まった回は後続の step が回っていないので、decisions・state は必ず落ちる。外す
            results = [r for r in results if r[0] not in ("decisions", "state")]
            kind = halt.get("kind", "expect_after")
            results.append((kind, {"step": halt["step"]}, INCONCLUSIVE, "; ".join(halt["reasons"])))
        # ccnavi 本体に、ブランチ・コミット・ワークツリーが残っていないかを実際の状態で確かめる
        v, why = repo_result(st.get("pre_repo"), repo_snapshot(root))
        results.append(("repo", "ccnavi 本体の HEAD・ブランチ・ワークツリー", v, why))
    finally:
        _cleanup(st["setup_made"], st.get("setup_dirs", []), root)
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
            lines.append(f"- [{label}] {_clip(body, EVIDENCE_LIMIT, where)}")
        elif context and name != "SessionStart":
            lines.append(f"- [{label}] 案内: {_clip(context, CONTEXT_LIMIT, where)}")
        elif data.get("systemMessage") and name != "SessionStart":
            lines.append(f"- [{label}] 通知: {_clip(data['systemMessage'], CONTEXT_LIMIT, where)}")
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


def _duplicates_hook(text, denial_texts):
    """エラーの文面が、hook の拒否理由と（前後の空白を除いて）まったく同じなら True。"""
    text = text.strip()
    if not text:
        return False
    return text in denial_texts


def _step_decisions_table(rows, step_sessions):
    """判定の記録を、見出し付きの表にする。step_sessions は {step 番号: [session id]}。"""
    lines = [
        "| step | event | tool | decision | code | rules | subject |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        steps = sorted(n for n, sids in step_sessions.items() if r.get("session") in sids)
        step = ",".join(str(n) for n in steps) or "?"
        rules = r.get("rules", "")
        if isinstance(rules, list):
            rules = ",".join(str(x) for x in rules)
        elif rules is None:
            rules = ""
        subject = _clip(r.get("subject") or "", 500, "logs/decisions.jsonl")
        cells = [
            step,
            r.get("event", ""),
            r.get("tool", ""),
            r.get("decision", ""),
            r.get("code", ""),
            rules,
            subject,
        ]
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    return lines


def _cell(s):
    """表のセルに入れる文字列から、列と行を崩す文字を除く。"""
    return str(s).replace("|", "\\|").replace("\n", " ")


def _results_table(results, limit=None):
    cut = (lambda s: s) if limit is None else (lambda s: s[:limit])
    lines = ["| 種類 | 期待 | 判定 | 理由 |", "|---|---|---|---|"]
    for kind, cond, v, why in results:
        c = json.dumps(cond, ensure_ascii=False) if not isinstance(cond, str) else cond
        lines.append(f"| {_cell(kind)} | {_cell(cut(c))} | {_cell(v)} | {_cell(cut(why))} |")
    return lines


def write_report(scn, out, st, results, rows, raw):
    lines = [
        f"# {scn['id']} {scn['title']}",
        "",
        f"全体: {overall(results)}  費用: {st['cost_usd']:.3f} USD",
        "",
    ]
    lines += _results_table(results, 100)
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
    steps = scn.get("steps") or []
    step_sessions = {}
    for p in sorted(out.glob("step-*.jsonl")):
        parsed = parse_stream(p.read_text(encoding="utf-8"))
        num = int(p.stem.split("-")[1])
        step_sessions[num] = parsed["session_ids"]
        ev += ["", f"## {p.stem}"]
        if 0 < num <= len(steps) and steps[num - 1].get("prompt"):
            ev += ["### 入力プロンプト", steps[num - 1]["prompt"].strip()]
        ev += ["### ツール呼び出し"]
        ev += [
            f"- {t['name']}: {_clip(json.dumps(t['input'], ensure_ascii=False), 500, p.name)}"
            for t in parsed["tool_calls"]
        ]
        denial_texts = hook_denial_texts(parsed["hook_events"])
        ok_lines, err_lines = [], []
        for r in parsed["tool_results"]:
            text = _text_of(r["content"])
            if not r["is_error"]:
                ok_lines.append(f"- {_clip(text, EVIDENCE_LIMIT, p.name)}")
            elif _duplicates_hook(text, denial_texts):
                err_lines.append("- hook の応答を参照（下の「hook の応答」と同じ文）")
            else:
                err_lines.append(f"- {_clip(text, EVIDENCE_LIMIT, p.name)}")
        ev += ["### ツール結果（成功）", *(ok_lines or ["- なし"])]
        ev += ["### ツール結果（エラー）", *(err_lines or ["- なし"])]
        hook_lines, omitted = summarize_hooks(parsed["hook_events"], p.name)
        ev += ["### hook の応答（拒否・確認・案内文・Stop だけ）", *hook_lines]
        if omitted:
            counts = "、".join(f"{k} {v} 件" for k, v in sorted(omitted.items()))
            ev.append(f"- 省いた応答: {counts}（全量は {p.name}）")
        ev += ["### 最後の報告", _clip(parsed["final_text"], 3000, p.name)]
    ev += [
        "",
        "## ccnavi の判定の記録（このセッションの行）",
        "",
        "列: step=どの step の行か / event=hook の種類 / tool=対象のツール / "
        "decision=判定（allow・deny・ask など）/"
        " code=判定の理由コード / rules=当たったルール / subject=判定の対象（コマンドやパス）。"
        "UNDECLARED はハンドオーバーに宣言が無いことを示す code。",
        "",
        *_step_decisions_table(rows, step_sessions),
    ]
    (out / "evidence.md").write_text("\n".join(ev) + "\n", encoding="utf-8")


def _one_line(text, limit=120):
    s = " ".join(str(text).split())
    return s if len(s) <= limit else s[:limit] + "…"


def _cond_text(cond):
    parts = [f"{k}={cond[k]}" for k in ("event", "tool", "decision", "code") if k in cond]
    if cond.get("rules_include"):
        parts.append("ルール∋" + ", ".join(cond["rules_include"]))
    if cond.get("subject_contains"):
        parts.append(f"対象∋{cond['subject_contains']}")
    return " ".join(parts)


def scenario_summary(scn):
    """回す前に人間へ見せる、シナリオの概要（何をさせ、何を期待し、何を残すか）。"""
    lines = [f"## 受け入れシナリオ {scn['id']}: {scn['title']}"]
    lines.append(
        f"層 {scn.get('layer', '-')}・要求 {', '.join(scn.get('reqs') or []) or '-'}・"
        f"状態 {scn.get('status', '-')}・モデル {scn.get('model', '-')}・"
        f"費用の上限 {scn.get('budget_usd', '-')} USD"
    )
    if scn.get("test_repo"):
        lines.append("作るもの: projects/test-repo にブランチ・コミット・MR が残る（後で片付ける）")
    else:
        lines.append(
            "作るもの: なし。ccnavi 本体に何か残らないことは、回したあと repo の判定で確かめる"
        )
    setup = (scn.get("setup") or {}).get("files", {})
    if setup:
        lines.append("回す前に作り、回したあとに消すファイル: " + ", ".join(setup))
    lines.append("手順:")
    for i, step in enumerate(scn["steps"], 1):
        if step.get("human"):
            lines.append(f"  {i}. 人間の操作: {_one_line(step.get('instruction', ''))}")
        else:
            lines.append(f"  {i}. {_one_line(step.get('prompt', ''))}")
    expect = scn.get("expect") or {}
    lines.append("期待:")
    for cond in expect.get("decisions", []):
        lines.append(f"  - 判定が出ている: {_cond_text(cond)}")
    for cond in expect.get("decisions_none", []):
        lines.append(f"  - 出ていてはいけない判定: {_cond_text(cond)}")
    for rel in expect.get("files_unchanged", []):
        lines.append(f"  - 変わっていないこと: {rel}")
    for item in expect.get("state", []):
        text = f"`{item['run']}`"
        if "expect_contains" in item:
            text += f" の出力に「{item['expect_contains']}」が出る"
        if "expect_not_contains" in item:
            text += f" の出力に「{item['expect_not_contains']}」が出ない"
        lines.append(f"  - 最後の状態: {text}")
    for s in expect.get("transcript_forbidden", []):
        lines.append(f"  - 出力に出てはいけない: {s}")
    for criterion in expect.get("judge", []):
        lines.append(f"  - 意味で読む: {criterion}")
    unconfirmed = scn.get("unconfirmed") or []
    if unconfirmed:
        lines.append("まだ実ログと突き合わせていない点:")
        lines += [f"  - {u}" for u in unconfirmed]
    return "\n".join(lines)


def cmd_run(args):
    _require_entry()
    root = Path(args.root).resolve()
    scn = load_scenario(args.scenario)
    scn["_prompt_steps"] = sum(1 for s in scn["steps"] if not s.get("human"))
    mode_ok, mode_text = mode_check(root)
    if not mode_ok:
        # 確認の前に止める。--confirmed を付けても、enable でなければ回さない
        print(mode_text)
        return 3
    clash = setup_clashes(scn, root)
    if clash:
        # 有るものを後片付けで消さないよう、回す前に止める
        print(f"setup のパスが既にある（消されてしまうので回さない）: {', '.join(clash)}")
        return 1
    if not args.confirmed:
        print("回す前に、人間に次を確認する。確認が取れたら --confirmed を付けて打ち直す。\n")
        print(scenario_summary(scn) + "\n")
        print(mode_text + "\n")
        print(preflight_facts(root))
        print(
            "問い 1: 今の状態はきれいで、最新か（他のセッションの作業中や未コミットの変更が無いか）"
        )
        if scn.get("test_repo"):
            print("問い 2: projects/test-repo にブランチや MR ができてよいか")
        return 3
    out = Path(
        args.out or ROOT / "logs" / "scenarios" / f"{scn['id']}-{datetime.now():%Y%m%d-%H%M%S}"
    )
    out.mkdir(parents=True, exist_ok=True)
    pre = {rel: file_digest(root / rel) for rel in scn["expect"].get("files_unchanged", [])}
    setup_files, setup_dirs = _setup(scn, root)
    st = {
        "scenario": str(Path(args.scenario).resolve()),
        "root": str(root),
        "next_step": 0,
        "session_ids": [],
        "cost_usd": 0.0,
        "pre_digests": pre,
        "pre_repo": repo_snapshot(root),
        "setup_made": setup_files,
        "setup_dirs": setup_dirs,
    }
    _write_state(out, st)
    return _continue(scn, out, st, root)


def _continue(scn, out, st, root):
    try:
        rc = drive(scn, out, st, Path(root))
    except BaseException:
        # 割り込みなど、drive を抜けて finish に届かないときも、setup のファイルを残さない
        _cleanup(st["setup_made"], st.get("setup_dirs", []), root)
        raise
    return finish(scn, out, st, Path(root)) if rc is None or rc == 11 else rc


def cmd_next(args):
    out = Path(args.out)
    st = json.loads((out / "state.json").read_text(encoding="utf-8"))
    if st.get("halt"):
        print(f"step {st['halt']['step']} で止めた回。続きは回せない")
        return 2
    # 人間の操作の間にモードが変わることがあるので、続けるたびに確かめる
    mode_ok, mode_text = mode_check(Path(st["root"]))
    if not mode_ok:
        print(mode_text)
        return 3
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
