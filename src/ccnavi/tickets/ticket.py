"""作業チケットの読み込みと、そこが宣言する作業範囲。

識別子とブランチ名の規則は `ticket_ids`、チケットの形（データクラスと書式の定数）は
`ticket_model`、範囲を適用しない置き場は `ticket_places`、同じ識別子のまとめ方は `ticket_fold`、
状態の置き場を守るルールと提案の文は `ticket_guard`、スクリプトが書く欄の書き換えは
`ticket_fields` に分けてある。どれも ticket を読まない。

## チケットは提案であって正ではない

チケットはエージェントが書く。だからチケットの中身をそのまま判定に使うと、
範囲の外で止められたエージェントが、チケットに 1 行足して自分の範囲を広げられる。
止められた側が止め方を書き換えられるなら、止めたことにならない。

判定が使うのは承認済みチケット（approval.py）だけ。ワークツリーの提案は、ユーザに見せて
承認を求めるためのもので、承認されるまで判定には使われない。承認されたあとに
提案を書き換えても、判定に使われているのは承認済みチケットの側なので範囲は広がらない。

## 絞ることしかできない

チケットが宣言できるのは「ここだけ書く」であって「ここも書ける」ではない。
判定はルールの判定とチケットの判定の厳しい側を採る。チケットが足すのは
「宣言した範囲の外は止める」「deny と書いた場所は止める」「ask と書いた場所は聞く」だけで、
ルールの allow を狭めることはあっても、ルールの deny や ask を緩めることは無い。
例外は 2 つ（`ticket_places.is_unscoped`）。チケットの置き場は、次の提案を書けるようにしておくために
範囲を適用しない。
下書きの置き場（`wip/handoff/`）も、`integrate` が外し `ready` が消す置き場なので範囲を適用しない。
子は親の部分集合で、親子は厳しい側を採る。どう書いてもチケットが無いときより
緩くはならない。

## 書式

`wip/proposals/<状態>/<識別子>.md` の先頭の frontmatter。
タイプは rules.yml と同じ `deny` / `ask` / `allow` で、今判定に使われるのは Write / Edit 系の
パスの項だけ。`match` に Bash を書いた項は「効かない」と名指しで警告する。

下の例は子の提案。親の提案は `parent:` と `phase:` を書かず、`mr:`・`plan:`・`phases:` を持つ
（全体の書式は docs/21_design/tickets/proposal-format.md）。手で書かず、
`ccnavi ticket new <識別子> --title <題> --plan <型,…> --issue <値> --mr <番号>` で雛形を作る。

    ---
    version: 1
    ticket: i0050-02-01
    parent: i0050
    phase: 2
    predecessors: [i0050-01-01]
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

状態は frontmatter ではなく置き場が表す。チケットは 1 本のファイルで、
提案の置き場（`wip/proposals/`）と承認済みチケットの置き場（`.ccnavi/approved/`）を
行き来する。ユーザが動かす向きは承認済みの側へ、エージェントが動かす向きは提案の側へ。

    wip/proposals/todo      承認待ち。エージェントが書く
    .ccnavi/approved/doing  承認済み。判定が範囲を読むのはここだけ
    wip/proposals/review    作業が終わり、ユーザのレビューを待つ
    .ccnavi/approved/done   閉じた（取り消しは cancelled_at を持ってここに入る）

`ls` で見え、コミットに残り、閉じたつもりの食い違いが起きない。
"""

from __future__ import annotations

import os
import re

import yaml

from ..infra import fsio, globmatch, tree, yamlread
from ..policy import rules
from ..policy.rules import SEVERITY_ERROR, SEVERITY_WARN, Problem
from . import ticket_fold, ticket_ids, ticket_model

# 範囲の件数の上限。大量に並べてユーザがレビューしきれない
# ようにし、その中に広い範囲を紛れ込ませる手口を防ぐためのもの。
MAX_SCOPE_ENTRIES = 20

# 親が計画の使うフェーズ定義のコピーを書く欄。
PHASES_KEY = "phases"

# 範囲のパスに書けない表記。`..` は範囲の外へ出る表記、絶対パスと `~` は
# プロジェクトの外を指す表記、`$` は展開されるまで行き先が決まらない表記。
_FORBIDDEN = (("..", "`..`"), ("~", "`~`"), ("$", "`$`"))


