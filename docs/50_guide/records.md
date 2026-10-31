---
title: 記録と振り返り
type: guide
description: 判定の記録（decisions.jsonl）の読み方、Stop の AI 判定、記録の後始末、学びの蓄積の使い方を利用者向けにまとめる
tags: [user-guide, records, learning-wiki]
keywords: [記録, decisions.jsonl, decision, jq, 秘密の伏せ, Stop の AI 判定, CCNAVI_STOP_REVIEW, prune, ローテート, CCNAVI_LOG_KEEP_DAYS, 学びの蓄積, learning-wiki, CCNAVI_LEARNING, /learn]
---

# 記録と振り返り

ccnaviは判定した呼び出しを記録し、その記録から次のルールの種を拾う。ここでは記録の読み方、StopのAI判定の使い方、記録の後始末、学びの蓄積の使い方を書く。
記録から候補を起こす`ccnavi --suggest`は [check.md](check.md)。

## 記録

判定した呼び出しは、通したものも含めて`logs/decisions.jsonl`に 1 行 1 件で追記される。1 行には判定の材料が欄として入る。

```
{"ts":"...","mode":"dry-run","event":"PreToolUse","tool":"Bash",
 "subject":"git push origin main","decision":"deny","enforced":false,
 "rules":["git-push"],"session":"...","ms":0.9}
```

まず読むのは次の欄である。

| 欄 | 中身 |
|---|---|
| `decision` | `allow` `ask` `deny` `handover` `skip`（Stopで促した回は`nudge`）。`handover`は権限モードに委ねた回 |
| `enforced` | 実際に適用したか。`dry-run`は、一般の保護では`false`、関門と補助の保護で止めた回は`true` |
| `code` | 判定の根拠の種別。よく出るのは`DENY_COMMAND_PATTERN`、`DENY_PATH`、`RULE_ASK`、`UNDECLARED`、`PARSE_UNCERTAIN`、`DENY_TICKET_SCOPE`。実行後チェックの行は`POST_VIOLATION`などで、複数の件がヒットした回は強いほう 1 つ |
| `reason` | 主に`skip`の行。判定まで進まなかった理由（`mode-disabled`、`no-turn-baseline`、`internal-error`など）。Stopの判定役まわりでは`allow`・`deny`の行にも付く（`stop-review-judge`、`stop-review-worker`） |
| `rules` | ヒットしたルールの名前（ルールファイル由来は`<相対パス>:<id>`、組み込みはid）。ルールファイルの外の決めごとで止めた回は先頭に括弧付きの名前（`builtin:ticket-scope`、`builtin:phase-review`など。判定ごとに分かれる） |
| `source` | 判定を下したルールのレイヤー。`common` / `self` / プロジェクトの名前 |

`jq`で数える。

```sh
jq -r 'select(.unwrapped) | .rules[]' logs/decisions.jsonl | sort | uniq -c
jq -r 'select(.decision == "deny") | .source' logs/decisions.jsonl | sort | uniq -c
```

チケットの子ごとの記録（`.ccnavi/approved/phases/<親>/<子>.risk.json`と`.judge.json`）にも項目ごとに`source`が入る。
フェーズ定義を根拠に置くフェーズのマーカーには、そのフェーズ定義のレイヤーが入る。

`subject`・`unwrapped`・`detail`は、書く直前に秘密の形（認証ヘッダーの値、`token=`や`password=`などの値、主なサービスのトークン、
URLに埋めた資格情報、コマンドのパスワード引数など）を伏せる。判定は伏せる前の文字列で下す。

### Stop の AI 判定

チケット提案の取りこぼし（提案を書かずに作成の許可だけ取る・具体性の無い提案・書いたのに送らない提案）を、
独立したコンテキストのagent型Stop hookが判定する。既定では登録されておらず、登録しても`CCNAVI_STOP_REVIEW`の
既定は`dry-run`（記録だけ残して止めない）。

- 登録・解除・点検: ccnavi の根（clone したリポジトリ）の`scripts/ccnavi-stop-review.sh`を、`sh scripts/ccnavi-stop-review.sh register|unregister|check [--root <ワークスペース>]`の形で打つ。
  判定役のモデルの既定は`haiku`、タイムアウトは 60 秒（`register --model <名前>`で変えられる）
