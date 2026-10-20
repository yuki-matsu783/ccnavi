---
type: design
title: 4. 起動と設定
description: hook としての起動の形、イベント、設定の解決、動作モード、応答の形、導入と配布、記録
tags: [design-doc, hook, config]
keywords: [起動, hook, イベント, 設定, 動作モード, dry-run, 導入, 配布, 記録]
---

[設計書の入口に戻る](../design.md)

## 4. 起動と設定

### 4.1 起動の形

実行ファイルは 1 本で、`.claude/settings.json` の hook として 8 つのイベントに同じコマンドで登録する。
標準入力の JSON（payload）の `hook_event_name` でイベントを見分け、標準出力に応答を返して終わる。

| 起動 | 挙動 |
|---|---|
| hook から（payload あり） | イベントごとの手順（4.2）。終了コード 0 は通す、2 は止める・差し戻す |
| hook の入力なし | 使い方を標準エラーに出して終了コード 1 |
| 診断用のオプション（`--lint` `--test` `--test-expectations` `--explain`） | ユーザと VS Code 拡張が使う（10 章） |
| `user …`（`ccnavi user` の操作）/ `ticket …` / `review …` | チケット制御の操作。ユーザが端末で打つか、ボードの子プロセスか保護済みスクリプトが呼ぶ（9 章） |

ワークスペースルートは `--root` → 環境変数 `CLAUDE_PROJECT_DIR` → 作業ディレクトリから上へ辿って
`.claude/` を持つ最初のディレクトリ、の順で決める。ただし `.claude/worktrees/<名前>` の位置の
ディレクトリ（ワークツリー）は `.claude/` を持っていても飛ばす。

`CLAUDE_PROJECT_DIR` が無い端末や Bash ツールから、`.claude/` を持つプロジェクトの中で `ccnavi` を直接打つと、`--root` を渡さない限りそのプロジェクトが根と見なされる。`--root` を付けるか、sh のラッパーを通す。未解決で、洗い出しは `docs/design/multi-repo/standalone-clone-gaps.md` の A2。

配布物は PyInstaller の onedir。実行時の依存は PyYAML 1 本で、読むのは `safe_load` に限る。
呼び出しごとに起動して終わる。

### 4.2 hook のイベント

