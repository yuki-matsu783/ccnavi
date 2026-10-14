"""作業・レビュー計画のテンプレートを溜める側。下書きを作り、ユーザが取り込む。

読む側（`plan_templates`）が持つのは効力のある置き場（共通とレイヤー）だけ。ここは効力の無い
下書きを扱う。

## 下書きを作る（`make`）

完了した親の `done/` の計画（`plan:` / `feedback:` の項の `type`・`review`・`after`）と、各子の
フロー（`.ccnavi/approved/flows/<子>.yml`）を 1 ファイルのテンプレートに写し、下書きの置き場
（`plan_templates.draft_file`）に書く。エージェントが打ってよい（効力が無い）。

- `after` は各計画の中の添字（1 始まり）にする。親の計画の `after` は全体計画の続きの絶対番号で、
  フィードバック計画の項は全体計画の項数を引いて直す。全体計画の `after` はそのまま。承認済みの
  親の計画の順序は承認が確かめているので、並べ替え（`plan_order.renumber`）は要らず、引き算だけで足りる
- 子のフローは、全体計画の n 番目の項の子（`phase` が n）のうち、フローを持つ最初の 1 本を写す。
  フィードバック計画の子のフローは写さない（テンプレートの `flows` は全体計画の添字で指す）
- 固有名の一般化は機械ではしない。親の題から入れる `description` と `when` の両方に `TODO` の印を
  付ける。直すのはエージェント。印が残っていれば検査が warn で言う
- 同名の下書きがあれば上書きせず断る。親が `ready` で閉じて退避済み（`logs/archive/`）なら、
  そこからは読まないと言って断る

## 取り込む（`prepare` / `commit`）

ユーザだけが打つ。`prepare` は何も書かず、下書きを検査して取り込み先との差分を作る。`commit` は
`--yes` と、見せた下書きのハッシュ（`--expect`）が揃ったときだけ書く。

- 下書きの中身は検査に通した読み取りのバイト列をそのまま書く（書き換えない）。書く前に
  `--expect` と、検査したバイト列のハッシュが同じことを確かめる
- 同名が取り込み先に在れば `--replace` が要る。共通とレイヤーに同名で中身が違うものが並ぶと、
  統合結果がレイヤーの側を使わない error にするので、そうなる取り込みは断る
- 書くのは一時ファイル → `fsync` → 差し替え（`fsio.write_bytes_atomic` / `write_new_durable`）。
  リンク・ハードリンク・ふつうのファイルでないものには書かない
- 取り込みが済んだら、下書きの今の中身が取り込んだときの中身（ハッシュ）と同じときだけ下書きを
  消す。違えば消さずにそう言う。リンクなどは消さない
"""

from __future__ import annotations

import difflib
import os
from dataclasses import dataclass, field

from ..infra import fsio, settings, tree
from ..policy import ruleload
from ..policy.rules import SEVERITY_ERROR, SEVERITY_WARN, Problem
from . import approval, archive, flow, plan_templates, ticket_ids, ticket_model

TO_COMMON = "common"
TO_PROJECT = "project"
TARGETS = (TO_COMMON, TO_PROJECT)

# ワークスペースのルートのツリー（名前が空）を人に見せるときの言い方。
ROOT_LABEL = "ワークスペースルート"

# 差分に添える前後の行数。
DIFF_CONTEXT = 3

# 下書きの題の前に付ける印（`plan_templates.TODO_MARK` を含む）。
_TODO_PREFIX = f"{plan_templates.TODO_MARK} 一般化する: "


def _line(text: object) -> str:
    return " ".join(str(text).split())


def tree_label(name: str) -> str:
    """ツリー名の人間向けの表記。ワークスペースのルート（""）は言葉にする。"""
    return name or ROOT_LABEL


# ---- 下書きを読む


