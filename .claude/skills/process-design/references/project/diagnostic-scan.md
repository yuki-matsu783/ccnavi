---
type: skill-reference
title: 診断に走査を足す
description: --explain と --lint に走査を足すとき、承認済みチケットの置き場を読み直さず、新しい警告が既存の lint のテストと取り違えられないようにする
tags: [process-design, process-test]
keywords: [explain, lint, 診断, 走査, 置き場, copies, test_raw_share, test_lint_ops, 警告]
---

# 診断に走査を足す

- `--explain` と `--lint` に走査を足すときは、承認済みチケットの置き場を読み直さず、1 度読んだコピー（`copies`）を引数で走査に渡す（例: `session_rejects.find_all(..., copies=copies)`）。置き場は 1 回の起動で 1 度だけ読む約束で、`tests/ticket/test_raw_share.py` が読んだ回数を数えて固定しているため。読み直す作りにすると、親とワークツリーが増えた置き場で読む回数が積で増え、VS Code の拡張の期限を超える
- 診断に新しい警告を足したら、並行した子を合流した後に `tests/ticket` を全件回す。`tests/ticket/test_lint_ops.py` には、警告の行を文言の一部（名前）で拾うテストがあり、別の子の足した警告を取り違えて、合流後にだけ落ちることがあるため。足す警告の文言は、既存のテストが拾う語と重ならないようにする
