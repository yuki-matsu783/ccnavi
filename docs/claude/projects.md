---
type: guide
title: プロジェクトを置いて作業する
description: ワークスペースに複数のリポジトリを置いて管理する方法。置き場、チケット、設定の関連
tags: [projects, ticket]
keywords: [プロジェクト, projects, リポジトリ, clone, チケット, マーカー, 記録, ワークツリー, 層, message]
---

# プロジェクトを置いて作業する

ワークスペースの下に別のリポジトリを clone して直すことがある（設計 11）。置き場は `projects/<名前>/` で、
`projects/` の直下 1 段だけを使う。それより深い場所や置き場の外にあるものはプロジェクトとして扱わない。
`projects/` が無いか空なら、以下はすべてワークスペース自身の作業として読む。

プロジェクトで作業するときは、`cd projects/<名前>` してから `ccnavi-git.sh` を打つ。git の操作はそのリポジトリに対して行われる。
チケットは `project:` 欄を持ち、値には `projects/` の下の名前が入る。この値は人が承認するときに決まり、
エージェントが申告した値は判定に使われない。

## 何がどこに置かれるか

| 何 | 場所 |
|---|---|
| 提案（承認待ち `todo/`、レビュー待ち `review/`） | そのツリーの `wip/proposals/<状態>/`。プロジェクト向けは `projects/<名前>/wip/proposals/<状態>/` |
| 承認済みチケット（作業中 `doing/`、閉じた `done/`）、フェーズのマーカー、子の記録（`.risk.json` など） | 親チケットのツリーの `.ccnavi/approved/`。プロジェクト向けは `projects/<名前>/.ccnavi/approved/` |
| git のラッパースクリプトの記録 | ワークスペースの `logs/<プロジェクト>/`。ワークスペース自身の記録は `logs/` |
| 判定の記録と控え | ワークスペースの `logs/decisions.jsonl` と `logs/state/` |
| ワークツリー | ワークスペースの `.claude/worktrees/<名前>` |
| 状態の跡（いつ・どの経路で置き場が動いたか。追記するだけの補助の記録で、正本は置き場） | 承認済みチケットと同じツリーの `.ccnavi/approved/events/<識別子>.ndjson`（1 行に 1 つの JSON。マーカーの跡は親のファイルに書く） |
| 子のフロー（担当のサブエージェントが読む手順書） | 親チケットのツリーの `.ccnavi/approved/flows/<子>.yml`（場所は固定で、チケットの欄では指定しない。中身は YAML） |
| 下書きと使い捨て | そのワークツリーの `scratchpad/`（追跡しない）。ワークツリーが無いときは、ワークスペースの外にあるセッションのスクラッチパッド |
| ワークスペースのルール | `.ccnavi/common/rules.yml`（共通層。リスクの配点も同じ場所）。フェーズの種類は自身の層の `.ccnavi/config/phases.yml` |
| プロジェクトの設定 | `projects/<名前>/.ccnavi/config/`（`rules.yml`・`phases.yml`・`risks.yml`）。正本はここにある。親チケットに着手するとき、共通層にある同じファイルはこの中身で上書きされる（設計 11.12） |

プロジェクトのリポジトリ（git）に入るのは、提案・承認済みチケットとマーカー・プロジェクトの設定の 3 つ。
承認とフェーズの進み具合は親チケットのブランチに含まれて他の機械へ届き、clone すれば続きから作業できる（設計 9.2、REQ-MLT-14）。
別の機械で続きをするのに要るもの（依頼時の HEAD、リスクの点、受け入れた指摘、Draft を外した印）は、
全部 `.ccnavi/approved/phases/<親>/` にある。記録と控え（`logs/`）とワークツリーはワークスペース側に置き、git には入れない。

子のフローはエージェントが書かない。シェルから書くこともしない。フローは人がボードのフロー編集画面で書き、
コミットと push も人が `ccnavi-push-approved.sh` で行う。着手中は人も書き換えられない。着手のあとにフローが変わると、
`NOTICE_TICKET_FLOW_CHANGED` で人に知らされる。親のツリーから起動されたときは、自分が担当する子のフローだけに従う（ADR-0085）。

チケットは 1 本のファイルで、`wip/proposals/todo/`（承認待ち）→ `.ccnavi/approved/doing/`（人が承認）→
`wip/proposals/review/`（`ticket finish`。レビューが要るとき）→ `.ccnavi/approved/done/`（人がレビュー）の順に
動く（ADR-0055）。`.ccnavi/approved/` へは人が動かし、`wip/proposals/` へはエージェントが `ccnavi-ticket.sh` を使って動かす。
レビューで残った指摘は、人がボードの「決める」か端末の `decide` で扱う。指摘ごとに「対応しない」
「このフェーズで直す（続きの子チケットを `doing/` に起こす）」「issue に回す」のどれかを選ぶ。

ラッパースクリプトは記録の綴りを cwd から開ける形で出す。そのまま `sed -n` などで開けばよい。

## ルールの文面に書く sh の綴り

`rules.yml` の `message` で sh を案内するときは、`{root}` から書く。`{root}` はワークスペースルートの
絶対パスに置き換わる。相対の `sh .ccnavi/scripts/...` は、cwd がプロジェクトの中だとスクリプトが見つからない。

```yaml
message: 生の git は実行しません。'sh {root}/.ccnavi/scripts/ccnavi-git.sh ...' を使ってください。
```
