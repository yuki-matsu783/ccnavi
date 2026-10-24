"""`ccnavi --learnings [--json]`。学びの蓄積の wiki のパターンごとの状態と観測元の数を一覧にする。

指定したツリー（`--root`。既定はいまのツリー）の `docs/wiki/` を読むだけで、何も書かない。
出すもの。

- パターンごとの状態（提案中・候補・決着）と観測元の数、観測元の数が閾値以上か
- 「候補」の件数と、`target` ごとの内訳
- `承認待ち` の提案の一覧
- frontmatter が壊れたファイルの名指し（数えない）

閾値は `CCNAVI_LEARNING_SOURCES`（既定 3）。cli を読まない。
"""

from __future__ import annotations

import argparse
import json
import os
from typing import TextIO

from ..infra import settings
from ..infra.modes import EXIT_ERROR, EXIT_OK
from ..records import wiki, wiki_load
from . import cli_args

# `--json` の形の版。欄を足すだけなら上げない。
JSON_VERSION = 1


def asked(args: argparse.Namespace) -> bool:
    return bool(args.learnings)


def usage_problem(args: argparse.Namespace) -> str:
    """`--learnings` の組み合わせの誤り。正しければ空。設定を読む前に見る。"""
    refused = cli_args._not_with_docs(args, "--learnings")
    if refused:
        return f"--learnings は {refused} と一緒に使えない"
    if args.docs or args.templates:
        return f"--learnings は {'--docs' if args.docs else '--templates'} と一緒に使えない"
    for flag, name, absent in cli_args.DOCS_FLAGS:
        if getattr(args, name) != absent:
            return f"{flag} は --docs でだけ使える"
    if args.command:
        return f"--learnings にはフラグ以外の語を付けられない（{' '.join(args.command)}）"
    return ""


def summary(found: wiki.Wiki, threshold: int) -> dict:
    """一覧の中身（`--json` の形）。ファイルも時計も見ない。

    処理フロー:
    1. `wiki.rows` でパターンの表を作り、候補（`wiki.CANDIDATE`）だけを取り出す。
    2. 対象（`wiki.TARGETS`）ごとに、パターン数・候補数・到達数（`reached`）を数える。
    3. 全体の数と、パターンごとの行、`wiki.pending` の提案、壊れた記録（`found.broken`）を
       並べて返す。
    """
    # パターンの表と候補。
    table = wiki.rows(found)
    candidates = [r for r in table if r.status == wiki.CANDIDATE]
    # 対象ごとの集計。
    by_target = {}
    for target in wiki.TARGETS:
        mine = [r for r in candidates if r.target == target]
        by_target[target] = {
            "patterns": sum(1 for r in table if r.target == target),
            "candidates": len(mine),
            "reached": sum(1 for r in mine if r.reached(threshold)),
        }
    return {
        "version": JSON_VERSION,
        "wiki": found.present,
        "threshold": threshold,
        "candidates": len(candidates),
        "reached": sum(1 for r in candidates if r.reached(threshold)),
        "by_target": by_target,
        "patterns": [
            {
                "name": r.name,
                "target": r.target,
                "title": r.title,
                "status": r.status,
                "sources": r.count,
                "reached": r.reached(threshold),
            }
            for r in table
        ],
        "pending": [
            {
                "proposal": i.proposal,
                "target": i.target,
                "files": list(i.files),
                "patterns": dict(i.patterns),
            }
            for i in wiki.pending(found)
        ],
        "broken": [{"file": b.file, "kind": b.kind, "problem": b.problem} for b in found.broken],
    }


def render(stdout: TextIO, root: str, data: dict) -> None:
    """人が読む形で書く。"""
    stdout.write("ccnavi: 学びの蓄積の一覧\n")
    stdout.write(f"  ツリー: {root}\n")
    if not data["wiki"] and not data["broken"]:
        stdout.write(f"  {wiki.PATTERNS_DIR}/ も {wiki.IMPACT_DIR}/ も無い\n")
        return
    env = settings.LEARNING_SOURCES_ENV
    stdout.write(f"  観測元の閾値: {data['threshold']}（{env}。以上で提案に足りる）\n")
    stdout.write(f"  候補: {data['candidates']} 件（観測元が閾値以上 {data['reached']} 件）\n")
    stdout.write("  target ごとの候補:\n")
    for target, row in data["by_target"].items():
        stdout.write(
            f"    {target:<9} 候補 {row['candidates']} 件"
            f"（閾値以上 {row['reached']} 件）  パターン {row['patterns']} 本\n"
        )
    stdout.write("  パターン:\n")
    for row in data["patterns"]:
        mark = "  閾値以上" if row["reached"] else ""
        stdout.write(
            f"    {row['name']}  {row['target']}  {row['status']}  観測元 {row['sources']}{mark}\n"
        )
    if not data["patterns"]:
        stdout.write("    （なし）\n")
    stdout.write("  承認待ちの提案:\n")
    for item in data["pending"]:
        names = "、".join(f"{n}({c})" for n, c in item["patterns"].items())
        stdout.write(f"    {item['proposal']}  {item['target']}  動機: {names or '（なし）'}\n")
    if not data["pending"]:
        stdout.write("    （なし）\n")
    if data["broken"]:
        stdout.write("  frontmatter が壊れたファイル（数えていない）:\n")
        for item in data["broken"]:
            stdout.write(f"    {item['file']}: {item['problem']}\n")


def run(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    args: argparse.Namespace,
) -> int:
    """`--learnings`。ツリーの wiki を読んで一覧を書く。"""
    if not os.path.isdir(root):
        stderr.write(f"ccnavi: --root {root} はディレクトリではない\n")
        return EXIT_ERROR
    try:
        found = wiki_load.load_tree(root)
    except OSError as exc:
        stderr.write(f"ccnavi: wiki を読めなかった: {exc}\n")
        return EXIT_ERROR
    data = summary(found, wiki.sources_threshold(conf.learning_sources))
    if args.json:
        stdout.write(json.dumps({"root": root, **data}, ensure_ascii=False, indent=1))
        stdout.write("\n")
    else:
        render(stdout, root, data)
    return EXIT_OK
