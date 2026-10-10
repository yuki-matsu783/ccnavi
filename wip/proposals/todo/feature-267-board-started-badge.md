---
version: 1
ticket: feature-267-board-started-badge
issue: 267
title: ボードの作業中カードに未着手・着手済みのバッジを出す
plan: [acceptance, implement, docs]
human_review:
  required: true
  reason: ボードの見た目と、カードに出す言葉が変わるため
rationale: |
  issue #267 の案 B。承認済みチケットは `--agree` の時点で作業中の列に入るが、`start` するまで
  `started_at` が空で範囲は適用されない。列は変えず、作業中の列のカードに着手の状態を示すバッジを足す。
allow:
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/src/*"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/test/*"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/docs/*"
started_at: ""
completed_at: ""
base_sha: ""
---
# ボードの作業中カードに未着手・着手済みのバッジを出す

issue: https://github.com/yuki-matsu783/ccnavi/issues/267

## 何をなぜ変えるか

`ccnavi --agree` の直後、`started_at` が空でも承認済みチケットは作業中の列に入る。ユーザが着手済みと
誤認し、`start` 忘れに気づきにくい。作業中の列にいるカードに、着手の状態を示すバッジを足す。

## 変えること

issue の本文は原因を `diagnose_board.py` の列分けとしているが、バッジは拡張の側だけで足せる。
実行ファイルのボードの JSON は `started_at` をすでに渡していて、拡張のカードも `startedAt` を持っている
（`core/board.ts:271`）。拡張は `startedAt` を先行のバッジの言葉の出し分けにすでに使っている
（`webview/board/text.ts:223`）。

- 承認済み(`copyStatus === "open"`)のカードに、`startedAt === ""` なら「未着手」、あれば「着手済み」を、枠の無い属性(`Facts`)で出す。`review/` にいるカード、未承認、閉じたカードには出さない
- `webview/board/Card.tsx` の `Facts` に足す。言葉は `webview/board/text.ts` に置く
- 実行ファイル（`src/ccnavi/`）は変えない。列分けも変えない
- 受け入れテストを `test/board/render.dom.test.ts` に足す。見本（`core/tour-sample.ts`）には未着手の例が既にある
- 要件の文書（`docs/requirements/board.md`）に、バッジの言葉と出す条件を書く

## 枠付きバッジにしない理由

枠付きのバッジは「ユーザが動く必要がある状態」だけに使う方針(`Badges` の注釈)がある。`start` を打つのはエージェントなので、
未着手は枠の無い属性にする。「要対応のみ」の絞り込み(`attention`)にも含めない。

## メリット

- 実行ファイルを変えないので、配布物や保護対象のファイルに触れない
- 列は変えないので、既存の並びや折り畳みの状態を壊さない
- 未着手のまま止まっているチケットが、カードを見れば分かる

## デメリット

- 列は「作業中」のままなので、未着手と着手済みが同じ列に混ざる。列で分ける案 A より、見た目の区別は弱い
- 属性が 1 つ増える。枠が無いので、警告色のバッジより目立たない

## やらない場合

issue のとおり、`start` 忘れに気づきにくい状態が続く。

## 代案

- 案 A（列を分ける）: 「承認済み・未着手」の列を新設する。区別は強いが、列の構成、ドラッグ、折り畳みの保存、
  `board-moved`、見本、要件文書に影響が広がる
- 実行ファイル側で判定済みの欄（`started: bool`）を JSON に足し、拡張はそれを読む。拡張が `started_at` から
  組み直さない方針（`FlowJson` の注釈）には沿うが、実行ファイルとの互換（`--version` の版）を動かすことになる。
  拡張はすでに `startedAt` を読んでいるので、今回は見送る

## 受け入れ条件

- [ ] agree 直後（`doing/`・`started_at` が空）のカードに「未着手」のバッジが出る
- [ ] `start` 後（`started_at` あり）のカードに「着手済み」が出る
- [ ] レビュー待ち・閉じたカードには出ない
- [ ] 拡張のテストが通る
