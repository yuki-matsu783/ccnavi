---
version: 1
ticket: approve-carry-05
parent: approve-carry
phase: 2
title: 受入テストを足す（2 回目の敵対的レビューの指摘）
rationale: '2 回目の敵対的レビュー（2026-09-13、Sonnet 5 本）で見つかった穴を受入テストにする。 承認の指紋が承認済みチケットに写る欄（issue、本文）を覆っていないこと、指紋のテストが超過の行の
  変化と大文字の指紋を踏んでいないこと、運ぶ sh の環境変数の名前が CCNAVI_TICKETS_APPROVED で あること、1 本のツリーの git add
  の失敗で全体が止まること、作業ツリーの置き場のシンボリックリンクを 運ぶこと。

  '
human_review:
  required: true
  reason: 承認の照合の約束を広げ、運ぶ sh の振る舞いを受入条件にするため
allow:
- match: Write|Edit
  glob: tests/*
started_at: 2026-09-13T23:36:22+0900
completed_at: 2026-09-13T23:45:52+0900
base_sha: de87bea5b516976f405f9223194eb84d9f4b4d8b
ccnavi_approved:
  approved_at: 2026-09-13T23:35:43+0900
  source_tree: approve-carry
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/approve-carry/wip/tickets/todo/approve-carry-05.md
---

# 受入テストを足す（2 回目の敵対的レビュー）

## 決めたこと（人の方針の範囲で、2026-09-13）

- ボードの承認の指紋は、承認画面の本文に加えて、束のチケットごとに承認済みチケットとして書き出す中身
  （`ticket.render` の結果。承認の記録の欄は含めない）も覆う。承認済みチケットに写るものが 1 文字でも
  変われば食い違いにする。鍵の名前 `digest` と `--digest` の綴りは変えない
- 承認画面に `issue:` を出す（MR の本文の `Closes #<番号>` になるため）
- 運ぶ sh は `CCNAVI_TICKETS_APPROVED`（`ccnavi/settings.py` の `APPROVED_ENV`）を読む。旧名 `CCNAVI_APPROVED` は読まない
- 運ぶ sh は、1 本のツリーで `git add` が失敗しても他のツリーを運び、終了コード 1 で終わる
- 運ぶ sh は、`.claude/worktrees/` と `projects/` の下のシンボリックリンクを飛ばし、標準エラーに言う

## 足すテスト

### 承認の照合（`tests/test_approve_json.py`）

1. プレビューのあとで、束の親の `issue:` だけを書き換えると、見せた `digest` の `--yes` は `mismatch` で 1、承認済みチケットは置かれない
2. プレビューのあとで、束の子の Markdown の本文だけを書き換えても同じく `mismatch`
3. 超過の行だけが変わる（例: 種類の scope を書き換えて子の超過が増える）と、プレビューの `digest` が変わる
4. 見せた `digest` を大文字にして `--yes` に渡しても承認できる
5. `issue:` を持つ親のプレビューの `text` に、その番号が出る

### 運ぶ sh（`tests/test_push_approved_sh.py`）

6. 既存の `test_carries_the_place_named_by_ccnavi_approved` の環境変数を `CCNAVI_TICKETS_APPROVED` に直す。旧名 `CCNAVI_APPROVED` だけを設定しても、その置き場は運ばない
7. 2 本のツリーのうち 1 本で `git add` が失敗する（そのツリーの index.lock を置く）と、もう 1 本は運ばれ、終了コード 1、失敗したツリーを標準エラーで名指しする
8. `.claude/worktrees/` の下に、ワークスペースの外のリポジトリへのシンボリックリンクを置くと、そのリポジトリは運ばず、標準エラーに言う。本物の作業ツリーは運ぶ

## やらないこと

- `ccnavi/`、`scripts/`、`.ccnavi/`、`vscode-extension/` には触らない
- 運ぶ sh の本体（staging で直す）
- 巨大な phases.yml の遅さ、保護ブランチの大文字小文字、overflow の severity（今回は直さない）
