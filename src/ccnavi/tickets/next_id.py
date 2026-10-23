"""`ccnavi ticket next-id [<先頭の語> <slug>]`。直接作業のワークツリー名に付ける識別子を答える。

親チケットには使わない。チケット作業は issue と MR のセットで、親の識別子は issue の番号から決まる
（`ccnavi-start.sh --issue`）。通し番号は、直接作業のワークツリーとブランチの名前にだけ残る。

次の通し番号を知るために、`--lint` の warn を出させる回り道をなくす。番号は、呼んだ時点で今ある
名前の番号の最大 + 1（`ticket_ids.next_serial`）。数える名前は `serial_names` が集める。予約は
しないので、同時に呼べば同じ番号が出ることがあり、消えた作業と同じ番号が再び出ることもある。
チケット制御が disable でも使える。

終了コードは、0 が成功（標準出力に番号か識別子だけを 1 行で出し、標準エラーは空）、
1 が識別子にできない形（標準出力は空。理由は標準エラーに出す）、
2 が番号を決められなかった場合（標準出力は空。数えきれなかった理由と
「ユーザに確かめてください」を標準エラーへ）。

- 引数なし: 次の通し番号だけを 1 行で出す
- `<先頭の語> <slug>`: `<先頭の語>-<番号>-<slug>` を 1 行で出す。形（先頭の語・slug の字・
  末尾が `-<2 桁>` でないこと・長さ）を満たさなければ、理由を言って何も出さない

読むだけで、何も書かない。ネットワークにも出ない。
"""

from __future__ import annotations

import os
from typing import TextIO

from ..infra import gitcmd, settings, tree
from . import approval, ticket_ids
from . import ticket as ticket_mod

# 数えきれなかったときに添える一言。
ASK_USER = "番号を決められないので、ユーザに確かめてください"


def serial_names(
    conf: settings.Settings, root: str, scanned: tuple | None = None
) -> tuple[list[str], list[str]]:
    """通し番号に数える名前と、数えきれなかった理由。

    集めるものは次のとおり。今の親チケット・提案（承認待ち・作業中・レビュー待ち・閉じたもの）、ワークスペースと
    各プロジェクトの手元のブランチ名（`ticket_ids.branch_to_id` で識別子の形に直す）、
    `.claude/worktrees/` の直下のディレクトリ名。origin の追跡ブランチは数えない（直接作業の
    ブランチは push しない決まり）。

    `scanned` は読み込み済みの `(承認済み, 閉じた, レビュー待ち, 提案)`。
    渡せば置き場を読み直さない。
    どこかのリポジトリのブランチを数えられなかったときは、理由を問題に入れる（番号を決めてはいけない）。
    """
    problems: list[str] = []
    if scanned is None:
        try:
            raw = approval.read_raw(conf, root)
            copies, _ = approval.scan(conf, root, raw=raw)
            closed, _ = approval.scan(conf, root, closed=True, raw=raw)
            review, _ = approval.scan_review(conf, root, raw=raw)
            proposals, _ = ticket_mod.scan(root, conf.tickets, conf.projects)
        except Exception as exc:  # noqa: BLE001  置き場を読めなければ、番号を決めない
            copies = closed = review = proposals = []
            problems.append(f"チケットと提案の置き場を読めなかった（{exc}）")
    else:
        copies, closed, review, proposals = scanned
    names = [t.ticket for t in list(copies) + list(closed) + list(review) + list(proposals)]
    repos = [(root, "ワークスペース")] + [
        (t.root, f"プロジェクト {t.name}") for t in tree.projects(conf.projects)
    ]
    for repo, label in repos:
        if not os.path.exists(os.path.join(repo, ".git")):
            # git のリポジトリでない（モード B のワークスペースなど）。
            # 数えるものが無いだけで、数えきれないのではない。
            continue
        done = gitcmd.run(repo, ["for-each-ref", "--format=%(refname)", "refs/heads/"])
        if not done.ok:
            why = done.failure or done.err.strip() or f"終了コード {done.code}"
            problems.append(f"{label}のブランチを数えられなかった（{why}）")
            continue
        names.extend(
            ticket_ids.branch_to_id(line.strip().removeprefix("refs/heads/"))
            for line in done.out.splitlines()
            if line.strip()
        )
    homes = os.path.join(root, tree.WORKTREES_DIR)
    try:
        entries = sorted(os.listdir(homes))
    except FileNotFoundError:
        entries = []
    except OSError as exc:
        entries = []
        problems.append(f"{tree.WORKTREES_DIR} を読めなかった（{exc}）")
    names.extend(e for e in entries if os.path.isdir(os.path.join(homes, e)))
    return names, problems


def serial_now(conf: settings.Settings, root: str) -> tuple[int, list[str]]:
    """いまの次の通し番号と、数えきれなかった理由（空なら全部数えた）。"""
    names, problems = serial_names(conf, root)
    return ticket_ids.next_serial(names, conf.branch_prefixes, skip_children=False), problems


def identifier_problem(prefix: str, slug: str, serial: int, prefixes) -> str:
    """`<先頭の語>-<番号>-<slug>` を親の識別子にできない理由。できれば空。"""
    if prefix not in prefixes:
        return f"先頭の語 {prefix} は使えない（使えるのは {', '.join(prefixes)}）"
    ident = f"{prefix}-{serial}-{slug}"
    if ticket_ids.form_of(ident, prefixes) is None:
        return (
            f"slug {slug} は使えない（先頭は英数字か日本語の字、続きは識別子に使える字。"
            "空白と `/` は書けない）"
        )
    if ticket_ids.child_tail_pattern().search(ident):
        return (
            f"{ident} は末尾が `-<2 桁>` で、子の識別子の途中（`<親>-<フェーズ番号>`）と紛れる。"
            "slug の末尾を変える"
        )
    problem = ticket_ids.id_problem(ident) or ticket_ids.branch_problem(ident)
    if problem:
        return problem
    if len(ident) > ticket_ids.SUGGESTED_ID_LENGTH:
        return (
            f"{ident} は {len(ident)} 文字で、推奨の長さ（{ticket_ids.SUGGESTED_ID_LENGTH} 文字）を"
            "超えています。slug を短くしてください。"
        )
    return ""


def run(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    args: list[str],
) -> int:
    """`ticket next-id` の本体。終了コードは、0 が成功、1 が識別子にできない形、
    2 が数えきれなかったとき。"""
    serial, problems = serial_now(conf, root)
    if problems:
        stderr.write(f"ccnavi: ticket next-id: {'。'.join(problems)}。{ASK_USER}\n")
        return 2
    if not args:
        stdout.write(f"{serial}\n")
        return 0
    if len(args) != 2:
        stderr.write(
            "ccnavi: ticket next-id は引数なしか <先頭の語> <slug> の 2 つで実行します。\n"
        )
        return 1
    prefix, slug = args
    problem = identifier_problem(prefix, slug, serial, conf.branch_prefixes)
    if problem:
        stderr.write(f"ccnavi: ticket next-id: {problem}\n")
        return 1
    stdout.write(f"{prefix}-{serial}-{slug}\n")
    return 0
