---
version: 1
ticket: approve-carry-06
parent: approve-carry
phase: 3
title: 実装する（承認の指紋を承認済みチケットに写る中身まで広げ、issue を画面に出す）
rationale: '2 回目の敵対的レビュー（2026-09-13）で、ボードの承認の指紋が承認画面の本文にしか掛からず、 画面に出ないのに承認済みチケットへ写る欄（issue、Markdown
  の本文、知らない frontmatter の欄）を 見せたあとに書き換えても承認が通ることが再現された。issue は MR の本文の Closes #<番号>
  に写る。 指紋を、本文に加えて束のチケットごとに書き出す中身まで覆うように広げ、承認画面に issue を出す。

  '
human_review:
  required: true
  reason: 承認の照合の約束を変えるため
allow:
- match: Write|Edit
  glob: ccnavi/*
- match: Write|Edit
  glob: tests/*
- match: Write|Edit
  glob: vscode-extension/*
started_at: 2026-09-13T23:36:24+0900
completed_at: 2026-09-13T23:52:25+0900
base_sha: de87bea5b516976f405f9223194eb84d9f4b4d8b
ccnavi_approved:
  approved_at: 2026-09-13T23:35:43+0900
  source_tree: approve-carry
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/approve-carry/wip/tickets/todo/approve-carry-06.md
---

# 実装する（承認の指紋の範囲）

## 入力

- 2 回目の敵対的レビューの再現: `/private/tmp/claude-501/-Volumes-Data-git-ccnavi/887b0287-fe6f-4d49-867f-138c9a212e6d/scratchpad/review2-approval/test_hidden_field_toctou.py`
- フェーズ 2 の approve-carry-05 の受入テスト 1〜5
- `ccnavi/approval.py` の `text_digest` / `approve_yes` / `screen`、`ccnavi/ticket.py` の `render`

## 成果物

- 指紋: 承認画面の本文 `text` と、束のチケットを順に `ticket.render`（承認の記録の欄を足す前）で書き出した中身を、区切りを挟んでつないだものの SHA-256（小文字 16 進）。preview の `digest` と `--yes` の照合の両方で同じ関数を使う。鍵の名前と引数の綴りは変えない
- 承認画面: 親が `issue:` を持つとき、その番号を 1 行出す
- 食い違いの文面は今のまま（識別子が同じなら「提案の中身が変わった」）。見出しの「本文」は「承認画面の本文と承認済みチケットに写る中身」の意味に直す
- 拡張のフィクスチャ（approve-preview.json ほか）を `CCNAVI_BOARD_FIXTURE=1` で書き直す。拡張のコードは指紋を中身を見ずに渡すだけなので、変わらないはず
- ruff、Python のテスト、拡張のテスト

## やらないこと

- 運ぶ sh（staging で直す）
- 文書（docs フェーズ）
