"""`ccnavi stop-review material|record`。Stop の agent hook（判定役）が Bash から打つ入口。

- `material <セッション> <transcript>`: 共有キーで判定計画を取り、材料を返す。候補でなければ、
  その旨とそのまま返す返り値を返す。対象のファイルは変えないが、試行の状態は更新する。
- `record <セッション> <transcript>`: 標準入力の判定結果（JSON）を検証し、agent hook がそのまま返す
  返り値（JSON 1 行）だけを標準出力に書く。拒否・打てない場合も `{"ok": true}`。

どちらも終了コード 0 で終わる。判定役の失敗が作業を止めてはならないため。
LLM は呼ばない。作業者向けの文書と `--docs` の索引には、この入口の打ち方を載せない。
"""

from __future__ import annotations

import json
import time
from typing import TextIO

from ..infra import settings
from ..records import stopreview

EXIT_OK = 0
# 計画が届くのを待つ時間と間隔（秒）。値は成立性の検証の後に決める（暫定）。
PLAN_WAIT_SECONDS = 5.0
PLAN_POLL_SECONDS = 0.25

NOT_CANDIDATE = "STOP_REVIEW_NOT_CANDIDATE"


def _not_candidate(stdout: TextIO, why: str) -> int:
    stdout.write(f"{NOT_CANDIDATE}\nreason: {why}\n")
    stdout.write(f"return: {json.dumps(stopreview.OK_RETURN)}\n")
    return EXIT_OK


def _ok(stdout: TextIO) -> int:
    stdout.write(json.dumps(stopreview.OK_RETURN) + "\n")
    return EXIT_OK


def run(stdin: TextIO, stdout: TextIO, stderr: TextIO, conf: settings.Settings, words: list[str]):
    if len(words) != 4 or words[1] not in ("material", "record"):
        stderr.write(
            "ccnavi: stop-review は material か record に <セッション> <transcript> を渡す\n"
        )
        return EXIT_OK if words[1:2] != ["record"] else _ok(stdout)
    _, verb, session, transcript = words
    if not conf.state:
        return _not_candidate(stdout, "no_state") if verb == "material" else _ok(stdout)
    stopreview.configure_log(conf.state, conf.log)
    key = stopreview.shared_key(session, transcript)
    if not key:
        return _not_candidate(stdout, "no_key") if verb == "material" else _ok(stdout)
    stopreview.expire_overdue(conf.state)
    if verb == "material":
        return _material(stdout, conf, key, transcript)
    return _record(stdin, stdout, conf, key)


def _material(stdout: TextIO, conf: settings.Settings, key: str, transcript: str) -> int:
    deadline = time.monotonic() + PLAN_WAIT_SECONDS
    while True:
        state, plan = stopreview.begin(conf.state, key)
        if state != "wait" or time.monotonic() >= deadline:
            break
        time.sleep(PLAN_POLL_SECONDS)
    if state == "wait":
        return _not_candidate(stdout, "plan_timeout")
    if state != "candidate" or plan is None:
        return _not_candidate(stdout, state)
    material = stopreview.turn_material(transcript)
    stopreview.note_insufficient(conf.state, key, bool(material["insufficient"]))
    stdout.write(stopreview.render_material(plan, material, stopreview.CRITERIA_MD) + "\n")
    return EXIT_OK


def _record(stdin: TextIO, stdout: TextIO, conf: settings.Settings, key: str) -> int:
    try:
        result = json.loads(stdin.read())
    except ValueError:
        result = None
    out = stopreview.accept(conf.state, key, result, material_version=stopreview.material_version())
    stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return EXIT_OK
