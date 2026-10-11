---
version: 1
ticket: feature-207-subagent-phase-session-rejects-orphan-worktree-02-05
parent: feature-207-subagent-phase-session-rejects-orphan-worktree
phase: 2
title: "#210 既存の lint のテストを、新しい名指しに合わせて直す"
rationale: |
  合流後の tests/ticket で test_lint_ops.test_a_worktree_registered_in_another_repository_is_not_warned が落ちた。#210 の実装（02-02）が原因で、元から落ちていたものではない（子の報告は「既存の失敗」としていたが、親ブランチに 02-02 が入った後の確認だった）。
human_review:
  required: true
  reason: 既存のテストの期待を変えるため
allow:
  - match: Write|Edit
    glob: "tests/ticket/test_lint_ops.py"
---
## 何が起きているか

テストは、ワークツリーの `.git` が `gitdir: /somewhere/projects/p/.git/worktrees/elsewhere` を指す形（別のリポジトリに登録されたワークツリー）を作り、`lint_lines("elsewhere")` が空であることを見る。意図は、「git に登録が無い」という警告（4. 登録の無いディレクトリ）が出ないこと。

`lint_lines("elsewhere")` は、名前を含む行を全部拾う。#210 の実装で、同じ形が「元のプロジェクトが無い」という別の警告の対象になった（先が見つからない形は、受入テスト 01-02 が名指しを要求している）。その警告の文面にも `elsewhere` が入るので、テストが誤って拾って落ちる。

## 直し方

プログラムは変えない。名指しの対象は、受入テスト（`test_worktree_without_project.py` の「先が無い形」）で定めたとおり。テストの確認を、意図に合わせて狭める。

- `lint_lines("elsewhere")` が空であることを、「git に登録が無い」を含む行が出ないことに変える（`.claude/worktrees/elsewhere は git に登録が無い` の行が無い）。
- あわせて、新しい警告が出ることを同じテストで確かめてよい（「元のプロジェクトが無い」を含む行が 1 件）。ただし、その主張は 01-02 の受入テストが持っているので、重ねすぎない。

## 確かめること

- `tests/ticket/test_lint_ops.py` が通ること。
- 他の `tests/ticket` のテストの結果が変わらないこと（変えるのは 1 ファイルのみ）。

## 影響範囲

テスト 1 ファイルのみ。プログラムと文書は変えない。
