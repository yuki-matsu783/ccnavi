---
version: 1
ticket: approve-carry-07
parent: approve-carry
phase: 4
title: 写す版を作る（ccnavi-push-approved.sh と ccnavi-approve.sh の差し替え、設計書の追記）
rationale: 'エージェントが書けない `.ccnavi/scripts/` の sh 2 本の完成品を `wip/design/scripts/` に全文で置き、
  人が写す手順を書く。ccnavi-push-approved.sh は新規、ccnavi-approve.sh は運ぶ部分を差し替える。 2 回目の敵対的レビューの指摘（環境変数の名前、git
  add の失敗で全体が止まる、シンボリックリンクを運ぶ）を 入れる。あわせて設計書 `wip/design/approve-carry.md` に、敵対的レビューで決めた変更（端末の検査を
  入れないこと、止める式の大文字小文字、phases.yml の読み込みの例外、指紋の照合とその範囲、detached だけの ときの振る舞い、--lint の変化）を追記する。

  '
human_review:
  required: true
  reason: 人が保護された sh を写す手順を決めるため
allow:
- match: Write|Edit
  glob: wip/design/*
- match: Write|Edit
  glob: tests/*
started_at: 2026-09-14T08:16:15+0900
completed_at: 2026-09-14T08:49:45+0900
base_sha: 952dfc5a164795150713416ee5d3f15c0a7c0ecf
ccnavi_approved:
  approved_at: 2026-09-14T08:15:22+0900
  source_tree: approve-carry
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/approve-carry/wip/tickets/todo/approve-carry-07.md
---

# 写す版を作る

## 入力

- 下書き: `/private/tmp/claude-501/-Volumes-Data-git-ccnavi/887b0287-fe6f-4d49-867f-138c9a212e6d/scratchpad/staging/ccnavi-push-approved.sh`（1 回目のレビューで書いたもの。直す前）
- approve-carry-05 のテストを通すために入れた直しの差分（05 の報告）
- 今の `.ccnavi/scripts/ccnavi-approve.sh`（main を取り込んだ版）
- 設計書 `wip/design/approve-carry.md`、2 回の敵対的レビューの決定（親チケットと 04・05・06 の承認済みチケット）

## 成果物

- `wip/design/scripts/ccnavi-push-approved.sh`（全文）
  - 環境変数は `CCNAVI_TICKETS_APPROVED`
  - `git add` の失敗は `commit` と同じく捕まえて次のツリーへ（終了コード 1）
  - `.claude/worktrees/` と `projects/` の下のシンボリックリンクは飛ばして標準エラーに言う。
    3 回目の敵対的レビューで、`projects/` や `.claude/worktrees/` そのものがシンボリックリンクだと
    リンク先を辿ってコミットすることが再現されたので、置き場そのものも確かめる
    （下書き `scratchpad/staging/ccnavi-push-approved.fixed.sh` のループを、置き場 → その下の 1 件ずつ、の二段にする）
  - detached だけのときは「無い」と言わずに 0
  - macOS bash 3.2 と BSD の道具で動く書き方（`tests/test_sh_portability.py` を通る）
- `wip/design/scripts/ccnavi-approve.sh`（全文。運ぶ部分を `sh "$(dirname "$0")/ccnavi-push-approved.sh" || :` と `exit 0` に差し替えた版）
- `wip/design/scripts/README.md`: 人が写す手順（2 本を同時に写すこと、写したあとに回すテスト `tests.test_push_approved_sh` と `tests.test_sh_portability`、実行ビット）
- 設計書のコード片（§4.1 の `ScopeVerdict` など）を ruff の整形に合わせる。lint の hook が作業ツリーごとに「整形されていない」と止めるため
- 設計書の追記: 敵対的レビューで変えた点を §1〜§4 に反映し、§8 の REQ の候補を足す（指紋の照合、判定が落ちないこと、止める式の大文字小文字）。`$CCNAVI_APPROVED` の誤記を直す
- `tests/test_approve_json.py` に受入テストを足す（3 回目の敵対的レビューのミューテーションで、指紋が承認済みチケットに写る中身を覆っていることを確かめるテストが無いと実証されたため）:
  - 承認画面に出ない frontmatter の欄（例: ccnavi の知らない欄）だけをプレビューのあとで書き換えると、見せた `digest` の `--yes` は `mismatch` で 1、承認済みチケットは置かれない
  - 親の改版をプレビューしてから、改版の提案の中身を書き換えると `mismatch`。改版のプレビューの指紋が、承認済みチケットの frontmatter に計画を差し替えた中身を覆っていること（提案の frontmatter をそのまま使う実装では落ちること）を、写しのミューテーションで確かめる
- `tests/test_push_approved_sh.py` に受入テストを足す: `projects/` そのものがシンボリックリンクのとき、`.claude/worktrees/` そのものがシンボリックリンクのとき、リンク先のリポジトリにコミットが増えず、標準エラーに言う（リンクが作れない環境では skip）
- 写す前の確かめ: 作業ツリーの写し（スクラッチパッド）に 2 本を置いて `tests.test_push_approved_sh` と `tests.test_sh_portability` が通ること。足したテストが、直す前の下書き（`.fixed.sh`）では落ちること

## やらないこと

- `.ccnavi/scripts/` そのものには書かない（人が写す）
- README / ccnavi.md / requirements.md は docs フェーズ
