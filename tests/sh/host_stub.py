"""ホスト（GitHub / GitLab）の代役。write-and-push と `ccnavi-ticket.sh` のテストが使う。

origin は手元の bare リポジトリのまま、`git remote get-url origin` だけがホストの形の URL
（`https://gitlab.com/acme/widgets.git`）を答える。fetch・push・ls-remote は bare のまま動く。
PATH の先頭に置いた gh / glab は失敗する代役、curl は routes.json で答える代役。
本物のネットワークには出ない。
トークンを渡さなければ、ホストの API は「確かめられなかった」になり、git のリモートの確認は通る。
"""

from __future__ import annotations

import json
import os
import stat

ORIGIN_URL = "https://gitlab.com/acme/widgets.git"
API = "https://gitlab.com/api/v4"
PROJECT = f"{API}/projects/acme%2Fwidgets"

# `git remote get-url origin` だけホストの形の URL を答え、あとは次の git へ渡す
# （自分の置き場は PATH から外す）。
GIT = """#!/bin/sh
case " $* " in
*" remote get-url origin "*)
  printf '%s\\n' "$FAKE_ORIGIN_URL"
  exit 0
  ;;
esac
self=$(dirname "$0")
rest=""
oldifs=$IFS
IFS=:
for p in $PATH; do
  [ "$p" = "$self" ] || rest="$rest:$p"
done
IFS=$oldifs
PATH="${rest#:}"
exec git "$@"
"""

CURL = r"""
import json, os, sys, time
argv = sys.argv[1:]
method, url, data = "GET", "", False
i = 0
while i < len(argv):
    a = argv[i]
    if a in ("-X", "-H", "--header", "--connect-timeout", "--max-time", "--data-binary"):
        if a == "-X":
            method = argv[i + 1]
        if a == "--data-binary":
            data = True
        i += 2
        continue
    if a.startswith("http"):
        url = a
    i += 1
body = sys.stdin.read() if data else ""
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(f"{method} {url}\n")
    if body:
        log.write("BODY " + " ".join(body.split("\n")) + "\n")
if os.environ.get("FAKE_SLEEP"):
    time.sleep(float(os.environ["FAKE_SLEEP"]))
with open(os.environ["FAKE_ROUTES"], encoding="utf-8") as f:
    routes = json.load(f)
status, payload = routes.get(f"{method} {url}", [404, {"message": "Not Found"}])
if status >= 400:
    sys.stderr.write(f"curl: (22) The requested URL returned error: {status}\n")
    sys.exit(22)
sys.stdout.write(json.dumps(payload))
"""


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def build(base, python):
    """代役を base/hostbin に置く。routes.json（空）とログの場所を返す。"""
    bin_dir = os.path.join(base, "hostbin")
    _write(os.path.join(bin_dir, "git"), GIT)
    _write(os.path.join(bin_dir, "gh"), "#!/bin/sh\nexit 1\n")
    _write(os.path.join(bin_dir, "glab"), "#!/bin/sh\nexit 1\n")
    _write(os.path.join(bin_dir, "fake_curl.py"), CURL)
    _write(
        os.path.join(bin_dir, "curl"),
        f"#!/bin/sh\nexec '{python}' '{bin_dir}/fake_curl.py' \"$@\"\n",
    )
    routes = os.path.join(base, "routes.json")
    _write(routes, "{}")
    return bin_dir, routes, os.path.join(base, "requests.log")


def write_routes(path, routes):
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(routes))


def env(bin_dir, routes, log, path=None, token=None):
    """ホストの代役を効かせる環境変数。PATH は渡されたものの先頭に代役の置き場を足す。"""
    out = {
        "PATH": os.pathsep.join(
            [bin_dir, path if path is not None else os.environ.get("PATH", "")]
        ),
        "FAKE_ORIGIN_URL": ORIGIN_URL,
        "FAKE_ROUTES": routes,
        "FAKE_LOG": log,
    }
    if token:
        out["GITLAB_TOKEN"] = token
    return out
