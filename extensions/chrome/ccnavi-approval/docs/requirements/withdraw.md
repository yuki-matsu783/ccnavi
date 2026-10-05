---
title: ccnavi 承認ボードの取り下げ
type: requirements
description: 着手前の新規の承認を取り下げるときのふるまい
tags: [design-doc, extension, approval]
keywords: [要件, 取り下げ, 承認コミット, todo, doing, workflow.yml]
---

# 取り下げ

入口: [ccnavi 承認ボードの要件](../requirements.md)

| ID | 区分 | 要件 |
|---|---|---|
| REQ-CHR-02 | 事象 | 承認者が着手前の新規の承認を取り下げたとき、Chrome 拡張は、承認コミット（`doing/<識別子>.md` を足した、親が 1 つの最新のコミットで、親のブランチの first-parent の鎖の上にあるもの）の親の提案を同梱の ccnavi に渡し、ccnavi が返した書くもの（承認済みチケットと待ち方のファイルを消し、その提案をそのまま `todo/` へ戻す）を親のブランチへ 1 コミットで書くこと。取り下げを出すかは同梱の ccnavi に決めさせること（REQ-APV-16） |

承認はチケットの中身を変えないので、`doing/` の中身が承認コミットの親の提案とバイト単位で同じなら、改版も着手もされていない。
GitHub の画面で動かした承認（rename のコミット）も同じように取り下げられる。親は、待ち方のファイル（`phases/<親>/workflow.yml`）が
あれば、その中身が承認したときの待ち方と同じであることも求め、一緒に消す。
