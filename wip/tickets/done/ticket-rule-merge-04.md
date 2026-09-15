---
version: 1
ticket: ticket-rule-merge-04
parent: ticket-rule-merge
phase: 4
title: ルールとチケットの判定を合わせたことと、glob の大文字小文字を文書に書く
rationale: |
  フェーズ 3 までに入れた振る舞いを、設計書・README・要求表に今の状態として書く。
  ルールの判定とチケットの判定を両方出して厳しい側を採ること、チケットの置き場を範囲の外から外すこと、
  ルールの glob と組み込みの守りをどの機械でも大文字小文字を区別せずに当てること。
  経緯は書かず、今の設計と理由だけを書く。
human_review:
  required: true
  reason: 判定の順番と glob の当て方という、利用者が読む約束を書き換えるため
allow:
- match: Write|Edit
  glob: ccnavi.md
- match: Write|Edit
  glob: README.md
- match: Write|Edit
  glob: requirements.md
- match: Write|Edit
  glob: HANDOVER.md
- match: Write|Edit
  glob: CONTEXT.md
- match: Write|Edit
  glob: docs/*
started_at: "2026-09-15T18:12:18+0900"
completed_at: "2026-09-15T18:28:00+0900"
base_sha: "e61f6d02f5aa35ee7133024a24b49269f706ad56"
---

# 文書: ルールとチケットの判定を合わせる

書く元は `wip/design/ticket-rule-merge.md`（フェーズ 1）と、フェーズ 3 の実装。
経緯（「以前は」「レビューで見つかった」など）は書かず、今の設計と理由だけを書く。

## 書くこと

### 設計書 `ccnavi.md`

1. **§6.1 実行前の判定の流れ**：ルールを当てたあと、ルールが deny でなければチケットの判定も出し、強い側を採る。
   同じ強さならルール
2. **§9.5 判定への効かせ方**：ルール 4 種 × チケットの表を、厳しい側を採る形に書き直す。
   チケットの置き場（提案と承認済みチケット）は範囲の外でも咎めない
3. **§7.2 実行後の監視**：ルールの allow に当たる変更にもチケットの範囲を当てる
4. **原則 P2**：チケットは閉じる向きにだけ効き、ルールの allow も狭める。ルールの deny と ask を緩めることは無い
5. **§5 ルールの記法**：`glob` はどの機械でも大文字小文字を区別しない。`regex` は書いたとおりに区別する
6. **理由コードと記録**：チケットがルールを狭めた回の `rules`（先頭が `(ticket-scope)`、後ろにルールの id）と
   `source`（空）、文面に足す 2 行
7. **承認画面と `--explain`** に足した注記

### `README.md`

1. `glob` の大文字小文字の節を、どの機械でも区別しない形に直す
2. チケットの範囲の節に、ルールの allow より強いことと、置き場を範囲の外から外すことを書く
3. 移り方：作業ツリーを allow で開けているプロジェクトは、承認済みチケットに結び付いた作業ツリーでチケットの範囲が効き始める。
   Linux・macOS では `rules.yml` の `glob` の allow も大文字小文字の違う綴りに当たる

### `requirements.md`

1. REQ-MLT-27 を「`glob` はどの機械でも大文字小文字を区別せずに照合し、`regex` は区別して照合する」に直す
2. REQ-TKT に、ルールとチケットの判定を合わせて厳しい側を採ることを足す（番号は空きを使う）

### `docs/`（ADR）

ADR の扱いは、利用者との相談の結果に従う。

- 残す場合：ADR-0022 を置き換える ADR と、glob の大文字小文字の ADR を足す。下書きは `wip/design/adr-ticket-rule-merge.md`
- 残さない場合：設計書の本文だけにし、ADR-0022 の状態を「置き換え済み」にして参照先を設計書の節にする

### `HANDOVER.md`

このチケットの作業で見つかった、別に直すものを書く。

- `check` が「request をやり直せ」と案内するのに、印が残っていると `request` が通らない
- `--lint` が、作業ツリーにある承認済みチケットと印を「統合されるまで効かない」と言う
- 組み直しが置き換えで落ちると、`dist/ccnavi.target` が書かれない
- ワークスペースの `.git` の commit-graph の控えの一覧が、欠けた控えを指している

## この子で直せないもの

- 受入テスト `tests/test_ticket_rule_merge.py` の冒頭が指す `wip/design/ticket-rule-merge.md` は、チケットを締めるときに消える。
  設計書の節に差し替えるには `tests/` に書く必要があり、文書の種類の範囲に入っていない。
  扱いは親が決める（フィードバック対応のフェーズで直すなど）

## 範囲

書くのは上の文書だけ。`ccnavi/` と `tests/` は読むが触らない。
