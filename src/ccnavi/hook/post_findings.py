"""変わったファイルを、守ると宣言した場所とチケットの範囲に適用する。スクリプト自身の書き込みの見分け。

post から分けた。post を読まない。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..infra import fsio, gitcmd, gitstate, pathcase, tree
from ..policy import rules
from ..tickets import phase_scope
from ..tickets.approval import write_and_push
from ..tickets.model import archive, ticket_fields, ticket_model, ticket_places

# 保護領域の宣言とみなすツール名。ルールの match にこのどれかが入っていれば、
# そのルールは「この場所に書かせない」を言っている。
WRITE_TOOLS = ("Write", "Edit", "NotebookEdit")

# 返す文に載せる理由コード。cli.py の表と同じ体系から借りている。
# 監査に残す事象名がそのまま POST_VIOLATION。
CODE_VIOLATION = "POST_VIOLATION"
# 承認されたチケットの作業範囲の外が変わった。実行前チェックが同じことを
# DENY_TICKET_SCOPE で止めるが、そちらは Write / Edit の引数しか見ない。
# シェルが書いたもの、ビルドの出力、スクリプトが内部で開いたファイルは、
# 引数に現れないのでここでしか見つからない。
CODE_TICKET_SCOPE = "POST_TICKET_SCOPE"

# 範囲外の変更を報告しているのは、ルールファイルの中のルールではなく承認済みチケット。
# 出所にこの名前をつけて、ルールファイルを探しても見つからないことを示す。
TICKET_SCOPE_RULE = "builtin:ticket-scope"

# 1 回の記録に載せる code の強さの順。記録の `code` は 1 つなので、件の code が混ざった回は
# 強いほうを載せ、内訳は detail に書く（`codes_note`）。ルールが守ると宣言した場所の変更は、
# チケットの範囲の外より強い（ルールの deny はチケットが緩めない、と同じ向き）。
CODE_ORDER = (CODE_VIOLATION, CODE_TICKET_SCOPE)


def record_code(found: list[Finding]) -> str:
    """見つけた件のうち、記録の `code` に載せる 1 つ。件が無ければ空。"""
    codes = {f.code for f in found}
    return next((c for c in CODE_ORDER if c in codes), "")


def codes_note(found: list[Finding]) -> str:
    """件の code が混ざった回の内訳。混ざらなければ空。

    形は `codes POST_VIOLATION 2, POST_TICKET_SCOPE 1`。
    """
    counts = {c: sum(1 for f in found if f.code == c) for c in CODE_ORDER}
    shown = [f"{c} {n}" for c, n in counts.items() if n]
    return "codes " + ", ".join(shown) if len(shown) > 1 else ""


@dataclass
class ScopeGuard:
    """承認された作業範囲を、実行後の側から適用するための持ち物。

    実行前チェックと同じ範囲・同じ適用の仕方を使う。別に書くと、同じ書き込みが
    実行前は通って実行後に報告される（あるいはその逆）ことになり、
    どちらが本当の範囲なのかを誰も言えなくなる。キーはファイルの行き先で、
    その行き先のワークツリーに結び付いた承認済みチケットの範囲を適用する。
    """

    root: str
    copies: dict[str, ticket_model.Ticket] = field(default_factory=dict)
    # プロジェクトの置き場。ワークツリーの元リポジトリをプロジェクトまで広げる。
    projects: str = ""
    # チケットの置き場（ツリーのルートからの相対）。提案と承認済みチケット。
    tickets: str = ""
    approved: str = ""

    def finding(self, full: str) -> tuple[rules.Rule, str] | None:
        """この変更が範囲の外なら、報告する文面と出所を返す。中なら None。

        範囲は実行前チェックと同じく、親の範囲と定義の上限で切り詰める（phase_scope.scope_verdict）。
        チケットの置き場は外でも報告しない。次のチケットを提案できなくすると、
        いちど承認した範囲から永久に出られなくなる。外し方は実行前チェックと同じ関数。

        処理フロー:
        1. 変更のパスのツリーを求め（`tree.tree_of`）、主のツリーを外す。
           担当（`tree.lookup`）のないものも外す。
        2. チケットの置き場・下書きの置き場・ELI5 の置き場は外す（`ticket_places`）。
        3. 親と型の定義から範囲の判定を取る（`phase_scope.type_for`・`phase_scope.scope_verdict`）。
           範囲の中なら None を返す。
        4. 外の理由（チケットの不備・フェーズの型・親の範囲・範囲の外）ごとに文面を組み、
           規則と承認済みチケットの出所を返す。
        """
        t = tree.tree_of(self.root, full, self.projects)
        if t is None or t.is_main:
            return None
        ticket = tree.lookup(self.copies, t.name)
        if ticket is None:
            return None
        rel = tree.relative(t, full)
        if ticket_places.is_ticket_place(rel, self.tickets, self.approved):
            return None
        # 下書きの置き場（`wip/handoff/`）と ELI5 の置き場は追跡されるので、ここでも外す
        # （実行前チェックと揃える）。
        if ticket_places.is_handoff_place(rel) or ticket_places.is_eli5_place(rel):
            return None
        parent = self.copies.get(ticket.parent) if ticket.is_child else None
        # 定義は親に固定したコピー（`phases:`）から引く。`phases.yml` は開かない。
        pt = phase_scope.type_for(ticket, parent)
        found = phase_scope.scope_verdict(ticket, parent, pt, rel)
        if not found.outside:
            return None
        area = ", ".join(ticket.paths(rules.ALLOW) + ticket.paths(rules.ASK)) or "(empty)"
        # 文は報告のときに文面ファイルから引く。範囲の外に出た理由ごとに識別子を分ける。
        values = {"ticket": ticket.ticket, "worktree": t.name}
        if found.limit == phase_scope.LIMIT_BLOCKED:
            # 範囲の外に出たのではなく、チケット自体が信頼できない。範囲を
            # 見せても直しようが無いので、引っかかった検査を名指しする。
            text_id = "RULE_TICKET_SCOPE__BLOCKED"
            values["blocked"] = str(ticket.blocked)
        elif found.limit == phase_scope.LIMIT_TYPE and found.type is not None:
            text_id = "RULE_TICKET_SCOPE__TYPE"
            values.update(
                area=area,
                type_title=found.type.title,
                type_id=found.type.id,
                scope=", ".join(found.type.scope_globs),
            )
        elif found.limit == phase_scope.LIMIT_PARENT and parent is not None:
            text_id = "RULE_TICKET_SCOPE__PARENT"
            values.update(
                area=area,
                parent=parent.ticket,
                parent_area=", ".join(parent.paths(rules.ALLOW) + parent.paths(rules.ASK))
                or "(empty)",
            )
        else:
            text_id = "RULE_TICKET_SCOPE__PLAIN"
            values["area"] = area
        rule = rules.Rule(
            id=TICKET_SCOPE_RULE, root=self.root, message_id=text_id, message_values=values
        )
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
    # どのツリーで見つけたか。ワークスペースルートなら空。記録のキーと報告に使う。
    tree_name: str = ""
    tree_root: str = ""

    def key(self) -> str:
        """記録のキー。ツリーが違えば同じ相対パスでも別の変更。

        別のリポジトリに同じ名前のワークツリーがありうるので、名前だけでなくルートの場所も入れる。
        """
        if not self.tree_name:
            return self.change.key()
        where = (
            f"{self.tree_name}@{pathcase.fold(self.tree_root)}"
            if self.tree_root
            else self.tree_name
        )
        return f"{where}|{self.change.key()}"


def _findings(
    changes: list[gitstate.Change],
    rule_set: rules.RuleSet,
    mine: tuple[str, ...],
    source: str,
    scope: ScopeGuard | None,
    where: tree.Tree,
    top: str = "",
    places: tuple[str, str] = ("", ""),
    root: str = "",
) -> list[Finding]:
    """変更のうち、報告すべきものを返す。

    ccnavi 自身が書く場所は先に除く。除かないと、記録を 1 行足すたびに
    自分がその記録を違反として報告し、その報告がまた記録を 1 行増やす。

    チケットの置き場に現れた変更も、ccnavi のサブコマンドが書いたと内容から読めるぶんだけ
    除く（`_script_writes`）。除かないと、`ticket start` が着手の時刻を書くたび、
    `review request` がマーカーを置くたびに、ccnavi 自身の書き込みが
    「エージェントによる書き換え」として報告され、戻す設定では戻されて手順が進まない。

    ルールの deny と ask にヒットした変更は、範囲の外でもルールの側で報告する。
    範囲を広げても済まない場所だから。ここで範囲の側の文面を返すと、
    「範囲を広げれば済む」と読ませて、済まないことを 1 往復あとに知らせる。

    ルールの allow にヒットする変更も、チケットの範囲は適用する。実行前チェックがルールと
    チケットの厳しい側を採るのと同じ。allow を理由に飛ばすと、実行前に
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
    """チケットの置き場の変更のうち、ccnavi のサブコマンドが書いたと読めるものの実パス。

    `ticket start` / `finish` / `cancel` と `review request` / `confirm` / `ready` は、
    承認済みチケットとマーカーを書く。書いた先は `deny` と宣言された場所なので、外さないと
    自分の手順を自分で違反として報告し、戻す設定では自分で戻す。

    誰が書いたかは記録せず、何が変わったかで答える。台帳はワークスペース側にあって
    git に入らないので、承認とマーカーが親のブランチに乗って届いた先（別の機械の clone）では
    1 件も残っていない。台帳で見ると、その機械でだけ報告が出る。内容で見れば同じ答えになる。

    外すのは 3 つ（と、`mr:` の書き足し 1 つ）。

    * `review request` が親の承認済みチケットに足す `mr:`。コミット済みの版で `mr:` が空
      （欄なし・値なし）、いまの版が `mr: 12` の 1 行だけの正の整数で、ほかの欄・本文が
      1 文字も変わっていないものだけ（`_mr_filled`）。`mr:` の値の変更（整数から別の整数・
      整数から空）やほかの欄の書き換えは、今までどおり報告する
    * どちらの版も範囲を宣言していないもの（マーカー、`.risk.json`、閉じの記録）
    * スクリプトだけが書く欄（`ticket_model.SCRIPT_FIELDS`）以外が 1 文字も変わっていないチケット
    * `finish` と `cancel` の移動。正規化した内容が同じチケットが `doing/` から消えて、
      レビュー待ちか閉じた置き場に現れた組。片側だけなら外さない

    * `ready` の退避（`tickets/model/archive.py`）が消したもの。閉じた親の `done/` の親子のチケット・
      `phases/<親>/`・`flows/` の削除で、ready のマーカーに載り、消えた中身が
      ワークスペースの `logs/archive/` のコピーと同じもの（見分けは write-and-push と同じ
      `write_and_push.archived_removal`）。`root`（ワークスペースルート）が空なら外さない

    範囲や親やフェーズや本文が変わったチケット、新しく現れた承認済みチケット、消えただけの
    チケットは外さず、今までどおり報告する。承認済みチケットの frontmatter はそのワークツリーの
    作業範囲そのもので、ここで報告が出ないと、引数に現れない
    書き込み（シェル、ビルド、スクリプトが内部で開くファイル）で自分の範囲を広げる経路ができる。

    読めなかったものは外さない。git を起こせない、期限に達した、ファイルを読めない。
    どれも「変わっていない」ではない。どちらとして扱うかを間違えると、git を数秒止めるだけで
    除外が通る。

    処理フロー:
    1. 置き場の変更だけを、ツリーの相対のパスで集める
       （`ticket_places.is_ticket_place`）。無ければ空。
    2. 変更ごとに、コミット済みの版と比べる。省ける欄（`_droppable`・`_mr_filled`）を除いて
       同じ形なら外す。読めないものは外さない（`gitstate.committed_text`）。
    3. 消えた側と現れた側を集め、内容が 1 対 1 で組になるものだけ外す（`finish`・`cancel` の移動）。
    4. `ready` の退避が消したものは、`_archived_removals` で外す。
    5. 残りは報告する。
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
        # 省いてよいのは、コミット済みの版がまだ持っていない欄だけ。サブコマンドはどれも
        # 1 度しか書かないので、既に値がある欄が変わったのならサブコマンドが書いたものではない
        # （`ticket_fields.script_fields_set`）。
        drop = _droppable(before)
        # `mr:` が空から整数になっただけの変更も、ccnavi の書き足しとして省いて比べる
        # （`_mr_filled`）。最初の比べだけで、移動の組（下の gone / arrived）には使わない。
        same = drop + _mr_filled(before, now)
        if _shape(before, same) == _shape(now, same):
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
        # 行き先の正規化した内容は、消えた側の省く欄で見る。`finish` が足す `completed_at` は
        # 消えた側がまだ持っていないので省かれ、着手の時刻と基準点は両側に残る。
        shape = _shape(before, drop)
        landed = [c for c, now in arrived if _shape(now, drop) == shape]
        leaving = [c for c, other, _ in gone if _shape(other, drop) == shape]
        # 組は 1 対 1 のときだけ外す。どちらかの側に同じ内容が 2 つ以上あると、
        # どれがどれの行き先なのかを内容からは決められない。正規の移動 1 件に、
        # 同じ内容のチケットのただの削除や、行き先に直接置いた偽物も一緒に外れる。
        # 同じ内容ということは識別子まで同じということなので、揃うのは普通の手順では
        # 起きない。曖昧なら全部報告する側を採る。
        if len(landed) == 1 and len(leaving) == 1:
            out.add(change.full)
            out.add(landed[0].full)
    return out


# `ccnavi-git.sh integrate-done` が片付けの直前に残す、取り込みの記録のコピーの置き場（git-path）。
INTEGRATE_DONE_DIR = "ccnavi-integrate-done"


def integrated_paths(top: str, base: str) -> set[str]:
    """`base..HEAD` のコミットのうち、integrate の squash と git で確かめたものだけが
    変えたパス（相対）。

    ターンの終わりの検査（コミット済みの差分）が使う。`integrate-done` は squash を
    コミットしてからワークツリー・ブランチ・記録を片付ける。外さないと、正規の取り込みで変わった
    ワークスペースルートのファイルが、毎回ターンの報告に並ぶ。

    手がかりは `integrate-done` が残す記録のコピー（`<git-path ccnavi-integrate-done>/<名前>`）。
    `.git` の中にあってエージェントにも書けるので、**内容は信用せず git の状態で確かめる。**
    コミット C を squash とみなすのは、次の全部が揃ったときだけ。

    * C の親が 1 つで、それが記録の `base`。C のツリーが記録の `tree`
    * 記録の `branch` がファイル名と同じで、その名前がチケットに結び付かない
    * 記録の `branch_sha`（B）が HEAD の祖先でない（squash は B を履歴に入れない。
      C 自身や前のコミットを B に書いた偽の記録はここで外れる）
    * B を含むローカルのブランチと stash が無い（正規の片付けでブランチは消えている）
    * `merge-tree --write-tree <base> B` の結果が C のツリーと、`wip/handoff/` の下を
      除いて同じ

    数えるコミットは `gitstate.committed` と同じ（`--first-parent --no-merges`）。
    `--not --remotes` は付けない。上流のコミットは除かれず、外すパスが減る側に倒れる。返すのは、
    squash と確かめたコミットが変え、それ以外のコミットが 1 度も変えていないパスだけ。
    同じパスを別のコミットも変えていれば外さない。記録が無い、git を起こせない、
    読めないものがあるときは空（外さない）。
    """
    if not top or not base:
        return set()
    loc = gitcmd.run(top, ["rev-parse", "--git-path", INTEGRATE_DONE_DIR])
    if not loc.ok or not loc.out.strip():
        return set()
    ddir = loc.out.strip()
    if not os.path.isabs(ddir):
        ddir = os.path.join(top, ddir)
    try:
        names = sorted(n for n in os.listdir(ddir) if not n.startswith("."))
    except OSError:
        return set()
    everything = [(n, _read_record(os.path.join(ddir, n))) for n in names]
    records = [(n, r) for n, r in everything if r.get("branch") == n]
    direct = [(n, r) for n, r in everything if r.get("version") == "4"]
    if not records and not direct:
        return set()
    listed = gitcmd.run(
        top, ["rev-list", "--first-parent", "--no-merges", "--parents", f"{base}..HEAD"]
    )
    if not listed.ok:
        return set()
    squashed: set[str] = set()
    other: set[str] = set()
    for line in listed.out.splitlines():
        sha, *parents = line.split()
        if not sha:
            continue
        paths = _commit_paths(top, sha)
        if paths is None:
            return set()
        if len(parents) == 1 and (
            _is_integrate_commit(top, sha, parents[0], records)
            or _is_direct_commit(top, sha, parents[0], direct)
        ):
            squashed |= paths
        else:
            other |= paths
    return squashed - other


def _commit_paths(top: str, sha: str) -> set[str] | None:
    """そのコミットが親から変えたパス。読めなければ None。

    改名は切る（`gitstate.committed` と同じ）。
    """
    done = gitcmd.run(
        top,
        [
            "diff-tree",
            "--no-commit-id",
            "--root",
            "-r",
            "--no-renames",
            "--ignore-submodules=none",
            "--name-only",
            "-z",
            sha,
        ],
        raw_paths=True,
    )
    if not done.ok:
        return None
    return {p for p in done.out.split("\0") if p}


def _is_direct_commit(
    top: str, sha: str, parent: str, records: list[tuple[str, dict[str, str]]]
) -> bool:
    """コミットが、版 4 の取り込みのK（`integrate-done` がfast-forwardで載せたコミット）か。

    Kの親が記録の `head`、Kの木が記録の `c_tree` で、記録に `k` があればそれがK自身で、
    名前がチケットに結び付かないこと。専用のrefは求めない。gcでオブジェクトが消えたあとは
    照合できず、報告に出る（許容）。記録は `.git` の中でエージェントにも書けるので、内容だけを
    信用せずgitのオブジェクトと照らす。意図的な偽装までは防げない。
    """
    tree_of = gitcmd.run(top, ["rev-parse", "--verify", f"{sha}^{{tree}}"])
    if not tree_of.ok:
        return False
    tree_sha = tree_of.out.strip()
    for name, record in records:
        if record.get("head") != parent or record.get("c_tree") != tree_sha:
            continue
        recorded_k = record.get("k", "-")
        if recorded_k not in ("", "-") and recorded_k != sha:
            continue
        if _ticket_bound(top, name):
            continue
        return True
    return False


def _is_integrate_commit(
    top: str, sha: str, parent: str, records: list[tuple[str, dict[str, str]]]
) -> bool:
    """コミットが、どれかの記録の squash だと git で確かめられるか（`integrated_paths` の条件）。"""
    tree_of = gitcmd.run(top, ["rev-parse", "--verify", f"{sha}^{{tree}}"])
    if not tree_of.ok:
        return False
    tree_sha = tree_of.out.strip()
    for name, record in records:
        if record.get("base") != parent or record.get("tree") != tree_sha:
            continue
        tip = record.get("branch_sha", "")
        if not tip or _ticket_bound(top, name):
            continue
        tip_ok = gitcmd.run(top, ["rev-parse", "--verify", "--quiet", f"{tip}^{{commit}}"])
        if not tip_ok.ok or tip_ok.out.strip() != tip:
            continue
        # 祖先なら 0、祖先でなければ 1。それ以外（起こせない・読めない）は外さない。
        ancestor = gitcmd.run(top, ["merge-base", "--is-ancestor", tip, "HEAD"])
        if ancestor.failure or ancestor.code != 1:
            continue
        holders = gitcmd.run(
            top,
            ["for-each-ref", "--format=%(refname)", "--contains", tip, "refs/heads", "refs/stash"],
        )
        if not holders.ok or holders.out.strip():
            continue
        merged = gitcmd.run(top, ["merge-tree", "--write-tree", parent, tip])
        if not merged.ok or not merged.out.strip():
            continue
        if _same_but_handoff(top, merged.out.split("\n", 1)[0].strip(), tree_sha):
            return True
    return False


def _same_but_handoff(top: str, left: str, right: str) -> bool:
    """2 つのツリーの差が、下書きの置き場（`wip/handoff/`）の下だけ（差が無いのも含む）か。

    `integrate` は squash のあとで、ブランチが持ち込んだ `wip/handoff/` の分を index から外す。
    なので index のツリーは `merge-tree --write-tree` の結果と、その分だけ違う。`wip/` のそれ以外
    （`wip/proposals/` など）の差は数える。ツリーが読めないときは False。
    """
    if not left or not right:
        return False
    if left == right:
        return True
    diff = gitcmd.run(
        top, ["diff-tree", "-r", "--no-renames", "--name-only", "-z", left, right], raw_paths=True
    )
    if not diff.ok:
        return False
    prefix = ticket_places.HANDOFF + "/"
    return all(p.startswith(prefix) for p in diff.out.split("\0") if p)


def _ticket_bound(top: str, name: str) -> bool:
    """その名前のチケットが承認済みか提案として在るか、origin に同名のブランチが在るか。

    `integrate` が断る「チケットに結び付く」判定（`ops_stop_integrate.bound_reason`）のうち、
    このツリーとワークツリーの置き場にある同名のチケットと、手元の `origin/<名前>` の参照を見る。
    片付けの後はワークツリーが無いので、親のブランチのワークツリーは参照で見分ける。
    手元の退避は見ない。記録を偽造して、チケットに結び付くブランチの squash を外させないため。
    """
    for place in (
        ".ccnavi/approved/doing",
        ".ccnavi/approved/done",
        "wip/proposals/todo",
        "wip/proposals/review",
    ):
        for base in (top, os.path.join(top, ".claude", "worktrees", name)):
            if os.path.lexists(os.path.join(base, place, name + ".md")):
                return True
    return gitcmd.run(top, ["show-ref", "--verify", "--quiet", f"refs/remotes/origin/{name}"]).ok


def _read_record(path: str) -> dict[str, str]:
    """`<キー> <値>` を 1 行 1 項目で並べた記録を読む（sh の `ccnavi_record_get` と同じ形）。"""
    text = fsio.read_text(path, errors="replace")
    if text is None:
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        if key and key not in out:
            out[key] = value
    return out


def _archived_removals(
    here: list[tuple[gitstate.Change, str]],
    approved_rel: str,
    top: str,
    where: tree.Tree,
    root: str,
) -> set[str]:
    """`ready` の退避が消したと読める削除の実パス（`write_and_push.archived_removal`。ready
    のマーカーに載り、
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
        if write_and_push.archived_removal(inside, None, before, ready, root, where.project):
            out.add(change.full)
    return out


