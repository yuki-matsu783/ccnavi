"""文面ファイル（拒否・促し・次の一手の文）の読み込み・検査・組み立て。

条件（いつ出すか）はルールとコードと sh に残り、文（何を出すか）は文面ファイルが持つ。呼ぶ側は
識別子と差し込む値を渡すだけで、組み立てた文を受け取る。止める・止めないの判定には関わらない。

置き場は共通レイヤー（`.ccnavi/config/common/messages/*.yml`）と自身のレイヤー
（`.ccnavi/config/project/messages/*.yml`）。同じ識別子を自身のレイヤーに書くと、
その 1 件の文が替わる。
替えられるのは文の欄だけで、種類（`kind`）と差し込みの名前（`values`）は共通レイヤーが決める。
検査（欄・差し込みの名前・`次:` の数・長さ・ADR 番号・相対の script 呼び出し）を通らない
上書きは採らず、共通レイヤーの文を使う。形式と欄の表は設計書の「文面ファイル」の章にある。

失敗の向き: `say` は例外を投げない。文面ファイルが無い・壊れている・識別子や値が合わないときは、
種類ごとの短い既定の文（`fallback`）を返す。判定は呼ぶ側が決めたままで、文だけが落ちる。
壊れた文面ファイルが防御を消さない。

このモジュールは判定を持たず、ルールの読み込みに依らない（hook の関門が呼ぶ）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import yaml

from ..infra import fsio, settings, yamlread
from . import rules
from .rules import SEVERITY_ERROR, SEVERITY_WARN, Problem

VERSION = 1
MESSAGES_DIR = "messages"
SUFFIX = ".yml"

LAYER_COMMON = settings.LAYER_COMMON
LAYER_SELF = settings.LAYER_SELF

DENY = "deny"
NUDGE = "nudge"
NEXT = "next"
CHOICES = "choices"
NOTE = "note"

# 種類ごとの（必須の欄, 任意の欄）。順は出力の行の順。
FIELDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    DENY: (("what", "why", "next", "no_bypass"), ("note",)),
    NUDGE: (("what", "next"), ("why", "no_bypass", "note")),
    NEXT: (("next",), ("note",)),
    CHOICES: (("items",), ()),
    NOTE: (("note",), ()),
}

# 識別子の頭と種類。拒否のコード（`DENY_*`・`RULE_ASK`）と促しのコード（`NUDGE_*`）は、
# 記録の `code` と同じ。
PREFIXES: dict[str, str] = {
    "DENY_": DENY,
    "RULE_": DENY,
    "NUDGE_": NUDGE,
    "NEXT_": NEXT,
    "CHOICES_": CHOICES,
    "NOTE_": NOTE,
}

# 出力で欄の前に付ける語。`what` と `items` は付けない（`items` は番号を付ける）。
LABELS = {"why": "なぜ: ", "next": "次: ", "no_bypass": "迂回しない: "}
NOTE_LABEL = "案内: "
CHOICES_HEAD = "次の手:"

# 1 欄の長さ（場合ごと）と、1 件の欄の長さの合計の上限。
LIMIT_FIELD = 600
LIMIT_ENTRY = 2000

ID = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*(?:__[A-Z0-9]+(?:_[A-Z0-9]+)*)?")
NAME = re.compile(r"[a-z][a-z0-9_]*")
MODIFIERS = ("sh", "path")
# 引用を済ませた値を受ける名前の終わり。`:sh` `:path` は付けられない。
QUOTED_SUFFIX = "_q"

_ADR = re.compile(r"ADR[-\s]?\d", re.IGNORECASE)
_TOKEN = re.compile(r"\{\{|\}\}|\{([^{}]*)\}|[{}]")
# 値に入れない文字。制御文字・行と段落の区切り・全角空白は、空白にする。
_UNSAFE = re.compile("[\x00-\x1f\x7f-\x9f  　]+")
_FILE_HINT = f"{settings.DEFAULT_PROJECT_HOME}/{settings.COMMON_DIR}/{MESSAGES_DIR}/"


class TextError(Exception):
    """文を組み立てられなかった。理由は文字列。"""


@dataclass
class Entry:
    """文面 1 件。`fields` は欄の名前 → 文字列、`{場合: 文字列}`、または `items` の文字列の並び。"""

    id: str
    kind: str
    values: tuple[str, ...]
    fields: dict[str, object]
    layer: str
    path: str


@dataclass
class Catalog:
    """読み込んだ文面。`entries` は上書きを重ねたもの、`common` は共通レイヤーだけ。"""

    entries: dict[str, Entry] = field(default_factory=dict)
    common: dict[str, Entry] = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)


def kind_of_id(text_id: str) -> str:
    """識別子の頭から決まる種類。知らない頭なら空。"""
    for prefix, kind in PREFIXES.items():
        if text_id.startswith(prefix):
            return kind
    return ""


def code_of_id(text_id: str) -> str:
    """識別子の `__` より前（記録の `code` と同じ部分）。"""
    return text_id.split("__", 1)[0]


def dirs(root: str) -> tuple[str, str]:
    """（共通レイヤーの置き場, 自身のレイヤーの置き場）。絶対パス。"""
    home = settings.DEFAULT_PROJECT_HOME.replace("/", os.sep)
    return (
        os.path.join(root, home, settings.COMMON_DIR, MESSAGES_DIR),
        os.path.join(root, home, settings.LAYER_CONFIG_DIR, MESSAGES_DIR),
    )


# ---- 差し込み


def scan(text: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """文面の差し込みを順に拾う。（（種類, 名前, 修飾）の並び, 形の誤り）。

    種類は `value`（`{名前}`・`{名前:sh}`・`{名前:path}`）か `script`（`{script:<名前>}`）。
    `{{` と `}}` は字の `{` `}` で、差し込みに数えない。
    """
    found: list[tuple[str, str, str]] = []
    bad: list[str] = []
    for hit in _TOKEN.finditer(text):
        whole = hit.group(0)
        inner = hit.group(1)
        if whole in ("{{", "}}"):
            continue
        if inner is None:
            bad.append(f"対にならない `{whole}`（字で使うときは `{whole}{whole}`）")
            continue
        if inner.startswith("script:"):
            name = inner[len("script:") :]
            if not rules.SCRIPT_NAME.fullmatch(name):
                bad.append(f"`{{{inner}}}` の sh の名前の形が違う")
                continue
            found.append(("script", name, ""))
            continue
        name, _, mod = inner.partition(":")
        if not NAME.fullmatch(name):
            bad.append(f"`{{{inner}}}` の名前の形が違う（小文字・数字・`_`）")
            continue
        if mod and mod not in MODIFIERS:
            bad.append(f"`{{{inner}}}` の修飾 `{mod}` を知らない（{' '.join(MODIFIERS)}）")
            continue
        if mod and name.endswith(QUOTED_SUFFIX):
            bad.append(f"`{{{inner}}}` は引用済みの値（`{QUOTED_SUFFIX}`）に修飾を付けられない")
            continue
        found.append(("value", name, mod))
    return found, bad


def clean_value(value: object) -> str:
    """値を文面に入れられる形にする。制御文字などを空白にして、行を足せないようにする。"""
    return _UNSAFE.sub(" ", str(value)).strip()


def fill(text: str, values: dict[str, str], root: str) -> str:
    """差し込みを置き換える。値が足りなければ `TextError`。"""

    def put(hit: re.Match) -> str:
        whole = hit.group(0)
        inner = hit.group(1)
        if whole == "{{":
            return "{"
        if whole == "}}":
            return "}"
        if inner is None:
            raise TextError(f"対にならない `{whole}`")
        if inner.startswith("script:"):
            if not root:
                raise TextError("sh の差し込みにはワークスペースルートが要る")
            return settings.script_command(root, inner[len("script:") :])
        name, _, mod = inner.partition(":")
        if name not in values:
            raise TextError(f"値 {name} が渡されていない")
        value = clean_value(values[name])
        if mod == "sh":
            return settings.shell_word(value)
        if mod == "path":
            return settings.shell_path(value)
        return value

    return _TOKEN.sub(put, text)


# ---- 検査


def _text_of(raw: object) -> str:
    return raw.strip() if isinstance(raw, str) else ""


def _check_field(
    name: str,
    raw: object,
    base_value: object,
    err,
    used: set[str],
    sizes: list[int],
) -> object | None:
    """欄 1 つを検査して、正規化した値を返す。誤りがあれば err に言って None。"""
    if name == "items":
        if not isinstance(raw, list) or len(raw) < 2:
            err("`items` は 2 件以上の文字列の並び")
            return None
        items: list[str] = []
        for item in raw:
            text = _text_of(item)
            if not text or len(text.splitlines()) > 1:
                err("`items` の 1 件は 1 行の文字列")
                return None
            if _check_text("items", text, err, used, sizes, single=True):
                return None
            items.append(text)
        return items
    if isinstance(raw, dict):
        if len(raw) < 2 or not all(isinstance(k, str) and NAME.fullmatch(k) for k in raw):
            err(f"`{name}` の場合分けは、2 つ以上の場合の名前（小文字・数字・`_`）で書く")
            return None
        if not isinstance(base_value, dict) and base_value is not None:
            err(f"`{name}` の場合分けは共通レイヤーに無い")
            return None
        if isinstance(base_value, dict) and set(raw) != set(base_value):
            err(f"`{name}` の場合の名前が共通レイヤーと違う")
            return None
        picked: dict[str, str] = {}
        failed = False
        for case, value in raw.items():
            text = _text_of(value)
            if not text:
                err(f"`{name}` の場合 {case} の文が無い")
                failed = True
                continue
            if _check_text(f"{name}.{case}", text, err, used, sizes, single=name != "note"):
                failed = True
            picked[case] = text
        return None if failed else picked
    if isinstance(base_value, dict):
        err(f"`{name}` は共通レイヤーが場合分けしている。場合の名前で書く")
        return None
    text = _text_of(raw)
    if not text:
        err(f"`{name}` が文字列でない、または空")
        return None
    if _check_text(name, text, err, used, sizes, single=name != "note"):
        return None
    return text


def _check_text(
    label: str, text: str, err, used: set[str], sizes: list[int], *, single: bool
) -> bool:
    """文 1 つの検査。誤りがあれば True。"""
    bad = False
    if single and len(text.splitlines()) > 1:
        err(f"`{label}` は 1 行で書く")
        bad = True
    if text.startswith(("次:", "次の手:")):
        err(f"`{label}` を `次:` で始めない（`次:` の行は組み立てる側が付ける）")
        bad = True
    if len(text) > LIMIT_FIELD:
        err(f"`{label}` が {LIMIT_FIELD} 字を超える（{len(text)} 字）")
        bad = True
    sizes.append(len(text))
    if _ADR.search(text):
        err(f"`{label}` に ADR 番号がある。決めた中身と理由を書く")
        bad = True
    if rules.has_relative_scripts(text):
        err(f"`{label}` に相対の `.ccnavi/scripts/` がある。`{{script:<名前>}}` を使う")
        bad = True
    found, problems = scan(text)
    for problem in problems:
        err(f"`{label}` の差し込み: {problem}")
        bad = True
    used.update(name for kind, name, _ in found if kind == "value")
    return bad


def check_entry(
    text_id: str, raw: object, layer: str, path: str, base: Entry | None = None
) -> tuple[Entry | None, list[Problem]]:
    """1 件を検査して組む。`base` は上書きされる共通レイヤーの 1 件（共通レイヤー自身なら None）。

    誤りがあれば（Entry は None）、呼び手はその 1 件を採らない。
    """
    problems: list[Problem] = []
    where = os.path.basename(path) or path

    def err(message: str) -> None:
        problems.append(Problem(SEVERITY_ERROR, text_id, f"{where}: {message}"))

    expect = kind_of_id(text_id)
    if not isinstance(text_id, str) or not ID.fullmatch(text_id):
        err("識別子の形が違う（大文字・数字・`_`。場合を分けるときだけ `__`）")
        return None, problems
    if not expect:
        err(f"識別子の頭は {' '.join(PREFIXES)} のどれか")
        return None, problems
    if not isinstance(raw, dict):
        err("1 件は欄の表で書く")
        return None, problems
    if base is None:
        kind = raw.get("kind")
        if not isinstance(kind, str) or kind not in FIELDS:
            err(f"`kind` は {' '.join(FIELDS)} のどれか")
            return None, problems
        if kind != expect:
            err(f"識別子の頭が {expect} の種類を言っているが、`kind` は {kind}")
            return None, problems
        declared = raw.get("values")
        if declared is None:
            declared = []
        if (
            not isinstance(declared, list)
            or not all(isinstance(v, str) and NAME.fullmatch(v) for v in declared)
            or len(set(declared)) != len(declared)
        ):
            err("`values` は重ならない名前（小文字・数字・`_`）の並び")
            return None, problems
        values = tuple(declared)
        allowed_extra = {"kind", "values"}
    else:
        for key in ("kind", "values"):
            if key in raw:
                err(f"`{key}` は上書きに書けない（共通レイヤーが決める）")
        kind = base.kind
        values = base.values
        allowed_extra = set()
    required, optional = FIELDS[kind]
    unknown = sorted(
        str(k)
        for k in set(raw) - set(required) - set(optional) - allowed_extra - {"kind", "values"}
    )
    if unknown:
        err(f"{kind} の欄に無い: {', '.join(unknown)}")
    for name in required:
        if name not in raw:
            err(f"欄 `{name}` が無い")
    used: set[str] = set()
    sizes: list[int] = []
    out: dict[str, object] = {}
    for name in (*required, *optional):
        if name not in raw:
            continue
        before = len(problems)
        got = _check_field(
            name, raw[name], base.fields.get(name) if base else None, err, used, sizes
        )
        if got is None and len(problems) == before:
            err(f"`{name}` を読めない")
        if got is not None:
            out[name] = got
    if used != set(values):
        only_text = sorted(used - set(values))
        only_values = sorted(set(values) - used)
        parts = []
        if only_text:
            parts.append(f"文面にだけある: {', '.join(only_text)}")
        if only_values:
            parts.append(f"`values` にだけある: {', '.join(only_values)}")
        err("差し込みの名前が `values` と合わない（" + "。".join(parts) + "）")
    if sum(sizes) > LIMIT_ENTRY:
        err(f"1 件の文の長さの合計が {LIMIT_ENTRY} 字を超える（{sum(sizes)} 字）")
    if problems:
        return None, problems
    return Entry(text_id, kind, values, out, layer, path), problems


def _parse(text: str, path: str) -> tuple[dict | None, list[Problem]]:
    """ファイルの外側（`version` と `messages`）を読む。"""
    where = os.path.basename(path) or path
    try:
        data = yamlread.safe_load(text)
    except (yaml.YAMLError, ValueError, RecursionError) as exc:
        return None, [Problem(SEVERITY_ERROR, "(messages)", f"{where}: 読めない: {exc}")]
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return None, [
            Problem(SEVERITY_ERROR, "(messages)", f"{where}: `version: {VERSION}` の表で書く")
        ]
    body = data.get("messages")
    if not isinstance(body, dict):
        return None, [Problem(SEVERITY_ERROR, "(messages)", f"{where}: `messages` が表でない")]
    return body, []


def check_text(
    text: str, layer: str, path: str, common: dict[str, Entry] | None = None
) -> tuple[dict[str, Entry], list[Problem]]:
    """文面ファイル 1 本の中身を検査する。下書きの取り込み前の検査も使う。

    `layer` が共通レイヤーなら 1 件ずつ単独で、自身のレイヤーなら `common`（共通レイヤーの全件）の
    同じ識別子に重ねて検査する。誤りのある 1 件は結果に入らない。
    """
    body, problems = _parse(text, path)
    entries: dict[str, Entry] = {}
    if body is None:
        return entries, problems
    for text_id, raw in body.items():
        base = None
        if layer != LAYER_COMMON:
            base = (common or {}).get(str(text_id))
            if base is None:
                problems.append(
                    Problem(
                        SEVERITY_WARN,
                        str(text_id),
                        f"{os.path.basename(path)}: "
                        "共通レイヤーに無い識別子。どの呼び出しも引かない",
                    )
                )
                continue
        entry, found = check_entry(str(text_id), raw, layer, path, base)
        problems.extend(found)
        if entry is not None:
            entries[entry.id] = entry
    return entries, problems


def _read_layer(
    directory: str, layer: str, common: dict[str, Entry] | None
) -> tuple[dict[str, Entry], list[Problem]]:
    entries: dict[str, Entry] = {}
    problems: list[Problem] = []
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(SUFFIX))
    except OSError:
        return entries, problems  # 置き場が無いのは正常
    for name in names:
        path = os.path.join(directory, name)
        text = fsio.read_text(path)
        if text is None:
            problems.append(Problem(SEVERITY_ERROR, "(messages)", f"{name}: 読めない"))
            continue
        found, notes = check_text(text, layer, path, common)
        problems.extend(notes)
        for text_id, entry in found.items():
            if text_id in entries:
                problems.append(
                    Problem(
                        SEVERITY_ERROR,
                        text_id,
                        f"{name}: 同じレイヤーの別のファイルにも同じ識別子がある。後ろは採らない",
                    )
                )
                continue
            entries[text_id] = entry
    return entries, problems


def load(root: str) -> Catalog:
    """共通レイヤーに自身のレイヤーを重ねて読む。例外を投げない（問題は `problems`）。"""
    common_dir, self_dir = dirs(root)
    common, problems = _read_layer(common_dir, LAYER_COMMON, None)
    over, more = _read_layer(self_dir, LAYER_SELF, common)
    merged = dict(common)
    merged.update(over)
    return Catalog(merged, common, problems + more)


# ---- 組み立て


def _pick(entry: Entry, name: str, variants: dict[str, str]) -> str:
    value = entry.fields[name]
    if isinstance(value, dict):
        case = variants.get(name, "")
        if case not in value:
            raise TextError(f"{entry.id}: `{name}` の場合 {case!r} を選べない（{' '.join(value)}）")
        return value[case]
    return str(value)


def render(entry: Entry, values: dict[str, str], variants: dict[str, str], root: str) -> str:
    """文を組み立てる。値が足りない・場合を選べないときは `TextError`。"""
    missing = [name for name in entry.values if name not in values]
    if missing:
        raise TextError(f"{entry.id}: 値が足りない: {', '.join(missing)}")

    def one(name: str) -> str:
        return fill(_pick(entry, name, variants), values, root)

    def noted(name: str) -> list[str]:
        if name not in entry.fields:
            return []
        return [NOTE_LABEL + ln for ln in one(name).splitlines() if ln.strip()]

    lines: list[str] = []
    if entry.kind == CHOICES:
        lines.append(CHOICES_HEAD)
        for number, item in enumerate(entry.fields["items"], 1):  # type: ignore[arg-type]
            lines.append(f"  {number}. {fill(item, values, root)}")
        return "\n".join(lines)
    if entry.kind == NOTE:
        return "\n".join(noted("note"))
    if "what" in entry.fields:
        lines.append(one("what"))
    for name in ("why", "next", "no_bypass"):
        if name in entry.fields:
            lines.append(LABELS[name] + one(name))
    lines.extend(noted("note"))
    return "\n".join(lines)


def fallback(text_id: str) -> str:
    """文を引けなかったときの短い既定の文。個々の文は持たず、種類ごとの数行だけ。

    止める・止めないの判定は呼ぶ側が決めたままで、変わらない。文は、文面を引けなかったことと、
    ユーザに確かめてもらう次の一手を言う。
    """
    kind = kind_of_id(text_id)
    gone = f"文面（{text_id}）を引けませんでした。"
    ask = (
        f"ユーザに、文面ファイル（{_FILE_HINT}）が読めるかを `ccnavi --lint` で確かめてもらい、"
        "返事を待つ"
    )
    if kind == NOTE:
        return NOTE_LABEL + gone
    if kind == CHOICES:
        return f"{CHOICES_HEAD}\n  1. {ask}（{gone}）"
    if kind == NEXT:
        return f"{LABELS['next']}{ask}（{gone}）"
    head = f"{gone}止めた判定は変わらず、短い既定の文で伝えます。"
    lines = [head, f"{LABELS['next']}{ask}"]
    if kind == DENY:
        lines.append(f"{LABELS['no_bypass']}言い換えや別の経路でも、同じ関門に当たります。")
    return "\n".join(lines)


def say(
    root: str,
    text_id: str,
    values: dict[str, str] | None = None,
    variants: dict[str, str] | None = None,
) -> str:
    """識別子の文を組み立てて返す。引けなければ既定の文（`fallback`）。例外を投げない。"""
    try:
        entry = load(root).entries.get(text_id)
        if entry is None:
            raise TextError(f"{text_id}: 文面が無い")
        return render(entry, values or {}, variants or {}, root)
    except (TextError, OSError, ValueError, RecursionError, yaml.YAMLError):
        return fallback(text_id)


def lines_of(text: str) -> list[str]:
    """出力の `次:` の行。テストと点検が数える。"""
    return [ln for ln in text.splitlines() if ln.startswith(LABELS["next"])]
