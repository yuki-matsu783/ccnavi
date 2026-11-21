"""作業・レビュー計画のテンプレートの下書きの受入テスト。

見るのは次のとおり。

1. 下書きの置き場: 親のツリーの `wip/handoff/<取り込み先の相対パス>`。取り込み先は `--to`
   （既定は project）と `--layer` で決まる。取り込みは `ccnavi user handoff`（test_handoff.py）
2. `--templates --draft <親>`: 完了した親の計画から下書きを作る。`after` を計画の中の添字に直し、
   子のフローをコピーし、同名の下書きは上書きせず断る。取り込み先に同名のテンプレートがあれば断る。
   作業中・退避済み・取り消した親は読めないと言う
3. 下書きの効力: `--templates` の統合結果にも `templates[]` にも入らない。
   `--lint` は error にしない。一覧に件数、`--json` に `drafts[]`
4. `--draft` と読む側はエージェントが打てる。下書きの置き場への Write は判定が変わらず止まらない
"""

from __future__ import annotations

import hashlib
import json
import os
import textwrap
import types
import unittest

from tests import ROOT, common_path
from tests.inproc import run_ccnavi
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, write
from tests.ticketsample import PHASES, child_text, parent_text

FLOW_IMPL = textwrap.dedent(
    """\
    id: wf-impl
    name: 実装の手順
    version: 1.0.0
    nodes:
      - {id: s, type: start, name: 開始, position: {x: 0, y: 0}, data: {}}
      - {id: p, type: prompt, name: 実装, position: {x: 1, y: 0}, data: {prompt: 先にテスト}}
      - {id: e, type: end, name: 終了, position: {x: 2, y: 0}, data: {}}
    connections:
      - {id: c1, from: s, to: p, fromPort: output, toPort: input}
      - {id: c2, from: p, to: e, fromPort: output, toPort: input}
    """
)

# 一般化を待つ欄に下書きが付ける印。
TODO_MARK = "TODO"
GUARD_ENV = "CCNAVI_GUARD_TICKET_APPROVAL"


def digest(raw: bytes) -> str:
    """テンプレートのファイルの中身のハッシュ（保存の `--expect` と同じ）。"""
    return hashlib.sha256(raw).hexdigest()


def _items(raw_items):
    """テンプレートの計画のステップ（`--check-file` の `data` の形）を type・after・deferred に。"""
    out = []
    for item in raw_items or []:
        if isinstance(item, str):
            item = {"type": item}
        out.append(
            types.SimpleNamespace(
                type=item["type"],
                after=list(item.get("after", [])),
                deferred=bool(item.get("deferred", False)),
            )
        )
    return out