@dataclass
class Draft:
    """下書きの置き場の `<名前>.yml` 1 本。

    `also` は同じ中身の下書きを持つほかのツリー（`tree.name` と同じ表記。ルートは ""）。
    """

    tree: tree.Tree
    # 検査に使うフェーズ定義のレイヤー（自身のレイヤーかプロジェクト名）。
    layer: str
    entry: plan_templates.Entry
    also: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.entry.name

    @property
    def path(self) -> str:
        return self.entry.path

    @property
    def rel(self) -> str:
        return self.entry.rel

    @property
    def sha(self) -> str:
        return self.entry.sha

    @property
    def importable(self) -> bool:
        """読めて、error が無い。取り込みの前提（取り込み先の定義での検査は別）。"""
        return self.entry.template is not None and not any(
            p.severity == SEVERITY_ERROR for p in self.entry.problems
        )


@dataclass
class DraftScan:
    drafts: list[Draft] = field(default_factory=list)
    # 置き場そのものの苦情（読めない、`.yml` でないファイル）。(置き場の相対パス, 苦情)
    places: list[tuple[str, Problem]] = field(default_factory=list)


def scan(conf: settings.Settings, root: str, candidates: dict | None = None) -> DraftScan:
    """全ツリー（ワークスペース・プロジェクト・ワークツリー）の下書きを読んで検査する。

    検査はそのツリーのレイヤーのフェーズ定義（共通 + レイヤー）で行う。同じ中身の下書きが
    複数のツリーに在れば（親のブランチに乗ったものが子のワークツリーにも checkout される）、
    最初の 1 本にまとめる。効力のある置き場（`plan_templates.scan`）は読まない。
    """
    known = [view.name for view in ruleload.layers(conf, root)]
    if candidates is None:
        candidates = flow.catalog(root)
    out = DraftScan()
    seen: dict[tuple[str, str], Draft] = {}
    said: set[str] = set()
    for where in approval.trees(conf, root):
        directory = plan_templates.draft_dir(conf, where.root)
        read = plan_templates.read_layer(plan_templates.LAYER_DRAFT, where.root, directory)
        for problem in read.problems:
            key = f"{problem.severity}:{problem.detail}"
            if key not in said:
                said.add(key)
                out.places.append((read.rel, problem))
        if not read.entries:
            continue
        layer = where.project if where.project in known else plan_templates.LAYER_SELF
        types = plan_templates.types_for(conf, root, layer)
        for entry in read.entries:
            if entry.template is not None:
                entry.checks.extend(plan_templates.check(entry.template, types, candidates))
            key2 = (entry.name, entry.sha)
            if entry.sha and key2 in seen:
                seen[key2].also.append(where.name)
                continue
            draft = Draft(where, layer, entry)
            if entry.sha:
                seen[key2] = draft
            out.drafts.append(draft)
    out.drafts.sort(key=lambda d: (d.name, d.rel, d.tree.name))
    return out


def _item_names(scanned: plan_templates.Scan, name: str) -> list[str]:
    """同じ名前のテンプレートが在るレイヤー（共通を先に）。"""
    found = []
    for view in [scanned.common, *scanned.layers]:
        if any(e.name == name for e in view.entries):
            found.append(view.name)
    return found


def draft_json(draft: Draft, scanned: plan_templates.Scan) -> dict:
    """画面（将来のボードのテンプレート管理画面）が取り込みを出せる 1 本分の答え。

    `text` は読んだ下書きの本文そのまま（編集画面が元の本文を取るため。reveal はしない）。
    `importable` は読めて error が無いこと。`exists_in` は同じ名前のテンプレートが在るレイヤー
    （取り込むと新規ではなく置き換えになる）。`problems` の深刻度は下書きの検査のままで、
    `--lint` は error も warn に下げて出す。
    """
    row = plan_templates.entry_json(draft.entry)
    keep = (
        "name",
        "path",
        "sha",
        "valid",
        "description",
        "when",
        "plan",
        "feedback",
        "has_flows",
        "flows",
        "problems",
    )
    out = {key: row[key] for key in keep}
    out["tree"] = draft.tree.name
    # 検査に使ったフェーズ定義の層（`templates[].layer` の「ファイルが在る層」とは別）。
    out["checked_with"] = draft.layer
    out["importable"] = draft.importable
    out["exists_in"] = _item_names(scanned, draft.name)
    out["also_in"] = list(draft.also)
    out["text"] = draft.entry.raw.decode("utf-8-sig", errors="replace")
    return out


