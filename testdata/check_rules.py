"""見本をぜんぶ判定に掛けて、期待と食い違ったものを並べる。

`/rules-check` スキルが呼ぶ。人が直接叩いてもよい。

    uv run python testdata/check_rules.py

判定は `ccnavi --test` を通す。ここで判定を作り直さないのが肝で、別の道で
確かめると、見本が通ったのに実運用で落ちる、という一番まずい形になる
（REQ-DIA-03）。呼び出しごとにプロセスが 1 つ起きるので見本 30 件で数秒かかるが、
確かめているものが本物であることのほうが大事。

終了コードは、食い違いが 1 件でもあれば 1。無ければ 0。
"""

from __future__ import annotations

import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "testdata", "rule-samples.yml")

# 見本のパスに書く合言葉。走らせた場所に読み替える。見本を絶対パスで
# 書かせておかないと、判定が解いた先が走らせた場所によって変わる。
PLACEHOLDER = "/repo"


def judge(rules_path: str, tool: str, subject: str) -> tuple[str, str]:
    """1 件を試す。(判定, 当たったルールの名前) を返す。"""
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "ccnavi",
            "--root",
            ROOT,
            "--rules",
            rules_path,
            "--ledger",
            "",
            "--test",
            tool,
            subject,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        env=environment,
    )
    if done.returncode != 0:
        return "(試験が失敗)", done.stderr.strip().splitlines()[-1] if done.stderr else ""

    verdict, hit = "", []
    for line in done.stdout.splitlines():
        if line.startswith("verdict: "):
            verdict = line.removeprefix("verdict: ").split(" ")[0]
        elif line.startswith("  ") and ":" in line and ("glob " in line or "regex " in line):
            hit.append(line.strip().split("  ")[0])
    return verdict, ", ".join(hit)


def main() -> int:
    rules_path = (
        sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, ".claude", "ccnavi", "rules.yml")
    )
    with open(SAMPLES, encoding="utf-8") as f:
        samples = yaml.safe_load(f)

    mismatches = []
    skipped = []
    counts = {"deny": [0, 0], "ask": [0, 0], "allow": [0, 0]}

    for want in ("deny", "ask", "allow"):
        for case in samples.get(want) or []:
            tool = str(case.get("tool") or "")
            subject = str(case.get("subject") or "").replace(PLACEHOLDER, ROOT)
            got, hit = judge(rules_path, tool, subject)
            counts[want][1] += 1
            if got == want:
                counts[want][0] += 1
                continue
            if want == "allow" and got == "skip":
                # 判定に入らなかった。呼び出しは通るので期待は満たしているが、
                # 通した理由が「allow に当たった」ではない。ルールを書いても
                # 当たらない場所なので、食い違いとは別に数えて必ず見せる。
                counts[want][0] += 1
                skipped.append((tool, case.get("subject"), case.get("why")))
                continue
            mismatches.append((want, got, tool, case.get("subject"), case.get("why"), hit))

    for want, got, tool, subject, why, hit in mismatches:
        print(f"食い違い: {want} のはずが {got}")
        print(f"  {tool}  {subject}")
        print(f"  なぜ deny/ask/allow に置いたか: {why}")
        if hit:
            print(f"  当たったルール: {hit}")
        print()

    for tool, subject, why in skipped:
        print(f"判定に入らずに通った（allow ではない）: {tool}  {subject}")
        print(f"  {why}")
        print()

    for want, (ok, total) in counts.items():
        print(f"{want}: {ok}/{total}")
    print(f"食い違い {len(mismatches)} 件、判定に入らなかったもの {len(skipped)} 件")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
