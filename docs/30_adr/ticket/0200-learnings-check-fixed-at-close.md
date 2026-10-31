---
type: adr
title: 学びの記録の検査は、フェーズ定義の deliverables に載せず、子を閉じる操作に固定で持つ
description: 子の ticket finish が wip/handoff/learnings/<子>.md の有無と空でないことを確かめる検査を、フェーズ定義の deliverables に書かず、閉じる操作の検査の隣に固定で置く
tags: [learning-wiki, ticket, phases]
keywords: [学びの蓄積, 気づきの記録, deliverables, ticket finish, フェーズ定義, 閉じる検査, 書き漏れ]
---
# ADR-0200: 学びの記録の検査は、フェーズ定義の deliverables に載せず、子を閉じる操作に固定で持つ

状態: 採用

## 状況

学びの蓄積が有効な親の子は、閉じる前に `wip/handoff/learnings/<子>.md` を子のブランチの HEAD にコミットしておく必要がある。
この記録が在って追跡されていて、空でないことを `ticket finish` が確かめる。成果物の有無を見るフェーズ定義の `deliverables` と見る点が同じなので、`deliverables` に載せる形も考えられた。

## 決定

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| 学びの記録の検査を、子の閉じる操作の検査のうち成果物を見る検査の隣に固定で置く。フェーズ定義の `deliverables` とは別に持つ | 置き場が固定で、定義ごとに変える意味が無い。全定義に書き足すと、書き漏れた定義の子だけ検査から抜ける | フェーズ定義の `deliverables` に載せる |

## 得たもの・失ったもの

- 得たもの: 定義の書き漏れで検査が抜けない
- 失ったもの: 定義ごとに検査の置き場所や中身を変えることはできない。外すときは、フェーズ定義の `learning` 欄を `off` にする

## 採らなかった案

上の表のとおり。
