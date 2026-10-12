"""テンプレートと下書きの保存・改名・移動・削除。

下見（prepare）は何も書かず、commit は expect が主語のハッシュと一致したときだけ書く。

- 主語は下書き（`draft`）・共通（`common`）・レイヤー（`project`）のどれか 1 本の `<名前>.yml`
- 保存（save）は下書きの中身だけを書く。取り込み済みは下書きに直して `--replace` で取り込む
- 改名（rename）と移動（move）は、新しい置き場に書いて読み直し、元を消す。元を消せなければ元は残る
- 消す（delete-draft / delete）は主語を消すだけで、ほかの層や下書きには触らない
"""

from __future__ import annotations

import difflib
import os
from dataclasses import dataclass, field

from ..infra import fsio, settings
from ..policy import ruleload
from ..policy.rules import SEVERITY_ERROR, Problem
from . import flow, plan_templates, template_drafts

OP_SAVE = "save"
OP_DELETE_DRAFT = "delete-draft"
OP_RENAME = "rename"
OP_MOVE = "move"
OP_DELETE = "delete"

ON_DRAFT = "draft"
ON_COMMON = "common"
ON_PROJECT = "project"


@dataclass
class EditPlan:
    op: str
    name: str
    on: str = ""  # 主語の場所: draft / common / project
    # 主語の層の名前（common は plan_templates.LAYER_COMMON。draft は検査に使う層）
    layer: str = ""
    new_name: str = ""  # rename の新しい名前
    to: str = ""  # move の行き先: common / project
    to_layer: str = ""  # move の行き先の層の名前
    source: str = ""  # 主語のファイルの絶対パス
    source_rel: str = ""
    source_base: str = ""  # 主語の置き場の根（flow.linked / flow.read_bytes の起点）
    target: str = ""  # 書く先の絶対パス（save は source と同じ。delete 系は空）
    target_rel: str = ""
    target_base: str = ""
    sha: str = ""  # 主語の今の中身のハッシュ（commit の expect はこれと一致させる）
    raw: bytes = b""  # 書く中身（save / rename / move）
    tree_root: str = ""  # 下書きの置かれたツリーの根（draft のとき）
    errors: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    old_text: str = ""
    new_text: str = ""
    diff: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class EditResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)
    written: bool = False
    removed: bool = False
    new_sha: str = ""


# ---- 下見


def prepare(
    conf: settings.Settings,
    root: str,
    op: str,
    name: str,
    *,
    on: str = "",
    layer: str = "",
    new_name: str = "",
    to: str = "",
    to_layer: str = "",
    body: bytes | None = None,
) -> EditPlan:
    """何も書かずに、主語・検査・差分・断る理由を作る。"""
    plan = EditPlan(
        op=op,
        name=name.strip(),
        on=on,
        layer=layer,
        new_name=new_name.strip(),
        to=to,
        to_layer=to_layer,
    )
    _subject(conf, root, plan)
    if plan.errors:
        return plan
    if op == OP_SAVE:
        _save(conf, root, plan, body)
    elif op == OP_DELETE_DRAFT:
        if plan.on != ON_DRAFT:
            plan.errors.append("消せる下書きは on=draft だけ")
    elif op == OP_RENAME:
        _rename(conf, root, plan)
    elif op == OP_MOVE:
        _move(conf, root, plan)
    elif op == OP_DELETE:
        _delete(conf, root, plan)
    else:
        plan.errors.append(f"操作 {op!r} は無い")
    return plan


