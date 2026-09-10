"""実績で測るリスク。`.claude/ccnavi/risk.yml` を読み、子を閉じるときに差分を数える。

## 宣言ではなく実績を測る

計画のときに「軽い」と思った作業が、やってみたら大きな変更になることがある。
宣言（チケットの範囲や `human_review.required: false`）だけを信じると、それが
レビューを通らずに進む。だから点は、子を閉じるときにその子の作業ツリーで
`base_sha..HEAD` の差分を数えて付ける。宣言の広さで数える点は持たない。
宣言の広さは、親がチケットを書くときに `human_review.reason` で言えばよい。

## 点が何をするか

段階の名前は 4 つで固定（LOW / MEDIUM / HIGH / CRITICAL）。閾値は `levels` で動かす。
`HIGH` 以上なら、種類が `review: none` でも子が `required: false` でも、そのフェーズは
人間レビューが要る扱いになりゲートが閉じる。実績で宣言を厳しい側にだけ上書きする。
実績が小さくても、宣言のレビュー要を下げることはしない。

## 項目は 3 系統

- 定量（組み込み）: `lines_over` / `files_over` / `deleted_over` / `glob`。差分から数える
- 定量（スクリプト）: `script:` に書いた sh を exe が走らせ、点を受け取る。置ける場所は
  guard の内側だけ。エージェントが書ける場所のスクリプトを許すと、自分のリスクを自分で決められる
- 定性（サブエージェント）: `judge:` に書いた問いを、親がサブエージェントに判断させ、
  `ccnavi-ticket.sh judge` で yes / no を記録する。判定が揃うまで子は閉じられない

測れなかった項目（スクリプトの失敗、読めない出力）は重い側に倒し、その項目の点を加える。
「測れないから 0」にすると、壊れたスクリプトがリスクを消す。

## 書式

    version: 1
    levels: {medium: 20, high: 40, critical: 70}
    factors:
      - {id: big-diff,   points: 25, lines_over: 300,   message: 行数が多い}
      - {id: many-files, points: 15, files_over: 10,    message: ファイルが多い}
      - {id: ci,         points: 35, glob: ".github/**", max: 35, message: CI に触った}
      - {id: deletes,    points: 20, deleted_over: 3,   message: 消したファイルが多い}
      - {id: complexity, points: 30, script: .claude/ccnavi/risk/complexity.sh, message: 複雑度}
      - {id: untested,   points: 30, judge: テストの無い振る舞いの変更を含むか, message: テスト無し}

ファイルが無ければ組み込みの既定（上の定量 4 項目と同じ値）。壊れていれば組み込みに落ち、
そのことは --lint と子を閉じるときの出力が言う。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field

import yaml

from . import globmatch, settings, tree
from .rules import SEVERITY_ERROR, SEVERITY_WARN, Problem

VERSION = 1

LEVEL_LOW = "LOW"
LEVEL_MEDIUM = "MEDIUM"
LEVEL_HIGH = "HIGH"
LEVEL_CRITICAL = "CRITICAL"
LEVELS = (LEVEL_LOW, LEVEL_MEDIUM, LEVEL_HIGH, LEVEL_CRITICAL)
# レビューを要る扱いに上書きする段階。
ESCALATE_FROM = (LEVEL_HIGH, LEVEL_CRITICAL)

DEFAULT_LEVELS = {"medium": 20, "high": 40, "critical": 70}

KIND_LINES = "lines_over"
KIND_FILES = "files_over"
KIND_DELETED = "deleted_over"
KIND_GLOB = "glob"
KIND_SCRIPT = "script"
KIND_JUDGE = "judge"
KINDS = (KIND_LINES, KIND_FILES, KIND_DELETED, KIND_GLOB, KIND_SCRIPT, KIND_JUDGE)

# スクリプトを置いてよい場所（ワークスペースルートからの相対の先頭）。guard の内側。
SCRIPT_HOMES = (".claude/ccnavi/", ".claude/scripts/")
SCRIPT_TIMEOUT_SECONDS = 30.0

BUILTIN = "(builtin)"

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass
class Factor:
    id: str
    points: int
    message: str
    kind: str
    # lines_over / files_over / deleted_over は int、glob / script / judge は str。
    value: object
    # glob の上限。当たるごとに加点するので、無ければ青天井。
    max: int | None = None
    compiled: re.Pattern | None = None

    def matches(self, rel: str) -> bool:
        return self.compiled is not None and self.compiled.match(rel) is not None


@dataclass
class Definition:
    levels: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_LEVELS))
    factors: list[Factor] = field(default_factory=list)
    # どこから読んだか。組み込みなら BUILTIN。
    source: str = BUILTIN
    # 読めなかった理由（組み込みに落ちたとき）。
    fallback: str = ""

    @property
    def judges(self) -> list[Factor]:
        return [f for f in self.factors if f.kind == KIND_JUDGE]

    def level_of(self, points: int) -> str:
        if points >= self.levels.get("critical", DEFAULT_LEVELS["critical"]):
            return LEVEL_CRITICAL
        if points >= self.levels.get("high", DEFAULT_LEVELS["high"]):
            return LEVEL_HIGH
        if points >= self.levels.get("medium", DEFAULT_LEVELS["medium"]):
            return LEVEL_MEDIUM
        return LEVEL_LOW

    def factor(self, ident: str) -> Factor | None:
        for f in self.factors:
            if f.id == ident:
                return f
        return None


def builtin() -> Definition:
    factors, _ = _factors(
        [
            {"id": "big-diff", "points": 25, "lines_over": 300, "message": "行数が多い"},
            {"id": "many-files", "points": 15, "files_over": 10, "message": "ファイルが多い"},
            {
                "id": "ci",
                "points": 35,
                "glob": ".github/**",
                "max": 35,
                "message": "CI やエージェント定義に触った",
            },
            {"id": "deletes", "points": 20, "deleted_over": 3, "message": "消したファイルが多い"},
        ],
        BUILTIN,
    )
    return Definition(factors=factors)


def load(path: str) -> tuple[Definition, list[Problem]]:
    """定義を読む。無ければ組み込み。壊れていれば組み込みに落ち、苦情を返す。"""
    if not path:
        return builtin(), []
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return builtin(), []
    except OSError as exc:
        fallen = builtin()
        fallen.fallback = f"{path} を読めない ({exc})"
        return fallen, [Problem(SEVERITY_ERROR, "(risk)", fallen.fallback)]
    definition, problems = parse(text, path)
    if definition is None:
        fallen = builtin()
        fallen.fallback = f"{path} が壊れている。組み込みの配点で数える"
        return fallen, problems
    return definition, problems


def parse(text: str, where: str = "(risk)") -> tuple[Definition | None, list[Problem]]:
    problems: list[Problem] = []
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, [Problem(SEVERITY_ERROR, where, f"YAML として読めない: {exc}")]
    if not isinstance(data, dict):
        return None, [Problem(SEVERITY_ERROR, where, "最上位が辞書ではない")]
    if data.get("version") != VERSION:
        return None, [
            Problem(
                SEVERITY_ERROR,
                where,
                f"版 {data.get('version')!r} は扱えない（このビルドが読むのは {VERSION}）",
            )
        ]
    levels = dict(DEFAULT_LEVELS)
    raw_levels = data.get("levels")
    if raw_levels is not None:
        if not isinstance(raw_levels, dict):
            return None, [Problem(SEVERITY_ERROR, where, "`levels` が辞書ではない")]
        for name in ("medium", "high", "critical"):
            if name in raw_levels:
                value = raw_levels[name]
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    problems.append(
                        Problem(SEVERITY_ERROR, where, f"`levels.{name}` が 0 以上の整数ではない")
                    )
                    continue
                levels[name] = value
        unknown = sorted(set(raw_levels) - {"medium", "high", "critical", "low"})
        for name in unknown:
            problems.append(
                Problem(SEVERITY_WARN, where, f"`levels.{name}` は知らない段階。名前は固定")
            )
    if not (levels["medium"] <= levels["high"] <= levels["critical"]):
        problems.append(
            Problem(SEVERITY_ERROR, where, "`levels` は medium <= high <= critical の順で書く")
        )
    raw_factors = data.get("factors")
    if raw_factors is None:
        raw_factors = []
    if not isinstance(raw_factors, list):
        return None, [Problem(SEVERITY_ERROR, where, "`factors` が並びではない")]
    factors, more = _factors(raw_factors, where)
    problems += more
    if any(p.severity == SEVERITY_ERROR for p in problems):
        return None, problems
    return Definition(levels=levels, factors=factors, source=where), problems


def _factors(raw: list, where: str) -> tuple[list[Factor], list[Problem]]:
    problems: list[Problem] = []
    factors: list[Factor] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            problems.append(Problem(SEVERITY_ERROR, where, f"`factors[{i}]` が辞書ではない"))
            continue
        ident = str(item.get("id") or "").strip()
        if not _ID.match(ident):
            problems.append(Problem(SEVERITY_ERROR, where, f"`factors[{i}].id` が無いか形が違う"))
            continue
        if ident in seen:
            problems.append(Problem(SEVERITY_ERROR, where, f"`{ident}` が重複している"))
            continue
        seen.add(ident)
        points = item.get("points")
        if not isinstance(points, int) or isinstance(points, bool) or points < 0:
            problems.append(Problem(SEVERITY_ERROR, ident, "`points` が 0 以上の整数ではない"))
            continue
        kinds = [k for k in KINDS if k in item]
        if len(kinds) != 1:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    ident,
                    "当て方は 1 つ（" + " / ".join(KINDS) + "）を書く",
                )
            )
            continue
        kind = kinds[0]
        value = item[kind]
        message = str(item.get("message") or "").strip() or ident
        maximum = item.get("max")
        if maximum is not None and (
            not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 0
        ):
            problems.append(Problem(SEVERITY_ERROR, ident, "`max` が 0 以上の整数ではない"))
            continue
        compiled = None
        if kind in (KIND_LINES, KIND_FILES, KIND_DELETED):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                problems.append(Problem(SEVERITY_ERROR, ident, f"`{kind}` が 0 以上の整数ではない"))
                continue
        elif kind == KIND_GLOB:
            if not isinstance(value, str) or not value.strip():
                problems.append(Problem(SEVERITY_ERROR, ident, "`glob` が文字列ではない"))
                continue
            glob = value.strip().replace("\\", "/").strip("/")
            flags = re.IGNORECASE if tree.CASE_INSENSITIVE else 0
            try:
                compiled = re.compile("^" + globmatch.translate(glob), flags)
            except re.error as exc:
                problems.append(Problem(SEVERITY_ERROR, ident, f"`glob` を式にできない: {exc}"))
                continue
            value = glob
        elif kind == KIND_SCRIPT:
            if not isinstance(value, str) or not value.strip():
                problems.append(Problem(SEVERITY_ERROR, ident, "`script` が文字列ではない"))
                continue
            rel = value.strip().replace("\\", "/")
            if os.path.isabs(rel) or ".." in rel or not rel.startswith(SCRIPT_HOMES):
                problems.append(
                    Problem(
                        SEVERITY_ERROR,
                        ident,
                        "`script` は "
                        + " か ".join(f"`{h}`" for h in SCRIPT_HOMES)
                        + " の下に、ワークスペースルートからの相対で置く（守られた場所）",
                    )
                )
                continue
            value = rel
        elif kind == KIND_JUDGE:
            if not isinstance(value, str) or not value.strip():
                problems.append(Problem(SEVERITY_ERROR, ident, "`judge` は問いの文を書く"))
                continue
            value = value.strip()
        factors.append(
            Factor(
                id=ident,
                points=points,
                message=message,
                kind=kind,
                value=value,
                max=maximum,
                compiled=compiled,
            )
        )
    return factors, problems


def load_definition(conf: settings.Settings) -> Definition:
    """設定から定義を読む。壊れていれば組み込み（fallback に理由）。"""
    definition, _ = load(conf.risk)
    return definition


# ---- 差分


@dataclass
class Change:
    path: str
    added: int = 0
    deleted: int = 0
    # A / M / D / R など。git の name-status の 1 文字目。
    status: str = "M"


@dataclass
class Diff:
    changes: list[Change] = field(default_factory=list)
    base: str = ""
    head: str = ""

    @property
    def lines(self) -> int:
        return sum(c.added + c.deleted for c in self.changes)

    @property
    def files(self) -> int:
        return len(self.changes)

    @property
    def deleted_files(self) -> int:
        return sum(1 for c in self.changes if c.status.startswith("D"))

    def summary(self) -> str:
        return (
            f"{self.files} ファイル、+{sum(c.added for c in self.changes)} "
            f"-{sum(c.deleted for c in self.changes)} 行、消したファイル {self.deleted_files}"
        )


def measure(worktree: str, base: str, head: str = "HEAD") -> tuple[Diff | None, str]:
    """`base..head` の差分を数える。読めなければ None と理由。"""
    if not base:
        return None, "基準点（base_sha）が無い"
    rc, out = _git(worktree, ["diff", "--numstat", "-z", f"{base}..{head}"])
    if rc != 0:
        return None, "基準点からの差分を読めない"
    changes: dict[str, Change] = {}
    # -z の numstat は `added\tdeleted\tpath\0`。rename は `added\tdeleted\t\0old\0new\0`。
    parts = out.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if not entry:
            continue
        cols = entry.split("\t")
        if len(cols) < 3:
            continue
        added = _int(cols[0])
        deleted = _int(cols[1])
        path = cols[2]
        if path == "" and i + 1 < len(parts):
            # rename: 次の 2 つが旧と新。
            path = parts[i + 1]
            i += 2
        path = path.replace("\\", "/")
        changes[path] = Change(path=path, added=added, deleted=deleted)
    rc, out = _git(worktree, ["diff", "--name-status", "-z", f"{base}..{head}"])
    if rc == 0:
        parts = [p for p in out.split("\0")]
        i = 0
        while i < len(parts):
            status = parts[i]
            i += 1
            if not status:
                continue
            if status.startswith(("R", "C")) and i + 1 < len(parts):
                path = parts[i + 1].replace("\\", "/")
                i += 2
            elif i < len(parts):
                path = parts[i].replace("\\", "/")
                i += 1
            else:
                break
            if path in changes:
                changes[path].status = status[:1]
            else:
                changes[path] = Change(path=path, status=status[:1])
    rc, sha = _git(worktree, ["rev-parse", head])
    return Diff(changes=list(changes.values()), base=base, head=sha.strip()), ""


# ---- 判定


@dataclass
class Hit:
    id: str
    points: int
    detail: str


@dataclass
class Score:
    points: int = 0
    level: str = LEVEL_LOW
    hits: list[Hit] = field(default_factory=list)
    # 測れなかった項目（重い側に倒して加点済み）。
    unmeasured: list[str] = field(default_factory=list)
    # 判定の無い定性項目。揃うまで子は閉じられない。
    pending: list[Factor] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"リスク: {self.points} ({self.level})"]
        out += [f"  +{h.points} {h.detail}" for h in self.hits]
        out += [f"  ! {u}" for u in self.unmeasured]
        return out

    def as_dict(self) -> dict:
        return {
            "points": self.points,
            "level": self.level,
            "hits": [{"id": h.id, "points": h.points, "detail": h.detail} for h in self.hits],
            "unmeasured": list(self.unmeasured),
        }


def evaluate(
    definition: Definition,
    diff: Diff,
    root: str,
    worktree: str,
    env: dict[str, str],
    judgements: dict[str, dict],
) -> Score:
    """差分と判定から点を出す。定性項目に判定が無ければ pending に積む。"""
    score = Score()
    for f in definition.factors:
        if f.kind == KIND_LINES:
            if diff.lines > int(f.value):
                detail = f"{f.message}（{diff.lines} 行 > {f.value}）"
                score.hits.append(Hit(f.id, f.points, detail))
        elif f.kind == KIND_FILES:
            if diff.files > int(f.value):
                score.hits.append(
                    Hit(f.id, f.points, f"{f.message}（{diff.files} ファイル > {f.value}）")
                )
        elif f.kind == KIND_DELETED:
            if diff.deleted_files > int(f.value):
                score.hits.append(
                    Hit(f.id, f.points, f"{f.message}（消した {diff.deleted_files} > {f.value}）")
                )
        elif f.kind == KIND_GLOB:
            hit = [c.path for c in diff.changes if f.matches(c.path)]
            if hit:
                points = f.points * len(hit)
                if f.max is not None:
                    points = min(points, f.max)
                shown = ", ".join(hit[:3]) + ("…" if len(hit) > 3 else "")
                score.hits.append(Hit(f.id, points, f"{f.message}（{shown}）"))
        elif f.kind == KIND_SCRIPT:
            points, note = run_script(root, str(f.value), worktree, env)
            if points is None:
                score.hits.append(Hit(f.id, f.points, f"{f.message}（測れなかった: {note}）"))
                score.unmeasured.append(f"{f.id}: {note}")
            elif points > 0:
                score.hits.append(Hit(f.id, points, f"{f.message}（{note or 'スクリプト'}）"))
        elif f.kind == KIND_JUDGE:
            verdict = judgements.get(f.id)
            if not isinstance(verdict, dict) or verdict.get("head") != diff.head:
                score.pending.append(f)
            elif verdict.get("hit"):
                reason = str(verdict.get("reason") or "").strip()
                score.hits.append(Hit(f.id, f.points, f"{f.message}（判定: {reason}）"))
    score.points = sum(h.points for h in score.hits)
    score.level = definition.level_of(score.points)
    return score


def run_script(root: str, rel: str, worktree: str, env: dict[str, str]) -> tuple[int | None, str]:
    """スクリプトを走らせて点を受け取る。測れなければ None と理由。"""
    path = os.path.join(root, rel.replace("/", os.sep))
    if not os.path.isfile(path):
        return None, f"{rel} が無い"
    environment = dict(os.environ)
    environment.update(env)
    try:
        done = subprocess.run(
            ["sh", path],
            cwd=worktree,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=SCRIPT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"{rel} を走らせられない ({exc.__class__.__name__})"
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()
        return None, f"{rel} が {done.returncode} で終わった" + (f": {tail[-1]}" if tail else "")
    text = done.stdout.strip()
    if not text:
        return None, f"{rel} が何も出さなかった"
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        points = data.get("points")
        if isinstance(points, int) and not isinstance(points, bool) and points >= 0:
            return points, str(data.get("message") or "")
        return None, f"{rel} の JSON に `points`（0 以上の整数）が無い"
    if isinstance(data, int) and not isinstance(data, bool) and data >= 0:
        return data, ""
    return None, f"{rel} の出力を点として読めない: {text[:60]}"


def judge_prompt(child: str, parent: str, diff: Diff, pending: list[Factor], worktree: str) -> str:
    """親がサブエージェントに渡す、定性項目の問いと差分の要約。"""
    lines = [
        f"# {child} のリスク判定（定性）",
        "",
        f"親: {parent}。作業ツリー: {worktree}。差分: `{diff.base[:12]}..{diff.head[:12]}`"
        f"（{diff.summary()}）。",
        "",
        "次の問いに、差分を読んで yes / no で答え、根拠を 1〜3 行で書く。",
        "判断するのはこの文書を渡されたサブエージェント。記録するのは親で、",
        f"'sh .claude/scripts/ccnavi-ticket.sh judge {child} <項目> yes|no --reason <根拠>' "
        "で 1 項目ずつ。",
        "",
    ]
    for f in pending:
        lines += [f"## {f.id}（yes なら +{f.points}）", "", str(f.value), ""]
    lines += ["## 変更したファイル", ""]
    for c in sorted(diff.changes, key=lambda x: x.path):
        lines.append(f"- {c.status} {c.path} (+{c.added} -{c.deleted})")
    lines.append("")
    return "\n".join(lines)


def _int(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        # バイナリは `-`。
        return 0


def _git(cwd: str, args: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(
            ["git", "-c", "core.quotePath=false", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return done.returncode, done.stdout
