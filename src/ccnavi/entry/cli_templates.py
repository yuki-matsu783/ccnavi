"""`ccnavi --templates`。作業・レビュー計画のテンプレートの一覧・検索・表示と、溜める側。

    ccnavi --templates [--layer <self|プロジェクト名>] [--keyword <語>]... [--json]
    ccnavi --templates --show <名前> [--layer <名前>] [--json]
    ccnavi --templates --draft <親> [--name <名前>] [--json]
    ccnavi --templates --import <下書き> --to common|project [--layer <名前>] [--replace]
                       [--yes --expect <ハッシュ>] [--json]

一覧・検索・表示は読むだけ。`--draft` は完了した親から下書きの置き場に下書きを書くだけで、
下書きに効力は無いので、どちらもエージェントが打ってよい。`--import` はユーザ専用で、`--yes` と
`--expect` が揃ったときだけ書く。エージェントが打てない形は `phase_forms._CLI_FORMS` が止める。
cli を読まない。
"""

from __future__ import annotations

import argparse
import json
from typing import TextIO

from ..infra import settings
from ..infra.modes import EXIT_ERROR, EXIT_OK
from ..policy import ruleload
from ..policy.rules import SEVERITY_ERROR, SEVERITY_INFO, SEVERITY_WARN
from ..tickets import plan_templates, template_drafts
from . import cli_args

# 値なしで渡された `--yes`。`--import` だけが読む（ほかの経路の `--yes` は識別子を取る）。
BARE_YES = "\x00bare"


def _ticket_new(args: argparse.Namespace) -> bool:
    """`ticket new` か（`--template` と `--layer` を読む経路）。"""
    return list(args.command[:2]) == ["ticket", "new"]


def asked(args: argparse.Namespace) -> bool:
    """`--templates` に関わるフラグが 1 つでも渡されたか（組み合わせの誤りを見る入口）。"""
    return bool(
        args.templates
        or args.template
        or args.show is not None
        or args.layer
        or args.draft is not None
        or args.import_ is not None
        or args.name
        or args.to
        or args.replace
    )


def _storing_problem(args: argparse.Namespace) -> str:
    """溜める側（`--draft` / `--import`）の組み合わせの誤り。"""
    if args.draft is not None and args.import_ is not None:
        return "--draft と --import は一緒に使えない"
    if args.draft is not None:
        for flag, given in (
            ("--layer", args.layer),
            ("--to", args.to),
            ("--replace", args.replace),
            ("--expect", args.expect is not None),
            ("--yes", bool(args.yes)),
        ):
            if given:
                return f"{flag} は --import でだけ使える"
        if args.show is not None or args.keyword:
            return "--draft は --show・--keyword と一緒に使えない"
        if not args.draft.strip():
            return "--draft には完了した親の識別子を渡す"
        return ""
    if args.name:
        return "--name は --draft でだけ使える"
    if args.import_ is None:
        for flag, given in (
            ("--to", args.to),
            ("--replace", args.replace),
            ("--expect", args.expect is not None),
            ("--yes", bool(args.yes)),
        ):
            if given:
                return f"{flag} は --import と一緒に使う"
        return ""
    if args.show is not None or args.keyword:
        return "--import は --show・--keyword と一緒に使えない"
    if not args.import_.strip():
        return "--import には下書きの名前かパスを渡す"
    if args.to not in template_drafts.TARGETS:
        return f"--import には --to {' か '.join(template_drafts.TARGETS)} を付ける"
    if args.layer and args.to == template_drafts.TO_COMMON:
        return "--layer は --to project のときだけ使う"
    if args.yes not in ("", BARE_YES):
        return "--yes に値は付けない（--import --yes --expect <ハッシュ>）"
    if args.yes and args.expect is None:
        return "--yes には --expect <下書きのハッシュ>（--import が見せる）を付ける"
    return ""


