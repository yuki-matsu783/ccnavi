"""Docker の本物の GitLab CE を起動・停止・確認する。手で走らせる道具で、自動テストは呼ばない。

    uv run python tests/manual/gitlab/docker_gitlab.py up [--pull] [--rotate]
    uv run python tests/manual/gitlab/docker_gitlab.py status
    uv run python tests/manual/gitlab/docker_gitlab.py down [--purge]

- 版は `GITLAB_IMAGE` の 1 か所で固定する（ダイジェスト指定）。上げるときはここを書き換える。
- ポートは `CCNAVI_GITLAB_PORT`（既定 8929）の 1 か所で決める。compose の external_url と
  公開ポート、probe_gitlab.py の既定の接続先（`CCNAVI_PROBE_GITLAB`）は、この値から作られる。
- `up`: イメージが無いときは取得せず止まる（`--pull` を付けたときだけ取得する）。動いていれば
  起動しない。健康になり、`/api/v4/version` が応答するまで待つ
  （上限 15 分。実測の再起動は約 2 分）。
  Docker のメモリが `MEMORY_MIN_BYTES` 未満なら `次:` で警告する。
- トークンは有効なら作り直さない。`--rotate` のときだけ作り直す（既存は失効する）。0600 のファイルに
  書き、端末には出さない。使うときは `set -a; . <status が出すパス>; set +a` で読み込む。
- root のパスワードは、ボリュームが無いときだけ作る。ボリュームが既にあれば、GitLab は初回の
  パスワードを持っているので作り直さない（作り直すと、状態のファイルと実際のパスワードが食い違う）。
- `down`: コンテナを止めて消す。ボリュームは残す。`--purge` でボリュームと状態のファイルも消す。
- 状態のファイルは `~/.cache/ccnavi/gitlab/`（`XDG_CACHE_HOME` があればその下）。リポジトリの外。
- 診断ログは `logs/diag/gitlab-manual.log` に書く（値は書かない）。
- 立て方（external_url と公開ポートの一致、127.0.0.1 へのバインド、--shm-size、--restart no）は
  compose.yml にある。CE は `MEM_LIMIT` のメモリが要る。
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
import time

import gitlab_api

from ccnavi.records import diaglog

# gitlab-ce 19.4.1（ダイジェストで固定。タグの 19.4.1 は Docker Hub に無い）
GITLAB_IMAGE = (
    "gitlab/gitlab-ce@sha256:9b33b45b9f42d176bada85ee5ecb81ddab7e506c435f44cd582206e284b2809c"
)
PORT = int(os.environ.get("CCNAVI_GITLAB_PORT", "8929"))
BASE = f"http://127.0.0.1:{PORT}"
# 下限は GitLab の要求（4GB）より大きくとる。足りないと OOM で engine ごと落ちる
MEM_LIMIT = "6g"
MEMORY_MIN_BYTES = 6 * 1024**3
CONTAINER = "gitlab"
# compose の project 名（compose.yml の name）と、そのボリューム名
DATA_VOLUME = "ccnavi-gitlab_gitlab_data"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, os.pardir))
COMPOSE_FILE = os.path.join(HERE, "compose.yml")
TOKEN_SCRIPT = os.path.join(HERE, "make_gitlab_tokens.rb")
TOKEN_KEYS = ("GITLAB_TOKEN", "CCNAVI_PROBE_REVIEWER_TOKEN")
TOKEN_OUTPUT_KEYS = (*TOKEN_KEYS, "CCNAVI_PROBE_REVIEWER_ID", "CCNAVI_PROBE_ROOT_ID")
WAIT_SECONDS = 15 * 60
POLL_SECONDS = 15
# docker の問い合わせの上限。GitLab が重いと docker exec は返らなくなるので、待つ側で切る
QUERY_TIMEOUT = 30
RAILS_TIMEOUT = 90
TOKEN_TIMEOUT = 300
log = diaglog.get("gitlab-manual", ROOT)


def state_dir() -> str:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "ccnavi", "gitlab")


def password_path() -> str:
    return os.path.join(state_dir(), "root.env")


def tokens_path() -> str:
    return os.path.join(state_dir(), "tokens.env")


def run(
    args: list[str],
    *,
    stdin: str | None = None,
    env: dict | None = None,
    timeout: float | None = QUERY_TIMEOUT,
):
    """時間切れは失敗（終了コード 124）として返す。GitLab が重いとき docker exec が返らないため。"""
    try:
        return subprocess.run(
            args,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, stdout="", stderr="時間切れ")


def read_env_file(path: str) -> dict[str, str]:
    if not os.path.isfile(path):
        return {}
    values = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if "=" in line:
                key, value = line.rstrip("\n").split("=", 1)
                values[key] = value
    return values


def write_env_file(path: str, values: dict[str, str]) -> None:
    """0600 で書く。既にあるファイルも 0600 に直す。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(path, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for key, value in values.items():
            f.write(f"{key}={value}\n")


def docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    return run(["docker", "version", "--format", "{{.Server.Version}}"]).returncode == 0


def docker_memory_bytes() -> int:
    done = run(["docker", "info", "--format", "{{.MemTotal}}"])
    try:
        return int(done.stdout.strip())
    except ValueError:
        return 0


def image_present() -> bool:
    return run(["docker", "image", "inspect", GITLAB_IMAGE]).returncode == 0


def volume_exists() -> bool:
    return run(["docker", "volume", "inspect", DATA_VOLUME]).returncode == 0


def container_state() -> tuple[str, str]:
    """(状態, ヘルス)。コンテナが無いときは ("", "")。"""
    done = run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Status}} {{.State.Health.Status}}",
            CONTAINER,
        ]
    )
    if done.returncode != 0:
        return "", ""
    parts = done.stdout.split()
    return (parts[0], parts[1] if len(parts) > 1 else "")


