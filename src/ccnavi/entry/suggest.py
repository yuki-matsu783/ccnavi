"""判定の記録から、ルールの候補を起こす（`ccnavi --suggest`、issue #149 の 4）。

記録（`logs/decisions.jsonl` と、同じ置き場の `decisions.*.jsonl`）を数えて、2 種類の候補を出す。

- ルールを足す候補。どのルールも言及せず（UNDECLARED）権限モードへ渡った呼び出しが、
  同じ形で何度も来ているもの。`ask` のルールの下書きにする。ルールが薄い場所は
  handover の数で分かる（audit.HANDOVER の説明）が、数えるのはユーザの手では手間が大きい
- 文面を見直す候補。同じルールが、同じ呼び出しを繰り返し止めているもの。止められた側が
  文面から次の一手を読めず、言い換えて打ち直している疑いがある（repeat と同じ数え方）。
  いまのルールをそのまま、繰り返し止めた呼び出しを見本につけて出す。直すのは `message`

出すのは `deny` と `ask` の候補だけで、`allow` は出さない。記録から通す側の候補を起こすと、
「よく来るから通す」になり、判定を緩める変更を機械が勧めることになる。

出すのは検証を通ったものだけ。候補ごとに、`--lint` と同じ読み（lint_rules._rules）で
ルールを確かめ、`--test-samples` と同じ判定（diagnose.try_one）で見本を回し、期待した
タイプにならなかったものは落とす（落とした数は出す）。ルールを足す候補は、共通レイヤーの
ルールファイルのコピーに 1 件足した一時ファイルで試す。本物のファイルには書かない。

形は `rules.yml` の 1 タイプぶんと、`rule-samples.yml` の 1 タイプぶんの組。置くのはユーザ
（`/ccnavi-config` の手順）。

実行ファイルはネットワークに出ない。読むのは記録と設定だけ。
"""

from __future__ import annotations

import dataclasses
import glob
import io
import json
import os
import re
import tempfile
from collections import Counter
from typing import TextIO
from urllib.parse import urlsplit

import yaml

from ..hook import reasons
from ..infra import hookio, settings, yamlread
from ..policy import ruleload, rules
from ..records import audit, repeat
from . import diagnose, lint_rules

# `--suggest --json` の形の版。読み手は VS Code 拡張のルール管理画面。形を変えたら上げる。
SUGGEST_VERSION = 1

# 候補の種類。
KIND_RULE = "rule"
KIND_MESSAGE = "message"

# ルールを足す候補にする、渡った回数の下限。数回ならルールを書くよりユーザが見たほうが早い。
HANDOVER_MIN = 5
# 種類ごとに出す候補の上限と、候補 1 件につける見本の上限。
CANDIDATE_LIMIT = 10
SAMPLE_LIMIT = 3
# 候補の id の前置き。ユーザが名前を付け直す前提の仮の名前であることを表記で言う。
ID_PREFIX = "suggest-"

# 記録の subject が上限で切られた目印（audit._limited）。切れた文字列は見本にできない。
_CUT = re.compile(r"…\(\+\d+\)$")
_ENV_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=\S*")
_COMMAND = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
_SUBCOMMAND = re.compile(r"[a-z][a-z0-9_-]*")
_GLOB_META = re.compile(r"[*?\[\]\\]")


def log_files(log: str) -> list[str]:
    """数える記録。いまの記録と、同じ置き場で同じ名前から回した `<名前>.*<拡張子>`。"""
    if not log:
        return []
    base, ext = os.path.splitext(os.path.basename(log))
    rotated = glob.glob(os.path.join(glob.escape(os.path.dirname(log) or "."), f"{base}.*{ext}"))
    found = sorted(p for p in rotated if os.path.isfile(p))
    if os.path.isfile(log):
        found.append(log)
    return found


def read_records(paths: list[str]) -> list[dict]:
    """記録を読む。読めない行は飛ばす（途中で切れた行、手で書いた行）。"""
    out = []
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(rec, dict):
                        out.append(rec)
        except OSError:
            continue
    return out


@dataclasses.dataclass
class Candidate:
    kind: str
    section: str
    tool: str
    count: int
    summary: str
    # rules.yml に置く 1 件（書いた表記の形）と、それをどのレイヤーのファイルに置くか。
    rule: dict
    layer: str
    rules_path: str
    # rule-samples.yml の 1 件ずつ（tool / subject / why）。subject のルートは `/repo`。
    samples: list[dict]

    def as_yaml(self) -> str:
        body = {
            "rules": {self.section: [self.rule]},
            "samples": {self.section: self.samples},
        }
        dumped = yaml.safe_dump(body, allow_unicode=True, sort_keys=False, width=100)
        return f"# {self.summary}\n{dumped}"

    def as_json(self) -> dict:
        return {
            "kind": self.kind,
            "section": self.section,
            "id": self.rule.get("id", ""),
            "tool": self.tool,
            "count": self.count,
            "summary": self.summary,
            "layer": self.layer,
            "rules_path": self.rules_path,
            "rule": self.rule,
            "samples": self.samples,
            "yaml": self.as_yaml(),
        }