def lint_problems(found: DraftScan) -> list[Problem]:
    """`--lint` に出す苦情。下書きに効力は無いので、error も warn に下げる（info と warn だけ）。"""
    out: list[Problem] = []
    for rel, problem in found.places:
        severity = SEVERITY_WARN if problem.severity == SEVERITY_ERROR else problem.severity
        out.append(Problem(severity, "", f"下書き {problem.detail or rel}"))
    for draft in found.drafts:
        for problem in draft.entry.problems:
            severity = SEVERITY_WARN if problem.severity == SEVERITY_ERROR else problem.severity
            out.append(Problem(severity, "", f"下書き {draft.rel}: {problem.detail}"))
    return out


# ---- 下書きを作る


@dataclass
class Made:
    ok: bool
    error: str = ""
    name: str = ""
    path: str = ""
    rel: str = ""
    sha: str = ""
    tree: str = ""
    template: plan_templates.Template | None = None
    # 知らせ（写せなかったフロー、落とした先行など）。
    notes: list[str] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)


def _find_parent(
    conf: settings.Settings, root: str, parent: str
) -> tuple[ticket_model.Ticket | None, list[ticket_model.Ticket], str]:
    """完了した親（`done/`）とその子たち。見つからなければ理由。"""
    refuse: tuple[None, list[ticket_model.Ticket]] = (None, [])
    if not ticket_ids.is_valid_id(parent):
        return *refuse, f"{parent!r} は親の識別子として読めない"
    closed, _ = approval.scan(conf, root, closed=True)
    hits = [t for t in closed if t.ticket == parent and not t.is_child]
    if not hits:
        opened, _ = approval.scan(conf, root)
        if any(t.ticket == parent for t in opened):
            why = f"{parent} はまだ作業中（doing/）。完了した（done/ に在る）親だけから作る"
        elif archive.find(root, parent):
            why = (
                f"{parent} は ready で閉じて退避済み（{archive.ARCHIVE_DIR}/）で、退避した親は"
                "読まない。ready の前に（done/ に在るうちに）下書きを作る"
            )
        else:
            why = f"{parent} の完了した計画（done/）が見つからない"
        return *refuse, why
    if len({t.tree_root for t in hits}) > 1:
        places = ", ".join(sorted(tree_label(t.tree or "") for t in hits))
        return *refuse, f"{parent} が複数のツリーにある（{places}）。決められない"
    found = hits[0]
    if found.state == ticket_model.CANCELLED:
        return *refuse, f"{parent} は取り消した親。完了した親だけから下書きを作る"
    if not found.has_plan:
        return *refuse, f"{parent} は計画（plan:）を持たない"
    children = [t for t in closed if t.parent == parent and t.phase is not None]
    return found, children, ""


def _relative_items(
    items: list[ticket_model.PlanItem], offset: int, key: str, notes: list[str]
) -> list[plan_templates.Item]:
    """親の計画の項を、その計画の中の添字の `after` に直す。`offset` はその計画の最初の番号 - 1。"""
    out = []
    for i, item in enumerate(items, start=1):
        after = []
        for value in item.after:
            local = value - offset
            if 1 <= local < i:
                after.append(local)
            else:
                notes.append(f"{key}[{i}] の after {value} は{key}の中の項を指さないので落とした")
        out.append(
            plan_templates.Item(type=item.type, review=item.review, after=sorted(set(after)))
        )
    return out


