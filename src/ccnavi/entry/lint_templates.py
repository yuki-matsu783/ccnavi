"""`--lint` のうち、作業・レビュー計画のテンプレートの検査。

共通とレイヤーの置き場の `<名前>.yml` を読み、読めるか・計画が組めるか・フローが読めるか・
共通と衝突していないかを言う。検査の中身は `plan_templates` で、ここは出どころの表記を付ける。

読むのは設定の置き場（テンプレートのディレクトリ）と、各ツリーの下書きの置き場（`wip/handoff/` の
`template` の種類にヒットするもの）だけ。だから走査に `copies` を渡す必要は無い。
下書きには効力が無いので、下書きの苦情は warn と info で言う。
"""

from __future__ import annotations

from ..infra import settings
from ..policy.rules import Problem
from ..tickets import template_drafts
from ..tickets.proposal import plan_templates


def where(layer: str) -> str:
    """苦情の出どころの表記。共通は `(templates)`、レイヤーは `(templates/<名前>)`。"""
    if layer == plan_templates.LAYER_COMMON:
        return "(templates)"
    return f"(templates/{layer})"


# 下書きの苦情の出どころ。レイヤーの名前（`(templates/<名前>)`）と紛れない表記にする。
DRAFTS_WHERE = "(template-drafts)"


def _templates(conf: settings.Settings, root: str) -> list[Problem]:
    """テンプレートの苦情。置き場が無い（テンプレートを持たない）のは正常で、何も言わない。

    下書きには効力が無いので、下書きの苦情は error にせず warn と info だけで言う。
    """
    scanned = plan_templates.scan(conf, root)
    found: list[Problem] = []
    if plan_templates.has_any(scanned):
        for layer, problem in plan_templates.problems_of(scanned):
            detail = f"{problem.rule}: {problem.detail}" if problem.rule else problem.detail
            found.append(Problem(problem.severity, where(layer), detail))
    drafts = template_drafts.scan(conf, root, scanned.candidates)
    for problem in template_drafts.lint_problems(drafts):
        found.append(Problem(problem.severity, DRAFTS_WHERE, problem.detail))
    return found
