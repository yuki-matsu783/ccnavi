---
title: 外に見える名前の改名と拡張の fixtures
type: learning-wiki
target: skill
sources: [feature-207]
description: src の --json キーや CLI のオプションを改めると、拡張の fixtures を読む Python のテストが落ちるパターン
tags: [skills, feedback]
keywords: [fixtures, 改名, JSON, キー, オプション, test_test_json, test_suggest, core, guard, フェーズ]
---

# 外に見える名前の改名は、拡張の fixtures を読むテストを落とす

## 説明

`src/` の `--json` 出力のキーや CLI のオプションを改めると、`extensions/vscode/ccnavi-board/test/fixtures/` を読む Python のテストが落ちる。
拡張は別言語（TypeScript）だが、fixtures は Python 側のテストからも読まれる。

## 原因

fixtures が旧キー・旧オプションのままで、新しい出力と食い違う。拡張側のテストだけを見ていると、Python 側が fixtures を読むことに気づかない。

## 操作の例

src のキーとオプションを改めたフェーズで、`tests/core/test_test_json.py` と `tests/guard/test_suggest.py` の 2 本が落ちた。
次のフェーズで fixtures を新しい名前に直すと通った。

## 回避策

- 改名では `core` と `guard` の両方を回す（`ticket` だけでは足りない）
- 拡張を別フェーズに分けるなら、前のフェーズでこの 2 本が落ちると見込んで分ける。直すのは fixtures を持つフェーズ