def _flows(
    conf: settings.Settings,
    parent: ticket_model.Ticket,
    children: list[ticket_model.Ticket],
    notes: list[str],
) -> dict[int, dict]:
    """全体計画の n 番目の項 → その子のフローの本文。フローを持つ最初の子の 1 本を写す。"""
    out: dict[int, dict] = {}
    for n in range(1, len(parent.plan) + 1):
        mine = sorted(
            (c for c in children if c.phase == n and c.state == ticket_model.DONE),
            key=lambda c: c.ticket,
        )
        taken = ""
        for child in mine:
            path = flow.flow_file(conf, parent.tree_root, child.ticket)
            if not os.path.lexists(path):
                continue
            raw, why = flow.read_bytes(path, parent.tree_root)
            data = None
            if raw is not None:
                data, why = flow.parse(raw)
            if data is None:
                notes.append(f"{n} 番目の子 {child.ticket} のフローは読めないので写さない（{why}）")
                continue
            if taken:
                notes.append(
                    f"{n} 番目は {taken} のフローを写した（{child.ticket} にもフローがある）"
                )
                continue
            out[n] = data
            taken = child.ticket
    return out


def _build(
    conf: settings.Settings,
    found: ticket_model.Ticket,
    children: list[ticket_model.Ticket],
    name: str,
) -> tuple[plan_templates.Template, list[str]]:
    """完了した親の計画とフローを、下書きの中身（まだ一般化していない）に写す。何も書かない。"""
    notes: list[str] = []
    planned = len(found.plan)
    plan = _relative_items(list(found.plan), 0, "plan", notes)
    feedback = _relative_items(list(found.feedback or []), planned, "feedback", notes)
    title = _line(found.title) or found.ticket
    template = plan_templates.Template(
        name=name,
        description=f"{_TODO_PREFIX}{title}",
        when=f"{_TODO_PREFIX}「{title}」と同じ形の作業をするとき",
        plan=plan,
        feedback=feedback,
        flows=_flows(conf, found, children, notes),
    )
    if found.feedback:
        flowless = [c for c in children if c.phase and c.phase > planned]
        if any(os.path.lexists(flow.flow_file(conf, found.tree_root, c.ticket)) for c in flowless):
            notes.append("フィードバック計画の子のフローは写さない（全体計画の子のフローだけ）")
    return template, notes


def make(conf: settings.Settings, root: str, parent: str, name: str = "") -> Made:
    """完了した親から下書きを作って、下書きの置き場に書く。上書きはしない。"""
    found, children, why = _find_parent(conf, root, parent)
    if found is None:
        return Made(False, error=why)
    name = (name or parent).strip()
    if not plan_templates.NAME_RE.match(name) or len(name) > plan_templates.NAME_LIMIT:
        return Made(
            False,
            error=f"下書きの名前 {_line(name)!r} は使えない（英数字で始め、英数字と `._-` だけ。"
            f"{plan_templates.NAME_LIMIT} 字まで）。--name で付ける",
        )
    template, notes = _build(conf, found, children, name)
    place = plan_templates.draft_file(conf, found.tree_root, name)
    directory = os.path.dirname(place)
    if flow.linked(found.tree_root, directory):
        return Made(
            False,
            error=f"下書きの置き場（{plan_templates.draft_dir_rel(conf)}/）の途中にリンクがあるので反映しません",
        )
    if os.path.lexists(place):
        rel = plan_templates.draft_rel(conf, name)
        return Made(
            False,
            error=(
                f"下書き {rel} が既に在る。上書きしない（別の --name を付けるか、先に削除します）"
            ),
        )
    err = fsio.write_new_durable(place, plan_templates.dump(template).encode("utf-8"))
    if err:
        return Made(False, error=f"下書きを保存できませんでした（{_line(err)}）")
    entry = plan_templates.read_entry(
        plan_templates.LAYER_DRAFT, found.tree_root, place, writable=True
    )
    layer = found.project if found.project in _known(conf, root) else plan_templates.LAYER_SELF
    problems = list(entry.notes)
    if entry.template is not None:
        types = plan_templates.types_for(conf, root, layer)
        problems += plan_templates.check(entry.template, types, flow.catalog(root))
    return Made(
        True,
        name=name,
        path=place,
        rel=plan_templates.draft_rel(conf, name),
        sha=entry.sha,
        tree=found.tree or "",
        template=entry.template or template,
        notes=notes,
        problems=problems,
    )


