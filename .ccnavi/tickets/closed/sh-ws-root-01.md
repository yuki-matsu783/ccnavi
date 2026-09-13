---
version: 1
ticket: sh-ws-root-01
parent: sh-ws-root
phase: 1
human_review:
  required: true
  reason: ワークスペースルートの導出をどう決めるかが、以後の全フェーズの前提になるため
title: 設計 — ワークスペースルートの導出と、sh 4 本の変更の割り付け
rationale: '親チケットの決定を、実装が読める形の設計として書く。

  焦点は 1 つ。3 本の sh が自分の根を git に聞いているのをやめ、ファイルシステムを

  上へ歩いて探す形にする。その契約（印・失敗の扱い・プロジェクト名の導出）を決め、

  4 本のスクリプトと rules.yml の変更を割り付け、受入テストで確かめることを列挙する。

  '
allow:
- match: Write|Edit
  glob: wip/design/*
ccnavi_approved:
  approved_at: 2026-09-12T22:29:48+0900
  source_tree: sh-ws-root
  source_path: C:\Users\taniyama\Desktop\git\ccnavi\.claude\worktrees\sh-ws-root\wip\tickets\todo\sh-ws-root-01.md
started_at: 2026-09-12T22:31:15+0900
base_sha: 46d4791c62df1e3006f4b57935204a9553ee08ef
completed_at: 2026-09-12T22:33:27+0900
---

# 設計 — ワークスペースルートの導出と、sh 4 本の変更の割り付け

## この子で決めること

1. ワークスペースルートの探し方（印を何にするか、`CCNAVI_WORKSPACE` の扱い、失敗の綴り）
2. 共通部分 `ccnavi-common.sh` の契約（何を提供し、何を提供しないか）
3. 作業ツリーの切り元からプロジェクト名を導く手順（実測した `gitdir:` の綴りに基づく）
4. `ccnavi-git.sh` / `ccnavi-ticket.sh` / `ccnavi-review.sh` / `test-py.sh` / `rules.yml` の
   変更の割り付け
5. 保護済みファイルを人が写す手順の形（何を `wip/design/scripts/` に置くか）
6. 受入テストで確かめること（モード A の非退行を含む）

## 成果物

`wip/design/sh-ws-root.md` 1 枚。`phases.yml` の `design` の `deliverables`（`wip/design/*.md`）
を満たす。

## この子で決めないこと

- 実装そのもの（次のフェーズ）
- `ccnavi.md` と `requirements.md` への反映（親チケットの判断で、今回は行わない）
- Python 側の修正（別チケット）

## レビューで判断がほしい点

設計書の末尾「10. レビューで見てほしいところ」に 5 点挙げる。特に、作業ツリーの中に
`.claude/scripts/` が checkout されている場合にどちらを根とするか、`worktree add` の引数解析の
規則、`ccnavi-setup.sh` が親チケットの宣言した範囲の外にある件の 3 つは、判断を仰いでから
実装に入りたい。
