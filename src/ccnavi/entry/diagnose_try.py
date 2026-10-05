"""判定を実行せずに試す。`--test` と `--test-samples`。

判定と同じ経路（`judge.decide_before`）を通して、応答と記録を読んでユーザに見せる。
モードは常に enable で動かす。見本の読み込みと回し方もここにある。
"""

from __future__ import annotations

import dataclasses
import io
import json
import time
from typing import TextIO

from ..hook import judge
from ..infra import hookio, modes, settings, yamlread
from ..policy import ruleload, rules
from ..records import audit
from . import diagnose_shared

# 対象を取り出せるツール。ここに無いツールは判定に届かないまま通るので、
# 試したいユーザには「当たらない」ではなく「そもそも見ていない」と言う。
# 一覧は judge の表そのもの。VS Code 拡張の KNOWN_TOOLS はこれと同じ順序。
KNOWN_TOOLS = (*judge.SUBJECT_FIELDS, rules.STOP_MATCH)


# `--test --json` と `--test-samples --json` の形の版。読み手は VS Code 拡張の
# ルール管理画面。形を変えたら上げる。
TEST_VERSION = 1

# 見本のパスに書く合言葉。走らせた場所に読み替える。見本を絶対パスで
# 書かせておかないと、判定が解いた先が走らせた場所によって変わる。
SAMPLE_PLACEHOLDER = "/repo"


def try_one(stderr: TextIO, conf: settings.Settings, root: str, tool: str, subject: str) -> dict:
    """1 件を判定して、結果とすべての理由を 1 つの辞書にする。

    文字で出す `test` と JSON で出す `test_json` の両方がここを読む。読み手ごとに
    判定を呼び直すと、端末で見た答えと画面で見た答えが違うものになりうる。

    鍵は README「試験の JSON」に書いてある。`known` が偽なら、そのツールは
    判定が対象を取り出せないもので、他の鍵は空のまま。
    """
    # 試験は記録を持たない。「1 度だけ渡す文」を試しで消費すると、本番の最初の
    # 1 回で届かなくなる。置き場を外すと selfguard もバックアップを取らないが、試験は
    # 実行しないのでそもそも戻すものが無い。
    conf = dataclasses.replace(conf, state="")

    out: dict = {
        "known": tool in KNOWN_TOOLS,
        "tool": tool,
        "subject": subject,
        "resolved": "",
        "verdict": "",
        "code": "",
        "reason": "",
        "degraded": "",
        "unwrapped": "",
        "fallback": "",
        "rules": [],
        "response": "",
    }
    if not out["known"]:
        return out
    if tool == rules.STOP_MATCH:
        return _try_stop(conf, root, out)

    field = judge.SUBJECT_FIELDS[tool]
    payload = hookio.Input(
        event=hookio.PRE_TOOL_USE,
        tool_name=tool,
        tool_input={field: subject},
        cwd=root,
    )
    record = audit.Record(
        mode=modes.ENABLE,
        event=payload.event,
        tool=tool,
        subject=judge.subject_of(payload),
    )

    # 応答は捨てずに拾う。判定が返す文面そのものを見せたいので、
    # ここで文を組み直さない。組み直すと、試験で読んだ文と
    # エージェントに届く文が違うものになる。
    captured = io.StringIO()
    judge.decide_before(
        captured,
        stderr,
        modes.ENABLE,
        conf,
        root,
        payload,
        record,
        time.monotonic() + judge.DEADLINE_SECONDS,
    )

    out["verdict"] = record.decision or audit.ALLOW
    out["code"] = record.code or ""
    # ファイルのパスは行き着く先まで解いてから当てる。解いた先を見せないと、
    # 当たらなかった理由が「パスの表記が違う」なのかどうかをユーザが言えない。
    out["resolved"] = record.subject if record.subject and record.subject != subject else ""
    out["reason"] = record.reason or ""
    out["degraded"] = record.degraded or ""
    # 実行役のコマンドが中で実行するコマンドで当たったときの、そのコマンド。
    out["unwrapped"] = record.unwrapped or ""
    out["fallback"] = record.fallback or ""
    out["rules"] = _rules_hit(stderr, conf, root, record)
    out["response"] = _response_text(captured.getvalue())
    # 引用の中から切り出したコマンドにだけヒットしたルールの id。記録と同じく、
    # 空なら鍵ごと出さない。読み手（VS Code 拡張）の知っている鍵の一覧を、
    # この場合が無い呼び出しで変えないため。
    if record.quoted:
        out["quoted"] = list(record.quoted)
    return out


