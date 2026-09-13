---
version: 1
ticket: shellread-sep-02
parent: shellread-sep
phase: 2
predecessors:
- shellread-sep-01
title: 印を分けたあとの読みと判定を受入テストに書く
rationale: '設計（wip/design/shellread-sep.md）の §4 に、変更後の「入力 → shellread が返す文字列」と

  「見本 → 判定」の対応表がある。それをそのままテストに書く。実装（3 番目）はこのテストが

  通ることを完了の条件にする。レビューは 3 番目と一緒に見る。


  実装前に書くので、そのままでは落ちる。`WORD_SEP` が shellread に無い間は skip する

  ようにして、実装が入った時点で自動的に効くようにする。ターンの終わりに走る検査を

  赤いまま置かないため。

  '
human_review:
  required: false
  reason: テストの追加だけ。実装と一緒に 3 番目でレビューする（延期）
allow:
- match: Write|Edit
  glob: tests/*
started_at: 2026-09-12T18:50:13+0900
completed_at: 2026-09-12T19:00:33+0900
base_sha: 628853846afa4d48e4c0cde963c65d9cc5530605
ccnavi_approved:
  approved_at: 2026-09-12T18:35:56+0900
  source_tree: shellread-sep
  source_path: C:\Users\taniyama\Desktop\git\ccnavi\.claude\worktrees\shellread-sep\wip\tickets\todo\shellread-sep-02.md
cancelled_at: ''
cancel_reason: ''
---

# 受入テスト: shellread の印を分ける

設計は `wip/design/shellread-sep.md`。§4 の 2 つの表と §3 の「変わってはいけないもの」をテストにする。

## 書くもの

1. `tests/test_shellread.py` に足す
   - §4「入力 → 返る文字列」の 14 行。`␀` は `\x00`、`␁` は `\x01`（`shellread.WORD_SEP`）
   - `show()` の可視化に `WORD_SEP` を足す（失敗したとき `<word>` のように見えるように）
   - `git` + `\x01` + `push` が入力から取り除かれて `git push` と読まれること
2. `tests/test_selfguard.py` に足す
   - `grep -n "> /repo/.claude/ccnavi/rules.yml" f` が組み込みの selfguard に当たらないこと
   - `sed -i "s/a b/c/" …rules.yml`、`tee "a b" …rules.yml`、`cp "a b" …rules.yml` が止まること
3. `tests/test_ticket.py`（ゲートの免除）に足す
   - ゲートが閉じている間、`sh .claude/scripts/ccnavi-git.sh commit -m "docs: a b"` が免除されること
4. §4「見本 → 判定」の 13 行を、`tests/fixtures/` のルールではなく `.claude/ccnavi/rules.yml` で
   判定する形で書く（`tests/test_acceptance.py` の既存の書き方に倣う。どのルールで判定しているかは
   そのファイルの先頭を読む）。`cat f | head` が allow に当たらないこと、`grep -n "<<" f` が
   degraded のままであることを含める

## skip の条件

`unittest.skipUnless(hasattr(shellread, "WORD_SEP"), "shellread-sep の実装待ち")` を、
新しく足すテストのクラスかメソッドに付ける。既存のテストには付けない。

## 範囲

`tests/` の下だけ。`ccnavi/` と `.claude/ccnavi/rule-samples.yml` は触らない（見本の移動は
チケットの外で行う）。
