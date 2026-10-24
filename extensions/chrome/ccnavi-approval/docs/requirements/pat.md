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
- PAT の権限: 承認・レビュー済みの書き込みと「始める」には、GitHub は Contents の書き込み、GitLab は `api` スコープが要る。残った指摘の行き先を確定するときは、さらに GitHub は Pull requests・Issues の書き込み、GitLab は Developer 以上のロールが要る。
  残った指摘の行き先を確定するときは、書く前に権限を確かめ、足りなければ何も書かずに、足りない権限の名前を出して止まる。
  fine-grained の GitHub PAT は書く前に権限を読めないので、書いて断られたとき（403/404）にも、要る権限の名前を出す