| イベント | すること | 応答 |
|---|---|---|
| `SessionStart` | 実行ファイルのバックアップを取る（設定ファイルは最初の発言で取る。8.3）。`additionalContextOnce` の記憶を捨てる。`source=startup` なら `match: Stop` の数えも捨てる（6.6）。`source=startup` と `resume` では、そのセッションの実行中のサブエージェントの印も捨てる（新しいプロセスで前の体は生きていない。compact と clear では捨てない。9.12）。サブエージェントでなければ、ワークスペースルートとプロジェクトの元ツリーに残った、終わっていない `integrate` の記録（`ccnavi-integrate/<名前>/marker`）を、区分 A〜D の行でモデルとユーザ（`systemMessage`）の両方に知らせる。記録が無ければ何も足さない。期限は hook の期限と開始から 2 秒のうち小さいほう（9.6）。サブエージェントでなければ、ccnavi が前提にしている作業の決まりと考え方を、`.ccnavi/CCNAVI.md` の本文として渡す。対象は、ワークツリー・`ccnavi-git.sh`・`wip/handoff/`・保護対象の直し方（`ccnavi-stage.sh`）・プロジェクトへの `cd`・サブエージェント・合意の要る変更・他セッションへの影響である。チケット制御が有効なら `ccnavi-ticket.sh status` も渡す。`hook/ccnavimd.py` が、条件 `<!-- ccnavi:if 名前 -->` と差し込み `{{名前}}` を解く。条件が成り立たないときの行は `<!-- ccnavi:else -->` で分ける。囲みの中は、深さ 1 段につき半角空白 2 つでインデントしてよく、載せるときに取る。名前ごとに独立した関数が真偽を返す。md が無いときは、無いことだけを 1 行で言う。書き方は `--lint` が `(ccnavi-md)` で見る。ワークツリーと git の行は、共通レイヤーと自身のレイヤーの deny に `workspace-root`・`raw-git` があるときだけ渡す。git の行は、生の git を組み込みで止めるとき（9.1）も渡す。索引の案内を出す回は、`--docs` で詳しく引くよう添える（REQ-SES-06）。チケット制御が有効なら、直接作業とチケット作業の使い分けも同じ md の条件 `tickets` で渡す（9.1）。cwd がプロジェクトの中なら、そのプロジェクトのスキルの目録を渡す（11.13）。サブエージェントでなければ、cwd のワークツリーに結び付く承認済みチケットの要求のうち、上位の設定（ルールの deny・親の範囲・フェーズ定義の上限）に却下されて効かないものを、要求・理由・出所と、件数と先頭 3 件で渡す。残りは `--explain` へ誘う。glob の重なりは近似なので、その断りを添える。ワークスペースルートからは、却下のあるチケットの件数だけ言う。`--explain` の節「■ 上位の設定に却下された要求」は全件を 1 件 1 行で出す。この 2 つで REQ-SES-02 を満たす。却下は記録に残さず、いまの承認済みチケットとルールから都度再計算する（承認はチケットの中身を変えない。ADR-0104）。判定は変えない。サブエージェントでなければ、ワークスペースとプロジェクトの md の frontmatter の索引を差分で新しくする。時間は 3 秒と hook の判定の期限の残りのうち小さいほうまでとする。`--docs` での引き方の 1 行を足す（書き方は、frontmatter の無い md を作った時点で `PostToolUse` が案内する。決まりの文書があれば `--docs --keyword frontmatter` で引くよう添える）。無視の行を足したツリー（その `.gitignore` に未コミットの差分が残る）、索引の対象外にしたツリー、書き換えなかった ccnavi-index.jsonl を渡す（10） | `additionalContext` |
| `UserPromptSubmit` | 保護領域にいまある変更を記録し、ターンの基準にする（7.4）。ユーザの発言なら、前のターンの終わりからの変更をユーザの手によるものとして受け入れる（設定ファイルのバックアップを今の状態に取り直し、`deny` の場所の今の変更を報告済みにする。バックグラウンド作業の完了通知では行わない。8.3、7.4）。取り直しに失敗したときは標準エラーと記録の `guarded` に残す。依頼文に issue・MR の指定があれば、着手の前に `ccnavi-start.sh` で紐づくブランチを探させる指示を渡す（候補が複数ならユーザに確かめる。9.13）。サブエージェントが終わったとき、子のワークツリーを読めず範囲外の変更を確かめられなかったことを示す印が残っていれば、次の依頼の前に「合流の前にそのワークツリーを確かめて」と伝える（9.12） | 無し。伝えることがあれば `additionalContext` |
| `PreToolUse` | 呼び出しを判定する（6 章）。コアファイルをバックアップする（8 章）。cwd がプロジェクトの中に入った最初の回に、そのスキルの目録を添える（11.13） | `permissionDecision` と `additionalContext` |
| `PostToolUse` | コアファイルをバックアップと突き合わせて戻す。作業ツリーを git で読み、保護領域の変更を報告し、設定に従って戻す（7 章）。チケットの状態を承認済みチケットへ書き出し、フェーズの終わりを告げる（9.8）。サブエージェントが差し戻しを無視して終わったことを親に言う。Write で新しく作った md に frontmatter か `type` が無ければ、書き方を 1 度だけ案内する（止めない。`NOTICE_DOC_FRONTMATTER`。10） | 終了コード 2 と標準エラー、または `additionalContext` |
| `PostToolUseFailure` | 失敗した呼び出しも、`PostToolUse` と同じに設定ファイルのバックアップとの突き合わせと実行後チェックを行い、結果を `additionalContext` で渡す（失敗する前に変えていることがあり、通さないと次の発言で受け入れられる。8.3）。ツール自体の失敗を数える（`failstreak`）。ユーザの中断（`is_interrupt`）は数えない。連続が `CCNAVI_FAIL_STREAK_COUNT` 回（既定 3。0 で無効）に達した回に、資料の索引（`--docs`）を引く案内を返す。止められないイベント。成功（`PostToolUse`）で連続を 0 に戻し、案内は連続の中で 1 回だけ。`PreToolUse` の `deny`（`dry-run` でも）も同じ連続に数え、案内は拒否の文面の末尾に付く。記録は `state/failstreak-<セッション>-<main\|エージェント>.json` | `additionalContext` |
| `Stop` | このターンで変わった保護領域をユーザへ報告する（7.4）。agent 型 Stop hook（判定役）を登録したワークスペースでは、既存の促しを実行したあとに AI 判定の計画を書き、判定役が差し戻しの返り値を返すことがある（[stop-review.md](stop-review.md)。setup は登録しない）。動かしている作業（payload の `background_tasks`・`session_crons`）が残っているかを `state/busy-<セッション>.json` に記録する。残っていなければ消す。起動と再開でも消す。残っていれば、次の発言での受け入れ（8.3、7.4）はしない。メインエージェントの cwd のワークツリーのチケットが、作業を終えたように見えるのに `finish` されていなければ、1 回だけ止めて促す（9.6）。促さなかった回で、レビューの依頼までの道の途中にあれば、1 回だけ止めて次の一手を促す（`NUDGE_TICKET_REQUEST`。実行中のサブエージェントが居る間は止めない。9.6・9.12）。途中とは、承認済みの親に作業中の子が残っている、または子が全部閉じたのに依頼のマーカーが無い状態のことである。どちらも促さなかった回は、メインエージェントの cwd か、このセッションが書き込んだ（候補の記録 `direct-<セッション>.json`）、チケットに結び付かない直接作業のワークツリー（`.claude/worktrees/<名前>`）に未コミットの変更か取り込み先に入っていないコミットがあり、取り込み中の marker も無ければ（チケット制御の有無は問わない）、1 回だけ止めて `integrate` の打ち方と、一時ワークツリーに展開した取り込み結果をユーザに確かめてもらう手順を促す（`NUDGE_INTEGRATE`。6.6）。どれも促さなかった回は、`match: Stop` のルールが渡す回なら止めてその文を渡す（6.6） | `systemMessage`。止めるときは `decision: block` と `reason` も |
| `SubagentStart` | 承認済みで開いている子チケットの一覧を渡す（9.12）。cwd がプロジェクトの中なら、そのプロジェクトのスキルの目録を渡す（11.13）。気づいたスキル候補は最後の報告に節を足して返すよう 1 行渡す（6.6）。`agent_id` があれば（チケット制御の有無によらず）、実行中の印を置き、同じ作業ツリーで編集する別のサブエージェントが動いていればその案内も渡す（9.12） | `additionalContext` |
| `SubagentStop` | 子のワークツリーの差分を読み、範囲外の変更（スクリプト経由など、`PreToolUse` が事前に止められなかったもの）が残っていれば 1 回だけ差し戻す。ワークツリーを読めず確かめられなかったときは、親へ伝える印を置く（9.12）。差し戻さない回に実行中の印を外す（9.12）。終わり方を `ok`・`fixed`・`outside`・`unchecked`・`unattributed` に分けて `state/subagent-<セッション>-<id>.outcome` に残す。担当の子が決まるのは cwd が子のときだけで、親のツリーからの起動は差し戻さず親へ知らせる（9.12） | 終了コード 2 と標準エラー |
| その他 | 何もせず通す。記録に `event-not-checked` | 無し |

