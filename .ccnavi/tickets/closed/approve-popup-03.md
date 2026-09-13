---
version: 1
ticket: approve-popup-03
parent: approve-popup
phase: 3
predecessors:
- approve-popup-01
title: 承認ポップアップの実装（実行ファイル・hook・拡張）
rationale: '設計 wip/design/approve-popup.md §2・§3 の実装。実行ファイルに `--approve --preview
  --json` と

  `--approve --yes <識別子,…> --json` を足し、`--yes` は端末の壁を通さず、組み込み deny

  `builtin-guard-ticket-approval` から `--approve --preview` を除く。hook は新しい写しを

  セッションごとに 1 度 additionalContext で伝える。拡張はパレットの承認コマンドと

  ターミナル経路を消し、ボードのオーバーレイで preview を見せて `--yes` を子プロセスで打ち、

  承認後に「コピー」「新しいセッションで開く」の通知を出す。

  フェーズ 2 の受入テスト A1〜A14 を通し、拡張側 B1〜B6 をここで書いて通す。

  フィクスチャ approve-preview.json / approve-yes.json / approve-mismatch.json を書き出す。

  '
human_review:
  required: true
  reason: 人の合意を出す経路と、拡張の子プロセスの起こし方が変わる
allow:
- match: Write|Edit
  glob: ccnavi/*
- match: Write|Edit
  glob: tests/*
- match: Write|Edit
  glob: vscode-extension/*
started_at: 2026-09-12T19:29:34+0900
completed_at: 2026-09-12T20:06:41+0900
base_sha: baf05184fe9e35329f13f7a4fb0c71946d996d11
ccnavi_approved:
  approved_at: 2026-09-12T19:28:36+0900
  source_tree: approve-popup
  source_path: C:\Users\taniyama\Desktop\git\ccnavi\.claude\worktrees\approve-popup\wip\tickets\todo\approve-popup-03.md
---

# 承認ポップアップの実装

## 順序（設計 §6）

1. `approval.py` を「束を組む」「見せる」「承認する」に割り、`preview_json` / `approve_yes` / `news` を足す
2. `cli.py` に `--preview`、`--yes <値>`。`--yes` のときは `_from_terminal` を飛ばす
3. `phase._CLI_FORMS` の `--approve` から `--preview` を除く
4. `reasons.approved`（Claude Code に渡す文）と、`events` / `judge` で `news` を合流
5. `CCNAVI_BOARD_FIXTURE=1` でフィクスチャを書き出す
6. 拡張: `core/approvemodel.ts` → `ccnavi.ts` → `core/render.ts` → `board-panel.ts` → 消すもの
   （`ccnaviBoard.approve`、`approveCommand`、`sendApprove`）→ テスト CB-T69〜 → README
7. `uv run python -m unittest discover -s tests -t .` と `pnpm test`、拡張開発ホストで手動確認
