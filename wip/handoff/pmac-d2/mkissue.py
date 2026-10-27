import os
import sys

sys.path.insert(0, "tests/manual/gitlab")
import gitlab_api

pid = int(sys.argv[1])
with open(os.path.expanduser("~/.cache/ccnavi/gitlab/tokens.env")) as f:
    env = dict(line.rstrip("\n").split("=", 1) for line in f if "=" in line)
c = gitlab_api.Client("http://127.0.0.1:8929")
for title, body in [
    ("捨ての issue（PMAC-D2）", "番号をずらすための issue。"),
    ("README の末尾に 1 行を足す（PMAC-D2）", "評価用。README.md の末尾に「テスト D2」を 1 行足す。"),
]:
    s, b = c.create_issue(env["GITLAB_TOKEN"], pid, title, body)
    print(s, b.get("iid") if isinstance(b, dict) else b)
