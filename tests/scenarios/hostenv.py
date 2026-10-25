"""受入シナリオの `setup.host`（評価の相手）を用意して片付ける。
決まりは docs/42_claude/acceptance-scenarios.md。

- `bare`: 対象の origin への通信を、一時ディレクトリの bare に向ける。
  git の代役が URL をホストの形で答える。
  ホストの API は gh / glab が失敗し、curl は空の routes で 404（「確かめられなかった」）
- `fake-gitlab`: `bare` に加え、`tests/fake_gitlab.py` を起こす（curl の代役は置かない）
- `docker-gitlab`: Docker の本物の GitLab（tests/manual/gitlab/docker_gitlab.py の `up`）に
  使い捨てのプロジェクトを作り、`main` を push する。
  origin への通信はそのプロジェクトへ向く。回したあとに消す
- `real`: 何もしない

実行器（runner.py）から呼ぶ。状態は JSON にできる辞書（`prepare` の戻り値）で、state.json に残す。
起こした模擬のスレッドは、この辞書の `dir` をキーにしたプロセス内の表で持つ。
"""

import os
import re
import shutil
import sys
import tempfile
import time
import urllib.parse
from datetime import datetime

from ccnavi.infra import pathcase
from ccnavi.records import diaglog
from tests import fake_gitlab, host_stub

HOSTS = ("bare", "fake-gitlab", "docker-gitlab", "real")
DOCKER = "docker-gitlab"
DEFAULT_HOST = "real"
LOG_NAME = "scenario-runner"
# 本物のホストに出る資格情報。claude の環境から外す（偽のトークンだけを渡す）
TOKEN_VAR = re.compile(r"^(GH|GITHUB|GLAB|GITLAB|CCNAVI_PROBE)_.*TOKEN$", re.IGNORECASE)
REMOTE_REFS = "refs/remotes/origin"

GITLAB_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "manual", "gitlab")
DEVELOPER = 30
DELETE_WAIT_SECONDS = 60
# git が `credential.<URL>.helper` で読む。値は環境変数 GITLAB_TOKEN で、引数には入れない
CRED_HELPER = '!f() { echo username=root; echo "password=$GITLAB_TOKEN"; }; f'

_SERVERS = {}
_sleep = time.sleep


def is_fake(host):
    return host in ("bare", "fake-gitlab")


def uses_stub_git(host):
    """origin への通信を git の代役で向け直す相手。is_fake に Docker の GitLab を足したもの。"""
    return is_fake(host) or host == DOCKER


def _connect():
    """(Client, エージェント役のトークン, GitLab の根の URL, トークンのファイルの値)。
    トークンは呼ぶたびにファイルから読む。
    tests/manual/gitlab は package ではなく、docker_gitlab.py は同じ場所の gitlab_api を import
    するので、sys.path の末尾に足して読む（同名の tests.fake_gitlab を隠さないため末尾）。"""
    tools = os.path.normpath(GITLAB_TOOLS)
    if tools not in sys.path:
        sys.path.append(tools)
    import docker_gitlab
    import gitlab_api

    values = docker_gitlab.read_env_file(docker_gitlab.tokens_path())
    token = values.get("GITLAB_TOKEN")
    if not token:
        raise RuntimeError(
            f"トークンのファイル {docker_gitlab.tokens_path()} に GITLAB_TOKEN が無い"
        )
    return gitlab_api.Client(docker_gitlab.BASE), token, docker_gitlab.BASE, values


def agent_token():
    return _connect()[1]


def check_docker_gitlab():
    """Docker の GitLab が応答するかを HTTP で見る。応答しなければ RuntimeError。何も作らない。"""
    client, token, base, _ = _connect()
    status, body = client.version(token)
    if status != 200:
        raise RuntimeError(f"{base} の GitLab が応答しない（HTTP {status}）")
    return body.get("version", "") if isinstance(body, dict) else ""


def project_name(scenario_id, now=None, rand=None):
    """GitLab のプロジェクト名。大文字小文字違いを別物にしないよう、ID は fold した綴りにする。
    接頭辞 `sc-<ID>-` で、残ったものを一覧して消せる。"""
    now = now or datetime.now()
    rand = rand or os.urandom(2).hex()
    ident = re.sub(r"[^a-z0-9]+", "-", pathcase.fold(str(scenario_id))).strip("-")
    return f"sc-{ident}-{now:%Y%m%d%H%M%S}-{rand}"


def default_state():
    return {"project": "ns/proj", "seq": 100, "issues": [], "mrs": []}


