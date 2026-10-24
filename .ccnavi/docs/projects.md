---
type: guide
title: プロジェクトを置いて作業する
description: ワークスペースに複数のリポジトリを置いて管理する方法。置き場、チケット、設定の関連
tags: [projects, ticket]
keywords: [プロジェクト, projects, リポジトリ, clone, チケット, マーカー, 記録, ワークツリー, レイヤー, message, status, 承認済みチケットの状態]
---

# プロジェクトを置いて作業する

ワークスペースの下に別のリポジトリをcloneして、そのリポジトリを直すことがある（設計11）。置き場は
`projects/<名前>/`の直下1段だけで、それより深い場所や置き場の外にあるものはプロジェクトとして扱わない。
`projects/`が無いか空なら、以下の説明はすべてワークスペース自身の作業に当てはめて読む。

- プロジェクトで作業するときは、`cd projects/<名前>`してから`ccnavi-git.sh`を実行する。gitの操作はそのリポジトリに対して行われる
- チケットは`project:`キーを持つ。値は`projects/`の下の名前で、ユーザが承認するときに決まる。エージェントが申告した値は判定に使われない
- プロジェクトのissueから始める親の識別子は`feature-<番号>-<プロジェクト名>-<slug>`にする。issueの番号はリポジトリごとなので、slugの頭のプロジェクト名でワークスペースのissueと名前を分ける
- 親の識別子は親のブランチ名の`/`を`-`に置き換えたもの。プロジェクトに既にある`/`を含むブランチで作業するときも、`ccnavi-start.sh`がそのブランチを直接チェックアウトしたワークツリーを作る。識別子・ファイル名・ワークツリー名・ロックの鍵は`/`を含まない。親のブランチ名は親のワークツリーのHEADから読む

## 何がどこに置かれるか

| 何 | 場所 |
|---|---|
| 提案（承認待ち`todo/`、レビュー待ち`review/`） | そのツリーの`wip/proposals/<状態>/`。プロジェクト向けは`projects/<名前>/wip/proposals/<状態>/` |
| 承認済みチケット（作業中`doing/`、閉じた`done/`）、フェーズのマーカー、子の記録（`.risk.json`など） | 親チケットのツリーの`.ccnavi/approved/`。プロジェクト向けは`projects/<名前>/.ccnavi/approved/` |
| gitのラッパースクリプトの記録 | ワークスペースの`logs/<プロジェクト>/`。ワークスペース自身の記録は`logs/` |
| 判定の記録とstateの置き場 | ワークスペースの`logs/decisions.jsonl`と`logs/state/`（親を閉じたあとの片付けの記録は`logs/state/retire/<親の識別子>.json`で、ロックは`logs/state/locks/<リポジトリ>/<親の識別子>`。親子ごとの取り込み状態は持たない） |
| ワークツリー | ワークスペースの`.claude/worktrees/<名前>` |
| 子のフロー（担当のサブエージェントが読む手順書） | 親チケットのツリーの`.ccnavi/approved/flows/<子>.yml`。置き場は固定。中身はYAML。チケットの欄で場所を指定しない |
| 閉じたチケットの退避（`ready`がDraftを外す前に、閉じた親子のチケットの`done/`・`phases/<親>/`・`flows/`を移す）。手元の機械にだけ残る | ワークスペースの`logs/archive/<リポジトリ>/`。ワークスペース自身は`self`、プロジェクトはその名前。その下は承認済みの領域と同じ構成 |
| 子のフローの下書き（エージェントが書く。効力は無い） | フローを保存するツリーと同じツリーの`wip/proposals/flows/<子>.yml`。プロジェクト向けは`projects/<名前>/wip/proposals/flows/<子>.yml` |
| 作業・レビュー計画のテンプレート（効力のあるもの。計画と子のフローの一式） | 共通は`.ccnavi/config/common/templates/<名前>.yml`、プロジェクトは`.ccnavi/config/project/templates/<名前>.yml`。取り込むのはユーザ（`.ccnavi/docs/templates.md`） |
| テンプレートの下書き（エージェントが書く。効力は無い） | 反映する先と同じ相対パスで`wip/handoff/`の下（`wip/handoff/.ccnavi/config/project/templates/<名前>.yml`など）。プロジェクト向けは`projects/<名前>/wip/handoff/…`。取り込むのはユーザ（`ccnavi user handoff`） |
| ユーザに渡す物（保護対象を直した写しなど） | ワークスペースのワークツリーの`wip/handoff/`（追跡する。直接作業では`integrate`が取り込み結果から外す） |
| 使い捨てのファイル・コミットしたくないもの | ワークスペースルート直下の`scratchpad/`（`.gitignore`で追跡から外す。`.ccnavi/docs/scratchpad.md`）。セッションのスクラッチパッドには置かない。プロジェクトの`projects/<名前>/`には`wip/`の規約が無いので、プロジェクトの作業中の置き物もワークスペース側に置く |
| ワークスペースのルール | 共通レイヤーの`.ccnavi/config/common/rules.yml`。リスクの配点とフェーズ定義（`phases.yml`）も同じ場所。レイアウトに依存するフェーズ定義は自身のレイヤーの`.ccnavi/config/project/phases.yml` |
| プロジェクトの設定 | `projects/<名前>/.ccnavi/config/project/`（`rules.yml`・`phases.yml`・`risks.yml`）。ここはプロジェクト専用で、着手では上書きされない。親チケットに着手したあと、ユーザの発言のたびに、共通レイヤーは親のワークツリーのプロジェクトの`.ccnavi/config/common/`にミラーされる（設計11.12。ずれは`ccnavi user mirror`で見て、揃えるのはユーザ）。フェーズ定義は共通レイヤーと`config/project/`の和 |

