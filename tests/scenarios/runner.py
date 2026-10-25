"""受入シナリオを claude -p で回す実行器。手順と決まりは docs/42_claude/acceptance-scenarios.md。

普段のテストには含めない。人間に確認を取ってから、明確な指示があったときだけ回す。

    uv run python -m tests.scenarios.runner preflight <シナリオ>
    uv run python -m tests.scenarios.runner run <シナリオ>
    uv run python -m tests.scenarios.runner run <シナリオ> --confirmed
    uv run python -m tests.scenarios.runner run-all <ディレクトリ or glob> --max-total-usd <USD>
    uv run python -m tests.scenarios.runner run-all <...> --max-total-usd <USD> --confirmed <目印>
    uv run python -m tests.scenarios.runner next <出力の置き場>
    uv run python -m tests.scenarios.runner restore <出力の置き場>
    uv run python -m tests.scenarios.runner judge <出力の置き場> <判定の表のファイル> [--accept]

run-all は、使い捨てのリポジトリのシナリオ（人間の操作が無く、setup.reset を持つもの）だけを、
1 回の確認で直列に回す。--confirmed を付けずに打つと、回す ID の一覧・概要・base・費用の上限と
目印を出して止まる（終了コード 3）。目印は ID の集合・base・費用の上限・概要から作る。
実行の直前に作り直し、合わなければ止まる。実費の累計が --max-total-usd を超えそうなら、
次のシナリオは回さずに止まる。終了コードは 0 全部 pass / 1 fail がある・回せるシナリオが無い /
2 未成立か意味の判定待ちがある・上限が不正 / 3 確認が要る・止まった。

next は、人間の操作で止まったあと、人間が「次へ」と言ってから打つ。
restore は、人間の操作で止まった回を続けずにやめるとき、setup のファイルを消し、
setup.host の代役の置き場を消し、使い捨てのリポジトリを setup.reset の base へ戻して、
残し印を外す。next は、setup.host が fake-gitlab なら模擬を残した状態ファイルのまま起こし直す。

--confirmed を付けるのは、人間が「状態がきれいで最新か」（と、test_repo のシナリオでは
「test-repo にブランチや MR ができてよいか」）に答えたあとだけ。
付けずに打つと、確認に使う事実を出して止まる（終了コード 3）。
ccnavi 本体にブランチ・コミット・ワークツリーが残らないことは、
回す前後の状態を比べて確かめる（repo）。

終了コード: 0 全部通った / 1 不合格がある・setup のパスが合わず回せない /
2 未成立か意味の判定待ちがある /
3 人間の確認が要る・回す前の拒否・使い捨てのリポジトリを戻せなかった（手作業が要る） /
10 人間の操作で止まった（「次へ」のあと next で続ける。setup.reset の対象と setup.host の
置き場は残し、模擬だけ止める）。

判定は 3 つ。機械で比べられるもの（ccnavi の判定の記録、ファイルの最終状態、
コマンドの出力）は pass / fail。期待した呼び出しをエージェントが打たなかったものは
inconclusive（未成立。fail にしない）。意味で読むもの（judge）は、evidence.md に証拠を
抜き出し、別のエージェントが基準に照らして読む。
"""

import argparse
import contextlib
import glob
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import yaml

from ccnavi.infra import pathcase
from ccnavi.records import diaglog
from ccnavi.tickets import review_host
from tests.scenarios import hostenv

ROOT = Path(__file__).resolve().parents[2]
GIT_SH = "{root}/.ccnavi/scripts/ccnavi-git.sh"
STEP_TIMEOUT_SEC = 900
# setup.git / setup.reset / setup.files の追記が触れてよい唯一のリポジトリ（ルートからの相対）
DISPOSABLE_REL = "projects/test-repo"
LOG_NAME = "scenario-runner"
RUNNER = "uv run python -m tests.scenarios.runner"
LOCK_NAME = "ccnavi-scenario.lock"  # 対象の .git/ の中。git clean で消えず、status も汚さない
LEFT_NAME = "ccnavi-scenario.left"  # 戻さずに残した回の印（中身は出力の置き場）
SETUP_KEYS = {"files", "issue", "git", "reset", "host", "host_state"}
SHA40 = re.compile(r"[0-9a-fA-F]{40}")
# setup.git で、対象の外へ出られる git の指定
GIT_ESCAPES = ("-C", "--git-dir", "--work-tree")
# git の先頭（サブコマンドより前）に置くと、設定や実行ファイルを差し替えられる指定
GIT_GLOBAL_ESCAPES = ("-c", "--config-env", "--exec-path")

PASS, FAIL, INCONCLUSIVE, REVIEW = "pass", "fail", "inconclusive", "needs-review"

# claude を呼ぶ道は、`python -m tests.scenarios.runner` で起動されたときだけ開く。
# ほかのモジュール（全量テストに入るテストを含む）が import して呼んでも、ここで止まる。
# 全量テストに入らないのは「test_ で始まるモジュールが無いから」だけでなく、この門でも守る。
_ENTRY = False


def _require_entry():
    if not _ENTRY:
        raise RuntimeError(
            "受入シナリオは `python -m tests.scenarios.runner` から起動したときだけ回せる"
            "（テストや他のモジュールから呼ばない）"
        )


# ---- シナリオの読み込み ----


def load_scenario(path):
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    for key in ("id", "steps"):
        if key not in data:
            raise ValueError(f"{path}: {key} がありません")
    data.setdefault("model", "haiku")
    data.setdefault("budget_usd", 1.0)
    data.setdefault("permission_mode", "auto")
    data.setdefault("expect", {})
    _check_setup(path, data.get("setup"))
    for n, step in enumerate(data["steps"], 1):
        after = step.get("expect_after")
        if after is None:
            continue
        if step.get("human"):
            raise ValueError(
                f"{path}: step {n} 人間の操作の step には expect_after を付けられません"
            )
        extra = set(after) - {"decisions", "state"} if isinstance(after, dict) else None
        if extra is None:
            raise ValueError(
                f"{path}: step {n} expect_after は decisions と state を持つ dict です"
            )
        if extra:
            raise ValueError(
                f"{path}: step {n} expect_after に書けるのは decisions と state だけです: "
                + ", ".join(sorted(extra))
            )
    return data


def file_spec(value):
    """setup.files の値を (中身, 追記か) にする。文字列は従来どおり作って消す形。"""
    if isinstance(value, dict):
        return value["content"], bool(value.get("append"))
    return value, False


def check_git_argv(argv):
    """setup.git の 1 コマンドを検べる。問題があれば ValueError。"""
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
        raise ValueError("argv は空でない文字列の配列にする（シェルを介さない）")
    if argv[0] != "git":
        raise ValueError(f"先頭は git に限る: {argv[0]}")
    for a in argv[1:]:
        if a.startswith(GIT_ESCAPES):  # -C<パス>、--git-dir=<パス> も含む
            raise ValueError(f"{a} は対象の外へ出られるので書けない（-C・--git-dir・--work-tree）")
    for a in argv[1:]:
        if not a.startswith("-"):
            break  # サブコマンド。これより後ろの -c は、サブコマンドの引数（switch -c など）
        if a.startswith(GIT_GLOBAL_ESCAPES):
            raise ValueError(f"{a} は git の設定や実行ファイルを差し替えられるので書けない")


def _check_setup(path, setup):
    if setup is None:
        return
    if not isinstance(setup, dict):
        raise ValueError(f"{path}: setup は dict です")
    extra = set(setup) - SETUP_KEYS
    if extra:
        raise ValueError(
            f"{path}: setup に書けるのは {', '.join(sorted(SETUP_KEYS))} です: "
            + ", ".join(sorted(map(str, extra)))
        )
    appends = []
    files = setup.get("files") or {}
    if not isinstance(files, dict):
        raise ValueError(f"{path}: setup.files は パス: 中身 の dict です")
    for rel, value in files.items():
        if isinstance(value, dict):
            bad = set(value) - {"content", "append"}
            if bad:
                raise ValueError(
                    f"{path}: setup.files[{rel}] に書けるのは content と append だけです: "
                    + ", ".join(sorted(map(str, bad)))
                )
            if not isinstance(value.get("content"), str):
                raise ValueError(f"{path}: setup.files[{rel}] の content は文字列で必須です")
            if "append" in value and not isinstance(value["append"], bool):
                raise ValueError(f"{path}: setup.files[{rel}] の append は true / false です")
            if value.get("append"):
                appends.append(rel)
        elif not isinstance(value, str):
            raise ValueError(f"{path}: setup.files[{rel}] は文字列か {{content, append}} です")
    git = setup.get("git")
    if git is not None:
        if not isinstance(git, list) or not git:
            raise ValueError(f"{path}: setup.git は argv の配列の配列です")
        for argv in git:
            try:
                check_git_argv(argv)
            except ValueError as exc:
                raise ValueError(f"{path}: setup.git {argv!r}: {exc}") from None
    reset = setup.get("reset")
    if reset is not None:
        if not isinstance(reset, dict) or set(reset) != {"base"}:
            raise ValueError(f"{path}: setup.reset は base だけを持つ dict です")
        base = reset["base"]
        if not isinstance(base, str) or not SHA40.fullmatch(base):
            raise ValueError(
                f"{path}: setup.reset.base は 40 桁の 16 進のコミット SHA にする"
                "（短縮 SHA・ブランチ名・タグ名は受けない）"
            )
        reset["base"] = base.lower()
    if (git or appends) and reset is None:
        raise ValueError(
            f"{path}: setup.git と setup.files の追記は、戻す setup.reset が無いと使えない"
        )
    hostenv.validate(path, setup)


# ---- claude -p の出力（stream-json）を読む ----


def _blocks(message):
    content = (message or {}).get("content")
    return content if isinstance(content, list) else []


def parse_stream(text):
    """stream-json の行から、証拠に使うものを抜き出す。形が違う行は読み飛ばす。"""
    out = {
        "session_ids": [],
        "tool_calls": [],
        "tool_results": [],
        "hook_events": [],
        "final_text": "",
        "cost_usd": 0.0,
    }
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        sid = ev.get("session_id")
        if sid and sid not in out["session_ids"]:
            out["session_ids"].append(sid)
        kind = ev.get("type")
        if kind == "assistant":
            for b in _blocks(ev.get("message")):
                if b.get("type") == "tool_use":
                    out["tool_calls"].append(
                        {
                            "name": b.get("name"),
                            "input": b.get("input"),
                            "id": b.get("id"),
                            "parent": ev.get("parent_tool_use_id"),
                        }
                    )
        elif kind == "user":
            for b in _blocks(ev.get("message")):
                if b.get("type") == "tool_result":
                    out["tool_results"].append(
                        {
                            "is_error": bool(b.get("is_error")),
                            "content": b.get("content"),
                            "tool_use_id": b.get("tool_use_id"),
                            "parent": ev.get("parent_tool_use_id"),
                        }
                    )
        elif kind == "result":
            out["final_text"] = ev.get("result") or ""
            out["cost_usd"] += float(ev.get("total_cost_usd") or 0)
        elif "hook" in str(ev.get("subtype", "")) or "hook" in str(kind):
            out["hook_events"].append(ev)
    return out


# ---- ccnavi の判定の記録（logs/decisions.jsonl）を比べる ----


def read_decisions(log_path, session_ids):
    """判定の記録から、回したセッションの行だけを取り出す（他のセッションの行は混ざる）。"""
    rows = []
    sessions = set(session_ids)
    p = Path(log_path)
    if not p.exists():
        return rows
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("session") in sessions:
                rows.append(rec)
    return rows


def _selected(rec, cond):
    """どの呼び出しの話かを決める欄（event・tool・subject_contains）。"""
    if "event" in cond and rec.get("event") != cond["event"]:
        return False
    if "tool" in cond and rec.get("tool") != cond["tool"]:
        return False
    return not (
        "subject_contains" in cond and cond["subject_contains"] not in str(rec.get("subject", ""))
    )


def _satisfies(rec, cond):
    """その呼び出しがどう判定されたかを決める欄（decision・code・rules_include・decision_not・hinted）。"""
    if "decision" in cond and rec.get("decision") != cond["decision"]:
        return False
    if "decision_not" in cond and rec.get("decision") == cond["decision_not"]:
        return False
    if "code" in cond and rec.get("code") != cond["code"]:
        return False
    rules = rec.get("rules") or []
    # hinted は、その呼び出しで once の案内を渡した行にだけ付く。欄が無い行は false 扱い。
    if "hinted" in cond and bool(rec.get("hinted")) != bool(cond["hinted"]):
        return False
    return all(r in rules for r in cond.get("rules_include", []))


def check_decision(rows, cond):
    """期待する判定が出ているか。呼び出し自体が無ければ inconclusive（fail にしない）。"""
    picked = [r for r in rows if _selected(r, cond)]
    if not picked:
        return INCONCLUSIVE, "該当する呼び出しが記録に無い（エージェントが打たなかった）"
    n = sum(1 for r in picked if _satisfies(r, cond))
    need = cond.get("min_count", 1)
    if n >= need:
        return PASS, f"{n} 件が一致"
    seen = sorted({(r.get("decision"), r.get("code")) for r in picked}, key=str)
    return FAIL, f"一致 {n} 件（{need} 件が要る）。記録にあった判定: {seen}"


def check_decision_none(rows, cond):
    """出ていてはいけない判定が出ていないか。"""
    hit = [
        r
        for r in rows
        if _selected(r, cond)
        and _satisfies(
            r, {k: v for k, v in cond.items() if k not in ("event", "tool", "subject_contains")}
        )
    ]
    if hit:
        return FAIL, f"出ていた: {_clip(hit[0].get('subject', ''), 200, 'logs/decisions.jsonl')}"
    return PASS, "出ていない"


# ---- 最終状態 ----


