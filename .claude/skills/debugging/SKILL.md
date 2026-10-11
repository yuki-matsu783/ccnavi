---
name: debugging
concept: true
description: >-
  デバッグの標準的な考え方への入口。不具合の原因調査、再現手順の作成、失敗したコマンドやログの切り分けで使う。細かい手順は references にあり、ccnavi --docs で共通とプロジェクトの両方から引く。
---

# debugging

デバッグの作業に入る前に、`ccnavi --docs --type skill-reference --tag debugging` で共通とプロジェクトの reference を引き、当たったものを読む。

## 基本の考え方

- 直す前に再現させる。再現できないまま直さない
- 仮説は1つずつ立て、1つずつ確かめる。確かめずに複数を同時に直さない
- 原因（なぜ起きたか）まで特定してから直す。症状だけを隠さない
- 直したあと、再現した手順で直ったことを確かめる

## reference の置き場

- 共通: `.claude/skills/debugging/references/`
- プロジェクト固有: `projects/<名前>/skills/debugging/references/`

中のディレクトリ構造は任意。足し方と書き方は `docs/claude/skill-concepts.md`。
