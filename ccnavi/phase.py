"""フェーズとゲート。子チケットのまとまりが終わったときに何をするかと、進もうとしたら止めること。

## フェーズの終わり

同じ親の同じ `phase` の子が `todo/` にも `doing/` にも無く、`done/` に 1 枚以上あるとき、
そのフェーズは終わり。`cancelled/` だけのフェーズは終わりではない（何も成果が無い）。

終わりの扱いは、`done/` の子に `human_review.required: true` が 1 枚でもあるかで分かれる。
あればゲートが閉じ、レビュー済みのマーカーが置かれるまでサブエージェントの起動とシェルを止める。
無ければ省略のマーカーを置いて進ませる。

## ゲートの鍵は cwd

書き込みは行き先で結ぶが、起動とシェルには行き先が無い。ゲートは呼び出しの `cwd` が
どの親の作業ツリーにあるかで親を引く。`cd` 1 回で外れる鍵だが、外れた先で起動した
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

from . import approval, gitcmd, phasetypes, risk, rules, selfguard, settings, tree
from . import ticket as ticket_mod

# ゲートの中でも通す形。状態を動かす・レビューを頼む・合流して片付ける、の 3 本を、
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
    r"(start|done|cancel|judge|request|check|note|accept|handoff|ready|wrapup|push)\b"
)

# シェルとして扱うツール。PowerShell は shellread で読めないので生の文字列に当てる。
SHELL_TOOLS = ("Bash", "PowerShell")

# ゲートが止めるツール。
GATED_TOOLS = ("Agent", *SHELL_TOOLS)

# ccnavi 自身の実行ファイルを、人の判断の経路に使う形。`--approve` `--reviewed` と、
# 状態とレビューのサブコマンド。スクリプト 2 本の中身がこれなので、スクリプトを
# 経由せずに打てば止める。CCNAVI_GUARD_TICKET_APPROVAL で切れる。
# `--approve --preview` は一覧を見るだけ（承認済みチケットを置かない）ので除く。ただし除外は
# `--approve` の枝にしか掛けない。承認そのものを行う `--yes` は独立した枝で必ず当てる。
# 免除の条件を 1 つにまとめると、同じコマンドに `--preview` を書き足すだけで `--yes` まで
# 免除される（実際にそうなっていた）。承認を通す形は、免除の理由が何であっても止める。
#
# 免除の範囲はコマンド 1 本まで。Bash なら shellread が `\x00` で切るが、PowerShell は
# 読めないので生の文字列に当たる（judge.screen）。生の文字列には `\x00` が無いので、
# 区切りとして `;` `&` `|` と改行も見る。見ないと、後ろのコマンドに書いた `--preview` が
# 前のコマンドの `--approve` を免除する。
# 語の中の目印（引用がつないだ空白）もまたがない。またぐと、引数の値に書いた
# `ccnavi --approve x "a --preview"` の `--preview` が免除の理由になる。
_NOT_PREVIEW = rf"(?![^{selfguard._NOT_A_WORD};&|\r\n]*--preview\b)"
_CLI_FORMS = (
    rf"(--yes\b|--approve\b{_NOT_PREVIEW}|--reviewed\b"
    r"|\b(ticket|review)\s+"
    r"(start|done|cancel|judge|prepare|requested|check|handoff|ready|wrapup)\b)"
)
CODE_TICKET_APPROVAL = "DENY_TICKET_APPROVAL_CLI"
TICKET_APPROVAL_RULE_ID = "builtin-guard-ticket-approval"


def commands(subject: str) -> list[str]:
    """shellread が切ったコマンドの並び。読めなかった生の文字列なら 1 本。"""
    return [c.strip() for c in subject.split("\x00") if c.strip()]


def exempt(subject: str, degraded: str) -> bool:
    """ゲートの中でも通してよいか。読み切れなかったコマンドは通さない。"""
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


# 返す文の理由コード。
CODE_GATE = "DENY_PHASE_GATE"
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
        """実績のリスクが、宣言に関わらずレビューを要る扱いにする段階か。"""
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
        states = list(self.states.values())
        if not states or any(s in (ticket_mod.TODO, ticket_mod.DOING, "") for s in states):
            return False
        return ticket_mod.DONE in states

    @property
    def review_required(self) -> bool:
        """このフェーズの終わりに人のレビューが要るか。

        計画があれば、種類の既定と計画の項と子の宣言のうち厳しい側が勝つ。延期した
        フェーズは自分ではレビューを持たず、次にレビューがあるフェーズが引き受ける。
        """
        if self.deferred:
            return False
        from_children = any(
            t.review_required for t in self.tickets if self.states.get(t.ticket) == ticket_mod.DONE
        )
        # 実績のリスクは、宣言を厳しい側にだけ上書きする（risk.py）。
        if self.risk_escalates:
            return True
        if not self.planned:
            return from_children
        by_type = self.type is not None and self.type.review == phasetypes.REVIEW_MR
        by_item = self.item is not None and self.item.review == ticket_mod.PLAN_REVIEW_MR
        return by_type or by_item or from_children or bool(self.covers)

    @property
    def gate_closed(self) -> bool:
        return self.ended and self.review_required and approval.MARK_REVIEWED not in self.marks


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
    組み込みへは落とさない。共通層が有るのに落とすと、共通層の種類が消える。
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


def sync(stderr: TextIO, root: str, conf: settings.Settings) -> list[ticket_mod.Ticket]:
    """提案の状態を承認済みチケットへ写し、閉じたものを閉じる。開いている承認済みチケットを返す。"""
    open_copies, notes = approval.scan(conf, root)
    for note in notes:
        stderr.write(f"ccnavi: {note}\n")
    remaining = []
    for copy in open_copies:
        state, path = _proposal(root, conf, copy)
        if state in ticket_mod.CLOSED:
            where = approval.dir_of(conf, copy)
            proposal, _ = ticket_mod.load(path)
            if proposal is not None:
                approval.update_copy(where, copy, _script_fields(proposal))
            failed = approval.close_copy(where, copy.ticket)
            if failed:
                stderr.write(f"ccnavi: {copy.ticket}: {failed}\n")
                remaining.append(copy)
            continue
        if path:
            proposal, _ = ticket_mod.load(path)
            if proposal is not None:
                fields = _script_fields(proposal)
                if any(getattr(copy, k) != v for k, v in fields.items()):
                    approval.update_copy(approval.dir_of(conf, copy), copy, fields)
                    for k, v in fields.items():
                        setattr(copy, k, v)
        remaining.append(copy)
    return remaining


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
    # 閉じた承認済みチケットは、提案がどこにあろうと閉じたまま。提案はエージェントが書ける
    # 場所にあるので、消す・同じ識別子を todo/ に書く、でフェーズを開き直せては
    # いけない。閉じたことの権威は承認済みチケットの側。
    for t in approval.children_of(closed_copies, parent_id):
        if t.phase is None:
            continue
        phase = by_number.setdefault(t.phase, Phase(parent_id, t.phase))
        phase.tickets.append(t)
        phase.states[t.ticket] = ticket_mod.CANCELLED if t.cancelled_at else ticket_mod.DONE
    closed_ids = {t.ticket for t in closed_copies}
    for t in approval.children_of(open_copies, parent_id):
        if t.phase is None or t.ticket in closed_ids:
            continue
        phase = by_number.setdefault(t.phase, Phase(parent_id, t.phase))
        phase.tickets.append(t)
        state, _ = _proposal(root, conf, t)
        phase.states[t.ticket] = state
    # マーカーと記録は親のツリーに置く。子の作業ツリーにも写しは checkout されるが、
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


def gate(root: str, conf: settings.Settings, parent_id: str) -> Phase | None:
    """閉じているゲート。無ければ None。"""
    for phase in phases_of(root, conf, parent_id):
        if phase.gate_closed:
            return phase
    return None


def parent_for_cwd(root: str, conf: settings.Settings, cwd: str) -> ticket_mod.Ticket | None:
    """cwd が親の作業ツリーの中なら、その親の承認済みチケット。"""
    t = tree.tree_of(root, cwd or os.getcwd(), conf.projects)
    if t is None or t.is_main:
        return None
    # 権威のある側を読む。承認は親の作業ツリーを作る前にも打てるので、そのときの
    # 承認済みチケットは提案があったツリー（プロジェクトのルート）に在る。
    open_copies, _ = approval.scan(conf, root)
    found = tree.lookup(approval.by_id(open_copies), t.name)
    if found is None or found.is_child:
        return None
    return found


def gate_reason(phase: Phase, tool: str, root: str) -> str:
    """ゲートが止めたときに返す文。次に何をすればよいかを言う。"""
    review_sh = settings.script_command(root, "ccnavi-review.sh")
    what = "サブエージェントの起動" if tool == "Agent" else "このシェル実行"
    marks = "依頼済み" if approval.MARK_REQUESTED in phase.marks else "未依頼"
    n = phase.number
    why = "人間レビューが要る子を含みます" if not phase.planned else "レビューが要るフェーズです"
    if phase.risk_escalates:
        why = f"実績のリスクが高い（{phase.risk_line}）ので、宣言に関わらずレビューが要ります"
    return "\n".join(
        [
            f"[ccnavi] {CODE_GATE} (parent: {phase.parent}, phase: {n}, {marks})",
            f"{phase.parent} のフェーズ {phase.label} は終わっていて、{why}。"
            f"レビュー済みのマーカーが置かれるまで、ゲートが{what}を止めます。",
            "やること: 子の成果を親ブランチへ合流して push し、"
            f"'{review_sh} request --phase {n} --body-file <依頼文>' "
            "でレビューを頼み、ターンを終えて利用者を待ってください。"
            f"利用者がレビューを終えたら '{review_sh} check --phase {n}' "
            "で確かめます。次のフェーズの計画（wip/tickets/todo/ への提案）は"
            "レビュー前に進めて構いません。",
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
                where, parent.ticket, n, approval.MARK_PENDING, _type_source(phase)
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
            texts.append(
                f"[ccnavi] {parent.ticket} のフェーズ {phase.label} が終わりました。{who}"
                f"子の成果を親ブランチへ合流して push し、"
                f"'{settings.script_command(root, 'ccnavi-review.sh')} request --phase {n} "
                "--body-file <依頼文>' "
                f"でレビュー{covers}を頼み、ターンを終えて利用者を待ってください。指摘があれば同じ"
                "フェーズに子を足せます。レビュー済みになるまで、ゲートがサブエージェントの起動と"
                "シェル実行を止めます。"
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
                )
            )
            break
        if not reviewed_or_skipped(phase):
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    child.ticket,
                    f"{child.phase} 番目の子は、{phase.label} のレビューが済むまで承認しない",
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


def settle_last_review(approved_dir: str, parent: ticket_mod.Ticket, stamp: str) -> str:
    """フィードバック計画の承認で、全体計画の最後のレビューを済んだ扱いにする。

    人がレビューの結果を見たうえで対応を計画したので、その計画の承認がレビューの
    合意になる。残った指摘は消えない。フィードバック作業フェーズの `check` が、
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
    """親がいまどの段階にいるか（設計 §9.7）。計画が無ければ空文字。"""
    if not parent.has_plan:
        return ""
    phases = phases_of(root, conf, parent.ticket)
    closed = gate(root, conf, parent.ticket)
    if closed is not None:
        return f"レビュー待ち（{closed.label}）"
    if approval.read_parent_mark(
        approval.home_dir(conf, root, parent.ticket, ""), parent.ticket, approval.PARENT_MARK_WRAPUP
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

# 範囲の外として止める判定。
_OUTSIDE = (ticket_mod.OUTSIDE, rules.DENY)


@dataclass
class ScopeVerdict:
    """子の作業ツリーの 1 つのパスについて、範囲の上限を合わせた判定。

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
    """
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
    deny に倒すと、人が phases.yml を直している間、全部の子の作業ツリーで書き込みが止まる。
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
    """子の作業ツリーに残っている範囲外の変更と、その判定。2 つめは読めなかった理由。

    見るのは `base_sha..HEAD` のコミット済みの差分と、未コミットの変更の両方。
    未コミットだけ見る検査では、範囲外を書いてコミットしたものが映らない。
    範囲は実行前の判定と同じく、親の範囲と種類の上限で切り詰める（scope_verdict）。
    """
    worktree = tree.worktree_path(root, child.ticket)
    if not os.path.isdir(worktree):
        return [], "作業ツリーが無い"
    # NUL 区切りで読む。既定の出力は非 ASCII と空白を含むパスを引用して 8 進に
    # 逃がすので、そのまま当てると範囲の中の日本語のファイルが必ず範囲外になる。
    paths: set[str] = set()
    if child.base_sha:
        rc, out = _git(worktree, ["diff", "--name-only", "-z", f"{child.base_sha}..HEAD"])
        if rc != 0:
            return [], "基準点からの差分を読めない"
        paths.update(p for p in out.split("\0") if p)
    rc, out = _git(
        worktree, ["status", "--porcelain", "-z", "--untracked-files=all", "--no-renames"]
    )
    if rc != 0:
        return [], "作業ツリーの状態を読めない"
    for entry in out.split("\0"):
        if len(entry) > 3 and entry[2] == " ":
            paths.add(entry[3:])
    pt = type_for(conf, root, child, parent)
    outside = []
    for rel in sorted(paths):
        rel = rel.replace("\\", "/")
        # 外すのはチケットの置き場だけ。下書きの置き場（`tmp/`）はここでは外さない。
        # 見ているのは `base_sha..HEAD` の差分（追跡ファイルだけ）と `git status`
        # （`--ignored` を付けない）で、追跡から外れている `tmp/` はどちらにも現れない。
        # 現れたということはそのツリーの git が `tmp/` を追跡しているということで、
        # 外してよい根拠（追跡されないので統合先へ乗らない）が崩れている。範囲外のものが
        # コミットに乗って統合先へ行く道を見ているのはここだけなので、そこは黙らせない。
        if ticket_mod.is_ticket_place(rel, conf.tickets, conf.approved):
            continue
        found = scope_verdict(child, parent, pt, rel)
        if found.outside:
            outside.append((rel, found))
    return outside, ""


def _proposal(root: str, conf: settings.Settings, copy: ticket_mod.Ticket) -> tuple[str, str]:
    """承認済みチケットの元になった提案が、いまどの状態にあるか。"""
    tree_root = _tree_root(root, copy.source_tree, conf.projects)
    if not tree_root:
        return "", ""
    return ticket_mod.locate(root, _tickets_rel_of(conf, copy, tree_root), tree_root, copy.ticket)


def _tickets_rel_of(conf: settings.Settings, copy: ticket_mod.Ticket, tree_root: str) -> str:
    """承認済みチケットの元になった提案の置き場（そのツリーのルートからの相対）。

    承認のときに記録した `source_path` から引く。提案は状態のディレクトリの中を動くので、
    下 2 段（`<状態>/<識別子>.md`）を落とした残りが置き場になる。

    その残りをこのツリーの下の相対に直せないときは、設定の綴りを使う。別のドライブ
    （Windows）、ツリーの外、ツリーのルートそのもの（相対が `.`）がそれにあたる。
    """
    base = os.path.dirname(os.path.dirname(copy.source_path))
    try:
        rel = os.path.relpath(base, tree_root).replace(os.sep, "/")
    except ValueError:  # 別のドライブ（Windows）
        return conf.tickets
    if rel == "." or rel.startswith("../"):
        return conf.tickets
    return rel


def _tree_root(root: str, name: str, projects_dir: str = "") -> str:
    if name == tree.MAIN:
        return tree.main_tree(root).root
    for t in [*tree.projects(projects_dir), *tree.worktrees(root, projects_dir)]:
        if t.name == name:
            return t.root
    return ""


def _script_fields(proposal: ticket_mod.Ticket) -> dict[str, str]:
    return {k: getattr(proposal, k) for k in ticket_mod.SCRIPT_FIELDS}


def _git(cwd: str, args: list[str]) -> tuple[int, str]:
    return gitcmd.output(cwd, args, TIMEOUT_SECONDS, raw_paths=True)
