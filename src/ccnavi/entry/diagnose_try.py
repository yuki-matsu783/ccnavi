"""判定を実行せずに試す。`--test` と `--test-expectations`。

判定と同じ経路（`judge.decide_before`）を通して、応答と記録を読んでユーザに見せる。
モードは常に enable で動かす。期待する判定の読み込みと回し方もここにある。
"""

from __future__ import annotations

import dataclasses
import io
import json
import os
import time
from typing import TextIO

from ..hook import judge
from ..infra import hookio, modes, settings, yamlread
from ..policy import ruleload, rules
from ..records import audit
from . import diagnose_shared

# 対象を取り出せるツール。ここに無いツールは判定に届かないまま通るので、
# 試したいユーザには「ヒットしない」ではなく「そもそも見ていない」と言う。
# 一覧は judge の表そのもの。VS Code 拡張の KNOWN_TOOLS はこれと同じ順序。
KNOWN_TOOLS = (*judge.SUBJECT_FIELDS, rules.STOP_MATCH)


# `--test --json` と `--test-expectations --json` の形の版。読み手は VS Code 拡張の
# ルール管理画面。形を変えたら上げる。
TEST_VERSION = 1

# ヒットしたルールの `source`。ルールファイルから読んだものと、その外から来た根拠の 2 つだけ。
# 値を増やすと、2 値で読む拡張（testmodel.ts）が知らない値を読み違えるので、増やすなら
# TEST_VERSION も上げる。
FILE = "file"
OUTSIDE = "outside"
# 外の根拠の式を見せる長さの上限（字）。組み込みの保護の式は 2 万字を超える。
OUTSIDE_FORM_LIMIT = 160

# `--permission-mode` に渡せる名前。hook が受け取りうる Claude Code の権限モードで、判定が
# 名前を知っているものだけ。判定は知らない名前を ask として扱う（`judge.undeclared_verdict`）が、
# 試験で打ち間違えた名前を黙って ask にすると、そのモードの答えとして読まれる。
PERMISSION_MODES = (*judge.PERMISSION_JUDGED, *judge.PERMISSION_NO_JUDGE)


@dataclasses.dataclass(frozen=True)
class Call:
    """試験の payload のうち、打つ側が変えられる欄。どれも空なら hook に無い既定で試す。

    cwd は呼び出しが来たディレクトリ（空ならワークスペースルート）。Bash の行き先のツリー、
    `cd` の読み、レビュー待ちの止め、子からの push の止めがこれで変わる。permission_mode は
    Claude Code の権限モード（空なら payload に無いのと同じ）。agent_id はサブエージェントの
    中で走った呼び出しの印で、サブエージェントに許さない操作の止めがこれで変わる。
    """

    cwd: str = ""
    permission_mode: str = ""
    agent_id: str = ""

    def given(self) -> bool:
        return bool(self.cwd or self.permission_mode or self.agent_id)


def call_problem(call: Call) -> str:
    """試験の payload の欄が使えないなら、その理由。使えるなら空。"""
    if call.permission_mode and call.permission_mode not in PERMISSION_MODES:
        return (
            f"--permission-mode {call.permission_mode!r} は権限モードの名前ではない。"
            f"渡せるのは {', '.join(PERMISSION_MODES)}"
        )
    if call.cwd and not os.path.isdir(call.cwd):
        return f"--cwd {call.cwd!r} はディレクトリではない（無いか、ファイル）"
    return ""


def payload_of(root: str, call: Call) -> dict:
    """試験で判定に渡す payload の欄。`--test` が出す `payload` と同じもの。"""
    return {
        "cwd": os.path.abspath(call.cwd) if call.cwd else root,
        "permission_mode": call.permission_mode,
        "agent_id": call.agent_id,
    }


# 期待する判定のパスに書く合言葉。走らせた場所に読み替える。期待する判定を絶対パスで
# 書かせておかないと、判定が解いた先が走らせた場所によって変わる。
EXPECTATION_PLACEHOLDER = "/repo"