`matcher` は全ツールにする（絞ると書かなかったツールで hook が起動しない）。同じイベントの他の hook は並行して順不同で走るので、
各 hook はそれだけで成立させる。

### 4.3 設定の解決

設定は環境変数 `CCNAVI_*` で渡す。プロジェクトは `.claude/settings.json` の `env` ブロックに書く。読む順は
プロセス環境 → `ccnavi.settings.local.json`（ccnavi 自身のソースツリーでだけ読む、1 人の上書き）→
コマンドラインのオプション。変数の一覧と意味は README「設定」。

`disable` は起動側の環境で指定したときしか有効にならない（REQ-CMN-04）。`ccnavi.settings.local.json` の `disable` は無視して理由を出す。
`.claude/settings.json` の `env` の `disable` はプロセス環境と区別できないので、書く経路を閉じる。`.claude/settings.json` は
コアファイル（8 章）である。`Write` / `Edit` はルールの `deny` が、シェルからの書き込みは組み込みが止め、それでも書かれたら実行後にバックアップから戻す。
`env` は再読み込みされないので、書けても同じセッションでは反映されない。書かれていれば `--lint` が error で名指しし、
導入スクリプトは `--mode disable` を断る。

読めない値は報告して `enable` として扱う。

### 4.4 動作モード

