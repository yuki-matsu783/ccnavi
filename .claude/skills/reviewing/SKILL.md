---
name: reviewing
concept: true
description: >-
  レビューの標準的な考え方への入口。差分やチケットを他者として読む作業、レビューの指摘への対応で使う。細かい手順は references にあり、ccnavi --docs で共通とプロジェクトの両方から引く。
---

# reviewing

レビューの作業に入る前に、`ccnavi --docs --type skill-reference --tag reviewing` で共通とプロジェクトの reference を引き、当たったものを読む。

## 基本の考え方

- 意図の説明を先に読まず、差分と結果から読む
- 指摘には再現の手順か根拠を付ける。推測だけの指摘は区別する
- 指摘の重さ（直さないと壊れる、直したほうがよい、好み）を分けて書く
- レビューする側は直さない。直すのは作った側

## reference の置き場

- 共通: `.claude/skills/reviewing/references/`
- プロジェクト固有: `projects/<名前>/docs/skills/reviewing/references/`

中のディレクトリ構造は任意。足し方と書き方は `docs/claude/skill-concepts.md`。
