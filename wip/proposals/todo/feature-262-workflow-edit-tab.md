---
version: 1
ticket: feature-262-workflow-edit-tab
title: 計画の順序を図で直すワークフロー編集タブと、承認画面の読むだけの図
plan:
  - acceptance                          # 1。何も待たない
  - {type: implement, after: [1]}       # 2。段 4
  - {type: implement, after: [2]}       # 3。段 2・5
  - {type: implement, after: [3]}       # 4。段 6
  - {type: docs, after: [4]}            # 5。最後の項。ほかの全部を（推移的に）待つ
rationale: |
  親の計画の順序（項の after）を、VS Code のワークフロー編集タブの図で直せるようにする。承認のオーバーレイには
  同じ図を読むだけで出す。順序の読み書きは実行ファイルの独立したフラグ --plan-order が受け持つ。
  前提は feature-261-plan-order-core（計画の after、待ち方の都度計算、親の phases:）が統合先に入っていること。
  設計は wip/design/phase-plan-order.md の 6 節と段 2・4・5・6・7。
allow:
  - match: Write|Edit
    glob: "src/ccnavi/*"
  - match: Write|Edit
    glob: "tests/*"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/*"
  - match: Write|Edit
    glob: "docs/design/*"
  - match: Write|Edit
    glob: "docs/requirements/*"
  - match: Write|Edit
    glob: "docs/adr/*"
  - match: Write|Edit
    glob: "README.md"
---
# 計画の順序を図で直すワークフロー編集タブと、承認画面の読むだけの図

設計: `wip/design/phase-plan-order.md` の 6 節（feature-261-plan-order-core で統合先に入る）。

## 何をするか

- 実行ファイル: 独立したフラグ `--plan-order <親>`（`--json`・`--order`・`--write`・`--expect`）。番号の振り直し（固定した番号を動かさない、詰められない線を返す、改版の錠を当てる）、親の計画と子の提案の `phase:` の書き換え（戻せる形、書く前に全文を読み直す）。`--agree --preview --json` の `plans`
- フェーズ管理画面: 図と関係の欄・`order` の選択を外す。一覧の編集は残す
- 承認のオーバーレイ: 同じ図を読むだけで出す。「ワークフローを編集」でオーバーレイを閉じてタブを開く。未保存のタブがある親を含む一覧は承認を打たない。保存したあとに提案が変わっていれば注意を出す
- ワークフロー編集タブ: 親の提案ごとに 1 枚。任意の線を引け、循環の線は断る。線を変えるたびに実行ファイルが番号を振り直す。保存で `--plan-order --write`

## フェーズと設計の段

| 番号 | フェーズ定義 | 中身（設計の段） |
|---|---|---|
| 1 | acceptance | `tests/` に `--plan-order` の受入テスト（振り直し、子の追従、`--expect`、詰められない線、改版の錠、止めに当たらないこと）と、振り直しの見本の表 `tests/fixtures/plan-reorder.json` |
| 2 | implement | 段 4。`--plan-order` と `plan_order.py`、preview の `plans`、`phase_forms.py` のテスト |
| 3 | implement | 段 2 と段 5。フェーズ管理画面の図を外す。共有の図の部品と、承認のオーバーレイの読むだけの図 |
| 4 | implement | 段 6。ワークフロー編集タブ（パネル・画面・保存・外の変化・未保存）、カードとオーバーレイの入口、承認の遷移のガード |
| 5 | docs | 段 7。拡張の README と `docs/requirements/`（phases・workflow・board）、`docs/design/structure.md`、`docs/design/tickets/approval.md` |

保護された置き場は触らないので、staging のフェーズは置かない。
