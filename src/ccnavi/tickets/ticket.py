"""作業チケットの読み込みと、そこが宣言する作業範囲。

識別子とブランチ名の規則は `ticket_ids`、チケットの形（データクラスと書式の定数）は
`ticket_model`、範囲を当てない置き場は `ticket_places`、同じ識別子のまとめ方は `ticket_fold`、
状態の置き場を守るルールと提案の文は `ticket_guard`、スクリプトが書く欄の書き換えは
`ticket_fields` に分けてある。どれも ticket を読まない。

## チケットは提案であって本物ではない

チケットはエージェントが書く。だからチケットの中身をそのまま判定に使うと、
範囲の外で止められたエージェントが、チケットに 1 行足して自分の範囲を広げられる。
止められた側が止め方を書き換えられるなら、止めたことにならない。

判定が使うのは承認済みチケット（approval.py）だけ。作業ツリーの提案は、ユーザに見せて
承認を求めるためのもので、承認されるまで判定には使われない。承認されたあとに
提案を書き換えても、判定に使われているのは承認済みチケットの側なので範囲は広がらない。

## 絞ることしかできない

チケットが宣言できるのは「ここだけ書く」であって「ここも書ける」ではない。
判定はルールの判定とチケットの判定の厳しい側を採る（設計 1）。チケットが足すのは
「宣言した範囲の外は止める」「deny と書いた場所は止める」「ask と書いた場所は聞く」だけで、
ルールの allow を狭めることはあっても、ルールの deny や ask を緩めることは無い。
例外は 2 つ（`ticket_places.is_unscoped`）。チケットの置き場は、次の提案を書けるようにしておくために
範囲を当てない。
下書きの置き場（`scratchpad/`）は、git が追跡しないので範囲を当てない。
子は親の部分集合で、親子は厳しい側を採る。どう書いてもチケットが無いときより
緩くはならない。

## 書式

`wip/proposals/<状態>/<識別子>.md` の先頭の frontmatter。設計 9.3。
タイプは rules.yml と同じ `deny` / `ask` / `allow` で、今判定に使われるのは Write / Edit 系の
パスの項だけ。`match` に Bash を書いた項は「効かない」と名指しで警告する。

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

# 範囲の件数の上限。設計 9.3。大量に並べてユーザがレビューしきれない
# ようにし、その中に広い範囲を紛れ込ませる手口を防ぐためのもの。
MAX_SCOPE_ENTRIES = 20

# 親が計画の使うフェーズ定義の写しを書く欄。
PHASES_KEY = "phases"

# 範囲のパスに書けない表記。`..` は範囲の外へ出る表記、絶対パスと `~` は
# プロジェクトの外を指す表記、`$` は展開されるまで行き先が決まらない表記。
_FORBIDDEN = (("..", "`..`"), ("~", "`~`"), ("$", "`$`"))


def _plan(
    name: str, key: str, raw, start: int = 1
) -> tuple[list[ticket_model.PlanItem] | None, list[Problem]]:
    """計画のリストを読む。定義が在るかはここでは見ない（定義を読むのは承認の側）。

    `start` はこの計画の最初の番号（全体計画は 1、フィードバック計画は全体計画の続き）。
    項の `after` は `_after` が読む。`after` の形の誤りと最後の項の延期では読み込みを落とさない
    （`workflow.problems` が error で言い、判定は `blocking_problems` で親を止める）。
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
                problems.append(Problem(SEVERITY_ERROR, name, f"{where} に `type` が無い"))
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
    """項の `after` を読む。正しい番号だけを `after` に、誤りは `after_errors` に入れる。

    正しい番号は、整数（bool は数えない）で、その計画の範囲の中（`first` 以上）で、自分（`number`）
    より小さいもの。フィードバック計画の項は全体計画の番号を指せない（全体計画はフィードバック
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
                    "フィードバック計画の項はフィードバック計画の番号だけを指せる"
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


def load(path: str) -> tuple[ticket_model.Ticket | None, list[Problem]]:
    """チケットを読んで組み立てる。無いことは不備ではない。"""
    try:
        # fsio を通す。承認の plan（書き込みを溜める段）の中では、
        # 同じ承認で動かしたチケットを動かした後の状態で読む。
        text = fsio.load_text(path)
    except FileNotFoundError:
        return None, []
    except OSError as exc:
        return None, [Problem(SEVERITY_ERROR, path, f"チケットを読めない ({exc})")]
    ticket, problems = parse(text)
    if ticket is not None:
        ticket.path = os.path.realpath(path)
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
        problems.append(Problem(SEVERITY_ERROR, "ticket", "チケット識別子が無い"))
        return None, problems

    for read in (_read_identity, _read_relations, _read_review, _read_scope):
        if read(ticket, front, problems):
            return None, problems
    return ticket, problems


def _read_identity(ticket: ticket_model.Ticket, front: dict, problems: list[Problem]) -> bool:
    """版と識別子と親子の形。読めなければ True。"""
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

    if ticket.parent:
        matched = ticket_ids._CHILD.match(name)
        if matched is None or matched.group("parent") != ticket.parent:
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
            problems.append(Problem(SEVERITY_ERROR, name, "子には `phase`（0 以上の整数）が要る"))
            return True
        if phase > ticket_ids.MAX_CHILD_NUMBER:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    name,
                    f"`phase: {phase}` は子の識別子に書けない。識別子のフェーズ番号は"
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
    """プロジェクト、先行、計画、課題の番号。読めなければ True。"""
    name = ticket.ticket
    # frontmatter の `project:` は照合用の宣言。本当のプロジェクトは提案を置いた場所で、
    # `scan` が上書きする（設計 11.5）。`scan` を通さない経路ではこの値が残る。
    ticket.declared_project = _text(front.get("project")).strip()
    ticket.project = ticket.declared_project

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
    # `workflow:` の欄は読まない。待ち方は計画の `after` から計算する（`workflow.compute`）。
    # 欄があれば `--agree`・`--lint`・判定が error にする（手で書いた欄が効くと読ませない）。

    # 親の `phases:`（計画が使うフェーズ定義の写し）は生の値だけを持つ。定義に読むのは
    # `phasetypes.types_of`（compose 層）で、形の誤りも子に書いた `phases:` も、ここでは
    # 落とさず判定の `blocked` の理由にする（`phasetypes.copy_problems`）。
    ticket.phases_raw = front.get(PHASES_KEY)

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

    raw_branch = front.get(ticket_ids.BRANCH_KEY)
    if raw_branch is not None:
        if not isinstance(raw_branch, str):
            problems.append(Problem(SEVERITY_ERROR, name, "`branch` はブランチ名を文字列で書く"))
            return True
        if ticket.is_child:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    name,
                    "`branch` は親だけの欄。子では読まない（子のブランチは子の識別子）",
                )
            )
        else:
            spelled = raw_branch.strip()
            why = ticket_ids.branch_problem(spelled)
            if why:
                problems.append(
                    Problem(SEVERITY_ERROR, name, f"`branch: {spelled}` は使えない。{why}")
                )
                return True
            ticket.branch = spelled
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
                    f"`{key}` はこの版の判定が使わない。書いても効かない。"
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
        # ワイルドカードがあれば、前置に 1 文字足したパスを親に当てる。`src/b/*` なら
        # `src/b/x`。無ければパスそのもの。前置の末尾の `/` を落として当てると、
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
    """子の範囲の regex を名指しする文。親の検査と定義の検査の両方が同じ文を使う。

    同じ文にしておけば、承認の画面で 2 度並べずにまとめられる。
    """
    return (
        f"子の範囲に regex `{regex}` は書けない。"
        "親と定義の上限に収まるかは、判定のときに当てて確かめる"
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
    プロジェクトのツリー（か、そこから切ったワークツリー）にある（設計 11.5、REQ-MLT-14）。
    ワークスペースの `wip/<名前>/proposals/` は読まない。
    同じ識別子が複数のツリーにあれば、本物とするツリーの側だけを残す。

    `approved` は、まとめる前の承認済みチケット（作業中・レビュー待ち・閉じた）の全部。
    渡せば、承認済みの識別子の提案は、承認済みチケットで決めた本物とするツリーの側だけを残す
    （`ticket_fold.dedupe`）。組むのは `approval.scan_proposals`（承認済みチケットはそちらが読む）。
    渡さないと、本物とするツリーの外に残った古い提案も残る。
    """
    found, problems = scan_all(root, tickets_rel, projects_dir)
    return ticket_fold.dedupe(found, approved), problems


