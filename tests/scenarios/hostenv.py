"""受入シナリオの `setup.host`（評価の相手）を用意して片付ける。
決まりは docs/42_claude/acceptance-scenarios.md。

- `bare`: 対象の origin への通信を、一時ディレクトリの bare に向ける。
  git の代役が URL をホストの形で答える。
  ホストの API は gh / glab が失敗し、curl は空の routes で 404（「確かめられなかった」）
- `fake-gitlab`: `bare` に加え、`tests/fake_gitlab.py` を起こす（curl の代役は置かない）
- `real`: 何もしない

実行器（runner.py）から呼ぶ。状態は JSON にできる辞書（`prepare` の戻り値）で、state.json に残す。
起こした模擬のスレッドは、この辞書の `dir` をキーにしたプロセス内の表で持つ。
"""

import os
import re
import shutil
import sys
import tempfile

from ccnavi.records import diaglog
from tests import fake_gitlab, host_stub

HOSTS = ("bare", "fake-gitlab", "real")
DEFAULT_HOST = "real"
LOG_NAME = "scenario-runner"
# 本物のホストに出る資格情報。claude の環境から外す（偽のトークンだけを渡す）
TOKEN_VAR = re.compile(r"^(GH|GITHUB|GLAB|GITLAB|CCNAVI_PROBE)_.*TOKEN$", re.IGNORECASE)
REMOTE_REFS = "refs/remotes/origin"

_SERVERS = {}


def is_fake(host):
    return host in ("bare", "fake-gitlab")


def default_state():
    return {"project": "ns/proj", "seq": 100, "issues": [], "mrs": []}


def validate(path, setup):
    """setup.host と setup.host_state の形を確かめる。誤りは ValueError。"""
    host = setup.get("host", DEFAULT_HOST)
    if host not in HOSTS:
        raise ValueError(f"{path}: setup.host は {' / '.join(HOSTS)} のどれかです: {host!r}")
    if is_fake(host) and setup.get("issue"):
        raise ValueError(
            f"{path}: setup.issue と setup.host: {host} は併用できません"
            "（issue は runner が gh / glab で作るので、代役の PATH では作れない）。"
            "fake-gitlab で issue が要るときは setup.host_state に書く"
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


def prepare(root, top, setup, git):
    """代役の置き場を作り、fake-gitlab のときは模擬を起こす。state.json に残せる辞書を返す。
    失敗したら、作ったものを消してから例外を投げる。
    `git(args, cwd=, input=)` は、本物の git を実行する関数。"""
    kind = setup.get("host", DEFAULT_HOST)
    place = tempfile.mkdtemp(prefix="ccnavi-host-")
    host = {"kind": kind, "dir": place, "top": str(top)}
    try:
        host["real_origin"] = _real_origin(top, git)
        host["remote_refs"] = _remote_refs(top, git)
        host["bare"] = os.path.join(place, "origin.git")
        _make_bare(top, host["bare"], git)
        r = git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=top)
        host["target_common"] = r.stdout.strip()
        bin_dir, routes, log = host_stub.build(place, sys.executable, curl=kind == "bare")
        host.update(bin=bin_dir, routes=routes, log=log)
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
    return host_stub.ORIGIN_URL


def git_via(host):
    """setup.git（runner が実行する git）にも、origin への通信を bare へ向ける -c を付ける。"""
    return ["-c", f"url.{host['bare']}.insteadOf={host['real_origin']}"]


def step_env(host, base=None):
    """claude に渡す環境。親の環境を継承し、本物のトークン系だけ除いて、代役を足す。"""
    out = {k: v for k, v in (base or os.environ).items() if not TOKEN_VAR.match(k)}
    out.update(
        host_stub.env(
            host["bin"],
            host["routes"],
            host["log"],
            path=out.get("PATH", ""),
            token=host.get("token"),
            origin_url=origin_url(host),
            target_common=host["target_common"],
            bare=host["bare"],
            real_origin=host["real_origin"],
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


def teardown(root, host, git):
    """模擬を止め、refs/remotes/origin/* を戻し、置き場を消す。何度呼んでもよい。
    refs の戻しの失敗は案内を出すだけで、置き場の削除は続ける。"""
    if not host or host.get("down"):
        return
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
