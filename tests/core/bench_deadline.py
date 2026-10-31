"""hook の判定の期限（`judge.DEADLINE_SECONDS`、REQ-CMN-08）が守れるかを測る。

テストではない。名前が `test_` で始まらないので `unittest discover` は拾わない。
測るのに数十秒掛かるうえ、絶対の時間は機械で変わるので、合否を付ける形にしていない。
見たいのは「どの入力が期限の何割を使うか」と「期限を超えたあとの実際の長さ」。

    uv run python -m tests.core.bench_deadline          # 小さい入力(数分)
    uv run python -m tests.core.bench_deadline --big    # 置換 8 万個、400KB の尾の大きさまで(長い)

期限の確認は、ルールを 1 つ照合するたびに見る（`judge.decide_before`）。1 つのルールの照合の途中と、
その前の読み（`shellread`）は途中で切れないので、1 回がそれより長いと、超えた時間はそのまま延びる。
そのため「期限との比」が 1 を超えても、`deadline-exceeded` で止まるとは限らない
（読みと照合が期限を超えたまま終わって、次のルールの確認で初めて止まる）。表では
次の 2 つを分けて出す。

- 時間: 呼び出し 1 回にかかった実時間（ルールの読み込みと記録を含む）
- 結果: 終了コードと、記録の `decision` / `reason`

ルールは、このリポジトリの共通レイヤーとプロジェクトのレイヤー（`.ccnavi/config/common/rules.yml`）と
組み込みをそのまま使う。記録は一時のワークスペースに残し、リポジトリの `logs/` を汚さない。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

from ccnavi.hook import judge
from tests import ROOT, common_path, config_path
from tests.inproc import run_ccnavi

BASE_COMMAND = "echo "


def workspace() -> str:
    """このリポジトリの設定をコピーした、記録を汚さない一時のワークスペース。"""
    ws = tempfile.mkdtemp(prefix="ccnavi-bench-deadline-")
    for kind in ("rules", "risk"):
        for place in (common_path, config_path):
            try:
                source = place(ROOT, kind)
            except KeyError:
                continue
            if os.path.exists(source):
                target = place(ws, kind)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(source, target)
    return ws


def cases(big: bool) -> list[tuple[str, str, dict[str, str]]]:
    """(名前, ツール, tool_input) の並び。同じ形で大きさを 4 倍ずつ上げ、伸び方が分かるようにする。

    `big` を付けると、置換 8 万個、400KB の尾の大きさまで測る。1 行に数分から数十分かかる。
    """
    plain = (2_000, 5_000, 10_000, 20_000) + ((100_000, 400_000) if big else ())
    subs = (500, 2_000, 5_000) + ((20_000, 80_000) if big else ())
    tails = (100_000,) + ((400_000,) if big else ())
    paths = (5_000, 10_000) + ((100_000,) if big else ())
    out: list[tuple[str, str, dict[str, str]]] = []
    for size in plain:
        out.append((f"長い Bash {size:,} 字", "Bash", {"command": BASE_COMMAND + "a" * size}))
    for count in subs:
        out.append((f"置換 {count:,} 個", "Bash", {"command": BASE_COMMAND + "$(:) " * count}))
    for tail in tails:
        # PowerShell は読まず、生の文字列にルールを適用する。`>` は上限（50）ちょうど。
        command = "echo " + ">" * 50 + "x" * tail
        out.append((f"`>` 50 個 + 尾 {tail:,} 字", "PowerShell", {"command": command}))
    over = "echo " + ">" * 51 + "x" * 100_000
    out.append(("`>` 51 個(上限超え) + 尾 10 万", "PowerShell", {"command": over}))
    out.append(("`>` 5 万個(上限超え)", "PowerShell", {"command": "echo " + ">" * 50_000}))
    for size in paths:
        path = {"file_path": "/" + "a" * size, "content": "x"}
        out.append((f"巨大な file_path {size:,} 字", "Write", path))
    return out


def one(ws: str, tool: str, tool_input: dict[str, str]) -> tuple[float, int, str, str]:
    """1 回動かして (秒, 終了コード, decision, reason)。"""
    environment = {
        **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
        "CCNAVI_LEARNING": "disable",
    }
    environment["CCNAVI_GUARD_CORE_FILES"] = "disable"
    environment.pop("CLAUDE_PROJECT_DIR", None)
    payload = json.dumps(
        {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}
    )
    log = os.path.join(ws, "logs", "decisions.jsonl")
    start = time.perf_counter()
    result = run_ccnavi(["--root", ws, "--mode", "enable"], input=payload, cwd=ws, env=environment)
    seconds = time.perf_counter() - start
    decision = reason = "-"
    if os.path.exists(log):
        with open(log, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        if rows:
            decision, reason = rows[-1].get("decision", "-"), rows[-1].get("reason", "-")
    return seconds, result.returncode, decision, reason


def main() -> None:
    print(f"Python {sys.version.split()[0]}  期限 {judge.DEADLINE_SECONDS:g} 秒")
    ws = workspace()
    try:
        print()
        print(f"  {'入力':<26} {'大きさ':>10} {'時間(s)':>8} {'期限比':>6}  終了 decision / reason")
        for name, tool, tool_input in cases("--big" in sys.argv):
            size = sum(len(v) for v in tool_input.values())
            seconds, code, decision, reason = one(ws, tool, tool_input)
            ratio = seconds / judge.DEADLINE_SECONDS
            mark = "  <- 期限超え" if ratio > 1 else ""
            print(
                f"  {name:<26} {size:>10,} {seconds:>8.2f} {ratio:>6.2f}  "
                f"{code:>3}  {decision} / {reason}{mark}",
                flush=True,
            )
        print()
        print("  期限比が 1 を超えた行は、期限を守れていない（hook の実行時間が期限より長い）。")
        print("  超えたのに reason が deadline-exceeded でない行は、読みや 1 回の照合の中で")
        print("  超えて、そのまま判定を終えたもの（期限の確認はルールを照合するたび）。")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    main()
