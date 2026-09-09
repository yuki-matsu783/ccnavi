"""作業チケットの読み込みと、そこが宣言する作業範囲。

## チケットは提案であって権威ではない

チケットはエージェントが書く。だからチケットの中身をそのまま判定に使うと、
範囲の外で止められたエージェントが、チケットに 1 行足して自分の首輪を伸ばせる。
止められた側が止め方を書き換えられる仕組みは、止めていない。

判定が使うのは承認済みの写し（approval.py）だけ。作業ツリーの提案は、人に見せて
承認を求めるためのもので、承認されるまで判定には 1 ミリも効かない。承認されたあとに
提案を書き換えても、効いているのは写しの側なので範囲は広がらない。

## 絞ることしかできない

チケットが宣言できるのは「ここだけ書く」であって「ここも書ける」ではない。
ルールが何も言わなかった場所に、チケットは「宣言した範囲の外は止める」を足すだけ。
子は親の部分集合で、親子は厳しい側が勝つ。どう書いてもチケットが無いときより
緩くはならない。

## 書式

`wip/tickets/<状態>/<識別子>.md` の先頭の frontmatter。設計 §24.3。
区画は rules.yml と同じ `deny` / `ask` / `allow` で、今効くのは Write / Edit 系の
パスの項だけ。`match` に Bash を書いた項は「効かない」と名指しで警告する。

    ---
    version: 1
    ticket: i0050-03
    parent: i0050
    phase: 2
    predecessors: [i0050-01]
    human_review: {required: true, reason: 設定の読み込み経路を変えるため}
    title: 設定画面の分割
    rationale: |
      Settings 配下のコンポーネント分割。
    allow:
      - match: Write|Edit|MultiEdit
        glob: "src/components/Settings/*"
    ask:
      - match: Write|Edit|MultiEdit
        glob: "src/components/*"
    started_at: ""
    completed_at: ""
    base_sha: ""
    ---

状態は frontmatter ではなく置き場（`todo` `doing` `done` `cancelled`）が表す。
`ls` で見え、コミットに残り、閉じたつもりが起きない。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import yaml

from . import globmatch, rules, tree
from .rules import SEVERITY_ERROR, SEVERITY_WARN, Problem

# frontmatter の囲い。
FENCE = "---"

# このビルドが読めるチケット書式の版。
VERSION = 1

# 状態。置き場の名前そのもの。
TODO = "todo"
DOING = "doing"
DONE = "done"
CANCELLED = "cancelled"
STATES = (TODO, DOING, DONE, CANCELLED)
# 閉じた状態。写しが closed/ へ動く。
CLOSED = (DONE, CANCELLED)
# 直接の作成・移動を止める置き場。todo/ への作成と編集は自由。
GUARDED_STATES = (DOING, DONE, CANCELLED)

# 範囲の項として効くツール。これ以外を match に書いた項は効かない。
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")

# 範囲の件数の上限。設計 §9.4 の max_ticket_rules。大量に並べて人のレビューを
# 潰し、その中に広い範囲を紛れ込ませる手口を防ぐためのもの。
MAX_SCOPE_ENTRIES = 20

# 範囲のパスに書けない綴り。`..` は範囲の外へ出る綴り、絶対パスと `~` は
# プロジェクトの外を指す綴り、`$` は展開されるまで行き先が決まらない綴り。
_FORBIDDEN = (("..", "`..`"), ("~", "`~`"), ("$", "`$`"))

# 識別子。親は自由な 1 語、子は `<親>-<2 桁連番>`。
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CHILD = re.compile(r"^(?P<parent>[A-Za-z0-9][A-Za-z0-9._-]*)-(?P<seq>\d{2})$")

# スクリプトだけが書く欄。人もエージェントも書かない。hook はこの 3 つ（と
# 取り消しの 2 つ）だけを提案から写しへ写す。
SCRIPT_FIELDS = ("started_at", "completed_at", "base_sha", "cancelled_at", "cancel_reason")

# 写しにだけある欄。承認の記録。
APPROVAL_KEY = "ccnavi_approved"

# glob のワイルドカード。これより前が字義どおりの前置。
_WILDCARDS = "*?["

# 範囲の判定。空文字は「言及していない」。
OUTSIDE = ""


@dataclass
class Entry:
    """範囲の 1 項。区画と、当てる式。"""

    decision: str
    glob: str = ""
    regex: str = ""
    compiled: re.Pattern | None = None

    def matches(self, rel: str) -> bool:
        if self.compiled is None:
            return False
        if self.glob and not any(c in self.glob for c in _WILDCARDS):
            # ワイルドカードの無い綴りは前置。`src` が範囲なら `src` という
            # 名前のファイルも `src/` の下も中。そこだけ外に落ちるのは驚きでしかない。
            return rel == self.glob or rel.startswith(self.glob + "/")
        return self.compiled.match(rel) is not None

    def prefix(self) -> str:
        """字義どおりの前置。部分集合の検査に使う。"""
        if self.regex:
            return ""
        cut = len(self.glob)
        for c in _WILDCARDS:
            i = self.glob.find(c)
            if i >= 0:
                cut = min(cut, i)
        return self.glob[:cut]


@dataclass
class Ticket:
    """チケット 1 本ぶん。提案としても写しとしても同じ形。"""

    ticket: str = ""
    parent: str = ""
    phase: int | None = None
    predecessors: list[str] = field(default_factory=list)
    review_required: bool = True
    review_reason: str = ""
    title: str = ""
    rationale: str = ""
    entries: list[Entry] = field(default_factory=list)
    # スクリプトが書く欄。
    started_at: str = ""
    completed_at: str = ""
    base_sha: str = ""
    cancelled_at: str = ""
    cancel_reason: str = ""
    # 読んだままの frontmatter。写しを作るときに使う。
    raw: dict = field(default_factory=dict)
    body: str = ""
    # 見つけた場所。提案なら状態と作業ツリー、写しなら承認の記録から。
    state: str = ""
    tree: str = ""
    tree_root: str = ""
    path: str = ""
    # 写しにだけある。
    approved_at: str = ""
    source_tree: str = ""
    source_path: str = ""
    risk: int = 0

    @property
    def is_child(self) -> bool:
        return bool(self.parent)

    def paths(self, decision: str) -> list[str]:
        return [e.glob or e.regex for e in self.entries if e.decision == decision]

    def decide(self, rel: str) -> str:
        """このチケットが、作業ツリーの根からの相対パスをどう扱うか。

        強い区画から見る。どこにも当たらなければ OUTSIDE で、それは範囲外。
        書いていない場所は範囲外、が子のファイルだけ読んで範囲が分かる条件。
        """
        for name in rules.SECTIONS:
            if any(e.decision == name and e.matches(rel) for e in self.entries):
                return name
        return OUTSIDE

    def is_own_file(self, full: str) -> bool:
        """この綴りがチケット自身の提案ファイルかどうか。"""
        for candidate in (self.path, self.source_path):
            if candidate and _same(candidate, full):
                return True
        return False


def load(path: str) -> tuple[Ticket | None, list[Problem]]:
    """チケットを読んで組み立てる。無いことは不備ではない。"""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return None, []
    except OSError as exc:
        return None, [Problem(SEVERITY_ERROR, path, f"チケットを読めない ({exc})")]
    ticket, problems = parse(text)
    if ticket is not None:
        ticket.path = os.path.realpath(path)
    return ticket, problems


def parse(text: str) -> tuple[Ticket | None, list[Problem]]:
    """読み込み済みの文面からチケットを組み立てる。

    ファイルを開く部分と分けてあるのは、テストと承認の画面が同じ道を通るため。
    """
    front, body, problems = _frontmatter(text)
    if front is None:
        return None, problems

    ticket = Ticket(
        ticket=_text(front.get("ticket")).strip(),
        parent=_text(front.get("parent")).strip(),
        title=_text(front.get("title")),
        rationale=_text(front.get("rationale")),
        raw=front,
        body=body,
    )
    if not ticket.ticket:
        problems.append(Problem(SEVERITY_ERROR, "ticket", "チケット識別子が無い"))
        return None, problems
    name = ticket.ticket

    version = front.get("version")
    if version != VERSION:
        problems.append(
            Problem(
                SEVERITY_ERROR,
                name,
                f"チケット書式の版 {version!r} は扱えない（このビルドが読むのは {VERSION}）。"
                "区画は rules.yml と同じ deny / ask / allow で、`target_directories` は読まない",
            )
        )
        return None, problems

    if not _ID.match(name):
        problems.append(Problem(SEVERITY_ERROR, name, "識別子に使えない文字がある"))
        return None, problems

    if ticket.parent:
        matched = _CHILD.match(name)
        if matched is None or matched.group("parent") != ticket.parent:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    name,
                    f"子の識別子は `{ticket.parent}-<2 桁連番>` の形で書く。親の識別子が名前空間",
                )
            )
            return None, problems
        phase = front.get("phase")
        if isinstance(phase, bool) or not isinstance(phase, int) or phase < 0:
            problems.append(Problem(SEVERITY_ERROR, name, "子には `phase`（0 以上の整数）が要る"))
            return None, problems
        ticket.phase = phase
    else:
        for key in ("phase", "predecessors"):
            if key in front:
                problems.append(
                    Problem(SEVERITY_WARN, name, f"`{key}` は子だけの欄。親では読まない")
                )

    raw_preds = front.get("predecessors")
    if isinstance(raw_preds, list):
        ticket.predecessors = [_text(p).strip() for p in raw_preds if _text(p).strip()]
    elif raw_preds is not None:
        problems.append(Problem(SEVERITY_ERROR, name, "`predecessors` は並びで書く"))
        return None, problems

    review = front.get("human_review")
    if isinstance(review, dict):
        required = review.get("required", True)
        if not isinstance(required, bool):
            problems.append(
                Problem(SEVERITY_ERROR, name, "`human_review.required` は true か false")
            )
            return None, problems
        ticket.review_required = required
        ticket.review_reason = _text(review.get("reason"))
    elif isinstance(review, bool):
        ticket.review_required = review
    elif review is not None:
        problems.append(
            Problem(SEVERITY_ERROR, name, "`human_review` は {required:, reason:} で書く")
        )
        return None, problems
    if not ticket.review_required and not ticket.review_reason:
        problems.append(
            Problem(
                SEVERITY_WARN, name, "人間レビューを省くなら `human_review.reason` に理由を書く"
            )
        )

    for key in ("tools", "deny_commands", "target_directories"):
        if key in front:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    name,
                    f"`{key}` はこの版の判定が使わない。書いても効かない。"
                    "範囲は deny / ask / allow の区画に `match: Write|Edit|MultiEdit` で書く",
                )
            )

    for key in SCRIPT_FIELDS:
        setattr(ticket, key, _text(front.get(key)).strip())

    entries, scope_problems = _entries(front, name)
    problems.extend(scope_problems)
    ticket.entries = entries

    if not any(e.decision in (rules.ALLOW, rules.ASK) for e in entries):
        problems.append(
            Problem(
                SEVERITY_ERROR,
                name,
                "allow にも ask にも、効く範囲の項が 1 件も無い。このチケットは承認しても"
                "何も書けない",
            )
        )
        return None, problems

    if len(entries) > MAX_SCOPE_ENTRIES:
        problems.append(
            Problem(
                SEVERITY_ERROR,
                name,
                f"範囲が {len(entries)} 件ある（上限 {MAX_SCOPE_ENTRIES} 件）。"
                "作業を分けるか、範囲をまとめること",
            )
        )
        return None, problems

    return ticket, problems


def render(ticket: Ticket, extra: dict | None = None) -> str:
    """チケットを文面に戻す。写しを作るときと、スクリプトが欄を書くときに使う。

    読んだ frontmatter をそのまま出す。並びが変わっても意味は変わらない。
    """
    front = dict(ticket.raw)
    if extra:
        front.update(extra)
    dumped = yaml.safe_dump(front, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"{FENCE}\n{dumped}{FENCE}\n{ticket.body}"


def subset_problems(child: Ticket, parent: Ticket) -> list[Problem]:
    """子が親の部分集合でない項を名指しする。

    glob 同士の包含は一般には決められないので、子の項の字義どおりの前置を
    親に当てる。前置が親の allow か ask に入っていれば中、入っていなければ外。
    `src/components/*` の前置は `src/components/`、`*` の前置は空文字で、
    空文字を中と言える親は `*` を持つ親だけになる。
    """
    problems = []
    for entry in child.entries:
        if entry.decision == rules.DENY:
            continue
        if entry.regex:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    child.ticket,
                    f"子の範囲に regex `{entry.regex}` は書けない。親の部分集合であることを"
                    "確かめられない",
                )
            )
            continue
        # ワイルドカードがあれば、前置に 1 文字足した綴りを親に当てる。`src/b/*` なら
        # `src/b/x`。無ければ綴りそのもの。前置の末尾の `/` を落として当てると、
        # 親の `src/b/*` が `src/b` に当たらず、正当な子を「超えている」と読む。
        probe = entry.prefix() + "x" if entry.glob != entry.prefix() else entry.glob
        verdict = parent.decide(probe)
        if verdict not in (rules.ALLOW, rules.ASK):
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    child.ticket,
                    f"`{entry.glob}` は親 {parent.ticket} の範囲を超えている",
                )
            )
    return problems


def combine(child: str, parent: str) -> str:
    """親子の判定を、厳しい側が勝つ形で合わせる。"""
    order = {rules.DENY: 3, rules.ASK: 2, rules.ALLOW: 1, OUTSIDE: 4}
    return child if order[child] >= order[parent] else parent


def scan(root: str, tickets_rel: str) -> tuple[list[Ticket], list[Problem]]:
    """main と全作業ツリーの提案を集める。状態と置き場を添える。"""
    found: list[Ticket] = []
    problems: list[Problem] = []
    for t in [tree.main_tree(root), *tree.worktrees(root)]:
        base = os.path.join(t.root, tickets_rel.replace("/", os.sep))
        for state in STATES:
            directory = os.path.join(base, state)
            try:
                names = sorted(os.listdir(directory))
            except OSError:
                continue
            # 大文字小文字を区別しない機械では `DONE/` が `done/` として開ける。
            # 置き場が状態なので、綴りまで同じディレクトリだけを読む。
            if os.path.basename(os.path.realpath(directory)) != state:
                problems.append(
                    Problem(
                        SEVERITY_WARN,
                        state,
                        f"{os.path.relpath(directory, root)} の綴りが状態の名前 `{state}` と"
                        "違うので読まない",
                    )
                )
                continue
            for name in names:
                if not name.endswith(".md"):
                    continue
                path = os.path.join(directory, name)
                ticket, complaints = load(path)
                for p in complaints:
                    p.rule = f"{p.rule} ({os.path.relpath(path, root)})"
                problems.extend(complaints)
                if ticket is None:
                    continue
                if ticket.ticket != name[:-3]:
                    problems.append(
                        Problem(
                            SEVERITY_ERROR,
                            ticket.ticket,
                            f"ファイル名 {name} と識別子 {ticket.ticket} が一致しない",
                        )
                    )
                    continue
                ticket.state, ticket.tree, ticket.tree_root = state, t.name, t.root
                found.append(ticket)
    return _dedupe(found), problems


def _dedupe(found: list[Ticket]) -> list[Ticket]:
    """同じ識別子が複数のツリーにあるとき、権威のあるツリーの側だけを残す。

    子の作業ツリーは親のブランチから切るので、親の `wip/tickets/` がそのまま
    写っている。権威は親のツリー（親自身なら自分のツリー）の側。そこに無い
    ときは全部残し、検証が「複数の場所にある」と言う。
    """
    by_id: dict[str, list[Ticket]] = {}
    for t in found:
        by_id.setdefault(t.ticket, []).append(t)
    kept: list[Ticket] = []
    for hits in by_id.values():
        home = hits[0].parent or hits[0].ticket
        at_home = [t for t in hits if t.tree == home]
        kept.extend(at_home if at_home else hits)
    return kept


def locate(root: str, tickets_rel: str, tree_root: str, ticket_id: str) -> tuple[str, str]:
    """この作業ツリーで、この識別子の提案がどの状態にあるか。無ければ空文字 2 つ。"""
    base = os.path.join(tree_root, tickets_rel.replace("/", os.sep))
    for state in STATES:
        path = os.path.join(base, state, ticket_id + ".md")
        if os.path.isfile(path):
            return state, path
    return "", ""


def state_dir_regex(tickets_rel: str) -> str:
    """直接の作成・移動を止める置き場に当たる式。パス用。"""
    parts = [re.escape(p) for p in tickets_rel.split("/") if p]
    joined = r"[\\/]".join(parts)
    return rf"(^|[\\/]){joined}[\\/]({'|'.join(GUARDED_STATES)})[\\/]"


def guard_rules(tickets_rel: str) -> list[rules.Rule]:
    """状態の置き場を守るルール。組み込みで、ルールファイルには書かない。

    通るのは状態を動かすスクリプトだけ。そのスクリプトの呼び出し文字列には
    置き場の綴りが現れないので、ここに当たらない。
    """
    from . import selfguard

    place = state_dir_regex(tickets_rel)
    message = (
        "チケットの状態は置き場（doing/ done/ cancelled/）で表し、動かすのは "
        "'sh .claude/scripts/ccnavi-ticket.sh start|done|cancel <識別子>' だけです。"
        "直接ファイルを作ったり動かしたりしないでください。todo/ への作成と編集は自由です。"
    )
    write_rule = rules.Rule(
        id=STATE_RULE_ID,
        match="|".join(WRITE_TOOLS),
        regex=place,
        message=message,
        decision=rules.DENY,
    )
    # 大文字小文字を区別しない機械では `DONE/` も同じ場所。区別する機械で余分に
    # 当たっても、状態の名前を大文字で書く正当な用事は無い。
    write_rule.compiled = re.compile(place, re.IGNORECASE)
    # シェルの側は前の区切りを求めない。コマンドの引数は空白で区切られていて、
    # 書き込む動詞の式が引数までを覆う。行き先は末尾の `/` が無い綴り
    # （`mv x wip/tickets/done`）でも当てる。
    parts = [re.escape(p) for p in tickets_rel.split("/") if p]
    states = "|".join(GUARDED_STATES)
    loose = r"[\\/]".join(parts) + rf"[\\/]({states})([\\/]|\s|$)"
    # 写す動詞は行き先が最後の引数。置き場から外へ写す読み向きの cp は止めない。
    last = r"[\\/]".join(parts) + rf"[\\/]({states})([\\/][^ \x00]*)?($|\x00)"
    shell = rf"{selfguard._WRITE_VERBS}{loose}|{selfguard._COPY_VERBS}{last}"
    shell_rule = rules.Rule(
        id=STATE_RULE_ID + "-shell",
        match="Bash|PowerShell",
        regex=shell,
        message=message,
        decision=rules.DENY,
    )
    shell_rule.compiled = re.compile(shell, re.IGNORECASE)
    return [write_rule, shell_rule]


def set_fields(text: str, fields: dict[str, str]) -> str:
    """提案の frontmatter の、スクリプトが書く欄だけを行単位で書き換える。

    読み直して書き出す（render）と、人が書いたコメントや `|` のブロックが
    消えて、親のブランチの diff が汚れる。提案は人も読むものなので、
    触るのは欄の行だけにする。無い欄は閉じの `---` の前に足す。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != FENCE:
        return text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == FENCE), None)
    if end is None:
        return text
    pending = {k: v for k, v in fields.items() if k in SCRIPT_FIELDS}
    for i in range(1, end):
        key = lines[i].split(":", 1)[0].strip()
        if key in pending and not lines[i].startswith((" ", "\t")):
            lines[i] = f"{key}: {_yaml_scalar(pending.pop(key))}"
    for key, value in pending.items():
        lines.insert(end, f"{key}: {_yaml_scalar(value)}")
        end += 1
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _yaml_scalar(value: str) -> str:
    """欄の値を、YAML が文字列として読み戻せる綴りにする。"""
    return (
        yaml.safe_dump(value, allow_unicode=True, default_style='"').strip().removesuffix("\n...")
    )