def _subject(conf: settings.Settings, root: str, plan: EditPlan) -> None:
    """主語（1 本のファイル）を探して `plan` に入れる。失敗なら理由を errors に入れる。"""
    if plan.on == ON_DRAFT:
        draft, why = template_drafts.locate(conf, root, plan.name)
        if draft is None:
            plan.errors.append(why)
            return
        plan.source = draft.path
        plan.source_rel = draft.rel
        plan.source_base = draft.tree.root
        plan.tree_root = draft.tree.root
        plan.sha = draft.sha
        plan.layer = draft.layer
        plan.name = draft.name
        plan.old_text = draft.entry.raw.decode("utf-8-sig", errors="replace")
        if draft.also:
            others = ", ".join(template_drafts.tree_label(n) for n in draft.also)
            plan.infos.append(
                f"同じ中身の下書きがほかのツリー（{others}）にも在る。そちらは変えない"
            )
        return
    if plan.on == ON_COMMON:
        if not plan_templates.common_writable(conf, root):
            plan.errors.append(
                "共通レイヤーの置き場はミラー（共通レイヤーの写し）なので書かない。"
                "ワークスペースルートで操作する"
            )
            return
        directory = settings.common_templates_dir(conf, root)
        plan.layer = plan_templates.LAYER_COMMON
        plan.source_base = root
    elif plan.on == ON_PROJECT:
        layer = plan.layer or plan_templates.LAYER_SELF
        views = {view.name: view for view in ruleload.layers(conf, root)}
        if layer not in views:
            plan.errors.append(f"レイヤー {layer!r} は無い（{' / '.join(views)}）")
            return
        directory = settings.layer_templates_dir(conf, views[layer].home)
        plan.layer = layer
        plan.source_base = views[layer].home
    else:
        plan.errors.append("場所は draft / common / project のどれか")
        return
    if not _name_ok(plan.name):
        plan.errors.append(f"テンプレートの名前 {plan.name!r} は使えない")
        return
    plan.source = os.path.join(directory, plan.name + plan_templates.SUFFIX)
    plan.source_rel = plan_templates.relative(root, plan.source)
    if flow.linked(plan.source_base, directory):
        plan.errors.append("置き場の途中にリンクがあるので触らない")
        return
    if not os.path.lexists(plan.source):
        plan.errors.append(f"{plan.source_rel} は無い")
        return
    raw, why = flow.read_bytes(plan.source, plan.source_base)
    if raw is None:
        plan.errors.append(f"{plan.source_rel} は触らない（{why}）")
        return
    plan.sha = plan_templates.digest(raw)
    plan.old_text = raw.decode("utf-8-sig", errors="replace")


def _name_ok(name: str) -> bool:
    return bool(plan_templates.NAME_RE.match(name)) and len(name) <= plan_templates.NAME_LIMIT


def _show(plan: EditPlan, old: str, new: str) -> None:
    """見せる文面と差分を作る。差分の元は reveal する前の文字列で取り、行ごとに reveal する。"""
    diff = difflib.unified_diff(
        old.splitlines(),
        new.splitlines(),
        fromfile=f"{plan.source_rel}（いま）",
        tofile=f"{plan.target_rel or plan.source_rel}（保存後）",
        n=template_drafts.DIFF_CONTEXT,
        lineterm="",
    )
    plan.diff = [plan_templates.reveal(line) for line in diff]
    plan.old_text = plan_templates.reveal(old)
    plan.new_text = plan_templates.reveal(new)
    if not plan.diff:
        plan.infos.append("今の中身と同じ（差分は無い）")


def _save(conf: settings.Settings, root: str, plan: EditPlan, body: bytes | None) -> None:
    if plan.on != ON_DRAFT:
        plan.errors.append(
            "保存できるのは下書きだけ（取り込み済みは下書きに直して --replace で取り込む）"
        )
        return
    if body is None:
        plan.errors.append("本文を渡す")
        return
    if len(body) > flow.FILE_LIMIT:
        plan.errors.append(f"本文が大きすぎる（{flow.FILE_LIMIT} バイトまで）")
        return
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        plan.errors.append("本文が UTF-8 ではない")
        return
    hidden = plan_templates.hidden_problem(text.replace("\r\n", "\n"))
    if hidden:
        plan.errors.append(hidden)
        return
    # stem を渡さない。渡すと名前の違いを読めない苦情にして、下の「name を変える」を出せない。
    template, notes = plan_templates.parse_template(body)
    if template is not None and template.name != plan.name:
        plan.errors.append("name を変えると別の下書きになる。改名は rename で行う")
        return
    plan.problems = list(notes)
    if template is not None:
        types = plan_templates.types_for(conf, root, plan.layer)
        plan.problems += plan_templates.check(template, types, flow.catalog(root))
    plan.raw = body
    plan.target = plan.source
    plan.target_rel = plan.source_rel
    plan.target_base = plan.source_base
    _show(plan, plan.old_text, text)


