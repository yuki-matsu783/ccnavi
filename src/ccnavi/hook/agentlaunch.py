"""サブエージェントの起動（Agent・Task の PreToolUse）で、担当を決める。

サブエージェントの hook の cwd は、起動した瞬間のメインの cwd に固定される。そのままでは、メインが
ワークスペースルートから任せた体に、子の範囲とフローの案内が届かない。ここでは、依頼文（プロンプト）の
言及から担当の子（かワークツリー）を決め、決まらないときは止める理由を返し、決まったときは
プロンプトに書き足す案内を返す。

- 言及は 2 種類。承認済みの子の識別子と、`.claude/worktrees/` を含むパス（`mentions`）
- 1 つずつ検証してから担当にまとめる（`_judge`）。止める理由の優先順位は `decide` の順
- 返すのは「止める理由」か「更新の計画」だけ。応答は書かず、判定も途中で終えない
  （`judge.decide_before` が既存の判定と合成する）

作業場所（cwd）の注入は、実測（`wip/research/agent-cwd-probe.md`）で Agent の引数として
使われなかったので行わない（`INJECT_CWD`）。案内はプロンプトに書き足す。範囲の判定は今までどおり、書き込み先の
ワークツリーのチケットで決まる。担当の体を `agent_id` で縛るものではない。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from ..infra import modes, pathcase, settings, tree
from ..tickets import approval, approval_checks, flow, ticket_model
from . import agentkind, childnote

CODE_ISOLATION = "DENY_AGENT_ISOLATION"
CODE_NO_CHILD = "DENY_AGENT_NO_CHILD"
CODE_MULTI_TARGET = "DENY_AGENT_MULTI_TARGET"
CODE_PARENT_TREE = "DENY_AGENT_PARENT_TREE"
CODE_CHILD_UNAVAILABLE = "DENY_AGENT_CHILD_UNAVAILABLE"
CODE_TARGET_INVALID = "DENY_AGENT_TARGET_INVALID"
CODE_CWD_CONFLICT = "DENY_AGENT_CWD_CONFLICT"

RULE_ISOLATION = "builtin:agent-isolation"
RULE_NO_CHILD = "builtin:agent-no-child"
RULE_MULTI_TARGET = "builtin:agent-multi-target"
RULE_PARENT_TREE = "builtin:agent-parent-tree"
RULE_CHILD_UNAVAILABLE = "builtin:agent-child-unavailable"
RULE_TARGET_INVALID = "builtin:agent-target-invalid"
RULE_CWD_CONFLICT = "builtin:agent-cwd-conflict"

# tool_input.cwd を担当のワークツリーに書き換えるか。この環境の実測（claude 2.1.309）では、
# hook が足した cwd は Agent の引数として使われなかった（allow を付けても同じ）。
# 使われる版で戻せるように、計画の形（`Plan.cwd`）は残してある。
INJECT_CWD = False

# 案内の長さの上限（文字）。
GUIDE_LIMIT = 4000
GUIDE_OPEN = "[ccnavi] 担当の案内（ここから）"
GUIDE_CLOSE = "[ccnavi] 担当の案内（ここまで）"

# 止める理由に添える、担当を決めるための共通の 1 文。
ASSIGN_NOTE = "1 体に担当は 1 つだけ割り当てる。担当以外の子の識別子とワークツリーのパスは書かない"
# 子の識別子に使われる文字。言及は語として一致するものだけ（前後がこの文字でないこと）。
_ID_CHARS = "A-Za-z0-9._-"
# プロンプトを語に分ける区切り（パスの候補を拾うため）。
_SPLIT = re.compile(r"[\s'\"`<>()（）「」『』\[\]{},、。;]+")
_FOLDER = ".claude/worktrees"
_ABSOLUTE = re.compile(r"^(?:/|[A-Za-z]:[/\\])")

KIND_CHILD = "child"
KIND_PARENT = "parent"
KIND_DIRECT = "direct"


@dataclass
class Stop:
    """止める理由。kind は `modes.GATE` か `modes.AUX`（dry-run でも止める）。"""

    code: str
    rule: str
    text: str
    kind: str


@dataclass
class Plan:
    """起動の入力の更新の計画。cwd と prompt を別々に持ち、空のものは更新しない。"""

    cwd: str = ""
    prompt: str = ""

    def apply(self, tool_input: dict) -> dict:
        """元の tool_input に更新を当てた新しい辞書。元は変えない。"""
        updated = dict(tool_input)
        if self.cwd:
            updated["cwd"] = self.cwd
        if self.prompt:
            updated["prompt"] = self.prompt
        return updated

    def __bool__(self) -> bool:
        return bool(self.cwd or self.prompt)


@dataclass
class Result:
    stop: Stop | None = None
    plan: Plan | None = None


@dataclass
class Mention:
    """プロンプトで拾った言及 1 件。text は元の表記、name は結び付く名前（識別子か名前）。"""

    kind: str  # "child" か "path"
    text: str
    name: str = ""
    # パスの検証の結果。空なら有効。
    invalid: str = ""
    worktree: tree.Tree | None = None


@dataclass
class Target:
    """検証を終えた担当の候補。同じワークツリーの言及は 1 つにまとまる。"""

    name: str
    role: str
    worktree: tree.Tree | None
    ticket: ticket_model.Ticket | None = None
    state: str = ""
    texts: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return pathcase.fold(self.worktree.root if self.worktree else self.name)


class Book:
    """承認済みチケットの索引。作業中・レビュー待ち・閉じたの 3 つを 1 度ずつ読む。"""

    def __init__(self, conf: settings.Settings, root: str, raw: approval.Raw | None = None):
        self.conf, self.root = conf, root
        raw = raw if raw is not None else approval.read_raw(conf, root)
        self.open, _ = approval.scan(conf, root, raw=raw)
        self.review, _ = approval.scan_review(conf, root, raw=raw)
        self.closed, _ = approval.scan(conf, root, closed=True, raw=raw)
        self.index = approval_checks.by_id(self.open)
        self.review_index = approval_checks.by_id(self.review)
        self.closed_index = approval_checks.by_id(self.closed)

    def find(self, name: str) -> tuple[ticket_model.Ticket | None, str]:
        """この名前のチケットと、置き場の状態（doing・review・done）。無ければ (None, "")。"""
        for state, index in (
            (ticket_model.DOING, self.index),
            (ticket_model.REVIEW, self.review_index),
            (ticket_model.DONE, self.closed_index),
        ):
            found = tree.lookup(index, name)
            if found is not None:
                return found, state
        return None, ""

    def children(self) -> list[ticket_model.Ticket]:
        """言及に数える子。作業中・レビュー待ち・閉じたのどれかにある子チケット。"""
        seen: dict[str, ticket_model.Ticket] = {}
        for pool in (self.open, self.review, self.closed):
            for t in pool:
                if t.is_child:
                    seen.setdefault(t.ticket, t)
        return list(seen.values())


def decide(
    conf: settings.Settings,
    root: str,
    payload,
    raw: approval.Raw | None = None,
) -> Result:
    """起動ツールの呼び出しについて、止める理由か更新の計画を返す。無ければ空の Result。"""
    if payload.tool_name not in modes.AGENT_TOOLS:
        return Result()
    tool_input = payload.tool_input
    # isolation は値に関係なく止める。作業場所は ccnavi が決め、isolation のワークツリー
    # （.claude/worktrees/agent-<id>）にはチケットの範囲が当たらない。チケット制御の有無によらない。
    if "isolation" in tool_input:
        return Result(stop=_isolation())

    prompt = payload.field_value("prompt")
    book = Book(conf, root, raw) if conf.tickets_enabled else None
    found = mentions(conf, root, prompt, book)
    here = tree.tree_of(root, payload.cwd, conf.projects) if payload.cwd else None

    # 1. 無効なパスが 1 つでもあれば止める。
    bad = [m for m in found if m.kind == "path" and m.invalid]
    if bad:
        return Result(stop=_target_invalid(bad))

    targets = _targets(conf, root, found, book)
    caller = _caller_child(book, here) if payload.agent_id and book is not None else None
    groups = {t.key: t for t in targets}
    if caller is not None:
        # 入れ子で、呼び手の担当が子のとき。呼び手の子も 1 つの担当先として数え、違う言及があれば
        # 2 つ以上になる（2）。言及が無い・同じ子だけなら、担当先は呼び手の子になる。
        groups.setdefault(caller.key, caller)

    # 2. 違うワークツリーが 2 つ以上。
    if len(groups) >= 2:
        gate = book is not None
        return Result(stop=_multi_target(list(groups.values()), gate))

    if len(groups) == 1:
        target = next(iter(groups.values()))
        # 3. 親のワークツリー。
        if target.role == KIND_PARENT and book is not None:
            return Result(stop=_parent_tree(target, book))
        # 4. 子で、担当にできない。
        if target.role == KIND_CHILD and book is not None:
            why = unavailable(conf, root, target)
            if why:
                return Result(stop=_child_unavailable(root, target, why))
        # 5. 既にある cwd が決めた担当先と違う。
        given = tool_input.get("cwd")
        if (
            isinstance(given, str)
            and given
            and target.worktree is not None
            and pathcase.fold(_real(given)) != pathcase.fold(_real(target.worktree.root))
        ):
            return Result(stop=_cwd_conflict(given, target))
        return Result(plan=_plan(conf, root, prompt, target, book))

    # 6. 言及が 0 件。親のワークツリーから、編集する種類を起こす（呼び手が子の体なら決まっている）。
    if book is not None and here is not None and not here.is_main:
        parent = tree.lookup(book.index, here.name)
        if parent is not None and not parent.is_child:
            working = _working_children(book, parent)
            subagent_type = tool_input.get("subagent_type")
            kind = agentkind.kind_of(
                [here.root, root],
                subagent_type if isinstance(subagent_type, str) else "",
                None,
            )
            if working and kind == agentkind.EDIT:
                return Result(stop=_no_child(parent, working))
    return Result()


def mentions(conf: settings.Settings, root: str, prompt: str, book: Book | None) -> list[Mention]:
    """プロンプトの言及を拾う。承認済みの子の識別子と、`.claude/worktrees/` を含むパス。

    ここでは担当にできるかを見ない。パスの検証だけは済ませて `invalid` に理由を持たせる。
    チケット制御が無効なときは、子の識別子は拾わない。
    """
    found: list[Mention] = []
    if book is not None:
        for child in sorted(book.children(), key=lambda t: t.ticket):
            if _word_in(child.ticket, prompt):
                found.append(Mention("child", child.ticket, name=child.ticket))
    trees = tree.worktrees(root, conf.projects) if _FOLDER.replace("/", os.sep) else []
    for text in _path_tokens(prompt):
        mention = Mention("path", text)
        _check_path(root, mention, trees)
        found.append(mention)
    return found


def _word_in(ident: str, prompt: str) -> bool:
    """識別子が語として現れるか。前後の文字が識別子に使う文字でないこと。大文字小文字は区別しない。"""
    pattern = rf"(?<![{_ID_CHARS}]){re.escape(ident)}(?![{_ID_CHARS}])"
    return re.search(pattern, prompt, re.IGNORECASE) is not None


def _path_tokens(prompt: str) -> list[str]:
    """プロンプトの中の、`.claude/worktrees/` を含む語。元の表記のまま、重複は 1 つにする。"""
    out: list[str] = []
    for token in _SPLIT.split(prompt):
        flat = token.replace("\\", "/")
        at = flat.casefold().find(_FOLDER)
        if at < 0:
            continue
        # `path=/abs/…` のような前置きは外す。
        eq = token.rfind("=", 0, at)
        text = token[eq + 1 :] if eq >= 0 else token
        text = text.rstrip(".:!?…")
        if text and text not in out:
            out.append(text)
    return out


def _real(path: str) -> str:
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):
        return os.path.abspath(path)


def _parts(path: str) -> list[str]:
    return [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]


def _lexical_rest(root: str, full: str) -> list[str] | None:
    """`full` がルートの下なら、ルートからの残りの名前（書かれた表記のまま）。外なら None。"""
    want = _parts(full)
    for base in (root, _real(root)):
        have = _parts(os.path.normpath(base))
        if len(want) >= len(have) and all(
            pathcase.fold(a) == pathcase.fold(b) for a, b in zip(have, want, strict=False)
        ):
            return want[len(have) :]
    return None


def _check_path(root: str, mention: Mention, trees: list[tree.Tree]) -> None:
    """パスが、認識されたワークツリーを指すか。指さなければ理由を `invalid` に書く。"""
    text = mention.text
    parts = _parts(text)
    if ".." in parts:
        mention.invalid = "`..` を含むので使えません"
        return
    full = text if _ABSOLUTE.match(text) else os.path.join(root, text)
    rest = _lexical_rest(root, full)
    if rest is None:
        mention.invalid = "このワークスペースの外を指しています"
        return
    wanted = [".claude", "worktrees"]
    if len(rest) < 2 or [pathcase.fold(p) for p in rest[:2]] != wanted:
        mention.invalid = "このワークスペースの .claude/worktrees/ の下を指していません"
        return
    if len(rest) < 3:
        mention.invalid = "ワークツリーの名前がありません"
        return
    # 途中にシンボリックリンクが無いこと。書かれたパスを辿り、実在する分だけ見る。
    walk = root
    for part in rest:
        walk = os.path.join(walk, part)
        if os.path.islink(walk):
            mention.invalid = f"途中にシンボリックリンクがあります（{part}）"
            return
    name = tree.nfc(rest[2])
    for candidate in trees:
        if pathcase.fold(candidate.name) == pathcase.fold(name):
            mention.name, mention.worktree = candidate.name, candidate
            return
    mention.invalid = "ccnavi のツリーに認識されたワークツリーではありません（登録がありません）"


def _targets(
    conf: settings.Settings, root: str, found: list[Mention], book: Book | None
) -> list[Target]:
    """有効な言及を、ワークツリーの実体ごとにまとめる。"""
    trees = tree.worktrees(root, conf.projects)
    by_key: dict[str, Target] = {}
    for m in found:
        if m.kind == "path" and m.invalid:
            continue
        worktree = m.worktree
        if m.kind == "child":
            worktree = next(
                (t for t in trees if pathcase.fold(t.name) == pathcase.fold(tree.nfc(m.name))),
                None,
            )
        ticket, state = book.find(m.name) if book is not None else (None, "")
        role = KIND_DIRECT
        if ticket is not None:
            role = KIND_CHILD if ticket.is_child else KIND_PARENT
        target = Target(m.name, role, worktree, ticket, state)
        key = target.key
        if key in by_key:
            by_key[key].texts.append(m.text)
        else:
            target.texts.append(m.text)
            by_key[key] = target
    return list(by_key.values())


def _caller_child(book: Book | None, here: tree.Tree | None) -> Target | None:
    """呼び手（体）の cwd が子のワークツリーなら、その子を担当先として返す。"""
    if book is None or here is None or here.is_main:
        return None
    ticket, state = book.find(here.name)
    if ticket is None or not ticket.is_child:
        return None
    return Target(ticket.ticket, KIND_CHILD, here, ticket, state, [here.name])


def unavailable(conf: settings.Settings, root: str, target: Target) -> str:
    """子を担当にできない理由。担当にできるなら空。ワークツリーの有無も見る。"""
    t = target.ticket
    if t is None:
        return ""
    if target.state == ticket_model.DONE:
        return "取り消された子です" if t.cancelled_at else "閉じた子です"
    if target.state == ticket_model.REVIEW or t.completed_at:
        return "完了してレビュー待ちの子です"
    if t.blocked:
        return f"blocked の子です（{t.blocked}）"
    if not t.started_at:
        return "未着手の子です"
    if target.worktree is None:
        return "ワークツリーがありません"
    return ""


def _project_mismatch(book: Book, target: Target) -> str:
    """親の `project:` と、ワークツリーの元リポジトリが食い違うときの説明。"""
    t = target.ticket
    parent = book.find(t.parent)[0] if t is not None else None
    if parent is None or target.worktree is None:
        return ""
    if pathcase.fold(parent.project) != pathcase.fold(target.worktree.project):
        return (
            f"親 {parent.ticket} のプロジェクト（{parent.project or '(ワークスペース)'}）と"
            f"ワークツリーの元リポジトリ（{target.worktree.project or '(ワークスペース)'}）が"
            "食い違っています"
        )
    return ""


def _working_children(book: Book, parent: ticket_model.Ticket) -> list[ticket_model.Ticket]:
    """親の、作業中の子（着手済みで blocked でないもの）。識別子の順。"""
    return sorted(
        (
            c
            for c in book.open
            if c.parent == parent.ticket and c.started_at and not c.blocked and c.in_progress
        ),
        key=lambda c: c.ticket,
    )


# ---- 止める理由の文面


def _stop(code: str, rule: str, lines: list[str], kind: str) -> Stop:
    return Stop(code, rule, "\n".join(lines), kind)


def _isolation() -> Stop:
    return _stop(
        CODE_ISOLATION,
        RULE_ISOLATION,
        [
            "isolation は使いません。チケットの子の作業なら、その子の識別子をプロンプトに書いて"
            "起動し直してください（ccnavi がその子の範囲とフローの案内を書き足します）。"
            "直接作業なら、ccnavi-worktree.sh で切ったワークツリーのパス"
            "（.claude/worktrees/<名前>）をプロンプトに書いて起動し直してください。"
            "担当以外の子の識別子やワークツリーのパスは、プロンプトに書かないでください。"
        ],
        modes.AUX,
    )


def _target_invalid(bad: list[Mention]) -> Stop:
    lines = ["プロンプトに書かれたワークツリーのパスが使えません。"]
    lines.extend(f"  {m.text}: {m.invalid}" for m in bad)
    lines.append(
        "チケットの子の作業なら、パスではなく子の識別子を 1 つ書いて起動し直してください。"
        "直接作業なら、ccnavi-worktree.sh で切ったワークツリーのパスを書いてください。"
    )
    return _stop(CODE_TARGET_INVALID, RULE_TARGET_INVALID, lines, modes.AUX)


def _multi_target(targets: list[Target], gate: bool) -> Stop:
    names = ", ".join(t.name for t in targets)
    return _stop(
        CODE_MULTI_TARGET,
        RULE_MULTI_TARGET,
        [
            f"プロンプトが違うワークツリーを {len(targets)} つ指しています（{names}）。",
            "1 体に 1 つを割り当てて別々に起動してください。"
            "担当以外の子の識別子やパスは書かないでください"
            "（「触れない」と書いた子も言及として数えます）。",
        ],
        modes.GATE if gate else modes.AUX,
    )


def _parent_tree(target: Target, book: Book) -> Stop:
    children = _working_children(book, target.ticket) if target.ticket is not None else []
    listed = "、".join(c.ticket for c in children[:5])
    lines = [
        f"{target.name} は親のワークツリーです。親のワークツリーでは作業しません。",
        "子の識別子をプロンプトに 1 つ書いて起動し直してください。"
        + (f"作業中の子: {listed}" if listed else ""),
    ]
    return _stop(CODE_PARENT_TREE, RULE_PARENT_TREE, lines, modes.GATE)


def _child_unavailable(root: str, target: Target, why: str) -> Stop:
    lines = [f"子 {target.name} は担当にできません: {why}。"]
    t = target.ticket
    startable = (
        t is not None
        and target.state == ticket_model.DOING
        and not t.blocked
        and not t.completed_at
        and (not t.started_at or target.worktree is None)
    )
    if startable:
        start = f"{settings.script_command(root, 'ccnavi-ticket.sh')} start {target.name}"
        lines.append(f"着手してから起動し直してください: {start}")
        return _stop(CODE_CHILD_UNAVAILABLE, RULE_CHILD_UNAVAILABLE, lines, modes.GATE)
    lines.append(
        "子の状態を動かすのはユーザの判断です。ユーザに状態を伝えて、指示を待ってください。"
    )
    return _stop(CODE_CHILD_UNAVAILABLE, RULE_CHILD_UNAVAILABLE, lines, modes.GATE)


def _cwd_conflict(given: str, target: Target) -> Stop:
    return _stop(
        CODE_CWD_CONFLICT,
        RULE_CWD_CONFLICT,
        [
            f"tool_input の cwd（{given}）が、プロンプトで決まった担当先"
            f"（{target.name}: {target.worktree.root if target.worktree else ''}）と違います。",
            "cwd は付けずに、担当の子の識別子かワークツリーのパスをプロンプトに 1 つ書いて"
            "起動し直してください。",
        ],
        modes.AUX,
    )


def _no_child(parent: ticket_model.Ticket, working: list[ticket_model.Ticket]) -> Stop:
    listed = "、".join(c.ticket for c in working[:5])
    more = f"（ほか {len(working) - 5} 件）" if len(working) > 5 else ""
    return _stop(
        CODE_NO_CHILD,
        RULE_NO_CHILD,
        [
            f"親 {parent.ticket} のワークツリーから、担当の子を指さずに編集するサブエージェントを"
            "起動することはできません。",
            f"作業中の子: {listed}{more}",
            "担当の子の識別子を 1 つ、プロンプトに書いて起動し直してください"
            "（ccnavi がその子のワークツリーで案内します）。調べるだけなら、読み取り専用の種類"
            "（Explore など）で起動してください。",
        ],
        modes.GATE,
    )


# ---- 更新の計画と、プロンプトに書き足す案内


def _plan(
    conf: settings.Settings,
    root: str,
    prompt: str,
    target: Target,
    book: Book | None,
) -> Plan | None:
    """担当が決まったときの更新の計画。案内を組めない・要らないなら None。"""
    cwd = target.worktree.root if INJECT_CWD and target.worktree is not None else ""
    guide = ""
    if book is not None and target.worktree is not None:
        try:
            guide = guidance(conf, root, target, book)
        except Exception:  # noqa: BLE001  案内を組めなくても起動は止めない
            guide = ""
    if not guide:
        return Plan(cwd=cwd) if cwd else None
    body = strip_guide(prompt)
    return Plan(cwd=cwd, prompt=f"{body}\n\n{guide}" if body else guide)


def strip_guide(prompt: str) -> str:
    """プロンプトにある案内の囲みを、中身を信用せずに取り除く。

    ここから…ここまで、を全部。閉じの無い「ここから」は末尾まで、開きの無い「ここまで」はその印だけ。
    """
    text = prompt
    while GUIDE_OPEN in text:
        start = text.index(GUIDE_OPEN)
        end = text.find(GUIDE_CLOSE, start + len(GUIDE_OPEN))
        text = text[:start] if end < 0 else text[:start] + text[end + len(GUIDE_CLOSE) :]
    return text.replace(GUIDE_CLOSE, "").rstrip()


def guidance(conf: settings.Settings, root: str, target: Target, book: Book) -> str:
    """プロンプトの末尾に足す、担当の案内。`GUIDE_LIMIT` 文字以内。

    必須の行（担当・ワークツリーの絶対パス・3 行・閉じの行・フローのファイルのパス）の予算を先に
    取り、残りに範囲・フローの手順・局面を並べる。収まらない分は省き、末尾を切り捨てない。
    """
    worktree = target.worktree
    assert worktree is not None
    if target.role == KIND_DIRECT:
        lines = [
            GUIDE_OPEN,
            f"担当: 直接作業のワークツリー {worktree.name}",
            f"ワークツリー: {worktree.root}",
            *childnote.RULE_LINES,
            GUIDE_CLOSE,
        ]
        return "\n".join(lines)
    ticket = target.ticket
    assert ticket is not None
    flow_path, _, flow_exists = flow.resolve(conf, root, ticket)
    head = [GUIDE_OPEN, f"担当の子: {ticket.ticket}", f"ワークツリー: {worktree.root}"]
    more = f"続きは {flow_path} を読む" if flow_exists else ""
    tail = [*childnote.RULE_LINES, *([more] if more else []), GUIDE_CLOSE]
    used = sum(len(line) + 1 for line in head + tail)
    room = max(0, GUIDE_LIMIT - used)
    preds = childnote.predecessors(conf, root, book.open, book.review, book.closed)
    block = childnote.child_block(
        conf, root, ticket, book.index, preds, room, True, brief_room=room
    )
    middle: list[str] = []
    cut = False
    groups = [
        [line for line in block.lines if line not in block.brief],
        block.brief,
        childnote.stage_lines(conf, root, book.index, [ticket]),
    ]
    for group in groups:
        size = sum(len(line) + 1 for line in group)
        if not group:
            continue
        if size <= room:
            middle.extend(group)
            room -= size
        else:
            cut = True
    lines = [*head, *middle]
    # 省いた分があるか、フローの手順を並べなかったなら、続きの行を残す。無ければ除く。
    lines.extend(tail if (cut or not block.brief) else [t for t in tail if t != more])
    return "\n".join(lines)
