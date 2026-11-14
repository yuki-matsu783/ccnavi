"""1 本のシナリオを回す流れ（層 6）。

step の種類を actors/ に振り分け、状態を書き、判定と後片付けまで進める。
"""

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from tests.scenarios import (
    expect,
    hostenv,
    issues,
    prepare,
    repo,
    report,
    schema,
    stream,
    workspace,
)
from tests.scenarios.actors import api, terminal
from tests.scenarios.common import (
    FAIL,
    INCONCLUSIVE,
    PASS,
    REVIEW,
    ROOT,
    RUNNER,
    Refusal,
    refuse,
    require_entry,
    write_state,
)

STEP_TIMEOUT_SEC = 900


def run_step(scn, step, out, idx, session_id, resume_id, persist, root, issue_no=None, env=None):
    """env が None なら親の環境を継承する（setup.host が real のとき）。"""
    require_entry()
    cmd = [
        "claude",
        "-p",
        issues.fill_issue(step["prompt"].strip(), issue_no),
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
    if schema.is_scripted(scn):
        cmd, env = _scripted(cmd, step, out / f"step-{idx}.script.json", env)
    r = subprocess.run(
        cmd, cwd=root, capture_output=True, text=True, timeout=STEP_TIMEOUT_SEC, env=env
    )
    (out / f"step-{idx}.jsonl").write_text(r.stdout, encoding="utf-8")
    if r.stderr.strip():
        (out / f"step-{idx}.stderr").write_text(r.stderr, encoding="utf-8")
    if r.returncode != 0:
        # 失敗した step の出力は、ここでは判定に使わない（drive が止める）
        raise RuntimeError(f"claude が終了コード {r.returncode} で終わった")
    return stream.parse_stream(r.stdout)


def _scripted(cmd, step, plan_path, env):
    """claude の代わりにスタブ（tests/fake_claude.py）を同じ引数で起こす形にする。"""
    plan = {"script": step["script"], "after_stop_block": step.get("after_stop_block") or []}
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ if env is None else env)
    env["CCNAVI_FAKE_CLAUDE_SCRIPT"] = str(plan_path)
    env["PYTHONPATH"] = str(ROOT)
    return [sys.executable, "-m", "tests.fake_claude", *cmd[1:]], env


def _persist_needed(steps, idx):
    """続きの step が --resume でつなぐなら、セッションを残す。"""
    for nxt in steps[idx + 1 :]:
        if nxt.get("human"):
            continue
        return bool(nxt.get("resume"))
    return False


def _failed_step_cost(scn, out, num, exc):
    """正常に終わらなかった step の実費の見積もり。累計の上限が過小に見えないようにする。
    出力が残っていれば（終了コードが 0 でない）その result の費用、時間切れで出力が無ければ
    step の上限（--max-budget-usd に渡す値。実費より多めの保守側）。それ以外は 0。"""
    path = out / f"step-{num}.jsonl"
    if path.exists():
        return stream.parse_stream(path.read_text(encoding="utf-8"))["cost_usd"]
    if isinstance(exc, subprocess.TimeoutExpired):
        return round(scn["budget_usd"] / max(1, scn["_prompt_steps"]), 2)
    return 0.0


