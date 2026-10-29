"""受入シナリオの相手側（GitLab）の状態を、step の終わりに読んで記録する。
決まりは .agents/docs/acceptance-scenarios.md の「相手側の状態の記録」。

- 対象は `setup.host` が `fake-gitlab` / `docker-gitlab` のとき。
  MR（一覧と 1 件）・スレッドと解決・issue
- 読むだけ。呼ぶのは GET の API
  （list_mrs・get_mr・list_discussions・list_approvals・list_issues）だけ
- 承認は MR ごとに読み、MR の `approvals` に残す。取れたら
  `{"taken": true, "approved", "approved_by"}`、取れなかったら `{"taken": false}`
  （承認が無い＝`taken` が true で `approved` が false、と区別できる）
- 取れなかったものは、その場所の `errors` に残す。例外は呼び出し側へ出さない（シナリオを止めない）
- トークンは記録に出さない。取る項目は選んだものだけにし、知っているトークンの値は伏せる

実行器（runner.py）が `take` を呼び、出力の置き場に `host-<step>.json` を書く。
"""

import json
from datetime import datetime

from ccnavi.records import diaglog
from tests.scenarios import hostenv

LOG_NAME = hostenv.LOG_NAME
TEXT_LIMIT = 1000
# 一覧は 1 ページ（100 件）だけ読む。100 件に達したら、切れた可能性を errors に残す。
PAGE_LIMIT = 100
MASK = "***"
MR_KEYS = (
    "iid",
    "title",
    "state",
    "draft",
    "source_branch",
    "target_branch",
    "merged_at",
    "closed_at",
    "merge_status",
    "detailed_merge_status",
    "has_conflicts",
    "blocking_discussions_resolved",
    "web_url",
    "created_at",
    "updated_at",
    "sha",
    "labels",
)
ISSUE_KEYS = ("iid", "title", "state", "web_url", "created_at", "closed_at", "labels")
NOTE_KEYS = ("id", "created_at", "system", "resolvable", "resolved", "type")


def file_name(step):
    return f"host-{step}.json"


def _clip(value):
    text = value if isinstance(value, str) else ""
    return text if len(text) <= TEXT_LIMIT else text[:TEXT_LIMIT] + f"…(+{len(text) - TEXT_LIMIT})"


def _user(value):
    """ユーザの辞書から username だけを取る（ほかの項目は取らない）。"""
    return value.get("username") if isinstance(value, dict) else None


def _pick(src, keys):
    return {k: src[k] for k in keys if k in src}


def _note(note):
    out = _pick(note, NOTE_KEYS)
    out["author"] = _user(note.get("author"))
    if note.get("resolved_by"):
        out["resolved_by"] = _user(note["resolved_by"])
    position = note.get("position")
    if isinstance(position, dict) and (position.get("new_path") or position.get("old_path")):
        out["path"] = position.get("new_path") or position.get("old_path")
        out["line"] = position.get("new_line") or position.get("old_line")
    out["body"] = _clip(note.get("body"))
    return out


def _thread(discussion):
    notes = [n for n in discussion.get("notes", []) if isinstance(n, dict)]
    resolvable = [n for n in notes if n.get("resolvable")]
    return {
        "id": discussion.get("id"),
        "individual_note": bool(discussion.get("individual_note")),
        # 本物の GitLab は、承認・解決などのシステムのメモもスレッドとして返す（模擬は返さない）
        "system": bool(notes) and all(n.get("system") for n in notes),
        # 解決できるメモがあり、すべて解決済みなら true。解決できないスレッドは null
        "resolved": all(n.get("resolved") for n in resolvable) if resolvable else None,
        "notes": [_note(n) for n in notes],
    }


def _approvals(body):
    """承認の応答から、残す 3 つ（approved・approved_by[].user.username・
    approved_by[].approved_at）だけを取る。
    user_can_approve・user_has_approved は、読んだトークンから見た値で意味が曖昧なので残さない。"""
    given = body.get("approved_by")
    given = given if isinstance(given, list) else []
    return {
        "taken": True,
        "approved": body.get("approved") is True,
        "approved_by": [
            {"username": _user(a.get("user")), "approved_at": a.get("approved_at")}
            for a in given
            if isinstance(a, dict)
        ],
    }


def _mr(mr):
    out = _pick(mr, MR_KEYS)
    # 古い GitLab は draft の代わりに work_in_progress を返す
    if "draft" not in out and "work_in_progress" in mr:
        out["draft"] = mr["work_in_progress"]
    out["author"] = _user(mr.get("author"))
    if mr.get("merged_by"):
        out["merged_by"] = _user(mr["merged_by"])
    out["description"] = _clip(mr.get("description"))
    return out


def _issue(issue):
    out = _pick(issue, ISSUE_KEYS)
    out["author"] = _user(issue.get("author"))
    out["description"] = _clip(issue.get("description"))
    return out


