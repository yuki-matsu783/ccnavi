"""作業・レビュー計画のテンプレート（読む側）。

テンプレートは、1 つの親の作業・レビュー計画（全体計画 `plan:` とフィードバック計画 `feedback:` の
ステップと `after`）と、各子のフローの本文を 1 ファイルにまとめたもの。完了した作業の形を、
次の作業で使い回すために置く。このモジュールは読む・足す・検査するだけで、書かない（取り込み・下書き・使う側は別）。

## 置き場と 2 層の統合結果

    共通        <ccnavi ディレクトリ>/config/common/templates/<名前>.yml
    プロジェクト <ccnavi ディレクトリ>/config/project/templates/<名前>.yml

置き場は `settings.common_templates_dir` / `settings.layer_templates_dir`。使うのは共通と、
1 つのレイヤー（自身のレイヤーかプロジェクト）の和。フェーズ定義（`phasetypes.merge`）と同じ衝突の
扱いにする。同名で全欄が同じなら info でレイヤーの側を破棄し、
中身が違えば error でレイヤーの側だけを
使わない。共通が壊れていれば共通を空として扱い、レイヤーの側は使う。

## 書式

    name: <名前>             # ファイル名（拡張子を除く）と同じ
    description: 何のための計画か
    when: 選ぶ手がかり        # 省いてよい
    plan:                    # 全体計画。ステップは定義の名前か {type, review, after}
      - design
      - {type: implement, after: [1]}
    feedback:                # フィードバック計画（省いてよい）
      - {type: implement-feedback}
    flows:                   # 全体計画のステップの添字（1 始まり）→ 子のフローの本文
      2: {id: ..., nodes: [...], connections: [...]}

`after` は**その計画の中の添字**（1 始まり）。フィードバック計画の `after` も、フィードバック計画の
中の添字で書く。提案にコピーするときの番号（フィードバック計画は全体計画の続き）への付け直しは、
使う側が行う。未知の欄は warn で、`extra` に持って書き戻しでも落とさない。

## 読み方

フローと同じ読み手（`flow.read_bytes` / `flow.parse_yaml`）を使う。リンク・ハードリンク・
ふつうのファイルでないものは読まず、大きさの上限（`flow.FILE_LIMIT`）があり、YAML の別名は拒み、
UTF-8（BOM は 1 つ外す）で、例外は外に出さない。

## 検査

計画は、合成した frontmatter を `ticket.parse` に渡して得た `Ticket` に、
承認の検査の関数を適用する。

- 順序（`after` の範囲・循環・終端が 1 つ・最後のステップの延期・延期の引き受け手）は error
- 型 id が使うフェーズ定義（共通 + レイヤーの `phases.yml`）に無い、`kind` が置き場と合わない、
  延期の引き受け手にレビューが無い、は warn にとどめる。定義が後から変わって食い違うのは
  避けられず、使うときの承認の検査で改めて error になるため
- フローは `flow.parse` と `flow_shape.structure_problems` / `name_problems`（`--lint --flow` の
  `lint.flow_problems` が組むのと同じ部品）。`flows` のキーが全体計画の添字に在ること

## 下書き（溜める側）

エージェントは下書きだけを書ける。置き場はほかの保護対象と同じ `wip/handoff/<取り込み先の
相対パス>`（`handoff`）。下書きに効力は無い。
下書きの作成と検査は `template_drafts`、取り込みは `handoff`（`ccnavi user handoff`）。

## 往復

`parse_template` で読んだ `Template` を `dump` で書き、もう一度読むと同じ `Template` になり、
`dump` の結果も変わらない（`as_data` が読んだ中身をそのまま書き戻せる形にする）。元の書き方
（コメント・キーの順・`- design` と `- {type: design}` の違い）までは保たない。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import stat
import unicodedata
from dataclasses import dataclass, field

import yaml

from ..infra import pathsafe, settings, tree
from ..policy import ruleload
from ..policy.rules import SEVERITY_ERROR, SEVERITY_INFO, SEVERITY_WARN, Problem
from . import agree_candidates
from .config import phasetypes, workflow
from .flow import graph as flow
from .flow import shape as flow_shape
from .model import ticket, ticket_ids, ticket_model

SUFFIX = flow.SUFFIX
FIELDS = ("name", "description", "when", "plan", "feedback", "flows")
# 下書きを読んだ `Entry.layer` に入れる印。
LAYER_DRAFT = "draft"
# 機械が下書きの `description` に入れる、一般化が済んでいない印。残っていれば検査が warn で言う。
TODO_MARK = "TODO"
# 名前に使える文字。ファイル名になるので、区切りも空白も入れない。
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
NAME_LIMIT = 64

LAYER_COMMON = settings.LAYER_COMMON
LAYER_SELF = settings.LAYER_SELF

# ステップの欄。
ITEM_FIELDS = ("type", "review", "after")

# 取り込まれない理由（`Entry.dropped`）。
DROP_INVALID = "invalid"
DROP_DUPLICATE = "duplicate"
DROP_CONFLICT = "conflict"

# 計画を検査するために合成する親の識別子。苦情には出さない。
_CHECK_ID = "template-check"


@dataclass
class Item:
    """計画の 1 ステップ。`after` はその計画の中の添字（1 始まり）。"""

    type: str
    review: str = ""
    after: list[int] = field(default_factory=list)

    def as_data(self):
        if not self.review and not self.after:
            return self.type
        out: dict = {"type": self.type}
        if self.review:
            out["review"] = self.review
        if self.after:
            out["after"] = list(self.after)
        return out


@dataclass
class Template:
    name: str
    description: str = ""
    when: str = ""
    plan: list[Item] = field(default_factory=list)
    feedback: list[Item] = field(default_factory=list)
    # 全体計画のステップの添字（1 始まり）→ フローの本文（フローのファイルと同じ形のマッピング）。
    flows: dict[int, dict] = field(default_factory=dict)
    # 未知の欄。書き戻しで落とさない。
    extra: dict = field(default_factory=dict)

    def as_data(self) -> dict:
        """書き戻せる形。読み直すと同じ `Template` になる。"""
        out: dict = {"name": self.name, "description": self.description}
        if self.when:
            out["when"] = self.when
        out["plan"] = [item.as_data() for item in self.plan]
        if self.feedback:
            out["feedback"] = [item.as_data() for item in self.feedback]
        if self.flows:
            out["flows"] = {i: self.flows[i] for i in sorted(self.flows)}
        out.update(self.extra)
        return out


@dataclass
class Entry:
    """置き場の `<名前>.yml` 1 本。読めなかったものも載せる（`template` が None）。"""

    layer: str
    name: str
    path: str
    rel: str
    sha: str = ""
    raw: bytes = b""
    template: Template | None = None
    # 読むときと統合結果の苦情。
    notes: list[Problem] = field(default_factory=list)
    # 計画とフローの検査の苦情（フェーズ定義が変われば変わるので、notes と分ける）。
    checks: list[Problem] = field(default_factory=list)
    # 統合結果で取り込まれたか。読めないもの・衝突で破棄したものは False。
    effective: bool = True
    dropped: str = ""
    dropped_detail: str = ""
    writable: bool = True

    @property
    def problems(self) -> list[Problem]:
        return self.notes + self.checks

    @property
    def valid(self) -> bool:
        return self.template is not None

    @property
    def usable(self) -> bool:
        """取り込まれていて、error も無い（使う側が受けてよい）。"""
        return self.effective and not any(p.severity == SEVERITY_ERROR for p in self.problems)


@dataclass
class LayerRead:
    """1 つのレイヤーの置き場。"""

    name: str
    directory: str
    rel: str
    exists: bool = False
    unreadable: str = ""
    writable: bool = True
    entries: list[Entry] = field(default_factory=list)
    # 置き場そのものの苦情（読めない、`.yml` でないファイル）。
    problems: list[Problem] = field(default_factory=list)


@dataclass
class Scan:
    """共通 + 各レイヤー。`layers` は自身のレイヤーが先、プロジェクトは名前順。"""

    root: str
    common: LayerRead
    layers: list[LayerRead] = field(default_factory=list)
    # フロー検査の候補（サブエージェントとスキルの名前）。
    candidates: dict = field(default_factory=dict)

    def layer(self, name: str) -> LayerRead | None:
        for found in self.layers:
            if found.name == name:
                return found
        return None


# ---- 読む（1 ファイル）


def _line(text: object) -> str:
    return " ".join(str(text).split())


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data) -> bool:
        return True


# 取り込みの差分で見えなくなる文字: 制御文字（改行・タブを除く）・書式文字（右から左への上書き、
# 幅ゼロの文字、BOM など）・行と段落の区切り。
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
_SHOWN_CONTROLS = "\n\t"


def hidden_chars(text: str) -> list[str]:
    """`text` に含まれる、読み違えを誘う文字（`\\n` と `\\t` は除く）。出てきた順で重複なし。"""
    return list(dict.fromkeys(ch for ch in text if _hidden(ch)))


def _hidden(ch: str) -> bool:
    return ch not in _SHOWN_CONTROLS and unicodedata.category(ch) in _HIDDEN_CATEGORIES


def hidden_problem(text: str) -> str:
    """`text` に読み違えを誘う文字があれば、error にする苦情の文面。無ければ空。"""
    hidden = hidden_chars(text)
    if not hidden:
        return ""
    shown = " ".join(escape_char(ch) for ch in hidden[:8])
    return (
        f"制御文字・書式文字が含まれている（{shown}{' ほか' if len(hidden) > 8 else ''}）。"
        "右から左への上書きや幅ゼロの文字、復帰などは、読み違えを誘うので使えない。削除します。"
    )


def escape_char(ch: str) -> str:
    """1 文字を `\\uXXXX`（BMP の外は `\\UXXXXXXXX`）にする。"""
    return f"\\u{ord(ch):04x}" if ord(ch) <= 0xFFFF else f"\\U{ord(ch):08x}"


def reveal(text: str) -> str:
    """表示用: 制御文字・書式文字を `\\uXXXX` に置き換える。書く中身には使わない。"""
    return "".join(escape_char(ch) if _hidden(ch) else ch for ch in text)


def _strings(value: object):
    """入れ子の中の文字列（キーも）を全部返す。"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, inner in value.items():
            yield from _strings(key)
            yield from _strings(inner)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            yield from _strings(inner)


