---
title: process-design に診断の走査と内部の呼び名の reference を足す
type: learning-wiki
description: process-design に診断の走査と、説明で内部の呼び名を使わない決まりを足す提案
tags: [skills, feedback]
keywords: [process-design, 診断, 走査, 呼び名, 用語, test_raw_share, test_lint_ops]
proposal: chore-210-skill-designing-scan-and-terms
target: skill
files:
  - .claude/skills/process-design/references/project/diagnostic-scan.md
  - .claude/skills/process-design/references/common/plain-terms.md
  - .claude/skills/process-design/references/project/purpose.md
  - .claude/skills/process-design/references/common/purpose.md
patterns: {diagnostic-scan-read-once: 1, internal-names-in-prose: 1}
status: 承認待ち
---

# process-design に診断の走査と、内部の呼び名の reference を足す

- 対象: `.claude/skills/process-design/references/project/diagnostic-scan.md`・`.claude/skills/process-design/references/common/plain-terms.md`・各 `purpose.md`（すべて新規）
- 要点: 診断の走査は 1 度読んだコピーを渡し、警告を足したら合流後に `tests/ticket` を回す。説明では内部の呼び名を使わず、状態を言葉で書く
- 動機のパターン: [diagnostic-scan-read-once](../patterns/diagnostic-scan-read-once.md)、[internal-names-in-prose](../patterns/internal-names-in-prose.md)
- 採否: 承認待ち（採否はユーザが決める）

差分（新規ファイルの本文。`project/diagnostic-scan.md`）:

```diff
+- `--explain` と `--lint` に走査を足すときは、承認済みチケットの置き場を読み直さず、1 度読んだコピー（copies）を走査に引数で渡す。置き場は 1 回の起動で 1 度だけ読む約束で、`tests/ticket/test_raw_share.py` が読んだ回数を数えて固定しているため
+- 診断に新しい警告を足したら、並行した子を合流した後に `tests/ticket` を全件回す。`test_lint_ops` には名前で警告の行を拾うテストがあり、別の子の足した警告を取り違えて、合流後にだけ落ちるため
```

差分（新規ファイルの本文。`common/plain-terms.md`）:

```diff
+- issue のタイトルや作業中の呼び名（比喩・略語）を、提案・設計書・ユーザへの説明にそのまま使わない。書いた人と issue を読んだ人にしか通じないため
+- 何が起きている状態かを言葉で書く（例: 「孤児のワークツリー」ではなく「元のプロジェクトが無いワークツリー」）
+- 短い呼び名を使い続けたいときは、初出で定義を添える。用語集に入れるかは別に判断する
```