def validate(path, setup):
    """setup.host と setup.host_state の形を確かめる。誤りは ValueError。"""
    host = setup.get("host", DEFAULT_HOST)
    if host not in HOSTS:
        raise ValueError(f"{path}: setup.host は {' / '.join(HOSTS)} のどれかです: {host!r}")
    if uses_stub_git(host) and setup.get("issue"):
        raise ValueError(
            f"{path}: setup.issue と setup.host: {host} は併用できません"
            "（issue は runner が gh / glab で作るので、代役の PATH では作れない）。"
            "fake-gitlab で issue が要るときは setup.host_state に書く。"
            "docker-gitlab は issue を API で作る"
        )
    if "host_state" in setup:
        state = setup["host_state"]
        if host != "fake-gitlab":
            raise ValueError(
                f"{path}: setup.host_state は setup.host: fake-gitlab のときだけ書けます"
            )
        if not isinstance(state, dict):
            raise ValueError(f"{path}: setup.host_state は dict です")
        project = state.get("project", default_state()["project"])
        if not isinstance(project, str) or not re.fullmatch(r"[^/\s]+(/[^/\s]+)+", project):
            raise ValueError(f"{path}: setup.host_state.project は <namespace>/<project> の形です")


def _log(root):
    return diaglog.get(LOG_NAME, str(root))


def _real_origin(top, git):
    # `remote get-url` は利用者の url.*.insteadOf で書き換えた後を返す。insteadOf は連鎖しないので、
    # bare へ向ける -c には設定の生の値を使う。
    r = git(["config", "--get", "remote.origin.url"], cwd=top)
    url = r.stdout.strip() if r.returncode == 0 else ""
    if not url:
        raise RuntimeError(f"対象 {top} に origin が無い。bare の相手を作れない")
    return url


def _make_bare(top, bare, git):
    """対象の origin（refs/remotes/origin/*）と同じ内容の bare を作る。ネットワークには出ない。
    origin の remote-tracking ref が無ければ、対象のローカルブランチから作る。"""
    r = git(["init", "--bare", "-q", bare], cwd=top)
    if r.returncode:
        raise RuntimeError(f"bare を作れない: {r.stderr.strip()}")
    specs = [
        [f"+{REMOTE_REFS}/*:refs/heads/*", f"^{REMOTE_REFS}/HEAD"],
        ["+refs/heads/*:refs/heads/*"],
    ]
    for spec in specs:
        r = git(["fetch", "-q", "--no-tags", top, *spec, "+refs/tags/*:refs/tags/*"], cwd=bare)
        if r.returncode:
            raise RuntimeError(f"bare へ取り込めない: {r.stderr.strip()}")
        heads = git(["for-each-ref", "--format=%(refname)", "refs/heads"], cwd=bare).stdout
        if heads.split():
            break
    r = git(["symbolic-ref", "--short", f"{REMOTE_REFS}/HEAD"], cwd=top)
    name = r.stdout.strip().removeprefix("origin/") if r.returncode == 0 else ""
    if not name or git(["rev-parse", "-q", "--verify", f"refs/heads/{name}"], cwd=bare).returncode:
        name = "main"
    git(["symbolic-ref", "HEAD", f"refs/heads/{name}"], cwd=bare)


def _remote_refs(top, git):
    """対象の refs/remotes/origin/*（HEAD を除く）。回したあとに戻すために、回す前の値を残す。"""
    r = git(["for-each-ref", "--format=%(refname) %(objectname)", REMOTE_REFS], cwd=top)
    out = {}
    for line in r.stdout.splitlines():
        ref, sha = line.split()
        if ref != f"{REMOTE_REFS}/HEAD":
            out[ref] = sha
    return out


def _restore_remote_refs(top, before, git):
    """fetch・push で動いた refs/remotes/origin/* を、回す前の値に戻す。"""
    now = _remote_refs(top, git)
    cmds = [f"delete {ref}" for ref in now if ref not in before]
    cmds += [f"update {ref} {sha}" for ref, sha in before.items() if now.get(ref) != sha]
    if not cmds:
        return
    r = git(["update-ref", "--stdin"], cwd=top, input="\n".join(cmds) + "\n")
    if r.returncode:
        raise RuntimeError(r.stderr.strip())