def try_one(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    tool: str,
    subject: str,
    call: Call | None = None,
) -> dict:
    """1 件を判定して、結果とすべての理由を 1 つのマップにする。

    文字で出す `test` と JSON で出す `test_json` の両方がここを読む。読み手ごとに
    判定を呼び直すと、ターミナルで見た答えと画面で見た答えが違うものになりうる。

    キーは docs/requirements/json-shapes.md「試験の JSON」に書いてある。
    `known` が偽なら、そのツールは
    判定が対象を取り出せないもので、他のキーは空のまま。

    `call` は payload の欄（`Call`）。渡さなければ cwd はワークスペースルートで、権限モードと
    サブエージェントの印は無い。欄を確かめる（`call_problem`）のは呼び手。
    """
    call = call or Call()
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
    used = payload_of(root, call)
    payload = hookio.Input(
        event=hookio.PRE_TOOL_USE,
        tool_name=tool,
        tool_input={field: subject},
        cwd=used["cwd"],
        permission_mode=used["permission_mode"],
        agent_id=used["agent_id"],
    )
    record = audit.Record(
        mode=modes.ENABLE,
        permission_mode=payload.permission_mode,
        event=payload.event,
        tool=tool,
        subject=judge.subject_of(payload),
    )

    # 応答は破棄せずに拾う。判定が返す文面そのものを見せたいので、
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
    # ファイルのパスは行き着く先まで解いてから適用する。解いた先を見せないと、
    # ヒットしなかった理由が「パスの表記が違う」なのかどうかをユーザが言えない。
    out["resolved"] = record.subject if record.subject and record.subject != subject else ""
    out["reason"] = record.reason or ""
    out["degraded"] = record.degraded or ""
    # 実行役のコマンドが中で実行するコマンドでヒットしたときの、そのコマンド。
    out["unwrapped"] = record.unwrapped or ""
    out["fallback"] = record.fallback or ""
    out["rules"] = _rules_hit(record)
    out["response"] = _response_text(captured.getvalue())
    # 引用の中から切り出したコマンドにだけヒットしたルールの id。記録と同じく、
    # 空ならキーごと出さない。読み手（VS Code 拡張）の知っているキーの一覧を、
    # この場合が無い呼び出しで変えないため。
    if record.quoted:
        out["quoted"] = list(record.quoted)
    # 判定に渡した payload の欄。`quoted` と同じく、欄を渡したときだけ出す。渡さなければ
    # cwd はルート（`root` のキー）で、ほかは空と決まっている。
    if call.given():
        out["payload"] = used
    return out


def _try_stop(conf: settings.Settings, root: str, out: dict) -> dict:
    """`Stop`（ターンの終わり）の試し。使われるルールを並べる。

    ターンの終わりに適用するのは、共通レイヤーと自身のレイヤーの `allow` で、
    `every` が 2 以上のものだけ
    （`ruleload.stop_rules`）。使われるものがあれば `allow`、無ければ判定に入らない（`skip`）。
    試しで記録を変えない（数えは進めない）。
    """
    picked = ruleload.stop_rules(conf, root)
    out["verdict"] = audit.ALLOW if picked else audit.SKIP
    out["rules"] = [
        {
            "id": rule.id,
            "source": FILE,
            "section": rule.decision,
            **diagnose_shared.rule_form(rule),
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
    call: Call | None = None,
) -> int:
    """1 件を判定して、結果とすべての理由を書く。終了コードは 0（payload の欄の誤りだけ 1）。

    判定の内容を終了コードで表さない。deny を非ゼロにすると、試験を回す側が
    「拒否された」と「試験そのものが失敗した」を見分けられなくなる。
    結果は 1 行目の `verdict:` で読む。
    """
    # 呼び出しの入力を確かめ、判定を 1 回回す。
    call = call or Call()
    problem = call_problem(call)
    if problem:
        stderr.write(f"ccnavi: {problem}\n")
        return 1
    out = try_one(stderr, conf, root, tool, subject, call)
    # ターンの終わり（STOP_MATCH）は、ルールを渡す回とは別の書き方で出す。
    if tool == rules.STOP_MATCH:
        stdout.write(f"verdict: {out['verdict']}\ntool: {tool}\n")
        stdout.write(
            "note: ターンの終わり。使われるのは共通レイヤーと自身のレイヤーの allow で、"
            "every が 2 以上のものだけ。渡す回にだけ止める\n"
        )
        if not out["rules"]:
            stdout.write("rules: (使われるルールがありません。ターンの終わりには止めない)\n")
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
    # 判定に渡した payload。hook はこの 3 つを Claude Code から受け取り、判定がそれで変わる。
    used = payload_of(root, call)
    stdout.write(
        f"payload: cwd={used['cwd']}"
        f"  permission_mode={used['permission_mode'] or '(無し)'}"
        f"  agent_id={used['agent_id'] or '(無し)'}\n"
    )
    if out["reason"]:
        stdout.write(f"reason: {out['reason']}\n")
    if out["degraded"]:
        stdout.write(f"degraded: {out['degraded']}（生の文字列に適用した）\n")
    if out["unwrapped"]:
        for layer in out["unwrapped"].split("\x00"):
            shown = " ".join(layer.replace("\x01", " ").split())
            stdout.write(f"unwrapped: {shown}（中で実行されるコマンドに適用した）\n")
    if out["fallback"]:
        stdout.write(f"fallback: {out['fallback']}（組み込みの既定で判定した）\n")

    if not out["rules"]:
        stdout.write("rules: (どのルールにもヒットしなかった)\n")
    else:
        stdout.write("rules:\n")
        for hit in out["rules"]:
            if hit["source"] == OUTSIDE:
                # チケットの範囲や組み込みの保護のように、ルールファイルの中に無い根拠。
                # ルールそのものがあれば、タイプと短く切った式を添える。
                if not hit["section"]:
                    stdout.write(f"  {hit['id']}（ルールファイルの外にある根拠）\n")
                    continue
                stdout.write(
                    f"  {hit['section']}:{hit['id']}（ルールファイルの外にある根拠）"
                    f"  {hit['kind']} {hit['written']!r}\n"
                )
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
    call: Call | None = None,
) -> int:
    """`--test` と同じ判定を JSON で出す。読み手は VS Code 拡張のルール管理画面。

    payload の欄の誤りは、JSON を出さずに終了コード 1 で返す（判定を試せていない）。
    """
    call = call or Call()
    problem = call_problem(call)
    if problem:
        stderr.write(f"ccnavi: {problem}\n")
        return 1
    body = {"version": TEST_VERSION, "root": root, "rules_path": conf.rules}
    body.update(try_one(stderr, conf, root, tool, subject, call))
    stdout.write(json.dumps(body, ensure_ascii=True, indent=1))
    stdout.write("\n")
    return 0


