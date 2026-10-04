---
title: ccnavi 承認ボードの承認
type: requirements
description: 承認を押したときの読み直し・照合・親のブランチへの 1 コミット
tags: [design-doc, extension, approval]
keywords: [要件, 承認, ダイジェスト, createCommitOnBranch, expectedHeadOid, 1 コミット]
---

# 承認

入口: [ccnavi 承認ボードの要件](../requirements.md)

- 承認: 押すと親子のチケット 1 組ぶんを読み直し、Python に見せた一覧とダイジェストを比べさせ、Python が返した書くもの（Changes）を
  GraphQL の `createCommitOnBranch`（`expectedHeadOid` = 読んだ先頭）の 1 コミットで親のブランチへ書く。先頭が動いていたら
  読み直して判定し直し、ダイジェストが同じなら書き直す（3 周まで）。書いた後、新しい先頭の中身が書いたとおりかを blob の sha で確かめる
