---
type: adr
title: 根の決め方を、実行ファイルと sh、起動する場所によらず揃える案
description: 実行ファイルの根は CLAUDE_PROJECT_DIR か .claude/ を持つ最も近い祖先、sh の根は ccnavi-common.sh を持つ祖先で、ワークスペース内 clone のプロジェクトやワークツリーで起動すると食い違う。揃え方の選択肢と、sh に CLAUDE_PROJECT_DIR を読ませない理由を残す
tags: [worktree, projects, multi-repo]
keywords: [根, ルート, CLAUDE_PROJECT_DIR, default_root, ccnavi_workspace, 単体 clone, ワークスペース内 clone, ワークツリー, --root, 食い違い]
---

# ADR-0211: 根の決め方を、実行ファイルと sh、起動する場所によらず揃える案

状態: 提案（選択肢を並べた段階で、どれも決めていない）

## 状況

ADR-0145 は、ワークスペース内 clone と単体 clone の両方で動かす考え方を決めた。根の決め方は、実行ファイルと sh で別々に実装されていて、単体 clone では揃うが、次の形では食い違う。いまの動きは、設計書「複数のリポジトリ」の「単体 clone での動作」の根の決め方の項にある。

- ワークスペース内 clone のプロジェクトで Claude Code を起動する。hook の根はそのプロジェクト、sh の根はワークスペースになり、ログと state の置き場が割れる。hook の拒否文が案内するワークツリーの作成を sh が「ワークスペースの外に出ます」と断る（重さは中）
- `CLAUDE_PROJECT_DIR` が無いターミナルで `ccnavi` を直接打つ。ワークスペース内 clone のプロジェクトの中では、そのプロジェクトが根になり、提案やワークツリーが見えず「チケットはありません」と出る。`.ccnavi/CCNAVI.md` はこの直打ちを案内している（重さは major）。`--root` を付けるか sh のラッパーを通すと避けられる
- `.claude/` を持たない単体 clone を `.claude/` のある祖先（`$HOME` など）の下に置く。ターミナルの直打ちだけで、祖先が根になる（重さは minor）
- ワークツリーの中で Claude Code を起動する。`CLAUDE_PROJECT_DIR` がワークツリーを指し、ログと state がワークツリーの中にできる（重さは中）
- sh の `projects` の判定は、大文字小文字を区別して比べる。パスは区別せずに比べる決まりに反する。Linux では別ディレクトリなので実害を再現できず、macOS・Windows の実機では確かめていない（重さは minor）

## 決定

まだ決めていない。選択肢は次のとおり。

| 案 | 内容 |
|---|---|
| a | いまのまま。hook は `CLAUDE_PROJECT_DIR`、実行ファイルは `.claude/` を持つ最も近い祖先を採る |
| b | 祖先を辿るとき、`<親>/projects/<名前>` の形で親が ccnavi の根（`.ccnavi/scripts/ccnavi-common.sh` を持つ）なら親を採り、sh と揃える |
| c | 根の目印を `.claude/` ではなく `.ccnavi/scripts/ccnavi-common.sh` にする |

- b と c は、ターミナルの直打ちの食い違いと、`.claude/` を持つ祖先の取り違えを減らす。`CLAUDE_PROJECT_DIR` が来る hook 側の食い違いとワークツリーで起動した場合は、これだけでは直らない
- sh に `CLAUDE_PROJECT_DIR` を第一候補として読ませる案は、選択肢にならない。Bash ツールの環境に `CLAUDE_PROJECT_DIR` は来ないことが設計書の付録で確かめてあり、コマンド行に付けて渡すことも `wrapguard.py` が止める
- sh の `projects` の判定は、`pathcase` 相当の小文字化比較にすればよい。実機での確認が先

## 理由

根は、ログと state の置き場、ワークツリーを切る場所、ルールの `{root}` を決める。食い違いはデータを失わせないが、案内どおりに打っても断られる形を作る。どの案が合うかは、プロジェクトでの起動を正式な形として扱うかで変わるので、それを決めるまで選択肢のまま残す。

## 得たもの・失ったもの

決めていないので、案ごとの得失を残す。

- a: 実装を変えずに済む。ターミナルの直打ちとプロジェクトでの起動の食い違いが残る
- b: ワークスペース内 clone の直打ちが sh と揃う。`projects/` という名前と、親の目印に頼る
- c: 目印が 1 つになり、`.claude/` を持つ祖先の取り違えが無くなる。`.claude/` だけを持つ配置（ccnavi を入れていないプロジェクト）では、根が見つからない形が増える

## 採らなかった案

sh に `CLAUDE_PROJECT_DIR` を読ませる案（上のとおり、環境に来ない）。
