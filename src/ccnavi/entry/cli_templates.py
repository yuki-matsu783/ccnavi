"""`ccnavi --templates`。作業・レビュー計画のテンプレートの一覧・検索・表示と、溜める側。

    ccnavi --templates [--layer <self|プロジェクト名>] [--keyword <語>]... [--json]
    ccnavi --templates --show <名前> [--layer <名前>] [--json]
    ccnavi --templates --draft <親> [--name <名前>] [--json]
    ccnavi --templates --import <下書き> --to common|project [--layer <名前>] [--replace]
                       [--yes --expect <ハッシュ>] [--json]
    ccnavi --templates --save-draft <名前> --body-file <ファイル>
                       [--yes --expect <ハッシュ>] [--json]
    ccnavi --templates --delete-draft <名前> [--yes --expect <ハッシュ>] [--json]
    ccnavi --templates --rename-template <名前> --new-name <新名> --on draft|common|project
                       [--layer <名前>] [--yes --expect <ハッシュ>] [--json]
    ccnavi --templates --move-template <名前> --on common|project [--layer <名前>]
                       --to common|project [--to-layer <名前>] [--yes --expect <ハッシュ>] [--json]
    ccnavi --templates --delete-template <名前> --on common|project [--layer <名前>]
                       [--yes --expect <ハッシュ>] [--json]

一覧・検索・表示は読むだけ。`--draft` は完了した親から下書きの置き場に下書きを書くだけで、
下書きに効力は無いので、エージェントが打ってよい。`--delete-draft` の下見（`--yes` なし）も
エージェントが打ってよい。`--import` はユーザ専用で、`--yes` と `--expect` が揃ったときだけ書く。
`--save-draft` `--rename-template` `--move-template` `--delete-template` もエージェントが打てない
（下見も含む。下書きの編集は Write で行う）。エージェントが打てない形は `phase_forms._CLI_FORMS`
が止める。cli を読まない。
"""

from __future__ import annotations

import argparse
import json
from typing import TextIO

from ..infra import settings
from ..infra.modes import EXIT_ERROR, EXIT_OK
from ..policy import ruleload
from ..policy.rules import SEVERITY_ERROR, SEVERITY_INFO, SEVERITY_WARN
from ..tickets import flow, plan_templates, template_drafts, template_edit
from . import cli_args

# 値なしで渡された `--yes`。取り込みと編集の旗だけが読む（ほかの経路の `--yes` は識別子を取る）。
BARE_YES = "\x00bare"

# 編集の旗（旗, 引数の属性, op）。1 度に 1 つだけ渡す。
EDIT_FLAGS = (
    ("--save-draft", "save_draft", template_edit.OP_SAVE),
    ("--delete-draft", "delete_draft", template_edit.OP_DELETE_DRAFT),
    ("--rename-template", "rename_template", template_edit.OP_RENAME),
    ("--move-template", "move_template", template_edit.OP_MOVE),
    ("--delete-template", "delete_template", template_edit.OP_DELETE),
)


def edit_op(args) -> tuple[str, str, str]:
    """渡された編集の旗 (旗, 名前, op)。無ければ ("", "", "")。"""
    for flag, attr, op in EDIT_FLAGS:
        value = getattr(args, attr)
        if value is not None:
            return flag, value, op
    return "", "", ""


def editing(args) -> bool:
    """取り込み（--import）か編集の旗が渡されたか。値なしの --yes を読む形。"""
    return args.import_ is not None or bool(edit_op(args)[0])


def _ticket_new(args: argparse.Namespace) -> bool:
    """`ticket new` か（`--template` と `--layer` を読む経路）。"""
    return list(args.command[:2]) == ["ticket", "new"]


def asked(args: argparse.Namespace) -> bool:
    """`--templates` に関わるフラグが 1 つでも渡されたか（組み合わせの誤りを見る入口）。"""
    return bool(
        args.templates
        or args.template
        or args.with_feedback
        or args.show is not None
        or args.layer
        or args.draft is not None
        or args.import_ is not None
        or args.name
        or args.to
        or args.replace
        or bool(edit_op(args)[0])
        or bool(args.new_name)
        or bool(args.on)
        or bool(args.to_layer)
    )