| `CCNAVI_MODE` | 挙動 |
|---|---|
| `enable`（既定） | 判定し、`deny` なら止め、`ask` ならユーザに確認を出す |
| `dry-run` | 同じ判定を行い、一般の保護は呼び出しに手を出さず「`enable` なら何をしていたか」を `additionalContext` で伝える。チケットの関門と補助の保護は、`dry-run` でも `enable` と同じく止める（6.8） |
| `disable` | 判定しない。記録に `mode-disabled` を残す |

戻す働きの 2 つ（`CCNAVI_RESTORE_IF_DENY`、`CCNAVI_GUARD_CORE_FILES`）も同じ 3 値を取り、`CCNAVI_MODE=dry-run` のときは
こちらが `enable` でも `dry-run` として振る舞う。組み合わせの表は [要件 2.2](../requirements/common.md)。

Stop の AI 判定の効き方 `CCNAVI_STOP_REVIEW` も同じ 3 値で、`CCNAVI_MODE` に掛かる（全体が `dry-run` なら `dry-run`、
`disable` なら `disable`）。例外は読めない値の扱いで、他の 3 値と違い `enable` ではなく `dry-run` として動く（差し戻しを
既定で有効にしないため）。起動するかどうかは設定でなく、agent 型 Stop hook の登録で決まる（`scripts/ccnavi-stop-review.sh`）。

`CCNAVI_GUARD_TICKET_APPROVAL` と `CCNAVI_TICKET_CONTROL`、`CCNAVI_GUARD_UNWATCHED` は `enable` / `disable` の 2 値。
`CCNAVI_LEARNING`（学びの蓄積。9.16）も 2 値で、`enable` のときだけ効く。書き誤りは `--lint` が warn で言い、`disable` として動く。

### 4.5 応答の形と結果

| 場合 | 応答 |
|---|---|
| `enable` の deny / ask | `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny\|ask", "permissionDecisionReason": "…", "additionalContext": "…"}}`。`additionalContext` はルールが文を持つときだけ |
| `enable` の allow、委ねた回 | 判定は出さない。通知（既定を使った、コアファイルを戻した、ルールの文）があれば `additionalContext` だけ |
| `dry-run` の deny / ask | 一般の保護は、`additionalContext` に「enable なら止めていた / 聞いていた」と理由を並べる。終了コード 0。関門と補助の保護は `enable` と同じ応答で止め、理由の末尾に「dry-run でもこの止めは効く」の 1 行を添える |
| `Stop` | `{"systemMessage": "…"}`。`finish`・レビュー依頼までの道・直接作業の `integrate` を促すときは、モードによらず（`dry-run` でも）`{"decision": "block", "reason": "…", "systemMessage": "…"}`。Stop でモデルに文を届ける経路は止めることしか無いため |
| `PostToolUse` / `SubagentStop` の差し戻し | 標準エラーに文、終了コード 2。`dry-run` なら `additionalContext` で 0 |

期限は `PreToolUse` で 3 秒。ルール照合の途中で超えたら `deadline-exceeded` として、`enable` は
終了コード 2。`dry-run` は、設定を読まずに安全と分かる読み取り（`hook.failsafe`）だけ 0 で、そのほかのツールの呼び出しは `dry-run` でも止める（REQ-CMN-08）。この block は理由を返せないので、生の文字列に `>` が 50 個を超えて並ぶ入力は、シェルから書き込む形の組み込みの保護だけを当てずに、ほかのルールの `deny` と `ask` を当てたあとで、`deny` に当たっていなければ `DENY_REDIRECT_LIMIT` に上げて理由と書き直し方を返す（付録 A）。この保護の照合は `>` の個数 × その後ろの空白の無い語の長さで伸びるので、上限で `>` の個数を抑える。上限の下でも尾が長い入力（400KB 程度）は期限に届きうる。実ルールでの実測では、`*x*` 形の glob の照合が長いと、期限の確認が間に合わずに 3 秒を超える（[12.1](limits.md)）。外側で打ち切られた場合には手が出ない。`PostToolUse` に期限は無く、
git の読み取りが 2 秒で打ち切られる。

