"""代役の git の環境の中で ccnavi-user-agree.sh を擬似 tty で打つ（評価のため）。"""

import json
import os
import subprocess
import sys

root = "/Users/y-taniyama/Data/ccnavi"
out_dir, parent = sys.argv[1], sys.argv[2]
answers = sys.argv[3] if len(sys.argv) > 3 else "y\n"
sys.path.insert(0, root)
sys.path.insert(0, os.path.join(root, "src"))
os.chdir(root)

from tests.scenarios import hostenv  # noqa: E402

with open(os.path.join(out_dir, "state.json"), encoding="utf-8") as f:
    host = json.load(f)["host"]
env = hostenv.step_env(host)
cmd = (
    "(sleep 1; printf '%s' \"$ANSWERS\") | "
    "script -q /dev/null sh .ccnavi/scripts/ccnavi-user-agree.sh $PARENT"
)
env["ANSWERS"] = answers
env["PARENT"] = parent
done = subprocess.run(
    ["/bin/sh", "-c", cmd],
    env=env,
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=300,
    check=False,
)
print("rc=", done.returncode)
print(done.stdout[-6000:])
print(done.stderr[-2000:])
