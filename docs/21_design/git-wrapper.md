---
type: design
title: git のラッパースクリプト
description: ccnavi-git.sh が通す形と止める形、限界、記録
tags: [design-doc, git, sh-scripts]
keywords: [ccnavi-git.sh, git, ラッパースクリプト, push, fetch, pull, refspec, DENY_WRAPPER_CORE_PATH, DENY_SUBAGENT_TICKET_OP, GIT_CONFIG_COUNT, GIT_EXTERNAL_DIFF, 通す, 止める]
---

[設計書の入口に戻る](../20_design.md)

# git のラッパースクリプト

`.ccnavi/scripts/ccnavi-git.sh` は安全な git だけを通し、出力を抑えて結果だけを返す。生の `git` はルールで拒否し、拒否の文面からここへ誘導する。
ここは何を通し、何を止めるかを書く。ラッパースクリプトで守る場所を消す・戻す形は [8. 自己防衛](self-guard.md) にある。

## 判定の置き場

通すか止めるかの判定は、実行ファイルの `ccnavi git check`（`go/internal/tickets/lifecycle/gitwrap`）が行う。引数だけで決まる判定（サブコマンドより前のオプション、どのサブコマンドでも許さない形（設定の一時上書き・ファイル書き込みや外部コマンド実行に変えるオプション・判定の起点を動かすオプションと、その略形）、読むだけのサブコマンド、`cat-file`・`grep`・`branch`・`tag`・`remote`・`stash`・`add`・`commit` の形、通さないサブコマンド）と、ワークツリーと状態を見る判定（守る場所に向く `rm`・`restore`・`checkout <ref> <パス>`、`worktree add` の行き先・名前・起点の新しさ、`checkout`・`switch` のブランチの移動、`merge`・`merge-file`、`fetch`・`pull`、`push`）がこれに当たる。実行ファイルはラッパースクリプトの cwd で git の状態を読む（ネットワークには出ない）。

ラッパースクリプトは git の引数を標準入力に NUL 区切りで渡し、答えの行を見る。1 行目は `allow`・`pass`・`deny <識別子>`（次の行から拒否の文）のどれか。`allow` には、git の引数の前に足す語（`prepend`。ワークツリーの中の `merge` に補う `--no-ff`）・出力の後ろに出す警告（`warn`）・push の前に確かめる親のブランチ（`gate-top`・`gate-branch`）・ユーザのチェックアウトで統合先をチェックアウトしているときの、その統合先（`target`。`pull` が衝突したときの案内に使う）が続くことがある。`pass` は `integrate`・`integrate-done` で、ラッパースクリプトはロック（`logs/state/locks/<repo>/.integrate`）を取ってから `ccnavi git integrate` を起こし、その判定と状態の操作（`go/internal/tickets/lifecycle/land`）を任せる。push のゲートの ls-remote もラッパースクリプトが打つ。実行ファイルが見つからない・答えないときは、形を確かめられないので拒否する。

## 何を通し、何を止めるか

- 通す形の一覧に無いものは拒否する。`git branch -D`、`git worktree remove --force`、`git tag -d`、`git checkout -- <パス>` のように
  取り返しがつかない形も、サブコマンドの中で止める
- `push` は**今いるブランチを、そのままの名前で送る形だけ**通す。`--force`・`--force-with-lease`・`--delete`・`--all`・`--mirror`・`--tags`、
  別の名前へ送る refspec（`HEAD:main` など）は通さない。`main` `master` `develop` `release` `release/*` と、そのリポジトリの統合先
  （手元の `origin/HEAD`、無ければ `origin/main`・`origin/master`。プロジェクトは、そのプロジェクト自身の `origin/HEAD` で決まる。
  `develop-v1.0.0` のような名前でもよい）へ直接は送れない。統合先が決まらなければ固定のリストだけで判定する。マージはユーザの側に残す。送り先が origin で、親のワークツリーのブランチなら、前の「push のゲート」も通す
- `fetch`・`pull` は**リモート名とブランチ名だけ**を通す。`:` か `+` を含む引数（refspec と URL）は通さない。統合先のユーザのチェックアウト（`git worktree list` の先頭のツリーで、現在のブランチが統合先のもの）の `pull` は、`merge` と同じ判定を使う。引数なしと、現在のブランチのリモート + 現在のブランチと同名（完全一致）の形だけを通す。それ以外は `merge-on-target` で止める。ワークツリーの中、統合先でないブランチ、統合先が決まらないとき（origin/HEAD が無いなど）は止めない。`fetch` のあとの `merge FETCH_HEAD` は `merge` の拒否で止まる。
  `branch` の `-M`（強制の改名）と `-C`（強制の複製）も通さない
- 承認済みチケットの置き場（`.ccnavi/approved/`）とレビュー待ち（`wip/proposals/review/`）にヒットするパスには、
  `checkout <ref> <パス>`・`restore --source <ref>`・`restore --ours / --theirs` を通さない
- ccnavi 自身を守る場所（`.ccnavi/`・`.claude/settings*.json`・`.claude/hooks/`・記録と state・`ccnavi-git.sh`）には、
  `rm`・`restore --source <ref>`・`checkout <ref> <パス>` を通さない。hook が先に止め、ラッパースクリプトも 2 つ目に止める。
  生の git の止めは組み込みでいつも働き、ラッパースクリプト自身を消しても外れない。衝突の解決（`restore --ours / --theirs`）は通る
- 親のワークツリーでは、今のブランチと、ブランチ名の `/` を `-` に置き換えたものがフォルダ名（識別子）になるブランチのほかへ
  `checkout`・`switch` で移れない。`worktree add` は行き先の名前がブランチ名の `/` を `-` に置き換えたものになる形だけ
- 送るのは親だけ。子チケットのワークツリーからの push はラッパースクリプトが拒み、サブエージェントからの push は hook が拒む（`DENY_SUBAGENT_TICKET_OP`）
- サブコマンドより前のオプション（`git -c ...` など）は 1 つも受け取らない。`GIT_CONFIG_COUNT` と `GIT_EXTERNAL_DIFF` は実行前に消す

これは事故と浪費を減らすためのもので、敵対的な回避への防御ではない。実行役のコマンドの一覧に無いもの（`python -c`・`ssh` など）に埋め込めば
hook の文字列一致はヒットしない。そこまで防ぐなら `permissions.deny` か sandbox が要る。

記録は `logs/git-<日時>-<pid>.log` に成功でも失敗でも全量を書き、新しい順に 50 本だけ残す。`logs/` は `.gitignore` に入れ、コミットしない。
