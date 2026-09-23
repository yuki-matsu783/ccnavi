"""作業チケットの読み込みと、そこが宣言する作業範囲。

## チケットは提案であって権威ではない

チケットはエージェントが書く。だからチケットの中身をそのまま判定に使うと、
範囲の外で止められたエージェントが、チケットに 1 行足して自分の首輪を伸ばせる。
止められた側が止め方を書き換えられる仕組みは、止めていない。

判定が使うのは承認済みチケット（approval.py）だけ。作業ツリーの提案は、人に見せて
承認を求めるためのもので、承認されるまで判定には効かない。承認されたあとに
提案を書き換えても、効いているのは承認済みチケットの側なので範囲は広がらない。

## 絞ることしかできない

チケットが宣言できるのは「ここだけ書く」であって「ここも書ける」ではない。
判定はルールの判定とチケットの判定の厳しい側を採る（設計 §1）。チケットが足すのは
「宣言した範囲の外は止める」「deny と書いた場所は止める」「ask と書いた場所は聞く」だけで、
ルールの allow を狭めることはあっても、ルールの deny や ask を緩めることは無い。
例外は 2 つ（`is_unscoped`）。チケットの置き場は、次の提案を書く道を残すために範囲を当てない。
下書きの置き場（`scratchpad/`）は、git が追跡しないので範囲を当てない。
子は親の部分集合で、親子は厳しい側が勝つ。どう書いてもチケットが無いときより
緩くはならない。

## 書式

`wip/proposals/<状態>/<識別子>.md` の先頭の frontmatter。設計 §9.3。
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

状態は frontmatter ではなく置き場が表す（ADR-0055）。チケットは 1 本のファイルで、
提案の置き場（`wip/proposals/`）と承認済みチケットの置き場（`.ccnavi/approved/`）を
行き来する。人が動かす向きは承認済みの側へ、エージェントが動かす向きは提案の側へ。

    wip/proposals/todo      承認待ち。エージェントが書く
    .ccnavi/approved/doing  承認済み。判定が範囲を読むのはここだけ
    wip/proposals/review    作業が終わり、人のレビューを待つ
    .ccnavi/approved/done   閉じた（取り消しは cancelled_at を持ってここに入る）

`ls` で見え、コミットに残り、閉じたつもりの食い違いが起きない。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import TextIO

import yaml

from . import ctxfile, globmatch, hookio, rules, selfguard, settings, tree
from .rules import SEVERITY_ERROR, SEVERITY_WARN, Problem

# frontmatter の囲い。
FENCE = "---"

# BOM (U+FEFF)。`str.strip()` は空白と見なさないので、付いていると先頭の `---` が
# `---` と一致しない。目に見えないので、弾くときは原因を名指しする（_frontmatter）。
BOM = "\ufeff"

# このビルドが読めるチケット書式の版。
VERSION = 1

# 状態。置き場の名前そのもの（ADR-0055）。
# 提案の置き場（`wip/proposals/`）に並ぶのは todo と review。
TODO = "todo"
REVIEW = "review"
# 承認済みチケットの置き場（`.ccnavi/approved/`）に並ぶのは doing と done。
DOING = "doing"
DONE = "done"
# 取り消しは置き場ではなく欄。`done/` に在って `cancelled_at` を持つものをこう呼ぶ。
CANCELLED = "cancelled"
# 提案の置き場を走査する状態。承認済みチケットの置き場は approval.py が読む。
STATES = (TODO, REVIEW)
PROPOSAL_STATES = STATES
APPROVED_STATES = (DOING, DONE)
# 閉じた状態。
CLOSED = (DONE, CANCELLED)
# 終わった状態。フェーズの終わりはこれで数える（レビュー待ちも作業としては終わっている）。
FINISHED = (REVIEW, DONE, CANCELLED)
# 直接の作成・移動を止める置き場。todo/ への作成と編集は自由。
GUARDED_STATES = (REVIEW,)

# 範囲の項として効くツール。これ以外を match に書いた項は効かない。
WRITE_TOOLS = ("Write", "Edit", "NotebookEdit")

# 範囲の件数の上限。設計 §9.3。大量に並べて人のレビューを
# 潰し、その中に広い範囲を紛れ込ませる手口を防ぐためのもの。
MAX_SCOPE_ENTRIES = 20

# 範囲のパスに書けない綴り。`..` は範囲の外へ出る綴り、絶対パスと `~` は
# プロジェクトの外を指す綴り、`$` は展開されるまで行き先が決まらない綴り。
_FORBIDDEN = (("..", "`..`"), ("~", "`~`"), ("$", "`$`"))

# 識別子。親は自由な 1 語、子は `<親>-<2 桁連番>`。
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CHILD = re.compile(r"^(?P<parent>[A-Za-z0-9][A-Za-z0-9._-]*)-(?P<seq>\d{2})$")


def child_pattern() -> re.Pattern:
    """子の識別子の形（`<親>-<2 桁連番>`）。承認の側が次の連番を数えるのに使う。"""
    return _CHILD


# スクリプトだけが書く欄。人もエージェントも書かない。承認済みチケットの側で
# 行単位に書き換える（`set_fields`）。
SCRIPT_FIELDS = ("started_at", "completed_at", "base_sha", "cancelled_at", "cancel_reason")

# 承認済みチケットにだけある欄。承認の記録。
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
    """チケット 1 本ぶん。提案としても承認済みチケットとしても同じ形。"""

    ticket: str = ""
    parent: str = ""
    phase: int | None = None
    predecessors: list[str] = field(default_factory=list)
    # issue は元になった課題の番号。親だけが持つ。マージリクエストを作るときに
    # `Closes #<番号>` へ写す。無くても動く。
    issue: int | None = None
    # project は作業のプロジェクト（`projects/` の名前、設計 §11.5）。決めるのは提案を
    # 置いた場所で、`scan` が入れる（プロジェクトの `wip/proposals/` ならその名前、ワークツリー
    # の中ならその元リポジトリ、ワークスペースの `wip/proposals/` なら空）。親も子も同じ置き場に
    # 並ぶので、継ぐ段は無い。判定は行き先のワークツリーの元リポジトリと突き合わせる。
    project: str = ""
    # declared_project は frontmatter に人が書いた `project:`。宣言ではなく照合に使う。
    # 置き場と違えば承認しない（approval.project_problems）。`scan` を通さずに読んだとき
    # （`load` を直に呼ぶ経路）は project と同じ値になる。
    declared_project: str = ""
    # plan は全体計画（作業フェーズの種類の並び）、feedback はフィードバック計画。
    # 親だけが持つ。feedback が None なのは「まだ計画していない」、[] は
    # 「見たうえで対応なし」。設計 §9.7。
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
    # 読んだままの frontmatter。承認済みチケットを作るときに使う。
    raw: dict = field(default_factory=dict)
    body: str = ""
    # 見つけた場所。提案なら状態とワークツリー、承認済みチケットなら承認の記録から。
    state: str = ""
    tree: str = ""
    tree_root: str = ""
    path: str = ""
    # 承認済みチケットにだけある。`ccnavi_approved` を持たない（人が置き場を動かしただけの）
    # チケットでは空になる。承認の権威は置き場で、この欄は記録（設計 §9.2）。
    approved_at: str = ""
    source_tree: str = ""
    source_path: str = ""
    # blocked は「このチケットは読めるが信じられない」理由。空でなければ判定は範囲を
    # 当てずに止める（phase.scope_verdict）。承認のときにしか当たらなかった構造の検査を、
    # 判定の側でも当てるために置く（ADR-0058）。
    blocked: str = ""

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
        """このチケットが、ワークツリーのルートからの相対パスをどう扱うか。

        強いタイプから見る。どこにも当たらなければ OUTSIDE で、それは範囲外。
        書いていない場所は範囲外、が子のファイルだけ読んで範囲が分かる条件。
        """
        for name in rules.SECTIONS:
            if any(e.decision == name and e.matches(rel) for e in self.entries):
                return name
        return OUTSIDE

    def entry_for(self, rel: str) -> Entry | None:
        """decide がその判定の根拠にした項。どの項にも当たらなければ None。

        判定の文面で「どの項に当たったか」を名指しするために使う。見る順は decide と同じ。
        """
        for name in rules.SECTIONS:
            for e in self.entries:
                if e.decision == name and e.matches(rel):
                    return e
        return None


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
    # `scan` が上書きする（設計 §11.5）。`scan` を通さない経路ではこの値が残る。
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
    """チケットを文面に戻す。承認済みチケットを作るときと、スクリプトが欄を書くときに使う。

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

    超えていても承認は止めない（warn）。判定が親の範囲で切り詰めるので、承認で止める
    理由が無い。承認の画面は「チケットで編集対象としているが、書き込めない場所」として別の見出しで見せる。
    """
    problems = []
    for entry in child.entries:
        if entry.decision == rules.DENY:
            continue
        if entry.regex:
            problems.append(
                Problem(SEVERITY_WARN, child.ticket, regex_overflow_detail(entry.regex))
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
                    SEVERITY_WARN,
                    child.ticket,
                    f"`{entry.glob}` は親 {parent.ticket} の範囲を超えている",
                )
            )
    return problems


def regex_overflow_detail(regex: str) -> str:
    """子の範囲の regex を名指しする文。親の検査と種類の検査の両方が同じ文を使う。

    同じ文にしておけば、承認の画面で 2 度並べずに畳める。
    """
    return f"子の範囲に regex `{regex}` は書けない。親と種類の上限に入るかを判定のときに当てる"


def combine(child: str, parent: str) -> str:
    """親子の判定を、厳しい側が勝つ形で合わせる。"""
    order = {rules.DENY: 3, rules.ASK: 2, rules.ALLOW: 1, OUTSIDE: 4}
    return child if order[child] >= order[parent] else parent


def is_ticket_place(rel: str, tickets_rel: str, approved_rel: str) -> bool:
    """ツリーのルートからの相対パスが、チケットの置き場の下にあるか。

    置き場は提案の置き場（`CCNAVI_TICKETS_PROPOSAL`）と承認済みチケットの置き場
    （`CCNAVI_TICKETS_APPROVED`）。
    ここはチケットの範囲の外でも咎めない。咎めると、親が自分のワークツリーに次の子を
    提案する道と、承認がブランチに乗る道が塞がる。

    外して開くのは提案の `todo/` だけ。`review/` は `guard_rules`、承認済みチケットは
    自己防衛の組み込みが deny で止め、ルールの deny はチケットより強い。

    前置は `/` の境で切る（`wip/proposalsX/` は置き場ではない）。大文字小文字は範囲の照合と
    同じく、どの機械でも区別しない。

    実行前の判定は `is_unscoped` を通ってここへ来る。実行後の監視とサブエージェント終了時の
    検査は直に呼ぶ（`post._script_writes` / `post._committed_findings` / `post.ScopeGuard.finding`、
    `phase.scope_findings`）。

    **実行後の監視から呼ぶときは、後ろに組み込みは控えていない。** 組み込みを足すのは
    `judge` だけで、実行後のルール集合には入らない。だから呼び出しごとの監視は、置き場を
    そのまま外さずに、内容で外すぶんを決める（`script_shape`、`post._script_writes`）。
    """
    return any(_under(rel, place) for place in (tickets_rel, approved_rel))


def _under(rel: str, place_rel: str) -> bool:
    """ツリーのルートからの相対パスが、その置き場の下にあるか。

    前置は `/` の境で切る（`wip/proposalsX/` は置き場ではない）。大文字小文字は範囲の照合と
    同じく、どの機械でも区別しない。
    """
    base = _fold(place_rel.replace("\\", "/").strip("/"))
    return bool(base) and _fold(rel.replace("\\", "/")).startswith(base + "/")


def leaves_open_state(rel: str, tickets_rel: str, approved_rel: str) -> bool:
    """チケットが `finish` / `cancel` / 人のレビューで出ていく元（作業中とレビュー待ち）か。

    移動の組を数えるとき、消えた側がここに居たことを求める。求めないと、承認待ちの提案
    （`todo/`）を `review/` に置き直す形——人の承認を通っていないものを、レビュー待ちに
    見せる形——が移動として外れる。
    """
    return _under(rel, f"{approved_rel}/{DOING}") or _under(rel, f"{tickets_rel}/{REVIEW}")


def lands_in_finished_state(rel: str, tickets_rel: str, approved_rel: str) -> bool:
    """`finish` と `cancel` がチケットを動かす先（レビュー待ちと閉じた置き場）か。

    実行後の監視が、スクリプトの移動（`doing/` から出ていく）とただの削除を見分けるのに使う。
    行き先をこの 2 つに絞るのは、`doing/` から出したチケットを `todo/` に置き直す形が
    「承認済みチケットを消す」のと同じ効き目を持つから。承認済みチケットが 1 本も無い
    ワークツリーは範囲を持たず、範囲を持たないツリーはチケットの側から何も言われない。
    """
    return _under(rel, f"{tickets_rel}/{REVIEW}") or _under(rel, f"{approved_rel}/{DONE}")


# 下書きと使い捨ての置き場。ツリーのルートの直下 1 段で、名前は固定。設定で動かさない。
# 動かせると、その値を実際のソースの置き場（`src` など）に向けるだけで、承認した範囲を
# 迂回して書ける場所ができる。除外してよい理由が「git が追跡しない」ことにある以上、
# 追跡から外しているワークスペースの `.gitignore` の 1 行と同じ綴りに固定するほうが筋が通る。
SCRATCH = "scratchpad"


def is_scratch_place(rel: str) -> bool:
    """ツリーのルートからの相対パスが、下書きの置き場の下にあるか。

    `scratchpad/` は `.gitignore` が追跡から外す置き場で、下書き・再現用のスクリプト・調べた
    出力を置く（CLAUDE.md「下書きと使い捨ての置き場」）。チケットの範囲の外でも咎めない。
    咎めると、範囲を宣言したワークツリーほど手元に何も置けなくなり、承認が要る作業だけが
    下書きの場所を失う。開けても範囲は広がらない。ここに書いたものは git が追跡しないので、
    統合先のブランチには 1 バイトも乗らない。

    **綴りの大文字小文字は区別する。範囲の照合（`_fold`）とは逆にしてある。** 外してよい
    理由が「追跡されない」ことにあり、追跡から外しているのは `.gitignore` の `/scratchpad/` で、
    その照合は Linux では区別するため。区別せずに外すと、Linux の `SCRATCHPAD/` が「追跡される
    のに範囲を当てない場所」になり、承認した範囲の外の変更が統合先へ乗る道ができる。
    区別する側に倒せば、どの機械でも除外は追跡から外れる範囲より狭いままで、狭いぶんは
    範囲の判定が止めるだけで済む。

    ルートの直下 1 段だけを見る。`docs/scratchpad/` は普通の作業対象で、`.gitignore` も外さない
    （`/scratchpad/` の先頭の `/` はツリーのルートに掛かる）。`scratchpad` という名前の
    ファイルも置き場ではない（末尾の `/` はディレクトリにしか当たらない）。
    `scratchpadX/` も置き場ではない。
    """
    return rel.replace("\\", "/").startswith(SCRATCH + "/")


def is_unscoped(rel: str, tickets_rel: str, approved_rel: str) -> bool:
    """チケットの範囲を当てない場所か。**実行前の判定（`judge`）だけが使う。**

    実行前は、これから書かれる 1 つのパスを見る。下書きの置き場を外すのはここだけで
    足りる。ここで通せば下書きは書けるので、これが機能の全部になる。

    実行後の監視（`post`）とサブエージェント終了時の検査（`phase.scope_findings`）は
    下書きの置き場を外さない。チケットの置き場の外し方も同じではなく、呼び出しごとの監視は
    内容で決める（`post._script_writes`）。外し方を揃えないのは、
    **揃える意味がその 2 か所には無い**から。どちらも入力は `git status`
    （`--ignored` を付けない）と `base_sha..HEAD` の差分（追跡ファイルだけ）で、
    追跡から外れている `scratchpad/` はそこに 1 本も現れない。つまり正しく設定された
    リポジトリでは、外しても外さなくても同じ答えになる。

    答えが変わるのは `scratchpad/` が追跡されているとき、すなわち外してよい根拠
    （追跡されないので統合先のブランチへ乗らない）が既に崩れているときだけ。
    そこで外すと、根拠が崩れたことを知らせる唯一の経路を自分で塞ぐことになる。
    だから外さない。実行前に通ったものが実行後に咎められる形は残るが、咎められる
    のは「そのリポジトリで `scratchpad/` が追跡されている」ときだけで、それは本当に
    知らせるべきことになる。
    """
    return is_ticket_place(rel, tickets_rel, approved_rel) or is_scratch_place(rel)


def scan(root: str, tickets_rel: str, projects_dir: str = "") -> tuple[list[Ticket], list[Problem]]:
    """ワークスペース・プロジェクト・全ワークツリーの提案を集める。状態と置き場を添える。

    集めるのは `todo/`（承認待ち）と `review/`（レビュー待ち）。`review/` に在るものは
    承認済みチケットが動いてきたもので、`ccnavi_approved` を持つ（approval.scan_review）。
    置き場はどのツリーでも同じ相対（`wip/proposals/`）で、プロジェクト向けの提案はその
    プロジェクトのツリー（か、そこから切ったワークツリー）にある（設計 §11.5、REQ-MLT-14）。
    ワークスペースの `wip/<名前>/proposals/` は読まない。
    同じ識別子が複数のツリーにあれば、権威のあるツリーの側だけを残す。
    """
    found, problems = scan_all(root, tickets_rel, projects_dir)
    return dedupe(found), problems


def scan_all(
    root: str, tickets_rel: str, projects_dir: str = ""
) -> tuple[list[Ticket], list[Problem]]:
    """ワークスペースルートと全ワークツリーの提案を、重複を畳まずに集める。

    ボード（`--explain --json`）が「どのツリーに写っているか」を見せるために使う。
    判定と承認は `scan` の畳んだ側を読む。
    """
    found: list[Ticket] = []
    problems: list[Problem] = []
    ws = tree.main_tree(root)
    # 置き場がプロジェクトを決める（設計 §11.5）。提案はどのツリーでも同じ相対の置き場に
    # あり、プロジェクト向けの提案はそのプロジェクトの git が持つ。承認をプロジェクトの
    # git で運ぶので、提案も同じブランチに乗せる（設計 §9.2、REQ-MLT-14）。
    # frontmatter の `project:` は照合に使うだけ。
    places = [
        (t, tickets_rel, t.project)
        for t in [ws, *tree.projects(projects_dir), *tree.worktrees(root, projects_dir)]
    ]
    for t, rel, place_project in places:
        base = os.path.join(t.root, rel.replace("/", os.sep))
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
                    # 承認済みチケットにも残す。judge は親の承認済みチケットを引けないとき
                    # （親が閉じた）子の承認済みチケットの
                    # `project` を見る。ここで入れないとその落ち先が空になる。
                    ticket.raw["project"] = place_project
                found.append(ticket)
    return found, problems


def fold(hits: list[Ticket]) -> list[Ticket]:
    """同じ識別子の写りを、権威のあるツリーで畳む。

    子のワークツリーは親のブランチから切るので、親の `wip/proposals/` がそのまま
    写っている。権威は親のツリー（親自身なら自分のツリー）の側。そこに 1 つ
    あればそれが本物で、残りは写し。

    親のツリーが無ければ元ツリー（ワークスペースルート。プロジェクトのチケットなら
    そのプロジェクト）の側を採る。ワークツリーは畳めば消えるが、元ツリーは消えない。
    親のワークツリーを作る前と、合流して畳んだ後がこの形で、ここで落ち先を決めないと、
    片付けただけのチケットが「複数の場所にある」になり、状態の操作が止まる。

    **ただし、元ツリーより先の置き場に在る写しが 1 つでもあれば採らない。** 元ツリーを
    権威にしてよい根拠は「他の写しは合流の結果で、同じか手前の状態」であって、合流
    していないワークツリーで先に進んだ写し（親のツリーで閉じ、子のツリーが取り込んだ形）
    があるときは成り立たない。そこで元ツリーを採ると、閉じた子をもう一度閉じ、リスクの
    記録を別の差分で書き直す。決めずに残し、人に合流させる。

    どちらも持っていなければ全部残る。残りが 2 つ以上になったら、どれが本物か
    決まらない（検証が「複数の場所にある」と言う状態）。

    リポジトリをまたいだ衝突は畳まない。識別子は人が選ぶ短い連番なので、プロジェクトが
    独立に振ればぶつかる（設計 §11）。それは写しではなく別物なので、どちらかを権威に
    すると、もう片方が黙って消えて `--lint` の「複数のリポジトリにある」も出なくなる。
    """
    if len({t.project for t in hits}) > 1:
        return hits
    home = hits[0].parent or hits[0].ticket
    at_home = [t for t in hits if t.tree == home]
    if at_home:
        return at_home
    at_origin = [t for t in hits if t.tree == origin_tree(t)]
    if not at_origin or behind(at_origin, hits):
        return hits
    return at_origin


def origin_tree(t: Ticket) -> str:
    """このチケットの元ツリーの名前。ワークスペースなら空、プロジェクトならその名前。

    ワークツリーの名前は識別子だが、元ツリーの名前はプロジェクトの名前（ワークスペース
    から切ったものなら空）。`tree.Tree.name` と同じ綴りで並ぶ。
    """
    return t.project or tree.MAIN


def progress(state: str) -> int:
    """置き場の進み具合。`todo` < `doing` < `review` < `done`。空は `doing` として読む。

    承認済みチケットの写しは置き場を持たないことがある（`state` が空）。判定と同じく
    作業中として数える。
    """
    order = (TODO, DOING, REVIEW, DONE)
    state = state or DOING
    if state == CANCELLED:
        state = DONE  # 取り消しも閉じた側。置き場は `done/`
    return order.index(state) if state in order else 0


def behind(some: list[Ticket], hits: list[Ticket]) -> bool:
    """`some` より先の置き場に在る写しが `hits` にあるか。"""
    return max(progress(t.state) for t in hits) > max(progress(t.state) for t in some)


def collided_states(states: list[str]) -> list[str]:
    """1 つのツリーの中で、どれが本物か決まらない置き場の並び。決まっていれば空。

    同じ識別子が 2 つの置き場に在るのは、動かす途中で止まった跡（写せたが消せなかった）。
    ただし `todo/` は親の改版の途中なので、承認済みチケットと並んでいてよい。
    `--lint` の ERROR と、ボードの `scattered` が同じ数え方をするためにここに置く。
    """
    distinct = sorted(set(states))
    if len(distinct) > 1 and TODO not in distinct:
        return distinct
    if len(states) > 1 and len(distinct) < len(states):
        return states
    return []


def collisions(hits: list[Ticket]) -> list[Ticket]:
    """どれが本物か決まらない写りの全部。決まっていれば空。

    畳んで 2 つ以上残り、かつその残りが `collided_states` に当たるときだけ入る。
    状態の操作が「複数の場所にある」で止まるのと、`--lint` が ERROR で言うのと、
    同じ条件（`lint._proposal_problems` も同じ関数を通る）。写りがあること自体は
    普通なので、畳んで 1 つに決まる写りは数えない。
    """
    folded = fold(hits)
    if len(folded) > 1 and collided_states([t.state for t in folded]):
        return folded
    return []


def by_ticket(found: list[Ticket]) -> dict[str, list[Ticket]]:
    """識別子ごとの写りの全部。並びは見つけた順。"""
    grouped: dict[str, list[Ticket]] = {}
    for t in found:
        grouped.setdefault(t.ticket, []).append(t)
    return grouped


def dedupe(found: list[Ticket]) -> list[Ticket]:
    """同じ識別子が複数のツリーにあるとき、権威のあるツリーの側だけを残す。"""
    kept: list[Ticket] = []
    for hits in by_ticket(found).values():
        kept.extend(fold(hits))
    return kept


def locate(root: str, tickets_rel: str, tree_root: str, ticket_id: str) -> tuple[str, str]:
    """このワークツリーで、この識別子の提案がどの状態にあるか。無ければ空文字 2 つ。"""
    base = os.path.join(tree_root, tickets_rel.replace("/", os.sep))
    for state in STATES:
        path = os.path.join(base, state, ticket_id + ".md")
        if os.path.isfile(path):
            return state, path
    return "", ""


def _place(tickets_rel: str) -> str:
    """置き場の綴りに当たる式。プロジェクトの名前を挟んだ形（`wip/<name>/proposals`）にも当たる。"""
    parts = [re.escape(p) for p in tickets_rel.split("/") if p]
    optional = r"(?:[^\\/\s\x00]+[\\/])?"
    if len(parts) < 2:
        return optional + r"[\\/]".join(parts)
    return parts[0] + r"[\\/]" + optional + r"[\\/]".join(parts[1:])


def state_dir_regex(tickets_rel: str) -> str:
    """直接の作成・移動を止める置き場に当たる式。パス用。"""
    return rf"(^|[\\/]){_place(tickets_rel)}[\\/]({'|'.join(GUARDED_STATES)})[\\/]"


def guard_rules(tickets_rel: str, root: str) -> list[rules.Rule]:
    """状態の置き場を守るルール。組み込みで、ルールファイルには書かない。

    通るのは状態を動かすスクリプトだけ。そのスクリプトの呼び出し文字列には
    置き場の綴りが現れないので、ここに当たらない。root は文面の sh の綴りに使う。
    """
    place = state_dir_regex(tickets_rel)
    message = (
        "チケットの状態は置き場で表します。review/（レビュー待ち）へ動かすのは "
        f"'{settings.script_command(root, 'ccnavi-ticket.sh')} finish <識別子>' だけです。"
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
    # （`mv x wip/proposals/done`）でも当てる。
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


def propose_notice(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    subject: str,
) -> str:
    """提案を書いた回に、承認を頼む前の確認を 1 度だけ伝える文（REQ-APV-14）。空なら渡さない。

    承認できない提案のまま人に承認を頼むと、落ちたことを知るのが端末に座った人になり、
    往復が 1 回増える。確かめる手立て（`--approve --preview --verify`）は在るので、
    書いた直後に、要る場所で言う。

    **判定には足さない。** これは文であって判定ではないので、ルールの表（`rule_set`）には
    入れず、承認の知らせ（`approval.news`）と同じ口から渡す。表に allow を 1 本足す形は
    採らない。次の 3 つを一緒に引き受けることになるため。

    - `todo/` が「ccnavi が言及する場所」になり、どのタイプも言及しないときの倒し方
      （`judge.undeclared_verdict`）を通らなくなる。確認できる者が居ないモードの deny も、
      知らない綴りのモードを ask に倒す既定も、そこだけ外れる（REQ-PRE-08）
    - 判定は強いタイプから見て最初に当たった段で決まるので、**提案の置き場に `deny` か
      `ask` を書いているワークスペースには文が届かない。** 承認の流れをいちばん
      気にしているところにだけ届かない、という向きになる
    - 組み込みで持つのは、ガード自身を守るものと取り返しの付かない操作だけ（設計 P4）。
      助言はそのどちらでもない

    渡るのは書き込みの**前**（PreToolUse）で、止まった回にも届く。文面を「書きました」と
    過去形にしないのはそのため。書けたかどうかは、この文を渡す時点では決まっていない。

    1 つの文脈（セッション、サブエージェントなら 1 回の起動）で最初の 1 回だけ渡る。
    数えは `ctxfile` の控えを、ルールと同じ形で使う（この 1 本は表に入れないので、
    判定には一切現れない）。2 本目からは黙る。提案を 1 本書くたびに同じ文を積むと、
    長いセッションではそれだけでコンテキストを食う。
    """
    if payload.tool_name not in WRITE_TOOLS or not subject:
        return ""
    if not _propose_place(conf.tickets, root).search(subject):
        return ""
    return ctxfile.for_rules(stderr, conf.state, payload, [_propose_rule(conf.tickets, conf.bin)])


def _propose_place(tickets_rel: str, root: str) -> re.Pattern:
    """承認待ちの置き場に当たる式。**ワークスペースの中に限る。**

    `guard_rules` は前を問わない形（`(^|[\\/])`）で当てるが、あれは deny なので、余分に
    当たるぶんは止めすぎる側へ外れるだけ。文を渡す側を同じ形で当てると、ワークスペースの
    外に `wip/proposals/todo/` という並びを掘っただけの場所でも「提案を書いた」と読む。
    ツリー（ワークツリー・プロジェクト）はどれもワークスペースルートの下（設計 §11)
    なので、ルートで留めれば正しい置き場は全部入り、外は入らない。

    大文字小文字は区別しない機械では `TODO/` も同じ場所。`guard_rules` と揃える。
    """
    place = rf"^{rules.root_pattern(root)}[\\/][^\x00]*{_place(tickets_rel)}[\\/]{TODO}[\\/]"
    return re.compile(place, re.IGNORECASE)


def _propose_rule(tickets_rel: str, bin_path: str) -> rules.Rule:
    """文と、数えの鍵になる id を運ぶ入れ物。表には入れない（`propose_notice` だけが持つ）。"""
    return rules.Rule(
        id=PROPOSE_RULE_ID,
        additional_context_once=(
            f"チケットの提案を書こうとしています（{tickets_rel}/todo/ は承認待ちの置き場で、"
            "ここに書いただけでは判定には効きません）。"
            "利用者に承認を依頼する前に、"
            f"'{settings.bin_command(bin_path)} --approve --preview --verify <識別子>' で"
            "承認できる状態かを確かめてください。承認済みチケットは置きません。"
            "終了コードが答えです。0 なら依頼してよく、3 なら承認の対象に入らない理由が出ます"
            "（親が承認されていない、計画に無いフェーズ、順序、承認待ちに無い識別子）。"
            "直してから依頼してください。1 は打ち方か設定の誤りで、提案の問題ではありません。"
            "**識別子には、いま書いたものを渡してください。** 省くと承認待ち全部が対象になり、"
            "書いた提案の frontmatter が壊れていても（承認待ちに並ばないので）気づけません。"
        ),
    )


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


def script_fields_set(text: str) -> tuple[str, ...]:
    """その版が既に値を持っている、スクリプトの欄。

    姿を突き合わせる側が「落としてよい欄」を決めるのに使う（`post._script_writes`）。
    **落としてよいのは、コミット済みの版がまだ持っていない欄だけ。** 副命令はどれも
    1 度しか書かない（`ops.start` は着手済みを拒む）ので、既に値がある欄が変わったのなら、
    それは副命令の仕業ではない。

    とくに `base_sha` は、サブエージェント終了時の検査（`phase.scope_findings` の
    `base_sha..HEAD`）と実績リスク（`risk.measure`）の基準点。ここを書き換えられると、
    コミット済みの範囲外の変更が検査から消える。落とす欄を「いつでも」にすると、その
    書き換えが実行後の監視からも消える。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != FENCE:
        return ()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == FENCE), None)
    if end is None:
        return ()
    held = []
    for line in lines[1:end]:
        if line.startswith((" ", "\t")) or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip() in SCRIPT_FIELDS and value.strip():
            held.append(key.strip())
    return tuple(held)


