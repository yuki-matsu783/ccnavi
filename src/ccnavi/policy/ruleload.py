"""この呼び出しに適用するルール集合を決める。

組み込みの deny を土台に、共通レイヤーを足し、そのツリーのレイヤーを足したものが答えになる。
足すだけで、後ろのレイヤーが前のレイヤーを上書きしたり取り消したりすることは
ない。組み込みの deny（取り返しの付かない操作の止め）は、共通レイヤーと config の有無・状態に
よらず常に適用される。判定そのものはここに無い。

## レイヤーは 3 種

- 共通レイヤー: `.ccnavi/common/rules.yml`。どのツリーにも当てはまる。
  置き場は固定で、env では動かない。プロジェクトを単体で clone したときは、そのプロジェクトの
  `.ccnavi/common/`（ミラー）がこれになる。ワークスペースの中では、プロジェクトのミラーは読まない
- 自身のレイヤー: ワークスペースルートの `.ccnavi/config/rules.yml`
  （単体 clone では、そのプロジェクトの `.ccnavi/config/rules.yml`）
- プロジェクトのレイヤー: `projects/<名前>/.ccnavi/config/rules.yml`

自身のレイヤーとプロジェクトのレイヤーの置き場も固定で、env では動かない。

パスを持つツール（Read / Grep / Glob / Write / Edit / NotebookEdit）
は共通レイヤー + 行き先のレイヤーの 1 つ。
パスを持たないツール（Bash / PowerShell / WebFetch / Skill / Agent）
は共通レイヤー + 自身のレイヤー +
全プロジェクトのレイヤー。
順は 共通レイヤー → 自身のレイヤー → プロジェクト（名前順）で、`deny` `ask`
`allow` の順は変わらない。

## 無いレイヤーと破損したレイヤー

ファイルが無いレイヤー（共通レイヤーを含む）は空。不備ではないので、記録にも `--lint` にも出さない。
共通レイヤーと config が両方無ければ、組み込みの既定（`Read` を通す allow を含む）だけで判定する。
破損しているレイヤーも空として扱うが、そちらは記録の `fallback` にレイヤーの名前を残し、`--lint` が
error で言う。組み込みの既定には戻さない。共通レイヤーが在るのに組み込みへ戻すと、共通レイヤーの
deny が消える側になる。共通レイヤー自身が読めないときだけ、今までどおり組み込みの既定に戻り、
そのときレイヤーは足さない。
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from typing import TextIO

from ..infra import hookio, settings, tree
from ..records import audit
from . import builtin, rules
from .rules import SEVERITY_INFO, SEVERITY_WARN, Problem

# レイヤーの名前。共通レイヤーと自身のレイヤーは固定で、
# プロジェクトのレイヤーはプロジェクトの名前を使う。
# 実体は settings が持つ（phase / risk も同じ名前が要るが、そこは ruleload を
# import できない）。ここは読み手のための別名。予約の判断は settings にまとめてある
# （`settings.is_reserved_layer_name`）。
LAYER_COMMON = settings.LAYER_COMMON
LAYER_SELF = settings.LAYER_SELF


@dataclass
class Layer:
    """共通レイヤーより後ろのレイヤー 1 つ。"""

    # name は記録と id の前置きに使う名前。`self` かプロジェクトの名前。
    name: str
    # home はそのレイヤーの git プロジェクトルート。設定ファイルはこの下にある。
    home: str
    # path はこのレイヤーの rules.yml。無くてもよい（無い = 空）。
    path: str


def read_common(
    stderr: TextIO, conf: settings.Settings, record: audit.Record, root: str
) -> rules.RuleSet:
    """共通レイヤーのルールファイルだけを読む。組み込みの deny の土台は足さない。

    ファイルが無いのは「設定が無い」正常で、空として扱う（記録の `fallback` に残さない）。
    読めなければ組み込みの既定に戻る。「設定が読めない」は「判断できない」
    ではなく「設定が破損している」。拒否にすると、破損したファイルを直すための
    呼び出しまで止まって回復できなくなる。既定モードが block なので、
    ファイルを置く前に hook を登録しただけでセッションの呼び出しが全部止まる。
    既定は設定の全体を受け取る。守る場所のパスは設定で動くので（builtin.rule_data）。
    """
    rules_path = conf.rules
    if not os.path.exists(rules_path):
        rule_set, problems = rules.RuleSet(version=rules.VERSION), []
    else:
        try:
            rule_set, problems = rules.load(rules_path, root)
        except (OSError, ValueError) as exc:
            stderr.write(f"ccnavi: ルールを読めない: {exc}\n")
            rule_set, problems = builtin.load(root, conf)
            record.fallback = builtin.FALLBACK
            record.detail = rules_path
    for problem in problems:
        stderr.write(f"ccnavi: {problem}\n")
    mark_source(rule_set, LAYER_COMMON)
    return rule_set


def load_rules(
    stderr: TextIO, conf: settings.Settings, record: audit.Record, root: str
) -> tuple[rules.RuleSet, str]:
    """共通レイヤーのルール集合（組み込みの deny の土台つき）と、それがどこから来たかを返す。

    組み込みの deny は共通レイヤーの有無・状態によらず常に足す。共通レイヤーが
    読めずに組み込みの既定へ戻ったときは、既定が同じ deny を持つので足さない。

    出所は、いま適用しているルールがどこから来たか。既定を使っているなら
    読めなかったファイルではない。そのファイルを出所として出すと、見に行ったユーザが
    適用されたルールを見つけられない。
    """
    rule_set = read_common(stderr, conf, record, root)
    if record.fallback != builtin.FALLBACK:
        add_base(rule_set, stderr)
    return rule_set, builtin.SOURCE if record.fallback else conf.rules


def add_base(rule_set: rules.RuleSet, stderr: TextIO | None = None) -> None:
    """組み込みの deny（土台）を足す。共通レイヤーのルールの後ろ、レイヤーより前に並ぶ。"""
    base, problems = builtin.load_base()
    for problem in problems:
        if stderr is not None:
            stderr.write(f"ccnavi: {problem}\n")
    mark_source(base, LAYER_COMMON)
    rule_set.deny.extend(base.deny)


def bare(conf: settings.Settings, record: audit.Record, chosen: list[Layer]) -> bool:
    """共通レイヤーも足すレイヤーも、ルールのファイルが 1 本も無いか（組み込みの既定だけの形）。

    共通レイヤーが破損しているとき（`fallback`）は、既に組み込みの既定を使っているので当てはまらない。
    """
    if record.fallback == builtin.FALLBACK:
        return False
    return not os.path.exists(conf.rules) and not any(os.path.exists(c.path) for c in chosen)


def add_read_allow(rule_set: rules.RuleSet) -> None:
    """組み込みの既定の「`Read` を通す」allow を足す。`bare` のときだけ呼ぶ。"""
    allow = builtin.read_allow()
    mark_source(rules.RuleSet(version=rules.VERSION, allow=allow), LAYER_COMMON)
    rule_set.allow.extend(allow)


# 行き先のレイヤーで判定するツール。subject に解決済みのパスが入っている（judge.subject_of）。
# Grep と Glob は探す場所を省けば cwd。ここに無いツールは全部のレイヤーの和で判定する。
PATH_TOOLS = ("Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit")


def layers(conf: settings.Settings, root: str) -> list[Layer]:
    """共通レイヤーより後ろのレイヤーを、足す順に並べる。自身のレイヤーが先、プロジェクトは名前順。

    予約名のプロジェクト（`projects/common/` と `projects/self/`）は数えない。
    `common:id` / `self:id` と区別が付かないので、名前を 2 つ予約するほうが、
    接頭辞の表記を別にするより手間が少ない。表記違い（`projects/Self/`）も
    同じに扱う（`settings.is_reserved_layer_name`）。`--lint` が error で言う。

    数えないことは、そのプロジェクトが緩く扱われるという意味ではない。行き先の
    レイヤーを引く側（layer_for）も同じ予約を見て「レイヤー無し」を返すので、共通レイヤーだけで
    判定する。片側でしか予約を見ていないと、数えない名前で別のレイヤーの判定を
    引けてしまう。
    """
    found = [
        Layer(
            LAYER_SELF,
            root,
            settings.layer_path(conf, root, settings.KIND_RULES, LAYER_SELF),
        )
    ]
    for p in tree.projects(conf.projects):
        if settings.is_reserved_layer_name(p.name):
            continue
        home = tree.project_root(conf.projects, p.name)
        found.append(
            Layer(p.name, home, settings.layer_path(conf, home, settings.KIND_RULES, p.name))
        )
    return found


def layer_for(conf: settings.Settings, root: str, target: tree.Tree | None) -> list[Layer]:
    """このツリーに足すレイヤー。書き込み系は行き先の 1 つだけ。

    ワークスペースのツリー（ワークスペースルートと、そこから切ったワークツリー）なら
    自身のレイヤー。プロジェクトのツリーならそのレイヤー。ワークスペースルートの外に行き先が
    あるなら、どのレイヤーでもないので何も足さない。

    行き先が予約名のプロジェクト（`projects/self/` / `projects/common/`）ならレイヤー無し。
    ここを名前引きに任せてはいけない。`target.project` が `"self"` のとき、名前は
    ワークスペース自身のレイヤーの名前と一致するので、そのプロジェクトへの Write / Edit が
    プロジェクト自身の deny を一度も読まずに、ワークスペースのレイヤーのルールで判定される。
    ワークスペースのレイヤーに広い `allow` があればそれで通る。レイヤー無しなら共通レイヤーだけで
    判定するので、緩む側にはならない。`--lint` が error で名指しし、ユーザが名前を変える
    までのあいだも、この 1 行が判定のすり替えを止める。
    """
    if target is None:
        return []
    if settings.is_reserved_layer_name(target.project):
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
    """この呼び出しに適用するルール集合と、その出所と、行き先のツリー。

    パスを持つツール（PATH_TOOLS）は行き先で 1 本に決まる。
    共通レイヤーに、行き先のツリーのレイヤーを足す。
    行き先がプロジェクトならそのレイヤー、ワークスペースのツリーなら自身のレイヤー。

    パスを持たないツール（Bash / PowerShell / WebFetch / Skill / Agent）は全部の和。呼び出しが
    どのプロジェクトのものかは特定しない。Bash で特定する仕掛け（cwd、cd の追跡、引数の語の走査）は
    「どのルールファイルを引くか」にしか影響せず、副作用は結局実行後チェックが拾う。WebFetch・Skill・
    Agent は特定する材料を持たない。和なら deny と ask は増える側になり、緩むのは allow の共有だけに
    なる。読めないレイヤーは和から外し、外したことを記録に残す。
    """
    target = None
    if payload.tool_name in PATH_TOOLS:
        target = tree.tree_of(root, record.subject, conf.projects)

    rule_set, source = load_rules(stderr, conf, record, root)
    if record.fallback == builtin.FALLBACK:
        # 共通レイヤーが破損している。レイヤーは足さない。破損した共通レイヤーの上に
        # レイヤーを足しても、何が判定に使われているのかをユーザが読めない。
        return rule_set, source, target

    by_path = payload.tool_name in PATH_TOOLS
    chosen = layer_for(conf, root, target) if by_path else layers(conf, root)
    add_layers(stderr, rule_set, chosen, root, record)
    if bare(conf, record, chosen):
        # 共通レイヤーも config も無い。組み込みの既定だけで判定する（`Read` を通す allow も含む）。
        add_read_allow(rule_set)
    return rule_set, source, target


def add_layers(
    stderr: TextIO,
    rule_set: rules.RuleSet,
    chosen: list[Layer],
    root: str,
    record: audit.Record,
) -> None:
    """共通レイヤーの上にレイヤーを順に足す。破損したレイヤーは空として扱い、記録に残す。

    レイヤーをまたいだ苦情（重複を捨てた info、同 id で中身が違う warn）は読み捨てる。
    判定の経路で呼び出しのたびに言うと、同じ話が毎回モデルへ届く。言う場所は `--lint`。
    """
    broken = []
    for layer in chosen:
        if not os.path.exists(layer.path):
            # 無いレイヤーは空。不備ではないので何も言わない。
            continue
        try:
            extra, notes = rules.load(layer.path, root)
        except (OSError, ValueError) as exc:
            stderr.write(f"ccnavi: レイヤー {layer.name} のルールを読めない: {exc}\n")
            broken.append(layer.name)
            continue
        for note in notes:
            stderr.write(f"ccnavi: {note}\n")
        prefix_ids(extra, layer.name)
        merge_rules(rule_set, extra, layer.name)
    if broken:
        record.fallback = ",".join(broken)
        note = "unreadable layer: " + ",".join(broken)
        record.detail = f"{record.detail}; {note}" if record.detail else note


def merge_rules(base: rules.RuleSet, extra: rules.RuleSet, layer: str) -> list[Problem]:
    """後ろのレイヤーを前の集合に足す。重複は捨て、同 id で中身が違うものは両方残す。

    裸の `id` が同じで `{root}` 置換後の全欄が一致する定義は、同じルールの重複と
    みなして後ろを捨てる（info）。見本をコピーして始めたプロジェクトが共通レイヤーと同じ行を
    持つのは普通の形で、それを衝突と呼ぶと本当の衝突が見落とされる。

    中身が違えば両方判定に使う（warn）。rules は足すだけの設定なので、`deny` と `ask` は
    増えるほうになる。`allow` は広がる側だが、書き込み系では行き先の 1 レイヤーにしか
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
                        f"`{rule.bare_id}` は前のレイヤーと全欄が同じなので、{layer} の側を捨てた。"
                        "判定は前のレイヤーの 1 本で行う",
                    )
                )
                continue
            if rule.bare_id in ids:
                problems.append(
                    Problem(
                        SEVERITY_WARN,
                        rule.id,
                        f"`{rule.bare_id}` は前のレイヤーと同じ id で中身が違う。両方が判定に効く。"
                        "同じ名前で違うものを指していると、記録を読んだユーザがどちらの話か決められない",
                    )
                )
            keys.add(rule.key())
            ids.add(rule.bare_id)
            base.section(name).append(rule)
    return problems


