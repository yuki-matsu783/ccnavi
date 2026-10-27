import json
import os
import sys
import time

sys.path.insert(0, "tests/manual/gitlab")
import gitlab_api

pid = int(sys.argv[1])
out = sys.argv[2]
with open(os.path.expanduser("~/.cache/ccnavi/gitlab/tokens.env")) as f:
    env = dict(line.rstrip("\n").split("=", 1) for line in f if "=" in line)
tok = env["GITLAB_TOKEN"]
c = gitlab_api.Client("http://127.0.0.1:8929")
last = None
deadline = time.time() + 1800
while time.time() < deadline:
    s, mrs = c.call("GET", f"/projects/{pid}/merge_requests?state=all", tok)
    if s != 200:
        break
    snap = {"t": time.strftime("%H:%M:%S"), "mrs": []}
    for m in mrs if isinstance(mrs, list) else []:
        iid = m["iid"]
        _, d = c.call("GET", f"/projects/{pid}/merge_requests/{iid}", tok)
        _, notes = c.call("GET", f"/projects/{pid}/merge_requests/{iid}/notes?per_page=100", tok)
        _, disc = c.call(
            "GET", f"/projects/{pid}/merge_requests/{iid}/discussions?per_page=100", tok
        )
        _, ap = c.call("GET", f"/projects/{pid}/merge_requests/{iid}/approvals", tok)
        snap["mrs"].append(
            {
                "iid": iid,
                "title": m["title"],
                "draft": m.get("draft"),
                "state": m["state"],
                "source": m["source_branch"],
                "notes": [
                    (n["author"]["username"], n["system"], n["body"][:120]) for n in notes
                ],
                "threads": [
                    (
                        [x["body"][:60] for x in dd["notes"]][:3],
                        dd["notes"][0].get("resolved"),
                    )
                    for dd in disc
                    if not dd["notes"][0]["system"]
                ],
                "approved_by": [a["user"]["username"] for a in ap.get("approved_by", [])]
                if isinstance(ap, dict)
                else None,
            }
        )
    key = json.dumps(snap["mrs"], ensure_ascii=False, sort_keys=True)
    if key != last:
        with open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(snap, ensure_ascii=False) + "\n")
        last = key
    time.sleep(4)
