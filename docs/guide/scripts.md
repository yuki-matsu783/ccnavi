---
title: 配布するスクリプト
type: guide
description: .ccnavi/scripts/ にある sh の一覧。どの sh が何をし、誰が打つか（エージェント・ユーザ・hook）。ユーザ専用の sh、ワークツリーと git の入口、チケットとレビューの入口、共有の部品
tags: [user-guide, scripts]
keywords: [スクリプト, sh, ccnavi-git.sh, ccnavi-ticket.sh, ccnavi-review.sh, ccnavi-worktree.sh, ccnavi-user, launcher, ラッパー, 保護済み]
---

# 配布するスクリプト

`.ccnavi/scripts/` の sh の一覧である。配布先にも同じ名前で配られる。生の git や、ユーザだけが打つ操作は、ここの sh を通す。
打ち方の手引きがあるものは各ページ（[git](git.md)・[チケット](tickets.md)・[レビュー](review.md)）にある。

「ユーザが打つ」とあるものは、エージェントからは止まる。端末で人が打つか、ボードの操作が送る。

| sh | 役割 |
|---|---|
| `.ccnavi/scripts/ccnavi-launcher.sh` | hook が起動する振り分けの sh（モード 100755）。原本と配布先で同じパス。1 つ上の `bin/<os>-<arch>/` から、この機械の実行ファイルを選ぶ。無ければ PATH の `ccnavi`、それも無ければ 127 |
| `.ccnavi/scripts/ccnavi-git.sh` | 安全な git だけを通し、出力を抑えて結果だけ返すラッパースクリプト |
| `.ccnavi/scripts/ccnavi-ticket.sh` | チケットの状態を動かす。親だけが呼ぶ。状態を聞く `status` と、気づきの記録を書く `learn` は、サブエージェントも呼べる。本体は `ccnavi ticket`。取り込み済みの親子のチケットでは write-and-push（取り込んでから書き、書いたパスだけをコミットして push するまで完了にしない） |
| `.ccnavi/scripts/ccnavi-review.sh` | レビューの依頼と確認。親だけが呼ぶ。本体は `ccnavi review`。状態を書くサブコマンドは write-and-push の対象の親子のチケットで write-and-push。`decide <N> --preview`（見るだけ）もここにある。ユーザ専用の `close-early`・`decide` は `ccnavi-user-close-early.sh`・`ccnavi-user-decide.sh` に分かれている |
| `.ccnavi/scripts/ccnavi-user-agree.sh` | 承認し、`ccnavi-user-push-approved.sh` でコミットして push する。ユーザが端末で打つ。本体は `ccnavi user agree` |
| `.ccnavi/scripts/ccnavi-user-push-approved.sh` | 承認済みチケットの置き場だけをコミットし、保護されたブランチでなければ親のブランチへ push する。ユーザが打つ（エージェントからは止まる）。端末の `ccnavi-user-agree.sh`・ボードの承認とフローの保存・ユーザの判断の入口のあとに呼ばれる。`[<親>...]` で親子のチケットを限る。取り込み済みの親子のチケットはロックを取り、取り込んでから送る |
| `.ccnavi/scripts/ccnavi-fetch.sh` | セッション開始時に親ブランチと、ワークツリーの起点になる統合先ブランチを取ってくる。進めるのは fast-forward だけ |
| `.ccnavi/scripts/ccnavi-session-start.sh` | セッション開始時の入口。`ccnavi-fetch.sh` と `ccnavi-dirs.sh` を順に呼ぶ。SessionStart にはこれを登録する |
| `.ccnavi/scripts/ccnavi-dirs.sh` | セッション開始時に、プロジェクト直下のディレクトリを更新が新しい順に最大 10 件出す。超えるときは `ls` を案内する |
| `.ccnavi/scripts/ccnavi-sync.sh` | 親のブランチを取り込む（早送りか merge。衝突したら取りやめてユーザの対応に切り替える）。リモートから消えた親のブランチを診断する（閉じたかはホストの MR で決める） |
| `.ccnavi/scripts/ccnavi-branches.sh` | issue・MR に紐づくブランチを探す。読むだけ。ホストは sh が読み、手元の候補は `ccnavi branches` が集める |
| `.ccnavi/scripts/ccnavi-start.sh` | issue・MR を指定された依頼の着手の入口。`ccnavi-branches.sh` で候補を探し、無ければ Draft MR・ワークツリー・ブランチを作る |
| `.ccnavi/scripts/ccnavi-worktree.sh` | 直接作業のワークツリーを、取り込み先（メインのチェックアウトが開いているブランチ）から切る。ずれが無ければ確認なしで取り込み先のHEADから切る。cwdのブランチ違い・取り込み先が統合先でない・`origin/<取り込み先>` とのずれ（遅れ・進み・分岐・無い・リモート未確認）・書きかけがあるときは、切らずに終了コード 3 で状態・勧め・リスク・確認コードを出す。ユーザの了承を得て `--base`・`--accept`・`--copy-wip`/`--no-wip` を付けて打ち直す。`--side-by-side` で、横で作業する間の自動復元を緩める（`ask` ルールで確認が出る）。記録は共通のgitディレクトリの `ccnavi-direct/<名前>` |
| `.ccnavi/scripts/ccnavi-stage.sh` | 書き込みを止められている保護対象のファイルを `wip/handoff/`（元と同じ階層。cwd が `.claude/worktrees/<名前>` の中ならそのワークツリーの `wip/handoff/`）へ `cp` する。`--commands` は編集済みの下書きの状態を出す（端末の前のユーザは `mv` の代わりに `ccnavi user handoff` で取り込める）。取り込むのはユーザ |
| `.ccnavi/scripts/ccnavi-clean.sh` / `ccnavi-clean.js` | ワークツリー 1 本の生成物（node_modules・.venv など）を消す。`worktree remove` の前に打つ。node が無ければ sh で同じものを消す。`--worktree <名前>` は掃除と `worktree remove` を 1 本で行う（本体は `ccnavi worktree drop`。cwd が中なら消さない）。子のブランチを消す `--branch` は `ccnavi-user-clean-branch.sh` に分かれている。配る（`scripts/ccnavi-setup.sh` の `DEPLOY_SCRIPTS`） |
| `.ccnavi/scripts/ccnavi-user-clean-branch.sh` | 親を閉じたときに残した子の手元のブランチを、条件を確かめ直して消す。ユーザが端末で打つ（エージェントからは止まる）。本体は `ccnavi worktree branch` |
| `.ccnavi/scripts/ccnavi-user-close-early.sh` | 残りがあるまま親を早めに閉じる判断を置く。ユーザが端末で打つ。残りを issue に書き出し、コメントを投稿する。本体は `ccnavi user close-early` |
| `.ccnavi/scripts/ccnavi-user-decide.sh` | 残った指摘（未解決のスレッド）の対応方針を決めて置く。ユーザが端末で打つ（ボードが押した選択を送る形も同じ）。本体は `ccnavi user reviewed` |
| `.ccnavi/scripts/ccnavi-user-lib-review.sh` | ユーザ専用のレビューの sh（`ccnavi-user-decide.sh`・`ccnavi-user-close-early.sh`）が共有する、ユーザの判断をホストに書く部品（指摘のスレッドへの返信と解決、issue の作成）。単体では動かない |
| `.ccnavi/scripts/ccnavi-common*.sh` | 保護済み sh が共有する部品。単体では動かない。入口の `ccnavi-common.sh`（パスの絶対化・ワークスペースルート・実行ファイルの場所・互換の版の食い違い・識別子とブランチ名の書き方の確かめ）が、state の置き場と統合先と親ブランチの push の関門（`-state`）・親子のチケットのロック（`-lock`）・write-and-push（`-write-and-push`）・ホスト（GitHub / GitLab）への接続（`-host`）・診断ログ（`-log`）の部品を最後に読み込む。レビューの sh だけが読むホストのマージリクエストの部品が `-review` |
