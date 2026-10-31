"""保護対象の下書きを、元の場所へ取り込む（`ccnavi user handoff`）。

エージェントが書けない保護対象（テンプレート・レイヤーの設定・docs・hook とラッパーの sh など）は、
`wip/handoff/<ツリーからの相対パス>` に下書きを置き、ユーザがこの取り込みで元の場所へ書く。
相対パスの中の `.claude` の階層は、Claude Code 本体が編集を断るので `handoff-.claude` に読み替えて
置き、取り込むときに戻す（表と規則は `handoff_names`）。
下書きを作るのは `ccnavi-stage.sh`（元のファイルをコピーする）か、
テンプレートなら `ccnavi --templates --draft`。

## 取り込み先（許可リスト）

取り込み先は、下書きがあるツリー（`--root`）の、読み替えを戻した相対パス。取り込めるのは種類の表（`KINDS`）に
ヒットするものだけで、パスは全要素を NFC と `pathcase.fold` にそろえてから適用する。表にヒットしない
下書きは対象外として件数だけを数え、error にはしない（`wip/handoff/` には学びの雛形や再現用の
スクリプトも置かれる）。表を直すときは、行を足し、docs の表も直す（テストが食い違いを見る）。

## 断る形

- 絶対パス・`..`・途中を含むリンク・ふつうのファイルでない・ハードリンク・全要素のどれかが `.git`
  で始まる・ツリーの外。同じ取り込み先に落ちる下書きが 2 本あれば（大文字小文字、NFC/NFD の違い）、
  両方を断る
- バイナリ（NUL を含む）と、UTF-8 として読めないもの
- 種類の表で新規作成を許さない種類の、取り込み先に無いもの

選んだ対象に 1 つでも error があれば、何も書かない。

## 照合の値（`--expect`）

対象ごとに (相対パスの NFC・fold、種類、下書きのバイト列の SHA-256、取り込み先のバイト列の SHA-256
（無ければ `-`））を、長さの前置き（`<バイト数>:`）
つきで並べたものが 1 件の記録。選んだ対象の記録をパスの並び順で連結した SHA-256 が `expect`。
改行コードは正規化せず、バイト列のまま。

## 書き方

一時ファイル → `fsync` → 差し替え（`fsio.write_bytes_atomic` / `write_new_durable`）。書く直前に
リンクと取り込み先のハッシュを確かめ直す。複数の対象の途中で失敗したら、書いたものを書く前の
バイト列（メモリに持つ）へ戻し、戻せなかったものを名指しする。複数ファイルの書き込みは、
プロセスの途中終了に対して原子的ではない。取り込めたら、下書きの今のバイト列が取り込んだものと
同じときだけ、下書きを消す。
"""

from __future__ import annotations

import dataclasses
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass, field

import yaml

from ...infra import fsio, gitcmd, pathcase, pathsafe, settings, yamlread
from ...policy import ruleload, texts
from ...policy.rules import SEVERITY_ERROR, SEVERITY_WARN, Problem
from ..config import check as config_check
from ..flow import graph as flow
from ..model import handoff_names, ticket_places
from ..proposal import plan_templates

JSON_VERSION = 1

# 下書きの置き場（ツリーのルートからの相対）。
HANDOFF_PARTS = (ticket_places.WIP_ROOT, "handoff")
HANDOFF_REL = "/".join(HANDOFF_PARTS)
# 下書きの置き場で読み替える階層名の表（元の階層名 → 下書きでの階層名）。中身は `handoff_names` が
# `.ccnavi/scripts/ccnavi-handoff-names.txt` から読む。sh（`ccnavi-stage.sh`）と同じファイル。
HANDOFF_ALIASES = handoff_names.ALIASES

KIND_TEMPLATE = "template"
KIND_MESSAGES = "messages"
KIND_LAYER_CONFIG = "layer-config"
KIND_DOCS = "docs"
KIND_DEFENSE = "defense"

STATE_NEW = "new"
STATE_CHANGED = "changed"
STATE_UNCHANGED = "unchanged"
STATE_REJECTED = "rejected"

# 差分に添える前後の行数。
DIFF_CONTEXT = 3
# 差分で、末尾に改行が無いことを言う行。
NO_EOL = "\\ 末尾に改行がありません"
# `sh -n` を待つ秒数。
SH_CHECK_SECONDS = 10


@dataclass(frozen=True)
class Kind:
    """取り込み先の種類の表の 1 行。"""

    id: str
    # docs に載せる形（ツリーからの相対）。表と docs の食い違いの検出が読む。
    shown: tuple[str, ...]
    # 全要素を NFC と fold にそろえた相対パス（"/" 区切り）に適用する式。
    patterns: tuple[str, ...]
    # 取り込み先に無いファイルを新しく作れるか。
    new: bool
    # ボードの経路（`--via board`）で取り込めるか。
    board: bool

    def matches(self, key: str) -> bool:
        return any(re.fullmatch(p, key) for p in self.patterns)


_NAME = r"[^/]+"
_DEEP = r"(?:[^/]+/)*[^/]+"
_LAYER_FILES = r"(?:rules|phases|risks)\.yml"

