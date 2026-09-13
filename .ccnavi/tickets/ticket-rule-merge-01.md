---
version: 1
ticket: ticket-rule-merge-01
parent: ticket-rule-merge
phase: 1
title: ルールとチケットの判定の合わせ方を決め、設計文書と ADR の下書きを書く
rationale: '親で合意した「ルールとチケットの判定を両方出し、厳しい側を採る」を実装に落とす前に、

  決めておくことがある。チケットの置き場を範囲の外から外す方法、理由コードと文面、

  実行後の監視とサブエージェント終了時の検査のそろえ方、組み込みの守りとの強さ、

  承認画面・--explain・--lint への影響、判定の代金。これを wip/design/ に書き、

  ADR-0022 を置き換える ADR の下書きも同じ場所に置く。受入テストと実装はこの文書に従う。

  '
human_review:
  required: true
  reason: 判定の順番を逆にする設計の決め事で、受入テスト・実装・文書がこれに従うため
allow:
- match: Write|Edit
  glob: wip/design/*
started_at: 2026-09-13T21:55:15+0900
completed_at: ''
base_sha: 81d92346b63c4a2c0cea69e6bb2996ff69e96e38
ccnavi_approved:
  approved_at: 2026-09-13T21:53:32+0900
  source_tree: ticket-rule-merge
  source_path: C:\Users\taniyama\Desktop\git\ccnavi\.claude\worktrees\ticket-rule-merge\wip\tickets\todo\ticket-rule-merge-01.md
---

# 設計: ルールとチケットの判定を合わせる

書くもの。

- `wip/design/ticket-rule-merge.md`（設計文書）
- `wip/design/adr-ticket-rule-merge.md`（ADR-0022 を置き換える ADR の下書き。番号は文書フェーズで振る）

## 前提（親で決まっていること）

- ルールの判定とチケットの判定を両方出し、厳しい側を採る。強さは `deny` > `ask` > `allow`
- チケットの範囲の外は `deny`
- ルールの `deny` は常に最も強い
- チケットが効く条件（ツール、行き先の作業ツリー、未承認は効かない、親子は厳しい側）は変えない

## 決めること

1. **実行前の判定の組み立て**（`ccnavi/judge.py:230-330`、`ticket_verdict`）。ルールが当たったときも
   チケットの判定を出す位置。同じ強さのときにどちらの文面を出すか。記録の `rules` と `source` に
   何を残すか（ルールの id とチケットの両方を残すか）
2. **チケットの置き場の外し方。** 実行前の判定（`judge.py:512`）と実行後の監視（`post.py:306`）は
   チケット自身の提案ファイルだけ、サブエージェント終了時の検査（`phase.py:686`）は提案と
   承認済みチケットの置き場を丸ごと外している。どれにそろえるか、1 つの関数にまとめるか。
   フェーズの印（`.ccnavi/tickets/phases/`）と閉じた承認済みチケットを含めるか。
   プロジェクトから切った作業ツリー（設計 §11）で置き場の綴りがどうなるか
3. **理由コードと文面。** 次の 3 つを同じコードにするか分けるか。
   範囲の外（今の `DENY_TICKET_SCOPE`）、チケットの `deny`、ルールの `allow` / `ask` をチケットが
   狭めた場合。分けるなら記録を読む側（VS Code 拡張、`--explain`、README 付録 A）への影響。
   文面は「ルールは通しているが、このチケットでは止める / 聞く」と読めること
4. **組み込みの守りとの関係**（`ccnavi/builtin.py`、`ccnavi/selfguard.py`、`ccnavi/phase.py` の
   組み込みルール）。組み込みに `allow` や `ask` があるか。あればチケットより強いまま残すか
5. **実行後の監視**（`post.py` の `_findings`、`_allowed`）。`_allowed` の近道を外したあとの順。
   ルールの `ask` に当たる変更をルールの側で報告し、チケットの範囲は見ないままでよいか
6. **サブエージェント終了時の検査**（`subagent.py`、`phase.py` の `scope_findings`）。
   変えることが無いか。置き場の外し方を 2 にそろえるなら、ここも同じ関数を通すか
7. **承認画面と VS Code 拡張のオーバーレイ**（`ccnavi/approval.py:935-1010`、
   `vscode-extension/ccnavi-board/`）。「ルールの `allow` もこの外では止まる」が読めるか。
   子の画面の「親からどれだけ絞ったか」に `deny` の項がどう見えるか
8. **`--explain` と `--lint`**（`ccnavi/lint.py` ほか）。ルールの `allow` とチケットの範囲の重なりについて
   今言っていることがあれば、変えたあとも正しいか。`--explain` が両方の判定を見せるか
9. **判定の代金。** 作業ツリーへの `Write` / `Edit` のたびに承認済みチケットを走査することになる。
   今の `approval.scan` の代金を記録の `ms` か手元の計測で見積もり、控えが要るかを決める
10. **縮退したとき。** ルールファイルが読めず組み込みの既定に落ちたとき（ADR-0012）、承認済みチケットが
    読めないとき、`CCNAVI_TICKET_CONTROL=disable` のとき、dry-run のときに、それぞれ何が起きるか
11. **配った先の移り方。** 作業ツリーを `allow` で開けているプロジェクトが更新したときに何が変わるか。
    README に書く移り方の要点

## 受入テストに渡すこと

2 番目（受入テスト作成）がそのままテストに書ける形で、設計文書の末尾に次を置く。

- ルール（deny / ask / allow / 何も言わない）× チケット（deny / ask / allow / 範囲の外 / チケットが無い）の
  全組み合わせについて、判定・理由コード・どちらの文面か、の表
- 同じ組み合わせを実行後の監視に当てたときに、報告するかしないか、どのコードか、の表
- チケットの置き場の外し方の見本（外すパスと外さないパス）
- 親子の合成と、未承認・作業ツリーが無い・チケット制御が無効のときに今と変わらないことの見本

## 範囲

書くのは `wip/design/` の下だけ。ソース、テスト、README、ccnavi.md、docs/ は読むが触らない。
