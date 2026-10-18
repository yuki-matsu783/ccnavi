---
type: adr
title: ユーザ専用の入口を、エージェントも使う入口から分離する
description: ユーザだけが打つ操作を、実行ファイルの user 名前空間と ccnavi-user-*.sh に集め、hook は入口の名前だけで止める
tags: [rules, approval, selfguard]
keywords: [user, 名前空間, 入口, 分離, 表, 承認, 端末, hook, preview, 読み取り]
---

# ADR-0142: ユーザ専用の入口を、エージェントも使う入口から分離する

状態: 提案

## 状況

ユーザだけが打つ操作（承認・レビュー済みの記録・早めに閉じる・記録の削除・テンプレートの取り込み・親を閉じたあとの
片付けなど）は、エージェントも使う入口と同じ実行ファイル・同じ sh に同居している。同じオプションの形で、エージェントに許す形と
許さない形が分かれている（`--agree` は止め、`--agree --preview` は通す。`ccnavi-review.sh decide <N>` は
`--preview` なら通し、`--choices` なら止める。`ccnavi-sync.sh` は `--forget` だけ止める、など）。

そのため hook はオプションの綴り・並び・組み合わせを読む。免除の `--preview` を単独の語として読む後読み、ボードの経路のオプションの組み合わせ、
引数を変数や glob で組み立てて隠す形の判定、実行ファイルのオプションの一覧（`EXE_OPTIONS`）との突き合わせがあり、
抜け道が見つかるたびに足されてきた。ユーザ専用の定義も、hook の正規表現、オプションの一覧、実行ファイルの端末要求の呼び出し、
拒否文、各 sh のヘルプに散らばる。

