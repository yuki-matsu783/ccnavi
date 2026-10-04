---
title: ccnavi 承認ボードのissue から始める
type: requirements
description: 「始める」で issue から親のブランチを作るときのふるまい
tags: [design-doc, extension, approval]
keywords: [要件, 始める, issue, 識別子, 親のブランチ, issue_identifier]
---

# issue から始める

入口: [ccnavi 承認ボードの要件](../requirements.md)

| ID | 区分 | 要件 |
|---|---|---|
| REQ-CHR-06 | 事象 | 承認者がボードの「issue を読む」で開いた issue で「始める」を押したとき、Chrome 拡張は、同梱の ccnavi の `ticket.issue_identifier` が issue の番号とタイトルから決めた識別子（`feature-<番号>-<slug>`・`feature-<番号>-<プロジェクト名>-<slug>`）の親のブランチを、統合先の今の先頭から作ること。その識別子が統合先の `done/` にある・同じ名前のブランチがある・開いた親子のチケットに同じ識別子がある・予約の名前に当たる・互換の版が違う、のどれかなら作らないこと。PR/MR は作らないこと（最初の push の後に `ccnavi-review.sh request` が作る）（REQ-APV-19） |
