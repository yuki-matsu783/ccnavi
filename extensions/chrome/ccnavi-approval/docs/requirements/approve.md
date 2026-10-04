---
title: ccnavi 承認ボードの承認
type: requirements
description: 承認を押したときの読み直し・照合・親のブランチへの 1 コミット
tags: [design-doc, extension, approval]
keywords: [要件, 承認, ダイジェスト, createCommitOnBranch, expectedHeadOid, 1 コミット]
---

# 承認

入口: [ccnavi 承認ボードの要件](../requirements.md)

| ID | 区分 | 要件 |
|---|---|---|
| REQ-CHR-01 | 事象 | 承認者が承認を押したとき、Chrome 拡張は、親子のチケット 1 組ぶんを読み直し、同梱の ccnavi に見せた一覧とダイジェストを比べさせて判定し直させ（REQ-APV-15）、返った書くもの（Changes）を GraphQL の `createCommitOnBranch`（`expectedHeadOid` = 読んだ先頭）の 1 コミットで親のブランチへ書くこと。先頭が動いていたら読み直して判定し直し、ダイジェストが同じなら書き直すこと（3 周まで）。ダイジェストが違えば何も書かないこと。書いた後、新しい先頭の中身が書いたとおりかを blob の sha で確かめること |
| REQ-CHR-04 | 常時 | Chrome 拡張は、書く先が保護されたブランチの名前（`main`・`master`・`develop`・`release`・`release-*`・`release/*`。大文字小文字は問わない）か統合先の名前なら、承認・取り下げ・レビュー済みを書かないこと。書くパスが置き場（`wip/proposals/`・`.ccnavi/approved/`）の外にあるときも書かないこと（REQ-APV-17） |