payload が JSON でない・オブジェクトでない・`hook_event_name` が無いとき、`hook_event_name`・`tool_name`・
`session_id`・`cwd`・`permission_mode`・`tool_use_id` のどれかが文字列でないとき、`tool_input` がオブジェクトでないとき、
その中の判定が読む欄（`command`・`file_path` など。`hookio.TOOL_INPUT_TEXT_FIELDS`）が文字列でないときは `payload-unusable` で
同じ結果になる。記録を書けないときは標準エラーに出して捨て、判定は変えない。UTF-8 にできない字（JSON の
`"\ud800"` のような孤立したサロゲート）は、記録では `\ud800` の 6 字にして書く。期限切れと `payload-unusable` の文は、
先頭に `[ccnavi] <理由>` を置き、次の一手（期限切れはコマンドを分ける・長い文はファイルで渡す、`payload-unusable` は
書き直しでは直らないので hook の登録と版をユーザに確かめてもらう）を添える。文はモードとイベントに合わせる。`dry-run` のうち、ツールの呼び出しを持たないイベントは
終了コード 0 で通すので「止めた」と言わず、`enable` なら止めていたと言う。`PreToolUse` とイベントすら読めなかった回は `dry-run` でも止める。`payload-unusable` の終了コード 2 が何を止め、
標準エラーが誰に届くかはイベントで違うので、読めたところまでのイベント名で文を分ける（`PreToolUse` は呼び出し、
`PostToolUse` は走ったあとの確認、`UserPromptSubmit` はユーザの発言、`Stop` / `SubagentStop` は終わり）。イベント名を
読めなかった回とほかのイベントは、何を止めたかを言わずに直す側だけを名指しする。記録の `event` も読めたぶんを残す。
期限を見るのは `PreToolUse` だけなので、期限切れの文はツール呼び出しについて言う。

**内部エラー。** hook として起動された回の処理（設定の読み込み、モードの解決、payload の読み取り、イベントの処理、記録、
診断ログ）が ccnavi 自身の想定外の例外で終わったら、その回は `internal-error` で判定に
至らなかった回として記録し（`detail` に例外の型と場所）、診断ログに error で 1 行残す。記録を書く途中で
落ちた回は、記録を重ねて試さない。例外を抜けさせると
Python は終了コード 1 で終わり、Claude Code は 0 と 2 以外を止める扱いにしないので、`PreToolUse` の呼び出しが
記録も無く通ってしまう。途中まで書いた標準出力は捨て、イベントごとに次のように終える。文には `[ccnavi] internal-error`、
例外の型、診断ログ（`logs/diag/ccnavi.log`）の場所を載せ、モデル宛ての文には「繰り返さずユーザに伝える」を添える。

| イベント | 終え方 |
|---|---|
| `PreToolUse`、payload を読む前（イベントが分からない） | 標準エラーに文、終了コード 2 で止める。ツールの種類で決め、Write・Edit・NotebookEdit・Agent とツールが分からない回は `dry-run` でも止める。Read・Grep・Glob は組み込みの最低限の deny（認証情報の形・`.git` の中・`logs/state/ops/`）に当たらなければ通し、Bash は狭い許可リスト（`pwd`・`ls`・`cat` / `head` / `tail` / `wc` の単独の 1 パス・`ccnavi-ticket.sh status` / `--help`）だけ通す。PowerShell は止める。未知のツールは `enable` だけ止める |
| `PostToolUse` | ツールはもう走っていて止めるものは無い。`enable` は終了コード 2 と標準エラーで、実行後の検査をしていないことをモデルに言う。`dry-run` は 0 |
| それ以外（`SessionStart`・`UserPromptSubmit`・`Stop`・`SubagentStart`・`SubagentStop`・未知のイベント） | 終了コード 0 で、ユーザに `systemMessage` で知らせる。終了コード 2 で終えると、`Stop` と `SubagentStop` は終われずに続けさせられ、`UserPromptSubmit` は依頼文を消す。ccnavi の不具合が作業そのものを止めないようにする。`SubagentStop` は、子の範囲を確かめられなかった回と同じ印を置き、親に届ける（9.12） |