@dataclass
class LayerView:
    """診断が見るレイヤー 1 つ。判定に使われているものと、使われなかった理由。"""

    name: str
    path: str
    # rule_set はこのレイヤーから実際に判定へ入ったぶん。重複で捨てた定義は入っていない。
    rule_set: rules.RuleSet
    # unreadable は読めなかった理由。空なら読めた（無いレイヤーも空として読めた扱い）。
    unreadable: str = ""
    # missing はファイルが無いこと。不備ではないので、診断は数えるだけで報告しない。
    missing: bool = False
    # problems はレイヤーをまたいだ苦情（重複の info、同 id の warn）。
    problems: list[Problem] = field(default_factory=list)


def survey(stderr: TextIO, conf: settings.Settings, root: str) -> list[LayerView]:
    """共通レイヤーと各レイヤーを、判定と同じ順・同じ読み方で読む。
    診断（`--explain` / `--lint`）用。

    返すのはレイヤーごとの「実際に判定に使われているルール」。重複で捨てた定義は入らないので、
    ここを並べたものが、Bash の和に入っているものと一致する。判定の側は
    `rules_for` を通る。読み方を 2 つ持たないために、重ね方はどちらも
    `merge_rules` の 1 本にまとめてある。
    """
    record = audit.Record()
    common = read_common(stderr, conf, record, root)
    views = [LayerView(LAYER_COMMON, conf.rules, common)]
    if record.fallback == builtin.FALLBACK:
        views[0].unreadable = record.detail or conf.rules
    elif not os.path.exists(conf.rules):
        views[0].missing = True
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
            # 共通レイヤーが破損しているときはレイヤーを足さない。診断もそう見せる。
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