def _literal_ok(text: str) -> bool:
    """YAML の literal block（`|`）で、中身を変えずに書ける文字列か。

    行末の空白、先頭の空白、末尾の空行、タブ・制御文字・書式文字を含むものは書けない
    （引用符で書く）。
    """
    if "\n" not in text or text[0] in " \n" or text.endswith("\n\n"):
        return False
    if any(unicodedata.category(ch) in _HIDDEN_CATEGORIES for ch in text.replace("\n", "")):
        return False  # タブ・復帰・NEL・Zl/Zp・書式文字を含む
    return not any(line.endswith(" ") for line in text.split("\n"))


def _represent_str(dumper: yaml.SafeDumper, data: str):
    style = "|" if _literal_ok(data) else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _represent_str)


def to_yaml(data: object) -> str:
    """テンプレート・フロー・合成した提案を YAML の文面にする共通の 1 か所。

    日本語はそのまま、キーの順は渡したまま、行は折らない。改行を含む文字列は literal block で
    書く（読める形）。literal で表せない文字列は PyYAML が選ぶ従来の書き方（引用符）になる。
    """
    return yaml.dump(
        data,
        Dumper=_Dumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=10**6,
    )


def dump(template: Template) -> str:
    """テンプレートを YAML の文面にする。読み直すと同じ `Template` になる。"""
    return to_yaml(template.as_data())


