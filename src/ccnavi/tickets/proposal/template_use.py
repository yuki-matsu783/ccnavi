"""作業・レビュー計画のテンプレートを使う側（`ccnavi ticket new <識別子> --template <名前>`）。

テンプレートから親の提案の計画と、子のフローの下書きの中身を組む。ファイルを書くのは
`scaffold`（`ticket new` の足場作り）。ここは読むだけで、書かない。

## 何をするか

- `resolve`: 共通 + 1 つのレイヤーの統合結果（`plan_templates` の走査）から、名前でテンプレートを
  引く。使えるのは統合結果で取り込まれた（`effective`）もので、error が無いもの。無い・破棄した・
  error があるなら、理由を返す
- `absolute`: テンプレートの `after`（計画の中の添字）を、提案の絶対番号に付け直す。全体計画は
  そのまま、フィードバック計画は全体計画のステップ数を足す。`template_drafts.make`（`--draft`）の
  正規化の逆
- `flow_drafts`: `flows`（全体計画のステップの添字がキー）を、子の識別子ごとの下書きの文面にする

## 子の識別子

子の識別子は `<親>-<2 桁のフェーズ番号>-<2 桁の連番>`（`ticket_ids.child_id`）。フェーズ番号は
全体計画のステップの番号。連番は承認の側がフェーズごとに 1 から数える
（`approval_ops.next_child_id`）ので、親の提案が承認される前の足場では、
そのフェーズの最初の子 `<親>-<NN>-01` と決める。子の提案は手で書く（足場は親だけ）ので、
ここで決めた識別子は予告にすぎない。別の連番で子を起こすなら、下書きの名前を子の識別子に合わせる。
"""

from __future__ import annotations

from dataclasses import dataclass

from ...policy.rules import SEVERITY_ERROR, SEVERITY_WARN
from ..model import ticket_ids
from . import plan_templates

# 子の連番は、フェーズの最初の子として決める。
FIRST_SEQ = 1


@dataclass
class Found:
    """引けたテンプレート。`warns` は使えるが言っておく苦情、`notes` は統合結果の知らせ。"""

    entry: plan_templates.Entry
    template: plan_templates.Template
    layer: str
    warns: list[str]
    notes: list[str]


def resolve(conf, root: str, layer: str, name: str) -> tuple[Found | None, str]:
    """`layer` を足した結果からテンプレートを名前で引く。使えなければ (None, 理由)。

    使えるのは、統合結果で取り込まれていて（衝突・重複で破棄されていない）、読めて、error の
    苦情が無いもの。共通とレイヤーに同名で中身が違うものが並ぶと、レイヤーの側は使われず、
    共通の側が引かれる。そのことは `notes` で言う。
    """
    scanned = plan_templates.scan(conf, root)
    if scanned.layer(layer) is None:
        known = " / ".join(view.name for view in scanned.layers) or "無い"
        return None, f"レイヤー {layer!r} はありません（{known}）"
    entry = plan_templates.find(scanned, layer, name)
    if entry is None:
        usable = sorted(e.name for e in plan_templates.adopted(scanned, layer) if e.usable)
        listed = ", ".join(usable) or "無い"
        return None, (
            f"テンプレート {name!r} がありません（{layer} で利用できるもの: {listed}）。"
            "一覧は 'ccnavi --templates'"
        )
    if entry.effective and entry.layer == plan_templates.LAYER_COMMON:
        # 共通の側は、統合結果の定義で検査し直す。
        types = plan_templates.types_for(conf, root, layer)
        entry = plan_templates.recheck(entry, types, scanned.candidates)
    if not entry.effective or entry.template is None:
        why = entry.dropped_detail or "読めない"
        return None, f"テンプレート {name!r}（{entry.rel}）は利用できない: {why}"
    errors = [p.detail for p in entry.problems if p.severity == SEVERITY_ERROR]
    if errors:
        return None, (
            f"テンプレート {name!r}（{entry.rel}）に error がある。直してから使う: "
            + " / ".join(errors)
        )
    warns = [p.detail for p in entry.problems if p.severity == SEVERITY_WARN]
    notes = [
        other.dropped_detail
        for view in scanned.layers
        for other in view.entries
        if other.name == name and other is not entry and other.dropped and other.dropped_detail
    ]
    return Found(entry, entry.template, layer, warns, notes), ""


def absolute(
    template: plan_templates.Template,
) -> tuple[list[plan_templates.Item], list[plan_templates.Item]]:
    """提案に書くステップ。

    `after` を、提案の絶対番号（フィードバック計画は全体計画の続き）に付け直す。
    """
    planned = len(template.plan)

    def shifted(items: list[plan_templates.Item], offset: int) -> list[plan_templates.Item]:
        return [
            plan_templates.Item(
                type=item.type,
                review=item.review,
                deferred=item.deferred,
                after=[n + offset for n in item.after],
            )
            for item in items
        ]

    return shifted(template.plan, 0), shifted(template.feedback, planned)


def child_id(parent: str, index: int) -> str:
    """全体計画の `index` 番目のステップの最初の子の識別子。"""
    return ticket_ids.child_id(parent, index, FIRST_SEQ)


def child_problem(parent: str, template: plan_templates.Template) -> str:
    """子の識別子が組めない理由（親の識別子が長すぎるなど）。組めれば空。"""
    for index in sorted(template.flows):
        why = ticket_ids.id_problem(child_id(parent, index))
        if why:
            return f"子の識別子 {child_id(parent, index)} が指定できない（{why}）。短い識別子にする"
    return ""


def flow_drafts(
    parent: str, template: plan_templates.Template
) -> tuple[list[tuple[str, int, str]], str]:
    """子のフローの下書き。

    [(子の識別子, 全体計画のステップの添字, 文面)]（添字順）と、組めない理由。
    """
    out: list[tuple[str, int, str]] = []
    for index in sorted(template.flows):
        text, why = plan_templates.dump_flow(template.flows[index])
        if why:
            return [], f"`flows[{index}]`: {why}"
        out.append((child_id(parent, index), index, text))
    return out, ""
