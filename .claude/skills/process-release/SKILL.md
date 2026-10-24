---
name: process-release
process: true
description: >-
  リリースの標準的な考え方への入口。統合先への取り込み、版の切り出し、配布物の作成、公開前の確認で使う。細かい手順は references にあり、ccnavi --docs で共通とプロジェクトの両方から引く。
---

# process-release

リリースの作業に入る前に、`ccnavi --docs --type skill-reference --tag process-release` で共通とプロジェクトの reference を引き、ヒットしたものを読む。

## 基本の考え方

- 出す前に、何が変わったか（差分と影響範囲）を一覧にする
- 取り込み前に、検査（lint・テスト）を全件回す
- 戻し方を先に決める。戻せない操作は、ユーザの承認を取ってから行う
- 出したあと、実際に動くことを確かめる

## reference の置き場

- 共通: `.claude/skills/process-release/references/common/`（プロジェクトの `skills/process-release/references/common/` へコピーされる。プロジェクトの中では直さない）
- そのリポジトリ固有: `references/project/`（ワークスペースは `.claude/skills/process-release/references/project/`、プロジェクトは `projects/<名前>/skills/process-release/references/project/`。コピーされない）

中のディレクトリ構造は任意。足し方と書き方は `.ccnavi/docs/skill-processes.md`。