KINDS: tuple[Kind, ...] = (
    Kind(
        KIND_TEMPLATE,
        (
            ".ccnavi/config/common/templates/<名前>.yml",
            ".ccnavi/config/project/templates/<名前>.yml",
            "projects/<名前>/.ccnavi/config/project/templates/<名前>.yml",
        ),
        (
            rf"\.ccnavi/config/common/templates/{_NAME}\.yml",
            rf"\.ccnavi/config/project/templates/{_NAME}\.yml",
            rf"projects/{_NAME}/\.ccnavi/config/project/templates/{_NAME}\.yml",
        ),
        new=True,
        board=True,
    ),
    Kind(
        KIND_MESSAGES,
        (".ccnavi/messages/<名前>.yml",),
        (rf"\.ccnavi/messages/{_NAME}\.yml",),
        new=True,
        board=False,
    ),
    Kind(
        KIND_LAYER_CONFIG,
        (
            ".ccnavi/config/{common,project}/{rules,phases,risks}.yml",
            "projects/<名前>/.ccnavi/config/project/{rules,phases,risks}.yml",
        ),
        (
            rf"\.ccnavi/config/(?:common|project)/{_LAYER_FILES}",
            rf"projects/{_NAME}/\.ccnavi/config/project/{_LAYER_FILES}",
        ),
        new=False,
        board=True,
    ),
    Kind(
        KIND_DOCS,
        (".ccnavi/docs/**/*.md", ".ccnavi/CCNAVI.md"),
        (
            rf"\.ccnavi/docs/{_DEEP}\.md",
            r"\.ccnavi/ccnavi\.md",
        ),
        new=False,
        board=True,
    ),
    Kind(
        KIND_DEFENSE,
        (
            ".ccnavi/scripts/**",
            ".claude/hooks/**",
            ".claude/settings.json",
            ".claude/settings.local.json",
            ".claude/skills/**/*.md",
        ),
        (
            rf"\.ccnavi/scripts/{_DEEP}",
            rf"\.claude/hooks/{_DEEP}",
            r"\.claude/settings\.json",
            r"\.claude/settings\.local\.json",
            # スキルの frontmatter は、使えるツール（allowed-tools）や hook を持てるので、
            # 権限を広げる記述になりうる。docs ではなく防御として、ターミナルでだけ取り込む。
            rf"\.claude/skills/{_DEEP}\.md",
        ),
        new=False,
        board=False,
    ),
)

_LAYER_KIND_OF_FILE = {name: kind for kind, name in settings.LAYER_FILE_NAMES.items()}


def fold_part(name: str) -> str:
    """パスの 1 要素を比べるための形（NFC と fold）。開く表記には使わない。"""
    return unicodedata.normalize("NFC", pathcase.fold(unicodedata.normalize("NFC", name)))


def key_of(rel: str) -> str:
    """相対パス（"/" 区切り）を、表に適用して比べるための形にする。"""
    return "/".join(fold_part(part) for part in rel.split("/"))


def kind_of(key: str) -> Kind | None:
    """そろえた相対パスがヒットする種類。どれにもヒットしなければ None（対象外）。"""
    for kind in KINDS:
        if kind.matches(key):
            return kind
    return None


def kind(kind_id: str) -> Kind:
    for row in KINDS:
        if row.id == kind_id:
            return row
    raise KeyError(kind_id)


def handoff_dir(tree_root: str) -> str:
    """このツリーの下書きの置き場（絶対）。"""
    return os.path.join(tree_root, *HANDOFF_PARTS)


def draft_rel(target_rel: str) -> str:
    """取り込み先の相対パスに対応する、下書きのツリーからの相対パス（階層名は読み替える）。"""
    return f"{HANDOFF_REL}/{handoff_names.to_draft(target_rel)}"


def _sha(raw: bytes | None) -> str:
    return hashlib.sha256(raw).hexdigest() if raw is not None else "-"


def _framed(value: str) -> bytes:
    data = value.encode("utf-8")
    return str(len(data)).encode("ascii") + b":" + data


def _line(text: object) -> str:
    return " ".join(str(text).split())


# ---- 下書きを探す


@dataclass
class Found:
    """置き場で見つけた下書き 1 本（まだ読んでいない）。"""

    # 取り込み先の相対パス（読み替えを戻した形）。
    rel: str
    key: str
    path: str
    # 置き場の中の相対パス（置いてある綴りのまま）。
    drel: str = ""


# 対象外にした理由。拡張の「取り込み待ち」画面に名前と並べて出す。
WHY_NOT_ALLOWED = "not-allowed"
WHY_LINKED_DIR = "linked-dir"


def walk(tree_root: str) -> tuple[list[Found], list[tuple[str, str]], str]:
    """置き場の下書き（ふつうのファイルとリンク）と、対象外の（置き場からの相対パス, 理由）と、
    置き場そのものの問題。

    リンクのディレクトリの中へは降りない（降りないので、その中の下書きは取り込まれない）。
    書き込みの途中で残った一時ファイル（一時名で書いてから差し替える途中のもの）は下書きに数えない。
    `rel` と `key` は、読み替えを戻した取り込み先のパス。
    """
    base = handoff_dir(tree_root)
    if not os.path.lexists(base):
        return [], [], ""
    if pathsafe.has_link(tree_root, base) or not os.path.isdir(base):
        return [], [], f"{HANDOFF_REL}/ がリンクか、ディレクトリではないので読めない"
    found: list[Found] = []
    ignored: list[tuple[str, str]] = []
    for parent, dirs, files in os.walk(base, followlinks=False):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(parent, name)
            drel = os.path.relpath(full, base).replace(os.sep, "/")
            if fsio.is_temp_name(name):
                continue
            rel = handoff_names.from_draft(drel)
            key = key_of(rel)
            if kind_of(key) is None:
                ignored.append((drel, WHY_NOT_ALLOWED))
                continue
            found.append(Found(rel, key, full, drel))
        # リンクのディレクトリ（`os.walk` は降りない）は、中を読まない代わりに対象外として数える。
        for name in dirs:
            full = os.path.join(parent, name)
            if os.path.islink(full):
                ignored.append((os.path.relpath(full, base).replace(os.sep, "/"), WHY_LINKED_DIR))
    found.sort(key=lambda f: (f.key, f.rel))
    return found, ignored, ""


