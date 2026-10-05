---
type: design
title: 11.10 実装時に実測するもの
description: 複数リポジトリの実装時に実測して確かめるもの
tags: [design-doc, projects]
keywords: [実測, 性能, Windows, gitdir]
---

[設計書の入口](../../design.md) > [11. 複数のリポジトリ](../multi-repo.md)

### 11.10 実装時に実測するもの

- プロジェクトの数に対する PreToolUse の `ms`。プロジェクト 5 本で期限の半分を超えるなら、ルールの読み込みに mtime の記録を足す
- Windows でプロジェクトから切ったワークツリーの `.git` ファイルの `gitdir:` の表記と区切り
- `projects/` をワークスペースの `.gitignore` に入れたとき、Claude Code がプロジェクトの中の CLAUDE.md を読むか。読まれてよいが、読まれるなら
  ワークスペースの CLAUDE.md と矛盾しないように書く
- `cwd` がプロジェクトの中にあるとき、hook の `${CLAUDE_PROJECT_DIR}` がワークスペースルートのままか
