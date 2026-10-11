---
type: adr
title: git のラッパーでも、自己防衛の対象を消す・別のコミットの中身に戻す形は hook が止める
description: ccnavi-git.sh の rm・restore --source・checkout <ref> <パス> が自己防衛の対象に向く形を、組み込みの builtin-guard-wrapper-paths で止め、sh も 2 重目に持つ
tags: [judging, selfguard, sh-scripts, hook]
keywords: [ccnavi-git.sh, rm, restore, checkout, 自己防衛, builtin-guard-wrapper-paths, DENY_WRAPPER_CORE_PATH, core_hit, 衝突の解決]
---
# ADR-0120: git のラッパーでも、自己防衛の対象を消す・別のコミットの中身に戻す形は hook が止める

状態: 採用

## 状況

2026-10-11 の見直しで、`sh .ccnavi/scripts/ccnavi-git.sh rm .ccnavi/scripts/ccnavi-git.sh` が hook で allow になり、
ラッパーも実際にファイルを消すことが分かった（`rm -r .ccnavi/scripts`・`restore --source=HEAD~5 -- .ccnavi` も同じ）。
シェルの `rm .ccnavi/scripts/ccnavi-git.sh` は `builtin-guard-setting-files` で止まる。止める式は書き込みのコマンドが
コマンドの頭に立つ形だけを見るので、`sh … ccnavi-git.sh rm <パス>` は当たらず、共通ルールの allow（`ccnavi-git`）が当たっていた。
ラッパーの中の `rm` は `-f` と木全体だけを、`restore` は承認済みチケットの置き場だけを見ていた。

ADR-0114 は、生の git の組み込みの止めを「チケット制御が有効で、ラッパーがあるとき」に足すと決めた。ラッパーを消すと
その条件が外れ、生の git が確認に落ちる。権限モードが auto か bypass なら通る。1 手で組み込みの止めを外すレバーになっていた。

## 決定

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| hook の組み込み `builtin-guard-wrapper-paths`（`DENY_WRAPPER_CORE_PATH`）で、`ccnavi-git.sh` の `rm`（`--cached` も）・`mv`・`restore --source <ref>`・`checkout <ref> <パス>` が自己防衛の対象に向く形を、ルールより先に止める | 止める判定は hook が持つ（P13）。ルールの allow より先に見ないと、共通ルールの `ccnavi-git` の allow が通す | sh の中だけで止める。sh は書き換えられる側で、P13 では 2 重目 |
| 対象は `builtin-guard-setting-files` と同じ場所の式で見る。書いたとおりのパスと、居場所（cwd と `cd` の追跡）で解いた絶対パスの両方に当てる | シェルの書き込みと同じ場所を守る。実行ファイル・動かした ccnavi ディレクトリ・共通レイヤーも同じ式に入っている | 名前の一覧を別に持つ。2 か所の一覧が食い違う |
| 守る場所を中に含むディレクトリ（ツリーの頂上、`.ccnavi`・`.claude`・`.git` を直に、または 1 つ下に持つディレクトリ、実行ファイルなどの上）も当たる | `restore --source <ref> -- .`・`rm -r projects` で中身ごと消す・戻す | パスの文字列だけを見る |
| `:` で始まる pathspec、グロブの前に固定のディレクトリが無い指定、`--pathspec-from-file`、居場所が読めないときの `..` は当たるとみなす | git の pathspec のグロブは `/` もまたぐので、`*.sh` はラッパー自身に当たる。読みで決められないものは止める側に倒す（ラッパーの承認済みチケットの置き場の検査と同じ） | グロブを展開して確かめる。hook は git を起こして pathspec を解かない |
| 索引の中身に戻す `restore`（`--staged`、衝突を解く `--ours` / `--theirs`）は通す | ルールファイルが衝突したときに直す道がここしか無い（ADR-0114 がラッパーで通すと決めた経路）。索引の中身はエージェントが書けない場所から来る | `restore` を全部止める。衝突したルールファイルを直せなくなる |
| `CCNAVI_GUARD_CORE_FILES` で切れる。チケット制御と、組み込みの既定への退避に依らず足す | 自己防衛の一部で、シェルの `rm` を止める組み込みと同じ口で切る。退避の間もシェルの `rm` は既定の 1 本が止めている | チケット制御に揃える。チケット制御を切ったワークスペースで、ラッパーだけが自己防衛の対象を消せる |
| ラッパーにも同じ検査（`core_hit`）を 2 重目に置く。見るのは既定の置き場の名前だけ | hook が読み切れずに確認に回した呼び出しを、ユーザが許したときの受け皿 | sh には置かない |
| 拒否の文面は、変更をユーザに依頼することと、通る形（衝突の解決・索引だけを戻す形）を案内する | P8。止めた理由と代わりの道を同じ文で返す | 止めたことだけを言う |

## 得たもの・失ったもの

- 得たもの: ラッパー越しに、シェルの `rm` で止まる場所を消せない。ラッパー自身が消えず、生の git の組み込みの止めが外れない
- 失ったもの: `rm '*.pyc'` のように、固定のディレクトリの無いグロブはラッパーでも通らない。1 つずつ名指しするか、ディレクトリを前に付ける
- 失ったもの: 自己防衛の対象をエージェントが git で過去の中身に戻す道は無い。戻すのはユーザ
- 見ないもの: `stash pop` / `apply` と `merge` は作業ツリーの変更を退避・取り込むだけで、守る場所を自分では過去の中身にしないので見ない

## 採らなかった案

- **ADR-0114 の条件からラッパーの有無を外す。** ユーザの決定で、ラッパーの無い配布先で git を打つ道が無くなるため採らない。穴はこの ADR で別に塞ぐ
- **シェルの書き込みの式（`builtin-guard-setting-files`）に `ccnavi-git.sh rm` の形を足す。** `restore` の `--source` の有無や `checkout` の語の位置、
  居場所から解いた絶対パスは式で読めない
