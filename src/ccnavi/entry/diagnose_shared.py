"""レイヤーごとの設定の読み出しと、ルールの書き方の整形。

`--explain`（diagnose_explain）、ボード（diagnose_board）、試験（diagnose_try）が共有する。
合成はしない。
"""

from __future__ import annotations

import os

from ..infra import settings, tree
from ..policy import ruleload, rules
from ..tickets import (
    phasetypes,
    risk,
)


def layer_home(conf: settings.Settings, root: str, name: str) -> str:
    """そのレイヤーの git プロジェクトルート。
    共通レイヤーは持たない（ワークスペースルートを返す）。"""
    if name in (ruleload.LAYER_COMMON, ruleload.LAYER_SELF):
        return root
    return tree.project_root(conf.projects, name)


def layer_config(conf: settings.Settings, root: str, name: str, kind: str) -> str:
    """そのレイヤーの phases / risk のパス。
    共通レイヤーは `.ccnavi/common/` 固定で、設定が持つ既定そのもの。"""
    if name == ruleload.LAYER_COMMON:
        return conf.phases if kind == settings.KIND_PHASES else conf.risk
    return settings.layer_path(conf, layer_home(conf, root, name), kind, name)


def written(entry) -> str:
    """範囲の 1 件を、書かれた表記で出す。翻訳後の式ではなく、ユーザが書いたほう。"""
    return entry.glob or entry.regex


def layer_phase_types(path: str) -> tuple[list, str]:
    """そのレイヤーのフェーズの種類と、読めなかった理由。無いレイヤーは空。

    合成はしない。ここで出すのは「どのレイヤーに何が書いてあるか」で、id ごとに
    合わせた結果は判定の側（phase）が持つ。`overlap` / `requires` の参照は
    確かめない。レイヤーは共通レイヤーの種類を指してよいので、1 本だけで確かめると
    正しい定義まで「読めない」になる（設計 11.4.1）。
    """
    if not path or not os.path.isfile(path):
        return [], ""
    types, notes = phasetypes.load(path, refs=False)
    if types is None:
        return [], "; ".join(str(n) for n in notes) or "読めない"
    return list(types.values()), ""


def layer_risk(conf: settings.Settings, name: str, path: str) -> tuple[list, str]:
    """そのレイヤーのリスクの項目と、読めなかった理由。無いレイヤーは空（組み込みには戻さない）。

    `script:` に書けるパスはレイヤーごとに違う（設計 11.4.2）ので、読み方もレイヤーごとに分ける。
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
    return {
        "kind": "glob" if rule.glob else "regex",
        "written": rule.glob or rule.regex,
        "pattern": rule.compiled.pattern if rule.compiled else "",
    }
