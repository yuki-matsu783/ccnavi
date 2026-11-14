"""本物の `claude -p` に決まった手を打たせ、hook に渡る JSON と stream-json の形を録る。

claude -p の代役の契約のうち、本物から録る側（仕様は `.agents/docs/claude-contract.md`）。
手動で回す。費用がかかる。
自動テストは呼ばない。

    uv run python tests/manual/claude/record_hooks.py --max-budget-usd 2
    uv run python tests/manual/claude/record_hooks.py --max-budget-usd 2 --dry-run

`--dry-run` は recorded.json を置かず、標準出力へ出す。

## やること

1. 使い捨ての木（一時ディレクトリ、`git init` 済み）に、hook の標準入力を書き出すだけの hook
   （`tests/contract/claudeside.py` の `HOOK`）を全イベントに登録した `.claude/settings.json` を
   置く。ccnavi は使わない
2. `claude -p --model haiku --setting-sources project` で 2 回回す。
   1 回目は決まった手の列（`claudeside.PROMPT`）。2 回目は `--resume` でつなぎ、
   SessionStart の `source` を見る
3. hook が受けた JSON をイベントごと（ツールのイベントはツールごと）に、stream-json を
   `type/subtype` ごとに、形へ正規化して `tests/contract/claude/recorded.json` に置く。値は持たない
4. 振る舞い（止めたツールが動いたか、理由がどこに出たか、Stop の差し戻しで続いたか、hook が並行に
   起きたか など）を `observations` に置く
5. 生の記録（値を含む）は `--raw-dir` に残す。既定はワークスペースルート直下の
   `scratchpad/claude-record/`（追跡されない）。代役を作るときに読む

置く前に、記録に機密やモデルの名前・機械のパスが入っていないかを確かめ、当たれば置かない。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, ROOT)

from tests.contract import claudeside, shape  # noqa: E402


def child_env() -> dict:
    drop = ("CLAUDE_PROJECT_DIR", "CCNAVI_")
    return {k: v for k, v in os.environ.items() if not k.startswith(drop)}


def run_claude(tree: str, prompt: str, budget: float, session: str, resume: bool) -> str:
    cmd = [
        "claude",
        "-p",
        prompt,
        "--model",
        "haiku",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-hook-events",
        "--max-budget-usd",
        str(budget),
        "--permission-mode",
        "auto",
        "--setting-sources",
        "project",
    ]
    cmd += ["--resume", session] if resume else ["--session-id", session]
    r = subprocess.run(cmd, cwd=tree, capture_output=True, text=True, timeout=900, env=child_env())
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-2000:])
        raise SystemExit(f"claude が終了コード {r.returncode} で終わった")
    return r.stdout


def claude_version() -> str:
    r = subprocess.run(["claude", "--version"], capture_output=True, text=True)
    m = re.search(r"\d+\.\d+\.\d+", r.stdout)
    return m.group(0) if m else "unknown"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--max-budget-usd",
        type=float,
        required=True,
        help="1 回の claude -p の上限（2 回回すので、合計はこの 2 倍まで）",
    )
    ap.add_argument("--raw-dir", default=os.path.join(ROOT, "scratchpad", "claude-record"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    base = tempfile.mkdtemp(prefix="ccnavi-claude-record-")
    try:
        tree = claudeside.build_tree(base)
        session = str(uuid.uuid4())
        doc = claudeside.collect(
            tree, lambda n, prompt: run_claude(tree, prompt, args.max_budget_usd, session, n > 0)
        )
        raw = doc.pop("_raw")
        os.makedirs(args.raw_dir, exist_ok=True)
        for i, t in enumerate(raw["streams"]):
            with open(
                os.path.join(args.raw_dir, f"stream-{i + 1}.jsonl"), "w", encoding="utf-8"
            ) as f:
                f.write(t)
        shutil.copy(raw["log_path"], os.path.join(args.raw_dir, "hooks.jsonl"))
        cost = sum(
            float(e.get("total_cost_usd") or 0)
            for s in raw["streams_parsed"]
            for e in s
            if e.get("type") == "result"
        )
        doc = {"source": "claude", "claude_version": claude_version(), **doc}
        text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
        # 目印（REC-）は observations のキーには入るので、形の部分だけを見る
        shapes = json.dumps({"hooks": doc["hooks"], "stream": doc["stream"]}, ensure_ascii=False)
        forbidden = claudeside.FORBIDDEN
        hits = shape.scan_forbidden(shapes, forbidden)
        hits += [h for h in shape.scan_forbidden(text, forbidden) if h != "REC の目印"]
        print(f"費用の合計: {cost:.4f} USD", file=sys.stderr)
        print(f"生の記録: {args.raw_dir}", file=sys.stderr)
        if hits:
            print(
                f"記録に入れてはいけない文字列に当たった: {sorted(set(hits))}。置かない",
                file=sys.stderr,
            )
            print(text)
            return 1
        if args.dry_run:
            print(text)
            return 0
        os.makedirs(os.path.dirname(claudeside.RECORDED), exist_ok=True)
        shape.write_atomic(claudeside.RECORDED, text)
        print(f"置いた: {os.path.relpath(claudeside.RECORDED, ROOT)}", file=sys.stderr)
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