class Drafts(PhaseHarness):
    """完了した親（`done/`）を直接置いて、下書きと取り込みを動かす道具。"""

    # `--draft` の `--to`（下書きの置き場が決まる）。
    to = "project"

    def setUp(self):
        super().setUp()
        # 共通のフェーズ定義。取り込み先が共通のとき、この定義で検査される。
        write(common_path(self.root, "phases"), PHASES)

    def tpl(self, *args, guard=None):
        """`--templates` と `--lint` を動かす。

        PhaseHarness.ccnavi が足す判定のオプションは付けない。

        `guard` はターミナルの確かめ（CCNAVI_GUARD_TICKET_APPROVAL）。`--templates`
        はオプションでは渡せない。
        """
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        env.pop("CLAUDE_PROJECT_DIR", None)
        if guard is not None:
            env[GUARD_ENV] = guard
        return run_ccnavi(["--root", self.root, *args], env=env, cwd=ROOT)

    # ---- 完了した親

    def done_parent(
        self,
        plan,
        feedback=None,
        flows=None,
        parent="i0001",
        tree=None,
        place="done",
        children=None,
    ):
        """親と子を `done/` に置く。`flows` は {子の識別子: フローの本文}。

        `children` が無ければ、全体計画の n 番目に子 `<親>-<nn>-01` を 1 つずつ置く。
        """
        tree = tree or self.parent_tree
        base = os.path.join(tree, ".ccnavi", "approved")
        write(os.path.join(base, place, parent + ".md"), parent_text(parent, plan, feedback))
        if children is None:
            children = {f"{parent}-{n:02d}-01": n for n in range(1, len(plan) + 1)}
        for child, phase in children.items():
            write(
                os.path.join(base, place, child + ".md"),
                child_text(child, parent, phase, ("src/*",)),
            )
        for child, body in (flows or {}).items():
            write(os.path.join(base, "flows", child + ".yml"), body)

    def draft_path(self, name="i0001", tree=None, to=None):
        """下書きの置き場。取り込み先（`target_rel`）の前に `wip/handoff/` を付けたもの。"""
        return os.path.join(
            tree or self.parent_tree, "wip", "handoff", *target_rel(name, to or self.to).split("/")
        )

    def draft(self, parent="i0001", *extra):
        to = ("--to", self.to) if self.to != "project" else ()
        return self.tpl("--templates", "--draft", parent, *to, *extra)

    def read_draft(self, name="i0001"):
        with open(self.draft_path(name), "rb") as f:
            return f.read()

    def checked(self, name="i0001"):
        """下書きを `--templates --check-file` で読んだ答え。"""
        result = self.tpl("--templates", "--check-file", self.draft_path(name), "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        answer = json.loads(result.stdout)
        self.assertTrue(answer["valid"], answer)
        return answer

    def drafted(self, name="i0001"):
        """下書きの中身（名前・説明・計画・フロー）。"""
        data = self.checked(name)["data"]
        return types.SimpleNamespace(
            name=data["name"],
            description=data.get("description", ""),
            when=data.get("when", ""),
            plan=_items(data.get("plan")),
            feedback=_items(data.get("feedback")),
            flows={int(k): v for k, v in (data.get("flows") or {}).items()},
        )

    def generalize(self, name="i0001", description="機能を設計から実装まで進める"):
        """下書きの TODO を直す（エージェントが一般化したあとの形）。"""
        text = self.read_draft(name).decode("utf-8")
        fixed = text.replace(f"{TODO_MARK} 一般化する: 親", description).replace(
            f"{TODO_MARK} 一般化する: 「親」と同じ形の作業をするとき",
            "新しい機能を足すとき",
        )
        self.assertNotIn(TODO_MARK, fixed)
        write(self.draft_path(name), fixed)
        return digest(fixed.encode("utf-8"))

    # ---- 取り込み（ほかの保護対象と同じ `user handoff`）

    def handoff(self, rel, tree=None, guard="disable"):
        """下書き 1 本を `ccnavi user handoff <rel> --yes --expect <見せたハッシュ>` で取り込む。"""
        tree = tree or self.parent_tree
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        env.pop("CLAUDE_PROJECT_DIR", None)
        env[GUARD_ENV] = guard
        shown = run_ccnavi(["--root", tree, "user", "handoff", rel, "--json"], env=env, cwd=ROOT)
        self.assertEqual(shown.returncode, 0, shown.stdout + shown.stderr)
        expect = json.loads(shown.stdout)["expect"]
        return run_ccnavi(
            ["--root", tree, "user", "handoff", rel, "--yes", "--expect", expect, "--json"],
            env=env,
            cwd=ROOT,
        )

    def answer(self, *args):
        result = self.tpl("--templates", "--json", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)


def target_rel(name="i0001", to="project", layer=""):
    """下書きの取り込み先（ツリーからの相対）。"""
    if to == "common":
        return f".ccnavi/config/common/templates/{name}.yml"
    if layer:
        return f"projects/{layer}/.ccnavi/config/project/templates/{name}.yml"
    return f".ccnavi/config/project/templates/{name}.yml"


def slurp(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


class MakeTest(Drafts):
    def test_the_made_draft_reads_back_and_the_text_asks_for_generalizing(self):
        self.done_parent(
            ["research", "design", "implement"],
            feedback=["implement-feedback", "implement-feedback"],
            flows={"i0001-03-01": FLOW_IMPL},
        )
        result = self.draft()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("wip/handoff/.ccnavi/config/project/templates/i0001.yml", result.stdout)
        self.assertIn(
            "ccnavi-user-handoff.sh .ccnavi/config/project/templates/i0001.yml", result.stdout
        )
        self.assertIn(
            "全体計画 3 ステップ / フィードバック計画 2 ステップ / フロー 1 本", result.stdout
        )
        # 説明に TODO の印が残り、検査が warn を言う。次の手は 1 つで、未追跡の下書きが
        # ready を止めることも言う
        self.assertIn("warn", result.stdout)
        self.assertIn(f"`description` に {TODO_MARK} が残っている", result.stdout)
        self.assertIn(f"`when` に {TODO_MARK} が残っている", result.stdout)
        self.assertIn("未追跡", result.stdout)
        self.assertIn("ready が止まる", result.stdout)
        self.assertEqual(sum(line.startswith("次: ") for line in result.stdout.splitlines()), 1)
        template = self.drafted()
        self.assertEqual(template.name, "i0001")
        self.assertEqual([i.type for i in template.plan], ["research", "design", "implement"])
        self.assertEqual([i.after for i in template.plan], [[], [1], [2]])
        self.assertEqual(list(template.flows), [3])
        self.assertEqual(template.flows[3]["name"], "実装の手順")
        self.assertTrue(template.description.startswith(TODO_MARK))
        self.assertTrue(template.when.startswith(TODO_MARK))
        # 一般化して印を消せば、苦情は消える。
        self.generalize()
        self.assertEqual(self.answer()["drafts"][0]["problems"], [])

    def test_the_draft_is_taken_by_handoff_and_then_used(self):
        self.done_parent(["design", "implement"], flows={"i0001-02-01": FLOW_IMPL})
        self.assertEqual(self.draft().returncode, 0)
        sha = self.generalize()
        taken = self.handoff(target_rel())
        self.assertEqual(taken.returncode, 0, taken.stdout + taken.stderr)
        placed = os.path.join(self.parent_tree, *target_rel().split("/"))
        with open(placed, "rb") as f:
            self.assertEqual(digest(f.read()), sha)
        self.assertFalse(os.path.exists(self.draft_path()))


class EffectTest(Drafts):
    """下書きには効力が無い。統合結果にも一覧にも入らず、lint の error にもならない。"""

    def test_a_draft_is_listed_apart_from_the_templates(self):
        out = self.tpl("--templates").stdout
        self.assertNotIn("下書き", out)
        self.done_parent(["design"])
        self.draft()
        # 統合結果にも合計にも入らず、`--show` でも引けない
        answer = self.answer()
        self.assertEqual(answer["templates"], [])
        self.assertEqual([row["name"] for v in answer["layers"] for row in v["templates"]], [])
        self.assertEqual(answer["sums"][0]["templates"], [])
        self.assertEqual(self.tpl("--templates", "--show", "i0001").returncode, 1)
        # 一覧は下書きがあることを 1 行で言う
        out = self.tpl("--templates").stdout
        self.assertIn("下書き: 1 件（wip/handoff/）", out)
        self.assertEqual(sum("下書き" in line for line in out.splitlines()), 1)
        # 語で絞ると下書きも絞られる
        self.assertEqual(len(self.answer("--keyword", "親")["drafts"]), 1)
        self.assertEqual(self.answer("--keyword", "無い語")["drafts"], [])

    def test_lint_never_makes_a_draft_an_error(self):
        write(self.draft_path("broken"), "name: [\n")
        write(self.draft_path("ends"), plan_template("ends", "  - design\n  - acceptance\n"))
        self.done_parent(["design"])
        self.draft()
        result = self.tpl("--lint", "--json", "--mode", "enable")
        found = [
            p for p in json.loads(result.stdout)["problems"] if p["where"] == "(template-drafts)"
        ]
        self.assertTrue(found)
        self.assertEqual({p["severity"] for p in found} - {"warn", "info"}, set())
        self.assertIn("warn", {p["severity"] for p in found})
        details = " ".join(p["detail"] for p in found)
        self.assertIn("wip/handoff/.ccnavi/config/project/templates/broken.yml", details)
        self.assertIn(TODO_MARK, details)
        # ほかの苦情（効力のある側）には混ざらない。
        others = [p for p in json.loads(result.stdout)["problems"] if "templates" in p["where"]]
        self.assertEqual([p for p in others if p["where"] != "(template-drafts)"], [])
        self.assertEqual(json.loads(result.stdout)["version"], 1)
        # 文の出力でも error にならない
        text = self.tpl("--lint", "--mode", "enable").stdout
        self.assertIn("template-drafts", text)
        self.assertNotIn("error: (template-drafts)", text)

    def test_a_draft_in_a_project_tree_is_found_at_the_projects_place(self):
        # 下書きの行でないファイルは一覧に出ない
        write(os.path.join(self.root, "wip", "handoff", "learnings", "x.md"), "x\n")
        write(os.path.join(self.root, "wip", "handoff", ".ccnavi", "docs", "a.md"), "a\n")
        self.assertEqual(self.answer()["drafts"], [])
        project = os.path.join(self.root, "projects", "lib")
        os.makedirs(os.path.join(project, ".git"))
        write(
            self.draft_path("lib-draft", tree=project), plan_template("lib-draft", "  - design\n")
        )
        (row,) = self.answer()["drafts"]
        self.assertEqual(row["checked_with"], "lib")
        self.assertEqual(row["layer"], "lib")
        self.assertEqual(row["tree"], "lib")
        self.assertEqual(row["path"], "wip/handoff/.ccnavi/config/project/templates/lib-draft.yml")


def plan_template(name, plan, description="試し"):
    return f"name: {name}\ndescription: {description}\nplan:\n{plan}"


class JudgmentUnchangedTest(Drafts):
    """判定・組み込みの保護・rules.yml は変えない。"""

    def test_the_draft_place_is_not_part_of_the_post_check(self):
        write(self.draft_path("x"), "name: x\n")
        ok = write(os.path.join(self.parent_tree, "wip", "a.md"), "a\n")
        result = self.hook("PostToolUse", "Write", self.parent_tree, file_path=ok, content="a")
        said = result.stdout + result.stderr
        self.assertNotIn("POST_TICKET_SCOPE", said)
        self.assertNotIn("POST_VIOLATION", said)

    def test_a_draft_is_not_read_as_a_ticket(self):
        write(self.draft_path("x"), plan_template("x", "  - design\n"))
        git(self.parent_tree, "add", "-A")
        result = self.ccnavi("--explain", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["tickets"], [])


if __name__ == "__main__":
    unittest.main()
