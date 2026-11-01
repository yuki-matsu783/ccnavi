"""サブコマンド run / run-all / next / restore / judge / preflight の中身（段 7）。"""

import glob
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

import yaml

from tests.scenarios import (
    drive,
    evidence,
    expect,
    hostenv,
    issues,
    prepare,
    repo,
    report,
    schema,
    workspace,
)
from tests.scenarios.common import (
    DISPOSABLE_REL,
    FAIL,
    PASS,
    ROOT,
    RUNNER,
    Refusal,
    refuse,
    require_entry,
    write_state,
)


def cmd_preflight(args):
    """回さずに、確認に出す文だけを出す。終了コードは run の確認と同じ 3。"""
    root = Path(args.root).resolve()
    scn = schema.load_scenario(args.scenario)
    _, mode_text = workspace.mode_check(root)
    print(report.confirmation_text(scn, root, mode_text))
    return 3


def record_acceptance(out, report_md, accept):
    """報告に足してある意味の判定に、ユーザの受け入れを記す。判定の表は変えない。"""
    if not accept:
        print("この回の意味の判定は、もう報告に足してある（受け入れを記すなら --accept を付ける）")
        return 1
    text = report_md.read_text(encoding="utf-8")
    head, section = text.split(expect.JUDGE_HEADING, 1)
    if "ユーザの受け入れ: あり" in section:
        print("受け入れは、もう記録してある（二重には記さない）")
        return 1
    judged = expect.parse_judgment(section)
    verdict = expect.final_verdict(expect.mechanical_results(out), judged, True)
    section = "\n".join(
        f"全体（機械判定と意味の判定の合わせ）: {verdict}"
        if line.startswith("全体（機械判定と意味の判定の合わせ）:")
        else line.replace("ユーザの受け入れ: なし", "ユーザの受け入れ: あり")
        for line in section.split("\n")
    )
    report_md.write_text(head + expect.JUDGE_HEADING + section, encoding="utf-8")
    print(f"ユーザの受け入れを記した。全体: {verdict}")
    return 0


def cmd_judge(args):
    """意味の判定（別のエージェントが書いた判定の表）を報告に足す。
    足してあれば、--accept でユーザの受け入れを記す。"""
    out = Path(args.out)
    report_md = out / "report.md"
    if expect.JUDGE_HEADING in report_md.read_text(encoding="utf-8"):
        return record_acceptance(out, report_md, args.accept)
    if not args.file:
        print("意味の判定がまだ無い。判定の表のファイルを渡す")
        return 1
    judged = expect.parse_judgment(Path(args.file).read_text(encoding="utf-8"))
    st = json.loads((out / "state.json").read_text(encoding="utf-8"))
    scn = schema.load_scenario(st["scenario"])
    expected = len(scn["expect"].get("judge", []))
    if not judged or len(judged) != expected:
        print(f"判定の表の行数が違う: 期待 {expected} 行、読めた {len(judged)} 行")
        return 1
    lines = ["", expect.JUDGE_HEADING, "", "| 基準 | 判定 | 理由 |", "|---|---|---|"]
    lines += [f"| {c} | {v} | {w} |" for c, v, w in judged]
    lines += ["", f"ユーザの受け入れ: {'あり' if args.accept else 'なし'}"]
    verdict = expect.final_verdict(expect.mechanical_results(out), judged, args.accept)
    lines += [f"全体（機械判定と意味の判定の合わせ）: {verdict}"]
    text = report_md.read_text(encoding="utf-8")
    report_md.write_text(text + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"意味の判定を報告に足した: {report_md}")
    print(f"全体: {verdict}")
    return 0