def _edit_problem(args: argparse.Namespace) -> str | None:
    """編集の旗（保存・削除・改名・移動）の組み合わせの誤り。編集の旗が無ければ None。

    正しければ空文字。
    """
    flag, name, op = edit_op(args)
    if not flag:
        return None
    others = [f for f, attr, _ in EDIT_FLAGS if f != flag and getattr(args, attr) is not None]
    if others:
        return (
            "--save-draft / --delete-draft / --rename-template / --move-template / "
            "--delete-template は一緒に使えない"
        )
    if args.draft is not None or args.import_ is not None:
        return f"{flag} は --draft・--import と一緒に使えない"
    if args.show is not None or args.keyword:
        return f"{flag} は --show・--keyword と一緒に使えない"
    if not name.strip():
        return f"{flag} には名前を渡す"
    if op == template_edit.OP_SAVE:
        if not args.body_file:
            return "--save-draft には --body-file <ファイル> を付ける"
        if args.new_name or args.on or args.to or args.to_layer or args.layer or args.replace:
            return "--save-draft に使えない旗がある（--body-file だけ）"
    elif op == template_edit.OP_DELETE_DRAFT:
        if (
            args.body_file
            or args.new_name
            or args.on
            or args.to
            or args.to_layer
            or args.layer
            or args.replace
        ):
            return "--delete-draft に使えない旗がある"
    elif op == template_edit.OP_RENAME:
        if args.on not in ("draft", "common", "project"):
            return "--rename-template には --on draft か common か project を付ける"
        if not args.new_name.strip():
            return "--rename-template には --new-name を付ける"
        if args.body_file or args.to or args.to_layer or args.replace:
            return "--rename-template に使えない旗がある"
        if args.layer and args.on != template_edit.ON_PROJECT:
            return "--layer は --on project のときだけ使う"
    elif op == template_edit.OP_MOVE:
        if args.on not in (template_edit.ON_COMMON, template_edit.ON_PROJECT):
            return "--move-template には --on common か project を付ける"
        if args.to not in template_drafts.TARGETS:
            return "--move-template には --to common か project を付ける"
        if args.layer and args.on != template_edit.ON_PROJECT:
            return "--layer は --on project のときだけ使う"
        if args.to_layer and args.to != template_drafts.TO_PROJECT:
            return "--to-layer は --to project のときだけ使う"
        if args.body_file or args.new_name or args.replace:
            return "--move-template に使えない旗がある"
    elif op == template_edit.OP_DELETE:
        if args.on not in (template_edit.ON_COMMON, template_edit.ON_PROJECT):
            return "--delete-template には --on common か project を付ける"
        if args.layer and args.on != template_edit.ON_PROJECT:
            return "--layer は --on project のときだけ使う"
        if args.body_file or args.new_name or args.to or args.to_layer or args.replace:
            return "--delete-template に使えない旗がある"
    if args.yes not in ("", BARE_YES):
        return f"--yes に値は付けない（{flag} --yes --expect <ハッシュ>）"
    if args.yes and args.expect is None:
        return "--yes には --expect <主語のハッシュ>（下見が見せる）を付ける"
    return ""


def _storing_problem(args: argparse.Namespace) -> str:
    """溜める側（`--draft` / `--import`）の組み合わせの誤り。"""
    edit = _edit_problem(args)
    if edit is not None:
        return edit
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
    if args.with_feedback and (args.templates or not _ticket_new(args) or not args.template):
        return "--with-feedback は ticket new <識別子> --title <題> --template <名前> でだけ使う"
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
            ("--save-draft", args.save_draft is not None),
            ("--delete-draft", args.delete_draft is not None),
            ("--rename-template", args.rename_template is not None),
            ("--move-template", args.move_template is not None),
            ("--delete-template", args.delete_template is not None),
            ("--new-name", bool(args.new_name)),
            ("--on", bool(args.on)),
            ("--to-layer", bool(args.to_layer)),
        ):
            if given:
                return f"{flag} は --templates と一緒に使う"
        return ""
    if args.docs:
        return "--templates は --docs と一緒に使えない"
    # 値なしの `--yes` は `--import` と編集の旗が読む。ほかの経路のフラグとは並べられない。
    probe = argparse.Namespace(**vars(args))
    if editing(args):
        probe.yes = ""
    # 取り込みを書く形の `--via`（board だけ）は、cli が見る。ここでは断らない。
    if editing(args) and args.yes:
        probe.via = ""
    # 保存の本文（`--body-file`）は下書きの中身で、`--docs` の断りには当たらない。
    if edit_op(args)[2] == template_edit.OP_SAVE:
        probe.body_file = ""
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
    if args.expect is not None and not editing(args):
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
    if edit_op(args)[0]:
        return _edit(stdout, stderr, conf, root, args)
    if args.draft is not None:
        return _draft(stdout, stderr, conf, root, args)
    if args.import_ is not None:
        return _import(stdout, stderr, conf, root, args)
    layer = (args.layer or plan_templates.LAYER_SELF).strip()
    known = [view.name for view in ruleload.layers(conf, root)]
    if layer not in known:
        return _fail(
            stdout, stderr, args.json, f"レイヤー {layer!r} はありません（{' / '.join(known)}）"
        )
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


