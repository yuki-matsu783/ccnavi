---
title: 生の git を止めてラッパースクリプトを使う
type: guide
description: ccnavi-git.sh の打ち方、出力の形、終了コード、push の扱い。生の git が止まる理由
tags: [user-guide, git, worktree]
keywords: [git, ccnavi-git.sh, ラッパースクリプト, push, 終了コード, 拒否, 出力, 要約]
---

# 生の git を止めてラッパースクリプトを使う

`.ccnavi/scripts/ccnavi-git.sh` は安全な git だけを通し、出力を抑えて結果だけを返す。生の `git` はルールで拒否し、拒否の文面からここへ誘導する。

```sh
sh .ccnavi/scripts/ccnavi-git.sh status
sh .ccnavi/scripts/ccnavi-git.sh log -p
sh .ccnavi/scripts/ccnavi-git.sh --help    # 通す形と通さない形の一覧
```

- 出力がコンテキストに丸ごと載るのを止める。全量は `logs/` に残し、標準出力へは要約 1 行と先頭 40 行だけを返す
- オプションの抜け道を、入口を 1 本にして防ぐ。`permissions.allow` の `Bash(git diff:*)` は前方一致でしかないので、サブコマンドごとに使ってよい形をここで決める

```
$ sh .ccnavi/scripts/ccnavi-git.sh log -p
ok  git log  56 コミット  log=logs/git-20260907-061907-23235.log
commit 845d832e329aa533ee8e0acf3ee61ea1990c47ca
...
... 残り 20893 行は logs/git-20260907-061907-23235.log にある（全文は Read ツールでこのファイルを読む）
```

終了コードは 0 が成功、1 は git が失敗、2 は引数か環境の誤り（拒否を含む）。失敗したときは末尾 30 行を返す。

## push の扱い

- push は、今いるブランチを同じ名前で送る形だけ通る
- 統合先のブランチへは直接送れない。マージはユーザが行う
- 送るのは親だけである。子チケットのワークツリーとサブエージェントからは送れない

通す形と止める形の一覧、止める理由、限界は [ラッパースクリプトの設計](../design/git-wrapper.md) にある。
