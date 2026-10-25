"""`ccnavi --templates`。作業・レビュー計画のテンプレートの一覧・検索・表示と、下書きの作成。

    ccnavi --templates [--layer <self|プロジェクト名>] [--keyword <語>]... [--json]
    ccnavi --templates --show <名前> [--layer <名前>] [--json]
    ccnavi --templates --check-file <パス> [--layer <名前>|common] [--json]
    ccnavi --templates --draft <親> [--name <名前>] [--to common|project] [--layer <名前>] [--json]

一覧・検索・表示・`--check-file` は読むだけ。`--check-file` は任意のパスのテンプレートを、
そのレイヤーのフェーズ定義で検査し、計画の図の中身（`plans`）と読んだ中身（`data`）を返す
（保存の前の確かめ用）。
`--draft` は完了した親から下書きを `wip/handoff/<取り込み先の相対パス>`
に書くだけで、下書きに効力は無いので、エージェントが打ってよい。取り込みはほかの保護対象と同じく、
ユーザが `ccnavi-user-handoff.sh` で行う（テンプレート専用の取り込み・改名・移動・削除は無い。
改名・移動・削除は、ユーザがファイルを直接操作する）。cli を読まない。
"""

from __future__ import annotations

import argparse
import json
from typing import TextIO

from ..infra import settings
from ..infra.modes import EXIT_ERROR, EXIT_OK
from ..policy import ruleload
from ..policy.rules import SEVERITY_ERROR, SEVERITY_INFO, SEVERITY_WARN
from ..tickets import handoff, plan_templates, template_drafts
from . import cli_args


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
        or args.check_file is not None
        or args.name
        or args.to
    )


def _storing_problem(args: argparse.Namespace) -> str:
    """下書きを作る側（`--draft`）の組み合わせの誤り。"""
    if args.draft is not None:
        for flag, given in (
            ("--expect", args.expect is not None),
            ("--yes", bool(args.yes)),
        ):
            if given:
                return (
                    f"{flag} は --draft と同時に指定できない（取り込みは ccnavi-user-handoff.sh）"
                )
        if args.show is not None or args.keyword:
            return "--draft は --show・--keyword と同時に指定できない"
        if not args.draft.strip():
            return "--draft には完了した親の識別子を渡す"
        if args.to and args.to not in template_drafts.TARGETS:
            return f"--to は {' か '.join(template_drafts.TARGETS)}"
        if args.layer and args.to == template_drafts.TO_COMMON:
            return "--layer は --to project のときだけ使う"
        return ""
    if args.name:
        return "--name は --draft でだけ指定できる"
    if args.to:
        return "--to は --draft でだけ指定できる"
    for flag, given in (("--expect", args.expect is not None), ("--yes", bool(args.yes))):
        if given:
            return (
                f"{flag} は --templates と同時に指定できない（取り込みは ccnavi-user-handoff.sh）"
            )
    return ""


def usage_problem(args: argparse.Namespace) -> str:
    """`--templates` の組み合わせの誤り。正しければ空。設定を読む前に見る。

    処理フロー:
    1. `--template` と `--with-feedback` は `ticket new` と一緒にだけ使えるかを見る。
    2. `--templates` が無いときは、テンプレートの一覧系フラグが付いていないかを見る。
    3. `--templates` のときは、`--docs` との併用と、`cli_args.DOCS_FLAGS` の絞り込みを見る。
       語（`command`）の付き方も見る。
    4. `--check-file` のときは、パスの有無と、一緒に使えないフラグを見る。
    5. それ以外は `_storing_problem` で保存の組み合わせを見て、`--show` の組み合わせを見る。
    """
    if args.template and (args.templates or not _ticket_new(args)):
        return "--template は ticket new <識別子> --title <タイトル> --template <名前> でだけ使う"
    if args.with_feedback and (args.templates or not _ticket_new(args) or not args.template):
        return "--with-feedback は ticket new <識別子> --title <タイトル> --template <名前> でだけ使う"
    if not args.templates:
        for flag, given in (
            ("--check-file", args.check_file is not None),
            ("--show", args.show is not None),
            # `ticket new --template` の `--layer` は、テンプレートを引くレイヤー。
            ("--layer", bool(args.layer) and not _ticket_new(args)),
            ("--draft", args.draft is not None),
            ("--name", bool(args.name)),
            ("--to", bool(args.to)),
        ):
            if given:
                return f"{flag} は --templates と一緒に使う"
        return ""
    if args.docs:
        return "--templates は --docs と同時に指定できない"
    refused = cli_args._not_with_docs(args)
    if refused:
        return f"--templates は {refused} と同時に指定できない"
    # 絞り込みのフラグは `--docs` の物。`--keyword` だけは同じ語を使う。
    for flag, name, absent in cli_args.DOCS_FLAGS:
        if flag != "--keyword" and getattr(args, name) != absent:
            return f"{flag} は --docs でだけ指定できる"
    if args.command:
        return f"--templates にはフラグ以外の語を付けられない（{' '.join(args.command)}）"
    if args.check_file is not None:
        if not args.check_file.strip():
            return "--check-file にはパスを渡す"
        for flag, given in (
            ("--show", args.show is not None),
            ("--draft", args.draft is not None),
            ("--keyword", bool(args.keyword)),
            ("--name", bool(args.name)),
            ("--to", bool(args.to)),
            ("--expect", args.expect is not None),
            ("--yes", bool(args.yes)),
        ):
            if given:
                return f"{flag} は --check-file と同時に指定できない"
        return ""
    storing = _storing_problem(args)
    if storing:
        return storing
    if args.show is not None and args.keyword:
        return "--show は --keyword と同時に指定できない"
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
    """一覧・検索・表示と、下書きの作成。

    `--layer` を省けば、一覧と表示はワークスペース自身のレイヤー（共通 + self）。
    """
    if args.draft is not None:
        return _draft(stdout, stderr, conf, root, args)
    if args.check_file is not None:
        return _check_file(stdout, stderr, conf, root, args)
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


