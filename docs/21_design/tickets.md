---
type: design
title: 9. チケット制御
description: チケット制御の入口。使うかどうかと、置き場・提案・承認・状態・フェーズ・レビューなど各節への案内
tags: [design-doc, ticket]
keywords: [チケット, チケット制御, 直接作業, チケット作業, CCNAVI_TICKET_CONTROL]
---

[設計書の入口に戻る](../20_design.md)

## 9. チケット制御

ルールが「どこに書かせないか」を決めるのに対して、チケットは「今回どこに書くか」を決める。
ルールは長く置くもので、チケットは作業 1 本のあいだだけ適用される。要求は [要件 REQ-TKT](../11_requirements/tickets.md)、
REQ-APV、REQ-RSK。用語は CONTEXT.md。

### 9.1 使うかどうか

全体ルールは全プロジェクトが使う。チケット制御（提案の承認・承認済みチケットの範囲・フェーズの HITL ポイント・
サブエージェントの制限・状態とレビューの操作）まで使うかは `CCNAVI_TICKET_CONTROL`
（`enable` / `disable`、既定 `enable`）で決める。`disable` のとき、判定・実行後チェック・診断はチケットを
一切見ず、`user agree` と `ticket` / `review` のサブコマンドは止まり、VS Code 拡張は「チケット管理」の
入口を隠す。

生の git は組み込みの deny（`builtin:guard-raw-git`）でいつも止め、git の入口をこのラッパー 1 本に寄せる（REQ-TKT-55）。ラッパーの中には、承認済みチケットの
置き場を別のコミットの中身に戻す `restore` / `checkout` を通さない、直接作業だけを取り込む `integrate`、子のワークツリーから送らない
`push`、親のブランチへ移るときに承認済みチケットを運ぶ `checkout` といった、チケット制御の可否がある。生の git は組み込みで
いつも止める。チケット制御の有無と共通レイヤーの有無には依らない。

- 式は `git` がコマンドの位置に立つ形。文面はラッパーがあればその打ち方を名指しし、無ければユーザに頼む形にする
- ルールファイルが読めずに組み込みの既定へ戻った間も足す（5.6）
- セッション開始の作業の決まりの git の行は、この組み込みが支えるので常に出す。文は、ラッパーがあれば打つ形、無ければユーザに頼む形を書く（条件 `git_wrapper`）

チケット制御が有効なワークスペースでも、すべての作業にチケットが要るわけではない。

- **直接作業**: 調査や小さな修正。チケットを起こさず、そのまま進める。判定は全体ルールだけ。
  ワークスペースルート直下と、承認済みチケットの無いワークツリーがこれにあたる
- **チケット作業**: 大きな修正。提案を書いて承認を受け、フェーズとリスクの配点に従って issue と
  マージリクエストを作りながら進める

どちらで進めるかはモデルが決める。ccnavi は `SessionStart`（起動・再開・
compact・clear のどの回も）で、この使い分けをモデルへ渡す。サブエージェントには渡さない。

渡す文に入れるのは線引きと入口（提案の置き場と `ccnavi-ticket.sh`）だけ。レビューの手順・フェーズの
フェーズ定義・リスクの配点・後工程の進め方は、それが要る場面の文や `--help` が名指しする。

dry-run のときは末尾に 1 行足し、通ったことを許可と読まないことまで言う（dry-run では、一般の deny にヒットした
呼び出しはそのまま実行される。チケットの関門と補助の保護は止める）。

## 詳細

| 節 | ファイル | 何が書いてあるか |
|---|---|---|
| 9.2 | [tickets/places.md](tickets/places.md) | 提案・承認済みチケット・マーカーの置き場と、誰が書くか |
| 9.3 | [tickets/proposal-format.md](tickets/proposal-format.md) | 提案の frontmatter と本文の書式 |
| 9.4 | [tickets/approval.md](tickets/approval.md) | `ccnavi user agree` による承認の対象と手順 |
| 9.5 | [tickets/judging-by-path.md](tickets/judging-by-path.md) | 書き込みの行き先のワークツリーから承認済みチケットを選んで判定する |
| 9.6 | [tickets/state-transitions.md](tickets/state-transitions.md) | チケットの置き場とマーカーの状態遷移 |
| 9.7 | [tickets/phases.md](tickets/phases.md) | phases.yml のフェーズ定義と、親の計画 |
| 9.8 | [tickets/hitl.md](tickets/hitl.md) | フェーズの終わりにユーザの手が入る HITL ポイント |
| 9.9 | [tickets/risk.md](tickets/risk.md) | 子を閉じるときに差分から測るリスク |
| 9.10 | [tickets/review.md](tickets/review.md) | レビューの依頼と確認 |
| 9.11 | [tickets/close.md](tickets/close.md) | 親を閉じる条件、早めに閉じる、Draft を外す |
| 9.12 | [tickets/subagents.md](tickets/subagents.md) | SubagentStart と SubagentStop でのサブエージェントの扱い |
| 9.13 | [tickets/branch-lookup.md](tickets/branch-lookup.md) | issue や MR に紐づくブランチを確かめる指示 |
| 9.14 | [tickets/phase-management.md](tickets/phase-management.md) | フェーズ管理の全体像。定義・計画・先行関係・レビュー・局面をつなぐ入口 |
| 9.15 | [tickets/templates.md](tickets/templates.md) | 作業・レビュー計画のテンプレート。置き場・2 層の統合結果・検査・`--templates` |
| 9.16 | [tickets/learning-wiki.md](tickets/learning-wiki.md) | 学びの蓄積。子の気づきを learning-wiki に溜め、反映先への提案として育てる流れ（一部実装済み） |
| 9.17 | [tickets/sharing.md](tickets/sharing.md) | 複数人・別 PC・別セッションでの作業。共有するもの・同期の手順・承認者の操作の入口・保証しないこと |
| 9.18 | [tickets/user-entry.md](tickets/user-entry.md) | ユーザ専用の入口。`ccnavi user`・`ccnavi-user-*.sh`・hook の止め・実行ファイルの読み替えと断り・ターミナル要求を、表 1 つ（`tickets/model/user_only.py`）から作る仕組み |
| 9.19 | [tickets/handoff.md](tickets/handoff.md) | 保護対象の変更の取り込み。`wip/handoff/` の下書きを `ccnavi user handoff` で取り込む手順・種類の表・取り込みの安全・ターミナル要求とボードの経路 |
| 9.20 | [tickets/chrome-approval.md](tickets/chrome-approval.md) | Chrome 拡張による承認。形と CSP・ブランチの読み方・承認の流れと競合・1 コミットの作り方・PAT の権限・「始める」・レビュー済みのマーカー |
