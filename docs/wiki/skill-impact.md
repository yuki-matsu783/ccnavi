---
title: スキル提案の採否
type: skill-wiki
description: スキルへの提案ごとの対象・要点・差分・採否の記録
tags: [skills, feedback]
keywords: [提案, 採否, 却下, 差分, commit, test-groups]
---

# 提案の記録

## commit/references/test-groups.md に fixtures の規則を足す

- 対象: `.claude/skills/commit/references/test-groups.md`
- 要点: `--json` のキーや CLI の旗の改名では `core` と `guard` の両方を回す。拡張の fixtures を読む Python のテスト 2 本が落ちるため。表の `extensions/vscode/` の行も強める
- 動機のパターン: [external-name-fixtures](patterns/external-name-fixtures.md)
- 採否: 承認待ち（採否はユーザが決める）

差分:

```diff
-| `extensions/vscode/` | `ticket`（`core` の `test_test_json` も例を読む） |
+| `extensions/vscode/` | `ticket`（`core` の `test_test_json` と `guard` の `test_suggest` も `test/fixtures/` を読む） |
```

```diff
+`src/` の `--json` 出力のキーや CLI の旗を変えるときは、`core` と `guard` の両方を回す。`extensions/vscode/ccnavi-board/test/fixtures/` を
+Python のテスト（`tests/core/test_test_json.py`・`tests/guard/test_suggest.py`）も読むので、fixtures の旧キーのままだと落ちる。
+拡張を別のフェーズに分けて計画するなら、前のフェーズでこの 2 本が落ちると分かったうえで分ける（直すのは fixtures を持つフェーズ）。
+
 `src/ccnavi/**/*.py` を変えたとき `e2e` は足さない（e2e は組み立て済みの実行ファイルを試す）。組み立て直したなら足す。
```
