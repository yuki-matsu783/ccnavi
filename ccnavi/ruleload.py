"""この呼び出しに当てるルール集合を決める。

ワークスペースのルールか、プロジェクトのルールか、その和か。読めなければ
組み込みの既定に落ち、落ちたことを記録に残す。判定そのものはここに無い。
"""

from __future__ import annotations

from typing import TextIO

from . import audit, builtin, hookio, phase, rules, settings, tree


def load_rules(
    stderr: TextIO, rules_path: str, record: audit.Record, root: str = ""
) -> tuple[rules.RuleSet, str]:
    """ルール集合と、それがどこから来たかを返す。

    読めなければ組み込みの既定に落ちる。「設定が読めない」は「判断できない」
    ではなく「設定が壊れている」。拒否側へ倒すと、壊れたファイルを直すための
    呼び出しまで止まって回復できなくなる。既定モードが block なので、
    ファイルを置く前に hook を登録しただけでセッションが死ぬ（REQ-PRE-06）。

    出所は、いま当てているルールがどこから来たか。既定に落ちているなら
    読めなかったファイルではない。そのファイルを名乗ると、見に行った人が
    当たったルールを見つけられない。
    """
    try:
        rule_set, problems = rules.load(rules_path, root)
    except (OSError, ValueError) as exc:
        stderr.write(f"ccnavi: ルールを読めない: {exc}\n")
        rule_set, problems = builtin.load()
        record.fallback = builtin.FALLBACK
        record.detail = rules_path
    for problem in problems:
        stderr.write(f"ccnavi: {problem}\n")
    return rule_set, builtin.SOURCE if record.fallback else rules_path


# 行き先で判定するツール。subject に解決済みのパスが入っている。
PATH_TOOLS = ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit")


def rules_for(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> tuple[rules.RuleSet, str, tree.Tree | None]:
    """この呼び出しに当てるルール集合と、その出所と、行き先のツリー（設計 §25.4）。

    パスを持つツールは行き先で 1 本に決まる。行き先のツリーがプロジェクトのものなら、
    その git プロジェクトルートにあるルールファイル。ワークスペースのツリーなら
    ワークスペースのルール。プロジェクトを数えていないワークスペースでは、いつも
    ワークスペースのルールになる。

    Bash はワークスペースと全プロジェクトのルールの和。呼び出しがどのプロジェクトの
    ものかは当てない。当てる仕掛け（cwd、cd の追跡、引数の語の走査）は「どのルール
    ファイルを引くか」にしか効かず、副作用は結局実行後の監視が拾う。和なら deny と
    ask は増える側に倒れ、緩むのは allow の共有だけになる（REQ-MLT-05）。読めない
    プロジェクトは和から外し、外したことを記録に残す（REQ-MLT-06）。
    """
    if payload.tool_name in PATH_TOOLS:
        target = tree.tree_of(root, record.subject, conf.projects)
        if target is not None and target.project:
            where = tree.project_root(conf.projects, target.project)
            rule_set, source = load_rules(
                stderr, settings.project_rules_path(conf, where), record, root
            )
            if not record.fallback:
                prefix_ids(rule_set, target.project)
            return rule_set, source, target
        rule_set, source = load_rules(stderr, conf.rules, record, root)
        return rule_set, source, target

    rule_set, source = load_rules(stderr, conf.rules, record, root)
    if payload.tool_name not in phase.SHELL_TOOLS:
        return rule_set, source, None
    skipped = []
    for p in tree.projects(conf.projects):
        path = settings.project_rules_path(conf, tree.project_root(conf.projects, p.project))
        try:
            extra, problems = rules.load(path, root)
        except (OSError, ValueError) as exc:
            stderr.write(f"ccnavi: プロジェクト {p.project} のルールを読めない: {exc}\n")
            skipped.append(p.project)
            continue
        for problem in problems:
            stderr.write(f"ccnavi: {problem}\n")
        prefix_ids(extra, p.project)
        for name in rules.SECTIONS:
            rule_set.section(name).extend(extra.section(name))
    if skipped:
        note = "unreadable project rules: " + ",".join(skipped)
        record.detail = f"{record.detail}; {note}" if record.detail else note
    return rule_set, source, None


def project_rules_files(conf: settings.Settings) -> list[tuple[str, str]]:
    """置き場にあるプロジェクトと、そのルールファイル。selfguard の守る対象に渡す。"""
    return [
        (p.name, settings.project_rules_path(conf, tree.project_root(conf.projects, p.name)))
        for p in tree.projects(conf.projects)
    ]


def prefix_ids(rule_set: rules.RuleSet, project: str) -> None:
    """プロジェクトのルールの id にプロジェクトの名前を添える。`lib:source` の形（REQ-MLT-07）。

    プロジェクトどうしで同じ id があっても衝突せず、記録を読んだ人がどのファイルを
    見に行けばよいかが id だけで分かる。ワークスペースのルールは裸の id のまま。
    """
    for name in rules.SECTIONS:
        for rule in rule_set.section(name):
            if rule.id:
                rule.id = f"{project}:{rule.id}"
