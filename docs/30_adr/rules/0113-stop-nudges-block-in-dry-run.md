---
type: adr
title: Stop の促し（finish・match: Stop）は dry-run でも止める
description: finish の促しと match: Stop のルールの促しを、モードに関わらず Stop で止めて渡す
tags: [hook, rules, ticket]
keywords: [Stop, nudge, dry-run, 促し, finish, "match: Stop", NUDGE_STOP_RULE]
---
# ADR-0113: Stop の促し（`finish`・`match: Stop`）は `dry-run` でも止める

状態: 採用

## 状況

ADR-0087（`finish` の促し）と ADR-0090（`match: Stop` のルール）は、`dry-run` では止めず、
止めたはずの文を `systemMessage` に載せていた。`systemMessage` はユーザにしか届かない。
`Stop` でモデルに文を届ける経路は、止めて `reason` に書くことしか無いので、`dry-run` のワークスペースでは
促しがモデルに一度も届かず、振り返りも `finish` の打ち忘れの指摘も働かなかった（ユーザの指摘）。

## 決定

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| 2 つの促しは、モードに関わらず `decision: block` で止める。`dry-run` の `systemMessage` への読み替えをやめる | 促しの目的は、モデルに文を届けること。届かない促しは、書いていないのと同じ | モードの約束を守って `dry-run` では止めない（ADR-0087・0090 の側） |
| `disable` は従来どおり何もしない | `disable` は ccnavi 全体を止めるモード | `disable` でも止める |
| 記録の `enforced` は、止めた回は真にする | 実際に止めたので | モードに合わせて偽にする。止めたのに「適用していない」と読める |

ADR-0087 と ADR-0090 の `dry-run` の行は、この ADR で置き換える。

## 得たもの・失ったもの

- 得たもの: `dry-run` のワークスペースでも、`finish` の促しと `match: Stop` の振り返りがモデルに届く
- 失ったもの: **`dry-run` が「何も適用しない」ではなくなった。** Stop の促しだけは、`dry-run` でもターンを伸ばす
- 失ったもの: `dry-run` で「enable なら止めていた」の通知（`[ccnavi dry-run] enable would have blocked this stop:`）は出なくなる。止めた文は `reason` としてモデルに届く

## 採らなかった案

- **ルールごとに `dry-run` で止めるかを選ぶ欄を足す。** 設定の欄が増える。ユーザは 2 つの促しを一律に止めることを選んだ