def digest(raw: bytes) -> str:
    """ファイルの中身のハッシュ。保存の `--expect` に使う（`source_sha` の流儀）。"""
    return hashlib.sha256(raw).hexdigest()


def parse_template(raw: bytes, stem: str = "") -> tuple[Template | None, list[Problem]]:
    """テンプレートの中身（バイト）を読む。読めなければ (None, error を含む苦情)。例外は出さない。

    `stem` を渡せば、`name` がそれと同じか確かめる（ファイル名が名前）。
    """
    data, why = flow.parse_yaml(raw)
    if why:
        return None, [Problem(SEVERITY_ERROR, stem, why)]
    if not isinstance(data, dict):
        return None, [Problem(SEVERITY_ERROR, stem, "最上位がマッピングではない")]
    problems: list[Problem] = []

    def error(text: str) -> None:
        problems.append(Problem(SEVERITY_ERROR, stem, text))

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        error("`name` が無いか、文字列でない")
        name = ""
    name = name.strip()
    if name and (not NAME_RE.match(name) or len(name) > NAME_LIMIT):
        error(
            f"`name` の `{_line(name)}` は指定できない（英数字で始め、英数字と `.` `_` `-` だけ。"
            f"{NAME_LIMIT} 字まで）"
        )
    elif stem and name and name != stem:
        error(f"`name` の `{name}` がファイル名（{stem}{SUFFIX}）と違う")

    texts: dict[str, str] = {}
    for key in ("description", "when"):
        value = data.get(key)
        if value is None:
            texts[key] = ""
        elif isinstance(value, str):
            texts[key] = value.strip()
        else:
            error(f"`{key}` は文字列で書く")
            texts[key] = ""
    if not texts["description"] and not any(p.severity == SEVERITY_ERROR for p in problems):
        error("`description` がありません（一覧に出す説明）")

    plan = _items(data.get("plan"), "plan", problems, stem, required=True)
    feedback = _items(data.get("feedback"), "feedback", problems, stem, required=False)
    flows = _flows(data.get("flows"), problems, stem)
    extra = {k: v for k, v in data.items() if k not in FIELDS}
    for key in extra:
        problems.append(Problem(SEVERITY_WARN, stem, f"未知の欄 `{_line(key)}` は読まない"))

    if any(p.severity == SEVERITY_ERROR for p in problems):
        return None, problems
    return (
        Template(
            name=name,
            description=texts["description"],
            when=texts["when"],
            plan=plan or [],
            feedback=feedback or [],
            flows=flows or {},
            extra=extra,
        ),
        problems,
    )


def _items(raw, key: str, problems: list[Problem], stem: str, required: bool) -> list[Item] | None:
    """計画のステップの欄（`key`）のリストを読む。欄が読めなければ None。苦情は `problems` に足す。

    `required` が真なら、欄が無いことと空であることを、どちらも苦情にする。
    ステップは、定義の名前だけの文字列か、`{type, review, after}` のマッピングで書く。

    処理フロー:
    1. 欄が無い・リストでない・必須なのに空なら、苦情を足して None を返す
    2. ステップを 1 件ずつ読む（添字は 1 から）。文字列は定義の名前だけのステップにする。
    空の文字列は苦情
    3. マッピングは、知らない欄を警告にして読まない。`type` は必須
    4. `review` は文字列に限る。`after` は整数の添字のリストに限り、重複は警告にして整える
    5. 苦情のあるステップは破棄する。読めたステップを `Item` にして返す
    """

    def error(text: str) -> None:
        problems.append(Problem(SEVERITY_ERROR, stem, text))

    # 欄そのものを確かめる。無い・リストでない・必須なのに空なら、ここで止める。
    if raw is None:
        if required:
            error(f"`{key}` がありません。")
        return None
    if not isinstance(raw, list):
        error(f"`{key}` はリストで書く")
        return None
    if required and not raw:
        error(f"`{key}` が空")
        return None
    items: list[Item] = []
    # ステップを 1 件ずつ読む。文字列は定義の名前だけのステップとして扱う。
    for i, entry in enumerate(raw, start=1):
        where = f"`{key}[{i}]`"
        if isinstance(entry, str):
            if not entry.strip():
                error(f"{where} が空")
                continue
            items.append(Item(type=entry.strip()))
            continue
        if not isinstance(entry, dict):
            error(f"{where} は定義の名前か {{type, review, after}} で書く")
            continue
        # 知らない欄は警告にして、読まない。
        for unknown in entry:
            if unknown not in ITEM_FIELDS:
                text = f"{where} の未知の欄 `{_line(unknown)}` は読まない"
                problems.append(Problem(SEVERITY_WARN, stem, text))
        # 種類は必須。
        kind = entry.get("type")
        if not isinstance(kind, str) or not kind.strip():
            error(f"{where} に `type` がありません。")
            continue
        review = entry.get("review")
        if review is not None and not isinstance(review, str):
            error(f"{where} の `review` は文字列で書く")
            continue
        # 添字は整数のリストに限る。重複は警告にして、昇順・重複なしに整える。
        after = entry.get("after")
        numbers: list[int] = []
        if after is not None:
            if not isinstance(after, list):
                error(f"{where} の `after` は添字のリストで書く")
                continue
            if any(isinstance(n, bool) or not isinstance(n, int) for n in after):
                error(f"{where} の `after` は整数の添字で書く")
                continue
            numbers = sorted(set(after))
            if len(numbers) != len(after):
                problems.append(
                    Problem(SEVERITY_WARN, stem, f"{where} の `after` に同じ添字を 2 度挙げている")
                )
        items.append(Item(type=kind.strip(), review=(review or "").strip(), after=numbers))
    return items


