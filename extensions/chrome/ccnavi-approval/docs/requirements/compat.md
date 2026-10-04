---
title: ccnavi 承認ボードの互換の版
type: requirements
description: 統合先の CCNAVI_COMPAT と同梱の互換の版が違うときのふるまい
tags: [design-doc, extension, approval]
keywords: [要件, 互換, CCNAVI_COMPAT, 版, 更新]
---

# 互換の版

入口: [ccnavi 承認ボードの要件](../requirements.md)

| ID | 区分 | 要件 |
|---|---|---|
| REQ-CHR-03 | 状態 | ワークスペースの統合先の `.ccnavi/scripts/ccnavi-common.sh` の `CCNAVI_COMPAT` と、組み立てのときに埋め込んだ同梱の互換の版が違う間、Chrome 拡張は、どちらを更新するかを出し、承認と取り下げを出さず、承認・取り下げ・レビュー済みを書かないこと。「始める」でもブランチを作らないこと（REQ-APV-17） |

互換の版を読む行の形は本体の REQ-EXT-06（[要件 2.13](../../../../../docs/requirements/extension-if.md)）。
