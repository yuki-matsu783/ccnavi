---
version: 1
ticket: shellread-sep-03
parent: shellread-sep
phase: 3
predecessors: [shellread-sep-01, shellread-sep-02]
title: shellread の印を 2 つに分け、受入テストを通す
rationale: |
  設計（wip/design/shellread-sep.md）§1 と §5 のとおりに実装する。語の中の切れ目を
  `WORD_SEP = "\x01"` に分け、入力からの除去、`_join` の 2 か所、selfguard.py の
  リダイレクト先の式を直す。完了の条件は、2 番目で書いた受入テスト（skip 付き）が
  skip されずに全部通ること。既存のテストも全部通ること。

  このフェーズの終わりに、1 番目（設計）と 2 番目（受入テスト）の分も含めて人の
  レビューを受ける。
human_review:
  required: true
  reason: 判定の入力の形が変わる。組み込みルールと既存の regex 全部に影響しうる
allow:
  - match: Write|Edit
    glob: "ccnavi/*"
  - match: Write|Edit
    glob: "tests/*"
started_at: ""
completed_at: ""
base_sha: ""
---

# 実装: shellread の印を分ける

設計は `wip/design/shellread-sep.md`。§1「決めたこと」と §5「実装の手順の案」に従う。

## やること

1. `ccnavi/shellread.py`
   - `WORD_SEP = "\x01"` を `SEP` の隣に足し、冒頭コメントを「印は 2 つ」に書き直す
     （`SEP` はコマンドの区切り、`WORD_SEP` は語の中の切れ目）
   - `read()` の先頭で `SEP` と同じく `WORD_SEP` も入力から取り除く
   - `_join` の 2 か所（引用がつないだ空白、語の中の演算子の両側）を `WORD_SEP` にする。
     `_render` の `SEP.join` は変えない
2. `ccnavi/selfguard.py`
   - リダイレクト先の式 `[^ \x00]*` を `[^ \x00\x01]*` にする（`shellread.WORD_SEP` を
     参照して組み立てる。文字を直書きしない）
   - `\x00` の意味を説明しているコメントに、語の中の印が別にあることを 1 行足す
3. `tests/` は 2 番目が書いたものをそのまま通す。skip の条件は `hasattr(shellread, "WORD_SEP")`
   なので、1 を入れれば自動的に効く。テストの期待値を実装に合わせて書き換えない。
   落ちるなら実装か設計のどちらかが間違っているので、設計を読み直してから直す
4. `tests/test_shellread.py` の既存のテストが `SEP` の意味を「語の中」で使っていれば、
   設計 §2 の表に従って `WORD_SEP` に直す（期待する判定は変えない）

## 完了の条件

作業ツリーの中で全部通ること。

```
uv run --with ruff ruff format --check .
uv run --with ruff ruff check .
uv run python -m unittest discover -s tests -t .
```

skip が 0 件（`shellread-sep の実装待ち` の skip が残っていないこと）。

## 範囲

`ccnavi/` と `tests/` だけ。`.claude/ccnavi/rule-samples.yml`（見本の 4 件を allow へ移す）と
文書はこのチケットの外。
