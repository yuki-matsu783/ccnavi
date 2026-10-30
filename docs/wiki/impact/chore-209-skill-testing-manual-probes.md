---
title: process-test に手動の probe の reference を足す
type: learning-wiki
description: process-test に手動の probe を回す前の確認を足す提案
tags: [skills, feedback]
keywords: [process-test, probe, manual, 偽陽性, 前提]
proposal: chore-209-skill-testing-manual-probes
target: skill
files:
  - .claude/skills/process-test/references/project/manual-probes.md
  - .claude/skills/process-test/references/project/purpose.md
patterns: {stale-manual-probe: 1}
status: 承認待ち
---

# process-test に手動の probe の reference を足す

- 対象: `.claude/skills/process-test/references/project/manual-probes.md`・`purpose.md`（どちらも新規）
- 要点: probe を回す前に前提を確かめ、止まった理由まで見る。前提を直したら docstring も直す
- 動機のパターン: [stale-manual-probe](../patterns/stale-manual-probe.md)
- 採否: 承認待ち（採否はユーザが決める）

差分（新規ファイルの本文）:

```diff
+- 回す前に、probe の手順が今の ccnavi の前提に合っているかを読む（一時リポジトリへの `.ccnavi/scripts/` の配置、親を先に start、`review request` の `--eli5`、`decide` の入力など）。`tests/manual/` は自動テストが呼ばないので、仕様が変わっても落ちずに古くなるため
+- 「止まる」ことを確かめる段は、止まったことだけでなく、止まった理由（拒否の文面やコード）が期待どおりかまで見る。前提の崩れで別の理由で止まっても PASS に見えるため
+- 前提を直したら、probe の docstring の「用意するもの」も同じ変更で直す
```
