---
version: 1
ticket: approve-popup-02
parent: approve-popup
phase: 2
predecessors: [approve-popup-01]
title: 承認ポップアップの受入テスト（実行ファイル側）
rationale: |
  設計 wip/design/approve-popup.md §5 の Python 側の受入テスト A1〜A14 を tests/ に書く。
  `--approve --preview --json` と `--approve --yes <識別子,…> --json` の形と exit、端末の壁を
  `--yes` が通らないこと、組み込み deny が `--yes` に当たり `--preview` に当たらないこと、
  hook が承認をセッションごとに 1 度だけ伝えること、フィクスチャの書き出し。
  実装（フェーズ 3）が無い間は失敗するテストとして置く。拡張側の B1〜B6 は
  vscode-extension/ の範囲がこの種類に無いので、フェーズ 3 で実装と一緒に書く。
human_review:
  required: true
  reason: 承認の経路の期待を固定するテスト
allow:
- match: Write|Edit
  glob: tests/*
started_at: ''
completed_at: ''
base_sha: ''
---

# 承認ポップアップの受入テスト

## 成果物

- `tests/test_approve_json.py`: A1〜A5、A14（`CCNAVI_APPROVE_FIXTURE=1` でフィクスチャを書き出す）
- `tests/test_phase.py` に A7、A8（組み込み deny の見本）
- `tests/test_approval_news.py`: A9〜A13（hook が伝える文と控え）
- A6（素の `--approve` は tty でない stdin で止まる）は既存のテストがあれば流用する

## 書き方

- `tests/test_board.py` の `scene()` と同じ手順で承認待ちの状態を作る
- hook の payload はファイルに書いてから読ませる（禁止語をコマンドに書かない）
- 期待する JSON の鍵は設計 §2.1 / §2.2 の表のとおり
