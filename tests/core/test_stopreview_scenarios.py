"""StopのAI判定の受入シナリオ13件を、場面データから決定的に確かめる。

場面データはtests/fixtures/stop_review/scenarios/にある。実機のモデルは呼ばない。
各場面について、場面データからstate・transcript・提案を組み、hookと同じ経路
（`events._plan_stop_review`・`cli_stopreview` のmaterialとrecord）を通して、次を確かめる。

- 候補になるか、計画の対象と必要な基準（`sr.required`）が期待と合うか
- 判定役に渡る材料に、`must_contain` の文字列がすべて含まれるか。これで判定役が根拠を受け取れる
- recordに判定結果を渡したときの、返り値・試行の状態・最終分類・再判定の結果。判定結果は
  場面データの `judge_result*.json`、無ければ `expected.json` の基準ごとの期待から組んだもの

検査しないもの: 判定役が正しく判定すること。基準ごとの期待は、正しい判定役が出すはずの結果として
recordに渡す入力であり、ここで確かめるのはccnavi側の確定の規則だけ。

場面の置き方。1回のStopの場面は場面の直下、2回のStopは `stop-1/`・`stop-2/` に分ける。

- `transcript.jsonl`: そのStopの時点の会話
- `proposals/<識別子>.txt`: Writeで書いた提案（`wip/proposals/todo/<識別子>.md` に置く）
- `propose.kv`: approval-requestの結果ファイル。`{now}`・`{commit}`・`{blob:<パス>}` を置き換える。
  あれば `proposals/` の提案をコミットして、送った版にする
- `judge_result*.json`: 判定役の結果を模したもの。`$ATTEMPT`・`$PROPOSAL:<識別子>` を置き換える
- `settings.json`（場面の直下）: `stop_review` の値。無ければenable
- `expected.json`（場面の直下）: 期待
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from typing import Any, NamedTuple
from unittest import mock

from ccnavi.entry import cli_stopreview
from ccnavi.hook import events
from ccnavi.infra import hookio, modes, settings
from ccnavi.records import stopops
from ccnavi.records import stopreview as sr
from tests import ROOT

SCENES = os.path.join(ROOT, "tests", "fixtures", "stop_review", "scenarios")
GIT = shutil.which("git")
TODO = "wip/proposals/todo"
SESSION = "s1"
SCENE_COUNT = 13
FIXES = {sr.R1: "write_proposal", sr.R2: "revise_proposal", sr.R4: "send_proposal"}


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def scene_names():
    return sorted(n for n in os.listdir(SCENES) if os.path.isdir(os.path.join(SCENES, n)))


def _prompts(path):
    """transcriptの中のユーザの発言から、ツールの結果と通知を除いたもの。UserPromptSubmitの足跡に使う。"""
    found = []
    for line in _read(path).splitlines():
        row = json.loads(line)
        if row.get("type") != "user":
            continue
        content = row["message"]["content"]
        if isinstance(content, list):
            content = "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
        if content and not content.lstrip().startswith("<task-notification"):
            found.append(content)
    return found


class Play:
    """場面を最初から再生する。Stopごとにhookの足跡を作り、計画・材料・判定結果を通す。"""

    def __init__(self, case, name):
        self.directory = os.path.join(SCENES, name)
        self.expected = _load(os.path.join(self.directory, "expected.json"))
        tmp = tempfile.TemporaryDirectory()
        case.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.state = os.path.join(self.root, "logs", "state")
        os.makedirs(self.state)
        self.transcript = os.path.join(self.root, "t.jsonl")
        declared = "enable"
        place = os.path.join(self.directory, "settings.json")
        if os.path.isfile(place):
            declared = _load(place)["stop_review"]
        self.conf = settings.Settings(
            state=self.state,
            log=os.path.join(self.root, "logs", "decisions.jsonl"),
            stop_review_agent=True,
            stop_review=declared,
            tickets="wip/proposals",
        )
        first = os.path.join(self.directory, "stop-1")
        if os.path.isdir(first):
            names = sorted(n for n in os.listdir(self.directory) if n.startswith("stop-"))
            self.stops = [os.path.join(self.directory, n) for n in names]
        else:
            self.stops = [self.directory]
        self.prompted = 0
        self.calls = 0
        self.repo = False

    # ---- hookの足跡

    def _hook(self, event, **fields):
        fields.setdefault("session_id", SESSION)
        fields.setdefault("cwd", self.root)
        events._track_proposals(self.conf, hookio.Input(event=event, **fields))

    def _git(self, *args):
        done = subprocess.run(
            ["git", "-C", self.root, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
             "-c", "commit.gpgsign=false", *args],
            capture_output=True, text=True, encoding="utf-8", check=True,
        )  # fmt: skip
        return done.stdout.strip()

    def _commit(self):
        if not self.repo:
            self._git("init", "-q")
            self.repo = True
        self._git("add", "-A", "wip")
        self._git("commit", "-q", "--allow-empty", "-m", "approval-request")
        return self._git("rev-parse", "HEAD")

    def _expand(self, text, commit):
        text = text.replace("{now}", str(int(time.time()))).replace("{commit}", commit)
        return re.sub(
            r"\{blob:([^}]+)\}", lambda m: self._git("rev-parse", f"HEAD:{m.group(1)}"), text
        )

    def stage(self, folder):
        """このStopまでに起きたこと（ユーザの発言・提案の書き込み・approval-requestの実行）を再現する。"""
        shutil.copyfile(os.path.join(folder, "transcript.jsonl"), self.transcript)
        prompts = _prompts(self.transcript)
        for text in prompts[self.prompted :]:
            self._hook(hookio.USER_PROMPT_SUBMIT, prompt=text)
        self.prompted = len(prompts)
        drafts = os.path.join(folder, "proposals")
        for name in sorted(os.listdir(drafts)) if os.path.isdir(drafts) else []:
            ident = os.path.splitext(name)[0]
            path = os.path.join(self.root, *TODO.split("/"), f"{ident}.md")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.copyfile(os.path.join(drafts, name), path)
            self._hook(
                hookio.POST_TOOL_USE, tool_name="Write", tool_input={"file_path": path},
                tool_use_id=f"w{self.calls}-{ident}",
            )  # fmt: skip
        kv = os.path.join(folder, "propose.kv")
        if os.path.isfile(kv):
            commit = self._commit()
            self.calls += 1
            tool_use_id = f"tu{self.calls}"
            body = self._expand(_read(kv), commit)
            ident = re.search(r"^id=(.+)$", body, re.MULTILINE).group(1)
            command = f"sh .ccnavi/scripts/ccnavi-ticket.sh approval-request {ident}"
            call = dict(tool_name="Bash", tool_input={"command": command}, tool_use_id=tool_use_id)
            self._hook(hookio.PRE_TOOL_USE, **call)
            directory = os.path.join(self.state, stopops.RESULT_DIR)
            os.makedirs(directory, exist_ok=True)
            place = os.path.join(directory, f"{tool_use_id}.kv")
            with open(place, "w", encoding="utf-8") as handle:
                handle.write(body)
            self._hook(hookio.POST_TOOL_USE, **call)

    # ---- Stopの計画・材料・判定結果

    def plan(self, continued):
        payload = hookio.Input(
            event=hookio.STOP, session_id=SESSION, transcript_path=self.transcript,
            cwd=self.root, stop_hook_active=continued,
        )  # fmt: skip
        continuation = events._ai_continuation(self.conf, payload)
        err = io.StringIO()
        events._plan_stop_review(
            err, self.conf, self.root, payload, modes.ENABLE, False, continuation, None
        )  # fmt: skip
        self.errors = err.getvalue()
        self.key = sr.shared_key(SESSION, self.transcript)
        return sr.read_plan(self.state, self.key)

    def _cli(self, verb, stdin=""):
        out = io.StringIO()
        code = cli_stopreview.run(
            io.StringIO(stdin), out, io.StringIO(), self.conf,
            ["stop-review", verb, SESSION, self.transcript], self.root,
        )  # fmt: skip
        assert code == 0
        return out.getvalue()

    def material(self):
        return self._cli("material")

    def record(self, result_text):
        return json.loads(self._cli("record", result_text))

    def judge_text(self, plan, text):
        """判定結果の雛形に、試行IDと提案の対象キーを入れる。"""
        keys = {p["ident"]: p["key"] for p in plan.get("proposals") or []}
        text = text.replace("$ATTEMPT", plan["attempt_id"])
        return re.sub(r"\$PROPOSAL:([\w.-]+)", lambda m: keys[m.group(1)], text)

    def attempt(self, plan):
        return sr.read_attempt(self.state, plan["attempt_id"])


def notation(plan):
    """対象キーを、期待の書き方（stop・proposal:<識別子>）にする。"""
    names = {p["key"]: f"proposal:{p['ident']}" for p in plan.get("proposals") or []}
    return lambda key: names.get(key, key)


def ideal_result(plan, criteria):
    """正しい判定役が出すはずの結果。基準ごとの期待（`criteria`）から組む。"""
    keys = {f"proposal:{p['ident']}": p["key"] for p in plan.get("proposals") or []}
    rows = []
    for criterion, per_target in criteria.items():
        for target, outcome in per_target.items():
            row = {"criterion": criterion, "target": keys.get(target, target), "result": outcome}
            if outcome == sr.FAIL:
                kind = "final_answer" if target == "stop" else "proposal"
                row["locations"] = [{"kind": kind, "text": "場面データの期待"}]
                if criterion in sr.FLAW_CRITERIA:
                    fix = FIXES[criterion]
                    row["fixes"] = [{"kind": fix, "text": "場面データの期待"}]
            rows.append(row)
    return json.dumps(
        {"attempt_id": plan["attempt_id"], "classification": sr.CLASS_TICKET_WORK, "results": rows}
    )


class Record(NamedTuple):
    play: Play
    plan: dict[str, Any]
    material: str
    reply: dict[str, Any]
    want: dict[str, Any]
    attempt: dict[str, Any]  # recordの直後の試行（次のStopで状態が進む前）
    original: dict[str, Any] | None  # 再判定なら、recordの直後の元の試行


@unittest.skipIf(GIT is None, "git が無い")
class ScenarioTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(cli_stopreview, "PLAN_WAIT_SECONDS", 0.3)
        patch.start()
        self.addCleanup(patch.stop)

    def _judge_text(self, play, folder, plan, want, choice=None):
        """recordに渡す判定結果。場面データのファイルがあればそれ、無ければ期待から組む。"""
        path = os.path.join(folder, choice or "judge_result.json")
        if os.path.isfile(path):
            return play.judge_text(plan, _read(path))
        self.assertIsNone(choice, f"{choice} が無い")
        return ideal_result(plan, want["criteria"])

    def _play(self, name, override=None):
        """場面を再生する。`override` は (Stopの番号, 判定結果のファイル)。そのStopで止める。"""
        play = Play(self, name)
        records = []
        for index, folder in enumerate(play.stops):
            want = play.expected["stops"][index]
            play.stage(folder)
            plan = play.plan(continued=index > 0)
            self.assertEqual(play.errors, "")
            self.assertIsNotNone(plan, f"{name} の Stop {index + 1}: 計画が書かれていない")
            material = play.material()
            choice = override[1] if override and override[0] == index else None
            reply = play.record(self._judge_text(play, folder, plan, want, choice))
            original = sr.read_attempt(play.state, plan["original"]) if plan["rejudge"] else None
            records.append(Record(play, plan, material, reply, want, play.attempt(plan), original))
            if choice:
                break
        return records

    def _assert_outcome(self, record, want, label):
        plan, reply, attempt = record.plan, record.reply, record.attempt
        self.assertEqual(reply["ok"], want["reply_ok"], label)
        self.assertEqual(attempt["state"], want["state"], label)
        got = (attempt["final"], attempt["judge"])
        self.assertEqual(got, (want["final"], want["judge"]), label)
        if not reply["ok"]:
            self.assertIn(plan["attempt_id"], reply["reason"], label)
            for needle in want.get("reply_contains", []):
                self.assertIn(needle, reply["reason"], label)
        if "rejudge_result" in want:
            self.assertEqual(record.original["rejudge"], want["rejudge_result"], label)

    def _assert_plan(self, record, label):
        play, plan, material, want = record.play, record.plan, record.material, record.want
        nick = notation(plan)
        self.assertEqual(plan["is_candidate"], want["candidate"], label)
        self.assertEqual(plan["effective"], want["effective"], label)
        self.assertEqual([nick(t) for t in plan["targets"]], want["targets"], label)
        self.assertEqual(plan["refs"], want["refs"], label)
        routes = {p["ident"]: p["route"] for p in plan["proposals"]}
        self.assertEqual(routes, want.get("routes", {}), label)
        needs = sorted([c, nick(t)] for c, t in sr.required(plan))
        self.assertEqual(needs, sorted(want["required"]), label)
        self.assertEqual(plan["rejudge"], want.get("is_rejudge", False), label)
        if plan["rejudge"]:
            original = sr.read_attempt(play.state, plan["original"])
            self.assertEqual(original["state"], want["original_state"], label)
        for needle in want["must_contain"]:
            self.assertIn(needle, material, f"{label}: 材料に「{needle}」が無い")

    def check_scene(self, name):
        records = self._play(name)
        for index, record in enumerate(records):
            label = f"{name} の Stop {index + 1}"
            self._assert_plan(record, label)
            self._assert_outcome(record, record.want, label)
        expected, last = records[0].play.expected, records[-1]
        self.assertEqual(
            (last.want["final"], last.want["judge"]), (expected["final"], expected["judge"])
        )
        if "rejudge" in expected:
            self.assertEqual(last.want["rejudge_result"], expected["rejudge"])
        for alt in last.want.get("alternatives", []):
            label = f"{name}: {alt['name']}"
            record = self._play(name, (len(records) - 1, alt["judge_result"]))[-1]
            self._assert_outcome(record, alt, label)
            # 再判定やdry-runの不備検出では差し戻しが残らない。次のStopで消費されるものが無いため。
            self.assertIsNone(sr.consume_continuation(record.play.state, SESSION), label)


def _method(name):
    def run(self):
        self.check_scene(name)

    return run


for _name in scene_names():
    setattr(ScenarioTest, "test_scene_" + re.sub(r"\W", "_", _name), _method(_name))


def _folders(name):
    root = os.path.join(SCENES, name)
    found = [os.path.join(root, n) for n in sorted(os.listdir(root)) if n.startswith("stop-")]
    return found or [root]


class SceneDataTest(unittest.TestCase):
    def test_all_thirteen_scenes_are_present_and_well_formed(self):
        names = scene_names()
        self.assertEqual([n[:2] for n in names], [f"{i:02d}" for i in range(1, SCENE_COUNT + 1)])
        finals = {sr.FINAL_OK, sr.FINAL_FLAWED, sr.FINAL_UNCHECKED, sr.FINAL_OUT_OF_SCOPE}
        for name in names:
            with self.subTest(scene=name):
                expected = _load(os.path.join(SCENES, name, "expected.json"))
                self.assertEqual(expected["id"], name[:2])
                self.assertTrue(expected["title"].strip() and "\n" not in expected["title"])
                self.assertIn(expected["final"], finals)
                self.assertEqual(len(expected["stops"]), len(_folders(name)))
                for stop in expected["stops"]:
                    self.assertTrue(stop["must_contain"])
                    self.assertTrue(all(isinstance(s, str) and s for s in stop["must_contain"]))

    def test_the_judge_result_files_agree_with_the_expected_criteria(self):
        # 場面データの判定結果が、expected.jsonの基準ごとの期待と食い違っていないこと。
        for name in scene_names():
            expected = _load(os.path.join(SCENES, name, "expected.json"))
            for folder, want in zip(_folders(name), expected["stops"], strict=True):
                path = os.path.join(folder, "judge_result.json")
                if not os.path.isfile(path):
                    continue
                with self.subTest(scene=name, folder=os.path.basename(folder)):
                    result = _load(path)
                    if result["classification"] == sr.CLASS_NOT_TICKET_WORK:
                        self.assertEqual(want["final"], sr.FINAL_OUT_OF_SCOPE)
                        continue
                    got: dict[str, dict[str, str]] = {}
                    for row in result["results"]:
                        target = row["target"].replace("$PROPOSAL:", "proposal:")
                        got.setdefault(row["criterion"], {})[target] = row["result"]
                    self.assertEqual(got, want["criteria"])


if __name__ == "__main__":
    unittest.main()