def spelled(tree_root: str, rel: str) -> tuple[str, str]:
    """取り込み先の絶対パスを、あるディレクトリとファイルの実際の表記で組む。(パス, 断る理由)。

    大文字小文字・NFC/NFD だけが違う名前は同じものとして扱う。同じ形に揃う名前が 2 つ並んで
    いれば、どちらか決められないので断る。
    """
    current = tree_root
    parts = rel.split("/")
    for i, part in enumerate(parts):
        try:
            names = os.listdir(current)
        except FileNotFoundError:
            return os.path.join(current, *parts[i:]), ""
        except NotADirectoryError:
            where = _line(parts[i - 1] if i else current)
            return "", f"取り込み先の途中（{where}）がディレクトリではない"
        except OSError as exc:
            return "", f"取り込み先の置き場を読めない（{_line(exc.strerror or exc)}）"
        want = fold_part(part)
        hits = sorted(n for n in names if fold_part(n) == want)
        if len(hits) > 1:
            return "", (
                "取り込み先に、大文字小文字か NFC/NFD だけが違う名前が並んでいる"
                f"（{', '.join(hits)}）"
            )
        current = os.path.join(current, hits[0] if hits else part)
        if not hits:
            return os.path.join(current, *parts[i + 1 :]), ""
    return current, ""


# ---- 1 本の下書き


@dataclass
class Entry:
    rel: str
    key: str
    kind: Kind
    draft: str
    # 置き場の中の相対パス（置いてある綴りのまま）。
    drel: str = ""
    target: str = ""
    raw: bytes | None = None
    current: bytes | None = None
    # テンプレートとレイヤーの設定の、レイヤーの名前（common / self / プロジェクト）。
    layer: str = ""
    # レイヤーの設定の種類（settings.KIND_*）。
    config_kind: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    diff: str = ""
    selected: bool = False
    # 種類はボードで取り込めるが、中身が防御の記述を動かすので、ターミナルに限る理由
    # （`rules.yml` の `guard-*` の変更や deny の削除。_defense_change）。空なら種類のまま。
    defense: str = ""

    @property
    def board(self) -> bool:
        """ボードの経路（`--via board`）で取り込めるか。"""
        return self.kind.board and not self.defense

    @property
    def draft_rel(self) -> str:
        """下書きのツリーからの相対パス。置いてある綴りのまま（無ければ表から組む）。"""
        return f"{HANDOFF_REL}/{self.drel}" if self.drel else draft_rel(self.rel)

    @property
    def state(self) -> str:
        if self.errors:
            return STATE_REJECTED
        if self.current is None:
            return STATE_NEW
        if self.current == self.raw:
            return STATE_UNCHANGED
        return STATE_CHANGED

    def record(self) -> bytes:
        """照合の値の 1 件分（長さの前置きつき）。"""
        return b"".join(
            _framed(v)
            for v in (
                self.key,
                self.kind.id,
                _sha(self.raw),
                _sha(self.current),
            )
        )

    @property
    def entry_sha(self) -> str:
        return hashlib.sha256(self.record()).hexdigest()


def expect_of(entries: list[Entry]) -> str:
    """選んだ対象の照合の値。パスの並び順で記録を連結した SHA-256。"""
    ordered = sorted(entries, key=lambda e: e.key)
    return hashlib.sha256(b"".join(e.record() for e in ordered)).hexdigest()


# ---- 下見


@dataclass
class Plan:
    tree: str
    entries: list[Entry] = field(default_factory=list)
    ignored: int = 0
    ignored_paths: list[tuple[str, str]] = field(default_factory=list)
    # 全体を止める誤り（置き場がリンク、引数のパスが読めないなど）。
    error: str = ""

    @property
    def selected(self) -> list[Entry]:
        return [e for e in self.entries if e.selected]

    @property
    def expect(self) -> str:
        return expect_of(self.selected)

    @property
    def ok(self) -> bool:
        return not self.error and all(e.state != STATE_REJECTED for e in self.selected)


def wanted_key(arg: str) -> tuple[str, str]:
    """引数のパスを、比べる形にする。(そろえた形, 断る理由)。"""
    text = arg.replace("\\", "/").strip()
    if not text:
        return "", "空のパスは指せない"
    if text.startswith("/") or os.path.isabs(text) or re.match(r"^[A-Za-z]:", text):
        return "", f"{_line(arg)} は絶対パス。ツリーからの相対パスで指す"
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts) or not parts:
        return "", f"{_line(arg)} は `..` を含むか空。ツリーからの相対パスで指す"
    key = key_of("/".join(parts))
    prefix = key_of(HANDOFF_REL) + "/"
    if key.startswith(prefix):
        # 置き場の中のパス（下書きの綴り）は、読み替えを戻して取り込み先のパスにする。
        key = key_of(handoff_names.from_draft(key[len(prefix) :]))
    return key, ""


