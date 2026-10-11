---
name: testing
concept: true
description: >-
  テストの標準的な考え方への入口。テストを書く・回す・直す作業、失敗したテストの切り分けで使う。細かい手順は references にあり、ccnavi --docs で共通とプロジェクトの両方から引く。
---

# testing

テストの作業に入る前に、`ccnavi --docs --type skill-reference --tag testing` で共通とプロジェクトの reference を引き、当たったものを読む。

## 基本の考え方

- 変更に近いテストから回し、通ってから範囲を広げる
- テストを足すときは、先に失敗することを確かめる
- 1つのテストで見る観点は1つにする
- テストを消す・無効にする・条件を緩めて通さない。落ちた理由を直す

## reference の置き場

- 共通: `.claude/skills/testing/references/`
- プロジェクト固有: `projects/<名前>/docs/skills/testing/references/`

中のディレクトリ構造は任意。足し方と書き方は `docs/claude/skill-concepts.md`。