### 4.6 導入と配布

`scripts/ccnavi-setup.sh <対象>` が、対象プロジェクトに設定を書き、道具を配る。何度打っても同じ形に
なる。既にある値と、ccnavi と関係のない hook はそのまま残す。

| 書くもの | 中身 |
|---|---|
| `.claude/settings.json` の `env` | `CCNAVI_MODE` / `CCNAVI_BIN_PATH` / `CCNAVI_RESTORE_IF_DENY` / `CCNAVI_GUARD_CORE_FILES` / `CCNAVI_GUARD_TICKET_APPROVAL` / `CCNAVI_GUARD_UNWATCHED` / `CCNAVI_TICKET_CONTROL`。`--all` は受けるが、今は足すものが無い。置き場の env は書かない（置き場は固定。残っていても読まない） |
| `.claude/settings.json` の `hooks` | 8 つのイベントに実行ファイルを登録する。既に別の表記で登録されていれば足さずに名前を挙げる |
| `.vscode/settings.json` | `git.detectWorktrees: true` と `git.repositoryScanMaxDepth: 2`。足りないキーだけを足す。`--no-vscode` で触らない |
| 配るもの | `dist/ccnavi/` の中身を `.ccnavi/bin/<os>-<arch>/` へ、設定 3 本のひな形、`.ccnavi/scripts/ccnavi-{ticket,review,git,common,fetch,sync,clean,branches,start,stage,worktree,launcher}.sh`、ユーザ専用の `ccnavi-user-{agree,push-approved,close-early,decide,clean-branch,lib-review}.sh`、共通部の部品 `ccnavi-common-{state,lock,write-and-push,host,log,review}.sh` と `ccnavi-clean.js`。取り込み（`ccnavi-fetch.sh`）は `SessionStart` に別の 1 行で登録する（`--no-fetch` で外す）。配布先に既にあるものは、`--force` のときだけ入れ替える。振り分けの sh は配った回に実行ビットを付け、配らなかった回でも外れていれば付け直す（`--no-deploy` の回と、配布元と配布先が同じ回には触らない）。使い方の文書は配布元の `.ccnavi/docs/*.md` を全部 `.ccnavi/docs/` へ配る（`--docs` の索引はここだけ `.ccnavi/` の下でも載せる。配布先の既にあるものは `--force` のときだけ入れ替える）。配布元に `.ccnavi/docs/` が無ければ「配布元に無くて配れないもの」に挙げる。共通の作業・レビュー計画のテンプレートは配布元の `.ccnavi/config/common/templates/*.yml` を 1 本ずつ同じ場所へ配る（既にあるものは `--force` のときだけ入れ替える。配布元に置き場や `*.yml` が無ければ何もせず、何も言わない）。共通の工程スキルは、配布元の `.claude/skills/<名前>/SKILL.md` の frontmatter に `process: true` を持つものについて、`SKILL.md` と `references/common/` の下の `.md` を同じ場所へ 1 ファイルずつ配る（`.DS_Store` や `ccnavi-index.jsonl` のような生成物は配らない。`process: true` の読み方は親チケットの着手が写す判定に寄せ、CRLF の改行と、行末の `# コメント`は許し、閉じの `---` が無いものは配らない。配布先の道にディレクトリでないものがあるときは、そのファイルだけ配らずに挙げる）（既にあるものは `--force` のときだけ入れ替える。親チケットの着手がプロジェクトへ写す元になる。`.ccnavi/docs/skill-processes.md`）。配布元に該当するスキルが無ければ何もせず、何も言わない |
| 配布先の `.gitignore` | 配った機械の置き場 `/.ccnavi/bin/<os>-<arch>/` の 1 行と、`--docs` の索引の `**/ccnavi-index.jsonl` の 1 行と、使い捨ての置き場 `/scratchpad` の 1 行。索引の行と `/scratchpad/` の行はそれぞれ別の見出しの下に入り、`/scratchpad` は同等以上の `/scratchpad/`・`scratchpad/`・`scratchpad` が既にあれば足さない。`scratchpad` を否定する行（`!/scratchpad` など）があればユーザの除外として足さない。`ccnavi-index.jsonl` の行が既にあれば足さない。`ccnavi-index.jsonl` を否定する行があればユーザの除外として足さない。どちらも配布先が git のリポジトリで、配るときだけ。振り分けの sh は追跡する側に置く。`projects/` の下のプロジェクトの `.gitignore` には、`--docs` が索引を書く前に足す（10） |