def _rules_hit(record: audit.Record) -> list[dict]:
    """ヒットしたルールを、タイプと翻訳後の式まで返す。

    翻訳後の式を出すのがこの試験の要。`glob` は正規表現に変わるので、
    書いたものとヒットするものの間に見えない変換が 1 つ挟まる。その変換を見せないと、
    ヒットしなかった理由をユーザが自分で辿れない。

    引くのは判定が適用したルールそのもの（`record.hits`）。ルールファイルを別の読み方で
    読み直すと、判定が足した組み込みや、レイヤーの和で重複として破棄された側の定義を
    引けず、判定の根拠と違うものを見せる（P9）。`hits` は `rules` の末尾と同じ順に並ぶので、
    id ではなく位置で対応させる。id は必須なので、同じ id のルールが複数あっても位置で引き分ける。

    `source` は `file`（ルールファイルから読んだルール）か `outside`（チケットの範囲や
    組み込みの保護・退避した既定のように、ルールファイルの外から来た根拠）。
    外の根拠でもルールそのものがあれば、タイプと式を出す。ただし式は短く切る。組み込みの
    保護の式は 2 万字を超えるものがあり、そのまま出すと 1 件の試験が読めなくなる。
    """
    if not record.rules:
        return []
    hits_of = list(record.hits)
    offset = len(record.rules) - len(hits_of)
    if offset < 0:
        hits_of, offset = [], len(record.rules)

    hits = []
    for i, name in enumerate(record.rules):
        rule = hits_of[i - offset] if i >= offset else None
        if rule is None or rules.record_name(rule) != name:
            hits.append(
                {
                    "id": name,
                    "source": OUTSIDE,
                    "section": "",
                    "kind": "",
                    "written": "",
                    "pattern": "",
                }
            )
            continue
        form = diagnose_shared.rule_form(rule)
        if not rule.from_file:
            form = {
                "kind": form["kind"],
                "written": _abbreviated(form["written"]),
                "pattern": _abbreviated(form["pattern"]),
            }
        hits.append(
            {
                "id": name,
                "source": FILE if rule.from_file else OUTSIDE,
                "section": rule.decision,
                **form,
            }
        )
    return hits


def _abbreviated(text: str) -> str:
    """外の根拠の式を、頭から `OUTSIDE_FORM_LIMIT` 字までに切る。切ったら全体の字数を添える。"""
    if len(text) <= OUTSIDE_FORM_LIMIT:
        return text
    return f"{text[:OUTSIDE_FORM_LIMIT]}…（全 {len(text)} 字）"


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
    # 理由と additionalContext は別のキーで、deny と ask では両方が届く。
    # 両方あるときは届く順に並べる。
    parts = [out.get("permissionDecisionReason") or "", out.get("additionalContext") or ""]
    return "\n\n".join(p for p in parts if p)


