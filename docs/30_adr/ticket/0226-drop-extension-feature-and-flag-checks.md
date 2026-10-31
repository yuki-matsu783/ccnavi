---
type: adr
title: 拡張が実行ファイルの flags・features を事前に確かめる処理をやめる
description: VS Code 拡張が --version --json の flags・features を見て、無い機能の画面を開かない処理（missingFlags・missingFeatures・probeVersion と各画面の check*）を消す。版のずれは断らず、実行ファイルの断りに任せる。Lock.turns も必須にする
tags: [extension, exe-boundary, distribution]
keywords: [後方互換, flags, features, missingFlags, missingFeatures, probeVersion, 版のずれ, 同時に配る, Lock.turns, ADR-0224]
---
# ADR-0226: 拡張が実行ファイルの flags・features を事前に確かめる処理をやめる

状態: 採用

## 状況

ADR-0224 は、sh と拡張の互換の版の食い違いの通知を消し、sh・拡張・実行ファイルを同時に配る前提にした。そのとき、拡張が `--version --json` の `flags`・`features` を見て、無い機能の画面を開かない処理は「使う機能の有無で動作を分ける処理」として残した。

しかし同じ前提のもとでは、これも版のずれ対策の一種で、後方互換を考えない決まりに反する。画面を開くたびに `--version --json` を起こし、各画面（取り込み待ち・ミラー・テンプレート・計画編集・フロー編集・承認・対応方針）に「古い版です」の分岐を持たせていた。

## 決定

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| `core/version.ts` の `missingFlags`・`missingFeatures`・`parseVersionJson` と、`ccnavi.ts` の `probeVersion`・`hasSource`・`check*`（`checkHandoff`・`checkMirror`・`checkTemplates`・`checkTemplateEdit`・`checkPlanOrder`・`checkUserEntry`）、`runFlowLint` の事前確認を消す | 同時に配る前提では、版のずれは配り方の誤りで、まれな異常状態。ずれたときは実行ファイルの断り（argparse の苦情など）をそのまま見せれば足りる | 事前確認を残す |
| 取り込み待ち画面の `disabled` の表示、ミラーの `unsupported` の状態、`handoffEntryOf` を消す。入口は常に開ける（`HANDOFF_ENTRY`）。案内の間の無効（`HandoffEntry.enabled` 偽）は残す | 無効にする理由が版のずれだけだった | 理由の型ごと消す（案内の間の無効に使うので残す） |
| `Lock.turns` を必須にし、`turnWarning` の `?? []` を消す | 錠を作る側がすべて応答中のセッションを渡す | 任意のままにする |
| `core/version.ts` には `EXTENSION_COMPAT` だけを残す | 配布の組み立て（`scripts/ccnavi-package.sh`）と `tests/core/test_version.py` が値を読む | 宣言ごと消す |

実行ファイルの `--version --json` の `flags`・`features` の出力は、Chrome 拡張や診断が読みうるので、この決定では消さない。（その後、`flags`・`features` の出力は消した。）

## 得たもの・失ったもの

- 得たもの: 画面を開くたびの `--version --json` の起動と、「古い版です」の分岐・文面・試験が無くなる
- 失ったもの: 版のずれたとき、画面が機能の有無を言わずに実行ファイルの断りで止まる

## 覆した記述

この決定が覆した、先の ADR の記述。

| ADR | 覆した記述 |
|---|---|
| ADR-0224 | 拡張が `flags`・`features` を見て、無い機能の画面を開かない処理を残す |
| ADR-0222 | 機能の有無を `features` で確かめる処理を残す。`Lock.turns?` を任意のままにする |
| ADR-0134 | 画面からの新しい操作を使えるかを `flags` と `features` で見分ける |
| ADR-0171 | `features` の `entry-marker` を、拡張が `--entry` を付けるかの見分けに使う |
