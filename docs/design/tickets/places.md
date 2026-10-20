---
type: design
title: 9.2 置き場
description: 提案・承認済みチケット・マーカーなど、チケットに関わるファイルの置き場と書き手
tags: [design-doc, ticket]
keywords: [置き場, 提案, 承認済みチケット, マーカー, wip/proposals, .ccnavi/approved, 正とする側, 本物とする側]
---

[設計書の入口](../../design.md) > [9. チケット制御](../tickets.md)

### 9.2 置き場

| 何 | 場所 | 誰が書く | git |
|---|---|---|---|
| 提案（親も子も） | `wip/proposals/<状態>/<識別子>.md`（ツリーのルートからの相対）。状態は `todo`（承認待ち）と `review`（レビュー待ち）の 2 つの置き場。プロジェクト向けはそのプロジェクトの側に置く（11 章） | `todo/` は親が書く。`review/` へ動かすのは `ccnavi-ticket.sh finish` だけ | それを持つリポジトリにコミット |
| 承認済みチケット | 親チケットのツリーの `.ccnavi/approved/<状態>/<識別子>.md`（ツリーのルートからの相対）。状態は `doing`（作業中。判定が読むのはここだけ）と `done`（閉じた。取り消しは `cancelled_at` を持つ） | `doing/` へは承認（`ccnavi user agree`、ボード、Chrome 拡張、置き場のファイルの移動。ユーザ）と、ユーザが `decide` で起こす続きの子。`done/` へはユーザのレビュー（`confirm` / `ccnavi-user-decide.sh` / `user reviewed` / Chrome 拡張のレビュー済み / `ccnavi-user-close-early.sh`）、レビュー不要の `finish`、`cancel`。コミットと push は `ccnavi-user-push-approved.sh`（9.4） | 親のブランチにコミット |
| フェーズのマーカー | 同 `phases/<親>/<N>.pending` / `.requested` / `.reviewed` / `.skipped` | hook、レビューのスクリプト、`ccnavi user reviewed` | 親のブランチにコミット |
| 親のマーカー | 同 `phases/<親>/ready.json` / `close-early.json` / `closed.json` | レビューのスクリプト、`ticket finish <親>`、ユーザ | 親のブランチにコミット。`ready` は Draft を外す前に、閉じた親子のチケットと一緒にワークスペースの `logs/archive/<リポジトリ>/phases/<親>/` へ退避する（ブランチでは他の機械へ届かなくなる） |
| 閉じたチケットの退避 | ワークスペースの `logs/archive/<リポジトリ>/`（承認済みの領域と同じ構成）と、ready のマーカー `ready/<親>.json` | `ccnavi-review.sh ready`（実行ファイルの `review ready`）だけ。エージェントの書き込みは記録の保護が止める | 入れない（`logs/` は git が追跡しない） |
| 子の記録 | 同 `phases/<親>/<子>.risk.json` / `.judge.json` / `.flow.json`（着手のときのフローのハッシュ。9.3.1） | `ticket finish` / `ticket record-risk` / `ticket start` | 親のブランチにコミット |
| 気づきの記録 | 子（と親）のワークツリーの `wip/handoff/learnings/<識別子>.md` と `.digest.json`（9.16）。直接作業は `docs/wiki/inbox/`、ルートは `wip/handoff/inbox/` | `ticket learn`、子のセッション | 子の合流で親のブランチへ届く。`wip/handoff/` の分は統合で外れる |
| 子のフロー | 同 `flows/<子>.yml`（9.3.1） | ユーザ（ボードのフロー編集画面）。エージェントの Write / Edit は判定が止める。承認で提案のツリーから一緒に動く | 親のブランチにコミット（`ccnavi-user-push-approved.sh` が置き場ごとコミットして push する） |
| 子のフローの下書き | フローと同じツリーの `wip/proposals/flows/<子>.yml`（9.3.1。効力は無い） | 頼まれたエージェント。ユーザがフロー編集画面で取り込み、保存が成功したら同じ中身のときだけ消す | 書いたエージェントがコミットしてよい。取り込んで消えた削除は `ccnavi-user-push-approved.sh` がフローと一緒にコミットして push する |
| テンプレートの下書き | `wip/handoff/` の下の、反映する先と同じ相対パス（例: `wip/handoff/.ccnavi/config/project/templates/<名前>.yml`。そのツリーの。プロジェクトのツリーならそのプロジェクトの下。9.15、9.19。効力は無い） | 頼まれたエージェント（`ccnavi --templates --draft <親>` か直接）。ユーザが `ccnavi user handoff` で取り込み、書けたら同じ中身のときだけ消す | 書いたエージェントがコミットしてよい。取り込んだ結果は、そのツリーのブランチの変更として残る |
| ワークツリー | `.claude/worktrees/<識別子>/` | 親が git のラッパースクリプトで切る | 管理外 |

