---
version: 1
ticket: approve-popup-01
parent: approve-popup
phase: 1
title: 承認ポップアップの設計（JSON の形・hook の控え・組み込み deny・オーバーレイの流れ）
rationale: '親チケットで決めた 11 の決定を、実装が迷わない粒度の設計文書 1 枚にする。

  決めるのは、`--approve --preview --json` と `--approve --yes --tickets … --json` の JSON
  の形、

  hook が「伝えた」を控える形（セッションごとに 1 度）、Bash で `--approve --yes` を止める

  組み込み deny の regex、ボードのオーバーレイの操作の流れと承認後の通知の文面。

  '
human_review:
  required: true
  reason: 人の合意を出す経路の壁の形を決める文書
allow:
- match: Write|Edit
  glob: wip/design/*
started_at: 2026-09-12T19:00:18+0900
completed_at: 2026-09-12T19:06:24+0900
base_sha: a8e25a29bc1475a9d178b995a2b54eb34fbdf702
ccnavi_approved:
  approved_at: 2026-09-12T18:58:35+0900
  source_tree: approve-popup
  source_path: C:\Users\taniyama\Desktop\git\ccnavi\.claude\worktrees\approve-popup\wip\tickets\todo\approve-popup-01.md
---

# 承認ポップアップの設計

## 成果物

`wip/design/approve-popup.md`。次を含む。

1. `--approve --preview --json` の形: `version` / `batch[]`（識別子・題・親・フェーズ・本文の行）/
   `rejected[]`（識別子と理由）/ `problems[]`。`screen()` の本文をそのまま `text` で持つか、
   項目に分けるかを決める
2. `--approve --yes --tickets <識別子,…> --json` の形: 承認した識別子、置いた写しのパス、
   `prompt`（Claude Code に渡す文）。束が一覧と違うときの返し方（exit 1 と JSON の `mismatch`）
3. hook の控え: `state/told-<session>-<agent>.json` に伝えた識別子を持ち、セッションの最初の
   hook 時点にあった写しを起点として控える形。UserPromptSubmit と PreToolUse の両方で読む
4. 組み込み deny: `(^|\x00)` から始まる ccnavi の呼び出しで `--approve` と `--yes` が同時に付く
   形の regex。見本（当たる / 当たらない）を添える
5. 拡張: オーバーレイの状態遷移（読み込み中 / 表示 / 承認中 / 済み / 食い違い）、
   `--yes` を打つ子プロセスの起こし方（`ccnavi.ts` の既存の run に寄せる）、承認後の通知の
   2 ボタンと `vscode://anthropic.claude-code/open?prompt=` の組み方、消すもの
   （`ccnaviBoard.approve`、`approveCommand`、`runInTerminal` の承認経路）
6. 文書に触る場所の一覧（README「承認の JSON」、拡張の README、ccnavi.md §17 / §24.10、HANDOVER）