def _summary(mrs, issues):
    every = [t for mr in mrs for t in mr.get("threads", [])]
    threads = [t for t in every if not t["system"]]
    return {
        "mrs": len(mrs),
        "mrs_opened": sum(1 for m in mrs if m.get("state") == "opened"),
        "mrs_merged": sum(1 for m in mrs if m.get("state") == "merged"),
        "mrs_draft": sum(1 for m in mrs if m.get("draft")),
        "mrs_approved": sum(1 for m in mrs if m.get("approvals", {}).get("approved")),
        "threads": len(threads),
        "threads_unresolved": sum(1 for t in threads if t["resolved"] is False),
        "threads_system": len(every) - len(threads),
        "issues": len(issues),
        "issues_opened": sum(1 for i in issues if i.get("state") == "opened"),
    }


def collect(client, token, project):
    """MR（一覧と 1 件ずつ）・スレッド・issue を読んで、記録の辞書を返す。読むだけ。
    一覧が取れなければ、その欄は空にして errors に残す。例外は呼び出し側へ出す（take が受ける）。"""
    errors = []
    calls = []

    def read(label, status_body):
        status, body = status_body
        calls.append({"api": label, "status": status})
        if not 200 <= status < 300:
            errors.append({"api": label, "status": status, "error": f"HTTP {status}"})
            return None
        return body

    def capped(label, items):
        if len(items) >= PAGE_LIMIT:
            errors.append({"api": label, "error": f"{PAGE_LIMIT} 件で切った（ページを送らない）"})

    mrs = []
    listed = read("list_mrs", client.list_mrs(token, project, state="all"))
    if calls[-1]["status"] == 0:
        # 相手に接続できない。残りを読んでも同じ待ちを重ねるだけなので、ここでやめる
        errors.append({"api": "snapshot", "error": "相手に接続できないので、残りは読まない"})
        return {
            "summary": _summary([], []),
            "mrs": [],
            "issues": [],
            "calls": calls,
            "errors": errors,
        }
    if listed is not None and not isinstance(listed, list):
        errors.append({"api": "list_mrs", "error": "応答が配列ではない"})
        listed = None
    capped("list_mrs", listed or [])
    for item in listed or []:
        iid = item.get("iid")
        detail = read(f"get_mr {iid}", client.get_mr(token, project, iid))
        if not isinstance(detail, dict):
            detail = item  # 1 件の詳細が取れなければ、一覧の分は残す（errors にも残る）
        view = _mr(detail)
        found = read(f"list_discussions {iid}", client.list_discussions(token, project, iid))
        if isinstance(found, list):
            capped(f"list_discussions {iid}", found)
            view["threads"] = [_thread(d) for d in found if isinstance(d, dict)]
        else:
            view["threads"] = []
            view["threads_unavailable"] = True
        got = read(f"list_approvals {iid}", client.list_approvals(token, project, iid))
        view["approvals"] = _approvals(got) if isinstance(got, dict) else {"taken": False}
        mrs.append(view)
    issues = []
    body = read("list_issues", client.list_issues(token, project, state="all", per_page=PAGE_LIMIT))
    if isinstance(body, list):
        capped("list_issues", body)
        issues = [_issue(i) for i in body if isinstance(i, dict)]
    return {
        "summary": _summary(mrs, issues),
        "mrs": mrs,
        "issues": issues,
        "calls": calls,
        "errors": errors,
    }


def _mask(text, secrets):
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            for form in (secret, json.dumps(secret)[1:-1]):
                text = text.replace(form, MASK)
    return text


def take(root, host, step, out, now=None):
    """相手側の状態を読んで `out/host-<step>.json` に書く。結果の要約の辞書を返す。
    要約: {"step", "file", "ok", "errors"}。読めなかった・書けなかったときも例外にせず、
    読めなかった理由を記録の errors に残す（書けなかったときは、要約の errors だけに残す）。"""
    log = diaglog.get(LOG_NAME, str(root))
    name = file_name(step)
    record = {
        "step": step,
        "taken_at": (now or datetime.now().astimezone()).isoformat(timespec="seconds"),
        "host": host["kind"],
        "project": host.get("project"),
    }
    secrets = []
    try:
        client, token, project, secrets = hostenv.api_access(host)
        record.update(collect(client, token, project))
    except Exception as exc:  # 取れなくてもシナリオは止めない。errors には例外の型名だけを入れる
        record.update(
            summary=_summary([], []),
            mrs=[],
            issues=[],
            calls=[],
            errors=[{"api": "snapshot", "error": f"例外 {type(exc).__name__}"}],
        )
    text = _mask(json.dumps(record, ensure_ascii=False, indent=1), secrets)
    result = {
        "step": step,
        "file": name,
        "ok": not record["errors"],
        "errors": len(record["errors"]),
    }
    try:
        (out / name).write_text(text + "\n", encoding="utf-8")
    except OSError as exc:
        result.update(file=None, ok=False, errors=result["errors"] + 1)
        log.error("相手側の状態を書けなかった", step=step, err=type(exc).__name__)
        return result
    if result["ok"]:
        log.info("相手側の状態を残した", step=step, file=name, **record["summary"])
    else:
        log.error(
            "相手側の状態を取れなかった所がある", step=step, file=name, errors=result["errors"]
        )
    return result
