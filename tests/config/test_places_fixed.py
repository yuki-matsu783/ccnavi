"""置き場は既定だけ（ADR-0084 の受入テスト。設計 wip/design/i0064-fixed-places.md §6 の A1・A2）。

置き場を動かしていた 6 つの環境変数
（`CCNAVI_PROJECTS` / `CCNAVI_PROJECT_HOME` / `CCNAVI_TICKETS_PROPOSAL` /
`CCNAVI_TICKETS_APPROVED` / `CCNAVI_LOG` / `CCNAVI_STATE`）を廃止する。
値が残っていても止めず、読まずに既定の置き場のまま動く。

- A1 6 つの env を既定と違う値で入れても、既定の置き場を返す
- A2 ccnavi 自身の上書き設定ファイル（`ccnavi.settings.local.json`）に 6 つのキーを書いても同じ。
  env の表と共有しているので、そちらのキーも読まれなくなる（ADR-0052 と同じ）

診断のフラグ（`--log` `--state` など）は残る。ここは **フラグを渡さずに** 起動する。
フラグを渡すと env や設定ファイルの値を上書きしてしまい、読まれているかどうかが見えない。

置き場は 3 通りで見る。

- `--explain --json` の `settings`（提案・承認済みチケット・プロジェクトの置き場）
- 同じ JSON の `layers`（プロジェクトの層を読む先。`CCNAVI_PROJECT_HOME` が効くとここが動く）
- hook を数回動かして、記録と控えが `<root>/logs/` に書かれ、よその場所には何も書かれないこと

実装前は赤になる。赤の理由は「まだ 6 つの env（と上書き設定ファイルのキー）を読んでいる」こと。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests.inproc import run_ccnavi

# hook を動かす順。UserPromptSubmit と PostToolUse が控えを書く。
EVENTS = (
    ("UserPromptSubmit", ""),
    ("PreToolUse", "Bash"),
    ("PostToolUse", "Bash"),
)

# 6 つの変数と、上書き設定ファイルでの同じ欄の名前。
ENV_TO_KEY = {
    "CCNAVI_PROJECTS": "projects",
    "CCNAVI_PROJECT_HOME": "project_home",
    "CCNAVI_TICKETS_PROPOSAL": "tickets",
    "CCNAVI_TICKETS_APPROVED": "approved",
    "CCNAVI_LOG": "log",
    "CCNAVI_STATE": "state",
}


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def same(path):
    """綴りの区切りと大文字小文字の違いを畳む。Windows の `/` と `\\` を同じに読む。"""
    return os.path.normcase(os.path.normpath(path))


class PlacesHarness(unittest.TestCase):
    """ワークスペースと、そこに置いたプロジェクト 1 つ（lib）。

    `elsewhere/` は env や設定ファイルが指す先。既定の置き場が使われていれば、
    ここには何も書かれない。
    """

    def setUp(self):
        base = tempfile.mkdtemp(prefix="ccnavi-places-fixed-")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.ws = os.path.join(base, "ws")
        self.elsewhere = os.path.join(base, "elsewhere")
        os.makedirs(self.elsewhere)
        write(os.path.join(self.ws, ".ccnavi", "common", "rules.yml"), '{"version": 1}')
        self.lib = os.path.join(self.ws, "projects", "lib")
        write(os.path.join(self.lib, ".ccnavi", "config", "rules.yml"), '{"version": 1}')
        for where in (self.ws, self.lib):
            subprocess.run(["git", "init", "-q"], cwd=where, check=True, capture_output=True)

    # ---- 起動

    def run_ccnavi(self, args, *, env=None, stdin=""):
        """フラグを渡さずに 1 回起動する。`--root` と `--mode` だけ。"""
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        environment.update(env or {})
        return run_ccnavi(["--root", self.ws, *args], input=stdin, env=environment)

    def hook(self, event, tool, *, env=None):
        payload = {
            "hook_event_name": event,
            "tool_name": tool,
            "cwd": self.ws,
            "session_id": "s1",
            "tool_input": {"command": "ls"} if tool else {},
        }
        done = self.run_ccnavi(["--mode", "enable"], env=env, stdin=json.dumps(payload))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    # ---- 読み取り

    def strayed(self):
        """`elsewhere/` に書かれたファイル。既定が使われていれば無い。"""
        found = []
        for where, _, names in os.walk(self.elsewhere):
            found += [os.path.relpath(os.path.join(where, n), self.elsewhere) for n in names]
        return sorted(found)

    def observe(self, *, env=None):
        """置き場について見えるものを 1 つの辞書にまとめる。"""
        for event, tool in EVENTS:
            self.hook(event, tool, env=env)
        # 記録と控えは hook が書いたものだけを見る。`--explain` を先に走らせると、そちらが
        # 書いた分と区別が付かない。
        wrote_log = os.path.isfile(os.path.join(self.ws, "logs", "log.jsonl"))
        wrote_state = os.path.isfile(os.path.join(self.ws, "logs", "state", "s1.json"))
        done = self.run_ccnavi(["--explain", "--json"], env=env)
        try:
            board = json.loads(done.stdout)
        except ValueError as exc:
            self.fail(f"--explain --json が読めない: {exc}\n{done.stdout}\n{done.stderr}")
        layers = {layer["name"]: layer for layer in board["layers"]}
        lib_rules = layers.get("lib", {}).get("rules", {}).get("path", "")
        return {
            "tickets": board["settings"]["tickets"],
            "approved": board["settings"]["approved"],
            "projects": same(board["settings"]["projects"]),
            "counted": board["projects"],
            "lib_rules": same(lib_rules) if lib_rules else "",
            "log": wrote_log,
            "state": wrote_state,
            "strayed": self.strayed(),
        }

    def defaults(self):
        return {
            "tickets": "wip/proposals",
            "approved": ".ccnavi/approved",
            "projects": same(os.path.join(self.ws, "projects")),
            "counted": ["lib"],
            "lib_rules": same(os.path.join(self.lib, ".ccnavi", "config", "rules.yml")),
            "log": True,
            "state": True,
            "strayed": [],
        }

    def moved(self):
        """6 つを、どれも既定と違う値にしたもの。キーは上書き設定ファイルの欄の名前。"""
        return {
            "projects": os.path.join(self.elsewhere, "projects"),
            "project_home": ".navi",
            "tickets": "other/proposals",
            "approved": "other/approved",
            "log": os.path.join(self.elsewhere, "log.jsonl"),
            "state": os.path.join(self.elsewhere, "state"),
        }


class EnvDoesNotMoveThePlacesTest(PlacesHarness):
    """A1: env は置き場を動かさない。"""

    def env_of(self, *keys):
        by_key = self.moved()
        return {name: by_key[key] for name, key in ENV_TO_KEY.items() if key in keys}

    def test_defaults_hold_without_any_env(self):
        """土台の確認。env が無ければ、期待する辞書は既定そのもの。"""
        self.assertEqual(self.observe(), self.defaults())

    def test_each_env_is_ignored(self):
        """6 つを 1 つずつ入れても、どの置き場も動かない。どの変数が読まれたかが名指しで出る。"""
        for name, key in ENV_TO_KEY.items():
            with self.subTest(env=name):
                self.assertEqual(self.observe(env=self.env_of(key)), self.defaults())

    def test_all_six_together_are_ignored(self):
        self.assertEqual(self.observe(env=self.env_of(*ENV_TO_KEY.values())), self.defaults())

    def test_empty_values_are_ignored_too(self):
        """空文字は「数えない」「記録しない」「控えを持たない」と読まれていた。もう言えない。"""
        empty = {name: "" for name in ENV_TO_KEY}
        self.assertEqual(self.observe(env=empty), self.defaults())


class LocalSettingsFileDoesNotMoveThePlacesTest(PlacesHarness):
    """A2: 上書き設定ファイルの 6 つのキーも読まれない。

    このファイルは ccnavi 自身のソースツリー（`pyproject.toml` の名前が `ccnavi`）でだけ読まれる。
    env の表と共有しているので、env を消せばここのキーも読まれなくなる。
    """

    def setUp(self):
        super().setUp()
        write(os.path.join(self.ws, "pyproject.toml"), '[project]\nname = "ccnavi"\n')

    def local(self, **conf):
        return write(os.path.join(self.ws, "ccnavi.settings.local.json"), json.dumps(conf))

    def test_each_key_is_ignored(self):
        by_key = self.moved()
        for key in ENV_TO_KEY.values():
            with self.subTest(key=key):
                self.local(**{key: by_key[key]})
                self.assertEqual(self.observe(), self.defaults())

    def test_all_six_keys_together_are_ignored(self):
        self.local(**self.moved())
        self.assertEqual(self.observe(), self.defaults())


if __name__ == "__main__":
    unittest.main()