def cmd_run(args):
    require_entry()
    root = Path(args.root).resolve()
    scn = schema.load_scenario(args.scenario)
    scn["_prompt_steps"] = sum(1 for s in scn["steps"] if not s.get("human"))
    mode_ok, mode_text = workspace.mode_check(root)
    if not mode_ok:
        # 確認の前に止める。--confirmed を付けても、enable でなければ回さない
        print(mode_text)
        return 3
    clash = prepare.setup_clashes(scn, root)
    if clash:
        # 有るものを後片付けで消さないよう、追記の対象が無いまま書き始めないよう、回す前に止める
        print(
            "setup.files のパスが合わない（作る形は既にある・追記の形は無い）: "
            + ", ".join(clash)
            + "\n案内: 作る形のパスは、回したあとに消すので、既にあると回さない"
            + "\n次: パスを直すか、既にあるファイルを退けて打ち直す"
        )
        return 1
    top = None
    if schema.uses_target(scn):
        try:
            top = repo.require_disposable_repo(root, root / DISPOSABLE_REL)
            prepare.check_before_run(scn, root, top)
        except Refusal as exc:
            return refuse(exc)
    if not args.confirmed:
        print(report.confirmation_text(scn, root, mode_text))
        return 3
    out = Path(
        args.out or ROOT / "logs" / "scenarios" / f"{scn['id']}-{datetime.now():%Y%m%d-%H%M%S}"
    ).resolve()  # cwd が変わっても next / restore が同じ回と分かるように
    return drive.start_scenario(scn, args.scenario, root, top, out)


def cmd_next(args):
    out = Path(args.out)
    st = json.loads((out / "state.json").read_text(encoding="utf-8"))
    if st.get("released"):
        print("対象を戻して放した回（restore 済みか終了済み）。続きは回せない")
        print(f"次: やり直すなら `{RUNNER} run <シナリオ> --confirmed`")
        return 2
    if st.get("halt"):
        # halt は finish で戻してある。届かなかった場合に備え、戻し直す（戻し済みなら何もしない）
        print(f"step {st['halt']['step']} で止めた回。続きは回せない")
        undeleted = prepare.teardown_host(st, st["root"])
        failed = repo.release_target(st, st["root"])
        write_state(out, st)
        return 3 if failed or undeleted else 2
    # 人間の操作の間にモードが変わることがあるので、続けるたびに確かめる
    mode_ok, mode_text = workspace.mode_check(Path(st["root"]))
    if not mode_ok:
        print(mode_text)
        return 3
    if st.get("target") and st.get("held"):
        try:
            st["target"]["lock"] = repo.acquire_target(st["target"]["path"], out, resuming=True)
        except Refusal as exc:
            return refuse(exc)
        st["held"] = False
    if st.get("host") and not st["host"].get("down"):
        for note in hostenv.resume(st["root"], st["host"]):
            print(f"案内: {note}")
        write_state(out, st)
    scn = schema.load_scenario(st["scenario"])
    scn["_prompt_steps"] = sum(1 for s in scn["steps"] if not s.get("human"))
    return drive.continue_scenario(scn, out, st, st["root"])


def cmd_restore(args):
    """人間の操作で止めた回を続けずにやめる。setup のファイルを消し、対象を base へ戻して放す。
    戻せなかった回の戻し直しにも使う。"""
    out = Path(args.out)
    st = json.loads((out / "state.json").read_text(encoding="utf-8"))
    scn = schema.load_scenario(st["scenario"])
    tgt = st.get("target")
    if tgt and not st.get("released"):
        try:
            tgt["lock"] = repo.acquire_target(tgt["path"], out, resuming=True)
        except Refusal as exc:
            return refuse(exc)
    prepare.remove_files(st["setup_made"], st.get("setup_dirs", []), st["root"])
    st["setup_made"] = []
    issue_spec = (scn.get("setup") or {}).get("issue")
    if issue_spec and issue_spec.get("close") and st.get("issue_no"):
        issues.close_issue(issue_spec, st["issue_no"], st["root"])
    undeleted = prepare.teardown_host(st, st["root"])
    failed = repo.release_target(st, st["root"])
    write_state(out, st)
    if failed or undeleted:
        return 3
    print(f"{out} の回を片付けた（setup のファイルを消し、対象を戻して放した）")
    return 0


def expand_scenario_paths(patterns):
    """run-all の引数（ディレクトリ・glob・ファイル）をシナリオのパスの列にする。重複は除く。"""
    found = []
    for pattern in patterns:
        p = Path(pattern)
        if p.is_dir():
            found += sorted(p.glob("*.yml"))
        elif p.is_file():
            found.append(p)
        else:
            found += [Path(m) for m in sorted(glob.glob(pattern))]
    seen, paths = set(), []
    for p in found:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            paths.append(p)
    return paths