状態は置き場で表す。チケットは 1 本のファイルで、2 つの置き場を行き来する。ユーザが動かす
向きは `.ccnavi/approved/` へ（承認・レビュー済み・受け入れ・早めに閉じる）、エージェントが動かす向きは
`wip/proposals/` へ（提案を書く・`finish` でレビュー待ちにする）。

```
  wip/proposals/todo ──ユーザが承認──→ .ccnavi/approved/doing
                                          │
                              エージェントが finish（レビュー要）
                                          ▼
  wip/proposals/review ──ユーザがレビュー──→ .ccnavi/approved/done
                                          ▲
                              エージェントが finish（レビュー不要）／cancel
```

`review/` への直接の作成・移動は、`Write` でもシェルでも、誰がやっても組み込みが止める
（`Write` 系は `builtin:ticket-state`、シェルは `builtin:ticket-state-shell`）。`.ccnavi/approved/` は
ccnavi ディレクトリの組み込みルール（`builtin:guard-project-home`）が止める。`todo/` への作成と編集は自由。
シェルの `cp` `ln` `install` は、組み込みの保護（11.6）と同じ部品で行き先を読む（`-t <dir>` の値か、選択肢でない
最後の引数）。`cp -t wip/proposals/review <提案>` も、行き先のあとに選択肢を回した形も止まり、置き場から外へ写すだけの形は止めない。
提案の置き場そのものを行き先にして、元の名前が状態の名前（`review`）か名前の決まらない形（`cp -r /tmp/review wip/proposals/`）も止める。
置き場として読むのは、名前が状態名と大文字小文字まで一致するディレクトリだけ。

ワークスペースルート直下はチケットを持たない。

提案の置き場は `wip/proposals/`、承認済みチケットの置き場は `.ccnavi/approved/`（どちらもツリーのルートからの相対）。
置き場はこの 2 つに固定で、環境変数では動かない。sh と VS Code 拡張も同じパスを見る。

**承認済みチケットは git で共有する。** 承認した機械と作業する機械が違っても承認が届くよう、承認済み
チケットもマーカーも親チケットのブランチに乗せ、`ccnavi-user-push-approved.sh` がコミットして push する（9.4）。
受け取る側では、セッションの最初に `ccnavi-fetch.sh` が fast-forward で取り込む。`--no-overwrite-ignore` を付けるので、ignored のファイルと重なるときは上書きせず、進められなかったと知らせる。同じ sh が、ワークツリーの
起点になる統合先（そのリポジトリの手元の `origin/HEAD` が指すもの。無ければ `origin/main`・`origin/master`）も、チェックアウトされていなければ `update-ref` で
進める。統合先の決め方は `ccnavi-common-state.sh` の `ccnavi_integration` にまとめてあり、`ccnavi-git.sh` は
その名前への直接の push を（`main` などの固定のリストと同じく）拒み、`ccnavi-review.sh` は依頼のとき、既存のマージリクエストのマージ先がこの統合先と一致するかを確かめる（マージリクエストを作るのは `ccnavi-start.sh`）。
手元の `origin/HEAD` は clone のときのまま更新されないので、`ccnavi-fetch.sh` が第 1 周（承認済みチケットの取得）の後、第 2 周（起点を進める）の前に、
リポジトリごとにホストの既定ブランチ（`ls-remote --symref origin HEAD`）を聞き、違えばそのブランチを fetch してから向け直して 1 行出す
（届かない・失敗のときは何も出さず何も変えない。ADR-0148）。`ccnavi-sync.sh` は、ホストに聞くのを先にして、読めないときに手元の
`origin/HEAD`・`main`・`master` に落とし、違えば取れた後に手元を向け直す。
リモートに届かないときは手元の版で判定を続ける。fetch は 1 回ずつ時間を監視して打ち切り、hook の
上限に当たらないようにする。

