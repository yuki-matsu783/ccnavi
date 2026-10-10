"""親の提案の計画を書き換える補助のフラグ `ccnavi --plan-order <親> ...`。

いまあるのは `--fill-phases` だけ。計画（`plan:` / `feedback:`）の項が使うフェーズ定義だけを
`phases.yml` から読み、提案の `phases:`（親に固定する定義の写し）の値をその定義の集まりに
差し替える（無ければ `plan:` の前に足す。項が使わない定義は外す）。写し間違いを無くし、
`phases.yml` を直したあとに承認待ちの提案を追いつかせるのも同じ 1 本で済ませるため（設計 9.7）。

改版の提案（作業中の承認済みチケットがある親）では、子が承認された番号が使う定義は承認済み
チケットの写しから、ほかは `phases.yml` から差し込む。改版の承認が同じ規則で確かめる
（`agree_candidates.copy_config_problems`）。

## エージェントが打ってよい

`--agree` の外の独立したフラグにしてある。書くのは `todo/` の親の提案（エージェントも書ける
置き場）だけで、承認はしない。組み込みの止め（`phase_forms._CLI_FORMS`）は `--agree` を含む
呼び出しを止めるので、`--agree` の副フラグにすると止めに当たる。`entry/cli.py` は `--agree` の
端末の確かめより手前でここへ分ける。書き換えたあとの承認はダイジェストで縛られる。

## ほかのバイトは変えない

差し替えるのは `phases:` の値の行だけで、ほかの行（コメントも）と本文はそのまま残す。書く前に
組んだ全文を読み直し、`phases` のほかの欄が読んだ形で元と同じでなければ何も書かない
（`phases:` の値にアンカーがあり、ほかの欄が別名で指しているときなど）。`phases:` の値の中に
書いたコメントは消える（定義のコメントは `phases.yml` 側に書く）。
"""

from __future__ import annotations

import re
from typing import TextIO

import yaml

from ..infra import fsio, settings, yamlread
from ..infra.modes import EXIT_ERROR, EXIT_OK
from ..policy import rules
from ..tickets import agree_candidates, approval, approval_checks, phase, phasetypes, ticket_model
from ..tickets import ticket as ticket_mod

_KEY_LINE = re.compile(rf"^{re.escape(phasetypes.COPY_KEY)}\s*:")
_PLAN_LINE = re.compile(r"^plan\s*:")


def fill_phases(
    stdout: TextIO, stderr: TextIO, conf: settings.Settings, root: str, name: str
) -> int:
    """`--plan-order <親> --fill-phases`。書けたか変わらなければ 0、書かなければ 1。"""
    target = _proposal(conf, root, name)
    if target is None:
        stderr.write(
            f"ccnavi: {name} の親の提案が承認待ちの置き場（{conf.tickets}/{ticket_model.TODO}/）に"
            "無い。--plan-order には承認待ちの親の識別子を渡す\n"
        )
        return EXIT_ERROR
    if not target.has_plan:
        stderr.write(f"ccnavi: {name} は計画（plan:）を持たないので、差し込む定義が無い\n")
        return EXIT_ERROR
    config = phase.load_types(conf, root, target.project)
    if config is None:
        stderr.write(
            f"ccnavi: フェーズ定義（{target.project or '自身'} のレイヤーの phases.yml）が"
            "無いか読めないので、写せない。何も書かない\n"
        )
        return EXIT_ERROR
    held = _held(conf, root, target)
    copy: dict[str, dict] = {}
    missing: list[str] = []
    for _, item in target.numbered():
        if item.type in copy or item.type in missing:
            continue
        pt = held.get(item.type) or config.get(item.type)
        if pt is None:
            missing.append(item.type)
            continue
        copy[item.type] = phasetypes.as_copy(pt)
    if missing:
        stderr.write(
            f"ccnavi: 計画の項が使う定義 {', '.join(missing)} が phases.yml に無い。"
            "計画を直すか、ユーザに phases.yml へ足してもらう。何も書かない\n"
        )
        return EXIT_ERROR
    raw = fsio.read_bytes(target.path)
    if raw is None:
        stderr.write(f"ccnavi: {target.path} を読めない。何も書かない\n")
        return EXIT_ERROR
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        stderr.write(f"ccnavi: {target.path} を UTF-8 として読めない。何も書かない\n")
        return EXIT_ERROR
    built, why = _rebuilt(text, copy)
    if why:
        stderr.write(f"ccnavi: {target.path}: {why}。何も書かない\n")
        return EXIT_ERROR
    before = set(phasetypes.types_of(target)) if phasetypes.has_copy(target) else set()
    dropped = sorted(before - set(copy))
    if built == text:
        stdout.write(f"{name} の phases: は計画が使う定義と同じ。変えない\n")
        return EXIT_OK
    failed = fsio.write_bytes_atomic(target.path, built.encode("utf-8"))
    if failed:
        stderr.write(f"ccnavi: {target.path} に書けない（{failed}）\n")
        return EXIT_ERROR
    line = f"{name} の phases: に {', '.join(copy)} を書いた"
    if dropped:
        line += f"（使わない定義を外した: {', '.join(dropped)}）"
    if held:
        names = ", ".join(sorted(held))
        line += f"。子が承認された番号の定義（{names}）は承認済みチケットから写した"
    stdout.write(line + "\n")
    return EXIT_OK


