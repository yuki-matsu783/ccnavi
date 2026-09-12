"""この呼び出しに当てるルール集合を決める。

共通層に、そのツリーの層を足したものが答えになる（設計 §25.4）。足すだけで、
後ろの層が前の層を上書きしたり取り消したりすることはない。共通層が読めなければ
組み込みの既定に落ち、落ちたことを記録に残す。判定そのものはここに無い。

## 層は 3 種

- 共通層: `CCNAVI_RULES` が指すファイル。どのツリーにも効く
- 自身の層: ワークスペースルートの `<CCNAVI_PROJECT_HOME>/config/rules.yml`
- プロジェクトの層: `projects/<名前>/<CCNAVI_PROJECT_HOME>/config/rules.yml`

Write / Edit / NotebookEdit は共通層 + 行き先の層の 1 つ。Bash は共通層 + 自身の層 +
全プロジェクトの層。順は 共通層 → 自身の層 → プロジェクト（名前順）で、`deny` `ask`
`allow` の順は変わらない。

## 無い層と壊れた層

ファイルが無い層は空。不備ではないので、記録にも `--lint` にも出さない。壊れている
層も空として扱うが、そちらは記録の `fallback` に層の名前を残し、`--lint` が error で
言う。組み込みの既定へは落とさない。共通層が有るのに組み込みへ落とすと、共通層の
deny が消える側に倒れる。共通層自身が読めないときだけ、今までどおり組み込みへ落ち、
そのとき層は足さない（REQ-PRE-06）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TextIO

from . import audit, builtin, hookio, phase, rules, settings, tree
from .rules import SEVERITY_INFO, SEVERITY_WARN, Problem

# 層の名前。共通層と自身の層は固定で、プロジェクトの層はその名前を名乗る。
LAYER_COMMON = "common"
LAYER_SELF = "self"


@dataclass
class Layer:
    """共通層より後ろの層 1 つ。"""

    # name は記録と id の前置きに使う名前。`self` かプロジェクトの名前。
    name: str
    # home はその層の git プロジェクトルート。設定ファイルはこの下にある。
    home: str
    # path はこの層の rules.yml。無くてもよい（無い = 空）。
    path: str


def load_rules(
    stderr: TextIO, rules_path: str, record: audit.Record, root: str = ""
) -> tuple[rules.RuleSet, str]:
    """共通層のルール集合と、それがどこから来たかを返す。

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
    mark_source(rule_set, LAYER_COMMON)
    return rule_set, builtin.SOURCE if record.fallback else rules_path


# 行き先で判定するツール。subject に解決済みのパスが入っている。
PATH_TOOLS = ("Read", "Write", "Edit", "NotebookEdit")


def layers(conf: settings.Settings, root: str) -> list[Layer]:
    """共通層より後ろの層を、足す順に並べる。自身の層が先、プロジェクトは名前順。

    `projects/self/` は数えない。`self:id` と区別が付かないので、名前を 1 つ
    予約するほうが、接頭辞の綴りを別にするより安い（設計 §25.4）。`--lint` が
    error で言う。
    """
    found = [
        Layer(
            LAYER_SELF,
            root,
            settings.layer_path(conf, root, settings.KIND_RULES, LAYER_SELF),
        )
    ]
    for p in tree.projects(conf.projects):
        if p.name == LAYER_SELF:
            continue
        home = tree.project_root(conf.projects, p.name)
        found.append(
            Layer(p.name, home, settings.layer_path(conf, home, settings.KIND_RULES, p.name))
        )
    return found


def layer_for(conf: settings.Settings, root: str, target: tree.Tree | None) -> list[Layer]:
    """このツリーに足す層。行き先の 1 つだけ（設計 §25.4 書き込み系）。

    ワークスペースのツリー（ワークスペースルートと、そこから切った作業ツリー）なら
    自身の層。プロジェクトのツリーならその層。ワークスペースルートの外に行き先が
    あるなら、どの層でもないので何も足さない。
    """
    if target is None:
        return []
    name = target.project or LAYER_SELF
    return [layer for layer in layers(conf, root) if layer.name == name]