def compose(args: list[str], password: str):
    env = dict(os.environ)
    env["GITLAB_IMAGE"] = GITLAB_IMAGE
    env["GITLAB_PORT"] = str(PORT)
    env["GITLAB_MEM_LIMIT"] = MEM_LIMIT
    env["GITLAB_ROOT_PASSWORD"] = password or "unused"
    # 起動と停止は時間がかかるので、時間切れで打ち切らない
    return run(["docker", "compose", "-f", COMPOSE_FILE, *args], env=env, timeout=None)


def rails_ok() -> bool:
    done = run(
        ["docker", "exec", CONTAINER, "gitlab-rails", "runner", "puts :ok"], timeout=RAILS_TIMEOUT
    )
    return done.returncode == 0 and "ok" in done.stdout


def ensure_tokens(client: gitlab_api.Client, rotate: bool) -> dict[str, str] | None:
    """有効なトークンが既にあれば、そのまま返す。無いときか --rotate のときだけ作る。

    401（失効）のときだけ無効とみなす。0・5xx は一時的な不通かもしれず、
    作り直すと有効な既存を失効させる。
    """
    current = read_env_file(tokens_path())
    present = all(current.get(k) for k in TOKEN_KEYS)
    if not rotate and present and all(client.user(current[k])[0] != 401 for k in TOKEN_KEYS):
        log.info("トークンを再利用した")
        return current
    # 有効でない（無い・失効した）か --rotate のときだけ作る。作ると既存は失効する
    with open(TOKEN_SCRIPT, encoding="utf-8") as f:
        script = f.read()
    done = run(
        ["docker", "exec", "-i", CONTAINER, "gitlab-rails", "runner", "-"],
        stdin=script,
        timeout=TOKEN_TIMEOUT,
    )
    if done.returncode != 0:
        log.error("トークンを作れなかった", rc=done.returncode)
        print("次: トークンを作れなかった。docker logs gitlab で見る", file=sys.stderr)
        return None
    found = {}
    for line in done.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            if key in TOKEN_OUTPUT_KEYS:
                found[key] = value
    if not all(found.get(k) for k in TOKEN_KEYS):
        log.error("トークンの出力が読めなかった")
        print("次: トークンの出力が読めなかった。make_gitlab_tokens.rb を確かめる", file=sys.stderr)
        return None
    write_env_file(tokens_path(), found)
    log.info("トークンを作った", count=len(found))
    return found


def wait_ready(client: gitlab_api.Client, rotate: bool):
    """健康・rails・/api/v4/version が揃うまで待つ。(トークン, version 応答) を返す。"""
    start = time.monotonic()
    shown = -1
    tokens = None
    while time.monotonic() - start < WAIT_SECONDS:
        health = container_state()[1]
        if health == "healthy" and rails_ok():
            if tokens is None:
                tokens = ensure_tokens(client, rotate)
            if tokens is not None:
                status, body = client.version(tokens["GITLAB_TOKEN"])
                if status == 200:
                    log.info("応答した", elapsed=int(time.monotonic() - start))
                    return tokens, body
        elapsed = int(time.monotonic() - start)
        if elapsed // 60 > shown:
            shown = elapsed // 60
            print(f"待っている（{elapsed} 秒。健康: {health or '-'}）", flush=True)
        time.sleep(POLL_SECONDS)
    return None, None


def root_password() -> str:
    """コンテナを作るときのパスワード。ボリュームが既にあれば作らず、今の値を返す。"""
    state, _ = container_state()
    if state or volume_exists():
        return read_env_file(password_path()).get("GITLAB_ROOT_PASSWORD", "")
    password = secrets.token_hex(24)
    write_env_file(password_path(), {"GITLAB_ROOT_PASSWORD": password})
    log.info("root のパスワードを作った")
    return password