def _known(conf: settings.Settings, root: str) -> list[str]:
    return [view.name for view in ruleload.layers(conf, root)]


# ---- 親が閉じるときの案内


def _shape(template: plan_templates.Template) -> tuple[list, list]:
    """計画の形（全体計画とフィードバック計画の項）。取り込み済みかを見るときの比べる対象。

    フローの本文は比べない。下書きの一般化でフローの文も直るので、文まで比べると、
    取り込んだ直後でも「同じ内容」に読めない。
    """
    return [i.as_data() for i in template.plan], [i.as_data() for i in template.feedback]


def close_notice(conf: settings.Settings, root: str, parent: str) -> str:
    """親が閉じたときに出す、テンプレート化の案内 1 行（末尾の改行つき）。出さないときは空文字。

    出すのは、完了した親（`done/`）が計画を持ち、その識別子の下書きがまだ無く、同じ計画の形の
    テンプレートが取り込まれていないときだけ。`ready` で閉じると材料（`done/` と `flows/`）は
    退避されて拾えなくなるので、`ready` の前に出す。何も書かず、読めなければ出さない。
    """
    found, children, _ = _find_parent(conf, root, parent)
    if found is None:
        return ""
    if os.path.lexists(plan_templates.draft_file(conf, found.tree_root, parent)):
        return ""
    template, _ = _build(conf, found, children, parent)
    scanned = plan_templates.scan(conf, root)
    for view in [scanned.common, *scanned.layers]:
        for entry in view.entries:
            if entry.template is not None and _shape(entry.template) == _shape(template):
                return ""
    return (
        "テンプレートにするなら、`ready` の前に "
        f"`ccnavi --templates --draft {parent}` で下書きを作る"
        "（ready で閉じると下書きの材料は退避されて拾えない）。"
        "下書きは取り込むか削除してから ready\n"
    )


# ---- 取り込む


@dataclass
class ImportPlan:
    """取り込みの下見。`errors` が空なら取り込める。何も書いていない。"""

    draft: Draft | None = None
    to: str = ""
    layer: str = ""
    target: str = ""
    target_rel: str = ""
    # 取り込み先の置き場の根（リンクの検査の起点）。
    base: str = ""
    exists: bool = False
    replace: bool = False
    sha: str = ""
    raw: bytes = b""
    errors: list[str] = field(default_factory=list)
    # 下書きの検査（取り込み先の定義で行った）。
    problems: list[Problem] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)
    # 取り込み先に新しく置く文面と、取り込み先のいまの文面（無ければ空）。見せるためのもので、
    # 制御文字・書式文字は `\uXXXX` に直してある（書く中身は `raw`）。
    new_text: str = ""
    old_text: str = ""
    diff: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def locate(conf: settings.Settings, root: str, ref: str) -> tuple[Draft | None, str]:
    """下書きの名前かパスから下書きを探す。見つからないか決まらなければ (None, 理由)。

    パスは下書きの置き場（`plan_templates.draft_dir`）の下の `.yml` だけを受ける。
    """
    ref = ref.strip()
    if not ref:
        return None, "下書きの名前かパスを渡す"
    found = scan(conf, root).drafts
    suffix = plan_templates.SUFFIX
    if "/" in ref or os.sep in ref or "\\" in ref:
        wanted = os.path.normcase(os.path.abspath(ref))
        for draft in found:
            if os.path.normcase(os.path.abspath(draft.path)) == wanted:
                return draft, ""
        place = plan_templates.draft_dir_rel(conf)
        return None, f"{ref} は下書きの置き場（{place}/）の下の {suffix} ではない"
    name = ref[: -len(suffix)] if ref.endswith(suffix) else ref
    hits = [d for d in found if d.name == name]
    if not hits:
        listed = ", ".join(sorted({d.name for d in found})) or "無い"
        return None, f"下書き {name!r} はありません（{listed}）"
    if len(hits) > 1:
        paths = ", ".join(f"{tree_label(d.tree.name)}:{d.rel}" for d in hits)
        return None, f"下書き {name!r} が中身の違う形で複数ある（{paths}）。パスで指す"
    return hits[0], ""


