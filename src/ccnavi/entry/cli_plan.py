"""親の提案の計画を書き換える補助のフラグ `ccnavi --plan-order <親> ...` の入口。

形は 2 つ。ここは引数を受けて答えを出すだけで、振り直しと書き戻しは `tickets/plan_order.py`、
定義の写しは `tickets/plan_phases.py` が持つ。

- `--json [--order <JSON>]` と `--order <JSON> --expect <ハッシュ> --write`: 計画の番号を振り直して
  答え、保存では提案の `plan:` / `feedback:` の値を書き換える。VS Code の計画編集タブが
  打つ
- `--fill-phases`: 計画（`plan:` / `feedback:`）の項が使うフェーズ定義だけを `phases.yml` から
  読み、提案の `phases:` の値をその定義の集まりに差し替える

## エージェントが打ってよい

`--agree` の外の独立したフラグにしてある。書くのは `todo/` の親の提案（エージェントも書ける
置き場）だけで、承認はしない。組み込みの止め（`phase_forms._CLI_FORMS`）は `--agree` を含む
呼び出しを止めるので、`--agree` の副フラグにすると止めに当たる。`entry/cli.py` は `--agree` の
端末の確かめより手前でここへ分ける。書き換えたあとの承認はダイジェストで縛られる。
"""

from __future__ import annotations

import json
from typing import TextIO

from ..infra import settings
from ..infra.modes import EXIT_ERROR, EXIT_OK
from ..tickets import approval, plan_order, plan_phases, ticket_model


def _not_found(stderr: TextIO, conf: settings.Settings, name: str) -> int:
    stderr.write(
        f"ccnavi: {name} の親の提案が承認待ちの置き場（{conf.tickets}/{ticket_model.TODO}/）に"
        "無い。--plan-order には承認待ちの親の識別子を渡す\n"
    )
    return EXIT_ERROR


def fill_phases(
    stdout: TextIO, stderr: TextIO, conf: settings.Settings, root: str, name: str
) -> int:
    """`--plan-order <親> --fill-phases`。書けたか変わらなければ 0、書かなければ 1。"""
    proposals, _ = approval.scan_proposals(conf, root)
    target, ambiguous = plan_order.pick(proposals, name)
    if ambiguous:
        stderr.write(f"ccnavi: {ambiguous}。何も書かない\n")
        return EXIT_ERROR
    if target is None:
        return _not_found(stderr, conf, name)
    current = plan_order.current_parent(conf, root, target.ticket)
    done = plan_phases.fill(conf, root, target, current)
    if done.why:
        stderr.write(f"ccnavi: {done.why}\n")
        return EXIT_ERROR
    if not done.changed:
        stdout.write(f"{name} の phases: は計画が使う定義と同じ。変えない\n")
        return EXIT_OK
    line = f"{name} の phases: に {', '.join(done.copied)} を書いた"
    if done.dropped:
        line += f"（使わない定義を外した: {', '.join(done.dropped)}）"
    if done.held:
        line += f"。子が承認された番号の定義（{', '.join(done.held)}）は承認済みチケットから写した"
    stdout.write(line + "\n")
    return EXIT_OK


def reorder(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    name: str,
    as_json: bool,
    order_text: str | None,
    write: bool,
    expect: str,
) -> int:
    """`--plan-order <親> --json [--order <JSON>]` と、保存の `--order --expect --write`。

    読むだけなら答えを出して 0。書くときは、書けたか変わらなければ 0、書かなければ 1（理由は
    標準エラー。`--json` なら答えの `written` が偽）。`--order` の形の誤りと、親の提案が無い・
    読めないときは 1（使い方の誤り）。
    """
    proposals, _ = approval.scan_proposals(conf, root)
    target, ambiguous = plan_order.pick(proposals, name)
    if ambiguous:
        stderr.write(f"ccnavi: {ambiguous}\n")
        return EXIT_ERROR
    if target is None:
        return _not_found(stderr, conf, name)
    current = plan_order.current_parent(conf, root, target.ticket)
    source, why = plan_order.load(conf, root, target, proposals, current)
    if source is None:
        stderr.write(f"ccnavi: {why}\n")
        return EXIT_ERROR
    order = None
    if order_text is not None:
        order, why = plan_order.parse_order(order_text, source.parent)
        if order is None:
            stderr.write(f"ccnavi: --order: {why}\n")
            return EXIT_ERROR
    ans = plan_order.answer(source, order)
    body = plan_order.body(ans)
    if not write:
        stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
        return EXIT_OK
    saved = plan_order.save(ans, expect)
    for message in saved.messages:
        stderr.write(f"ccnavi: {message}\n")
    for detail in saved.details:
        stderr.write(f"  {detail}\n")
    body["written"] = saved.written
    sha = ""
    if saved.written:
        sha = plan_order.reread_sha(conf, root, name, current)
        body["source_sha"] = sha
        for plan in body["plans"]:
            plan["source_sha"] = sha
    if as_json:
        stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
    elif saved.written:
        stdout.write(f"{name} の計画の順序を書き換えた（source_sha: {sha}）\n")
    elif saved.ok:
        stdout.write(f"{name} の計画は振り直しても変わらない。書かない\n")
    return EXIT_OK if saved.ok else EXIT_ERROR
