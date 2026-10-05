---
title: ccnavi 承認ボードのPAT の期限
type: requirements
description: PAT の期限の読み方と知らせ方
tags: [design-doc, extension, approval]
keywords: [要件, PAT, 期限, バッジ, chrome.alarms]
---

# PAT の期限

入口: [ccnavi 承認ボードの要件](../requirements.md)

- PAT の期限（作るときの既定は 90 日）: service worker が応答ヘッダ `github-authentication-token-expiration` から読み、読めなければ登録のときの日付。
  切れる 7 日前からボードの帯とアイコンのバッジで知らせる（`chrome.alarms` で 1 日 1 回）