def sum_view(stderr: TextIO, conf: settings.Settings, root: str, layer: Layer) -> LayerView:
    """共通レイヤーに 1 つのレイヤーを足した和を、判定と同じ読み方で読む。診断用。

    `survey` は全レイヤーを順に重ねる（先のレイヤーと全欄が同じ定義は後ろを捨てる）ので、
    後ろのプロジェクトの欄は、他のレイヤーの定義に引かれて欠ける。
    ここは「共通 + そのレイヤー」だけで重ね、パスを持つツールの判定と同じ和を返す。
    返す `rule_set` は共通レイヤーを含む和で、組み込みの deny の土台は含まない。
    """
    record = audit.Record()
    common = read_common(stderr, conf, record, root)
    merged = rules.RuleSet(version=common.version)
    for name in rules.SECTIONS:
        merged.section(name).extend(common.section(name))
    view = LayerView(layer.name, layer.path, merged)
    if record.fallback == builtin.FALLBACK:
        view.unreadable = record.detail or conf.rules
        return view
    if not os.path.exists(layer.path):
        view.missing = True
        return view
    try:
        extra, notes = rules.load(layer.path, root)
    except (OSError, ValueError) as exc:
        view.unreadable = str(exc)
        return view
    view.problems.extend(notes)
    prefix_ids(extra, layer.name)
    view.problems.extend(merge_rules(merged, extra, layer.name))
    return view


