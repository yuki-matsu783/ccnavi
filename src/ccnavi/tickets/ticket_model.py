"""チケットの形。書式の定数・状態の名前・範囲の項・計画の項・待ち方と、チケット 1 本ぶんの
データクラス。

読み込み（`ticket.parse`）と置き場・まとめ方の側がどれも使うので、ここに置く。ticket から分けた。
ticket を読まない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..policy import rules

# frontmatter の囲い。
FENCE = "---"

# BOM (U+FEFF)。`str.strip()` は空白と見なさないので、付いていると先頭の `---` が
# `---` と一致しない。目に見えないので、弾くときは原因を名指しする（ticket._frontmatter）。
BOM = "\ufeff"

# このビルドが読めるチケット書式の版。
VERSION = 1

# 状態。置き場の名前そのもの。
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
# 終わった状態。フェーズの終わりはこれで数える（レビュー待ちも作業としては終わっている）。
FINISHED = (REVIEW, DONE, CANCELLED)
# 直接の作成・移動を止める置き場。todo/ への作成と編集は自由。
GUARDED_STATES = (REVIEW,)

# 範囲の項として判定に使われるツール。これ以外を match に書いた項は使われない。
WRITE_TOOLS = ("Write", "Edit", "NotebookEdit")

# スクリプトだけが書く欄。ユーザもエージェントも書かない。承認済みチケットの側で
# 行単位に書き換える（`ticket_fields.set_fields`）。承認は提案の中身を変えずに動かすので、
# 提案に書いた値はそのまま承認済みチケットの値になる。だから提案に空でない値があれば
# `--agree` と `--lint` が error にする（`agree_candidates.script_field_problems`）。
SCRIPT_FIELDS = ("started_at", "completed_at", "base_sha", "cancelled_at", "cancel_reason")

# 前の版の承認が承認済みチケットに書き足していた記録の欄。いまの承認は中身を変えないので
# 書かない。残っている古い承認済みチケットを読むためだけに名前を持つ。
APPROVAL_KEY = "ccnavi_approved"

# glob のワイルドカード。これより前が字義どおりの前置。
_WILDCARDS = "*?["


def _fold(path: str) -> str:
    """比べるための表記。範囲の照合はどの機械でも大文字小文字を区別しない。"""
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
            # ワイルドカードの無いパスは前置。`src` が範囲なら `src` という
            # 名前のファイルも `src/` の下も中。そこだけ外になるのは驚きでしかない。
            # 大文字小文字は揃えてから比べる。機械によって区別の有無が変わると、
            # 同じチケットと同じパスで止まる場所が Windows と Linux で食い違う。
            # 範囲はユーザが宣言する意図なので、機械の都合ではなく書かれたパスの意味で読む。
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


# 計画の項が書けるレビューの指定。`mr` は定義が none でも要る（強める）、`defer` は
# 次にレビューがあるフェーズと一緒に見る（延期）。弱める向き（none）は書けない。
PLAN_REVIEW_MR = "mr"
PLAN_REVIEW_DEFER = "defer"
PLAN_REVIEWS = (PLAN_REVIEW_MR, PLAN_REVIEW_DEFER)


@dataclass
class PlanItem:
    """親の計画の 1 項。フェーズ定義の名前と、レビューの指定。"""

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


WORKFLOW_KEY = "workflow"
WORKFLOW_SEQUENTIAL = "sequential"
WORKFLOW_DAG = "dag"


@dataclass
class Workflow:
    """全体計画の待ち方のコピー。`--agree` が計算して `<承認済みの置き場>/phases/<親>/workflow.yml`
    に書く。前の版は親の承認済みチケットの `workflow:` 欄に書いていた。

    `waits` は全体計画の番号 → 待つ番号、`review_at` は延期した番号 → 引き受ける番号。
    判定はこのコピーだけを読み、`phases.yml` を読み直さない。
    """

    order: str = WORKFLOW_SEQUENTIAL
    waits: dict[int, list[int]] = field(default_factory=dict)
    review_at: dict[int, int] = field(default_factory=dict)

    def as_raw(self) -> dict:
        return {
            "order": self.order,
            "waits": {n: list(w) for n, w in sorted(self.waits.items())},
            "review_at": dict(sorted(self.review_at.items())),
        }


@dataclass
class Ticket:
    """チケット 1 本ぶん。提案としても承認済みチケットとしても同じ形。"""

    ticket: str = ""
    parent: str = ""
    phase: int | None = None
    predecessors: list[str] = field(default_factory=list)
    # issue は元になった課題の番号。親だけが持つ。マージリクエストを作るときに
    # `Closes #<番号>` へ書き出す。無くても動く。
    issue: int | None = None
    # issue_repo は、課題が別のリポジトリにあるときのその表記（`issue: owner/repo#N` の
    # `owner/repo`）。同じリポジトリの課題なら空。識別子は issue から決めずユーザが付ける。
    issue_repo: str = ""
    # branch は親のブランチ名。親だけが持つ任意の欄で、無ければ親のブランチ名は
    # 識別子そのもの。識別子・ファイル名・ワークツリー名は `/` を含まないまま、`feature/123-login`
    # のような既存のブランチで作業するために使う。承認画面に出し、承認のダイジェストに入る
    # （チケットの全文が入る）。
    branch: str = ""
    # project は作業のプロジェクト（`projects/` の名前）。決めるのは提案を
    # 置いた場所で、`ticket.scan` が入れる（プロジェクトの `wip/proposals/` ならその名前、
    # ワークツリーの中ならその元リポジトリ、ワークスペースの `wip/proposals/` なら空）。
    # 親も子も同じ置き場に並ぶので、継ぐ段は無い。判定は行き先のワークツリーの元リポジトリと
    # 照合する。
    project: str = ""
    # declared_project は frontmatter にユーザが書いた `project:`。宣言ではなく照合に使う。
    # 置き場と違えば承認しない（approval_checks.project_problems）。`ticket.scan` を通さずに
    # 読んだとき（`ticket.load` を直に呼ぶ経路）は project と同じ値になる。
    declared_project: str = ""
    # plan は全体計画（作業のフェーズ定義のリスト）、feedback はフィードバック計画。
    # 親だけが持つ。feedback が None なのは「まだ計画していない」、[] は
    # 「見たうえで対応なし」。
    plan: list[PlanItem] = field(default_factory=list)
    feedback: list[PlanItem] | None = None
    # workflow は全体計画の待ち方のコピー。親の承認済みチケットだけが持ち、書くのは `--agree`。
    workflow: Workflow | None = None
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
    # 見つけた場所。提案なら状態とワークツリー、
    # 承認済みチケットなら置き場（`approval.scan_all`）から。
    state: str = ""
    tree: str = ""
    tree_root: str = ""
    path: str = ""
    # 前の版の承認が書いた記録（`ccnavi_approved`）を持つ古い承認済みチケットにだけある。
    # いまの承認は書かないので、新しい承認済みチケットでは空。
    # 承認を正とするのは置き場。
    approved_at: str = ""
    source_tree: str = ""
    source_path: str = ""
    # 待ち方のファイル（`phases/<親>/workflow.yml`）が在るのに読めない理由。空なら読めたか無い。
    # 判定は読めない待ち方を一直線と読まずに止める（`approval_checks.blocking_problems`）。
    workflow_unreadable: str = ""
    # 待ち方を古い形の `workflow:` 欄から採ったか（`approval.load_copy`）。採るのは今の phases.yml
    # から計算した待ち方と同じときだけで、違えば欄を捨てて一直線で読み、
    # `workflow_record_differs` を立てる（`approval.settle_old_workflows`）。
    workflow_from_record: bool = False
    workflow_record_differs: bool = False
    # 親のツリーで見つけた未着手のチケットが、手元の退避（`logs/archive/`）の閉じたチケットと
    # 同じ識別子のときの理由（`archive.drop_archived`）。
    # 判定は止める（`approval_checks.content_problems`）。
    archived_clash: str = ""
    # blocked は「このチケットは読めるが信じられない」理由。空でなければ判定は範囲を
    # 当てずに止める（phase_scope.scope_verdict）。承認のときにしか当たらなかった構造の検査を、
    # 判定の側でも当てるために置く（置き場を手で動かして承認すると `--agree` を通らない）。
    blocked: str = ""

    @property
    def is_child(self) -> bool:
        return bool(self.parent)

    @property
    def has_plan(self) -> bool:
        return bool(self.plan)

    @property
    def in_progress(self) -> bool:
        """着手していて、終わってもいないし取り消されてもいない。"""
        return bool(self.started_at) and not self.completed_at and not self.cancelled_at

    def numbered(self) -> list[tuple[int, PlanItem]]:
        """計画の項に番号を振る。全体計画が 1 から、フィードバック計画はその続き。"""
        items = list(self.plan) + list(self.feedback or [])
        return [(i + 1, item) for i, item in enumerate(items)]

    def item_at(self, number: int) -> PlanItem | None:
        for n, item in self.numbered():
            if n == number:
                return item
        return None

    def in_plan(self, number: int) -> bool:
        return 1 <= number <= len(self.plan)

    def review_at(self, number: int) -> int | None:
        """この番号のフェーズのレビューが行われる番号。延期なら引き受ける番号。

        全体計画の番号はコピーした待ち方（`workflow`）で読む。フィードバック計画は一直線で、次にレビューが
        ある番号。延期の先が無ければ None（計画が破損している）。
        """
        item = self.item_at(number)
        if self.workflow is not None and self.in_plan(number) and item is not None:
            if not item.deferred:
                return number
            return self.workflow.review_at.get(number)
        items = self.numbered()
        for n, item in items:
            if n >= number and not item.deferred:
                return n
        return None

    def covered_by(self, number: int) -> list[int]:
        """この番号のレビューが含む、それより前の延期したフェーズの番号。"""
        if self.workflow is not None and self.in_plan(number):
            return sorted(n for n, at in self.workflow.review_at.items() if at == number)
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
