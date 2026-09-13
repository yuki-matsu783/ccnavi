---
version: 1
ticket: sh-ws-root-03
parent: sh-ws-root
phase: 3
predecessors:
- sh-ws-root-02
human_review:
  required: true
  reason: ワークスペースルートの導出と push ガードの経路を実際に書き換えるため。ガードが効かなくなる向きの誤りが入りうる
title: 実装 — ルート探索を切り出し、sh 4 本と rules.yml を直す
rationale: 'フェーズ 2 で赤にした 16 件を緑にする。設計 1〜4 節の実装。

  保護済みファイルはチケットの承認でも書けない（judge.py:246-247）ので、完成品を

  wip/design/scripts/ に置き、人が写す。写す手順も同じ場所に書く。

  受入テストは CCNAVI_SH_DIR で写す前の版を指して回し、写したあとにもう 1 度回す。

  '
allow:
- match: Write|Edit
  glob: wip/design/*
- match: Write|Edit
  glob: tests/*
ccnavi_approved:
  approved_at: 2026-09-13T00:02:17+0900
  source_tree: sh-ws-root
  source_path: C:\Users\taniyama\Desktop\git\ccnavi\.claude\worktrees\sh-ws-root\wip\tickets\todo\sh-ws-root-03.md
started_at: 2026-09-13T00:03:06+0900
base_sha: a7cedea2f37c1c93c0a0f355984c60d098314bec
completed_at: 2026-09-13T00:36:58+0900
---

# 実装 — ルート探索を切り出し、sh 4 本と rules.yml を直す

設計 `wip/design/sh-ws-root.md` の 1〜4 節を実装する。フェーズ 2 の 16 件の赤を緑にする。

## 作るもの

`wip/design/scripts/` に完成品を置く。差分ではなく全文。人が手で当てる工程を無くすため。

| 置くもの | 写す先 |
|---|---|
| `ccnavi-common.sh` | `.claude/scripts/ccnavi-common.sh`（新設） |
| `ccnavi-git.sh` | `.claude/scripts/ccnavi-git.sh` |
| `ccnavi-ticket.sh` | `.claude/scripts/ccnavi-ticket.sh` |
| `ccnavi-review.sh` | `.claude/scripts/ccnavi-review.sh` |
| `test-py.sh` | `.claude/hooks/test-py.sh` |
| `rules.yml` | `.claude/ccnavi/rules.yml` |
| `ccnavi-setup.sh` | `scripts/ccnavi-setup.sh` |
| `COPY.md` | （写さない。手順書） |

## 直すこと

1. ルート探索を `ccnavi-common.sh` に切り出す。印は `.claude/scripts/`、上へ歩いて
   最初に当たったもの。`CCNAVI_WORKSPACE` があれば優先し、印が無ければ失敗（設計 2）
2. プロジェクト名の導出。`projects/<名前>/` か、作業ツリーの `.git` の `gitdir:` から
   切り元をたどる。取れなければ空（設計 3）
3. 記録を `<ワークスペース>/logs/<プロジェクト>/` に寄せる。返す綴りは、cwd が
   プロジェクトでも開ける形にする（設計 4.1）
4. push ガードのルート導出をワークスペースルートに直す。検査の中身は変えない（設計 4.1）
5. `worktree add` の行き先を検査して、ワークスペースの `.claude/worktrees/` の外なら止める。
   知らないオプションも止める（設計 4.1）
6. `ccnavi-ticket.sh` と `ccnavi-review.sh` の根を差し替える（設計 4.2、4.3）
7. `origin` を伏せる処理を `ccnavi_mask_url` に集約し、`origin` を出力しうる全箇所に通す。
   `fail` には伏せた綴りだけを渡す（設計 4.3）
8. ブランチ名のスラッシュを、ファイル名を組み立てる前に置換する（設計 4.3）
9. `test-py.sh` の存在チェックを `[ -d "$target/tests" ]` にする（設計 4.4）
10. `rules.yml` の `message` 7 か所を `{root}/.claude/scripts/...` にする（設計 4.5）
11. `ccnavi-setup.sh` の `DEPLOY_SCRIPTS` と点検の一覧に `ccnavi-common.sh` を足す（設計 4.6）

## 確かめ方

写す前は、受入テストの写し元を差し替えて回す。

```
CCNAVI_E2E=1 CCNAVI_SH_DIR=wip/design/scripts uv run python -m unittest tests.test_e2e_sh -v
```

20 件すべてが緑になること。既存の in-process 426 件も緑のままであること。
`rules.yml` の変更は `tests/test_root_placeholder.py` と見本の検査に掛かる。

写したあと、人が同じテストを `CCNAVI_SH_DIR` 無しでもう 1 度回す。手順は `COPY.md` に書く。

## 受入テストに手を入れる場合

`allow` に `tests/*` を入れてあるが、**期待を緩める向きの変更はしない**。実装が通らない
からといって判定を甘くすると、フェーズ 2 でやったことが無駄になる。触るのは、
組み立ての不備（フィクスチャの作り方、環境の見つけ方）に限る。触ったら、何をなぜ
変えたかをレビューの依頼文に書く。

## この子で決めないこと

- `ccnavi.md` と `requirements.md` への反映（フェーズ 4 でも行わない。親チケットの判断）
- `CLAUDE.md` と `HANDOVER.md` の更新（フェーズ 4）
- Python 側の修正（別チケット）
