---
version: 1
ticket: approve-carry-01
parent: approve-carry
phase: 1
title: 設計を書く（承認済みチケットを運ぶ sh の切り出し、範囲の超過を判定で止める）
rationale: '親チケット approve-carry の決定を `wip/design/approve-carry.md` に書く。 ccnavi-approve.sh
  から切り出す ccnavi-push-approved.sh の中身と呼ばれ方、ボードが端末に送る形、 承認で warn に下げる検査、判定でフェーズの種類の
  scope を切り詰める形と文面、dry-run での伝え方を決め、 実装の入口ごとに何を変えるかを 1 行ずつ添える。コードには触らない。

  '
human_review:
  required: true
  reason: 承認の検査を緩め、判定の範囲の決め方を変える設計のため
allow:
- match: Write|Edit
  glob: wip/design/*
started_at: 2026-09-13T19:43:08+0900
completed_at: 2026-09-13T19:47:42+0900
base_sha: 3800df28a45981087e96d249416726419e6f7942
ccnavi_approved:
  approved_at: 2026-09-13T19:41:25+0900
  source_tree: approve-carry
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/approve-carry/wip/tickets/todo/approve-carry-01.md
---

# 設計を書く

## 親の承認のあとに人が決めたこと（2026-09-13）

- 切り出す sh の名前は `ccnavi-push-approved.sh`。承認済みチケットをコミットして親のブランチへ push する
  という処理をそのまま名前にする。承認済みチケットを取ってくる `ccnavi-fetch.sh` と対になる
- `ccnavi-push-approved.sh` はエージェントに打たせない。`ccnavi-approve.sh` と同じく組み込みの deny
  （`builtin-guard-ticket-approval`）で止め、運ぶのは常に人の手にする

## 入力

- 親チケット `.ccnavi/tickets/approve-carry.md` の決定一覧
- `.ccnavi/scripts/ccnavi-approve.sh`、`.ccnavi/scripts/ccnavi-fetch.sh`
- `vscode-extension/ccnavi-board/src/board-panel.ts`（`confirmApproval`、`accept` の端末送り）、
  `src/core/commands.ts`
- `ccnavi/approval.py`（`validate`、`_candidates`）、`ccnavi/ticket.py`（`subset_problems`、`combine`）、
  `ccnavi/phasetypes.py`（`scope_problems`）、`ccnavi/phase.py`（組み込みの deny `ticket_approval_rule`）
- 判定の範囲の合成（`ccnavi/judge.py` ほか）、`ccnavi/subagent.py` の範囲外の差し戻し
- ccnavi.md §9（承認）、README「承認の JSON」

## 成果物

- `wip/design/approve-carry.md`。次を含む
  - `ccnavi-push-approved.sh`: 引数、数えるツリー、コミットするパス、push しないブランチ、終了コード、
    ワークスペースルートの探し方。`ccnavi-approve.sh` からの呼び方
  - 組み込みの deny に `ccnavi-push-approved.sh` を加える形と文面
  - ボード: `--approve --yes` が通ったあとに端末へ送る 1 行、送る cwd、承認が 0 件のときは送らないこと
  - 承認: warn に下げる 3 つ（親の範囲・種類の scope・regex）と、error のまま残すものの一覧。
    承認画面の本文と `--preview --json` の `batch[]` での警告の見せ方
  - 判定: 子の範囲を種類の scope で切り詰める合成の順（子・種類・親）、種類が読めないときの倒し方、
    regex の子の扱い、deny / ask のどちらになるか、文面で名指しする上限、dry-run での言い方
  - `subagent.py` の差し戻しが種類の scope を数えるか
  - 実装の入口ごとの変更点の一覧（1 行ずつ）と、受入テストで押さえる振る舞いの一覧
  - requirements.md に足す・直す REQ の候補（文面だけ）

## やらないこと

- コードと文書本体には触らない
- phases.yml の scope は変えない（人の持ち物）