def _rename(conf: settings.Settings, root: str, plan: EditPlan) -> None:
    if not _name_ok(plan.new_name):
        plan.errors.append(f"新しい名前 {plan.new_name!r} は使えない")
        return
    if plan.new_name == plan.name:
        plan.errors.append("新しい名前が今の名前と同じ")
        return
    if plan.on == ON_DRAFT:
        target = plan_templates.draft_file(conf, plan.tree_root, plan.new_name)
    else:
        target = os.path.join(os.path.dirname(plan.source), plan.new_name + plan_templates.SUFFIX)
    plan.target = target
    plan.target_rel = plan_templates.relative(root, target)
    plan.target_base = plan.source_base
    if flow.linked(plan.source_base, os.path.dirname(target)):
        plan.errors.append(f"{plan.target_rel} の置き場の途中にリンクがあるので書かない")
        return
    if os.path.lexists(target):
        plan.errors.append(f"{plan.target_rel} が既に在る")
        return
    raw, why = flow.read_bytes(plan.source, plan.source_base)
    if raw is None:
        plan.errors.append(f"{plan.source_rel} は読めないので改名できない（{why}）")
        return
    template, notes = plan_templates.parse_template(raw, plan.name)
    if template is None:
        detail = next((p.detail for p in notes if p.severity == SEVERITY_ERROR), "読めない")
        plan.errors.append(f"読めないテンプレートは改名できない（{detail}）")
        return
    template.name = plan.new_name
    plan.raw = plan_templates.dump(template).encode("utf-8")
    if plan.on != ON_DRAFT:
        to = template_drafts.TO_COMMON if plan.on == ON_COMMON else template_drafts.TO_PROJECT
        errs, infos = template_drafts.collide(conf, root, template, to, plan.layer)
        plan.errors.extend(errs)
        plan.infos.extend(infos)
    _show(plan, plan.old_text, plan.raw.decode("utf-8"))


def _move(conf: settings.Settings, root: str, plan: EditPlan) -> None:
    if plan.on == ON_DRAFT:
        plan.errors.append("移動できるのは取り込み済みのテンプレートだけ")
        return
    if plan.to not in template_drafts.TARGETS:
        plan.errors.append("--to は common か project")
        return
    if plan.to == template_drafts.TO_COMMON:
        if not plan_templates.common_writable(conf, root):
            plan.errors.append(
                "共通レイヤーの置き場はミラー（共通レイヤーの写し）なので書かない。"
                "ワークスペースルートで操作する"
            )
            return
        directory = settings.common_templates_dir(conf, root)
        plan.to_layer = plan_templates.LAYER_COMMON
        base = root
    else:
        layer = plan.to_layer or plan_templates.LAYER_SELF
        views = {view.name: view for view in ruleload.layers(conf, root)}
        if layer not in views:
            plan.errors.append(f"レイヤー {layer!r} は無い（{' / '.join(views)}）")
            return
        directory = settings.layer_templates_dir(conf, views[layer].home)
        plan.to_layer = layer
        base = views[layer].home
    same_on = ON_COMMON if plan.to == template_drafts.TO_COMMON else ON_PROJECT
    if plan.on == same_on and plan.to_layer == plan.layer:
        plan.errors.append("同じ層への移動は何も変えない")
        return
    plan.target = os.path.join(directory, plan.name + plan_templates.SUFFIX)
    plan.target_rel = plan_templates.relative(root, plan.target)
    plan.target_base = base
    if flow.linked(base, directory):
        plan.errors.append("移動先の置き場の途中にリンクがあるので書かない")
        return
    if os.path.lexists(plan.target):
        plan.errors.append(f"移動先に {plan.target_rel} が既に在る")
        return
    raw, why = flow.read_bytes(plan.source, plan.source_base)
    if raw is None:
        plan.errors.append(f"{plan.source_rel} は読めないので移動できない（{why}）")
        return
    plan.raw = raw
    template, notes = plan_templates.parse_template(raw, plan.name)
    if template is None:
        plan.errors.append("読めないテンプレートは移動できない（下の error を直す）")
        plan.problems = list(notes)
        return
    types = plan_templates.types_for(conf, root, plan.to_layer)
    plan.problems = list(notes) + plan_templates.check(template, types, flow.catalog(root))
    if any(p.severity == SEVERITY_ERROR for p in plan.problems):
        plan.errors.append("行き先の定義で error があるので移動できない（下の error を直す）")
        return
    # 移動元自身が同名の相手として数えられるので、その行は捨てる。
    errs, infos = template_drafts.collide(conf, root, template, plan.to, plan.to_layer)
    plan.errors.extend(e for e in errs if plan.source_rel not in e)
    plan.infos.extend(i for i in infos if plan.source_rel not in i)


def _delete(conf: settings.Settings, root: str, plan: EditPlan) -> None:
    if plan.on == ON_DRAFT:
        plan.errors.append("取り込み済みのテンプレートだけ消せる。下書きは delete-draft")
        return
    scanned = plan_templates.scan(conf, root)
    others = [n for n in template_drafts._item_names(scanned, plan.name) if n != plan.layer]
    if others:
        plan.infos.append(f"ほかの層（{', '.join(others)}）に同名が在る。消すとそちらが使われる")


