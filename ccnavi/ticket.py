"""作業チケットの読み込みと、そこが宣言する作業範囲。

## チケットは提案であって権威ではない

チケットはエージェントが書く。だからチケットの中身をそのまま判定に使うと、
範囲の外で止められたエージェントが、チケットに 1 行足して自分の首輪を伸ばせる。
止められた側が止め方を書き換えられる仕組みは、止めていない。

判定が使うのは承認台帳に載った範囲だけにしてある（approval.py）。作業ツリーの
チケットは、人に見せて承認を求めるための提案で、承認されるまで判定には
1 ミリも効かない。承認されたあとにファイルを書き換えても、効いているのは
台帳の側なので範囲は広がらない。広がらないかわりに食い違いが起きるので、
食い違いは毎回報告する。

## 絞ることしかできない

チケットが宣言できるのは「ここだけ書く」であって「ここも書ける」ではない。
今のビルドの判定は deny の一覧で、チケットが無ければ全部通る。チケットは
そこへ「宣言した範囲の外は止める」を足すだけなので、チケットをどう書いても
チケットが無いときより緩くはならない。この向きが保たれている限り、
エージェントが書いたファイルを判定に使っても危険は増えない。

## 書式

`.current-ticket.md` の先頭の frontmatter。設計 §9.1 のとおり YAML。

    ---
    ticket: PROJ-1234
    title: ユーザー設定画面のリファクタリング
    rationale: |
      設定コンポーネントの分割。
    target_directories:
      write:
        "src/components/Settings": allow
    ---

    ## 作業内容
    ...(本文は判定に影響しない)

読むのは `ticket` `title` `rationale` と `target_directories.write` だけ。
設計にある他の欄（`tools`、`deny_commands`、`target_directories.read`）は
このビルドの判定が使わない。使わない欄を黙って受け取ると、書いた人は
効いていると思い、効いていないことに誰も気づかない。だから読み飛ばさずに
「これは効かない」と名指しで報告する。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

import yaml

from .rules import SEVERITY_ERROR, SEVERITY_WARN, Problem

# frontmatter の囲い。
FENCE = "---"

# 範囲の件数の上限。設計 §9.4 の max_ticket_rules。大量に並べて人のレビューを
# 潰し、その中に広い範囲を紛れ込ませる手口を防ぐためのもの。上限を超えたら
# チケットごと拒否する。1 件ずつ落とすと、落ちた 1 件がどれなのかを
# 承認する人が追えない。
MAX_SCOPE_ENTRIES = 20

# 範囲のパスに書けない綴り。設計 §9.4 の検証 4 番。
# `..` は範囲の外へ出る綴り、絶対パスと `~` はプロジェクトの外を指す綴り、
# `$` は展開されるまで行き先が決まらない綴り。`*` は前置として読めない綴りで、
# 範囲は前置で書くことになっている。
_FORBIDDEN = (("..", "`..`"), ("~", "`~`"), ("$", "`$`"), ("*", "`*`"))

# このビルドの判定が使わない欄。読めても効かないので名指しで報告する。
_UNUSED = (
    ("tools", "ツール権限"),
    ("deny_commands", "追加の禁止コマンド"),
)


@dataclass
class Ticket:
    """チケット 1 本ぶん。人に見せて承認を求めるための姿。"""

    ticket: str = ""
    title: str = ""
    rationale: str = ""
    # write はプロジェクト根からの相対の前置。ここだけが判定に効く。
    write: list[str] = field(default_factory=list)

    def digest(self) -> str:
        """承認の同一性を決める指紋。

        frontmatter の生の文字列ではなく、解釈した中身から取る。人が承認画面で
        見たものと、指紋が覆う範囲を一致させるため。生の文字列から取ると、
        コメントを 1 つ足しただけで再承認を求めることになり、承認が
        「また出た、押しておこう」に化ける。逆に範囲だけから取ると、
        rationale を書き換えて別の作業に見せかけても指紋が変わらない。
        人が読んで判断した欄は、すべて覆う。
        """
        canonical = json.dumps(
            {
                "ticket": self.ticket,
                "title": self.title,
                "rationale": self.rationale,
                "write": sorted(self.write),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load(path: str) -> tuple[Ticket | None, list[Problem]]:
    """チケットを読んで組み立てる。

    ファイルが無いことは不備ではない。チケットによる制御は任意で、
    置かなければ範囲の制限が掛からないだけ。置いていないプロジェクトに
    苦情を返すと、苦情のほうが常態になって読まれなくなる。
    """
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return None, []
    except OSError as exc:
        return None, [Problem(SEVERITY_ERROR, path, f"チケットを読めない ({exc})")]
    return parse(text)


def parse(text: str) -> tuple[Ticket | None, list[Problem]]:
    """読み込み済みの文面からチケットを組み立てる。

    ファイルを開く部分と分けてあるのは、テストと承認の画面が同じ道を通るため。
    別の道で組み立てると、承認したものと判定が使うものが食い違いうる。
    """
    front, problems = _frontmatter(text)
    if front is None:
        return None, problems

    ticket = Ticket(
        ticket=_text(front.get("ticket")),
        title=_text(front.get("title")),
        rationale=_text(front.get("rationale")),
    )
    if not ticket.ticket:
        problems.append(Problem(SEVERITY_ERROR, "ticket", "チケット識別子が無い"))
        return None, problems

    name = ticket.ticket
    for key, label in _UNUSED:
        if key in front:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    name,
                    f"`{key}`（{label}）はこのビルドの判定が使わない。書いても効かない",
                )
            )

    scope, scope_problems = _write_scope(front, name)
    problems.extend(scope_problems)
    ticket.write = scope

    if not ticket.write:
        # 範囲が 1 件も無いチケットは、承認しても何も止めない。止めないことを
        # 止めているつもりで居られるのがいちばん悪いので、error にする。
        problems.append(
            Problem(
                SEVERITY_ERROR,
                name,
                "`target_directories.write` に allow の範囲が 1 件も無い。"
                "このチケットは承認しても何も止めない",
            )
        )
        return None, problems

    if len(ticket.write) > MAX_SCOPE_ENTRIES:
        problems.append(
            Problem(
                SEVERITY_ERROR,
                name,
                f"範囲が {len(ticket.write)} 件ある（上限 {MAX_SCOPE_ENTRIES} 件）。"
                "作業を分けるか、範囲をまとめること",
            )
        )
        return None, problems

    return ticket, problems


def _frontmatter(text: str) -> tuple[dict | None, list[Problem]]:
    """先頭の frontmatter を切り出して読む。

    `---` で始まらないファイルはチケットではない。本文だけの覚え書きを
    チケットとして読み違えるより、読まなかったと言うほうがよい。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != FENCE:
        return None, [Problem(SEVERITY_ERROR, "(file)", f"先頭が `{FENCE}` で始まっていない")]

    for i in range(1, len(lines)):
        if lines[i].strip() == FENCE:
            body = "\n".join(lines[1:i])
            break
    else:
        return None, [Problem(SEVERITY_ERROR, "(file)", f"frontmatter が `{FENCE}` で閉じていない")]

    try:
        # safe_load に限る。任意の Python の型を組み立てる load は、
        # エージェントが書けるファイルに向けては使えない。
        front = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        return None, [
            Problem(SEVERITY_ERROR, "(file)", f"frontmatter を YAML として読めない ({exc})")
        ]

    if front is None:
        return None, [Problem(SEVERITY_ERROR, "(file)", "frontmatter が空")]
    if not isinstance(front, dict):
        return None, [Problem(SEVERITY_ERROR, "(file)", "frontmatter がキーと値の並びではない")]
    return front, []


