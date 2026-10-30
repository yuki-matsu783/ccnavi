---
title: process-review に既存の失敗を確かめる reference を足す
type: learning-wiki
description: process-review に既存の失敗と書く前に統合先で再現を確かめる決まりを足す提案
tags: [skills, feedback]
keywords: [process-review, 既存の失敗, 再現, 統合先, ref]
proposal: chore-211-skill-reviewing-preexisting-failure
target: skill
files:
  - .claude/skills/process-review/references/common/preexisting-failures.md
  - .claude/skills/process-review/references/common/purpose.md
patterns: {preexisting-failure-misattributed: 1}
status: 承認待ち
---

# process-review に既存の失敗を確かめる reference を足す

- 対象: `.claude/skills/process-review/references/common/preexisting-failures.md`・`purpose.md`（どちらも新規）
- 要点: 「既存の失敗」と書く前に統合先で再現を確かめ、どの ref で再現したかを添える
- 動機のパターン: [preexisting-failure-misattributed](../patterns/preexisting-failure-misattributed.md)
- 採否: 承認待ち（採否はユーザが決める）

差分（新規ファイルの本文）:

```diff
+- 「既存の失敗」と書く前に、変更前の状態（統合先）で同じテストが落ちるかを確かめる。親のブランチに他の子の変更が入った後で見た失敗は、他の子が原因のことがあるため
+- 報告やレビューで「既存」と扱うときは、どの ref で再現したかを添える
```
