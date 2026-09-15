---
version: 1
ticket: approve-carry-02
parent: approve-carry
phase: 2
title: 受入テストを書く（運ぶ sh、承認の超過の warn、判定の種類の切り詰め）
rationale: '設計 `wip/design/approve-carry.md` §6 の振る舞いを受入テストにする。 承認で超過が warn になり束に載ること、判定が種類の
  scope でも切り詰めて DENY_TICKET_SCOPE / POST_TICKET_SCOPE / SubagentStop の差し戻しになること、dry-run
  で言って通ること、 ccnavi-push-approved.sh の運び方と終了コード、エージェントから止まること、導入スクリプトが配ること。 実装より先に書き、今の実装では落ちることを確かめる。

  '
human_review:
  required: true
  reason: 承認の検査を緩める変更の受入条件を決めるため
allow:
- match: Write|Edit
  glob: tests/*
started_at: 2026-09-13T19:56:24+0900
completed_at: 2026-09-13T20:21:33+0900
base_sha: 0ae7d2d431160bb8db46ca00c81710d865df373b
ccnavi_approved:
  approved_at: 2026-09-13T19:55:55+0900
  source_tree: approve-carry
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/approve-carry/wip/tickets/todo/approve-carry-02.md
---

# 受入テストを書く

## 入力

- 設計 `wip/design/approve-carry.md`（§6 が一覧、§1〜§4 が中身）
- 既存のテスト: `tests/test_phases.py`（`test_child_must_fit_the_phase_type`）、
  `tests/test_approve_json.py`（`test_preview_lists_the_batch_and_does_not_place_copies`）、
  `tests/test_ticket.py`（`test_cli_paths_are_denied_from_the_shell_unless_disabled`）、
  `tests/test_clean_sh.py`（sh を一時の git で走らせる形）、`tests/test_setup.py`、
  `tests/test_sh_portability.py`

## 成果物

- 設計 §6 の 1〜25 を受入テストに書く。既存のテストで振る舞いが逆になるもの（承認を拒む → 通す）は書き直す
- `ccnavi-push-approved.sh` のテストは新しいファイル（例: `tests/test_push_approved_sh.py`）に置く。
  sh が無い間は落ちる
- §6 の 26（拡張の `pushApprovedCommand`）は、受入テストの種類の範囲（`tests/*`）の外なので
  実装フェーズ（approve-carry-03）で書く
- 今の実装で落ちるテストの一覧を閉じるときに記録する

## やらないこと

- `ccnavi/`、`scripts/`、`.ccnavi/scripts/`、拡張の `src/` には触らない
- テストを通すための実装はしない