def _make_docker_project(root, host, top, scenario_id, git):
    """Docker の GitLab に使い捨てのプロジェクトを作り、人間役をメンバーに加え、main を push する。
    識別子は作った直後に host へ入れる（以後の失敗でも teardown が消せる）。
    root の PAT は host に入れない（state.json に残る）。"""
    client, token, base, values = _connect()
    status, body = client.version(token)
    host["version"] = body.get("version", "") if status == 200 and isinstance(body, dict) else ""
    name = project_name(scenario_id)
    status, body = client.create_project(token, name)
    if status != 201 or not isinstance(body, dict) or "id" not in body:
        raise RuntimeError(f"プロジェクト {name} を作れない（HTTP {status}）")
    host.update(project_id=body["id"], project=body["path_with_namespace"], base=base)
    host["url"] = f"{base}/{host['project']}"
    host["bare"] = host["url"] + ".git"  # origin の通信先（git の代役が向け直す先）
    _log(root).info("プロジェクトを作った", project_id=host["project_id"], url=host["url"])
    reviewer = values.get("CCNAVI_PROBE_REVIEWER_ID")
    if not reviewer:
        raise RuntimeError("トークンのファイルに CCNAVI_PROBE_REVIEWER_ID が無い")
    status, _ = client.add_member(token, host["project_id"], int(reviewer), DEVELOPER)
    if status not in (200, 201):
        raise RuntimeError(f"人間役をメンバーに加えられない（HTTP {status}）")
    r = git(
        [*git_via(host), "push", "-q", "origin", "refs/heads/main:refs/heads/main"],
        cwd=top,
        env=git_env(host),
    )
    if r.returncode:
        raise RuntimeError(f"main を push できない: {r.stderr.strip()}")


def git_env(host):
    """runner 自身の git にだけ渡す環境。claude の環境や state.json には出さない。"""
    if host["kind"] != DOCKER:
        return {}
    return {"GITLAB_TOKEN": agent_token(), "GIT_TERMINAL_PROMPT": "0"}


def prepare(root, top, setup, git, scenario_id=""):
    """代役の置き場を作り、fake-gitlab のときは模擬を、docker-gitlab のときはプロジェクトを作る。
    state.json に残せる辞書を返す。失敗したら、作ったものを消してから例外を投げる。
    `git(args, cwd=, input=, env=)` は、本物の git を実行する関数。"""
    kind = setup.get("host", DEFAULT_HOST)
    place = tempfile.mkdtemp(prefix="ccnavi-host-")
    host = {"kind": kind, "dir": place, "top": str(top)}
    try:
        host["real_origin"] = _real_origin(top, git)
        host["remote_refs"] = _remote_refs(top, git)
        if kind != DOCKER:
            host["bare"] = os.path.join(place, "origin.git")
            _make_bare(top, host["bare"], git)
        r = git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=top)
        host["target_common"] = r.stdout.strip()
        bin_dir, routes, log = host_stub.build(place, sys.executable, curl=kind == "bare")
        host.update(bin=bin_dir, routes=routes, log=log)
        if kind == DOCKER:
            _make_docker_project(root, host, top, scenario_id, git)
        if kind == "fake-gitlab":
            state = {**default_state(), **(setup.get("host_state") or {})}
            host["project"] = state["project"]
            host["state_path"] = os.path.join(place, "fake-gitlab.json")
            server = fake_gitlab.start(host["state_path"], initial_state=state)
            _SERVERS[place] = server
            host["port"], host["token"] = server.port, server.token
        _log(root).info(
            "ホストの代役を置いた",
            host=kind,
            dir=place,
            port=host.get("port"),
            target=str(top),
        )
    except BaseException:
        teardown(root, host, git)
        raise
    return host


def origin_url(host):
    if host["kind"] == "fake-gitlab":
        return f"http://127.0.0.1:{host['port']}/{host['project']}.git"
    if host["kind"] == DOCKER:
        return host["bare"]
    return host_stub.ORIGIN_URL


def git_via(host):
    """setup.git（runner が実行する git）にも、origin への通信を bare へ向ける -c を付ける。
    docker-gitlab は、利用者の credential helper に PAT が渡らないよう、同じ key を
    空で打ち消してから、環境変数 GITLAB_TOKEN を返す helper を足す
    （host_stub の git の代役と同じ）。"""
    via = ["-c", f"url.{host['bare']}.insteadOf={host['real_origin']}"]
    if host["kind"] == DOCKER:
        key = f"credential.{host['base']}.helper"
        via += ["-c", f"{key}=", "-c", f"{key}={CRED_HELPER}"]
    return via