def _droppable(before: str | None) -> tuple[str, ...]:
    """正規化するときに省いてよいスクリプトの欄。コミット済みの版がまだ持っていない欄だけ。"""
    held = ticket_fields.script_fields_set(before) if before is not None else ()
    return tuple(f for f in ticket_model.SCRIPT_FIELDS if f not in held)


def _mr_filled(before: str | None, now: str | None) -> tuple[str, ...]:
    """`review request` が親の承認済みチケットに書き足す `mr:` を、比べるときだけ省く欄。

    ccnavi 自身の書き込みと見なす例外で、判定が緩む向きなので条件を狭くしている。
    コミット済みの版に frontmatter があって `mr:` が空（欄なし・値なし）で、いまの版の
    `mr:` が `mr: 12` の 1 行だけの正の整数のときだけ省く。省いたうえで、ほかの欄・本文が
    1 文字も変わっていなければ外れる。ほかが変わっていれば `_shape` が食い違うので報告する。
    `mr:` の値の変更（整数から別の整数・整数から空）や、コミット済みの版が無い新規のチケットは、
    ここにヒットせず今までどおり報告する。
    """
    if before is None or now is None:
        return ()
    if ticket_fields.script_shape(before, ()) is None:
        return ()
    if not ticket_fields.mr_is_empty(before):
        return ()
    if ticket_fields.plain_int_field(now, ticket_fields.MR_KEY) is None:
        return ()
    return (ticket_fields.MR_KEY,)


