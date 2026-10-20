"""gh / glab の資格情報を git に使わせる sh（`ccnavi_git_auth`）の読み手。

`CCNAVI_SH_DIR` で、読む sh の出どころを差し替えられる。既定はこのツリーの `.ccnavi/scripts/`。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT

SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", "") or ".ccnavi/scripts")
HOST_SH = os.path.join(SH_DIR, "ccnavi-common-host.sh")
SHELL = shutil.which("sh") or shutil.which("bash")

PROBE = """. "$1"
cd "$2"
ccnavi_git_auth .
ccnavi_git_auth .
printf 'count=%s\\n' "${GIT_CONFIG_COUNT:-}"
n=${GIT_CONFIG_COUNT:-0}
i=0
while [ "$i" -lt "$n" ]; do
  eval "printf 'key=%s\\\\nvalue=%s\\\\n' \\"\\$GIT_CONFIG_KEY_$i\\" \\"\\$GIT_CONFIG_VALUE_$i\\""
  i=$((i + 1))
done
"""


@unittest.skipIf(SHELL is None, "sh が無い")
class GitAuthShTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "repo")
        self.bin = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.repo)
        os.makedirs(self.bin)
        subprocess.run(["git", "init", "-q", self.repo], check=True)

    def tool(self, name: str) -> str:
        path = os.path.join(self.bin, name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(path, 0o755)
        return path

    def run_auth(
        self, origin: str, parent_count: str | None = None, path: str | None = None
    ) -> dict:
        subprocess.run(["git", "-C", self.repo, "remote", "add", "origin", origin], check=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG")}
        env["PATH"] = path or self.bin + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
        if parent_count is not None:
            env["GIT_CONFIG_COUNT"] = parent_count
            env["GIT_CONFIG_KEY_0"] = "user.name"
            env["GIT_CONFIG_VALUE_0"] = "x"
        done = subprocess.run(
            [SHELL, "-c", PROBE, "probe", HOST_SH, self.repo],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            check=False,
        )
        lines = [ln.split("=", 1) for ln in done.stdout.splitlines() if "=" in ln]
        return {
            "count": next((v for k, v in lines if k == "count"), ""),
            "keys": [v for k, v in lines if k == "key"],
            "values": [v for k, v in lines if k == "value"],
        }

    def test_github_https_gets_gh_helper_once(self):
        gh = self.tool("gh")
        got = self.run_auth("https://github.com/o/r.git")
        self.assertEqual(got["count"], "1")
        self.assertEqual(got["keys"], ["credential.https://github.com.helper"])
        self.assertEqual(got["values"], [f'!"{gh}" auth git-credential'])

    def test_gitlab_https_gets_glab_helper_with_port(self):
        glab = self.tool("glab")
        got = self.run_auth("http://localhost:8929/g/p.git")
        self.assertEqual(got["keys"], ["credential.http://localhost:8929.helper"])
        self.assertEqual(got["values"], [f'!"{glab}" auth git-credential'])

    def test_ssh_origin_adds_nothing(self):
        self.tool("gh")
        self.assertEqual(self.run_auth("git@github.com:o/r.git")["count"], "")

    def test_missing_tool_adds_nothing(self):
        if shutil.which("gh", path="/usr/bin:/bin"):
            self.skipTest("システムに gh がある")
        self.assertEqual(self.run_auth("https://github.com/o/r.git")["count"], "")

    def test_relative_tool_path_is_refused(self):
        os.makedirs(os.path.join(self.repo, "bin"))
        with open(os.path.join(self.repo, "bin", "gh"), "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(os.path.join(self.repo, "bin", "gh"), 0o755)
        got = self.run_auth("https://github.com/o/r.git", path="bin:/usr/bin:/bin")
        self.assertEqual(got["keys"], [])

    def test_tool_path_with_dollar_is_refused(self):
        bad = os.path.join(self.tmp.name, "d$(touch pwned)")
        os.makedirs(bad)
        with open(os.path.join(bad, "gh"), "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(os.path.join(bad, "gh"), 0o755)
        got = self.run_auth("https://github.com/o/r.git", path=bad + ":/usr/bin:/bin")
        self.assertEqual(got["keys"], [])
        self.assertFalse(os.path.exists("pwned"))

    def test_parent_config_is_kept(self):
        self.tool("gh")
        got = self.run_auth("https://github.com/o/r.git", parent_count="1")
        self.assertEqual(got["count"], "2")
        self.assertEqual(got["keys"][0], "user.name")

    def test_hostile_host_runs_nothing(self):
        self.tool("glab")
        marker = os.path.join(self.tmp.name, "pwned")
        self.run_auth(f"https://$(touch {marker})/a/b.git")
        self.assertFalse(os.path.exists(marker))


if __name__ == "__main__":
    unittest.main()
