# プロジェクトを置いて作業する

ワークスペースの下に別のリポジトリを clone して直すことがある（設計 11）。置き場は
`projects/<名前>/` の直下 1 段だけ。それより深い場所と置き場の外はプロジェクトと数えない。
`projects/` が無いか空なら、以下はすべてワークスペース自身の作業として読む。

- プロジェクトの作業は `cd projects/<名前>` してから `ccnavi-git.sh` を打つ。git の操作はそのリポジトリに対して行われる
- チケットは `project:` を持つ。値は `projects/` の名前で、決めるのは人の承認。エージェントが申告した値は判定に使われない

## 何がどこに置かれるか

| 何 | 場所 |
|---|---|
| 提案（承認待ち `todo/`、レビュー待ち `review/`） | そのツリーの `wip/proposals/<状態>/`。プロジェクト向けは `projects/<名前>/wip/proposals/<状態>/` |
| 承認済みチケット（作業中 `doing/`、閉じた `done/`）、フェーズのマーカー、子の記録（`.risk.json` など） | 親チケットのツリーの `.ccnavi/approved/`。プロジェクト向けは `projects/<名前>/.ccnavi/approved/` |
| git のラッパースクリプトの記録 | ワークスペースの `logs/<プロジェクト>/`。ワークスペース自身は `logs/` |
| 判定の記録と控え | ワークスペースの `logs/log.jsonl` と `logs/state/` |
| ワークツリー | ワークスペースの `.claude/worktrees/<名前>` |
| 子のフロー（担当のサブエージェントが読む手順書） | 親チケットのツリーの `.ccnavi/approved/flows/<子>.json`（固定。チケットの欄では指さない） |
| 下書きと使い捨て | そのワークツリーの `scratchpad/`（追跡しない）。ワークツリーが無いときは、ワークスペースの外にあるセッションのスクラッチパッド |
| ワークスペースのルール | `.ccnavi/common/rules.yml`（共通層。リスクの配点も同じ場所）。フェーズの種類は自身の層 `.ccnavi/config/phases.yml` |
| プロジェクトの設定 | `projects/<名前>/.ccnavi/config/`（`rules.yml`・`phases.yml`・`risks.yml`）。正本はここ。親の着手のときに、共通層にあるファイルはその中身で上書きされる（設計 11.12） |

プロジェクトのリポジトリ（git）に入るのは、提案・承認済みチケットとマーカー・プロジェクトの設定の 3 つ。
承認とフェーズの進みは親チケットのブランチに乗って他の機械へ届き、clone すれば続きができる（設計 9.2、REQ-MLT-14）。
別の機械で続きをするのに要るもの（依頼時の HEAD、リスクの点、受け入れた指摘、Draft を外した印）は
全部 `.ccnavi/approved/phases/<親>/` にある。記録と控え（`logs/`）とワークツリーはワークスペース側で、git には入れない。

子のフローはエージェントが書かない（シェルからも）。書くのは人（ボードのフロー編集画面）で、コミットと push も人
（`ccnavi-push-approved.sh`）。着手中は人も書き換えられず、着手のあとに変わると `NOTICE_TICKET_FLOW_CHANGED` で
人に知らされる。親のツリーから起動されたら、自分の担当の子のフローだけに従う（ADR-0085）。

チケットは 1 本のファイルで、`wip/proposals/todo/`（承認待ち）→ `.ccnavi/approved/doing/`（人が承認）→
`wip/proposals/review/`（`ticket finish`。レビュー要のとき）→ `.ccnavi/approved/done/`（人がレビュー）と
動く（ADR-0055）。`.ccnavi/approved/` へ動かすのは人、`wip/proposals/` へ動かすのはエージェント
（`ccnavi-ticket.sh` 経由）。レビューで残った指摘は、人が `decide`（ボードの「決める」か端末）で、指摘ごとに
「対応しない」「このフェーズで直す（続きの子チケットを `doing/` に起こす）」「issue に回す」を選ぶ。

ラッパースクリプトが返す記録の綴りは cwd から開ける形で出る。そのまま `sed -n` などで開けばよい。

## ルールの文面に書く sh の綴り

`rules.yml` の `message` で sh を案内するときは `{root}` から書く。`{root}` はワークスペースルートの
絶対パスに置き換わる。相対の `sh .ccnavi/scripts/...` は、`cwd` がプロジェクトの中だと届かない。

```yaml
message: 生の git は実行しません。'sh {root}/.ccnavi/scripts/ccnavi-git.sh ...' を使ってください。
```
