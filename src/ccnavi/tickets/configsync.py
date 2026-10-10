"""着手の前に、共通レイヤーをプロジェクトの `.ccnavi/common/` へミラーする（設計 11.12）。

共通レイヤー（ワークスペースルートの `.ccnavi/common/`）はワークスペースの git にあるので、
プロジェクトだけを clone したユーザからは見えない。そこで親チケットの `ticket start` で、
共通レイヤーの中身を、親のワークツリーにあるプロジェクトの `.ccnavi/common/` へ写す。
プロジェクトの `.ccnavi/config/` には触れない。

- 共通レイヤーが無い、または空なら、何も配らず、ミラーも消さない（着手は止めない）
- 写すのは `rules.yml`・`risks.yml`・`rule-samples.yml` と、配点の `script:` が指す
  `.ccnavi/common/scripts/` の下。中身の違うものだけ上書きし、共通レイヤーから無くなった
  ファイルはミラーからも消す。ミラーは共通レイヤーの写しで誰も編集しないので、失うものは無い
- `script:` の値は書き換えない。ミラーの置き場も `.ccnavi/common/scripts/` で、共通レイヤーと
  同じ表記のまま指せる
- 写す前に、ミラーとして（単体 clone では共通レイヤーとして）読めるかを確かめる。error が
  あれば何も写さず、着手しない。共通レイヤーに `phases.yml` があるときも error で、配らない
- 写し先かその途中がシンボリックリンクなら止める
- 上書きと削除は一時ファイルからの置き換えで行う。途中で止まれば、した分を元へ戻す

ミラーの書き込みは、実行後チェックとバックアップと復元（どちらも `.ccnavi/` を守る）から見れば
エージェントの書き込みと区別が付かない。区別は内容で付ける（`is_synced_write`）。
誰が書いたかの台帳は持たない。台帳は git に入らないので、clone した別の機械には届かない。
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass

import yaml

from ..infra import fsio, settings, tree, yamlread
from ..policy import rules
from . import approval, approval_checks, risk

COMMON_DIR = settings.COMMON_DIR
# 共通レイヤーの見本のファイル名。
SAMPLES_NAME = "rule-samples.yml"
# `is_synced_write` の `content` を渡さなかった目印。None は「そのファイルが無い」の意味で使う。
UNSET = object()


@dataclass
class Change:
    """ミラーに加える（加えた）変更 1 本。"""

    # 親のワークツリーからの相対。"/" 区切り。
    rel: str
    target: str
    # 書く中身。None なら消す。
    content: bytes | None
    # 変える前の中身。無ければ None。戻すときに使う。
    before: bytes | None = None

    @property
    def deletes(self) -> bool:
        return self.content is None


def mirror_dir(conf: settings.Settings, tree_root: str) -> str:
    """そのツリーのミラーの置き場（絶対）。"""
    return os.path.join(tree_root, _home(conf).replace("/", os.sep), COMMON_DIR)


def common_dir(conf: settings.Settings, root: str) -> str:
    """ワークスペースルートの共通レイヤーの置き場（絶対）。"""
    return mirror_dir(conf, root)


def sources(conf: settings.Settings, root: str) -> tuple[dict[str, bytes], str]:
    """共通レイヤーのうち、ミラーへ写すもの。ミラーの置き場からの相対 → 中身。

    読めなければ理由を返す（何も写さない）。共通レイヤーに `phases.yml` があるときも理由を返す
    （置けないものなので配らない）。
    """
    base = common_dir(conf, root)
    out: dict[str, bytes] = {}
    phases = os.path.join(base, settings.LAYER_FILE_NAMES[settings.KIND_PHASES])
    if os.path.lexists(phases):
        return {}, (
            "共通レイヤーに phases.yml がある（置けないので配らない。"
            "フェーズ定義は config の phases.yml に置く）"
        )
    risk_raw = b""
    for name in (
        settings.LAYER_FILE_NAMES[settings.KIND_RULES],
        settings.LAYER_FILE_NAMES[settings.KIND_RISK],
        SAMPLES_NAME,
    ):
        raw, why = _read_strict(os.path.join(base, name))
        if why:
            return {}, f"共通レイヤーの {name} を読めない ({why})"
        if raw is None:
            continue
        out[name] = raw
        if name == settings.LAYER_FILE_NAMES[settings.KIND_RISK]:
            risk_raw = raw
    for kind in (settings.KIND_RULES, settings.KIND_RISK):
        name = settings.LAYER_FILE_NAMES[kind]
        if name in out:
            why = _unreadable_as_common(kind, out[name])
            if why:
                return {}, f"共通レイヤーの {name} を、ミラーとして読めない: {why}"
    for rel in _script_values(risk_raw):
        raw, why = _read_strict(os.path.join(root, rel.replace("/", os.sep)))
        if why or raw is None:
            return {}, f"共通レイヤーの配点が指すスクリプト {rel} を読めない ({why or '無い'})"
        out[rel.removeprefix(_script_prefix())] = raw
    return out, ""


def plan(conf: settings.Settings, root: str, tree_root: str) -> tuple[list[Change], str]:
    """ミラーに加える変更の一覧。写せない理由があれば、それを返す（何も写さない）。"""
    wanted, why = sources(conf, root)
    if why:
        return [], why
    if not wanted:
        # 共通レイヤーが無い、または空。「設定が無い」正常で、配るものが無い。ミラーは消さない
        # （共通を消し忘れた・取り違えたときに、ミラーを全部失わないため）。着手は止めない。
        return [], ""
    base = mirror_dir(conf, tree_root)
    if not _inside(tree_root, base) or _linked(tree_root, base):
        return [], f"{_rel_plain(tree_root, base)} がシンボリックリンクか、その下にある。写さない"
    present, why = _scan(base)
    if why:
        return [], why
    out: list[Change] = []
    for name in sorted(wanted):
        target = os.path.join(base, name.replace("/", os.sep))
        if _linked(tree_root, target):
            return (
                [],
                f"{_rel_plain(tree_root, target)} がシンボリックリンクか、その下にある。写さない",
            )
        before, why = _read_strict(target)
        if why:
            return [], f"{_rel(tree_root, target)} を読めない ({why})"
        if before is not None and _same(before, wanted[name]):
            continue
        out.append(Change(_rel(tree_root, target), target, wanted[name], before))
    for name in sorted(present):
        if name in wanted:
            continue
        target = os.path.join(base, name.replace("/", os.sep))
        before, why = _read_strict(target)
        if why or before is None:
            return [], f"{_rel(tree_root, target)} を読めない ({why or '無い'})"
        out.append(Change(_rel(tree_root, target), target, None, before))
    return out, ""


def apply(changes: list[Change], base: str = "") -> str:
    """ミラーへ変更を加える。書けなかった理由を返す。書けたら空文字。

    1 本でも書けなければ、した分を元へ戻し、戻せなかったものを名指しする。`base` を渡せば、
    消して空になったディレクトリを base の下から片付ける。
    """
    done: list[Change] = []
    for c in changes:
        failed = _put(c.target, c.content)
        if failed:
            stuck = [w.rel for w in reversed(done) if _put(w.target, w.before) != ""]
            if stuck:
                return f"{c.rel} を書けない: {failed}（戻せなかったもの: {', '.join(stuck)}）"
            return f"{c.rel} を書けない: {failed}（した分は戻した）"
        done.append(c)
    if base:
        _prune_empty(base)
    return ""


def is_synced_write(
    conf: settings.Settings,
    root: str,
    path: str,
    content: bytes | None | object = UNSET,
) -> bool:
    """その変更が、親の着手がミラーへ配った書き込みだと読めるか（設計 11.12）。

    `content` は変更後の中身（渡さなければディスク上の今の中身。None はそのファイルが無い）。
    コミットに入った分を見るときは、コミットされた中身を渡すこと。ディスクで答えると、
    好きな中身でコミットしてからディスクだけ戻す形が外れる。

    読めるのは次が全部揃ったときだけ。

    1. 置き場が、プロジェクトから切った承認済みの親チケットのワークツリー（名前が親の
       識別子で、そのチケットの `project:` がワークツリーのプロジェクトと同じ）の中の
       `.ccnavi/common/`。元リポジトリ、子のワークツリー、チケットの無いワークツリー、
       他のプロジェクトのワークツリーは外さない
    2. そのパス（解く前）の途中にシンボリックリンクが無い。リンクで差し替えると、指す先の中身で
       答えてしまう
    3. その中身が、ワークスペースルートの共通レイヤーの対応するものと同じ（バイト列で比べる。
       共通レイヤーに無いものは、無いのが同じ）

    読めないものは外さない。
    """
    where = tree.tree_of(root, _key(path), conf.projects)
    if where is None or not where.project or where.is_main:
        return False
    rel = _rel(where.root, path)
    prefix = f"{_home(conf)}/{COMMON_DIR}/"
    if not rel.startswith(prefix) or rel == prefix:
        return False
    if _linked(where.root, path):
        return False
    if not _approved_parent(conf, root, where):
        return False
    expected = _read(os.path.join(common_dir(conf, root), rel[len(prefix) :].replace("/", os.sep)))
    now = _read(path) if content is UNSET else content
    return now == expected


def _approved_parent(conf: settings.Settings, root: str, where: tree.Tree) -> bool:
    """そのワークツリーが、同じプロジェクト向けの承認済みの親チケットのものか。"""
    copies, _ = approval.scan(conf, root)
    closed, _ = approval.scan(conf, root, closed=True)
    ticket = approval_checks.by_id(copies + closed).get(where.name)
    return ticket is not None and not ticket.is_child and ticket.project == where.project


def _home(conf: settings.Settings) -> str:
    return (conf.project_home or settings.DEFAULT_PROJECT_HOME).replace("\\", "/").strip("/")


def _script_prefix() -> str:
    """共通レイヤーのスクリプトの置き場の、`common/` までの先頭。`script:` の値から外す。"""
    return risk.SCRIPT_HOMES[0].split(f"{COMMON_DIR}/", 1)[0] + f"{COMMON_DIR}/"


def _script_values(raw: bytes) -> list[str]:
    """共通レイヤーの配点が指すスクリプト（`script:` の値。ワークスペースルートからの相対）。"""
    if not raw:
        return []
    definition, _ = risk.parse(raw.decode("utf-8", errors="replace"), "(risk)")
    if definition is None:
        return []
    found: list[str] = []
    for f in definition.factors:
        value = str(f.value)
        is_common_script = f.kind == risk.KIND_SCRIPT and value.startswith(risk.SCRIPT_HOMES[0])
        if is_common_script and value not in found:
            found.append(value)
    return found


def _unreadable_as_common(kind: str, content: bytes) -> str:
    """ミラーとして（単体 clone では共通レイヤーとして）読めないなら、その理由。"""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return "UTF-8 として読めない"
    if kind == settings.KIND_RISK:
        definition, problems = risk.parse(text, "(risk)", risk.SCRIPT_HOMES)
        errors = [p for p in problems if p.severity == rules.SEVERITY_ERROR]
        if definition is None or errors:
            return "; ".join(p.detail for p in errors) or "配点として読めない"
        return ""
    try:
        data = yamlread.safe_load(text)
    except yaml.YAMLError:
        return "YAML として読めない"
    if not isinstance(data, dict):
        return "マッピングではない"
    _, problems = rules.parse(data)
    errors = [p for p in problems if p.severity == rules.SEVERITY_ERROR]
    return "; ".join(p.detail for p in errors)


def _scan(base: str) -> tuple[list[str], str]:
    """ミラーの置き場に今ある通常のファイル（base からの相対、"/" 区切り）。

    シンボリックリンクが 1 本でもあれば止める（理由を返す）。置き場が無ければ空。
    """
    found: list[str] = []
    if not os.path.lexists(base):
        return found, ""
    for parent, dirs, names in os.walk(base):
        for name in [*dirs, *names]:
            path = os.path.join(parent, name)
            if os.path.islink(path):
                shown = _rel_plain(os.path.dirname(base), path)
                return [], f"{shown} がシンボリックリンク。写さない"
        for name in names:
            found.append(os.path.relpath(os.path.join(parent, name), base).replace(os.sep, "/"))
    return found, ""


def _prune_empty(base: str) -> None:
    """消して空になったディレクトリを、base の下から片付ける。base 自身は残す。失敗は無視する。"""
    for parent, _, _ in os.walk(base, topdown=False):
        if parent != base:
            with contextlib.suppress(OSError):
                os.rmdir(parent)


def _put(path: str, content: bytes | None) -> str:
    """1 本を書く（None なら消す）。駄目なら理由。

    書くのは一時ファイルからの置き換え。fsio を通す（C1 の記録層が、写したものを「この実行で書いた
    パス」に数え、`start` の C1 でコミットする）。
    """
    if content is None:
        if not os.path.lexists(path):
            return ""
        return fsio.unlink(path)
    return fsio.replace_bytes(path, content, ".ccnavi-sync")


def _linked(tree_root: str, path: str) -> bool:
    """tree_root から path までの途中（path 自身を含む）に、シンボリックリンクがあるか。

    パスのままさかのぼり、実体が tree_root に着いたところで止める。tree_root より上の
    リンク（macOS の `/tmp` など）は数えない。着けなければ（外を指している）リンクと同じに扱う。
    """
    base = _key(tree_root)
    current = os.path.abspath(path)
    while _key(current) != base:
        if os.path.islink(current):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return True
        current = parent
    return False


def _rel_plain(base: str, path: str) -> str:
    """リンクを解かない相対。ユーザに見せるパス。"""
    return os.path.relpath(os.path.abspath(path), os.path.abspath(base)).replace(os.sep, "/")


def _inside(tree_root: str, target: str) -> bool:
    base = _key(tree_root)
    real = _key(target)
    return real == base or real.startswith(base + os.sep)


def _key(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _same(a: bytes, b: bytes) -> bool:
    """中身が同じか。改行の違いは見ない（Windows のチェックアウトで書き換わるため）。"""
    return a.replace(b"\r\n", b"\n") == b.replace(b"\r\n", b"\n")


def _rel(base: str, path: str) -> str:
    return os.path.relpath(os.path.realpath(path), os.path.realpath(base)).replace(os.sep, "/")


def _read(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _read_strict(path: str) -> tuple[bytes | None, str]:
    """無ければ (None, "")。在るのに読めなければ (None, 理由)。"""
    try:
        with open(path, "rb") as f:
            return f.read(), ""
    except FileNotFoundError:
        return None, ""
    except OSError as exc:
        return None, str(exc)
