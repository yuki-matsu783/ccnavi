---
title: スキル振り返りの経過
type: skill-wiki
description: 振り返りごとに拾ったものと、足した・直したパターンの記録
tags: [skills, feedback]
keywords: [振り返り, 経過, ログ, パターン, wiki]
---

# 振り返りの経過

## 2026-10-11

- 拾ったもの: 改名で拡張の fixtures を読む Python のテスト 2 本が落ちた件。サブエージェントの報告と親の作業で同じ候補が出た
- 足したパターン: [external-name-fixtures](patterns/external-name-fixtures.md)（1 件）
- 提案: `commit/references/test-groups.md` への追記（`skill-impact.md`、承認待ち）

## 2026-10-12

- 拾ったもの: feature-207 の作業で、サブエージェントの報告とメインの失敗から 8 件。うち 6 件を候補にし、2 件（委任の手順・提案の手順）は docs/claude/ と ccnavi の案内の領分として除いた
- 足したパターン: [ccnavi-git-relative-path](patterns/ccnavi-git-relative-path.md)、[test-run-known-failure](patterns/test-run-known-failure.md)、[stale-manual-probe](patterns/stale-manual-probe.md)、[diagnostic-scan-read-once](patterns/diagnostic-scan-read-once.md)、[internal-names-in-prose](patterns/internal-names-in-prose.md)、[preexisting-failure-misattributed](patterns/preexisting-failure-misattributed.md)（6 件）
- 提案: スキルの変更は親の範囲に置き場が無いので、提案チケット 4 本（`chore-208-skill-commit-git-entry-tests`・`chore-209-skill-testing-manual-probes`・`chore-210-skill-designing-scan-and-terms`・`chore-211-skill-reviewing-preexisting-failure`。`skill-impact.md`、承認待ち）。同じ誤りを持つ `.ccnavi/docs/worktree.md` は `chore-212-worktree-doc-absolute-path`