def _plan(
    name: str, key: str, raw, start: int = 1
) -> tuple[list[ticket_model.PlanItem] | None, list[Problem]]:
    """計画のリストを読む。定義が在るかを確かめるのは承認の側。

    `start` はこの計画の最初の番号（全体計画は 1、フィードバック計画は全体計画の続き）。
    ステップの `after` は `_after` が読む。`after`
    の形の誤りと最後のステップの延期では読み込みを落とさない（`workflow.problems` が error
    で言い、判定は `blocking_problems` で親を止める）。
    """
    problems: list[Problem] = []
    if not isinstance(raw, list):
        problems.append(Problem(SEVERITY_ERROR, name, f"`{key}` はリストで書く"))
        return None, problems
    items: list[ticket_model.PlanItem] = []
    for i, entry in enumerate(raw):
        where = f"{key}[{i}]"
        if isinstance(entry, str) and entry.strip():
            items.append(ticket_model.PlanItem(type=entry.strip()))
            continue
        if isinstance(entry, dict):
            kind = _text(entry.get("type")).strip()
            review = _text(entry.get("review")).strip()
            if not kind:
                problems.append(Problem(SEVERITY_ERROR, name, f"{where} に `type` がありません。"))
                return None, problems
            if review and review not in ticket_model.PLAN_REVIEWS:
                problems.append(
                    Problem(
                        SEVERITY_ERROR,
                        name,
                        f"{where} の `review` は {' か '.join(ticket_model.PLAN_REVIEWS)}。"
                        "弱める向き（none）は書けない",
                    )
                )
                return None, problems
            item = ticket_model.PlanItem(type=kind, review=review)
            _after(item, entry.get("after"), start, start + i)
            items.append(item)
            continue
        problems.append(
            Problem(SEVERITY_ERROR, name, f"{where} は定義の名前か {{type, review, after}}")
        )
        return None, problems
    return items, problems


def _after(item: ticket_model.PlanItem, raw, first: int, number: int) -> None:
    """ステップの `after` を読む。正しい番号だけを `after` に、誤りは `after_errors` に入れる。

    正しい番号は、整数（bool は数えない）で、その計画の範囲の中（`first` 以上）で、自分（`number`）
    より小さいもの。フィードバック計画のステップは全体計画の番号を指せない（全体計画はフィードバック
    計画の前提として全部済んでいる）。どんな値でも例外を出さない。
    """
    if raw is None:
        return
    if not isinstance(raw, list):
        item.after_errors.append((raw, "番号のリストで書く"))
        return
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int):
            item.after_errors.append((value, "番号（整数）で書く"))
            continue
        if value >= number:
            item.after_errors.append((value, f"自分（{number}）より小さい番号だけを指せる"))
            continue
        if value < first:
            item.after_errors.append(
                (
                    value,
                    "フィードバック計画のステップはフィードバック計画の番号だけを指せる"
                    if first > 1
                    else "1 以上の番号で書く",
                )
            )
            continue
        if value in item.after:
            if value not in item.after_repeated:
                item.after_repeated.append(value)
            continue
        item.after.append(value)
    item.after.sort()


# 別のリポジトリの課題の表記（`owner/repo#12`。GitLab の入れ子のグループ `group/sub/proj#12` も）。
_ISSUE_REF = re.compile(r"^(?P<repo>[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)#(?P<number>\d+)$")


def _issue_ref(raw) -> tuple[str, int | None]:
    """課題の表記。答えは (別のリポジトリの `owner/repo` か空, 番号か None)。"""
    if isinstance(raw, str):
        m = _ISSUE_REF.match(raw.strip())
        if m:
            repo = m.group("repo")
            if any(part in (".", "..") for part in repo.split("/")):
                return "", None
            number = int(m.group("number"))
            return repo, (number if number > 0 else None)
    return "", _issue_number(raw)


def issue_text(raw) -> str:
    """`issue:` に書く値（`12` か `owner/repo#12`）。読めなければ空。`ticket new` が使う。"""
    repo, number = _issue_ref(raw)
    if number is None:
        return ""
    return f"{repo}#{number}" if repo else str(number)


