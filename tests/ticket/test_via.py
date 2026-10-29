"""`tickets.model.via`。起動の間だけ経路とアカウントを覚え、抜けると前の値へ戻す。"""

from __future__ import annotations

import unittest

from ccnavi.tickets.model import via


class ViaTest(unittest.TestCase):
    def test_a_session_sets_via_and_actor_and_restores_them(self):
        before = (via.via(), via.actor())
        with via.session(via.VIA_BOARD, "octo"):
            self.assertEqual((via.via(), via.actor()), (via.VIA_BOARD, "octo"))
            with via.session(via.VIA_CHROME):
                self.assertEqual((via.via(), via.actor()), (via.VIA_CHROME, ""))
            self.assertEqual((via.via(), via.actor()), (via.VIA_BOARD, "octo"))
        self.assertEqual((via.via(), via.actor()), before)

    def test_set_actor_ignores_an_empty_value_and_set_via_replaces(self):
        with via.session(via.VIA_CLI, "octo"):
            via.set_actor("")
            self.assertEqual(via.actor(), "octo")
            via.set_via(via.VIA_TERMINAL)
            self.assertEqual(via.via(), via.VIA_TERMINAL)


if __name__ == "__main__":
    unittest.main()
