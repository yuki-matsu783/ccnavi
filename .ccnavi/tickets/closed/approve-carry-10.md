---
version: 1
ticket: approve-carry-10
parent: approve-carry
phase: 4
title: 写す版を直す（フェーズ 4 の敵対的レビューの指摘）
rationale: 'フェーズ 4 の敵対的レビュー（2026-09-14、Sonnet）の指摘を、人が写す完成品に入れる。 写す手順の README に git
  add の手順が無く、git update-index --chmod が新しい sh で失敗すること、 元に戻す手順が生の git を案内していて、エージェントが写すと
  CLAUDE.md の方針とぶつかること、 CCNAVI_PROJECTS が末尾の / を落とすと空になる値のとき、運ぶ sh がワークスペースルートの直下を
  すべてツリーとして走査しシンボリックリンクの確かめもすり抜けること。

  '
human_review:
  required: true
  reason: 人が保護された sh を写す手順を直すため
allow:
- match: Write|Edit
  glob: wip/design/*
- match: Write|Edit
  glob: tests/*
started_at: 2026-09-14T21:39:10+0900
completed_at: 2026-09-14T21:43:40+0900
base_sha: 8d6e58e4310eed0433894f7c6afe3efd7396a9f2
ccnavi_approved:
  approved_at: 2026-09-14T21:38:32+0900
  source_tree: approve-carry
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/approve-carry/wip/tickets/todo/approve-carry-10.md
---

# 写す版を直す

## 成果物

- `wip/design/scripts/README.md`
  - 写す手順に、`cp` のあと `git add .ccnavi/scripts/ccnavi-push-approved.sh .ccnavi/scripts/ccnavi-approve.sh` を足す。`git update-index --chmod=+x` はその後に打つと明記する
  - 冒頭に「この手順は人が端末で打つ。エージェントは `.ccnavi/scripts/` に書けず、git も `ccnavi-git.sh` を通すので、この節をそのまま実行しない」と書く
- `wip/design/scripts/ccnavi-push-approved.sh`: `CCNAVI_PROJECTS` と `CCNAVI_TICKETS_APPROVED` の末尾の `/` を落とした結果が空なら、既定値に戻す（空の置き場でワークスペースルートの直下を走査しない）
- `tests/test_push_approved_sh.py`: `CCNAVI_PROJECTS=/` のとき、ワークスペースルートの直下の、`projects` でも `.claude/worktrees` でもないディレクトリの git リポジトリを運ばないこと
- 確かめ: 完成品の sh を置いた置き場（`CCNAVI_SH_DIR`、`ccnavi-common.sh` を含む）で `tests.test_push_approved_sh` が全部通ること。足したテストが直す前の sh では落ちること

## やらないこと

- 環境変数の `..` の検査、`ccnavi-common.sh` が無いときの終了コード、`-` で始まるブランチ名（人が設定する値が前提で、既存の sh と同じ書き方。今回は直さない）
