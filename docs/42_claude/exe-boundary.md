---
type: guide
title: 実行ファイルの境界
description: ccnavi実行ファイルの責務範囲。ネットワークに出ない、gitとの連携、シェルスクリプトとの役割分担
tags: [exe-boundary, sh-scripts]
keywords: [実行ファイル, ネットワーク, 境界, sh, Python, 判定, hook, payload, レビュー, 取得した結果]
---

# 実行ファイルの境界

- ccnaviの実行ファイルはネットワークにつながない。実行ファイルが判断の材料にするのは、ワークツリー、git、
  承認済みチケット、マーカー、hookのpayloadといった、プロジェクトのディレクトリの中で確かめられるものだけ
- マージリクエスト、レビューのスレッド、ホストのAPI、認証のような、ディレクトリの外にあるものは、
  `.ccnavi/scripts/`のshが取得してJSONファイルに書き、そのパスを実行ファイルの`--result`に渡す。
  実行ファイルが行うのは、そのJSONを読んでチケットを状態ごとの置き場へ移すことと、マーカーを置くことだけ
- 例外が1つある。リスクの配点の`script:`は、ユーザが置いたスクリプトを実行ファイルが`sh`で起動し、標準出力の点を受け取る
  （設計 9.9）。起動するのはプロジェクトの中のユーザのスクリプトだけで、実行ファイル自身はネットワークに出ない
- ホストへ出るのは、shと、Chrome 拡張の TypeScript（service worker）だけ。Chrome 拡張は、取得した結果を実行ファイルと同じ Python のコアへ渡して判定させ、ホストへの書き込み（issue・返信・解決・コミット）は、拡張自身が行う
- メッセージ送信（UserPromptSubmit）のhookも実行ファイルの側にあり、ホストに問い合わせない。ホストへの問い合わせは
  sh（`ccnavi-start.sh`・`ccnavi-branches.sh`）が受け持ち、hookは記録（承認済みチケット・マーカーなど）を読むだけ。
  hookのtimeoutは10秒で、ネットワークを待てないため
- 操作を止めるか通すかは、hookと実行ファイルが判定する（P13）。shがccnaviの状態を読んで操作を止める処理は、
  hookと実行ファイルの判定に重ねた2段目の確認にあたる。shに新しい判定を足したくなったら、まずhook側に置けないかを検討する
  - gitの状態だけを見て止める処理（`ccnavi-ticket.sh`の`ensure_child_worktree`が、手元だけのコミットがある子のブランチを
    消さずに止めるなど）は、ccnaviの状態を読まないので、この決まりの対象ではない
  - `ccnavi_is_ident`・`ccnavi_is_branch`・`ccnavi_branch_ok`（sh）は、実行ファイルの`id_problem`・`branch_problem`の
    ASCIIで見分けられる分だけの複製。字の種類・NFC・長さは見ないので、名前を受け入れる入口は実行ファイルの
    `worktree id`に聞く（`ccnavi_id_ask`）。規則を変えるときは両方を直す
- P13の例外: 入口を1本に寄せたラッパー（`ccnavi-git.sh`）の中の可否の判定（`integrate-ticket`（結び付くかは実行ファイルの`tree bound`に聞く（Stopの促しと同じ判定）。取り込み先のブランチは`tree base-bound`に聞く）、統合先と消えた親のブランチへの
  pushの拒否、親のワークツリーでの`checkout`・`switch`の行き先の確認）と、`integrate`・`integrate-done`の状態操作はshに置く。条件は3つで、
  生の`git`が組み込みで止まっていること、ラッパーが許可リストで読むこと、判定の材料のうちチケットの中身は実行ファイルに聞くこと
  （設計 9.2）。この3つを満たさない判定はshに足さない
- P13の例外の範囲は、親のブランチの共通のpush関門（`ccnavi_push_gate`。`ccnavi-common-state.sh`）にも広げる。
  関門を通る経路は、`ccnavi-git.sh`の`push`（送り先がoriginのとき）、write-and-push（`ccnavi-common-write-and-push.sh`の`ccnavi_wap_prepare`）、
  `ccnavi-user-push-approved.sh`の3つで、後ろの2つは素の`git push`を打つ。この2つはラッパーを通らないので「ラッパーが許可リストで読むこと」を
  満たさないが、同じ関門を共有しないと消えた親のブランチを作り直せてしまうため、関門だけを例外に入れる。関門の材料のうちチケットの中身
  （着手済み＝承認済みの親チケットの`started_at`・`.requested`・退避の有無）は実行ファイルの`tree parent`に聞き、shは自分で読まない。承認済みというだけでは形跡にしない。関門が`ls-remote`を打つのはshの側
  （ネットワークはshの仕事）。この2経路にほかの判定は足さない
- shはチケットのYAMLもJSONも自分で読まない。要る答えは実行ファイルのサブコマンド（`tree parent`・`tree bound`・`tree base-bound`・`tree guarded`・`tree leftover`・`tree handoff-pending`・`write-and-push family`・`sync retired`・
  `sync paths`など）に聞き、1行1項目の答えを見る。答えが得られないときにどちらへ倒すかは呼ぶ側が決める
  （止める側の検査は止める向き、ツリーを動かす側は動かさない向き）。