def scan_all(
    root: str, tickets_rel: str, projects_dir: str = ""
) -> tuple[list[ticket_model.Ticket], list[Problem]]:
    """ワークスペースルートと全ワークツリーの提案を、重複をまとめずに集める。

    ボード（`--explain --json`）が「どのツリーにあるか」を見せるために使う。
    判定と承認は `scan` のまとめた側を読む。
    """
    found: list[ticket_model.Ticket] = []
    problems: list[Problem] = []
    ws = tree.main_tree(root)
    # 置き場がプロジェクトを決める（設計 11.5）。提案はどのツリーでも同じ相対の置き場に
    # あり、プロジェクト向けの提案はそのプロジェクトの git が持つ。承認をプロジェクトの
    # git で共有するので、提案も同じブランチに乗せる（設計 9.2、REQ-MLT-14）。
    # frontmatter の `project:` は照合に使うだけ。
    places = [
        (t, tickets_rel, t.project)
        for t in [ws, *tree.projects(projects_dir), *tree.worktrees(root, projects_dir)]
    ]
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
                # 書かない欄をダイジェスト（`agree_digest.approval_digest`）に入れることになる。
                # 承認済みチケットの `project` は置き場（ツリー）から決まる（`approval.scan_all`）。
                found.append(ticket)
    return found, problems


def _entries(front: dict, name: str) -> tuple[list[ticket_model.Entry], list[Problem]]:
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
            match = _text(raw.get("match"))
            tools = [w.strip() for w in match.split("|") if w.strip()]
            if not any(t in ticket_model.WRITE_TOOLS for t in tools):
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
            entries.append(
                ticket_model.Entry(decision=section, glob=glob, regex=regex, compiled=compiled)
            )
    return entries, problems


def _frontmatter(text: str) -> tuple[dict | None, str, list[Problem]]:
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
    for i in range(1, len(lines)):
        if lines[i].strip() == ticket_model.FENCE:
            head = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1 :])
            break
    else:
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