def _flows(raw, problems: list[Problem], stem: str) -> dict[int, dict] | None:
    def error(text: str) -> None:
        problems.append(Problem(SEVERITY_ERROR, stem, text))

    if raw is None:
        return None
    if not isinstance(raw, dict):
        error("`flows` は、全体計画のステップの添字をキーにしたマッピングで書く")
        return None
    out: dict[int, dict] = {}
    for key, body in raw.items():
        if isinstance(key, bool) or not (
            isinstance(key, int) or (isinstance(key, str) and key.isascii() and key.isdigit())
        ):
            error(f"`flows` のキー `{_line(key)}` は全体計画のステップの添字（整数）で書く")
            continue
        index = int(key)
        if index in out:
            error(f"`flows` に添字 {index} が 2 度ある")
            continue
        if not isinstance(body, dict):
            error(f"`flows[{index}]` はフローの本文（マッピング）で書く")
            continue
        out[index] = body
    return out


def dump_flow(body: dict) -> tuple[str, str]:
    """フローの本文を、フローのファイルの文面（YAML）にする。(文面, 書けない理由)。

    使う側（`template_use`）が子のフローの下書きに書く文面と、`_flow_read` が確かめる文面は同じ。
    """
    try:
        text = to_yaml(body)
    except (yaml.YAMLError, RecursionError, ValueError, TypeError) as exc:
        return "", f"YAML に変換できません ({_line(exc)})"
    return text, ""


def _flow_read(body: dict) -> tuple[dict | None, str]:
    """フローの本文を、フローのファイルと同じ読み手と形の検査で確かめる。"""
    text, why = dump_flow(body)
    if why:
        return None, why
    return flow.parse(text.encode("utf-8"))


# ---- 検査


def _definitions(types) -> dict[str, phasetypes.PhaseType]:
    return dict(types) if types else {}


def check(
    template: Template,
    types: dict[str, phasetypes.PhaseType] | None,
    candidates: dict | None = None,
) -> list[Problem]:
    """計画とフローの検査。`types` はそのときのフェーズ定義（共通 + レイヤー。無ければ None）。

    順序の誤りと、`flows` の添字・フローの形は error。型 id が定義に無い、`kind` が置き場と
    合わない、延期の引き受け手にレビューが無い、は warn（定義が後から変わりうるため）。
    `description` か `when` に `TODO` が残っている（下書きの一般化が済んでいない）のも warn。
    """
    name = template.name
    found: list[Problem] = []

    def say(severity: str, text: str) -> None:
        found.append(Problem(severity, name, text))

    for key in ("description", "when"):
        if TODO_MARK in getattr(template, key):
            say(
                SEVERITY_WARN,
                f"`{key}` に {TODO_MARK} が残っている（固有名の一般化が済んでいない下書き）。"
                "直してから取り込む",
            )
    hidden = hidden_problem("\n".join(_strings(template.as_data())))
    if hidden:
        say(SEVERITY_ERROR, hidden)
    total = len(template.plan) + len(template.feedback)
    if total > ticket_ids.MAX_CHILD_NUMBER:
        say(
            SEVERITY_ERROR,
            f"計画が {total} ステップある（上限 {ticket_ids.MAX_CHILD_NUMBER} ステップ）。"
            "計画を分ける",
        )
    for key, items in (("plan", template.plan), ("feedback", template.feedback)):
        for i, item in enumerate(items, start=1):
            for value in item.after:
                if value < 1 or value >= i:
                    say(
                        SEVERITY_ERROR,
                        f"`{key}[{i}]` の `after` の {value} は読めない"
                        f"（{key} の中の添字で、自分（{i}）より小さい 1 以上の数だけを指せる）",
                    )
            if item.review and item.review not in ticket_model.PLAN_REVIEWS:
                say(
                    SEVERITY_ERROR,
                    f"`{key}[{i}]` の `review` は {' か '.join(ticket_model.PLAN_REVIEWS)}。"
                    "弱める向き（none）は書けない",
                )
    if not any(p.severity == SEVERITY_ERROR for p in found):
        found.extend(_plan_problems(template, types))
    found.extend(_flow_problems(template, candidates))
    return found


def _composed(template: Template, ptypes: dict) -> str:
    """検査のために合成する親の提案の本文。フィードバック計画の `after` は続きの番号にする。"""
    planned = len(template.plan)

    def raw(item: Item, offset: int):
        if not item.review and not item.after:
            return item.type
        out: dict = {"type": item.type}
        if item.review:
            out["review"] = item.review
        if item.after:
            out["after"] = [n + offset for n in item.after]
        return out

    front: dict = {
        "version": ticket_model.VERSION,
        "ticket": _CHECK_ID,
        "plan": [raw(item, 0) for item in template.plan],
    }
    if template.feedback:
        front["feedback"] = [raw(item, planned) for item in template.feedback]
    front.update(
        {
            "phases": ptypes,
            "human_review": {"required": True, "reason": "template"},
            "title": "template",
            "rationale": "template",
            "allow": [{"match": "Write|Edit", "glob": "src/*"}],
        }
    )
    body = to_yaml(front)
    return f"{ticket_model.FENCE}\n{body}{ticket_model.FENCE}\n"


