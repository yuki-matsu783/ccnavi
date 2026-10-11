---
name: designing
description: >-
  設計の標準的な考え方への入口。設計・方式の検討、変更の影響範囲の見積もり、設計書や設計判断の記録を書く作業で使う。細かい手順は references にあり、ccnavi --docs で共通とプロジェクトの両方から引く。
---

# designing

設計の作業に入る前に、`ccnavi --docs --type skill-reference --tag designing` で共通とプロジェクトの reference を引き、当たったものを読む。

## 基本の考え方

- 決める前に、要求（何を満たすか）と制約（変えられないもの）を分けて書く
- 案は2つ以上比べ、採らなかった案とその理由も残す
- 得るものだけでなく、失うものと代償を書く
- 決めたことと、まだ決めていないことを分ける。後者は相談の対象にする

## reference の置き場

- 共通: `.claude/skills/designing/references/`
- プロジェクト固有: `projects/<名前>/docs/skills/designing/references/`

中のディレクトリ構造は任意。足し方と書き方は `docs/claude/skill-concepts.md`。