- 実行ファイルには、ディレクトリの外へ情報を取りに行くコードを足さない。取得する処理はshに任せ、
  実行ファイルには取得した結果を読む処理だけを足す。shと実行ファイルの間の契約は、このJSONの形で決まる。
  形は設計 9.10に書いてある
- 終了コードの 0（成功）・1（前提の未充足）・2（引数か環境の誤り。実行ファイルでは判定を書けず拒否にした）は
  どこでも同じ意味で使う。3 と 4 は、実行ファイルとshで、shどうしでも意味が違う。読む側はどれの値かを確かめて分ける

  | どこ | 3 | 4 |
  |---|---|---|
  | 実行ファイル | `EXIT_ANSWER_NO`（問われたことへの「いいえ」。`--agree --preview --verify`） | `EXIT_PARTIAL`（`user agree` だけ。承認済みチケットを一部だけ置いて止まった） |
  | `ccnavi-start.sh` | `EXIT_MULTI`（候補が複数・MR の無い候補・closed の issue・ワークツリーの無い既存の MR・似た issue・MR・意味が近いかの確認待ち。何も作らず、ユーザに聞く） | `EXIT_HOST`（ホストに届かず、代行が要る。GitHub は MCP、GitLab はユーザが確かめる） |
  | `ccnavi-worktree.sh` | `EXIT_CONFIRM`（確認が要る。何も切らず、ユーザの了承を待つ） | —（origin に届かなくても止めず、リモート未確認として 3 で確認を求める） |
  | `ccnavi-git.sh` | —（拒否はすべて 2。前提の未充足を 1 で返す区別はしない） | — |
  | `ccnavi-review.sh merged` | `unknown`（MR がマージ済みかを確かめられない） | — |
  | `ccnavi-fetch.sh` | 常に 0（取ってこられないことは失敗ではない） | — |

  `ccnavi-review.sh mr-state <番号>`（読み取りだけ）は 3 と 4 を使わず、0（状態が分かった）・1（`unknown`。状態が分からない）・
  2（引数の誤り）で返す。1 行目は `mr-state 1`、2 行目は `open <番号> <yes|no> <URL>`・`merged <番号> <URL>`・`closed <番号> <URL>`・
  `unknown <種類> <理由>`（種類は `no-tool`・`auth`・`network`・`api`・`not-found`）。ボードが読む

- shは文面ファイル（`.ccnavi/messages/`）を直接 grep・cat しない。文は実行ファイルの `text <識別子> [名前=値]…` に引かせる（`ccnavi_text`）。引けないとき（実行ファイルが無い・互換が合わない・文面ファイルが壊れている）は sh の短い既定の文を出して先へ進み、止める・止めないの判定は変えない（[文面ファイルに文を足す・直す](message-files.md)）
- 実行ファイルの標準出力をshが読む約束もある。`review ready`は1行目にコメントの下書きのパス、2行目に`tree <退避したツリーのルート>`を出し、
  `wip/`から消したものがあれば続けて`wip-from <消す前のHEAD>`と`wip <パス>`（1行1本）を出す。shはwrite-and-pushの中でこの削除を
  コミットして送ってからDraftを外す（設計 9.10）。ファイルを`logs/archive/`へ動かすのと`wip/`の追跡済みのファイルを消すのは実行ファイルで、
  ネットワーク（push・Draftを外す・コメント）はshが受け持つ
- 親を閉じたときのブランチの片付けも実行ファイルが行う（`worktree retire <親>`・`worktree branch <名前> [confirmed]`・
  `sync tidy <親> <リポジトリ> <ブランチ> <sha>`。`tickets/retire.py`）。MR がマージ済みかと、その先頭の sha は sh が
  `ccnavi-review.sh merged [<番号>]` で取って渡し、実行ファイルはその sha を基準に、手元の先頭が含まれるかなどをローカルの git で
  確かめて消す。エージェントに `git branch -D` を開けないので、消す入口はこれらだけ（`ccnavi-user-clean-branch.sh`）。最後の行が
  `record yes|no`（片付けが済んだか）で、`yes`のときは実行ファイルが`retire/<親>.json`も消す（shは消さない）。
  `ready`が書く`retire/<親>.json`（MRの番号を含む）が、親のワークツリーを片付けた後の列挙元で、shはJSONを読まず`sync retired`に聞く。
  放棄した親の記録は`worktree retire --abandon <親>`（ユーザだけ）で消す。
- ワークツリーの片付け（`worktree drop <名前>`・`worktree tidy <親>`）も実行ファイルが行う。消すかどうかの判断（cwdが中か、未コミットが
  あるか、gitのワークツリーか）と、生成物の掃除・`git worktree remove`はローカルの操作で、ネットワークには出ない。
  shは状態を書く操作（write-and-pushならpushまで）が済んでから呼ぶ。`ready`ではDraftを外したあとの最後に呼ぶ
