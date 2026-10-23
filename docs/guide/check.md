---
title: 設定の確かめ方
type: guide
description: ルールが何に当たるか（--test・--explain）、期待する判定（--test-expectations）、記録からの候補（--suggest）、設定の検証（--lint）の使い方と出力の見方
tags: [user-guide, diagnostics, config]
keywords: [--test, --explain, --test-expectations, --suggest, --lint, 設定の検証, 期待する判定, 終了コード, 出力例, ルールが読めないとき]
---

# 設定の確かめ方

ルールを書いたあと、意図どおりに効いているかを端末から確かめる。ここに並べる診断はhookからは呼ばれない。ユーザが打つ。
判定は実運用と同じ経路を通る（[要件 2.10](../requirements/diagnostics.md)）。ルールの書き方は [rules.md](rules.md)。

| やりたいこと | 打つもの |
|---|---|
| ある呼び出しが止まるか調べる | `ccnavi --test` |
| いま適用されている宣言を見渡す | `ccnavi --explain` |
| 書いたルールが期待どおりか、まとめて確かめる | `ccnavi --test-expectations` |
| 記録からルールの候補を出す | `ccnavi --suggest` |
| 防御を無効化しうる記述を拾う | `ccnavi --lint` |

## ルールが何に当たるかを確かめる

```sh
ccnavi --test Bash "cd /repo && git push"
```

```
verdict: deny (DENY_COMMAND_PATTERN)
tool: Bash
subject: cd /repo && git push
payload: cwd=/repo  permission_mode=(無し)  agent_id=(無し)
rules:
  deny:git-push  glob '*git push*'
    -> (?s:(?>.*?git\ push).*)\Z
response:
  [ccnavi] DENY_COMMAND_PATTERN (rule: git-push)
  ...
```

出るのは、判定と根拠コード、判定に渡したpayload、当たったルールとタイプ、`glob`を翻訳した正規表現、返る文面。パスなら行き着く先、実行役のコマンドの中で
当たったなら`unwrapped:`の行も出る。モードは常に`enable`。
当たったルールは判定が当てたものそのもので、ルールファイルの外から来た根拠（組み込みの保護、チケットの状態の保護、
読めずに退避した組み込みの既定）は「ルールファイルの外にある根拠」と出る。組み込みの式は長いので、頭だけを出して全体の字数を添える。

hookはClaude Codeから呼び出しの場所・権限モード・サブエージェントの印を受け取り、判定がそれで変わる。試験の既定はワークスペースルートで打った、
権限モードの無い、メインの呼び出し（ルールが言及していない呼び出しは`ask`と出る）。変えて試すときは次の 3 つを渡す。

```sh
ccnavi --test Bash "git push" --cwd .claude/worktrees/feat-x --permission-mode dontAsk --agent-id a1
```

| フラグ | payloadの欄 | 変わるもの |
|---|---|---|
| `--cwd <ディレクトリ>` | `cwd` | Bashの行き先のツリー、`cd`の読み、レビュー待ちの止め、子からのpushの止め。起動した場所からの相対でよい。無いディレクトリは断る。Windowsではネイティブの表記（`C:\...`）で書く |
| `--permission-mode <モード>` | `permission_mode` | ルールが言及していない呼び出しの結末（[rules.md](rules.md)の「ルールが言及していない呼び出し」）。`auto` / `default` / `acceptEdits` / `plan` / `dontAsk` / `bypassPermissions`のどれか。ほかの名前は断る（hookは知らない名前を`ask`にするが、試験で打ち間違いを黙って`ask`にすると、そのモードの答えに読める） |
| `--agent-id <id>` | `agent_id` | サブエージェントに許さない操作の止め（`DENY_SUBAGENT_TICKET_OP`） |

`--permission-mode`と`--agent-id`は`--test`でだけ使える。ほかの経路（`--lint`・`--explain`・`--docs`も）に渡すと終了コード 1 で断る。
フラグは`--test <ツール> <対象>`の前か後ろに置く。2 語の間に置く（`--test Bash --permission-mode auto '<cmd>'`）とオプションの名前が対象になるので、
ツールか対象にオプションが入った形と、2 語の後ろに余った語がある形は終了コード 1 で断る。