def step_env(host, base=None):
    """claude に渡す環境。親の環境を継承し、本物のトークン系だけ除いて、代役を足す。"""
    out = {k: v for k, v in (base or os.environ).items() if not TOKEN_VAR.match(k)}
    out.update(
        host_stub.env(
            host["bin"],
            host["routes"],
            host["log"],
            path=out.get("PATH", ""),
            token=agent_token() if host["kind"] == DOCKER else host.get("token"),
            origin_url=origin_url(host),
            target_common=host["target_common"],
            bare=host["bare"],
            real_origin=host["real_origin"],
            cred_url=host.get("base"),
        )
    )
    return out


def hold(root, host):
    """人間の操作で止めるとき。模擬は止め、置き場（bare・代役・模擬の状態）は残す。"""
    server = _SERVERS.pop(host["dir"], None)
    if server is not None:
        server.stop()
        _log(root).info("模擬を止めた（置き場は残す）", dir=host["dir"], port=host["port"])


def resume(root, host):
    """`next` で続けるとき。模擬を、残した状態ファイルのまま（initial_state なしで）起こし直す。
    同じポートで起こし、取れなければ OS に決めさせる。ポートが変わったときは案内の文を返す。"""
    if host["kind"] != "fake-gitlab" or host["dir"] in _SERVERS:
        return []
    old = host["port"]
    try:
        server = fake_gitlab.start(host["state_path"], port=old)
    except OSError:
        server = fake_gitlab.start(host["state_path"])
    _SERVERS[host["dir"]] = server
    host["port"] = server.port
    if server.port == old:
        return []
    return [
        f"模擬のポートが {old} から {server.port} に変わった。"
        f"前の step で作った記録に http://127.0.0.1:{old}/ の URL があれば、古いまま"
    ]


def _delete_project(root, host):
    """Docker の GitLab のプロジェクトを消す。消せたら None、消せなかったら理由。
    GitLab 19 は遅延削除で、1 回目の DELETE（202）のあとも GET が 200（marked_for_deletion_on 付き）
    のまま残る。その間に 2 回目を `permanently_remove=true&full_path=<改名後のパス>` で打つ。"""
    try:
        client, token, _, _ = _connect()
    except Exception as exc:  # 後片付けを途中で止めない
        return str(exc)
    pid = host["project_id"]
    status, _ = client.delete_project(token, pid)
    if status == 404:
        return None
    if status not in (200, 202, 204):
        return f"DELETE が HTTP {status}"
    permanent = False
    for _ in range(DELETE_WAIT_SECONDS):
        status, body = client.call("GET", f"/projects/{pid}", token)
        if status == 404:
            _log(root).info("プロジェクトを消した", project_id=pid, url=host["url"])
            return None
        if not permanent and isinstance(body, dict) and body.get("marked_for_deletion_on"):
            full = urllib.parse.quote(body.get("path_with_namespace", ""), safe="")
            client.call(
                "DELETE", f"/projects/{pid}?permanently_remove=true&full_path={full}", token
            )
            permanent = True
        _sleep(1)
    return f"{DELETE_WAIT_SECONDS} 秒待っても 404 にならない"


def teardown(root, host, git):
    """模擬を止め、refs/remotes/origin/* を戻し、置き場を消す。何度呼んでもよい。
    refs の戻しの失敗は案内を出すだけで、置き場の削除は続ける。
    docker-gitlab のプロジェクトは消せるまで host に残し、消せなかったら True を返す
    （再び呼べば、消す試みだけ続ける）。"""
    failed = False
    if host and host.get("project_id") and not host.get("deleted"):
        why = _delete_project(root, host)
        if why is None:
            host["deleted"] = True
        else:
            failed = True
            _log(root).error("プロジェクトを消せなかった", project_id=host["project_id"], err=why)
            print(
                f"案内: docker-gitlab のプロジェクト {host['url']}（ID {host['project_id']}）を"
                f"消せなかった: {why}。手作業で消す"
            )
    if not host or host.get("down"):
        return failed
    server = _SERVERS.pop(host["dir"], None)
    if server is not None:
        server.stop()
    if host.get("remote_refs") is not None and os.path.isdir(host.get("top", "")):
        try:
            _restore_remote_refs(host["top"], host["remote_refs"], git)
        except Exception as exc:  # 後片付けを途中で止めない
            _log(root).error("refs/remotes/origin を戻せなかった", target=host["top"], err=str(exc))
            print(f"案内: {host['top']} の refs/remotes/origin/* を戻せなかった: {exc}")
    shutil.rmtree(host["dir"], ignore_errors=True)
    host["down"] = True
    _log(root).info("ホストの代役を片付けた", host=host["kind"], dir=host["dir"])
    return failed
