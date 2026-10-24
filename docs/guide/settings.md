---
title: 設定
type: guide
description: .claude/settings.json に書く hook の登録と env の例、置き場の一覧、環境変数の表。値と既定を引くためのページ
tags: [user-guide, settings, env, hook]
keywords: [設定, settings.json, env, 環境変数, hook, CCNAVI_MODE, CCNAVI_ENTRY_PATH, CCNAVI_RESTORE_IF_DENY, CCNAVI_GUARD_CORE_FILES, CCNAVI_TICKET_CONTROL, 置き場, 8 つのイベント, matcher, CLAUDE_PROJECT_DIR]
---

# 設定

設定は環境変数で、`.claude/settings.json` の `env` ブロックに書く（Claude Code の設定スキーマは
独自のキーを受け付けない）。hook には実行ファイルだけを登録すればよい。
導入スクリプトの打ち方は[導入と配布](install.md)にある。このページは、書かれる形と変数の意味を引くためのページである。

## settings.json の例

下の形は `sh scripts/ccnavi-setup.sh <ワークスペースルート>` が書く。手で書く人の参考に、全文を載せる。

```json
{
  "hooks": {
    "SessionStart": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "\"${CLAUDE_PROJECT_DIR}/${CCNAVI_ENTRY_PATH}\"", "timeout": 10 }
      ]},
      { "matcher": "", "hooks": [
        { "type": "command", "command": "sh \"${CLAUDE_PROJECT_DIR}/.ccnavi/scripts/ccnavi-session-start.sh\"", "timeout": 60 }
      ]}
    ],
    "UserPromptSubmit": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "\"${CLAUDE_PROJECT_DIR}/${CCNAVI_ENTRY_PATH}\"", "timeout": 10 }
      ]}
    ],
    "PreToolUse": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "\"${CLAUDE_PROJECT_DIR}/${CCNAVI_ENTRY_PATH}\"", "timeout": 10 }
      ]}
    ],
    "PostToolUse": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "\"${CLAUDE_PROJECT_DIR}/${CCNAVI_ENTRY_PATH}\"", "timeout": 10 }
      ]}
    ],
    "PostToolUseFailure": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "\"${CLAUDE_PROJECT_DIR}/${CCNAVI_ENTRY_PATH}\"", "timeout": 10 }
      ]}
    ],
    "Stop": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "\"${CLAUDE_PROJECT_DIR}/${CCNAVI_ENTRY_PATH}\"", "timeout": 10 }
      ]}
    ],
    "SubagentStart": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "\"${CLAUDE_PROJECT_DIR}/${CCNAVI_ENTRY_PATH}\"", "timeout": 10 }
      ]}
    ],
    "SubagentStop": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "\"${CLAUDE_PROJECT_DIR}/${CCNAVI_ENTRY_PATH}\"", "timeout": 10 }
      ]}
    ]
  },
  "env": {
    "CCNAVI_MODE": "dry-run",
    "CCNAVI_ENTRY_PATH": ".ccnavi/scripts/ccnavi-launcher.sh",
    "CCNAVI_RESTORE_IF_DENY": "dry-run",
    "CCNAVI_GUARD_CORE_FILES": "dry-run",
    "CCNAVI_GUARD_TICKET_APPROVAL": "enable",
    "CCNAVI_GUARD_UNWATCHED": "enable",
    "CCNAVI_TICKET_CONTROL": "enable"
  }
}
```

`command` を `${CCNAVI_ENTRY_PATH}` で書くのは、守る対象と起動する実体を 1 か所にまとめるため。
その代わり、`env` からこの 1 行が消えると hook が起動しなくなる（[つまずきやすい点](troubleshooting.md)）。

## hook の 8 つのイベント

同じ実行ファイルを上の 8 つのイベントに登録する。payload にイベント名が入っているので、どれを走らせるかは ccnavi が選ぶ。
`matcher` は絞らない。絞ると、書かなかったツールで hook 自体が起動しなくなる。
`SessionStart` の 2 本目は入口の sh（`ccnavi-session-start.sh`。中で `ccnavi-fetch.sh` と、直下のディレクトリ一覧の `ccnavi-dirs.sh` を順に呼ぶ）で、承認済みチケットと
マーカー（親ブランチに乗って届く）と、ワークツリーの起点になる統合先ブランチを fast-forward で
取ってくる。登録しないと、別の機械で承認したものが反映されず、古い `main` からブランチを切ることになる。

