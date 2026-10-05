"""変わったファイルを、守ると宣言した場所とチケットの範囲に当てる。スクリプト自身の書き込みの見分け。

post から分けた。post を読まない。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field

from ..infra import fsio, gitcmd, gitstate, tree
from ..policy import rules
from ..tickets import (
    archive,
    phase_scope,
    phasetypes,
    ticket_fields,
    ticket_model,
    ticket_places,
)
from . import c1

# 保護領域の宣言とみなすツール名。ルールの match にこのどれかが入っていれば、
# そのルールは「この場所に書かせない」を言っている。
WRITE_TOOLS = ("Write", "Edit", "NotebookEdit")

# 返す文に載せる理由コード。cli.py の表と同じ体系から借りている。
# 設計 7.2 が監査に残す事象名がそのまま POST_VIOLATION。
CODE_VIOLATION = "POST_VIOLATION"
# 承認されたチケットの作業範囲の外が変わった。実行前チェックが同じことを
# DENY_TICKET_SCOPE で止めるが、そちらは Write / Edit の引数しか見ない。
# シェルが書いたもの、ビルドの出力、スクリプトが内部で開いたファイルは、
# 引数に現れないのでここでしか見つからない。
CODE_TICKET_SCOPE = "POST_TICKET_SCOPE"

# 範囲外の変更を報告しているのは、ルールファイルの中のルールではなく承認済みチケット。
# 出所にこの名前をつけて、ルールファイルを探しても見つからないことを示す。
TICKET_SCOPE_RULE = "(ticket-scope)"


def _spelled(change: gitstate.Change, top: str) -> str:
    """変更の、リンクを解く前の絶対パス。ツリーのルートが分からなければ解いた先。"""
    if not top or not change.path:
        return change.full
    return os.path.join(top, change.path.replace("/", os.sep))


@dataclass
class ScopeGuard:
    """承認された作業範囲を、実行後の側から当てるための持ち物。

    実行前チェックと同じ範囲・同じ当て方を使う。別に書くと、同じ書き込みが
    実行前は通って実行後に報告される（あるいはその逆）ことになり、
    どちらが本当の範囲なのかを誰も言えなくなる。鍵はファイルの行き先で、
    その行き先のワークツリーに結び付いた承認済みチケットの範囲を当てる。
    """

    root: str
    copies: dict[str, ticket_model.Ticket] = field(default_factory=dict)
    # プロジェクトの置き場。ワークツリーの元リポジトリをプロジェクトまで広げる（設計 11.3）。
    projects: str = ""
    # チケットの置き場（ツリーのルートからの相対）。提案と承認済みチケット。
    tickets: str = ""
    approved: str = ""
    # フェーズの種類。親の `project:` のレイヤーごとに、作るときに 1 度だけ読んだもの。
    # 変更 1 件ごとに phases.yml を開かない。読めないレイヤーは空。
    types: dict[str, dict[str, phasetypes.PhaseType]] = field(default_factory=dict)

    def finding(self, full: str) -> tuple[rules.Rule, str] | None:
        """この変更が範囲の外なら、報告する文面と出所を返す。中なら None。

        範囲は実行前チェックと同じく、親の範囲と種類の上限で切り詰める（phase_scope.scope_verdict）。
        チケットの置き場は外でも報告しない。次のチケットを提案できなくすると、
        いちど承認した範囲から永久に出られなくなる。外し方は実行前チェックと同じ関数。
        """
        t = tree.tree_of(self.root, full, self.projects)
        if t is None or t.is_main:
            return None
        ticket = tree.lookup(self.copies, t.name)
        if ticket is None:
            return None
        rel = tree.relative(t, full)
        # 外すのはチケットの置き場だけ。下書きの置き場（`scratchpad/`）はここでは外さない。
        # 見ているのは `git status`（`--ignored` を付けない）が挙げた変更なので、
        # 追跡から外れている `scratchpad/` はそもそもこの経路に現れない。現れたということは
        # そのツリーの git が `scratchpad/` を追跡しているということで、外してよい根拠
        # （追跡されないので統合先へ乗らない）が崩れている。そこは報告から外さずに言う。
        if ticket_places.is_ticket_place(rel, self.tickets, self.approved):
            return None
        # ELI5 の置き場は追跡されるので、ここでも外す（実行前チェックと揃える）。
        if ticket_places.is_eli5_place(rel):
            return None
        parent = self.copies.get(ticket.parent) if ticket.is_child else None
        item = phase_scope.plan_item(ticket, parent)
        pt = (
            self.types.get(parent.project, {}).get(item.type)
            if item is not None and parent is not None
            else None
        )
        found = phase_scope.scope_verdict(ticket, parent, pt, rel)
        if not found.outside:
            return None
        area = ", ".join(ticket.paths(rules.ALLOW) + ticket.paths(rules.ASK)) or "(empty)"
        inside = (
            f"This path is inside the work area that ticket {ticket.ticket} declares ({area}) "
            f"for worktree {t.name}, but "
        )
        overflow = (
            "The ticket was approved with that overflow shown as a warning; writes there stay "
            "blocked. "
        )
        if found.limit == phase_scope.LIMIT_BLOCKED:
            # 範囲の外に出たのではなく、チケット自体が信頼できない。範囲を
            # 見せても直しようが無いので、引っかかった検査を名指しする。
            message = (
                f"The approved ticket {ticket.ticket} for worktree {t.name} does not hold "
                f"together ({ticket.blocked}), so its work area is not in effect and every "
                "change here is reported. Nothing you write can fix this: the user has to "
                "repair the approved ticket or where it sits. Tell them the problem above and "
                "ask them to run 'ccnavi --lint', which names every ticket in this state."
            )
        elif found.limit == phase_scope.LIMIT_TYPE and found.type is not None:
            message = (
                inside + f"outside what phase type {found.type.title} ({found.type.id}) allows "
                f"({', '.join(found.type.scope_globs)}). "
                + overflow
                + "Send the output to a path that type covers, do the work in a later phase "
                "whose type covers this path, or ask the user to change phases.yml."
            )
        elif found.limit == phase_scope.LIMIT_PARENT and parent is not None:
            parent_area = (
                ", ".join(parent.paths(rules.ALLOW) + parent.paths(rules.ASK)) or "(empty)"
            )
            message = (
                inside
                + f"outside its parent {parent.ticket} ({parent_area}). "
                + overflow
                + "Send the output inside the parent's area, or, if the task genuinely needs "
                "this path, tell the parent so it can propose a ticket whose parent covers it "
                "and ask the user to run 'ccnavi --agree'."
            )
        else:
            message = (
                f"This path is outside the work area that ticket {ticket.ticket} declares "
                f"({area}) for worktree {t.name}. Send the output inside that area, or, if the "
                "task genuinely needs this path, tell the parent so it can propose a ticket that "
                "covers it and ask the user to run 'ccnavi --agree'."
            )
        rule = rules.Rule(id=TICKET_SCOPE_RULE, message=message)
        # 出所はその承認済みチケット自身の場所。承認済みチケットはツリーごとに在るので、
        # 1 か所にはまとめられない。
        return rule, ticket.path


@dataclass
class Finding:
    """報告する 1 件。何が変わったかと、それを報告しているのが誰かの組。

    報告する側が 2 通りある。ルールファイルが守ると宣言した場所と、承認された
    チケットが作業範囲の外だと言う場所。どちらから来たかを一緒に持っておかないと、
    報告の出所がすべてルールファイルと書かれることになり、見に行ったユーザが
    そこに無いルールを探すことになる。
    """

    change: gitstate.Change
    group: list[rules.Rule]
    source: str
    code: str
    # どのツリーで見つけたか。ワークスペースルートなら空。記録の鍵と報告に使う。
    tree_name: str = ""
    tree_root: str = ""

    def key(self) -> str:
        """記録の鍵。ツリーが違えば同じ相対パスでも別の変更。"""
        return f"{self.tree_name}|{self.change.key()}" if self.tree_name else self.change.key()


def _findings(
    changes: list[gitstate.Change],
    rule_set: rules.RuleSet,
    mine: tuple[str, ...],
    source: str,
    scope: ScopeGuard | None,
    where: tree.Tree,
    top: str = "",
    places: tuple[str, str] = ("", ""),
    synced: Callable[..., bool] | None = None,
    root: str = "",
) -> list[Finding]:
    """変更のうち、報告すべきものを返す。

    ccnavi 自身が書く場所は先に落とす。落とさないと、記録を 1 行足すたびに
    自分がその記録を違反として報告し、その報告がまた記録を 1 行増やす。

    チケットの置き場に現れた変更も、ccnavi の副命令が書いたと内容から読めるぶんだけ
    落とす（`_script_writes`）。落とさないと、`ticket start` が着手の時刻を書くたび、
    `review request` がマーカーを置くたびに、ccnavi 自身の書き込みが
    「エージェントによる書き換え」として報告され、戻す設定では戻されて手順が進まない。

    ルールの deny と ask に当たった変更は、範囲の外でもルールの側で報告する。
    範囲を広げても済まない場所だから。ここで範囲の側の文面を返すと、
    「範囲を広げれば済む」と読ませて、済まないことを 1 往復あとに知らせる。

    ルールの allow に当たる変更も、チケットの範囲は当てる。実行前チェックがルールと
    チケットの厳しい側を採るのと同じ（設計 5）。allow を理由に飛ばすと、実行前に
    止まる書き込みがシェルから入ったときに誰も言わない。
    """
    own = tuple(os.path.realpath(p) for p in mine if p)
    name = where.name
    tree_root = where.root
    script = _script_writes(changes, places, top, where, root)
    found = []
    for change in changes:
        if any(change.full == p or change.full.startswith(p + os.sep) for p in own):
            continue
        if change.full in script:
            continue
        # 着手のときに共通レイヤーでプロジェクトのレイヤーを上書きした分
        # （`configsync.is_synced_write`）。
        # 内容と上書きの記録で見分け、読めないものは外さない。渡すのは解く前のパス。
        # 解いた先で答えると、設定を別のコピーへのシンボリックリンクに差し替えた形が、
        # 指す先の中身で外れる。
        if synced is not None and synced(_spelled(change, top or tree_root)):
            continue
        group = [rule for rule in _guarding(rule_set) if _guards_writes(rule, change.full)]
        if group:
            found.append(Finding(change, group, source, CODE_VIOLATION, name, tree_root))
            continue
        if scope is None:
            continue
        hit = scope.finding(change.full)
        if hit is not None:
            rule, place = hit
            found.append(Finding(change, [rule], place, CODE_TICKET_SCOPE, name, tree_root))
    return found


def _script_writes(
    changes: list[gitstate.Change],
    places: tuple[str, str],
    top: str,
    where: tree.Tree,
    root: str = "",
) -> set[str]:
    """チケットの置き場の変更のうち、ccnavi の副命令が書いたと読めるものの実パス。

    `ticket start` / `finish` / `cancel` と `review request` / `confirm` / `ready` は、
    承認済みチケットとマーカーを書く。書いた先は `deny` と宣言された場所なので、外さないと
    自分の手順を自分で違反として報告し、戻す設定では自分で戻す。

    **誰が書いたかは記録せず、何が変わったかで答える。** 台帳はワークスペース側にあって
    git に入らないので、承認とマーカーが親のブランチに乗って届いた先（別の機械の clone）では
    1 件も残っていない。台帳で見ると、その機械でだけ報告が出る。内容で見れば同じ答えになる。

    外すのは 3 つ。

    * どちらの版も範囲を宣言していないもの（マーカー、`.risk.json`、閉じの記録）
    * スクリプトだけが書く欄（`ticket_model.SCRIPT_FIELDS`）以外が 1 文字も変わっていないチケット
    * `finish` と `cancel` の移動。正規化した内容が同じチケットが `doing/` から消えて、
      レビュー待ちか閉じた置き場に現れた組。片側だけなら外さない

    * `ready` の退避（`tickets/archive.py`）が消したもの。閉じた親の `done/` の親子のチケット・
      `phases/<親>/`・`events/`・`flows/` の削除で、ready のマーカーに載り、消えた中身が
      ワークスペースの `logs/archive/` のコピーと同じもの（見分けは C1 と同じ
      `c1.archived_removal`）。`root`（ワークスペースルート）が空なら外さない

    範囲や親やフェーズや本文が変わったチケット、新しく現れた承認済みチケット、消えただけの
    チケットは外さず、今までどおり報告する。承認済みチケットの frontmatter はそのワークツリーの
    作業範囲そのもので、ここで報告が出ないと、引数に現れない
    書き込み（シェル、ビルド、スクリプトが内部で開くファイル）で自分の範囲を広げる経路ができる。

    読めなかったものは外さない。git を起こせない、期限に達した、ファイルを読めない。
    どれも「変わっていない」ではない。どちらとして扱うかを間違えると、git を数秒止めるだけで
    除外が通る。
    """
    tickets_rel, approved_rel = places
    if not top or not (tickets_rel or approved_rel):
        return set()
    here = [
        (change, rel)
        for change in changes
        for rel in [tree.relative(where, change.full)]
        if ticket_places.is_ticket_place(rel, tickets_rel, approved_rel)
    ]
    if not here:
        return set()

    out: set[str] = set()
    # 消えた側と現れた側。組になったときだけ外す（`finish` と `cancel` の移動）。
    gone: list[tuple[gitstate.Change, str, tuple[str, ...]]] = []
    arrived: list[tuple[gitstate.Change, str]] = []
    for change, rel in here:
        before, readable = gitstate.committed_text(top, change.path)
        if not readable:
            # 読めないものは外さない。読めないことは「変わっていない」ではない。
            continue
        now = fsio.read_text(change.full, errors="replace")
        # 落としてよいのは、コミット済みの版がまだ持っていない欄だけ。副命令はどれも
        # 1 度しか書かないので、既に値がある欄が変わったのなら副命令が書いたものではない
        # （`ticket_fields.script_fields_set`）。
        drop = _droppable(before)
        if _shape(before, drop) == _shape(now, drop):
            # 正規化した内容が同じ。どちらも範囲を宣言していない（マーカー・記録）か、
            # まだ無かったスクリプトの欄が足されただけか。
            out.add(change.full)
        elif now is None or _shape(now, drop) is None:
            if before is not None and ticket_places.leaves_open_state(
                rel, tickets_rel, approved_rel
            ):
                gone.append((change, before, drop))
        elif _shape(before, drop) is None and ticket_places.lands_in_finished_state(
            rel, tickets_rel, approved_rel
        ):
            arrived.append((change, now))
    out |= _archived_removals(here, approved_rel, top, where, root)
    for change, before, drop in gone:
        # 行き先の正規化した内容は、消えた側の落とす欄で見る。`finish` が足す `completed_at` は
        # 消えた側がまだ持っていないので落ち、着手の時刻と基準点は両側に残る。
        shape = _shape(before, drop)
        landed = [c for c, now in arrived if _shape(now, drop) == shape]
        leaving = [c for c, other, _ in gone if _shape(other, drop) == shape]
        # **組は 1 対 1 のときだけ外す。** どちらかの側に同じ内容が 2 つ以上あると、
        # どれがどれの行き先なのかを内容からは決められない。正規の移動 1 件に、
        # 同じ内容のチケットのただの削除や、行き先に直接置いた偽物も一緒に外れる。
        # 同じ内容ということは識別子まで同じということなので、揃うのは普通の手順では
        # 起きない。曖昧なら全部報告する側を採る。
        if len(landed) == 1 and len(leaving) == 1:
            out.add(change.full)
            out.add(landed[0].full)
    return out


def _archived_removals(
    here: list[tuple[gitstate.Change, str]],
    approved_rel: str,
    top: str,
    where: tree.Tree,
    root: str,
) -> set[str]:
    """`ready` の退避が消したと読める削除の実パス（`c1.archived_removal`。ready のマーカーに載り、
    中身が退避したコピーと同じもの）。"""
    if not root or not approved_rel:
        return set()
    approved = approved_rel.strip("/")
    gone = [(change, rel) for change, rel in here if not os.path.lexists(change.full)]
    if not gone:
        return set()
    ready = archive.ready_files(root, where.project, where.root, archive.tree_head(where.root))
    if not ready:
        return set()
    out: set[str] = set()
    for change, rel in gone:
        if not rel.startswith(approved + "/"):
            continue
        before, readable = gitcmd.blob(top, "HEAD", change.path)
        if not readable or before is None:
            continue
        inside = rel[len(approved) + 1 :]
        if c1.archived_removal(inside, None, before, ready, root, where.project):
            out.add(change.full)
    return out


def _droppable(before: str | None) -> tuple[str, ...]:
    """正規化するときに落としてよいスクリプトの欄。コミット済みの版がまだ持っていない欄だけ。"""
    held = ticket_fields.script_fields_set(before) if before is not None else ()
    return tuple(f for f in ticket_model.SCRIPT_FIELDS if f not in held)


def _shape(text: str | None, drop: tuple[str, ...]) -> str | None:
    """その版を正規化した内容。無い・読めない・チケットでないなら None。"""
    return ticket_fields.script_shape(text, drop) if text is not None else None


def _guarding(rule_set: rules.RuleSet) -> list[rules.Rule]:
    """保護領域を宣言していると読むルール。`deny` と `ask` の両方。

    `ask` を入れるのは、そこが「ユーザが 1 度見るべき場所」だとプロジェクトが
    言っている場所だから。シェルやビルドが書いたぶんは誰にも確認が出ないまま
    通っているので、あとから言う先がここしかない。

    `allow` は入れない。ルールとしては通してよいと宣言された場所なので、ルールの側から
    報告することではない。チケットの範囲は `_findings` が別に当てる。`deny` や `ask` と
    同じ場所に当たる `allow` があっても、強いほうを採る。当てる順は実行前チェックと同じ。
    """
    return rule_set.deny + rule_set.ask


def _guards_writes(rule: rules.Rule, path: str) -> bool:
    """このルールがこのパスへの書き込みを禁じているか。

    ルールの当て方は実行前チェックと同じ rule.matches に任せる。ここで別に
    書くと、同じルールが実行前と実行後で違う場所に当たることになり、
    どちらが正しいのかを誰も言えなくなる。
    """
    return any(rule.matches(tool, path) for tool in WRITE_TOOLS)
