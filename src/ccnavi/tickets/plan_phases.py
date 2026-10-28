"""親の提案の `phases:` に、
計画が使うフェーズ定義をコピーする（`--plan-order <親> --fill-phases`）。

計画（`plan:` / `feedback:`）のステップが使うフェーズ定義だけを `phases.yml`（共通レイヤーと、親の
`project:` が指すレイヤーの和）から読み、提案の
`phases:`（親に固定する定義のコピー）の値をその定義の集まりに差し替える（無ければ `plan:` の前に
足す。ステップが使わない定義は外す）。コピー間違いを無くし、`phases.yml` を直したあとに承認待ちの
提案を追いつかせるのも同じ 1 本で済ませるため。

改版の提案（作業中の承認済みチケットがある親）では、子が承認された番号が使う定義は承認済み
チケットのコピーから、ほかは `phases.yml` から差し込む。改版の承認が同じ規則で確かめる
（`agree_candidates.copy_config_problems`）。

## ほかのバイトは変えない

差し替えるのは `phases:` の値の行だけで、ほかの行（コメントも）と本文はそのまま残す。書く前に
組んだ全文を読み直し、`phases` のほかの欄が読んだ形で元と同じでなければ何も書かない
（`phases:` の値にアンカーがあり、ほかの欄が別名で指しているときなど）。`phases:` の値の中に
書いたコメントは消える（定義のコメントは `phases.yml` 側に書く）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from ..infra import fsio, settings
from ..policy import rules
from . import agree_candidates, plan_order
from .config import phasetypes
from .model import ticket as ticket_mod
from .model import ticket_model

_KEY_LINE = re.compile(rf"^{re.escape(phasetypes.COPY_KEY)}\s*:")
_PLAN_LINE = re.compile(r"^plan\s*:")


@dataclass
class Filled:
    """コピーした結果。`why` が空でなければ書いていない（その理由の文）。"""

    why: str = ""
    # 書き換えたか（変わらなければ偽）。
    changed: bool = False
    copied: list[str] = field(default_factory=list)
    # コピーから外した、計画が使わない定義。
    dropped: list[str] = field(default_factory=list)
    # 承認済みチケットのコピーから差し込んだ定義（子が承認された番号が使うもの）。
    held: list[str] = field(default_factory=list)


def fill(
    conf: settings.Settings,
    root: str,
    target: ticket_model.Ticket,
    current: ticket_model.Ticket | None,
) -> Filled:
    """`target`（承認待ちの親の提案）の `phases:` を、計画が使う定義に差し替えて書く。

    `current` は改版ならその親の承認済みチケット、新規なら None。
    """
    name = target.ticket
    # コピーする定義を決める計画と、書き換える本文は、
    # 同じ 1 回の読みから取る（間に提案が書き換わって、
    # 古い計画に合わせた `phases:` が新しい本文に入らないように）。
    raw = fsio.read_bytes(target.path)
    if raw is None:
        return Filled(f"{target.path} を読めない。何も反映しません。")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return Filled(f"{target.path} を UTF-8 として読めない。何も反映しません。")
    read, _ = ticket_mod.parse(text)
    if read is None or read.ticket != name or read.is_child:
        return Filled(f"{target.path} を親の提案として読めない。何も反映しません。")
    read.project = target.project
    if not read.has_plan:
        return Filled(f"{name} は計画（plan:）を持たないので、差し込む定義がありません。")
    config = phasetypes.load_types(conf, root, target.project)
    if config is None:
        return Filled(
            f"フェーズ定義（共通レイヤーと {target.project or '自身'} のレイヤーの phases.yml）が"
            "どちらも無いか読めないので、コピーできない。何も反映しません。"
        )
    held = _held(conf, root, current)
    copy: dict[str, dict] = {}
    missing: list[str] = []
    for _, item in read.numbered():
        if item.type in copy or item.type in missing:
            continue
        pt = held.get(item.type) or config.get(item.type)
        if pt is None:
            missing.append(item.type)
            continue
        copy[item.type] = phasetypes.as_copy(pt)
    if missing:
        return Filled(
            f"計画のステップが使う定義 {', '.join(missing)} が phases.yml にありません。"
            "計画を直すか、ユーザに phases.yml へ追加してもらう。何も反映しません。"
        )
    built, why = _rebuilt(text, copy)
    if why:
        return Filled(f"{target.path}: {why}。何も反映しません。")
    before = set(phasetypes.types_of(read)) if phasetypes.has_copy(read) else set()
    done = Filled(copied=list(copy), dropped=sorted(before - set(copy)), held=sorted(held))
    if built == text:
        return done
    failed = fsio.write_bytes_atomic(target.path, built.encode("utf-8"))
    if failed:
        return Filled(f"{target.path} に保存できませんでした（{failed}）")
    done.changed = True
    return done


def _held(
    conf: settings.Settings, root: str, current: ticket_model.Ticket | None
) -> dict[str, phasetypes.PhaseType]:
    """改版なら、子が承認された番号が使う定義（承認済みチケットのコピーの値）。新規なら空。"""
    if current is None:
        return {}
    types = phasetypes.types_of(current)
    held: dict[str, phasetypes.PhaseType] = {}
    for n in agree_candidates.fixed_numbers(conf, root, current.ticket):
        item = current.item_at(n)
        if item is not None and item.type in types:
            held[item.type] = types[item.type]
    return held


def _rebuilt(text: str, copy: dict[str, dict]) -> tuple[str, str]:
    """`phases:` の値だけを差し替えた全文と、書けない理由（書けるなら空）。"""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != ticket_model.FENCE:
        return "", "frontmatter が読めない"
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == ticket_model.FENCE), None)
    if end is None:
        return "", "frontmatter が閉じていない"
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    dumped = yaml.safe_dump(
        {phasetypes.COPY_KEY: copy}, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    block = [line + newline for line in dumped.rstrip("\n").split("\n")]
    head = [i for i in range(1, end) if _KEY_LINE.match(lines[i])]
    if head:
        start = head[0]
        stop = start + 1
        while stop < end and (not lines[stop].strip() or lines[stop][0] in " \t"):
            stop += 1
        # 値のあとの空行は値の外に残す。
        while stop - 1 > start and not lines[stop - 1].strip():
            stop -= 1
        # 値の中に行頭（0 桁目）のコメントがあると、字下げで値の範囲を決められない。
        after = stop
        while after < end and (not lines[after].strip() or lines[after].startswith("#")):
            after += 1
        if after > stop and after < end and lines[after][0] in " \t":
            return "", (
                "`phases:` の値の中に行頭（0 桁目）のコメントがあり、値の範囲が決まらない。"
                "コメントを字下げするか削除してから再実行します。"
            )
    else:
        plan = [i for i in range(1, end) if _PLAN_LINE.match(lines[i])]
        start = stop = plan[0] if plan else end
    built = "".join(lines[:start] + block + lines[stop:])
    why = _changed_elsewhere(text, built, copy)
    return (built, "") if not why else ("", why)


def _changed_elsewhere(old: str, new: str, copy: dict[str, dict]) -> str:
    """組んだ全文を読み直し、`phases` のほかの欄と本文が元と同じで、差し込んだ定義と合うか。"""
    after, why = plan_order.changed_elsewhere(
        old, new, (phasetypes.COPY_KEY,), f"`{phasetypes.COPY_KEY}:` の値"
    )
    if after is None:
        return why
    written, problems = phasetypes.read_copy(after.get(phasetypes.COPY_KEY))
    wanted, _ = phasetypes.read_copy(copy)
    if any(p.severity == rules.SEVERITY_ERROR for p in problems) or set(written) != set(wanted):
        return "組んだ全文の phases: が差し込んだ定義と合わないので反映しません"
    if any(not phasetypes.same(written[i], wanted[i]) for i in wanted):
        return "組んだ全文の phases: が差し込んだ定義と合わないので反映しません"
    ticket, _ = ticket_mod.parse(new)
    if ticket is None:
        return "組んだ全文がチケットとして読めないので反映しません"
    return ""
