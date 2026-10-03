---
type: adr
title: ELI5 の HTML は wip/ にコミットしてマージリクエストの差分に載せ、指摘はユーザが crit push で送る
description: ADR-0094 の置き場と指摘の写し方を置き換える。request は wip/ の下にコミット済みの HTML を求め、指摘は crit push の行のスレッドになる
tags: [review, sh-scripts]
keywords: [レビュー, 依頼, ELI5, HTML, crit, crit push, wip, マージリクエスト, 差分, スレッド, confirm, decide, ready]
---
# ADR-0095: ELI5 の HTML は wip/ にコミットしてマージリクエストの差分に載せ、指摘はユーザが crit push で送る

状態: 採用

ADR-0094 の置き場・crit の指摘の写し方・依頼文の末尾の 1 行・標準出力の案内を置き換える。ほかの行は 0094 のまま。範囲の書き足しと ELI5 だけの直しでの依頼し直しは置き換え（ADR-0096）、置き場の絞り込み（`wip/eli5/`）・名前の字・モード・案内の綴りの包み方は置き換え（ADR-0097）。

## 状況

ADR-0094 で、レビューの依頼（`ccnavi-review.sh request`）に ELI5 の HTML（`--eli5`）を必須にした。置き場は
依頼文と同じ追跡しない場所（`wip/tmp/` など）で、ユーザは手元で `crit <パス>` を打って見て、crit で付けた指摘は
エージェントが読んで `comment --body-file` でマージリクエストに写す手順だった。これには 2 つの問題があった
（2026-10-01、ユーザと確かめた）。

- 写す手間が手作業になる。エージェントが crit の出力を読み、本文を書き、投稿する
- `comment` の投稿は機構の投稿（`<!-- ccnavi:comment -->`）で、`confirm` の未解決に数えないのが普通。
  crit の指摘がレビュー済みを止めない

crit には、手元のコメントをマージリクエストの行のスレッドとして送る `crit push` がある（GitHub は `gh`、
GitLab は `glab` を使う）。ただし送れるのはマージリクエストの差分にあるファイルの行だけ。ユーザは案 1
（HTML を差分に載せ、ユーザが crit push で送る）を選んだ。

## 決定

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| ELI5 の HTML は、依頼を出すワークツリーの `wip/` の下にコミットする。既定の名前は `wip/eli5/phase-<N>.html` | HEAD に入って push されていれば、マージリクエストの差分に載り、crit push で行に指摘を送れる。`wip/` は途中の作業の置き場で、`ready` の前に丸ごと消すので、squash した成果物に HTML は残らない | `docs/` など成果物の置き場に置く。マージで既定のブランチに残る |
| `--eli5` は必須のまま。既定の置き場から自動で探さない | `--body-file` と同じく、何を渡したかがコマンド行に残る。hook の案内（`phase.py`・`review.py`）は前の版から `--eli5` を書いており、省ける形にすると古い sh と新しい案内の組み合わせで食い違いが増える。案内には既定の名前（`--eli5 wip/eli5/phase-<N>.html`）を書くので、打つ手間はほぼ同じ | 省けば `wip/eli5/phase-<N>.html` を読む。気づかないうちに別のファイルを読む経路ができ、フェーズの番号の付け違いに気づきにくい |
| `request` の前提に足す: ファイルがこのワークツリー（打った場所のリポジトリ）の中にある、ツリーのルートからの相対が `wip/` で始まる、HEAD に入っている、手元の中身が HEAD と同じ。欠けたものは全部を挙げて 1 で止める（拡張子違い・`--eli5` の欠けは今までどおり 2）。ロックの前で、何も書かない | 差分に載っていることを保証するため。push 済みかは実行ファイルの前提（HEAD が push 済み）がそのまま見る。全部を挙げるのは実行ファイルの前提の検査と同じ流儀 | 実行ファイルで確かめる。ADR-0094 と同じ理由（互換の版の扱い）で採らない |
| 旧方式（追跡しない `wip/tmp/` の HTML）は「HEAD に無い」で止める | 差分に無い HTML には crit push で指摘を送れない | 旧方式も通し、案内だけ変える。送れない指摘が手元に残る |
| 投稿する依頼文の末尾の 1 行と標準出力の案内を、「ELI5 は差分の `<相対パス>`。ツリーのルートで `crit review <相対パス>` を開いてソースの行に指摘を付け、`crit push <番号>` で送る」に変える。crit・gh・glab が PATH に無くても止めず、無いことを 1 行添える | 下の「crit の確認」のとおり、送れるのはソースの行に付けた指摘だけ。crit push は crit を打った場所からの相対パスで行を送るので、ルートで打たせる | 案内は `crit <パス>` のまま。描画のプレビューになり、付けた指摘が送られない |
| 「エージェントが crit の指摘を読んで comment で写す」手順はやめる | ユーザが crit push で送った指摘はユーザのスレッドで、既存の `confirm`（未解決を数えて止める）と `decide`（1 件ずつ行き先を選ぶ）がそのまま扱う。判定は 1 行も変えない | 写す手順を残す。写した投稿は機構の投稿で止まらない |

