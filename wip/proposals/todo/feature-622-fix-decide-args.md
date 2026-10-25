---
version: 1
ticket: feature-622-fix-decide-args
issue: 622
mr: 624
title: "VS Code 拡張の「レビュー済み」が、行き先を置く sh に余計な decide を渡して止まる"
phases:
  quick-work:
    kind: work
    title: 小さい作業
    review: mr
    scope: inherit
    when: 設計判断や並行作業が要らないとき
plan:
  - quick-work
rationale: |
  引数の修正と文言・文書・試験の追従が 1 つの小さな変更で、設計判断も並行作業も要らないため、1 ステップで足りる。
allow:
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/src/core/commands.ts"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/src/ccnavi.ts"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/src/core/approval-machine.ts"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/src/core/decidemodel.ts"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/test/shared/commands.test.ts"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/test/shared/read-only.test.ts"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/docs/requirements/board.md"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/docs/design/manual-checks.md"
---
## 目的

issue #622 を直す。VS Code 拡張の［レビュー済みにする］と［この対応方針を確定］が、`ccnavi-user-decide.sh` へ先頭に `decide` を付けた引数を渡し、スクリプトが「`<N>（フェーズ番号）が必要です。`」で終了コード 2 になって止まる。

## 範囲

`extensions/vscode/ccnavi-board/` の中だけ。

- `decideArgs`（`src/core/commands.ts`）の先頭の `"decide"` を外す。スクリプト側で `decide` を読み飛ばす案は採らない（使い方の文書と食い違う形を受けることになる）
- 試験: `test/shared/commands.test.ts` の期待を直す。`test/shared/read-only.test.ts` に、`runDecideYes` が `USER_DECIDE_SCRIPT` と `decideArgs` を使っていることを `bodyOf` で確かめる最小の試験を足す（実スクリプトは起動しない）
- 文言・コメント（`src/core/commands.ts`・`src/ccnavi.ts`・`src/core/approval-machine.ts`・`src/core/decidemodel.ts`）と文書（`docs/requirements/board.md`）を `ccnavi-user-decide.sh <N> --choices …` の形にそろえる
- `docs/design/manual-checks.md` に、指摘 0 件の［レビュー済みにする］と指摘 1 件以上の［この対応方針を確定］がエラーなく進む行を足す

## 対象外

- `.ccnavi/scripts/ccnavi-user-decide.sh`（変えない）
- 読む側の `decidePreviewArgs`（`ccnavi-review.sh decide <N> --preview` 用で、`decide` 付きが正しい）
- `extensions/chrome/ccnavi-approval/VERIFY.md` の同じ形の記述。別 issue の候補として MR に書く
- `package.json` の無関係な差分

## 完了の確かめ方

- `decideArgs` の先頭が数字のフェーズ番号になり、スクリプトの使い方（`<N> [--choices <JSON> --digest <ダイジェスト>]`）と一致する
- 拡張のテストが通る
- 拡張開発ホストでの実機確認ができなければ、その旨と理由を MR に書く

## 計画の理由

小さな 1 ステップの修正のため `quick-work` だけにした。