def _placeholder(subject: str, root: str) -> str:
    """見本に書く文字列。ルートを `/repo` にする（diagnose.SAMPLE_PLACEHOLDER）。"""
    for spelled in {os.path.realpath(root), root}:
        if spelled and spelled != "/":
            subject = subject.replace(spelled, diagnose.SAMPLE_PLACEHOLDER)
    return subject


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "x"


def _shape(tool: str, subject: str, root: str) -> tuple[str, dict, str] | None:
    """渡った呼び出しの形（数える鍵）と、それに当てるルールの下書きと、id の元。

    形にできなければ None。

    - Bash: 先頭のコマンドの名前と、続く語がサブコマンドらしければそれ（`npm install`）
    - パスのツール: 置かれたディレクトリの下（`{root}/docs/*`）
    - WebFetch: 同じ scheme とホストの下
    """
    if tool == "Bash":
        words = subject.strip().split("\n", 1)[0].split()
        while words and _ENV_ASSIGN.fullmatch(words[0]):
            words.pop(0)
        if not words:
            return None
        head = re.split(r"[\\/]", words[0])[-1]
        if not _COMMAND.fullmatch(head):
            return None
        sub = words[1] if len(words) > 1 and _SUBCOMMAND.fullmatch(words[1]) else ""
        spelled = f"{head} {sub}".strip()
        pattern = r"(^|[\s;&|(])(\S*[\\/])?" + re.escape(head)
        if sub:
            pattern += r"\s+" + re.escape(sub)
        pattern += r"(\s|$)"
        return spelled, {"regex": pattern}, spelled
    if tool in ruleload.PATH_TOOLS:
        where = os.path.dirname(subject.replace("\\", "/"))
        top = rules.root_glob(root)
        if not where or _GLOB_META.search(where):
            return None
        if where == top or where.startswith(top + "/"):
            where = rules.ROOT_PLACEHOLDER + where[len(top) :]
        elif where == "/":
            return None
        spelled = f"{where}/*"
        name = "-".join(where.replace(rules.ROOT_PLACEHOLDER, "").strip("/").split("/")[-2:])
        return spelled, {"glob": spelled}, f"{tool}-{name or 'root'}"
    if tool == "WebFetch":
        parts = urlsplit(subject)
        if not parts.scheme or not parts.netloc or _GLOB_META.search(parts.netloc):
            return None
        spelled = f"{parts.scheme}://{parts.netloc}/*"
        return spelled, {"glob": spelled}, f"fetch-{parts.netloc}"
    return None


def _usable(rec: dict) -> bool:
    """候補の材料にできる実行前チェックの行か。切れた文字列と、読み切れなかった呼び出しは使わない。"""
    subject = rec.get("subject")
    return (
        rec.get("event") == hookio.PRE_TOOL_USE
        and isinstance(subject, str)
        and bool(subject)
        and not _CUT.search(subject)
        and not rec.get("degraded")
    )


def _handover_groups(
    records: list[dict], root: str
) -> list[tuple[str, str, dict, str, int, list[str]]]:
    """ルールが言及しなかった呼び出しを形ごとにまとめ、多い順に並べる。"""
    groups: dict[tuple[str, str], dict] = {}
    for rec in records:
        if not _usable(rec):
            continue
        if rec.get("decision") != audit.HANDOVER and rec.get("code") != reasons.CODE_UNDECLARED:
            continue
        tool, subject = str(rec.get("tool") or ""), rec["subject"]
        shaped = _shape(tool, subject, root)
        if shaped is None:
            continue
        spelled, form, name = shaped
        g = groups.setdefault(
            (tool, spelled), {"form": form, "name": name, "subjects": Counter(), "n": 0}
        )
        g["n"] += 1
        g["subjects"][subject] += 1
    ranked = sorted(groups.items(), key=lambda kv: (-kv[1]["n"], kv[0]))
    return [
        (tool, spelled, g["form"], g["name"], g["n"], [s for s, _ in g["subjects"].most_common()])
        for (tool, spelled), g in ranked
        if g["n"] >= HANDOVER_MIN
    ]