**hook の側に置けないか（docs/claude/exe-boundary.md）。** ADR-0094 と同じ理由で sh に置いた。加えて、HEAD に入っているかは
git に聞く検査で、hook がコマンド行から決められるものではない。

### crit の確認（2026-10-01、crit v0.21.0 の公開バイナリと、そのソース）

- `crit <file.html>`（引数がその 1 つだけ）は preview モードになり、描画した HTML の要素に「ピン」を付ける。ピンは行を持たず
  （`start_line` 0、`dom_anchor` あり）、`crit push` は GitHub でも GitLab でもピンを送らない（`push --dry-run` で 0 件。ソースでは
  `internal/github/push_buckets.go` の「live pins are local-only」と、`internal/gitlab/push.go` の `DOMAnchor != nil` の除外）
- `crit review <file.html>` はファイルのモードになり、HTML のソースに行の指摘を付けられる。偽の `gh` で `crit push <番号>` を
  打つと、`POST repos/{owner}/{repo}/pulls/<番号>/reviews` に `{"event":"COMMENT","comments":[{"path":"wip/eli5/phase-1.html","line":4,"side":"RIGHT"}]}`
  を送った。GitHub ではレビューのコメント（`reviewThreads` の 1 件）になり、sh の `fetch` が未解決のスレッドとして写す
- 確かめていないもの: 本物の GitHub・GitLab が受け付けるか（認証済みの `gh` / `glab` と実物のマージリクエストが要る）、GitLab の
  `glab` での送り方（Draft Notes をまとめて公開する。discussion になるので sh の `fetch` は `resolvable` の注記として拾うはず）

### スレッドが止めること・選べること

`crit push` のスレッドは本文が目印（`<!-- ccnavi:`）で始まらないので、`review._unresolved` は、依頼を投稿したアカウントと
同じアカウントが送ったものでも数える（GitLab で除くのは「目印で始まり、かつ投稿者が依頼者」だけ）。`confirm` は未解決として
止め、`decide` の一覧に 1 件として出る。`tests/ticket/test_ticket.py` の `test_a_crit_push_thread_on_the_eli5_blocks_and_leaves_with_wip`
と、`tests/ticket/test_review_actor.py` の `test_a_crit_push_thread_is_counted_even_from_the_poster` が根拠。

`crit push --event request-changes` は変更要求のレビューを出す。これは `decide` でも通せず、解くのはレビュアーの approve か dismiss だけ
（9.10 の `confirm` の表）。

## 得たもの・失ったもの

- 得たもの: crit の指摘がマージリクエストの行のスレッドとして直接残り、写す手間が無くなる。指摘はユーザのスレッドなので、
  `confirm` が止め、`decide` で 1 件ずつ選べる
- 得たもの: HTML が push 済みのブランチにあるので、ユーザは自分の機械でブランチを取ってきて開ける
- 失ったもの: ELI5 だけを直したコミットも「ユーザが見るものが動いた」に数える。依頼の後に直すと `confirm` が止まり、
  `request` の打ち直し（REQ-TKT-16 の依頼し直し）が要る。`wip/eli5/` を ccnavi 自身の置き場と同じく数えない形は、
  判定が緩む向きの変更なので入れていない
- 失ったもの: 直す前の行に付いたスレッドは、直したあとも未解決のまま残る（GitHub では outdated の表示が付く）。ユーザが解決するか `decide` で決める
- 失ったもの: 描画した HTML の上で付けたピンは送れない。送りたい指摘はソースの行に付け直す
- 失ったもの: 親チケットの範囲（`allow`）に `wip/eli5/*`（か `wip/*`）が無いと、親のワークツリーで HTML を書く Write が
  範囲の外として止まる。範囲を当てない場所に足す（`ticket.is_unscoped`）のは判定が緩む向きなので入れておらず、提案を書くときに範囲へ入れる
- 失ったもの: マージリクエストの差分に HTML が 1 枚増える。`ready` の前に `wip/` ごと消えるので、squash の成果物には残らない

## 採らなかった案

- **`wip/eli5/` を「動いた」に数えない・範囲を当てない場所にする。** ELI5 だけの直しで依頼し直さずに済み、範囲の書き足しも要らないが、
  どちらも判定が緩む向きで、相談なしには入れない
- **sh が HTML をコミットして push する。** 依頼の前提（未コミット無し・push 済み）を sh 自身が満たしに行く形になり、
  依頼と git の操作の境目があいまいになる
- **ELI5 をマージリクエストの本文やコメントに貼る。** crit push の対象にならない（差分のファイルではない）
