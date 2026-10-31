---
title: fixtures の規則を commit の test-groups に足す
type: learning-wiki
description: commit の test-groups に拡張の fixtures を読むテストの規則を足す提案
tags: [skills, feedback]
keywords: [commit, test-groups, fixtures, 改名, core, guard, vscode]
proposal: skill-commit-test-groups-fixtures
target: skill
files:
  - .claude/skills/commit/references/test-groups.md
patterns: {external-name-fixtures: 1}
status: 承認待ち
---

# commit/references/test-groups.md に fixtures の規則を足す

- 対象: `.claude/skills/commit/references/test-groups.md`
- 要点: `--json` のキーや CLI のオプションの改名では `core` と `guard` の両方を回す。拡張の fixtures を読む Python のテスト 2 本が落ちるため。表の `extensions/vscode/` の行も強める
- 動機のパターン: [external-name-fixtures](../patterns/external-name-fixtures.md)
- 採否: 承認待ち（採否はユーザが決める）

差分:

```diff
-| `extensions/vscode/` | `ticket`（`core` の `test_test_json` も例を読む） |
+| `extensions/vscode/` | `ticket`（`core` の `test_test_json` と `guard` の `test_suggest` も `test/fixtures/` を読む） |
```

```diff
+`src/` の `--json` 出力のキーや CLI のオプションを変えるときは、`core` と `guard` の両方を回す。`extensions/vscode/ccnavi-board/test/fixtures/` を
+Python のテスト（`tests/core/test_test_json.py`・`tests/guard/test_suggest.py`）も読むので、fixtures の旧キーのままだと落ちる。
+拡張を別のフェーズに分けて計画するなら、前のフェーズでこの 2 本が落ちると分かったうえで分ける（直すのは fixtures を持つフェーズ）。
+
 `src/ccnavi/**/*.py` を変えたとき `e2e` は足さない（e2e は組み立て済みの実行ファイルを試す）。組み立て直したなら足す。
```
