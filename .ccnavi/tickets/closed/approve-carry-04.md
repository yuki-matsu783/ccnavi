---
version: 1
ticket: approve-carry-04
parent: approve-carry
phase: 2
title: 受入テストを足す（敵対的レビューの指摘と、決めた直しの振る舞い）
rationale: 'フェーズ 2・3 の敵対的レビュー（2026-09-13）の指摘を受入テストにする。 テストの穴（projects の下のツリー、CCNAVI_APPROVED、ステージに残る変更、種類が読めない境目）を埋め、
  人が決めた直し（ボードの承認で見せた本文の指紋を照合する、phases.yml の読み込みの例外で判定が 落ちない、止める正規表現が大文字小文字を区別しない、導入スクリプトが配れなかった
  sh を まだ無いものに出す、detached だけのときの振る舞い）の振る舞いを先に書く。

  '
human_review:
  required: true
  reason: 承認の照合の約束と、判定が落ちない約束を受入条件にするため
allow:
- match: Write|Edit
  glob: tests/*
started_at: 2026-09-13T22:05:36+0900
completed_at: 2026-09-13T22:24:35+0900
base_sha: a42e56f414ead01a1d749d35a86e1b7f81f84ca4
ccnavi_approved:
  approved_at: 2026-09-13T22:04:45+0900
  source_tree: approve-carry
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/approve-carry/wip/tickets/todo/approve-carry-04.md
---

# 受入テストを足す

## 人が決めたこと（2026-09-13、敵対的レビューのあと）

- `ccnavi-push-approved.sh` に端末の検査は入れない。エージェントを止めるのは組み込みの deny だけ
- ボードは今までどおり Enter まで送る。止める文面の「利用者が確かめて実行します」を実装に合わせて直す
- 止める正規表現は大文字小文字を区別しない
- ボードの承認は、プレビューで見せた承認画面の本文（`text`）の指紋を `--yes` で照合する（案 A）
- phases.yml の読み込みの例外（`UnicodeDecodeError`）は「壊れている」として扱い、判定を落とさない
- detached のツリーにしか変更が無いときは「運ぶ承認済みチケットは無い。」と言わず、0 で終わる

## 足すテスト

### 承認の照合（`tests/test_approve_json.py`）

1. `--preview --json` の答えに `digest`（`text` の指紋）が載る。同じ状態で 2 回プレビューすると同じ値になる
2. 見せた `digest` を `--yes` に渡すと承認できる
3. プレビューのあとで、束の子の題・理由・範囲（上限の内側）・範囲（上限の外）のどれかを書き換えてから、見せた `digest` で `--yes` を打つと、承認されず `mismatch` が返り、終了コード 1、承認済みチケットは置かれない（4 通りを subTest で）
4. `--yes` に `digest` を渡さないと承認されない（誤りとして言う）
5. 識別子の並びの食い違い（今のテスト）は今までどおり `mismatch`

引数の綴り（`--digest <値>` など）と JSON の鍵の名前は、書く前に実装フェーズ（approve-carry-03）と揃える。
決まっていなければ `--digest` と `digest` で書き、閉じるときに名指しする。

### 判定が落ちない（`tests/test_phases.py`）

6. 共通層の phases.yml に不正な UTF-8 のバイト列があるとき、計画を持つ親の子の作業ツリーへの Write で hook が例外で終わらない（終了コードが 0 か 2、応答が JSON として読める）。種類では切り詰めず、「種類の上限では切り詰めていない」の注記が出る
7. 同じ状態で、Bash の実行前の判定（ゲートの経路）も例外で終わらない
8. 親が計画を持ち、子の番号が計画にあり、phases.yml がどの層にも無いときは、注記を出さない（`unread_type` のファイルの有無の境目。レビューで見逃しを実証済み）

### 止める正規表現（`tests/test_ticket.py`）

9. `sh .ccnavi/scripts/CCNAVI-PUSH-APPROVED.sh`、`SH .ccnavi/scripts/ccnavi-push-approved.sh`、`sh .ccnavi/scripts/CCNAVI-APPROVE.sh` が `DENY_TICKET_APPROVAL_CLI`
10. 止める文面に「利用者が確かめて実行します」が無い

### 運ぶ sh（`tests/test_push_approved_sh.py`）

11. `projects/<名前>/` の下のツリーの置き場の変更をコミットして push する
12. `CCNAVI_APPROVED` を既定と違う綴りにすると、その置き場を運び、`.ccnavi/tickets` は運ばない
13. 実行後、同じツリーの他人の変更がステージ（インデックス）に残っていない
14. detached のツリーにしか変更が無いとき、0 で終わり「運ぶ承認済みチケットは無い。」を言わず、飛ばしたことを標準エラーに言う

### 導入スクリプト（`tests/test_setup.py`）

15. 配布元に `ccnavi-push-approved.sh` が無いまま適用すると、最後の「まだ無いもの」に `ccnavi-push-approved.sh` が出る

## やらないこと

- `ccnavi/`、`scripts/`、`.ccnavi/`、`vscode-extension/` には触らない
- 拡張の `--yes` に指紋を渡すテストは実装フェーズ（approve-carry-03）で書く（`tests/*` の外）
- 今の実装で落ちるテストの一覧を閉じるときに記録する
