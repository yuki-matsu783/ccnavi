"""報告の書き出しと、人間に見せる確認の文面（段 5）。

report.md・report.json・evidence.md・messages.md を書く。
結果を受け取るだけで、評価を呼び返さない。
"""

import json
import shlex
from pathlib import Path

from tests.scenarios import (
    evidence,
    expect,
    hostenv,
    issues,
    prepare,
    repo,
    schema,
    stream,
    workspace,
)
from tests.scenarios.actors import api, terminal
from tests.scenarios.common import DISPOSABLE_REL, PASS, RUNNER, Refusal, clip

HOST_NOTES = {
    "real": "real: 本物の GitHub / GitLab を相手にした",
    "bare": "bare: ローカルの bare を origin にした。ホストの API は失敗（確かめられなかった）",
    "fake-gitlab": "fake-gitlab: ローカルの bare と fake_gitlab.py を相手にした",
    "docker-gitlab": "docker-gitlab: Docker の本物の GitLab CE を相手にした。"
    "gh / glab は使えず curl で呼ぶ。CE にない機能（変更要求など）は確かめられない",
}


FAKE_NOTE = "。模擬は本物と同じ形を返す保証が無く、模擬のずれで見逃した問題がありうる"


def host_note(host):
    return HOST_NOTES[host] + (FAKE_NOTE if hostenv.is_fake(host) else "")


SNAPSHOT_PLAN = (
    "相手側の状態: step の終わりごとに MR・スレッド・承認・issue を API で読み、"
    "出力の置き場の host-N.json に残す（読むだけ。トークンは伏せる）"
)


def snapshot_note(st):
    """report の host の行に足す、相手側の状態の記録の要約。記録が無い相手には空。"""
    snaps = st.get("host_snapshots")
    if not snaps:
        return ""
    bad = [s for s in snaps if not s["ok"]]
    text = (
        f"相手側の状態: step ごとに host-N.json へ残した（{len(snaps)} 件。承認は MR ごとに読んだ）"
    )
    if bad:
        text += "。取れなかった所がある step: " + "、".join(str(s["step"]) for s in bad)
    return text


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
        subject = clip(r.get("subject") or "", 500, "logs/decisions.jsonl")
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


ANSWER_LIMIT = 1500


def step_answer_lines(scn, out):
    """step ごとの入力プロンプトと、claude の最後の出力を報告に出す行。
    人間の操作の step は、その指示を出す。"""
    lines = []
    for num, step in enumerate(scn.get("steps") or [], 1):
        lines.append("")
        if step.get("human"):
            lines.append(f"### step {num}（人間の操作）")
            lines.append(_one_line(step.get("instruction", ""), 200))
            if step.get("human_run"):
                lines.append("実行器が打つ: " + terminal.run_text(step))
            if step.get("human_api"):
                lines.append(
                    "人間役の API: " + "、".join(api.op_text(o) for o in step["human_api"])
                )
            continue
        lines.append(f"### step {num}")
        lines.append("【入力プロンプト】")
        lines.append(step.get("prompt", "").strip())
        lines.append("【最終出力】")
        path = out / f"step-{num}.jsonl"
        if not path.exists():
            lines.append("未実行")
            continue
        final = stream.parse_stream(path.read_text(encoding="utf-8"))["final_text"]
        lines.append(clip(final, ANSWER_LIMIT, path.name) or "（出力なし）")
    return lines


TREND_SHOWN = 10


def _shown(lines, more):
    if more > 0:
        lines.append(f"- ほか {more} 件")
    return lines


def trend_lines(out, rows):
    """拒否の傾向の節（report.md）。人が読む目印で、判定ではない。"""
    streams = evidence.step_streams(out)
    step_sessions = {n: p["session_ids"] for n, p in streams.items()}
    lines = [
        "",
        "## 拒否の傾向（目印。判定ではない）",
        "誤検知と見逃しがある。定義と限界は acceptance-scenarios.md の「証拠の読み方」。",
    ]
    repeats = evidence.repeated_denials(rows, step_sessions)
    lines += [
        "",
        f"### 反復拒否（同じ code の deny が間に allow なしで {evidence.REPEAT_MIN} 回以上）",
    ]
    lines += [
        f"- step {r['step']}: {r['code'] or '(code なし)'} が {r['count']} 回連続。"
        f"対象: {_one_line(', '.join(r['subjects'][:3]), 200)}"
        for r in repeats[:TREND_SHOWN]
    ] or ["- なし"]
    _shown(lines, len(repeats) - TREND_SHOWN)
    bypass = evidence.bypass_suspects(rows, streams, step_sessions)
    lines += [
        "",
        "### 迂回の疑い（deny された Edit / Write と同じパスへの、その後の Edit / Write）",
    ]
    lines += [
        f"- step {b['step']}: {b['path']} が {b['denied']} 回拒否されたあと、"
        f"同じパスへの Edit / Write が全 {b['calls']} 回"
        for b in bypass[:TREND_SHOWN]
    ] or ["- なし"]
    _shown(lines, len(bypass) - TREND_SHOWN)
    bare = evidence.unguided_denials(streams)
    lines += ["", "### 案内の無い拒否（理由文に次の手を示す語・コード片・`次:` などの行が無い）"]
    lines += [
        f"- step {n} [{label}]: {_one_line(text, 120)}" for n, label, text in bare[:TREND_SHOWN]
    ]
    lines += [] if bare else ["- なし"]
    _shown(lines, len(bare) - TREND_SHOWN)
    return lines


