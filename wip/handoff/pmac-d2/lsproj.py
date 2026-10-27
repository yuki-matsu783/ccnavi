import os
import sys

sys.path.insert(0, "tests/manual/gitlab")
import gitlab_api

env = dict(
    line.rstrip("\n").split("=", 1)
    for line in open(os.path.expanduser("~/.cache/ccnavi/gitlab/tokens.env"))
    if "=" in line
)
c = gitlab_api.Client("http://127.0.0.1:8929")
s, b = c.call("GET", "/projects?per_page=100&order_by=created_at", env["GITLAB_TOKEN"])
print(s, [(p["id"], p["path_with_namespace"], p["created_at"][:19]) for p in b])