def _write_scope(front: dict, name: str) -> tuple[list[str], list[Problem]]:
    """`target_directories.write` から、判定に使う前置の一覧を取り出す。

    `allow` だけを範囲に数える。設計は `ask` も書けることになっているが、
    このビルドに ask は無い。無い判定を黙って allow に読み替えると、
    確認を求めたつもりの範囲が無確認で書ける範囲になる。数えないほうへ倒すと
    その範囲は「範囲外」として止まるので、倒れる先が厳しい側になる。
    """
    problems: list[Problem] = []
    directories = front.get("target_directories")
    if directories is None:
        return [], problems
    if not isinstance(directories, dict):
        return [], [Problem(SEVERITY_ERROR, name, "`target_directories` がキーと値の並びではない")]

    if "read" in directories:
        problems.append(
            Problem(
                SEVERITY_WARN,
                name,
                "`target_directories.read` はこのビルドの判定が使わない。読み取りは制限しない",
            )
        )

    raw = directories.get("write")
    if raw is None:
        return [], problems
    if not isinstance(raw, dict):
        return [], [
            *problems,
            Problem(SEVERITY_ERROR, name, "`target_directories.write` がキーと値の並びではない"),
        ]

    scope: list[str] = []
    for key, value in raw.items():
        path = _text(key).strip()
        decision = _text(value).strip().lower()
        if not path:
            problems.append(Problem(SEVERITY_ERROR, name, "範囲のパスが空"))
            continue
        if decision != "allow":
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    name,
                    f"`{path}: {decision or '(空)'}` は allow ではないので範囲に数えない。"
                    "このビルドに ask は無いため、この場所は範囲外として止まる",
                )
            )
            continue
        bad = _forbidden(path)
        if bad:
            problems.append(Problem(SEVERITY_ERROR, name, f"範囲のパス `{path}` に{bad}は書けない"))
            continue
        if os.path.isabs(path) or (len(path) > 1 and path[1] == ":"):
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    name,
                    f"範囲のパス `{path}` が絶対パス。範囲はプロジェクト根からの相対で書く",
                )
            )
            continue
        scope.append(path.replace("\\", "/").strip("/"))

    return scope, problems


def inside(root: str, scope: list[str], full: str) -> bool:
    """行き着く先が範囲の中かどうか。

    当てる先は解いた絶対パス。実行前の判定がファイルのパスを解いてから
    ルールに当てるのと同じ理由で、`src/../etc` のような綴りで範囲の外へ
    出られてはいけない。範囲の側も同じ解き方をしてから比べる。

    範囲そのものを指すパスも中とみなす。`src` が範囲なら `src` という名前の
    ファイルを作ることも作業のうちで、そこだけ外に落ちるのは驚きでしかない。
    """
    if not full:
        return False
    target = os.path.normpath(full)
    for entry in scope:
        base = os.path.realpath(os.path.join(root, entry.replace("/", os.sep)))
        if target == base or target.startswith(base + os.sep):
            return True
    return False


def _forbidden(path: str) -> str:
    for literal, label in _FORBIDDEN:
        if literal in path:
            return label
    return ""


def _text(value: object) -> str:
    """YAML から来た値を文字列にする。

    数字だけのチケット識別子は YAML が int にして返す。str() で受けないと、
    `ticket: 1234` と書いたチケットが「識別子が無い」で落ちる。
    """
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value)
