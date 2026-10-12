---
name: process-research
process: true
description: >-
  調査の標準的な考え方への入口。既存コードの読み解き、技術や案の比較、変更の影響範囲の洗い出しなど、不具合の原因調査以外の調べもので使う。不具合の原因調査は process-debug。細かい手順は references にあり、ccnavi --docs で共通とプロジェクトの両方から引く。
---

# process-research

調査の作業に入る前に、`ccnavi --docs --type skill-reference --tag process-research` で共通とプロジェクトの reference を引き、当たったものを読む。

## 基本の考え方

- 調べる前に、何を知りたいか（問い）と、どこまで分かれば足りるか（打ち切る基準）を書く
- 事実（読んだ・動かして確かめたこと）と推測を分けて書く。確かめていないことを事実として書かない
- 出どころ（ファイルと行、コマンドと出力、URL）を残す。後で同じ答えを再現できるようにする
- 結論には、確度と、まだ分かっていないことを添える

## reference の置き場

- 共通: `.claude/skills/process-research/references/common/`（プロジェクトの `skills/process-research/references/common/` へ写される。プロジェクトの中では直さない）
- そのリポジトリ固有: `references/project/`（ワークスペースは `.claude/skills/process-research/references/project/`、プロジェクトは `projects/<名前>/skills/process-research/references/project/`。写されない）

中のディレクトリ構造は任意。足し方と書き方は `.ccnavi/docs/skill-processes.md`。
