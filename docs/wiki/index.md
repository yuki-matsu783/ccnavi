---
title: スキル wiki の索引
type: skill-wiki
description: スキルの振り返りで溜めたパターンの一覧。1 パターン 1 行
tags: [skills, feedback]
keywords: [wiki, パターン, 索引, fixtures, 改名, JSON, キー, 旗]
---

# パターンの一覧

- [external-name-fixtures](patterns/external-name-fixtures.md): 外に見える名前（`--json` のキー・CLI の旗）を改めると、拡張の fixtures を読む別言語側のテストが落ちる。原因は fixtures が旧名のままのため。回すグループを `core` と `guard` にし、fixtures を持つフェーズで直す
