"""承認台帳。人がチケットに合意したことの記録で、判定はここだけを読む。

## なぜ台帳が権威なのか

チケットのファイルはエージェントが書ける。判定がファイルを直接読むと、
範囲の外で止められたエージェントが範囲を書き足して通れる。台帳を挟むと、
書き足したチケットは指紋が変わり、台帳のどの行とも一致しなくなる。
一致しなければ未承認で、未承認のチケットは判定に効かない。

効かないということは、範囲の制限が外れて元の状態に戻るということ。ここを
「全部拒否」に倒さないのは、倒した先でチケットを直す手も止まるから。
ルールファイルが読めないときに組み込みの既定へ落ちる（REQ-PRE-06）のと
同じ形で、倒れた先に回復の道を残す。そのかわり、未承認のあいだは呼び出しの
たびに言う。黙って外れた制限は、掛かっているつもりで居られるぶん、
最初から無いより悪い。

## 追記しかしない

1 行 1 承認で追記する。上書きすると、いつ何を承認したかの履歴が消え、
前回からの差分（REQ-APV-02）を出す元も無くなる。現在有効なのは
最後の行 1 つで、それより前の行は差分と監査のために残る。

## 台帳を守るのはルールの側

台帳は `.claude/ccnavi/` に置く。そこはこのリポジトリのルールが
Write / Edit を止め、組み込みの既定がシェル経由の書き込みを止めている場所で、
チケットのために別の保護を足す必要がない。設計 §17.5 は
`.claude/approved-tickets.jsonl` に置いているが、置き場を分けると
守り方も 2 通りになり、片方が緩んでも気づかれない。

設計 §17.6 が書いているとおり、これは任意のシェル実行ができる相手には
効かない。台帳は hook と同じ権限で書かれるので、そこまで届いた相手は
台帳も書ける。防げるのは、エージェントがツールを使って範囲を広げることと、
承認前のチケットで作業が始まる事故と、チケットの切り替え忘れ。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import TextIO

from . import rules
from . import ticket as ticket_mod

# リスクの段階。設計 §17.3 の表。
LEVELS = ((70, "CRITICAL"), (40, "HIGH"), (20, "MEDIUM"), (0, "LOW"))

# 二段階承認に入る境目。設計 §17.4 の high_risk_threshold。
#
# 40 ちょうどの扱いは設計の中で揺れている（§17.3 の転記注記）。段階の表は
# 40 を HIGH に含め、HIGH の挙動は二段階承認と書く。§17.4 の本文は「超える場合」。
# 2 つが一致する側、つまり 40 を含める側を採った。境目で緩いほうを選ぶ理由が
# 無いのと、40 で HIGH と表示しながらワンキーで通せるほうが読み手を裏切る。
HIGH_RISK = 40

# 高影響領域。ここへの書き込みは、実行環境・CI・エージェント定義に波及する。
# 設計 §17.3 の +35 の行。
HIGH_IMPACT = (".claude", ".github", ".git", "ci", "Dockerfile", ".gitlab-ci.yml")

# 加点。設計 §17.3 のうち、このビルドに対応する概念があるものだけを写した。
# 写していない行（expected_roots 外、Layer 0-B 不変 deny、ask の allow 化、
# tools と approval_cache の緩和、sandbox 外）は、その概念自体がまだ無い。
# 概念の無い加点を 0 点として黙って落とすと、点が低いことが安全の証拠に見える。
# 承認画面はスコアと一緒に「何を見ていないか」を出す。
POINTS_NEW_SCOPE = 5
POINTS_HIGH_IMPACT = 35
POINTS_PROJECT_ROOT = 30
POINTS_GUARDED = 25
POINTS_DOUBLED = 15

# スコアが見ていないもの。承認画面に出す。
UNSCORED = (
    "expected_roots（想定委譲範囲）との照合",
    "sandbox_root の外かどうか",
    "ツール権限と確認の記憶の緩和",
)


@dataclass
class Approval:
    """台帳の 1 行。承認したときのチケットの姿をそのまま留める。"""

    ticket: str = ""
    title: str = ""
    digest: str = ""
    write: list[str] = field(default_factory=list)
    risk: int = 0
    approved_at: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "approved_at": self.approved_at,
                "ticket": self.ticket,
                "title": self.title,
                "digest": self.digest,
                "write": self.write,
                "risk": self.risk,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


def current(path: str) -> tuple[Approval | None, str]:
    """今効いている承認と、読めなかった理由を返す。

    有効なのは最後の行 1 つ。前の行は差分と監査のために残っているだけで、
    2 本のチケットを同時に承認済みにはしない。同時に効く範囲が 2 つあると、
    片方を承認したつもりの人が、もう片方の範囲まで開けたことになる。
    """
    try:
        with open(path, encoding="utf-8") as f:
            lines = [line for line in f.read().splitlines() if line.strip()]
    except FileNotFoundError:
        return None, ""
    except OSError as exc:
        return None, f"承認台帳を読めない ({exc})"

    if not lines:
        return None, ""
    try:
        raw = json.loads(lines[-1])
    except ValueError as exc:
        return None, f"承認台帳の最後の行を JSON として読めない ({exc})"
    if not isinstance(raw, dict):
        return None, "承認台帳の最後の行がオブジェクトではない"

    write = raw.get("write")
    return (
        Approval(
            ticket=str(raw.get("ticket") or ""),
            title=str(raw.get("title") or ""),
            digest=str(raw.get("digest") or ""),
            write=[str(p) for p in write] if isinstance(write, list) else [],
            risk=raw.get("risk") if isinstance(raw.get("risk"), int) else 0,
            approved_at=str(raw.get("approved_at") or ""),
        ),
        "",
    )


def append(path: str, approval: Approval) -> str:
    """台帳に 1 行足す。書けなかった理由を返す。書けたら空文字。"""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(approval.to_json() + "\n")
    except OSError as exc:
        return f"承認台帳に書けない ({exc})"
    return ""


def score(
    proposed: ticket_mod.Ticket, previous: Approval | None, rule_set: rules.RuleSet, root: str
) -> tuple[int, list[str]]:
    """リスクを数え、加点した理由を人の読める形で返す。

    数だけを出しても承認の判断には使えない。40 という数字は「何が 40 なのか」が
    分からなければ押す指を止められないので、点と一緒にその内訳を返す。
    """
    known = set(previous.write) if previous else set()
    points = 0
    why: list[str] = []

    for path in sorted(proposed.write):
        if path not in known:
            points += POINTS_NEW_SCOPE
            why.append(f"+{POINTS_NEW_SCOPE} 新しい書き込み範囲 `{path}`")
        if not path or path == ".":
            points += POINTS_PROJECT_ROOT
            why.append(f"+{POINTS_PROJECT_ROOT} `{path or '(空)'}` はプロジェクト全体を開ける")
            continue
        head = path.split("/")[0]
        if head in HIGH_IMPACT or path.endswith(".tf"):
            points += POINTS_HIGH_IMPACT
            why.append(f"+{POINTS_HIGH_IMPACT} `{path}` は実行環境・CI・エージェント定義に波及する")
        guarded = _guarded_by(path, rule_set, root)
        if guarded:
            points += POINTS_GUARDED
            why.append(f"+{POINTS_GUARDED} `{path}` はルール {guarded} が守っている場所を含む")

    if previous and known and len(proposed.write) >= len(known) * 2:
        points += POINTS_DOUBLED
        why.append(
            f"+{POINTS_DOUBLED} 前回承認（{previous.ticket}）から範囲が "
            f"{len(known)} 件 → {len(proposed.write)} 件に広がっている"
        )

    return points, why


def level(points: int) -> str:
    """スコアの段階名。"""
    for threshold, name in LEVELS:
        if points >= threshold:
            return name
    return LEVELS[-1][1]


def screen(
    proposed: ticket_mod.Ticket,
    previous: Approval | None,
    points: int,
    why: list[str],
    problems: list[rules.Problem],
) -> str:
    """承認を求める画面を組む。

    設計 §17.1 のとおり、frontmatter の全文は見せない。人に見せるのは
    「何が新たに書けるようになるか」「前回との差分」「リスク」の 3 つ。
    全文を目視させる形にすると、長いものほど読まれなくなり、長く書けば
    通せるという逆の誘因ができる。

    新たに書けるようになる領域を最初に置くのは設計 §17.2 の指定。これが
    被害の上限を決める情報なので、読み手が最初に当たる位置に置く。
    """
    known = set(previous.write) if previous else set()
    now = set(proposed.write)

    lines = [
        f"Ticket 承認リクエスト: {proposed.ticket}: {proposed.title}",
        "",
        "■ このチケットで書き込みが許される領域（これ以外はすべて止まる）",
    ]
    lines += [f"    {'+ ' if p not in known else '  '}{p}/" for p in sorted(now)] or ["    (無し)"]

    if proposed.rationale.strip():
        lines += ["", "■ 理由（エージェントの記述）"]
        lines += [f"    {line}" for line in proposed.rationale.strip().splitlines()]

    lines += ["", "■ 前回承認からの差分"]
    if previous is None:
        lines.append("    前回の承認が無い。これが最初の承認になる")
    else:
        added = sorted(now - known)
        removed = sorted(known - now)
        lines.append(f"    前回: {previous.ticket} ({previous.approved_at})")
        lines += [f"    + {p}/  新規" for p in added]
        lines += [f"    - {p}/  削除" for p in removed]
        if not added and not removed:
            lines.append("    範囲の変化なし")

    lines += ["", f"■ リスクスコア: {points} ({level(points)})"]
    lines += [f"    {line}" for line in why] or ["    加点なし"]

    lines += ["", "■ このスコアが見ていないもの"]
    lines += [f"    {item}" for item in UNSCORED]

    complaints = [p for p in problems if p.severity == rules.SEVERITY_WARN]
    if complaints:
        lines += ["", "■ チケットの記述のうち、判定に効かないもの"]
        lines += [f"    {p.detail}" for p in complaints]

    return "\n".join(lines)


def approve(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    ticket_path: str,
    ledger_path: str,
    rule_set: rules.RuleSet,
    root: str,
) -> int:
    """チケットを人に見せ、承認されたら台帳に追記する。

    エージェントではなく人が端末から叩く経路。チケットを書き直す道
    （設計 §17.2 の「編集して承認」「再生成」）は用意しない。チケットを
    書くのはエージェントの仕事で、承認する場所で書き替えられると、
    承認した人が承認したものの作者になる。承認と作成は分けたままにする。
    """
    proposed, problems = ticket_mod.load(ticket_path)
    errors = [p for p in problems if p.severity == rules.SEVERITY_ERROR]
    if proposed is None:
        stderr.write(f"ccnavi: {ticket_path} からチケットを読めない\n")
        for problem in errors or problems:
            stderr.write(f"  {problem}\n")
        if not problems:
            stderr.write("  ファイルが無い\n")
        return 1

    previous, unreadable = current(ledger_path)
    if unreadable:
        # 台帳が読めないまま追記すると、前回からの差分が「前回が無い」に化ける。
        # 差分が出ない承認は、段階的に範囲を広げる手口をそのまま通す。
        stderr.write(f"ccnavi: {unreadable}\n")
        return 1

    points, why = score(proposed, previous, rule_set, root)
    stdout.write(screen(proposed, previous, points, why, problems) + "\n\n")

    if points >= HIGH_RISK:
        stdout.write(
            f"⚠ リスクスコア {points} ({level(points)})。ワンキーでは承認できない。\n"
            "  上の「書き込みが許される領域」は、却下される要求ではなく実際に許可される範囲。\n"
            f"  承認するならチケット識別子を入力: [{proposed.ticket}] "
        )
        stdout.flush()
        answer = _read(stdin).strip()
        if answer != proposed.ticket:
            stderr.write("ccnavi: 識別子が一致しないので承認しなかった\n")
            return 1
    else:
        stdout.write("承認する場合は y、やめる場合はそれ以外: ")
        stdout.flush()
        if _read(stdin).strip().lower() not in ("y", "yes"):
            stderr.write("ccnavi: 承認しなかった\n")
            return 1

    approval = Approval(
        ticket=proposed.ticket,
        title=proposed.title,
        digest=proposed.digest(),
        write=sorted(proposed.write),
        risk=points,
        approved_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )
    failed = append(ledger_path, approval)
    if failed:
        stderr.write(f"ccnavi: {failed}\n")
        return 1

    stdout.write(f"\n承認した。{ledger_path} に記録した。\n")
    stdout.write("この範囲は次のツール呼び出しから効く。\n")
    return 0


def _read(stdin: TextIO) -> str:
    """1 行読む。端末が繋がっていなければ空文字。

    非対話で叩かれたときに、読めない入力を承認として扱わない。承認は
    人が居ることが前提の操作で、居ないなら承認しないほうへ倒す。
    """
    try:
        return stdin.readline()
    except (OSError, ValueError):
        return ""


def _guarded_by(path: str, rule_set: rules.RuleSet, root: str) -> str:
    """この範囲が、既存のルールの守る場所を含んでいたら、そのルール名を返す。

    範囲が守られた場所に掛かっていること自体は禁止ではない。ルールのほうが
    強いので、範囲に入れても個々の書き込みはルールが止める。ただし
    「守られた場所を作業範囲だと言っているチケット」は、通常の作業では
    出てこないので、承認する人の目に留める。
    """
    full = os.path.join(root, path.replace("/", os.sep))
    probe = os.path.join(full, "probe")
    # 見るのは deny と ask だけ。allow が守っている場所というものは無い。
    hits = [
        rule.id or "(id 無し)"
        for rule in rule_set.deny + rule_set.ask
        if any(rule.matches(tool, probe) for tool in ("Write", "Edit", "MultiEdit"))
    ]
    return ", ".join(sorted(set(hits)))