def run_all_reason(scn, root, top, top_why):
    """run-all の対象にしない理由。対象にできるなら None。#546 の判定（使い捨てのリポジトリ）に、
    続けて回すのに必要な条件（人間の操作が無い・前の回の状態を残さない）を足す。"""
    setup = scn.get("setup") or {}
    if any(step.get("human") for step in scn["steps"]):
        return "人間の操作の step がある（続けて回せない。1 本ずつ確認して回す）"
    if not schema.uses_target(scn):
        return (
            f"使い捨てのリポジトリ（{DISPOSABLE_REL}）に作用する setup が無い"
            "（ccnavi 本体に作用しうる。1 本ずつ確認して回す）"
        )
    if not (setup.get("reset") or {}).get("base"):
        return "setup.reset が無い（前の回の状態が次の回に残る）"
    clash = prepare.setup_clashes(scn, root)
    if clash:
        return "setup.files のパスが合わない（作る形は既にある・追記の形は無い）: " + ", ".join(
            clash
        )
    if top is None:
        return f"対象のリポジトリを使えない: {top_why}"
    try:
        prepare.check_before_run(scn, root, top)
    except Refusal as exc:
        return f"回す前の確認で断られる: {exc.what}"
    return None


def run_all_plan(paths, root):
    """(対象, 対象にしない) を返す。対象は [(パス, シナリオ)] を ID 順に、対象にしないものは
    [(パスか ID, 理由)] を、読んだ順に並べる。"""
    root = Path(root)
    top, top_why = None, ""
    try:
        top = repo.require_disposable_repo(root, root / DISPOSABLE_REL)
    except Refusal as exc:
        top_why = exc.what
    eligible, skipped, ids = [], [], {}
    for path in paths:
        try:
            scn = schema.load_scenario(path)
        except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
            skipped.append((str(path), f"読み込めない: {exc}"))
            continue
        sid = str(scn["id"])
        if sid in ids:
            skipped.append((sid, f"ID が重複している（{ids[sid]} と同じ）"))
            continue
        ids[sid] = str(path)
        scn["_prompt_steps"] = sum(1 for s in scn["steps"] if not s.get("human"))
        why = run_all_reason(scn, root, top, top_why)
        if why:
            skipped.append((sid, why))
        else:
            eligible.append((path, scn))
    eligible.sort(key=lambda e: str(e[1]["id"]))
    return eligible, skipped


