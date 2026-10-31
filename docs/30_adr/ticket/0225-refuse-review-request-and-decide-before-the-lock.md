---
type: adr
title: review-request と decide は、ワークツリーの中で分かる前提をロック・取り込みの前に断る
description: review-request の prepare の前提と decide の依頼の記録・動きの検査を、ロック・取り込み・コミットの前に読むだけの口（--check）で通す。ホストの API が要る条件は対象外にする
tags: [ticket, review, gate-conditions, script-output]
keywords: [関門, 最初に断る, 事前の検査, --check, review-request, prepare, decide, 取り込み, ロック, 依頼の記録, 依頼のあとに動いた]
---
# ADR-0225: review-request と decide は、ワークツリーの中で分かる前提をロック・取り込みの前に断る

状態: 採用

## 状況

ADR-0217・0221 は、開始前に分かる条件を、何も変えずに最初に断ると決め、`ticket start|finish|cancel|consolidate` に `--check` を入れた。`review-request` と `decide` が残っていた。

- `review-request` は、ELI5 の確認のあとに `wap_start`（ロック・取り込み・ローカルのコミット）を済ませ、それから `review prepare` が前提（フェーズの終了・合流・未コミット・push・依頼済み・依頼文）を確かめていた。`--check` は `wap_start` を飛ばすが、`prepare` は依頼文を state に書き出していた
- `decide` は、`wap_start` やアカウントの問い合わせのあとに、`ccnavi user reviewed` が依頼の記録の有無と、依頼のあとに動いたかを確かめていた。`--check` は無かった。ターミナルで選ぶ形では、ユーザが選び終えたあとで断られた

## 決定

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| `review-request` は、`wap_start` の前に `ccnavi --check review prepare` を通す。`prepare` の検査は `request_gate` に切り出し、本体の `prepare` と `prepare --check` が同じ関数を通る。取り込みのあとに `prepare` を通し直す（取り込みで状態が変わった場合も同じ関数で断る） | 取り込みとコミットの前に何も変えずに断る。ADR-0221 と同じ型 | `prepare` の呼び出しを `wap_start` の前に動かす（取り込みのあとの状態で見る検査が消える） |
| `review-request --check` は、`prepare --check` のあと、本文を書き出さずにマージリクエストの確認へ進む | `--check` が state に書かない。確認は同じ前提を通る | 従来どおり `prepare` を実行して本文を消す |
| `decide` は、アカウントの問い合わせ・ロック・取り込みの前に、`ccnavi --entry sh --check user reviewed N --accept-unresolved`（選択とダイジェストを渡されたときは `--yes` と `--digest` も）を通す。検査は `decide_gate`（親とフェーズ・依頼の記録・依頼のあとに動いていないこと）と、選択とダイジェストの形（`_parse_choices`）で、本体（`_decision`・`decide_yes`）と同じ関数 | ユーザが選ぶ前、取り込みの前に断る。`--resume` の形も、選択を復元したあとに同じ検査を通る | 選び終えたあとに断る |
| 断るときは、理由のあとに `次:` を 1 行足す（`NEXT_REVIEW_REQUEST_REFUSED`・`NEXT_DECIDE_REFUSED`） | 直して打ち直せば済む形にする | 理由だけを出す |
| `--check` は終了コードで返し、ターミナルを求めない。書く形の decide と見なさず、write-and-push を通さない | 読むだけの口にする | 書く形と同じ扱い |

## 事前に判断しない条件（対象外）

ホストの API が要る条件は、事前には判断しない。

- `review-request`: マージリクエストの有無・元ブランチ・マージ先。`--check` は従来どおり確認する。本番では取り込みのあとに見る
- `decide`: 変更要求のレビュー、未解決の指摘、見せた指摘とのダイジェストの照合、issue に回す下書きの置き場と続きの子の作成可否。取り直した結果が要る

## 得たもの・失ったもの

- 得たもの: `review-request` の前提と `decide` の依頼の記録・動きの検査が、ロック・取り込み・コミットの前に何も変えずに断られる。判定は 1 か所の関数
- 残る制限: 事前の検査は、取り込みの前の手元の状態で見る。別のPCで状態が進んだ場合は、先に取り込んでから打ち直す必要がある（ADR-0221 と同じ）
- 失ったもの: なし。取り込みのあとの `prepare` は残るので、取り込みで状態が変わっても同じ関数で断る
