---
version: 1
ticket: ticket-rule-merge-03
parent: ticket-rule-merge
phase: 3
title: ルールとチケットの判定を合わせる実装
rationale: '設計 wip/design/ticket-rule-merge.md（フェーズ 1 でレビュー済み）を実装する。

  実行前の判定でルールとチケットの判定を両方出して厳しい側を採り、実行後の監視も同じ順にそろえ、

  チケットの置き場を範囲の外から外す判定を 3 か所で 1 つの関数にまとめる。

  受入テスト tests/test_ticket_rule_merge.py（フェーズ 2）が実装待ちで飛ばしている 21 行を

  全部通すことを完了の条件にする。目印は ccnavi.ticket.is_ticket_place。

  '
human_review:
  required: true
  reason: 実行前の判定の順番を逆にする本体で、ガードの効き方が変わるため
allow:
- match: Write|Edit
  glob: ccnavi/*
- match: Write|Edit
  glob: tests/*
- match: Write|Edit
  glob: vscode-extension/*
started_at: ''
completed_at: ''
base_sha: ''
ccnavi_approved:
  approved_at: 2026-09-15T14:16:22+0900
  source_tree: ticket-rule-merge
  source_path: C:\Users\taniyama\Desktop\git\ccnavi\.claude\worktrees\ticket-rule-merge\wip\tickets\todo\ticket-rule-merge-03.md
---

# 実装: ルールとチケットの判定を合わせる

設計は `wip/design/ticket-rule-merge.md`。受入テストは `tests/test_ticket_rule_merge.py`。

## 完了の条件

1. `ccnavi/ticket.py` に `is_ticket_place` を足す（受入テストの目印。別の名前にしない）
2. `uv run python -m unittest tests.test_ticket_rule_merge` が **飛ばし 0 件** で全部通る
3. 既存のテストが全件通る（`uv run python -m unittest discover -s tests -t .`）
4. `ruff format` と `ruff check` が通る

## やること（設計の節ごと）

1. **§2 置き場を範囲の外から外す関数**（`ccnavi/ticket.py`）。`is_ticket_place(rel, tickets_rel, approved_rel)`。
   ツリーのルートからの相対パスが提案の置き場か承認済みチケットの置き場の下なら真。前置は `/` の境で切る。
   `judge.ticket_verdict`、`post.ScopeGuard.finding`、`phase.scope_findings` の 3 か所がこれを呼ぶ
2. **§1 実行前の判定**（`ccnavi/judge.py`）。ルールが deny でなければチケットの判定も出し、強い側を採る。
   同じ強さならルール。承認済みチケットの走査は 1 回の判定で 1 度にし、`project_mismatch` と共有する（§9）。
   記録の `rules` と `source` は §1 の表のとおり
3. **§3 文面**（`ccnavi/judge.py`）。理由コードは増やさない。チケットがルールを狭めた回は
   `rule: <id> (<allow|ask>) lets this through, but the ticket for this worktree narrows it` の行を、
   チケットの deny の項に当たった回は `ticket entry: deny <綴り>` の行を足す。
   2 つが重なったときに両方出すかは、フェーズ 2 のレビューの判断に従う（受入テストは両方出る前提）
4. **§5 実行後の監視**（`ccnavi/post.py`）。`_allowed` の近道を外して消す。ルールの deny / ask を先に報告する順は変えない
5. **§6 サブエージェント終了時**（`ccnavi/phase.py`）。外し方を 1 の関数に置き換えるだけで、振る舞いは変えない
6. **§7 承認画面**（`ccnavi/approval.py`）。親の見出しの下に注記を 1 行足す。子の画面は変えない。
   VS Code 拡張のオーバーレイが `--approve --preview --json` の `text` をそのまま出していることを確かめ、
   出していなければ `vscode-extension/` を直す
7. **§8 `--explain`**（`ccnavi/diagnose.py`）。「チケットの作業範囲」の節の頭に注記を 1 行足す

## 報告に含めること

- 受入テストの飛ばし件数が 0 になったこと、既存テストの件数
- 判定の代金。作業ツリーへの Write 1 回で `approval.scan` が何回走るか（変更の前後）。
  記録の `ms` か手元の計測で
- 設計と違う実装にしたところがあれば、その理由

## 範囲

書くのは `ccnavi/` と `tests/`（受入テストは直さない。直す必要が出たら理由を報告する）と、
必要なときだけ `vscode-extension/`。設計文書と README は文書フェーズで直す。