def _fail(stdout: TextIO, stderr: TextIO, as_json: bool, error: str) -> int:
    """失敗の答え。`--json` なら標準出力に `{"ok": false, "error": ...}`、でなければ標準エラー。"""
    if as_json:
        answer = {"version": plan_templates.JSON_VERSION, "ok": False, "error": error}
        stdout.write(json.dumps(answer, ensure_ascii=True, indent=1))
        stdout.write("\n")
    else:
        stderr.write(f"ccnavi: {error}\n")
    return EXIT_ERROR


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
        stdout.write(
            "  該当するテンプレートはありません。\n"
            if keywords
            else "  テンプレートはありません。\n"
        )
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
        "下書きは未追跡のファイルで、残っていると親の ready が止まる。取り込むか、"
        "ready の前に下書きを削除します（またはコミットする）\n"
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
        stdout.write(f"下書き: {draft.rel}（{template_drafts.tree_label(draft.tree.name)}）\n")
        stdout.write(f"  ハッシュ: {plan.sha}\n")
    if plan.target_rel:
        verb = "置き換える" if plan.exists else "新しく置く"
        stdout.write(f"取り込み先: {plan.target_rel}（{plan.layer}。{verb}）\n")
    # 読み違えを誘う文字（右から左への上書き、幅ゼロの文字など）は `\uXXXX` にして見せる。
    for problem in plan.problems:
        stdout.write(f"  {_label(problem.severity)} {plan_templates.reveal(problem.detail)}\n")
    for line in plan.infos:
        stdout.write(f"  info  {plan_templates.reveal(line)}\n")
    errors = result.errors if result is not None and result.errors else plan.errors
    for line in errors:
        out.write(f"ccnavi: {plan_templates.reveal(line)}\n")
    if draft is None or errors:
        if draft is not None and result is None:
            stdout.write("何も反映していません。\n")
        return
    if result is None:
        _show_change(stdout, plan)
        stdout.write("何も反映していません。この内容で取り込むなら、ユーザが次を実行します。\n")
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
        stdout.write(f"下書き {draft.rel} を削除しました（取り込んだときと同じ中身だった）\n")
    elif result.kept:
        stdout.write(f"下書き {draft.rel}: {result.kept}\n")


def _edit(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    args: argparse.Namespace,
) -> int:
    """下書きの保存・削除、取り込み済みの改名・移動・削除（`--save-draft` など。ユーザ専用）。

    `--yes` と `--expect` が揃うまで何も書かない。
    """
    flag, name, op = edit_op(args)
    body = None
    if op == template_edit.OP_SAVE:
        try:
            with open(args.body_file, "rb") as f:
                # 上限の 1 バイト先まで読み、大きすぎは prepare に断らせる。
                body = f.read(flow.FILE_LIMIT + 1)
        except OSError as exc:
            return _fail(stdout, stderr, args.json, f"--body-file を読めない（{exc}）")
    on = args.on
    if op in (template_edit.OP_SAVE, template_edit.OP_DELETE_DRAFT):
        on = template_edit.ON_DRAFT
    plan = template_edit.prepare(
        conf,
        root,
        op,
        name.strip(),
        on=on,
        layer=args.layer.strip(),
        new_name=args.new_name,
        to=args.to,
        to_layer=args.to_layer.strip(),
        body=body,
    )
    result = None
    if args.yes:
        result = template_edit.commit(plan, args.expect or "")
    elif args.expect is not None and plan.ok and args.expect != plan.sha:
        plan.errors.append(
            "--expect が主語の今の中身のハッシュと違う（見せたあとに書き直された）。"
            f"今のハッシュ: {plan.sha}"
        )
    ok = result.ok if result is not None else plan.ok
    if args.json:
        stdout.write(json.dumps(template_edit.plan_json(plan, result), ensure_ascii=True, indent=1))
        stdout.write("\n")
        return EXIT_OK if ok else EXIT_ERROR
    out = stdout if ok else stderr
    _edit_text(out, stdout, plan, result, flag, args)
    return EXIT_OK if ok else EXIT_ERROR


