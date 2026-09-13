---
version: 1
ticket: ticket-rule-merge-02
parent: ticket-rule-merge
phase: 2
title: ルールとチケットの判定を合わせたときの受入テストを書く
rationale: '設計 wip/design/ticket-rule-merge.md の §12 の表を、そのままテストに書く。

  実行前の判定（ルール 4 種 × チケット 5 種）、実行後の監視、チケットの置き場の外し方、

  今と変わらないこと、診断の出力。実装の前なので、合わせ方を変えた行は落ちるテストになる。

  落ちる行と通る行を報告に並べ、実装フェーズがどの行を通せばよいかを名指しする。

  '
human_review:
  required: true
  reason: 判定の振る舞いを固定するテストで、実装とレビューの基準になるため
allow:
- match: Write|Edit
  glob: tests/*
started_at: ''
completed_at: ''
base_sha: ''
ccnavi_approved:
  approved_at: 2026-09-13T22:48:42+0900
  source_tree: ticket-rule-merge
  source_path: C:\Users\taniyama\Desktop\git\ccnavi\.claude\worktrees\ticket-rule-merge\wip\tickets\todo\ticket-rule-merge-02.md
---

# 受入テスト: ルールとチケットの判定を合わせる

書くもの: `tests/test_ticket_rule_merge.py`（新しいファイル）。既存のテストは変えない。

## 前提

- 設計はフェーズ 1 でレビュー済み（PR #34）。表は `wip/design/ticket-rule-merge.md` の §12
- 道具は `tests/test_ticket.py` と同じ形にそろえる。本物の git リポジトリと作業ツリーを一時ディレクトリに作り、
  `tests.inproc.run_ccnavi` で道具を外から叩いて応答と記録だけを見る
- ルールは表の列ごとに組み立てる。`test_ticket.py` の `RULES` は `allow` を持たないので流用しない

## 書くこと

1. **実行前の判定（§12.1）。** ルール（deny / ask / allow / 何も言わない）× チケット（deny / ask / allow /
   範囲の外 / チケットが無い）の全行。判定、`code`、文面がルールかチケットか、`rules` の先頭、`source` の空か否か。
   `rule:` 行と `ticket entry` 行の有無。ルールの `additionalContext` が判定に関わらず載ること
2. **実行後の監視（§12.2）。** 子の作業ツリーでシェルが書いたあとの `PostToolUse`。報告の有無とコード
3. **チケットの置き場の外し方（§12.3）。** 実行前・実行後・サブエージェント終了時の 3 か所で同じ答えになること。
   `CCNAVI_TICKETS` と `CCNAVI_APPROVED` を別の綴りにしたとき。`wip/ticketsX/` の境
4. **今と変わらないこと（§12.4）。** 親子の合成、未承認、作業ツリーが無い、チケット制御が無効、main、
   ルールより先の点検、dry-run、組み込みの既定に落ちたとき
5. **診断（§12.5）。** `--test` の `verdict` と `rules`、承認画面の 1 行、`--explain` の 1 行

## 報告に含めること

- 全テストの件数と、落ちたテストの名前と、それが §12 のどの行か
- 既存のテスト（`uv run python -m unittest discover -s tests -t .`）が今も全部通ること
- 表に書いてあってテストにできなかったものがあれば、その理由

## 範囲

書くのは `tests/` の下だけ。`ccnavi/` と設計文書は読むが触らない。
