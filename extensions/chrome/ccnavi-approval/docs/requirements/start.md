---
title: ccnavi 承認ボードのissue から始める
type: requirements
description: 「始める」で issue から親のブランチを作るときのふるまい
tags: [design-doc, extension, approval]
keywords: [要件, 始める, issue, 識別子, 親のブランチ, issue_identifier]
---

# issue から始める

入口: [ccnavi 承認ボードの要件](../requirements.md)

- 「始める」: ボードの「issue を読む」で開いた issue を読み、「始める」を押すと、issue の番号から決めた識別子
  （`feature-<番号>-<slug>`・`feature-<番号>-<プロジェクト名>-<slug>`。slug は issue のタイトルから作る。同梱の ccnavi の `ticket.issue_identifier`）の親のブランチを統合先の今の先頭から作る。
  統合先の `done/` にある・同じ名前のブランチがある・開いた親子のチケットに同じ識別子がある・予約の名前・互換の版が違う、のどれかなら作らない。
  PR/MR は作らない（最初の push の後に `ccnavi-review.sh request` が作る）
