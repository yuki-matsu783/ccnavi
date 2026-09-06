"""判定を動かすルール集合の読み込み。

ルールは実行ファイルの外に置く。作り直さずに足せるようにするため。
ルールは自分の文面を持つ。これが「代わりの手段」を忘れさせない仕組みで、
ルールを 1 件足すことが、代わりに何をすべきかを書くことを強制する。

## 3 つの区画

ルールファイルは `deny` `ask` `allow` の 3 つに分かれる。どれも同じ形の
ルールを並べるだけで、置かれた区画がその判定になる。

    version: 3
    deny:
      - id: guard-config
        match: Write|Edit|MultiEdit
        glob: "*/.claude/ccnavi/*"
        message: ガード自身の設定です。利用者に依頼してください。
    ask:
      - id: migrations
        match: Write|Edit
        glob: "*/migrations/*"
        message: 移行ファイルは実行前に人が中身を見ます。
    allow:
      - id: source
        match: Read|Write|Edit|MultiEdit
        glob: "*/ccnavi/*"

文面は `deny` と `ask` では必須。止めるなら代わりの手段を、聞くなら何を見て
判断すればよいかを、ルール自身が言わなければならない。`allow` では要らない。
通した呼び出しには誰にも何も返らないので、書いても届く先が無い。

## 強さ

同じ呼び出しに複数の区画が当たることがある。強い順に `deny` `ask` `allow`。
1 件でも `deny` に当たれば拒否で、`ask` があれば確認、どちらも無く `allow` に
当たれば許可になる。どこにも当たらなければ暗黙的 ask（判定は cli.py）。

強い側を先に見るのは、緩める側のルールを 1 行足しただけで守りが消える形を
作らないため。`allow` は「まだ何も言われていない場所」に許可を置くもので、
`deny` の穴を開ける道具ではない。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

import yaml

# このビルドが読めるルールファイルの書式の版。
# 2 で区画が 3 つに分かれ、ファイルの形式も JSON から YAML になった。
VERSION = 3

# 深刻度。ガードを壊すものと、弱めるだけのものを分ける。
SEVERITY_ERROR = "error"
SEVERITY_WARN = "warn"

# 区画の名前。強い順。cli.py の判定もこの順に見る。
DENY = "deny"
ASK = "ask"
ALLOW = "allow"
SECTIONS = (DENY, ASK, ALLOW)

# 文面が要る区画。allow は通すだけなので、書いても届く先が無い。
_NEEDS_MESSAGE = (DENY, ASK)

# Python の正規表現エンジンは後方参照と先読みを扱えるが、ルールファイルの契約は
# それを使わないことになっている。書ける範囲を狭いままにしておくと、ルールが
# エンジンをまたいでも同じ意味を保つ。加えて、この 2 つは組み合わせ爆発を起こす
# 書き方の入口でもあり、判定の途中で固まった hook は期限に達して素通りになる。
_UNSUPPORTED = (
    (r"(?=", "先読み"),
    (r"(?!", "否定先読み"),
    (r"(?<=", "後読み"),
    (r"(?<!", "否定後読み"),
)
_BACKREFERENCE = re.compile(r"\\[1-9]")


@dataclass
class Problem:
    """ルールファイルへの苦情 1 件。直せるように名指しする。"""

    severity: str
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.severity}: {self.rule or '(file)'}: {self.detail}"


@dataclass
class Rule:
    """探すものと、見つけたときに言うことの組。"""

    # id は報告でルールを名指しするための名前。壊れたものを指させるように。
    id: str = ""
    # match は対象のツール名を "|" で並べたもの。"Write|Edit" など。
    match: str = ""
    # glob は fnmatch と同じ意味の glob。文字列全体に当たるので、部分一致が
    # 欲しければ前後に "*" を自分で書く。"*git push*"、"*.pem"、"*/secrets/*" など。
    # 詳しくは globmatch.translate を参照。
    glob: str = ""
    # regex は本当に正規表現が要るときの逃げ道。これに手を伸ばしたルールこそ
    # いちばん厳しく見直す対象なので、glob の別の綴りではなく別の欄にしてある。
    # glob では書けない「語の左側の切れ目」が要るときも、こちらを使う。
    regex: str = ""
    # message は、なぜ止めたかと、代わりに何をすればよいかを言う。
    message: str = ""
    # decision はこのルールが置かれていた区画。当たったルールを 1 件だけ
    # 取り出しても、それがどの判定だったのかを言えるようにする。
    decision: str = ""

    compiled: re.Pattern | None = None

    def matches(self, tool: str, subject: str) -> bool:
        """このルールがこのツールと対象に当たるかどうか。"""
        if self.compiled is None or not subject:
            return False
        for want in self.match.split("|"):
            if want.strip() == tool:
                return self.compiled.search(subject) is not None
        return False


@dataclass
class RuleSet:
    """ルールファイル 1 本ぶん。区画ごとに分けて持つ。

    1 本の並びにして各ルールが自分の判定を名乗る形にもできるが、分けておくと
    「強い順に見る」が並びの順そのものになる。判定の側が区画を選び違える形を
    残さないほうが、あとから区画を足したときに事故が起きにくい。
    """

    version: int = 0
    deny: list[Rule] = field(default_factory=list)
    ask: list[Rule] = field(default_factory=list)
    allow: list[Rule] = field(default_factory=list)

    def section(self, name: str) -> list[Rule]:
        return {DENY: self.deny, ASK: self.ask, ALLOW: self.allow}[name]

    def all(self) -> Iterator[Rule]:
        """全区画を強い順に。検証と、区画をまたいだ数え上げのために。"""
        for name in SECTIONS:
            yield from self.section(name)


def load(path: str) -> tuple[RuleSet, list[Problem]]:
    """ルールファイルを読んで組み立てる。

    解釈できないルールは、黙って飛ばさずに落として名指しで報告する。
    黙って消えたルールは、誰も気づかないガードの穴になるから。
    組み立てられたルールはそのまま返すので、1 件の不備がガード全体を落とさない。

    読むのは safe_load に限る。任意の Python の型を組み立てる load は、
    エージェントが書ける場所にあるファイルに向けては使わない。

    YAML の解析の失敗は ValueError に包み直す。呼び手はここが投げるものを
    OSError と ValueError の 2 つで受けていて、素通りする例外が 1 つでもあると、
    ルールファイルの書き損じがそのまま hook の異常終了になる。既定に落ちる
    経路（REQ-PRE-06）を通らずに落ちるので、壊れたファイルを直す呼び出しも
    止まる。読み手を替えるたびに、包み直しの側も一緒に見ること。
    """
    with open(path, encoding="utf-8") as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise ValueError(f"{path} を YAML として読めない: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} のルールがキーと値の並びではない")
    return parse(data)


def parse(data: dict) -> tuple[RuleSet, list[Problem]]:
    """読み込み済みのルールを組み立てる。

    ファイルを開く部分と分けてあるのは、組み込みの既定ルールが同じ経路を通るため。
    別の道で組み立てると、ファイルから読んだときと既定に落ちたときで
    ルールの意味が食い違いうる。食い違えば、ガードが落ちている最中に
    さらに読み違えることになる。
    """
    rule_set = RuleSet(version=data.get("version") or 0)
    problems: list[Problem] = []

    if rule_set.version != VERSION:
        problems.append(
            Problem(
                SEVERITY_ERROR,
                "",
                f"ルール書式の版 {rule_set.version} は扱えない（このビルドが読むのは {VERSION}）。"
                f"版 1 は 1 本の `rules` の並び、版 2 は "
                f"`{'` `'.join(SECTIONS)}` の 3 区画で欄の名前が `pattern`。"
                "版 3 は欄の名前が `glob` で、意味も fnmatch の glob になった。"
                "文字列全体に当たるので、部分一致が要るなら前後に `*` を書く",
            )
        )

    for name in SECTIONS:
        raw_section = data.get(name)
        if raw_section is None:
            continue
        if not isinstance(raw_section, list):
            problems.append(Problem(SEVERITY_ERROR, f"({name})", f"`{name}` が並びではない"))
            continue
        for i, raw in enumerate(raw_section):
            rule, problem = _build(raw, name, i)
            if problem is not None:
                problems.append(problem)
                continue
            rule_set.section(name).append(rule)

    return rule_set, problems


def _build(raw: object, section: str, index: int) -> tuple[Rule, None] | tuple[None, Problem]:
    from .globmatch import translate

    where = f"{section}[{index}]"
    if not isinstance(raw, dict):
        return None, Problem(SEVERITY_ERROR, where, "ルールがキーと値の並びではない")

    rule = Rule(
        id=str(raw.get("id") or ""),
        match=str(raw.get("match") or ""),
        glob=str(raw.get("glob") or ""),
        regex=str(raw.get("regex") or ""),
        message=str(raw.get("message") or ""),
        decision=section,
    )
    name = f"{section}:{rule.id}" if rule.id else where

    if not rule.message and section in _NEEDS_MESSAGE:
        return None, Problem(
            SEVERITY_ERROR, name, "文面が無い。ルールは代わりに何をすべきかを言わなければならない"
        )
    if not rule.match:
        return None, Problem(SEVERITY_ERROR, name, "match が無い。どのツールにも当たらない")
    if not rule.glob and not rule.regex:
        return None, Problem(SEVERITY_ERROR, name, "glob も regex も無い")
    if rule.glob and rule.regex:
        return None, Problem(
            SEVERITY_ERROR, name, "glob と regex の両方がある。どちらで判定するのか決められない"
        )

    expression = rule.regex or translate(rule.glob)

    if rule.regex:
        unsupported = _unsupported(rule.regex)
        if unsupported:
            return None, Problem(SEVERITY_ERROR, name, f"{unsupported}は使えない")

    try:
        rule.compiled = re.compile(expression)
    except re.error as exc:
        return None, Problem(SEVERITY_ERROR, name, f"正規表現として組み立てられない: {exc}")

    return rule, None


def _unsupported(expression: str) -> str:
    for literal, label in _UNSUPPORTED:
        if literal in expression:
            return label
    if _BACKREFERENCE.search(expression):
        return "後方参照"
    return ""