def write_messages(out, rows):
    """messages.md を書く。日本語の不自然さを人が読む材料で、機械の判定はしない。"""
    msgs = evidence.collect_messages(evidence.step_streams(out), rows)
    lines = [
        "# 文面の一覧（hook の応答）",
        "",
        "拒否・確認・案内の文面。パスと数字だけが違うものは 1 件にまとめた"
        "（コミットの ID は `<SHA>`、パスは `<PATH>`、数字は `<N>` に置き換えて比べる）。"
        "出どころは decisions.jsonl の行と順に突き合わせたもので、数が合わないときは出さない。",
    ]
    for n, m in enumerate(msgs, 1):
        lines += ["", f"## {n}. {m['kind']}（{m['count']} 回）"]
        lines.append(f"- 事象: {', '.join(m['events'])} / step: {', '.join(map(str, m['steps']))}")
        lines.append(f"- 出どころ（code / rules）: {'; '.join(m['src']) or '不明'}")
        lines += evidence.fence(m["text"])
    if not msgs:
        lines += ["", "なし"]
    (out / "messages.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report(scn, out, st, results, rows, raw):
    lines = [
        f"# {scn['id']} {scn['title']}",
        "",
        f"全体: {expect.overall(results)}  費用: {st['cost_usd']:.3f} USD",
        "",
        f"相手（setup.host）: {host_note(schema.scenario_host(scn))}",
        "",
    ]
    if snapshot_note(st):
        lines += [snapshot_note(st), ""]
    lines += _results_table(results, 100)
    lines += ["", "## ステップごとの入力プロンプトと最終出力", *step_answer_lines(scn, out)]
    if scn.get("unconfirmed"):
        lines += ["", "## 確かめる点（unconfirmed）"] + [f"- {u}" for u in scn["unconfirmed"]]
    lines += trend_lines(out, rows)
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "report.json").write_text(
        json.dumps(
            [{"kind": k, "expect": c, "verdict": v, "why": w} for k, c, v, w in results]
            + [
                {
                    "kind": "cost",
                    "expect": "total_cost_usd",
                    "verdict": PASS,
                    "why": f"{st['cost_usd']:.4f} USD",
                    "cost_usd": st["cost_usd"],
                }
            ],
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
        f"全体: {expect.overall(results)}",
        "",
        *_results_table(results),
        "",
        "judge の基準:",
        *[f"- {c}" for c in scn["expect"].get("judge", [])],
    ]
    if st.get("human_ops"):
        ev += ["", "## 人間役の操作"]
        for o in st["human_ops"]:
            extra = f" discussion {o['discussion_id']}" if o.get("discussion_id") else ""
            name = f"（{o.get('as') or o.get('thread')}）" if o.get("as") or o.get("thread") else ""
            ev.append(
                f"- step {o['step']}: {o['op']}{name} → HTTP {o['status']}{extra}"
                + (f" 失敗: {o['error']}" if o.get("error") else "")
            )
    if st.get("human_runs"):
        ev += ["", "## 人間役の操作（端末）"]
        for r in st["human_runs"]:
            ev.append(
                f"- step {r['step']}: sh {r['script']} {' '.join(r['tickets'])} → {r['status']}"
                + (f"（終了コード {r['exit']}、ログ: {r['log']}）" if "exit" in r else "")
            )
    steps = scn.get("steps") or []
    step_sessions = {}
    for p in sorted(out.glob("step-*.jsonl")):
        parsed = stream.parse_stream(p.read_text(encoding="utf-8"))
        num = int(p.stem.split("-")[1])
        step_sessions[num] = parsed["session_ids"]
        ev += ["", f"## {p.stem}"]
        if 0 < num <= len(steps) and steps[num - 1].get("prompt"):
            ev += ["### 入力プロンプト", steps[num - 1]["prompt"].strip()]
        ev += ["### ツール呼び出し"]
        ev += [
            f"- {t['name']}: {clip(json.dumps(t['input'], ensure_ascii=False), 500, p.name)}"
            for t in parsed["tool_calls"]
        ]
        denial_texts = evidence.hook_denial_texts(parsed["hook_events"])
        ok_lines, err_lines = [], []
        for r in parsed["tool_results"]:
            text = stream.text_of(r["content"])
            if not r["is_error"]:
                ok_lines.append(f"- {clip(text, evidence.EVIDENCE_LIMIT, p.name)}")
            elif evidence.duplicates_hook(text, denial_texts):
                err_lines.append("- hook の応答を参照（下の「hook の応答」と同じ文）")
            else:
                err_lines.append(f"- {clip(text, evidence.EVIDENCE_LIMIT, p.name)}")
        ev += ["### ツール結果（成功）", *(ok_lines or ["- なし"])]
        ev += ["### ツール結果（エラー）", *(err_lines or ["- なし"])]
        hook_lines, omitted = evidence.summarize_hooks(parsed["hook_events"], p.name)
        ev += ["### hook の応答（拒否・確認・案内文・Stop だけ）", *hook_lines]
        if omitted:
            counts = "、".join(f"{k} {v} 件" for k, v in sorted(omitted.items()))
            ev.append(f"- 省いた応答: {counts}（全量は {p.name}）")
        ev += ["### 最後の報告", clip(parsed["final_text"], 3000, p.name)]
        ev += evidence.context_lines(parsed, num, out)
    ev += [
        "",
        "## ccnavi の判定の記録（このセッションの行）",
        "",
        "列: step=どの step の行か / event=hook の種類 / tool=対象のツール / "
        "decision=判定（allow・deny・ask など）/"
        " code=判定の理由コード / rules=ヒットしたルール / subject=判定の対象（コマンドやパス）。"
        "UNDECLARED はハンドオーバーに宣言が無いことを示す code。",
        "",
        *_step_decisions_table(rows, step_sessions),
    ]
    (out / "evidence.md").write_text("\n".join(ev) + "\n", encoding="utf-8")
    write_messages(out, rows)


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
    if schema.is_scripted(scn):
        lines.append(
            "相手: 代役（tests/fake_claude.py）が step の script どおりに打つ。claude は呼ばず、"
            "費用はかからない。judge は評価しない"
        )
    if scn.get("test_repo"):
        lines.append("作るもの: projects/test-repo にブランチ・コミット・MR が残る（後で片付ける）")
    else:
        lines.append(
            "作るもの: なし。ccnavi 本体に何か残らないことは、回したあと repo の判定で確かめる"
        )
    files = {
        rel: schema.file_spec(v) for rel, v in ((scn.get("setup") or {}).get("files") or {}).items()
    }
    made = [rel for rel, (_, append) in files.items() if not append]
    appended = [rel for rel, (_, append) in files.items() if append]
    if made:
        lines.append("回す前に作り、回したあとに消すファイル: " + ", ".join(made))
    if appended:
        lines.append(
            "回す前に追記する、対象にある管理下のファイル（消さない。戻すのは setup.reset）: "
            + ", ".join(appended)
        )
    for argv in (scn.get("setup") or {}).get("git") or []:
        lines.append("回す前に対象で打つ git: " + shlex.join(argv))
    reset = (scn.get("setup") or {}).get("reset")
    if reset:
        lines.append(
            f"回す前と回したあとに、{DISPOSABLE_REL} を {reset['base']} へ戻す"
            "（未コミットの変更・main 以外のブランチ・ワークツリー・タグ・stash が消える）。"
            "人間の操作で止めた回は、戻さずに残す（続きは next、やめるのは restore）"
        )
    host = schema.scenario_host(scn)
    if hostenv.is_fake(host):
        lines.append(
            f"相手（setup.host）: {host}。ホスト（GitHub / GitLab）へは出ない"
            "（`claude -p` 自身の API 通信は要る）。本物のトークンは claude に渡さない。"
            "origin への通信は一時ディレクトリの bare へ向き、回したあとに消す" + FAKE_NOTE
        )
        if host == "fake-gitlab":
            lines.append(SNAPSHOT_PLAN)
    if host == hostenv.DOCKER:
        lines.append(
            "相手（setup.host）: docker-gitlab。Docker の GitLab（127.0.0.1）に"
            "使い捨てのプロジェクトを作り（名前は sc-<シナリオ ID>-…）、"
            "人間役をメンバーに加えて main を push する。"
            "回したあとにプロジェクトを削除する（人間の操作で止めた回は残す）。"
            "エージェント役のトークンは claude に渡し、人間役のトークンは渡さない"
        )
        lines.append(SNAPSHOT_PLAN)
    issue = (scn.get("setup") or {}).get("issue")
    if issue:
        close = "（回したあとに閉じる）" if issue.get("close") else "（閉じない）"
        lines.append(
            f"回す前に {issue['repo']} へ issue を 1 つ作り、"
            f"prompt の {issues.ISSUE_PLACEHOLDER} に番号を入れる{close}"
        )
    lines.append("手順:")
    for i, step in enumerate(scn["steps"], 1):
        if step.get("human"):
            text = _one_line(step.get("instruction", ""))
            if step.get("human_api"):
                ops = "、".join(api.op_text(o) for o in step["human_api"])
                text = f"{text}（人間役の API: {ops}。止まらずに進む）".lstrip()
            if step.get("human_run"):
                text = (
                    f"実行器が打つ: {terminal.run_text(step)}。止まらずに進む"
                    f"（手で打つのは端末を作れないときだけ: {text}）"
                )
            lines.append(f"  {i}. 人間の操作: {text}")
        else:
            lines.append(f"  {i}. {_one_line(step.get('prompt', ''))}")
    expected = scn.get("expect") or {}
    lines.append("期待:")
    for cond in expected.get("decisions", []):
        lines.append(f"  - 判定が出ている: {_cond_text(cond)}")
    for cond in expected.get("decisions_none", []):
        lines.append(f"  - 出ていてはいけない判定: {_cond_text(cond)}")
    for rel in expected.get("files_unchanged", []):
        lines.append(f"  - 変わっていないこと: {rel}")
    for item in expected.get("state", []):
        text = f"`{item['run']}`"
        if "expect_contains" in item:
            text += f" の出力に「{item['expect_contains']}」が出る"
        if "expect_not_contains" in item:
            text += f" の出力に「{item['expect_not_contains']}」が出ない"
        lines.append(f"  - 最後の状態: {text}")
    for s in expected.get("transcript_forbidden", []):
        lines.append(f"  - 出力に出てはいけない: {s}")
    for criterion in expected.get("judge", []):
        lines.append(f"  - 意味で読む: {criterion}")
    unconfirmed = scn.get("unconfirmed") or []
    if unconfirmed:
        lines.append("まだ実ログと突き合わせていない点:")
        lines += [f"  - {u}" for u in unconfirmed]
    return "\n".join(lines)


def confirmation_text(scn, root, mode_text):
    """回す前に人間へ見せる文。順は、事実 → ワークスペース状態 → ユーザ確認事項。
    run の確認と preflight で同じものを出す。"""
    facts = [scenario_summary(scn), "", mode_text]
    clash = prepare.setup_clashes(scn, root)
    if clash:
        facts += [
            "",
            "注意: setup.files のパスが合わない"
            f"（作る形は既にある・追記の形は無い。run は回さない）: {', '.join(clash)}",
        ]
    if schema.uses_target(scn):
        try:
            top = repo.require_disposable_repo(root, Path(root) / DISPOSABLE_REL)
            prepare.check_before_run(scn, root, top)
            facts += ["", f"対象のリポジトリ: {top}（使い捨てと確かめた）"]
        except Refusal as exc:
            facts += ["", f"注意: 対象のリポジトリを使えない（run は回さない）\n{exc}"]
    questions = [
        "確認事項 1: ワークツリーに未コミットの変更がなく、origin と同期しているか"
        "（他のセッションが作業中の変更を含まないか）"
    ]
    if scn.get("test_repo"):
        questions.append("確認事項 2: projects/test-repo にブランチや MR ができてよいか")
    if (scn.get("setup") or {}).get("reset"):
        questions.append(
            f"確認事項 3: {DISPOSABLE_REL} の未コミットの変更・main 以外のブランチ・"
            "ワークツリー・タグ・stash を、回す前と回したあとに消してよいか（setup.reset）"
        )
    return "\n".join(
        [
            "回す前に、人間に次を確認する。確認が取れたら --confirmed を付けて打ち直す。",
            "",
            "# 1. 事実",
            "",
            *facts,
            "",
            "# 2. ワークスペース状態",
            "",
            workspace.preflight_facts(root),
            "",
            "# 3. ユーザ確認事項",
            "",
            *questions,
        ]
    )


def skipped_lines(skipped):
    return [f"- {who}: {why}" for who, why in skipped] or ["- なし"]


def run_all_confirmation(eligible, skipped, cap, digest, mode_text, root, scenario_args):
    """run-all の確認文。全シナリオに対する 1 回の確認で、回す ID の一覧・概要・作るもの・
    対象の状態・base・費用の上限を見せる。"""
    budget = sum(float(scn["budget_usd"]) for _, scn in eligible)
    table = ["| ID | タイトル | base | モデル | 1 本の上限 (USD) |", "|---|---|---|---|---|"]
    table += [
        f"| {scn['id']} | {_cell(scn['title'])} | {scn['setup']['reset']['base'][:12]} | "
        f"{scn['model']} | {scn['budget_usd']} |"
        for _, scn in eligible
    ]
    questions = [
        "確認事項 1: ワークツリーに未コミットの変更がなく、origin と同期しているか"
        "（他のセッションが作業中の変更を含まないか）",
        f"確認事項 2: {DISPOSABLE_REL} の未コミットの変更・main 以外のブランチ・ワークツリー・"
        "タグ・stash を、各シナリオの前と後に消してよいか（setup.reset。base は上の表）",
    ]
    if any(scn.get("test_repo") or (scn.get("setup") or {}).get("issue") for _, scn in eligible):
        questions.append(
            f"確認事項 3: {DISPOSABLE_REL} にブランチや MR ができてよいか"
            "（setup.issue のシナリオは、本物のホストに issue も作る）"
        )
    questions.append(
        f"確認事項 4: 実費の累計 {cap} USD までを使ってよいか"
        f"（1 本の上限の合計は {budget:.2f} USD。"
        "累計の残りを 1 本の上限が超えるシナリオは回さない）"
    )
    command = (
        f"{RUNNER} run-all {' '.join(shlex.quote(a) for a in scenario_args)} "
        f"--max-total-usd {cap} --confirmed {digest}"
    )
    lines = [
        "回す前に、人間に次を 1 回だけ確認する（シナリオごとには取り直さない）。"
        "確認が取れたら、最後の打ち方で回す。",
        "",
        "この確認が有効なのは、下の ID の集合・base・費用の上限・概要に限る。"
        "実行の直前に作り直した目印が違えば、回さずに止まる（増減・書き換えがあれば取り直す）。",
        "",
        f"# 1. 回す一覧（{len(eligible)} 本。直列。ID 順）",
        "",
        *table,
        "",
        "# 2. 概要と作るもの",
        "",
        *[scenario_summary(scn) + "\n" for _, scn in eligible],
        f"# 3. 対象にしないもの（{len(skipped)} 件。回さない）",
        "",
        *skipped_lines(skipped),
        "",
        "# 4. 事実",
        "",
        mode_text,
        "",
        f"対象のリポジトリ: {Path(root) / DISPOSABLE_REL}（使い捨てと確かめた）",
        "",
        workspace.preflight_facts(root),
        "# 5. ユーザ確認事項",
        "",
        *questions,
        "",
        "# 6. 回す打ち方（確認の目印つき）",
        "",
        command,
    ]
    return "\n".join(lines)


def run_all_table(rows):
    lines = [
        "| ID | 判定 | 実費 (USD) | ツール呼び出し | 拒否 | ask | 出力 |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {r['id']} | {r['verdict']} | {r['cost_usd']:.3f} | {r['tool_calls']} | "
        f"{r['denies']} | {r['asks']} | {r['out']} |"
        for r in rows
    ]
    return lines


def write_run_all_summary(batch, rows, skipped, cap, remaining_ids, stopped):
    total = sum(r["cost_usd"] for r in rows)
    lines = [
        "# run-all の結果",
        "",
        f"回した本数: {len(rows)}  実費の累計: {total:.3f} USD（上限 {cap} USD）",
        "",
        *run_all_table(rows),
    ]
    if stopped:
        lines += [
            "",
            f"## 止まった: {stopped}",
            f"回した本数 {len(rows)}。残りの ID: {', '.join(remaining_ids) or 'なし'}",
        ]
    lines += ["", "## 対象にしなかったもの", *skipped_lines(skipped)]
    (batch / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (batch / "summary.json").write_text(
        json.dumps(
            {
                "max_total_usd": cap,
                "total_cost_usd": total,
                "stopped": stopped,
                "remaining": remaining_ids,
                "scenarios": rows,
                "skipped": [{"who": w, "why": y} for w, y in skipped],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return lines
