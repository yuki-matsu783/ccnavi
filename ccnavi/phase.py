"""フェーズの終わりと HITL ポイント（Human In The Loop。人の手が入るところ）。

子チケットのまとまりが終わったときに何をするかと、人を待たずに進もうとしたら止めること。

## フェーズの終わり

同じ親の同じ `phase` の子が `todo/` にも `doing/` にも無く、`done/` に 1 枚以上あるとき、
そのフェーズは終わり。`cancelled/` だけのフェーズは終わりではない（何も成果が無い）。

終わりの扱いは、`done/` の子に `human_review.required: true` が 1 枚でもあるかで分かれる。
あれば HITL ポイントに来たということで、レビュー済みのマーカーが置かれるまで
サブエージェントの起動とシェルを止める。
無ければ省略のマーカーを置いて進ませる。

## 止めるときの鍵は cwd

書き込みは行き先で結ぶが、起動とシェルには行き先が無い。止めるかどうかは呼び出しの `cwd` が
どの親のワークツリーにあるかで親を引く。`cd` 1 回で外れる鍵だが、外れた先で起動した
サブエージェントの書き込みは行き先で止まるので、致命傷にならない。

## 提案から承認済みチケットへ写すもの

スクリプトが書く欄（着手・完了の時刻と基準点）だけを、提案から承認済みチケットへ写す。
範囲に触らない欄なので、写しても承認の意味は変わらない。提案が `done/` か
`cancelled/` に動いていたら、承認済みチケットを `closed/` へ動かす。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import TextIO

from . import approval, gitcmd, phasetypes, risk, rules, selfguard, settings, shellread, tree
from . import ticket as ticket_mod

# 止めている間でも通す形。状態を動かす・レビューを頼む・合流して片付ける、の 3 本を、
# コマンドの位置で `sh` から呼ぶ形だけ。綴りがどこかに含まれるだけでは通さない。
# 連結されたコマンドの全部がこの形でなければ、1 つでも違えば止める。
_EXEMPT_COMMAND = re.compile(r"^(sh|bash)\s+\S*ccnavi-(ticket|review|git)\.sh(\s|$)")

# サブエージェントに許さない操作。状態を動かす形・レビューの形・リモートへ送る形を、
# コマンドの位置で。読むだけの `cat` や `--help` は止めない。
# push を含めるのは、リモートに置く枝は親ブランチ 1 本で、それを送るのが親の仕事だから。
# git のラッパースクリプトも子チケットのツリーからの push を拒むが、そちらは cwd のツリーで見る。
# サブエージェントが親のツリーへ cd して打てばラッパースクリプトは通すので、素性で止める層を
# ここに持つ。
_FORBIDDEN_COMMAND = re.compile(
    r"(^|[;&|]\s*)(sh|bash)\s+\S*ccnavi-(ticket|review|git)\.sh\s+"
    r"(start|finish|cancel|record-risk|request|confirm|comment|decide|to-issue|ready|close-early|push)\b"
)

# シェルとして扱うツール。PowerShell は shellread で読めないので生の文字列に当てる。
SHELL_TOOLS = ("Bash", "PowerShell")

# レビューが済むまで止めるツール。
HELD_TOOLS = ("Agent", *SHELL_TOOLS)

# ccnavi 自身の実行ファイルを、人の判断の経路に使う形。`--approve` `--reviewed` `--close-early` と、
# 状態とレビューのサブコマンド。スクリプト 2 本の中身がこれなので、スクリプトを
# 経由せずに打てば止める。CCNAVI_GUARD_TICKET_APPROVAL で切れる。
# `--approve --preview` は一覧を見るだけ（承認済みチケットを置かない）ので除く。ただし除外は
# `--approve` の枝にしか掛けない。承認そのものを行う `--yes` は独立した枝で必ず当てる。
# 免除の条件を 1 つにまとめると、同じコマンドに `--preview` を書き足すだけで `--yes` まで
# 免除される。承認を通す形は、免除の理由が何であっても止める。
#
# 免除の範囲はコマンド 1 本まで。Bash なら shellread が `\x00` で切るが、PowerShell は
# 読めないので生の文字列に当たる（judge.screen）。生の文字列には `\x00` が無いので、
# 区切りとして `;` `&` `|` と改行も見る。見ないと、後ろのコマンドに書いた `--preview` が
# 前のコマンドの `--approve` を免除する。
# 語の中の目印（引用がつないだ空白）もまたがない。またぐと、引数の値に書いた
# `ccnavi --approve x "a --preview"` の `--preview` が免除の理由になる。
#
# **免除の理由になるのは、単独の語として立った `--preview` だけ。** 前は生の空白（`--approve` の
# 後ろに必ず 1 つある）、後ろは空白か区切りか行末。これを見ないと、別のフラグの**値**に書いた
# `--preview` で免除が成立する。`ccnavi --approve --reason=--preview` は、argparse が
# `--reason` の値として食うので `--preview` は立たず、実行ファイルは本物の `--approve` を
# 走らせる。hook が見る文字列と、実行ファイルが走らせる枝がそこでズレる。
# `=` を挟む形だけでなく、`--preview=x` や `x--preview` のように語にくっついた形も免除しない。
# 語の切れ目は生の空白だけで数える。語の中の目印（引用がつないだ空白）は数えない。
# 数えると、引用の中に書いた `"a --preview"` が単独の語に見えて免除が戻る。
_PREVIEW_END = rf"[ \t;&|\r\n{re.escape(shellread.SEP)}]"
_PREVIEW_WORD = rf"[ \t]--preview(?={_PREVIEW_END}|$)"
_NOT_PREVIEW = rf"(?![^{selfguard._NOT_A_WORD};&|\r\n]*{_PREVIEW_WORD})"
_CLI_FORMS = (
    rf"(--yes\b|--approve\b{_NOT_PREVIEW}|--reviewed\b|--close-early\b"
    r"|\b(ticket|review)\s+"
    r"(start|finish|cancel|record-risk|prepare|requested|confirm|to-issue|ready)\b)"
)
CODE_TICKET_APPROVAL = "DENY_TICKET_APPROVAL_CLI"
TICKET_APPROVAL_RULE_ID = "builtin-guard-ticket-approval"


def commands(subject: str) -> list[str]:
    """shellread が切ったコマンドの並び。読めなかった生の文字列なら 1 本。"""
    return [c.strip() for c in subject.split("\x00") if c.strip()]


def exempt(subject: str, degraded: str) -> bool:
    """止めている間でも通してよいか。読み切れなかったコマンドは通さない。"""
    if degraded:
        return False
    parts = commands(subject)
    return bool(parts) and all(_EXEMPT_COMMAND.match(c) for c in parts)


def forbidden(subject: str, unwrapped: str = "") -> bool:
    """サブエージェントに許さない形を含むか。

    unwrapped は shellread が作る、中で実行されるコマンドの層（`\\x00` でつないだもの）。
    禁止の形はコマンドの先頭の `sh` に固定しているので、`env sh …` や `sh -c '…'` は
    元の形では当たらない。層にも当てる。止める側にだけ足す当て先で、`exempt` には渡さない。
    """
    return any(_FORBIDDEN_COMMAND.search(c) for c in commands(subject) + commands(unwrapped))


# 人の判断の経路の端末要求を、エージェントのコマンド行で切る形。実行ファイルは
# `CCNAVI_GUARD_TICKET_APPROVAL` と `--guard-ticket-approval` で端末要求を外す
# （テストと CI のため）。
# 実行ファイルを呼ぶ綴りは追い切れない（`uv run -m ccnavi`、名前を変えた写し）ので、呼び方では
# なく切る形そのもので止める。切れなければ、端末を持たないエージェントは実行ファイルの側で止まる。
#
# 見るのは生の文字列。`bash -c '…'` や `$( … )` の中に書いた形も同じに数える。変数は代入の形
# （`X=`・`env X=`・`export X=`・PowerShell の `$env:X =`）、フラグは `enable` 以外の値を
# 渡す形。この綴りそのものを grep で探すコマンドも当たるが、それは Grep ツールで済む。
_GUARD_NAME = "CCNAVI_GUARD_TICKET_APPROVAL"
_GUARD_OFF = re.compile(
    rf"(?<![$\w]){_GUARD_NAME}\s*\+?="
    rf"|\$\{{{_GUARD_NAME}:?="
    rf"|SetEnvironmentVariable\s*\(\s*['\"]{_GUARD_NAME}"
    rf"|\b(?:Set-Item|New-Item|si|ni)\b[^\n;]*env:[\\/]?{_GUARD_NAME}"
    r"|(?:\bread|\bmapfile|\breadarray|\bprintf\s[^\n;&|]*-v)\b[^\n;&|]*"
    rf"\b{_GUARD_NAME}\b"
    r"|--guard-ticket-approval(?:\s*=\s*|\s+)(?!['\"]?enable(?![\w-]))",
    re.IGNORECASE,
)


def turns_off_guard(subject: str) -> str:
    """人の判断の経路の端末要求を切る形があれば、その綴り。無ければ空。"""
    match = _GUARD_OFF.search(subject)
    return match.group(0).strip() if match else ""


def guard_off_message(found: str) -> str:
    """端末要求を切る形で止めた文。"""
    return (
        f"人の判断の経路（承認・レビュー済み・締め）の端末要求を切る形（{found}）を、"
        "コマンド行に書いています。この変数とフラグは、テストや CI が端末を持たずに実行ファイルを"
        "回すためのもので、エージェントが置くものではありません。承認・レビュー済み・締めは利用者が"
        "端末かボードで行います。この綴りを探したいだけなら、シェルの grep ではなく Grep ツールを"
        "使ってください。"
    )


def ticket_approval_rule(bin_path: str, root: str) -> rules.Rule:
    """ccnavi の実行ファイルを人の判断の経路に使う形を止めるルール。

    承認のスクリプト（`ccnavi-approve.sh`）も同じ形で止める。中身は `--approve` の
    呼び出しと写しの push で、打つのは端末に座っている人。実行ファイルの側は標準入力が
    端末であることを求めるので、hook から呼んでも通らないが、綴りで止めておけば
    「なぜ通らないのか」が当たったルールの id で分かる。

    承認済みチケットを運ぶスクリプト（`ccnavi-push-approved.sh`）も止める。運ぶことは
    合意そのものではないが、push は外へ出す操作で、運ぶ時機を決めるのは人。
    """
    names = [r"ccnavi(\.exe)?"]
    clause = selfguard.binary_clause(bin_path)
    if clause:
        names.append(clause)
    launcher = r"((uv\s+run\s+)?python[\w.]*\s+-m\s+ccnavi|(\S*[\\/])?(" + "|".join(names) + "))"
    script = r"(^|\x00|[;&|]\s*)(sh|bash)\s+\S*ccnavi-(approve|push-approved)\.sh\b"
    # 大文字小文字を区別しない。Windows と macOS の既定のファイルシステムは綴りの大小を
    # 区別しないので、`SH .ccnavi/scripts/CCNAVI-APPROVE.sh` や `CCNAVI.EXE --approve` でも
    # 同じものが走る。区別すると綴りを変えるだけで外せる。引数の形（_CLI_FORMS）まで
    # 広がるが、実行ファイルの引数は大小を区別するので、広がるのは止める側だけ
    # （`--PREVIEW` で免除の形になっても、実行ファイルがその引数を受け付けない）。
    expression = rf"(?i)(^|\x00|[;&|]\s*)(&\s*)?{launcher}\s+[^\x00]*{_CLI_FORMS}" rf"|{script}"
    rule = rules.Rule(
        id=TICKET_APPROVAL_RULE_ID,
        match="|".join(SHELL_TOOLS),
        regex=expression,
        message=(
            "ccnavi の承認・レビュー済みの受け入れ・チケットの状態の操作は、エージェントが"
            "直接打つものではありません。状態の移動とレビューは "
            f"'{settings.script_command(root, 'ccnavi-ticket.sh')}' と "
            f"'{settings.script_command(root, 'ccnavi-review.sh')}' を"
            "使い、承認は利用者が VS Code のボードか "
            f"'{settings.script_command(root, 'ccnavi-approve.sh')}' で、"
            "未解決の受け入れは利用者が端末で行います。承認済みチケットのコミットと push"
            f"（'{settings.script_command(root, 'ccnavi-push-approved.sh')}'）も人が打ちます。"
            "ボードで承認すると、承認済みチケットのコミットと push が端末で実行されます。"
            "エージェントは打ちません。"
            "承認待ちの一覧を見るだけなら 'ccnavi --approve --preview' は通ります。"
        ),
        decision=rules.DENY,
    )
    rule.compiled = re.compile(expression)
    return rule


# 止めている間の呼び名。依頼のマーカーが境目で、未依頼はエージェントの番、
# 依頼済みは人の番（設計 §9.8）。
LABEL_PREPARING = "レビュー準備中"
LABEL_WAITING = "レビュー待ち"

# 返す文の理由コード。
CODE_REVIEW = "DENY_PHASE_REVIEW"
CODE_SUBAGENT = "DENY_SUBAGENT_TICKET_OP"

# git に与える時間。
TIMEOUT_SECONDS = 2.0


@dataclass
class Phase:
    """1 つの親の 1 つのフェーズ。

    親が計画を持てば、番号に種類と計画の項が付く（設計 §9.7）。持たなければ
    番号だけで、今までどおり子の `human_review` からレビューの要否を決める。
    """

    parent: str
    number: int
    tickets: list[ticket_mod.Ticket] = field(default_factory=list)
    states: dict[str, str] = field(default_factory=dict)
    marks: dict[str, dict] = field(default_factory=dict)
    # 計画があるときだけ。item は計画の項、type は種類、owner は親の承認済みチケット。
    item: ticket_mod.PlanItem | None = None
    type: phasetypes.PhaseType | None = None
    owner: ticket_mod.Ticket | None = None
    # 子ごとの実績のリスク（閉じるときに数えた記録）。子の識別子 → 記録。
    risks: dict[str, dict] = field(default_factory=dict)
    # 延期を引き受けた前のフェーズが、種類として宣言している「見る場所」。引き受けた側は
    # 厳しい側で見る（`review_kind`）。組むのは `phases_of`。
    covered_reviews: list[str] = field(default_factory=list)

    @property
    def risk(self) -> dict | None:
        """このフェーズの実績のリスク。子の最大値。無ければ None。"""
        best: dict | None = None
        for record in self.risks.values():
            if best is None or int(record.get("points") or 0) > int(best.get("points") or 0):
                best = record
        return best

    @property
    def risk_escalates(self) -> bool:
        """実績のリスクが、宣言に関わらずレビューが要る扱いにする等級か。"""
        record = self.risk
        return record is not None and str(record.get("level") or "") in risk.ESCALATE_FROM

    @property
    def risk_line(self) -> str:
        """人向けの 1 行。`リスク: 58 (HIGH) — 行数が多い（…）、…`。無ければ空。"""
        record = self.risk
        if record is None:
            return ""
        hits = [str(h.get("detail") or "") for h in record.get("hits") or [] if isinstance(h, dict)]
        text = f"リスク: {record.get('points', 0)} ({record.get('level', '')})"
        return text + (" — " + "、".join(hits) if hits else "")

    @property
    def planned(self) -> bool:
        return self.item is not None

    @property
    def title(self) -> str:
        """人向けの名前。種類が無ければ番号だけ。"""
        if self.type is not None:
            return self.type.title
        if self.item is not None:
            return self.item.type
        return ""

    @property
    def label(self) -> str:
        return f"{self.number}（{self.title}）" if self.title else str(self.number)

    @property
    def deferred(self) -> bool:
        return self.item is not None and self.item.deferred

    @property
    def review_at(self) -> int | None:
        """このフェーズのレビューが行われる番号。延期なら次にレビューがある番号。"""
        if self.owner is None or not self.planned:
            return self.number
        return self.owner.review_at(self.number)

    @property
    def covers(self) -> list[int]:
        """このフェーズのレビューが含む、延期した前のフェーズの番号。"""
        if self.owner is None or not self.planned:
            return []
        return self.owner.covered_by(self.number)

    @property
    def ended(self) -> bool:
        """終わったか。作業中（`doing/`）の子が無く、レビュー待ちか閉じた子が 1 枚以上。

        取り消しだけのフェーズは終わらない。レビュー待ちは作業としては終わっていて、
        人が見るのを待っている段（設計 §9.8）。
        """
        states = list(self.states.values())
        if not states or any(s in (ticket_mod.TODO, ticket_mod.DOING, "") for s in states):
            return False
        return any(s in (ticket_mod.REVIEW, ticket_mod.DONE) for s in states)

    @property
    def declared_review(self) -> str | None:
        """このフェーズの種類と計画の項が言う「見る場所」。宣言が無ければ None。

        計画の項は `mr` にだけ強められる（`ticket.PLAN_REVIEWS`）ので、項が `mr` なら
        種類より優先する。種類の無い番号（計画が無い、種類のファイルが無い、その名前の
        種類が読めない）は、言っている者が居ないので None。
        """
        if self.item is not None and self.item.review == ticket_mod.PLAN_REVIEW_MR:
            return phasetypes.REVIEW_MR
        if self.type is not None:
            return self.type.review
        return None

    @property
    def review_kind(self) -> str:
        """このフェーズの終わりに人がどこで見るか。`none` / `chat` / `mr`（設計 §9.8）。

        見る場所を言えるのは、種類と計画の項と、引き受けた延期だけ。そのうち厳しい側が
        勝つ。子の宣言（`human_review.required`）と実績のリスクは「要る」とだけ言い、
        場所は言わないので、宣言が「見ない」だったフェーズを `chat` へ上げるにとどまる。
        どちらも止める向きにしか働かず、宣言された `mr` を `chat` に落とすことはない。

        場所を言う者が 1 人も居なければ（計画が無い、種類が読めない）今までどおりで、
        子が「人が見る」と言うか実績が高ければ `mr`。緩い側に倒すと、種類のファイルが
        読めないときにレビューの行き先が消える。延期を引き受けている番号は、覆っている分の
        宣言が読めなくても `mr` を受け取る（`_covered_review`）ので、ここには落ちない。

        延期したフェーズは自分では見る場所を持たず、次に見るフェーズが引き受ける。
        """
        if self.deferred:
            return phasetypes.REVIEW_NONE
        from_children = any(
            t.review_required
            for t in self.tickets
            if self.states.get(t.ticket) in (ticket_mod.REVIEW, ticket_mod.DONE)
        )
        # 実績のリスクは、宣言を厳しい側にだけ上書きする（risk.py）。
        needed = from_children or self.risk_escalates
        declared = [d for d in [self.declared_review, *self.covered_reviews] if d is not None]
        if not declared:
            return phasetypes.REVIEW_MR if needed else phasetypes.REVIEW_NONE
        where = declared[0]
        for covered in declared[1:]:
            where = phasetypes.stricter(where, covered)
        # 「要る」としか言われていないフェーズは、いちばん安い見る場所まで上げる。マージリクエストを
        # 勧めるのは文の側の仕事で、強制はしない（ADR-0065）。
        if needed and where == phasetypes.REVIEW_NONE:
            where = phasetypes.REVIEW_CHAT
        return where

    @property
    def review_required(self) -> bool:
        """このフェーズの終わりに人のレビューが要るか。見る場所が `none` でなければ要る。"""
        return self.review_kind != phasetypes.REVIEW_NONE

    @property
    def review_in_chat(self) -> bool:
        """このセッションで人が見るフェーズか。ホストへは出ない。"""
        return self.review_kind == phasetypes.REVIEW_CHAT

    @property
    def gate_closed(self) -> bool:
        """レビューが済むまで止めているか。

        欄の名前は JSON の綴り（`gate_closed`）に合わせてある。人に見せる名前は
        `review_label` が出す「レビュー準備中」「レビュー待ち」で、この綴りは
        判定とボードの間の契約としてだけ残っている（設計 §9.8）。
        """
        return self.ended and self.review_required and approval.MARK_REVIEWED not in self.marks

    @property
    def review_waiting(self) -> bool:
        """依頼を出したのにまだ止まっている。人のレビュー待ち。

        ボードはこれを写すだけで、止まっているかとマーカーから組み直さない。依頼していない
        フェーズは止まっていても待ちではなく（レビュー準備中）、先に親が request を打つ。

        これはマージリクエストの待ちだけを言う。`review: chat` のフェーズは普段 `request` を
        打たないので False のまま。ボードの「受け入れ」（未解決スレッドを受け入れて進む）が
        この欄に繋がっており、写しの無い chat のフェーズに出すと打てない操作を見せることになる。
        打ったときは（実績のリスクが高いときに勧める向き。設計 §9.10）ホストに写しがあるので、
        `mr` と同じに True でよい。このセッションで見る待ちは `review_kind` と `gate_closed`
        で読む（設計 §9.8）。
        """
        return self.gate_closed and approval.MARK_REQUESTED in self.marks

    @property
    def review_label(self) -> str:
        """止めている間の呼び名。次に動く者で分かれる（設計 §9.8）。

        まだ依頼していなければ動くのはエージェント（合流・push・依頼）なので
        「レビュー準備中」、依頼が出ていれば動くのは人なので「レビュー待ち」。
        `review: chat` のフェーズは普段この段を持たないので、人が端末で
        `--reviewed --chat` を打つまで「レビュー準備中」のまま。`request` を通した
        ときだけ（設計 §9.10）`mr` と同じに「レビュー待ち」へ移る。
        """
        return LABEL_WAITING if self.review_waiting else LABEL_PREPARING


def types_path(conf: settings.Settings, root: str, project: str) -> str:
    """そのプロジェクトの層の phases.yml。空の `project` はワークスペース自身の層。

    予約名（`common` / `self`）のプロジェクトは層として数えないので、綴りを持たない
    （設計 §11.4）。名前で引くと `project or LAYER_SELF` がワークスペース自身の層の
    名札と一致し、そのプロジェクトの phases がワークスペースの層として合成される。
    """
    if settings.is_reserved_layer_name(project):
        return ""
    home = tree.project_root(conf.projects, project) if project else root
    if not home:
        return ""
    return settings.layer_path(conf, home, settings.KIND_PHASES, project or settings.LAYER_SELF)


def common_types(
    conf: settings.Settings,
) -> tuple[dict[str, phasetypes.PhaseType] | None, list[rules.Problem]]:
    """共通層の種類。ファイルが無いか壊れていれば None（番号だけの挙動）。"""
    if not conf.phases:
        return None, []
    types, notes = phasetypes.load(conf.phases)
    phasetypes.mark_source(types, settings.LAYER_COMMON)
    return types, list(notes)


def layer_types(
    conf: settings.Settings, root: str, project: str = ""
) -> tuple[dict[str, phasetypes.PhaseType] | None, list[rules.Problem]]:
    """共通層 + その層の種類と、**その層の**苦情（設計 §11.4.1）。

    どの層を足すかは親の承認済みチケットの `project:` が決める。空ならワークスペース自身の層。
    共通層自身の苦情は返さない。言う場所は `--lint` の共通層の項で、そこと二重に
    言うと、層の話を読みに来た人が同じ文を 2 度読むことになる。

    無い層は空（苦情なし）。壊れた層も空として扱うが、そちらは error を返す。
    組み込みへは落とさない。共通層が在るのに落とすと、共通層の種類が消える。
    """
    common, notes = common_types(conf)
    if common is None and notes:
        # 共通層が壊れている。層は足さない（設計 §11.2）。
        return None, []
    path = types_path(conf, root, project)
    if not path or not os.path.exists(path):
        return common, []
    extra, layer_notes = phasetypes.load(path, refs=False)
    if extra is None:
        return common, list(layer_notes)
    merged, problems = phasetypes.merge(common, extra, project or settings.LAYER_SELF)
    return merged, list(layer_notes) + problems


def load_types(
    conf: settings.Settings, root: str = "", project: str = ""
) -> dict[str, phasetypes.PhaseType] | None:
    """判定が使うフェーズの種類。どの層にも無ければ None（番号だけの挙動）。"""
    types, _ = layer_types(conf, root, project)
    return types


def _covered_review(phase: Phase | None) -> str:
    """延期を引き受けた側に渡す「覆っている分の見る場所」。読めなければ `mr`（設計 §9.8）。"""
    if phase is None or phase.declared_review is None:
        return phasetypes.REVIEW_MR
    return phase.declared_review


def phases_of(
    root: str,
    conf: settings.Settings,
    parent_id: str,
    proposed: ticket_mod.Ticket | None = None,
) -> list[Phase]:
    """この親のフェーズを番号順に。開いている承認済みチケットと閉じた承認済みチケットの両方から組む。

    親が計画を持てば、まだ子の無い番号も並ぶ（計画が言っている番号は全部フェーズ）。

    `proposed` は、承認済みチケットがまだ無いときに計画を読む親。承認で同じときに通った親の
    提案を渡す（`order_problems`）。渡さないと、親と後のフェーズの子を一緒に承認したとき
    計画が読めずフェーズが 1 つも並ばず、順序の検査が何も見ないまま通る。承認済みチケットが
    あればそちらが勝つ（改版の計画は承認されるまで効かない）。
    """
    open_copies, _ = approval.scan(conf, root)
    closed_copies, _ = approval.scan(conf, root, closed=True)
    by_number: dict[int, Phase] = {}
    owner = approval.by_id(open_copies + closed_copies).get(parent_id)
    if owner is None and proposed is not None and proposed.ticket == parent_id:
        owner = proposed
    if owner is not None and owner.has_plan:
        # 層は親の承認済みチケットの `project:` が決める（設計 §11.4.1）。人が承認した値で、
        # 子は親から継ぐので、判定が申告に依存する形にはならない。
        types = load_types(conf, root, owner.project) or {}
        for n, item in owner.numbered():
            by_number[n] = Phase(parent_id, n, item=item, type=types.get(item.type), owner=owner)
        # 延期を引き受けた側に、引き受けた分の「見る場所」を渡す。厳しい側を採るのは
        # `review_kind`。ここで渡さないと、chat の計画に mr の延期が混ざったときに
        # 引き受けた側が chat のままになり、宣言した mr が消える。
        #
        # 覆っている分の宣言が読めない番号は `mr` に倒す。延期できるのはレビューのある
        # 種類だけ（承認が確かめる）なので、そこには必ず見る場所を言った者が居た。
        # 読めなくなったことを理由に、その番号のレビューが消えてはいけない。
        for phase in by_number.values():
            phase.covered_reviews = [_covered_review(by_number.get(c)) for c in phase.covers]
    # 状態は置き場そのもの（ADR-0055）。閉じた（`done/`）、レビュー待ち（`review/`）、
    # 作業中（`doing/`）の順に読み、同じ識別子が 2 つの置き場に在れば閉じた側が勝つ。
    # 閉じたことの権威は承認済みチケットの側で、エージェントが書ける `todo/` に同じ識別子を
    # 書いてもフェーズは開き直らない（そちらは承認待ちにもならない。approval.waiting）。
    review_copies, _ = approval.scan_review(conf, root)
    seen: set[str] = set()
    for pool in (closed_copies, review_copies, open_copies):
        for t in approval.children_of(pool, parent_id):
            if t.phase is None or t.ticket in seen:
                continue
            seen.add(t.ticket)
            phase = by_number.setdefault(t.phase, Phase(parent_id, t.phase))
            phase.tickets.append(t)
            phase.states[t.ticket] = t.state
    # マーカーと記録は親のツリーに置く。子のワークツリーにも写しは checkout されるが、
    # マーカーを子の側に書くと、同じフェーズのマーカーが複数のツリーに散る。
    where = approval.home_dir(conf, root, parent_id, "")
    for phase in by_number.values():
        phase.marks = approval.marks(where, parent_id, phase.number)
        for t in phase.tickets:
            record = approval.read_child_record(
                where, parent_id, t.ticket, approval.CHILD_RECORD_RISK
            )
            if record:
                phase.risks[t.ticket] = record
    return [by_number[n] for n in sorted(by_number)]


def held_phase(root: str, conf: settings.Settings, parent_id: str) -> Phase | None:
    """レビューが済むまで止めているフェーズ。無ければ None。"""
    for phase in phases_of(root, conf, parent_id):
        if phase.gate_closed:
            return phase
    return None


def parent_for_cwd(root: str, conf: settings.Settings, cwd: str) -> ticket_mod.Ticket | None:
    """cwd が親のワークツリーの中なら、その親の承認済みチケット。"""
    t = tree.tree_of(root, cwd or os.getcwd(), conf.projects)
    if t is None or t.is_main:
        return None
    # 権威のある側を読む。承認は親のワークツリーを作る前にも打てるので、そのときの
    # 承認済みチケットは提案があったツリー（プロジェクトのルート）に在る。
    open_copies, _ = approval.scan(conf, root)
    found = tree.lookup(approval.by_id(open_copies), t.name)
    if found is None or found.is_child:
        return None
    return found


def hold_reason(phase: Phase, tool: str, root: str) -> str:
    """止めたときに返す文。いまどの段にいて、次に何をすればよいかを言う。

    段の名前（レビュー準備中／レビュー待ち）を見出しに置く。止まっている事実だけを
    言っても次の一手が出ないので、段ごとにやることを書き分ける（設計 §9.8）。
    """
    review_sh = settings.script_command(root, "ccnavi-review.sh")
    what = "サブエージェントの起動" if tool == "Agent" else "このシェル実行"
    n = phase.number
    why = "人間レビューが要る子を含みます" if not phase.planned else "レビューが要るフェーズです"
    if phase.risk_escalates:
        why = f"実績のリスクが高い（{phase.risk_line}）ので、宣言に関わらずレビューが要ります"
    later = "次のフェーズの計画（wip/proposals/todo/ への提案）はレビュー前に進めて構いません。"
    if phase.review_waiting:
        # 依頼は出してある。ここで動くのは人で、エージェントは待つほうに回る。
        todo = (
            "やること: 利用者のレビューを待ってください。レビューが終わったら "
            f"'{review_sh} confirm --phase {n}' で確かめます。指摘が付いていたら、"
            f"同じフェーズに子を足してやり直せます。{later}"
        )
    elif phase.review_in_chat:
        todo = (
            "やること: 子の成果を親ブランチへ合流し、利用者に差分を見てもらって、"
            "ターンを終えて利用者を待ってください。このフェーズはこのセッションで見る計画"
            "（review: chat）なので、マージリクエストは要りません。先へ進めるのは、"
            f"利用者が端末で打つ 'ccnavi --reviewed {n} --chat' です"
            f"（エージェントからは打てません）。{later}"
        )
    else:
        todo = (
            "やること: 子の成果を親ブランチへ合流して push し、"
            f"'{review_sh} request --phase {n} --body-file <依頼文>' "
            "でレビューを頼み、ターンを終えて利用者を待ってください。"
            f"利用者がレビューを終えたら '{review_sh} confirm --phase {n}' "
            f"で確かめます。{later}"
        )
    return "\n".join(
        [
            f"[ccnavi] {CODE_REVIEW} (parent: {phase.parent}, phase: {n}, {phase.review_label})",
            f"{phase.parent} のフェーズ {phase.label} は{phase.review_label}です。"
            f"フェーズは終わっていて、{why}。"
            f"レビュー済みのマーカーが置かれるまで、{what}を止めます。",
            todo,
        ]
    )


def _type_source(phase: Phase) -> dict:
    """種類を根拠に置くマーカーに足す、その種類の層（設計 §11.9）。

    `review:` が絡むマーカー（省略と保留）にだけ足す。他のマーカーは種類を見ずに置くので、
    層を書いても根拠にならない。種類の無いフェーズでは欄そのものを置かない。
    """
    return {"source": phase.type.source} if phase.type is not None else {}


def announce(stderr: TextIO, root: str, conf: settings.Settings, parent: ticket_mod.Ticket) -> str:
    """終わったばかりのフェーズについて 1 度だけ言う文。無ければ空文字。"""
    texts = []
    where = approval.home_dir(conf, root, parent.ticket, "")
    phases = phases_of(root, conf, parent.ticket)
    for phase in phases:
        if not phase.ended or phase.marks:
            continue
        n = phase.number
        if phase.deferred:
            at = phase.review_at
            failed = approval.write_mark(
                where, parent.ticket, n, approval.MARK_SKIPPED, {"deferred_to": at}
            )
            if failed:
                stderr.write(f"ccnavi: フェーズのマーカーを書けない: {failed}\n")
            texts.append(
                f"[ccnavi] {parent.ticket} のフェーズ {phase.label} が終わりました。"
                f"レビューは {at} 番目と一緒に見る計画なので、ここでは止めません。"
                + _next_hint(parent, phases, n)
            )
        elif phase.review_required:
            failed = approval.write_mark(
                where,
                parent.ticket,
                n,
                approval.MARK_PENDING,
                {"review": phase.review_kind, **_type_source(phase)},
            )
            if failed:
                stderr.write(f"ccnavi: フェーズのマーカーを書けない: {failed}\n")
            required = [t.ticket for t in phase.tickets if t.review_required]
            covers = (
                f"（{', '.join(str(c) for c in phase.covers)} 番目の分も含めて）"
                if phase.covers
                else ""
            )
            who = f"人間レビュー要の子: {', '.join(required)}。" if required else ""
            if phase.risk_escalates:
                who += "実績のリスクが高いので、宣言に関わらずレビューが要ります"
                who += f"（{phase.risk_line}）。"
            elif phase.risk_line:
                who += f"{phase.risk_line}。"
            hold_note = (
                "指摘があれば同じフェーズに子を足せます。レビュー済みになるまで、"
                "サブエージェントの起動とシェル実行は止まります。"
            )
            if phase.review_in_chat:
                # このセッションで人が見る。ホストへは出ないので push もしない。
                # 先へ進めるのは端末の人で、エージェントには打てない。
                advise = (
                    "実績のリスクが高いので、マージリクエストで見てもらうことを勧めます"
                    "（種類の `review` を mr にするか、利用者に相談）。それでも chat で"
                    "通すかは利用者が決めます。"
                    if phase.risk_escalates
                    else ""
                )
                texts.append(
                    f"[ccnavi] {parent.ticket} のフェーズ {phase.label} が終わりました"
                    f"（{LABEL_PREPARING}）。{who}"
                    "このフェーズはこのセッションで見る計画（review: chat）です。"
                    "子の成果を親ブランチへ合流し、利用者に差分を見てもらってください。"
                    f"{advise}"
                    "レビュー"
                    f"{covers}が済んだら、利用者が端末で "
                    f"'ccnavi --reviewed {n} --chat' を打つと先へ進めます"
                    "（この経路はエージェントには打てません）。"
                    f"ターンを終えて利用者を待ってください。{hold_note}"
                )
            else:
                texts.append(
                    f"[ccnavi] {parent.ticket} のフェーズ {phase.label} が終わりました"
                    f"（{LABEL_PREPARING}）。{who}"
                    f"子の成果を親ブランチへ合流して push し、"
                    f"'{settings.script_command(root, 'ccnavi-review.sh')} request --phase {n} "
                    "--body-file <依頼文>' "
                    f"でレビュー{covers}を頼み、ターンを終えて利用者を待ってください。{hold_note}"
                )
        else:
            failed = approval.write_mark(
                where,
                parent.ticket,
                n,
                approval.MARK_SKIPPED,
                {"tickets": [t.ticket for t in phase.tickets], **_type_source(phase)},
            )
            if failed:
                stderr.write(f"ccnavi: フェーズのマーカーを書けない: {failed}\n")
            risk_note = f"{phase.risk_line}。" if phase.risk_line else ""
            texts.append(
                f"[ccnavi] {parent.ticket} のフェーズ {phase.label} が終わりました。{risk_note}"
                "レビューは不要なので、省略して次へ進めます。省略した事実は記録に残しました。"
                + _next_hint(parent, phases, n)
            )
    return "\n\n".join(texts)


def _next_hint(parent: ticket_mod.Ticket, phases: list[Phase], number: int) -> str:
    """次のフェーズの計画を促す 1 文。計画が無ければ空。"""
    if not parent.has_plan:
        return ""
    following = [p for p in phases if p.number > number]
    if not following:
        if parent.feedback is None:
            return (
                "全体計画のフェーズは全部終わりました。レビューが済んだら、次はフィードバック計画です。"
                "親チケットに `feedback:` を足して（対応が無くても `[]` で）改版を提案し、"
                "承認を受けてください。"
            )
        return "計画のフェーズは全部終わりました。親チケットを閉じられます。"
    nxt = following[0]
    return (
        f"次は {nxt.label} の計画です。そのフェーズの子チケットを提案して承認を受けてください。"
        + (f"（種類の案内: {nxt.type.when}）" if nxt.type is not None and nxt.type.when else "")
    )


def reviewed_or_skipped(phase: Phase) -> bool:
    """このフェーズのレビューが済んでいるか、要らないか。延期は次の番号に委ねる。"""
    if phase.deferred:
        return True
    if not phase.review_required:
        return True
    return approval.MARK_REVIEWED in phase.marks


def order_problems(
    root: str,
    conf: settings.Settings,
    child: ticket_mod.Ticket,
    parent: ticket_mod.Ticket,
    types: dict[str, phasetypes.PhaseType] | None,
    adding: list[ticket_mod.Ticket] | None = None,
) -> list[rules.Problem]:
    """N 番目の子を承認してよいか。前のフェーズが閉じてレビューが済んでいるか（設計 §9.7）。

    `overlap` に挙げた組だけ、前のフェーズが開いていても通す。

    ここで出す苦情は `rules.KIND_NOT_YET`。承認は落とすが、書いた側に直すものは無く、
    前のフェーズが閉じれば同じ提案がそのまま通る。全体を見る `--lint` はこの印を見て
    warn に落とす（`lint._approval_problems`）。

    `adding` は同じ承認で先に通った、同じ親の子。承認されればそのフェーズには開いた子が
    増え、マーカーも消える（`_apply` の `clear_marks`）。ディスクの上では閉じていても、開いた
    フェーズとして読む。読まないと、前のフェーズに足す子と、そのフェーズが済んだ前提の
    次の子が一緒に承認され、1 本ずつ承認したときに落ちるものが、まとめて承認すると通る。
    """
    if not parent.has_plan or child.phase is None:
        return []
    mine = parent.item_at(child.phase)
    if mine is None:
        return []
    my_type = (types or {}).get(mine.type)
    reopened: dict[int, list[str]] = {}
    for t in adding or []:
        if t.phase is not None:
            reopened.setdefault(t.phase, []).append(t.ticket)
    # 同じ承認でフィードバック計画を出しているなら、全体計画の最後のレビューはその承認で
    # 済む（settle_last_review）。承認の前にマーカーは無いので、ここでは計画の側から読む。
    settled = len(parent.plan) if parent.feedback is not None else 0
    problems: list[rules.Problem] = []
    for phase in phases_of(root, conf, parent.ticket, parent):
        if phase.number >= child.phase:
            break
        if phase.type is not None and my_type is not None and phase.type.overlaps(my_type):
            continue
        ended = phase.ended and phase.number not in reopened
        if phase.number == settled and ended:
            continue
        if not ended:
            if phase.number in reopened:
                names = ", ".join(reopened[phase.number])
                state = (
                    f"同じ承認で {names} を足すので開き直る"
                    if phase.ended
                    else f"同じ承認で {names} を足すが、まだ閉じていない"
                )
            else:
                state = "子がまだ無い" if not phase.tickets else "子が開いている"
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    child.ticket,
                    f"{child.phase} 番目の子は、{phase.label} が閉じるまで承認しない（{state}）。"
                    "作業が終わるまで次の計画は立てない",
                    rules.KIND_NOT_YET,
                )
            )
            break
        if not reviewed_or_skipped(phase):
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    child.ticket,
                    f"{child.phase} 番目の子は、{phase.label} のレビューが済むまで承認しない",
                    rules.KIND_NOT_YET,
                )
            )
            break
    return problems


def plan_finished(root: str, conf: settings.Settings, parent: ticket_mod.Ticket) -> bool:
    """全体計画のフェーズが全部閉じ、最後のレビューを頼んであるか。フィードバック計画を出せる条件。

    レビューが「通った」ことは求めない。差し戻し（未解決の指摘）を受けたあとに出すのが
    フィードバック計画なので、通っていないのが普通。求めるのは、最後のフェーズまで閉じて
    レビューを依頼したこと。その依頼への人の応えを見て、親が計画を書く。
    """
    if not parent.has_plan:
        return False
    last = len(parent.plan)
    for phase in phases_of(root, conf, parent.ticket):
        if phase.number > last:
            break
        if not phase.ended:
            return False
        if phase.number == last:
            asked = {approval.MARK_REQUESTED, approval.MARK_REVIEWED, approval.MARK_SKIPPED}
            return reviewed_or_skipped(phase) or bool(asked & set(phase.marks))
        if not reviewed_or_skipped(phase):
            return False
    return False


def review_venues(root: str, conf: settings.Settings, parent_id: str) -> dict[int, str]:
    """この親のフェーズ番号 → 人がどこで見るか（`none` / `chat` / `mr`）。"""
    return {p.number: p.review_kind for p in phases_of(root, conf, parent_id)}


def chat_only(
    root: str, conf: settings.Settings, parent_id: str, venues: dict[int, str] | None = None
) -> bool:
    """マージリクエストに出さない運び方か。フェーズが 1 つも無ければ False。

    1 つでも `mr` で見るフェーズがあれば、その親にはマージリクエストが在る（レビューの依頼が作る）
    ので、締めも Draft を外す道に乗る。`venues` は数え直しを省くための持ち込み。
    """
    if venues is None:
        venues = review_venues(root, conf, parent_id)
    return bool(venues) and phasetypes.REVIEW_MR not in venues.values()


def settle_last_review(approved_dir: str, parent: ticket_mod.Ticket, stamp: str) -> str:
    """フィードバック計画の承認で、全体計画の最後のレビューを済んだ扱いにする。

    人がレビューの結果を見たうえで対応を計画したので、その計画の承認がレビューの
    合意になる。残った指摘は消えない。フィードバック作業フェーズの `confirm` が、
    解決されていない指摘を全部数える。
    """
    if not parent.has_plan:
        return ""
    last = len(parent.plan)
    if approval.read_mark(approved_dir, parent.ticket, last, approval.MARK_REVIEWED) is not None:
        return ""
    return approval.write_mark(
        approved_dir,
        parent.ticket,
        last,
        approval.MARK_REVIEWED,
        {"by": "feedback-plan", "at": stamp, "accepted": []},
    )


def stage(root: str, conf: settings.Settings, parent: ticket_mod.Ticket) -> str:
    """親がいまどの局面にいるか（設計 §9.7）。計画が無ければ空文字。"""
    if not parent.has_plan:
        return ""
    phases = phases_of(root, conf, parent.ticket)
    held = held_phase(root, conf, parent.ticket)
    if held is not None:
        return f"{held.review_label}（{held.label}）"
    if approval.read_parent_mark(
        approval.home_dir(conf, root, parent.ticket, ""),
        parent.ticket,
        approval.PARENT_MARK_CLOSE_EARLY,
    ):
        # 人が締めた。残りは別の issue に写してあるので、閉じられる。
        return "閉じられる（利用者が締めた）"
    in_feedback = parent.feedback is not None and len(parent.feedback) > 0
    for phase in phases:
        if not phase.ended:
            where = "フィードバック対応中" if phase.number > len(parent.plan) else "作業中"
            return f"{where}（{phase.label}）"
    if parent.feedback is None:
        return "フィードバック計画待ち"
    return "閉じられる" if not in_feedback else "閉じられる（フィードバック対応済み）"


# 範囲の外へ出した上限の名前（ScopeVerdict.limit）。
LIMIT_TICKET = "ticket"
LIMIT_PARENT = "parent"
LIMIT_TYPE = "type"
# チケット自体が信じられない（`Ticket.blocked`）。範囲を当てる前に止める（ADR-0058）。
LIMIT_BLOCKED = "blocked"

# 範囲の外として止める判定。
_OUTSIDE = (ticket_mod.OUTSIDE, rules.DENY)


@dataclass
class ScopeVerdict:
    """子のワークツリーの 1 つのパスについて、範囲の上限を合わせた判定。

    上限は子自身・親・フェーズの種類の 3 つで、厳しい側が勝つ。どれが外へ出したかを
    持つのは、文面でその上限を名指しするため。名指しが無いと、承認で見た範囲の中なのに
    止まった理由を、止められた側が読めない。
    """

    # ALLOW / ASK / DENY / OUTSIDE
    verdict: str
    # 外へ出した上限。中なら空。
    limit: str = ""
    type: phasetypes.PhaseType | None = None

    @property
    def outside(self) -> bool:
        return self.verdict in _OUTSIDE


def scope_verdict(
    child: ticket_mod.Ticket,
    parent: ticket_mod.Ticket | None,
    pt: phasetypes.PhaseType | None,
    rel: str,
) -> ScopeVerdict:
    """子の範囲を、親の範囲と種類の上限で切り詰める。

    範囲を当てる 3 か所（実行前の判定、実行後の監視、SubagentStop の差し戻し）はこれを
    通す。別に書くと、同じ書き込みが実行前は通って実行後に咎められる。承認は範囲の超過を
    警告で通すので、超えた分を止めるのはここだけになる。

    順は 子 → 親 → 種類。厳しい側が勝つので順は判定を変えないが、`limit` は最初に
    外へ出した上限を名指しする。種類の上限は allow か外しか言わない。子が ask と書いた
    場所が種類の中なら ask のまま。

    その前に `blocked` を見る。承認のときにしか当たらなかった構造の検査に引っかかった
    チケットは、範囲を当てても意味が無い（親が引けない子は、どの範囲で切り詰めるかが
    決まらない）。範囲の中でも外でも止める（ADR-0058）。
    """
    if child.blocked:
        return ScopeVerdict(rules.DENY, LIMIT_BLOCKED, pt)
    verdict = child.decide(rel)
    limit = LIMIT_TICKET if verdict in _OUTSIDE else ""
    if parent is not None:
        verdict = ticket_mod.combine(verdict, parent.decide(rel))
        if not limit and verdict in _OUTSIDE:
            limit = LIMIT_PARENT
    if pt is not None and not pt.inherits_scope:
        verdict = ticket_mod.combine(verdict, pt.decide(rel))
        if not limit and verdict in _OUTSIDE:
            limit = LIMIT_TYPE
    return ScopeVerdict(verdict, limit, pt)


def plan_item(
    child: ticket_mod.Ticket, parent: ticket_mod.Ticket | None
) -> ticket_mod.PlanItem | None:
    """子の番号が指す、親の計画の項。親が計画を持たない、番号が無い、計画に無いなら None。"""
    if parent is None or not parent.has_plan or child.phase is None:
        return None
    return parent.item_at(child.phase)


def type_for(
    conf: settings.Settings,
    root: str,
    child: ticket_mod.Ticket,
    parent: ticket_mod.Ticket | None,
    types: dict[str, phasetypes.PhaseType] | None = None,
) -> phasetypes.PhaseType | None:
    """子の番号の種類。親が計画を持たない、番号が無い、種類が引けないなら None。

    `types` を渡せばそこから引き、ファイルは読まない（実行後の監視は層ごとに 1 度だけ
    読んで持つ）。渡さなければ、親が計画を持つときだけ親の `project:` の層を読む。
    """
    item = plan_item(child, parent)
    if item is None or parent is None:
        return None
    if types is None:
        types = load_types(conf, root, parent.project)
    return (types or {}).get(item.type)


def unread_type(
    conf: settings.Settings,
    root: str,
    child: ticket_mod.Ticket,
    parent: ticket_mod.Ticket | None,
    types: dict[str, phasetypes.PhaseType] | None,
) -> str:
    """子の番号の種類が読めないなら、その種類の id。読めた、または読むものが無ければ空。

    `types` は `load_types` が返したもの（None を含む）。phases.yml がどの層にも無いのは
    番号だけの挙動で、読めないのではないので何も言わない。ファイルは在るのに種類が
    引けない（壊れた・種類を消した）ときだけ返す。そのとき判定は種類では切り詰めない。
    deny に倒すと、人が phases.yml を直している間、全部の子のワークツリーで書き込みが止まる。
    """
    item = plan_item(child, parent)
    if item is None or parent is None:
        return ""
    if types is not None:
        return "" if item.type in types else item.type
    files = (conf.phases, types_path(conf, root, parent.project))
    return item.type if any(p and os.path.exists(p) for p in files) else ""


def scope_findings(
    root: str, conf: settings.Settings, child: ticket_mod.Ticket, parent: ticket_mod.Ticket | None
) -> tuple[list[tuple[str, ScopeVerdict]], str]:
    """子のワークツリーに残っている範囲外の変更と、その判定。2 つめは読めなかった理由。

    見るのは `base_sha..HEAD` のコミット済みの差分と、未コミットの変更の両方。
    未コミットだけ見る検査では、範囲外を書いてコミットしたものが映らない。
    範囲は実行前の判定と同じく、親の範囲と種類の上限で切り詰める（scope_verdict）。
    """
    worktree = tree.worktree_path(root, child.ticket)
    if not os.path.isdir(worktree):
        return [], "ワークツリーが無い"
    # NUL 区切りで読む。既定の出力は非 ASCII と空白を含むパスを引用して 8 進に
    # 逃がすので、そのまま当てると範囲の中の日本語のファイルが必ず範囲外になる。
    #
    # `--no-renames` と `--ignore-submodules` は、差分から行が消える道を塞ぐ。改名を
    # 1 行にまとめられると移動元が消え、範囲外のファイルを範囲の中へ改名したものが
    # 素通りする。`.gitmodules` の `ignore = all` は submodule の進みを丸ごと消す
    # （`.gitmodules` は追跡されるので、外から届く）。
    #
    # status だけ `dirty` なのは、submodule の中の汚れは親のコミットに乗らないから。
    # 乗るのはポインタの移動で、`dirty` はそれを見せる。`none` にすると、submodule の
    # 中に置かれた生成物まで範囲外として報告することになる。
    paths: set[str] = set()
    if child.base_sha:
        rc, out = _git(
            worktree,
            [
                "diff",
                "--name-only",
                "--no-renames",
                "--ignore-submodules=none",
                "-z",
                f"{child.base_sha}..HEAD",
            ],
        )
        if rc != 0:
            return [], "基準点からの差分を読めない"
        paths.update(p for p in out.split("\0") if p)
    rc, out = _git(
        worktree,
        [
            "status",
            "--porcelain",
            "-z",
            "--untracked-files=all",
            "--no-renames",
            "--ignore-submodules=dirty",
        ],
    )
    if rc != 0:
        return [], "ワークツリーの状態を読めない"
    for entry in out.split("\0"):
        if len(entry) > 3 and entry[2] == " ":
            paths.add(entry[3:])
    pt = type_for(conf, root, child, parent)
    outside = []
    for rel in sorted(paths):
        rel = rel.replace("\\", "/")
        # 外すのはチケットの置き場だけ。下書きの置き場（`scratchpad/`）はここでは外さない。
        # 見ているのは `base_sha..HEAD` の差分（追跡ファイルだけ）と `git status`
        # （`--ignored` を付けない）で、追跡から外れている `scratchpad/` はどちらにも現れない。
        # 現れたということはそのツリーの git が `scratchpad/` を追跡しているということで、
        # 外してよい根拠（追跡されないので統合先へ乗らない）が崩れている。範囲外のものが
        # コミットに乗って統合先へ行く道を見ているのはここだけなので、そこは黙らせない。
        if ticket_mod.is_ticket_place(rel, conf.tickets, conf.approved):
            continue
        found = scope_verdict(child, parent, pt, rel)
        if found.outside:
            outside.append((rel, found))
    return outside, ""


def _git(cwd: str, args: list[str]) -> tuple[int, str]:
    return gitcmd.output(cwd, args, TIMEOUT_SECONDS, raw_paths=True)
