---
version: 1
ticket: chore-757-handoff-drop-orig-01-01
parent: chore-757-handoff-drop-orig
phase: 1
title: 下書きの控え（.orig）をやめる
human_review:
  required: true
  reason: --expect の形式と、拡張の conflict の表示が変わるため
allow:
  - match: Write|Edit
    glob: ".ccnavi/scripts/ccnavi-stage.sh"
  - match: Write|Edit
    glob: ".ccnavi/docs/*"
  - match: Write|Edit
    glob: "src/*"
  - match: Write|Edit
    glob: "tests/*"
  - match: Write|Edit
    glob: "docs/21_design/*"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/*"
  - match: Write|Edit
    glob: "extensions/chrome/ccnavi-approval/*"
started_at: "2026-10-31T11:42:11+0900"
base_sha: "4e072b78cf65b3365f042fbb27b1e0c591921a8c"
completed_at: "2026-10-31T12:13:02+0900"
merged_sha: "b3932fe9a8e85c69206d509fb39f550793be29e2"
finished_to: "review"
---
親の「範囲」のとおりに、`.orig` と `conflict` の判定をやめる。

進め方:

1. `.orig`・`conflict` を使っている箇所を洗い出す（実行ファイル、`ccnavi-stage.sh`、VS Code 拡張、Chrome 拡張、設計書、`.ccnavi/docs/`、テスト）。`conflict` は別の意味の語もあるので、取り込みの `conflict`（下書きを作った後に取り込み先が変わった）だけを対象にする。結果は MR の説明に書く
2. 実装を直す。`--expect` のハッシュから `.orig` の項を外す。拡張の表示と処理を、実行ファイルの `--json` の形に合わせて直す
3. `.ccnavi/scripts/ccnavi-stage.sh` と `.ccnavi/docs/` は保護対象。ガードが有効なら、下書きに写して直し、取り込みをユーザに頼む（`.ccnavi/docs/scratchpad.md`）
4. 設計書に、`.orig` をやめた理由（確認は助言で、歯止めは `--expect`。残ると `integrate` を止める。退行は git の履歴で戻せる）を書く
5. テストは、変えた範囲だけ回す
