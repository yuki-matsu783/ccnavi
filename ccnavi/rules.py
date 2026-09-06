"""判定を動かすルール集合の読み込み。

ルールは実行ファイルの外に置く。作り直さずに足せるようにするため。
ルールは自分の文面を持つ。これが「代わりの手段」を忘れさせない仕組みで、
ルールを 1 件足すことが、代わりに何をすべきかを書くことを強制する。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# このビルドが読めるルールファイルの書式の版。
VERSION = 1

# 深刻度。ガードを壊すものと、弱めるだけのものを分ける。
SEVERITY_ERROR = "error"
SEVERITY_WARN = "warn"

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
    # pattern は日常の言い方。"git push *"、"*.pem"、"secrets/" など。
    # 記法の全体は pattern.translate を参照。
    pattern: str = ""
    # regex は本当に正規表現が要るときの逃げ道。これに手を伸ばしたルールこそ
    # いちばん厳しく見直す対象なので、pattern の別の綴りではなく別の欄にしてある。
    regex: str = ""
    # message は、なぜ止めたかと、代わりに何をすればよいかを言う。
    message: str = ""

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
    """ルールファイル 1 本ぶん。"""

    version: int = 0
    rules: list[Rule] = field(default_factory=list)


def load(path: str) -> tuple[RuleSet, list[Problem]]:
    """ルールファイルを読んで組み立てる。

    解釈できないルールは、黙って飛ばさずに落として名指しで報告する。
    黙って消えたルールは、誰も気づかないガードの穴になるから。
    組み立てられたルールはそのまま返すので、1 件の不備がガード全体を落とさない。
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} のルールがオブジェクトではない")
    return parse(data)


def parse(data: dict) -> tuple[RuleSet, list[Problem]]:
    """読み込み済みのルールを組み立てる。

    ファイルを開く部分と分けてあるのは、組み込みの既定ルールが同じ経路を通るため。
    別の道で組み立てると、ファイルから読んだときと既定に落ちたときで
    ルールの意味が食い違いうる。食い違えば、ガードが落ちている最中に
    さらに読み違えることになる。
    """
    raw_rules = data.get("rules")
    rule_set = RuleSet(version=data.get("version") or 0)
    problems: list[Problem] = []

    if rule_set.version != VERSION:
        problems.append(
            Problem(
                SEVERITY_ERROR,
                "",
                f"ルール書式の版 {rule_set.version} は扱えない（このビルドが読むのは {VERSION}）",
            )
        )

    for i, raw in enumerate(raw_rules if isinstance(raw_rules, list) else []):
        rule, problem = _build(raw, i)
        if problem is not None:
            problems.append(problem)
            continue
        rule_set.rules.append(rule)

    return rule_set, problems


def _build(raw: object, index: int) -> tuple[Rule, None] | tuple[None, Problem]:
    from .pattern import translate

    if not isinstance(raw, dict):
        return None, Problem(SEVERITY_ERROR, f"rules[{index}]", "ルールがオブジェクトではない")

    rule = Rule(
        id=str(raw.get("id") or ""),
        match=str(raw.get("match") or ""),
        pattern=str(raw.get("pattern") or ""),
        regex=str(raw.get("regex") or ""),
        message=str(raw.get("message") or ""),
    )
    name = rule.id or f"rules[{index}]"

    if not rule.message:
        return None, Problem(
            SEVERITY_ERROR, name, "文面が無い。ルールは代わりに何をすべきかを言わなければならない"
        )
    if not rule.match:
        return None, Problem(SEVERITY_ERROR, name, "match が無い。どのツールにも当たらない")
    if not rule.pattern and not rule.regex:
        return None, Problem(SEVERITY_ERROR, name, "pattern も regex も無い")
    if rule.pattern and rule.regex:
        return None, Problem(
            SEVERITY_ERROR, name, "pattern と regex の両方がある。どちらで判定するのか決められない"
        )

    expression = rule.regex or translate(rule.pattern)

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