def _try_stop(conf: settings.Settings, root: str, out: dict) -> dict:
    """`Stop`（ターンの終わり）の試し。判定ではなく、使われるルールを並べる。

    ターンの終わりに当てるのは、共通レイヤーと自身のレイヤーの `allow` で、
    `every` が 2 以上のものだけ
    （`ruleload.stop_rules`）。使われるものがあれば `allow`、無ければ判定に入らない（`skip`）。
    `deny` / `ask` にはならない。数えは見ない（試しで記録を進めない）。
    """
    picked = ruleload.stop_rules(conf, root)
    out["verdict"] = audit.ALLOW if picked else audit.SKIP
    out["rules"] = [
        {
            "id": rule.id,
            "source": "file",
            "section": rule.decision,
            **diagnose_shared._rule_form(rule),
        }
        for rule in picked
    ]
    return out


def test(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    tool: str,
    subject: str,
) -> int:
    """1 件を判定して、結果とすべての理由を書く。終了コードは常に 0。

    判定の内容を終了コードで表さない。deny を非ゼロにすると、試験を回す側が
    「拒否された」と「試験そのものが失敗した」を見分けられなくなる。
    結果は 1 行目の `verdict:` で読む。
    """
    out = try_one(stderr, conf, root, tool, subject)
    if tool == rules.STOP_MATCH:
        stdout.write(f"verdict: {out['verdict']}\ntool: {tool}\n")
        stdout.write(
            "note: ターンの終わり。使われるのは共通レイヤーと自身のレイヤーの allow で、"
            "every が 2 以上のものだけ。渡す回にだけ止める\n"
        )
        if not out["rules"]:
            stdout.write("rules: (使われるルールが無い。ターンの終わりには止めない)\n")
            return 0
        stdout.write("rules:\n")
        for hit in out["rules"]:
            stdout.write(f"  {hit['id']}\n")
        return 0
    if not out["known"]:
        stdout.write(f"verdict: (判定に入らない)\ntool: {tool}\n")
        stdout.write(
            f"note: {tool} は判定が対象を取り出せないツール。ルールを書いてもヒットせず、"
            "呼び出しはそのまま通る\n"
        )
        return 0

    code = out["code"]
    stdout.write(f"verdict: {out['verdict']}{f' ({code})' if code else ''}\n")
    stdout.write(f"tool: {tool}\n")
    stdout.write(f"subject: {subject}\n")
    if out["resolved"]:
        stdout.write(f"resolved: {out['resolved']}\n")
    if out["reason"]:
        stdout.write(f"reason: {out['reason']}\n")
    if out["degraded"]:
        stdout.write(f"degraded: {out['degraded']}（生の文字列に当てた）\n")
    if out["unwrapped"]:
        for layer in out["unwrapped"].split("\x00"):
            shown = " ".join(layer.replace("\x01", " ").split())
            stdout.write(f"unwrapped: {shown}（中で実行されるコマンドに当てた）\n")
    if out["fallback"]:
        stdout.write(f"fallback: {out['fallback']}（組み込みの既定で判定した）\n")

    if not out["rules"]:
        stdout.write("rules: (どのルールにもヒットしなかった)\n")
    else:
        stdout.write("rules:\n")
        for hit in out["rules"]:
            if hit["source"] == "outside":
                # チケットの範囲のように、ルールファイルの中に無い根拠。
                stdout.write(f"  {hit['id']}（ルールファイルの外にある根拠）\n")
                continue
            stdout.write(f"  {hit['section']}:{hit['id']}  {hit['kind']} {hit['written']!r}\n")
            stdout.write(f"    -> {hit['pattern'] or '(組み立て失敗)'}\n")
    if out.get("quoted"):
        stdout.write(
            f"quoted: {', '.join(out['quoted'])}"
            "（引用の中から切り出したコマンドにだけヒットした）\n"
        )

    if not out["response"]:
        stdout.write("response: (何も返さない)\n")
    else:
        stdout.write("response:\n")
        for line in out["response"].splitlines():
            stdout.write(f"  {line}\n")
    return 0