def usage_problem(args: argparse.Namespace) -> str:
    """`--templates` の組み合わせの誤り。正しければ空。設定を読む前に見る。"""
    if args.template and (args.templates or not _ticket_new(args)):
        return "--template は ticket new <識別子> --title <題> --template <名前> でだけ使う"
    if not args.templates:
        for flag, given in (
            ("--show", args.show is not None),
            # `ticket new --template` の `--layer` は、テンプレートを引くレイヤー。
            ("--layer", bool(args.layer) and not _ticket_new(args)),
            ("--draft", args.draft is not None),
            ("--import", args.import_ is not None),
            ("--name", bool(args.name)),
            ("--to", bool(args.to)),
            ("--replace", args.replace),
        ):
            if given:
                return f"{flag} は --templates と一緒に使う"
        return ""
    if args.docs:
        return "--templates は --docs と一緒に使えない"
    # 値なしの `--yes` は `--import` が読む。ほかの経路のフラグとは並べられない。
    probe = argparse.Namespace(**vars(args))
    if args.import_ is not None:
        probe.yes = ""
    refused = cli_args._not_with_docs(probe)
    if refused:
        return f"--templates は {refused} と一緒に使えない"
    # 絞り込みのフラグは `--docs` の物。`--keyword` だけは同じ語を使う。
    for flag, name, absent in cli_args.DOCS_FLAGS:
        if flag != "--keyword" and getattr(args, name) != absent:
            return f"{flag} は --docs でだけ使える"
    if args.command:
        return f"--templates にはフラグ以外の語を付けられない（{' '.join(args.command)}）"
    storing = _storing_problem(args)
    if storing:
        return storing
    if args.expect is not None and args.import_ is None:
        return "--expect は --import と一緒に使う"
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
    """一覧・検索・表示と、下書きの作成・取り込み。

    `--layer` を省けば、一覧と表示はワークスペース自身のレイヤー（共通 + self）。
    """
    if args.draft is not None:
        return _draft(stdout, stderr, conf, root, args)
    if args.import_ is not None:
        return _import(stdout, stderr, conf, root, args)
    layer = (args.layer or plan_templates.LAYER_SELF).strip()
    known = [view.name for view in ruleload.layers(conf, root)]
    if layer not in known:
        stderr.write(f"ccnavi: レイヤー {layer!r} は無い（{' / '.join(known)}）\n")
        return EXIT_ERROR
    scanned = plan_templates.scan(conf, root)
    if args.show is not None:
        return _show(stdout, stderr, conf, root, scanned, layer, args.show.strip(), args.json)
    keywords = [k for k in (args.keyword or []) if k.strip()]
    drafts = _drafts(conf, root, scanned, keywords)
    if args.json:
        answer = plan_templates.as_json(conf, root, scanned, layer, keywords)
        answer["drafts"] = drafts
        stdout.write(json.dumps(answer, ensure_ascii=True, indent=1))
        stdout.write("\n")
        return EXIT_OK
    return _list(stdout, conf, root, scanned, layer, keywords, len(drafts))


def _drafts(
    conf: settings.Settings, root: str, scanned: plan_templates.Scan, keywords: list[str]
) -> list[dict]:
    """下書きの答え（`drafts[]`）。効力は無いので、足し算にも `templates[]` にも入れない。"""
    found = template_drafts.scan(conf, root, scanned.candidates).drafts
    return [
        template_drafts.draft_json(d, scanned)
        for d in found
        if plan_templates.matches(d.entry, keywords)
    ]