def _target(conf: settings.Settings, root: str, to: str, layer: str, plan: ImportPlan) -> str:
    """取り込み先を決めて `plan` に入れる。決まらなければ理由。"""
    name = plan.draft.name if plan.draft else ""
    if to == TO_COMMON:
        if layer:
            return "--layer は --to project のときだけ使う"
        if not plan_templates.common_writable(conf, root):
            return (
                "共通レイヤーの置き場はミラー（共通レイヤーの写し）なので反映しません。"
                "ワークスペースルートで取り込む"
            )
        directory = settings.common_templates_dir(conf, root)
        plan.layer = plan_templates.LAYER_COMMON
        plan.base = root
    elif to == TO_PROJECT:
        layer = layer or plan_templates.LAYER_SELF
        views = {view.name: view for view in ruleload.layers(conf, root)}
        if layer not in views:
            return f"レイヤー {layer!r} はありません（{' / '.join(views)}）"
        directory = settings.layer_templates_dir(conf, views[layer].home)
        plan.layer = layer
        plan.base = views[layer].home
    else:
        return f"--to は {' か '.join(TARGETS)}"
    plan.to = to
    plan.target = os.path.join(directory, name + plan_templates.SUFFIX)
    plan.target_rel = plan_templates.relative(root, plan.target)
    return ""


def prepare(
    conf: settings.Settings,
    root: str,
    ref: str,
    to: str,
    layer: str = "",
    replace: bool = False,
) -> ImportPlan:
    """取り込みの下見。下書きの検査・取り込み先との差分・断る理由を作る。何も書かない。"""
    plan = ImportPlan(replace=replace)
    draft, why = locate(conf, root, ref)
    if draft is None:
        plan.errors.append(why)
        return plan
    plan.draft = draft
    entry = draft.entry
    plan.sha = entry.sha
    plan.raw = entry.raw
    if entry.template is None:
        plan.problems = list(entry.notes)
        plan.errors.append("下書きを読めないので取り込めない（下の error を直す）")
        return plan
    refused = _target(conf, root, to, layer, plan)
    if refused:
        plan.errors.append(refused)
        return plan
    # 取り込み先の定義で検査し直す（下書きの置かれたツリーの定義とは違いうる）。
    types = plan_templates.types_for(conf, root, plan.layer)
    plan.problems = list(entry.notes) + plan_templates.check(
        entry.template, types, flow.catalog(root)
    )
    # コメントなど、読んだ中身に出ない場所の文字も見る（CRLF の改行は問題にしない）。
    raw_hidden = plan_templates.hidden_problem(
        entry.raw.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
    )
    if raw_hidden and not any(p.detail.startswith(raw_hidden[:12]) for p in plan.problems):
        plan.problems.append(Problem(SEVERITY_ERROR, entry.name, raw_hidden))
    if any(p.severity == SEVERITY_ERROR for p in plan.problems):
        plan.errors.append("下書きに error があるので取り込めない（下の error を直す）")
    _existing(plan, root)
    _collisions(conf, root, plan)
    new_raw = entry.raw.decode("utf-8-sig", errors="replace")
    if plan.exists and not plan.errors:
        # 差分は書く中身そのもので取り、見せる行だけ制御文字・書式文字を `\uXXXX` にする。
        plan.diff = [
            plan_templates.reveal(line)
            for line in difflib.unified_diff(
                plan.old_text.splitlines(),
                new_raw.splitlines(),
                fromfile=f"{plan.target_rel}（いまの {plan.layer}）",
                tofile=f"{draft.rel}（下書き）",
                n=DIFF_CONTEXT,
                lineterm="",
            )
        ]
        if not plan.diff:
            plan.infos.append("取り込み先と同じ中身（差分はありません）")
    # 見せる本文。書く中身は `raw`（バイト列）で、ここは変えない。
    plan.new_text = plan_templates.reveal(new_raw)
    plan.old_text = plan_templates.reveal(plan.old_text)
    return plan