def issue_label(t: ticket_model.Ticket) -> str:
    """課題の表記（`#12`・`owner/repo#12`）。MR の `Closes` と承認の画面が使う。無ければ空。"""
    if t.issue is None:
        return ""
    return f"{t.issue_repo}#{t.issue}"


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


def _mr_number(raw) -> int | None:
    """MR の番号。`12`・`"!12"`・`"#12"` を読む。読めなければ None。"""
    if isinstance(raw, str):
        raw = raw.strip().lstrip("!#").strip()
    return _issue_number(raw)


def load(path: str) -> tuple[ticket_model.Ticket | None, list[Problem]]:
    """チケットを読んで組み立てる。無いことは不備ではない。"""
    try:
        # fsio を通す。承認の plan（書き込みを溜める段）の中では、
        # 同じ承認で動かしたチケットを動かした後の状態で読む。
        source, text = fsio.load_source(path)
    except FileNotFoundError:
        return None, []
    except OSError as exc:
        return None, [Problem(SEVERITY_ERROR, path, f"チケットを読めない ({exc})")]
    ticket, problems = parse(text)
    if ticket is not None:
        ticket.path = os.path.realpath(path)
        ticket.source = source
    return ticket, problems


def parse(text: str) -> tuple[ticket_model.Ticket | None, list[Problem]]:
    """読み込み済みの文面からチケットを組み立てる。

    ファイルを開く部分と分けてあるのは、テストと承認の画面が同じ処理を通るため。

    欄は種類ごとに読み手を分けてある。どれかが「これ以上読めない」と言えば
    そこで止め、それまでの苦情を全部返す。苦情の順序は欄の順序のまま。
    """
    front, body, problems = _frontmatter(text)
    if front is None:
        return None, problems

    ticket = ticket_model.Ticket(
        ticket=_text(front.get("ticket")).strip(),
        parent=_text(front.get("parent")).strip(),
        title=_text(front.get("title")),
        rationale=_text(front.get("rationale")),
        raw=front,
        body=body,
    )
    if not ticket.ticket:
        problems.append(Problem(SEVERITY_ERROR, "ticket", "チケット識別子がありません。"))
        return None, problems

    for read in (_read_identity, _read_relations, _read_review, _read_scope):
        if read(ticket, front, problems):
            return None, problems
    # 保守用の子の印。値の正しさはここで見ない（落とすと承認済みチケットが索引に入らず、
    # 判定がその親を知らないまま範囲を適用しなくなる）。構造の誤りは `maintenance_problem` が
    # 承認と判定の両方で言う。
    if ticket_model.MAINTENANCE_KEY in front:
        value = front[ticket_model.MAINTENANCE_KEY]
        ticket.maintenance = "" if value is None else value
    return ticket, problems