def _plan_config(
    template: Template, types: dict[str, phasetypes.PhaseType] | None
) -> tuple[dict, dict[str, phasetypes.PhaseType], dict[str, phasetypes.PhaseType], list[Problem]]:
    """順序の検査に使う定義。(合成する親の `phases:`, 検査の定義, 仮の定義, 定義が無い苦情)。

    定義に無い型は、順序の検査用に仮の定義へ置き換える（kind は置き場に合わせる）。
    定義が無いことは warn で別に言う。
    """
    name = template.name
    known = _definitions(types)
    found: list[Problem] = []
    ptypes: dict = {}
    placeholders: dict[str, phasetypes.PhaseType] = {}
    for key, items, kind in (
        ("plan", template.plan, phasetypes.KIND_WORK),
        ("feedback", template.feedback, phasetypes.KIND_FEEDBACK),
    ):
        for i, item in enumerate(items, start=1):
            if item.type in ptypes:
                continue
            real = known.get(item.type)
            if real is not None:
                ptypes[item.type] = phasetypes.as_copy(real)
                continue
            pt = phasetypes.PhaseType(
                id=item.type, title=f"(定義なし) {item.type}", kind=kind, review="mr"
            )
            placeholders[item.type] = pt
            ptypes[item.type] = phasetypes.as_copy(pt)
            if known:
                found.append(
                    Problem(
                        SEVERITY_WARN,
                        name,
                        f"`{key}[{i}]` の型 `{item.type}` が phases.yml にありません"
                        "（定義が変わったか、このレイヤーにありません。使う前に直す）",
                    )
                )
    if not known and placeholders:
        found.append(
            Problem(
                SEVERITY_WARN,
                name,
                "フェーズ定義（phases.yml）が無いので、型が在るか・kind が合うかは確かめていない",
            )
        )
    return ptypes, {**known, **placeholders}, placeholders, found


def _plan_problems(
    template: Template, types: dict[str, phasetypes.PhaseType] | None
) -> list[Problem]:
    name = template.name
    ptypes, config, _, found = _plan_config(template, types)
    parsed, notes = ticket.parse(_composed(template, ptypes))
    if parsed is None:
        return found + [Problem(p.severity, name, p.detail) for p in notes]
    structural = workflow.problems(parsed, None)
    seen = {p.detail for p in structural}
    found.extend(Problem(SEVERITY_ERROR, name, p.detail) for p in structural)
    for p in agree_candidates.plan_problems(parsed, config, approved=True):
        if p.detail in seen:
            continue
        seen.add(p.detail)
        # 定義に左右される食い違いは warn にとどめる。
        found.append(Problem(SEVERITY_WARN, name, p.detail))
    return found


def graph(
    template: Template, types: dict[str, phasetypes.PhaseType] | None, sha: str = ""
) -> list[dict]:
    """計画の図の中身。`plan_order.plans` と同じ形（`ApprovePlan`）で、計画ごとに 1 件。

    番号は全体計画が 1..n、フィードバック計画はその続き。`after` は部の中の添字で書く。
    合成できない（`after` が読めないなど）ときは、ステップと `after` を Template から直接組み、
    `loose` と `ready` は空にする。`problems` には `after` の誤りだけを入れる。
    `ticket.parse` の苦情は入れない。

    処理フロー:
    1. `_plan_config` で型の定義を得て、`_composed` の文面を `ticket.parse` で読む
    2. `after` の誤りが無ければ、`workflow.compute` で先行の形を計算する（読めなければ計算しない）
    3. 先行の形から、`loose` と `ready` を得る
    4. 全体計画、フィードバック計画の順に、ステップが在る部ごとに 1 件の図を組む
    5. ステップごとに、型の定義からタイトルと審査の種類を引く。`after` は全体の番号に直す
    6. 部の誤り（`_order_errors` と `workflow.problems`）を `problems` に入れて返す
    """
    # 型の定義と、合成した計画を読む。
    ptypes, config, _, _ = _plan_config(template, types)
    parsed, _ = ticket.parse(_composed(template, ptypes))
    # `after` に誤りが無いときだけ、先行の形を計算する。
    wf = None
    if parsed is not None and not any(
        p.severity == SEVERITY_ERROR for p in _order_errors(template)
    ):
        wf = workflow.compute(parsed)
    planned = len(template.plan)
    loose = workflow.loose(parsed, wf) if parsed is not None and wf is not None else []
    ready = workflow.ready(parsed, wf) if parsed is not None and wf is not None else []
    out = []
    # 部ごとに 1 件の図を組む。全体計画は 1 から、フィードバック計画はその続きから番号を振る。
    for key, first, items in (
        ("plan", 1, template.plan),
        ("feedback", planned + 1, template.feedback),
    ):
        if not items:
            continue
        numbers = range(first, first + len(items))
        offset = first - 1
        # `after` は部の中の添字なので、全体の番号に直す。
        after = {
            str(n): sorted({m + offset for m in item.after})
            for n, item in zip(numbers, items, strict=True)
            if item.after
        }
        # ステップごとの行。タイトルと審査の種類は、型の定義（あれば）から引く。
        rows = []
        for n, item in zip(numbers, items, strict=True):
            pt = config.get(item.type)
            review = (
                ticket_model.PLAN_REVIEW_MR
                if item.review == ticket_model.PLAN_REVIEW_MR
                else (pt.review if pt is not None else "")
            )
            rows.append(
                {
                    "number": n,
                    "from": n,
                    "type": item.type,
                    "title": pt.title if pt is not None and pt.title else item.type,
                    "review": review,
                    "deferred": item.review == ticket_model.PLAN_REVIEW_DEFER,
                    "review_at": wf.review_at.get(n) if wf is not None else None,
                    "locked": False,
                }
            )
        errors = [p.detail for p in _order_errors(template) if p.detail.startswith(f"`{key}[")]
        if wf is not None and parsed is not None:
            errors += [
                str(p) for p in workflow.problems(parsed, None, key) if p.severity == SEVERITY_ERROR
            ]
        out.append(
            {
                "ticket": template.name,
                "part": key,
                "source_sha": sha,
                "items": rows,
                "after": after,
                "proposed": after,
                "current": None,
                "loose": [n for n in loose if n in numbers],
                "ready": [n for n in ready if n in numbers],
                "problems": errors,
            }
        )
    return out


def _order_errors(template: Template) -> list[Problem]:
    """`after` の範囲と `review` の値の誤り（`check` の最初の段と同じ）。"""
    found: list[Problem] = []
    for key, items in (("plan", template.plan), ("feedback", template.feedback)):
        for i, item in enumerate(items, start=1):
            for value in item.after:
                if value < 1 or value >= i:
                    found.append(
                        Problem(
                            SEVERITY_ERROR,
                            template.name,
                            f"`{key}[{i}]` の `after` の {value} は読めない"
                            f"（{key} の中の添字で、自分（{i}）より小さい 1 以上の数だけを指せる）",
                        )
                    )
            if item.review and item.review not in ticket_model.PLAN_REVIEWS:
                found.append(
                    Problem(SEVERITY_ERROR, template.name, f"`{key}[{i}]` の `review` は読めない")
                )
    return found