def file_digest(path):
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def check_state(item, root):
    cwd = Path(root) / item["cwd"] if item.get("cwd") else Path(root)
    r = subprocess.run(
        item["run"], shell=True, cwd=cwd, capture_output=True, text=True, timeout=120
    )
    out = (r.stdout or "") + (r.stderr or "")
    if "expect_contains" in item and item["expect_contains"] not in out:
        tail = _clip(out.strip(), 300, "state の run を手で打ち直して確かめる")
        return FAIL, f"出力に「{item['expect_contains']}」が無い: {tail}"
    if "expect_not_contains" in item and item["expect_not_contains"] in out:
        return FAIL, f"出力に「{item['expect_not_contains']}」がある"
    return PASS, "一致"


# ---- 期待をまとめて評価する ----


def evaluate(expect, rows, raw_text, root, pre_digests):
    """期待ごとの結果の一覧 [(種類, 内容, 判定, 理由)] を返す。"""
    results = []
    for cond in expect.get("decisions", []):
        v, why = check_decision(rows, cond)
        results.append(("decisions", cond, v, why))
    for cond in expect.get("decisions_none", []):
        v, why = check_decision_none(rows, cond)
        results.append(("decisions_none", cond, v, why))
    for rel in expect.get("files_unchanged", []):
        now = file_digest(Path(root) / rel)
        v, why = (
            (PASS, "変わっていない")
            if now == pre_digests.get(rel)
            else (FAIL, "回す前と変わっている")
        )
        results.append(("files_unchanged", rel, v, why))
    for item in expect.get("state", []):
        v, why = check_state(item, root)
        results.append(("state", item, v, why))
    for s in expect.get("transcript_forbidden", []):
        v, why = (FAIL, "出力に出ている") if s in raw_text else (PASS, "出ていない")
        results.append(("transcript_forbidden", s, v, why))
    for criterion in expect.get("judge", []):
        results.append(("judge", criterion, REVIEW, "証拠を読んで判定する"))
    return results


def evaluate_after(step_expect, st, root):
    """step の expect_after を評価する。判定の行は、ここまでに回した全 step のセッションから探す。
    結果は evaluate と同じ形の [(種類, 内容, 判定, 理由)]。"""
    rows = read_decisions(Path(root) / "logs/decisions.jsonl", st["session_ids"])
    results = []
    for cond in step_expect.get("decisions", []):
        v, why = check_decision(rows, cond)
        results.append(("decisions", cond, v, why))
    for item in step_expect.get("state", []):
        v, why = check_state(item, root)
        results.append(("state", item, v, why))
    return results


def overall(results):
    kinds = [r[2] for r in results]
    for v in (FAIL, INCONCLUSIVE, REVIEW):
        if v in kinds:
            return v
    return PASS


# ---- ccnavi 本体の変化を確かめる（回す前と後で比べる） ----

REPO_CHANGED = "回す前と変わっている（HEAD・ブランチ・ワークツリーのどれか）"


def repo_snapshot(root):
    """ccnavi 本体の HEAD・ブランチの一覧・ワークツリーの一覧。回す前と後で比べる。"""
    parts = []
    for args in (
        ["rev-parse", "HEAD"],
        ["for-each-ref", "--format=%(refname)", "refs/heads"],
        ["worktree", "list", "--porcelain"],
    ):
        r = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=120
        )
        parts.append(r.stdout if r.returncode == 0 else f"エラー {r.returncode}: {r.stderr}")
    return "\n".join(parts)


def repo_result(pre, now):
    """回す前と後の ccnavi 本体の状態を比べる。(判定, 理由)。
    pre が無いとき（この決まりより前に作られた状態ファイル）は、確かめられないので未成立にする。"""
    if pre is None:
        return INCONCLUSIVE, "回す前の状態が無い（この実行より前に作られた状態ファイル）"
    if pre == now:
        return PASS, "変わっていない"
    return FAIL, REPO_CHANGED


# ---- 人間への確認に使う事実 ----


def preflight_facts(root):
    git = GIT_SH.format(root=root)
    lines = []
    for label, cwd in (
        ("ワークスペースルート", root),
        ("projects/test-repo", Path(root) / "projects/test-repo"),
    ):
        if not Path(cwd).is_dir():
            lines.append(f"## {label}: 置き場が無い（{cwd}）\n")
            continue
        for sub in ("status", "worktree list", "branch"):
            r = subprocess.run(
                f"sh {git} {sub}", shell=True, cwd=cwd, capture_output=True, text=True, timeout=120
            )
            lines.append(f"## {label}: git {sub}\n{(r.stdout or r.stderr).strip()}\n")
    return "\n".join(lines)


MODE_ENV = "CCNAVI_MODE"


def mode_check(root):
    """判定のモードが enable かを確かめる。(enable か, 人間に見せる文)。

    dry-run では、hook は拒否を報告するだけで止めない（enforced が false の行になる）。
    シナリオは「止まる」ことを確かめるので、enable 以外では回さない。値は、プロジェクトの
    .claude/settings.json の env と、実行の環境変数を見る。どちらかに enable 以外の値が
    あれば止める。値が無ければ enable（hook の既定と同じ）。
    """
    settings_path = Path(root) / ".claude/settings.json"
    try:
        declared = json.loads(settings_path.read_text(encoding="utf-8")).get("env", {})
    except FileNotFoundError:
        declared = {}
    except (OSError, ValueError) as exc:
        return (
            False,
            f"## 判定のモード: {settings_path} を読めない（{exc}）。確かめられないので回さない",
        )
    sources = [
        (".claude/settings.json の env", declared.get(MODE_ENV)),
        ("実行の環境変数", os.environ.get(MODE_ENV)),
    ]
    lines = ["## 判定のモード（CCNAVI_MODE）"]
    ok = True
    for label, value in sources:
        if value is None:
            lines.append(f"- {label}: 指定なし")
            continue
        lines.append(f"- {label}: {value}")
        if value.strip().lower() not in ("", "enable"):
            ok = False
    if ok:
        lines.append("enable で判定している。回してよい")
    else:
        lines.append(
            "enable ではない。dry-run などでは止まらない判定が混じるので回さない。"
            "モードを enable にしてから打ち直す（設定の変更はユーザが行う）"
        )
    return ok, "\n".join(lines)


# ---- 実行 ----


def run_step(scn, step, out, idx, session_id, resume_id, persist, root, issue_no=None, env=None):
    """env が None なら親の環境を継承する（setup.host が real のとき）。"""
    _require_entry()
    cmd = [
        "claude",
        "-p",
        fill_issue(step["prompt"].strip(), issue_no),
        "--model",
        scn["model"],
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-hook-events",
        "--max-budget-usd",
        str(round(scn["budget_usd"] / max(1, scn["_prompt_steps"]), 2)),
        "--permission-mode",
        scn["permission_mode"],
    ]
    if resume_id:
        cmd += ["--resume", resume_id]
    else:
        cmd += ["--session-id", session_id]
    if not persist:
        cmd += ["--no-session-persistence"]
    r = subprocess.run(
        cmd, cwd=root, capture_output=True, text=True, timeout=STEP_TIMEOUT_SEC, env=env
    )
    (out / f"step-{idx}.jsonl").write_text(r.stdout, encoding="utf-8")
    if r.stderr.strip():
        (out / f"step-{idx}.stderr").write_text(r.stderr, encoding="utf-8")
    if r.returncode != 0:
        # 失敗した step の出力は、ここでは判定に使わない（drive が止める）
        raise RuntimeError(f"claude が終了コード {r.returncode} で終わった")
    return parse_stream(r.stdout)


def _persist_needed(steps, idx):
    """続きの step が --resume でつなぐなら、セッションを残す。"""
    for nxt in steps[idx + 1 :]:
        if nxt.get("human"):
            continue
        return bool(nxt.get("resume"))
    return False


def _write_state(out, st):
    (out / "state.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


class Refusal(Exception):
    """回す前の拒否・停止。str() は、理由の行・`案内:` の行・最後に `次:` の 1 行。
    行頭の語は docs/42_claude/script-output.md。"""

    def __init__(self, what, next_line, *notes):
        super().__init__(what)
        self.what, self.next_line, self.notes = what, next_line, notes

    def __str__(self):
        return "\n".join([self.what, *[f"案内: {n}" for n in self.notes], f"次: {self.next_line}"])


# ---- 使い捨てのリポジトリ（setup.git / setup.reset / setup.files の追記が触れる唯一の対象） ----


def _git_env():
    """cwd で対象を決めさせるため、git の向き先を変える環境変数を外す。"""
    drop = (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
        "GIT_CONFIG_PARAMETERS",
    )
    return {k: v for k, v in os.environ.items() if k not in drop}


def _git(args, cwd, input=None, env=None):
    """env は、この git にだけ足す環境（docker-gitlab のトークン）。"""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        env={**_git_env(), **(env or {})},
        input=input,
        stdin=subprocess.DEVNULL if input is None else None,
    )


def _toplevel(path):
    """path が属するリポジトリの toplevel の実体。リポジトリでない・path が無いときは None。"""
    try:
        r = _git(["rev-parse", "--show-toplevel"], cwd=path)
    except OSError:
        return None
    return os.path.realpath(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None


def _common_dir(path):
    """リンクされたワークツリーも同じになる、リポジトリ共通の .git の実体（比べる形）。"""
    r = _git(["rev-parse", "--git-common-dir"], cwd=path)
    return pathcase.fold(os.path.realpath(os.path.join(path, r.stdout.strip())))


def _origin(top):
    r = _git(["remote", "get-url", "origin"], cwd=top)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return pathcase.fold(r.stdout.strip().rstrip("/").removesuffix(".git"))


def _inside(path, top):
    top = pathcase.fold(top).rstrip(os.sep)
    return pathcase.fold(path).startswith(top + os.sep)


def require_disposable_repo(root, target, allowed=None):
    """target が使い捨てのリポジトリだと確かめ、その toplevel（実体）を返す。違えば Refusal。

    setup.git・setup.reset・setup.files の追記は、すべてこれを通る。比べるのは toplevel の実体で、
    ccnavi 本体（root）と同じもの、origin が本体と同じものは断る。許可されるのは allowed
    （既定は root/projects/test-repo）の実体だけ。どちらかに origin が無いときは origin を比べない。
    """
    allowed = Path(allowed) if allowed is not None else Path(root) / DISPOSABLE_REL
    use = "setup.git・setup.reset・setup.files の追記は、使い捨てのリポジトリにだけ使える"
    top = _toplevel(target)
    if top is None:
        raise Refusal(
            f"対象 {target} は git のリポジトリではない（または無い）",
            f"{DISPOSABLE_REL} を用意してから打ち直す",
            use,
        )
    body = os.path.realpath(root)
    if pathcase.fold(top) == pathcase.fold(body):
        raise Refusal(
            f"対象が ccnavi 本体（ワークスペースルート）になっている: {top}",
            f"対象を使い捨てのリポジトリ（{DISPOSABLE_REL}）にして打ち直す",
            use,
        )
    if _common_dir(top) == _common_dir(body):
        raise Refusal(
            f"対象が ccnavi 本体と同じリポジトリのワークツリーになっている: {top}",
            f"本体とは別のリポジトリを {DISPOSABLE_REL} に置いて打ち直す",
            use,
        )
    mine, theirs = _origin(top), _origin(body)
    if mine is not None and mine == theirs:
        raise Refusal(
            f"対象の origin が ccnavi 本体の origin と同じ: {top}",
            f"本体とは別のリポジトリを {DISPOSABLE_REL} に置いて打ち直す",
            use,
        )
    want = os.path.realpath(allowed)
    if pathcase.fold(top) != pathcase.fold(want):
        raise Refusal(
            f"対象の実体 {top} が、許可された {want} と違う",
            f"対象を {DISPOSABLE_REL} にして打ち直す",
            use,
        )
    return top


def _git_dir(top):
    r = _git(["rev-parse", "--absolute-git-dir"], cwd=top)
    if r.returncode != 0:
        raise RuntimeError(f".git の場所を取れない: {top}: {r.stderr.strip()}")
    return Path(os.path.realpath(r.stdout.strip()))


def _log(root):
    return diaglog.get(LOG_NAME, str(root))


def _first_line(text):
    lines = text.strip().splitlines()
    return lines[0] if lines else ""


def _target_git(root, top, args, level="info", env=None):
    """対象で git を実行する（cwd は判定を通した toplevel）。コマンドと結果を診断ログに残す。
    level は、reset のように何度も呼ぶときだけ debug にする（ループの中で INFO を書かない）。"""
    r = _git(args, cwd=top, env=env)
    getattr(_log(root), level)(
        "対象の git を実行した",
        cmd=shlex.join(["git", *args]),
        rc=r.returncode,
        out_len=len(r.stdout),
        err=_first_line(r.stderr) if r.returncode else "",
    )
    return r


def run_setup_git(root, target, commands, allowed=None, host=None):
    """setup.git のコマンドを、対象の toplevel を cwd にして順に実行する（shell=False）。
    各コマンドのあと ccnavi 本体の repo_snapshot が変わっていたら止める。
    host（setup.host の辞書）があれば、本物の origin への通信を bare へ向ける -c を足す。"""
    top = require_disposable_repo(root, target, allowed)
    before = repo_snapshot(root)
    via = hostenv.git_via(host) if host else []
    env = hostenv.git_env(host) if host else None
    for argv in commands:
        try:
            check_git_argv(argv)
        except ValueError as exc:
            raise Refusal(f"setup.git を実行しない: {exc}", "シナリオの setup.git を直す") from None
        r = _target_git(root, top, [*via, *argv[1:]], env=env)
        if r.returncode != 0:
            raise Refusal(
                f"setup.git が失敗した: {shlex.join(argv)}（終了コード {r.returncode}）",
                "原因を直して打ち直す",
                f"git の出力: {_first_line(r.stderr)}",
                "対象は後始末で戻す（setup.reset があるとき）",
            )
        if repo_snapshot(root) != before:
            raise Refusal(
                f"setup.git の実行で ccnavi 本体の HEAD・ブランチ・ワークツリーが変わった: "
                f"{shlex.join(argv)}",
                "ccnavi 本体の状態を手で確かめて戻し、シナリオの setup.git を直す",
                "回し続けない",
            )


CCNAVI_STATE_DIRS = ("ccnavi-direct", "ccnavi-integrate", "ccnavi-integrate-done")
CCNAVI_REF_PREFIX = "refs/ccnavi"


def _git_common_path(top):
    """対象の git の共通ディレクトリ（リンクされたワークツリーでも本体の .git）の実体。"""
    r = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=top)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"git の共通ディレクトリを取れない: {top}: {r.stderr.strip()}")
    return Path(os.path.realpath(r.stdout.strip()))


