---
title: 生の git を止めてラッパースクリプトを使う
type: guide
description: ccnavi-git.sh の打ち方、出力の形、終了コード、push の扱い。生の git が止まる理由
tags: [user-guide, git, worktree]
keywords: [git, ccnavi-git.sh, ラッパースクリプト, push, 終了コード, 拒否, 出力, 要約]
---

# 生の git を止めてラッパースクリプトを使う

`.ccnavi/scripts/ccnavi-git.sh` は、安全な git だけを通し、結果だけを短く返す。エージェントが生の `git` を打つと止まり、止めた文面がこの sh を案内する。

```sh
sh .ccnavi/scripts/ccnavi-git.sh status
sh .ccnavi/scripts/ccnavi-git.sh log -p
sh .ccnavi/scripts/ccnavi-git.sh --help    # 通す形と通さない形の一覧
```

- 出力が長くてもエージェントの作業を圧迫しない。全文は `logs/` に残し、返すのは要約 1 行と先頭の数十行だけ（行数は[設定](settings.md)の `CCNAVI_GIT_MAX_LINES` で変える）
- サブコマンドごとに使ってよい形を決めて、危険なオプションの付け方を通さない

```
$ sh .ccnavi/scripts/ccnavi-git.sh log -p
ok  git log  56 コミット  log=logs/git-20260907-061907-23235.log
commit 845d832e329aa533ee8e0acf3ee61ea1990c47ca
...
... 残り 20893 行は logs/git-20260907-061907-23235.log にある（全文は Read ツールでこのファイルを読む）
```

終了コードは 0 が成功、1 は git が失敗、2 は引数か環境の誤り（拒否を含む）。失敗したときは末尾の数行を返す（行数と残す記録の本数は[設定](settings.md)）。

## push の扱い

- push は、今いるブランチを同じ名前で送る形だけ通る
- 統合先のブランチへは直接送れない。マージはユーザが行う
- 送るのは親だけである。子チケットのワークツリーとサブエージェントからは送れない

通す形と止める形の一覧は `--help` で出る。