def run_steps(scn, out, st, root):
    """st['next_step'] から、人間の操作の step か末尾まで進める。
    人間の操作で止まったら 10、末尾まで進んだら None を返す。
    expect_after を満たさなければ 11 を返す。"""
    steps = scn["steps"]
    while st["next_step"] < len(steps):
        idx = st["next_step"]
        step = steps[idx]
        if step.get("human") and step.get("human_api"):
            rc = api.run(out, st, idx, step, root)
            prepare.snapshot_host(out, st, idx, root)
            if rc:
                return rc
            continue
        if step.get("human") and step.get("human_run"):
            rc = terminal.run(out, st, idx, step, root)
            if rc != "pause":
                prepare.snapshot_host(out, st, idx, root)
                if rc:
                    return rc
                continue
            print(
                f"\nstep {idx + 1}: この環境では擬似端末を作れないので、人間役を打てない。手で打つ"
            )
        if step.get("human"):
            print(f"\n== 人間の操作（step {idx + 1}）==")
            print(step.get("instruction", "").strip())
            for c in step.get("check", []):
                print(f"  確認: {c}")
            print(
                "\n済んだら人間が「次へ」と言う。続きは: python -m tests.scenarios.runner next", out
            )
            st["next_step"] = idx + 1
            write_state(out, st)
            return 10
        resume_id = st["session_ids"][-1] if step.get("resume") and st["session_ids"] else None
        sid = str(uuid.uuid4())
        t0 = time.time()
        persist = _persist_needed(steps, idx)
        try:
            ev = run_step(
                scn,
                step,
                out,
                idx + 1,
                sid,
                resume_id,
                persist,
                root,
                st.get("issue_no"),
                env=prepare.host_env(st),
            )
        except Exception as e:  # 起動できない・時間切れ・終了コードが 0 でない
            st["cost_usd"] += _failed_step_cost(scn, out, idx + 1, e)
            prepare.snapshot_host(out, st, idx, root)
            # 後続は回さず止める。finish に回して、後片付けを通す
            st["halt"] = {
                "step": idx + 1,
                "kind": "run",
                "reasons": [f"step {idx + 1} が正常に終わらなかった: {e}"],
            }
            write_state(out, st)
            print(f"step {idx + 1}: 正常に終わらなかった（{e}）。後続の step には進まず止める")
            return 11
        print(f"step {idx + 1}: {time.time() - t0:.0f} 秒、費用 {ev['cost_usd']:.3f} USD")
        for s in ev["session_ids"] or [sid]:
            if s not in st["session_ids"]:
                st["session_ids"].append(s)
        st["cost_usd"] += ev["cost_usd"]
        st["next_step"] = idx + 1
        write_state(out, st)
        prepare.snapshot_host(out, st, idx, root)
        after = step.get("expect_after")
        if after:
            try:
                bad = [r for r in expect.evaluate_after(after, st, root) if r[2] != PASS]
            except Exception as e:  # check_state の timeout・cwd 不在など。止めて finish に回す
                bad = [("expect_after", after, INCONCLUSIVE, f"評価で例外: {e!r}")]
            if bad:
                st["halt"] = {
                    "step": idx + 1,
                    "reasons": [
                        f"{r[0]} {json.dumps(r[1], ensure_ascii=False)}: {r[3]}" for r in bad
                    ],
                }
                write_state(out, st)
                print(f"step {idx + 1}: expect_after を満たさない。後続の step には進まず止める")
                return 11
    return None


def finish(scn, out, st, root):
    try:
        # state の run は setup のファイルが残っているうちに回す。消すのは判定のあと
        raw = "".join(p.read_text(encoding="utf-8") for p in sorted(out.glob("step-*.jsonl")))
        rows = expect.read_decisions(Path(root) / "logs/decisions.jsonl", st["session_ids"])
        spec = scn["expect"]
        if schema.is_scripted(scn):
            # スタブは台本どおりに打つので、モデルの振る舞いを読む judge は評価しない
            spec = {k: v for k, v in spec.items() if k != "judge"}
        results = expect.evaluate(spec, rows, raw, root, st["pre_digests"])
        halt = st.get("halt")
        if halt:
            # 止まった回は後続の step が回っていないので、decisions・state は必ず落ちる。外す
            results = [r for r in results if r[0] not in ("decisions", "state")]
            kind = halt.get("kind", "expect_after")
            results.append((kind, {"step": halt["step"]}, INCONCLUSIVE, "; ".join(halt["reasons"])))
        # ccnavi 本体に、ブランチ・コミット・ワークツリーが残っていないかを実際の状態で確かめる
        v, why = expect.repo_result(st.get("pre_repo"), workspace.repo_snapshot(root))
        results.append(("repo", "ccnavi 本体の HEAD・ブランチ・ワークツリー", v, why))
        note = report.host_note(schema.scenario_host(scn))
        if (st.get("host") or {}).get("version"):
            note = note.replace(
                "docker-gitlab:", f"docker-gitlab（GitLab {st['host']['version']}）:", 1
            )
        results.append(("host", "setup.host", PASS, note))
    finally:
        prepare.remove_files(st["setup_made"], st.get("setup_dirs", []), root)
        st["setup_made"] = []  # restore が、あとから同じパスに作られたファイルを消さないように
        issue = (scn.get("setup") or {}).get("issue")
        if issue and issue.get("close") and st.get("issue_no"):
            issues.close_issue(issue, st["issue_no"], root)
        undeleted = prepare.teardown_host(st, root)
        not_restored = repo.release_target(st, root)
        write_state(out, st)
    if not_restored:
        results.append(("reset", st["target"]["path"], FAIL, "base に戻せなかった。手作業が要る"))
    if st.get("human_ops"):
        bad = [o for o in st["human_ops"] if not 200 <= o["status"] < 300 or o.get("error")]
        total = len(st["human_ops"])
        results.append(
            (
                "human-api",
                "人間役の API 操作",
                FAIL if bad else PASS,
                f"{total} 件中 {total - len(bad)} 件が成功"
                + (f"、{len(bad)} 件が失敗" if bad else ""),
            )
        )
    for run in st.get("human_runs") or []:
        if run.get("status") != "done":
            continue
        # 人間が見ていた項目を、実行器が打った回は誰も見ていない。pass にせず目視を求める
        for item in scn["steps"][run["step"] - 1].get("check") or []:
            results.append(
                (
                    "human-run-check",
                    {"step": run["step"]},
                    REVIEW,
                    f"{item}（実行器が打ったので未確認。ログ: {run['log']}）",
                )
            )
    if undeleted:
        host = st["host"]
        results.append(
            (
                "host-delete",
                host["url"],
                FAIL,
                f"プロジェクト（ID {host['project_id']}）を消せなかった。手作業で消す",
            )
        )
    report.write_report(scn, out, st, results, rows, raw)
    final = expect.overall(results)
    print(f"\n結果: {final}（レポート: {out}/report.md）")
    if not_restored or undeleted:
        return 3  # レポートは書いた。次の回を始めさせない
    return {PASS: 0, FAIL: 1, INCONCLUSIVE: 2, REVIEW: 2}[final]


