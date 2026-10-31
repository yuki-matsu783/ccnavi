---
title: commit の git の入口と既知の失敗の回し方
type: learning-wiki
description: commit スキルの git の入口を絶対パスにし、既知の失敗がある間のテストの回し方を足す提案
tags: [skills, feedback]
keywords: [commit, ccnavi-git.sh, 絶対パス, 既知の失敗, bench, 全件, finish]
proposal: chore-208-skill-commit-git-entry-tests
target: skill
files:
  - .claude/skills/commit/SKILL.md
  - .claude/skills/commit/references/test-groups.md
  - .claude/skills/commit/references/purpose.md
patterns: {ccnavi-git-relative-path: 1, test-run-known-failure: 1}
status: 承認待ち
---

# commit の git の入口を絶対パスにし、既知の失敗がある間の回し方を足す

- 対象: `.claude/skills/commit/SKILL.md`・`.claude/skills/commit/references/test-groups.md`・`.claude/skills/commit/references/purpose.md`（新規）
- 要点: ワークツリーや `projects/<名前>` の中では ccnavi-git.sh を絶対パスで呼ぶ。既知の失敗がある間はグループごとに回し、bench は名指しでバックグラウンド、全件は最後の子を finish する前
- 動機のパターン: [ccnavi-git-relative-path](../patterns/ccnavi-git-relative-path.md)、[test-run-known-failure](../patterns/test-run-known-failure.md)
- 採否: 承認待ち（採否はユーザが決める）

差分:

```diff
 git は直接呼ばず、`ccnavi-git.sh` 経由で打つ（生の git は止められるため）。以下の例はワークスペースルートの形で書く。
-ワークツリーの中では先頭を `sh ../../../.ccnavi/scripts/ccnavi-git.sh` にする。cwd ごとの呼び方は `.ccnavi/docs/worktree.md` の「基本」を見る。
+ワークツリーや `projects/<名前>` の中では、先頭を `sh <ワークスペースルートの絶対パス>/.ccnavi/scripts/ccnavi-git.sh` と直書きする。
+相対パス（`../../../`）は cwd がワークツリーの最上段のときしか届かないため。コマンド名やパスを変数に入れない（`DENY_COMMAND_NAME_EXPANSION` で止まる）。
```

```diff
 落ちたら新しいプロセスを起こすのをやめ、落ちた 1 本の出力だけを出す。ターンの終わりの hook はこれを使わない。
+既知の失敗が残っている間は `python -m tests` で全件を見られないので、グループごとに `discover -s tests/<グループ> -t .` で回し、失敗の数を既知と比べる。
+
+`tests/<グループ>/bench_*.py` はテストではなく、`uv run python -m tests.core.bench_deadline` のように名指しで回す。
+実ルールを使う bench は `*x*` 形の glob の照合が入力の長さの 2 乗で伸び、小さい入力でも数分かかる。ターンの中で回すならバックグラウンドで回す。
```

```diff
-- 統合先に取り込む前、MR に出す前
+- 統合先に取り込む前、MR に出す前（親の最後のフェーズの子を finish する前に済ませる。レビュー準備中はシェルの実行が止まるため）
```
