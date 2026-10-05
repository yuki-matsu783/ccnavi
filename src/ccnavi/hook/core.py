"""判定のコアの入口。名前を再エクスポートするだけの薄いファサード。

実体は 4 つのモジュールに分けてある。呼び手（`entry.cli` と Chrome 拡張）は
`core.<名前>` のまま引く。

- `core_base`: 入力（Snapshot・Actor）・判定（judge_approval）・書くもの（Changes・plan）・
  Writer（write_fs）
- `core_approve`: 手元の承認の入口（approve・preview・verify・approve_yes）
- `core_review`: レビュー済み（confirm・confirm_local・reviewable・requested_head・moved_on_host）
- `core_withdraw`: 承認の取り下げ（withdraw・withdrawable）

分けた先のモジュールは `core` を読まない（読むと循環する。tests/core/test_module_layers.py）。
`mock.patch.object(cli.core, "approve")` のように、呼び手が引く `core` の名前を差し替えられる。
"""

from __future__ import annotations

from . import core_approve, core_base, core_review, core_withdraw

Actor = core_base.Actor
Snapshot = core_base.Snapshot
read_fs = core_base.read_fs
Verdict = core_base.Verdict
judge_approval = core_base.judge_approval
CREATE = core_base.CREATE
UPDATE = core_base.UPDATE
DELETE = core_base.DELETE
Changes = core_base.Changes
plan = core_base.plan
write_fs = core_base.write_fs
Checked = core_base.Checked
reviewed_mark = core_base.reviewed_mark

approve = core_approve.approve
preview = core_approve.preview
verify = core_approve.verify
approve_yes = core_approve.approve_yes

confirm = core_review.confirm
confirm_local = core_review.confirm_local
reviewable = core_review.reviewable
requested_head = core_review.requested_head
moved_on_host = core_review.moved_on_host

withdraw = core_withdraw.withdraw
withdrawable = core_withdraw.withdrawable