def _clear_ccnavi_state(root, top, run):
    """ccnavi 自身が対象の git に書く状態（直接作業の記録・取り込みの marker・専用の ref）を消す。
    top は require_disposable_repo を通したものだけ。消すのは共通ディレクトリ直下の名指しの 3 つと
    refs/ccnavi の下だけで、logs/ など .gitignore 済みのものは触らない。"""
    common = _git_common_path(top)
    for name in CCNAVI_STATE_DIRS:
        path = common / name
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    for ref in run("for-each-ref", "--format=%(refname)", CCNAVI_REF_PREFIX).split():
        run("update-ref", "-d", ref)


def reset_repo(root, target, base, allowed=None):
    """対象を base へ戻す。順序: ワークツリーを消す → prune → ccnavi の記録・marker・ref を消す →
    main を base に合わせて切り替える → main 以外のローカルブランチを消す → タグを消す →
    stash を空にする → reset --hard → clean -fd。
    ccnavi の状態は、ワークツリーを消して prune したあとに消す。記録は一時ワークツリーや
    ブランチを指すので、ワークツリーが消せずに止まった回で、記録だけ先に消さない。
    clean に -x は付けない（.gitignore 済みを消さない）。refs/remotes は触らない。"""
    top = require_disposable_repo(root, target, allowed)

    def run(*args):
        r = _target_git(root, top, list(args), level="debug")
        if r.returncode != 0:
            _log(root).error("対象を戻せなかった", target=top, base=base, cmd=" ".join(args[:3]))
            raise Refusal(
                f"対象を戻す途中で失敗した: git {' '.join(args[:3])}（終了コード {r.returncode}）",
                f"{top} を手で {base} に戻す",
                f"git の出力: {_first_line(r.stderr)}",
            )
        return r.stdout

    run("cat-file", "-e", f"{base}^{{commit}}")
    paths = [
        line[len("worktree ") :]
        for line in run("worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    ]
    keep = {pathcase.fold(os.path.realpath(p)) for p in (paths[:1] + [top])}
    for p in paths:
        if pathcase.fold(os.path.realpath(p)) not in keep:
            run("worktree", "remove", "--force", p)
    run("worktree", "prune")
    _clear_ccnavi_state(root, top, run)
    run("checkout", "-f", "-B", "main", base)
    for name in run("for-each-ref", "--format=%(refname:short)", "refs/heads").split():
        if name != "main":
            run("branch", "-D", name)
    tags = run("tag", "-l").split()
    for i in range(0, len(tags), 100):
        run("tag", "-d", *tags[i : i + 100])
    run("stash", "clear")
    run("reset", "--hard", base)
    run("clean", "-fd")
    _log(root).info("対象を base に戻した", target=top, base=base)


def _pid_alive(pid):
    """その PID のプロセスが生きているか。判定できない（権限など）ときは生きていると見なす。
    Windows の os.kill はプロセスを終わらせてしまうので、OpenProcess で見る。"""
    if os.name == "nt":
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            k32.CloseHandle(handle)
            return True
        return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: その PID は無い
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _lock_pid(lock):
    try:
        pid = int(lock.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def acquire_target(top, out, resuming=False):
    """対象を排他で使い始める。ロックのパスを返す。使えなければ Refusal。

    ロックは対象の .git/ の中（O_CREAT|O_EXCL。中身は PID）。あれば PID が生きているかを見て、
    生きている・判定できないなら断り、死んでいれば奪う。戻さずに残した回の印があれば、
    その回を続けるとき（resuming で出力の置き場が同じ）以外は断る。"""
    git_dir = _git_dir(top)
    left = git_dir / LEFT_NAME
    if left.exists():
        who = left.read_text(encoding="utf-8").strip()
        if not (resuming and pathcase.fold(who) == pathcase.fold(str(out))):
            raise Refusal(
                f"前の回が対象を戻さずに残している: {who}",
                f"続けるなら `{RUNNER} next {who}`、やめるなら `{RUNNER} restore {who}`",
                "戻せずに残した回も同じ。restore で戻す",
            )
    lock = git_dir / LOCK_NAME
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pid = _lock_pid(lock)
            if pid is None or _pid_alive(pid):
                raise Refusal(
                    f"同じ対象を別の回が使っている（PID {pid or '読めない'}）: {lock}",
                    "その回が終わってから打ち直す",
                    "ロックの中身が PID として読めないときは、使っている回が無いことを"
                    "確かめてからファイルを消す",
                ) from None
            with contextlib.suppress(FileNotFoundError):
                lock.unlink()  # 持ち主が死んでいる。奪う
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return str(lock)
    raise Refusal(f"ロックを奪えなかった: {lock}", "少し待って打ち直す")


def release_target(st, root, hold=False):
    """対象を放す。例外は出さない。戻せなかったときは文面を出し、その文面を返す。

    hold: 人間の操作で止めた回。base へ戻さず、ロックだけ放して残し印を書く（続きは next）。
    それ以外: base へ戻し、ロックと残し印を放す。戻せなければ残し印を書き、次の回を断らせる。"""
    tgt = st.get("target")
    if not tgt or st.get("released"):
        return None
    top, base, out = tgt["path"], tgt.get("base"), st.get("out", "")
    err = None
    try:
        git_dir = _git_dir(top)
        left = git_dir / LEFT_NAME
        if not hold and base:
            try:
                reset_repo(root, top, base)
            except Exception as exc:  # 戻せない原因は何でも、手作業の案内にする
                err = str(exc) or repr(exc)
        if hold or err:
            left.write_text(out, encoding="utf-8")
        else:
            with contextlib.suppress(FileNotFoundError):
                left.unlink()
        with contextlib.suppress(FileNotFoundError):
            (git_dir / LOCK_NAME).unlink()
    except Exception as exc:
        err = err or repr(exc)
    if hold:
        st["held"] = True
        return None
    if err:
        text = str(
            Refusal(
                f"対象 {top} を {base} に戻せなかった。手作業で戻す必要がある",
                f"{top} を手で {base} に戻し、`{RUNNER} restore {out}` で印を外す",
                f"原因: {_first_line(err)}",
                "戻すまで、次の回は始められない",
            )
        )
        print(text)
        return text
    st["released"] = True
    return None


def resolve_append(rel, root, toplevel):
    """追記の対象（ワークスペースルートからの相対）を実体にして返す。
    toplevel の中にある、git 管理下のファイルだけ。.. ・絶対パス・外へ出るリンクは断る。"""
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        raise Refusal(
            f"追記のパスは、ワークスペースルートからの相対パスで .. を含まない形にする: {rel}",
            "setup.files のパスを直す",
        )
    real = os.path.realpath(Path(root) / p)
    if not _inside(real, toplevel):
        raise Refusal(
            f"追記のパスが対象 {toplevel} の外にある（外へ出るリンクを含む）: {rel}",
            "setup.files のパスを、対象の中のファイルにする",
        )
    if not os.path.isfile(real):
        raise Refusal(
            f"追記の対象のファイルが無い: {rel}",
            "追記は対象にすでにあるファイルにだけ使える。パスを直すか、作って消す形（文字列）にする",
        )
    rel_in_top = os.path.relpath(real, toplevel)
    r = _git(["--literal-pathspecs", "ls-files", "--error-unmatch", "--", rel_in_top], cwd=toplevel)
    if r.returncode != 0:
        raise Refusal(
            f"追記の対象が git 管理下にない: {rel}",
            "追記は git 管理下のファイルにだけ使える。パスを直す",
        )
    return real


def check_before_run(scn, root, top):
    """回す前（確認文より前）に確かめられる setup の誤りを、Refusal で返す。何も変えない。
    追記の対象は reset のあとにもう一度確かめる（base の中身で変わりうるため）。"""
    setup = scn.get("setup") or {}
    for rel, v in (setup.get("files") or {}).items():
        if file_spec(v)[1]:
            resolve_append(rel, root, top)
    base = (setup.get("reset") or {}).get("base")
    if base and _git(["cat-file", "-e", f"{base}^{{commit}}"], cwd=top).returncode != 0:
        raise Refusal(
            f"setup.reset.base {base} が対象 {top} にコミットとして無い",
            "base を、対象にあるコミットの 40 桁の SHA に直して打ち直す",
            "対象には何もしていない",
        )
    if scenario_host(scn) == hostenv.DOCKER:
        try:
            hostenv.check_docker_gitlab()
        except (RuntimeError, OSError) as exc:
            raise Refusal(
                f"docker-gitlab の GitLab を使えない: {exc}",
                "`uv run python tests/manual/gitlab/docker_gitlab.py up` で立て"
                "（動いていれば `status` で確かめる。docs/42_claude/gitlab-manual.md）、打ち直す",
                "対象にも GitLab にも何もしていない",
            ) from None


def setup_clashes(scn, root):
    """setup.files のパスのうち、回せない原因になるもの。
    作る形（文字列）は、すでにあるもの（後片付けで消してしまうため）。
    追記の形（append: true）は、まだ無いもの（追記は対象にある管理下のファイルにだけ使う）。"""
    files = (scn.get("setup") or {}).get("files") or {}
    return [rel for rel, v in files.items() if (Path(root) / rel).exists() != file_spec(v)[1]]


def scenario_host(scn):
    """setup.host の値（bare / fake-gitlab / docker-gitlab / real）。欄が無ければ real。"""
    return (scn.get("setup") or {}).get("host", hostenv.DEFAULT_HOST)


def uses_target(scn):
    """使い捨てのリポジトリを触る setup（git・reset・追記・代役を使う相手）があるか。"""
    setup = scn.get("setup") or {}
    appends = any(file_spec(v)[1] for v in (setup.get("files") or {}).values())
    return bool(
        setup.get("git")
        or setup.get("reset")
        or appends
        or hostenv.uses_stub_git(scenario_host(scn))
    )


def _setup(scn, root, toplevel=None):
    """setup のファイルを作り、追記する。(作ったファイル, 作ったディレクトリ) を返す。
    作る形は、既にあるパスには触らない（setup_clashes で先に確かめてある）。
    ディレクトリは、この関数が新しく作ったものだけ。追記は作ったものに数えない
    （戻すのは setup.reset。消すと管理下のファイルを削除してしまう）。
    追記の対象は、先に全部確かめてから（toplevel は判定を通した対象）、書き始める。"""
    files, dirs, appends = [], [], {}
    entries = {
        rel: file_spec(v) for rel, v in ((scn.get("setup") or {}).get("files") or {}).items()
    }
    for rel, (body, append) in entries.items():
        if append:
            if toplevel is None:
                raise Refusal(
                    "追記の対象を決められない（対象の判定を通っていない）", "setup を直す"
                )
            appends[resolve_append(rel, root, toplevel)] = body
    try:
        for rel, (body, append) in entries.items():
            if append:
                continue
            p = Path(root) / rel
            missing = []
            d = p.parent
            while d != Path(root) and not d.exists():
                missing.append(d)
                d = d.parent
            for d in reversed(missing):
                d.mkdir()
                dirs.append(str(d.relative_to(root)))
            p.write_text(body, encoding="utf-8")
            files.append(rel)
        for real, body in appends.items():
            with open(real, "a", encoding="utf-8", newline="") as f:
                f.write(body)
    except BaseException:
        _cleanup(files, dirs, root)  # 途中で失敗したとき、ここまでに作ったものを残さない
        raise
    return files, dirs


def _cleanup(made, made_dirs, root):
    for rel in made:
        p = Path(root) / rel
        if p.is_file():
            p.unlink()
    for rel in sorted(made_dirs, key=len, reverse=True):  # 深いものから。空のときだけ消す
        with contextlib.suppress(OSError):
            (Path(root) / rel).rmdir()


ISSUE_PLACEHOLDER = "{issue}"


def issue_number(url):
    """issue 作成の出力から、issue の番号を取る。

    URL の行（GitHub は `.../issues/12`、GitLab は `.../-/issues/12`）の末尾の数字。
    glab は URL の前後に説明の行を出すので、最終行ではなく URL の行を探す。
    """
    for line in reversed(url.strip().splitlines()):
        tail = line.strip().rstrip("/").rsplit("/", 1)[-1]
        if "://" in line and "/issues/" in line and tail.isdigit():
            return int(tail)
    raise RuntimeError(f"issue の番号が取れない: {url.strip()}")


def host_cli(kind):
    """ホストの種類（classify の kind）から CLI の名前を返す。GitLab は glab、GitHub は gh。"""
    if kind == "github":
        return "gh"
    if kind == "gitlab":
        return "glab"
    raise RuntimeError("origin からホストの種類を判別できない")


def issue_create_args(kind, issue):
    """issue を作る引数の列（CLI の名前から始まる）。"""
    title, body = issue["title"], issue.get("body", "")
    if kind == "github":
        return ["gh", "issue", "create", "--title", title, "--body", body]
    return [host_cli(kind), "issue", "create", "--title", title, "--description", body, "--yes"]


def issue_close_args(kind, number):
    """issue を閉じる引数の列（CLI の名前から始まる）。"""
    return [host_cli(kind), "issue", "close", str(number)]


def repo_host_kind(repo_dir):
    """リポジトリの origin から、ホストの種類（github / gitlab。読めなければ空）を返す。"""
    r = subprocess.run(
        ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        return ""
    return review_host.classify(r.stdout.strip()).kind


def make_issue(issue, root):
    """setup の issue の欄どおりに、test-repo へ issue を新しく 1 つ作る。番号を返す。"""
    repo = Path(root) / issue["repo"]
    args = issue_create_args(repo_host_kind(repo), issue)
    r = subprocess.run(
        args, cwd=repo, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL
    )
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])} が終了コード {r.returncode}: {r.stderr.strip()}")
    return issue_number(r.stdout)


def close_issue(issue, number, root):
    """回したあとに、作った issue を閉じる（setup の issue の close: true のときだけ）。"""
    repo = Path(root) / issue["repo"]
    try:
        r = subprocess.run(
            issue_close_args(repo_host_kind(repo), number),
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        # 後片付けの途中なので、対象を戻す処理を止めない
        print(f"issue #{number} を閉じられなかった: {exc}")
        return
    if r.returncode != 0:
        print(f"issue #{number} を閉じられなかった: {r.stderr.strip()}")


def fill_issue(text, number):
    """prompt の {issue} を、作った issue の番号に置き換える。番号が無ければそのまま。"""
    return text if number is None else text.replace(ISSUE_PLACEHOLDER, str(number))


def _host_env(st):
    """claude に渡す環境。setup.host が real 以外のときだけ作る（real は None）。"""
    host = st.get("host")
    return hostenv.step_env(host) if host and not host.get("down") else None


def _teardown_host(st, root):
    """模擬を止め、代役の置き場を消す（何度呼んでもよい）。docker-gitlab のプロジェクトを
    消せなかったとき（手作業が要る）は True。"""
    if st.get("host"):
        return bool(hostenv.teardown(root, st["host"], _git))
    return False


def _start_host(scn, st, out, root, top):
    """setup.host が bare / fake-gitlab / docker-gitlab なら、相手を用意して state に残す。"""
    if not hostenv.uses_stub_git(scenario_host(scn)):
        return
    try:
        # prepare が途中で失敗すると辞書は返らない。作ったものは prepare 自身が消す
        st["host"] = hostenv.prepare(root, top, scn["setup"], _git, scn["id"])
    except RuntimeError as exc:
        raise Refusal(
            f"setup.host の相手を用意できなかった: {exc}",
            f"対象 {top} の origin を確かめて打ち直す",
            "対象は後始末で戻す",
        ) from None
    _write_state(out, st)


def _failed_step_cost(scn, out, num, exc):
    """正常に終わらなかった step の実費の見積もり。累計の上限が過小に見えないようにする。
    出力が残っていれば（終了コードが 0 でない）その result の費用、時間切れで出力が無ければ
    step の上限（--max-budget-usd に渡す値。実費より多めの保守側）。それ以外は 0。"""
    path = out / f"step-{num}.jsonl"
    if path.exists():
        return parse_stream(path.read_text(encoding="utf-8"))["cost_usd"]
    if isinstance(exc, subprocess.TimeoutExpired):
        return round(scn["budget_usd"] / max(1, scn["_prompt_steps"]), 2)
    return 0.0


def drive(scn, out, st, root):
    """st['next_step'] から、人間の操作の step か末尾まで進める。
    人間の操作で止まったら 10、末尾まで進んだら None を返す。
    expect_after を満たさなければ 11 を返す。"""
    steps = scn["steps"]
    while st["next_step"] < len(steps):
        idx = st["next_step"]
        step = steps[idx]
        if step.get("human"):
            print(f"\n== 人間の操作（step {idx + 1}）==")
            print(step.get("instruction", "").strip())
            for c in step.get("check", []):
                print(f"  確認: {c}")
            print(
                "\n済んだら人間が「次へ」と言う。続きは: python -m tests.scenarios.runner next", out
            )
            st["next_step"] = idx + 1
            _write_state(out, st)
            return 10
        resume_id = st["session_ids"][-1] if step.get("resume") and st["session_ids"] else None
        sid = str(uuid.uuid4())
        t0 = time.time()
        persist = _persist_needed(steps, idx)
        try:
            ev = run_step(
                scn,
                step,
                out,
                idx + 1,
                sid,
                resume_id,
                persist,
                root,
                st.get("issue_no"),
                env=_host_env(st),
            )
        except Exception as e:  # 起動できない・時間切れ・終了コードが 0 でない
            st["cost_usd"] += _failed_step_cost(scn, out, idx + 1, e)
            # 後続は回さず止める。finish に回して、後片付けを通す
            st["halt"] = {
                "step": idx + 1,
                "kind": "run",
                "reasons": [f"step {idx + 1} が正常に終わらなかった: {e}"],
            }
            _write_state(out, st)
            print(f"step {idx + 1}: 正常に終わらなかった（{e}）。後続の step には進まず止める")
            return 11
        print(f"step {idx + 1}: {time.time() - t0:.0f} 秒、費用 {ev['cost_usd']:.3f} USD")
        for s in ev["session_ids"] or [sid]:
            if s not in st["session_ids"]:
                st["session_ids"].append(s)
        st["cost_usd"] += ev["cost_usd"]
        st["next_step"] = idx + 1
        _write_state(out, st)
        after = step.get("expect_after")
        if after:
            try:
                bad = [r for r in evaluate_after(after, st, root) if r[2] != PASS]
            except Exception as e:  # check_state の timeout・cwd 不在など。止めて finish に回す
                bad = [("expect_after", after, INCONCLUSIVE, f"評価で例外: {e!r}")]
            if bad:
                st["halt"] = {
                    "step": idx + 1,
                    "reasons": [
                        f"{r[0]} {json.dumps(r[1], ensure_ascii=False)}: {r[3]}" for r in bad
                    ],
                }
                _write_state(out, st)
                print(f"step {idx + 1}: expect_after を満たさない。後続の step には進まず止める")
                return 11
    return None


HOST_NOTES = {
    "real": "real: 本物の GitHub / GitLab を相手にした",
    "bare": "bare: ローカルの bare を origin にした。ホストの API は失敗（確かめられなかった）",
    "fake-gitlab": "fake-gitlab: ローカルの bare と fake_gitlab.py を相手にした",
    "docker-gitlab": "docker-gitlab: Docker の本物の GitLab CE に使い捨てのプロジェクトを作った。"
    "CE にない機能（変更要求など）は確かめられない",
}
FAKE_NOTE = "。模擬は本物と同じ形を返す保証が無く、模擬のずれで見逃した問題がありうる"


def host_note(host):
    return HOST_NOTES[host] + (FAKE_NOTE if hostenv.is_fake(host) else "")


def finish(scn, out, st, root):
    try:
        # state の run は setup のファイルが残っているうちに回す。消すのは判定のあと
        raw = "".join(p.read_text(encoding="utf-8") for p in sorted(out.glob("step-*.jsonl")))
        rows = read_decisions(Path(root) / "logs/decisions.jsonl", st["session_ids"])
        results = evaluate(scn["expect"], rows, raw, root, st["pre_digests"])
        halt = st.get("halt")
        if halt:
            # 止まった回は後続の step が回っていないので、decisions・state は必ず落ちる。外す
            results = [r for r in results if r[0] not in ("decisions", "state")]
            kind = halt.get("kind", "expect_after")
            results.append((kind, {"step": halt["step"]}, INCONCLUSIVE, "; ".join(halt["reasons"])))
        # ccnavi 本体に、ブランチ・コミット・ワークツリーが残っていないかを実際の状態で確かめる
        v, why = repo_result(st.get("pre_repo"), repo_snapshot(root))
        results.append(("repo", "ccnavi 本体の HEAD・ブランチ・ワークツリー", v, why))
        note = host_note(scenario_host(scn))
        if (st.get("host") or {}).get("version"):
            note += f"（GitLab {st['host']['version']}）"
        results.append(("host", "setup.host", PASS, note))
    finally:
        _cleanup(st["setup_made"], st.get("setup_dirs", []), root)
        st["setup_made"] = []  # restore が、あとから同じパスに作られたファイルを消さないように
        issue = (scn.get("setup") or {}).get("issue")
        if issue and issue.get("close") and st.get("issue_no"):
            close_issue(issue, st["issue_no"], root)
        undeleted = _teardown_host(st, root)
        not_restored = release_target(st, root)
        _write_state(out, st)
    if not_restored:
        results.append(("reset", st["target"]["path"], FAIL, "base に戻せなかった。手作業が要る"))
    if undeleted:
        host = st["host"]
        results.append(
            (
                "host-delete",
                host["url"],
                FAIL,
                f"プロジェクト（ID {host['project_id']}）を消せなかった。手作業で消す",
            )
        )
    write_report(scn, out, st, results, rows, raw)
    final = overall(results)
    print(f"\n結果: {final}（レポート: {out}/report.md）")
    if not_restored or undeleted:
        return 3  # レポートは書いた。次の回を始めさせない
    return {PASS: 0, FAIL: 1, INCONCLUSIVE: 2, REVIEW: 2}[final]


EVIDENCE_LIMIT = 1000
CONTEXT_LIMIT = 2000


def _clip(text, limit, where):
    """limit を超えたら切り、切ったことと全量の在りかを末尾に書く。"""
    text = str(text)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…（全 {len(text)} 文字のうち先頭 {limit} 文字。全量は {where}）"


def _text_of(content):
    """ツール結果の content（文字列かブロックの列）を 1 つの文字列にする。"""
    if content is None:
        return ""
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                parts.append(b.get("text") or f"[{b.get('type', '?')} ブロック]")
            else:
                parts.append(str(b))
        return "\n".join(parts)
    return str(content)


def _hook_payload(ev):
    """hook_response の stdout / output が JSON オブジェクトならその dict を返す。違えば {}。"""
    for key in ("stdout", "output"):
        raw = ev.get(key)
        if isinstance(raw, str) and raw.lstrip().startswith("{"):
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
    return {}


def summarize_hooks(hook_events, where):
    """hook の応答を、判定に要るものの行と、省いたものの件数に分ける。
    残すのは、拒否・確認（permissionDecision が deny / ask、または decision が block）、
    案内文（additionalContext。SessionStart のものを除く）、中身のある Stop の応答、
    終了コードが 0 でない応答、停止（continue: false / stopReason）、
    systemMessage（SessionStart を除く）も残す。
    hook_started / hook_progress は数えない。SessionStart と出力が空の応答は件数だけにする。"""
    lines, omitted = [], {}
    for ev in hook_events:
        if ev.get("subtype") != "hook_response":
            continue
        name = ev.get("hook_event") or ev.get("hook_event_name") or "?"
        data = _hook_payload(ev)
        exit_code = ev.get("exit_code")
        spec = data.get("hookSpecificOutput")
        spec = spec if isinstance(spec, dict) else {}
        decision = spec.get("permissionDecision") or data.get("decision")
        reason = spec.get("permissionDecisionReason") or data.get("reason")
        context = spec.get("additionalContext")
        label = f"{name} {ev.get('hook_name', '')}".strip()
        if decision in ("deny", "ask", "block"):
            # 拒否の文面は切らない
            lines.append(f"- [{label}] {decision}: {reason or ''}")
        elif isinstance(exit_code, int) and exit_code != 0:
            # 終了コードが 0 でない応答（exit 2 のブロックなど）は、文面を切らない
            body = (
                (ev.get("stderr") or "").strip()
                or (ev.get("stdout") or "").strip()
                or str(ev.get("output") or "")
            )
            lines.append(f"- [{label}] 終了コード {exit_code}: {body}")
        elif data.get("continue") is False or data.get("stopReason"):
            lines.append(f"- [{label}] 停止: {data.get('stopReason') or ''}")
        elif name == "Stop" and (data or ev.get("stdout") or ev.get("stderr")):
            body = (
                json.dumps(data, ensure_ascii=False)
                if data
                else ev.get("stdout") or ev.get("stderr")
            )
            lines.append(f"- [{label}] {_clip(body, EVIDENCE_LIMIT, where)}")
        elif context and name != "SessionStart":
            lines.append(f"- [{label}] 案内: {_clip(context, CONTEXT_LIMIT, where)}")
        elif data.get("systemMessage") and name != "SessionStart":
            lines.append(f"- [{label}] 通知: {_clip(data['systemMessage'], CONTEXT_LIMIT, where)}")
        else:
            omitted[name] = omitted.get(name, 0) + 1
    return lines, omitted


def hook_denial_texts(hook_events):
    """拒否・確認・ブロックの hook 応答にある理由の文面を集める。
    ツール結果のエラーとの重複判定に使う。"""
    texts = []
    for ev in hook_events:
        if ev.get("subtype") != "hook_response":
            continue
        data = _hook_payload(ev)
        spec = data.get("hookSpecificOutput")
        spec = spec if isinstance(spec, dict) else {}
        decision = spec.get("permissionDecision") or data.get("decision")
        reason = spec.get("permissionDecisionReason") or data.get("reason")
        text = str(reason).strip() if reason else ""
        if decision in ("deny", "ask", "block") and text:
            texts.append(text)
    return texts


def _duplicates_hook(text, denial_texts):
    """エラーの文面が、hook の拒否理由と（前後の空白を除いて）まったく同じなら True。"""
    text = text.strip()
    if not text:
        return False
    return text in denial_texts


def _step_decisions_table(rows, step_sessions):
    """判定の記録を、見出し付きの表にする。step_sessions は {step 番号: [session id]}。"""
    lines = [
        "| step | event | tool | decision | code | rules | subject |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        steps = sorted(n for n, sids in step_sessions.items() if r.get("session") in sids)
        step = ",".join(str(n) for n in steps) or "?"
        rules = r.get("rules", "")
        if isinstance(rules, list):
            rules = ",".join(str(x) for x in rules)
        elif rules is None:
            rules = ""
        subject = _clip(r.get("subject") or "", 500, "logs/decisions.jsonl")
        cells = [
            step,
            r.get("event", ""),
            r.get("tool", ""),
            r.get("decision", ""),
            r.get("code", ""),
            rules,
            subject,
        ]
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    return lines


def _cell(s):
    """表のセルに入れる文字列から、列と行を崩す文字を除く。"""
    return str(s).replace("|", "\\|").replace("\n", " ")


def _results_table(results, limit=None):
    cut = (lambda s: s) if limit is None else (lambda s: s[:limit])
    lines = ["| 種類 | 期待 | 判定 | 理由 |", "|---|---|---|---|"]
    for kind, cond, v, why in results:
        c = json.dumps(cond, ensure_ascii=False) if not isinstance(cond, str) else cond
        lines.append(f"| {_cell(kind)} | {_cell(cut(c))} | {_cell(v)} | {_cell(cut(why))} |")
    return lines


ANSWER_LIMIT = 1500


def step_answer_lines(scn, out):
    """step ごとの入力プロンプトと、claude の最後の出力を報告に出す行。
    人間の操作の step は、その指示を出す。"""
    lines = []
    for num, step in enumerate(scn.get("steps") or [], 1):
        lines.append("")
        if step.get("human"):
            lines.append(f"### step {num}（人間の操作）")
            lines.append(_one_line(step.get("instruction", ""), 200))
            continue
        lines.append(f"### step {num}")
        lines.append("【入力プロンプト】")
        lines.append(step.get("prompt", "").strip())
        lines.append("【最終出力】")
        path = out / f"step-{num}.jsonl"
        if not path.exists():
            lines.append("未実行")
            continue
        final = parse_stream(path.read_text(encoding="utf-8"))["final_text"]
        lines.append(_clip(final, ANSWER_LIMIT, path.name) or "（出力なし）")
    return lines


# ---- 渡った文脈・拒否の傾向・文面の一覧（evidence.md・report.md・messages.md） ----

CONTEXT_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse")
GUIDE_HEADS = ("次:", "案内:", "必須:")
REPEAT_MIN = 3
AGENT_TOOL = "Agent"
WRITE_TOOLS = ("Edit", "Write")
# decisions.jsonl の subject が上限で切られた目印（src/ccnavi/records/audit.py の _limited）
CUT_MARK = "…(+"
TREND_SHOWN = 10


def _hook_name(ev):
    return ev.get("hook_event") or ev.get("hook_event_name") or "?"


def hook_items(hook_events):
    """hook の応答から、エージェントかユーザに渡る文面を取り出す。
    kind は 拒否（deny・block）・確認（ask）・案内（additionalContext）。
    1 つの応答が理由と案内の両方を持てば 2 件になる。"""
    items = []
    for ev in hook_events:
        if ev.get("subtype") != "hook_response":
            continue
        data = _hook_payload(ev)
        spec = data.get("hookSpecificOutput")
        spec = spec if isinstance(spec, dict) else {}
        decision = spec.get("permissionDecision") or data.get("decision")
        reason = spec.get("permissionDecisionReason") or data.get("reason")
        base = {"event": _hook_name(ev), "label": ev.get("hook_name") or _hook_name(ev)}
        if decision in ("deny", "ask", "block") and reason:
            kind = "確認" if decision == "ask" else "拒否"
            items.append({**base, "kind": kind, "text": str(reason).strip()})
        context = spec.get("additionalContext")
        if context:
            items.append({**base, "kind": "案内", "text": str(context).strip()})
    return items


def _fence(text):
    """text を囲むコードフェンスの行。text の中のバッククォートの列より長くする。"""
    bar = "`" * (max([len(m) for m in re.findall(r"`+", text)] + [2]) + 1)
    return [f"{bar}text", text, bar]


def step_streams(out):
    """出力ディレクトリの step-N.jsonl を parse_stream して {N: 結果} にする（N の昇順）。"""
    streams = {}
    for p in sorted(out.glob("step-*.jsonl"), key=lambda q: int(q.stem.split("-")[1])):
        streams[int(p.stem.split("-")[1])] = parse_stream(p.read_text(encoding="utf-8"))
    return streams


def context_lines(parsed, num, out):
    """step に渡った文脈の節（evidence.md）の行を返し、全文を context-<num>.md に書く。
    親とサブエージェントは分ける。何も無ければ [] を返し、ファイルも書かない。"""
    where = f"context-{num}.md"
    items = hook_items(parsed["hook_events"])
    main = [i for i in items if i["kind"] != "案内" or i["event"] in CONTEXT_EVENTS]
    sub_ctx = [i for i in items if i["kind"] == "案内" and i["event"] == "SubagentStart"]
    calls = parsed["tool_calls"]
    agents = [c for c in calls if c["name"] == AGENT_TOOL and c.get("id")]
    if not (main or agents or sub_ctx):
        return []
    lines = [
        "",
        f"### 渡った文脈（全文は {where}）",
        "SessionStart・UserPromptSubmit・PreToolUse の案内（additionalContext）と、"
        "拒否・確認の理由。先頭だけを出す。",
    ]
    full = [f"# step {num} に渡った文脈（全文）"]

    def add(heading, text, limit):
        n = len(text)
        lines.append(f"- {heading}（{n} 文字）")
        lines.extend(_fence(_clip(text, limit, where)))
        full.extend(["", f"## {heading}（{n} 文字）", *_fence(text)])

    for i in main:
        limit = CONTEXT_LIMIT if i["kind"] == "案内" else EVIDENCE_LIMIT
        add(f"{i['event']} [{i['label']}] {i['kind']}", i["text"], limit)
    if not main:
        lines.append("- なし")
    if agents or sub_ctx:
        lines += ["", "#### サブエージェント（親の呼び出しと分けて出す）"]
        full += ["", "# サブエージェント"]
    for a in agents:
        spec = a["input"] if isinstance(a["input"], dict) else {}
        add(
            f"Agent 呼び出し {a['id']}（subagent_type: {spec.get('subagent_type', '?')}）"
            "に親が渡した prompt",
            str(spec.get("prompt") or ""),
            EVIDENCE_LIMIT,
        )
        kids = [c for c in calls if c.get("parent") == a["id"]]
        lines.append(f"- この Agent 呼び出しの中のツール呼び出し: {len(kids)} 件")
        lines += [
            f"  - {c['name']}: {_clip(json.dumps(c['input'], ensure_ascii=False), 500, where)}"
            for c in kids
        ]
        full += ["", f"## Agent 呼び出し {a['id']} の中のツール呼び出し（{len(kids)} 件）"]
        full += [f"- {c['name']}: {json.dumps(c['input'], ensure_ascii=False)}" for c in kids]
    for i in sub_ctx:
        add(
            f"SubagentStart [{i['label']}] 案内（どの Agent 呼び出しのものかは取れない）",
            i["text"],
            CONTEXT_LIMIT,
        )
    if agents or sub_ctx:
        lines.append(
            "- 取れない: サブエージェントが受け取った SessionStart の文脈・スキル・CLAUDE.md。"
            "stream-json に出るのは上の分だけで、渡っていないものは推測しない"
        )
    (out / where).write_text("\n".join(full) + "\n", encoding="utf-8")
    return lines


def _step_label(row, step_sessions):
    steps = sorted(n for n, sids in step_sessions.items() if row.get("session") in sids)
    return ",".join(str(n) for n in steps) or "?"


def repeated_denials(rows, step_sessions):
    """同じ step で、同じ code の deny が、間に allow を挟まず REPEAT_MIN 回以上続いた箇所。
    間にある deny 以外の行（skip・handover など）は数えず、途切れにもしない。"""
    found, cur = [], {}

    def flush(step):
        run = cur.pop(step, None)
        if run and run["count"] >= REPEAT_MIN:
            found.append(run)

    for r in rows:
        step = _step_label(r, step_sessions)
        decision = r.get("decision")
        if decision == "allow":
            flush(step)
        elif decision == "deny":
            code = r.get("code") or ""
            run = cur.get(step)
            if run and run["code"] == code:
                run["count"] += 1
                run["subjects"].append(r.get("subject") or "")
            else:
                flush(step)
                cur[step] = {
                    "step": step,
                    "code": code,
                    "count": 1,
                    "subjects": [r.get("subject") or ""],
                }
    for step in list(cur):
        flush(step)
    return found


def _write_path(call):
    spec = call["input"] if isinstance(call["input"], dict) else {}
    path = spec.get("file_path")
    return path if call["name"] in WRITE_TOOLS and isinstance(path, str) and path else None


def bypass_suspects(rows, streams, step_sessions):
    """Edit / Write が deny されたあと、同じ step で同じパス（pathcase.fold で比べる）への
    Edit / Write がまだ打たれている箇所。deny の数より呼び出しの数が多いものを疑う。
    Bash の中身からはパスを取らない。切れた subject は比べない。"""
    denied = {}
    for r in rows:
        subject = r.get("subject") or ""
        if (
            r.get("decision") == "deny"
            and r.get("event") == "PreToolUse"
            and r.get("tool") in WRITE_TOOLS
            and subject
            and CUT_MARK not in subject
        ):
            key = (_step_label(r, step_sessions), pathcase.fold(subject))
            denied.setdefault(key, []).append(subject)
    found = []
    for (step, folded), subjects in denied.items():
        paths = []
        for n in (int(x) for x in step.split(",") if x.isdigit()):
            paths += [p for p in map(_write_path, streams.get(n, {}).get("tool_calls", [])) if p]
        later = [p for p in paths if pathcase.fold(p) == folded]
        if len(later) > len(subjects):
            found.append(
                {"step": step, "path": subjects[0], "denied": len(subjects), "calls": len(later)}
            )
    return found


# 次の手を示す語。hook の拒否文は、次の手をマーカーのない行頭の文で書くことが多いので、
# マーカーの行（GUIDE_HEADS）に加えて、依頼・指示の言い回しも見る（#570）。
# 「使う」「確かめ」のように、禁止の説明にも出る語は入れない。
GUIDE_PHRASES = (
    "ください",
    "頼んで",
    "依頼",
    "使って",
    "使います",
    "従って",
    "してから",
    "書き直",
    "打ち直",
    "伝えて",
    "報告して",
)
EMPTY_REASON = "(理由文が空)"
GUIDE_COMMAND = re.compile(r"`[^`\n]+`")  # 打つ形をコード片で示す文（取り込みの案内など）


def has_guide(text):
    """理由文が次の手を示しているか。次のどれかなら True。
    - 行頭（前の空白を除く）が `次:`・`案内:`・`必須:`
    - 依頼・指示の語（GUIDE_PHRASES）がある
    - 打つ形を、バッククォートで囲んだコード片で示している
    語の有無で見るので、誤検知と見逃しがある（限界は acceptance-scenarios.md）。"""
    if any(line.strip().startswith(GUIDE_HEADS) for line in text.splitlines()):
        return True
    return any(p in text for p in GUIDE_PHRASES) or bool(GUIDE_COMMAND.search(text))


def _empty_denials(hook_events):
    """理由文が空の拒否（deny・block）の hook 名。hook_items は文面があるものだけなので別に拾う。"""
    labels = []
    for ev in hook_events:
        if ev.get("subtype") != "hook_response":
            continue
        data = _hook_payload(ev)
        spec = data.get("hookSpecificOutput")
        spec = spec if isinstance(spec, dict) else {}
        decision = spec.get("permissionDecision") or data.get("decision")
        reason = spec.get("permissionDecisionReason") or data.get("reason")
        if decision in ("deny", "block") and not (reason and str(reason).strip()):
            labels.append(ev.get("hook_name") or _hook_name(ev))
    return labels


def unguided_denials(streams):
    """次の手を示していない拒否の理由文（has_guide が偽、または理由文が空）。
    (step 番号, hook 名, 理由文) の列。理由文が空のものは EMPTY_REASON を入れる。"""
    found = []
    for num, parsed in streams.items():
        for i in hook_items(parsed["hook_events"]):
            if i["kind"] == "拒否" and not has_guide(i["text"]):
                found.append((num, i["label"], i["text"]))
        found += [(num, label, EMPTY_REASON) for label in _empty_denials(parsed["hook_events"])]
    return found


def _shown(lines, more):
    if more > 0:
        lines.append(f"- ほか {more} 件")
    return lines


def trend_lines(out, rows):
    """拒否の傾向の節（report.md）。人が読む目印で、判定ではない。"""
    streams = step_streams(out)
    step_sessions = {n: p["session_ids"] for n, p in streams.items()}
    lines = [
        "",
        "## 拒否の傾向（目印。判定ではない）",
        "誤検知と見逃しがある。定義と限界は acceptance-scenarios.md の「証拠の読み方」。",
    ]
    repeats = repeated_denials(rows, step_sessions)
    lines += ["", f"### 反復拒否（同じ code の deny が間に allow なしで {REPEAT_MIN} 回以上）"]
    lines += [
        f"- step {r['step']}: {r['code'] or '(code なし)'} が {r['count']} 回連続。"
        f"対象: {_one_line(', '.join(r['subjects'][:3]), 200)}"
        for r in repeats[:TREND_SHOWN]
    ] or ["- なし"]
    _shown(lines, len(repeats) - TREND_SHOWN)
    bypass = bypass_suspects(rows, streams, step_sessions)
    lines += [
        "",
        "### 迂回の疑い（deny された Edit / Write と同じパスへの、その後の Edit / Write）",
    ]
    lines += [
        f"- step {b['step']}: {b['path']} が {b['denied']} 回拒否されたあと、"
        f"同じパスへの Edit / Write が全 {b['calls']} 回"
        for b in bypass[:TREND_SHOWN]
    ] or ["- なし"]
    _shown(lines, len(bypass) - TREND_SHOWN)
    bare = unguided_denials(streams)
    lines += ["", "### 案内の無い拒否（理由文に次の手を示す語・コード片・`次:` などの行が無い）"]
    lines += [
        f"- step {n} [{label}]: {_one_line(text, 120)}" for n, label, text in bare[:TREND_SHOWN]
    ]
    lines += [] if bare else ["- なし"]
    _shown(lines, len(bare) - TREND_SHOWN)
    return lines


def normalize_message(text):
    """文面を、パスと数字の違いを除いた形にする。置き換えの順は、コミットの ID → パス → 数字
    （数字を先にすると ID が壊れる）。"""
    text = re.sub(r"\b[0-9a-f]{7,40}\b", "<SHA>", text)
    path = r"(?:[A-Za-z0-9_.\-~:@%+=/\\]|<SHA>)*[/\\](?:[A-Za-z0-9_.\-~:@%+=/\\]|<SHA>)*"
    text = re.sub(path, "<PATH>", text)
    return re.sub(r"\d+", "<N>", text)


def _sources(items, rows):
    """hook の応答に出どころ（code・rules）を付ける。応答には無いので、decisions.jsonl の行と
    順に突き合わせる。数が合わなければ付けない。items は (step, item) の列。"""

    def pre(i):
        return i["event"] == "PreToolUse"

    row_sets = {
        "reason": [
            r
            for r in rows
            if r.get("event") == "PreToolUse"
            and r.get("decision") in ("deny", "ask")
            and r.get("enforced") is not False
        ],
        "hint": [r for r in rows if r.get("event") == "PreToolUse" and r.get("hinted")],
    }
    item_sets = {
        "reason": [x for x in items if x[1]["kind"] != "案内" and pre(x[1])],
        "hint": [x for x in items if x[1]["kind"] == "案内" and pre(x[1])],
    }
    found = {}
    for key, its in item_sets.items():
        if len(its) != len(row_sets[key]):
            continue
        for (_, item), row in zip(its, row_sets[key], strict=True):
            rules = row.get("rules") or []
            rules = ",".join(map(str, rules)) if isinstance(rules, list) else str(rules)
            found[id(item)] = f"{row.get('code') or '-'} / {rules or '-'}"
    return found


def collect_messages(streams, rows):
    """hook の応答（拒否・確認・案内）の文面を、正規化した文面で重複を除いて集める。
    案内は SessionStart のものを除く（CCNAVI.md の全文なので、文脈の節で見る）。"""
    items = []
    for num, parsed in streams.items():
        for i in hook_items(parsed["hook_events"]):
            if i["kind"] != "案内" or i["event"] != "SessionStart":
                items.append((num, i))
    sources = _sources(items, rows)
    merged = {}
    for num, i in items:
        key = (i["kind"], normalize_message(i["text"]))
        m = merged.setdefault(
            key,
            {
                "kind": i["kind"],
                "text": i["text"],
                "count": 0,
                "events": [],
                "steps": [],
                "src": [],
            },
        )
        m["count"] += 1
        for field, value in (("events", i["event"]), ("steps", num), ("src", sources.get(id(i)))):
            if value is not None and value not in m[field]:
                m[field].append(value)
    return list(merged.values())


def write_messages(out, rows):
    """messages.md を書く。日本語の不自然さを人が読む材料で、機械の判定はしない。"""
    msgs = collect_messages(step_streams(out), rows)
    lines = [
        "# 文面の一覧（hook の応答）",
        "",
        "拒否・確認・案内の文面。パスと数字だけが違うものは 1 件にまとめた"
        "（コミットの ID は `<SHA>`、パスは `<PATH>`、数字は `<N>` に置き換えて比べる）。"
        "出どころは decisions.jsonl の行と順に突き合わせたもので、数が合わないときは出さない。",
    ]
    for n, m in enumerate(msgs, 1):
        lines += ["", f"## {n}. {m['kind']}（{m['count']} 回）"]
        lines.append(f"- 事象: {', '.join(m['events'])} / step: {', '.join(map(str, m['steps']))}")
        lines.append(f"- 出どころ（code / rules）: {'; '.join(m['src']) or '不明'}")
        lines += _fence(m["text"])
    if not msgs:
        lines += ["", "なし"]
    (out / "messages.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report(scn, out, st, results, rows, raw):
    lines = [
        f"# {scn['id']} {scn['title']}",
        "",
        f"全体: {overall(results)}  費用: {st['cost_usd']:.3f} USD",
        "",
        f"相手（setup.host）: {host_note(scenario_host(scn))}",
        "",
    ]
    lines += _results_table(results, 100)
    lines += ["", "## ステップごとの入力プロンプトと最終出力", *step_answer_lines(scn, out)]
    if scn.get("unconfirmed"):
        lines += ["", "## 確かめる点（unconfirmed）"] + [f"- {u}" for u in scn["unconfirmed"]]
    lines += trend_lines(out, rows)
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "report.json").write_text(
        json.dumps(
            [{"kind": k, "expect": c, "verdict": v, "why": w} for k, c, v, w in results]
            + [
                {
                    "kind": "cost",
                    "expect": "total_cost_usd",
                    "verdict": PASS,
                    "why": f"{st['cost_usd']:.4f} USD",
                    "cost_usd": st["cost_usd"],
                }
            ],
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    ev = [
        "# 証拠（意味の判定用）",
        "",
        "## 機械判定の結果",
        "",
        f"全体: {overall(results)}",
        "",
        *_results_table(results),
        "",
        "judge の基準:",
        *[f"- {c}" for c in scn["expect"].get("judge", [])],
    ]
    steps = scn.get("steps") or []
    step_sessions = {}
    for p in sorted(out.glob("step-*.jsonl")):
        parsed = parse_stream(p.read_text(encoding="utf-8"))
        num = int(p.stem.split("-")[1])
        step_sessions[num] = parsed["session_ids"]
        ev += ["", f"## {p.stem}"]
        if 0 < num <= len(steps) and steps[num - 1].get("prompt"):
            ev += ["### 入力プロンプト", steps[num - 1]["prompt"].strip()]
        ev += ["### ツール呼び出し"]
        ev += [
            f"- {t['name']}: {_clip(json.dumps(t['input'], ensure_ascii=False), 500, p.name)}"
            for t in parsed["tool_calls"]
        ]
        denial_texts = hook_denial_texts(parsed["hook_events"])
        ok_lines, err_lines = [], []
        for r in parsed["tool_results"]:
            text = _text_of(r["content"])
            if not r["is_error"]:
                ok_lines.append(f"- {_clip(text, EVIDENCE_LIMIT, p.name)}")
            elif _duplicates_hook(text, denial_texts):
                err_lines.append("- hook の応答を参照（下の「hook の応答」と同じ文）")
            else:
                err_lines.append(f"- {_clip(text, EVIDENCE_LIMIT, p.name)}")
        ev += ["### ツール結果（成功）", *(ok_lines or ["- なし"])]
        ev += ["### ツール結果（エラー）", *(err_lines or ["- なし"])]
        hook_lines, omitted = summarize_hooks(parsed["hook_events"], p.name)
        ev += ["### hook の応答（拒否・確認・案内文・Stop だけ）", *hook_lines]
        if omitted:
            counts = "、".join(f"{k} {v} 件" for k, v in sorted(omitted.items()))
            ev.append(f"- 省いた応答: {counts}（全量は {p.name}）")
        ev += ["### 最後の報告", _clip(parsed["final_text"], 3000, p.name)]
        ev += context_lines(parsed, num, out)
    ev += [
        "",
        "## ccnavi の判定の記録（このセッションの行）",
        "",
        "列: step=どの step の行か / event=hook の種類 / tool=対象のツール / "
        "decision=判定（allow・deny・ask など）/"
        " code=判定の理由コード / rules=ヒットしたルール / subject=判定の対象（コマンドやパス）。"
        "UNDECLARED はハンドオーバーに宣言が無いことを示す code。",
        "",
        *_step_decisions_table(rows, step_sessions),
    ]
    (out / "evidence.md").write_text("\n".join(ev) + "\n", encoding="utf-8")
    write_messages(out, rows)


def _one_line(text, limit=120):
    s = " ".join(str(text).split())
    return s if len(s) <= limit else s[:limit] + "…"


def _cond_text(cond):
    parts = [f"{k}={cond[k]}" for k in ("event", "tool", "decision", "code") if k in cond]
    if cond.get("rules_include"):
        parts.append("ルール∋" + ", ".join(cond["rules_include"]))
    if cond.get("subject_contains"):
        parts.append(f"対象∋{cond['subject_contains']}")
    return " ".join(parts)


def scenario_summary(scn):
    """回す前に人間へ見せる、シナリオの概要（何をさせ、何を期待し、何を残すか）。"""
    lines = [f"## 受け入れシナリオ {scn['id']}: {scn['title']}"]
    lines.append(
        f"層 {scn.get('layer', '-')}・要求 {', '.join(scn.get('reqs') or []) or '-'}・"
        f"状態 {scn.get('status', '-')}・モデル {scn.get('model', '-')}・"
        f"費用の上限 {scn.get('budget_usd', '-')} USD"
    )
    if scn.get("test_repo"):
        lines.append("作るもの: projects/test-repo にブランチ・コミット・MR が残る（後で片付ける）")
    else:
        lines.append(
            "作るもの: なし。ccnavi 本体に何か残らないことは、回したあと repo の判定で確かめる"
        )
    files = {rel: file_spec(v) for rel, v in ((scn.get("setup") or {}).get("files") or {}).items()}
    made = [rel for rel, (_, append) in files.items() if not append]
    appended = [rel for rel, (_, append) in files.items() if append]
    if made:
        lines.append("回す前に作り、回したあとに消すファイル: " + ", ".join(made))
    if appended:
        lines.append(
            "回す前に追記する、対象にある管理下のファイル（消さない。戻すのは setup.reset）: "
            + ", ".join(appended)
        )
    for argv in (scn.get("setup") or {}).get("git") or []:
        lines.append("回す前に対象で打つ git: " + shlex.join(argv))
    reset = (scn.get("setup") or {}).get("reset")
    if reset:
        lines.append(
            f"回す前と回したあとに、{DISPOSABLE_REL} を {reset['base']} へ戻す"
            "（未コミットの変更・main 以外のブランチ・ワークツリー・タグ・stash が消える）。"
            "人間の操作で止めた回は、戻さずに残す（続きは next、やめるのは restore）"
        )
    host = scenario_host(scn)
    if hostenv.is_fake(host):
        lines.append(
            f"相手（setup.host）: {host}。ホスト（GitHub / GitLab）へは出ない"
            "（`claude -p` 自身の API 通信は要る）。本物のトークンは claude に渡さない。"
            "origin への通信は一時ディレクトリの bare へ向き、回したあとに消す" + FAKE_NOTE
        )
    if host == hostenv.DOCKER:
        lines.append(
            "相手（setup.host）: docker-gitlab。Docker の GitLab（127.0.0.1）に"
            "使い捨てのプロジェクトを作り（名前は sc-<シナリオ ID>-…）、"
            "人間役をメンバーに加えて main を push する。"
            "回したあとにプロジェクトを削除する（人間の操作で止めた回は残す）。"
            "エージェント役のトークンは claude に渡し、人間役のトークンは渡さない"
        )
    issue = (scn.get("setup") or {}).get("issue")
    if issue:
        close = "（回したあとに閉じる）" if issue.get("close") else "（閉じない）"
        lines.append(
            f"回す前に {issue['repo']} へ issue を 1 つ作り、"
            f"prompt の {ISSUE_PLACEHOLDER} に番号を入れる{close}"
        )
    lines.append("手順:")
    for i, step in enumerate(scn["steps"], 1):
        if step.get("human"):
            lines.append(f"  {i}. 人間の操作: {_one_line(step.get('instruction', ''))}")
        else:
            lines.append(f"  {i}. {_one_line(step.get('prompt', ''))}")
    expect = scn.get("expect") or {}
    lines.append("期待:")
    for cond in expect.get("decisions", []):
        lines.append(f"  - 判定が出ている: {_cond_text(cond)}")
    for cond in expect.get("decisions_none", []):
        lines.append(f"  - 出ていてはいけない判定: {_cond_text(cond)}")
    for rel in expect.get("files_unchanged", []):
        lines.append(f"  - 変わっていないこと: {rel}")
    for item in expect.get("state", []):
        text = f"`{item['run']}`"
        if "expect_contains" in item:
            text += f" の出力に「{item['expect_contains']}」が出る"
        if "expect_not_contains" in item:
            text += f" の出力に「{item['expect_not_contains']}」が出ない"
        lines.append(f"  - 最後の状態: {text}")
    for s in expect.get("transcript_forbidden", []):
        lines.append(f"  - 出力に出てはいけない: {s}")
    for criterion in expect.get("judge", []):
        lines.append(f"  - 意味で読む: {criterion}")
    unconfirmed = scn.get("unconfirmed") or []
    if unconfirmed:
        lines.append("まだ実ログと突き合わせていない点:")
        lines += [f"  - {u}" for u in unconfirmed]
    return "\n".join(lines)


def confirmation_text(scn, root, mode_text):
    """回す前に人間へ見せる文。順は、事実 → ワークスペース状態 → ユーザ確認事項。
    run の確認と preflight で同じものを出す。"""
    facts = [scenario_summary(scn), "", mode_text]
    clash = setup_clashes(scn, root)
    if clash:
        facts += [
            "",
            "注意: setup.files のパスが合わない"
            f"（作る形は既にある・追記の形は無い。run は回さない）: {', '.join(clash)}",
        ]
    if uses_target(scn):
        try:
            top = require_disposable_repo(root, Path(root) / DISPOSABLE_REL)
            check_before_run(scn, root, top)
            facts += ["", f"対象のリポジトリ: {top}（使い捨てと確かめた）"]
        except Refusal as exc:
            facts += ["", f"注意: 対象のリポジトリを使えない（run は回さない）\n{exc}"]
    questions = [
        "確認事項 1: ワークツリーに未コミットの変更がなく、origin と同期しているか"
        "（他のセッションが作業中の変更を含まないか）"
    ]
    if scn.get("test_repo"):
        questions.append("確認事項 2: projects/test-repo にブランチや MR ができてよいか")
    if (scn.get("setup") or {}).get("reset"):
        questions.append(
            f"確認事項 3: {DISPOSABLE_REL} の未コミットの変更・main 以外のブランチ・"
            "ワークツリー・タグ・stash を、回す前と回したあとに消してよいか（setup.reset）"
        )
    return "\n".join(
        [
            "回す前に、人間に次を確認する。確認が取れたら --confirmed を付けて打ち直す。",
            "",
            "# 1. 事実",
            "",
            *facts,
            "",
            "# 2. ワークスペース状態",
            "",
            preflight_facts(root),
            "",
            "# 3. ユーザ確認事項",
            "",
            *questions,
        ]
    )


def cmd_preflight(args):
    """回さずに、確認に出す文だけを出す。終了コードは run の確認と同じ 3。"""
    root = Path(args.root).resolve()
    scn = load_scenario(args.scenario)
    _, mode_text = mode_check(root)
    print(confirmation_text(scn, root, mode_text))
    return 3


JUDGE_HEADING = "## 意味の判定（別のエージェント）"


def parse_judgment(text):
    """判定の表（| 基準 | 判定 | 理由 |）を読む。[(基準, 判定, 理由)]。判定は pass か fail だけ。"""
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 3 or cells[0] == "基準" or set(cells[0]) <= set("-: "):
            continue
        verdict = cells[1].lower()
        if verdict not in (PASS, FAIL):
            raise ValueError(f"判定は pass か fail にする: {cells[0]} → {cells[1]}")
        rows.append((cells[0], verdict, cells[2]))
    return rows


def final_verdict(mechanical, judged, accepted):
    """機械判定と意味の判定と、ユーザの受け入れから、最終の判定を決める。"""
    if not accepted:
        return "受け入れ待ち（ユーザが判定を確かめるまで、全体の判定は確定しない）"
    judge_verdicts = [v for _, v, _ in judged]
    if overall(mechanical) != PASS:
        return overall(mechanical)
    return FAIL if FAIL in judge_verdicts else PASS


def mechanical_results(out):
    """報告の機械判定（judge 以外）を、final_verdict に渡す形で読む。"""
    recorded = json.loads((out / "report.json").read_text(encoding="utf-8"))
    return [
        (r["kind"], r["expect"], r["verdict"], r["why"]) for r in recorded if r["kind"] != "judge"
    ]


def record_acceptance(out, report, accept):
    """報告に足してある意味の判定に、ユーザの受け入れを記す。判定の表は変えない。"""
    if not accept:
        print("この回の意味の判定は、もう報告に足してある（受け入れを記すなら --accept を付ける）")
        return 1
    text = report.read_text(encoding="utf-8")
    head, section = text.split(JUDGE_HEADING, 1)
    if "ユーザの受け入れ: あり" in section:
        print("受け入れは、もう記録してある（二重には記さない）")
        return 1
    judged = parse_judgment(section)
    verdict = final_verdict(mechanical_results(out), judged, True)
    section = "\n".join(
        f"全体（機械判定と意味の判定の合わせ）: {verdict}"
        if line.startswith("全体（機械判定と意味の判定の合わせ）:")
        else line.replace("ユーザの受け入れ: なし", "ユーザの受け入れ: あり")
        for line in section.split("\n")
    )
    report.write_text(head + JUDGE_HEADING + section, encoding="utf-8")
    print(f"ユーザの受け入れを記した。全体: {verdict}")
    return 0


def cmd_judge(args):
    """意味の判定（別のエージェントが書いた判定の表）を報告に足す。
    足してあれば、--accept でユーザの受け入れを記す。"""
    out = Path(args.out)
    report = out / "report.md"
    if JUDGE_HEADING in report.read_text(encoding="utf-8"):
        return record_acceptance(out, report, args.accept)
    if not args.file:
        print("意味の判定がまだ無い。判定の表のファイルを渡す")
        return 1
    judged = parse_judgment(Path(args.file).read_text(encoding="utf-8"))
    st = json.loads((out / "state.json").read_text(encoding="utf-8"))
    scn = load_scenario(st["scenario"])
    expected = len(scn["expect"].get("judge", []))
    if not judged or len(judged) != expected:
        print(f"判定の表の行数が違う: 期待 {expected} 行、読めた {len(judged)} 行")
        return 1
    lines = ["", JUDGE_HEADING, "", "| 基準 | 判定 | 理由 |", "|---|---|---|"]
    lines += [f"| {c} | {v} | {w} |" for c, v, w in judged]
    lines += ["", f"ユーザの受け入れ: {'あり' if args.accept else 'なし'}"]
    verdict = final_verdict(mechanical_results(out), judged, args.accept)
    lines += [f"全体（機械判定と意味の判定の合わせ）: {verdict}"]
    text = report.read_text(encoding="utf-8")
    report.write_text(text + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"意味の判定を報告に足した: {report}")
    print(f"全体: {verdict}")
    return 0


def cmd_run(args):
    _require_entry()
    root = Path(args.root).resolve()
    scn = load_scenario(args.scenario)
    scn["_prompt_steps"] = sum(1 for s in scn["steps"] if not s.get("human"))
    mode_ok, mode_text = mode_check(root)
    if not mode_ok:
        # 確認の前に止める。--confirmed を付けても、enable でなければ回さない
        print(mode_text)
        return 3
    clash = setup_clashes(scn, root)
    if clash:
        # 有るものを後片付けで消さないよう、追記の対象が無いまま書き始めないよう、回す前に止める
        print(
            "setup.files のパスが合わない（作る形は既にある・追記の形は無い）: "
            + ", ".join(clash)
            + "\n案内: 作る形のパスは、回したあとに消すので、既にあると回さない"
            + "\n次: パスを直すか、既にあるファイルを退けて打ち直す"
        )
        return 1
    top = None
    if uses_target(scn):
        try:
            top = require_disposable_repo(root, root / DISPOSABLE_REL)
            check_before_run(scn, root, top)
        except Refusal as exc:
            return _refuse(exc)
    if not args.confirmed:
        print(confirmation_text(scn, root, mode_text))
        return 3
    out = Path(
        args.out or ROOT / "logs" / "scenarios" / f"{scn['id']}-{datetime.now():%Y%m%d-%H%M%S}"
    ).resolve()  # cwd が変わっても next / restore が同じ回と分かるように
    return start_scenario(scn, args.scenario, root, top, out)


def start_scenario(scn, scenario_path, root, top, out):
    """確認と回す前の検査が済んだシナリオを回す（setup → step → 判定 → 後片付け）。終了コード。
    run と run-all が使う。top は使い捨てのリポジトリの実体（対象を触らないシナリオは None）。"""
    out.mkdir(parents=True, exist_ok=True)
    pre = {rel: file_digest(root / rel) for rel in scn["expect"].get("files_unchanged", [])}
    setup = scn.get("setup") or {}
    base = (setup.get("reset") or {}).get("base")
    st = {
        "scenario": str(Path(scenario_path).resolve()),
        "root": str(root),
        "out": str(out),
        "next_step": 0,
        "session_ids": [],
        "cost_usd": 0.0,
        "pre_digests": pre,
        "pre_repo": repo_snapshot(root),
        "setup_made": [],
        "setup_dirs": [],
        "issue_no": None,
        "target": None,
    }
    _write_state(out, st)
    try:
        if top:
            lock = acquire_target(top, out)
            st["target"] = {"path": top, "base": base, "lock": lock}
            _write_state(out, st)
            if base:
                reset_repo(root, top, base)
            _start_host(scn, st, out, root, top)
        # issue は回すたびに新しく作る。番号は state に残し、続きの step の prompt にも入れる
        if setup.get("issue"):
            st["issue_no"] = make_issue(setup["issue"], root)
        st["setup_made"], st["setup_dirs"] = _setup(scn, root, top)
        if setup.get("git"):
            run_setup_git(root, top, setup["git"], host=st.get("host"))
    except Refusal as exc:
        _abort(scn, out, st, root)
        return _refuse(exc)
    except BaseException:
        _abort(scn, out, st, root)
        raise
    _write_state(out, st)
    return _continue(scn, out, st, root)


def _refuse(exc):
    """回す前の拒否・停止の文面（理由・案内:・次:）を出して、終了コード 3 を返す。"""
    print(exc)
    return 3


def _abort(scn, out, st, root):
    """回し始める前に止めたとき、作ったものを消し、対象を戻して放す（戻せなければ文面が出る）。"""
    st["setup_made"] = st.get("setup_made") or []
    _cleanup(st["setup_made"], st.get("setup_dirs", []), root)
    issue = (scn.get("setup") or {}).get("issue")
    if issue and issue.get("close") and st.get("issue_no"):
        close_issue(issue, st["issue_no"], root)
    _teardown_host(st, root)
    release_target(st, root)
    _write_state(out, st)


def _continue(scn, out, st, root):
    try:
        rc = drive(scn, out, st, Path(root))
    except BaseException:
        # 割り込みなど、drive を抜けて finish に届かないときも、setup のファイルを残さず、
        # 対象を base へ戻す（戻せなければ文面を出す。元の例外はそのまま投げ直す）
        _cleanup(st["setup_made"], st.get("setup_dirs", []), root)
        st["setup_made"] = []
        _teardown_host(st, root)
        release_target(st, root)
        _write_state(out, st)
        raise
    if rc == 10 and st.get("host"):
        # 模擬は止める。置き場（bare・代役・模擬の状態）は、next が同じ環境で続けるので残す
        hostenv.hold(root, st["host"])
        _write_state(out, st)
        if st["host"].get("project_id"):
            print(
                f"案内: docker-gitlab のプロジェクトを残している: {st['host']['url']}"
                f"（ID {st['host']['project_id']}）。next が最後まで終わるか restore で消える"
            )
    if rc == 10 and st.get("target"):
        # 人間が操作したあとの next で続くので、対象は base に戻さず残す（ロックだけ放す）
        release_target(st, root, hold=True)
        _write_state(out, st)
        tgt = st["target"]
        if tgt.get("base"):
            print(
                f"案内: {tgt['path']} を {tgt['base']} に戻していない"
                "（setup のファイルも残している）。戻すのは next が最後まで終わったとき"
            )
            print(f"次: 続けるなら `{RUNNER} next {out}`、やめるなら `{RUNNER} restore {out}`")
    return finish(scn, out, st, Path(root)) if rc is None or rc == 11 else rc


def cmd_next(args):
    out = Path(args.out)
    st = json.loads((out / "state.json").read_text(encoding="utf-8"))
    if st.get("released"):
        print("対象を戻して放した回（restore 済みか終了済み）。続きは回せない")
        print(f"次: やり直すなら `{RUNNER} run <シナリオ> --confirmed`")
        return 2
    if st.get("halt"):
        # halt は finish で戻してある。届かなかった場合に備え、戻し直す（戻し済みなら何もしない）
        print(f"step {st['halt']['step']} で止めた回。続きは回せない")
        undeleted = _teardown_host(st, st["root"])
        failed = release_target(st, st["root"])
        _write_state(out, st)
        return 3 if failed or undeleted else 2
    # 人間の操作の間にモードが変わることがあるので、続けるたびに確かめる
    mode_ok, mode_text = mode_check(Path(st["root"]))
    if not mode_ok:
        print(mode_text)
        return 3
    if st.get("target") and st.get("held"):
        try:
            st["target"]["lock"] = acquire_target(st["target"]["path"], out, resuming=True)
        except Refusal as exc:
            return _refuse(exc)
        st["held"] = False
    if st.get("host") and not st["host"].get("down"):
        for note in hostenv.resume(st["root"], st["host"]):
            print(f"案内: {note}")
        _write_state(out, st)
    scn = load_scenario(st["scenario"])
    scn["_prompt_steps"] = sum(1 for s in scn["steps"] if not s.get("human"))
    return _continue(scn, out, st, st["root"])


def cmd_restore(args):
    """人間の操作で止めた回を続けずにやめる。setup のファイルを消し、対象を base へ戻して放す。
    戻せなかった回の戻し直しにも使う。"""
    out = Path(args.out)
    st = json.loads((out / "state.json").read_text(encoding="utf-8"))
    scn = load_scenario(st["scenario"])
    tgt = st.get("target")
    if tgt and not st.get("released"):
        try:
            tgt["lock"] = acquire_target(tgt["path"], out, resuming=True)
        except Refusal as exc:
            return _refuse(exc)
    _cleanup(st["setup_made"], st.get("setup_dirs", []), st["root"])
    st["setup_made"] = []
    issue = (scn.get("setup") or {}).get("issue")
    if issue and issue.get("close") and st.get("issue_no"):
        close_issue(issue, st["issue_no"], st["root"])
    undeleted = _teardown_host(st, st["root"])
    failed = release_target(st, st["root"])
    _write_state(out, st)
    if failed or undeleted:
        return 3
    print(f"{out} の回を片付けた（setup のファイルを消し、対象を戻して放した）")
    return 0


# ---- run-all（使い捨てのリポジトリのシナリオを、1 回の確認で直列に回す） ----


def expand_scenario_paths(patterns):
    """run-all の引数（ディレクトリ・glob・ファイル）をシナリオのパスの列にする。重複は除く。"""
    found = []
    for pattern in patterns:
        p = Path(pattern)
        if p.is_dir():
            found += sorted(p.glob("*.yml"))
        elif p.is_file():
            found.append(p)
        else:
            found += [Path(m) for m in sorted(glob.glob(pattern))]
    seen, paths = set(), []
    for p in found:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            paths.append(p)
    return paths


def run_all_reason(scn, root, top, top_why):
    """run-all の対象にしない理由。対象にできるなら None。#546 の判定（使い捨てのリポジトリ）に、
    続けて回すのに必要な条件（人間の操作が無い・前の回の状態を残さない）を足す。"""
    setup = scn.get("setup") or {}
    if any(step.get("human") for step in scn["steps"]):
        return "人間の操作の step がある（続けて回せない。1 本ずつ確認して回す）"
    if not uses_target(scn):
        return (
            f"使い捨てのリポジトリ（{DISPOSABLE_REL}）に作用する setup が無い"
            "（ccnavi 本体に作用しうる。1 本ずつ確認して回す）"
        )
    if not (setup.get("reset") or {}).get("base"):
        return "setup.reset が無い（前の回の状態が次の回に残る）"
    clash = setup_clashes(scn, root)
    if clash:
        return "setup.files のパスが合わない（作る形は既にある・追記の形は無い）: " + ", ".join(
            clash
        )
    if top is None:
        return f"対象のリポジトリを使えない: {top_why}"
    try:
        check_before_run(scn, root, top)
    except Refusal as exc:
        return f"回す前の確認で断られる: {exc.what}"
    return None


def run_all_plan(paths, root):
    """(対象, 対象にしない) を返す。対象は [(パス, シナリオ)] を ID 順に、対象にしないものは
    [(パスか ID, 理由)] を、読んだ順に並べる。"""
    root = Path(root)
    top, top_why = None, ""
    try:
        top = require_disposable_repo(root, root / DISPOSABLE_REL)
    except Refusal as exc:
        top_why = exc.what
    eligible, skipped, ids = [], [], {}
    for path in paths:
        try:
            scn = load_scenario(path)
        except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
            skipped.append((str(path), f"読み込めない: {exc}"))
            continue
        sid = str(scn["id"])
        if sid in ids:
            skipped.append((sid, f"ID が重複している（{ids[sid]} と同じ）"))
            continue
        ids[sid] = str(path)
        scn["_prompt_steps"] = sum(1 for s in scn["steps"] if not s.get("human"))
        why = run_all_reason(scn, root, top, top_why)
        if why:
            skipped.append((sid, why))
        else:
            eligible.append((path, scn))
    eligible.sort(key=lambda e: str(e[1]["id"]))
    return eligible, skipped


def run_all_digest(eligible, cap):
    """確認した内容の目印。ID の集合・base・シナリオごとの費用の上限・累計の上限・
    人が読んだ概要から作る。どれか一つでも変われば目印も変わる。
    実行の直前に作り直して、確認したときの値と比べる。"""
    body = {
        "max_total_usd": cap,
        "scenarios": [
            {
                "id": str(scn["id"]),
                "base": scn["setup"]["reset"]["base"],
                "budget_usd": scn["budget_usd"],
                "summary": scenario_summary(scn),
                # 概要に出ない欄（prompt の全文・permission_mode・model・expect・setup の中身）も
                # 含める。書き換えがあれば目印が変わり、確認を取り直す（安全側）
                "scenario": {k: v for k, v in scn.items() if k != "_prompt_steps"},
            }
            for _, scn in eligible
        ],
    }
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def skipped_lines(skipped):
    return [f"- {who}: {why}" for who, why in skipped] or ["- なし"]


def run_all_confirmation(eligible, skipped, cap, digest, mode_text, root, scenario_args):
    """run-all の確認文。全シナリオに対する 1 回の確認で、回す ID の一覧・概要・作るもの・
    対象の状態・base・費用の上限を見せる。"""
    budget = sum(float(scn["budget_usd"]) for _, scn in eligible)
    table = ["| ID | 題 | base | モデル | 1 本の上限 (USD) |", "|---|---|---|---|---|"]
    table += [
        f"| {scn['id']} | {_cell(scn['title'])} | {scn['setup']['reset']['base'][:12]} | "
        f"{scn['model']} | {scn['budget_usd']} |"
        for _, scn in eligible
    ]
    questions = [
        "確認事項 1: ワークツリーに未コミットの変更がなく、origin と同期しているか"
        "（他のセッションが作業中の変更を含まないか）",
        f"確認事項 2: {DISPOSABLE_REL} の未コミットの変更・main 以外のブランチ・ワークツリー・"
        "タグ・stash を、各シナリオの前と後に消してよいか（setup.reset。base は上の表）",
    ]
    if any(scn.get("test_repo") or (scn.get("setup") or {}).get("issue") for _, scn in eligible):
        questions.append(
            f"確認事項 3: {DISPOSABLE_REL} にブランチや MR ができてよいか"
            "（setup.issue のシナリオは、本物のホストに issue も作る）"
        )
    questions.append(
        f"確認事項 4: 実費の累計 {cap} USD までを使ってよいか"
        f"（1 本の上限の合計は {budget:.2f} USD。"
        "累計の残りを 1 本の上限が超えるシナリオは回さない）"
    )
    command = (
        f"{RUNNER} run-all {' '.join(shlex.quote(a) for a in scenario_args)} "
        f"--max-total-usd {cap} --confirmed {digest}"
    )
    lines = [
        "回す前に、人間に次を 1 回だけ確認する（シナリオごとには取り直さない）。"
        "確認が取れたら、最後の打ち方で回す。",
        "",
        "この確認が有効なのは、下の ID の集合・base・費用の上限・概要に限る。"
        "実行の直前に作り直した目印が違えば、回さずに止まる（増減・書き換えがあれば取り直す）。",
        "",
        f"# 1. 回す一覧（{len(eligible)} 本。直列。ID 順）",
        "",
        *table,
        "",
        "# 2. 概要と作るもの",
        "",
        *[scenario_summary(scn) + "\n" for _, scn in eligible],
        f"# 3. 対象にしないもの（{len(skipped)} 件。回さない）",
        "",
        *skipped_lines(skipped),
        "",
        "# 4. 事実",
        "",
        mode_text,
        "",
        f"対象のリポジトリ: {Path(root) / DISPOSABLE_REL}（使い捨てと確かめた）",
        "",
        preflight_facts(root),
        "# 5. ユーザ確認事項",
        "",
        *questions,
        "",
        "# 6. 回す打ち方（確認の目印つき）",
        "",
        command,
    ]
    return "\n".join(lines)


def scenario_row(scn, out, root, rc):
    """run-all の表の 1 行（ID・判定・実費・ツール呼び出し数・拒否数・ask 数・出力の置き場）。"""
    row = {
        "id": str(scn["id"]),
        "rc": rc,
        "verdict": "not-run",
        "cost_usd": 0.0,
        "tool_calls": 0,
        "denies": 0,
        "asks": 0,
        "out": str(out),
    }
    try:
        st = json.loads((out / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return row
    row["cost_usd"] = float(st.get("cost_usd") or 0.0)
    row["tool_calls"] = sum(len(p["tool_calls"]) for p in step_streams(out).values())
    decided = read_decisions(Path(root) / "logs/decisions.jsonl", st.get("session_ids") or [])
    row["denies"] = sum(1 for r in decided if r.get("decision") == "deny")
    row["asks"] = sum(1 for r in decided if r.get("decision") == "ask")
    try:
        recorded = json.loads((out / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return row
    row["verdict"] = overall([(r["kind"], r["expect"], r["verdict"], r["why"]) for r in recorded])
    return row


def run_all_table(rows):
    lines = [
        "| ID | 判定 | 実費 (USD) | ツール呼び出し | 拒否 | ask | 出力 |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {r['id']} | {r['verdict']} | {r['cost_usd']:.3f} | {r['tool_calls']} | "
        f"{r['denies']} | {r['asks']} | {r['out']} |"
        for r in rows
    ]
    return lines


def write_run_all_summary(batch, rows, skipped, cap, remaining_ids, stopped):
    total = sum(r["cost_usd"] for r in rows)
    lines = [
        "# run-all の結果",
        "",
        f"回した本数: {len(rows)}  実費の累計: {total:.3f} USD（上限 {cap} USD）",
        "",
        *run_all_table(rows),
    ]
    if stopped:
        lines += [
            "",
            f"## 止まった: {stopped}",
            f"回した本数 {len(rows)}。残りの ID: {', '.join(remaining_ids) or 'なし'}",
        ]
    lines += ["", "## 対象にしなかったもの", *skipped_lines(skipped)]
    (batch / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (batch / "summary.json").write_text(
        json.dumps(
            {
                "max_total_usd": cap,
                "total_cost_usd": total,
                "stopped": stopped,
                "remaining": remaining_ids,
                "scenarios": rows,
                "skipped": [{"who": w, "why": y} for w, y in skipped],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return lines


def _run_all_loop(eligible, root, cap, batch, rows):
    """順に回す。止まった理由（なければ空）を返す。rows に、回した順に行を足す。"""
    spent = 0.0
    for path, scn in eligible:
        sid = str(scn["id"])
        remaining = cap - spent
        if float(scn["budget_usd"]) > remaining + 1e-9:
            return (
                f"{sid} の費用の上限 {scn['budget_usd']} USD が、累計の残り "
                f"{max(remaining, 0):.3f} USD（上限 {cap} USD - 実費 {spent:.3f} USD）を超える"
            )
        mode_ok, mode_text = mode_check(root)
        if not mode_ok:
            return f"{sid} の前に、判定のモードが enable でなくなった\n{mode_text}"
        out = batch / sid
        try:
            top = require_disposable_repo(root, root / DISPOSABLE_REL)
            check_before_run(scn, root, top)
        except Refusal as exc:
            return f"{sid} を回す前に断られた\n{exc}"
        rc = start_scenario(scn, path, root, top, out)
        row = scenario_row(scn, out, root, rc)
        rows.append(row)
        spent += row["cost_usd"]
        print(f"-- {sid}: {row['verdict']}（実費 {row['cost_usd']:.3f} USD、累計 {spent:.3f} USD）")
        if rc in (3, 10):
            return f"{sid} が終了コード {rc} で止まった（対象の状態を手で確かめる）"
    return ""


def cmd_run_all(args):
    _require_entry()
    root = Path(args.root).resolve()
    cap = round(float(args.max_total_usd), 4)
    if not math.isfinite(cap) or cap <= 0:
        print("--max-total-usd は 0 より大きい有限の値にする")
        print("次: 実費の累計の上限を USD で渡して打ち直す")
        return 2
    mode_ok, mode_text = mode_check(root)
    if not mode_ok:
        print(mode_text)
        return 3
    eligible, skipped = run_all_plan(expand_scenario_paths(args.scenarios), root)
    if not eligible:
        print("run-all で回せるシナリオが無い\n対象にしないもの:")
        print("\n".join(skipped_lines(skipped)))
        print("次: 対象にしなかった理由を直すか、1 本ずつ `run` で確認して回す")
        return 1
    digest = run_all_digest(eligible, cap)
    if args.confirmed is None:
        print(run_all_confirmation(eligible, skipped, cap, digest, mode_text, root, args.scenarios))
        return 3
    if args.confirmed != digest:
        print(
            "確認した内容と今回の対象が違う（ID の集合・base・費用の上限・概要のどれかが"
            "増えたか減ったか、書き換わった）。回さない"
        )
        print(f"今の対象: {', '.join(str(s['id']) for _, s in eligible)}（目印 {digest}）")
        print(f"案内: 確認した目印は {args.confirmed}")
        print("次: --confirmed を付けずに打ち直して確認を取り直す")
        return 3
    batch = Path(
        args.out or ROOT / "logs" / "scenarios" / f"run-all-{datetime.now():%Y%m%d-%H%M%S}"
    ).resolve()
    batch.mkdir(parents=True, exist_ok=True)
    rows, stopped = [], ""
    try:
        stopped = _run_all_loop(eligible, root, cap, batch, rows)
    finally:
        done = {r["id"] for r in rows}
        remaining = [str(s["id"]) for _, s in eligible if str(s["id"]) not in done]
        lines = write_run_all_summary(batch, rows, skipped, cap, remaining, stopped or "")
        print("\n".join(["", *lines]))
        print(f"\n要約: {batch}/summary.md")
    if stopped:
        print(f"次: 残りを回すなら、{RUNNER} run-all を --confirmed なしで打って確認を取り直す")
        return 3
    verdicts = {r["verdict"] for r in rows}
    if FAIL in verdicts:
        return 1
    return 0 if verdicts <= {PASS} else 2


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("scenario")
    r.add_argument("--confirmed", action="store_true")
    r.add_argument("--root", default=str(ROOT))
    r.add_argument("--out")
    r.set_defaults(fn=cmd_run)
    ra = sub.add_parser("run-all")
    ra.add_argument("scenarios", nargs="+", help="ディレクトリ・glob・シナリオのファイル")
    ra.add_argument("--max-total-usd", type=float, required=True, help="実費の累計の上限")
    ra.add_argument("--confirmed", help="確認文が出した目印（ID の集合・base・費用の上限・概要）")
    ra.add_argument("--root", default=str(ROOT))
    ra.add_argument("--out", help="要約（summary.md / summary.json）の置き場")
    ra.set_defaults(fn=cmd_run_all)
    p = sub.add_parser("preflight")
    p.add_argument("scenario")
    p.add_argument("--root", default=str(ROOT))
    p.set_defaults(fn=cmd_preflight)
    j = sub.add_parser("judge")
    j.add_argument("out")
    j.add_argument(
        "file",
        nargs="?",
        help="判定の表（| 基準 | 判定 | 理由 |）のファイル。足し済みの回の受け入れには要らない",
    )
    j.add_argument("--accept", action="store_true", help="ユーザが判定を受け入れた")
    j.set_defaults(fn=cmd_judge)
    n = sub.add_parser("next")
    n.add_argument("out")
    n.set_defaults(fn=cmd_next)
    rs = sub.add_parser("restore")
    rs.add_argument("out")
    rs.set_defaults(fn=cmd_restore)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    _ENTRY = True
    sys.exit(main())
