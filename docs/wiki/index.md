---
title: スキル wiki の索引
type: learning-wiki
description: スキルの振り返りで溜めたパターンの一覧。1 パターン 1 行
tags: [skills, feedback]
keywords: [wiki, パターン, 索引, fixtures, 改名, JSON, キー, オプション, ccnavi-git.sh, 相対パス, probe, 診断, 用語, 既存の失敗]
---

# パターンの一覧

- [external-name-fixtures](patterns/external-name-fixtures.md): 外に見える名前（`--json` のキー・CLI のオプション）を改めると、拡張の fixtures を読む別言語側のテストが落ちる。原因は fixtures が旧名のままのため。回すグループを `core` と `guard` にし、fixtures を持つフェーズで直す
- [ccnavi-git-relative-path](patterns/ccnavi-git-relative-path.md): ワークツリーから相対パスで ccnavi-git.sh を呼ぶと届かない。原因は cwd が最上段とは限らず、スキルの記述が拒否の案内と食い違うため。絶対パスを直書きし、コマンド名を変数に入れない
- [test-run-known-failure](patterns/test-run-known-failure.md): 既知の失敗があると `python -m tests` が途中で止まり、bench は数分かかり、レビュー準備中は全件を回せない。グループごとに discover し、全件は finish の前に回す
- [stale-manual-probe](patterns/stale-manual-probe.md): 手動の probe は仕様変更で静かに古くなり、偽陽性で PASS する。回す前に前提を確かめ、止まった理由まで見る
- [diagnostic-scan-read-once](patterns/diagnostic-scan-read-once.md): 診断に走査を足すと置き場の読み直しで test_raw_share が、新しい警告で test_lint_ops が落ちる。copies を渡し、合流後に tests/ticket を回す
- [internal-names-in-prose](patterns/internal-names-in-prose.md): issue のタイトルや内部の呼び名をそのまま書くと伝わらない。状態を言葉で書き、初出で定義を添える
- [preexisting-failure-misattributed](patterns/preexisting-failure-misattributed.md): 合流後に見た「既存の失敗」が他の子の変更が原因だった。統合先で再現を確かめてから既存と書く