def load_expectations(path: str, root: str) -> list[dict]:
    """期待する判定を読んで、タイプの順に平らなリストにする。

    タイプの名前が期待する判定になる。`deny` なら止まるはず、`allow` なら通るはず。
    `subject` の合言葉 `/repo` は走らせた場所に読み替える。
    """
    with open(path, encoding="utf-8") as f:
        raw = yamlread.safe_load(f.read())
    if not isinstance(raw, dict):
        raise ValueError(f"期待する判定の形が違う。最上位はタイプの対応表のはず: {path}")
    expectations = []
    for want in rules.SECTIONS:
        for case in raw.get(want) or []:
            if not isinstance(case, dict):
                raise ValueError(
                    f"期待する判定の形が違う。タイプ {want} の 1 件が対応表ではない: {path}"
                )
            written = str(case.get("subject") or "")
            expectations.append(
                {
                    "expected": want,
                    "tool": str(case.get("tool") or ""),
                    "subject": written,
                    "resolved_subject": written.replace(EXPECTATION_PLACEHOLDER, root),
                    "why": str(case.get("why") or ""),
                }
            )
    return expectations


def run_expectations(stderr: TextIO, conf: settings.Settings, root: str, path: str) -> dict:
    """期待する判定をぜんぶ判定に掛けて、期待と突き合わせた結果を 1 つのマップにする。

    判定は `judge` を通す。ここで判定を作り直さないのが肝で、別の経路で確かめると、
    期待する判定が通ったのに実運用で失敗する、という一番まずい形になる。

    `ok` は期待どおりか。`skipped` は allow の期待が判定に入らずに通ったもので、
    呼び出しは通るので期待は満たしているが、通した理由が「allow にヒットした」では
    ない。ルールを書いてもヒットしない場所なので、食い違いとは別に数えて必ず見せる。
    """
    # 期待ごとに判定するので、共通レイヤーの苦情は期待の数だけ書かれる。
    # 行き先で読むレイヤーは期待ごとに
    # 違うので、まとめて破棄せずに、まだ書いていない行だけを書く。
    said: set[str] = set()
    results = []
    for expectation in load_expectations(path, root):
        heard = io.StringIO()
        out = try_one(heard, conf, root, expectation["tool"], expectation["resolved_subject"])
        for line in heard.getvalue().splitlines(keepends=True):
            if line not in said:
                said.add(line)
                stderr.write(line)
        want = expectation["expected"]
        got = out["verdict"] if out["known"] else audit.SKIP
        skipped = want == rules.ALLOW and got == audit.SKIP
        results.append(
            {
                **expectation,
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
        "expectations_path": path,
        "counts": counts,
        "mismatches": sum(1 for r in results if not r["ok"]),
        "skipped": sum(1 for r in results if r["skipped"]),
        "expectations": results,
    }


def test_expectations(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    path: str,
    as_json: bool,
) -> int:
    """`--test-expectations`。期待する判定をぜんぶ回して、食い違いを並べる。

    文字で出すときの終了コードは、食い違いが 1 件でもあれば 1。`/ccnavi-config`
    スキルと `scripts/check_rules.py` がそれを見る。JSON で出すときは常に 0 で、
    食い違いの数は本文の `mismatches` にある。読み手が「食い違った」と
    「試験そのものが失敗した」を終了コードで見分けられるように。
    """
    # 期待する判定を読んで、ぜんぶ回す。読めなければ 1 で終える。
    try:
        body = run_expectations(stderr, conf, root, path)
    except (OSError, ValueError) as exc:
        stderr.write(f"ccnavi: 期待する判定を読めない: {exc}\n")
        return 1

    # JSON はそのまま出す（終了コードは 0）。
    if as_json:
        stdout.write(json.dumps(body, ensure_ascii=True, indent=1))
        stdout.write("\n")
        return 0

    # 食い違い（期待と判定が違うもの）を、理由とヒットしたルートつきで並べる。
    for r in body["expectations"]:
        if r["ok"]:
            continue
        stdout.write(f"食い違い: {r['expected']} のはずが {r['verdict']}\n")
        stdout.write(f"  {r['tool']}  {r['subject']}\n")
        stdout.write(f"  なぜ deny/ask/allow に置いたか: {r['why']}\n")
        hit = ", ".join(f"{h['section']}:{h['id']}" for h in r["rules"] if h["source"] == FILE)
        if hit:
            stdout.write(f"  ヒットしたルール: {hit}\n")
        stdout.write("\n")
    for r in body["expectations"]:
        if not r["skipped"]:
            continue
        stdout.write(f"判定に入らずに通った（allow ではない）: {r['tool']}  {r['subject']}\n")
        stdout.write(f"  {r['why']}\n\n")
    for want, c in body["counts"].items():
        stdout.write(f"{want}: {c['ok']}/{c['total']}\n")
    stdout.write(f"食い違い {body['mismatches']} 件、判定に入らなかったもの {body['skipped']} 件\n")
    return 1 if body["mismatches"] else 0