def layer_files(conf: settings.Settings, root: str) -> list[settings.LayerFile]:
    """守る対象（selfguard）に渡す、レイヤーごとの設定ファイル（種別, 名札, kind, パス）。

    共通レイヤーは phases と risk の 2 本だけ返す。共通レイヤーの rules は
    `selfguard_targets.targets` が `rules_path` で受け取っているので、ここから重ねると同じファイルが
    2 度並ぶ。

    自身のレイヤーとプロジェクトのレイヤーは 3 本とも返す。差し替え（`--project-rules-file`）は
    見ない。あれは診断のためのもので、守る対象は本来の置き場のほうになる。

    種別（`settings.ORIGIN_*`）をつける。受け取る側はバックアップの key と、戻す先の git を
    ここから決める。名札の表記では決められない。`projects/common/` の名札は
    `common` なので、名札で比べると共通レイヤーと同じ key になり、重複の排除でそのレイヤーの 3 本が
    バックアップと復元の対象からまとめて外れる。

    予約名のプロジェクトも並べる。判定のレイヤーとしては数えない（layers）が、ファイルは
    守る。`--lint` が名前を変えるよう言っているあいだも、そこに置いてある 3 本は
    ccnavi の設定ファイルで、書き換えられたら戻すほうが筋が通る。判定に使われない
    ものを守るだけなので、緩む側にはならない。

    在るかどうかは見ない。無いファイルは selfguard が対象から外すので、
    ここで存在を確かめると、同じ判断が 2 か所に分かれる。
    """
    found = [
        settings.LayerFile(settings.ORIGIN_COMMON, LAYER_COMMON, settings.KIND_PHASES, conf.phases),
        settings.LayerFile(settings.ORIGIN_COMMON, LAYER_COMMON, settings.KIND_RISK, conf.risk),
    ]
    homes = [(settings.ORIGIN_SELF, LAYER_SELF, root)]
    homes += [
        (settings.ORIGIN_PROJECT, p.name, tree.project_root(conf.projects, p.name))
        for p in tree.projects(conf.projects)
    ]
    for origin, name, home in homes:
        for kind in settings.LAYER_KINDS:
            found.append(
                settings.LayerFile(origin, name, kind, settings.layer_real_path(conf, home, kind))
            )
        if origin == settings.ORIGIN_PROJECT:
            # 共通レイヤーのミラー。ワークスペースの中では判定に読まないが、書き換えられたら
            # 戻す。
            for kind in settings.LAYER_KINDS:
                found.append(
                    settings.LayerFile(
                        settings.ORIGIN_MIRROR,
                        name,
                        kind,
                        settings.mirror_real_path(conf, home, kind),
                    )
                )
    return found


