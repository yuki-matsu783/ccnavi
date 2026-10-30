---
title: 配布するスクリプト
type: guide
description: .ccnavi/scripts/ にある sh の一覧。どの sh が何をし、誰が打つか（エージェント・ユーザ・hook）。ユーザ専用の sh、ワークツリーと git の入口、チケットとレビューの入口、直接は打たないファイル
tags: [user-guide, scripts]
keywords: [スクリプト, sh, ccnavi-git.sh, ccnavi-ticket.sh, ccnavi-review.sh, ccnavi-worktree.sh, ccnavi-user, ccnavi-handoff-names.txt, launcher, ラッパー, 保護済み]
---

# 配布するスクリプト

`.ccnavi/scripts/` の sh の一覧である。配布先にも同じ名前で配られる。生の git や、ユーザだけが打つ操作は、ここの sh を通す。
各 sh の引数・出力・終了コードの細かい決まりは、その sh の先頭の説明に書いてある。ここには役割と、誰が打つかだけを書く。
打ち方の手引きがあるものは各ページ（[git](git.md)・[チケット](tickets.md)・[レビュー](review.md)）にある。

「ユーザ」とあるものは、エージェントからは止まる。ターミナルで人が打つか、ボードの操作が送る。
「hook」は settings.json に登録され、セッションの節目で自動で走る。

## hook が起動する

| sh | 役割 | 打つ人 |
|---|---|---|
| `ccnavi-launcher.sh` | hook が起動する振り分け。`.ccnavi/bin/<os>-<arch>/` から、この機械の実行ファイルを選んで起動する。無ければ PATH の `ccnavi` | hook |
| `ccnavi-session-start.sh` | セッション開始の入口。下の `ccnavi-fetch.sh` と `ccnavi-dirs.sh` を順に呼ぶ | hook |
| `ccnavi-fetch.sh` | リモートの最新を取る。親ブランチと、ワークツリーの起点になる統合先ブランチ。進めるのは fast-forward だけ | hook |
| `ccnavi-dirs.sh` | `projects/` に clone 済みの外リポジトリを、最新コミットが新しい順に出す | hook |
| `ccnavi-idle.sh` | 大きな会話が放置されたとき、時間が経って会話の再利用が効かなくなる前に、次のセッションへの引き継ぎ文書を書かせる。`start`（待機）・`notice`（ユーザへの知らせ）・`reset`（入力で待機をやめる）。[設定](settings.md)の「大きな会話を放置したとき」 | hook |

## git・ワークツリー

| sh | 役割 | 打つ人 |
|---|---|---|
| `ccnavi-git.sh` | 安全な git だけを通し、出力を抑えて結果だけ返す。生の git の代わり（[生の git を止めてラッパースクリプトを使う](git.md)） | エージェント |
| `ccnavi-worktree.sh` | 直接作業のワークツリーを、取り込み先から切る。状況にずれや書きかけがあれば、切らずに状態と確認コードを出し、ユーザの了承を得て打ち直す。親のワークツリーの中からは、親の提案が承認前のときだけ切り替えられる（それ以外は断る） | エージェント |
| `ccnavi-sync.sh` | 親のブランチをリモートから取り込む。リモートから消えた親のブランチを診断する | エージェント |
| `ccnavi-clean.sh` / `ccnavi-clean.js` | ワークツリー 1 本の生成物（node_modules・.venv など）を消す。`--worktree` で、掃除とワークツリーの削除を 1 本で行う | エージェント |
| `ccnavi-stage.sh` | 書き込みを止められている保護対象のファイルを `wip/handoff/` へコピーして、下書きで直せるようにする（`.claude` の下は、`wip/handoff/` では `handoff-.claude` の名前で置かれる）。元の場所へ取り込むのはユーザ（`sh .ccnavi/scripts/ccnavi-user-handoff.sh`） | エージェント |

## issue・MR・チケット・レビュー

| sh | 役割 | 打つ人 |
|---|---|---|
| `ccnavi-branches.sh` | issue・MR に紐づくブランチを探す。読むだけ | エージェント |
| `ccnavi-start.sh` | issue・MR を指定された依頼の着手の入口。既存のブランチを確かめ、無ければ Draft MR・ワークツリー・ブランチを作る | エージェント |
| `ccnavi-ticket.sh` | チケットの状態を動かす（[チケット](tickets.md)）。親だけが呼ぶ。`status` と `learn` はサブエージェントも呼べる | エージェント |
| `ccnavi-review.sh` | レビューの依頼と確認（[レビュー](review.md)）。親だけが呼ぶ | エージェント |

## ユーザが打つ

| sh | 役割 | 打つ人 |
|---|---|---|
| `ccnavi-user-agree.sh` | 承認し、`ccnavi-user-push-approved.sh` でコミットして push する | ユーザ |
| `ccnavi-user-push-approved.sh` | 承認済みチケットの置き場だけをコミットし、保護されたブランチでなければ親のブランチへ push する。承認・ボードの保存・ユーザの判断のあとに呼ばれる | ユーザ |
| `ccnavi-user-decide.sh` | 残った指摘（未解決のスレッド）の対応方針を決めて置く | ユーザ |
| `ccnavi-user-close-early.sh` | 残りがあるまま親を早めに閉じる判断を置く。残りを issue に書き出す | ユーザ |
| `ccnavi-user-clean-branch.sh` | 親を閉じたときに残した、子の手元のブランチを消す。条件は消す前に確かめ直す | ユーザ |
| `ccnavi-user-handoff.sh` | 保護対象の下書き（`wip/handoff/`）を、本来の場所へ取り込む。引数なしは取り込み待ちを見せるだけ | ユーザ |
| `ccnavi-user-mirror.sh` | 共通レイヤーのミラーと工程スキル（`skills/`）のずれを見て、プロジェクトへ揃える。引数なしは見せるだけ | ユーザ |
| `ccnavi-user-prune.sh` | 判定ログと state を整理する。`--preview` は動かすものを並べるだけ | ユーザ |

## 直接は打たないファイル

`.ccnavi/scripts/` には、上の sh のほかに、他の sh が読み込む部品（`ccnavi-common*.sh`・`ccnavi-user-lib-review.sh`）と、
下書きの置き場で読み替える `.claude` の階層名の表（`ccnavi-handoff-names.txt`。sh ではなくデータ）がある。どれも直接は打たない。