def _read_identity(ticket: ticket_model.Ticket, front: dict, problems: list[Problem]) -> bool:
    """版と識別子と親子の形。読めなければ True。

    処理フロー:
    1. 版（`version`）が `ticket_model.VERSION` と違えば止める。
    2. 識別子を `ticket_ids.id_problem` で見る。
    3. `parent` があれば子として見る。識別子が子の形（`_CHILD`）で、親の識別子と一致するかを見る。
       親のつもりで `parent:` を書いた形は、その旨を添える。
    4. `phase` が 0 以上の整数で、`ticket_ids.MAX_CHILD_NUMBER` 以下かを見る。
       識別子のフェーズ番号と一致するかも見て、`ticket.phase` に入れる。
    5. `parent` が無ければ、子だけの欄（`phase`・`predecessors`）が在れば WARN にして読まない。
    """
    name = ticket.ticket
    version = front.get("version")
    if version != ticket_model.VERSION:
        problems.append(
            Problem(
                SEVERITY_ERROR,
                name,
                f"チケット書式の版 {version!r} は扱えない"
                f"（このビルドが読むのは {ticket_model.VERSION}）。"
                "タイプは rules.yml と同じ deny / ask / allow で、`target_directories` は読まない",
            )
        )
        return True

    problem = ticket_ids.id_problem(name)
    if problem:
        problems.append(Problem(SEVERITY_ERROR, name, problem))
        return True

    # 子か親かで、読む欄と照合を分ける。
    if ticket.parent:
        matched = ticket_ids._CHILD.match(name)
        if matched is None or matched.group("parent") != ticket.parent:
            if matched is None:
                # 親のつもりで `parent:` を書いた形（`parent:` が自分の識別子のときが典型）
                lead = (
                    f"`parent: {ticket.parent}` が自分の識別子と同じ。"
                    if ticket.parent == name
                    else ""
                )
                example = ticket_ids.child_id(ticket.parent, 5, 1)
                problems.append(
                    Problem(
                        SEVERITY_ERROR,
                        name,
                        f"{lead}子の識別子は `{ticket.parent}-<2 桁のフェーズ番号>-<2 桁の連番>`"
                        f" の形で書く（フェーズ 5 の 1 枚目なら `{example}`）。"
                        "親の提案を書いているなら、`parent:` と `phase:` の行を消す"
                        "（この 2 つは子だけの欄）。雛形は "
                        "`ccnavi ticket new <識別子> --title <題> --plan <型,…>"
                        " --issue <値> --mr <番号>` が作る",
                    )
                )
                return True
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    name,
                    f"子の識別子は `{ticket.parent}-<2 桁のフェーズ番号>-<2 桁の連番>` の形で書く"
                    f"（フェーズ 5 の 1 枚目なら `{ticket_ids.child_id(ticket.parent, 5, 1)}`）。"
                    "親の識別子が名前空間で、フェーズ番号は `phase` と同じ値",
                )
            )
            return True
        phase = front.get("phase")
        if isinstance(phase, bool) or not isinstance(phase, int) or phase < 0:
            problems.append(
                Problem(SEVERITY_ERROR, name, "子には `phase`（0 以上の整数）が必要です")
            )
            return True
        if phase > ticket_ids.MAX_CHILD_NUMBER:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    name,
                    f"`phase: {phase}` は子の識別子に指定できません。識別子のフェーズ番号は"
                    f" 2 桁（0〜{ticket_ids.MAX_CHILD_NUMBER}）なので、"
                    f"計画を {ticket_ids.MAX_CHILD_NUMBER} "
                    "フェーズまでに分ける",
                )
            )
            return True
        if int(matched.group("phase")) != phase:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    name,
                    f"子の識別子のフェーズ番号（{matched.group('phase')}）が `phase: {phase}` と"
                    f"食い違う。識別子は `{ticket.parent}-{phase:02d}-<2 桁の連番>` の形で書く",
                )
            )
            return True
        ticket.phase = phase
    else:
        for key in ("phase", "predecessors"):
            if key in front:
                problems.append(
                    Problem(SEVERITY_WARN, name, f"`{key}` は子だけの欄。親では読まない")
                )
    return False


