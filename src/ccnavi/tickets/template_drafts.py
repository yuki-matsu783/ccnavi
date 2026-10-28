"""作業・レビュー計画のテンプレートの下書き。完了した親から作り、ユーザが取り込む。

読む側（`plan_templates`）が持つのは効力のある置き場（共通とレイヤー）だけ。ここは効力の無い
下書きを扱う。下書きの置き場は、ほかの保護対象と同じ `wip/handoff/<取り込み先の相対パス>`
（`handoff`）で、取り込みも `ccnavi-user-handoff.sh` が行う。

## 下書きを作る（`make`）

完了した親の `done/` の計画（`plan:` / `feedback:` のステップの `type`・`review`・`after`）と、
各子のフロー（`.ccnavi/approved/flows/<子>.yml`）を 1 ファイルのテンプレートにコピーし、親のツリーの
`wip/handoff/` の下の、取り込み先と同じ相対パスに書く。エージェントが打ってよい（効力が無い）。

- `after` は各計画の中の添字（1 始まり）にする。親の計画の `after` は全体計画の続きの絶対番号で、
  フィードバック計画のステップは全体計画のステップ数を引いて直す。全体計画の `after` はそのまま。
  承認済みの親の計画の順序は承認が確かめているので、並べ替え（`plan_order.renumber`）は要らず、引き算だけで足りる
- 子のフローは、全体計画の n 番目のステップの子（`phase` が n）のうち、フローを持つ
  最初の 1 本をコピーする。
  コピーするのは全体計画の子のフローだけ（テンプレートの `flows` は全体計画の添字で指す）
- 固有名の一般化はエージェントが直す。親のタイトルから入れる `description` と `when` の両方に
  `TODO` の印を付ける。印が残っていれば検査が warn で言う
- 取り込み先に同名のテンプレートが既にあれば、上書きになる下書きを作らずに断る（直すときは
  `ccnavi-stage.sh` で既存をコピーして編集する）。同名の下書きがあれば上書きせず断る。
  親が `ready` で
  閉じて退避済み（`logs/archive/`）なら、そこからは読まないと言って断る

## 一覧と検査（`scan`）

全ツリーの `wip/handoff/` の、取り込み先の表で `template` の種類にヒットする下書きを読み、
取り込み先の
レイヤーのフェーズ定義で検査する。`--templates --json` の `drafts[]` と `--lint` が読む。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..infra import fsio, pathsafe, settings, tree
from ..policy import ruleload
from ..policy.rules import SEVERITY_ERROR, SEVERITY_WARN, Problem
from . import handoff, plan_templates
from .approval import store as approval
from .flow import graph as flow
from .model import archive, ticket_ids, ticket_model, ticket_places

TO_COMMON = "common"
TO_PROJECT = "project"
TARGETS = (TO_COMMON, TO_PROJECT)

# ワークスペースのルートのツリー（名前が空）を人に見せるときの言い方。
ROOT_LABEL = "ワークスペースルート"

# 下書きのタイトルの前に付ける印（`plan_templates.TODO_MARK` を含む）。
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
    # 取り込み先のレイヤー（common・self・プロジェクト名）。検査はこのフェーズ定義で行う。
    layer: str
    entry: plan_templates.Entry
    # 取り込み先（ツリーからの相対、"/" 区切り）。
    target_rel: str = ""
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


def _target_layer(conf: settings.Settings, root: str, where: tree.Tree, key: str) -> str:
    """下書きの取り込み先のレイヤー。共通・自身（プロジェクトのツリーならそのプロジェクト）・
    プロジェクト。"""
    known = _known(conf, root)
    parts = key.split("/")
    if parts[0] == "projects":
        name = next((n for n in known if handoff.fold_part(n) == parts[1]), "")
        return name or plan_templates.LAYER_SELF
    if parts[2] == settings.LAYER_COMMON:
        return plan_templates.LAYER_COMMON
    return where.project if where.project in known else plan_templates.LAYER_SELF


def scan(conf: settings.Settings, root: str, candidates: dict | None = None) -> DraftScan:
    """全ツリー（ワークスペース・プロジェクト・ワークツリー）のテンプレートの下書きを読んで検査する。

    下書きは `wip/handoff/` の下で、取り込み先の表の `template` の種類にヒットするもの。
    検査は取り込み先のレイヤーのフェーズ定義（共通は共通だけ、ほかは共通 + レイヤー）で行う。
    同じ中身の下書きが
    複数のツリーに在れば（親のブランチに乗ったものが子のワークツリーにも checkout される）、
    最初の 1 本にまとめる。
    """
    if candidates is None:
        candidates = flow.catalog(root)
    out = DraftScan()
    seen: dict[tuple[str, str], Draft] = {}
    said: set[str] = set()
    for where in approval.trees(conf, root):
        found, _, refused = handoff.walk(where.root)
        if refused and refused not in said:
            said.add(refused)
            out.places.append(
                (
                    handoff.HANDOFF_REL,
                    Problem(SEVERITY_ERROR, "", f"{handoff.HANDOFF_REL}: {refused}"),
                )
            )
        for item in found:
            if handoff.kind_of(item.key) is not handoff.kind(handoff.KIND_TEMPLATE):
                continue
            entry = plan_templates.read_entry(plan_templates.LAYER_DRAFT, where.root, item.path)
            layer = _target_layer(conf, root, where, item.key)
            types = plan_templates.types_for(conf, root, layer)
            if entry.template is not None:
                entry.checks.extend(plan_templates.check(entry.template, types, candidates))
            key2 = (entry.name, entry.sha)
            if entry.sha and key2 in seen:
                seen[key2].also.append(where.name)
                continue
            draft = Draft(where, layer, entry, target_rel=item.rel)
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
    """ボードのテンプレートの画面が下書きを並べる 1 本分の答え。

    `path` は下書き（ツリーからの相対。`wip/handoff/…`）、
    `target` は取り込み先（ツリーからの相対）、
    `layer` は取り込み先のレイヤー。取り込みは `ccnavi-user-handoff.sh <target>` で行う。
    `text` は読んだ下書きの本文そのまま（reveal はしない）。
    `importable` は読めて error が無いこと。`exists_in` は同じ名前のテンプレートが在るレイヤー。
    `problems` の深刻度は下書きの検査のままで、`--lint` は error も warn に下げて出す。
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
    out["target"] = draft.target_rel
    out["layer"] = draft.layer
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
    # 取り込み先（ツリーからの相対）と、そのレイヤー。
    target: str = ""
    layer: str = ""
    sha: str = ""
    tree: str = ""
    # 下書きを置いたツリーのルート（`user handoff` を打つツリー）。
    tree_root: str = ""
    template: plan_templates.Template | None = None
    # 知らせ（コピーできなかったフロー、落とした先行など）。
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
    """親の計画のステップを、その計画の中の添字の `after` に直す。

    `offset` はその計画の最初の番号 - 1。
    """
    out = []
    for i, item in enumerate(items, start=1):
        after = []
        for value in item.after:
            local = value - offset
            if 1 <= local < i:
                after.append(local)
            else:
                notes.append(
                    f"{key}[{i}] の after {value} は{key}の中のステップを指さないので落とした"
                )
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
    """全体計画の n 番目のステップ → その子のフローの本文。
    フローを持つ最初の子の 1 本をコピーする。"""
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
                notes.append(
                    f"{n} 番目の子 {child.ticket} のフローは読めないのでコピーしない（{why}）"
                )
                continue
            if taken:
                notes.append(
                    f"{n} 番目は {taken} のフローをコピーした（{child.ticket} にもフローがある）"
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
    """完了した親の計画とフローを、下書きの中身（まだ一般化していない）にコピーする。何も書かない。"""
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
            notes.append("フィードバック計画の子のフローはコピーしない（全体計画の子のフローだけ）")
    return template, notes


def _target_of(
    conf: settings.Settings, root: str, found: ticket_model.Ticket, name: str, to: str, layer: str
) -> tuple[str, str, str]:
    """下書きの取り込み先（ツリーからの相対）と、そのレイヤー。決まらなければ空の 2 つと理由。"""
    home = fsio.slashed(conf.project_home or settings.DEFAULT_PROJECT_HOME).strip("/")
    file = f"{settings.TEMPLATES_DIR}/{name}{plan_templates.SUFFIX}"
    if to == TO_COMMON:
        if layer:
            return "", "", "--layer は --to project のときだけ使う"
        if found.project:
            return (
                "",
                "",
                (
                    f"{found.ticket} はプロジェクト（{found.project}）のツリーの親。そのツリーの"
                    "共通の置き場は共通レイヤーのコピーなので、共通の下書きは作らない"
                ),
            )
        return f"{home}/{settings.COMMON_DIR}/{file}", plan_templates.LAYER_COMMON, ""
    if to != TO_PROJECT:
        return "", "", f"--to は {' か '.join(TARGETS)}"
    if not layer or layer == plan_templates.LAYER_SELF:
        own = found.project if found.project in _known(conf, root) else plan_templates.LAYER_SELF
        return f"{home}/{settings.LAYER_CONFIG_DIR}/{file}", own, ""
    views = {view.name: view for view in ruleload.layers(conf, root)}
    view = views.get(layer)
    if view is None:
        return "", "", f"レイヤー {layer!r} はありません（{' / '.join(views)}）"
    rel = plan_templates.relative(found.tree_root, view.home)
    if os.path.isabs(rel) or rel.startswith(".."):
        return (
            "",
            "",
            (
                f"レイヤー {layer} の置き場が親のツリー（{tree_label(found.tree or '')}）の"
                "外にある。"
                "このツリーの下書きからは取り込めない"
            ),
        )
    return f"{rel}/{home}/{settings.LAYER_CONFIG_DIR}/{file}", layer, ""


def make(
    conf: settings.Settings,
    root: str,
    parent: str,
    name: str = "",
    to: str = TO_PROJECT,
    layer: str = "",
) -> Made:
    """完了した親から下書きを作って、親のツリーの `wip/handoff/` に書く。上書きはしない。

    処理フロー:
    1. `_find_parent` で、完了した親と子の一覧を引く。
    2. 下書きの名前（既定は親の識別子）を `plan_templates.NAME_RE` と `NAME_LIMIT` で検査する。
    3. `_target_of` で取り込み先（テンプレートの置き場と、そのレイヤー）を決める。
    4. `handoff.spelled` で親のツリーからの綴りを求め、
       取り込み先に同名のテンプレートが在れば止める。
    5. `handoff.draft_rel` で下書きの置き場を求める。途中にリンクがあれば止める。
       同名の下書きが在れば止める。
    6. `_build` でテンプレートを組み、`fsio.write_new_durable` で新規に書く。
    7. 書いたものを `plan_templates.read_entry` で読み直す。テンプレートがあれば
       `plan_templates.check` で型と流れを見る。
    8. 結果を `Made` にして返す。どこかで止まったときは `error` に理由を入れて返す。
    """
    found, children, why = _find_parent(conf, root, parent)
    if found is None:
        return Made(False, error=why)
    name = (name or parent).strip()
    if not plan_templates.NAME_RE.match(name) or len(name) > plan_templates.NAME_LIMIT:
        return Made(
            False,
            error=f"下書きの名前 {_line(name)!r} は指定できない"
            "（英数字で始め、英数字と `._-` だけ。"
            f"{plan_templates.NAME_LIMIT} 字まで）。--name で付ける",
        )
    target_rel, target_layer, why = _target_of(conf, root, found, name, to or TO_PROJECT, layer)
    if why:
        return Made(False, error=why)
    base = found.tree_root
    target, why = handoff.spelled(base, target_rel)
    if why:
        return Made(False, error=why)
    if os.path.lexists(target):
        return Made(
            False,
            error=(
                f"取り込み先に同名のテンプレート {target_rel} が既に在る。上書きになる下書きは"
                "作らない（別の --name を付けるか、直すなら ccnavi-stage.sh で既存をコピーして"
                "編集する）"
            ),
        )
    rel = handoff.draft_rel(target_rel)
    place, why = handoff.spelled(base, rel)
    if why:
        return Made(False, error=why)
    if pathsafe.has_link(base, os.path.dirname(place)):
        return Made(
            False,
            error=f"下書きの置き場（{os.path.dirname(rel)}/）の途中にリンクがあるので反映しません",
        )
    if os.path.lexists(place):
        return Made(
            False,
            error=(
                f"下書き {rel} が既に在る。上書きしない（別の --name を付けるか、先に削除します）"
            ),
        )
    template, notes = _build(conf, found, children, name)
    err = fsio.write_new_durable(place, plan_templates.dump(template).encode("utf-8"))
    if err:
        return Made(False, error=f"下書きを保存できませんでした（{_line(err)}）")
    entry = plan_templates.read_entry(plan_templates.LAYER_DRAFT, base, place, writable=True)
    problems = list(entry.notes)
    if entry.template is not None:
        types = plan_templates.types_for(conf, root, target_layer)
        problems += plan_templates.check(entry.template, types, flow.catalog(root))
    return Made(
        True,
        name=name,
        path=place,
        rel=rel,
        target=target_rel,
        layer=target_layer,
        sha=entry.sha,
        tree=found.tree or "",
        tree_root=base,
        template=entry.template or template,
        notes=notes,
        problems=problems,
    )


def _known(conf: settings.Settings, root: str) -> list[str]:
    return [view.name for view in ruleload.layers(conf, root)]


# ---- 親が閉じるときの案内


def _shape(template: plan_templates.Template) -> tuple[list, list]:
    """計画の形（全体計画とフィードバック計画のステップ）。取り込み済みかを見るときの比べる対象。

    フローの本文は比べない。下書きの一般化でフローの文も直るので、文まで比べると、
    取り込んだ直後でも「同じ内容」に読めない。
    """
    return [i.as_data() for i in template.plan], [i.as_data() for i in template.feedback]


def close_notice(conf: settings.Settings, root: str, parent: str, mr: bool = True) -> str:
    """親が閉じたときに出す、テンプレート化の案内 1 行（末尾の改行つき）。出さないときは空文字。

    出すのは、完了した親（`done/`）が計画を持ち、その識別子の下書き（親のツリーの
    `wip/handoff/` の `<識別子>.yml`）がまだ無く、同じ計画の形のテンプレートが取り込まれて
    いないときだけ。`ready` で閉じると材料（`done/` と `flows/`）は退避されて拾えなくなるので、
    `ready` の前に出す。読めなければ出さない。

    `mr` が偽なら、マージリクエストを作らない親の文面にする。この親には `ready` が無いので、
    材料は `wip/` を削除して統合先に取り込むまで残り、取り込んだあとは拾えない。
    """
    found, children, _ = _find_parent(conf, root, parent)
    if found is None:
        return ""
    wanted = handoff.fold_part(parent + plan_templates.SUFFIX)
    found_drafts, _, _ = handoff.walk(found.tree_root)
    template_kind = handoff.kind(handoff.KIND_TEMPLATE)
    for item in found_drafts:
        if handoff.kind_of(item.key) is template_kind and item.key.rsplit("/", 1)[-1] == wanted:
            return ""
    template, _ = _build(conf, found, children, parent)
    scanned = plan_templates.scan(conf, root)
    for view in [scanned.common, *scanned.layers]:
        for entry in view.entries:
            if entry.template is not None and _shape(entry.template) == _shape(template):
                return ""
    if not mr:
        return (
            f"テンプレートにするなら、`ccnavi --templates --draft {parent}` で下書きを作る。"
            f"`{ticket_places.WIP_ROOT}/` を削除して統合先に取り込む前に打つ"
            "（取り込んだあとは材料が残らず拾えない）。"
            "下書きは `wip/` を削除する前に、ユーザが `ccnavi-user-handoff.sh` で取り込む\n"
        )
    return (
        "テンプレートにするなら、`ready` の前に "
        f"`ccnavi --templates --draft {parent}` で下書きを作る"
        "（ready で閉じると下書きの材料は退避されて拾えない）。"
        "下書きはユーザが `ccnavi-user-handoff.sh` で取り込むか、削除してから ready\n"
    )
