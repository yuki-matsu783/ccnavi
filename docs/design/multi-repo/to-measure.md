---
type: design
title: 11.10 実装時に実測するもの
description: 複数リポジトリの実装時に実測して確かめるもの
tags: [design-doc, projects]
keywords: [実測, 性能, Windows, gitdir]
---

[設計書の入口](../../design.md) > [11. 複数のリポジトリ](../multi-repo.md)

### 11.10 実装時に実測するもの

- Windows でプロジェクトから切ったワークツリーの `.git` ファイルの `gitdir:` の表記と区切り

#### 実測で確かめた前提

- プロジェクトの数に対する PreToolUse の `ms`: 5 本で 44〜55 ms（最大 76 ms）、20 本で 133 ms。1 本あたり約 +5 ms の線形で、期限 3 秒の半分（1500 ms）に届くのは約 270 本。
  `ms` は実行ファイルの起動を含まない。rules.yml は共通レイヤーのコピー（202 行）で代用した。
- `projects/` をワークスペースの `.gitignore` に入れても、プロジェクトの CLAUDE.md は読まれる。ルート起動では `projects/` 配下のファイルを読んだ時点で追加され、
  プロジェクト内で起動すると起動時に読まれる。ワークスペースの CLAUDE.md と矛盾しないように書く
- ルートで起動して cd した場合、`${CLAUDE_PROJECT_DIR}` はルートのまま（stdin の `cwd` だけが変わる）。プロジェクト内で起動すると `CLAUDE_PROJECT_DIR` がプロジェクトになり、
  ワークスペースの `.claude/settings.json` は読まれず、ワークスペースの ccnavi の hook は動かない（プロジェクトの `.claude/` の hook とスキルが使われる）。プロジェクトの作業はルートで起動して cd で入る前提