def _repeated_denies(records: list[dict], n: int) -> list[tuple[str, str, int, list[str]]]:
    """同じルールが同じ呼び出しを n 回以上止めたもの。ルールごとに（id, ツール, 回数, 対象）。"""
    counts: Counter = Counter()
    spelled: dict[tuple[str, str], str] = {}
    tools: dict[str, str] = {}
    for rec in records:
        if not _usable(rec) or rec.get("decision") != audit.DENY or not rec.get("enforced"):
            continue
        ids = rec.get("rules") or []
        if not ids or not isinstance(ids[0], str):
            continue
        key = (ids[0], repeat.normalize(rec["subject"]))
        counts[key] += 1
        spelled.setdefault(key, rec["subject"])
        tools.setdefault(ids[0], str(rec.get("tool") or ""))
    by_rule: dict[str, list[tuple[int, str]]] = {}
    for (rule_id, norm), got in counts.items():
        if got >= n:
            by_rule.setdefault(rule_id, []).append((got, spelled[(rule_id, norm)]))
    out = []
    for rule_id, hits in by_rule.items():
        hits.sort(key=lambda h: (-h[0], h[1]))
        out.append((rule_id, tools[rule_id], sum(h[0] for h in hits), [h[1] for h in hits]))
    out.sort(key=lambda r: (-r[2], r[0]))
    return out


def _written_rule(rule: rules.Rule) -> dict:
    """ルール 1 件を、書いた表記の形に戻す。"""
    out: dict = {"id": rule.bare_id or rule.id, "match": rule.match}
    out["glob" if rule.glob else "regex"] = rule.glob or rule.regex
    if rule.message:
        out["message"] = rule.message
    for key, value in (
        ("additionalContext", rule.additional_context),
        ("additionalContextOnce", rule.additional_context_once),
        ("additionalContextFile", rule.additional_context_file),
        ("additionalContextOnceFile", rule.additional_context_once_file),
    ):
        if value:
            out[key] = value
    if rule.every_written is not None:
        out["every"] = rule.every_written
    return out


def _lint_errors(path: str, root: str, rule_id: str, layer: bool) -> bool:
    """そのルールに `--lint` の error が付くか。"""
    return any(
        p.severity == rules.SEVERITY_ERROR and p.rule == rule_id
        for p in lint_rules._rules(path, root, layer=layer)
    )


def _samples_pass(
    conf: settings.Settings, root: str, samples: list[dict], want: str, rule_id: str
) -> bool:
    """見本がぜんぶ期待したタイプになり、候補のルールに当たるか（`--test-samples` と同じ判定）。"""
    for sample in samples:
        subject = sample["subject"].replace(diagnose.SAMPLE_PLACEHOLDER, root)
        out = diagnose.try_one(io.StringIO(), conf, root, sample["tool"], subject)
        if out["verdict"] != want or rule_id not in [h["id"] for h in out["rules"]]:
            return False
    return True


def _rule_candidates(
    conf: settings.Settings, root: str, records: list[dict]
) -> tuple[list[Candidate], int]:
    groups = _handover_groups(records, root)
    if not groups:
        return [], 0
    try:
        with open(conf.rules, encoding="utf-8") as f:
            base = yamlread.safe_load(f.read())
    except (OSError, yaml.YAMLError):
        # 共通レイヤーを読めなければ、足したコピーで試せない。試せない候補は出さない。
        return [], len(groups)
    if not isinstance(base, dict):
        return [], len(groups)
    taken = {
        str(r.get("id"))
        for name in rules.SECTIONS
        for r in (base.get(name) or [])
        if isinstance(r, dict)
    }
    found: list[Candidate] = []
    dropped = 0
    with tempfile.TemporaryDirectory(prefix="ccnavi-suggest-") as scratch:
        for tool, spelled, form, name, count, subjects in groups:
            if len(found) >= CANDIDATE_LIMIT:
                break
            rule_id = ID_PREFIX + _slug(name)
            while rule_id in taken:
                rule_id += "-2"
            taken.add(rule_id)
            rule = {"id": rule_id, "match": tool, **form}
            samples = [
                {
                    "tool": tool,
                    "subject": _placeholder(s, root),
                    "why": f"記録で、どのルールにも当たらず権限モードへ渡った形（{spelled}）",
                }
                for s in subjects[:SAMPLE_LIMIT]
            ]
            data = dict(base)
            data[rules.ASK] = [*(base.get(rules.ASK) or []), rule]
            path = os.path.join(scratch, f"{rule_id}.yml")
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
            trial = dataclasses.replace(conf, rules=path)
            if _lint_errors(path, root, rule_id, False) or not _samples_pass(
                trial, root, samples, rules.ASK, rule_id
            ):
                dropped += 1
                continue
            found.append(
                Candidate(
                    kind=KIND_RULE,
                    section=rules.ASK,
                    tool=tool,
                    count=count,
                    summary=(
                        f"ルールを足す候補: {tool} の {spelled} が {count} 回、"
                        "どのルールにも当たらず権限モードへ渡った。"
                        "止めるべきなら deny に移して message を書いてください"
                    ),
                    rule=rule,
                    layer=ruleload.LAYER_COMMON,
                    rules_path=conf.rules,
                    samples=samples,
                )
            )
    return found, dropped


