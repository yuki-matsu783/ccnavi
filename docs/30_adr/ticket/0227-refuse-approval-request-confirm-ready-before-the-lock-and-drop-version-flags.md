---
type: adr
title: approval-request・confirm・ready の分かる拒否条件を取り込みの前に断り、--version の flags・features を消す
description: approval-request を他の操作と同じ型で先に断り、confirm と ready の手元で分かる条件を --check でロック・取り込みの前に通す。decide の選択の形の拒否にも次の一手を付ける。実行ファイルの --version --json の flags・features を消す（ADR-0226 の残した部分を覆す）
tags: [ticket, review, gate-conditions, exe-boundary, distribution]
keywords: [関門, 最初に断る, --check, approval-request, confirm, ready, decide, 次:, flags, features, --version, ADR-0226, 後方互換]
---
# ADR-0227: approval-request・confirm・ready の分かる拒否条件を取り込みの前に断り、--version の flags・features を消す

状態: 採用

## 状況

ADR-0217・0221・0225 で、開始前に分かる条件はロック・取り込みの前に `--check` で断る型にした。横展開の調査で、次が残っていた。

- `ccnavi-ticket.sh approval-request` だけ、`--check` の前段を飛ばしていた。コメントは「事前確認の流れで検査が済む」と書いたが、事前確認は環境の確認で、後の関門（`approval_request_gate`）ではない。本体の検査はロックと取り込みのあとだった
- `ccnavi-review.sh confirm` と `ready` は `wap_start`（ロック・取り込み）のあとで、依頼の記録・依頼のあとに動いたか・親を閉じられるかを確かめていた
- `decide` の `--yes` の選択とダイジェストの形が不正なとき、`--check` では `次:` が付くが、本体は付かなかった
- ADR-0226 は、実行ファイルの `--version --json` の `flags`・`features` を「Chrome 拡張や診断が読みうる」として残した。確かめると、本番の読み手は無かった

## 決定

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| `approval-request` も、ロック・取り込みの前に `ccnavi --check ticket approval-request` を通す。他の操作と同じ型にし、コメントを直す | 後の関門と同じ関数（`approval_request_gate`）で先に断る。決まりどおり | 事前確認で足りると見なす |
| `confirm` は `confirm_gate`、`ready` は `ready_gate` に手元で分かる条件を切り出し、本体と `review confirm --check`・`review ready --check` が同じ関数を通る。sh はアカウントの問い合わせ・ロック・取り込みの前に呼ぶ。断るときは `次:` を 1 行足す（`NEXT_CONFIRM_REFUSED`・`NEXT_READY_REFUSED`） | ADR-0225 と同じ型。同じ条件を 2 か所に書かない | 本体の検査を順だけ入れ替える |
| `ready --check` は、前の回が退避を始めている打ち直しなら通して本体に任せる | 打ち直しかどうかは MR が Draft かで決まり、ホストの結果が要る | 打ち直しも断る |
| `confirm`・`ready` の対象外は、ホストの結果が要る条件（未解決の指摘・変更要求・MR の照合・Draft かどうか） | 事前には判断できない | — |
| `decide` の選択とダイジェストの形の拒否は、本体（`decide_yes`）も `decide_gate` と同じ `_refused` で `次:` を付ける。`--digest` の拒否文から「古い版のボード」の推測を消す | 本体と `--check` で拒否の文を揃える。版のずれは推測しない | `--check` だけに `次:` |
| `--version --json` の `flags`・`features` と、その元の `FLAGS`・`FEATURES`・`flags()` を消す。`compat`・`commit`・`built`・`version`・`schema`・`formats` は残す | 読み手が無い。sh は `compat:`、配布の組み立ては `commit:` を読む。後方互換を考えない決まりどおり | 診断のために残す |

ADR-0226 の「`flags`・`features` の出力は、Chrome 拡張や診断が読みうるので、この決定では消さない」の部分は、この ADR で覆す。ADR-0226 のほかの決定は変えない。

## 得たもの・失ったもの

- 得たもの: `approval-request`・`confirm`・`ready` の分かる拒否が、ロック・取り込みの前に何も変えずに出る。拒否の文に `次:` が揃う。読まれない出力と、それを守る試験が無くなる
- 残る穴: 事前の検査は取り込みの前の手元の状態で見る（ADR-0221 と同じ）。取り込みのあとの本体も同じ関数で断る
- 失ったもの: `--version --json` で受け付けるフラグを一覧できなくなる
