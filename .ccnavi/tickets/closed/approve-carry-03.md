---
version: 1
ticket: approve-carry-03
parent: approve-carry
phase: 3
title: 実装する（承認の超過の warn、判定の種類の切り詰め、ボードの端末送り、配布）
rationale: '設計 `wip/design/approve-carry.md` §5 の変更を入れる。承認で超過の 3 つを warn に下げて 束に載せ、承認画面と
  JSON に出す。判定の 3 か所（実行前・実行後の監視・SubagentStop）を phase.scope_verdict に寄せ、種類の scope でも切り詰めて上限を文面で名指しする。組み込みの
  deny に ccnavi-push-approved.sh を加える。ボードは承認のあと運ぶ sh を端末に送る。導入スクリプトで配る。 .ccnavi/scripts/
  の sh 2 本はエージェントが書けないので、ここでは作らず staging（フェーズ 4）に回す。

  '
human_review:
  required: true
  reason: 判定の範囲の決め方と承認の検査を変えるため
allow:
- match: Write|Edit
  glob: ccnavi/*
- match: Write|Edit
  glob: tests/*
- match: Write|Edit
  glob: scripts/*
- match: Write|Edit
  glob: vscode-extension/*
started_at: 2026-09-13T19:56:25+0900
completed_at: 2026-09-13T22:48:14+0900
base_sha: 0ae7d2d431160bb8db46ca00c81710d865df373b
ccnavi_approved:
  approved_at: 2026-09-13T19:55:55+0900
  source_tree: approve-carry
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/approve-carry/wip/tickets/todo/approve-carry-03.md
---

# 実装する

## 入力

- 設計 `wip/design/approve-carry.md`（§5 が入口の一覧）
- フェーズ 2 の受入テスト

## 成果物

- `ccnavi/phase.py`: `scope_verdict` / `type_for`、`scope_findings` の書き直し、`ticket_approval_rule` の広げ
- `ccnavi/judge.py`: `ticket_verdict` の書き直し、`limit:` 行、種類が読めないときの notice
- `ccnavi/post.py`: `ScopeGuard` の種類と `finding` の書き直し
- `ccnavi/subagent.py`: 差し戻しの上限の名指し
- `ccnavi/ticket.py` / `ccnavi/phasetypes.py`: 超過の severity を warn に
- `ccnavi/approval.py`: `Candidate.overflow`、`screen` の見出し、JSON の `overflow[]`
- `scripts/ccnavi-setup.sh`: `DEPLOY_SCRIPTS` に `ccnavi-push-approved.sh`
- `vscode-extension/ccnavi-board/src/`: `pushApprovedCommand`、`confirmApproval` の端末送り、sh が無いときの警告、
  通知の文、`approvemodel.ts` の `overflow[]`
- `vscode-extension/ccnavi-board/test/fixtures/approve-preview.json` の書き直し
- 拡張のテスト: `pushApprovedCommand` の綴り（設計 §6 の 26。受入テストの種類の範囲の外なのでここで書く）
- ruff と unittest、拡張のテストが通ること（`ccnavi-push-approved.sh` を要るテストは staging まで落ちてよい。
  落ちているものを閉じるときに名指しする）

## やらないこと

- `.ccnavi/scripts/ccnavi-push-approved.sh` と `.ccnavi/scripts/ccnavi-approve.sh`（staging で人が写す）
- 文書（README / ccnavi.md / requirements.md）は docs フェーズ