def cmd_up(args: argparse.Namespace) -> int:
    if not docker_ok():
        print(
            "次: Docker が動いていない（docker version が通らない）。Docker を起動してから打ち直す"
        )
        return 2
    memory = docker_memory_bytes()
    if memory < MEMORY_MIN_BYTES:
        gb = memory / 1024**3
        print(
            f"次: Docker に割り当てたメモリが {gb:.1f}GB。GitLab CE は 6GB 以上が要る。"
            "Docker の設定で増やしてから打ち直す（足りないと engine ごと落ちる）"
        )
        log.warn("メモリが少ない", gb=round(gb, 1))
    if not image_present():
        if not args.pull:
            print(
                f"次: イメージ {GITLAB_IMAGE} がローカルに無い（約 4GB）。取るなら up --pull を打つ"
            )
            return 2
        log.info("イメージを取る")
        if subprocess.run(["docker", "pull", GITLAB_IMAGE]).returncode != 0:
            print("次: pull が失敗した。ネットワークとプロキシを確かめる")
            return 1
    state, _ = container_state()
    password = root_password()
    if state != "running":
        log.info("起動する", port=PORT)
        done = compose(["up", "-d", "--pull", "never"], password)
        if done.returncode != 0:
            log.error("起動できなかった", rc=done.returncode)
            print("次: コンテナを起動できなかった", file=sys.stderr)
            print(done.stderr.strip(), file=sys.stderr)
            return 1
    print("起動を待っている。再起動は約 2 分かかる（実測）")
    client = gitlab_api.Client(BASE)
    tokens, body = wait_ready(client, args.rotate)
    if tokens is None:
        log.error("15 分待っても応答しない")
        print("次: 15 分待っても応答しない。docker logs gitlab で見る")
        return 1
    version = body.get("version") if isinstance(body, dict) else "?"
    print(f"起動した。GitLab {version}（{BASE}）")
    print(f"トークン: {tokens_path()}（set -a; . <このパス>; set +a で読む。端末には出さない）")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    if not docker_ok():
        print("次: Docker が動いていない")
        return 2
    state, health = container_state()
    print(f"コンテナ: {state or '無し'}" + (f"（健康: {health}）" if health else ""))
    tokens = read_env_file(tokens_path())
    print(f"トークン: {tokens_path()}")
    if state != "running":
        return 0
    client = gitlab_api.Client(BASE)
    if not all(tokens.get(k) for k in TOKEN_KEYS):
        print("トークン: 無し（up で作る）")
        return 0
    status, body = client.version(tokens["GITLAB_TOKEN"])
    version = body.get("version") if isinstance(body, dict) else body
    print(f"API: HTTP {status}、GitLab {version}")
    codes = [client.user(tokens[k])[0] for k in TOKEN_KEYS]
    # up と同じく、401（失効）だけを無効とみなす。0・5xx は一時的な不通かもしれないので「不明」
    if all(code == 200 for code in codes):
        verdict = "有効"
    elif 401 in codes:
        verdict = "無効（up で作り直す）"
    else:
        verdict = f"不明（HTTP {codes}。時間をおいて再実行）"
    print(f"トークンの有効性: {verdict}")
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    if not docker_ok():
        print("次: Docker が動いていない")
        return 2
    password = read_env_file(password_path()).get("GITLAB_ROOT_PASSWORD", "")
    done = compose(["down", *(["-v"] if args.purge else [])], password)
    if done.returncode != 0:
        log.error("停止できなかった", rc=done.returncode)
        print("次: 停止できなかった", file=sys.stderr)
        print(done.stderr.strip(), file=sys.stderr)
        return 1
    if args.purge:
        for path in (password_path(), tokens_path()):
            if os.path.exists(path):
                os.remove(path)
        log.info("止めて、ボリュームと状態を消した")
        print("止めて、ボリュームと状態のファイルを消した")
    else:
        log.info("止めた", purge=False)
        print("止めた（ボリュームは残す。--purge で消す）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Docker の GitLab CE を起動・停止・確認する")
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("up", help="起動して、応答するまで待つ")
    up.add_argument("--pull", action="store_true", help="イメージが無いとき取得する")
    up.add_argument("--rotate", action="store_true", help="トークンを作り直す")
    up.set_defaults(func=cmd_up)
    status = sub.add_parser("status", help="状態を出す")
    status.set_defaults(func=cmd_status)
    down = sub.add_parser("down", help="止めて消す")
    down.add_argument("--purge", action="store_true", help="ボリュームと状態のファイルも消す")
    down.set_defaults(func=cmd_down)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
