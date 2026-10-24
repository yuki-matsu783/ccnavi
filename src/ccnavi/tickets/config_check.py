"""レイヤーの設定 3 本（rules・phases・risks）の検査。`--lint` と `user handoff` が同じ関数を呼ぶ。

`--lint` はワークスペースの今の設定に掛け、`user handoff` は下書きを取り込んだあとの状態（設定の
置き場を下書きへ差し替えた設定）に掛ける。同じ関数を通すので、取り込みの検査が通ったのに
`--lint` が error を言う、という食い違いが起きない。

見るのは 1 つのレイヤーの 1 つの種類ずつ。レイヤーをまたぐ検査（ワークツリーにしか無いファイル、
取り込み状態と統合先のレイヤーの食い違いなど）は `--lint` の側（`entry/lint_layers`）に残す。
"""

from __future__ import annotations

import io
import os
from typing import TextIO

from ..infra import pathcase, settings, tree
from ..policy import rule_lint, ruleload
from ..policy.rules import SEVERITY_ERROR, Problem
from . import phasetypes, risk


def layer_where(name: str) -> str:
    """そのレイヤーの苦情の出どころの表記。VS Code 拡張がこの前置きでプロジェクトを引く。"""
    if name == ruleload.LAYER_COMMON:
        return "(rules)"
    if name == ruleload.LAYER_SELF:
        return "(self)"
    return f"(projects/{name})"


def layer_home(conf: settings.Settings, root: str, name: str) -> str:
    """そのレイヤーの git プロジェクトルート。自身のレイヤーならワークスペースルート。"""
    if name == ruleload.LAYER_SELF:
        return root
    return tree.project_root(conf.projects, name)


def named_rules(conf: settings.Settings, root: str) -> bool:
    """共通レイヤーのルールの置き場が、既定の場所ではなく差し替えで名指しされたものか。"""
    default = os.path.join(root, settings.DEFAULT_RULES)
    return pathcase.fold(os.path.abspath(conf.rules)) != pathcase.fold(os.path.abspath(default))


def common_rules(conf: settings.Settings, root: str) -> list[Problem]:
    """共通レイヤーのルールファイルの苦情。

    無いのは「設定が無い」正常（無い = 空）。不正なときだけ言う。差し替え（診断の `--rules`、
    取り込みの下書き）で名指しされたファイルが無いのは、置き場が空なのとは別なので言う。
    """
    if os.path.exists(conf.rules) or named_rules(conf, root):
        return rule_lint.file_problems(conf.rules, root)
    return []


def common_phases(conf: settings.Settings) -> list[Problem]:
    """共通レイヤーの `phases.yml` が読めるか。無いのは正常（無い = 空）で、何も言わない。

    読めなければ判定は共通レイヤーを空として扱い、各レイヤーの定義だけを使う。
    各レイヤーの `phases.yml` が読めるか、共通レイヤーと足せるかは `layer_configs` が見る。
    """
    _, notes = phasetypes.common_types(conf)
    return [Problem(p.severity, "(phases)", f"{p.rule}: {p.detail}") for p in notes]


def common_risk(conf: settings.Settings, root: str) -> list[Problem]:
    """共通レイヤーのリスクの配点が読めるか。無いのは不備ではない（組み込みの配点）。

    `script:` が指す先があることも見る。走らせるときは「測れなかった」で重いほうに
    なるが、そこで気づくのは子を閉じる時点になる。
    """
    if not conf.risk:
        return []
    definition, notes = risk.load(conf.risk)
    problems = [Problem(p.severity, "(risk)", f"{p.rule}: {p.detail}") for p in notes]
    if not definition.fallback:
        risk.mark_layer(definition, settings.LAYER_COMMON, root)
        problems += [
            Problem(p.severity, "(risk)", f"{p.rule}: {p.detail}")
            for p in risk.script_problems(definition)
        ]
    return problems


