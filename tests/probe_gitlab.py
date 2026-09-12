"""実物の GitLab に sh 3 本と exe を当てて 1 周する。人が手で走らせる道具で、自動テストは呼ばない。

tests/test_ticket.py と同じ形で一時リポジトリを作り、使い捨てのプロジェクトを GitLab に作って、
`ccnavi-ticket.sh` / `ccnavi-git.sh` / `ccnavi-review.sh` を本物に通す。人間役（レビュアー）は
別ユーザのトークンで API を直に叩く。sh と同じ道具を使わないほうが、片方の壊れがもう片方に隠れない。

## 用意するもの

- 動いている GitLab（既定 `http://localhost:8929`。`CCNAVI_PROBE_GITLAB` で変える）
- トークン 2 本。`docker exec -i gitlab gitlab-rails runner - < tests/make_gitlab_tokens.rb` が
  `GITLAB_TOKEN`（root、エージェント役）と `CCNAVI_PROBE_REVIEWER_TOKEN`（人間役）を出す
- 組み立て済みの exe（`dist/ccnavi/ccnavi`）。`CCNAVI_BIN_PATH` で差し替えられる
- jq と curl（sh が使う）

    GITLAB_TOKEN=... CCNAVI_PROBE_REVIEWER_TOKEN=... uv run python tests/probe_gitlab.py

## 認証画面を出さない

push は URL にトークンを埋めない（埋めると origin の綴りに混ざる）。git ラッパは
`GIT_CONFIG_COUNT` を落とすので環境変数でも差し替えられない。一時リポジトリの
`credential.helper` を空文字で一度リセットしてから（system / global の GCM を外す）、
トークンを返す helper を足す。

## 分かること

sh の HTTP 経路（curl・jq・ページング・投稿・認証）と、GitLab が返す JSON の形が sh の読み方と
合っているか。結果と生の JSON は `CCNAVI_PROBE_OUT`（既定は一時ディレクトリ）に残る。
変更要求（request_changes）は GitLab EE の機能で、CE では 404 になる。CE ではその段だけ落ちる。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT_OF_CCNAVI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REVIEW_SH = os.path.join(ROOT_OF_CCNAVI, ".claude", "scripts", "ccnavi-review.sh")
GIT_SH = os.path.join(ROOT_OF_CCNAVI, ".claude", "scripts", "ccnavi-git.sh")
TICKET_SH = os.path.join(ROOT_OF_CCNAVI, ".claude", "scripts", "ccnavi-ticket.sh")
GITLAB = os.environ.get("CCNAVI_PROBE_GITLAB", "http://localhost:8929").rstrip("/")
API = GITLAB + "/api/v4"

ROOT_TOKEN = os.environ.get("GITLAB_TOKEN", "")
REVIEWER_TOKEN = os.environ.get("CCNAVI_PROBE_REVIEWER_TOKEN", "")
if not ROOT_TOKEN or not REVIEWER_TOKEN:
    sys.exit(
        "GITLAB_TOKEN と CCNAVI_PROBE_REVIEWER_TOKEN が要る。tests/make_gitlab_tokens.rb で作る"
    )


def _exe() -> str:
    given = os.environ.get("CCNAVI_BIN_PATH", "")
    if given and os.path.isabs(given):
        return given
    base = os.path.join(ROOT_OF_CCNAVI, given or os.path.join("dist", "ccnavi", "ccnavi"))
    for candidate in (base, base + ".exe"):
        if os.path.isfile(candidate):
            return candidate
    sys.exit(f"exe が無い ({base})。build.py で組み立てる")


EXE = _exe()
STAMP = time.strftime("%m%d-%H%M%S")
OUT = os.environ.get("CCNAVI_PROBE_OUT") or tempfile.mkdtemp(prefix="ccnavi-probe-")
OUT = os.path.join(OUT, "glrun-" + STAMP)
ROOT = os.path.join(OUT, "repo")
os.makedirs(OUT, exist_ok=True)
# 走っている間ずっと書くので、閉じるのは finish()。
LOG = open(os.path.join(OUT, "steps.log"), "w", encoding="utf-8")  # noqa: SIM115
RESULTS: list[tuple[str, bool, str]] = []
API_SEQ = 0

RULES = {
    "version": 3,
    "deny": [
        {
            "id": "guard-approved",
            "match": "Write|Edit|MultiEdit|NotebookEdit",
            "glob": "*/.claude/ccnavi/*",
            "message": "ガードの設定と写しです。利用者に依頼してください。",
        }
    ],
}


def say(text: str) -> None:
    print(text, flush=True)
    LOG.write(text + "\n")
    LOG.flush()


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    say(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def redact(text: str) -> str:
    return text.replace(ROOT_TOKEN, "<root-token>").replace(REVIEWER_TOKEN, "<reviewer-token>")


def env_for() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.update(
        {
            "GITLAB_TOKEN": ROOT_TOKEN,
            "CCNAVI_BIN_PATH": EXE,
            "CCNAVI_GUARD_TICKET_APPROVAL": "disable",
            "CCNAVI_MODE": "enable",
            "CCNAVI_GUARD_CORE_FILES": "disable",
            "CCNAVI_RESTORE_IF_DENY": "disable",
            "PYTHONUTF8": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "never",
            "GIT_AUTHOR_NAME": "probe",
            "GIT_AUTHOR_EMAIL": "probe@example.com",
            "GIT_COMMITTER_NAME": "probe",
            "GIT_COMMITTER_EMAIL": "probe@example.com",
        }
    )
    return env


ENV = env_for()


def run(
    args: list[str], cwd: str, stdin: str = "", check: bool = False
) -> subprocess.CompletedProcess:
    done = subprocess.run(
        args,
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ENV,
    )
    shown = " ".join(args)
    LOG.write(f"\n$ ({cwd}) {redact(shown)}\n  rc={done.returncode}\n")
    if done.stdout.strip():
        LOG.write("  stdout: " + redact(done.stdout.strip()).replace("\n", "\n          ") + "\n")
    if done.stderr.strip():
        LOG.write("  stderr: " + redact(done.stderr.strip()).replace("\n", "\n          ") + "\n")
    LOG.flush()
    if check and done.returncode != 0:
        raise AssertionError(f"{shown}\n{done.stdout}\n{done.stderr}")
    return done


def git(cwd: str, *args: str) -> str:
    return run(["git", *args], cwd, check=True).stdout


def exe(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
    return run([EXE, "--root", ROOT, *args], ROOT_OF_CCNAVI, stdin=stdin)


def hook(event: str, tool: str, cwd: str, **tool_input) -> str:
    payload = {
        "hook_event_name": event,
        "tool_name": tool,
        "cwd": cwd,
        "session_id": "probe",
        "tool_input": tool_input,
    }
    done = exe(stdin=json.dumps(payload))
    if not done.stdout.strip():
        return ""
    try:
        out = json.loads(done.stdout).get("hookSpecificOutput", {})
    except ValueError:
        return done.stdout
    return out.get("permissionDecisionReason") or out.get("additionalContext") or ""


def sh(script: str, cwd: str, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
    return run(["sh", script, *args], cwd, stdin=stdin)


def api(
    method: str, path: str, token: str, payload: dict | None = None, tag: str = ""
) -> tuple[int, object]:
    """人間役の API。生の応答を OUT に残す。起動直後の GitLab は遅いので 120 秒を 3 回まで待つ。"""
    global API_SEQ
    API_SEQ += 1
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={"PRIVATE-TOKEN": token, "Content-Type": "application/json"},
    )
    status, raw = 0, ""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                status, raw = res.status, res.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read().decode("utf-8", "replace")
            break
        except (TimeoutError, OSError) as exc:
            LOG.write(f"\nAPI {method} {path} 失敗 ({exc}) {attempt + 1} 回目\n")
            LOG.flush()
            status, raw = 0, json.dumps({"error": str(exc)})
            time.sleep(10)
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        body = raw
    slug = tag or path.strip("/").replace("/", "_")[:60]
    with open(
        os.path.join(OUT, f"api-{API_SEQ:02d}-{method}-{slug}.json"), "w", encoding="utf-8"
    ) as f:
        json.dump(
            {"status": status, "path": path, "payload": payload, "body": body},
            f,
            ensure_ascii=False,
            indent=1,
        )
    LOG.write(f"\nAPI {method} {path} -> {status}\n")
    LOG.flush()
    return status, body


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def ticket_text(name, *, parent="", phase=None, allow=(), review=True, title="作業", issue=None):
    lines = ["---", "version: 1", f"ticket: {name}"]
    if parent:
        lines += [f"parent: {parent}", f"phase: {phase}"]
    if issue:
        lines.append(f"issue: {issue}")
    lines += [
        "human_review:",
        f"  required: {'true' if review else 'false'}",
        "  reason: 実測",
        f"title: {title}",
        "rationale: |",
        "  実物の GitLab に当てる実測。",
    ]
    if allow:
        lines.append("allow:")
        for g in allow:
            lines += ["  - match: Write|Edit|MultiEdit", f'    glob: "{g}"']
    lines += [
        'started_at: ""',
        'completed_at: ""',
        'base_sha: ""',
        "---",
        "",
        "本文（人が読む説明）",
        "",
    ]
    return "\n".join(lines)


def worktree(name: str, base: str) -> str:
    path = os.path.join(ROOT, ".claude", "worktrees", name)
    git(ROOT, "worktree", "add", "--quiet", path, "-b", name, base)
    return path


def propose(tree: str, name: str, **kw) -> str:
    return write(
        os.path.join(tree, "wip", "tickets", "todo", name + ".md"), ticket_text(name, **kw)
    )


def commit_all(tree: str, msg: str) -> None:
    git(tree, "add", "-A")
    git(tree, "commit", "--quiet", "-m", msg)


def mr_of(project_id: int, branch: str) -> dict | None:
    _, found = api(
        "GET",
        f"/projects/{project_id}/merge_requests?state=all&source_branch={branch}",
        ROOT_TOKEN,
        tag=f"mr-list-{branch}",
    )
    return found[0] if isinstance(found, list) and found else None


def notes_of(project_id: int, iid: int) -> list[dict]:
    _, found = api(
        "GET",
        f"/projects/{project_id}/merge_requests/{iid}/notes?per_page=100&sort=asc&order_by=created_at",
        ROOT_TOKEN,
        tag=f"notes-{iid}",
    )
    return found if isinstance(found, list) else []


def has_marker(notes: list[dict], marker: str) -> bool:
    return any(n.get("body", "").startswith(marker) for n in notes)


def main() -> int:
    say(f"== 実測の置き場: {OUT}")
    status, version = api("GET", "/version", ROOT_TOKEN, tag="version")
    record(
        "API に root トークンで入れる",
        status == 200,
        f"GitLab {version.get('version') if isinstance(version, dict) else version}",
    )
    status, me = api("GET", "/user", REVIEWER_TOKEN, tag="reviewer-user")
    record(
        "レビュアーのトークンで入れる",
        status == 200,
        str(me.get("username") if isinstance(me, dict) else me),
    )
    if status != 200:
        return finish()
    reviewer_id = int(me["id"])

    # ---- 0. 人が用意するもの: プロジェクト、メンバー、課題、main
    project_path = f"ccnavi-probe-{STAMP}"
    status, project = api(
        "POST",
        "/projects",
        ROOT_TOKEN,
        {
            "name": project_path,
            "path": project_path,
            "visibility": "private",
            "initialize_with_readme": False,
        },
        tag="project-create",
    )
    record(
        "プロジェクトを作る",
        status == 201,
        str(project.get("web_url") if isinstance(project, dict) else project),
    )
    if status != 201:
        return finish()
    pid = project["id"]
    status, _ = api(
        "POST",
        f"/projects/{pid}/members",
        ROOT_TOKEN,
        {"user_id": reviewer_id, "access_level": 40},
        tag="member-add",
    )
    record("レビュアーをメンバーにする", status == 201)
    status, issue = api(
        "POST",
        f"/projects/{pid}/issues",
        ROOT_TOKEN,
        {"title": "greeter に挨拶を足す", "description": "実測の元になる課題"},
        tag="issue-create",
    )
    record(
        "課題を立てる", status == 201, f"#{issue.get('iid') if isinstance(issue, dict) else '?'}"
    )
    issue_no = issue["iid"]

    os.makedirs(os.path.join(ROOT, ".claude"), exist_ok=True)
    git(ROOT, "init", "--quiet", "-b", "main")
    write(os.path.join(ROOT, "src", "keep.py"), "print(1)\n")
    write(os.path.join(ROOT, ".gitignore"), ".claude/\nwip/tmp/\n")
    commit_all(ROOT, "init")
    origin = f"{GITLAB}/root/{project_path}.git"
    git(ROOT, "remote", "add", "origin", origin)
    # 認証は git の設定側に置く。空文字で system / global の helper（GCM）を外し、
    # 環境変数 GITLAB_TOKEN を返す helper を足す。ラッパが落とすのは GIT_CONFIG_COUNT だけで、
    # 環境変数は helper の sh に届く。トークンをファイルに書かない（置き去りになる）。
    git(ROOT, "config", "--add", "credential.helper", "")
    git(
        ROOT,
        "config",
        "--add",
        "credential.helper",
        '!f() { echo username=oauth2; echo "password=$GITLAB_TOKEN"; }; f',
    )
    pushed = run(["git", "push", "--quiet", "-u", "origin", "main"], ROOT)
    record(
        "main を push（認証画面なし）", pushed.returncode == 0, redact(pushed.stderr.strip())[:200]
    )
    write(os.path.join(ROOT, ".claude", "ccnavi", "rules.yml"), json.dumps(RULES))

    # ---- 1. 親 1 本と子 2 本、フェーズ 1
    parent_tree = worktree("i0001", "main")
    propose(parent_tree, "i0001", allow=("src/*", "wip/*"), title="挨拶を足す", issue=issue_no)
    propose(
        parent_tree,
        "i0001-01",
        parent="i0001",
        phase=1,
        allow=("src/a/*",),
        review=True,
        title="a を書く",
    )
    propose(
        parent_tree,
        "i0001-02",
        parent="i0001",
        phase=1,
        allow=("src/b/*",),
        review=False,
        title="b を書く",
    )
    commit_all(parent_tree, "tickets")
    approved = exe("--approve", stdin="y\n")
    record(
        "--approve（親 1 子 2）", approved.returncode == 0, redact(approved.stderr.strip())[:200]
    )

    for child in ("i0001-01", "i0001-02"):
        tree = worktree(child, "i0001")
        started = sh(TICKET_SH, parent_tree, "start", child)
        record(f"ticket start {child}", started.returncode == 0, started.stderr.strip()[:200])
        write(os.path.join(tree, "src", child[-1], "work.py"), f"# {child}\n")
        commit_all(tree, f"{child}: work")
    commit_all(parent_tree, "start")

    child_push = sh(
        GIT_SH,
        os.path.join(ROOT, ".claude", "worktrees", "i0001-01"),
        "push",
        "-u",
        "origin",
        "i0001-01",
    )
    record(
        "子の作業ツリーからの push はラッパが拒む",
        child_push.returncode != 0 and "子チケット" in (child_push.stderr + child_push.stdout),
    )

    for child in ("i0001-01", "i0001-02"):
        done = sh(TICKET_SH, parent_tree, "done", child)
        record(f"ticket done {child}", done.returncode == 0, done.stderr.strip()[:200])
    commit_all(parent_tree, "done")
    for child in ("i0001-01", "i0001-02"):
        git(parent_tree, "merge", "--quiet", "--no-edit", child)

    said = hook("PostToolUse", "Bash", parent_tree, command="ls")
    record(
        "フェーズ 1 の終わりの告知",
        "フェーズ 1 が終わりました" in said,
        said[:120].replace("\n", " "),
    )
    record(
        "ゲートが Agent を止める",
        "DENY_PHASE_GATE" in hook("PreToolUse", "Agent", parent_tree, description="次の子"),
    )

    body = write(os.path.join(OUT, "request-1.md"), "見てほしい点\n\n- src/a と src/b を足した\n")
    early = sh(REVIEW_SH, parent_tree, "request", "--phase", "1", "--body-file", body)
    record(
        "push 前の request は前提で止まる",
        early.returncode != 0 and "push" in early.stderr,
        early.stderr.strip()[-120:],
    )

    pushed = sh(GIT_SH, parent_tree, "push", "-u", "origin", "i0001")
    record(
        "親の push（ラッパ経由、認証画面なし）",
        pushed.returncode == 0,
        redact(pushed.stdout + pushed.stderr).strip()[:120],
    )
    status, _ = api(
        "GET", f"/projects/{pid}/repository/branches/i0001", ROOT_TOKEN, tag="branch-i0001"
    )
    record("GitLab に i0001 ブランチがある", status == 200)

    # ---- 2. origin の読み方と request
    origin_seen = sh(REVIEW_SH, parent_tree, "origin")
    say("origin の読み方:\n" + redact(origin_seen.stdout + origin_seen.stderr))
    record(
        "origin をホスト（ポート付き）とパスに読む",
        f"path=root/{project_path}" in origin_seen.stdout
        and "api_base=" + GITLAB + "/api/v4" in origin_seen.stdout,
    )

    requested = sh(REVIEW_SH, parent_tree, "request", "--phase", "1", "--body-file", body)
    say("request:\n" + redact(requested.stdout + requested.stderr))
    record("request が通る（MR を作って投稿して印）", requested.returncode == 0)
    mr = mr_of(pid, "i0001")
    record(
        "MR が Draft で作られている",
        bool(mr) and bool(mr.get("draft")) and mr.get("title", "").startswith("Draft:"),
        f"{mr and mr.get('web_url')} title={mr and mr.get('title')}",
    )
    record(
        "MR の本文に Closes #課題 がある",
        bool(mr) and f"Closes #{issue_no}" in (mr.get("description") or ""),
    )
    if not mr:
        return finish()
    iid = mr["iid"]
    record(
        "依頼の note に ccnavi:request の印がある",
        has_marker(notes_of(pid, iid), "<!-- ccnavi:request i0001:1 -->"),
    )
    again = sh(REVIEW_SH, parent_tree, "request", "--phase", "1", "--body-file", body)
    record(
        "2 度目の request は依頼済みで止まる", again.returncode != 0 and "依頼済み" in again.stderr
    )
    fetched = sh(REVIEW_SH, parent_tree, "fetch")
    write(os.path.join(OUT, "fetch-1-empty.json"), fetched.stdout)
    record(
        "fetch が JSON を返す",
        fetched.returncode == 0 and fetched.stdout.strip().startswith("{"),
        fetched.stderr.strip()[:200],
    )

    # ---- 3. レビュアーが指摘 → check → 変更要求（EE だけ）→ 解決と approve → check
    status, disc = api(
        "POST",
        f"/projects/{pid}/merge_requests/{iid}/discussions",
        REVIEWER_TOKEN,
        {"body": "ここは名前を変えてほしい"},
        tag="discussion-create",
    )
    record(
        "レビュアーが討論を立てる（位置なし、resolvable）",
        status == 201 and bool(disc.get("notes", [{}])[0].get("resolvable")),
    )
    disc_id = disc.get("id", "")
    fetched = sh(REVIEW_SH, parent_tree, "fetch")
    write(os.path.join(OUT, "fetch-2-unresolved.json"), fetched.stdout)
    checked = sh(REVIEW_SH, parent_tree, "check", "--phase", "1")
    record(
        "未解決があると check は止まる",
        checked.returncode != 0 and "未解決" in checked.stderr,
        checked.stderr.strip()[:200],
    )
    record(
        "その間ゲートは閉じたまま",
        "DENY_PHASE_GATE" in hook("PreToolUse", "Agent", parent_tree, description="次の子"),
    )

    api(
        "PUT",
        f"/projects/{pid}/merge_requests/{iid}",
        ROOT_TOKEN,
        {"reviewer_ids": [reviewer_id]},
        tag="mr-set-reviewer",
    )
    status, rc_body = api(
        "POST",
        f"/projects/{pid}/merge_requests/{iid}/request_changes",
        REVIEWER_TOKEN,
        tag="request-changes",
    )
    ee = status in (200, 201)
    record(
        "レビュアーが変更要求を出す（EE の機能。CE では 404）",
        ee or status == 404,
        f"status={status}",
    )
    status, _ = api(
        "PUT",
        f"/projects/{pid}/merge_requests/{iid}/discussions/{disc_id}",
        REVIEWER_TOKEN,
        {"resolved": True},
        tag="discussion-resolve",
    )
    record("レビュアーが討論を解決する", status == 200)
    if ee:
        fetched = sh(REVIEW_SH, parent_tree, "fetch")
        write(os.path.join(OUT, "fetch-3-changes-requested.json"), fetched.stdout)
        checked = sh(REVIEW_SH, parent_tree, "check", "--phase", "1")
        record(
            "変更要求が立っていると check は止まる",
            checked.returncode != 0 and "変更要求" in checked.stderr,
            checked.stderr.strip()[:200],
        )
        accepted = sh(REVIEW_SH, parent_tree, "accept", "1", stdin="y\n")
        record(
            "変更要求は accept でも通らない",
            accepted.returncode != 0 and "変更要求" in (accepted.stderr + accepted.stdout),
        )
    status, ap = api(
        "POST", f"/projects/{pid}/merge_requests/{iid}/approve", REVIEWER_TOKEN, tag="approve"
    )
    record("レビュアーが approve する", status in (200, 201), f"status={status}")
    _, reviewers = api(
        "GET",
        f"/projects/{pid}/merge_requests/{iid}/reviewers",
        ROOT_TOKEN,
        tag="reviewers-after-approve",
    )
    say("approve 後の reviewers: " + json.dumps(reviewers, ensure_ascii=False)[:300])
    fetched = sh(REVIEW_SH, parent_tree, "fetch")
    write(os.path.join(OUT, "fetch-4-approved.json"), fetched.stdout)
    checked = sh(REVIEW_SH, parent_tree, "check", "--phase", "1")
    record(
        "解決と approve の後は check が通る",
        checked.returncode == 0,
        (checked.stdout + checked.stderr).strip()[:200],
    )
    record(
        "ゲートが開く",
        "DENY_PHASE_GATE" not in hook("PreToolUse", "Agent", parent_tree, description="次の子"),
    )

    memo = write(
        os.path.join(OUT, "note-1.md"), "チャットで「命名は次のフェーズで直す」と合意した。\n"
    )
    noted = sh(REVIEW_SH, parent_tree, "note", "--body-file", memo)
    record("note が投稿される", noted.returncode == 0, (noted.stdout + noted.stderr).strip()[:160])
    record("note に ccnavi:note の印がある", has_marker(notes_of(pid, iid), "<!-- ccnavi:note -->"))

    status, disc2 = api(
        "POST",
        f"/projects/{pid}/merge_requests/{iid}/discussions",
        REVIEWER_TOKEN,
        {"body": "typo。次でよい"},
        tag="discussion-create-2",
    )
    disc2_note = disc2.get("notes", [{}])[0].get("id", "")
    accepted = sh(REVIEW_SH, parent_tree, "accept", "1", stdin="y\n")
    say("accept:\n" + redact(accepted.stdout + accepted.stderr))
    record(
        "accept で未解決を受け入れる", accepted.returncode == 0 and "受け入れた" in accepted.stdout
    )
    acc = [n for n in notes_of(pid, iid) if n.get("body", "").startswith("<!-- ccnavi:accept -->")]
    record(
        "受け入れの note が MR に写る",
        bool(acc) and f"#note_{disc2_note}" in acc[-1].get("body", ""),
    )
    checked = sh(REVIEW_SH, parent_tree, "check", "--phase", "1")
    record(
        "受け入れ済みの討論は check で数えない",
        checked.returncode == 0,
        checked.stderr.strip()[:200],
    )

    # ---- 4. 親を閉じて ready（Draft を外す）
    refused = sh(REVIEW_SH, parent_tree, "ready")
    record(
        "wip/ が追跡されたままなら ready は止まる",
        refused.returncode != 0 and "wip" in refused.stderr,
        refused.stderr.strip()[:200],
    )
    record("親の start", sh(TICKET_SH, parent_tree, "start", "i0001").returncode == 0)
    closed = sh(TICKET_SH, parent_tree, "done", "i0001")
    record("親の done", closed.returncode == 0, (closed.stdout + closed.stderr).strip()[:200])
    commit_all(parent_tree, "状態の移動")
    git(parent_tree, "rm", "-r", "-q", "wip")
    git(parent_tree, "commit", "--quiet", "-m", "chore: wip を片付ける")
    pushed = sh(GIT_SH, parent_tree, "push")
    record(
        "親の push（引数なし）",
        pushed.returncode == 0,
        redact(pushed.stdout + pushed.stderr).strip()[:120],
    )
    ready = sh(REVIEW_SH, parent_tree, "ready")
    say("ready:\n" + redact(ready.stdout + ready.stderr))
    record("ready が通る", ready.returncode == 0)
    mr = mr_of(pid, "i0001")
    record(
        "MR の Draft が外れ squash が立つ",
        bool(mr) and not mr.get("draft") and bool(mr.get("squash")),
        f"title={mr and mr.get('title')} draft={mr and mr.get('draft')} "
        f"squash={mr and mr.get('squash')}",
    )
    record("ready の note が MR にある", has_marker(notes_of(pid, iid), "<!-- ccnavi:ready -->"))

    # ---- 5. 別の親を人が締める（wrapup）
    parent2 = worktree("i0002", "main")
    propose(parent2, "i0002", allow=("src/*", "wip/*"), title="途中で締める親")
    propose(
        parent2,
        "i0002-01",
        parent="i0002",
        phase=1,
        allow=("src/c/*",),
        review=True,
        title="c を書く",
    )
    commit_all(parent2, "tickets")
    approved = exe("--approve", stdin="y\n")
    record("--approve（i0002）", approved.returncode == 0, redact(approved.stderr.strip())[:200])
    record("i0002 の push", sh(GIT_SH, parent2, "push", "-u", "origin", "i0002").returncode == 0)
    status, mr2 = api(
        "POST",
        f"/projects/{pid}/merge_requests",
        ROOT_TOKEN,
        {"source_branch": "i0002", "target_branch": "main", "title": "Draft: 途中で締める親"},
        tag="mr-create-i0002",
    )
    record("人が i0002 の MR を作る", status == 201)
    if status == 201:
        api(
            "POST",
            f"/projects/{pid}/merge_requests/{mr2['iid']}/discussions",
            REVIEWER_TOKEN,
            {"body": "残る指摘"},
            tag="discussion-create-3",
        )
        wrapped = sh(REVIEW_SH, parent2, "wrapup", "--reason", "実測はここまで", stdin="y\n")
        say("wrapup:\n" + redact(wrapped.stdout + wrapped.stderr))
        record("wrapup が通る", wrapped.returncode == 0)
        _, issues = api(
            "GET",
            f"/projects/{pid}/issues?state=opened&order_by=created_at&sort=desc",
            ROOT_TOKEN,
            tag="issues-after-wrapup",
        )
        made = [
            i for i in (issues if isinstance(issues, list) else []) if "残り" in i.get("title", "")
        ]
        record(
            "残りを写す issue ができる", bool(made), f"{made[0].get('web_url') if made else '無し'}"
        )
        record(
            "未着手の子が cancelled/ へ動く",
            os.path.exists(os.path.join(parent2, "wip", "tickets", "cancelled", "i0002-01.md")),
        )
        record(
            "wrapup の note が MR にある",
            has_marker(notes_of(pid, mr2["iid"]), "<!-- ccnavi:wrapup -->"),
        )

    # ---- 6. URL にトークンを埋めた origin でも読めて、出力に漏れない
    scheme, _, hostpath = origin.partition("://")
    git(parent2, "remote", "set-url", "origin", f"{scheme}://oauth2:{ROOT_TOKEN}@{hostpath}")
    seen = sh(REVIEW_SH, parent2, "origin")
    say("トークン入り URL の origin の読み方:\n" + redact(seen.stdout + seen.stderr))
    record(
        "トークン入り URL でも host と api_base が正しい",
        "api_base=" + GITLAB + "/api/v4" in seen.stdout,
    )
    record(
        "トークン入り URL のトークンが出力に漏れない", ROOT_TOKEN not in seen.stdout + seen.stderr
    )
    git(parent2, "remote", "set-url", "origin", origin)
    return finish()


def finish() -> int:
    say("\n==== まとめ ====")
    for name, ok, _ in RESULTS:
        say(f"{'PASS' if ok else 'FAIL'}  {name}")
    failed = [r for r in RESULTS if not r[1]]
    say(f"\n{len(RESULTS) - len(failed)} / {len(RESULTS)} PASS。置き場: {OUT}")
    LOG.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
