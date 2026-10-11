---
version: 1
ticket: refactor-288-wip-scratchpad
title: 下書きの置き場を wip/scratchpad/ に一本化する
human_review:
  required: true
  reason: integrate の挙動（消す操作）と、実行前・実行後チェックの除外を変えるため
rationale: |
  ワークツリー直下の scratchpad/（無視される置き場）を廃止し、下書きは wip/scratchpad/（追跡して push、統合時に消す）に一本化する。
allow:
  - match: Write|Edit
    glob: "src/ccnavi/*"
  - match: Write|Edit
    glob: "tests/*"
  - match: Write|Edit
    glob: "docs/*"
  - match: Write|Edit
    glob: ".ccnavi/scripts/ccnavi-git.sh"
  - match: Write|Edit
    glob: ".ccnavi/common/*"
  - match: Write|Edit
    glob: ".gitignore"
started_at: "2026-10-11T12:36:17+0900"
completed_at: "2026-10-11T15:30:29+0900"
base_sha: "cb1621ca4bee8881fbeab27d96234d48272a70db"
---
## 背景

下書きの置き場が 2 つある。ワークツリー直下の `scratchpad/`（`.gitignore` で無視。ワークツリーごと消える）と、
ルート直下の `wip/scratchpad/`（追跡して push）。ワークツリー内でも `wip/scratchpad/` に置き、
統合時に掃除する運用のほうが、履歴に残り、`.gitignore` の有無に依存せず、分かりやすい。

## 決めたこと（ユーザ）

1. ワークツリー内の下書きも `wip/scratchpad/` に置く
2. `integrate` は、取り込む前に `wip/scratchpad/` の追跡済みファイルを消してから staged にする
3. SessionStart の案内などの文言を直す
4. `scratchpad/`（無視される置き場）を廃止する

## 変更

- `ccnavi-git.sh integrate`: squash で取り込んだあと、`wip/scratchpad/` の追跡済みファイルを index から外して staged にする。
  取り込み側のチェック（index が `merge-tree --write-tree` と同じか）は、消した分を差し引いた形に合わせる。
  `integrate-done` の「staged が取り込んだときと違う」判定も同じ
- 廃止: `.gitignore` の `/scratchpad/`、`ticket_places` の `SCRATCH` 除外、`worktrees._in_scratchpad`、
  `lint_places` の警告、`phase_scope` / `post_findings` / `ticket.py` の関連コメントと分岐
- 文言: `docs/claude/scratchpad.md`（`wip/scratchpad/` 中心に書き直す）、CLAUDE.md の対応表、
  SessionStart の案内（`docs/design/startup-config.md` と該当コード）、`docs/claude/worktree.md`、関連 ADR/設計書
- テスト: `test_scratchpad_copy` ほか、`scratchpad/` を前提にしたものを新しい挙動に合わせる

## メリット

- 置き場が 1 つになる。下書きが履歴に残り、レビューで見せられる
- `scratchpad/` が追跡されているリポジトリでの警告（lint / 実行後チェック）が要らなくなる

## デメリット・リスク

- 消す操作を `integrate` に足す。`wip/scratchpad/` 以外を巻き込まないことを、テストで固定する
- ワークツリーで `wip/scratchpad/` に書いた下書きが、チケットの範囲の判定にかかるか要確認
  （`ticket_places` の `wip/` の扱いと、範囲に `wip/scratchpad/*` を足す必要が出るか）。出るなら、除外を `wip/scratchpad/` に移す
- コミットしたくないもの（機密、巨大な出力）の置き場が、セッションのスクラッチパッド（Environment 欄）だけになる
- プロジェクト（`projects/<名前>/`）には `wip/` の規約が無い。本チケットではワークスペース側だけを対象にする

## やらない場合

2 つの置き場と、片方だけ警告が出る状態が残る。

## 代案

`scratchpad/` を残し、`wip/scratchpad/` との使い分けを文言で説明するだけにする。