def start_scenario(scn, scenario_path, root, top, out):
    """確認と回す前の検査が済んだシナリオを回す（setup → step → 判定 → 後片付け）。終了コード。
    run と run-all が使う。top は使い捨てのリポジトリの実体（対象を触らないシナリオは None）。"""
    out.mkdir(parents=True, exist_ok=True)
    pre = {rel: expect.file_digest(root / rel) for rel in scn["expect"].get("files_unchanged", [])}
    setup = scn.get("setup") or {}
    base = (setup.get("reset") or {}).get("base")
    st = {
        "scenario": str(Path(scenario_path).resolve()),
        "agent": scn.get("agent", schema.AGENT_CLAUDE),
        "root": str(root),
        "out": str(out),
        "next_step": 0,
        "session_ids": [],
        "cost_usd": 0.0,
        "pre_digests": pre,
        "pre_repo": workspace.repo_snapshot(root),
        "setup_made": [],
        "setup_dirs": [],
        "issue_no": None,
        "target": None,
    }
    write_state(out, st)
    try:
        if top:
            lock = repo.acquire_target(top, out)
            st["target"] = {"path": top, "base": base, "lock": lock}
            write_state(out, st)
            if base:
                repo.reset_repo(root, top, base)
            prepare.start_host(scn, st, out, root, top)
        # issue は回すたびに新しく作る。番号は state に残し、続きの step の prompt にも入れる
        if setup.get("issue"):
            st["issue_no"] = issues.make_issue(setup["issue"], root)
        st["setup_made"], st["setup_dirs"] = prepare.make_files(scn, root, top)
        if setup.get("git"):
            repo.run_setup_git(root, top, setup["git"], host=st.get("host"))
        if any(s.get("human_run") for s in scn["steps"]):
            # human_run の対象を決める基準。setup で置いた提案は対象にしない
            st["proposals_before"] = terminal.pending_proposals(root)
    except Refusal as exc:
        abort(scn, out, st, root)
        return refuse(exc)
    except BaseException:
        abort(scn, out, st, root)
        raise
    write_state(out, st)
    return continue_scenario(scn, out, st, root)


def abort(scn, out, st, root):
    """回し始める前に止めたとき、作ったものを消し、対象を戻して放す（戻せなければ文面が出る）。"""
    st["setup_made"] = st.get("setup_made") or []
    prepare.remove_files(st["setup_made"], st.get("setup_dirs", []), root)
    issue = (scn.get("setup") or {}).get("issue")
    if issue and issue.get("close") and st.get("issue_no"):
        issues.close_issue(issue, st["issue_no"], root)
    prepare.teardown_host(st, root)
    repo.release_target(st, root)
    write_state(out, st)


def continue_scenario(scn, out, st, root):
    try:
        rc = run_steps(scn, out, st, Path(root))
    except BaseException:
        # 割り込みなど、drive を抜けて finish に届かないときも、setup のファイルを残さず、
        # 対象を base へ戻す（戻せなければ文面を出す。元の例外はそのまま投げ直す）
        prepare.remove_files(st["setup_made"], st.get("setup_dirs", []), root)
        st["setup_made"] = []
        prepare.teardown_host(st, root)
        repo.release_target(st, root)
        write_state(out, st)
        raise
    if rc == 10 and st.get("host"):
        # 模擬は止める。置き場（bare・スタブ・模擬の状態）は、next が同じ環境で続けるので残す
        hostenv.hold(root, st["host"])
        write_state(out, st)
        if st["host"].get("project_id"):
            print(
                f"案内: docker-gitlab のプロジェクトを残している: {st['host']['url']}"
                f"（ID {st['host']['project_id']}）。next が最後まで終わるか restore で消える"
            )
    if rc == 10 and st.get("target"):
        # 人間が操作したあとの next で続くので、対象は base に戻さず残す（ロックだけ放す）
        repo.release_target(st, root, hold=True)
        write_state(out, st)
        tgt = st["target"]
        if tgt.get("base"):
            print(
                f"案内: {tgt['path']} を {tgt['base']} に戻していない"
                "（setup のファイルも残している）。戻すのは next が最後まで終わったとき"
            )
            print(f"次: 続けるなら `{RUNNER} next {out}`、やめるなら `{RUNNER} restore {out}`")
    return finish(scn, out, st, Path(root)) if rc is None or rc == 11 else rc