def test_json(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    tool: str,
    subject: str,
) -> int:
    """`--test` と同じ判定を JSON で出す。読み手は VS Code 拡張のルール管理画面。"""
    body = {"version": TEST_VERSION, "root": root, "rules_path": conf.rules}
    body.update(try_one(stderr, conf, root, tool, subject))
    stdout.write(json.dumps(body, ensure_ascii=True, indent=1))
    stdout.write("\n")
    return 0


def _rules_hit(
    stderr: TextIO, conf: settings.Settings, root: str, record: audit.Record
) -> list[dict]:
    """当たったルールを、タイプと翻訳後の式まで返す。

    翻訳後の式を出すのがこの試験の要。`glob` は正規表現に変わるので、
    書いたものと当たるものの間に見えない変換が 1 つ挟まる。その変換を見せないと、
    当たらなかった理由をユーザが自分で辿れない。

    `source` は `file`（ルールファイルの中）か `outside`（チケットの範囲のように、
    ルールファイルの外から来た根拠）。レイヤーのルールは `self:docs` / `lib:source` の形の
    id で当たるので（REQ-MLT-07）、同じ表記で引けるようにレイヤーごと並べる。
    """
    if not record.rules:
        return []

    by_id = {}
    # 判定（`judge.decide_before`）が同じ共通レイヤーを読み、苦情を先に書いている。ここでも書くと
    # 同じ行が 2 度出るので、読み直しの苦情は捨てる。レイヤーの苦情は `survey` が書かずに持つ。
    for view in ruleload.survey(io.StringIO(), conf, root):
        by_id.update({rule.id: rule for rule in view.rule_set.all() if rule.id})

    hits = []
    for name in record.rules:
        rule = by_id.get(name)
        if rule is None:
            hits.append(
                {
                    "id": name,
                    "source": "outside",
                    "section": "",
                    "kind": "",
                    "written": "",
                    "pattern": "",
                }
            )
            continue
        hits.append(
            {
                "id": name,
                "source": "file",
                "section": rule.decision,
                **diagnose_shared._rule_form(rule),
            }
        )
    return hits


def _response_text(written: str) -> str:
    """判定が返した文面をそのまま取り出す。

    止めるだけでは足りない、というのがこの道具の目的なので、試験でも
    「何が返るか」まで見せる。文面の無い拒否は、受け取った側に
    次にすることが分からない。
    """
    if not written.strip():
        return ""
    try:
        out = json.loads(written)["hookSpecificOutput"]
    except (ValueError, KeyError):
        return written.strip()
    # 理由と additionalContext は別の鍵で、deny と ask では両方が届く。
    # 両方あるときは届く順に並べる。
    parts = [out.get("permissionDecisionReason") or "", out.get("additionalContext") or ""]
    return "\n\n".join(p for p in parts if p)


def load_samples(path: str, root: str) -> list[dict]:
    """見本を読んで、タイプの順に平らなリストにする。

    タイプの名前が期待する判定になる。`deny` なら止まるはず、`allow` なら通るはず。
    `subject` の合言葉 `/repo` は走らせた場所に読み替える。
    """
    with open(path, encoding="utf-8") as f:
        raw = yamlread.safe_load(f.read())
    if not isinstance(raw, dict):
        raise ValueError(f"見本の形が違う。最上位はタイプの対応表のはず: {path}")
    samples = []
    for want in rules.SECTIONS:
        for case in raw.get(want) or []:
            if not isinstance(case, dict):
                raise ValueError(f"見本の形が違う。タイプ {want} の 1 件が対応表ではない: {path}")
            written = str(case.get("subject") or "")
            samples.append(
                {
                    "expected": want,
                    "tool": str(case.get("tool") or ""),
                    "subject": written,
                    "resolved_subject": written.replace(SAMPLE_PLACEHOLDER, root),
                    "why": str(case.get("why") or ""),
                }
            )
    return samples