def _check_file(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    args: argparse.Namespace,
) -> int:
    """任意のパスのテンプレートを検査する（`--check-file`）。書かない。"""
    layer = (args.layer or plan_templates.LAYER_SELF).strip()
    known = [view.name for view in ruleload.layers(conf, root)]
    if layer not in known and layer != plan_templates.LAYER_COMMON:
        return _fail(
            stdout, stderr, args.json, f"レイヤー {layer!r} はありません（{' / '.join(known)}）"
        )
    path = args.check_file.strip()
    answer = plan_templates.check_file(conf, root, path, layer)
    if args.json:
        stdout.write(json.dumps(answer, ensure_ascii=True, indent=1))
        stdout.write("\n")
        return EXIT_OK
    stdout.write(f"ccnavi: テンプレートの検査: {path}（{layer}）\n")
    stdout.write(f"  名前: {answer['name']}  読めた: {'はい' if answer['valid'] else 'いいえ'}\n")
    for said in answer["problems"]:
        stdout.write(f"  {_label(said['severity'])} {said['detail']}\n")
    return EXIT_OK


def _drafts(
    conf: settings.Settings, root: str, scanned: plan_templates.Scan, keywords: list[str]
) -> list[dict]:
    """下書きの答え（`drafts[]`）。効力は無いので、統合結果にも `templates[]` にも入れない。"""
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
        stdout.write(f"下書き: {drafts} 件（{handoff.HANDOFF_REL}/）\n")
    return EXIT_OK


def _draft(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    args: argparse.Namespace,
) -> int:
    """完了した親から下書きを作る（`--draft`）。上書きはしない。

    処理フロー:
    1. `template_drafts.make` で、親の計画から下書きを作る（取り込み先は既定でプロジェクト）。
    2. `--json` なら結果を JSON で出す。
    3. そうでなければ、失敗を出して終えるか、作った下書きの概要・問題・次の手順を出す。
       次の手順は、取り込みをユーザに頼む案内を含む。

    終了コードは、作れたら `EXIT_OK`、失敗は `EXIT_ERROR`。
    """
    made = template_drafts.make(
        conf,
        root,
        args.draft.strip(),
        args.name.strip(),
        to=args.to or template_drafts.TO_PROJECT,
        layer=args.layer.strip(),
    )
    if args.json:
        answer = {
            "version": plan_templates.JSON_VERSION,
            "ok": made.ok,
            "error": made.error,
            "name": made.name,
            "path": made.rel,
            "target": made.target,
            "layer": made.layer,
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
    stdout.write(f"  取り込み先: {made.target}（{made.layer}）\n")
    stdout.write(
        f"  親: {args.draft.strip()}  全体計画 {len(template.plan)} ステップ / "
        f"フィードバック計画 {len(template.feedback)} ステップ / フロー {len(template.flows)} 本\n"
    )
    stdout.write(f"  ハッシュ: {made.sha}\n")
    for note in made.notes:
        stdout.write(f"  info  {note}\n")
    for problem in made.problems:
        stdout.write(f"  {_label(problem.severity)} {problem.detail}\n")
    stdout.write(
        "次: description と when を固有名の無い言い方に直し、計画とフローの不要な手順を削る。"
        "効力が無い下書きで、取り込む（利用できるようにする）のはユーザ\n"
        f"案内: 取り込みは、ユーザがこのツリー（{made.tree_root}）で "
        f"`ccnavi-user-handoff.sh {made.target}` を打って行う\n"
        "案内: 下書きは未追跡のファイルで、残っていると親の ready が止まる。取り込むか、"
        "ready の前に下書きを削除します（またはコミットする）\n"
    )
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
    """テンプレート 1 つの中身を、表示する（`--show`）。読むだけで、何も書かない。

    処理フロー:
    1. `plan_templates.find` で、共通とレイヤーの中から名前で引く。無ければ失敗を出して終える。
    2. 共通のテンプレートは、いまのレイヤーの種類で `plan_templates.recheck` にかけ直す。
    3. `--json` なら、要約と本文を JSON で出す。
    4. そうでなければ、要約（説明・いつ・計画・フロー・問題）を出し、最後にファイルの中身を
       `plan_templates.reveal` を通して出す。
    """
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
