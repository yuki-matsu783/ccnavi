"""文面ファイル（拒否・促し・次の一手の文）の読み込み・検査・組み立て。

条件（いつ出すか）はルールとコードと sh に残り、文（何を出すか）は文面ファイルが持つ。
呼ぶ側は識別子と差し込む値を渡すだけで、組み立てた文を受け取る。
止める・止めないの判定には関わらない。

置き場はワークスペースルートの `.ccnavi/messages/*.yml` だけ（文は ccnavi の仕組み側のもので、
設定ではないので `config/` の外に置く）。レイヤーごとの `messages/` は読まない
（文だけを替える口は持たない）。
検査（欄・差し込みの名前・`次:` の数・長さ・ADR 番号・相対の script 呼び出し）を
通らない 1 件は採らない。形式と欄の表は設計書の「文面ファイル」の章にある。

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
from .rules import SEVERITY_ERROR, Problem

VERSION = 1
MESSAGES_DIR = "messages"
SUFFIX = ".yml"

DENY = "deny"
NUDGE = "nudge"
NEXT = "next"
CHOICES = "choices"
NOTE = "note"
NOTICE = "notice"

# 種類ごとの（必須の欄, 任意の欄）。順は出力の行の順。
FIELDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    DENY: (("what", "why", "next", "no_bypass"), ("note",)),
    NUDGE: (("what", "next"), ("why", "no_bypass", "note")),
    NEXT: (("next",), ("note",)),
    CHOICES: (("items",), ()),
    NOTE: (("note",), ()),
    # 止めない通知。判定の見出し（コードと出所）と対象の行はコードに残し、本文だけを持つ。
    NOTICE: (("what",), ("why", "next", "note")),
}

# `RULE_` の文（組み込みルールの文）は、`what` だけが必須で、ほかの欄は省くか空にしてよい。
# 言うことが無い欄を作り足さないため。`RULE_ASK` と `RULE_ASK__*`（確認が自動で拒否になるときの理由）は
# 記録の `code` と同じ識別子で、ほかの拒否と同じ 4 欄を必ず持つ。
RULE_LOOSE_FIELDS: tuple[tuple[str, ...], tuple[str, ...]] = (
    ("what",),
    ("why", "next", "no_bypass", "note"),
)
RULE_PREFIX = "RULE_"
RULE_ASK = "RULE_ASK"

# 識別子の頭と種類。拒否のコード（`DENY_*`・`RULE_ASK`）と促しのコード（`NUDGE_*`）は、
# 記録の `code` と同じ。通知（`NOTICE_*`）は止めない文で、記録の `code` とは別。
PREFIXES: dict[str, str] = {
    "DENY_": DENY,
    "RULE_": DENY,
    "NUDGE_": NUDGE,
    "NEXT_": NEXT,
    "CHOICES_": CHOICES,
    "NOTE_": NOTE,
    "NOTICE_": NOTICE,
}

# 出力で欄の前に付ける語。`what` と `items` は付けない（`items` は番号を付ける）。
LABELS = {"why": "なぜ: ", "next": "次: ", "no_bypass": "迂回しない: "}
NOTE_LABEL = "案内: "
CHOICES_HEAD = "次の手:"

# 理由や報告に載せる対象の文字列の長さの上限。対象はエージェントが今書いたものなので、
# 同じものを指せれば足りる。ヒアドキュメントには 1 ファイル分を丸ごと書けるので、
# 全文を載せると本体が下へ押し出されて読まれなくなる。
SUBJECT_LIMIT = 200
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
_FILE_HINT = f"{settings.DEFAULT_PROJECT_HOME}/{MESSAGES_DIR}/"


class TextError(Exception):
    """文を組み立てられなかった。理由は文字列。"""


@dataclass
class Entry:
    """文面 1 件。`fields` は欄の名前 → 文字列、`{場合: 文字列}`、または `items` の文字列の並び。"""

    id: str
    kind: str
    values: tuple[str, ...]
    fields: dict[str, object]
    path: str


@dataclass
class Catalog:
    """読み込んだ文面（置き場の全件）。"""

    entries: dict[str, Entry] = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)


def kind_of_id(text_id: str) -> str:
    """識別子の頭から決まる種類。知らない頭なら空。"""
    for prefix, kind in PREFIXES.items():
        if text_id.startswith(prefix):
            return kind
    return ""


def fields_for(text_id: str, kind: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """識別子と種類から決まる（必須の欄, 任意の欄）。

    `RULE_` の文（`RULE_ASK` の系統を除く）だけが、`what` 以外の欄を任意にする。
    """
    if kind == DENY and text_id.startswith(RULE_PREFIX):
        if text_id != RULE_ASK and not text_id.startswith(RULE_ASK + "__"):
            return RULE_LOOSE_FIELDS
    return FIELDS[kind]


def code_of_id(text_id: str) -> str:
    """識別子の `__` より前（記録の `code` と同じ部分）。"""
    return text_id.split("__", 1)[0]


def dirs(root: str) -> str:
    """文面ファイルの置き場（`<root>/.ccnavi/messages`）。絶対パス。"""
    home = settings.DEFAULT_PROJECT_HOME.replace("/", os.sep)
    return os.path.join(root, home, MESSAGES_DIR)


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


def _blank(raw: object) -> bool:
    """欄が空（書かれていない・空文字・空白だけ）か。"""
    return raw is None or (isinstance(raw, str) and not raw.strip())


def _text_of(raw: object) -> str:
    return raw.strip() if isinstance(raw, str) else ""


def _check_field(
    name: str,
    raw: object,
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


def check_entry(text_id: str, raw: object, path: str) -> tuple[Entry | None, list[Problem]]:
    """1 件を検査して組む。誤りがあれば（Entry は None）、呼び手はその 1 件を採らない。"""
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
    required, optional = fields_for(text_id, kind)
    loose = (required, optional) == RULE_LOOSE_FIELDS
    unknown = sorted(str(k) for k in set(raw) - set(required) - set(optional) - {"kind", "values"})
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
        if loose and name not in required and _blank(raw[name]):
            continue  # 言うことが無い欄は省くのと同じ（`RULE_` の文だけ）
        before = len(problems)
        got = _check_field(name, raw[name], err, used, sizes)
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
    return Entry(text_id, kind, values, out, path), problems


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


def check_text(text: str, path: str) -> tuple[dict[str, Entry], list[Problem]]:
    """文面ファイル 1 本の中身を 1 件ずつ単独で検査する。下書きの取り込み前の検査も使う。

    誤りのある 1 件は結果に入らない。
    """
    body, problems = _parse(text, path)
    entries: dict[str, Entry] = {}
    if body is None:
        return entries, problems
    for text_id, raw in body.items():
        entry, found = check_entry(str(text_id), raw, path)
        problems.extend(found)
        if entry is not None:
            entries[entry.id] = entry
    return entries, problems


def _read_dir(directory: str) -> tuple[dict[str, Entry], list[Problem]]:
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
        found, notes = check_text(text, path)
        problems.extend(notes)
        for text_id, entry in found.items():
            if text_id in entries:
                problems.append(
                    Problem(
                        SEVERITY_ERROR,
                        text_id,
                        f"{name}: 別のファイルにも同じ識別子がある。後ろは採らない",
                    )
                )
                continue
            entries[text_id] = entry
    return entries, problems


# 置き場ごとの全件の読み込み結果（`--lint` と点検が使う。1 件を引く `say` は `find` を使う）。
# ファイルの名前・更新時刻・大きさが変わったら読み直す。変わらなければ同じ結果を返す
# （呼ぶ側は書き換えない）。
_CACHE: dict[str, tuple[tuple, Catalog]] = {}


def _signature(directory: str) -> tuple:
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(SUFFIX))
    except OSError:
        return ()
    found: list[tuple] = []
    for name in names:
        try:
            stat = os.stat(os.path.join(directory, name))
        except OSError:
            found.append((name, None, None))
            continue
        found.append((name, stat.st_mtime_ns, stat.st_size))
    return tuple(found)


def load(root: str) -> Catalog:
    """置き場の文面を全件読む。例外を投げない（問題は `problems`）。"""
    directory = dirs(root)
    signature = _signature(directory)
    hit = _CACHE.get(directory)
    if hit is not None and hit[0] == signature:
        return hit[1]
    entries, problems = _read_dir(directory)
    catalog = Catalog(entries, problems)
    _CACHE[directory] = (signature, catalog)
    return catalog


# 1 件を引くときは、識別子の字を含むファイルだけを YAML として読む（hook は 1 回ごとに別の
# プロセスで、全件を読むと文 1 件のために十数本を毎回読むため）。名前順に見て、その識別子を
# 検査を通る形で持つ最初のファイルの 1 件を採る。全件を読む `load` と同じ結果になる
# （別のファイルの重複は後ろを捨てる）。ファイルごとの結果は、更新時刻と大きさが変わるまで使い回す。
_FILES: dict[str, tuple[tuple, str, dict[str, Entry] | None]] = {}


def _file(path: str) -> tuple[str, dict[str, Entry] | None] | None:
    """ファイル 1 本の中身と、検査済みの件（未検査なら None）。読めなければ None。"""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    signature = (stat.st_mtime_ns, stat.st_size)
    hit = _FILES.get(path)
    if hit is not None and hit[0] == signature:
        return hit[1], hit[2]
    text = fsio.read_text(path)
    if text is None:
        return None
    _FILES[path] = (signature, text, None)
    return text, None


def find(root: str, text_id: str) -> Entry | None:
    """識別子の 1 件。無い・検査を通らないなら None。例外を投げない。"""
    directory = dirs(root)
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(SUFFIX))
    except OSError:
        return None
    for name in names:
        path = os.path.join(directory, name)
        got = _file(path)
        if got is None or text_id not in got[0]:
            continue
        text, entries = got
        if entries is None:
            entries = check_text(text, path)[0]
            _FILES[path] = (_FILES[path][0], text, entries)
        if text_id in entries:
            return entries[text_id]
    return None


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
    if kind == NOTICE:
        return f"{gone}\n{LABELS['next']}{ask}"
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
        entry = find(root, text_id)
        if entry is None:
            raise TextError(f"{text_id}: 文面が無い")
        return render(entry, values or {}, variants or {}, root)
    except (TextError, OSError, ValueError, RecursionError, yaml.YAMLError):
        return fallback(text_id)


def spec(
    message_id: str,
    *,
    git: str | None = None,
    wrapper: bool = False,
    cased: tuple[str, ...] = (),
    variants: dict[str, str] | None = None,
) -> dict:
    """組み込みのルールの文の指定（識別子・値・場合）。ルールの dict に展開して渡す。

    `git` は文面に出す git の打ち方（文面が `{git}` を使うときだけ渡す）。`cased` は、
    ラッパーの有無で文が分かれる欄の名前で、ラッパーがあれば `wrapper`、なければ `plain` を選ぶ。
    `variants` はそれ以外の場合の選び方（欄の名前 → 場合）。
    """
    chosen = {name: "wrapper" if wrapper else "plain" for name in cased}
    return {
        "message_id": message_id,
        "message_values": {} if git is None else {"git": git},
        "message_variants": {**chosen, **(variants or {})},
    }


def spoken(rule: rules.Rule) -> str:
    """ルールがモデルに渡す文。識別子を持つ組み込みのルールは、いま引く。

    組み立てでは引かない（hook の呼び出しごとに組み立てるので、通る呼び出しに文面ファイルの
    冷たい読みを乗せないため）。拒否に当たった回に、ここで引く。
    """
    if rule.message_id and not rule.degraded_message:
        return say(rule.root, rule.message_id, rule.message_values, rule.message_variants)
    return rule.spoken_message()


def lines_of(text: str) -> list[str]:
    """出力の `次:` の行。テストと点検が数える。"""
    return [ln for ln in text.splitlines() if ln.startswith(LABELS["next"])]