def _proposal(conf: settings.Settings, root: str, name: str) -> ticket_model.Ticket | None:
    """承認待ち（`todo/`）の親の提案。承認と同じ集め方で、本物とするツリーの側を採る。"""
    proposals, _ = approval.scan_proposals(conf, root)
    for t in proposals:
        if t.ticket == name and t.state == ticket_model.TODO and not t.is_child and t.path:
            return t
    return None


def _held(
    conf: settings.Settings, root: str, target: ticket_model.Ticket
) -> dict[str, phasetypes.PhaseType]:
    """改版なら、子が承認された番号が使う定義（承認済みチケットの写しの値）。新規なら空。"""
    copies, _ = approval.scan(conf, root)
    current = approval_checks.by_id(copies).get(target.ticket)
    if current is None or current.is_child:
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
    else:
        plan = [i for i in range(1, end) if _PLAN_LINE.match(lines[i])]
        start = stop = plan[0] if plan else end
    built = "".join(lines[:start] + block + lines[stop:])
    why = _changed_elsewhere(text, built, copy)
    return (built, "") if not why else ("", why)


def _changed_elsewhere(old: str, new: str, copy: dict[str, dict]) -> str:
    """組んだ全文を読み直し、`phases` のほかの欄と本文が元と同じか。違えば理由。"""
    try:
        before = _front(old)
        after = _front(new)
    except (yaml.YAMLError, ValueError) as exc:
        return (
            f"組んだ全文を読み直せない（{exc}）。`phases:` の値をほかの欄が別名で"
            "指していないか確かめる"
        )
    if before is None or after is None:
        return "組んだ全文の frontmatter が読めない"
    rest_before = {k: v for k, v in before.items() if k != phasetypes.COPY_KEY}
    rest_after = {k: v for k, v in after.items() if k != phasetypes.COPY_KEY}
    if rest_before != rest_after:
        return (
            "差し替えると phases のほかの欄が変わる（`phases:` の値にアンカーがあり、ほかの欄が"
            "別名で指しているなど）ので書かない"
        )
    if old.split(ticket_model.FENCE, 2)[2:] != new.split(ticket_model.FENCE, 2)[2:]:
        return "差し替えると本文が変わるので書かない"
    written, problems = phasetypes.read_copy(after.get(phasetypes.COPY_KEY))
    wanted, _ = phasetypes.read_copy(copy)
    if any(p.severity == rules.SEVERITY_ERROR for p in problems) or set(written) != set(wanted):
        return "組んだ全文の phases: が差し込んだ定義と合わないので書かない"
    if any(not phasetypes.same(written[i], wanted[i]) for i in wanted):
        return "組んだ全文の phases: が差し込んだ定義と合わないので書かない"
    ticket, _ = ticket_mod.parse(new)
    if ticket is None:
        return "組んだ全文がチケットとして読めないので書かない"
    return ""


def _front(text: str) -> dict | None:
    """frontmatter を読む（`ticket._frontmatter` と同じ切り方）。"""
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == ticket_model.FENCE), None)
    if end is None:
        return None
    front = yamlread.safe_load("\n".join(lines[1:end]))
    return front if isinstance(front, dict) else None
