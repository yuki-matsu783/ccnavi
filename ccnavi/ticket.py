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
タイプは rules.yml と同じ `deny` / `ask` / `allow` で、今効くのは Write / Edit 系の
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
      - match: Write|Edit
        glob: "src/components/Settings/*"
    ask:
      - match: Write|Edit
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

from . import globmatch, rules, selfguard, tree
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
WRITE_TOOLS = ("Write", "Edit", "NotebookEdit")

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


def _plan(name: str, key: str, raw) -> tuple[list[PlanItem] | None, list[Problem]]:
    """計画の並びを読む。種類が在るかはここでは見ない（種類を読むのは承認の側）。"""
    problems: list[Problem] = []
    if not isinstance(raw, list):
        problems.append(Problem(SEVERITY_ERROR, name, f"`{key}` は並びで書く"))
        return None, problems
    items: list[PlanItem] = []
    for i, entry in enumerate(raw):
        where = f"{key}[{i}]"
        if isinstance(entry, str) and entry.strip():
            items.append(PlanItem(type=entry.strip()))
            continue
        if isinstance(entry, dict):
            kind = _text(entry.get("type")).strip()
            review = _text(entry.get("review")).strip()
            if not kind:
                problems.append(Problem(SEVERITY_ERROR, name, f"{where} に `type` が無い"))
                return None, problems
            if review and review not in PLAN_REVIEWS:
                problems.append(
                    Problem(
                        SEVERITY_ERROR,
                        name,
                        f"{where} の `review` は {' か '.join(PLAN_REVIEWS)}。"
                        "弱める向き（none）は書けない",
                    )
                )
                return None, problems
            items.append(PlanItem(type=kind, review=review))
            continue
        problems.append(Problem(SEVERITY_ERROR, name, f"{where} は種類の名前か {{type, review}}"))
        return None, problems
    if items and items[-1].deferred:
        problems.append(
            Problem(SEVERITY_ERROR, name, f"`{key}` の最後の項は延期できない。誰も見ないまま終わる")
        )
        return None, problems
    return items, problems


def _issue_number(raw) -> int | None:
    """課題の番号。`12` でも `"#12"` でも読む。読めなければ None。"""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    text = str(raw).strip().lstrip("#").strip()
    if not text.isdigit():
        return None
    number = int(text)
    return number if number > 0 else None


def _fold(path: str) -> str:
    """比べるための綴り。範囲の照合はどの機械でも大文字小文字を区別しない。"""
    return path.lower()


# 範囲の判定。空文字は「言及していない」。
OUTSIDE = ""


@dataclass
class Entry:
    """範囲の 1 項。タイプと、当てる式。"""

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
            # 大文字小文字は揃えてから比べる。機械によって区別の有無が変わると、
            # 同じチケットと同じ綴りで止まる場所が Windows と Linux で食い違う。
            # 範囲は人が宣言する意図なので、機械の都合ではなく綴りの意味で読む。
            here, there = _fold(rel), _fold(self.glob)
            return here == there or here.startswith(there + "/")
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


# 計画の項が書けるレビューの指定。`mr` は種類が none でも要る（強める）、`defer` は
# 次にレビューがあるフェーズと一緒に見る（延期）。弱める向き（none）は書けない。
PLAN_REVIEW_MR = "mr"
PLAN_REVIEW_DEFER = "defer"
PLAN_REVIEWS = (PLAN_REVIEW_MR, PLAN_REVIEW_DEFER)


@dataclass
class PlanItem:
    """親の計画の 1 項。フェーズの種類の名前と、レビューの指定。"""

    type: str
    review: str = ""

    @property
    def deferred(self) -> bool:
        return self.review == PLAN_REVIEW_DEFER

    def as_raw(self):
        return {"type": self.type, "review": self.review} if self.review else self.type

    def __eq__(self, other) -> bool:
        return (
            isinstance(other, PlanItem) and self.type == other.type and self.review == other.review
        )


