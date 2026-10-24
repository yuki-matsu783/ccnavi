---
type: adr
title: パスと名前の大文字小文字は、どの機械でも区別せず同じものとして扱う
description: 大文字小文字だけが違うパス・名前を同一として比べ、両立させない。機械ごとの分岐（normcase・CASE_INSENSITIVE）をやめ、比べるキーを pathcase.fold に一本化する
tags: [environment, worktree]
keywords: [大文字小文字, casefold, normcase, pathcase, CASE_INSENSITIVE, ワークツリー, 識別子]
---

# ADR-0140: パスと名前の大文字小文字は、どの機械でも区別せず同じものとして扱う

状態: 採用

## 状況

ツリーの判定（`tree.py`）は`os.path.normcase`と`CASE_INSENSITIVE`で「区別しない機械でだけ区別しない」にしていた。
Linux で区別し、Windows と macOS の既定では区別しないので、同じ入力で答えが機械ごとに割れる。
実際に、ワークツリーの名前を表記違いで切ったときのテストは Linux でスキップされ、Windows でだけ走っていた。
`default_root()`の取り違え（ワークツリーを`.claude`で止まる）の直しで、判定の大文字小文字が機械で割れる箇所が洗い出された。

## 決定

大文字小文字だけが違うパス・名前は同じものとして扱い、両立させない。

- 比べるときはどの機械でも区別しない。比べるキーは`infra/pathcase.fold()`に一本化する（`normcase`の後に`casefold`）
- キーはファイルを開く表記には使わない。表記は元のまま使い、比べて残りを取り出すときは`tree.parts_under()`を使う
- `CASE_INSENSITIVE`と、表記まで同じであることを求める`exact_name`をやめる。`worktree_path()`は在るディレクトリの実際の表記で返す
- `wip/`の置き場（`wip/handoff/` `wip/eli5/`）も区別しない。範囲の除外も`ready`の片付けも同じ見方にそろえる
- 例外: git が追跡する綴りに依る名前（状態のディレクトリ、ブランチ名、`.gitignore`の`/scratchpad`）は、git が区別するので綴りまで求める

## 理由

Windows で clone したときにだけ失敗する判定を、Linux のテストで見つけられない。機械で分岐する書き方をやめれば、
Linux の CI が Windows の挙動を保証する。

## 得たもの・失ったもの

- 判定の答えが機械によらない。Linux でも表記違いのワークツリーが承認済みチケットに結び付く
- Linux で`Foo`と`foo`を別のディレクトリとして使う運用はできなくなる（意図どおり）
- 実体パスの同一性判定も`casefold`になるので、Linux で別ディレクトリの`Foo`と`foo`を同一と見る。同居させない運用が前提
- sh の`case`文の一部は、ccnavi が自分で組んだ綴りを前提にしていて、まだ機械の綴りに依る

## 採らなかった案

- 機械ごとの分岐を残す: 今回の不具合の原因そのもの
- ファイルシステムの実際の挙動を調べて分ける: 判定が環境に依り、テストで固定できない
