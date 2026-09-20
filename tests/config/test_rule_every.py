"""ルールの `every`（渡す回の刻み）の受入テスト。道具を外から叩いて応答だけを見る。

`every: N` が刻むのは「渡す回」で、当たった回数が `N` の倍数になった回だけが渡す回に
なる。`every` が無ければ当たるたびが渡す回。2 つの文はどちらもその「渡す回」を基準に
読み直す。

| 欄 | いつ渡るか | `every: 5` のとき |
|---|---|---|
| `additionalContext` | 渡す回のたび | 5・10・15…回目 |
| `additionalContextOnce` | 渡す回の最初の 1 回 | 5 回目だけ |

`every` を「どちらの文に掛かるか」ではなく「渡す回の刻み」と決めるので、掛かる先の
例外を覚えなくてよい。ここを先に固定しておかないと、`additionalContextOnce` が
1 回目に届く形へ戻る。

数えの置き場は `additionalContextOnce` の控えと同じ（`logs/state/once-<session>-<agent>.json`）で、
文脈の分け方も同じ（セッションと、サブエージェントならその 1 回の起動）。控えを置く場所が
無いとき（`--state ""`）は毎回渡す（`ctxfile.py` の既存の決まり）。

実装はまだ無い。このテストは実装フェーズ（i0055 のフェーズ 2）が緑にする。
`--lint` の検査は tests/core/test_lint_every.py。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest

from tests import ROOT, common_path
from tests.inproc import run_ccnavi

EVERY = "ここまでに 5 件の編集があった。ルールと突き合わせること。"
ONCE = "突き合わせの観点は docs/adr/ を見る。"


def rule(name: str, **extra) -> dict:
    """狭い allow。広い allow の warn（既存の検査）に引っかからない形で書く。"""
    return {"id": name, "match": "Write", "glob": "*/src/*", **extra}


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class EveryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = self.dir.name
        self.state = os.path.join(self.root, "state")
        self.last: subprocess.CompletedProcess | None = None
        self.addCleanup(self.dir.cleanup)

    def rules(self, *allow: dict) -> str:
        """共通層の既定の場所にルールを 1 本置く。

        `--rules` では渡さない。あれは診断でだけ効き、hook の判定には届かない
        （ADR-0067）。
        """
        body = {
            "version": 1,
            "deny": [
                {"id": "push", "match": "Bash", "glob": "*git push*", "message": "push は人が行う"}
            ],
            "allow": list(allow),
        }
        return write(common_path(self.root, "rules"), json.dumps(body))

    def run_ccnavi(
        self, *args: str, payload: str = "", state: str | None = None
    ) -> subprocess.CompletedProcess:
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        return run_ccnavi(
            [
                "--root",
                self.root,
                "--log",
                "",
                "--state",
                self.state if state is None else state,
                "--approved",
                "",
                *args,
            ],
            input=payload,
            cwd=ROOT,
            env=environment,
        )

    def hit(
        self,
        *,
        session: str = "s1",
        agent: str = "",
        state: str | None = None,
    ) -> str:
        """ルールに 1 回当てて、モデルへ渡った文。渡らなければ空。"""
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "session_id": session,
                "agent_id": agent,
                "cwd": self.root,
                "tool_input": {"file_path": os.path.join(self.root, "src", "a.py")},
            }
        )
        done = self.run_ccnavi("--mode", "enable", payload=payload, state=state)
        self.last = done
        self.assertEqual(done.returncode, 0, done.stderr)
        if not done.stdout.strip():
            return ""
        try:
            return json.loads(done.stdout)["hookSpecificOutput"].get("additionalContext", "")
        except (ValueError, KeyError) as exc:
            self.fail(f"標準出力が期待した JSON ではない: {exc}\nstdout: {done.stdout!r}")

    def hits(self, times: int, **kw) -> list[str]:
        return [self.hit(**kw) for _ in range(times)]

    def saved(self) -> str:
        """控えに残った本文を全部つないだもの。何も残っていなければ空。"""
        if not os.path.isdir(self.state):
            return ""
        bodies = []
        for name in sorted(os.listdir(self.state)):
            # 控えは once-<session>-<agent>.json。同じ置き場に selfguard/ などもある。
            if not name.startswith("once-") or not name.endswith(".json"):
                continue
            with open(os.path.join(self.state, name), encoding="utf-8") as f:
                bodies.append(f.read())
        return "\n".join(bodies)

    # --- 渡す回の刻み -------------------------------------------------------

    def test_every_delivers_on_the_multiples_only(self):
        """`every: 5` は 5 回目と 10 回目に渡し、その間は渡さない。"""
        self.rules(rule("src", additionalContext=EVERY, every=5))
        self.assertEqual(self.hits(10), ["", "", "", "", EVERY, "", "", "", "", EVERY])

    def test_once_comes_on_the_first_delivering_hit_and_not_again(self):
        """`additionalContextOnce` は渡す回の最初の 1 回（5 回目）だけ。10 回目には出ない。"""
        self.rules(rule("src", additionalContext=EVERY, additionalContextOnce=ONCE, every=5))
        got = self.hits(10)
        self.assertEqual(got[:4], ["", "", "", ""])
        self.assertEqual(got[4], f"{EVERY}\n\n{ONCE}")
        self.assertEqual(got[5:9], ["", "", "", ""])
        self.assertEqual(got[9], EVERY)

    def test_rules_without_every_keep_the_old_timing(self):
        """`every` を書かないルールは今までどおり。毎回渡し、`Once` は 1 回目。"""
        self.rules(rule("src", additionalContext=EVERY, additionalContextOnce=ONCE))
        self.assertEqual(self.hits(3), [f"{EVERY}\n\n{ONCE}", EVERY, EVERY])

    def test_every_one_is_the_same_as_no_every(self):
        """`every: 1` と `every` 無しは同じ結果になる。"""
        # 2 本を同じ場所に置くので、先に片方を回してから置き換える。
        self.rules(rule("src", additionalContext=EVERY, additionalContextOnce=ONCE, every=1))
        one = self.hits(3, session="one")
        self.rules(rule("src", additionalContext=EVERY, additionalContextOnce=ONCE))
        self.assertEqual(one, self.hits(3, session="plain"))

    def test_once_alone_with_every_speaks_once_at_the_nth(self):
        """`additionalContextOnce` だけを持つ `every: 5` は、5 回目に 1 度だけ渡す。"""
        self.rules(rule("src", additionalContextOnce=ONCE, every=5))
        self.assertEqual(self.hits(10), ["", "", "", "", ONCE, "", "", "", "", ""])

    def test_file_bodies_follow_the_field_they_belong_to(self):
        """`additionalContextFile` は `additionalContext` に、`OnceFile` は `Once` に従う。"""
        write(os.path.join(self.root, "docs", "every.md"), "渡す回のたびの本文")
        write(os.path.join(self.root, "docs", "once.md"), "最初の渡す回だけの本文")
        self.rules(
            rule(
                "src",
                additionalContext=EVERY,
                additionalContextFile="docs/every.md",
                additionalContextOnce=ONCE,
                additionalContextOnceFile="docs/once.md",
                every=5,
            )
        )
        got = self.hits(10)
        self.assertEqual(
            got[4], f"{EVERY}\n\n渡す回のたびの本文\n\n{ONCE}\n\n最初の渡す回だけの本文"
        )
        self.assertEqual(got[9], f"{EVERY}\n\n渡す回のたびの本文")
        self.assertEqual([g for i, g in enumerate(got) if i not in (4, 9)], [""] * 8)

    # --- 控え ---------------------------------------------------------------

    def test_the_count_is_split_by_session_and_agent(self):
        """数えはセッションと `agent_id` で分かれる。サブエージェントは自分の数えを持つ。"""
        self.rules(rule("src", additionalContextOnce=ONCE, every=3))
        self.assertEqual(self.hits(3, session="s1"), ["", "", ONCE])
        # 別のセッションは 0 から数える。
        self.assertEqual(self.hits(2, session="s2"), ["", ""])
        # 同じセッションのサブエージェントも自分の数えを持つ。
        self.assertEqual(self.hits(3, session="s1", agent="a1"), ["", "", ONCE])
        # 子の 3 回は親の数えを進めていない（親の 4 回目は渡す回ではない）。
        self.assertEqual(self.hit(session="s1"), "")

    def test_an_old_array_state_file_still_reads(self):
        """古い形（配列）の控えを読んでも落ちず、渡した控えとして読む。"""
        write(os.path.join(self.state, "once-s1-main.json"), json.dumps({"given": ["src"]}))
        self.rules(rule("src", additionalContextOnce=ONCE))
        self.assertEqual(self.hit(session="s1"), "")
        self.assertNotIn("控えを読めない", (self.last.stderr if self.last else ""))
        # 控えの無いセッションには今までどおり渡る。
        self.assertEqual(self.hit(session="s2"), ONCE)

    def test_no_state_dir_delivers_every_time(self):
        """`--state ""` のときは数えを覚えられないので、渡す回を刻まず毎回渡す。"""
        self.rules(rule("src", additionalContext=EVERY, additionalContextOnce=ONCE, every=5))
        self.assertEqual(self.hits(3, state=""), [f"{EVERY}\n\n{ONCE}"] * 3)

    def test_rules_with_neither_every_nor_once_leave_no_count(self):
        """`every` も `Once` も持たないルールは、控えに数えを書かない。"""
        self.rules(rule("plain", additionalContext=EVERY))
        self.assertEqual(self.hits(3), [EVERY] * 3)
        self.assertNotIn("plain", self.saved())


if __name__ == "__main__":
    unittest.main()