def _flow_problems(template: Template, candidates: dict | None) -> list[Problem]:
    name = template.name
    found: list[Problem] = []
    planned = len(template.plan)
    for index in sorted(template.flows):
        if index < 1 or index > planned:
            found.append(
                Problem(
                    SEVERITY_ERROR,
                    name,
                    f"`flows` の添字 {index} が全体計画にない（全体計画は 1〜{planned}）",
                )
            )
            continue
        data, why = _flow_read(template.flows[index])
        if data is None:
            found.append(Problem(SEVERITY_ERROR, name, f"`flows[{index}]`: {why}"))
            continue
        said = flow_shape.structure_problems(data)
        if candidates is not None:
            said += flow_shape.name_problems(data, candidates)
        found.extend(Problem(SEVERITY_WARN, name, f"`flows[{index}]`: {line}") for line in said)
    return found


# ---- 読む（置き場）


def relative(root: str, path: str) -> str:
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return path.replace(os.sep, "/")
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return path.replace(os.sep, "/")
    return rel.replace(os.sep, "/")


def read_entry(layer: str, root: str, path: str, writable: bool = True) -> Entry:
    """置き場の 1 本を読む。読めなければ理由を error の苦情に持つ（`template` は None）。"""
    base = os.path.basename(path)
    stem = base[: -len(SUFFIX)] if base.endswith(SUFFIX) else base
    entry = Entry(layer=layer, name=stem, path=path, rel=relative(root, path), writable=writable)
    raw, why = flow.read_bytes(path, root)
    if raw is None:
        entry.notes.append(Problem(SEVERITY_ERROR, stem, why))
        entry.effective = False
        entry.dropped, entry.dropped_detail = DROP_INVALID, why
        return entry
    entry.raw = raw
    entry.sha = digest(raw)
    if not NAME_RE.match(stem) or len(stem) > NAME_LIMIT:
        text = f"ファイル名 `{_line(base)}` は名前に使えない（英数字で始め、英数字と `._-` だけ）"
        entry.notes.append(Problem(SEVERITY_ERROR, stem, text))
        entry.effective = False
        entry.dropped, entry.dropped_detail = DROP_INVALID, text
        return entry
    template, notes = parse_template(raw, stem)
    entry.notes.extend(notes)
    if template is None:
        entry.effective = False
        first = next((p.detail for p in notes if p.severity == SEVERITY_ERROR), "読めない")
        entry.dropped, entry.dropped_detail = DROP_INVALID, first
        return entry
    entry.template = template
    return entry


def read_layer(layer: str, root: str, directory: str, writable: bool = True) -> LayerRead:
    """レイヤーの置き場の `<名前>.yml` を全部読む（名前順）。無いのは正常（無い = 空）。"""
    read = LayerRead(
        name=layer, directory=directory, rel=relative(root, directory), writable=writable
    )
    try:
        before = os.lstat(directory)
    except FileNotFoundError:
        return read
    except OSError as exc:
        read.unreadable = f"読めない ({_line(exc.strerror or type(exc).__name__)})"
    else:
        if stat.S_ISLNK(before.st_mode) or pathsafe.has_link(root, directory):
            read.unreadable = "置き場がシンボリックリンクなので読まない"
        elif not stat.S_ISDIR(before.st_mode):
            read.unreadable = "置き場がディレクトリではない"
    read.exists = True
    if read.unreadable:
        read.problems.append(Problem(SEVERITY_ERROR, "", f"{read.rel}: {read.unreadable}"))
        return read
    try:
        names = sorted(os.listdir(directory))
    except OSError as exc:
        read.unreadable = f"読めない ({_line(exc.strerror or type(exc).__name__)})"
        read.problems.append(Problem(SEVERITY_ERROR, "", f"{read.rel}: {read.unreadable}"))
        return read
    for name in names:
        if name.startswith("."):
            continue
        path = os.path.join(directory, name)
        if not name.endswith(SUFFIX):
            text = f"{read.rel}/{_line(name)} は `{SUFFIX}` でないので読まない"
            read.problems.append(Problem(SEVERITY_WARN, "", text))
            continue
        read.entries.append(read_entry(layer, root, path, writable))
    return read


def merge(common: LayerRead, extra: LayerRead) -> list[Problem]:
    """共通にレイヤーを足す。レイヤーの側の `Entry` に取り込まれたか・破棄した理由を付ける。

    同名で全欄が同じならレイヤーの側を破棄して info、中身が違えば error でレイヤーの側を使わない。
    共通の側が読めていないもの（壊れた共通）は無いものとして扱い、レイヤーの側を使う。
    苦情は返し、レイヤーの側の `Entry.notes` にも足す。
    """
    base = {e.name: e for e in common.entries if e.valid}
    said: list[Problem] = []
    for entry in extra.entries:
        prior = base.get(entry.name)
        if prior is None or entry.template is None or prior.template is None:
            continue
        layer = entry.layer
        if dump(prior.template) == dump(entry.template):
            problem = Problem(
                SEVERITY_INFO,
                entry.name,
                f"`{entry.name}` は共通と全欄が同じなので、{layer} の側を破棄した。"
                "共通の 1 本を使う",
            )
            entry.dropped = DROP_DUPLICATE
        else:
            differing = [
                k
                for k, a, b in zip(
                    FIELDS,
                    (
                        prior.template.name,
                        prior.template.description,
                        prior.template.when,
                        [i.as_data() for i in prior.template.plan],
                        [i.as_data() for i in prior.template.feedback],
                        prior.template.flows,
                    ),
                    (
                        entry.template.name,
                        entry.template.description,
                        entry.template.when,
                        [i.as_data() for i in entry.template.plan],
                        [i.as_data() for i in entry.template.feedback],
                        entry.template.flows,
                    ),
                    strict=True,
                )
                if a != b
            ] or ["extra"]
            problem = Problem(
                SEVERITY_ERROR,
                entry.name,
                f"`{entry.name}` は共通と同じ名前で中身が違う（違う欄: {', '.join(differing)}）。"
                f"{layer} の `{entry.name}` は使わず、共通の定義を使う。"
                f"中身を変えたいなら {layer} の側に別の名前を付ける",
            )
            entry.dropped = DROP_CONFLICT
        entry.dropped_detail = problem.detail
        entry.effective = False
        entry.notes.append(problem)
        said.append(problem)
    return said