def prepare(conf: settings.Settings, root: str, wanted: list[str] | None = None) -> Plan:
    """下書きを読み、検査し、取り込み先との差分を作る。何も書かない。

    `wanted` は引数で指したパス（ツリーからの相対）。None なら、表にヒットして `unchanged` でない
    もの全部を選ぶ。

    処理フロー:
    1. `walk` で下書きを集める。拒否があれば、その理由を入れて返す。
    2. 同じ取り込み先に落ちる下書きが複数ないかを数える。複数なら、どちらにも理由を足す。
    3. 各下書きを `_read` で読み、`Entry` にする。
    4. `wanted` が None なら、未変更でないものを選ぶ。指定があれば `wanted_key` で引き、
       表に無ければ理由を入れて返す。
    5. `_check_contents` で中身を検査し、`_make_diff` で取り込み先との差分を作る。
    """
    plan = Plan(tree=root)
    found, plan.ignored_paths, refused = walk(root)
    plan.ignored = len(plan.ignored_paths)
    if refused:
        plan.error = refused
        return plan
    keys: dict[str, int] = {}
    for item in found:
        keys[item.key] = keys.get(item.key, 0) + 1
    for item in found:
        entry = Entry(item.rel, item.key, kind_of(item.key) or KINDS[0], item.path, item.drel)
        if keys[item.key] > 1:
            entry.errors.append(
                "同じ取り込み先に落ちる下書きが複数ある（大文字小文字か NFC/NFD だけが違う）。"
                "1 本に揃える"
            )
        _read(conf, root, entry)
        plan.entries.append(entry)
    if wanted is None:
        for entry in plan.entries:
            entry.selected = entry.state != STATE_UNCHANGED
    else:
        index = {e.key: e for e in plan.entries}
        for arg in wanted:
            key, why = wanted_key(arg)
            if why:
                plan.error = why
                return plan
            hit = index.get(key)
            if hit is None:
                plan.error = (
                    f"{_line(arg)} の下書きが {HANDOFF_REL}/ に無いか、"
                    "取り込める種類の表にヒットしない"
                )
                return plan
            hit.selected = True
    _check_contents(conf, root, plan)
    for entry in plan.entries:
        _make_diff(entry)
    return plan


def _read(conf: settings.Settings, root: str, entry: Entry) -> None:
    """下書き・取り込み先を読み、形の上の断る理由を足す。中身の検査は別。

    処理フロー:
    1. パスの要素に `.git` で始まるものがあれば断る。
    2. 下書きを読み、NUL を含まないか、UTF-8 として読めるかを見る。
    3. `_locate_layer` で下書きのレイヤーを求め、`spelled` で取り込み先を決める。
    4. 取り込み先の途中にリンクがあるか、ツリーの外を指すなら断る。
    5. 取り込み先があれば読む。無いのに種類が新しく作れないものなら断る。
    """
    parts = entry.key.split("/")
    if any(part.startswith(".git") for part in parts):
        entry.errors.append("パスの要素に `.git` で始まるものがある。git の置き場には取り込めない")
    raw, why = flow.read_bytes(entry.draft, root)
    if raw is None:
        entry.errors.append(f"下書きを読めない（{why}）")
    else:
        entry.raw = raw
        if b"\x00" in raw:
            entry.errors.append("バイナリ（NUL を含む）は取り込めない")
        else:
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError:
                entry.errors.append("UTF-8 として読めない")
    _locate_layer(conf, root, entry)
    target, why = spelled(root, entry.rel)
    if why:
        entry.errors.append(why)
        return
    entry.target = target
    if not _inside(root, target) or pathsafe.has_link(root, target):
        entry.errors.append("取り込み先の途中にリンクがあるか、ツリーの外を指すので書かない")
        return
    if os.path.lexists(target):
        current, why = flow.read_bytes(target, root)
        if current is None:
            entry.errors.append(f"取り込み先には書かない（{why}）")
            return
        entry.current = current
    elif not entry.kind.new:
        entry.errors.append(
            f"取り込み先 {entry.rel} が無い。この種類（{entry.kind.id}）は新しく作れない"
        )


def _inside(root: str, path: str) -> bool:
    base = pathcase.fold(os.path.normpath(os.path.abspath(root)))
    full = pathcase.fold(os.path.normpath(os.path.abspath(path)))
    return full.startswith(base + os.sep)


def _locate_layer(conf: settings.Settings, root: str, entry: Entry) -> None:
    """テンプレートとレイヤーの設定の、レイヤーを決める。読まれない置き場なら断る。"""
    # 文面ファイルはレイヤーを持たない（`projects/<名前>/.ccnavi/messages/` は KINDS が許さない）。
    if entry.kind.id not in (KIND_TEMPLATE, KIND_LAYER_CONFIG):
        return
    parts = entry.key.split("/")
    if parts[0] == "projects":
        name = entry.rel.split("/")[1]
        layers = {fold_part(view.name): view for view in ruleload.layers(conf, root)}
        view = layers.get(fold_part(name))
        if view is None or view.name == ruleload.LAYER_SELF:
            entry.errors.append(
                f"projects/{_line(name)} はレイヤーとして読まれるプロジェクトではない"
                "（取り込んでも使われない）"
            )
            return
        entry.layer = view.name
        home_key = key_of(os.path.relpath(view.home, root).replace(os.sep, "/"))
        if home_key != "/".join(parts[:2]):
            entry.errors.append(f"projects/{_line(name)} のレイヤーの置き場がこのツリーの外にある")
    elif parts[2] == "common":
        entry.layer = settings.LAYER_COMMON
        if not plan_templates.common_writable(conf, root):
            entry.errors.append(
                "このツリーの `.ccnavi/config/common/` は共通レイヤーのコピー（ミラー）なので"
                "書かない。"
                "ワークスペースのツリーで取り込む"
            )
    else:
        entry.layer = settings.LAYER_SELF
    if entry.kind.id == KIND_LAYER_CONFIG:
        entry.config_kind = _LAYER_KIND_OF_FILE.get(parts[-1], "")


# ---- 中身の検査