def _read_relations(ticket: ticket_model.Ticket, front: dict, problems: list[Problem]) -> bool:
    """プロジェクト、先行、計画、課題の番号。読めなければ True。

    処理フロー:
    1. `project` を `declared_project` に入れ、`project` も同じ値にする（`scan`
       が置き場で上書きする）。
    2. `predecessors` をリストで読む。
    3. `plan`・`feedback` を `_plan` でステップにする。子では読まず WARN にする。
       フィードバックの番号は全体計画の続き。
    4. `phases` は生の値のまま `phases_raw` に持つ。定義との照合は後段で行う。
    5. `issue` を `_issue_ref` で読む。親だけが持つ欄で、子では WARN にして読まない。
    6. `mr` を `_mr_number` で読む。親だけが持つ欄で、子では WARN にして読まない。
    7. `branch` 欄が在れば止める（親のブランチ名は欄に書かず、ワークツリーの HEAD から読む）。
    """
    name = ticket.ticket
    # frontmatter の `project:` は照合用の宣言。本当のプロジェクトは提案を置いた場所で、
    # `scan` が上書きする。`scan` を通さない経路ではこの値が残る。
    ticket.declared_project = _text(front.get("project")).strip()
    ticket.project = ticket.declared_project

    # 先行と、計画・フィードバック計画のステップ。
    raw_preds = front.get("predecessors")
    if isinstance(raw_preds, list):
        ticket.predecessors = [_text(p).strip() for p in raw_preds if _text(p).strip()]
    elif raw_preds is not None:
        problems.append(Problem(SEVERITY_ERROR, name, "`predecessors` はリストで書く"))
        return True

    for key in ("plan", "feedback"):
        raw_plan = front.get(key)
        if raw_plan is None:
            continue
        if ticket.is_child:
            problems.append(Problem(SEVERITY_WARN, name, f"`{key}` は親だけの欄。子では読まない"))
            continue
        # フィードバック計画の番号は全体計画の続き。
        items, bad = _plan(name, key, raw_plan, 1 if key == "plan" else len(ticket.plan) + 1)
        problems.extend(bad)
        if items is None:
            return True
        if key == "plan":
            ticket.plan = items
        else:
            ticket.feedback = items
    # 先行関係は計画の `after` から計算する（`workflow.compute`）。
    # `workflow:` の欄があれば `--agree`・`--lint`・判定が error にする
    # （手で書いた欄が効くと読ませない）。

    # 親の `phases:`（計画が使うフェーズ定義のコピー）は生の値だけを持つ。定義に読むのは
    # `phasetypes.types_of`（compose 層）で、形の誤りも子に書いた `phases:` も、ここでは
    # 落とさず判定の `blocked` の理由にする（`phasetypes.copy_problems`）。
    ticket.phases_raw = front.get(PHASES_KEY)

    # 課題と MR は親だけが持つ。
    raw_issue = front.get("issue")
    if raw_issue is not None:
        repo, number = _issue_ref(raw_issue)
        if number is None:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    name,
                    "`issue` は課題の番号（正の整数）で書く。`#12` も可。"
                    "別のリポジトリの課題は `owner/repo#12`",
                )
            )
            return True
        if ticket.is_child:
            problems.append(Problem(SEVERITY_WARN, name, "`issue` は親だけの欄。子では読まない"))
        else:
            ticket.issue = number
            ticket.issue_repo = repo

    raw_mr = front.get("mr")
    if raw_mr is not None and raw_mr != "":
        mr_number = _mr_number(raw_mr)
        if mr_number is None:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    name,
                    "`mr` はマージリクエストの番号（正の整数）で書く。`!12`・`#12` も可。"
                    "同じリポジトリのものだけ（`owner/repo` 形式は書けない）",
                )
            )
            return True
        if ticket.is_child:
            problems.append(Problem(SEVERITY_WARN, name, "`mr` は親だけの欄。子では読まない"))
        else:
            ticket.mr = mr_number

    # 親のブランチは欄に書かせない。
    if ticket_ids.BRANCH_KEY in front:
        problems.append(
            Problem(
                SEVERITY_ERROR,
                name,
                "`branch` は指定できない欄。親のブランチ名は親のワークツリーの HEAD から読み、"
                "親の識別子はその `/` を `-` に置き換えたもの。欄を消す",
            )
        )
        return True
    return False


def _read_review(ticket: ticket_model.Ticket, front: dict, problems: list[Problem]) -> bool:
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
                SEVERITY_WARN,
                name,
                "人間レビューを省くなら `human_review.reason` に理由を書いてください",
            )
        )
    return False


def _read_scope(ticket: ticket_model.Ticket, front: dict, problems: list[Problem]) -> bool:
    """判定に使われない欄への注意、スクリプトの欄、範囲の項。範囲が成り立たなければ True。"""
    name = ticket.ticket
    for key in ("tools", "deny_commands", "target_directories"):
        if key in front:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    name,
                    f"`{key}` はこの版の判定が使わない。書いても反映されない。"
                    "範囲は deny / ask / allow のタイプに `match: Write|Edit` で書く",
                )
            )

    for key in ticket_model.SCRIPT_FIELDS:
        setattr(ticket, key, _text(front.get(key)).strip())

    entries, scope_problems = _entries(front, name)
    problems.extend(scope_problems)
    ticket.entries = entries

    if not any(e.decision in (rules.ALLOW, rules.ASK) for e in entries):
        problems.append(
            Problem(
                SEVERITY_ERROR,
                name,
                "allow にも ask にも、有効な範囲の項が 1 件もありません。このチケットは承認しても"
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
                "作業を分けるか、範囲をまとめてください",
            )
        )
        return True
    return False


