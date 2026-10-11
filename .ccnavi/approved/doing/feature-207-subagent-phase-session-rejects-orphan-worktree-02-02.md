---
version: 1
ticket: feature-207-subagent-phase-session-rejects-orphan-worktree-02-02
parent: feature-207-subagent-phase-session-rejects-orphan-worktree
phase: 2
title: "#210 元のプロジェクトが無いワークツリーを --lint と --explain が名指しする"
rationale: |
  プロジェクトを消したあとも残った、元のプロジェクトを失ったワークツリーは、列挙から外れて中のパスがワークスペースルートの扱いになり、プロジェクトの deny が外れる。
  判定は変えず、診断で名指しする。
human_review:
  required: true
  reason: --lint と --explain の出力が変わるため
allow:
  - match: Write|Edit
    glob: "src/ccnavi/infra/tree.py"
  - match: Write|Edit
    glob: "src/ccnavi/entry/lint*.py"
  - match: Write|Edit
    glob: "src/ccnavi/entry/diagnose_explain.py"
  - match: Write|Edit
    glob: "tests/core/*"
started_at: ""
completed_at: ""
base_sha: ""
---
## 調べて分かったこと

`tree.worktrees()` は `.claude/worktrees/` の下を調べ、`owner_of` が元のプロジェクト(ワークスペースか `projects/` のプロジェクト)を見つけた候補だけを数える。
プロジェクトが消えると相互参照が成り立たず候補が落ち、`tree_of` の最長一致で中のパスはワークスペースルートに落ちる(プロジェクトの deny が掛からない)。
列挙は実行前チェックの中で呼ばれるので、git を起こさない約束がある。

## 実装方針

- `infra/tree.py` に `worktrees_without_project(root, projects_dir) -> list[ProjectlessWorktree]` を足す。`.claude/worktrees/` の下のディレクトリで、`.git` がファイルで先頭が `gitdir:` なのに `owner_of` が None のもの。
  `ProjectlessWorktree` は名前・パス・`gitdir:` の指す先(読めた分)・指す先が存在するか。既存の `worktrees()` / `tree_of` は変えない(判定は変えない)。
- `.git` がファイルでないただのディレクトリは数えない(ワークツリーだったとは言えない)。
- `--lint`: `lint_places`(または `lint_layers`)に 1 関数足し、`check` で呼ぶ。warn。文面の案:
  「ワークツリー <名前> は元のプロジェクトが無い(<gitdir> の先が見つからない)。このパスはワークスペースルートとして判定され、そのプロジェクトの deny が効いていない。ワークツリーを消すか、プロジェクトを戻してください」。
  消し方に git のコマンドを書く場合は `ccnavi-git.sh` 経由の表記にする(生の git は組み込みで止まるため)。
- `--explain`: 見出し「■ 元のプロジェクトが無いワークツリー」の節を足し、該当がある時だけ 1 件 1 行で同じ内容を出す。`--explain --json` には欄を足さない(決定済み)。
- 実行前チェックの経路には足さない(呼び出しのたびに走査すると期限を圧迫する)。診断の経路だけ。

## 影響範囲

`--lint` の warn が増える(既に該当するワークツリーがあれば初回に出る)。終了コードの規則は warn では変わらないことを確かめる。判定・列挙は変わらない。
