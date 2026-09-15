---
version: 1
ticket: approve-carry-08
parent: approve-carry
phase: 3
title: 実装する（承認の指紋を区切り文字に頼らない形にする）
rationale: '3 回目の敵対的レビュー（2026-09-14）で、承認の指紋が本文と束のチケットの中身を `\x00` でつないで 計算していること、docstring
  の「`\x00` は本文に出ない」が誤りであること（Markdown の本文に生の NUL が そのまま残る）が再現された。つなぎ目をずらす攻撃は frontmatter
  の並びに助けられて成り立たなかったが、 安全が設計ではなく偶然に依っている。部分ごとに SHA-256 を取り、その並びを SHA-256 にする形に変える。

  '
human_review:
  required: true
  reason: 承認の照合の計算を変えるため
allow:
- match: Write|Edit
  glob: ccnavi/*
- match: Write|Edit
  glob: tests/*
- match: Write|Edit
  glob: vscode-extension/*
started_at: 2026-09-14T07:41:17+0900
completed_at: 2026-09-14T07:46:40+0900
base_sha: d84d5585dff1fe006bc922e6b5b390dd7c570614
ccnavi_approved:
  approved_at: 2026-09-14T07:40:35+0900
  source_tree: approve-carry
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/approve-carry/wip/tickets/todo/approve-carry-08.md
---

# 実装する（指紋の計算）

## 人が決めたこと（2026-09-14）

- 今直す（案 A）。変更が小さいので、敵対的レビューは繰り返さず、テストの結果と親の確認で閉じる

## 入力

- 再現: `/private/tmp/claude-501/-Volumes-Data-git-ccnavi/887b0287-fe6f-4d49-867f-138c9a212e6d/scratchpad/review3-digest/test_nul_boundary_shift.py`
- `ccnavi/approval.py` の `approval_digest`

## 成果物

- `approval_digest`: 承認画面の本文 `text` と、束のチケットごとの `_carried(cand)` を、それぞれ UTF-8 の SHA-256（16 進）にし、その並びを改行でつないだものの SHA-256（小文字 16 進）を返す。部分の数も並びに含める（例: 先頭に件数）。docstring から「`\x00` は本文に出ない」の主張を消し、区切りに頼らない理由を書く
- テスト（`tests/test_approve_json.py`）: 束の 2 件のうち片方の Markdown の本文に生の NUL を含めても、プレビューと承認が通ること。プレビューのあとで、本文の NUL の前後の中身を 2 件の間で入れ替えると `mismatch` になること
- 拡張のフィクスチャを `CCNAVI_BOARD_FIXTURE=1` で書き直す（指紋は伏せ字なので差分は出ない見込み）
- ruff、関係する Python のテスト、拡張のテスト

## やらないこと

- 本文の NUL を拒む・消す検査（提案の書式の変更になるので、今回は入れない）
- 運ぶ sh（staging）