def render(ticket: ticket_model.Ticket, extra: dict | None = None) -> str:
    """チケットを文面に戻す。承認済みチケットを作るときと、スクリプトが欄を書くときに使う。

    読んだ frontmatter をそのまま出す。順序が変わっても意味は変わらない。
    """
    front = dict(ticket.raw)
    if extra:
        front.update(extra)
    dumped = yaml.safe_dump(front, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"{ticket_model.FENCE}\n{dumped}{ticket_model.FENCE}\n{ticket.body}"


def subset_problems(child: ticket_model.Ticket, parent: ticket_model.Ticket) -> list[Problem]:
    """子が親の部分集合でない項を名指しする。

    glob 同士の包含は一般には決められないので、子の項の字義どおりの前置を
    親に適用する。前置が親の allow か ask に入っていれば中、入っていなければ外。
    `src/components/*` の前置は `src/components/`、`*` の前置は空文字で、
    空文字を中と言える親は `*` を持つ親だけになる。

    超えていれば warn で言う。判定が親の範囲で切り詰める。
    承認の画面は「チケットで編集対象としているが、書き込めない場所」として別の見出しで見せる。
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
        # ワイルドカードがあれば、前置に 1 文字足したパスを親に適用する。`src/b/*` なら
        # `src/b/x`。無ければパスそのもの。前置の末尾の `/` を除いて適用すると、
        # 親の `src/b/*` が `src/b` にヒットせず、正当な子を「超えている」と読む。
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
    """子の範囲の regex を名指しする文。親の検査と定義の検査の両方が同じ文を使う。

    同じ文にしておけば、承認の画面で 2 度並べずにまとめられる。
    """
    return (
        f"子の範囲に regex `{regex}` は書けない。"
        "親と定義の上限に収まるかは、判定のときに照合して確かめる"
    )


def combine(child: str, parent: str) -> str:
    """親子の判定を、厳しい側を採る形で合わせる。"""
    order = {rules.DENY: 3, rules.ASK: 2, rules.ALLOW: 1, ticket_model.OUTSIDE: 4}
    return child if order[child] >= order[parent] else parent


def scan(
    root: str,
    tickets_rel: str,
    projects_dir: str = "",
    approved: list[ticket_model.Ticket] | None = None,
) -> tuple[list[ticket_model.Ticket], list[Problem]]:
    """ワークスペース・プロジェクト・全ワークツリーの提案を集める。状態と置き場をつける。

    集めるのは `todo/`（承認待ち）と `review/`（レビュー待ち）。`review/` に在るものは
    承認済みチケットが `finish` で動いてきたもので、`completed_at` を持つ（approval.scan_review）。
    置き場はどのツリーでも同じ相対（`wip/proposals/`）で、プロジェクト向けの提案はその
    プロジェクトのツリー（か、そこから切ったワークツリー）にある。
    同じ識別子が複数のツリーにあれば、正とするツリーの側だけを残す。

    `approved` は、まとめる前の承認済みチケット（作業中・レビュー待ち・閉じた）の全部。
    渡せば、承認済みの識別子の提案は、承認済みチケットで決めた正とするツリーの側だけを残す
    （`ticket_fold.dedupe`）。組むのは `approval.scan_proposals`（承認済みチケットはそちらが読む）。
    渡さないと、正とするツリーの外に残った古い提案も残る。
    """
    found, problems = scan_all(root, tickets_rel, projects_dir)
    return ticket_fold.dedupe(found, approved), problems


def scan_all(
    root: str, tickets_rel: str, projects_dir: str = ""
) -> tuple[list[ticket_model.Ticket], list[Problem]]:
    """ワークスペースルートと全ワークツリーの提案を、重複をまとめずに集める。

    ボード（`--explain --json`）が「どのツリーにあるか」を見せるために使う。
    判定と承認は `scan` のまとめた側を読む。

    処理フロー:
    1. 置き場の組を作る。ワークスペースルート、プロジェクト、ワークツリーの順。
    2. 置き場ごとに、状態（`ticket_model.STATES`）のフォルダを読む。フォルダが無ければ飛ばす。
    3. 実体のフォルダ名（リンクを解いた先）が状態の名前と違うものは WARN にして読まない。
    4. `.md` の項目を `load` で読む。読んだ問題には、ファイルの相対パスを添えて積む。
    5. ファイル名と識別子が一致しなければ ERROR にして、その提案は入れない。
    6. 状態・ツリー名・ツリーのルート・プロジェクトを入れて `found` に積む。
    """
    found: list[ticket_model.Ticket] = []
    problems: list[Problem] = []
    ws = tree.main_tree(root)
    # 置き場がプロジェクトを決める。提案はどのツリーでも同じ相対の置き場に
    # あり、プロジェクト向けの提案はそのプロジェクトの git が持つ。承認をプロジェクトの
    # git で共有するので、提案も同じブランチに乗せる。
    # frontmatter の `project:` は照合に使うだけ。
    # 置き場の組を作る。
    places = [
        (t, tickets_rel, t.project)
        for t in [ws, *tree.projects(projects_dir), *tree.worktrees(root, projects_dir)]
    ]
    # 置き場ごとに、状態のフォルダを読む。
    for t, rel, place_project in places:
        base = os.path.join(t.root, rel.replace("/", os.sep))
        for state in ticket_model.STATES:
            directory = os.path.join(base, state)
            try:
                names = sorted(os.listdir(directory))
            except OSError:
                continue
            # 大文字小文字を区別しない機械では `DONE/` が `done/` として開ける。
            # 置き場が状態なので、表記まで同じディレクトリだけを読む。
            if os.path.basename(os.path.realpath(directory)) != state:
                problems.append(
                    Problem(
                        SEVERITY_WARN,
                        state,
                        f"{os.path.relpath(directory, root)} の表記が状態の名前 `{state}` と"
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
                # `raw` に `project` を差し込まない。承認は提案のバイト列をそのまま動かすので、
                # 書かない欄を承認する中身に混ぜることになる。
                # 承認済みチケットの `project` は置き場（ツリー）から決まる（`approval.scan_all`）。
                found.append(ticket)
    return found, problems


def _entries(front: dict, name: str) -> tuple[list[ticket_model.Entry], list[Problem]]:
    """frontmatter の欄（`rules.SECTIONS`）の項目を、判定に使える Entry の列にする。

    処理フロー:
    1. 欄ごとに、リストかを見る。欄が無ければ飛ばす。
    2. 項目ごとに、マッピングか、`match` に書き込みのツールがあるかを見る。
       書き込みのツールが無い項目は WARN にして読まない。
    3. `glob` と `regex` は、どちらか 1 つだけを書く。`_forbidden` で書けない範囲を弾き、
       絶対パスも弾く。
    4. `glob` は `globmatch.translate` で式にし、`regex` はそのまま式にする。
       どちらも大文字小文字を区別せずに組む。
    5. 組めた項目を Entry にする。

    戻り値は (entries, problems)。問題の出所の名前は `name` を使う。
    """
    problems: list[Problem] = []
    entries: list[ticket_model.Entry] = []
    for section in rules.SECTIONS:
        raw_section = front.get(section)
        if raw_section is None:
            continue
        if not isinstance(raw_section, list):
            problems.append(Problem(SEVERITY_ERROR, name, f"`{section}` がリストではない"))
            continue
        for i, raw in enumerate(raw_section):
            where = f"{section}[{i}]"
            if not isinstance(raw, dict):
                problems.append(Problem(SEVERITY_ERROR, name, f"{where} がマッピングではない"))
                continue
            # 書き込みのツールを含む項目だけを読む。
            match = _text(raw.get("match"))
            tools = [w.strip() for w in match.split("|") if w.strip()]
            if not any(t in ticket_model.WRITE_TOOLS for t in tools):
                problems.append(
                    Problem(
                        SEVERITY_WARN,
                        name,
                        f"{where} の match `{match}` は書き込みのツールを含まない。"
                        "この版の判定はパスの範囲しか見ないので反映されない",
                    )
                )
                continue
            # 範囲は glob か regex のどちらか 1 つだけを書く。
            glob = _text(raw.get("glob")).strip()
            regex = _text(raw.get("regex")).strip()
            if not glob and not regex:
                problems.append(
                    Problem(SEVERITY_ERROR, name, f"{where} に glob も regex もありません。")
                )
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
            # 範囲を式にする。
            expression = regex or ("^" + globmatch.translate(glob))
            # `src/Components/*` と `src/components/*` は同じ範囲として扱う。
            # 適用する側だけ区別すると、宣言した範囲に自分のファイルが入らない、が
            # 起きる。機械ごとに変えないのは、同じチケットがどの環境でも同じ場所で
            # 止まるため。`regex` も同じに扱う（ルールと揃える。rules._build）。
            # 区別が要る `regex` は `(?-i:...)` で囲む。
            flags = re.IGNORECASE
            try:
                compiled = re.compile(expression, flags)
            except re.error as exc:
                problems.append(Problem(SEVERITY_ERROR, name, f"{where} を式にできない: {exc}"))
                continue
            entries.append(
                ticket_model.Entry(decision=section, glob=glob, regex=regex, compiled=compiled)
            )
    return entries, problems


def _closing_fence(lines: list[str]) -> int | None:
    """閉じの `---` の行の番号。先頭の行（開きの `---`）は数えない。無ければ None。"""
    return next((i for i in range(1, len(lines)) if lines[i].strip() == ticket_model.FENCE), None)


def front_mapping(text: str) -> dict | None:
    """frontmatter を読んだマッピング。`parse` と同じ切り方で、問題の文は組まない。

    frontmatter の形でない・閉じていない・マッピングでないときは None。YAML として読めなければ
    `yaml.YAMLError` をそのまま投げる（理由の文は呼び手が組む）。提案の欄を書き換えた全文を
    読み直して、書き換えた欄のほかが元と同じかを比べるのに使う。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != ticket_model.FENCE:
        return None
    end = _closing_fence(lines)
    if end is None:
        return None
    front = yamlread.safe_load("\n".join(lines[1:end]))
    return front if isinstance(front, dict) else None


def _frontmatter(text: str) -> tuple[dict | None, str, list[Problem]]:
    """テキストの先頭の frontmatter を読み、(マッピング, 本文, 問題の列) を返す。

    処理フロー:
    1. 先頭の行が `---` でなければ止める。BOM が付いているときは、その旨を名指しする（BOM
       は読み飛ばさない）。
    2. 閉じの `---` を `_closing_fence` で探す。無ければ止める。
    3. 開きと閉じの間を `yamlread.safe_load` で読む。YAML として読めなければ止める。
    4. 空か、マッピングでなければ止める。
    5. 閉じの後ろを本文にする。末尾に改行が無ければ 1 つ足す。

    止めたときは (None, "", 問題の列) を返す。読めたときは問題の列が空になる。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != ticket_model.FENCE:
        # 通す側にはしない。「先頭の 1 バイト目から `---`」が frontmatter の契約で、
        # BOM を読み飛ばすと同じファイルが書き手の道具ごとに違う内容で通る。弾いたまま、
        # 目に見えない原因だけを名指しする。
        if lines and lines[0].lstrip(ticket_model.BOM).strip() == ticket_model.FENCE:
            detail = (
                f"先頭に BOM (U+FEFF) が付いていて `{ticket_model.FENCE}` で始まっていない。"
                "BOM 無しの UTF-8 で保存し直してください"
            )
        else:
            detail = f"先頭が `{ticket_model.FENCE}` で始まっていない"
        return None, "", [Problem(SEVERITY_ERROR, "(file)", detail)]
    end = _closing_fence(lines)
    if end is None:
        return (
            None,
            "",
            [
                Problem(
                    SEVERITY_ERROR,
                    "(file)",
                    f"frontmatter が `{ticket_model.FENCE}` で閉じていない",
                )
            ],
        )
    head = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :])
    try:
        # safe_load に限る。任意の Python の型を組み立てる load は、
        # エージェントが書けるファイルに向けては使えない（yamlread は safe な読み手だけを使う）。
        front = yamlread.safe_load(head)
    except yaml.YAMLError as exc:
        return (
            None,
            "",
            [Problem(SEVERITY_ERROR, "(file)", f"frontmatter を YAML として読めない ({exc})")],
        )
    if front is None:
        return None, "", [Problem(SEVERITY_ERROR, "(file)", "frontmatter が空")]
    if not isinstance(front, dict):
        return None, "", [Problem(SEVERITY_ERROR, "(file)", "frontmatter がマッピングではない")]
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
