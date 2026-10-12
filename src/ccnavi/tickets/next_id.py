"""`ccnavi ticket next-id [<先頭の語> <slug>]`。issue の無い親の提案に使う識別子を答える。

次の通し番号を知るために `--lint` の warn を出させる回り道を無くす。番号は、承認待ち・
作業中・レビュー待ち・閉じた親の識別子の番号の最大 + 1（`ticket_ids.next_serial`）。

- 引数なし: 次の通し番号だけを 1 行で出す
- `<先頭の語> <slug>`: `<先頭の語>-<番号>-<slug>` を 1 行で出す。形（先頭の語・slug の字・
  末尾が `-<2 桁>` でないこと・長さ）を満たさなければ、理由を言って何も出さない

読むだけで、何も書かない。ネットワークにも出ない。
"""

from __future__ import annotations

from typing import TextIO

from ..infra import settings
from . import approval, ticket_ids
from . import ticket as ticket_mod


def serial_now(conf: settings.Settings, root: str) -> int:
    """いまの次の通し番号。`--lint` が warn に添える番号と同じ集め方。"""
    raw = approval.read_raw(conf, root)
    copies, _ = approval.scan(conf, root, raw=raw)
    closed, _ = approval.scan(conf, root, closed=True, raw=raw)
    review, _ = approval.scan_review(conf, root, raw=raw)
    proposals, _ = ticket_mod.scan(root, conf.tickets, conf.projects)
    names = [t.ticket for t in list(copies) + list(closed) + list(review) + list(proposals)]
    return ticket_ids.next_serial(names, conf.branch_prefixes)


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
            f"{ident} は {len(ident)} 文字で、勧める長さ（{ticket_ids.SUGGESTED_ID_LENGTH} 文字）を"
            "超える。slug を短くする"
        )
    return ""


def run(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    args: list[str],
) -> int:
    """`ticket next-id` の本体。終了コードは 0 が成功、1 が識別子にできない形。"""
    serial = serial_now(conf, root)
    if not args:
        stdout.write(f"{serial}\n")
        return 0
    if len(args) != 2:
        stderr.write("ccnavi: ticket next-id は引数なしか <先頭の語> <slug> の 2 つで打つ\n")
        return 1
    prefix, slug = args
    problem = identifier_problem(prefix, slug, serial, conf.branch_prefixes)
    if problem:
        stderr.write(f"ccnavi: ticket next-id: {problem}\n")
        return 1
    stdout.write(f"{prefix}-{serial}-{slug}\n")
    return 0
