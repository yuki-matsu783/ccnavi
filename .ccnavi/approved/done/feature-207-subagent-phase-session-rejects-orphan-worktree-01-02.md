---
version: 1
ticket: feature-207-subagent-phase-session-rejects-orphan-worktree-01-02
parent: feature-207-subagent-phase-session-rejects-orphan-worktree
phase: 1
title: "#210 元のプロジェクトが無いワークツリーの名指しの受入テスト"
rationale: |
  元のプロジェクトが無いワークツリー(プロジェクトを消したあとも残った、元のプロジェクトを失ったワークツリー)を --lint と --explain が名指しすること、判定は変わらないことをテストで固定する。
human_review:
  required: true
  reason: 診断の文面が仕様になるため
allow:
  - match: Write|Edit
    glob: "tests/core/*"
started_at: "2026-10-12T03:12:31+0900"
completed_at: "2026-10-12T03:46:48+0900"
base_sha: "8548fc14bfe6a07a792443d35f8c15550610d23f"
---
## テストの方針

`tests/core/test_worktree_without_project.py`(新規)。実際に git でプロジェクトを作り、そこからワークツリーを切ってから、プロジェクトのディレクトリを消す。

- 対象の定義: `.claude/worktrees/<名前>/.git` が `gitdir:` の行を持つファイルで、`tree.owner_of` が元のプロジェクトを見つけられないもの。
  gitdir が指す先が無い場合と、先は在るが相互参照が成り立たない場合の両方
- `--lint`: warn 1 件。名前・元の gitdir・「元のプロジェクトが無く、そのプロジェクトの deny が効いていない(中のパスはワークスペースルートとして判定される)」・「ワークツリーを消すか、プロジェクトを戻す」を含む。「孤児」という語は使わない
- `--explain`: 見出し「元のプロジェクトが無いワークツリー」の 1 節で同じ内容。該当が無いときは節を出さない
- ふつうのディレクトリ(`.git` が無い)と、正常なワークツリーは名指ししない
- 判定が変わらない回帰: 該当ワークツリーの中のパスは従来どおりワークスペースルートの扱い(`tree.tree_of` の結果が変わらない)
- git を起動しない(列挙はファイルシステムだけ。`tree.worktrees` の約束)