def script_shape(text: str, drop: tuple[str, ...] = SCRIPT_FIELDS) -> str | None:
    """frontmatter を持つチケットなら、`drop` の欄を落とした姿を返す。無ければ None。

    実行後の監視が「この変更は ccnavi の副命令が書いたぶんか」を、台帳ではなく内容で
    答えるのに使う（`post._script_writes`）。台帳を持たないのは、承認とマーカーが親の
    ブランチに乗って別の機械へ届くため。台帳はワークスペース側にあって git に入らないので、
    clone した続きでは 1 件も残っていない。内容で見るなら、どの機械でも同じ答えになる。

    落とすのは `drop` に挙げた欄の行と、その欄の値として続く字下げの行だけ。`drop` は
    `SCRIPT_FIELDS` の部分集合で、決めるのは呼ぶ側（`script_fields_set` を引いて、
    コミット済みの版がまだ持っていない欄だけを渡す）。範囲
    （`allow` / `ask` / `deny`）も `parent` も `project` も `phase` も本文も残るので、
    そこが 1 文字でも変われば別の姿になり、監視は今までどおり報告する。

    切り出し方は `set_fields` と揃える。あちらが行単位で書き換えるので、こちらも行単位で
    落とす。揃えないと、スクリプトが書いた直後の姿が「スクリプトが書いていない形」に見える。

    frontmatter を持たないもの（マーカー、`.risk.json`、閉じの記録）は None。範囲を
    宣言しないので、正規の設置と偽の設置を内容からは見分けられない。**そこは外れる。**
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != FENCE:
        return None
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == FENCE), None)
    if end is None:
        return None
    kept, dropping = [], False
    for i, line in enumerate(lines):
        if not 1 <= i < end:
            kept.append(line)
            continue
        indented = line.startswith((" ", "\t"))
        if dropping and indented:
            continue
        dropping = not indented and line.split(":", 1)[0].strip() in drop
        if not dropping:
            kept.append(line)
    return "\n".join(kept)


def insert_front(text: str, key: str, value) -> str:
    """frontmatter の閉じの `---` の前に、欄を 1 つ足す。人の書いた行は保つ。

    承認のときに `ccnavi_approved` を足すのに使う。読み直して書き出す（render）と、
    人が書いたコメントや `|` のブロックが消える。同じ鍵の行が既にあれば消してから足す
    （最上位の鍵だけ。字下げされた行はその欄の続きとして一緒に消す）。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != FENCE:
        return text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == FENCE), None)
    if end is None:
        return text
    kept = [lines[0]]
    skipping = False
    for line in lines[1:end]:
        if line.startswith((" ", "\t", "-")) and skipping:
            continue
        skipping = line.split(":", 1)[0].strip() == key and not line.startswith((" ", "\t"))
        if not skipping:
            kept.append(line)
    dumped = yaml.safe_dump(
        {key: value}, allow_unicode=True, sort_keys=False, default_flow_style=False
    ).rstrip("\n")
    kept.extend(dumped.splitlines())
    kept.extend(lines[end:])
    return "\n".join(kept) + ("\n" if text.endswith("\n") else "")