登録しないと何が欠けるかは次のとおり。各イベントで ccnavi が何をするかは[起動と設定の設計](../design/startup-config.md)の「hook のイベント」にある。

| イベント | 登録しないと |
|---|---|
| `SessionStart` | 実行ファイルが差し替えられても戻せない。モデルが、ccnavi の決まりと、チケットを起こす時点を知らないまま進む。承認済みチケットの要求が上位の設定に却下されていても知らされない。ドキュメントの索引が新しくならず、引き方の案内も出ない。古い記録の後始末も走らない |
| `UserPromptSubmit` | ターンの終わりの報告が出ない（コミットに入った変更も見えない） |
| `PreToolUse` | 判定そのものが働かない |
| `PostToolUse` | 引数に現れない書き込みを取りこぼす。フェーズの終わりが伝わらない。frontmatter の無い md を新しく作っても、書き方の案内が出ない |
| `PostToolUseFailure` | 失敗の連続を数えられず、案内が出ない |
| `Stop` | 変更がユーザの目に触れない。閉じ忘れたチケットが作業中に残る。同じ呼び出しを繰り返し止めたことがユーザに伝わらない。ターンの終わりの促しが届かない |
| `SubagentStart` | サブエージェントが自分の範囲を知らずに始める |
| `SubagentStop` | 範囲外のコミット済みの変更が親に届く |

判定だけを見るなら `PreToolUse` と `PostToolUse` の 2 つでも動く。

## セッション開始で渡される作業の決まり

`SessionStart` で渡す作業の決まりは、`.ccnavi/CCNAVI.md` の本文で、起動・再開・compact・clear のたびに、メインのセッションにだけ届く。
ccnavi の動作に依存する決まりと考え方なので、ワークスペースの `CLAUDE.md` に同じことを書かなくてよい。導入スクリプトが `.ccnavi/docs/`（詳しい決まり）と一緒に配る。

md の構成は、考え方（コンテキストを清潔に保つ・判断するのはユーザ・並行する他セッションに影響しない）、作業の決まり
（ワークツリー・git の入口・下書きの置き場・保護対象の直し方・`projects/`・`status`・`--docs`）、チケット制御を使っているときの
作業の進め方（直接作業とチケット作業の使い分け）。詳しいことは名指しした入口（`--help`、拒否の案内、`status`、`--docs`）に聞く。

md の書き方（条件 `ccnavi:if` / `ccnavi:else`、差し込み `{{名前}}`）と、条件の足し方は、配布先にも配る [`.ccnavi/docs/ccnavi-md.md`](../../.ccnavi/docs/ccnavi-md.md) に書いてある。条件の判定の仕組みは [開発の入口](../development.md) にある。
ccnavi の実行ファイル側で条件を足す手順は[開発の入口](../development.md)にある。

## 置き場

置き場は固定で、環境変数では動かない。

| 置き場 | 場所 |
|---|---|
| プロジェクト | `projects/`（ワークスペースルートの直下。直下で `.git` を持つディレクトリがプロジェクトになる） |
| ccnavi ディレクトリ | `.ccnavi/`（各 git プロジェクトルートの直下。その下の `config/project/{rules,phases,risks}.yml` が 1 つのレイヤーの 3 本、`config/common/` が共通レイヤー（ミラー）、`scripts/` が配点の `script:` の置き場） |
| 提案 | `wip/proposals/`（各ツリーのルートの直下。そのツリーの git が追跡する） |
| 承認済みチケットとフェーズのマーカー | `.ccnavi/approved/`（各ツリーのルートの直下。そのツリーの git が追跡し、親チケットのブランチに乗って他の機械へ届く） |
| 判定の記録 | `logs/decisions.jsonl`（ワークスペースルートの下） |
| state | `logs/state/`（ワークスペースルートの下。実行後チェックの記録と、促しの記録・実行中の印・サブエージェントの終了結果・直接作業の候補などの一時の印。Stop の AI 判定の計画・試行・propose の操作実績も、ここに置く。ホストへの送信の操作記録 `ops/` もここに置き、prune は消さない） |
| Stop の AI 判定の記録 | `logs/stop-review/`（試行のイベント `events.jsonl`、判定に渡した材料の保存 `material/`、独立した評価 `labels.jsonl`。`decisions.jsonl` には書かない） |

