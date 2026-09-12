---
version: 1
ticket: config-union-08
parent: config-union
phase: 4
predecessors: [config-union-07]
title: 設定 3 本の和を文書に反映する
rationale: |
  `wip/design/config-union.md`（レビュー済みの設計）と、フェーズ 3 で入った実装を、文書に写す。
  対象は設計書 `ccnavi.md` の §25、`README.md` の設定とルールの書き方、`requirements.md` の
  REQ-MLT、用語集 `CONTEXT.md`、引き継ぎ `HANDOVER.md`。設計書の §25 は改版案をそのまま
  移すのではなく、実装で決まったこと（`--approve` の終了コード、`glob` の大文字小文字、
  生の `id` のコロン、`self` の予約、A-3 の綴りの分け方）を反映した形にする。
human_review:
  required: true
  reason: 外から見える約束（要求と設定の書き方）を変えるため
allow:
  - match: Write|Edit
    glob: "ccnavi.md"
  - match: Write|Edit
    glob: "README.md"
  - match: Write|Edit
    glob: "requirements.md"
  - match: Write|Edit
    glob: "CONTEXT.md"
  - match: Write|Edit
    glob: "HANDOVER.md"
started_at: ""
completed_at: ""
base_sha: ""
---

# 設定 3 本の和を文書に反映する

## `ccnavi.md` §25

`wip/design/config-union.md` を元に §25 を差し替える。改版案そのままではなく、実装で決まった
ことを反映する。

- §25.2 置き場の表（共通層 / 自身の層 / プロジェクトの層 / 固有スクリプト）、`CCNAVI_PROJECT_HOME`
- §25.4 rules の和、順序、`id` の接頭辞、重複の排除、同 id の扱い、無い層と壊れた層
- §25.4.1 / §25.4.2 phases と risk の合成。**`overlap` / `requires` を合成後に確かめる形**
  （層 1 本を読むときは参照を確かめず、合成後に新しく足した種類だけを当てる）は実装で決まった
  ことなので、設計書にも書く
- §25.6 守るもの。各層の 3 本と共通層の phases / risk が中核。`.ccnavi/scripts/` は中核に入れず
  組み込み deny と復元に任せる。作業ツリーの写しは切り元基準
- §25.8 `{root}` の置換先
- §25.9 診断と記録（`--explain` の層ごとの全件、記録の `source`、`--lint` の項目、導入スクリプト）
- §25.11 入れないもの、§25.12 移行
- 採らなかった形（プロジェクトの `.claude/`、上書きを許す形、`cwd` の追跡）も短く残す

## `README.md`

- 環境変数の表に `CCNAVI_PROJECT_HOME` を足し、`CCNAVI_PROJECT_RULES` を廃止として書く
- ルールの書き方に 3 行足す。**`glob` は機械の見方で大文字小文字を扱い、`regex` は区別を残す**。
  **生の `id` にコロンは書けない**（層の接頭辞と見分けが付かないため）。**`self` は綴り違いも予約**
- 層の説明（共通層 / 自身の層 / プロジェクトの層、どのツールがどの和を使うか）
- `--explain` と記録の読み方に `source` を足す
- 「使うかどうかはプロジェクトが決める」の節の「プロジェクト」が**ワークスペース単位**の意味で
  あることを明確にする（`projects/<名前>` の単位ではない）

## `requirements.md`

設計の末尾に挙げた REQ 候補（変更 9 件 + 新規 10 件）を、既存の REQ-MLT の番号に合わせて書く。
実装で決まった分（`--approve` の終了コード、`glob` の大文字小文字、`id` のコロン、`self` の予約、
シェル書き込みの綴り）も要求として書く。

## `CONTEXT.md`

用語を足す。**共通層 / 自身の層 / プロジェクトの層 / 層の和**。既にある「全体ルール」との関係を書く。

## `HANDOVER.md`

いま動くものの一覧に、層の和と自己保護の広がりを足す。読む順の表は変えない。

## やらないこと

- `CLAUDE.md` は触らない（運用の約束は変わっていない。`.ccnavi/` の説明が要るなら別途）
- VS Code 拡張の文書（`vscode-extension/ccnavi-board/README.md`）は範囲外。拡張が旧置き場を
  読み続ける代償は `ccnavi.md` の §25.11 に 1 行書く
