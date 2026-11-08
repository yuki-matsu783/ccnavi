"""人間役の端末操作（human_run。段 4）。"""

import contextlib
import json
import os
import select
import signal
import subprocess
import time
from pathlib import Path

from tests.inproc import run_ccnavi
from tests.scenarios import prepare, schema
from tests.scenarios.common import DISPOSABLE_REL, fold, write_state

HUMAN_RUN_TIMEOUT_SEC = 300


class NoTerminal(Exception):
    """擬似端末を作れない（Windows の Python には pty が無い）。"""


def run_text(step):
    script, what = schema.HUMAN_RUN_SCRIPTS[step["human_run"]["script"]]
    return f"`{Path(script).name}`（{what}）"


def _board(root):
    """ボードの JSON（`ccnavi --explain --json`）。チケットの一覧と承認待ちの識別子を読む。"""
    done = run_ccnavi(["--explain", "--json", "--root", str(root)], cwd=str(root), messages=False)
    if done.returncode != 0:
        raise RuntimeError(f"--explain が終了コード {done.returncode}: {done.stderr.strip()}")
    return json.loads(done.stdout)


def pending_proposals(root):
    """承認待ちの提案の [識別子, プロジェクト] の並び。複数のツリーに同じ識別子が出るので畳む。"""
    board = _board(root)
    waiting = set(board["pending_approval"])
    return [
        list(p)
        for p in sorted(
            {(t["ticket"], t["project"]) for t in board["tickets"] if t["ticket"] in waiting}
        )
    ]


def approved_ids(root):
    """承認済みで閉じていないチケットの識別子（全ツリー）。"""
    return {t["ticket"] for t in _board(root)["tickets"] if t["copy"]["status"] == "open"}


def _human_run_env(st, root):
    """人間役の環境。ターミナル要求を本物の端末で通すため、その要求を切る変数を外す。
    別のワークスペースへ向かないよう、CCNAVI_WORKSPACE を root に固定する。
    模擬のホストでは、push 先を模擬に向けるため claude と同じ環境を使う。"""
    env = dict(prepare.host_env(st) or os.environ)
    env.pop("CCNAVI_GUARD_TICKET_APPROVAL", None)
    env["CCNAVI_WORKSPACE"] = str(root)
    return env


def run_on_terminal(argv, cwd, env, answers, timeout=HUMAN_RUN_TIMEOUT_SEC):
    """argv を擬似端末に付けて起動し、（終了コード, 出力）を返す。
    時間切れは subprocess.TimeoutExpired。孫（ccnavi・git）も残さないよう、
    プロセスグループごと止める。"""
    if not hasattr(os, "openpty"):
        raise NoTerminal
    try:
        master, slave = os.openpty()
    except OSError as e:
        raise NoTerminal from e
    try:
        proc = subprocess.Popen(
            argv,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=cwd,
            env=env,
            start_new_session=True,
        )
    except BaseException:
        os.close(master)
        raise
    finally:
        # 親が slave を持ったままだと、子が全部終わっても master の読みが終わらない
        os.close(slave)
    chunks = []
    try:
        # 端末は行単位の入力なので、sh が読むまで溜まる。子が先に終わって書けなければ捨てる
        for answer in answers:
            try:
                os.write(master, (answer + "\n").encode("utf-8"))
            except OSError:
                break
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            ready, _, _ = select.select([master], [], [], min(left, 1.0))
            if not ready:
                continue
            try:
                data = os.read(master, 4096)
            except OSError:  # Linux は slave が全部閉じると EIO を返す。EOF として扱う
                break
            if not data:
                break
            chunks.append(data)
        rc = proc.wait(timeout=max(1.0, deadline - time.monotonic()))
    except BaseException:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        raise
    finally:
        os.close(master)
    return rc, b"".join(chunks).decode("utf-8", errors="replace").replace("\r", "")


def _halt_human_run(out, st, num, reason):
    st["halt"] = {"step": num, "kind": "human_run", "reasons": [reason]}
    write_state(out, st)
    print(f"step {num}: 人間役の操作を止めた（{reason}）。後続の step には進まない")
    return 11


