"""`ccnavi --templates`。作業・レビュー計画のテンプレートの一覧・検索・表示（読むだけ）。

    ccnavi --templates [--layer <self|プロジェクト名>] [--keyword <語>]... [--json]
    ccnavi --templates --show <名前> [--layer <名前>] [--json]

読むだけで何も書かないので、エージェントが打ってよい。cli を読まない。
"""

from __future__ import annotations

import argparse
import json
from typing import TextIO

from ..infra import settings
from ..infra.modes import EXIT_ERROR, EXIT_OK
from ..policy import ruleload
from ..policy.rules import SEVERITY_ERROR, SEVERITY_INFO, SEVERITY_WARN
from ..tickets import plan_templates
from . import cli_args


def usage_problem(args: argparse.Namespace) -> str:
    """`--templates` の組み合わせの誤り。正しければ空。設定を読む前に見る。"""
    if not args.templates:
        if args.show is not None:
            return "--show は --templates と一緒に使う"
        if args.layer:
            return "--layer は --templates と一緒に使う"
        return ""
    if args.docs:
        return "--templates は --docs と一緒に使えない"
    refused = cli_args._not_with_docs(args)
    if refused:
        return f"--templates は {refused} と一緒に使えない"
    # 絞り込みのフラグは `--docs` の物。`--keyword` だけは同じ語を使う。
    for flag, name, absent in cli_args.DOCS_FLAGS:
        if flag != "--keyword" and getattr(args, name) != absent:
            return f"{flag} は --docs でだけ使える"
    if args.command:
        return f"--templates にはフラグ以外の語を付けられない（{' '.join(args.command)}）"
    if args.show is not None and args.keyword:
        return "--show は --keyword と一緒に使えない"
    if args.show is not None and not args.show.strip():
        return "--show には名前を渡す"
    return ""


def run(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    args: argparse.Namespace,
) -> int:
    """一覧・検索・表示。`--layer` を省けばワークスペース自身のレイヤー（共通 + self）。"""
    layer = (args.layer or plan_templates.LAYER_SELF).strip()
    known = [view.name for view in ruleload.layers(conf, root)]
    if layer not in known:
        stderr.write(f"ccnavi: レイヤー {layer!r} は無い（{' / '.join(known)}）\n")
        return EXIT_ERROR
    scanned = plan_templates.scan(conf, root)
    if args.show is not None:
        return _show(stdout, stderr, conf, root, scanned, layer, args.show.strip(), args.json)
    keywords = [k for k in (args.keyword or []) if k.strip()]
    if args.json:
        answer = plan_templates.as_json(conf, root, scanned, layer, keywords)
        stdout.write(json.dumps(answer, ensure_ascii=True, indent=1))
        stdout.write("\n")
        return EXIT_OK
    return _list(stdout, conf, root, scanned, layer, keywords)


def _list(
    stdout: TextIO,
    conf: settings.Settings,
    root: str,
    scanned: plan_templates.Scan,
    layer: str,
    keywords: list[str],
) -> int:
    answer = plan_templates.as_json(conf, root, scanned, layer, keywords)
    rows = answer["templates"]
    stdout.write(f"ccnavi: テンプレート（共通 + {layer}）\n")
    if not rows:
        stdout.write("  該当するテンプレートは無い\n" if keywords else "  テンプレートは無い\n")
    for row in rows:
        mark = ""
        if row["dropped"]:
            mark = f"（使わない: {row['dropped']['reason']}）"
        elif not row["usable"]:
            mark = "（error あり）"
        summary = row["description"]
        if not row["valid"] and row["dropped"]:
            summary = row["dropped"]["detail"]
        stdout.write(f"  {row['name']}  [{row['layer']}]{mark}  {summary}\n")
        if row["when"]:
            stdout.write(f"      いつ: {row['when']}\n")
    # 置き場の苦情と、読めなかったものは、一覧に載せない理由として最後に言う。
    for view in answer["layers"]:
        if view["name"] not in (plan_templates.LAYER_COMMON, layer):
            continue
        for said in view["problems"]:
            stdout.write(f"  {_label(said['severity'])} {view['name']}: {said['detail']}\n")
    return EXIT_OK


def _label(severity: str) -> str:
    return {SEVERITY_ERROR: "error", SEVERITY_WARN: "warn ", SEVERITY_INFO: "info "}.get(
        severity, severity
    )


def _show(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    scanned: plan_templates.Scan,
    layer: str,
    name: str,
    as_json: bool,
) -> int:
    entry = plan_templates.find(scanned, layer, name)
    if entry is None:
        stderr.write(f"ccnavi: テンプレート {name!r} は無い（共通 + {layer}）\n")
        return EXIT_ERROR
    if entry.layer == plan_templates.LAYER_COMMON:
        types = plan_templates._types_for(conf, root, layer)
        entry = plan_templates.recheck(entry, types, scanned.candidates)
    row = plan_templates.entry_json(entry)
    text = entry.raw.decode("utf-8-sig", errors="replace")
    if as_json:
        answer = {
            "version": plan_templates.JSON_VERSION,
            "root": root,
            "layer": layer,
            "template": {**row, "text": text},
        }
        stdout.write(json.dumps(answer, ensure_ascii=True, indent=1))
        stdout.write("\n")
        return EXIT_OK
    stdout.write(f"テンプレート: {row['name']}（{row['layer']}）\n")
    stdout.write(f"  置き場: {row['path']}\n")
    if row["sha"]:
        stdout.write(f"  ハッシュ: {row['sha']}\n")
    if row["dropped"]:
        stdout.write(f"  使わない: {row['dropped']['detail']}\n")
    if row["description"]:
        stdout.write(f"  説明: {row['description']}\n")
    if row["when"]:
        stdout.write(f"  いつ: {row['when']}\n")
    for title, key in (("全体計画", "plan"), ("フィードバック計画", "feedback")):
        if not row[key]:
            continue
        stdout.write(f"  {title}:\n")
        for item in row[key]:
            extra = []
            if item["after"]:
                extra.append("after " + ", ".join(str(n) for n in item["after"]))
            if item["review"]:
                extra.append(f"review {item['review']}")
            tail = f"（{'; '.join(extra)}）" if extra else ""
            stdout.write(f"    {item['index']}. {item['type']}{tail}\n")
    if row["flows"]:
        stdout.write("  フロー:\n")
        for flow in row["flows"]:
            label = f" {flow['name']}" if flow["name"] else ""
            stdout.write(
                f"    全体計画の {flow['index']} 番目:{label}（ノード {flow['nodes']}、"
                f"線 {flow['connections']}）\n"
            )
    for said in row["problems"]:
        stdout.write(f"  {_label(said['severity'])} {said['detail']}\n")
    stdout.write("--- ファイルの中身 ---\n")
    stdout.write(text if text.endswith("\n") or not text else text + "\n")
    return EXIT_OK
