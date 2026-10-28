"""レイヤーごとの設定の読み出しと、ルールの書き方の整形。

`--explain`（diagnose_explain）、ボード（diagnose_board）、試験（diagnose_try）が共有する。
"""

from __future__ import annotations

import os

from ..infra import settings, tree
from ..policy import ruleload, rules
from ..tickets.config import phasetypes, risk


def layer_home(conf: settings.Settings, root: str, name: str) -> str:
    """そのレイヤーの git プロジェクトルート。
    共通レイヤーは持たない（ワークスペースルートを返す）。"""
    if name in (ruleload.LAYER_COMMON, ruleload.LAYER_SELF):
        return root
    return tree.project_root(conf.projects, name)


def layer_config(conf: settings.Settings, root: str, name: str, kind: str) -> str:
    """そのレイヤーの phases / risk のパス。
    共通レイヤーは `.ccnavi/config/common/` 固定で、設定が持つ既定そのもの。"""
    if name == ruleload.LAYER_COMMON:
        return conf.phases if kind == settings.KIND_PHASES else conf.risk
    return settings.layer_path(conf, layer_home(conf, root, name), kind, name)


def written(entry) -> str:
    """範囲の 1 件を、書かれた表記で出す。ユーザが書いたほう。"""
    return entry.glob or entry.regex


def layer_phase_types(path: str) -> tuple[list, str]:
    """そのレイヤーのフェーズ定義と、読めなかった理由。無いレイヤーは空。

    ここで出すのは「どのレイヤーに何が書いてあるか」で、共通レイヤーも同じ読み方を
    する。判定が使う和（共通レイヤー + 親の `project:` が指すレイヤー）は `phasetypes.layer_types`
    が組む。定義は順序を持たない（順序は親の計画のステップの `after`）。
    """
    types, why = layer_phase_set(path)
    return (list(types.values()) if types is not None else []), why


def layer_phase_set(path: str) -> tuple[phasetypes.PhaseTypes | None, str]:
    """`layer_phase_types` と同じ。定義の集合のまま返す。無ければ None。"""
    if not path or not os.path.isfile(path):
        return None, ""
    types, notes = phasetypes.load(path)
    if types is None:
        return None, "; ".join(str(n) for n in notes) or "読めない"
    return types, ""


def layer_risk(conf: settings.Settings, name: str, path: str) -> tuple[list, str]:
    """そのレイヤーのリスクの項目と、読めなかった理由。無いレイヤーは空。

    `script:` に書けるパスはレイヤーごとに違うので、読み方もレイヤーごとに分ける。
    """
    if not path or not os.path.isfile(path):
        return [], ""
    if name == ruleload.LAYER_COMMON:
        definition, _ = risk.load(path)
        if definition.fallback:
            return [], definition.fallback
        return list(definition.factors), ""
    definition, notes = risk.load_layer(path, (settings.layer_script_home(conf),))
    if definition is None:
        return [], "; ".join(str(n) for n in notes) or "読めない"
    return list(definition.factors), ""


def rule_form(rule: rules.Rule) -> dict:
    """ルールの書き方。書いた表記と、翻訳後の式。"""
    if rule.when is not None:
        # when には翻訳後の式が 1 本に定まらない。書いた形（`when` のマッピング）を添える。
        # `written` は組み合わせ条件を 1 行にした形だけにする。`kind` が `when` を言うので、
        # `written_body` の先頭の `when: ` を残すと、`kind` と並べて出す画面で
        # 「when when: …」と二重になる。
        return {
            "kind": rules.WHEN_KEY,
            "written": rule.when.describe(),
            "pattern": rule.compiled.pattern if rule.compiled else "",
            "when": rule.when.written(),
        }
    return {
        "kind": "glob" if rule.glob else "regex",
        "written": rule.glob or rule.regex,
        "pattern": rule.compiled.pattern if rule.compiled else "",
    }