親のワークツリー（`.claude/worktrees/<P>` で、HEAD のブランチ名を変換した識別子がフォルダ名と合うもの）は、SessionStart では
早送りだけにし、ロックが取れなければ飛ばす。分かれた親子のチケットの merge と、リモートから消えた
親のブランチの確かめ（閉じたかは MR がマージ済みかで決める。ホストに聞けなければ推定せず止まる）は、ユーザが打つ
`ccnavi-sync.sh` が持つ。統合先は、ホストに聞いた名前（`ls-remote --symref origin HEAD`）、聞けなければ手元の `origin/HEAD`、`main`・`master` の順で、
リモートにある最初の名前にする。どれも無ければ止める。親のブランチ名は、実行ファイルの `ccnavi write-and-push family <P>` の `branch` の行（親のワークツリーの HEAD から読んだ名前）で知る。
`ccnavi-sync.sh` は取り込んだ後に `ccnavi sync check <P>` でその親子のチケットを判定し直し、error があれば表示して終了コード 1 にする
（理由を直して打ち直せば通る）。MR がマージ済みと分かったら、手元の片付け（`ccnavi sync tidy`。9.11）を行う。

**`ccnavi-git.sh` の中の判定は P13 の例外。** git の入口は `ccnavi-git.sh` 1 本に寄せてあり（生の
`git` は組み込みでいつも止まる）、その中で次の可否を sh が一次に判定する。チケットに結び付くブランチの `integrate` を断る
（`integrate-ticket`）、統合先への push を断る、親のワークツリーのブランチが
リモートから消えていれば push を断る（push の関門。下）、親のワークツリーでの
`checkout`・`switch` の行き先を、今のブランチと、変換した名前がフォルダ名になるブランチに限る（`worktree add` も変換した名前の
形だけ）。`integrate` / `integrate-done` の状態操作（取り込みの記録・一時ワークツリー・取り込み先への `merge --ff-only --no-overwrite-ignore`・取り込んだブランチの
`update-ref -d`）も sh が持つ。例外に入るのは、生の操作が組み込みで止まり、ラッパーが許可リストで読み、判定の材料のうち
チケットの中身を実行ファイルに聞くものだけ。sh はチケットの YAML を読まず、親のワークツリーか・承認済みの親チケットと着手済みか・`.requested`・退避の有無・
親チケットの `mr:` は `ccnavi tree parent`、`integrate` が断る「チケットに結び付く名前」かどうかは `ccnavi tree bound`（Stop の促しと同じ判定）、直接作業の取り込み先のブランチがチケットに結び付くかは `ccnavi tree base-bound`、親のブランチ名は `ccnavi write-and-push family` の 1 行の答えで決める。答えが得られないときは、
止める側の検査は止める向きに（`integrate` は断り、checkout は親のワークツリーとして狭める）、ツリーを動かす側
（`ccnavi-fetch.sh` の早送り、`ccnavi-sync.sh` の取り込み）は動かさない向きに倒す。

push の関門（`ccnavi_push_gate`、`ccnavi-common-state.sh`）も同じ例外に入る。範囲は `ccnavi-git.sh` の中に限らず、
write-and-push（`ccnavi_wap_prepare`）と `ccnavi-user-push-approved.sh` の push の前も含める。この 3 経路はどれも素の push を打つので、
ここを通さないと、消えた親のブランチを作り直してしまう。対象は親のワークツリーのブランチだけ（通常のブランチと子の push には掛けない）。
形跡（`refs/remotes/origin/<B>`、または着手済み（承認済みの親チケットの `started_at`）・`.requested`・退避）があれば `ls-remote`（60 秒）でリモートにあるかを聞き、
無ければ拒否、失敗・時間切れは確かめられない（拒否側）。形跡が無く、着手も依頼も退避も無ければ初回の push として通す。
承認済みというだけでは形跡にしない（承認して最初に打つ push を通す。着手は write-and-push で push してから完了するので、着手済みなら一度は push している）。
実行ファイルが無い、または古くて `started` の行を返さないときは、参照の形跡だけで決める。`ccnavi-start.sh` の push も `ccnavi-git.sh push` を通るので掛かる。`merged`（ホストへの照会）は関門では呼ばない。
迂回は、ユーザが端末で `git push origin <B>` を一度打つこと。捨てた親は、親のワークツリーを片付ければ対象から外れる。