def common_writable(conf: settings.Settings, root: str) -> bool:
    """共通レイヤーの置き場を書き換えてよいか。

    発言のときの同期がプロジェクトの `config/common/` へコピーしたミラー
    （着手済みの親のワークツリーの中）は、
    共通レイヤーのコピーで誰も編集しないので読み取り専用。ワークスペースルートの共通は書ける。
    """

    where = tree.tree_of(root, root, conf.projects)
    return where is None or where.is_main or not where.project


def types_for(conf: settings.Settings, root: str, layer: str):
    """そのレイヤーの検査に使うフェーズ定義（共通 + レイヤー）。共通は共通だけ。"""
    if layer == LAYER_COMMON:
        types, _ = phasetypes.common_types(conf)
        return types
    project = "" if layer == LAYER_SELF else layer
    types, _ = phasetypes.layer_types(conf, root, project)
    return types


def recheck(entry: Entry, types, candidates: dict | None) -> Entry:
    """別のフェーズ定義（統合結果）で、検査をやり直したコピー。読むときの苦情は残す。"""
    if entry.template is None or not entry.effective:
        return entry
    return dataclasses.replace(entry, checks=check(entry.template, types, candidates))


def scan(conf: settings.Settings, root: str) -> Scan:
    """共通と全レイヤー（自身のレイヤー・プロジェクト）のテンプレートを読んで検査する。

    各 `Entry` の検査は、そのレイヤーのフェーズ定義（共通は共通だけ、レイヤーは共通 + レイヤー）で
    行う。統合結果（`merge`）の印は、レイヤーの側の `Entry` に付く。承認済みチケットの置き場は
    読まない（`--lint` が 1 度読んだコピーを渡す走査とは別で、ここは設定の置き場だけを読む）。
    """
    candidates = flow.catalog(root)
    common = read_layer(
        LAYER_COMMON,
        root,
        settings.common_templates_dir(conf, root),
        writable=common_writable(conf, root),
    )
    common_types = types_for(conf, root, LAYER_COMMON)
    for entry in common.entries:
        if entry.template is not None:
            entry.checks.extend(check(entry.template, common_types, candidates))
    out = Scan(root=root, common=common, candidates=candidates)
    for view in ruleload.layers(conf, root):
        layer = read_layer(view.name, root, settings.layer_templates_dir(conf, view.home))
        merge(common, layer)
        types = types_for(conf, root, view.name)
        for entry in layer.entries:
            if entry.template is not None and entry.effective:
                entry.checks.extend(check(entry.template, types, candidates))
        out.layers.append(layer)
    return out


def adopted(scan_: Scan, layer: str) -> list[Entry]:
    """`layer` を足したときの、取り込まれた・破棄した全部（共通が先、名前順）。

    読み込み済みの `Entry` を並べるだけ。共通の側の検査は共通だけのフェーズ定義で済んでいて、
    統合結果の定義でやり直す（`recheck`）のは呼び出し側。
    """
    view = scan_.layer(layer)
    if view is None:
        return []
    return list(scan_.common.entries) + list(view.entries)


def problems_of(scan_: Scan) -> list[tuple[str, Problem]]:
    """全部の苦情。(どのレイヤーか, 苦情)。`--lint` が使う。"""
    out: list[tuple[str, Problem]] = []
    for view in [scan_.common, *scan_.layers]:
        out.extend((view.name, p) for p in view.problems)
        for entry in view.entries:
            out.extend((view.name, p) for p in entry.problems)
    return out


def has_any(scan_: Scan) -> bool:
    return any(v.exists for v in [scan_.common, *scan_.layers])


# ---- 検索


def matches(entry: Entry, keywords: list[str]) -> bool:
    """`name`・`description`・`when` のどれかに語のどれかが含まれるか（大文字小文字は問わない）。"""
    if not keywords:
        return True
    t = entry.template
    haystack = " ".join([entry.name, t.description if t else "", t.when if t else ""]).casefold()
    return any(k.casefold() in haystack for k in keywords if k)


# ---- JSON

JSON_VERSION = 1


def _problem_json(p: Problem) -> dict:
    return {"severity": p.severity, "detail": p.detail}


def _item_json(items: list[Item], first: int) -> list[dict]:
    return [
        {
            "index": i,
            "number": first + i - 1,
            "type": item.type,
            "review": item.review,
            "after": list(item.after),
        }
        for i, item in enumerate(items, start=1)
    ]


def _flow_json(index: int, body: dict) -> dict:
    nodes = body.get("nodes")
    nodes = nodes if isinstance(nodes, list) else []
    kinds: dict[str, int] = {}
    for node in nodes:
        kind = str(node.get("type", "")) if isinstance(node, dict) else ""
        kinds[kind] = kinds.get(kind, 0) + 1
    connections = body.get("connections")
    name = body.get("name")
    return {
        "index": index,
        "name": name if isinstance(name, str) else "",
        "nodes": len(nodes),
        "connections": len(connections) if isinstance(connections, list) else 0,
        "node_types": {k: kinds[k] for k in sorted(kinds)},
    }