def _overlay(conf: settings.Settings, entries: list[Entry]) -> settings.Settings:
    """取り込んだあとの状態の設定。レイヤーの設定の置き場を、下書きへ差し替える。"""
    out = dataclasses.replace(
        conf,
        project_rules_files=dict(conf.project_rules_files),
        project_phases_files=dict(conf.project_phases_files),
        project_risk_files=dict(conf.project_risk_files),
    )
    for entry in entries:
        if entry.kind.id != KIND_LAYER_CONFIG or entry.raw is None or not entry.config_kind:
            continue
        if entry.layer == settings.LAYER_COMMON:
            attr = {
                settings.KIND_RULES: "rules",
                settings.KIND_PHASES: "phases",
                settings.KIND_RISK: "risk",
            }[entry.config_kind]
            setattr(out, attr, entry.draft)
        elif entry.layer:
            swaps = {
                settings.KIND_RULES: out.project_rules_files,
                settings.KIND_PHASES: out.project_phases_files,
                settings.KIND_RISK: out.project_risk_files,
            }[entry.config_kind]
            swaps[entry.layer] = entry.draft
    return out


def _said(entry: Entry, problems: list[Problem]) -> None:
    for p in problems:
        text = f"{p.rule}: {p.detail}" if p.rule else p.detail
        if p.severity == SEVERITY_ERROR:
            entry.errors.append(text)
        elif p.severity == SEVERITY_WARN:
            entry.warnings.append(text)


def _check_contents(conf: settings.Settings, root: str, plan: Plan) -> None:
    """種類ごとの中身の検査。同じ回に選んだ対象は、取り込んだあとの状態で検査する。"""
    readable = [e for e in plan.entries if e.raw is not None and not e.errors]
    chosen = [e for e in readable if e.selected]
    candidates = None
    for entry in readable:
        together = chosen if entry.selected else [*chosen, entry]
        if entry.kind.id == KIND_TEMPLATE:
            if candidates is None:
                candidates = flow.catalog(root)
            _check_template(conf, root, entry, together, candidates)
        elif entry.kind.id == KIND_LAYER_CONFIG and entry.config_kind:
            overlay = _overlay(conf, together)
            _said(entry, config_check.file_problems(overlay, root, entry.config_kind, entry.layer))
            if entry.config_kind == settings.KIND_RULES:
                entry.defense = _defense_change(entry.current, entry.raw)
        elif entry.kind.id == KIND_MESSAGES:
            _check_messages(root, entry)
        elif entry.kind.id == KIND_DEFENSE:
            _check_defense(entry)


def _check_messages(root: str, entry: Entry) -> None:
    """文面ファイルの下書き。取り込んだあとに検査（`--lint`）で落ちる文は取り込ませない。

    欄・差し込みの名前・`次:` の数などを外した文で、拒否や促しの型を弱められないようにする。
    """
    try:
        text = (entry.raw or b"").decode("utf-8")
    except UnicodeDecodeError:
        entry.errors.append("UTF-8 として読めない")
        return
    _, problems = texts.check_text(text, entry.draft or entry.rel)
    _said(entry, problems)


# ルールの id のうち、防御の記述の印。レイヤーの rules.yml に利用者が書く防御のルールの印。
# ccnavi 自身の保護は組み込み（`builtin:guard-*`）にある。
DEFENSE_ID_PREFIX = "guard-"
_DECISIONS = ("deny", "ask", "allow")


def _rule_rows(raw: bytes | None) -> dict[tuple[str, str], tuple[str, ...]] | None:
    """ルールファイルの各ルールを、(判定, id) → 中身（キーを並べた JSON）の組にする。

    id の無いルールは中身そのものをキーにする。読めなければ None。
    """
    if raw is None:
        return {}
    try:
        data = yamlread.safe_load(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, yaml.YAMLError):
        return None
    if data is None:
        return {}
    if not isinstance(data, dict):
        return None
    rows: dict[tuple[str, str], list[str]] = {}
    for decision in _DECISIONS:
        items = data.get(decision) or []
        if not isinstance(items, list):
            return None
        for item in items:
            body = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
            rule_id = item.get("id") if isinstance(item, dict) else None
            key = (decision, str(rule_id) if rule_id else "#" + body)
            rows.setdefault(key, []).append(body)
    return {key: tuple(sorted(bodies)) for key, bodies in rows.items()}


def _defense_change(current: bytes | None, raw: bytes | None) -> str:
    """`rules.yml` の下書きが防御の記述を動かすなら、ターミナルに限る理由。動かさなければ空。

    防御の記述とみなすのは、`guard-` で始まる id のルール（足す・消す・判定を移す・`except` を
    含む中身を変える）と、今ある deny（消す・中身を変える）。deny を足すのは止める側への変更なので
    数えない。どちらかが読めなければ、確かめられないのでターミナルに限る。
    """
    before = _rule_rows(current)
    after = _rule_rows(raw)
    if before is None or after is None:
        return "ルールファイルとして読めず、防御の記述が変わるかを確かめられない"
    moved: list[str] = []
    for key in sorted(set(before) | set(after)):
        if before.get(key) == after.get(key):
            continue
        decision, rule_id = key
        shown = "id の無いルール" if rule_id.startswith("#") else rule_id
        if rule_id.casefold().startswith(DEFENSE_ID_PREFIX):
            moved.append(f"{decision} の {shown}")
        elif decision == "deny" and key in before:
            moved.append(f"deny の {shown}（消す・変える）")
    if not moved:
        return ""
    return "防御の記述を動かす（" + "、".join(moved[:5]) + ("ほか" if len(moved) > 5 else "") + "）"


