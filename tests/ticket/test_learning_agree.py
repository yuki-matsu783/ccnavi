"""承認画面の「この親に固定する定義」で、学びの蓄積を切った定義に `learning: off` を出す。

`learning` を書かない（既定の on の）定義には何も出さない。
"""

from __future__ import annotations

from tests.ticket.test_phases import PHASES, PhaseHarness

# 調査の定義だけ学びの蓄積を切る。コピーは phases.yml から取るので、両方に書く。
PHASES_LEARNING_OFF = PHASES.replace(
    "    agent: explorer\n", "    agent: explorer\n    learning: off\n", 1
)


def fixed_block(screen: str) -> str:
    """承認画面の「この親に固定する定義」の節だけを返す。"""
    return screen.split("■ この親に固定するフェーズ定義", 1)[1].split("■", 1)[0]


class LearningAgreeScreenTest(PhaseHarness):
    def setUp(self):
        super().setUp()
        self.phases_text = PHASES_LEARNING_OFF
        with open(self.phases, "w", encoding="utf-8", newline="\n") as f:
            f.write(PHASES_LEARNING_OFF)

    def test_a_definition_with_learning_off_shows_it_on_the_screen(self):
        result = self.family(plan=("research", "design"))
        fixed = fixed_block(result.stdout)
        research_line = [line for line in fixed.splitlines() if "調査 / research" in line]
        self.assertTrue(research_line, fixed)
        self.assertIn("learning: off", research_line[0])
        design_line = [line for line in fixed.splitlines() if "設計 / design" in line]
        self.assertTrue(design_line, fixed)
        self.assertNotIn("learning: off", design_line[0])

    def test_a_parent_without_learning_shows_nothing(self):
        self.phases_text = PHASES
        with open(self.phases, "w", encoding="utf-8", newline="\n") as f:
            f.write(PHASES)
        result = self.family(plan=("research", "design"))
        self.assertIn("■ この親に固定するフェーズ定義", result.stdout)
        self.assertNotIn("learning: off", result.stdout)