def run(out, st, idx, step, root):
    """human_run の sh を人間役として端末で打ち、止まらずに次の step へ進む。
    止めたら 11、進んだら None。端末を作れないときは "pause"
    （今の human step と同じく一時停止させる）。

    処理フロー:
    1. 前の回で打ち始めたまま止まった記録があれば、打ち直さずに止める。
    2. 承認待ちの提案を集め、基準（回し始めか、前の human_run のあと）に無い識別子を対象にする。
       0 件か、test-repo 以外の提案が混ざっていれば止める
       （黙って外すと親だけ落ちて承認が失敗する）。
    3. 打ち始めたことを state に書いてから、端末で打つ。
    4. 終了コードでなく、対象が新しく承認済みになったか、打つ前から承認済みで
       承認待ちから消えたか（親の改版）で成否を見る
       （push の失敗や保護ブランチでも 0 で終わるため。承認待ちのまま残った改版は数えない）。
    """
    num = idx + 1
    runs = st.setdefault("human_runs", [])
    if any(r["step"] == num and r["status"] == "started" for r in runs):
        return _halt_human_run(
            out,
            st,
            num,
            "前の回で打ち始めたまま止まった。承認と push の状態を確かめてから run でやり直す",
        )
    if not hasattr(os, "openpty"):
        return "pause"
    if "proposals_before" not in st:
        # 基準が無いと、承認待ちが全部「増えた」ことになる。打たずに止める
        return _halt_human_run(out, st, num, "承認待ちの基準が state に無い。run でやり直す")
    before = {p[0] for p in st["proposals_before"]}
    added = [p for p in pending_proposals(root) if p[0] not in before]
    if not added:
        return _halt_human_run(out, st, num, "承認する提案が増えていない")
    project = Path(DISPOSABLE_REL).name
    other = [p for p in added if fold(p[1]) != fold(project)]
    if other:
        return _halt_human_run(
            out,
            st,
            num,
            f"{project} 以外の提案が増えている: "
            + ", ".join(f"{i}（{proj or 'ワークスペースルート'}）" for i, proj in other),
        )
    ids = sorted({p[0] for p in added})
    spec = step["human_run"]
    script = schema.HUMAN_RUN_SCRIPTS[spec["script"]][0]
    record = {"step": num, "script": script, "tickets": ids, "status": "started"}
    runs.append(record)
    write_state(out, st)
    print(f"\n== 人間役の操作（step {num}、端末）==\n  sh {script} {' '.join(ids)}")
    log = out / f"human-run-{num}.log"
    approved_before = approved_ids(root)
    try:
        rc, text = run_on_terminal(
            ["sh", script, *ids], root, _human_run_env(st, root), spec.get("answers", [])
        )
    except NoTerminal:
        runs.remove(record)
        write_state(out, st)
        return "pause"
    except subprocess.TimeoutExpired:
        record["status"] = "halted"
        return _halt_human_run(out, st, num, f"{HUMAN_RUN_TIMEOUT_SEC} 秒で終わらなかった")
    except OSError as e:  # sh や cwd が無いなど、起動できない
        record["status"] = "halted"
        return _halt_human_run(out, st, num, f"起動できない: {e}")
    log.write_text(text, encoding="utf-8")
    for line in text.strip().splitlines()[-5:]:
        print(f"  | {line}")
    record.update(exit=rc, log=str(log))
    pending_now = pending_proposals(root)
    pending_ids = {p[0] for p in pending_now}
    # 新しく承認済みになったか、打つ前から承認済みで承認待ちから消えた（親の改版）なら成功
    approved = (approved_ids(root) - approved_before) | (approved_before - pending_ids)
    missing = [i for i in ids if i not in approved]
    if missing:
        record["status"] = "halted"
        return _halt_human_run(
            out,
            st,
            num,
            f"新しく承認済みにならなかった: {', '.join(missing)}（終了コード {rc}、ログ: {log}）",
        )
    record["status"] = "done"
    st["proposals_before"] = pending_now
    st["next_step"] = num
    write_state(out, st)
    return None