def _existing(plan: ImportPlan, root: str) -> None:
    """取り込み先に同名が在るか。在れば中身を読み、リンクなどなら断る。"""
    if flow.linked(plan.base, os.path.dirname(plan.target)):
        plan.errors.append(
            f"取り込み先の置き場（{plan_templates.relative(root, os.path.dirname(plan.target))}）の"
            "途中にリンクがあるので反映しません"
        )
        return
    if not os.path.lexists(plan.target):
        if plan.replace:
            plan.infos.append("置き換える相手はありません（新規として置く）")
        return
    plan.exists = True
    raw, why = flow.read_bytes(plan.target, plan.base)
    if raw is None:
        plan.errors.append(f"取り込み先 {plan.target_rel} は書き換えない（{why}）")
        return
    plan.old_text = raw.decode("utf-8-sig", errors="replace")
    if not plan.replace:
        plan.errors.append(
            f"取り込み先に {plan.target_rel} が既に在る。改訂は --replace を付ける"
            "（差分を見せたうえで置き換える）"
        )


def collide(
    conf: settings.Settings,
    root: str,
    template: plan_templates.Template,
    to: str,
    layer: str,
    skip: str = "",
) -> tuple[list[str], list[str]]:
    """取り込むと、共通とレイヤーに同名で中身の違うものが並ぶ形になるなら断る。(errors, infos)。
    `skip` は衝突の相手に数えないファイル（移動元自身。ワークスペースルートからの相対パス）。

    統合結果はその形のレイヤーの側を使わない error にする（`plan_templates.merge`）。
    `to` が common なら `layer` は使わない。project なら `layer` は取り込み先のレイヤー。
    """
    errors: list[str] = []
    infos: list[str] = []
    scanned = plan_templates.scan(conf, root)
    mine = plan_templates.dump(template)
    name = template.name
    if to == TO_COMMON:
        for view in scanned.layers:
            for other in view.entries:
                if other.name != name or other.template is None:
                    continue
                if skip and other.rel == skip:
                    continue
                if plan_templates.dump(other.template) != mine:
                    errors.append(
                        f"{view.name} に同名で中身の違う {other.rel} が在る。共通へ取り込むと"
                        f"{view.name} の側が使われなくなる。先に {view.name} の側を直す"
                    )
                else:
                    infos.append(
                        f"{view.name} に全欄が同じ {other.rel} が在る"
                        f"（取り込むと {view.name} の側は捨てられる）"
                    )
        return errors, infos
    for other in scanned.common.entries:
        if other.name != name or other.template is None:
            continue
        if skip and other.rel == skip:
            continue
        if plan_templates.dump(other.template) != mine:
            errors.append(
                f"共通に同名で中身の違う {other.rel} が在る。取り込んでも {layer} の側は"
                "使われない。別の名前にする"
            )
        else:
            infos.append(
                f"共通に全欄が同じ {other.rel} が在る（取り込んでも {layer} の側は捨てられる）"
            )
    return errors, infos


def _collisions(conf: settings.Settings, root: str, plan: ImportPlan) -> None:
    """`collide` の結果を取り込みの下見に足す。"""
    template = plan.draft.entry.template if plan.draft else None
    if template is None:
        return
    errs, infos = collide(conf, root, template, plan.to, plan.layer)
    plan.errors.extend(errs)
    plan.infos.extend(infos)