いま適用されている宣言を数え上げるには`--explain`。レイヤーごとのルール、フェーズ定義とリスクの配点の表、承認されたチケットの作業範囲が出る。

```
■ rules 共通レイヤー（.ccnavi/config/common/rules.yml、deny 12 / ask 3 / allow 4）
  deny  workspace-root               Write|Edit|NotebookEdit            ^{root}[\\/]
■ rules ワークスペースのレイヤー（.ccnavi/config/project/rules.yml、deny 0 / ask 0 / allow 1）
  allow self:worktrees               Write|Edit                         */.claude/worktrees/*
■ rules lib（projects/lib/.ccnavi/config/project/rules.yml、deny 1 / ask 0 / allow 0）
  deny  lib:schema                   Write|Edit                         */schema/*
■ phases（自身のレイヤー 7 種、lib 0 種）
  id              レイヤー        kind    title           review  scope
  design          自身のレイヤー  work    設計            mr      wip/design/*
■ risk（levels: medium 20 / high 40 / critical 70）
  id              レイヤー        加点条件            points  message
  big-diff        共通レイヤー    lines_over 300      25      行数が多い
```

（件数と中身は例。）順序は判定と同じ 共通レイヤー → 自身のレイヤー → プロジェクト（名前順）で、重複として捨てた定義は出ない。
「どのルールにも一致しないツール呼び出し」の節は、権限モードごとに、読めた呼び出しと読み切れない呼び出し（`degraded`）の結末を 1 行ずつ出す。
`--explain`は、ルールの表のあとに「dry-runのときに止めるもの」（一般の保護は報告だけにし、チケットの関門とその抜け道をふさぐ補助の保護は止める）と、「判定に達せなかったとき」（内部の例外・期限切れ・payloadが読めない。ツールごとに、`enable`で止めるか通すか、`dry-run`で止めるか通すか）の節も出す。
読めないレイヤーは「読めない: <理由>。このレイヤーは空として扱う」、設定ファイルが無いレイヤーは「このレイヤーの設定ファイルはありません。設定なしとして扱います。」と出る。
`levels`の行は共通レイヤーの値。レイヤーで`levels`を書くとキーごとに小さいほうが採られるので、チケットに適用される値はその`project:`のレイヤーで変わる。

出力の機械可読な形は [JSONの形](../requirements/json-shapes.md)。判定の仕組みは [設計 10](../design/diagnostics.md)。

## 期待する判定で確かめる

```sh
ccnavi --test-expectations .ccnavi/config/common/rule-expectations.yml
```

期待する判定をすべて判定に掛け、期待と食い違ったものを名指しする（1 件でもあれば終了コード 1）。期待は`deny` `ask` `allow`のタイプに置き、
タイプの名前が期待する判定になる。`subject`の`/repo`は走らせたワークスペースルートに読み替えられる。
`tool: Stop`（`subject: "(stop)"`）は、ターンの終わりに使われるルールがあるかを見る（あれば`allow`）。
ルールを 1 件足したら期待も 1 行足し、**止めたくないものも必ず一緒に置く**。

期待のファイルはエージェントが直接書けない（[hooks.md](hooks.md)の「コアファイルを守る」）。下書きはワークツリーの`wip/handoff/`に置き、ルールの下書きと一緒にユーザに渡す。
`/ccnavi-config`スキルがこの流れを回し、フェーズ定義とリスクの配点も見る。エージェント向けの手順は [ルールを育てる](../../.ccnavi/docs/grow-rules.md)。
開発者が同じ検査を回す方法は [開発](../development.md)。

## 記録から候補を起こす

```sh
ccnavi --suggest [--json]
```

記録（`logs/decisions.jsonl`と、同じ置き場で回した`decisions.*.jsonl`）を数えて、ルールの下書きを出す。何も書かない。