def _yaml_scalar(value: str) -> str:
    """欄の値を、YAML が文字列として読み戻せる綴りにする。"""
    return (
        yaml.safe_dump(value, allow_unicode=True, default_style='"').strip().removesuffix("\n...")
    )


STATE_RULE_ID = "builtin-ticket-state"
# 提案を書いた回に、承認を頼む前の確認を伝えるルールの id。
PROPOSE_RULE_ID = "builtin-ticket-propose"


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
                        f"{where} の範囲 `{glob}` が絶対パス。ワークツリーのルートからの相対で書く",
                    )
                )
                continue
            glob = glob.replace("\\", "/").strip("/")
            expression = regex or ("^" + globmatch.translate(glob))
            # `src/Components/*` と `src/components/*` は同じ範囲として扱う。
            # 当てる側だけ区別すると、宣言した範囲に自分のファイルが入らない、が
            # 起きる。機械ごとに変えないのは、同じチケットがどの環境でも同じ場所で
            # 止まるため。`regex` も同じに扱う（ルールと揃える。rules._build）。
            # 区別が要る `regex` は `(?-i:...)` で囲む。
            flags = re.IGNORECASE
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
        # 通す側には倒さない。「先頭の 1 バイト目から `---`」が frontmatter の契約で、
        # BOM を読み飛ばすと同じファイルが書き手の道具ごとに違う姿で通る。弾いたまま、
        # 目に見えない原因だけを名指しする。
        if lines and lines[0].lstrip(BOM).strip() == FENCE:
            detail = (
                f"先頭に BOM (U+FEFF) が付いていて `{FENCE}` で始まっていない。"
                "BOM 無しの UTF-8 で保存し直す"
            )
        else:
            detail = f"先頭が `{FENCE}` で始まっていない"
        return None, "", [Problem(SEVERITY_ERROR, "(file)", detail)]
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