def _check_template(
    conf: settings.Settings,
    root: str,
    entry: Entry,
    together: list[Entry],
    candidates: dict,
) -> None:
    read = plan_templates.read_entry(entry.layer or settings.LAYER_SELF, root, entry.draft)
    if read.raw != entry.raw:
        entry.errors.append("下書きを読んでいるあいだに書き換わりました。もう一度見てください")
        return
    _said(entry, read.notes)
    template = read.template
    if template is None:
        return
    types = plan_templates.types_for(_overlay(conf, together), root, entry.layer)
    _said(entry, plan_templates.check(template, types, candidates))
    # コメントなど、読んだ中身に出ない場所の文字も見る（CRLF の改行は差分が見せる）。
    hidden = plan_templates.hidden_problem(
        (entry.raw or b"").decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
    )
    if hidden and not any(hidden[:12] in e for e in entry.errors):
        entry.errors.append(hidden)
    _template_collisions(conf, root, entry, template, together)


def _template_collisions(
    conf: settings.Settings,
    root: str,
    entry: Entry,
    template: plan_templates.Template,
    together: list[Entry],
) -> None:
    """取り込むと、共通とレイヤーに同名で中身の違うものが並ぶ形になるなら断る。

    統合結果はその形のレイヤーの側を使わない error にする（`plan_templates.merge`）。比べる相手は、
    同じ回に取り込む下書き（取り込んだあとの状態）を優先し、無ければ今の置き場のもの。
    """
    mine = plan_templates.dump(template)
    name = fold_part(template.name)
    others: dict[tuple[str, str], tuple[str, str]] = {}
    scanned = plan_templates.scan(conf, root)
    for view in [scanned.common, *scanned.layers]:
        for other in view.entries:
            if other.template is not None:
                others[(view.name, fold_part(other.name))] = (
                    other.rel,
                    plan_templates.dump(other.template),
                )
    for other in together:
        if other is entry or other.kind.id != KIND_TEMPLATE or other.raw is None:
            continue
        parsed, _ = plan_templates.parse_template(other.raw)
        if parsed is not None:
            others[(other.layer, fold_part(parsed.name))] = (
                other.rel,
                plan_templates.dump(parsed),
            )
    if entry.layer == settings.LAYER_COMMON:
        rivals = [
            (layer, rel, text)
            for (layer, other_name), (rel, text) in others.items()
            if layer != settings.LAYER_COMMON and other_name == name
        ]
        for layer, rel, text in rivals:
            if text != mine:
                entry.errors.append(
                    f"{layer} に同名で中身の違う {rel} がある。共通へ取り込むと {layer} の側が"
                    f"使われなくなる。先に {layer} の側を直す"
                )
        return
    rival = others.get((settings.LAYER_COMMON, name))
    if rival is not None and rival[1] != mine:
        entry.errors.append(
            f"共通に同名で中身の違う {rival[0]} がある。取り込んでも {entry.layer} の側は"
            "使われない。別の名前にする"
        )


def _check_defense(entry: Entry) -> None:
    raw = entry.raw or b""
    name = entry.key.rsplit("/", 1)[-1]
    if name.endswith(".sh"):
        if b"\r" in raw:
            entry.errors.append(
                "CR の改行（CRLF）がある。sh は CRLF の行を読めず、hook とラッパーが"
                "全部止まる。LF に直す"
            )
            return
        why = _sh_syntax(entry.draft)
        if why:
            entry.errors.append(why)
    elif name in ("settings.json", "settings.local.json"):
        try:
            json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            entry.errors.append(f"JSON として読めない（{_line(exc)}）")