STATE_RULE_ID = "builtin-ticket-state"


def _entries(front: dict, name: str) -> tuple[list[Entry], list[Problem]]:
    problems: list[Problem] = []
    entries: list[Entry] = []
    for section in rules.SECTIONS:
        raw_section = front.get(section)
        if raw_section is None:
            continue
        if not isinstance(raw_section, list):
            problems.append(Problem(SEVERITY_ERROR, name, f"`{section}` が並びではない"))
            continue
        for i, raw in enumerate(raw_section):
            where = f"{section}[{i}]"
            if not isinstance(raw, dict):
                problems.append(Problem(SEVERITY_ERROR, name, f"{where} がキーと値の並びではない"))
                continue
            match = _text(raw.get("match"))
            tools = [w.strip() for w in match.split("|") if w.strip()]
            if not any(t in WRITE_TOOLS for t in tools):
                problems.append(
                    Problem(
                        SEVERITY_WARN,
                        name,
                        f"{where} の match `{match}` は書き込みのツールを含まない。"
                        "この版の判定はパスの範囲しか見ないので効かない",
                    )
                )
                continue
            glob = _text(raw.get("glob")).strip()
            regex = _text(raw.get("regex")).strip()
            if not glob and not regex:
                problems.append(Problem(SEVERITY_ERROR, name, f"{where} に glob も regex も無い"))
                continue
            if glob and regex:
                problems.append(
                    Problem(SEVERITY_ERROR, name, f"{where} に glob と regex の両方がある")
                )
                continue
            bad = _forbidden(glob or regex)
            if bad:
                problems.append(Problem(SEVERITY_ERROR, name, f"{where} の範囲に{bad}は書けない"))
                continue
            if glob and (os.path.isabs(glob) or (len(glob) > 1 and glob[1] == ":")):
                problems.append(
                    Problem(
                        SEVERITY_ERROR,
                        name,
                        f"{where} の範囲 `{glob}` が絶対パス。作業ツリーの根からの相対で書く",
                    )
                )
                continue
            glob = glob.replace("\\", "/").strip("/")
            expression = regex or ("^" + globmatch.translate(glob))
            try:
                compiled = re.compile(expression)
            except re.error as exc:
                problems.append(Problem(SEVERITY_ERROR, name, f"{where} を式にできない: {exc}"))
                continue
            entries.append(Entry(decision=section, glob=glob, regex=regex, compiled=compiled))
    return entries, problems