`projects/` のプロジェクトの作業も、ワークスペースルートで Claude Code を起動して `cd` で入る。プロジェクトの中で起動すると
`CLAUDE_PROJECT_DIR` がプロジェクトになり、ワークスペースの `.claude/settings.json` が読まれず、ワークスペースの ccnavi の hook は動かない
（プロジェクトが自前の `.claude/` を持っていれば、その hook とスキルが使われる）。

置き場は環境変数で動かせない。「記録しない」「state を保存しない」「プロジェクトを数えない」も指定できない。
プロジェクトを数えたくなければ `projects/` を作らない。診断に限るフラグの理由などは[環境変数の内部の挙動](../design/startup-config.md)にある。
置き場の全項目は[置き場の設計](../design/multi-repo/places.md)にある。

## 環境変数

| 変数 | 意味 |
|---|---|
| `CCNAVI_MODE` | `enable`（既定）、`dry-run`、`disable`。違いは[hook の挙動](hooks.md)の「動作モード」 |
| `CCNAVI_LOG_ROTATE_MB` | 記録をローテートする大きさ（MB）。既定は `10`。`0` でローテートしない。`1` より小さい値は既定で動く（[記録](records.md)の「記録の後始末」） |
| `CCNAVI_LOG_KEEP_DAYS` | ローテートした記録を残す日数。既定は `14`。`0` で消さない。`1` より小さい値は既定で動く |
| `CCNAVI_STATE_KEEP_DAYS` | 終わったセッションの記録を残す日数。既定は `14`。`0` で消さない。`1` より小さい値は既定で動く |
| `CCNAVI_RESTORE_IF_DENY` | `enable`（既定）、`dry-run`、`disable`。`deny` と宣言した場所が副作用で変わったとき、git から戻すか。`dry-run` は戻さずに「戻すはずだった」と知らせる。横で作業する記録が有効なメインのチェックアウトの変更は、`enable` でも戻さず報告するだけにする |
| `CCNAVI_GUARD_CORE_FILES` | `enable`（既定）、`dry-run`、`disable`。ccnavi が動くために要るファイルを守るか。書き込みを止める側と、バックアップして戻す側の両方が切り替わる。承認済みの置き場と操作記録への書き込み、保護済みの sh への環境変数の指定は、この値に関係なく止める |
| `CCNAVI_STOP_REVIEW` | `dry-run`（既定）、`enable`、`disable`。Stop の AI 判定の効き方。全体のモードと掛け合わせる（全体が dry-run なら dry-run、disable なら disable）。`dry-run` は記録だけで止めず、`enable` は検証に通った不備検出だけを差し戻す。登録は `scripts/ccnavi-stop-review.sh` で行う（[Stop の AI 判定](records.md)） |
| `CCNAVI_STOP_REVIEW_AGENT` | `1` のとき、Stop で判定計画を作る。agent hook を登録したワークスペースに置かれる（`scripts/ccnavi-stop-review.sh register` が書く）。無ければ何も作らない。評価の集計は `ccnavi stop-review report` |
| `CCNAVI_STOP_REVIEW_KEEP_DAYS` | 判定の再評価用の材料（`logs/stop-review/`）を残す日数。既定は `90`。`0` にすると消さない |
| `CCNAVI_DENY_REPEAT` | 同じ理由で同じ呼び出しを何回止めたら、拒否の文面に「言い換えずにユーザに相談する」一文を足し、ターンの終わりにユーザへ報告するか。既定は `3`。2 未満と読めない値は既定に戻る。判定は変わらない（[ルールの書き方](rules.md)の「同じ呼び出しを繰り返し止めたとき」） |
| `CCNAVI_FAIL_STREAK_COUNT` | ツール呼び出しの失敗か ccnavi の拒否が何回続けて起きたら、資料を引く案内（`--docs` で索引を引く）を出すか。既定は `3`。`0` なら数えも案内もしない。負数と読めない値は既定に戻る。判定は変わらない（[ルールの書き方](rules.md)の「失敗や拒否が続いたとき」） |
| `CCNAVI_GUARD_UNWATCHED` | `enable`（既定）、`disable`。ユーザにも classifier にも確認できないモード（`dontAsk` / `bypassPermissions`）で、ルールがどこも言及しない呼び出しを止めるか。`disable` なら判定を返さず、そのモードの取り決めに委ねる（[ルールが言及していない呼び出し](rules.md)）。`dry-run` は無く、それ以外の値は `enable` として動き、`--lint` が指摘する |
| `CCNAVI_ENTRY_PATH` | hook が起動する ccnavi 自身。指定すると守る対象に入る。既定は無い。導入スクリプトは `.ccnavi/scripts/ccnavi-launcher.sh`（振り分けの sh）と書き、実行ファイルは `.ccnavi/bin/<os>-<arch>/` に入る。無ければ PATH の `ccnavi` を使う（[導入と配布](install.md)）。拡張子は書かない。Windows の `.exe` は ccnavi が補う |
| `CCNAVI_TICKET_CONTROL` | `enable`（既定）、`disable`。チケット制御（提案の承認・承認済みチケットの範囲・フェーズの HITL ポイント・サブエージェントの制限）を使うか。`disable` なら `ccnavi user` の操作と `ticket` / `review` のサブコマンドは動かず、セッション開始の案内も出ず、VS Code 拡張の「チケット管理」も出ない。それ以外の値は `enable` として動き、`--lint` が error にする |
| `CCNAVI_GUARD_TICKET_APPROVAL` | `enable`（既定）、`disable`。チケットの承認の経路を守るか。enable なら、シェルから ccnavi の実行ファイルを `user …` の入口 / `ticket …` / `review …` 付きで打つ形と、`ccnavi-user-*.sh` を打つ形を止め（`DENY_TICKET_APPROVAL_CLI`）、`user agree` と `user reviewed` と `user close-early` と `user prune` と、書くとき（`--yes`）の `user handoff` は標準入力がターミナルであることを求める。エージェントのコマンド行にこの変数の名前を（読むだけの形のほかで）書く形と、`--guard-ticket-approval` に `enable` 以外を渡す形も同じ理由コードで止める（表示・検索の道具だけのコマンドは除く）。ターミナルを求めない入口（`ccnavi-user-decide.sh`・実行ファイルの `--yes`）に、変数・置換・glob で組み立てた引数や `xargs` で流し込む引数を渡す形も、読み切れないので同じ理由コードで止める。テストやターミナルの無い実行環境（CI など）で切る。`dry-run` は取らず、書かれていたら `enable` として扱い、`--lint` が error にする |
| `GITHUB_TOKEN` / `GITLAB_TOKEN` | レビューの依頼と確認がリモートを読み書きするときの認証。どちらが要るかは origin の URL で決まる |
| `CCNAVI_LEARNING` | 学びの蓄積を使うか。`enable` / `disable`、既定 `enable`。子チケットを閉じるときに気づきの記録（`wip/handoff/learnings/<子>.md`）を求め、親を閉じるときに気づきの処理の証跡を確かめる（[記録](records.md)の「学びの蓄積」） |
| `CCNAVI_LEARNING_SOURCES` | パターンの観測元の数が何以上なら提案に足りるか。既定 3 |
| `CCNAVI_LEARNING_NOTICE_COUNT` | 提案の候補が何件溜まったら SessionStart で知らせるか。既定 5 |
| `CCNAVI_FETCH_TIMEOUT` | セッション開始時の `ccnavi-fetch.sh` が、fetch 1 回を打ち切る秒数。既定は `15`。数字でない値は既定に戻る（[複数人・別 PC](multi-pc.md)） |
| `CCNAVI_FETCH_BUDGET` | 同じ sh が親のブランチを早送りする時間の枠（秒）。既定は `45`。数字でない値は既定に戻る |
| `CCNAVI_LOCK_WAIT` | `ccnavi-sync.sh` と write-and-push 系の sh が、他のセッションのロックを待つ秒数。既定は `120`。数字でない値は既定に戻る |
| `CCNAVI_SYNC_TIMEOUT` | `ccnavi-sync.sh` が、`ls-remote` と fetch 1 回を打ち切る秒数。既定は `60` |
| `CCNAVI_WRITE_AND_PUSH_TIMEOUT` | write-and-push 系の sh と子のワークツリーからの push が、push と `ls-remote` 1 回を打ち切る秒数。既定は `60` |
| `CCNAVI_WRITE_AND_PUSH_COMMIT_TIMEOUT` | write-and-push 系の sh が、コミット 1 回を打ち切る秒数。既定は `60` |
| `CCNAVI_PREFLIGHT_TIMEOUT` | 作業前の事前確認の 1 回の問い合わせを打ち切る秒数。既定は問い合わせが `15`、git の確認が `30`。指定すると両方がその値になる |
| `CCNAVI_PREFLIGHT_TOTAL` | 事前確認の全体の秒数。既定は `60`。過ぎたら残りの項目は確かめずに飛ばす |
| `CCNAVI_START_HOST_TIMEOUT` | `ccnavi-start.sh` が、ホスト（issue・MR）の問い合わせを打ち切る秒数。既定は `30` |
| `CCNAVI_START_GIT_TIMEOUT` | `ccnavi-start.sh` が、`ls-remote` を打ち切る秒数。既定は `30` |
| `CCNAVI_REVIEW_ACCOUNT_TIMEOUT` | レビューの sh が、ホストのアカウント名を調べるのを打ち切る秒数。既定は `15` |
| `CCNAVI_BRANCHES_HOST_TIMEOUT` | `ccnavi-branches.sh` が、ホストの問い合わせを打ち切る秒数。既定は `30` |
| `CCNAVI_WORKTREE_GIT_TIMEOUT` | `ccnavi-worktree.sh` が、origin の同名ブランチを調べる `ls-remote` を打ち切る秒数。既定は `30` |
| `CCNAVI_GIT_MAX_LINES` | `ccnavi-git.sh` が返す本文の行数の上限。既定は `40`。超えたぶんは `logs/` にだけ残る |
| `CCNAVI_GIT_FAIL_LINES` | `ccnavi-git.sh` が、失敗したときに返す末尾の行数。既定は `30` |
| `CCNAVI_GIT_KEEP_LOGS` | `ccnavi-git.sh` が残す記録の本数。既定は `50` |
| `CCNAVI_WORKSPACE` | ワークスペースルートの絶対パス。hook の外（ターミナルなど）から ccnavi の sh を打つとき、sh がルートを探せなければ渡す。ワークスペースルートの `.ccnavi/scripts/ccnavi-common.sh` が在るパスでなければ使われない |
| `CCNAVI_BRANCH_PREFIXES` | 親の識別子の先頭の語のリスト（カンマ区切り）。既定は `feature,hotfix,fix,bugfix,chore,refactor,docs`。`main` `master` `develop` `release` は使えない（[チケット](tickets.md)の「識別子とブランチ名」） |
| `CCNAVI_LOG_LEVEL` | 診断ログ（`logs/diag/`）に出すレベル。`DEBUG` `INFO` `WARN` `ERROR`（大文字小文字は問わない）。既定と読めない値は `INFO` |