def _sh_syntax(path: str) -> str:
    """`sh -n` で読めるか。読めなければ理由。sh が無い機械では確かめられないので断る。"""
    shell = shutil.which("sh")
    if not shell:
        return "sh が見つからないので、文法を確かめられない（確かめられないものは取り込まない）"
    try:
        done = subprocess.run(
            [shell, "-n", path],
            capture_output=True,
            text=True,
            timeout=SH_CHECK_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"sh -n を実行できない（{_line(exc)}）"
    if done.returncode != 0:
        said = _line(done.stderr or done.stdout)[:400]
        return f"sh -n が文法の誤りを言った（{said}）"
    return ""


# ---- 差分


def _visible(ch: str) -> str:
    if plan_templates.hidden_chars(ch) or unicodedata.combining(ch):
        return plan_templates.escape_char(ch)
    return ch


def visible_lines(raw: bytes | None) -> list[str]:
    """差分に出す行。改行コード・BOM・制御文字・ゼロ幅の文字・結合文字は `\\uXXXX` にする。

    末尾に改行が無ければ、そう言う行を足す（改行だけの違いも差分に出す）。
    """
    if raw is None:
        return []
    text = raw.decode("utf-8", errors="replace")
    lines = text.split("\n")
    tail = lines.pop()
    out = ["".join(_visible(ch) for ch in line) for line in lines]
    if tail:
        out.append("".join(_visible(ch) for ch in tail))
        out.append(NO_EOL)
    return out


def _make_diff(entry: Entry) -> None:
    if entry.raw is None or entry.current == entry.raw:
        return
    old = visible_lines(entry.current)
    new = visible_lines(entry.raw)
    lines = list(
        difflib.unified_diff(
            old,
            new,
            fromfile=f"a/{entry.rel}" if entry.current is not None else "/dev/null",
            tofile=f"b/{entry.rel}",
            n=DIFF_CONTEXT,
            lineterm="",
        )
    )
    if not lines:
        entry.errors.append(
            "差分が見えないのに、バイト列が取り込み先と違う。見えない違いは取り込まない"
        )
        return
    entry.diff = "\n".join(lines)


# ---- 取り込む


@dataclass
class Applied:
    ok: bool
    error: str = ""
    applied: list[Entry] = field(default_factory=list)
    removed_drafts: list[str] = field(default_factory=list)
    kept_drafts: list[tuple[str, str]] = field(default_factory=list)


def board_refused(plan: Plan) -> str:
    """ボードの経路で取り込めない種類を選んでいれば、その断り文。"""
    blocked = [e for e in plan.selected if not e.board]
    if not blocked:
        return ""
    paths = ", ".join(e.rel for e in blocked)
    first = blocked[0]
    why = f"{first.kind.id} の種類で" if not first.kind.board else f"{first.defense}ので"
    return (
        f"{paths} は {why}、ボードからは取り込めない。"
        "ターミナルで `ccnavi-user-handoff.sh <パス> --yes --expect <ハッシュ>` を打つ"
    )


def not_ok_reason(plan: Plan) -> str:
    """選んだ対象に error があるときの、名指しの文。"""
    if plan.error:
        return plan.error
    bad = [e for e in plan.selected if e.state == STATE_REJECTED]
    named = "、".join(f"{e.rel}（{e.errors[0]}）" for e in bad[:10])
    return f"選んだ対象に error がある: {named}。何も書いていません"


def apply(plan: Plan, expect: str, via_board: bool = False) -> Applied:
    """選んだ対象を書く。`plan` が通り、`expect` が今の照合の値と同じときだけ書く。

    処理フロー:
    1. 計画の理由、対象の有無、`via_board` の拒否、`plan.ok` を順に確かめる。
    2. `expect` が無いか、今の `plan.expect` と違えば、何も書かずに返す。
    3. 選んだ対象を `_write_one` で順に書く。途中で失敗したら `_roll_back` で書いたものを戻す。
    4. 全部書けたら、各対象の下書きを `_clean_draft` で片付ける。
    """
    if plan.error:
        return Applied(False, plan.error)
    chosen = sorted(plan.selected, key=lambda e: e.key)
    if not chosen:
        return Applied(False, "取り込む対象がありません")
    if via_board:
        refused = board_refused(plan)
        if refused:
            return Applied(False, refused)
    if not plan.ok:
        return Applied(False, not_ok_reason(plan))
    if not expect:
        return Applied(
            False,
            "--yes には --expect <ハッシュ>（見るだけの形が示す値）を付けてください",
        )
    if expect != plan.expect:
        return Applied(
            False,
            "--expect が今の対象のハッシュと違う（見せたあとに、下書きか取り込み先が変わった）。"
            f"何も書いていません。今のハッシュ: {plan.expect}",
        )
    written: list[Entry] = []
    failure = ""
    for entry in chosen:
        if entry.state == STATE_UNCHANGED:
            continue
        failure = _write_one(plan.tree, entry)
        if failure:
            break
        written.append(entry)
    if failure:
        undone = _roll_back(written)
        text = f"{failure}。書いたものは元に戻しました。何も取り込んでいません"
        if undone:
            text = (
                f"{failure}。書いたもののうち、元に戻せなかったものがある: {', '.join(undone)}。"
                "手で確かめてください"
            )
        return Applied(False, text)
    done = Applied(True, applied=written)
    for entry in chosen:
        _clean_draft(plan.tree, entry, done)
    return done


def _write_one(root: str, entry: Entry) -> str:
    """1 本を書く。書く直前にリンクと取り込み先のハッシュを確かめ直す。失敗なら理由。"""
    if pathsafe.has_link(root, entry.target) or not _inside(root, entry.target):
        return f"{entry.rel}: 取り込み先の途中がリンクに変わった"
    if os.path.lexists(entry.target):
        now, why = flow.read_bytes(entry.target, root)
        if now is None:
            return f"{entry.rel}: 取り込み先には書かない（{why}）"
    else:
        now = None
    if _sha(now) != _sha(entry.current):
        return f"{entry.rel}: 見せたあとに取り込み先が変わった"
    draft_now, why = flow.read_bytes(entry.draft, root)
    if draft_now != entry.raw:
        return f"{entry.rel}: 見せたあとに下書きが変わった"
    assert entry.raw is not None
    if entry.current is None:
        err = fsio.write_new_durable(entry.target, entry.raw)
    else:
        err = fsio.write_bytes_atomic(entry.target, entry.raw)
    if err:
        return f"{entry.rel} を書けない（{_line(err)}）"
    back, why = flow.read_bytes(entry.target, root)
    if back != entry.raw:
        return f"{entry.rel} を書いたが、読み直すと中身が違う（{why or '内容'}）"
    return ""


def _roll_back(written: list[Entry]) -> list[str]:
    """書いたものを書く前のバイト列に戻す。戻せなかったものの相対パス。"""
    failed: list[str] = []
    for entry in reversed(written):
        if entry.current is None:
            err = fsio.unlink(entry.target)
        else:
            err = fsio.write_bytes_atomic(entry.target, entry.current)
        if err:
            failed.append(entry.rel)
    return failed


def _clean_draft(root: str, entry: Entry, done: Applied) -> None:
    """下書きの今のバイト列が取り込んだものと同じときだけ、下書きを消す。

    処理フロー:
    1. 下書きを読み直し、取り込んだときの `entry.raw` と同じかを見る。違えば消さずに残す理由を積む。
    2. 下書きを `fsio.unlink` で消し、`removed_drafts` に入れる。
    """
    now, why = flow.read_bytes(entry.draft, root)
    if now is None:
        done.kept_drafts.append((entry.draft_rel, f"下書きを読めなかったので消していない（{why}）"))
        return
    if now != entry.raw:
        done.kept_drafts.append(
            (entry.draft_rel, "取り込んだあとに下書きが書き直されているので消していない")
        )
        return
    err = fsio.unlink(entry.draft)
    if err:
        done.kept_drafts.append((entry.draft_rel, f"下書きを消せない（{_line(err)}）"))
        return
    done.removed_drafts.append(entry.draft_rel)


# ---- ready の止め


def pending(tree_root: str) -> list[str]:
    """表にヒットする下書きのうち、取り込み先と中身が違うもの（取り込んでいないもの）の相対パス。

    `ready` は `wip/` を消すので、取り込んでいない下書きが残っていれば止める。読めない下書きと
    取り込み先も「違う」に数える（止める側）。共通の置き場がミラーかどうかは見ない。
    """
    found, _, refused = walk(tree_root)
    if refused:
        return [HANDOFF_REL + "/"]
    out: list[str] = []
    for item in found:
        raw, _ = flow.read_bytes(item.path, tree_root)
        target, why = spelled(tree_root, item.rel)
        current = None
        if not why and os.path.lexists(target):
            current, _ = flow.read_bytes(target, tree_root)
        if raw is None or why or current != raw:
            out.append(f"{HANDOFF_REL}/{item.drel}")
    return out


def _draft_files(tree_root: str) -> list[str]:
    """置き場の下にあるファイル（リンクを含む。ディレクトリでないもの）を、置き場からの相対パスで返す。"""
    base = handoff_dir(tree_root)
    out: list[str] = []
    for parent, dirs, files in os.walk(base, followlinks=False):
        dirs.sort()
        for name in sorted(files) + sorted(
            n for n in dirs if os.path.islink(os.path.join(parent, n))
        ):
            out.append(os.path.relpath(os.path.join(parent, name), base).replace(os.sep, "/"))
    return out


def _holds(tree_root: str, start: str, sources: list[str], raw: bytes | None) -> bool | None:
    """起点の木のいずれかのパスの中身が、この中身と同じかを返す。起点から読めなければ None。"""
    same = False
    for source in sources:
        held, readable = gitcmd.blob(tree_root, start, source, timeout=30.0)
        if not readable:
            return None
        same = same or (raw is not None and held == raw)
    return same


def survey(tree_root: str, start: str) -> tuple[list[str], list[str]] | None:
    """置き場の下書きを、`<起点>` の木の元のパスと突き合わせる。

    結果は `(元と違う写し, 元の無い下書き)`。`ccnavi-git.sh integrate` が、
    取り込み結果に含まれない下書きを見分けるのに使う。
    置き場を除いた下書きの相対パス（階層名の読み替えを戻したもの）を、`<起点>` の木のパスと
    `key_of` で比べる（大文字小文字・NFC/NFD の違いは同じものとして扱う）。比べるのは見つける
    ためだけで、元を読むときは起点の木の表記を使う。

    - 起点の木に同じ下書きがあり中身も同じ: ブランチが持ち込んだものではない。
      どちらの一覧にも入れない
    - 元が起点の木に無い: 元の無い下書き（新規のメモ・報告）
    - 元があり中身が同じ: 編集していない写し。どちらの一覧にも入れない
    - 元があり中身が違う、または読めない（リンクなど）: 元と違う写し（取り込んでいない）

    起点の木を読めなければ None（呼ぶ側は止める）。パスは置き場つきで、置き場の表記のまま。
    """
    if not os.path.isdir(handoff_dir(tree_root)):
        return [], []
    listed = gitcmd.run(tree_root, ["ls-tree", "-r", "-z", "--name-only", start], timeout=30.0)
    if not listed.ok:
        return None
    origins: dict[str, list[str]] = {}
    for name in listed.out.split("\0"):
        if name:
            origins.setdefault(key_of(name), []).append(name)
    foreign: list[str] = []
    notes: list[str] = []
    for rel in _draft_files(tree_root):
        shown = f"{HANDOFF_REL}/{rel}"
        raw, _ = flow.read_bytes(os.path.join(handoff_dir(tree_root), *rel.split("/")), tree_root)
        own = origins.get(key_of(shown))
        if own and _holds(tree_root, start, own, raw):
            continue
        sources = origins.get(key_of(handoff_names.from_draft(rel)))
        if not sources:
            notes.append(shown)
        elif not _holds(tree_root, start, sources, raw):
            foreign.append(shown)
    return foreign, notes


# ---- JSON


def entry_json(entry: Entry) -> dict:
    return {
        "path": entry.rel,
        "draft": entry.draft_rel,
        "kind": entry.kind.id,
        "state": entry.state,
        "board": entry.board,
        "selected": entry.selected,
        "errors": list(entry.errors),
        "warnings": list(entry.warnings),
        "diff": entry.diff,
        "entry_sha": entry.entry_sha,
    }


def plan_json(plan: Plan) -> dict:
    out: dict = {
        "version": JSON_VERSION,
        "tree": plan.tree,
        "ignored": plan.ignored,
        "ignored_paths": [{"path": path, "why": why} for path, why in plan.ignored_paths],
        "entries": [entry_json(e) for e in plan.entries],
        "expect": plan.expect,
        "ok": plan.ok,
    }
    if plan.error:
        out["error"] = plan.error
    return out


def applied_json(done: Applied) -> dict:
    if not done.ok:
        return {"version": JSON_VERSION, "ok": False, "error": done.error}
    return {
        "version": JSON_VERSION,
        "ok": True,
        "applied": [{"path": e.rel, "kind": e.kind.id} for e in done.applied],
        "removed_drafts": list(done.removed_drafts),
        "kept_drafts": [{"path": p, "reason": r} for p, r in done.kept_drafts],
    }