def _frontmatter(text: str) -> tuple[dict | None, str, list[Problem]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != FENCE:
        return None, "", [Problem(SEVERITY_ERROR, "(file)", f"先頭が `{FENCE}` で始まっていない")]
    for i in range(1, len(lines)):
        if lines[i].strip() == FENCE:
            head = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1 :])
            break
    else:
        return (
            None,
            "",
            [Problem(SEVERITY_ERROR, "(file)", f"frontmatter が `{FENCE}` で閉じていない")],
        )
    try:
        # safe_load に限る。任意の Python の型を組み立てる load は、
        # エージェントが書けるファイルに向けては使えない。
        front = yaml.safe_load(head)
    except yaml.YAMLError as exc:
        return (
            None,
            "",
            [Problem(SEVERITY_ERROR, "(file)", f"frontmatter を YAML として読めない ({exc})")],
        )
    if front is None:
        return None, "", [Problem(SEVERITY_ERROR, "(file)", "frontmatter が空")]
    if not isinstance(front, dict):
        return None, "", [Problem(SEVERITY_ERROR, "(file)", "frontmatter がキーと値の並びではない")]
    return front, body + ("\n" if body and not body.endswith("\n") else ""), []


def _forbidden(path: str) -> str:
    for literal, label in _FORBIDDEN:
        if literal in path:
            return label
    return ""


def _text(value: object) -> str:
    """YAML から来た値を文字列にする。数字だけの識別子は int で返ってくる。"""
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    return str(value)


def _same(a: str, b: str) -> bool:
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except OSError:
        return False
