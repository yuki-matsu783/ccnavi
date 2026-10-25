"""実物の GitLab に sh 5 本と exe を適用して 1 周する。ユーザが手で走らせる道具で、
自動テストは呼ばない。

tests/ticket/test_ticket.py と同じ形で一時リポジトリを作り、使い捨てのプロジェクトを
GitLab に作って、`ccnavi-ticket.sh` / `ccnavi-git.sh` / `ccnavi-review.sh` /
`ccnavi-user-push-approved.sh` / `ccnavi-user-close-early.sh` を実物に通す。
人間役（レビュアー）は別ユーザのトークンで API を直に呼ぶ。
sh と同じ道具を使わないのは、片方の不具合がもう片方に隠れないようにするため。

## 用意するもの

- 動いている GitLab（既定 `http://127.0.0.1:8929`。`CCNAVI_PROBE_GITLAB` で変える）。
  `tests/manual/gitlab/docker_gitlab.py up` で立てる（手順は docs/42_claude/gitlab-manual.md）
- トークン 2 本（`GITLAB_TOKEN`（root、エージェント役）と
  `CCNAVI_PROBE_REVIEWER_TOKEN`（人間役））。
  `docker_gitlab.py up` が作り、0600 のファイルに書く。`set -a; . <そのパス>; set +a` で読む
- 組み立て済みの exe（`dist/ccnavi/ccnavi`）。`CCNAVI_ENTRY_PATH` で差し替えられる
- jq と curl（sh が使う）

    uv run python tests/manual/gitlab/probe_gitlab.py

GitLab API を人間役のトークンで呼ぶ部分は `gitlab_api.py` にある（import しても副作用が無い）。
`--help` は、この説明を出して終わる（トークンも exe も要らない）。

## 認証画面を出さない

push は URL にトークンを埋めない（埋めると origin の URL に混ざる）。git のラッパースクリプトは
`GIT_CONFIG_COUNT` を取り除くため、環境変数では差し替えられない。一時リポジトリの
`credential.helper` を空文字で一度リセットしてから（system / global の GCM を外す）、
トークンを返す helper を足す。

## 分かること

sh の HTTP 経路（curl・jq・ページング・投稿・認証）と、GitLab が返す JSON の形が sh の読み方と
合っているか。結果と生の JSON は `CCNAVI_PROBE_OUT`（既定は一時ディレクトリ）に残る。
変更要求（request_changes）は GitLab EE の機能で、CE では 404 になる。
CE では 404 を許し、その段は PASS とする。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import gitlab_api

# 提案の書式（計画・config の phases）は、ticket のテストと同じ補助で作る
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)
from tests.ticketsample import plain_parent, write_plain_phases  # noqa: E402

ROOT_OF_CCNAVI = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
REVIEW_SH = os.path.join(ROOT_OF_CCNAVI, ".ccnavi", "scripts", "ccnavi-review.sh")
CLOSE_EARLY_SH = os.path.join(ROOT_OF_CCNAVI, ".ccnavi", "scripts", "ccnavi-user-close-early.sh")
PUSH_APPROVED_SH = os.path.join(
    ROOT_OF_CCNAVI, ".ccnavi", "scripts", "ccnavi-user-push-approved.sh"
)
GIT_SH = os.path.join(ROOT_OF_CCNAVI, ".ccnavi", "scripts", "ccnavi-git.sh")
TICKET_SH = os.path.join(ROOT_OF_CCNAVI, ".ccnavi", "scripts", "ccnavi-ticket.sh")
GITLAB = os.environ.get(
    "CCNAVI_PROBE_GITLAB",
    f"http://127.0.0.1:{os.environ.get('CCNAVI_GITLAB_PORT', '8929')}",
).rstrip("/")

# import では何も起こさない。トークン・exe・一時ディレクトリ・ログは setup() が main() の中で作る。
ROOT_TOKEN = ""
REVIEWER_TOKEN = ""
EXE = ""
OUT = ""
ROOT = ""
ENV: dict[str, str] = {}
STAMP = ""
LOG = None
RESULTS: list[tuple[str, bool, str]] = []
API_SEQ = 0


def _exe() -> str:
    given = os.environ.get("CCNAVI_ENTRY_PATH", "")
    if given and os.path.isabs(given):
        return given
    base = os.path.join(ROOT_OF_CCNAVI, given or os.path.join("dist", "ccnavi", "ccnavi"))
    for candidate in (base, base + ".exe"):
        if os.path.isfile(candidate):
            return candidate
    sys.exit(f"exe が無い ({base})。build.py で組み立てる")


def setup() -> None:
    """トークン・exe・実測の置き場を作る。main() の最初に 1 回だけ呼ぶ。"""
    global ROOT_TOKEN, REVIEWER_TOKEN, EXE, OUT, ROOT, LOG, ENV, STAMP
    ROOT_TOKEN = os.environ.get("GITLAB_TOKEN", "")
    REVIEWER_TOKEN = os.environ.get("CCNAVI_PROBE_REVIEWER_TOKEN", "")
    if not ROOT_TOKEN or not REVIEWER_TOKEN:
        sys.exit(
            "GITLAB_TOKEN と CCNAVI_PROBE_REVIEWER_TOKEN が要る。"
            "tests/manual/gitlab/make_gitlab_tokens.rb で作る"
        )
    EXE = _exe()
    STAMP = time.strftime("%m%d-%H%M%S")
    OUT = os.environ.get("CCNAVI_PROBE_OUT") or tempfile.mkdtemp(prefix="ccnavi-probe-")
    OUT = os.path.join(OUT, "glrun-" + STAMP)
    ROOT = os.path.join(OUT, "repo")
    os.makedirs(OUT, exist_ok=True)
    # 実行中ずっと書き続けるので、閉じるのは finish() で行う。
    LOG = open(os.path.join(OUT, "steps.log"), "w", encoding="utf-8")  # noqa: SIM115
    ENV = env_for()


RULES = {
    "version": 1,
    "deny": [
        {
            "id": "guard-approved",
            "match": "Write|Edit|NotebookEdit",
            "glob": "*/.ccnavi/*",
            "message": "ガードの設定と承認済みチケットです。ユーザに依頼してください。",
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
            "CCNAVI_ENTRY_PATH": EXE,
            # 一時リポジトリをワークスペースにする（sh の ticket 系は cwd からこの根を使う）
            "CCNAVI_WORKSPACE": ROOT,
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


def record_api(event: dict) -> None:
    """人間役の API の生の応答を OUT に残す。"""
    global API_SEQ
    if event["kind"] == "retry":
        LOG.write(
            f"\nAPI {event['method']} {event['path']} 失敗 ({event['error']}) "
            f"{event['attempt']} 回目\n"
        )
        LOG.flush()
        return
    API_SEQ += 1
    method, path, status = event["method"], event["path"], event["status"]
    slug = event["tag"] or path.strip("/").replace("/", "_")[:60]
    with open(
        os.path.join(OUT, f"api-{API_SEQ:02d}-{method}-{slug}.json"), "w", encoding="utf-8"
    ) as f:
        json.dump(
            {"status": status, "path": path, "payload": event["payload"], "body": event["body"]},
            f,
            ensure_ascii=False,
            indent=1,
        )
    LOG.write(f"\nAPI {method} {path} -> {status}\n")
    LOG.flush()


CLIENT = gitlab_api.Client(GITLAB, recorder=record_api)


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


EXPLAINER = "wip/eli5/phase-1.html"


def write_explainer(tree: str) -> str:
    # request は --eli5 の HTML を要る。外部の読み込みは無し（ccnavi-review の規則）
    return write(
        os.path.join(tree, "wip", "eli5", "phase-1.html"),
        '<!doctype html><meta charset="utf-8"><title>実測</title>'
        "<p>挨拶を足す変更の確認。危険は無い（実測用）。</p>\n",
    )


def ticket_text(name, *, parent="", phase=None, allow=(), review=True, title="作業", issue=None):
    lines = ["---", "version: 1", f"ticket: {name}"]
    if parent:
        lines += [f"parent: {parent}", f"phase: {phase}"]
    if not parent:
        # 親は MR の番号を持つ（ccnavi の親チケットは issue と MR のセット）
        lines.append("mr: 1")
    if issue:
        lines.append(f"issue: {issue}")
    lines += [
        "human_review:",
        f"  required: {'true' if review else 'false'}",
        "  reason: 実測",
        f"title: {title}",
        "rationale: |",
        "  実物の GitLab に適用する実測。",
    ]
    if allow:
        lines.append("allow:")
        for g in allow:
            lines += ["  - match: Write|Edit", f'    glob: "{g}"']
    lines += [
        'started_at: ""',
        'completed_at: ""',
        'base_sha: ""',
        "---",
        "",
        "本文（ユーザが読む説明）",
        "",
    ]
    # 親には計画（1 フェーズ）を足す。子は plain_parent が何もしない
    return plain_parent("\n".join(lines), plan=1)


def worktree(name: str, base: str) -> str:
    path = os.path.join(ROOT, ".claude", "worktrees", name)
    git(ROOT, "worktree", "add", "--quiet", path, "-b", name, base)
    return path


def propose(tree: str, name: str, **kw) -> str:
    return write(
        os.path.join(tree, "wip", "proposals", "todo", name + ".md"), ticket_text(name, **kw)
    )


def commit_all(tree: str, msg: str) -> None:
    git(tree, "add", "-A")
    # 変更が無い段（start など）でも止まらない
    git(tree, "commit", "--quiet", "--allow-empty", "-m", msg)


def mr_of(project_id: int, branch: str) -> dict | None:
    return CLIENT.find_mr(ROOT_TOKEN, project_id, branch)


def notes_of(project_id: int, iid: int) -> list[dict]:
    return CLIENT.notes(ROOT_TOKEN, project_id, iid)


has_marker = gitlab_api.has_marker
# 依頼の目印は製品の形のまま照合する（review_host.MARKER_REQUEST に key=<16 桁の 16 進> が続く）
REQUEST_MARKER = re.compile(r"<!-- ccnavi:request i0001:1 key=[0-9a-f]{16} -->")


def request_marked(project_id: int, iid: int) -> bool:
    return any(
        REQUEST_MARKER.fullmatch(note.get("body", "").split("\n", 1)[0].strip())
        for note in notes_of(project_id, iid)
    )


def main() -> int:
    if any(arg in ("-h", "--help") for arg in sys.argv[1:]):
        print((__doc__ or "").strip())
        return 0
    setup()
    say(f"== 実測の置き場: {OUT}")
    status, version = CLIENT.version(ROOT_TOKEN)
    record(
        "API に root トークンで入れる",
        status == 200,
        f"GitLab {version.get('version') if isinstance(version, dict) else version}",
    )
    status, me = CLIENT.user(REVIEWER_TOKEN)
    record(
        "レビュアーのトークンで入れる",
        status == 200,
        str(me.get("username") if isinstance(me, dict) else me),
    )
    if status != 200:
        return finish()
    reviewer_id = int(me["id"])

    # ---- 0. ユーザが用意するもの: プロジェクト、メンバー、課題、main
    project_path = f"ccnavi-probe-{STAMP}"
    status, project = CLIENT.create_project(ROOT_TOKEN, project_path)
    record(
        "プロジェクトを作る",
        status == 201,
        str(project.get("web_url") if isinstance(project, dict) else project),
    )
    if status != 201:
        return finish()
    pid = project["id"]
    status, _ = CLIENT.add_member(ROOT_TOKEN, pid, reviewer_id, 40)
    record("レビュアーをメンバーにする", status == 201)
    status, issue = CLIENT.create_issue(
        ROOT_TOKEN, pid, "greeter に挨拶を足す", "実測の元になる課題"
    )
    record(
        "課題を立てる", status == 201, f"#{issue.get('iid') if isinstance(issue, dict) else '?'}"
    )
    issue_no = issue["iid"]

    os.makedirs(os.path.join(ROOT, ".claude"), exist_ok=True)
    git(ROOT, "init", "--quiet", "-b", "main")
    # 一時リポジトリをワークスペースにする。sh の道具（scripts と config）を写す
    shutil.copytree(
        os.path.join(ROOT_OF_CCNAVI, ".ccnavi"),
        os.path.join(ROOT, ".ccnavi"),
        ignore=shutil.ignore_patterns("bin", "docs", "__pycache__"),
    )
    write(os.path.join(ROOT, "src", "keep.py"), "print(1)\n")
    # 実物と同じく、.ccnavi/bin だけ無視する。承認済みチケットは .ccnavi の下で git が追跡する
    write(os.path.join(ROOT, ".gitignore"), ".claude/\n.ccnavi/bin/\nwip/tmp/\n")
    # 設定は init の commit に入れる（後から書くと、main と手元の config が食い違う）
    write(os.path.join(ROOT, ".ccnavi", "config", "common", "rules.yml"), json.dumps(RULES))
    write_plain_phases(ROOT)
    commit_all(ROOT, "init")
    origin = f"{GITLAB}/root/{project_path}.git"
    git(ROOT, "remote", "add", "origin", origin)
    # 認証は git の設定側に置く。空文字で system / global の helper（GCM）を外し、
    # 環境変数 GITLAB_TOKEN を返す helper を足す。ラッパースクリプトが除くのは
    # GIT_CONFIG_COUNT だけで、環境変数は helper の sh に届く。トークンをファイルに
    # 書かない（置き去りになる）。
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

    # ---- 1. 親 1 本と子 2 本、フェーズ 1
    parent_tree = worktree("i0001", "main")
    propose(parent_tree, "i0001", allow=("src/*", "wip/*"), title="挨拶を足す", issue=issue_no)
    propose(
        parent_tree,
        "i0001-01-01",
        parent="i0001",
        phase=1,
        allow=("src/a/*",),
        review=True,
        title="a を書く",
    )
    propose(
        parent_tree,
        "i0001-01-02",
        parent="i0001",
        phase=1,
        allow=("src/b/*",),
        review=False,
        title="b を書く",
    )
    write_explainer(parent_tree)
    commit_all(parent_tree, "tickets")
    # 子の start は、親のブランチがリモートにあることを前提とする（ccnavi-start 経由の親）
    pushed_parent = sh(GIT_SH, parent_tree, "push", "-u", "origin", "i0001")
    record(
        "親 i0001 を push",
        pushed_parent.returncode == 0,
        redact(pushed_parent.stderr.strip())[:200],
    )
    # 親の mr 欄（ticket_text の "mr: 1"）は、親の Draft MR が既にある前提。
    # ccnavi-start が作る MR を、probe は通らずに API で作る。説明に Closes を入れる
    status, parent_mr = CLIENT.call(
        "POST",
        f"/projects/{pid}/merge_requests",
        ROOT_TOKEN,
        {
            "source_branch": "i0001",
            "target_branch": "main",
            "title": "Draft: 挨拶を足す",
            "description": f"Closes #{issue_no}",
        },
        tag="mr-create-parent",
    )
    record(
        "親の Draft MR を作る（mr: 1 と一致）",
        status == 201 and isinstance(parent_mr, dict) and parent_mr.get("iid") == 1,
        f"status={status}",
    )
    approved = exe("user", "agree", stdin="y\n")
    record("--agree（親 1 子 2）", approved.returncode == 0, redact(approved.stderr.strip())[:200])
    # 承認の push はユーザが打つ（ccnavi-user-push-approved.sh）。probe はユーザの役を務める
    pushed_approval = sh(PUSH_APPROVED_SH, parent_tree, "i0001")
    record(
        "承認を push（ユーザの役）",
        pushed_approval.returncode == 0,
        redact(pushed_approval.stdout + pushed_approval.stderr).strip()[:200],
    )
    parent_started = sh(TICKET_SH, parent_tree, "start", "i0001")
    say("親 i0001 の start（全文）:\n" + redact(parent_started.stdout + parent_started.stderr))
    record("親 i0001 の start", parent_started.returncode == 0, parent_started.stderr.strip()[:200])

    for child in ("i0001-01-01", "i0001-01-02"):
        tree = worktree(child, "i0001")
        started = sh(TICKET_SH, parent_tree, "start", child)
        record(f"ticket start {child}", started.returncode == 0, started.stderr.strip()[:200])
        # 子の allow（propose の src/a/*・src/b/*）の範囲に入るパスに書く
        write(
            os.path.join(tree, "src", {"i0001-01-01": "a", "i0001-01-02": "b"}[child], "work.py"),
            f"# {child}\n",
        )
        # finish では学びの記録が必須（learning.py の record_missing）。learn で雛形を書いてから
        # 同じコミットに入れる（learnings: none のまま）
        learned = sh(TICKET_SH, tree, "learn")
        record(
            f"learn が雛形を書く（{child}）", learned.returncode == 0, learned.stderr.strip()[:200]
        )
        commit_all(tree, f"{child}: work")
    commit_all(parent_tree, "start")

    child_push = sh(
        GIT_SH,
        os.path.join(ROOT, ".claude", "worktrees", "i0001-01-01"),
        "push",
        "-u",
        "origin",
        "i0001-01-01",
    )
    record(
        "子のワークツリーからの push はラッパースクリプトが拒む",
        child_push.returncode != 0 and "子チケット" in (child_push.stderr + child_push.stdout),
    )

    for child in ("i0001-01-01", "i0001-01-02"):
        done = sh(TICKET_SH, parent_tree, "finish", child)
        record(f"ticket finish {child}", done.returncode == 0, done.stderr.strip()[:200])
    commit_all(parent_tree, "finish")
    for child in ("i0001-01-01", "i0001-01-02"):
        git(parent_tree, "merge", "--quiet", "--no-edit", child)

    said = hook("PostToolUse", "Bash", parent_tree, command="ls")
    record(
        "フェーズ 1 の終わりの告知",
        # フェーズの名前（番号（タイトル））ごと照合する（製品の文言どおり）
        "フェーズ 1（作業） が終わりました" in said,
        said[:120].replace("\n", " "),
    )
    record(
        "レビュー待ちが Agent を止める",
        "DENY_PHASE_REVIEW" in hook("PreToolUse", "Agent", parent_tree, description="次の子"),
    )

    body = write(os.path.join(OUT, "request-1.md"), "見てほしい点\n\n- src/a と src/b を足した\n")
    # 未 push の src の変更を 1 つ足してから request する（ccnavi の置き場の差分だけでは止まらない）
    write(os.path.join(parent_tree, "src", "a", "unpushed.py"), "# 未 push\n")
    commit_all(parent_tree, "未 push の変更")
    early = sh(
        REVIEW_SH, parent_tree, "request", "--phase", "1", "--body-file", body, "--eli5", EXPLAINER
    )
    record(
        "push 前の request は前提で止まる",
        early.returncode != 0 and "push" in early.stderr,
        early.stderr.strip()[-120:],
    )

    pushed = sh(GIT_SH, parent_tree, "push", "-u", "origin", "i0001")
    record(
        "親の push（ラッパースクリプト経由、認証画面なし）",
        pushed.returncode == 0,
        redact(pushed.stdout + pushed.stderr).strip()[:120],
    )
    status, _ = CLIENT.get_branch(ROOT_TOKEN, pid, "i0001")
    record("GitLab に i0001 ブランチがある", status == 200)

    # ---- 2. origin の読み方と request
    origin_seen = sh(REVIEW_SH, parent_tree, "origin")
    say("origin の読み方:\n" + redact(origin_seen.stdout + origin_seen.stderr))
    record(
        "origin をホスト（ポート付き）とパスに読む",
        f"path=root/{project_path}" in origin_seen.stdout
        and "api_base=" + GITLAB + "/api/v4" in origin_seen.stdout,
    )

    requested = sh(
        REVIEW_SH, parent_tree, "request", "--phase", "1", "--body-file", body, "--eli5", EXPLAINER
    )
    say("request:\n" + redact(requested.stdout + requested.stderr))
    record("request が通る（MR を作って投稿してマーカー）", requested.returncode == 0)
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
        "依頼の note に ccnavi:request のマーカーがある",
        request_marked(pid, iid),
    )
    again = sh(
        REVIEW_SH, parent_tree, "request", "--phase", "1", "--body-file", body, "--eli5", EXPLAINER
    )
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

    # ---- 3. レビュアーが指摘 → confirm → 変更要求（EE だけ）→ 解決と approve → confirm
    status, disc = CLIENT.create_discussion(REVIEWER_TOKEN, pid, iid, "ここは名前を変えてほしい")
    record(
        "レビュアーがスレッドを立てる（位置なし、resolvable）",
        status == 201 and bool(disc.get("notes", [{}])[0].get("resolvable")),
    )
    disc_id = disc.get("id", "")
    fetched = sh(REVIEW_SH, parent_tree, "fetch")
    write(os.path.join(OUT, "fetch-2-unresolved.json"), fetched.stdout)
    checked = sh(REVIEW_SH, parent_tree, "confirm", "--phase", "1")
    record(
        "未解決があると confirm は止まる",
        checked.returncode != 0 and "未解決" in checked.stderr,
        checked.stderr.strip()[:200],
    )
    record(
        "その間は止まったまま",
        "DENY_PHASE_REVIEW" in hook("PreToolUse", "Agent", parent_tree, description="次の子"),
    )

    CLIENT.set_reviewers(ROOT_TOKEN, pid, iid, [reviewer_id])
    status, rc_body = CLIENT.request_changes(REVIEWER_TOKEN, pid, iid)
    ee = status in (200, 201)
    record(
        "レビュアーが変更要求を出す（EE の機能。CE では 404）",
        ee or status == 404,
        f"status={status}",
    )
    status, _ = CLIENT.resolve_discussion(REVIEWER_TOKEN, pid, iid, disc_id)
    record("レビュアーがスレッドを解決する", status == 200)
    if ee:
        fetched = sh(REVIEW_SH, parent_tree, "fetch")
        write(os.path.join(OUT, "fetch-3-changes-requested.json"), fetched.stdout)
        checked = sh(REVIEW_SH, parent_tree, "confirm", "--phase", "1")
        record(
            "変更要求が立っていると confirm は止まる",
            checked.returncode != 0 and "変更要求" in checked.stderr,
            checked.stderr.strip()[:200],
        )
        accepted = sh(REVIEW_SH, parent_tree, "decide", "1", stdin="y\n")
        record(
            "変更要求は decide でも通らない",
            accepted.returncode != 0 and "変更要求" in (accepted.stderr + accepted.stdout),
        )
    status, ap = CLIENT.approve(REVIEWER_TOKEN, pid, iid)
    record("レビュアーが approve する", status in (200, 201), f"status={status}")
    _, reviewers = CLIENT.reviewers(ROOT_TOKEN, pid, iid)
    say("approve 後の reviewers: " + json.dumps(reviewers, ensure_ascii=False)[:300])
    fetched = sh(REVIEW_SH, parent_tree, "fetch")
    write(os.path.join(OUT, "fetch-4-approved.json"), fetched.stdout)
    checked = sh(REVIEW_SH, parent_tree, "confirm", "--phase", "1")
    record(
        "解決と approve の後は confirm が通る",
        checked.returncode == 0,
        (checked.stdout + checked.stderr).strip()[:200],
    )
    record(
        "止まっていたのが解ける",
        "DENY_PHASE_REVIEW" not in hook("PreToolUse", "Agent", parent_tree, description="次の子"),
    )

    memo = write(
        os.path.join(OUT, "comment-1.md"), "チャットで「命名は次のフェーズで直す」と合意した。\n"
    )
    noted = sh(REVIEW_SH, parent_tree, "comment", "--body-file", memo)
    record(
        "comment が投稿される", noted.returncode == 0, (noted.stdout + noted.stderr).strip()[:160]
    )
    record(
        "comment に ccnavi:comment のマーカーがある",
        has_marker(notes_of(pid, iid), "<!-- ccnavi:comment -->"),
    )

    status, disc2 = CLIENT.create_discussion(
        REVIEWER_TOKEN, pid, iid, "typo。次でよい", tag="discussion-create-2"
    )
    disc2_note = disc2.get("notes", [{}])[0].get("id", "")
    accepted = sh(REVIEW_SH, parent_tree, "decide", "1", stdin="y\n")
    say("decide:\n" + redact(accepted.stdout + accepted.stderr))
    record(
        "decide で未解決を受け入れる", accepted.returncode == 0 and "受け入れた" in accepted.stdout
    )
    acc = [n for n in notes_of(pid, iid) if n.get("body", "").startswith("<!-- ccnavi:decide -->")]
    record(
        "受け入れの note が MR に書き込まれる",
        bool(acc) and f"#note_{disc2_note}" in acc[-1].get("body", ""),
    )
    checked = sh(REVIEW_SH, parent_tree, "confirm", "--phase", "1")
    record(
        "受け入れ済みのスレッドは confirm で数えない",
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
    # 親の start は冒頭（子の start より前）で済ませている。ここでは呼び直さない
    closed = sh(TICKET_SH, parent_tree, "finish", "i0001")
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

    # ---- 5. 別の親をユーザが早めに閉じる（close-early）
    parent2 = worktree("i0002", "main")
    # 親の提案には issue が要る（agree は issue が無いと承認しない）
    status, issue2 = CLIENT.create_issue(ROOT_TOKEN, pid, "途中で早めに閉じる親", "実測の 2 本目")
    record("課題 2 を立てる", status == 201)
    propose(
        parent2,
        "i0002",
        allow=("src/*", "wip/*"),
        title="途中で早めに閉じる親",
        issue=issue2["iid"],
    )
    propose(
        parent2,
        "i0002-01-01",
        parent="i0002",
        phase=1,
        allow=("src/c/*",),
        review=True,
        title="c を書く",
    )
    commit_all(parent2, "tickets")
    approved = exe("user", "agree", stdin="y\n")
    record("--agree（i0002）", approved.returncode == 0, redact(approved.stderr.strip())[:200])
    record("i0002 の push", sh(GIT_SH, parent2, "push", "-u", "origin", "i0002").returncode == 0)
    # push 直後はブランチが API に見えないことがある（400 source_branch does not exist）
    for _ in range(10):
        if CLIENT.get_branch(ROOT_TOKEN, pid, "i0002")[0] == 200:
            break
        time.sleep(1)
    status, mr2 = CLIENT.create_mr(ROOT_TOKEN, pid, "i0002", "main", "Draft: 途中で早めに閉じる親")
    record("ユーザが i0002 の MR を作る", status == 201)
    if status == 201:
        CLIENT.create_discussion(
            REVIEWER_TOKEN, pid, mr2["iid"], "残る指摘", tag="discussion-create-3"
        )
        wrapped = sh(CLOSE_EARLY_SH, parent2, "--reason", "実測はここまで", stdin="y\n")
        say("close-early:\n" + redact(wrapped.stdout + wrapped.stderr))
        record("close-early が通る", wrapped.returncode == 0)
        _, issues = CLIENT.list_issues(ROOT_TOKEN, pid)
        made = [
            i for i in (issues if isinstance(issues, list) else []) if "残り" in i.get("title", "")
        ]
        record(
            "残りを書き出す issue ができる",
            bool(made),
            f"{made[0].get('web_url') if made else '無し'}",
        )
        record(
            "未着手の子が cancelled/ へ動く",
            os.path.exists(
                os.path.join(parent2, "wip", "proposals", "cancelled", "i0002-01-01.md")
            ),
        )
        record(
            "close-early のコメントが MR にある",
            has_marker(notes_of(pid, mr2["iid"]), "<!-- ccnavi:close-early -->"),
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