def _edit_text(
    out: TextIO,
    stdout: TextIO,
    plan: template_edit.EditPlan,
    result: template_edit.EditResult | None,
    flag: str,
    args: argparse.Namespace,
) -> None:
    if plan.source_rel:
        stdout.write(f"{flag} {plan.name}: {plan.source_rel}\n")
    if plan.sha:
        stdout.write(f"  ハッシュ: {plan.sha}\n")
    if plan.target_rel and plan.target_rel != plan.source_rel:
        stdout.write(f"  書き先: {plan.target_rel}\n")
    # 読み違えを誘う文字は `\uXXXX` にして見せる（`prepare` が reveal 済みの文を作る）。
    for problem in plan.problems:
        stdout.write(f"  {_label(problem.severity)} {plan_templates.reveal(problem.detail)}\n")
    for line in plan.infos:
        stdout.write(f"  info  {plan_templates.reveal(line)}\n")
    errors = result.errors if result is not None and result.errors else plan.errors
    for line in errors:
        out.write(f"ccnavi: {plan_templates.reveal(line)}\n")
    if errors:
        if result is None:
            stdout.write("何も反映していません。\n")
        return
    if result is None:
        if plan.diff:
            stdout.write(f"--- 差分（前後 {template_drafts.DIFF_CONTEXT} 行つき） ---\n")
            for line in plan.diff:
                stdout.write(line + "\n")
        stdout.write("何も反映していません。この内容で実行するなら、ユーザが次を実行します。\n")
        stdout.write(
            f"  ccnavi --templates {_edit_tokens(flag, plan, args)} --yes --expect {plan.sha}\n"
        )
        return
    if plan.op == template_edit.OP_SAVE:
        stdout.write(f"書いた: {plan.target_rel}\n")
    elif plan.op in (template_edit.OP_RENAME, template_edit.OP_MOVE):
        stdout.write(f"書いた: {plan.target_rel}（元の {plan.source_rel} を削除しました）\n")
    else:
        stdout.write(f"削除した: {plan.source_rel}\n")
    for line in result.infos:
        stdout.write(f"  info  {line}\n")


def _edit_tokens(flag: str, plan: template_edit.EditPlan, args: argparse.Namespace) -> str:
    """下見の答えを書き込みの形に直した旗の並び（`--yes --expect` の前まで）。"""
    tokens = [flag, plan.name]
    if plan.op == template_edit.OP_SAVE:
        tokens += ["--body-file", args.body_file]
    for flag_name, value in (
        ("--on", args.on),
        ("--new-name", args.new_name),
        ("--layer", args.layer),
        ("--to", args.to),
        ("--to-layer", args.to_layer),
    ):
        if value:
            tokens += [flag_name, value]
    return " ".join(tokens)


def _show_change(stdout: TextIO, plan: template_drafts.ImportPlan) -> None:
    """取り込み先の同名との差分（文の前後つき）。新規なら取り込む本文を全部見せる。

    `plan.diff` と `plan.new_text` は、制御文字・書式文字を `\\uXXXX` に直してある（`prepare`）。
    """
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
        return _fail(
            stdout, stderr, as_json, f"テンプレート {name!r} はありません（共通 + {layer}）"
        )
    if entry.layer == plan_templates.LAYER_COMMON:
        types = plan_templates.types_for(conf, root, layer)
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
    text = plan_templates.reveal(text)
    stdout.write(text if text.endswith("\n") or not text else text + "\n")
    return EXIT_OK