| 候補 | 拾うもの | 出す下書き |
|---|---|---|
| ルールを足す（`rule`） | どのルールも言及せず（`UNDECLARED`）権限モードに渡った呼び出しが、同じ形で 5 回以上 | `ask`のルール。形はBashなら先頭のコマンドとサブコマンド（`npm install`）、パスのツールならディレクトリの下（`{root}/docs/*`）、WebFetchならホストの下 |
| 文面を見直す（`message`） | 同じルールが同じ呼び出し（[rules.md](rules.md)の「同じ呼び出しを繰り返し止めたとき」の均し方）を`CCNAVI_DENY_REPEAT`回以上止めた | いまの`deny`のルールそのまま。直すのは`message` |

どちらも`rules.yml`の 1 タイプぶん（`rules:`）と、`rule-expectations.yml`の 1 タイプぶん（`expectations:`。記録の呼び出しを 3 件まで、ルートは`/repo`）の組で、
候補ごとにYAMLの文書を 1 つずつ並べる。出すのは`deny`と`ask`だけで、`allow`は出さない（記録から通す側を勧めると、判定を緩める変更を機械が勧めることになる）。
候補はどれも、`--lint`と同じ読みでそのルールにerrorが無く、期待が`--test-expectations`と同じ判定で期待したタイプになり、そのルールに当たったものだけ。
ルールを足す候補は、共通レイヤーのルールファイルのコピーに 1 件足した一時ファイルで試す。通らなかったもの（いまのルールでは別の判定になる形、
ルールファイルの外から来た根拠の拒否）は数だけを出す。読み切れなかった呼び出し（`degraded`）と、上限で切れた`subject`は数えない。
置くのはユーザで、`/ccnavi-config`の手順で確かめてから置く。

## 設定の検証

`--lint`は判定を行わず、防御を無効化しうる記述だけを報告する。ルールを書き換えたあとと、CIで走らせる。

```sh
ccnavi --lint                                # 実運用と同じ設定を見る
ccnavi --lint --rules .ccnavi/config/common/next.yml # 入れ替える前のファイルを見る
ccnavi --lint --json                         # 同じ苦情を JSON で
ccnavi --lint --flow .ccnavi/approved/flows/i0001-01-01.yml # 子のフロー 1 本も確かめる
```

```
ccnavi: 設定を検証する
  ルール: /repo/.ccnavi/config/common/rules.yml
  deny の場所を戻す: enable
  コアファイルを守る: enable
  チケット制御: enable
  チケットの承認の経路を守る: enable
  モード: dry-run（この起動の環境から解決したもの）
warn: (mode): dry-run なので判定しても呼び出しに手を出さない
error: deny:no-message: 文面が無い。ルールは代わりに何をすべきかを言わなければならない
error: deny:both: glob と regex の両方がある。どちらで判定するのか決められない
error: ask[2]: id が無い。ルールには id を書いてください（記録と文面にこの名前が出る）
error 3 件、warn 1 件、info 0 件
```

先頭の数行は「何を見て検証したか」（「チケットの承認の経路を守る」はチケット制御が有効なときだけ）。モードは検証を起動した環境から
解決したもので、`.claude/settings.json`の`env`はセッションにしか渡らないため、どこから来たかを示す。
`error`が 1 件でもあれば終了コードは 1、warnだけなら 0。CIで失敗にする対象は`error`だけでよい。
ルールの読み込みは判定と同じ経路を使う。

何を拾うかの一覧（深刻度ごと）は [要件 2.10.1 設定の検証の指摘一覧](../requirements/diagnostics.md#2101-設定の検証の指摘一覧)。

## ルールファイルが読めないとき

組み込みの既定を使って判定を続ける。止まらない（止めると不正な設定を直す操作まで止まる）。根拠は [設計 5.6](../design/rules.md#56-ルールが読めないとき)。
既定に入っているのは取り返しの付かない操作だけ。`rm -rf`、`git push`、`git reset --hard`、認証情報の置き場、シェルからガード自身の設定への書き込み。
止めるのは書き込みの形だけで、場所の名前が出ただけの文は通す。既定を使ったことは、止めた回だけでなく通した回にも伝える。
不正なファイルの見つけ方は上の`--lint`。ルールファイルを直す手順は [設計 5.6](../design/rules.md#56-ルールが読めないとき) と [hooks.md](hooks.md)の「コアファイルを守る」。