def _dropped_json(entry: Entry) -> dict | None:
    """統合結果で取り込まれなかった理由 `{reason, detail}`。取り込まれたものは None。"""
    if not entry.dropped:
        return None
    return {"reason": entry.dropped, "detail": entry.dropped_detail}


def entry_json(entry: Entry) -> dict:
    """画面がそのまま描ける 1 本分の答え。"""
    t = entry.template
    return {
        "name": entry.name,
        "layer": entry.layer,
        "path": entry.rel,
        "sha": entry.sha,
        "valid": t is not None,
        "description": t.description if t else "",
        "when": t.when if t else "",
        "plan": _item_json(t.plan, 1) if t else [],
        "feedback": _item_json(t.feedback, len(t.plan) + 1) if t else [],
        "has_flows": bool(t and t.flows),
        "flows": [_flow_json(i, t.flows[i]) for i in sorted(t.flows)] if t else [],
        "effective": entry.effective,
        "usable": entry.usable,
        "dropped": _dropped_json(entry),
        "writable": entry.writable,
        "problems": [_problem_json(p) for p in entry.problems],
    }


def layer_json(view: LayerRead) -> dict:
    return {
        "name": view.name,
        "path": view.rel,
        "exists": view.exists,
        "unreadable": view.unreadable,
        "writable": view.writable,
        "problems": [_problem_json(p) for p in view.problems],
        "templates": [entry_json(e) for e in view.entries],
    }


def as_json(
    conf: settings.Settings,
    root: str,
    scan_: Scan,
    layer: str = LAYER_SELF,
    keywords: list[str] | None = None,
) -> dict:
    """`ccnavi --templates --json` の答え。

    - `layer`: 一覧の対象のレイヤー（`templates` がその統合結果）
    - `templates`: 共通 + その 1 レイヤー。
    取り込まれたか（`effective`）・破棄した理由（`dropped`）・
      検査（`problems`。統合結果のフェーズ定義で検査した）つき。`keywords` で絞る
    - `layers`: 共通と各レイヤーの生の中身（そのレイヤーのフェーズ定義で検査した）
    - `sums`: レイヤーごとの統合結果の索引（どれが効いているか）
    """
    keywords = keywords or []
    view = scan_.layer(layer)
    types = types_for(conf, root, layer) if view is not None else None
    chosen: list[Entry] = []
    for entry in adopted(scan_, layer):
        # 統合結果で検査し直すのは共通の側（レイヤーの側は同じ定義で検査済み）。
        if entry.layer == LAYER_COMMON:
            entry = recheck(entry, types, scan_.candidates)
        if matches(entry, keywords):
            chosen.append(entry)
    return {
        "version": JSON_VERSION,
        "root": root,
        "layer": layer,
        "keywords": list(keywords),
        "templates": [entry_json(e) for e in chosen],
        "layers": [layer_json(v) for v in [scan_.common, *scan_.layers]],
        "sums": [
            {
                "name": v.name,
                "layers": [LAYER_COMMON, v.name],
                "templates": [
                    {
                        "name": e.name,
                        "layer": e.layer,
                        "effective": e.effective,
                        "dropped": _dropped_json(e),
                    }
                    for e in adopted(scan_, v.name)
                ],
            }
            for v in scan_.layers
        ],
        "errors": sum(1 for _, p in problems_of(scan_) if p.severity == SEVERITY_ERROR),
        "warns": sum(1 for _, p in problems_of(scan_) if p.severity == SEVERITY_WARN),
        "infos": sum(1 for _, p in problems_of(scan_) if p.severity == SEVERITY_INFO),
    }


def check_file(conf: settings.Settings, root: str, path: str, layer: str = LAYER_SELF) -> dict:
    """任意のパスのテンプレートを検査した答え（`--templates --check-file`）。書かない。

    名前の期待値はファイル名から拡張子を除いたもの。`data` は読んだ中身のコピー（読めなければ
    null）で、
    呼び手が「書こうとした中身を実行ファイルがそのまま読んだか」を見るのに使う。
    `types` はこのレイヤーで使えるフェーズ定義（共通 + レイヤー）。
    """
    base = os.path.basename(path)
    stem = base[: -len(SUFFIX)] if base.endswith(SUFFIX) else base
    types = types_for(conf, root, layer)
    answer: dict = {
        "version": JSON_VERSION,
        "ok": True,
        "layer": layer,
        "name": stem,
        "sha": "",
        "valid": False,
        "problems": [],
        "data": None,
        "plans": [],
        "types": [
            {"id": t.id, "title": t.title, "kind": t.kind, "review": t.review}
            for _, t in sorted((types or {}).items())
        ],
    }
    raw, why = flow.read_bytes(path)
    if raw is None:
        answer["problems"] = [_problem_json(Problem(SEVERITY_ERROR, stem, why))]
        return answer
    answer["sha"] = digest(raw)
    found: list[Problem] = []
    if not NAME_RE.match(stem) or len(stem) > NAME_LIMIT:
        found.append(
            Problem(
                SEVERITY_ERROR,
                stem,
                f"ファイル名 `{_line(base)}` は名前に使えない（英数字で始め、英数字と `._-` だけ）",
            )
        )
    template, notes = parse_template(raw, stem)
    found.extend(notes)
    if template is not None:
        found.extend(check(template, types))
        answer["valid"] = True
        answer["data"] = json.loads(json.dumps(template.as_data(), default=str))
        answer["plans"] = graph(template, types, answer["sha"])
    answer["problems"] = [_problem_json(p) for p in found]
    return answer


def find(scan_: Scan, layer: str, name: str) -> Entry | None:
    """名前の `Entry`。取り込まれたものを先に、破棄したものは最後に探す。"""
    found = [e for e in adopted(scan_, layer) if e.name == name]
    found.sort(key=lambda e: not e.effective)
    return found[0] if found else None
