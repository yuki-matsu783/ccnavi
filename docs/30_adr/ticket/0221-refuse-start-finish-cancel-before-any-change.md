---
type: adr
title: start・finish・cancel・consolidate は、書く前に分かる検査を取り込みの前に通して断る
description: チケット作業の子の start・finish・cancel と consolidate の検査を、ロック・取り込み・コミット・子のワークツリーの作成の前に読むだけの口（--check）で通す。合流のあとでないと分からない検査は本体に残す
tags: [ticket, gate-conditions, script-output]
keywords: [関門, 最初に断る, 事前の検査, --check, start, finish, cancel, consolidate, 取り込み, 子のワークツリー, 先行, 学びの記録]
---
# ADR-0221: start・finish・cancel・consolidate は、書く前に分かる検査を取り込みの前に通して断る

状態: 採用

## 状況

ADR-0217 は、開始前に分かる条件で止まる操作を、何も変えずに最初に断ると決めた。チケット作業の子の `start`・`finish`・`cancel` と `consolidate` が残っていた。

`ccnavi-ticket.sh` は、事前確認のあと、ロック・取り込み（`ccnavi-sync.sh`）・hook のマーカーのコミットを済ませてから実行ファイルの本体を起こす。本体の検査はその中にあった。

- `start` は、子のワークツリーとブランチを作ってから、先行チケットの未了などで断り、残骸が残った
- `finish` は、親へ合流してから検査して断り、合流を戻した
- `cancel` は、`--reason` が無くても、先に取り込みとコミットが走った

## 決定

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| 実行ファイルの `ticket start\|finish\|cancel\|consolidate <識別子> --check` を、読むだけの検査の口にする。sh は事前確認の直後、`ccnavi_wap_begin`（ロック・取り込み・コミット）と `ensure_child_worktree` の前に呼び、断られたらそこで止める | 断られたときに何も変わっていないので、直して打ち直せば済む。`approval-request --check` と同じ型 | 本体の中で検査の順を入れ替える（取り込みとコミットは本体の前にある） |
| 本体と `--check` は同じ関数を通す（`start_gate`・`finish_gate`・`cancel_gate`・`consolidate.gate`）。断ったときの `次:` もその関数が 1 行出す（`NEXT_OPS_REFUSED`、`cancel` は `NEXT_OPS_REFUSED__CANCEL`） | 同じ条件を 2 か所に書かない。本体で断る場合（取り込みで状態が変わったとき）も同じ文になる | 検査用の関数を別に持つ |
| 手元でチケットを引けないとき、`--check` は断らずに通す | 別のPCで承認・着手された分は、取り込みのあとで初めて手元に来る。取り込みの前に断ると、その流れが通らなくなる | 引けなければ断る |

## 前に出した検査・本体に残した検査

| 操作 | 前に出した（取り込みの前に断る） | 本体に残した |
|---|---|---|
| `start` | 作業中か・着手済みか・置き場（`misplaced_home`）・計画（`plan_missing`）・親の着手（`parent_not_started`）・先行の完了（`predecessors_unmet`） | 子のワークツリーの有無と元リポジトリ（sh が先に作るので、事前には無い）、HEAD の読み取り |
| `finish` | 作業中か・着手済みか・置き場・親を閉じてよいか（`parent_still_busy`）・学びの記録が子の HEAD にあるか（`learning.record_missing`。子の HEAD を読むだけ） | 成果物の追跡（`deliverables_missing`。親と子の追跡の結果。合流後のツリーで見る）、親の学びの置き場（`learning.parent_blocked`。取り込み後の親の HEAD で見る）、実績リスクの計測と判定、合流の git の結果（衝突・未コミット・範囲外のパス。`ccnavi_wap_merge_plan`） |
| `cancel` | `--reason` の有無・作業中か・未承認でないか・親を閉じてよいか | なし |
| `consolidate` | 学びの蓄積が有効か・親が作業中で着手済みか・開いている統合の子・置き場・親を閉じてよいか・学びの置き場の未コミット・処理すべき気づき | 統合の子の文面（`build`）と書き込み |

## 得たもの・失ったもの

- 得たもの: `start` が子のワークツリーを作る前に、`finish` が合流する前に、`cancel` が取り込む前に断る。断られたときの `次:` は 1 行で、同じ操作を打ち直すだけで済む
- 残る穴: 事前の検査は取り込みの前の手元の状態で見る。別のPCで先行が閉じたばかりなど、取り込めば通る場合は、先に断られうる。この場合は、先に `ccnavi-sync.sh` で取り込んでから打ち直す必要がある
- 失ったもの: `finish` の検査の順が変わった（学びの記録が成果物より先に調べられる）

## 採らなかった案

上の表のとおり。
