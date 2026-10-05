---
type: design
title: 9. チケット制御
description: チケット制御の入口。使うかどうかと、置き場・提案・承認・状態・フェーズ・レビューなど各節への案内
tags: [design-doc, ticket]
keywords: [チケット, チケット制御, 直接作業, チケット作業, CCNAVI_TICKET_CONTROL]
---

[設計書の入口に戻る](../design.md)

## 9. チケット制御

ルールが「どこに書かせないか」を決めるのに対して、チケットは「今回どこに書くか」を決める。
ルールは長く置くもので、チケットは作業 1 本のあいだだけ適用される。要求は [要件 REQ-TKT](../requirements/tickets.md)、
REQ-APV、REQ-RSK。用語は CONTEXT.md。

### 9.1 使うかどうか

全体ルールは全プロジェクトが使う。チケット制御（提案の承認・承認済みチケットの範囲・フェーズの HITL ポイント・
サブエージェントの制限・状態とレビューの操作）まで使うかは `CCNAVI_TICKET_CONTROL`
（`enable` / `disable`、既定 `enable`）で決める。`disable` のとき、判定・実行後チェック・診断はチケットを
一切見ず、`--agree` と `ticket` / `review` の副命令は止まり、VS Code 拡張は「チケット管理」の
入口を出さない。承認済みチケットの置き場を空にすることでは切り替わらない。

チケット制御が有効なワークスペースでも、すべての作業にチケットが要るわけではない。

- **直接作業**: 調査や小さな修正。チケットを起こさず、そのまま進める。判定は全体ルールだけ。
  ワークスペースルート直下と、承認済みチケットの無いワークツリーがこれにあたる
- **チケット作業**: 大きな修正。提案を書いて承認を受け、フェーズとリスクの配点に従って issue と
  マージリクエストを作りながら進める

どちらで進めるかはモデルが決める。ccnavi はこの線引きを判定では保証しない。代わりに `SessionStart`（起動・再開・
compact・clear のどの回も）で、この使い分けをモデルへ渡す。サブエージェントには渡さない。

渡す文に入れるのは線引きと入口（提案の置き場と `ccnavi-ticket.sh`）だけ。レビューの手順・フェーズの
フェーズ定義・リスクの配点・後工程の進め方は、それが要る場面の文や `--help` が名指しする。

dry-run のときは末尾に 1 行足し、通ったことを許可と読まないことまで言う（dry-run では deny に当たった
呼び出しもそのまま実行される）。

## 詳細

| 節 | ファイル | 何が書いてあるか |
|---|---|---|
| 9.2 | [tickets/places.md](tickets/places.md) | 提案・承認済みチケット・マーカーの置き場と、誰が書くか |
| 9.3 | [tickets/proposal-format.md](tickets/proposal-format.md) | 提案の frontmatter と本文の書式 |
| 9.4 | [tickets/approval.md](tickets/approval.md) | `ccnavi --agree` による承認の対象と手順 |
| 9.5 | [tickets/judging-by-path.md](tickets/judging-by-path.md) | 書き込みの行き先のワークツリーから承認済みチケットを選んで判定する |
| 9.6 | [tickets/state-transitions.md](tickets/state-transitions.md) | チケットの置き場とマーカーの状態遷移 |
| 9.7 | [tickets/phases.md](tickets/phases.md) | phases.yml のフェーズ定義と、親の計画 |
| 9.8 | [tickets/hitl.md](tickets/hitl.md) | フェーズの終わりにユーザの手が入る HITL ポイント |
| 9.9 | [tickets/risk.md](tickets/risk.md) | 子を閉じるときに差分から測るリスク |
| 9.10 | [tickets/review.md](tickets/review.md) | レビューの依頼と確認 |
| 9.11 | [tickets/close.md](tickets/close.md) | 親を閉じる条件、早めに閉じる、Draft を外す |
| 9.12 | [tickets/subagents.md](tickets/subagents.md) | SubagentStart と SubagentStop でのサブエージェントの扱い |
| 9.13 | [tickets/branch-lookup.md](tickets/branch-lookup.md) | issue や MR に紐づくブランチを確かめる指示 |