**正とする側。** 全部の親子のチケットで同じく、親のブランチに固定する。手元では `.claude/worktrees/<P>`（HEAD のブランチ名の
`/` を `-` に置き換えたものが `P` になるツリー）。判定に使うのは、親のワークツリーの有無・
HEAD とフォルダ名の一致・途中の操作を示すファイルだけで、別の PC でも同じ結果になる。
判定はファイルの有無と中身だけで行う。

- 親のワークツリーが無い、HEAD のブランチ名を変換したものがフォルダ名と合わない: **決まらない**。その親子のチケットの承認・状態の操作
  （着手・終了・取り消し・記録・レビューのマーカー）・実行前チェックを止め、準備のしかた（`ccnavi-start.sh --mr <番号>`）を出す。
  ボードにはその親子のチケットが出ない
- 親のブランチがリモートから消えたことは、push の関門が止め、`ccnavi-sync.sh` が診断する（上）。
  オフラインの hook では消失が分からない
- 閉じた親子のチケット: 閉じたかの根拠は MR がマージ済みかだけで、`ccnavi-sync.sh` がホストに聞いて、親の片付けまで行う（9.11）。
  閉じた別の親子の子を先行に持つチケットは、`predecessors` を満たす置き場が
  どこにも無いので `PRED_MISSING` になる。文面は「どの置き場にもありません。閉じて片付いた親子のチケットなら、取り込み済みなので
  predecessors から削除してください」で、置き換えの手段は無い
- 親のワークツリーを片付けた後（`ready` の後）の片付けの列挙元は、`ready` が書いた `retire/<P>.json`（`mr`・`branch`・子の一覧）。
  放棄した親（MR をマージせずに閉じた親）の記録は、ユーザが `ccnavi worktree retire --abandon <P>` で消す（エージェントの Bash は
  `phase_forms` のガードが止める）。記録が残る間、`ccnavi-sync.sh` は毎回案内を出す（片付け待ちだけでは終了コード 0。
  write-and-push を無関係に止めないため。1 になるのは、親のワークツリーが在る間の、リモートに無くマージもされていない親など）
- 親のワークツリー以外にあるチケット（ワークスペースルートや子のワークツリーに残った古いコピー、元ツリーに未コミットで残った
  チケットなど）は、`--lint` の診断にだけ使う。承認も、親のワークツリーの上の提案だけを対象にする
- 閉じた識別子の再利用は、開始時（`ccnavi-start.sh`）にホストへ同名のブランチにマージ済みの MR が無いかを聞いて確かめるだけ。検知はこの開始時の確かめだけで、承認の経路（手元・Chrome とも）では検知しない。
- 先行は親のブランチ上のチケットで読み直し、決まらなければ満たさない（厳しくする向きだけ）
- 親のワークツリーに途中の操作（merge・rebase など）があって HEAD がブランチを指さないときも決まらない（文面で途中の操作を名指しする）
- レイヤー（フェーズ定義・ルール・配点）は、手元では作業ツリーから読む（統合先のレイヤーから
  計算するのは Chrome だけ）。取得済みの `origin/<統合先>` のレイヤー（`git show` で読む）との違いは `--lint` と `ccnavi sync check` の
  warn で言い、親のブランチの上のプロジェクトのレイヤーの食い違いは `--lint` の warn で言う。
  運用は「ルール・フェーズ定義・リスク配点の変更は、先に統合先へ入れてから、それを使う作業をする」

既知の制限: 承認は push するまで他の機械には反映されない。承認の記録は履歴に残る。ccnavi が入っていない機械の
エージェントが偽の承認済みチケットを push できる（防ぐには承認への署名が要る。未実装）。

承認済みチケットの置き場は ccnavi ディレクトリ（`.ccnavi/`、11 章）の下で、組み込みの
`builtin:guard-project-home` が丸ごと守る。ccnavi ディレクトリの外に向けると `--lint` が error で言う。
提案の置き場は親が書くので、ccnavi ディレクトリの外に置く。

**正とするのは親のツリーの版 1 つ。** 子のワークツリーにも同じ承認済みチケットが checkout されるが、読むのは
親のツリー（親のワークツリー）の側だけ（子のワークツリー上の古いチケットから、閉じたチケットを復活させない）。親のワークツリーが
無い、そこにチケットが無い、複数のリポジトリにまたがるときは、正とするツリーが決まらない
（`ticket_fold.authority` が `None` を返す）。状態の操作は止まる。