置き場は 2 つに分けて固定する。

```
.ccnavi/scripts/ccnavi-launcher.sh      ← CCNAVI_BIN_PATH が指す振り分けの sh（追跡する）
.ccnavi/scripts/ccnavi-ticket.sh など   ← 代わりに通る sh
.ccnavi/bin/darwin-arm64/ccnavi         ← 機械ごとの組み立て（_internal/ も同じ置き場。無視する）
.ccnavi/bin/linux-x86_64/ccnavi
.ccnavi/bin/windows-x86_64/ccnavi.exe
```

hook の `command` は振り分けの sh を指す 1 行で、どの実行ファイルを起動するかは起動した機械が決める。置き場の名前は
`build.py` が書く `dist/ccnavi.target` の `<os>-<arch>` で、語は `src/ccnavi/infra/platformtag.py`・`.ccnavi/scripts/ccnavi-launcher.sh`・
`scripts/ccnavi-setup.sh` の `host_target` の 3 か所で揃える。sh は ccnavi のリポジトリでも配布先でも同じパスに置き、
ccnavi のリポジトリの hook も同じ sh を通る。

sh の探し方: `here=${0%/*}`（`$0` に `/` が無ければ `.`）から `bin_dir=$here/../bin` を作り、`uname -sm` を 1 回読んで
`<os>-<arch>` を決める。arm64 の macOS と Windows は、自分向けが無いときだけ x86_64 へ回る。語ごとに `ccnavi` →
`ccnavi.exe` の順に `[ -f ]` を見て、見つかったものへ `exec` で引数と標準入力を渡す。

- 隣（`.ccnavi/scripts/<os>-<arch>/`）は探さない。自己防衛のパス（8.2）が当たらない置き場になるため
- `bin_dir` は `..` を含むまま使い、正規化しない。シンボリックリンクも解かない
- 見つからなければ終了コード 127 で、文面に `<os>-<arch>` と探した `bin_dir` を出す。在るが実行できなければ `exec` が失敗して 126

hook は sh を直に起動するので、sh に実行ビットが要る。ccnavi のリポジトリでは追跡するモードを 100755 にする。配布先では
導入スクリプトが付け、付いていなければ `--lint` が error で言う（10 章）。

ccnavi のリポジトリでの組み立て: `build.py` はまず `git rev-parse HEAD`（未コミットの変更があれば `-dirty` を付ける）を
`build/stamp/ccnavi_buildinfo.py` に書き、PyInstaller に一緒にバンドルさせる（`--version` の `commit`。パッケージの外に置く）。次に PyInstaller の出力を `dist/ccnavi/` に入れ替え、`dist/ccnavi.target` を
書いたあと、`install()` で `dist/ccnavi/` を `.ccnavi/bin/<os>-<arch>/` へコピーする（`dist/` は導入スクリプトの配布元で、
代わりに通る sh が env の無いときに探す先でもあるので残す）。隣の `<os>-<arch>.new` にコピーし切ってから `_swap` で入れ替える。
`_swap` は置き場を `.old` へ退避してから新しいほうを移す（Windows でも走っている実行ファイルの名前は変えられる。付録 C）。
失敗したら 0.3 秒おきに 5 回までやり直し、やり直しきれなければ `.old` を置き場に戻してから投げる。`install()` が失敗したら
`build.py` は 1 を返し、`dist/` は新しく、`.ccnavi/bin/<os>-<arch>/` は前のまま、と言う。