def layer_rules(
    stderr: TextIO, conf: settings.Settings, root: str, only: str | None = None
) -> list[Problem]:
    """レイヤーのルールファイルに食い違いが無いか。`only` を渡せばそのレイヤーだけ。

    見るのは 2 つ。レイヤーのファイルが読めることと、レイヤーをまたいだ重複と同名の衝突。
    `.ccnavi/config/project/` が無いことは言わない。
    無いのは正常（無いレイヤー = 空）で、言うと本当に言うべきものが埋もれる。

    共通レイヤーは `common_rules` が別に見ているので、ここではレイヤーの 2 つ目以降だけを回す。
    """
    problems: list[Problem] = []
    for view in ruleload.survey(stderr, conf, root)[1:]:
        if only is not None and view.name != only:
            continue
        where = layer_where(view.name)
        if view.unreadable:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    where,
                    f"{view.path} を読めない ({view.unreadable})。"
                    "このレイヤーは空として扱っている。"
                    "共通レイヤーだけで判定しているので、ここに書いた宣言は 1 件も使われていない",
                )
            )
            continue
        if view.missing:
            continue
        from_file = rule_lint.file_problems(
            view.path,
            root,
            home=layer_home(conf, root, view.name),
            layer=True,
            project=view.name != ruleload.LAYER_SELF,
        )
        for c in from_file:
            problems.append(Problem(c.severity, f"{where} {c.rule}".rstrip(), c.detail))
        # `survey` はレイヤーのファイルの苦情も `problems` に入れている。`file_problems` が同じ
        # ファイルを読んで言ったものは数えない。数えると同じ苦情が 2 度並び、件数も水増しされる。
        told = {(c.severity, c.rule, c.detail) for c in from_file}
        for c in view.problems:
            if (c.severity, c.rule, c.detail) in told:
                continue
            problems.append(Problem(c.severity, f"{where} {c.rule}".rstrip(), c.detail))
    return problems


def layer_names(conf: settings.Settings) -> list[str]:
    """自身のレイヤーと、予約名でないプロジェクトのレイヤーの名前。"""
    names = [ruleload.LAYER_SELF]
    names += [
        p.name for p in tree.projects(conf.projects) if not settings.is_reserved_layer_name(p.name)
    ]
    return names


def layer_configs(
    conf: settings.Settings, root: str, only: str | None = None, kind: str | None = None
) -> list[Problem]:
    """各レイヤーの phases / risk が、共通レイヤーと合成できるか。

    `only` を渡せばそのレイヤーだけ、`kind`（`settings.KIND_PHASES` か `KIND_RISK`）を渡せば
    その種類だけを見る。

    見るのは合成したあとの内容。phases は同 `id` で中身が違う、`title` が共通レイヤーと重なる、
    risk は `levels` が逆転する、`script:` がレイヤーの外を指すか指す先が無い、を error で言い、
    phases の全欄一致で破棄した重複を info で言う。共通レイヤー自身の苦情は `common_phases` /
    `common_risk` が別に言うので、ここではレイヤーの側だけを数える。自身のレイヤーとプロジェクトの
    レイヤーどうしは合成しない。

    `.ccnavi/config/project/` が無いことは言わない。無いのは正常（無いレイヤー = 空）。
    """
    problems: list[Problem] = []
    for name in layer_names(conf):
        if only is not None and name != only:
            continue
        where = layer_where(name)
        project = "" if name == ruleload.LAYER_SELF else name
        if kind in (None, settings.KIND_PHASES):
            _, notes = phasetypes.layer_types(conf, root, project)
            for p in notes:
                problems.append(
                    Problem(p.severity, f"{where} (phases) {p.rule}".rstrip(), p.detail)
                )
        if kind in (None, settings.KIND_RISK):
            definition, notes = risk.layer_definition(conf, root, project)
            for p in [*notes, *risk.script_problems(definition, name)]:
                problems.append(Problem(p.severity, f"{where} (risk) {p.rule}".rstrip(), p.detail))
    return problems


def file_problems(conf: settings.Settings, root: str, kind: str, layer: str) -> list[Problem]:
    """1 つのレイヤーの 1 つの種類の設定ファイルを、`--lint` と同じ水準で検査した苦情。

    `kind` は `settings.KIND_RULES` / `KIND_PHASES` / `KIND_RISK`、`layer` は `common` / `self` /
    プロジェクトの名前。読むファイルは `conf` が指す先（取り込みの検査では下書きへ差し替えてある）。
    """
    if layer == settings.LAYER_COMMON:
        if kind == settings.KIND_RULES:
            return common_rules(conf, root)
        if kind == settings.KIND_PHASES:
            return common_phases(conf)
        return common_risk(conf, root)
    if kind == settings.KIND_RULES:
        return layer_rules(io.StringIO(), conf, root, only=layer)
    return layer_configs(conf, root, only=layer, kind=kind)
