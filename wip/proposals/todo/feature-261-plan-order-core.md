---
version: 1
ticket: feature-261-plan-order-core
title: フェーズの順序を親の計画で決め、待ち方を計画から計算し、使う定義を親に固定する（実行ファイル）
plan:
  - design
  - acceptance
  - implement
  - implement
  - implement
  - staging
  - docs
rationale: |
  phases.yml から順序の欄（order・after・overlap・requires）をなくし、親の計画の項に after で先行を書く。
  待ち方は承認済みの計画から都度計算し、phases/<親>/workflow.yml を廃止する。親の frontmatter に
  使うフェーズ定義（phases:）を固定し、承認のあとは phases.yml を読まない。設計は wip/design/phase-plan-order.md、
  ADR の草案は wip/design/0109-plan-order-on-plan-items.md。画面（ワークフロー編集タブ）は次の親で扱う。
allow:
  - match: Write|Edit
    glob: "src/ccnavi/*"
  - match: Write|Edit
    glob: "tests/*"
  - match: Write|Edit
    glob: "extensions/chrome/ccnavi-approval/*"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/src/core/*"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/test/*"
  - match: Write|Edit
    glob: "wip/design/*"
  - match: Write|Edit
    glob: "docs/design/*"
  - match: Write|Edit
    glob: "docs/requirements/*"
  - match: Write|Edit
    glob: "docs/adr/*"
  - match: Write|Edit
    glob: "docs/claude/skill-review.md"
  - match: Write|Edit
    glob: "README.md"
  - match: Write|Edit
    glob: ".claude/skills/ccnavi-config/*"
---
# フェーズの順序を親の計画で決め、待ち方を計画から計算し、使う定義を親に固定する（実行ファイル）

設計: `wip/design/phase-plan-order.md`（以下「設計」）。ADR の草案: `wip/design/0109-plan-order-on-plan-items.md`。

## 何をするか

設計の段 0・1・1b・1c・3 を、この親で実行ファイルに入れる。互換の版を 8 から 9 に 1 回だけ上げるため、段 1・1b・1c は同じ親（同じ MR）に入れる。

- 親の計画の項に `after: [番号]` を書けるようにし、待ち方を項の `after` から計算する（`phases.yml` の `order`・`after`・`overlap`・`requires` は読まず、`--lint` が warn）
- `phases/<親>/workflow.yml` を廃止し、待ち方は承認済みの計画から都度計算する。取り下げはチケットのバイト一致だけで決める
- 親の frontmatter の `phases:` に、計画が使う定義だけのコピーを持たせる。`--agree` が `phases.yml` の同じ名前の定義と同じかを確かめ、承認のあとの判定は `phases:` を読む。補助のフラグ `--plan-order <親> --fill-phases`
- 判定の側で、承認済みチケットだけを読んで済む計画と定義の検査を当て、壊れた親とその子（承認・着手・書き込み）を止める
- 改版の錠を番号ごとにし、推移的な待ちで比べる。改版は計画と `phases:` を差し替える
- フィードバック計画にも `after`・終端・延期の引き受け手の規則を当てる
- 互換の版を 9 に上げる（実行ファイル・拡張の `version.ts`・Chrome の同梱。`ccnavi-common.sh` は staging）

## フェーズと設計の段

今の `phases.yml` は一直線（番号順に前を全部待つ）で読むので、計画は番号順に進む。

| 番号 | フェーズ定義 | 中身（設計の段） |
|---|---|---|
| 1 | design | 設計草案と ADR 草案を、この親のブランチの `wip/design/` に入れて MR で見てもらう（今は別のブランチ `phase-plan-order-design` にある） |
| 2 | acceptance | 受入テスト。`tests/` に、`after` の形と崩れた入力・終端・延期の引き受け手（両方の計画）・`phases:` の検査・改版の錠と比べ方・取り下げ・手で動かした承認の判定を、今は落ちるテストとして書く。待ち方の見本の表 `tests/fixtures/plan-waits.json` |
| 3 | implement | 段 0 と段 1・1b。見本の計画を `after` の付いた形に直す（helper、1 行の計画を持つテスト、拡張の `board.json`、Chrome の `repo.ts`）。計画の `after`、待ち方の計算と検査、`workflow.yml` の廃止、判定の側の検査、取り下げ、改版の比べ方、互換の版 9 |
| 4 | implement | 段 1c。親の `phases:`、`--fill-phases`、判定・`phases_of`・成果物・子の範囲が `phases:` を読む、`phases.yml` の定義の `scope` / `deliverables` の 20 件の上限 |
| 5 | implement | 段 3。フィードバック計画の `after` と検査、促す文と見本の合流 |
| 6 | staging | 保護された置き場の完成品を `wip/design/scripts/` に全文で置く: `phases.yml`（関係の欄を消し、`when` を足す）、`ccnavi-common.sh`（`CCNAVI_COMPAT=9`）、`ccnavi-ticket.sh`（使い方の文）、`common-rules.yml`（`skill-review-at-proposal` の案内）。ユーザがコピーする |
| 7 | docs | `docs/design/tickets/`（phases・proposal-format・approval・state-transitions・hitl）、`docs/requirements/`、`README.md`、`docs/claude/skill-review.md`、`.claude/skills/ccnavi-config/`、ADR（新しい ADR と、置き換える ADR の状態の行、索引） |

- 受入テストと実装は並行してよい（今の定義で `acceptance` は `implement` と並行を許す）
- 互換の版は段 3 の子で上げ、`ccnavi-common.sh` は 6 番の staging で渡す。ユーザがコピーするまで、この親のブランチの実行ファイルと sh の版は食い違う（統合先に入れる前にコピーする）

## 範囲

`allow` は設計 8 節の触るファイルから決めた。保護された置き場（`.ccnavi/config/phases.yml`、`.ccnavi/scripts/`、`.claude/hooks/`、`rules.yml`）は入れない（6 番の staging で、ユーザがコピーする）。
拡張は、この親で直す `src/core/`（`version.ts`・`model.ts`・`tour-sample.ts`）と `test/` だけを入れる。画面（フェーズ管理画面の図を外す、ワークフロー編集タブ）は次の親で扱う。

## 次の親

画面の側（設計の段 2・4・5・6・7 の拡張の分）は、この親が閉じてから別の親で扱う: `--plan-order` の順序の読み書き（`--order`・`--write`・子の提案の追従）、フェーズ管理画面の図を外す、承認のオーバーレイの読むだけの図、ワークフロー編集タブ。