def _message_candidates(
    conf: settings.Settings, root: str, records: list[dict], n: int
) -> tuple[list[Candidate], int]:
    repeated = _repeated_denies(records, n)
    if not repeated:
        return [], 0
    views = [v for v in ruleload.survey(io.StringIO(), conf, root) if not v.unreadable]
    found: list[Candidate] = []
    dropped = 0
    for rule_id, tool, count, subjects in repeated:
        if len(found) >= CANDIDATE_LIMIT:
            break
        hit = next(
            (
                (view, rule)
                for view in views
                for rule in view.rule_set.section(rules.DENY)
                if rule.id == rule_id
            ),
            None,
        )
        if hit is None:
            # ルールファイルの外から来た根拠（組み込みの保護・チケット）か、もう無いルール。
            # 文面を直せる先が無いので候補にしない。
            dropped += 1
            continue
        view, rule = hit
        written = _written_rule(rule)
        samples = [
            {
                "tool": tool,
                "subject": _placeholder(s, root),
                "why": f"記録で、{rule_id} が同じ呼び出しを {n} 回以上止めた形",
            }
            for s in subjects[:SAMPLE_LIMIT]
        ]
        layer = view.name != ruleload.LAYER_COMMON
        if _lint_errors(view.path, root, written["id"], layer) or not _samples_pass(
            conf, root, samples, rules.DENY, rule_id
        ):
            dropped += 1
            continue
        found.append(
            Candidate(
                kind=KIND_MESSAGE,
                section=rules.DENY,
                tool=tool,
                count=count,
                summary=(
                    f"message を見直す候補: {rule_id} が同じ呼び出しを繰り返し止めた"
                    f"（{count} 回）。エージェントが文面から次に何をすればよいかを"
                    "読み取れていない疑い"
                ),
                rule=written,
                layer=view.name,
                rules_path=view.path,
                samples=samples,
            )
        )
    return found, dropped


def collect(conf: settings.Settings, root: str) -> dict:
    """候補を集めて 1 つの辞書にする。鍵は README「候補の JSON」。"""
    # 試験と同じく記録を持たない。候補の検証で「1 度だけ渡す文」を消費しない。
    conf = dataclasses.replace(conf, state="")
    paths = log_files(conf.log)
    records = read_records(paths)
    n = repeat.threshold(conf.deny_repeat)
    added, dropped_rules = _rule_candidates(conf, root, records)
    review, dropped_messages = _message_candidates(conf, root, records, n)
    return {
        "version": SUGGEST_VERSION,
        "root": root,
        "rules_path": conf.rules,
        "logs": paths,
        "records": len(records),
        "candidates": [c.as_json() for c in added + review],
        "dropped": dropped_rules + dropped_messages,
    }


def report(stdout: TextIO, conf: settings.Settings, root: str, as_json: bool) -> int:
    """`--suggest`。候補を YAML の下書きか JSON で出す。終了コードは常に 0（候補が無くても）。"""
    body = collect(conf, root)
    if as_json:
        stdout.write(json.dumps(body, ensure_ascii=True, indent=1))
        stdout.write("\n")
        return 0
    logs = body["logs"]
    if not logs:
        stdout.write(f"ccnavi: 記録が無い（{conf.log or '記録先が空'}）。候補は起こせない\n")
        return 0
    stdout.write(
        f"# 記録 {len(logs)} 本・{body['records']} 行から起こした候補 {len(body['candidates'])} 件"
        f"（検証を通らず落としたもの {body['dropped']} 件）。\n"
        "# どれも下書き。置くのはユーザで、置く前に /ccnavi-config の手順で確かめる。\n"
    )
    for c in body["candidates"]:
        stdout.write("---\n")
        stdout.write(f"# 置き場: {c['rules_path']}（rules）と rule-samples.yml（samples）\n")
        stdout.write(c["yaml"])
    return 0
