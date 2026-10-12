"""`--lint` のうち、作業・レビュー計画のテンプレートの検査。

共通とレイヤーの置き場の `<名前>.yml` を読み、読めるか・計画が組めるか・フローが読めるか・
共通と衝突していないかを言う。検査の中身は `plan_templates` で、ここは出どころの表記を付ける。

読むのは設定の置き場（テンプレートのディレクトリ）だけで、承認済みチケットの置き場は読まない。
だから走査に `copies` を渡す必要は無い（読み直しの回数は増えない）。
"""

from __future__ import annotations

from ..infra import settings
from ..policy.rules import Problem
from ..tickets import plan_templates


def where(layer: str) -> str:
    """苦情の出どころの表記。共通は `(templates)`、レイヤーは `(templates/<名前>)`。"""
    if layer == plan_templates.LAYER_COMMON:
        return "(templates)"
    return f"(templates/{layer})"


def _templates(conf: settings.Settings, root: str) -> list[Problem]:
    """テンプレートの苦情。置き場が無い（テンプレートを持たない）のは正常で、何も言わない。"""
    scanned = plan_templates.scan(conf, root)
    if not plan_templates.has_any(scanned):
        return []
    found: list[Problem] = []
    for layer, problem in plan_templates.problems_of(scanned):
        detail = f"{problem.rule}: {problem.detail}" if problem.rule else problem.detail
        found.append(Problem(problem.severity, where(layer), detail))
    return found