上の秒数と行数の調整値は、`settings.json` の `env` で渡す。保護済みの sh を呼ぶコマンド行に前置きすると、`CCNAVI_GIT_MAX_LINES`・`CCNAVI_GIT_FAIL_LINES`・`CCNAVI_GIT_KEEP_LOGS`・`CCNAVI_FETCH_TIMEOUT` 以外は止まる。
`CCNAVI_LOCK_HELD`・`CCNAVI_PROPOSE_INNER`・`CCNAVI_PROPOSE_NOTE`・`CCNAVI_DOCTOR_*`・`CCNAVI_ROOT`・`CCNAVI_LOG_NAME` など、sh 同士やテストの受け渡しに使う変数は内部用で、ここには載せない。利用者は設定しない。

`CCNAVI_GUARD_TICKET_APPROVAL` のどの形を止めるかの細部は、[環境変数の内部の挙動](../design/startup-config.md)にある。

`${CLAUDE_PROJECT_DIR}` は hook の `command` では展開されるが `env` では展開されない。
`env` には相対パスを書く。値の変更はセッションを開き直すまで反映されない。

## ワークスペースルートの決まり方

ワークスペースルートは `CLAUDE_PROJECT_DIR`、無ければ作業ディレクトリから親へ辿って
`.claude` を探して決める。ワークツリー（`.claude/worktrees/<名前>`）は自分も `.claude` を
持つが、ワークスペースルートとは数えず飛ばして親へ辿る。

`CLAUDE_PROJECT_DIR` が無いターミナルや Bash ツールから、`.claude/` を持つプロジェクトの中で `ccnavi` を直接打つと、`--root` を渡さない限りそのプロジェクトが根と見なされる。`--root` を付けるか、sh のラッパーを通す。未解決の洗い出しは[単体 clone の洗い出し](../design/multi-repo/standalone-clone-gaps.md)にある。
