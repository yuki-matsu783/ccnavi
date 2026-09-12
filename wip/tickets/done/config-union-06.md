---
version: 1
ticket: config-union-06
parent: config-union
phase: 2
predecessors: [config-union-02]
title: 受入テストの穴を埋める（敵対的レビューの指摘）
rationale: |
  config-union-02 の受入テストに、敵対的レビューが 5 観点で見つけた穴を埋める。設計が
  約束しているのにテストが無いもの 6 件（実行後の監視の和、共通層の写しの保護、`{root}` の
  置換先、judge の `source`、壊れた risk 層、2 つ以上の非空プロジェクト層と `ask` の層）と、
  実装を締めていない主張 3 件（層の順序、`detail` の部分一致、失敗時に stderr が出ない）を直す。
  あわせて fixture の `.gitignore` を実物に合わせ、setUp の重さを減らす。
human_review:
  required: true
  reason: 実装が満たすべき振る舞いを足すため
allow:
  - match: Write|Edit
    glob: "tests/*"
started_at: "2026-09-12T21:51:55+0900"
completed_at: "2026-09-12T22:46:24+0900"
base_sha: "58dc9b29d292440e5d0c1535b1a20fec4b48ce47"
---

# 受入テストの穴を埋める

## 足すテスト（設計の約束のうちテストが無いもの）

1. **実行後の監視の和（§25.7）**。プロジェクトのツリーで、共通層にしか無い deny の対象を
   シェルで汚し、`PostToolUse` が共通層のルールで検知することを確かめる。自身の層にしか無い
   deny についても、ワークスペースの作業ツリーで同じことを確かめる。今は「行き先の層 1 本」の
   ままの実装でも全件緑になる
2. **共通層の写しの保護（§25.6）**。fixture の `.gitignore` が `/.claude/` を丸ごと無視して
   いるので、設計が名指しした既存の穴（作業ツリーの中の `.claude/ccnavi/phases.yml` が書けて
   復元されない）を一度も再現していない。実物に合わせて `/.claude/worktrees/`、
   `/.claude/ccnavi/state/`、`/.claude/ccnavi/tickets/` だけを無視し、共通層の 3 本は追跡する。
   そのうえで、ワークスペースから切った作業ツリーの中の共通層の写しを壊し、戻ることを確かめる
3. **`{root}` の置換先（§25.8）**。プロジェクトの層のルールに `{root}/...` を書き、置換先が
   そのプロジェクトではなくワークスペースルートであることを確かめる。共通層の定義を `{root}`
   ごと写したプロジェクトの層の定義が、置換後の全欄一致で重複として捨てられることも確かめる
4. **judge の `source`（§25.9）**。`judge:` の項目を層に置いて子を閉じ、`<子>.judge.json` の
   各項目に `source` が入ることを確かめる。種類を根拠に置く印にも `source` が入ること
5. **壊れた risk 層**。rules と phases には「壊れた層」のテストがあるが risk に無い。壊れた
   YAML を置き、`--lint` error と `fallback` に層の名前、共通層の factors だけで点が付くこと
6. **2 つ以上の非空プロジェクト層**。今は `app` の層が常に空で、Bash の和に 2 つ目の層が
   参加する場面が無い。`lib` と `app` の両方に違う deny を置き、両方が効くことと、並びが
   名前順であることを確かめる
7. **`ask` の層**。3 ファイルに `ask` のルールが 1 件も無い。共通層に `ask`、プロジェクトの層に
   同じ対象への `allow` を置き、判定が `ask` のまま緩まないことを確かめる（設計が明示している代償）
8. **共通層の `script:`**。合成を通した経路で共通層の `script:` が走ること、指す先が無ければ
   `--lint` error になることを、共通層側でも確かめる
9. **NotebookEdit** を 1 件でも実際の `tool_name` として通す

## 締め直すテスト（実装を締めていない主張）

- `test_project_allow_stays_inside_the_project`（`tests/test_config_union.py:449`）の
  `assertIn(record["rules"][0], ("ws-src", "lib:source"))` を `assertEqual(..., "ws-src")` に。
  コメントは「共通層が先に当たる」と言っているのに、逆順でも通る
- `--lint` の Problem を `detail` の部分一致で見ているもの（`self` / `lib` のような
  ありふれた語）は、`where` を先に絞ってから見る形に揃える
- `tests/test_config_union_guard.py` の `RestoreTest` の assert に stderr を添える。今は
  失敗しても理由が出ず、実装フェーズで 1 件ずつ読むときに困る

## 直す報告

- 02 の報告の「緑 6 件は今どおりの挙動を確かめている」は正しくない。4 件は**機能がまだ無いから**
  緑になっている（プロジェクトの層が旧置き場を読んでいて丸ごと組み込みに落ちる、
  `builtin-guard-project-home` がまだ無いので `assertNotIn` が常に真）。正味の現状維持の確認は
  `test_broken_common_layer_falls_back_to_builtin_as_before` と
  `test_workspace_without_projects_or_own_layer_is_unchanged` の 2 件。`CONFIG_UNION_TODO.md` に
  この訂正を書く

## 速さ

- `setUp` が全 57 件でワークスペースとプロジェクト 2 つ（git 3 本）を組み直しており、
  1 回のフルランで git の起動が 530 回前後になる見積り。読み取りだけのクラスは
  `setUpClass` に寄せるか、そのテストが使う層だけを書く形に削る。目標は git の起動を半分以下に

## やらないこと

- `ccnavi/` のコードには触らない（実装は 03 / 04 / 05）
- `CONFIG_UNION_TODO.md` に挙がっている既存テストの修正（実装フェーズの仕事）
