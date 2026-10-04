---
title: ccnavi 承認ボードの取り下げ
type: requirements
description: 着手前の新規の承認を取り下げるときのふるまい
tags: [design-doc, extension, approval]
keywords: [要件, 取り下げ, 承認コミット, todo, doing, workflow.yml]
---

# 取り下げ

入口: [ccnavi 承認ボードの要件](../requirements.md)

- 取り下げ: 着手前の新規の承認だけ。承認コミット（`doing/<識別子>.md` を足した、親が 1 つの最新のコミットで、親のブランチの first-parent の鎖の上にあるもの）の親の提案を
  そのまま `todo/` へ戻す 1 コミット。出すかは Python が決める。承認はチケットの中身を変えないので、`doing/` の中身が承認コミットの親の
  提案とバイト単位で同じなら、改版も着手もされていない。GitHub の画面で動かした承認（rename のコミット）も同じように取り下げられる。
  親は、待ち方のファイル（`phases/<親>/workflow.yml`）があれば、その中身が承認したときの待ち方と同じであることも求め、一緒に消す