def _shape(text: str | None, drop: tuple[str, ...]) -> str | None:
    """その版を正規化した内容。無い・読めない・チケットでないなら None。"""
    return ticket_fields.script_shape(text, drop) if text is not None else None


def _guarding(rule_set: rules.RuleSet) -> list[rules.Rule]:
    """保護領域を宣言していると読むルール。`deny` と `ask` の両方。

    `ask` を入れるのは、そこが「ユーザが 1 度見るべき場所」だとプロジェクトが
    言っている場所だから。シェルやビルドが書いたぶんは誰にも確認が出ないまま
    通っているので、あとから言う先がここしかない。

    `allow` は入れない。ルールとしては通してよいと宣言された場所なので、ルールの側から
    報告することではない。チケットの範囲は `_findings` が別に適用する。`deny` や `ask` と
    同じ場所にヒットする `allow` があっても、強いほうを採る。適用する順は実行前チェックと同じ。
    """
    return rule_set.deny + rule_set.ask


def _guards_writes(rule: rules.Rule, path: str) -> bool:
    """このルールがこのパスへの書き込みを禁じているか。

    ルールの適用の仕方は実行前チェックと同じ rule.matches に任せる。ここで別に
    書くと、同じルールが実行前と実行後で違う場所にヒットすることになり、
    どちらが正しいのかを誰も言えなくなる。
    """
    return any(rule.matches(tool, path) for tool in WRITE_TOOLS)
