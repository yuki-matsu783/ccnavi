"""docker-gitlab のシナリオを人間の操作の step で止めて続けるときの、操作者の道具。

  uv run python tests/manual/gitlab/scenario_tools.py issue <出力の置き場> [--title 題] [--root 根]
  uv run python tests/manual/gitlab/scenario_tools.py agree <出力の置き場> <識別子>... [--root 根]
  uv run python tests/manual/gitlab/scenario_tools.py leak  <出力の置き場> [--root 根]

issue: 使い捨てのプロジェクトに、人間役のトークンで issue を 1 件作る（human_api に無い操作）。
agree: 代役の git の環境（hostenv.step_env）の中で、擬似 tty から y を送って承認を代行する。
       ユーザ専用の承認の sh は hook が止めるので、ユーザの端末で実行する。素の shell で打たない。
leak:  トークンの値が記録に出ていないかを、一致したファイル名だけで報告する（値は出さない）。

--root はワークスペースルート（既定は、このファイルから見たリポジトリの根）。
トークンの値は端末に出さない。
"""

import argparse
import json
import os
import pty
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
DEFAULT_ROOT = HERE.parents[3]
sys.path.insert(0, str(DEFAULT_ROOT))
from tests.scenarios import hostenv  # noqa: E402


def load_state(out):
    return json.loads((Path(out) / "state.json").read_text(encoding="utf-8"))


def cmd_issue(args):
    state = load_state(args.out)
    client, _, _, values = hostenv._connect()
    token = values[hostenv.HUMAN_TOKEN_KEY]
    status, body = client.create_issue(token, state["host"]["project_id"], args.title, "")
    iid = body.get("iid") if isinstance(body, dict) else None
    print("status", status, "iid", iid)
    return 0 if 200 <= status < 300 else 1


def cmd_agree(args):
    root = Path(args.root)
    env = hostenv.step_env(load_state(args.out)["host"], base=dict(os.environ))
    os.chdir(root)
    sent = []

    def stdin_read(_fd):
        if sent:
            return b""
        time.sleep(4)  # y/N の問いが出るのを待つ
        sent.append(1)
        return b"y\n"

    def master_read(fd):
        data = os.read(fd, 4096).decode("utf-8", "replace")
        # 空を返すと pty.spawn が読みを打ち切るので、伏せた文字列を返す
        return re.sub(r"glpat-[A-Za-z0-9_-]+", "glpat-***", data).encode("utf-8")

    os.environ.clear()
    os.environ.update(env)
    argv = ["sh", str(root / ".ccnavi/scripts/ccnavi-user-agree.sh"), *args.ids]
    code = os.waitstatus_to_exitcode(pty.spawn(argv, master_read, stdin_read))
    print("\n終了コード:", code)
    return code


def cmd_leak(args):
    root = Path(args.root)
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    values = {}
    for line in (cache / "ccnavi/gitlab/tokens.env").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        value = value.strip().strip("'\"")
        if "TOKEN" in key and value:
            values[key] = value
    targets = [Path(args.out), root / "logs", root / "projects/test-repo/.git/config"]
    hits = {key: [] for key in values}
    for target in targets:
        files = [target] if target.is_file() else [p for p in target.rglob("*") if p.is_file()]
        for path in files:
            try:
                data = path.read_bytes()
            except OSError:
                continue
            for key, value in values.items():
                if value.encode() in data:
                    hits[key].append(str(path))
    names = {"GITLAB_TOKEN": "エージェント役", hostenv.HUMAN_TOKEN_KEY: "人間役"}
    for key, files in hits.items():
        print(f"{names.get(key, key)}: {len(files)} 件")
        for f in files[:20]:
            print("  ", f)
    return 1 if any(hits.values()) else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, func in (("issue", cmd_issue), ("agree", cmd_agree), ("leak", cmd_leak)):
        p = sub.add_parser(name)
        p.add_argument("out", help="runner の出力の置き場（state.json のある所）")
        p.add_argument("--root", default=str(DEFAULT_ROOT))
        p.set_defaults(func=func)
        if name == "issue":
            p.add_argument("--title", default="README の末尾に 1 行を足す")
        if name == "agree":
            p.add_argument("ids", nargs="+", help="承認する識別子（親・子）")
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
