"""共通レイヤーをプロジェクトの `.ccnavi/config/common/` へミラーする。書く時は `promptsync`（ユーザの発言）と `mirrorsync`（ユーザが押す入口）が決める。

共通レイヤー（ワークスペースルートの `.ccnavi/config/common/`）はワークスペースの git にあるので、
プロジェクトだけを clone したユーザからは見えない。そこでユーザの発言のたびに、
共通レイヤーの中身を、着手済みの親のワークツリーにあるプロジェクトの `.ccnavi/config/common/` へ写す。
プロジェクトの `.ccnavi/config/project/` には触れない。

- 共通レイヤーが無い、または空なら、何も配らず、ミラーは残す
- 写すのは `rules.yml`・`phases.yml`・`risks.yml`・`rule-expectations.yml` と、配点の
  `script:` が指す `.ccnavi/config/common/scripts/` の下と、作業・レビュー計画のテンプレート
  （`.ccnavi/config/common/templates/*.yml`）。中身の違うものだけ上書きし、
  共通レイヤーから無くなったファイルはミラーからも消す。ミラーは共通レイヤーの写しで誰も編集しないので、失うものは無い
- `script:` の値はそのまま写す。ミラーの置き場も `.ccnavi/config/common/scripts/` で、共通レイヤーと
  同じ表記のまま指せる
- 写す前に、ミラーとして（単体 clone では共通レイヤーとして）読めるかを確かめる。error が
  あれば何も写さず、着手しない。ただしテンプレートは別で、error のある 1 本は飛ばし、置き場が
  読めない（リンクなど）ときはテンプレートだけ写さず、ほかの設定の写しと着手は止めない
- 写し先かその途中がシンボリックリンクなら止める
- 上書きと削除は一時ファイルからの置き換えで行う。途中で止まれば、した分を元へ戻す

ミラーを書くのは、ユーザの発言のとき（`promptsync`）とユーザが押す入口（`mirrorsync`）だけで、
ツール呼び出しの中では書かない。書いた分は次の受け入れ（`selfguard.accept`）で基準になるので、
実行後チェックとバックアップと復元から特別扱いする仕掛けは持たない。
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass

import yaml

from ..infra import fsio, pathcase, settings, yamlread
from ..policy import rules
from . import flow, phasetypes, plan_templates, risk

COMMON_DIR = settings.COMMON_DIR
# 共通レイヤーの期待する判定のファイル名。
EXPECTATIONS_NAME = "rule-expectations.yml"


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

    読めなければ理由を返す（何も写さない）。
    """
    base = common_dir(conf, root)
    out: dict[str, bytes] = {}
    risk_raw = b""
    for name in (
        settings.LAYER_FILE_NAMES[settings.KIND_RULES],
        settings.LAYER_FILE_NAMES[settings.KIND_PHASES],
        settings.LAYER_FILE_NAMES[settings.KIND_RISK],
        EXPECTATIONS_NAME,
    ):
        raw, why = _read_strict(os.path.join(base, name))
        if why:
            return {}, f"共通レイヤーの {name} を読めない ({why})"
        if raw is None:
            continue
        out[name] = raw
        if name == settings.LAYER_FILE_NAMES[settings.KIND_RISK]:
            risk_raw = raw
    for kind in settings.LAYER_KINDS:
        name = settings.LAYER_FILE_NAMES[kind]
        if name in out:
            why = _unreadable_as_common(kind, out[name], root)
            if why:
                return {}, f"共通レイヤーの {name} を、ミラーとして読めない: {why}"
    for rel in _script_values(risk_raw):
        raw, why = _read_strict(os.path.join(root, rel.replace("/", os.sep)))
        if why or raw is None:
            return {}, f"共通レイヤーの配点が指すスクリプト {rel} を読めない ({why or '無い'})"
        out[rel.removeprefix(_script_prefix())] = raw
    out.update(_templates(conf, root))
    return out, ""


def _templates(conf: settings.Settings, root: str) -> dict[str, bytes]:
    """共通レイヤーの作業・レビュー計画のテンプレート。ミラーの置き場からの相対 → 中身。

    置き場（`templates/`）が無いのは正常で、何も返さない。置き場そのものが読めない（リンクなど）
    ときも、理由は返さずテンプレートを 1 本も写さない（置き場がリンクなのは、dotfiles 管理などで
    起こりうる）。error のあるテンプレートは 1 本ずつ飛ばす（`--lint` が error で知らせる）。
    """
    read = plan_templates.read_layer(
        plan_templates.LAYER_COMMON, root, settings.common_templates_dir(conf, root)
    )
    if read.unreadable:
        return {}
    types = plan_templates.types_for(conf, root, plan_templates.LAYER_COMMON)
    candidates = flow.catalog(root)
    out: dict[str, bytes] = {}
    for entry in read.entries:
        problems = list(entry.notes)
        if entry.template is not None:
            problems += plan_templates.check(entry.template, types, candidates)
        if any(p.severity == rules.SEVERITY_ERROR for p in problems):
            continue
        out[f"{settings.TEMPLATES_DIR}/{entry.name}{plan_templates.SUFFIX}"] = entry.raw
    return out


def plan(conf: settings.Settings, root: str, tree_root: str) -> tuple[list[Change], str]:
    """ミラーに加える変更の一覧。写せない理由があれば、それを返す（何も写さない）。"""
    wanted, why = sources(conf, root)
    if why:
        return [], why
    if not wanted:
        # 共通レイヤーが無い、または空。「設定が無い」正常で、配るものが無い。ミラーは消さない
        # （共通を消し忘れた・取り違えたときに、ミラーを全部失わないため）。
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
                return (
                    f"{c.rel} を保存できませんでした: {failed}"
                    f"（戻せなかったもの: {', '.join(stuck)}）"
                )
            return f"{c.rel} を保存できませんでした: {failed}（した分は戻した）"
        done.append(c)
    if base:
        _prune_empty(base)
    return ""


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


def _unreadable_as_common(kind: str, content: bytes, root: str) -> str:
    """ミラーとして（単体 clone では共通レイヤーとして）読めないなら、その理由。"""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return "UTF-8 として読めない"
    if kind == settings.KIND_PHASES:
        types, problems = phasetypes.parse(text, "(phases)")
        errors = [p for p in problems if p.severity == rules.SEVERITY_ERROR]
        if types is None or errors:
            return "; ".join(p.detail for p in errors) or "フェーズ定義として読めない"
        return ""
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
    _, problems = rules.parse(data, root)
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

    書くのは一時ファイルからの置き換え。fsio を通す（write-and-push の記録層が、
    写したものを「この実行で書いた
    パス」に数え、`start` の write-and-push でコミットする）。
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
    return pathcase.fold(os.path.realpath(path))


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
