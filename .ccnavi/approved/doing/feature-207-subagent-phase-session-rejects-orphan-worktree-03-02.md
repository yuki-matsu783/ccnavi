---
version: 1
ticket: feature-207-subagent-phase-session-rejects-orphan-worktree-03-02
parent: feature-207-subagent-phase-session-rejects-orphan-worktree
phase: 3
title: "#210 用語集に「元のプロジェクトが無いワークツリー」を足す"
rationale: |
  フェーズ 3 のレビューで決めた。診断が名指しする対象なのに、用語集（docs/CONTEXT.md）に項目が無く、初めて見る人が意味を引けない。定義を 1 項目足す。
human_review:
  required: true
  reason: 用語集の定義を足すため（プログラムは変えない）
allow:
  - match: Write|Edit
    glob: "docs/CONTEXT.md"
started_at: "2026-10-12T05:58:23+0900"
base_sha: "8b735d179b2728f18fbe249825438e0cc1a9ff3a"
---
## やること

`docs/CONTEXT.md` の、ワークツリーまわりの用語の並びに、次の項目を 1 つ足す。

- 用語：元のプロジェクトが無いワークツリー
- 定義：プロジェクト（`projects/<名前>/` の別リポジトリ）を消したあとも残った、元のプロジェクトを失ったワークツリー。元リポジトリをたどれないので、ワークツリー内のパスがワークスペースルートとして判定され、そのプロジェクトの `deny` が効かない。
- 扱い：判定は変えない。`--lint` が warn、`--explain` が節「■ 元のプロジェクトが無いワークツリー」で名指しする（`--explain --json` には欄を足さない）。対処は、ワークツリーを消すか、プロジェクトを戻すこと。
- 参照：`docs/design/multi-repo/trees.md` の 11.3。

用語集の既存の書式（見出し・並び順・リンクの形）に合わせる。「孤児」という語は使わない。

## 確かめること

- `docs/design/multi-repo/trees.md` と `docs/design/diagnostics.md` の記述と、定義が食い違わないこと。
- 用語集の中の既存の用語への参照が壊れていないこと。

## 影響範囲

文書 1 ファイルのみ。プログラムとテストは変えない。
