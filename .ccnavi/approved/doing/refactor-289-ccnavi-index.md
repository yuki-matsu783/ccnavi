---
version: 1
ticket: refactor-289-ccnavi-index
title: 索引を ccnavi-index.jsonl にして、プロジェクトでも必ず索引の対象にする
human_review:
  required: true
  reason: 他人のリポジトリの無視設定に書き込む。元に戻しにくく、影響範囲が広いため
rationale: |
  索引のファイル名を index.jsonl から ccnavi-index.jsonl に変え、.gitignore に無いプロジェクトでも追記して索引の対象にする。
allow:
  - match: Write|Edit
    glob: "src/ccnavi/*"
  - match: Write|Edit
    glob: "tests/*"
  - match: Write|Edit
    glob: "docs/*"
  - match: Write|Edit
    glob: "scripts/ccnavi-setup.sh"
  - match: Write|Edit
    glob: ".gitignore"
started_at: ""
completed_at: ""
base_sha: ""
---
## 背景

索引（`index.jsonl`）は、そのリポジトリの `.gitignore` に `**/index.jsonl` が無いと作られず、
SessionStart で「索引の対象外」と案内される。ccnavi は他人のリポジトリの `.gitignore` を書き換えない設計のため、
プロジェクトでは索引が使えない。また `index.jsonl` は他のツールの同名ファイルと被りうる。

## 決めたこと（ユーザ）

1. 索引のファイル名を `ccnavi-index.jsonl` にする
2. 他人のリポジトリ（`projects/<名前>/`）にも強制的に追記し、索引の対象にする

## 変更

- `docsearch.py` / `docsearch_index.py` の `INDEX_NAME`、仕様の文章、`UNIGNORED` の文言を `ccnavi-index.jsonl` にする
- 無視の設定が無いツリーは、索引を書く前に無視の行（`**/ccnavi-index.jsonl`）を追記する。「対象外」の分岐は、追記に失敗したときだけ残す
- `scripts/ccnavi-setup.sh` と、ワークスペースの `.gitignore` の行を `ccnavi-index.jsonl` に直す
- 旧 `index.jsonl`: ccnavi が書いた形のものだけ、新しい名前に移る時に消す（他のツールのものは触らない）
- docs（`frontmatter.md`、`startup-config.md`、`design.md` 系）とテスト

## 追記先（ユーザが決定）: プロジェクトの `.gitignore`

`.git/info/exclude` ではなく、各プロジェクトの `.gitignore` に `**/ccnavi-index.jsonl` を追記する
（明示的で、他の機械にも伝わる）。代償として、プロジェクトの追跡済みファイルが変わり、
未コミットの差分が残る（下のデメリット）。

## メリット

- プロジェクトの md も、全部 `--docs` で引ける
- 同名ファイルとの衝突が無くなる

## デメリット・リスク

- 他人のリポジトリの設定に ccnavi が書き込む。これまでの「`.gitignore` は書き換えない」を覆す（`frontmatter.md`・`startup-config.md` の記述も直す）
- A では、プロジェクトの `.gitignore` の差分が未コミットで残る。`worktree remove`・`integrate` の「未コミットの変更」判定に引っかかる
- 旧 `index.jsonl` からの移行で、ccnavi のものでないファイルを消さないことをテストで固定する
- ユーザが `ccnavi-index.jsonl` を追跡している場合（`!` の行）は追記しない、という現行の配慮は残す

## やらない場合

プロジェクトでは索引が使えず、`index.jsonl` の名前衝突の恐れも残る。