def mark_source(rule_set: rules.RuleSet, layer: str) -> None:
    """この集合のルールに、どのレイヤーから来たかを記す。記録の `source` になる。"""
    for rule in rule_set.all():
        rule.source = layer


def prefix_ids(rule_set: rules.RuleSet, layer: str) -> None:
    """レイヤーのルールの id にレイヤーの名前をつける。
    `self:docs` / `lib:source` の形。

    レイヤーどうしで同じ id があっても記録の上では衝突せず、読んだユーザがどのファイルを
    見に行けばよいかが id だけで分かる。共通レイヤーのルールは裸の id のまま。
    """
    mark_source(rule_set, layer)
    for rule in rule_set.all():
        if rule.id:
            rule.id = f"{layer}{rules.ID_SEPARATOR}{rule.id}"


def stop_rules(conf: settings.Settings, root: str) -> list[rules.Rule]:
    """ターンの終わりに適用するルール。共通レイヤーとワークスペース自身のレイヤーの `allow` だけ。

    - プロジェクトのレイヤーは見ない。
    着手で共通レイヤーをプロジェクトのレイヤーへ配ったコピーが古くなっても
      二重に数えない
    - 同じ id（レイヤーの前置きを除いた表記）は最初の 1 本だけ
    - `every` が 2 より小さいものは使わない。ターンの終わりのたびに止まる（`--lint` も言う）
    """
    picked: list[rules.Rule] = []
    seen: set[str] = set()
    for rule in _workspace_rules(conf, root).allow:
        if rule.every < 2 or not rule.matches(rules.STOP_MATCH, rules.STOP_SUBJECT):
            continue
        key = rule.bare_id or rule.id
        if key and key in seen:
            continue
        seen.add(key)
        picked.append(rule)
    return picked


def _workspace_rules(conf: settings.Settings, root: str) -> rules.RuleSet:
    """共通レイヤーとワークスペース自身のレイヤーの和。苦情は読み捨て、記録には残さない。

    判定の経路ではない場面（ターンの終わり、セッションの開始）で、ワークスペースが何を
    宣言しているかを見るため。苦情を言う場所は判定と `--lint`。共通レイヤーが読めずに
    組み込みの既定に戻ったときは、レイヤーを足さない（判定と同じ）。
    """
    said = io.StringIO()
    rule_set, source = load_rules(said, conf, audit.Record(), root)
    if source != builtin.SOURCE:
        own = [layer for layer in layers(conf, root) if layer.name == LAYER_SELF]
        add_layers(said, rule_set, own, root, audit.Record())
    return rule_set


def deny_ids(conf: settings.Settings, root: str) -> set[str]:
    """共通レイヤーとワークスペース自身のレイヤーにある deny の id（書かれたままの表記）。

    セッション開始の作業の決まり（`reasons.conventions`）が、その決まりを支えるルールを
    ワークスペースが置いているときだけ行を出すのに使う。
    """
    return {rule.bare_id or rule.id for rule in _workspace_rules(conf, root).deny if rule.id}