def run_all_digest(eligible, cap):
    """確認した内容の目印。ID の集合・base・シナリオごとの費用の上限・累計の上限・
    人が読んだ概要から作る。どれか一つでも変われば目印も変わる。
    実行の直前に作り直して、確認したときの値と比べる。"""
    body = {
        "max_total_usd": cap,
        "scenarios": [
            {
                "id": str(scn["id"]),
                "base": scn["setup"]["reset"]["base"],
                "budget_usd": scn["budget_usd"],
                "summary": report.scenario_summary(scn),
                # 概要に出ない欄（prompt の全文・permission_mode・model・expect・setup の中身）も
                # 含める。書き換えがあれば目印が変わり、確認を取り直す（安全側）
                "scenario": {k: v for k, v in scn.items() if k != "_prompt_steps"},
            }
            for _, scn in eligible
        ],
    }
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def scenario_row(scn, out, root, rc):
    """run-all の表の 1 行（ID・判定・実費・ツール呼び出し数・拒否数・ask 数・出力の置き場）。"""
    row = {
        "id": str(scn["id"]),
        "rc": rc,
        "verdict": "not-run",
        "cost_usd": 0.0,
        "tool_calls": 0,
        "denies": 0,
        "asks": 0,
        "out": str(out),
    }
    try:
        st = json.loads((out / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return row
    row["cost_usd"] = float(st.get("cost_usd") or 0.0)
    row["tool_calls"] = sum(len(p["tool_calls"]) for p in evidence.step_streams(out).values())
    decided = expect.read_decisions(
        Path(root) / "logs/decisions.jsonl", st.get("session_ids") or []
    )
    row["denies"] = sum(1 for r in decided if r.get("decision") == "deny")
    row["asks"] = sum(1 for r in decided if r.get("decision") == "ask")
    try:
        recorded = json.loads((out / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return row
    row["verdict"] = expect.overall(
        [(r["kind"], r["expect"], r["verdict"], r["why"]) for r in recorded]
    )
    return row


def _run_all_loop(eligible, root, cap, batch, rows):
    """順に回す。止まった理由（なければ空）を返す。rows に、回した順に行を足す。"""
    spent = 0.0
    for path, scn in eligible:
        sid = str(scn["id"])
        remaining = cap - spent
        if float(scn["budget_usd"]) > remaining + 1e-9:
            return (
                f"{sid} の費用の上限 {scn['budget_usd']} USD が、累計の残り "
                f"{max(remaining, 0):.3f} USD（上限 {cap} USD - 実費 {spent:.3f} USD）を超える"
            )
        mode_ok, mode_text = workspace.mode_check(root)
        if not mode_ok:
            return f"{sid} の前に、判定のモードが enable でなくなった\n{mode_text}"
        out = batch / sid
        try:
            top = repo.require_disposable_repo(root, root / DISPOSABLE_REL)
            prepare.check_before_run(scn, root, top)
        except Refusal as exc:
            return f"{sid} を回す前に断られた\n{exc}"
        rc = drive.start_scenario(scn, path, root, top, out)
        row = scenario_row(scn, out, root, rc)
        rows.append(row)
        spent += row["cost_usd"]
        print(f"-- {sid}: {row['verdict']}（実費 {row['cost_usd']:.3f} USD、累計 {spent:.3f} USD）")
        if rc in (3, 10):
            return f"{sid} が終了コード {rc} で止まった（対象の状態を手で確かめる）"
    return ""


def cmd_run_all(args):
    require_entry()
    root = Path(args.root).resolve()
    cap = round(float(args.max_total_usd), 4)
    if not math.isfinite(cap) or cap <= 0:
        print("--max-total-usd は 0 より大きい有限の値にする")
        print("次: 実費の累計の上限を USD で渡して打ち直す")
        return 2
    mode_ok, mode_text = workspace.mode_check(root)
    if not mode_ok:
        print(mode_text)
        return 3
    eligible, skipped = run_all_plan(expand_scenario_paths(args.scenarios), root)
    if not eligible:
        print("run-all で回せるシナリオが無い\n対象にしないもの:")
        print("\n".join(report.skipped_lines(skipped)))
        print("次: 対象にしなかった理由を直すか、1 本ずつ `run` で確認して回す")
        return 1
    digest = run_all_digest(eligible, cap)
    if args.confirmed is None:
        print(
            report.run_all_confirmation(
                eligible, skipped, cap, digest, mode_text, root, args.scenarios
            )
        )
        return 3
    if args.confirmed != digest:
        print(
            "確認した内容と今回の対象が違う（ID の集合・base・費用の上限・概要のどれかが"
            "増えたか減ったか、書き換わった）。回さない"
        )
        print(f"今の対象: {', '.join(str(s['id']) for _, s in eligible)}（目印 {digest}）")
        print(f"案内: 確認した目印は {args.confirmed}")
        print("次: --confirmed を付けずに打ち直して確認を取り直す")
        return 3
    batch = Path(
        args.out or ROOT / "logs" / "scenarios" / f"run-all-{datetime.now():%Y%m%d-%H%M%S}"
    ).resolve()
    batch.mkdir(parents=True, exist_ok=True)
    rows, stopped = [], ""
    try:
        stopped = _run_all_loop(eligible, root, cap, batch, rows)
    finally:
        done = {r["id"] for r in rows}
        remaining = [str(s["id"]) for _, s in eligible if str(s["id"]) not in done]
        lines = report.write_run_all_summary(batch, rows, skipped, cap, remaining, stopped or "")
        print("\n".join(["", *lines]))
        print(f"\n要約: {batch}/summary.md")
    if stopped:
        print(f"次: 残りを回すなら、{RUNNER} run-all を --confirmed なしで打って確認を取り直す")
        return 3
    verdicts = {r["verdict"] for r in rows}
    if FAIL in verdicts:
        return 1
    return 0 if verdicts <= {PASS} else 2
