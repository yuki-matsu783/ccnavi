---
version: 1
ticket: feature-622-fix-decide-args-01-01
parent: feature-622-fix-decide-args
phase: 1
title: decideArgs の先頭の decide を外し、文言・文書・試験をそろえる
human_review:
  required: true
  reason: ユーザ専用の sh へ渡す引数を変えるため
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
親の「範囲」のとおり、`decideArgs` の先頭の `"decide"` を外し、`commands.test.ts` の期待・`ccnavi.ts` などの文言とコメント・`board.md` を `ccnavi-user-decide.sh <N> --choices …` の形にそろえる。
試験は最低限で、`read-only.test.ts` に `runDecideYes` が `USER_DECIDE_SCRIPT` と `decideArgs` を使っていることを `bodyOf` で確かめる 1 件を足す。`manual-checks.md` に、指摘 0 件と 1 件以上の両方を押してエラーが出ずに進むことを見る行を足す。
`.ccnavi/scripts/ccnavi-user-decide.sh` と `decidePreviewArgs` は触らない。
