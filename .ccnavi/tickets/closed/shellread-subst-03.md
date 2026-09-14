---
version: 1
ticket: shellread-subst-03
parent: shellread-subst
phase: 3
predecessors:
- shellread-subst-01
- shellread-subst-02
title: コマンド置換・改行・プロセス置換を読む走査を入れ、受入テストを通す
rationale: '設計（wip/design/shellread-subst.md）§1 と §5 のとおりに実装する。shellread.py に shlex
  の前の

  走査（試作 wip/design/shellread-subst-proto.py）を入れ、切り出した中身を外側の後ろにつなぎ、

  `bare` と縮退の理由 2 つを足す。判定の側は、`bare` に当て直して引用の中の断りを足し、

  記録と `--test` に `quoted` を出す。完了の条件は、2 番目で書いた受入テスト（skip 付き）が

  skip されずに全部通ること。既存のテストも全部通ること。


  範囲には、設計 §0 で足した 4 つの穴（改行、プロセス置換、語の途中の `#`、予約語の直後）を含む。

  うち 3 つは今 allow が後ろのコマンドまで通している。


  このフェーズの終わりに、1 番目（設計）と 2 番目（受入テスト）の分も含めて人のレビューを受ける。

  '
human_review:
  required: true
  reason: 判定の入力の形が変わる。組み込みのルールと既存の regex 全部に影響し、allow が外れる形と deny に変わる形がある
allow:
- match: Write|Edit
  glob: ccnavi/*
- match: Write|Edit
  glob: tests/*
ccnavi_approved:
  approved_at: 2026-09-14T23:10:05+0900
  source_tree: shellread-subst
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/shellread-subst/wip/tickets/todo/shellread-subst-03.md
started_at: 2026-09-14T23:14:25+0900
base_sha: 9762461cbf98d82bc96f7617904775e6e881f0d1
completed_at: 2026-09-14T23:39:36+0900
---

# 実装: コマンド置換・改行・プロセス置換を読む

設計は `wip/design/shellread-subst.md`。§1「決めたこと」と §5「実装の手順の案」に従う。

## やること

1. `ccnavi/shellread.py`
   - 試作の走査（`_Scanner`）、`Reading.bare`、予約語、`REASON_UNTERMINATED_SUBST` と `REASON_AMBIGUOUS_SUBST` を入れる
   - `_tokenize` に `commenters = ""`。`_drop_heredoc_bodies` を消す
   - 冒頭と各関数のコメントを、今の書き方（なぜそうするか）に合わせて書き直す。63〜66 行目の
     「コマンド置換の括弧も区切りに落ちるので何もしなくてよい」は成り立たなくなる
2. `ccnavi/judge.py` / `reasons.py` / `audit.py` / `diagnose.py`
   - deny / ask の理由を組むところで、当たったルールを `bare` に当て直し、当たらなければ引用の中の断り（設計 §1.4）を足す
   - `reasons.unreadable` に 2 つの理由の文、`reasons.undeclared` の縮退の文に `case` の一言
   - 記録に `quoted`（ルールの id の並び。空なら書かない）、`--test` の文字と JSON に `quoted`
   - `--test --json` の形が変わるので、`tests/test_test_json.py` の手順で拡張の例（`vscode-extension/.../test.json`）を書き直す必要があれば、このフェーズの範囲の外なので報告する
3. `tests/`
   - 2 番目の skip を外れて通ることを確かめる（skip の条件は `REASON_AMBIGUOUS_SUBST` の有無なので、書き換えは要らない）
   - `test_acceptance.py` の `test_引用された記号を止めるのは許容した誤検知` の説明を、走査が heredoc と読まなかった `<<` の話に直す
4. 全テスト、`tests/test_sh_portability.py`、ruff を回す
5. `.ccnavi/common/rule-samples.yml` に足す見本の下書き（設計 §3 から選ぶ）を scratchpad に置き、利用者に渡す。見本は組み込みのルールで守られていて、エージェントは書けない

## 範囲

`ccnavi/` と `tests/` だけ。README / ccnavi.md / HANDOVER.md は 4 番目（文書）で直す。
