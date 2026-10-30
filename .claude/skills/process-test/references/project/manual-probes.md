---
type: skill-reference
title: 手動の probe を回す
description: tests/manual/ の probe は自動テストに入らず仕様変更で古くなるので、回す前に前提を確かめ、結果は理由まで見る
tags: [process-test]
keywords: [probe, manual, tests/manual, 手動, 偽陽性, PASS, 前提, github, gitlab]
---

# 手動の probe を回す

- 回す前に、probe の手順が今の ccnavi の前提に合っているかを読む。見る点は、一時リポジトリへの `.ccnavi/scripts/` の配置、子より先に親を `ticket start` すること、`review-request` の `--eli5`、`review decide` の入力など。`tests/manual/` は自動テストが呼ばないので、仕様が変わっても落ちずに古くなるため
- 「止まる」ことを確かめる段は、止まったことだけでなく、止まった理由（拒否の文面やコード）が期待どおりかまで見る。たとえば Agent を止める段は、終了コード 0・判定 deny・理由に `DENY_PHASE_REVIEW` の 3 つを見る。前提の崩れで別の理由で止まっても、止まったことだけを見ると PASS に見えるため
- 前提を直したら、probe の docstring の「用意するもの」も同じ変更で直す。次に回す人が最初に読む場所なので
