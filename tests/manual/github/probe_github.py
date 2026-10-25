"""実物の GitHub に sh 3 本と exe を適用して 1 周する。ユーザが手で走らせる道具で、
自動テストは呼ばない。tests/manual/gitlab/probe_gitlab.py の GitHub 版。

使い捨てのブランチと PR と issue を、実験場のリポジトリ
（既定 `yuki-matsu783/test-repo`。PUBLIC）に作る。終わったら PR と issue は閉じ
（public なので履歴に残る）、ブランチは消す。ここに秘密や個人情報を書き込まない。
ほかのリポジトリには書かない。ccnavi 本体のリポジトリ（このチェックアウトの origin）は、
指定しても拒む。

tests/ticket/test_ticket.py と同じ形で一時リポジトリを作り、`ccnavi-ticket.sh` / `ccnavi-git.sh` /
`ccnavi-review.sh` を実物に通す。人間役（レビュアー）の操作は、sh と別の道具（urllib で REST と
GraphQL を直に呼ぶ）で行う。sh と同じ道具を使わないほうが、片方の不具合がもう片方に隠れない。

## 用意するもの

- 実験場への書き込み権限があるトークン。`GITHUB_TOKEN`、無ければ `gh auth token` から取る
- gh（認証済み）。sh は gh があればそれを使う。curl の経路は、gh を PATH から外して
  同じ読みをやり直して確かめる。gh は `GH_TOKEN` > `GITHUB_TOKEN` > 保存済みの認証の順で使うので、
  別のアカウントに化けないよう、この probe は `GH_TOKEN` と `GITHUB_TOKEN` に
  同じトークンを置いて走らせる
- 組み立て済みの exe。`CCNAVI_ENTRY_PATH` で絶対パスを渡す（無ければ `dist/ccnavi/ccnavi`）。
  このチェックアウトより古い exe だと食い違うので、起動時に版を出す
- jq と curl（sh が使う）

    CCNAVI_ENTRY_PATH=/path/to/ccnavi uv run python tests/manual/github/probe_github.py

## 一人では確かめられないもの

人間役もトークンの持ち主と同じアカウント。GitHub は自分の PR に approve も変更要求も出させないので、
`CHANGES_REQUESTED` を実物で読む段は無い（読み方は fixture の試験が見る）。スレッド・COMMENTED の
レビュー・投稿・Draft 外しは実物で見る。別のアカウントのトークンを `CCNAVI_PROBE_REVIEWER_TOKEN` に
置けば、その段も走る。

## 分かること

sh の HTTP 経路（gh と curl・jq・ページング・投稿・認証）と、GitHub が返す JSON（特に GraphQL の
`reviewThreads`）の形が sh の読み方と合っているか。結果と生の JSON は `CCNAVI_PROBE_OUT`
（既定は一時ディレクトリ）に残る。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT_OF_CCNAVI = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
REVIEW_SH = os.path.join(ROOT_OF_CCNAVI, ".ccnavi", "scripts", "ccnavi-review.sh")
DECIDE_SH = os.path.join(ROOT_OF_CCNAVI, ".ccnavi", "scripts", "ccnavi-user-decide.sh")
CLOSE_EARLY_SH = os.path.join(ROOT_OF_CCNAVI, ".ccnavi", "scripts", "ccnavi-user-close-early.sh")
GIT_SH = os.path.join(ROOT_OF_CCNAVI, ".ccnavi", "scripts", "ccnavi-git.sh")
TICKET_SH = os.path.join(ROOT_OF_CCNAVI, ".ccnavi", "scripts", "ccnavi-ticket.sh")
REPO = os.environ.get("CCNAVI_PROBE_REPO", "yuki-matsu783/test-repo")
OWNER = REPO.split("/")[0]
API = "https://api.github.com"


def _own_repo() -> str:
    """ccnavi 本体のリポジトリ（このチェックアウトの origin）の owner/name。読めなければ空。"""
    done = subprocess.run(
        ["git", "-C", ROOT_OF_CCNAVI, "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
    )
    url = done.stdout.strip()
    m = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$", url)
    return m.group(1).lower() if m else ""


# 実験場以外には書かない。変えられるのは、ユーザが明示した実験場だけ。本体には何があっても書かない
if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", REPO):
    sys.exit(f"CCNAVI_PROBE_REPO は owner/name の形で ({REPO})")
if REPO.lower() == _own_repo():
    sys.exit(f"{REPO} は ccnavi 本体のリポジトリ。ここには書かない")
if REPO != "yuki-matsu783/test-repo" and not os.environ.get("CCNAVI_PROBE_REPO_OK"):
    sys.exit("実験場は yuki-matsu783/test-repo だけ。ほかを使うなら CCNAVI_PROBE_REPO_OK=1 を置く")
if not _own_repo():
    sys.exit(
        "このチェックアウトの origin を読めず、本体への書き込みを避けられるか分からない。止めた"
    )


def _token() -> str:
    given = os.environ.get("GITHUB_TOKEN", "")
    if given:
        return given
    done = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
    return done.stdout.strip()


TOKEN = _token()
REVIEWER_TOKEN = os.environ.get("CCNAVI_PROBE_REVIEWER_TOKEN", "")
if not TOKEN:
    sys.exit("GITHUB_TOKEN が要る（gh auth login 済みなら gh auth token から取れる）")


def _exe() -> str:
    given = os.environ.get("CCNAVI_ENTRY_PATH", "")
    if given and os.path.isabs(given):
        return given
    base = os.path.join(ROOT_OF_CCNAVI, given or os.path.join("dist", "ccnavi", "ccnavi"))
    for candidate in (base, base + ".exe"):
        if os.path.isfile(candidate):
            return candidate
    sys.exit(f"exe が無い ({base})。CCNAVI_ENTRY_PATH に絶対パスを渡すか、build.py で組み立てる")


EXE = _exe()


def _warn_stale_exe() -> None:
    """exe の版と、このチェックアウトの HEAD を並べて出す。古い exe は仕様が違うことがある。"""
    ver = subprocess.run(
        [EXE, "--version"], capture_output=True, text=True, cwd=tempfile.gettempdir()
    )
    head = subprocess.run(
        ["git", "-C", ROOT_OF_CCNAVI, "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
    )
    shown, sha = ver.stdout.strip(), head.stdout.strip()
    print(f"exe の版: {shown or '(読めない)'} / このチェックアウトの HEAD: {sha}", flush=True)
    if os.environ.get("CCNAVI_PROBE_STRICT_EXE") and (not sha or sha not in shown):
        sys.exit("exe がこのチェックアウトから組み立てたものと確認できない。build.py で組み直す")
    if not sha or sha not in shown:
        print(
            "警告: exe が古いかもしれない（CCNAVI_PROBE_STRICT_EXE=1 で止める）",
            flush=True,
        )


_warn_stale_exe()
STAMP = time.strftime("%m%d-%H%M%S")
OUT = os.environ.get("CCNAVI_PROBE_OUT") or tempfile.mkdtemp(prefix="ccnavi-probe-")
OUT = os.path.join(OUT, "ghrun-" + STAMP)
ROOT = os.path.join(OUT, "repo")
os.makedirs(OUT, exist_ok=True)
# 走っている間ずっと書くので、閉じるのは finish()。
LOG = open(os.path.join(OUT, "steps.log"), "w", encoding="utf-8")  # noqa: SIM115
RESULTS: list[tuple[str, bool, str]] = []
API_SEQ = 0
# 片付ける対象。作った順に積む
MADE_BRANCHES: list[str] = []
MADE_PRS: list[int] = []
MADE_ISSUES: list[int] = []
# 統合先に使う使い捨てのブランチ（PR の宛先）
BASE = f"ccnavi-probe-base-{STAMP}"

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
    for secret in (TOKEN, REVIEWER_TOKEN):
        if secret:
            text = text.replace(secret, "<token>")
    return text


def env_for() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.update(
        {
            "GITHUB_TOKEN": TOKEN,
            "GH_TOKEN": TOKEN,
            "CCNAVI_ENTRY_PATH": EXE,
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


def env_without_gh() -> dict[str, str]:
    """gh を PATH から外した環境。sh が curl とトークンの経路に切り替わるのを見る。"""
    env = dict(ENV)
    gh = shutil.which("gh")
    if gh:
        gh_dir = os.path.dirname(gh)
        # gh と同じ場所にある道具（jq・curl・git）は残したいので、gh だけ見えない置き場を作る
        shim = os.path.join(OUT, "nogh-bin")
        os.makedirs(shim, exist_ok=True)
        for name in os.listdir(gh_dir):
            if os.path.splitext(name)[0].lower() == "gh":  # gh と gh.exe
                continue
            link = os.path.join(shim, name)
            if not os.path.lexists(link):
                try:
                    os.symlink(os.path.join(gh_dir, name), link)
                except OSError:  # Windows など。コピーで代える
                    with contextlib.suppress(OSError):
                        shutil.copy2(os.path.join(gh_dir, name), link)
        parts = [p for p in env["PATH"].split(os.pathsep) if p != gh_dir]
        env["PATH"] = os.pathsep.join([shim, *parts])
    return env


def run(
    args: list[str],
    cwd: str,
    stdin: str = "",
    check: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    done = subprocess.run(
        args,
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env or ENV,
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
    # cwd も一時リポジトリにする（--root を無視する古い exe が、本体に書かないように）
    return run([EXE, "--root", ROOT, *args], ROOT, stdin=stdin)


def hook_full(event: str, tool: str, cwd: str, **tool_input) -> tuple[int, str, str]:
    """hook を 1 回打つ。(終了コード, permissionDecision, 理由か追加の文脈)。"""
    payload = {
        "hook_event_name": event,
        "tool_name": tool,
        "cwd": cwd,
        "session_id": "probe",
        "tool_input": tool_input,
    }
    done = exe(stdin=json.dumps(payload))
    if not done.stdout.strip():
        return done.returncode, "", ""
    try:
        out = json.loads(done.stdout).get("hookSpecificOutput", {})
    except ValueError:
        return done.returncode, "", done.stdout
    return (
        done.returncode,
        out.get("permissionDecision") or "",
        out.get("permissionDecisionReason") or out.get("additionalContext") or "",
    )


def hook(event: str, tool: str, cwd: str, **tool_input) -> str:
    return hook_full(event, tool, cwd, **tool_input)[2]


def agent_gate(cwd: str) -> tuple[bool, str]:
    """子を起こす Agent が止められているか。(止められている, 詳細)。

    止められている = exe が正常終了し、permissionDecision が deny で、
    理由に DENY_PHASE_REVIEW がある。
    """
    rc, decision, reason = hook_full("PreToolUse", "Agent", cwd, description="次の子")
    blocked = rc == 0 and decision == "deny" and "DENY_PHASE_REVIEW" in reason
    return blocked, f"rc={rc} decision={decision!r} reason={reason[:60]!r}"


def sh(
    script: str, cwd: str, *args: str, stdin: str = "", env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    return run(["sh", script, *args], cwd, stdin=stdin, env=env)


def api(
    method: str,
    path: str,
    token: str = "",
    payload: dict | None = None,
    tag: str = "",
) -> tuple[int, object]:
    """人間役の REST。生の応答を OUT に残す。再送は GET だけ（POST を送り直すと二重に作る）。"""
    global API_SEQ
    API_SEQ += 1
    token = token or TOKEN
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "ccnavi-probe",
        },
    )
    status, raw = 0, ""
    for attempt in range(3 if method == "GET" else 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                status, raw = res.status, res.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read().decode("utf-8", "replace")
            break
        except (TimeoutError, OSError) as exc:
            LOG.write(f"\nAPI {method} {path} 失敗 ({exc}) {attempt + 1} 回目\n")
            LOG.flush()
            status, raw = 0, json.dumps({"error": str(exc)})
            time.sleep(5)
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        body = raw
    slug = tag or path.strip("/").replace("/", "_").replace("?", "_")[:60]
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


def graphql(query: str, variables: dict, token: str = "", tag: str = "") -> dict:
    status, body = api(
        "POST", "/graphql", token, {"query": query, "variables": variables}, tag=tag or "graphql"
    )
    return body if isinstance(body, dict) else {"status": status, "body": body}


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
        "  実物の GitHub に適用する実測。",
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
    return "\n".join(lines)


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
    if not git(tree, "status", "--porcelain").strip():
        return  # 取り込み済みの親子では ccnavi が自分でコミットしている
    git(tree, "commit", "--quiet", "-m", msg)


def pr_of(branch: str) -> dict | None:
    _, found = api(
        "GET",
        f"/repos/{REPO}/pulls?state=all&head={OWNER}:{branch}",
        tag=f"pr-list-{branch}",
    )
    return found[0] if isinstance(found, list) and found else None


def comments_of(number: int) -> list[dict]:
    _, found = api(
        "GET",
        f"/repos/{REPO}/issues/{number}/comments?per_page=100",
        tag=f"comments-{number}",
    )
    return found if isinstance(found, list) else []


def has_marker(comments: list[dict], marker: str) -> bool:
    return any(c.get("body", "").startswith(marker) for c in comments)


def threads_of(number: int, tag: str = "threads") -> list[dict]:
    """人間役が GraphQL で直に読むスレッド。sh の読みと突き合わせる。"""
    res = graphql(
        "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n)"
        "{reviewThreads(first:100){nodes{id isResolved isOutdated comments(first:1)"
        "{nodes{url path line originalLine body createdAt}}}}}}}",
        {"o": OWNER, "r": REPO.split("/")[1], "n": number},
        tag=tag,
    )
    try:
        return res["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    except (KeyError, TypeError):
        return []


def fetched_json(proc: subprocess.CompletedProcess) -> dict:
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {}


def main() -> int:
    say(f"== 実測の置き場: {OUT}")
    status, me = api("GET", "/user", tag="user")
    record(
        "API にトークンで入れる",
        status == 200,
        str(me.get("login") if isinstance(me, dict) else me),
    )
    if status != 200:
        return finish()
    status, repo = api("GET", f"/repos/{REPO}", tag="repo")
    record(
        "実験場に書ける",
        status == 200 and bool(repo.get("permissions", {}).get("push")),
        f"{REPO} private={repo.get('private') if isinstance(repo, dict) else '?'}",
    )
    if status != 200:
        return finish()
    reviewer = REVIEWER_TOKEN or TOKEN
    if REVIEWER_TOKEN:
        _, rme = api("GET", "/user", REVIEWER_TOKEN, tag="reviewer-user")
        self_review = rme.get("login") == me.get("login")
    else:
        self_review = True
    say(f"人間役は {'同じアカウント' if self_review else '別のアカウント'}")

    # ---- 0. ユーザが用意するもの: 課題と、統合先のブランチ
    status, issue = api(
        "POST",
        f"/repos/{REPO}/issues",
        payload={"title": "ccnavi 実測 (使い捨て)", "body": "実測の元になる課題。閉じて終わり。"},
        tag="issue-create",
    )
    record(
        "課題を立てる", status == 201, f"#{issue.get('number') if isinstance(issue, dict) else '?'}"
    )
    if status != 201:
        return finish()
    issue_no = issue["number"]
    MADE_ISSUES.append(issue_no)

    os.makedirs(os.path.join(ROOT, ".claude"), exist_ok=True)
    git(ROOT, "init", "--quiet", "-b", "main")
    write(os.path.join(ROOT, "src", "keep.py"), "print(1)\n")
    write(os.path.join(ROOT, ".gitignore"), ".claude/\nwip/tmp/\n")
    commit_all(ROOT, "init")
    origin = f"https://github.com/{REPO}.git"
    git(ROOT, "remote", "add", "origin", origin)
    # 認証は git の設定側に置く。空文字で system / global の helper（GCM）を外し、
    # 環境変数 GITHUB_TOKEN を返す helper を足す。トークンをファイルに書かない（置き去りになる）。
    git(ROOT, "config", "--add", "credential.helper", "")
    git(
        ROOT,
        "config",
        "--add",
        "credential.helper",
        '!f() { echo username=x-access-token; echo "password=$GITHUB_TOKEN"; }; f',
    )
    # 親のブランチ名はチケットの識別子で決まる（i0001・i0002）。実験場に同じ名前が残っていると
    # 上書きや削除で他の人のものに触れるので、あれば止まる
    for name in ("i0001", "i0002"):
        st, _ = api("GET", f"/repos/{REPO}/branches/{name}", tag=f"precheck-{name}")
        if st != 404:
            record(f"実験場に {name} ブランチが無い", False, f"status={st}。手で片付けてから")
            return finish()
    # 統合先は main ではなく使い捨てのブランチにする。履歴は無関係でよい
    pushed = run(["git", "push", "--quiet", "origin", f"main:refs/heads/{BASE}"], ROOT)
    if pushed.returncode == 0:
        MADE_BRANCHES.append(BASE)
    record(
        "統合先の使い捨てブランチを push（認証画面なし）",
        pushed.returncode == 0,
        redact(pushed.stderr.strip())[:200],
    )
    if pushed.returncode != 0:
        return finish()
    write(os.path.join(ROOT, ".ccnavi", "config", "common", "rules.yml"), json.dumps(RULES))
    # sh はワークスペースルートを cwd から上へ探す（.ccnavi/scripts/ccnavi-common.sh を持つ親）。
    # 一時リポジトリにも scripts を置く
    shutil.copytree(
        os.path.join(ROOT_OF_CCNAVI, ".ccnavi", "scripts"),
        os.path.join(ROOT, ".ccnavi", "scripts"),
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
    commit_all(parent_tree, "tickets")
    approved = exe("user", "agree", stdin="y\n")
    record("--agree（親 1 子 2）", approved.returncode == 0, redact(approved.stderr.strip())[:200])

    # 子より先に親に着手する（親が doing/ に無いと子は始められない）
    started = sh(TICKET_SH, parent_tree, "start", "i0001")
    record("ticket start i0001（親）", started.returncode == 0, started.stderr.strip()[:200])
    commit_all(parent_tree, "start parent")

    for child in ("i0001-01-01", "i0001-01-02"):
        tree = worktree(child, "i0001")
        started = sh(TICKET_SH, parent_tree, "start", child)
        record(f"ticket start {child}", started.returncode == 0, started.stderr.strip()[:200])
        write(os.path.join(tree, "src", child[-1], "work.py"), f"# {child}\nprint({child[-1]})\n")
        commit_all(tree, f"{child}: work")
    commit_all(parent_tree, "start")

    for child in ("i0001-01-01", "i0001-01-02"):
        done = sh(TICKET_SH, parent_tree, "finish", child)
        record(f"ticket finish {child}", done.returncode == 0, done.stderr.strip()[:200])
    commit_all(parent_tree, "finish")
    for child in ("i0001-01-01", "i0001-01-02"):
        git(parent_tree, "merge", "--quiet", "--no-edit", child)

    said = hook("PostToolUse", "Bash", parent_tree, command="ls")
    record(
        "フェーズ 1 の終わりの告知",
        "フェーズ 1（作業） が終わりました" in said,
        said[:120].replace("\n", " "),
    )
    blocked, detail = agent_gate(parent_tree)
    record("レビュー待ちが Agent を止める", blocked, detail)

    # 依頼には ELI5 の HTML が要る。wip/eli5/ の下に置いてコミットする
    # （ready の前に wip/ ごと消える）
    ELI5 = "wip/eli5/phase-1.html"
    write(
        os.path.join(parent_tree, ELI5),
        "<!doctype html><meta charset=utf-8><title>eli5</title><p>src/a と src/b を足した。</p>\n",
    )
    commit_all(parent_tree, "eli5")

    body = write(os.path.join(OUT, "request-1.md"), "見てほしい点\n\n- src/a と src/b を足した\n")
    early = sh(
        REVIEW_SH, parent_tree, "request", "--phase", "1", "--body-file", body, "--eli5", ELI5
    )
    record(
        "push 前の request は前提で止まる",
        early.returncode != 0 and "親ブランチの HEAD が push されていない" in early.stderr,
        early.stderr.strip()[-120:],
    )

    pushed = sh(GIT_SH, parent_tree, "push", "-u", "origin", "i0001")
    if pushed.returncode == 0:
        MADE_BRANCHES.append("i0001")
    record(
        "親の push（ラッパースクリプト経由、認証画面なし）",
        pushed.returncode == 0,
        redact(pushed.stdout + pushed.stderr).strip()[:120],
    )
    status, _ = api("GET", f"/repos/{REPO}/branches/i0001", tag="branch-i0001")
    record("GitHub に i0001 ブランチがある", status == 200)

    # ---- 2. origin の読み方と request
    origin_seen = sh(REVIEW_SH, parent_tree, "origin")
    say("origin の読み方:\n" + redact(origin_seen.stdout + origin_seen.stderr))
    record(
        "origin をホストとパスに読む",
        f"path={REPO}" in origin_seen.stdout
        and "api_base=https://api.github.com" in origin_seen.stdout
        and "kind=github" in origin_seen.stdout,
    )
    record("送り先は gh", "transport=gh" in origin_seen.stdout, origin_seen.stdout[-60:])

    requested = sh(
        REVIEW_SH, parent_tree, "request", "--phase", "1", "--body-file", body, "--eli5", ELI5
    )
    say("request:\n" + redact(requested.stdout + requested.stderr))
    record("request が通る（PR を作って投稿してマーカー）", requested.returncode == 0)
    pr = pr_of("i0001")
    if pr:
        MADE_PRS.append(pr["number"])
    record(
        "PR が Draft で作られている",
        bool(pr) and bool(pr.get("draft")),
        f"{pr and pr.get('html_url')} title={pr and pr.get('title')}",
    )
    record(
        "PR の宛先が統合先",
        bool(pr) and pr["base"]["ref"] == BASE,
    )
    record(
        "PR の本文に Closes #課題 がある",
        bool(pr) and f"Closes #{issue_no}" in (pr.get("body") or ""),
    )
    if not pr:
        return finish()
    num = pr["number"]
    record(
        "依頼のコメントに ccnavi:request のマーカーがある",
        has_marker(comments_of(num), "<!-- ccnavi:request i0001:1 "),
    )
    again = sh(
        REVIEW_SH, parent_tree, "request", "--phase", "1", "--body-file", body, "--eli5", ELI5
    )
    record(
        "2 度目の request は依頼済みで止まる", again.returncode != 0 and "依頼済み" in again.stderr
    )
    fetched = sh(REVIEW_SH, parent_tree, "fetch")
    write(os.path.join(OUT, "fetch-1-empty.json"), fetched.stdout)
    f1 = fetched_json(fetched)
    record(
        "fetch が JSON を返す（スレッドもレビューも空）",
        fetched.returncode == 0
        and f1.get("host") == "github"
        and f1.get("mr", {}).get("number") == num
        and f1.get("threads") == []
        and f1.get("reviews") == [],
        fetched.stderr.strip()[:200],
    )

    # ---- 3. レビュアーが指摘 → confirm → 解決 → confirm
    # 差分の行に付くレビューコメント 2 件を、COMMENTED のレビューとして出す
    status, rv = api(
        "POST",
        f"/repos/{REPO}/pulls/{num}/reviews",
        reviewer,
        {
            "event": "COMMENT",
            "body": "全体の所感",
            "comments": [
                {
                    "path": "src/1/work.py",
                    "line": 1,
                    "side": "RIGHT",
                    "body": "ここは名前を変えてほしい",
                },
                {"path": "src/2/work.py", "line": 2, "side": "RIGHT", "body": "ここも"},
            ],
        },
        tag="review-create",
    )
    record(
        "レビュアーが行コメント 2 件つきの COMMENTED レビューを出す",
        status == 200 and rv.get("state") == "COMMENTED",
        f"status={status}",
    )
    human = threads_of(num, "threads-after-review")
    record(
        "GraphQL でスレッドが 2 本読める",
        len(human) == 2 and not any(t["isResolved"] for t in human),
    )

    fetched = sh(REVIEW_SH, parent_tree, "fetch")
    write(os.path.join(OUT, "fetch-2-unresolved.json"), fetched.stdout)
    f2 = fetched_json(fetched)
    th = f2.get("threads", [])
    record(
        "sh が reviewThreads を読む（2 本・未解決・path と line と URL）",
        len(th) == 2
        and all(t["resolved"] is False for t in th)
        and {(t["path"], t["line"]) for t in th} == {("src/1/work.py", 1), ("src/2/work.py", 2)}
        and all(
            t["url"].startswith("https://github.com/") and "#discussion_r" in t["url"] for t in th
        )
        and all(t["created_at"].endswith("Z") for t in th),
        json.dumps(th, ensure_ascii=False)[:300],
    )
    rvs = f2.get("reviews", [])
    record(
        "sh がレビューを読む（COMMENTED 1 件。author は数の id）",
        len(rvs) == 1 and rvs[0]["state"] == "COMMENTED" and rvs[0]["author"].isdigit(),
        json.dumps(rvs, ensure_ascii=False)[:300],
    )
    checked = sh(REVIEW_SH, parent_tree, "confirm", "--phase", "1")
    record(
        "未解決があると confirm は止まる",
        checked.returncode != 0 and "未解決" in checked.stderr,
        checked.stderr.strip()[:200],
    )
    blocked, detail = agent_gate(parent_tree)
    record("その間は止まったまま", blocked, detail)

    # curl の経路（gh を外す）でも同じに読めるか
    curl_env = env_without_gh()
    seen = sh(REVIEW_SH, parent_tree, "origin", env=curl_env)
    record("gh を外すと curl の経路になる", "transport=curl" in seen.stdout, seen.stdout[-40:])
    via_curl = sh(REVIEW_SH, parent_tree, "fetch", env=curl_env)
    write(os.path.join(OUT, "fetch-2-unresolved-curl.json"), via_curl.stdout)
    c2 = fetched_json(via_curl)
    same = {k: c2.get(k) for k in ("host", "mr", "threads", "reviews")} == {
        k: f2.get(k) for k in ("host", "mr", "threads", "reviews")
    }
    record(
        "curl の経路の fetch が gh の経路と同じ",
        via_curl.returncode == 0 and same,
        via_curl.stderr.strip()[:200],
    )

    if not self_review:
        status, _ = api(
            "POST",
            f"/repos/{REPO}/pulls/{num}/reviews",
            reviewer,
            {"event": "REQUEST_CHANGES", "body": "直してから"},
            tag="request-changes",
        )
        record("別アカウントが変更要求を出す", status == 200, f"status={status}")
        fetched = sh(REVIEW_SH, parent_tree, "fetch")
        write(os.path.join(OUT, "fetch-3-changes-requested.json"), fetched.stdout)
        checked = sh(REVIEW_SH, parent_tree, "confirm", "--phase", "1")
        record(
            "変更要求が立っていると confirm は止まる",
            checked.returncode != 0 and "変更要求" in checked.stderr,
            checked.stderr.strip()[:200],
        )
    else:
        say("自分の PR には変更要求も approve も出せないので、その段は走らない（docstring 参照）")

    # 差分の外へのレビューは 422 で拒まれ、レビューごと作られない
    # （crit push が差分外の行に付けた指摘の行き先）
    _, before = api(
        "GET", f"/repos/{REPO}/pulls/{num}/reviews?per_page=100", tag="reviews-before-422"
    )
    n_before = len(before) if isinstance(before, list) else -1
    for label, comment, expect in (
        ("差分外の行", {"path": "src/1/work.py", "line": 99}, "Line could not be resolved"),
        ("差分に無いファイル", {"path": "src/keep.py", "line": 1}, "Path could not be resolved"),
    ):
        st, rej = api(
            "POST",
            f"/repos/{REPO}/pulls/{num}/reviews",
            reviewer,
            {
                "event": "COMMENT",
                "comments": [{**comment, "side": "RIGHT", "body": "範囲外"}],
            },
            tag="review-outside-diff",
        )
        errors = " ".join(rej.get("errors", [])) if isinstance(rej, dict) else ""
        record(
            f"{label}へのレビューは 422 で拒まれる",
            st == 422 and expect in errors,
            f"{st} {errors}",
        )
    _, after = api(
        "GET", f"/repos/{REPO}/pulls/{num}/reviews?per_page=100", tag="reviews-after-422"
    )
    record(
        "拒まれたレビューは作られない（レビューの件数が増えない）",
        n_before >= 1 and isinstance(after, list) and len(after) == n_before,
        f"{n_before} -> {len(after) if isinstance(after, list) else '?'}",
    )

    # 1 本目を解決（GraphQL の resolveReviewThread）。2 本目は残す
    first = next(t for t in human if t["comments"]["nodes"][0]["path"] == "src/1/work.py")
    res = graphql(
        "mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{id isResolved}}}",
        {"id": first["id"]},
        reviewer,
        tag="thread-resolve",
    )
    record(
        "レビュアーが 1 本目のスレッドを解決する",
        res.get("data", {}).get("resolveReviewThread", {}).get("thread", {}).get("isResolved")
        is True,
    )
    fetched = sh(REVIEW_SH, parent_tree, "fetch")
    th = fetched_json(fetched).get("threads", [])
    record(
        "sh が解決済みを読み分ける（1 本解決・1 本未解決）",
        sorted(t["resolved"] for t in th) == [False, True],
        json.dumps([t["resolved"] for t in th]),
    )
    checked = sh(REVIEW_SH, parent_tree, "confirm", "--phase", "1")
    record(
        "1 本残っていれば confirm は止まる",
        checked.returncode != 0 and "未解決" in checked.stderr,
        checked.stderr.strip()[:200],
    )
    second = next(t for t in human if t["id"] != first["id"])
    res2 = graphql(
        "mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{id isResolved}}}",
        {"id": second["id"]},
        reviewer,
        tag="thread-resolve-2",
    )
    record(
        "レビュアーが 2 本目のスレッドを解決する",
        res2.get("data", {}).get("resolveReviewThread", {}).get("thread", {}).get("isResolved")
        is True,
    )
    fetched = sh(REVIEW_SH, parent_tree, "fetch")
    write(os.path.join(OUT, "fetch-4-resolved.json"), fetched.stdout)
    checked = sh(REVIEW_SH, parent_tree, "confirm", "--phase", "1")
    record(
        "全部解決の後は confirm が通る",
        checked.returncode == 0,
        (checked.stdout + checked.stderr).strip()[:200],
    )
    rc, decision, reason = hook_full("PreToolUse", "Agent", parent_tree, description="次の子")
    record(
        "止まっていたのが解ける",
        rc == 0 and decision != "deny" and "DENY_PHASE_REVIEW" not in reason,
        f"rc={rc} decision={decision!r}",
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
        has_marker(comments_of(num), "<!-- ccnavi:comment -->"),
    )

    # 3 本目のスレッド。decide で受け入れる
    status, rv2 = api(
        "POST",
        f"/repos/{REPO}/pulls/{num}/reviews",
        reviewer,
        {
            "event": "COMMENT",
            "body": "もう 1 件",
            "comments": [
                {"path": "src/1/work.py", "line": 2, "side": "RIGHT", "body": "typo。次でよい"}
            ],
        },
        tag="review-create-2",
    )
    record("レビュアーが 3 本目のスレッドを立てる", status == 200)
    third_ids = [t["id"] for t in threads_of(num, "threads-3") if not t["isResolved"]]
    # 読む側（ccnavi-review.sh decide <N> --preview）で threads と digest を取り、
    # 書く側（ユーザ専用の ccnavi-user-decide.sh）は人間役としてこの probe が打つ
    preview = sh(REVIEW_SH, parent_tree, "decide", "1", "--preview")
    write(os.path.join(OUT, "decide-preview.json"), preview.stdout)
    try:
        shown = json.loads(preview.stdout)
    except ValueError:
        shown = {}
    threads = shown.get("threads") if isinstance(shown, dict) else None
    record(
        "decide --preview が threads と digest を JSON で返す",
        preview.returncode == 0 and bool(threads) and bool(shown.get("digest")),
        redact(preview.stderr)[-160:],
    )
    choices = json.dumps(
        {t["key"]: "keep" for t in threads or [] if isinstance(t, dict) and "key" in t},
        ensure_ascii=False,
    )
    accepted = sh(
        DECIDE_SH, parent_tree, "1", "--choices", choices, "--digest", str(shown.get("digest", ""))
    )
    say("decide:\n" + redact(accepted.stdout + accepted.stderr))
    record(
        "decide で未解決を受け入れる",
        accepted.returncode == 0 and "対応しない 1 件" in accepted.stdout,
        accepted.stderr.strip()[:200],
    )
    acc = [c for c in comments_of(num) if c.get("body", "").startswith("<!-- ccnavi:decide -->")]
    record(
        "受け入れのコメントが PR に書き込まれる",
        bool(acc) and "#discussion_r" in acc[-1].get("body", ""),
        (acc[-1]["body"][:200] if acc else ""),
    )
    checked = sh(REVIEW_SH, parent_tree, "confirm", "--phase", "1")
    record(
        "フェーズが決着済みなら confirm は「レビュー済み」で止まる",
        checked.returncode != 0 and "レビュー済み" in checked.stderr,
        checked.stderr.strip()[-120:],
    )
    say(f"3 本目のスレッド: {third_ids}")

    # ---- 4. 親を閉じて ready（Draft を外す）
    closed = sh(TICKET_SH, parent_tree, "finish", "i0001")
    record("親の done", closed.returncode == 0, (closed.stdout + closed.stderr).strip()[:200])
    commit_all(parent_tree, "状態の移動")
    ready = sh(REVIEW_SH, parent_tree, "ready")
    say("ready:\n" + redact(ready.stdout + ready.stderr))
    record(
        "ready が通る（wip/ の追跡済みのファイルも消える）",
        ready.returncode == 0 and "wip/eli5/phase-1.html" in ready.stdout,
    )
    pr = pr_of("i0001")
    record(
        "PR の Draft が外れ、題から Draft: が消える",
        bool(pr) and not pr.get("draft") and not pr.get("title", "").lower().startswith("draft"),
        f"title={pr and pr.get('title')} draft={pr and pr.get('draft')}",
    )
    record("ready のコメントが PR にある", has_marker(comments_of(num), "<!-- ccnavi:ready -->"))

    # コメントを付けた行を書き換えるコミットを push すると、スレッドは古く（outdated）なり、
    # GraphQL の line は null になる。sh は 0 に読む。
    # push すると依頼の後に親の HEAD が動くので、依頼・confirm の段が全部済んだ ready の後でやる
    write(os.path.join(parent_tree, "src", "2", "work.py"), "# rewritten\n")
    commit_all(parent_tree, "コメントを付けた行を書き換える")
    moved = sh(GIT_SH, parent_tree, "push")
    record(
        "書き換えのコミットを push",
        moved.returncode == 0,
        redact(moved.stdout + moved.stderr)[:120],
    )
    outdated = {}
    for _ in range(8):  # 古くなった印が付くまで少しかかる
        nodes = threads_of(num, "threads-outdated")
        outdated = next(
            (t for t in nodes if t["comments"]["nodes"][0]["path"] == "src/2/work.py"), {}
        )
        if outdated.get("isOutdated"):
            break
        time.sleep(3)
    node = outdated.get("comments", {}).get("nodes", [{}])[0]
    record(
        "GraphQL は古いスレッドの line を null で返す",
        bool(outdated.get("isOutdated")) and node.get("line") is None,
        f"isOutdated={outdated.get('isOutdated')} line={node.get('line')} "
        f"originalLine={node.get('originalLine')}",
    )
    fetched = sh(REVIEW_SH, parent_tree, "fetch")
    write(os.path.join(OUT, "fetch-3-outdated.json"), fetched.stdout)
    old = [t for t in fetched_json(fetched).get("threads", []) if t["path"] == "src/2/work.py"]
    record(
        "sh は古いスレッドを line 0・解決済みのまま読む",
        fetched.returncode == 0
        and len(old) == 1
        and old[0]["line"] == 0
        and old[0]["resolved"] is True,
        json.dumps(old, ensure_ascii=False)[:200],
    )

    # ---- 5. 別の親をユーザが早めに閉じる（close-early）
    parent2 = worktree("i0002", "main")
    propose(parent2, "i0002", allow=("src/*", "wip/*"), title="途中で早めに閉じる親")
    propose(
        parent2,
        "i0002-01-01",
        parent="i0002",
        phase=1,
        allow=("src/c/*",),
        review=True,
        title="c を書く",
    )
    write(os.path.join(parent2, "src", "c.py"), "print('c')\n")
    commit_all(parent2, "tickets")
    approved = exe("user", "agree", stdin="y\n")
    record("--agree（i0002）", approved.returncode == 0, redact(approved.stderr.strip())[:200])
    pushed2 = sh(GIT_SH, parent2, "push", "-u", "origin", "i0002")
    if pushed2.returncode == 0:
        MADE_BRANCHES.append("i0002")
    record("i0002 の push", pushed2.returncode == 0)
    status, pr2 = api(
        "POST",
        f"/repos/{REPO}/pulls",
        payload={
            "head": "i0002",
            "base": BASE,
            "title": "Draft: 途中で早めに閉じる親",
            "draft": True,
        },
        tag="pr-create-i0002",
    )
    record("ユーザが i0002 の PR を作る", status == 201)
    if status == 201:
        MADE_PRS.append(pr2["number"])
        api(
            "POST",
            f"/repos/{REPO}/pulls/{pr2['number']}/reviews",
            reviewer,
            {
                "event": "COMMENT",
                "comments": [{"path": "src/c.py", "line": 1, "side": "RIGHT", "body": "残る指摘"}],
            },
            tag="review-create-3",
        )
        wrapped = sh(CLOSE_EARLY_SH, parent2, "--reason", "実測はここまで", stdin="y\n")
        say("close-early:\n" + redact(wrapped.stdout + wrapped.stderr))
        record("close-early が通る", wrapped.returncode == 0)
        # 作った issue は出力の「残りを #N に書き出した」から引く
        # （一覧は作った直後だと載らないことがある）
        found = re.search(r"残りを #(\d+) に書き出した", wrapped.stdout)
        if found:
            MADE_ISSUES.append(int(found.group(1)))
            _, made = api("GET", f"/repos/{REPO}/issues/{found.group(1)}", tag="issue-close-early")
        else:
            made = {}
        record(
            "残りを書き出す issue ができる",
            bool(found) and made.get("state") == "open" and "残り" in made.get("title", ""),
            f"{made.get('html_url', '無し')}",
        )
        record(
            "未着手の子が取り消されて done/ へ動く",
            os.path.exists(os.path.join(parent2, ".ccnavi", "approved", "done", "i0002-01-01.md")),
        )
        record(
            "close-early のコメントが PR にある",
            has_marker(comments_of(pr2["number"]), "<!-- ccnavi:close-early -->"),
        )

    # ---- 6. URL にトークンを埋めた origin でも読めて、出力に漏れない
    # URL は引数にも .git/config にも置かない（ps に見える・落ちると残る）。
    # git の環境変数での設定で、この 1 回の呼び出しの origin だけを差し替える
    scheme, _, hostpath = origin.partition("://")
    url_env = dict(ENV)
    url_env.update(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "remote.origin.url",
            "GIT_CONFIG_VALUE_0": f"{scheme}://x-access-token:{TOKEN}@{hostpath}",
        }
    )
    seen = sh(REVIEW_SH, parent2, "origin", env=url_env)
    say("トークン入り URL の origin の読み方:\n" + redact(seen.stdout + seen.stderr))
    record(
        "トークン入り URL でも host と api_base が正しい",
        "api_base=https://api.github.com" in seen.stdout and f"path={REPO}" in seen.stdout,
    )
    leaked = TOKEN in seen.stdout + seen.stderr
    via_url = sh(REVIEW_SH, parent2, "fetch", env=url_env)
    leaked = leaked or TOKEN in via_url.stdout + via_url.stderr
    record("トークン入り URL のトークンが origin / fetch の出力に漏れない", not leaked)
    return finish()


CLEANUP_FAILED: list[str] = []


def cleanup() -> None:
    """作った PR と issue を閉じ（public なので履歴には残る）、ブランチを消す。実験場の中だけ。

    各呼び出しの status を見て、失敗は CLEANUP_FAILED に挙げる。
    """
    for number in MADE_PRS:
        st, _ = api(
            "PATCH", f"/repos/{REPO}/pulls/{number}", payload={"state": "closed"}, tag="cleanup-pr"
        )
        say(f"片付け: PR #{number} を閉じる -> {st}")
        if st != 200:
            CLEANUP_FAILED.append(f"PR #{number} ({st})")
    for branch in MADE_BRANCHES:
        st, _ = api("DELETE", f"/repos/{REPO}/git/refs/heads/{branch}", tag="cleanup-branch")
        say(f"片付け: ブランチ {branch} を消す -> {st}")
        if st not in (204, 422):  # 422 は既に無い
            CLEANUP_FAILED.append(f"ブランチ {branch} ({st})")
    for number in MADE_ISSUES:
        st, _ = api(
            "PATCH",
            f"/repos/{REPO}/issues/{number}",
            payload={"state": "closed", "state_reason": "not_planned"},
            tag="cleanup-issue",
        )
        say(f"片付け: issue #{number} を閉じる -> {st}")
        if st != 200:
            CLEANUP_FAILED.append(f"issue #{number} ({st})")
    if CLEANUP_FAILED:
        say(f"片付けに失敗したもの（手で片付ける）: {', '.join(CLEANUP_FAILED)}")
    else:
        say(f"片付け: PR {MADE_PRS} と issue {MADE_ISSUES} を閉じた")
        say(f"片付け: ブランチ {MADE_BRANCHES} を削除した")


def finish() -> int:
    try:
        cleanup()
    except Exception as exc:  # noqa: BLE001 - 片付けの失敗は結果に出して続ける
        say(f"片付けに失敗: {exc}。{MADE_PRS} {MADE_BRANCHES} {MADE_ISSUES} を手で片付ける")
    say("\n==== まとめ ====")
    for name, ok, _ in RESULTS:
        say(f"{'PASS' if ok else 'FAIL'}  {name}")
    failed = [r for r in RESULTS if not r[1]]
    say(f"\n{len(RESULTS) - len(failed)} / {len(RESULTS)} PASS。置き場: {OUT}")
    LOG.close()
    return 1 if failed or CLEANUP_FAILED else 0


def guarded() -> int:
    """途中で落ちても、作った PR・ブランチ・課題は片付ける。"""
    try:
        return main()
    except BaseException:
        try:
            cleanup()
        except Exception as exc:  # noqa: BLE001
            say(f"片付けに失敗: {exc}。{MADE_PRS} {MADE_BRANCHES} {MADE_ISSUES} を手で片付ける")
        raise


if __name__ == "__main__":
    sys.exit(guarded())