def _list(
    stdout: TextIO,
    conf: settings.Settings,
    root: str,
    scanned: plan_templates.Scan,
    layer: str,
    keywords: list[str],
    drafts: int = 0,
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
    if drafts:
        stdout.write(f"下書き: {drafts} 件（{plan_templates.draft_dir_rel(conf)}/）\n")
    return EXIT_OK


def _draft(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    args: argparse.Namespace,
) -> int:
    """完了した親から下書きを作る（`--draft`）。上書きはしない。"""
    made = template_drafts.make(conf, root, args.draft.strip(), args.name.strip())
    if args.json:
        answer = {
            "version": plan_templates.JSON_VERSION,
            "ok": made.ok,
            "error": made.error,
            "name": made.name,
            "path": made.rel,
            "sha": made.sha,
            "tree": made.tree,
            "notes": made.notes,
            "problems": [{"severity": p.severity, "detail": p.detail} for p in made.problems],
        }
        stdout.write(json.dumps(answer, ensure_ascii=True, indent=1))
        stdout.write("\n")
        return EXIT_OK if made.ok else EXIT_ERROR
    if not made.ok:
        stderr.write(f"ccnavi: {made.error}\n")
        return EXIT_ERROR
    template = made.template
    assert template is not None
    stdout.write(f"ccnavi: 下書きを書いた: {made.rel}\n")
    stdout.write(
        f"  親: {args.draft.strip()}  全体計画 {len(template.plan)} 項 / "
        f"フィードバック計画 {len(template.feedback)} 項 / フロー {len(template.flows)} 本\n"
    )
    stdout.write(f"  ハッシュ: {made.sha}\n")
    for note in made.notes:
        stdout.write(f"  info  {note}\n")
    for problem in made.problems:
        stdout.write(f"  {_label(problem.severity)} {problem.detail}\n")
    stdout.write(
        "次: description と when を固有名の無い言い方に直し、計画とフローの不要な手順を削る。"
        "効力が無い下書きで、取り込む（使えるようにする）のはユーザ\n"
    )
    return EXIT_OK


def _import(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    args: argparse.Namespace,
) -> int:
    """下書きを取り込む（`--import`。ユーザ専用）。`--yes` と `--expect` が揃うまで何も書かない。"""
    plan = template_drafts.prepare(
        conf, root, args.import_, args.to, args.layer.strip(), args.replace
    )
    result = None
    if args.yes:
        result = template_drafts.commit(plan, args.expect or "")
    elif args.expect is not None and plan.ok and args.expect != plan.sha:
        plan.errors.append(
            "--expect が下書きの今の中身のハッシュと違う（見せたあとに下書きが書き直された）。"
            f"今のハッシュ: {plan.sha}"
        )
    ok = result.ok if result is not None else plan.ok
    if args.json:
        stdout.write(
            json.dumps(template_drafts.plan_json(plan, result), ensure_ascii=True, indent=1)
        )
        stdout.write("\n")
        return EXIT_OK if ok else EXIT_ERROR
    out = stdout if ok else stderr
    _import_text(out, stdout, plan, result)
    return EXIT_OK if ok else EXIT_ERROR


def _import_text(
    out: TextIO,
    stdout: TextIO,
    plan: template_drafts.ImportPlan,
    result: template_drafts.Imported | None,
) -> None:
    draft = plan.draft
    if draft is not None:
        stdout.write(f"下書き: {draft.rel}（{draft.tree.name or 'ワークスペースルート'}）\n")
        stdout.write(f"  ハッシュ: {plan.sha}\n")
    if plan.target_rel:
        verb = "置き換える" if plan.exists else "新しく置く"
        stdout.write(f"取り込み先: {plan.target_rel}（{plan.layer}。{verb}）\n")
    for problem in plan.problems:
        stdout.write(f"  {_label(problem.severity)} {problem.detail}\n")
    for line in plan.infos:
        stdout.write(f"  info  {line}\n")
    errors = result.errors if result is not None and result.errors else plan.errors
    for line in errors:
        out.write(f"ccnavi: {line}\n")
    if draft is None or errors:
        if draft is not None and result is None:
            stdout.write("何も書いていない。\n")
        return
    if result is None:
        _show_change(stdout, plan)
        stdout.write("何も書いていない。この内容で取り込むなら、ユーザが次を打つ。\n")
        flags = f"--import {draft.name} --to {plan.to}"
        if plan.to == template_drafts.TO_PROJECT:
            flags += f" --layer {plan.layer}"
        if plan.exists:
            flags += " --replace"
        stdout.write(f"  ccnavi --templates {flags} --yes --expect {plan.sha}\n")
        return
    stdout.write(f"取り込んだ: {plan.target_rel}\n")
    for line in result.infos:
        stdout.write(f"  info  {line}\n")
    if result.removed:
        stdout.write(f"下書き {draft.rel} を消した（取り込んだときと同じ中身だった）\n")
    elif result.kept:
        stdout.write(f"下書き {draft.rel}: {result.kept}\n")


def _show_change(stdout: TextIO, plan: template_drafts.ImportPlan) -> None:
    """取り込み先の同名との差分（文の前後つき）。新規なら取り込む本文を全部見せる。"""
    if plan.exists:
        if plan.diff:
            stdout.write(f"--- 差分（前後 {template_drafts.DIFF_CONTEXT} 行つき） ---\n")
            for line in plan.diff:
                stdout.write(line + "\n")
        return
    stdout.write("--- 取り込む本文（フローの文は案内文になるので読む） ---\n")
    text = plan.new_text
    stdout.write(text if text.endswith("\n") or not text else text + "\n")
    stdout.write("---\n")


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