@dataclass
class Ticket:
    """チケット 1 本ぶん。提案としても写しとしても同じ形。"""

    ticket: str = ""
    parent: str = ""
    phase: int | None = None
    predecessors: list[str] = field(default_factory=list)
    # issue は元になった課題の番号。親だけが持つ。マージリクエストを作るときに
    # `Closes #<番号>` へ写す。無くても動く。
    issue: int | None = None
    # project は作業のプロジェクト（`projects/` の名前、設計 §25.5）。決めるのは提案を
    # 置いた場所で、`scan` が入れる（`wip/<名前>/tickets/` ならその名前、作業ツリーの中なら
    # その切り元、ワークスペースの `wip/tickets/` なら空）。親も子も同じ置き場に並ぶので、
    # 継ぐ段は無い。判定は行き先の作業ツリーの切り元と突き合わせる。
    project: str = ""
    # declared_project は frontmatter に人が書いた `project:`。宣言ではなく照合に使う。
    # 置き場と違えば承認しない（approval.project_problems）。`scan` を通さずに読んだとき
    # （`load` を直に呼ぶ経路）は project と同じ値になる。
    declared_project: str = ""
    # plan は全体計画（作業フェーズの種類の並び）、feedback はフィードバック計画。
    # 親だけが持つ。feedback が None なのは「まだ計画していない」、[] は
    # 「見たうえで対応なし」。設計 §24.15.2。
    plan: list[PlanItem] = field(default_factory=list)
    feedback: list[PlanItem] | None = None
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

    @property
    def is_child(self) -> bool:
        return bool(self.parent)

    @property
    def has_plan(self) -> bool:
        return bool(self.plan)

    def numbered(self) -> list[tuple[int, PlanItem]]:
        """計画の項に番号を振る。全体計画が 1 から、フィードバック計画はその続き。"""
        items = list(self.plan) + list(self.feedback or [])
        return [(i + 1, item) for i, item in enumerate(items)]

    def item_at(self, number: int) -> PlanItem | None:
        for n, item in self.numbered():
            if n == number:
                return item
        return None

    def review_at(self, number: int) -> int | None:
        """この番号のフェーズのレビューが行われる番号。延期なら次にレビューがある番号。

        延期の連鎖の先が無ければ None（計画が壊れている）。
        """
        items = self.numbered()
        for n, item in items:
            if n >= number and not item.deferred:
                return n
        return None

    def covered_by(self, number: int) -> list[int]:
        """この番号のレビューが含む、それより前の延期したフェーズの番号。"""
        found = []
        for n, item in self.numbered():
            if n >= number:
                break
            if item.deferred and self.review_at(n) == number:
                found.append(n)
        return found

    def paths(self, decision: str) -> list[str]:
        return [e.glob or e.regex for e in self.entries if e.decision == decision]

    def decide(self, rel: str) -> str:
        """このチケットが、作業ツリーのルートからの相対パスをどう扱うか。

        強いタイプから見る。どこにも当たらなければ OUTSIDE で、それは範囲外。
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

    欄は種類ごとに読み手を分けてある。どれかが「これ以上読めない」と言えば
    そこで止め、それまでの苦情を全部返す。苦情の並びは欄の並びのまま。
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

    for read in (_read_identity, _read_relations, _read_review, _read_scope):
        if read(ticket, front, problems):
            return None, problems
    return ticket, problems


def _read_identity(ticket: Ticket, front: dict, problems: list[Problem]) -> bool:
    """版と識別子と親子の形。読めなければ True。"""
    name = ticket.ticket
    version = front.get("version")
    if version != VERSION:
        problems.append(
            Problem(
                SEVERITY_ERROR,
                name,
                f"チケット書式の版 {version!r} は扱えない（このビルドが読むのは {VERSION}）。"
                "タイプは rules.yml と同じ deny / ask / allow で、`target_directories` は読まない",
            )
        )
        return True

    if not _ID.match(name):
        problems.append(Problem(SEVERITY_ERROR, name, "識別子に使えない文字がある"))
        return True

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
            return True
        phase = front.get("phase")
        if isinstance(phase, bool) or not isinstance(phase, int) or phase < 0:
            problems.append(Problem(SEVERITY_ERROR, name, "子には `phase`（0 以上の整数）が要る"))
            return True
        ticket.phase = phase
    else:
        for key in ("phase", "predecessors"):
            if key in front:
                problems.append(
                    Problem(SEVERITY_WARN, name, f"`{key}` は子だけの欄。親では読まない")
                )
    return False


def _read_relations(ticket: Ticket, front: dict, problems: list[Problem]) -> bool:
    """プロジェクト、先行、計画、課題の番号。読めなければ True。"""
    name = ticket.ticket
    # frontmatter の `project:` は照合用の宣言。本当のプロジェクトは提案を置いた場所で、
    # `scan` が上書きする（設計 §25.5）。`scan` を通さない経路ではこの値が残る。
    ticket.declared_project = _text(front.get("project")).strip()
    ticket.project = ticket.declared_project

    raw_preds = front.get("predecessors")
    if isinstance(raw_preds, list):
        ticket.predecessors = [_text(p).strip() for p in raw_preds if _text(p).strip()]
    elif raw_preds is not None:
        problems.append(Problem(SEVERITY_ERROR, name, "`predecessors` は並びで書く"))
        return True

    for key in ("plan", "feedback"):
        raw_plan = front.get(key)
        if raw_plan is None:
            continue
        if ticket.is_child:
            problems.append(Problem(SEVERITY_WARN, name, f"`{key}` は親だけの欄。子では読まない"))
            continue
        items, bad = _plan(name, key, raw_plan)
        problems.extend(bad)
        if items is None:
            return True
        if key == "plan":
            ticket.plan = items
        else:
            ticket.feedback = items

    raw_issue = front.get("issue")
    if raw_issue is not None:
        number = _issue_number(raw_issue)
        if number is None:
            problems.append(
                Problem(SEVERITY_ERROR, name, "`issue` は課題の番号（正の整数）で書く。`#12` も可")
            )
            return True
        if ticket.is_child:
            problems.append(Problem(SEVERITY_WARN, name, "`issue` は親だけの欄。子では読まない"))
        else:
            ticket.issue = number
    return False


def _read_review(ticket: Ticket, front: dict, problems: list[Problem]) -> bool:
    """人間レビューの要否と理由。読めなければ True。"""
    name = ticket.ticket
    review = front.get("human_review")
    if isinstance(review, dict):
        required = review.get("required", True)
        if not isinstance(required, bool):
            problems.append(
                Problem(SEVERITY_ERROR, name, "`human_review.required` は true か false")
            )
            return True
        ticket.review_required = required
        ticket.review_reason = _text(review.get("reason"))
    elif isinstance(review, bool):
        ticket.review_required = review
    elif review is not None:
        problems.append(
            Problem(SEVERITY_ERROR, name, "`human_review` は {required:, reason:} で書く")
        )
        return True
    if not ticket.review_required and not ticket.review_reason:
        problems.append(
            Problem(
                SEVERITY_WARN, name, "人間レビューを省くなら `human_review.reason` に理由を書く"
            )
        )
    return False


def _read_scope(ticket: Ticket, front: dict, problems: list[Problem]) -> bool:
    """効かない欄への注意、スクリプトの欄、範囲の項。範囲が成り立たなければ True。"""
    name = ticket.ticket
    for key in ("tools", "deny_commands", "target_directories"):
        if key in front:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    name,
                    f"`{key}` はこの版の判定が使わない。書いても効かない。"
                    "範囲は deny / ask / allow のタイプに `match: Write|Edit` で書く",
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
        return True

    if len(entries) > MAX_SCOPE_ENTRIES:
        problems.append(
            Problem(
                SEVERITY_ERROR,
                name,
                f"範囲が {len(entries)} 件ある（上限 {MAX_SCOPE_ENTRIES} 件）。"
                "作業を分けるか、範囲をまとめること",
            )
        )
        return True
    return False


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


def tickets_rel_for(tickets_rel: str, project: str) -> str:
    """このプロジェクトの提案の置き場。ワークスペースルートからの相対（設計 §25.5）。

    `wip/tickets` なら `wip/<project>/tickets`。プロジェクトの名前は最初の区切りの
    あとに挟む。プロジェクトのリポジトリの中には書かない。そこは public で、提案は
    ワークスペースの運用の痕跡だから（REQ-MLT-14）。空の名前ならそのまま。
    """
    if not project:
        return tickets_rel
    parts = [p for p in tickets_rel.split("/") if p]
    if len(parts) < 2:
        return "/".join([project, *parts])
    return "/".join([parts[0], project, *parts[1:]])


def project_segment(tickets_rel: str) -> int:
    """`tickets_rel_for` がプロジェクトの名前を挟む位置（区切りで数えて 0 始まり）。"""
    parts = [p for p in tickets_rel.split("/") if p]
    return 0 if len(parts) < 2 else 1


def scan(root: str, tickets_rel: str, projects_dir: str = "") -> tuple[list[Ticket], list[Problem]]:
    """main と全作業ツリーの提案を集める。状態と置き場を添える。

    プロジェクト向けの提案はワークスペースルートの `wip/<project>/tickets/` にある。
    作業ツリーの側には無い（プロジェクトのブランチには wip/ が無い）。
    同じ識別子が複数のツリーにあれば、権威のあるツリーの側だけを残す。
    """
    found, problems = scan_all(root, tickets_rel, projects_dir)
    return dedupe(found), problems


def scan_all(
    root: str, tickets_rel: str, projects_dir: str = ""
) -> tuple[list[Ticket], list[Problem]]:
    """main と全作業ツリーの提案を、重複を畳まずに集める。

    ボード（`--explain --json`）が「どのツリーに写っているか」を見せるために使う。
    判定と承認は `scan` の畳んだ側を読む。
    """
    found: list[Ticket] = []
    problems: list[Problem] = []
    ws = tree.main_tree(root)
    # 置き場がプロジェクトを決める（設計 §25.5）。ワークスペースの `wip/tickets/` は
    # ワークスペース自身、`wip/<名前>/tickets/` はそのプロジェクト、作業ツリーの中は
    # その作業ツリーの切り元。frontmatter の `project:` は照合に使うだけ。
    # 4 つめは、名前が綴りどおりか確かめるディレクトリ。プロジェクトの名前が path の
    # 区切りに出るのはワークスペースの側だけで、作業ツリーの側は切り元から決まる。
    places = [(t, tickets_rel, t.project, "") for t in [ws, *tree.worktrees(root, projects_dir)]]
    for p in tree.projects(projects_dir):
        rel = tickets_rel_for(tickets_rel, p.name)
        parts = [q for q in rel.split("/") if q]
        segment = os.path.join(ws.root, *parts[: project_segment(tickets_rel) + 1])
        places.append((ws, rel, p.name, segment))
    for t, rel, place_project, segment in places:
        base = os.path.join(t.root, rel.replace("/", os.sep))
        # 大文字小文字を区別しない機械では `wip/Lib/` が `wip/lib/` として開ける。
        # 置き場がプロジェクトを決めるので、綴りまで同じディレクトリだけを読む。
        if segment and os.path.basename(os.path.realpath(segment)) != place_project:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    place_project,
                    f"{os.path.relpath(segment, root)} の綴りがプロジェクトの名前 "
                    f"`{place_project}` と違うので読まない",
                )
            )
            continue
        if place_project and t.kind == tree.KIND_WORKTREE:
            # プロジェクトのリポジトリの中に提案は置かない（REQ-MLT-14）。読みはするが言う。
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    place_project,
                    f"作業ツリー {t.name}（{place_project} から切った）の中に提案がある。"
                    f"プロジェクトの提案はワークスペースの "
                    f"{tickets_rel_for(tickets_rel, place_project)}/ に置く",
                )
            )
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
                ticket.project = place_project
                if place_project and not ticket.declared_project:
                    # 写しにも残す。judge は親の写しを引けないとき（親が閉じた）子の写しの
                    # `project` を見る。ここで入れないとその落ち先が空になる。
                    ticket.raw["project"] = place_project
                found.append(ticket)
    return found, problems


def dedupe(found: list[Ticket]) -> list[Ticket]:
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


def _place(tickets_rel: str) -> str:
    """置き場の綴りに当たる式。プロジェクトの名前を挟んだ形（`wip/<name>/tickets`）にも当たる。"""
    parts = [re.escape(p) for p in tickets_rel.split("/") if p]
    optional = r"(?:[^\\/\s\x00]+[\\/])?"
    if len(parts) < 2:
        return optional + r"[\\/]".join(parts)
    return parts[0] + r"[\\/]" + optional + r"[\\/]".join(parts[1:])


def state_dir_regex(tickets_rel: str) -> str:
    """直接の作成・移動を止める置き場に当たる式。パス用。"""
    return rf"(^|[\\/]){_place(tickets_rel)}[\\/]({'|'.join(GUARDED_STATES)})[\\/]"


def guard_rules(tickets_rel: str) -> list[rules.Rule]:
    """状態の置き場を守るルール。組み込みで、ルールファイルには書かない。

    通るのは状態を動かすスクリプトだけ。そのスクリプトの呼び出し文字列には
    置き場の綴りが現れないので、ここに当たらない。
    """
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
    states = "|".join(GUARDED_STATES)
    loose = _place(tickets_rel) + rf"[\\/]({states})([\\/]|\s|$)"
    # 写す動詞は行き先が最後の引数。置き場から外へ写す読み向きの cp は止めない。
    last = _place(tickets_rel) + rf"[\\/]({states})([\\/][^ \x00]*)?($|\x00)"
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
                        f"{where} の範囲 `{glob}` が絶対パス。作業ツリーのルートからの相対で書く",
                    )
                )
                continue
            glob = glob.replace("\\", "/").strip("/")
            expression = regex or ("^" + globmatch.translate(glob))
            # `src/Components/*` と `src/components/*` は同じ範囲として扱う。
            # 当てる側だけ区別すると、宣言した範囲に自分のファイルが入らない、が
            # 起きる。機械ごとに変えないのは、同じチケットがどの環境でも同じ場所で
            # 止まるため。regex は書いた人が意図を持てるので、そこだけ区別を残す。
            flags = 0 if regex else re.IGNORECASE
            try:
                compiled = re.compile(expression, flags)
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