def run_samples(stderr: TextIO, conf: settings.Settings, root: str, path: str) -> dict:
    """見本をぜんぶ判定に掛けて、期待と突き合わせた結果を 1 つの辞書にする。

    判定は `judge` を通す。ここで判定を作り直さないのが肝で、別の経路で確かめると、
    見本が通ったのに実運用で落ちる、という一番まずい形になる（REQ-DIA-03）。

    `ok` は期待どおりか。`skipped` は allow の見本が判定に入らずに通ったもので、
    呼び出しは通るので期待は満たしているが、通した理由が「allow に当たった」では
    ない。ルールを書いても当たらない場所なので、食い違いとは別に数えて必ず見せる。
    """
    # 見本ごとに判定するので、共通レイヤーの苦情は見本の数だけ書かれる。
    # 行き先で読むレイヤーは見本ごとに
    # 違うので、まとめて捨てずに、まだ書いていない行だけを書く。
    said: set[str] = set()
    results = []
    for sample in load_samples(path, root):
        heard = io.StringIO()
        out = try_one(heard, conf, root, sample["tool"], sample["resolved_subject"])
        for line in heard.getvalue().splitlines(keepends=True):
            if line not in said:
                said.add(line)
                stderr.write(line)
        want = sample["expected"]
        got = out["verdict"] if out["known"] else audit.SKIP
        skipped = want == rules.ALLOW and got == audit.SKIP
        results.append(
            {
                **sample,
                "known": out["known"],
                "verdict": got,
                "code": out["code"],
                "reason": out["reason"],
                "rules": out["rules"],
                "ok": got == want or skipped,
                "skipped": skipped,
            }
        )
    counts = {
        want: {
            "ok": sum(1 for r in results if r["expected"] == want and r["ok"]),
            "total": sum(1 for r in results if r["expected"] == want),
        }
        for want in rules.SECTIONS
    }
    return {
        "version": TEST_VERSION,
        "root": root,
        "rules_path": conf.rules,
        "samples_path": path,
        "counts": counts,
        "mismatches": sum(1 for r in results if not r["ok"]),
        "skipped": sum(1 for r in results if r["skipped"]),
        "samples": results,
    }


def test_samples(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    path: str,
    as_json: bool,
) -> int:
    """`--test-samples`。見本をぜんぶ回して、食い違いを並べる。

    文字で出すときの終了コードは、食い違いが 1 件でもあれば 1。`/ccnavi-config`
    スキルと `tools/check_rules.py` がそれを見る。JSON で出すときは常に 0 で、
    食い違いの数は本文の `mismatches` にある。読み手が「食い違った」と
    「試験そのものが失敗した」を終了コードで見分けられるように。
    """
    try:
        body = run_samples(stderr, conf, root, path)
    except (OSError, ValueError) as exc:
        stderr.write(f"ccnavi: 見本を読めない: {exc}\n")
        return 1

    if as_json:
        stdout.write(json.dumps(body, ensure_ascii=True, indent=1))
        stdout.write("\n")
        return 0

    for r in body["samples"]:
        if r["ok"]:
            continue
        stdout.write(f"食い違い: {r['expected']} のはずが {r['verdict']}\n")
        stdout.write(f"  {r['tool']}  {r['subject']}\n")
        stdout.write(f"  なぜ deny/ask/allow に置いたか: {r['why']}\n")
        hit = ", ".join(f"{h['section']}:{h['id']}" for h in r["rules"] if h["source"] == "file")
        if hit:
            stdout.write(f"  ヒットしたルール: {hit}\n")
        stdout.write("\n")
    for r in body["samples"]:
        if not r["skipped"]:
            continue
        stdout.write(f"判定に入らずに通った（allow ではない）: {r['tool']}  {r['subject']}\n")
        stdout.write(f"  {r['why']}\n\n")
    for want, c in body["counts"].items():
        stdout.write(f"{want}: {c['ok']}/{c['total']}\n")
    stdout.write(f"食い違い {body['mismatches']} 件、判定に入らなかったもの {body['skipped']} 件\n")
    return 1 if body["mismatches"] else 0
