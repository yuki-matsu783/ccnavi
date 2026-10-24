"""`ccnavi stop-review material|record`。Stop の agent hook（判定役）が Bash から打つ入口。

- `material <セッション> <transcript>`: 共有キーで判定計画を取り、材料を返す。候補でなければ、
  その旨の返り値を、そのまま返す。対象のファイルは変えないが、試行の状態は更新する。
- `record <セッション> <transcript>`: 標準入力の判定結果（JSON）を検証し、agent hook がそのまま返す
  返り値（JSON 1 行）だけを標準出力に書く。拒否・実行できない場合も `{"ok": true}`。

どちらも終了コード 0 で終わる。判定役の失敗が作業を止めてはならないため。
LLM は呼ばない。作業者向けの文書と `--docs` の索引には、この入口の打ち方を載せない。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import TextIO

from ..infra import pathcase, settings, tree
from ..records import stopeval, stopreview
from ..tickets import approval, approval_checks

EXIT_OK = 0
# 計画が届くのを待つ時間と間隔（秒）。値は成立性の検証の後に決める（暫定）。
PLAN_WAIT_SECONDS = 5.0
PLAN_POLL_SECONDS = 0.25

NOT_CANDIDATE = "STOP_REVIEW_NOT_CANDIDATE"
_SHA = re.compile(r"[0-9a-f]{7,64}")


def _not_candidate(stdout: TextIO, why: str) -> int:
    stdout.write(f"{NOT_CANDIDATE}\nreason: {why}\n")
    stdout.write(f"return: {json.dumps(stopreview.OK_RETURN)}\n")
    return EXIT_OK


def _ok(stdout: TextIO) -> int:
    stdout.write(json.dumps(stopreview.OK_RETURN) + "\n")
    return EXIT_OK


def run(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    words: list[str],
    root: str = "",
):
    """`stop-review` の入口。`report`・`material`・`record` のどれかに振り分ける。

    処理フロー:
    1. `report` なら、評価の集計を `_report` で出して終える（例外は 1 で終える）。
    2. `record` の引数が JSON の 1 語で渡されたら、それを `inline` に取り、語を 4 つに揃える。
    3. 語の形が合わなければ、使い方を標準エラーに書く。`record` は `_ok` の出力で、
       それ以外は 0 で終える。
    4. 状態の置き場が無いときは、`material` なら候補なし（`_not_candidate`）、`record` は
       `_ok` で終える。
    5. 共有のキー（`stopreview.shared_key`）を作り、作れなければ記録して、同じように終える。
    6. 期限切れを片付け（`stopreview.expire_overdue`）、`material` は `_material`、`record` は
       `_record` へ渡す。例外は、判定役が作業を止めないように決まった形で終える。
    """
    if words[1:2] == ["report"] and len(words) == 2:
        try:
            return _report(stdout, conf)
        except Exception as exc:  # 集計の失敗で落ちない。壊れた記録を名指しする
            stderr.write(f"ccnavi: 評価の集計に失敗した（{type(exc).__name__}: {exc}）\n")
            return 1
    inline = ""
    if len(words) == 5 and words[1] == "record":
        inline = words[
            4
        ]  # JSON を最後の引数で渡す形（権限の許可に合わせた、パイプ・ヒアドキュメントの無い形）
        words = words[:4]
    if len(words) != 4 or words[1] not in ("material", "record"):
        stderr.write(
            "ccnavi: stop-review は material か record に <セッション> <transcript> を渡す\n"
        )
        return EXIT_OK if words[1:2] != ["record"] else _ok(stdout)
    _, verb, session, transcript = words
    if not conf.state:
        return _not_candidate(stdout, "no_state") if verb == "material" else _ok(stdout)
    stopreview.configure_log(conf.state, stopreview.logs_dir(conf.log))
    key = stopreview.shared_key(session, transcript)
    if not key:
        stopreview.log_event(conf.state, "no_key", {"reason": "judge"})
        return _not_candidate(stdout, "no_key") if verb == "material" else _ok(stdout)
    try:
        stopreview.expire_overdue(conf.state)
        if verb == "material":
            return _material(stdout, conf, key, transcript, root)
        return _record(stdin, stdout, conf, key, root, inline)
    except Exception as exc:  # 判定役の失敗が作業を止めてはならない。出力は必ず決まった形にする
        stderr.write(f"ccnavi: stop-review {verb} に失敗した: {type(exc).__name__}\n")
        return _not_candidate(stdout, "error") if verb == "material" else _ok(stdout)


def _material(stdout: TextIO, conf: settings.Settings, key: str, transcript: str, root: str) -> int:
    deadline = time.monotonic() + PLAN_WAIT_SECONDS
    while True:
        state, plan = stopreview.begin(conf.state, key)
        if state != "wait" or time.monotonic() >= deadline:
            break
        time.sleep(PLAN_POLL_SECONDS)
    if state == "wait":
        stopreview.log_event(conf.state, "plan_timeout", {"attempt_id": stopreview.attempt_id(key)})
        return _not_candidate(stdout, "plan_timeout")
    if state != "candidate" or plan is None:
        return _not_candidate(stdout, state)
    criteria, _source = stopreview.load_criteria(root)
    material = stopreview.turn_material(transcript)
    # 定義が計画を作った時から変わっていたら、材料が変わったとして扱う（record が拒否する）。
    changed = stopreview.material_version(criteria) != plan.get("material_version")
    stopreview.note_insufficient(
        conf.state, key, bool(material["insufficient"]) or changed or not criteria
    )
    bodies = {p["key"]: _body(root, p) for p in plan.get("proposals") or []}
    rendered = stopreview.render_material(plan, material, criteria, bodies)
    stopreview.save_material(
        stopreview.logs_dir(conf.log),
        plan["attempt_id"],
        stopreview.EXTRACTOR_VERSION,
        criteria,
        rendered,
    )
    stdout.write(rendered + "\n")
    return EXIT_OK


def _unsent_body(root: str, proposal: dict) -> str:
    """送っていない提案の本文。書いた md の現在の中身を、上限つきで読む。"""
    parts = []
    for item in proposal.get("files") or []:
        path = str(item.get("path", ""))
        if not path.casefold().endswith(".md") or not _inside(root, path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                parts.append(f"--- {path}（未送信）---\n{handle.read(stopreview.MATERIAL_LIMIT)}")
        except OSError:
            continue
    return "\n".join(parts)


def _body(root: str, proposal: dict) -> str:
    """送った版の提案の本文。送ったコミットから読む（ワークツリーの現在の姿ではなく、送った版）。

    読めないときは空文字（材料に本文が無いと示す）。送った提案のファイルは `todo/` の下の md。
    """
    if proposal.get("route") == "unsent":
        return _unsent_body(root, proposal)
    if not (root and _SHA.fullmatch(str(proposal.get("commit", "")))):
        return ""
    parts = []
    for item in proposal.get("files") or []:
        path = str(item.get("path", ""))
        if not path.endswith(".md") or path.startswith("/") or ".." in path.split("/"):
            continue
        try:
            shown = subprocess.run(
                ["git", "-C", root, "show", f"{proposal['commit']}:{path}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if shown.returncode == 0:
            parts.append(f"--- {path} ---\n{shown.stdout[: stopreview.MATERIAL_LIMIT]}")
    return "\n".join(parts)


def _record(
    stdin: TextIO, stdout: TextIO, conf: settings.Settings, key: str, root: str, inline: str = ""
) -> int:
    try:
        result = json.loads(inline or stdin.read())
    except ValueError:
        result = None
    criteria, _source = stopreview.load_criteria(root)
    plan = stopreview.read_plan(conf.state, key) or {}
    idents = {p["key"]: p["ident"] for p in plan.get("proposals") or []}
    out = stopreview.accept(
        conf.state,
        key,
        result,
        material_version=stopreview.material_version(criteria),
        still_unapproved=lambda target: not _approved(conf, root, idents.get(target, "")),
    )
    stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return EXIT_OK


def _approved(conf: settings.Settings, root: str, ident: str) -> bool:
    """提案が今、承認済みの置き場にあるか。判定の途中で別のPCから承認されたかを確かめる。"""
    if not ident:
        return False
    copies, _ = approval.scan(conf, root)
    return tree.lookup(approval_checks.by_id(copies), ident) is not None


def _report(stdout: TextIO, conf: settings.Settings) -> int:
    """評価の集計を出す（ユーザが採用を判断する材料）。判定役の入口ではない。"""
    log_dir = stopreview.logs_dir(conf.log)
    stdout.write(
        stopeval.render(
            stopeval.summarize(conf.state, log_dir), stopeval.unlinked_total(conf.state)
        )
        + "\n"
    )
    return EXIT_OK


def _inside(root: str, path: str) -> bool:
    """実体がワークスペースの中にあるか（リンクで外のファイルを材料に出さない）。"""
    if not root:
        return False
    real, base = pathcase.fold(os.path.realpath(path)), pathcase.fold(os.path.realpath(root))
    return real == base or real.startswith(base.rstrip("/\\") + os.sep)