# ---- 書く


def commit(plan: EditPlan, expect: str) -> EditResult:
    """書く。`expect` が主語の今の中身のハッシュと同じときだけ書く。"""
    if not plan.ok:
        return EditResult(False, errors=list(plan.errors))
    if not expect:
        return EditResult(
            False,
            errors=["--yes には --expect <主語のハッシュ> が要る（見せた内容を操作するため）"],
        )
    stale = (
        f"--expect が主語の今の中身のハッシュと違う（見せたあとに書き直された）。"
        f"何も書かない。今のハッシュ: {plan.sha}"
    )
    if expect != plan.sha:
        return EditResult(False, errors=[stale])
    # 見せてから書くまでの間の書き換えを防ぐため、書く直前にもう一度読む。
    now, _ = flow.read_bytes(plan.source, plan.source_base)
    if now is None or plan_templates.digest(now) != plan.sha:
        return EditResult(False, errors=[stale])
    if plan.op == OP_SAVE:
        return _commit_save(plan)
    if plan.op in (OP_DELETE_DRAFT, OP_DELETE):
        return _commit_delete(plan)
    return _commit_copy(plan)


def _commit_save(plan: EditPlan) -> EditResult:
    err = fsio.write_bytes_atomic(plan.target, plan.raw)
    if err:
        return EditResult(
            False, errors=[f"{plan.target_rel} を書けない（{template_drafts._line(err)}）"]
        )
    raw, why = flow.read_bytes(plan.target, plan.target_base)
    if raw is None or plan_templates.digest(raw) != plan_templates.digest(plan.raw):
        return EditResult(
            False,
            written=True,
            errors=[f"{plan.target_rel} を書いたが、読み直すと書いた中身と違う（{why or '内容'}）"],
        )
    return EditResult(True, written=True, new_sha=plan_templates.digest(plan.raw))


def _commit_delete(plan: EditPlan) -> EditResult:
    err = fsio.unlink(plan.source)
    if err:
        return EditResult(
            False, errors=[f"{plan.source_rel} を消せなかった（{template_drafts._line(err)}）"]
        )
    return EditResult(True, removed=True, infos=list(plan.infos))


def _commit_copy(plan: EditPlan) -> EditResult:
    """改名・移動。新しい置き場に書いて読み直し、読めたら元を消す。元を消せなければ元は残る。"""
    err = fsio.write_new_durable(plan.target, plan.raw)
    if err:
        return EditResult(
            False, errors=[f"{plan.target_rel} を書けない（{template_drafts._line(err)}）"]
        )
    raw, why = flow.read_bytes(plan.target, plan.target_base)
    if raw is None or plan_templates.digest(raw) != plan_templates.digest(plan.raw):
        return EditResult(
            False,
            written=True,
            errors=[
                f"{plan.target_rel} を書いたが、読み直すと書いた中身と違う（{why or '内容'}）。"
                f"元は消していない（{plan.source_rel}）"
            ],
        )
    err = fsio.unlink(plan.source)
    if err:
        return EditResult(
            False,
            written=True,
            errors=[
                f"{plan.target_rel} は書いたが、元の {plan.source_rel} を消せなかった"
                f"（{template_drafts._line(err)}）"
            ],
        )
    return EditResult(True, written=True, removed=True, new_sha=plan_templates.digest(plan.raw))


# ---- JSON


def plan_json(plan: EditPlan, result: EditResult | None = None) -> dict:
    """`--json` の答え。下見と、書いたときはその結果。"""
    infos: list[str] = []
    for line in list(plan.infos) + (list(result.infos) if result else []):
        if line not in infos:
            infos.append(line)
    return {
        "version": plan_templates.JSON_VERSION,
        "ok": plan.ok if result is None else result.ok,
        "op": plan.op,
        "name": plan.name,
        "on": plan.on,
        "layer": plan.layer,
        "new_name": plan.new_name,
        "to": plan.to,
        "to_layer": plan.to_layer,
        "source": plan.source_rel,
        "target": plan.target_rel,
        "sha": plan.sha,
        "errors": list(plan.errors if result is None or not result.errors else result.errors),
        "infos": infos,
        "problems": [{"severity": p.severity, "detail": p.detail} for p in plan.problems],
        "diff": list(plan.diff),
        "new_text": plan.new_text,
        "written": bool(result and result.written),
        "removed": bool(result and result.removed),
        "new_sha": result.new_sha if result else "",
    }