## 決定

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| ユーザ専用の実行ファイルの操作は、すべて `ccnavi user <操作>` に集める。`user` は実行ファイルの**最初の引数**のときだけ名前空間として読む（オプションの後ろの `user` は、ただの語） | hook が「ccnavi の呼び出しの最初の引数が `user`」だけを見れば止められる。オプションの綴りも組み合わせも読まない。`ccnavi --docs user` のような読み取りは最初の引数がオプションなので当たらない | `user` をコマンド行のどこでも探す（`--keyword user` まで止まる） |
| 操作は次の対応で `user` に移す: `--agree` → `user agree [<識別子>…]`、`--reviewed <N>` → `user reviewed <N>`、`--close-early` → `user close-early`、`--prune` → `user prune`、`--templates --import/--save-draft/--delete-draft/--rename-template/--move-template/--delete-template` → `user templates import/save-draft/delete-draft/rename/move/delete`、`sync tidy` → `user sync tidy`、`worktree retire/branch` → `user worktree retire/branch`。`--yes`・`--digest`・`--expect`・`--via`・`--reason`・`--result` などの付随するオプションは、そのまま `user` の操作に付ける | 操作の中身（実装）は変えず、入口の綴りだけを変える。実行ファイルは `user <操作> …` を従来の内部のオプションの形へ読み替えて、既存の処理に渡す | 実装を作り直す |
| `user` の外で、ユーザ専用のオプション（`--agree`（`--preview` なし）・`--reviewed`・`--close-early`・`--prune`（`--preview` なし）・`--yes`・テンプレートの書き込みのオプション・`sync tidy`・`worktree retire/branch`）が渡されたら、実行ファイルは何も動かさず終了コード 2 で断り、`user` の形を案内する | オプションの形でも動く入口を残すと、hook がオプションを読み続けることになる。実行ファイルと hook は同じ版で配られるので、オプションを断れば hook はオプションを見なくてよい | オプションの形を別名として残す |
| エージェントも使う読み取り（`--agree --preview [--verify] [--json]`、`--prune --preview`）は `user` の外に残し、動かす側と同じ関数を呼ぶ。実行ファイルの中では、オプションの読み替えと断りを `user_only` の表 1 本で行う | 動かす実装と見せる実装が 2 つに分かれない。hook は読み取りの形を免除として読まなくてよい（そもそも止めない） | 読み取りも `user` に入れてエージェントには使わせない |
| hook の止めは 2 種類だけにする: ① 実行ファイルの呼び出しの最初の引数が `user`（変数・glob・コマンド置換で `user` になりうる語も止める）。② `ccnavi-user-*.sh`（引数に依らず） | 入口の名前だけで決まる。オプションの組み合わせ・`--preview` の免除・隠した引数の組み合わせの判定を持たない | オプションごとの正規表現を持ち続ける |
| ユーザ専用の sh は `ccnavi-user-<操作>.sh` に分ける: `ccnavi-agree.sh` → `ccnavi-user-agree.sh`、`ccnavi-push-approved.sh` → `ccnavi-user-push-approved.sh`、`ccnavi-sync.sh --forget` → `ccnavi-user-forget.sh`、`ccnavi-review.sh close-early` → `ccnavi-user-close-early.sh`、`ccnavi-review.sh decide` の書く側（`--choices` 経路を含む）→ `ccnavi-user-decide.sh`、`ccnavi-clean.sh --branch` → `ccnavi-user-clean-branch.sh`。もとの sh からはこれらの操作を外す。`ccnavi-review.sh decide <N> --preview`（一覧を見るだけ）はエージェントも使うので残し、共有の処理は `ccnavi-common-*.sh` に置いて両方が読む | sh も、混在をやめれば名前だけで止められる。同じ処理を複数の sh に写さない | 混在した sh の中で引数を読む |
| 古い入口の名前（旧 sh 名と、旧 sh の中のユーザ専用のモード）は、表の `legacy` の行として hook が止め続ける。実行ファイルの旧いオプションは断る | 配布先に古い sh が残っていても、エージェントに打たせない。配布の setup が旧 sh を消す | 旧名は放置する |
| 実行ファイルの端末要求（`_from_terminal`）は残す。オプションの表示名は表の `Op.terminal` から引く | 擬似端末を使われない限り、hook が抜けられた場合の 2 段目になる | 端末要求を外す |
| ユーザ専用の定義は `tickets/user_only.py` の表を正本にする。hook の正規表現、実行ファイルのオプションの読み替えと断り、拒否文に載せる入口の一覧、配布の旧名の掃除は、この表から作る。表と各 sh・docs の食い違いはテストで検出する | 操作を足すとき、直す場所を 1 か所にする | 各所に正規表現を書く |
| VS Code 拡張・Chrome 拡張・各 sh・docs・テストは、新しい入口に合わせる | 拡張の子プロセスは `user` の入口を呼ぶ。hook を通らないので影響しない | 拡張だけ旧形に残す |

## 得たもの・失ったもの

- 得たもの: hook の止めが入口の名前の一致だけになり、オプションの組み合わせ・免除・隠した引数の判定が要らなくなる。ユーザ専用の定義が表 1 つになる
- 失ったもの: **コマンドの形が変わる。** 承認・レビュー済み・早めに閉じる・片付けなどを打つ手順、拡張、docs、テストが一斉に変わる。旧名は断られる（旧 sh は hook が止め続ける）
- 失ったもの: `user` の最初の引数でない呼び出し（`ccnavi --root X user agree`）は動かない。ラッパーの sh は `user <操作>` を先頭に置く
- 失ったもの: 名前を変えたコピー・`awk` の `system()` など、ccnavi の名前を持たない呼び出しは hook が見ない（従来どおり。実行ファイルの端末要求が 2 段目）

## 採らなかった案

- ユーザ専用だけを別の実行ファイル（`ccnavi-user`）にする: ビルド・配布・導入 sh・拡張が 2 本になり、影響が最も広い
- sh だけを分離して、実行ファイルのオプションは残す: オプションの定義が hook と実行ファイルに二重に残る
