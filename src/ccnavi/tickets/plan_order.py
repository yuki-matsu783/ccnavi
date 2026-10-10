"""計画の番号の振り直しと、提案への書き戻し（設計 9.7。`ccnavi --plan-order <親>`）。

VS Code のワークフロー編集タブは、図で引いた線を「元の番号（いま提案に書いてある番号）での直接の
先行」として `--order` に付けて渡す。ここで番号を振り直し、待ち方を計算して検査し、図に描く中身
（`plans`）を返す。保存（`--write`）では親の提案の `plan:` / `feedback:` の値だけを書き換える。
振り直しの正はここ 1 か所で、画面は同じ決まりで仮の番号を出すだけ。決まりの見張りは画面と共有する
見本の表（`tests/fixtures/plan-reorder.json`）。

## 振り直しの決まり

- 先行が全部前に並んだ項のうち、元の番号がいちばん小さいものから順に番号を振る（Kahn の方法。
  同じ段の項は元の番号順を保つ）。線を変えても並びが崩れなければ番号は変わらない
- 子が承認された番号（固定した番号）は動かさず、空いた番号に承認されていない項を詰める。詰める項が
  無ければ「詰められない」と断る。固定した番号へ入る線（固定した項の先行）は変えられない
- 循環になる線は断る。断ったときは振り直さず、提案のままの答えを返す（タブは線を引く前に戻す）
- `after` は振り直した番号に付け替える。`review: defer` は項に付いたまま動く。延期の引き受け手は
  保存せず、振り直した計画から計算し直す
- 全体計画とフィードバック計画は別に振り直す。フィードバック計画の番号は全体計画の続きで、
  フィードバック計画の項はフィードバック計画の項だけを待てる

## 書き戻し

書くのは親の提案（`todo/`。エージェントも書ける置き場）だけで、承認はしない。書き換えたあとの
承認はダイジェストで縛られる。書く前に次を確かめ、どれかに当たれば何も書かない。

- 開いたときのハッシュ（`--expect`）が、親の提案と、その親の `todo/` の子の提案をまとめた今の
  ハッシュ（`source_sha`）と同じ
- 断った線が無く、振り直した計画の順序の検査（`workflow.problems`）に error が無く、改版なら
  改版の錠（`agree_candidates.lock_problems`）に当たらない
- 組んだ全文を読み直し、`plan` / `feedback` / `phases` のほかの欄と本文が、読んだ形で元と同じ
  （値にアンカーがあり、ほかの欄が別名で指しているときなど）

計画の値の中に書いたコメントは消える（値の外のコメントは残す）。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass, field

import yaml

from ..infra import fsio, settings, yamlread
from ..policy import rules
from . import agree_candidates, approval, phasetypes, ticket_model, workflow
from . import ticket as ticket_mod

# `--plan-order <親> --json` の答えの版。欄を足すだけなら上げない。
VERSION = 1

REFUSED_CYCLE = "cycle"
REFUSED_LOCKED = "locked"
REFUSED_UNPACKABLE = "unpackable"

PARTS = ("plan", "feedback")

# 元の番号 → 直接の先行（元の番号）。計画ごと。
Order = dict[str, dict[int, list[int]]]


@dataclass
class Refusal:
    """振り直せない線。`kind` は `cycle` / `locked` / `unpackable`、`number` はその番号。"""

    kind: str
    number: int
    text: str

    def as_json(self) -> dict:
        return {"kind": self.kind, "number": self.number, "text": self.text}


@dataclass
class Renumbered:
    """振り直した計画。`moved` は元の番号 → 振り直した番号（全体計画とフィードバック計画）。"""

    plan: list[ticket_model.PlanItem]
    feedback: list[ticket_model.PlanItem] | None
    moved: dict[int, int]
    refused: list[Refusal] = field(default_factory=list)


def _parts(t: ticket_model.Ticket) -> list[tuple[str, int, list[ticket_model.PlanItem]]]:
    """(欄の名前, 最初の番号, 項)。フィードバック計画は空でも欄として数える。"""
    parts = [("plan", 1, list(t.plan))]
    if t.feedback is not None:
        parts.append(("feedback", len(t.plan) + 1, list(t.feedback)))
    return parts


def parse_order(text: str, parent: ticket_model.Ticket) -> tuple[Order | None, str]:
    """`--order` の JSON を読む。読めなければ (None, 理由)。

    形は `{"plan": {"1": [], "2": [3]}, "feedback": {...}}`。番号は元の番号。渡した計画は全部の
    番号の先行をそこから取る（書いていない番号は何も待たない）。渡さない計画は提案のまま。
    """
    try:
        data = json.loads(text)
    except ValueError as exc:
        return None, f"JSON として読めない（{exc}）"
    if not isinstance(data, dict):
        return None, '`{"plan": {...}, "feedback": {...}}` の形で渡す'
    ranges = {key: range(first, first + len(items)) for key, first, items in _parts(parent)}
    order: Order = {}
    for key, value in data.items():
        if key not in PARTS:
            return None, f"`{key}` は読まない。渡せるのは plan と feedback"
        if key not in ranges:
            return None, "提案にフィードバック計画（feedback）が無い"
        if not isinstance(value, dict):
            return None, f"`{key}` は番号 → 先行の番号のリストの辞書で渡す"
        numbers = ranges[key]
        part: dict[int, list[int]] = {}
        for name, preds in value.items():
            if not (isinstance(name, str) and name.isdigit() and int(name) in numbers):
                return None, (
                    f"`{key}` の番号 {name!r} は計画に無い"
                    f"（{numbers.start}〜{numbers.stop - 1} 番）"
                )
            n = int(name)
            if not isinstance(preds, list):
                return None, f"`{key}` の {n} 番の先行は番号のリストで渡す"
            got: list[int] = []
            for m in preds:
                if isinstance(m, bool) or not isinstance(m, int):
                    return None, f"`{key}` の {n} 番の先行 {m!r} は番号ではない"
                if m == n:
                    return None, f"`{key}` の {n} 番は自分を待てない"
                if m not in numbers:
                    return None, (
                        f"`{key}` の {n} 番の先行 {m} は同じ計画の番号ではない"
                        f"（{numbers.start}〜{numbers.stop - 1} 番）"
                    )
                if m not in got:
                    got.append(m)
            part[n] = sorted(got)
        order[key] = part
    return order, ""


def renumber(parent: ticket_model.Ticket, order: Order | None, fixed: set[int]) -> Renumbered:
    """番号を振り直す（モジュールの説明の決まり）。断ったときは提案のまま返す。"""
    order = order or {}
    refused: list[Refusal] = []
    moved: dict[int, int] = {}
    built: dict[str, list[ticket_model.PlanItem]] = {}
    for key, first, items in _parts(parent):
        numbers = list(range(first, first + len(items)))
        given = order.get(key)
        preds = {
            n: (given.get(n, []) if given is not None else sorted(set(items[n - first].after)))
            for n in numbers
        }
        held = [n for n in numbers if n in fixed]
        locked = [n for n in held if set(preds[n]) != set(items[n - first].after)]
        for n in locked:
            refused.append(
                Refusal(
                    REFUSED_LOCKED,
                    n,
                    f"{n} 番（{items[n - first].type}）は子が承認されているので、"
                    "入る線（待つ項）を変えられない",
                )
            )
        cycle = _cycle(preds, numbers)
        if cycle:
            path = " → ".join(map(str, cycle + cycle[:1]))
            refused.append(Refusal(REFUSED_CYCLE, cycle[0], f"循環になる線（{path}）"))
        if locked or cycle:
            continue
        placed: dict[int, int] = {}
        stuck = 0
        for pos in numbers:
            if pos in fixed:
                pick = pos if all(m in placed for m in preds[pos]) else None
            else:
                ready = [
                    m
                    for m in numbers
                    if m not in placed and m not in fixed and all(p in placed for p in preds[m])
                ]
                pick = ready[0] if ready else None
            if pick is None:
                stuck = pos
                break
            placed[pick] = pos
        if stuck:
            refused.append(
                Refusal(
                    REFUSED_UNPACKABLE,
                    stuck,
                    "子が承認された番号を動かさずに番号を振れない"
                    f"（{stuck} 番に置ける項が無い）。詰められないので、この線は引けない",
                )
            )
            continue
        moved.update(placed)
        back = {new: old for old, new in placed.items()}
        built[key] = [
            dataclasses.replace(
                items[back[pos] - first],
                after=sorted(placed[m] for m in preds[back[pos]]),
                after_errors=[],
                after_repeated=[],
            )
            for pos in numbers
        ]
    if refused:
        identity = {n: n for n, _ in parent.numbered()}
        plan = [dataclasses.replace(i) for i in parent.plan]
        feedback = (
            None if parent.feedback is None else [dataclasses.replace(i) for i in parent.feedback]
        )
        return Renumbered(plan, feedback, identity, refused)
    return Renumbered(built["plan"], built.get("feedback"), moved)


def _cycle(preds: dict[int, list[int]], numbers: list[int]) -> list[int]:
    """先行を辿って循環があれば、その番号の並び（待つ側から先行へ）。無ければ空。"""
    state: dict[int, int] = {}
    stack: list[int] = []

    def visit(n: int) -> list[int]:
        state[n] = 1
        stack.append(n)
        for m in preds.get(n, []):
            if state.get(m) == 1:
                return stack[stack.index(m) :]
            if m not in state:
                found = visit(m)
                if found:
                    return found
        stack.pop()
        state[n] = 2
        return []

    for n in numbers:
        if n not in state:
            found = visit(n)
            if found:
                return list(reversed(found))
    return []


@dataclass
class Source:
    """振り直しの元。親の提案と、その親の `todo/` の子の提案（読んだバイト列ごと）。"""

    parent: ticket_model.Ticket
    raw: bytes
    children: list[tuple[ticket_model.Ticket, bytes]]
    current: ticket_model.Ticket | None
    fixed: set[int]

    @property
    def sha(self) -> str:
        return digest([(self.parent, self.raw)] + self.children)


def digest(files: list[tuple[ticket_model.Ticket, bytes]]) -> str:
    """提案のバイト列をまとめたハッシュ。ツリーと、ツリーからの相対パスで区切る。

    機械ごとに違う絶対パスは入れない（拡張とテストが同じ値を見られるように）。
    """
    h = hashlib.sha256()
    for t, raw in sorted(files, key=lambda f: (f[0].tree, approval.source_path(f[0]))):
        h.update(f"{t.tree}\0{approval.source_path(t)}\0{len(raw)}\0".encode())
        h.update(raw)
    return h.hexdigest()


def load(
    conf: settings.Settings,
    root: str,
    proposal: ticket_model.Ticket,
    proposals: list[ticket_model.Ticket],
    current: ticket_model.Ticket | None,
) -> tuple[Source | None, str]:
    """振り直しの元を読む。親の提案を 1 回だけ読み、その中身で計画とハッシュを決める。"""
    raw = fsio.read_bytes(proposal.path)
    if raw is None:
        return None, f"{proposal.path} を読めない"
    parent = _parse(raw, proposal)
    if parent is None or parent.ticket != proposal.ticket or parent.is_child:
        return None, f"{proposal.path} を親の提案として読めない"
    if not parent.has_plan:
        return None, f"{proposal.ticket} は計画（plan:）を持たない"
    children: list[tuple[ticket_model.Ticket, bytes]] = []
    for t in proposals:
        if not (t.is_child and t.parent == parent.ticket and t.state == ticket_model.TODO):
            continue
        if not t.path:
            continue
        data = fsio.read_bytes(t.path)
        if data is None:
            return None, f"子の提案 {t.path} を読めない"
        child = _parse(data, t) or t
        children.append((child, data))
    fixed = agree_candidates.fixed_numbers(conf, root, parent.ticket)
    return Source(parent, raw, children, current, fixed), ""


def _parse(raw: bytes, found: ticket_model.Ticket) -> ticket_model.Ticket | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    t, _ = ticket_mod.parse(text)
    if t is None:
        return None
    for name in ("project", "state", "tree", "tree_root", "path"):
        setattr(t, name, getattr(found, name))
    return t


@dataclass
class Answer:
    """振り直した答え。`--plan-order --json` と承認の preview の `plans` が同じものから作る。"""

    source: Source
    renumbered: Renumbered
    revised: ticket_model.Ticket
    problems: list[rules.Problem]
    revision_problems: list[rules.Problem]

    @property
    def writable(self) -> bool:
        return not (self.renumbered.refused or self.problems or self.revision_problems)

    @property
    def moving_children(self) -> list[tuple[ticket_model.Ticket, int]]:
        """番号が変わる項を指す子の提案と、その新しい番号。"""
        moved = self.renumbered.moved
        out = []
        for child, _ in self.source.children:
            if child.phase is not None and moved.get(child.phase, child.phase) != child.phase:
                out.append((child, moved[child.phase]))
        return out


def answer(source: Source, order: Order | None) -> Answer:
    parent = source.parent
    got = renumber(parent, order, source.fixed)
    revised = dataclasses.replace(parent, plan=got.plan, feedback=got.feedback, workflow=None)
    revised.workflow = workflow.compute(revised)
    types = phasetypes.types_of(parent)
    revised.phase_types = types
    problems = [p for p in workflow.problems(revised, types) if p.severity == rules.SEVERITY_ERROR]
    lock: list[rules.Problem] = []
    if source.current is not None and not got.refused:
        lock = agree_candidates.lock_problems(revised, source.current, source.fixed, got.moved)
    return Answer(source, got, revised, problems, lock)


def plans(ans: Answer) -> list[dict]:
    """図に描く中身。計画ごとに 1 件（全体計画とフィードバック計画）。"""
    parent, revised = ans.source.parent, ans.revised
    wf = revised.workflow or workflow.compute(revised)
    types = phasetypes.types_of(parent)
    back = {new: old for old, new in ans.renumbered.moved.items()}
    loose = workflow.loose(revised, wf)
    ready = workflow.ready(revised, wf)
    current = ans.source.current
    held = workflow.compute(current) if current is not None else None
    sha = ans.source.sha
    out = []
    for key, first, items in _parts(revised):
        numbers = range(first, first + len(items))
        original = dict(parent.numbered())
        entry_items = []
        for n, item in zip(numbers, items, strict=True):
            pt = types.get(item.type)
            review = (
                ticket_model.PLAN_REVIEW_MR
                if item.review == ticket_model.PLAN_REVIEW_MR
                else (pt.review if pt is not None else "")
            )
            entry_items.append(
                {
                    "number": n,
                    "from": back.get(n, n),
                    "type": item.type,
                    "title": pt.title if pt is not None and pt.title else item.type,
                    "review": review,
                    "deferred": item.deferred,
                    "review_at": wf.review_at.get(n),
                    "locked": n in ans.source.fixed,
                }
            )
        out.append(
            {
                "ticket": parent.ticket,
                "part": key,
                "source_sha": sha,
                "items": entry_items,
                "after": {
                    str(n): list(i.after) for n, i in zip(numbers, items, strict=True) if i.after
                },
                "proposed": {
                    str(n): sorted(set(original[n].after))
                    for n in numbers
                    if n in original and original[n].after
                },
                "current": None if held is None else _direct(current, held, key),
                "loose": [n for n in loose if n in numbers],
                "ready": [n for n in ready if n in numbers],
                "problems": [
                    str(p)
                    for p in workflow.problems(revised, types, key)
                    if p.severity == rules.SEVERITY_ERROR
                ],
            }
        )
    return out


def _direct(
    current: ticket_model.Ticket, wf: ticket_model.Workflow, part: str
) -> dict[str, list[int]]:
    """承認済みの待ち方から戻した直接の先行（推移的に辿れる線を除く）。その計画の番号だけ。"""
    for key, first, items in _parts(current):
        if key != part:
            continue
        numbers = range(first, first + len(items))
        out: dict[str, list[int]] = {}
        for n in numbers:
            waits = [m for m in wf.waits.get(n, []) if m in numbers]
            direct = [m for m in waits if not any(m in wf.waits.get(k, []) for k in waits)]
            if direct:
                out[str(n)] = direct
        return out
    return {}


def body(ans: Answer) -> dict:
    """`--plan-order <親> --json` の答え。"""
    loose = workflow.loose(ans.revised, ans.revised.workflow)
    return {
        "version": VERSION,
        "ticket": ans.source.parent.ticket,
        "source_sha": ans.source.sha,
        "plans": plans(ans),
        "problems": [str(p) for p in ans.problems],
        "loose": loose,
        "refused": [r.as_json() for r in ans.renumbered.refused],
        "revision_problems": [str(p) for p in ans.revision_problems],
        "children": [
            {"ticket": c.ticket, "path": c.path, "phase": c.phase, "new_phase": new}
            for c, new in ans.moving_children
        ],
        "writable": ans.writable,
    }


def preview_plans(
    conf: settings.Settings,
    root: str,
    parents: list[tuple[ticket_model.Ticket, ticket_model.Ticket | None]],
    proposals: list[ticket_model.Ticket],
) -> list[dict]:
    """承認の preview に載せる `plans`。`--order` は渡さない（承認する提案そのものの順序）。

    `parents` は承認の対象に入った計画を持つ親の提案と、改版ならその承認済みチケット。
    """
    out: list[dict] = []
    for proposal, current in parents:
        source, _ = load(conf, root, proposal, proposals, current)
        if source is not None:
            out += plans(answer(source, None))
    return out


# ---- 書き戻し

_FENCE = ticket_model.FENCE


def rebuilt(ans: Answer) -> tuple[bytes | None, str]:
    """親の提案の `plan:` / `feedback:` の値だけを振り直した形に差し替えたバイト列。

    変わらなければ (None, "")。書けなければ (None, 理由)。
    """
    parent, revised = ans.source.parent, ans.revised
    changed = [
        key
        for key, old, new in (
            ("plan", parent.plan, revised.plan),
            ("feedback", parent.feedback or [], revised.feedback or []),
        )
        if [i.as_raw() for i in old] != [i.as_raw() for i in new]
    ]
    if not changed:
        return None, ""
    try:
        text = ans.source.raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "UTF-8 として読めない"
    built = text
    for key in changed:
        items = revised.plan if key == "plan" else (revised.feedback or [])
        built, why = _replace_value(built, key, items)
        if why:
            return None, why
    why = _changed_elsewhere(text, built, revised)
    if why:
        return None, why
    return built.encode("utf-8"), ""


def _item_text(item: ticket_model.PlanItem) -> str:
    dumped = yaml.safe_dump(
        [item.as_raw()],
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=True,
        width=1 << 30,
    ).strip()
    return dumped[1:-1].strip()


def _replace_value(text: str, key: str, items: list[ticket_model.PlanItem]) -> tuple[str, str]:
    """frontmatter の `key:` の値を項の並びに差し替える。ほかの行は変えない。"""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != _FENCE:
        return text, "frontmatter が読めない"
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == _FENCE), None)
    if end is None:
        return text, "frontmatter が閉じていない"
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    pattern = re.compile(rf"^{re.escape(key)}\s*:")
    heads = [i for i in range(1, end) if pattern.match(lines[i])]
    if len(heads) != 1:
        return text, f"`{key}:` の行が 1 つに決まらない"
    start = heads[0]
    inline = lines[start].split(":", 1)[1].split("#", 1)[0].strip()
    stop = start + 1
    while stop < end:
        line = lines[stop]
        if not line.strip() or line[0] in " \t" or (not inline and line.startswith("-")):
            stop += 1
            continue
        break
    while stop - 1 > start and not lines[stop - 1].strip():
        stop -= 1
    after = stop
    while after < end and (not lines[after].strip() or lines[after].startswith("#")):
        after += 1
    if after > stop and after < end and (lines[after][0] in " \t" or lines[after].startswith("-")):
        return text, (
            f"`{key}:` の値の中に行頭（0 桁目）のコメントがあり、値の範囲が決まらない。"
            "コメントを字下げするか消してから打ち直す"
        )
    if items:
        block = [f"{key}:{newline}"] + [f"  - {_item_text(i)}{newline}" for i in items]
    else:
        block = [f"{key}: []{newline}"]
    return "".join(lines[:start] + block + lines[stop:]), ""


def _front(text: str) -> dict | None:
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == _FENCE), None)
    if end is None:
        return None
    front = yamlread.safe_load("\n".join(lines[1:end]))
    return front if isinstance(front, dict) else None


_REWRITTEN = ("plan", "feedback", phasetypes.COPY_KEY)


def _changed_elsewhere(old: str, new: str, revised: ticket_model.Ticket) -> str:
    """組んだ全文を読み直し、計画のほかの欄と本文が元と同じか。違えば理由（書かない）。"""
    try:
        before = _front(old)
        after = _front(new)
    except (yaml.YAMLError, ValueError) as exc:
        return (
            f"組んだ全文を読み直せない（{exc}）。`plan:` / `feedback:` の値をほかの欄が別名で"
            "指していないか確かめる"
        )
    if before is None or after is None:
        return "組んだ全文の frontmatter が読めない"
    rest_before = {k: v for k, v in before.items() if k not in _REWRITTEN}
    rest_after = {k: v for k, v in after.items() if k not in _REWRITTEN}
    if rest_before != rest_after or before.get(phasetypes.COPY_KEY) != after.get(
        phasetypes.COPY_KEY
    ):
        return (
            "差し替えると plan・feedback のほかの欄が変わる（値にアンカーがあり、ほかの欄が"
            "別名で指しているなど）ので書かない"
        )
    if old.split(_FENCE, 2)[2:] != new.split(_FENCE, 2)[2:]:
        return "差し替えると本文が変わるので書かない"
    ticket, _ = ticket_mod.parse(new)
    if ticket is None:
        return "組んだ全文がチケットとして読めないので書かない"

    def shape(items):
        return [(i.type, i.review, list(i.after)) for i in items or []]

    if shape(ticket.plan) != shape(revised.plan) or shape(ticket.feedback) != shape(
        revised.feedback
    ):
        return "組んだ全文の計画が振り直した計画と合わないので書かない"
    return ""


def write(ans: Answer, content: bytes) -> str:
    """親の提案を書き換える。読んだときから中身が変わっていれば書かない。書けなければ理由。"""
    path = ans.source.parent.path
    now = fsio.read_bytes(path)
    if now != ans.source.raw:
        return "提案が外で変わった（読んでから書くまでの間）"
    for child, raw in ans.source.children:
        if fsio.read_bytes(child.path) != raw:
            return f"子の提案 {child.ticket} が外で変わった（読んでから書くまでの間）"
    failed = fsio.write_bytes_atomic(path, content)
    if failed:
        return f"{path} に書けない（{failed}）"
    return ""


def written_sha(ans: Answer, content: bytes) -> str:
    """書いたあとのハッシュ（親の提案を書いた中身にしたもの）。"""
    return digest([(ans.source.parent, content)] + ans.source.children)
