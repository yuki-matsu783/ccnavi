---
type: adr
title: 片付けでは、git が無視しているファイルを止める理由にせず、scratchpad へ退避する
description: ワークツリーの片付けで、git が無視している生成物でないファイルを名指しして止めるのをやめ、ワークスペースルートの scratchpad/worktrees/<名前>/ へ rename で退避してから消す。生成物は一覧で消す
tags: [worktree, cleanup]
keywords: [片付け, 無視, 退避, scratchpad, worktree drop, 生成物, target, Cargo, IGNORED, ccnavi-clean]
---

# ADR-0231: 片付けでは、git が無視しているファイルを止める理由にせず、scratchpad へ退避する

状態: 採用

## 状況

`worktree drop` は、git が無視している生成物でないファイル（`.env`、Rust の `target/`、テストの `out/` など）が
あると、何も消さずに名指しして止まっていた。`git worktree remove` が無視ファイルごと消して戻せないためで、
データを失わない点は正しい。一方、削除一覧に無い生成物（Rust の `target/`）が毎回止めた。一覧を言語ごとに
増やし続けるのは保守の負担になり、止まるたびにユーザが手で退避・削除する必要があった。

## 決定

| 決めたこと | なぜ |
|---|---|
| 生成物でない無視ファイルは、止める理由にしない。`drop` が `<ルート>/scratchpad/worktrees/<名前>/<元の相対パス>` へ、最上位の項目ごとの rename で退避してから `git worktree remove` する | データは失われず、止まって人手を待つ状態が無くなる。rename は中を走査しないので、同じボリュームなら一瞬で、深い `node_modules` の 260 文字の制限にも掛からない |
| 退避先が git に無視されていない・退避先に同じパスがある・移せない、のどれかなら、何も動かさず（途中で止まったときは退避済みを言って）理由を出して止める | `.env` のコミットと、前の退避の上書きを防ぐ。まれな異常は推測や修復をせず、状態と直し方を言って止める |
| 退避したものは ccnavi が消さない | 戻せない削除はユーザの判断にする。`scratchpad/` の既存の扱いと同じ |
| 生成物は退避せず、一覧で消す。`__pycache__`・`.mypy_cache`・`.tox`・`.next` などは名前だけ、`out`・`dist`・`.cache`・`target`・`build` は隣に印のファイルがあるときだけ。追跡されているものは消さない。一覧は `worktrees.py` だけが持ち、`ccnavi-clean.sh` は実行ファイル（`worktree clean`・`worktree drop`）を呼ぶ。Node の `ccnavi-clean.js` は廃止した | 作り直せる大きなキャッシュは、退避すると scratchpad に溜まる。一覧を2か所に持つと食い違うので1か所にする。Windows の深いパスは、Python の `shutil.rmtree` と長いパスの接頭辞で扱う |
| gitが無視する出力は、はじめから `scratchpad/worktrees/<名前>/` に出す、と文書に書く。強制はしない | 出力先を1か所にまとめる。守られなくても退避が受ける |

## 結果

- 止まる条件が減る。`worktree retire` の事前の拒否（`blocked`）から、無視ファイルの項目を外した
- 退避の成否は rename を試すまで分からないので、事前の判定には入れない
- 同じ名前のワークツリーを作り直して退避が重なると、同じパスは上書きせず止まる。ユーザが退避先を消してから打ち直す
- 後方互換は考えない。無視ファイルで止まるこれまでの挙動を読む処理は作らない