@dataclass
class Imported:
    ok: bool
    errors: list[str] = field(default_factory=list)
    written: bool = False
    # 下書きを消したか。消さなかった理由は `kept`。
    removed: bool = False
    kept: str = ""
    infos: list[str] = field(default_factory=list)


def commit(plan: ImportPlan, expect: str) -> Imported:
    """取り込む。`prepare` が断らず、`expect` が下書きの中身のハッシュと同じときだけ書く。"""
    if not plan.ok or plan.draft is None:
        return Imported(False, errors=list(plan.errors))
    if not expect:
        return Imported(
            False,
            errors=[
                "--yes には --expect <下書きのハッシュ> を指定してください"
                "（見せた下書きを取り込むため）"
            ],
        )
    if expect != plan.sha:
        return Imported(
            False,
            errors=[
                "--expect が下書きの今の中身のハッシュと違う（見せたあとに下書きが書き直された）。"
                f"何も反映しません。今のハッシュ: {plan.sha}。差分を見直す"
            ],
        )
    if plan.exists:
        err = fsio.write_bytes_atomic(plan.target, plan.raw)
    else:
        err = fsio.write_new_durable(plan.target, plan.raw)
    if err:
        return Imported(False, errors=[f"{plan.target_rel} を保存できませんでした（{_line(err)}）"])
    # 書いたものを読み直す。読めない・違うなら、下書きは消さない。
    raw, why = flow.read_bytes(plan.target, plan.base)
    if raw is None or plan_templates.digest(raw) != plan.sha:
        return Imported(
            False,
            written=True,
            errors=[
                f"{plan.target_rel} を保存したが、読み直すと取り込んだ中身と違う（{why or '内容'}）"
                "。"
                "下書きは削除していません。"
            ],
        )
    done = Imported(True, written=True)
    _remove_draft(plan, done)
    return done


def _remove_draft(plan: ImportPlan, done: Imported) -> None:
    """下書きの今の中身が取り込んだときの中身と同じときだけ消す。リンクなどは消さない。"""
    draft = plan.draft
    assert draft is not None
    raw, why = flow.read_bytes(draft.path, draft.tree.root)
    if raw is None:
        done.kept = f"下書きを読めないので削除していません（{why}）"
        return
    if plan_templates.digest(raw) != plan.sha:
        done.kept = "取り込んだあとに下書きが書き直されているので削除していません。"
        return
    err = fsio.unlink(draft.path)
    if err:
        done.kept = f"下書きを削除できませんでした（{_line(err)}）"
        return
    done.removed = True
    if draft.also:
        others = ", ".join(tree_label(name) for name in draft.also)
        done.infos.append(f"同じ中身の下書きがほかのツリー（{others}）にも在る。削除していません。")


def plan_json(plan: ImportPlan, result: Imported | None = None) -> dict:
    """`--import --json` の答え。下見と、書いたときはその結果。"""
    draft = plan.draft
    out: dict = {
        "version": plan_templates.JSON_VERSION,
        "ok": plan.ok if result is None else result.ok,
        "draft": (
            {
                "name": draft.name,
                "tree": draft.tree.name,
                "path": draft.rel,
                "sha": draft.sha,
            }
            if draft
            else None
        ),
        "to": plan.to,
        "layer": plan.layer,
        "target": plan.target_rel,
        "exists": plan.exists,
        "replace": plan.replace,
        "errors": list(plan.errors if result is None else result.errors or plan.errors),
        "infos": list(plan.infos),
        "problems": [{"severity": p.severity, "detail": p.detail} for p in plan.problems],
        "diff": list(plan.diff),
        "new_text": plan.new_text,
        "written": bool(result and result.written),
        "removed": bool(result and result.removed),
        "kept": result.kept if result else "",
    }
    return out