def rules_for(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> tuple[rules.RuleSet, str, tree.Tree | None]:
    """この呼び出しに当てるルール集合と、その出所と、行き先のツリー（設計 §25.4）。

    パスを持つツールは行き先で 1 本に決まる。共通層に、行き先のツリーの層を足す。
    行き先がプロジェクトならその層、ワークスペースのツリーなら自身の層。

    Bash は全部の和。呼び出しがどのプロジェクトのものかは当てない。当てる仕掛け
    （cwd、cd の追跡、引数の語の走査）は「どのルールファイルを引くか」にしか効かず、
    副作用は結局実行後の監視が拾う。和なら deny と ask は増える側に倒れ、緩むのは
    allow の共有だけになる（REQ-MLT-05）。読めない層は和から外し、外したことを
    記録に残す（REQ-MLT-06）。
    """
    target = None
    if payload.tool_name in PATH_TOOLS:
        target = tree.tree_of(root, record.subject, conf.projects)

    rule_set, source = load_rules(stderr, conf.rules, record, root)
    if record.fallback == builtin.FALLBACK:
        # 共通層が壊れている。層は足さない。壊れた共通層の上に層を足しても、
        # 何が効いているのかを人が読めない。
        return rule_set, source, target

    if payload.tool_name in PATH_TOOLS:
        add_layers(stderr, rule_set, layer_for(conf, root, target), root, record)
        return rule_set, source, target
    if payload.tool_name not in phase.SHELL_TOOLS:
        return rule_set, source, None
    add_layers(stderr, rule_set, layers(conf, root), root, record)
    return rule_set, source, None


def add_layers(
    stderr: TextIO,
    rule_set: rules.RuleSet,
    chosen: list[Layer],
    root: str,
    record: audit.Record,
) -> list[Problem]:
    """共通層の上に層を順に足す。壊れた層は空として扱い、記録に残す。

    返すのは層をまたいだ苦情（重複を捨てた info、同 id で中身が違う warn）。
    判定の経路は読み捨てる。呼び出しのたびに言うと、同じ話が毎回モデルへ届く。
    言う場所は `--lint`。
    """
    problems: list[Problem] = []
    broken = []
    for layer in chosen:
        if not os.path.exists(layer.path):
            # 無い層は空。不備ではないので何も言わない。
            continue
        try:
            extra, notes = rules.load(layer.path, root)
        except (OSError, ValueError) as exc:
            stderr.write(f"ccnavi: 層 {layer.name} のルールを読めない: {exc}\n")
            broken.append(layer.name)
            continue
        for note in notes:
            stderr.write(f"ccnavi: {note}\n")
        prefix_ids(extra, layer.name)
        problems.extend(merge_rules(rule_set, extra, layer.name))
    if broken:
        record.fallback = ",".join(broken)
        note = "unreadable layer: " + ",".join(broken)
        record.detail = f"{record.detail}; {note}" if record.detail else note
    return problems


def merge_rules(base: rules.RuleSet, extra: rules.RuleSet, layer: str) -> list[Problem]:
    """後ろの層を前の集合に足す。写しは捨て、同 id で中身が違うものは両方残す。

    裸の `id` が同じで `{root}` 置換後の全欄が一致する定義は、同じルールの写しと
    みなして後ろを捨てる（info）。見本を写して始めたプロジェクトが共通層と同じ行を
    持つのは普通の形で、それを衝突と呼ぶと本当の衝突が埋もれる。

    中身が違えば両方効かせる（warn）。rules は足すだけの面なので、`deny` と `ask` は
    増える側に倒れる。`allow` は広がる側だが、書き込み系では行き先の 1 層にしか
    足さないので、広がる範囲はそのツリーの中に閉じる。
    """
    problems: list[Problem] = []
    keys = {rule.key() for rule in base.all()}
    ids = {rule.bare_id for rule in base.all() if rule.bare_id}
    for name in rules.SECTIONS:
        for rule in extra.section(name):
            if rule.key() in keys:
                problems.append(
                    Problem(
                        SEVERITY_INFO,
                        rule.id,
                        f"`{rule.bare_id}` は前の層と全欄が同じなので、{layer} の側を捨てた。"
                        "判定は前の層の 1 本で行う",
                    )
                )
                continue
            if rule.bare_id in ids:
                problems.append(
                    Problem(
                        SEVERITY_WARN,
                        rule.id,
                        f"`{rule.bare_id}` は前の層と同じ id で中身が違う。両方が判定に効く。"
                        "同じ名前で違うものを指していると、記録を読んだ人がどちらの話か決められない",
                    )
                )
            keys.add(rule.key())
            ids.add(rule.bare_id)
            base.section(name).append(rule)
    return problems


@dataclass
class LayerView:
    """診断が見る層 1 つ。判定に効いているものと、効かなかった理由。"""

    name: str
    path: str
    # rule_set はこの層から実際に判定へ入ったぶん。重複で捨てた定義は入っていない。
    rule_set: rules.RuleSet
    # unreadable は読めなかった理由。空なら読めた（無い層も空として読めた扱い）。
    unreadable: str = ""
    # missing はファイルが無いこと。不備ではないので、診断は数えるだけで咎めない。
    missing: bool = False
    # problems は層をまたいだ苦情（重複の info、同 id の warn）。
    problems: list[Problem] = field(default_factory=list)


def survey(stderr: TextIO, conf: settings.Settings, root: str) -> list[LayerView]:
    """共通層と各層を、判定と同じ順・同じ読み方で読む。診断（`--explain` / `--lint`）用。

    返すのは層ごとの「実際に効いているルール」。重複で捨てた定義は入らないので、
    ここを並べたものが、Bash の和に入っているものと一致する。判定の側は
    `rules_for` を通る。読み方を 2 つ持たないために、重ね方はどちらも
    `merge_rules` の 1 本に寄せてある。
    """
    record = audit.Record()
    common, _ = load_rules(stderr, conf.rules, record, root)
    views = [LayerView(LAYER_COMMON, conf.rules, common)]
    if record.fallback == builtin.FALLBACK:
        views[0].unreadable = record.detail or conf.rules
    merged = rules.RuleSet(version=common.version)
    for name in rules.SECTIONS:
        merged.section(name).extend(common.section(name))

    for layer in layers(conf, root):
        view = LayerView(layer.name, layer.path, rules.RuleSet(version=rules.VERSION))
        views.append(view)
        if not os.path.exists(layer.path):
            view.missing = True
            continue
        if views[0].unreadable:
            # 共通層が壊れているときは層を足さない（設計 §25.2）。診断もそう見せる。
            continue
        try:
            extra, notes = rules.load(layer.path, root)
        except (OSError, ValueError) as exc:
            view.unreadable = str(exc)
            continue
        view.problems.extend(notes)
        prefix_ids(extra, layer.name)
        before = {section: len(merged.section(section)) for section in rules.SECTIONS}
        view.problems.extend(merge_rules(merged, extra, layer.name))
        for section in rules.SECTIONS:
            view.rule_set.section(section).extend(merged.section(section)[before[section] :])
    return views


def layer_files(conf: settings.Settings, root: str) -> list[tuple[str, str, str]]:
    """守る対象（selfguard）に渡す、層ごとの設定ファイル（層の名前, kind, 綴り）。

    共通層は phases と risk の 2 本だけ返す。共通層の rules は `selfguard.targets` が
    `rules_path` で受け取っているので、ここから重ねると同じファイルが 2 度並ぶ。

    自身の層とプロジェクトの層は 3 本とも返す。差し替え（`--project-rules-file`）は
    見ない。あれは診断のためのもので、守る対象は本来の置き場のほうになる。

    在るかどうかは見ない。無いファイルは selfguard が対象から外す（REQ-SLF-03）ので、
    ここで存在を確かめると、同じ判断が 2 か所に分かれる。
    """
    found = [
        (LAYER_COMMON, settings.KIND_PHASES, conf.phases),
        (LAYER_COMMON, settings.KIND_RISK, conf.risk),
    ]
    for layer in layers(conf, root):
        for kind in settings.LAYER_KINDS:
            found.append((layer.name, kind, settings.layer_real_path(conf, layer.home, kind)))
    return found


def mark_source(rule_set: rules.RuleSet, layer: str) -> None:
    """この集合のルールが、どの層から来たかを名乗らせる。記録の `source` になる。"""
    for rule in rule_set.all():
        rule.source = layer


def prefix_ids(rule_set: rules.RuleSet, layer: str) -> None:
    """層のルールの id に層の名前を添える。`self:docs` / `lib:source` の形（REQ-MLT-07）。

    層どうしで同じ id があっても記録の上では衝突せず、読んだ人がどのファイルを
    見に行けばよいかが id だけで分かる。共通層のルールは裸の id のまま。
    """
    mark_source(rule_set, layer)
    for rule in rule_set.all():
        if rule.id:
            rule.id = f"{layer}:{rule.id}"