- rename 2 回の間（数 ms）に来た hook は sh が 127 で終わり、その 1 回は判定が走らない
- ワークツリーで組み立てるとコピー先はそのワークツリーの `.ccnavi/bin/` なので、走っている hook（ワークスペースルートの sh）は変わらない
- 組み立て直すと、セッション開始で取った実行ファイルのバックアップ（8.3）と食い違う。ツール呼び出しの外で変わった版は、`enable` なら次のツール呼び出しの前に基準へ取り込む（`selfguard._rebase_if_changed`）。そのあとは、
  ツール呼び出しの中で変わった版だけを、呼び出しの直前の版に戻す（`selfguard._check_heavy`）。戻す前に、書かれていた版は `state/aside/` に退避する。`dry-run` は取り込まず、「このツール呼び出しで変わった」と言う

既定でないパス: 導入スクリプトは、`.claude/settings.json` にいま書かれている `CCNAVI_BIN_PATH` で動きを決める。

| いまの `CCNAVI_BIN_PATH` | 動き |
|---|---|
| 無い | `.ccnavi/scripts/ccnavi-launcher.sh` を書く |
| `.ccnavi/scripts/ccnavi-launcher.sh` | 何もしない |
| それ以外（ユーザが決めたパス） | 書き換えず、名指しで 1 行出す。導入は止めず、終了コードも変えない。`--check` では揃っていない側に数える。「値が違う env」の一覧には入れない |

`--mode disable` は断る（4.3）。`--check` は書かずに揃っていないところだけを並べ、揃っていなければ終了コード 1。
名指しした `--deploy` が組み立てられていないか、目印が無くて置き場を決められなければ終了コード 2 で断る。ただし、
既定の配布元が使えないだけなら理由を 1 行出して設定は書く。別の機械向けの組み立ては、その機械の置き場へ配り、
この機械で動くものが無いことを言う。仕様は `tests/sh/test_setup.py` と `tests/sh/test_launcher.py` が固定している。

### 4.7 記録

ツール呼び出しは、通したものも判定しなかったものも 1 件 1 行の JSON として
`logs/decisions.jsonl`（固定）に追記する。1 行の欄は付録 B。判定しなかった回も残すので、記録が無ければ ccnavi が動かなかったと読める。

記録に書く `subject` / `unwrapped` / `detail` は、書く直前に秘密の形を伏せる（`src/ccnavi/records/redact.py`）。伏せるのは
記録だけで、判定は伏せる前の文字列で下す。記録が 10 MB を超えたら `decisions.<日時>.jsonl` へローテートし、ローテートした
記録と終わったセッションの記録（`logs/state/`）は 14 日で消す（Stop の AI 判定の計画・試行・操作の記録はセッションを名前に持たないので、それ自身の日付で 14 日、再評価用の材料とイベントの記録は別の保持日数 `CCNAVI_STOP_REVIEW_KEEP_DAYS`＝既定 90 日）。走るのはセッション開始と `ccnavi user prune` で、
記録はセッションごとにまとめて、どれかが保持日数のうちに書かれていれば全部残す
（`src/ccnavi/records/prune.py`）。`<セッション>.json`（改名前の記録）と `<セッション>.reported.json` と読むのは UUID の形の名前だけで、知らない名前のファイルと、
リンクになった置き場（`logs/state` そのものと `selfguard/`）には触らない。操作記録の `ops/` も消さない（14 日で消す対象に入らない）。しきい値は 0 のほか 1 MB・1 日より
小さい値と有限でない値を受けず、既定で動く。ローテート先は `O_EXCL` で先に押さえてから名前を変える。
伏せる前にも 4000 字（上限の 4 倍）で切り、伏せる手間が実行前チェックの期限に届かないようにする。「記録が無ければ動かなかった」は、ローテートした分と合わせて読む。

| 欄 | 数えるもの |
|---|---|
| `decision` = `allow` / `ask` / `deny` / `handover` / `nudge` / `skip` | 下した判定。`handover` は権限モードに委ねた回。`nudge` は `Stop` で `finish` を促した回と、`match: Stop` のルールで止めた回 |
| `enforced` | 実際に適用したか。`dry-run` は、一般の保護では偽、関門と補助の保護で止めた回は真 |
| `code` | 判定の根拠の種別（付録 A） |
| `reason` | `skip` の理由 |
| `degraded` | コマンドを読み切れず生の文字列に当てたかどうか |
| `fallback` | ルールを読めず組み込みの既定で判定したかどうか |
| `paths` / `detail` | 実行後チェックが拾った変更と件数 |
