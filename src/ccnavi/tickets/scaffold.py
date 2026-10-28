"""親チケットの提案の雛形を作る。

`ccnavi ticket new <識別子> --title <タイトル> --plan <型,型,...> --issue <値> --mr <番号>`。

提案を手で書くと frontmatter の形を間違えやすく、とくにフェーズ定義の `scope` を `allow` に
コピーし忘れて、`design` フェーズの子が `wip/design/*` に書けず詰まる。この雛形の生成は、選んだ
フェーズの `scope` を `phases.yml` から集め、重複を除いて `allow` に書く（判定側で `wip/` を
既定許可にはしない。承認していない範囲が黙って広がるため）。

## 決まり

- 対象は親だけ。子の識別子（`<親>-<NN>-<NN>`）は断る
- 出力先は、識別子と同名のワークツリーの `<提案の置き場>/todo/<識別子>.md`。cwd がそのワークツリーの
  中でなければ、切り方を案内して止まる
- `phases.yml` が無いか読めないときは、承認の検査を通る plan を作れない（設計 9.7）ので止まる
- plan の型は `work` の定義だけ（`feedback` の定義は `feedback:` に置く）
- plan の `after` は一直線（前のステップが各ステップに先行する。終端が 1 つになり、
承認の検査を通る）
- `scope: inherit` の型だけで `allow` が空になるときは止める（`allow` か `ask` が 1 件は要る）。
  止めるときは、今回の範囲を `--scope` で渡し直すよう案内する（`quick-work` のような継ぐ型を
  選んだ人が、範囲を持つ型を足す回り道へ迷わないように）
- `--scope <glob>`（繰り返せる）は今回の作業の範囲。型の `scope` を集めたものに足して
  `allow` に書く。ワークツリーのルートからの相対の glob だけ（絶対パスと `..` は断る）
- 既にある提案・承認済みチケットは上書きしない
- 書いたあと、`allow` の項が計画のどの定義の `scope` とも重ならないときは、「承認しても書けない」と
  警告する（子は書けない）。計画に `scope: inherit` の定義があれば上限が無いので言わない
- 書いたあとに `plan_phases.fill` で `phases:`（定義のコピー）を差し込む。無いと `--agree` が落ちる

## テンプレートから作る（`--template <名前> [--layer <名前>]`）

`--plan` の代わりに、溜めたテンプレート（`plan_templates`）の計画を使う（併用は断る）。引き方・
`after` の付け直し・子のフローの下書きの組み方は `template_use`。ここは書く側:

- ステップは `type`・`review`・`after` をそのままコピーする（`after` は提案の絶対番号。
  フィードバック計画は全体計画の続き）。`allow` は計画とフィードバック計画の型の `scope` を集める
- 書く前に、ステップの型が `phases.yml` に在ること・kind が置き場（plan は work、
  feedback は feedback）と合うことを確かめる。
  書いたあとに承認の検査（`agree_candidates.plan_problems`）を適用し、error なら消す
- 子のフローの下書きを `<提案の置き場>/flows/<親>-<NN>-01.yml` に書く。
  下書きに効力は無い。同じ子の識別子の提案・フロー・下書きが在れば、何も書かずに断る。書き損じたら戻す

ネットワークにも git にも触らない。
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import TextIO

from ..infra import fsio, pathsafe, settings, tree
from ..infra.modes import EXIT_ERROR, EXIT_OK
from ..policy.rules import SEVERITY_ERROR
from . import agree_candidates, flow, phasetypes, plan_phases, plan_templates, template_use
from .model import proposal_form, standing, ticket_ids, ticket_model
from .model import ticket as ticket_mod

# `allow` に書く項の `match`。判定に使われるのは Write / Edit / NotebookEdit を含む項だけ。
ALLOW_MATCH = "Write|Edit"


def parse_plan(text: str) -> list[str]:
    """`--plan design,acceptance,implement` を型の並びにする。空の要素は破棄する。"""
    return [part.strip() for part in (text or "").split(",") if part.strip()]


def collect_allow(
    types: dict[str, phasetypes.PhaseType], plan: list[str]
) -> tuple[list[str], list[str]]:
    """plan の順に各型の `scope` を集め、重複を除いた glob の並びと、`inherit` の型の並び。

    重複は大文字小文字を区別せずに数える（範囲の照合が区別しないため）。
    """
    seen: set[str] = set()
    globs: list[str] = []
    inherited: list[str] = []
    for name in plan:
        pt = types[name]
        if pt.scope is None:
            if name not in inherited:
                inherited.append(name)
            continue
        for glob in pt.scope_globs:
            key = glob.lower()
            if key not in seen:
                seen.add(key)
                globs.append(glob)
    return globs, inherited


def parse_scope(values: list[str] | None) -> tuple[list[str], str]:
    """`--scope` の値（繰り返し可。カンマ区切りも可）を glob の並びにする。不正なら理由を返す。"""
    globs: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        for part in value.split(","):
            glob = part.strip()
            if not glob:
                continue
            if any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in glob):
                return [], f"--scope {glob!r} に改行や制御文字は指定できない"
            if "<" in glob or ">" in glob:
                return [], (
                    f"--scope {glob!r} に `<` `>` は指定できない。"
                    "`<触るファイルの glob>` のような案内の仮の値は、実際のパスに置き換える"
                )
            norm = glob.replace("\\", "/")
            if norm.startswith("/") or re.match(r"[A-Za-z]:", norm) or ".." in norm.split("/"):
                return [], (
                    f"--scope {glob!r} はワークツリーのルートからの相対の glob で書く"
                    "（絶対パスと `..` は指定できない）"
                )
            if glob.lower() not in seen:
                seen.add(glob.lower())
                globs.append(glob)
    return globs, ""


def render(
    ident: str,
    title: str,
    plan: list[str],
    allow: list[str],
    issue: str | None,
    mr: int | None = None,
) -> str:
    """雛形の全文。`phases:` は書かない（書いたあとに `plan_phases.fill` が差し込む）。"""
    items = [
        plan_templates.Item(type=name, after=[number - 1] if number > 1 else [])
        for number, name in enumerate(plan, start=1)
    ]
    return render_items(ident, title, items, [], allow, issue, mr)


# 引用符なしで書いてよい型の名前（YAML の真偽・null の語は除く）。
_PLAIN = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_YAML_WORDS = frozenset({"y", "n", "yes", "no", "on", "off", "true", "false", "null"})


def _scalar(text: str) -> str:
    if _PLAIN.fullmatch(text) and text.lower() not in _YAML_WORDS:
        return text
    return _quote(text)


def _item_line(item: plan_templates.Item) -> str:
    """計画の 1 ステップ（`ticket.parse` が読める形。`PlanItem.as_raw` と同じ書き分け）。"""
    if not item.review and not item.after:
        return f"  - {_scalar(item.type)}"
    parts = [f"type: {_scalar(item.type)}"]
    if item.review:
        parts.append(f"review: {_scalar(item.review)}")
    if item.after:
        parts.append(f"after: [{', '.join(str(n) for n in item.after)}]")
    return "  - {" + ", ".join(parts) + "}"


def render_items(
    ident: str,
    title: str,
    plan: list[plan_templates.Item],
    feedback: list[plan_templates.Item],
    allow: list[str],
    issue: str | None,
    mr: int | None = None,
) -> str:
    """雛形の全文。`issue` は `12` か `owner/repo#12`。`mr` は親のブランチのマージリクエストの番号
    （どちらも親の作成と承認の前提）。
    `after` は提案の絶対番号（フィードバック計画は全体計画の続き）で渡す。"""
    lines = [ticket_model.FENCE, f"version: {ticket_model.VERSION}", f"ticket: {ident}"]
    if issue is not None:
        lines.append(f"issue: {issue}")
    if mr is not None:
        lines.append(f"mr: {mr}")
    lines.append(f"title: {_quote(title)}")
    lines.append("plan:")
    lines.extend(_item_line(item) for item in plan)
    if feedback:
        lines.append("feedback:")
        lines.extend(_item_line(item) for item in feedback)
    lines.append("rationale: |")
    lines.append(f"  {proposal_form.RATIONALE_TODO}")
    lines.append("allow:")
    for glob in allow:
        lines.append(f"  - match: {ALLOW_MATCH}")
        lines.append(f"    glob: {_quote(glob)}")
    lines.append(ticket_model.FENCE)
    for heading in proposal_form.PARENT_HEADINGS:
        lines += [f"## {heading}", "", proposal_form.HEADING_TODO[heading], ""]
    return "\n".join(lines)


def _quote(text: str) -> str:
    """YAML の二重引用符の文字列にする（JSON の文字列は YAML として読める）。"""
    return json.dumps(text, ensure_ascii=False)


def run(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    cwd: str,
    ident: str,
    title: str,
    plan_text: str,
    issue: str | None,
    template: str = "",
    layer: str = "",
    with_feedback: bool = False,
    scope: list[str] | None = None,
    mr: int | None = None,
) -> int:
    """`ticket new` の本体。書けたら 0、書かなければ 1（理由は標準エラー）。

    `template` を渡すと、`--plan`（型の列）の代わりにテンプレートから計画と子のフローの下書きを作る
    （`layer` はテンプレートを統合結果で引くレイヤー。省けば cwd のツリーのレイヤー）。
    フィードバック計画は `with_feedback` のときだけ書く。
    `scope` は今回の作業の範囲（glob）。型の `scope` を集めたものに足して `allow` に書く。

    処理フロー:
    1. 識別子・タイトル・issue・MR を検査する（`ticket_ids.id_problem`・`ticket_mod.issue_text`）。
       `template`・`plan`・`layer`・`with_feedback` の組み合わせも見る。
    2. cwd が識別子と同名の親のワークツリーか（`tree.tree_of`）、その HEAD
       のブランチが識別子か（`standing.head_branch`）を見る。
    3. フェーズ定義を `phasetypes.load_types` で読む。テンプレートなら `_from_template`
       で計画と子の下書きを作る。
       `plan` のときは、型が work の定義かを見る。
    4. 範囲を `collect_allow` で型の `scope` から集め、`scope` 引数を足す。空や上限超過は書かない。
    5. 同じ識別子の提案か承認済みチケットが在れば書かない（`_existing`）。テンプレートなら
       `_flow_clash` で子の流れの衝突を見る。
    6. 雛形を `render`（テンプレートなら `render_items`）で作り、`fsio.write_new_durable`
       で新規に書く。既存は上書きしない。
    7. `plan_phases.fill` でコピー（phases:）を差し込み、`phasetypes.parent_scope_problems`
       で範囲が定義の上限を超えていないかを見る。
       超えていれば案内として出す（止めない）。テンプレートなら `_agree_problem` の検査を通し、
       子の流れを `_write_flows` で書く。
       どこかで失敗したら、書いたファイルを消して戻す。
    8. 次の手順を案内して終わる。
    """

    def refuse(why: str) -> int:
        stderr.write(f"ccnavi: ticket new: {why}。何も反映しません。\n")
        return EXIT_ERROR

    problem = ticket_ids.id_problem(ident)
    if problem:
        return refuse(f"識別子 {ident!r} が指定できない（{problem}）")
    if ticket_ids.child_pattern().fullmatch(ident):
        return refuse(
            f"{ident} は子の識別子の形（<親>-<NN>-<NN>）。雛形を出すのは親だけ。"
            "子の提案は手で書く（書式は ccnavi --docs --keyword 提案書式）"
        )
    if not (title or "").strip():
        return refuse("--title <タイトル> が必要です")
    if any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in title):
        return refuse("--title に改行や制御文字は指定できない")
    stripped = title.strip()
    if stripped.startswith("<") and stripped.endswith(">"):
        return refuse(
            f"--title {stripped!r} は案内の仮の値。実際のタイトル（issue や MR のタイトル）に置き換える"
        )
    # チケット作業は issue と MR のセット。どちらかが無い親の提案は作らない
    if issue is None:
        return refuse(
            "--issue <番号> が必要です（別のリポジトリなら owner/repo#番号）。"
            "ccnavi-start.sh が「必須」の行で出す"
        )
    if mr is None:
        return refuse("--mr <番号> が必要です。ccnavi-start.sh が「必須」の行で出す")
    issue = ticket_mod.issue_text(issue)
    if not issue:
        return refuse("--issue には正の整数か owner/repo#番号 を渡す")
    if mr <= 0:
        return refuse("--mr には正の整数を渡す")
    plan = parse_plan(plan_text)
    if template:
        if (plan_text or "").strip():
            return refuse("--template と --plan は同時に指定できない（計画はテンプレートから作る）")
    elif layer:
        return refuse("--layer は --template と一緒に使う")
    elif with_feedback:
        return refuse("--with-feedback は --template と一緒に使う")
    elif not plan:
        return refuse('--plan "design,acceptance,implement" のように型をカンマで区切って渡す')

    where = tree.tree_of(root, cwd, conf.projects)
    if (
        where is None
        or where.kind != tree.KIND_WORKTREE
        or unicodedata.normalize("NFC", where.name) != ident
    ):
        start_sh = settings.script_command(root, "ccnavi-start.sh")
        return refuse(
            f"cwd が .claude/worktrees/{ident} の中ではない。"
            "提案は親のワークツリー（識別子と同名）に書く。"
            f"先に '{start_sh} --mr <MR の番号>'（MR が無ければ '--issue <issue の番号>'）で、"
            "親のブランチを直接チェックアウトした親のワークツリーを作り、その中から実行します"
        )
    if not standing.head_branch(where.root, ident):
        return refuse(
            f"親のワークツリー（.claude/worktrees/{ident}）の HEAD のブランチ名の `/` を `-` に"
            f"置き換えたものが {ident} になっていない。親のブランチ名は MR のブランチ"
            "（`ccnavi-start.sh` が出す）で、ワークツリーはそのブランチを直接チェックアウトする"
        )
    types = phasetypes.load_types(conf, root, where.project)
    if types is None:
        return refuse(
            f"フェーズ定義（{where.project or '自身'} のレイヤーの phases.yml）が無いか読めない。"
            "plan の型の定義を読めず、承認の検査を通る plan とコピー（phases:）を作れない"
            "（設計 9.7）。phases.yml を用意してから実行します。"
        )
    used: _Used | None = None
    if template:
        used, why = _from_template(conf, root, where, types, ident, template, layer, with_feedback)
        if used is None:
            return refuse(why)
        scoped = [item.type for item in used.plan + used.feedback]
    else:
        unknown = [n for n in dict.fromkeys(plan) if n not in types]
        if unknown:
            return refuse(
                f"plan の型 {', '.join(unknown)} が phases.yml にありません。指定できる型: "
                + ", ".join(n for n, t in types.items() if t.kind == phasetypes.KIND_WORK)
            )
        feedback = [n for n in dict.fromkeys(plan) if types[n].kind != phasetypes.KIND_WORK]
        if feedback:
            return refuse(
                f"plan の型 {', '.join(feedback)} は feedback の定義。"
                "plan には work の定義だけを置く"
            )
        scoped = plan

    allow, inherited = collect_allow(types, scoped)
    extra, why = parse_scope(scope)
    if why:
        return refuse(why)
    seen = {glob.lower() for glob in allow}
    allow += [glob for glob in extra if glob.lower() not in seen]
    if not allow:
        return refuse(
            f"plan の型（{', '.join(inherited)}）は scope: inherit のため、範囲を親の allow から"
            "継承します。今回の範囲が未指定で、allow が集まりません（allow か ask が 1 件は"
            "必要です）。ユーザに今回編集するファイルを確認し、--scope <glob> で"
            "指定して再実行してください"
            '（例: --plan quick-work --scope "src/ui/error.ts"）。'
            "範囲を持つ型を追加する必要はありません"
        )
    if len(allow) > ticket_mod.MAX_SCOPE_ENTRIES:
        return refuse(
            f"集めた範囲が {len(allow)} 件で、上限の {ticket_mod.MAX_SCOPE_ENTRIES} 件を超える。"
            "plan の型を減らす"
        )

    taken = _existing(conf, where.root, ident)
    if taken:
        return refuse(f"{ident} の提案か承認済みチケットが既に在る（{taken}）。上書きしない")
    if used is not None:
        clash = _flow_clash(conf, where.root, used)
        if clash:
            return refuse(clash)

    path = os.path.join(
        where.root,
        (conf.tickets or settings.DEFAULT_TICKETS).replace("/", os.sep),
        ticket_model.TODO,
        ident + ".md",
    )
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError as exc:
        return refuse(f"{os.path.dirname(path)} を作れない（{exc}）")
    if used is None:
        text = render(ident, title.strip(), plan, allow, issue, mr)
    else:
        text = render_items(ident, title.strip(), used.plan, used.feedback, allow, issue, mr)
    failed = fsio.write_new_durable(path, text.encode("utf-8"))
    if failed:
        return refuse(f"{path} に保存できませんでした（{failed}）")

    # コピー（phases:）を差し込む。無いと --agree が「ステップの定義がコピーに無い」で落ちる。
    target, _ = ticket_mod.load(path)
    done = plan_phases.Filled(why=f"{path} を親の提案として読めない") if target is None else None
    if target is not None:
        target.project = where.project
        done = plan_phases.fill(conf, root, target, None)
    if done is not None and done.why:
        fsio.remove(path)
        return refuse(f"phases: を差し込めなかった（{done.why}）")

    # コピーを差し込んだあとの提案を読み直して、allow が定義の上限を超えていないかを見る
    filled, _ = ticket_mod.load(path)
    over = phasetypes.parent_scope_problems(filled) if filled is not None else []
    written: list[str] = []
    if used is not None:
        # 承認の検査（定義の kind と置き場、順序）を、書いた提案に適用する。通らなければ書き戻す。
        problem = _agree_problem(conf, root, path, where.project)
        if problem:
            fsio.remove(path)
            return refuse(f"テンプレートから作った提案が承認の検査に合格しない（{problem}）")
        failed = _write_flows(conf, where.root, used, written)
        if failed:
            for rolled in written:
                fsio.remove(rolled)
            fsio.remove(path)
            return refuse(failed)

    if used is None:
        stdout.write(f"{ident} の雛形を {path} に書いた\n")
        stdout.write(f"plan: {' -> '.join(plan)}（前のステップが各ステップに先行する一直線）\n")
    else:
        _report(stdout, ident, path, used, written)
    stdout.write("allow（phases.yml の scope を集めた）:\n")
    for glob in allow:
        stdout.write(f"  {ALLOW_MATCH} {glob}\n")
    for p in over:
        stdout.write(f"案内: 承認しても書けない: {p.detail}\n")
    tail = (
        "並行させたいステップは after を直す。"
        if used is None
        else "計画のステップと文の固有名を直す。"
    )
    # ccnavi を PATH に持たない環境でも、そのまま打てるように、起動の sh 経由で書く
    launcher = settings.script_command(root, "ccnavi-launcher.sh")
    stdout.write(
        f"次: {path} の rationale と、見出しの下の目印（「（ここに〜を書く）」）を実際の内容に"
        "書き換える。目印が残っていると承認で止まる。"
        f"allow を実際の作業に合わせて絞る・足す。{tail}"
        f"子の提案（'{ident}-<フェーズ番号 2 桁>-<連番 2 桁>'。例: '{ident}-01-01'）も"
        "同じ承認で通すため、先に手で書く。"
        f"書けたら '{launcher} --lint' で確かめ、"
        f"'{launcher} --agree --preview --verify {ident}' が通ったら、"
        f"'{settings.script_command(root, 'ccnavi-ticket.sh')} propose {ident}'"
        "（親の識別子で打つ。子の識別子では打たない）で"
        "提案を親のブランチへ送り、"
        "承認をユーザに頼んで返事を待つ。承認が済むまで実装はしない\n"
    )
    stdout.write(
        f"案内: plan を変えたら '{launcher} --plan-order {ident} --fill-phases' "
        "で phases: を再実行します。"
        "承認が通らないなど詰まったら、直接実装に切り替えず、ユーザに報告して止まる\n"
    )
    return EXIT_OK


@dataclass
class _Used:
    """テンプレートから組んだ、書く中身。"""

    found: template_use.Found
    plan: list[plan_templates.Item]
    feedback: list[plan_templates.Item]
    # (子の識別子, 全体計画のステップの添字, 文面)。
    drafts: list[tuple[str, int, str]]
    # テンプレートにあるが書かなかったフィードバック計画のステップ数（`--with-feedback`
    # なしのとき）。
    skipped_feedback: int = 0


def _from_template(
    conf: settings.Settings,
    root: str,
    where: tree.Tree,
    types: dict[str, phasetypes.PhaseType],
    ident: str,
    name: str,
    layer: str,
    with_feedback: bool = False,
) -> tuple[_Used | None, str]:
    """テンプレートを引いて、書く中身を組む。組めなければ (None, 理由)。何も書かない。

    処理フロー:
    1. `layer` が空なら、cwd のツリーのプロジェクト（無ければ自身のレイヤー）を使う。
    2. `template_use.resolve` でテンプレートを引く。無ければ理由を返して止める。
    3. `template_use.absolute` で全体計画とフィードバック計画のステップを組む。フィードバックは
       `with_feedback` が無ければ破棄し、そのステップ数を `skipped` に残す。
    4. ステップの型（計画とフィードバックの両方）が `types` に全部在るかを見る。
    5. 計画は work、フィードバックは feedback の kind の型だけかを見る。
    6. `template_use.child_problem` と `template_use.flow_drafts` で、
       子の提案と子のフローの下書きを組む。
    7. 組めた中身を `_Used` にして返す。ここではまだ何も書かない。
    """
    layer = layer or where.project or plan_templates.LAYER_SELF
    found, why = template_use.resolve(conf, root, layer, name)
    if found is None:
        return None, why
    template = found.template
    plan, feedback = template_use.absolute(template)
    skipped = 0
    if not with_feedback:
        # フィードバック計画は全体計画の最後のレビューを見て、改版で 1 回だけ足す決まり。
        skipped, feedback = len(feedback), []
    missing = [n for n in dict.fromkeys(i.type for i in plan + feedback) if n not in types]
    if missing:
        return None, (
            f"テンプレート {name!r} のステップの定義 {', '.join(missing)} が "
            "phases.yml にありません"
            f"（{where.project or '自身'} のレイヤー）。テンプレートを直すか、"
            "ユーザに phases.yml へ追加してもらう"
        )
    for key, items, kind in (
        ("plan", plan, phasetypes.KIND_WORK),
        ("feedback", feedback, phasetypes.KIND_FEEDBACK),
    ):
        wrong = [i.type for i in items if types[i.type].kind != kind]
        if wrong:
            return None, (
                f"テンプレート {name!r} の {key} の型 {', '.join(dict.fromkeys(wrong))} は"
                f" kind が {kind} でない。{key} には {kind} の定義だけを置く"
            )
    why = template_use.child_problem(ident, template)
    if why:
        return None, why
    drafts, why = template_use.flow_drafts(ident, template)
    if why:
        return None, why
    return _Used(found, plan, feedback, drafts, skipped), ""


def _flow_clash(conf: settings.Settings, top: str, used: _Used) -> str:
    """子のフローの下書きを書けない理由（在るものは上書きしない）。書けるなら空。"""
    if not used.drafts:
        return ""
    directory = os.path.dirname(flow.draft_file(conf, top, used.drafts[0][0]))
    if pathsafe.has_link(top, directory):
        place = f"{flow.tickets_rel(conf)}/{flow.FLOWS_DIR}"
        return f"フロー下書きの置き場（{place}/）の途中にリンクがあるので反映しません"
    for child, _, _ in used.drafts:
        draft = flow.draft_file(conf, top, child)
        if os.path.lexists(draft):
            return f"フロー下書き {flow.draft_rel(conf, child)} が既に在る。上書きしない"
        if os.path.lexists(flow.flow_file(conf, top, child)):
            return f"子 {child} のフロー（{flow.flow_rel(conf, child)}）が既に在る。上書きしない"
        taken = _existing(conf, top, child)
        if taken:
            return f"{child} の提案か承認済みチケットが既に在る（{taken}）。上書きしない"
    return ""


def _write_flows(conf: settings.Settings, top: str, used: _Used, written: list[str]) -> str:
    """子のフローの下書きを書く。書いたパスを `written` に足す。書けなければ理由。"""
    for child, _, text in used.drafts:
        place = flow.draft_file(conf, top, child)
        try:
            os.makedirs(os.path.dirname(place), exist_ok=True)
        except OSError as exc:
            return f"{os.path.dirname(place)} を作れない（{exc}）"
        failed = fsio.write_new_durable(place, text.encode("utf-8"))
        if failed:
            return f"フロー下書き {flow.draft_rel(conf, child)} に保存できませんでした（{failed}）"
        written.append(place)
    return ""


def _agree_problem(conf: settings.Settings, root: str, path: str, project: str) -> str:
    """書いた提案に承認の検査（`agree_candidates.plan_problems`）を適用した error。無ければ空。"""
    target, _ = ticket_mod.load(path)
    if target is None:
        return "親の提案として読めない"
    target.project = project
    config = phasetypes.load_types(conf, root, project)
    found = agree_candidates.plan_problems(target, config)
    return " / ".join(p.detail for p in found if p.severity == SEVERITY_ERROR)


def _plan_text(items: list[plan_templates.Item], first: int) -> str:
    parts = []
    for number, item in enumerate(items, start=first):
        extra = []
        if item.after:
            extra.append("先行 " + ",".join(str(n) for n in item.after))
        if item.review:
            extra.append(item.review)
        parts.append(f"{number} {item.type}" + (f"（{'・'.join(extra)}）" if extra else ""))
    return " / ".join(parts)


def _report(stdout: TextIO, ident: str, path: str, used: _Used, written: list[str]) -> None:
    """テンプレートから作ったときの出力。何を書いたか、フローの下書きの効力、次の手。"""
    found = used.found
    stdout.write(
        f"{ident} の提案を {path} に書いた"
        f"（テンプレート {found.template.name}、{found.entry.layer} の {found.entry.rel}）\n"
    )
    stdout.write(f"plan（{len(used.plan)} ステップ）: {_plan_text(used.plan, 1)}\n")
    if used.feedback:
        text = _plan_text(used.feedback, len(used.plan) + 1)
        stdout.write(f"feedback（{len(used.feedback)} ステップ）: {text}\n")
    elif used.skipped_feedback:
        stdout.write(
            f"テンプレートにはフィードバック計画（{used.skipped_feedback} ステップ）がある。"
            "全体計画のレビューを見てから追加するなら、`--with-feedback` を付けて作り直すか、"
            "提案を改版で追加します。\n"
        )
    for note in found.notes:
        stdout.write(f"note: {note}\n")
    for warn in found.warns:
        stdout.write(f"warn: {warn}\n")
    if written:
        stdout.write(f"フローの下書き {len(written)} 件:\n")
        for (child, index, _), place in zip(used.drafts, written, strict=True):
            stdout.write(f"  計画の {index} 番目 -> {child}: {place}\n")
        stdout.write(
            "下書きには効力がありません。フローの文は子への案内文になるので、ユーザがフロー編集画面で"
            "読んで取り込むまで効かない。子の識別子は各フェーズの最初の子"
            f"（{ident}-<NN>-01）と仮定した。別の連番で子を起こすなら下書きの名前を合わせる\n"
        )
    else:
        stdout.write("フローの下書きはありません（テンプレートに flows がありません）\n")


def _existing(conf: settings.Settings, top: str, ident: str) -> str:
    """同じ識別子の提案か承認済みチケットがこのツリーに在れば、そのパス。無ければ空。"""
    tickets = os.path.join(top, (conf.tickets or settings.DEFAULT_TICKETS).replace("/", os.sep))
    approved = settings.approved_dir(conf, top)
    for base, states in (
        (tickets, (ticket_model.TODO, ticket_model.REVIEW)),
        (approved, (ticket_model.DOING, ticket_model.DONE)),
    ):
        for state in states:
            candidate = os.path.join(base, state, ident + ".md")
            if os.path.lexists(candidate):
                return candidate
    return ""
