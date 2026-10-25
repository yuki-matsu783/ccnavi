"""`--lint` のうち、学びの蓄積の wiki（`docs/wiki/`）のパターンと採否の記録の検査。

ワークスペース自身と各プロジェクトのユーザのチェックアウトを見る。frontmatter が壊れた
ファイルをファイル名つきの warn で名指しする（`--learnings` はそのファイルを数えない）。
`docs/wiki/` の無いツリーでは何も言わない。
"""

from __future__ import annotations

from ..infra import settings, tree
from ..policy.rules import SEVERITY_WARN, Problem
from ..records import wiki_load


def _wiki(conf: settings.Settings, root: str) -> list[Problem]:
    problems: list[Problem] = []
    for main in [tree.main_tree(root), *tree.projects(conf.projects)]:
        found = wiki_load.load_tree(main.root)
        label = main.project or "self"
        for item in found.broken:
            what = "パターン" if item.kind == "pattern" else "採否の記録"
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    f"(wiki:{label})",
                    f"{item.file}: {what}の frontmatter を読めない（{item.problem}）。数えない",
                )
            )
    return problems