- 効き方: `CCNAVI_STOP_REVIEW`（`dry-run` / `enable` / `disable`）。`enable`のときは、検証に通った不備検出だけを
  1 回差し戻す。差し戻しの次のStopでは、既存の促しを 1 回通常どおり動かし、修正後の材料で 1 回だけ再判定する
- 判定の基準: 組み込みの基準を、ワークスペースの`.ccnavi/config/stop-review.md`があればそれで置き換える（無ければ組み込み）。
  ファイルが読めない・空のときは、別の基準を推測せず未検査にする
- 集計: `ccnavi stop-review report`。評価が無い指標は「不明」と表示する
- 登録すると、Stopのたびに判定役のモデルが動くので、待ち時間と費用が増える

### 記録の後始末

セッションが始まるたびに、記録とstateを片付ける。実行前チェックでは走らない。

- `decisions.jsonl`が`CCNAVI_LOG_ROTATE_MB`（既定 10 MB）を超えていたら、`logs/decisions.<日時>.jsonl`へ名前を変える。中身は破棄しない
- ローテートした記録のうち、最後に書かれてから`CCNAVI_LOG_KEEP_DAYS`（既定 14 日）を過ぎたものを消す
- `logs/state/`の、セッションを名前に持つ記録を、そのセッションのどれもが`CCNAVI_STATE_KEEP_DAYS`（既定 14 日）書かれていなければまとめて消す
- `logs/stop-review/`の再評価用の材料とイベントの記録は、別の保持日数`CCNAVI_STOP_REVIEW_KEEP_DAYS`（既定 90 日）で消す

動かした数はセッション開始の行の`detail`に`pruned rotated=1 logs=2 state=5`の形で残る。
手で走らせるのはユーザで、ターミナルから打つ。エージェントからは動かす形が止まり、記録とstateの置き場への書き込みも止まる。

```sh
ccnavi --prune --preview   # 動かすものを並べるだけ
sh .ccnavi/scripts/ccnavi-user-prune.sh             # 動かして、動かしたものを出す（ターミナルから）
```

## 学びの蓄積

判定の記録と、チケット作業で拾った気づきを、ルール・配点・フェーズ定義・テンプレート・スキル・文書への提案として育てる。
記録の取得・ローテート、`ccnavi --suggest`の候補生成、ターンの終わりの振り返りの促しはccnaviのコードが実行する。
気づきを書く、振り返って一般化する、wikiを書くのはエージェントが行う。ルール・配点・フェーズ定義・スキル・文書への反映は、人間が判断して採用する。
AIモデル自体の学習ではなく、人間の承認なしに設定やルールを変えることもない。

- `CCNAVI_LEARNING`の既定は`enable`（`disable`で切れる）。フェーズ定義の`learning: off`で、その定義の子だけ外せる
- 子は閉じる前に気づきの記録を残す。`finish`はコミットされていない記録を拒む。気づきが無ければ`learnings: none`のままでよい
- 対話の中で「覚えておいて」と言うときは`/learn <内容>`。チケットに結び付いたワークツリーでは子の記録に、直接作業のワークツリーでは`docs/wiki/inbox/`に。ワークスペースルートとプロジェクトのルートでは書かずに断るので、ワークツリーを切って打ち直す
- 親を閉じる前に、親のすべての気づきを判定する。処理されていない気づきがあると親の`finish`が止まり、統合の手順を案内する
- `ccnavi --learnings [--json]`がパターンごとの状態（提案中・候補・決着）と観測元の数を出す。SessionStartで、提案の候補が溜まるか閾値に届くと、ユーザにだけ知らせる。提案はエージェントに頼む。採否はMRのマージで決まる。ルール・配点・フェーズ定義は下書きまでで、ユーザが置く
- `type: learning-wiki`の文書は、`ccnavi --docs --type learning-wiki`と明示したときだけ引ける（作業のセッションに渡さない）

エージェント向けの手順は [提案の手順](../../.ccnavi/docs/learning-propose.md)。

## さらに詳しく

- 学びの蓄積の外から観測できる約束: 要件書の[学びの蓄積](../11_requirements/learning-wiki.md)