プロジェクトのgitリポジトリに入るのは、提案、承認済みチケットとマーカー、プロジェクトの設定の3つ。
承認とフェーズの進み具合は親チケットのブランチに含まれて他の機械へ届くので、cloneすれば続きから作業できる（設計9.2、REQ-MLT-14）。
別の機械で続きをするのに必要な、依頼時のHEAD、リスクの点は、すべて`.ccnavi/approved/phases/<親>/`にある。
Draftを外したマーカー（`ready.json`）は、`ready`が閉じたチケットと一緒にワークスペースの`logs/archive/`へ退避するので、
ブランチでは他の機械へ届かない。記録とstateの置き場（`logs/`）とワークツリーはワークスペース側に置き、gitには入れない。
閉じた親子のチケットは`ready`が`logs/archive/`へ移して親のブランチから消すので、既定のブランチには残らない。

効力のあるフロー（`.ccnavi/approved/flows/`）は、エージェントが書かず、シェルからも書かない。下書きは`wip/proposals/flows/<子>.yml`に、
頼まれたときだけ書き、`ccnavi --lint --flow <下書き>`で確かめてから渡す。下書きには効力が無く、ユーザがボードのフロー編集画面で
差分を読んで取り込み、保存したものだけが効く。保存とコミット・pushはユーザが`ccnavi-user-push-approved.sh`で行う。取り込んで消えた下書きも
一緒にコミットして push する。着手中はユーザも書き換えられない。着手のあとにフローが変わると、`NOTICE_TICKET_FLOW_CHANGED`で
ユーザに知らされる。親のツリーから起動されたときは、自分が担当する子のフローだけに従う。

## 承認済みチケットの状態を確かめるとき

着手の前、引き継ぎを読んだあと、承認の有無に迷ったときは、ファイルを読んで推測せず
`sh .ccnavi/scripts/ccnavi-ticket.sh status [<親>]`を打つ。置き場、承認の時刻、着手しているか、
未コミットか・未pushか、止まっている理由、次の一手をccnaviが言う。読むだけなので、サブエージェントからも実行できます。

承認はチケットの中身を変えない。手で`doing/`へ動かした承認とccnaviの承認は同じ中身になるので、欄が無いことや
未コミットであることは、承認が途中で止まった印ではない。未コミットの承認済みチケットはエージェントが運ばず、
statusが言うとおりユーザに`ccnavi-user-push-approved.sh <親>`を打ってもらう。

## チケットの置き場の移り方

チケットは1つのファイルで、`wip/proposals/todo/`（承認待ち。エージェントが`ccnavi-ticket.sh propose`で親のブランチへ送る）→`.ccnavi/approved/doing/`（ユーザが承認）→
`wip/proposals/review/`（`ticket finish`。レビューが要るとき）→`.ccnavi/approved/done/`（ユーザがレビュー）の順に
移る。`.ccnavi/approved/`へ移すのはユーザ、`wip/proposals/`へ移すのはエージェントで、
エージェントは`ccnavi-ticket.sh`を使う。レビューで残った指摘は、ユーザが`decide`で指摘ごとに扱いを決める。`decide`はボードの［レビュー完了］か端末の`ccnavi-user-decide.sh <N>`から使う。
選べるのは「対応しない」「このフェーズで直す」「issueに回す」の3つ。「このフェーズで直す」は続きの子チケットを`doing/`に作る。

ラッパースクリプトは、記録のパスをcwdから開ける形で出す。そのまま`sed -n`などで開けばよい。

## ルールの文面にshを書くとき

`rules.yml`の`message`でshを案内するときは、`{root}`から書き始める。`{root}`はワークスペースルートの
絶対パスに置き換わる。相対パスの`sh .ccnavi/scripts/...`は、`cwd`がプロジェクトの中にあると見つからない。

```yaml
message: 生の git は実行しません。{script:ccnavi-git.sh} ... を使ってください。
```

`{script:<名前>}`は`sh <引用したパス>/.ccnavi/scripts/<名前>`に置き換わる（空白や`'`を含むパスも、そのまま打てる形になる）。
名前は`[A-Za-z0-9._-]+`だけ。形が違うもの、スクリプトが無いものは`--lint`が警告する。
