---
type: design
title: 4. 起動と設定
description: hook としての起動の形、イベント、設定の解決、動作モード、応答の形、導入と配布、記録
tags: [design-doc, hook, config]
keywords: [起動, hook, イベント, 設定, 動作モード, dry-run, 導入, 配布, 記録]
---

[設計書の入口に戻る](../20_design.md)

## 4. 起動と設定

### 4.1 起動の形

実行ファイルは 1 本で、`.claude/settings.json` の hook として 8 つのイベントに同じコマンドで登録する。
標準入力の JSON（payload）の `hook_event_name` でイベントを見分け、標準出力に応答を返して終わる。

| 起動 | 挙動 |
|---|---|
| hook から（payload あり） | イベントごとの手順（4.2）。終了コード 0 は通す、2 は止める・差し戻す |
| hook の入力なし | 使い方を標準エラーに出して終了コード 1 |
| 診断用のオプション（`--lint` `--test` `--test-expectations` `--explain`） | ユーザと VS Code 拡張が使う（10 章） |
| `user …`（`ccnavi user` の操作）/ `ticket …` / `review …` | チケット制御の操作。ユーザがターミナルで打つか、ボードの子プロセスか保護済みスクリプトが呼ぶ（9 章） |

ワークスペースルートは `--root` → 環境変数 `CLAUDE_PROJECT_DIR` → 作業ディレクトリから上へ辿って
`.claude/` を持つ最初のディレクトリ、の順で決める。ただし `.claude/worktrees/<名前>` の位置の
ディレクトリ（ワークツリー）は `.claude/` を持っていても飛ばす。

`CLAUDE_PROJECT_DIR` が無いターミナルや Bash ツールから、`.claude/` を持つプロジェクトの中で `ccnavi` を直接打つと、`--root` を渡さない限りそのプロジェクトが根と見なされる。`--root` を付けるか、sh のラッパーを通す。未解決で、洗い出しは `docs/21_design/multi-repo/standalone-clone-gaps.md` の A2。

配布物は PyInstaller の onedir。実行時の依存は PyYAML 1 本で、読むのは `safe_load` に限る。
呼び出しごとに起動して終わる。

### 4.2 hook のイベント

| イベント | すること | 応答 |
|---|---|---|
| `SessionStart` | 実行ファイルのバックアップを取る（設定ファイルは最初の発言で取る。8.3）。`additionalContextOnce` の記憶（渡した回の数え）を、起動・再開・compact・clear のどの回でも破棄する（古いセッションの分も消す。`every` の刻みも 0 から数え直す）。`source=startup` なら `match: Stop` の数えも破棄する（6.6）。`source=startup` と `resume` では、そのセッションの実行中のサブエージェントの印と、動かしている作業の印（`state/busy-<セッション>.json`）と、ターン中の印（`state/turn-<セッション>.json`）も破棄する（新しいプロセスで前の体は生きていない。compact と clear では破棄しない。9.12）。`source=startup` では、起動時に在る保護領域の変更を実行後チェックの基準として記録する（7.4）。どの回でも、記録のローテートと、古い記録・終わったセッションの記録の削除（prune）を行う（失敗しても開始は止めない）。サブエージェントでなければ、ワークスペースルートとプロジェクトの元ツリーに残った、終わっていない `integrate` の記録（`ccnavi-integrate/<名前>/marker`）を、版 4 の記録は続きの一手の行で、それ以外は「版を知らない壊れた記録」として、モデルとユーザ（`systemMessage`）の両方に知らせる。記録が無ければ何も足さない。期限は hook の期限と開始から 2 秒のうち小さいほう（9.6）。サブエージェントでなければ、ccnavi が前提にしている作業の決まりと考え方を、`.ccnavi/CCNAVI.md` の本文として渡す。対象は、ワークツリー・`ccnavi-git.sh`・`wip/handoff/`・保護対象の直し方（`ccnavi-stage.sh`）・プロジェクトへの `cd`・サブエージェント・合意の要る変更・他セッションへの影響である。チケット制御が有効なら `ccnavi-ticket.sh status` も渡す。`hook/ccnavimd.py` が、条件 `<!-- ccnavi:if 名前 -->` と差し込み `{{名前}}` を解く。条件が成り立たないときの行は `<!-- ccnavi:else -->` で分ける。囲みの中は、深さ 1 段につき半角空白 2 つでインデントしてよく、載せるときに取る。名前ごとに独立した関数が真偽を返す。md が無いときは、無いことだけを 1 行で言う。書き方は `--lint` が `(ccnavi-md)` で見る。ワークツリーと git の行は、共通レイヤーと自身のレイヤーの deny に `workspace-root`・`raw-git` があるときだけ渡す。git の行は、生の git を組み込みで止めるとき（9.1）も渡す。索引の案内を出す回は、`--docs` で詳しく引くよう添える（REQ-SES-06）。チケット制御が有効なら、直接作業とチケット作業の使い分けも同じ md の条件 `tickets` で渡す（9.1）。cwd がプロジェクトの中なら、そのプロジェクトのスキルの目録を渡す（11.13）。チケット制御が有効で、サブエージェントでなければ、cwd のワークツリーに結び付く承認済みチケットの要求のうち、上位の設定（ルールの deny・親の範囲・フェーズ定義の上限）に却下されて効かないものを、要求・理由・出所と、件数と先頭 3 件で渡す。残りは `--explain` へ誘う。glob の重なりは近似なので、その断りを添える。ワークスペースルートからは、却下のあるチケットの件数だけ言う。`--explain` の節「■ 上位の設定に却下された要求」は全件を 1 件 1 行で出す。この 2 つで REQ-SES-02 を満たす。却下は記録に残さず、いまの承認済みチケットとルールから都度再計算する（承認はチケットの中身を変えないので、承認時の結果を固定せず、ルールが変われば結果も変わる）。判定は変えない。この却下の段落（ワークスペースルートでは件数の行）は `systemMessage` にも載せる。サブエージェントでなければ、このPCで作業中の親を 1 行ずつ渡す（チケット制御が有効なとき。親・ブランチ・MR・次の子・未送信。読めなくても開始は止めない）。学びの蓄積が有効なら、提案の候補が新しく現れた回だけ、ユーザにだけ（`systemMessage`。モデルの文脈には入れない）知らせる。サブエージェントでなければ、ワークスペースとプロジェクトの md の frontmatter の索引を差分で新しくする。時間は 3 秒と hook の判定の期限の残りのうち小さいほうまでとする。`--docs` での引き方の 1 行を足す（書き方は、frontmatter の無い md を作った時点で `PostToolUse` が案内する。決まりの文書があれば `--docs --keyword frontmatter` で引くよう添える）。無視の行を足したツリー（その `.gitignore` に未コミットの差分が残る）、索引の対象外にしたツリー、書き換えなかった ccnavi-index.jsonl を渡す（10） | `additionalContext`。却下・終わっていない `integrate`・学びの提案は `systemMessage` にも |
| `UserPromptSubmit` | ターン中の印を置く（ユーザの発言でもバックグラウンド作業の完了通知でも置く。8.4）。保護領域にいまある変更を記録し、ターンの基準にする（7.4）。ユーザの発言なら、まず共通レイヤーと工程スキルを、着手済みの親のワークツリーのプロジェクトへコピー（`promptsync`。差分が無ければ何も読み書きしない。11.12）、そのあと、前のターンの終わりからの変更をユーザの手によるものとして受け入れる（設定ファイルのバックアップを今の状態に取り直し、`deny` の場所の今の変更を報告済みにする。バックグラウンド作業の完了通知では行わない。8.3、7.4）。取り直しに失敗したときは標準エラーと記録の `guarded` に残す。依頼文に issue・MR の指定があれば、実装の依頼なら `ccnavi-start.sh --check` と `ccnavi-start.sh` を打たせ（出力の「次:」に従う）、説明・調査だけなら答えてよい、という短い指示を渡す（コードブロック・インラインコード・語の一部の番号は拾わない。9.13）。サブエージェントが終わったとき、子のワークツリーを読めず範囲外の変更を確かめられなかったことを示す印が残っていれば、次の依頼の前に「合流の前にそのワークツリーを確かめて」と伝える（9.12） | 無し。伝えることがあれば `additionalContext` |
| `PreToolUse` | ターン中の印の更新時刻を新しくする（`PostToolUse`・`PostToolUseFailure` も同じ。印が無ければ作らない。8.4）。呼び出しを判定する（6 章）。コアファイルをバックアップする（8 章）。cwd がプロジェクトの中に入った最初の回に、そのスキルの目録を添える（11.13） | `permissionDecision` と `additionalContext` |
| `PostToolUse` | コアファイルをバックアップと突き合わせて戻す。ワークツリーを git で読み、保護領域の変更を報告し、設定に従って戻す（7 章）。チケットの状態を承認済みチケットへ書き出し、フェーズの終わりを告げる（9.8）。サブエージェントが差し戻しを無視して終わったことを親に言う。Write で新しく作った md に frontmatter か `type` が無ければ、書き方を 1 度だけ案内する（止めない。`NOTICE_DOC_FRONTMATTER`。10）。親の提案を Write で新しく手書きして書式を誤っていれば（`parent:` を書いた、`plan:`・`phases:`・`mr:` が無い）、書いた時点で 1 度だけ案内する（止めない。`NOTICE_PROPOSAL_PARENT_KEY`・`NOTICE_PROPOSAL_PARENT_FIELDS`。付録 A）。直接作業の候補を登録する（git は呼ばない） | 終了コード 2 と標準エラー、または `additionalContext` |
| `PostToolUseFailure` | メインエージェントのユーザの中断（`is_interrupt`）では、ターン中の印を消す（中断では `Stop` が来ないことがある。8.4）。失敗した呼び出しも、`PostToolUse` と同じに設定ファイルのバックアップとの突き合わせと実行後チェックを行い、結果を `additionalContext` で渡す（失敗する前に変えていることがあり、通さないと次の発言で受け入れられる。8.3）。ツール自体の失敗を数える（`failstreak`）。ユーザの中断（`is_interrupt`）は数えない。連続が `CCNAVI_FAIL_STREAK_COUNT` 回（既定 3。0 で無効）に達した回に、資料の索引（`--docs`）を引く案内を返す。止められないイベント。成功（`PostToolUse`）で連続を 0 に戻し、案内は連続の中で 1 回だけ。`PreToolUse` の `deny`（`dry-run` でも）も同じ連続に数え、案内は拒否の文面の末尾に付く。記録は `state/failstreak-<セッション>-<main\|エージェント>.json` | `additionalContext` |
| `Stop` | ターン中の印を消す（8.4）。このターンで変わった保護領域をユーザへ報告する（7.4）。同じ理由で繰り返し止めた呼び出しが `CCNAVI_DENY_REPEAT` 回（既定 3）に達していれば、言い換えで回っているかもしれないことをユーザに 1 度言う。agent型Stop hook（判定役）を登録したワークスペースでは、既存の促しを実行したあとにAI判定の計画を書き、判定役が差し戻しの返り値を返すことがある（[stop-review.md](stop-review.md)。setupは登録しない）。動かしている作業（payloadの `background_tasks`・`session_crons`）が残っているかを `state/busy-<セッション>.json` に記録する。残っていなければ消す。起動と再開でも消す。残っていれば、次の発言での受け入れ（8.3、7.4）はしない。メインエージェントのcwdのワークツリーのチケットが、作業を終えたように見えるのに `finish` されていなければ、1 回だけ止めて促す（9.6）。促さなかった回で、レビューの依頼までの道の途中にあれば、1 回だけ止めて次の一手を促す（`NUDGE_TICKET_REQUEST`。実行中のサブエージェントが居る間は止めない。9.6・9.12）。途中とは、承認済みの親に作業中の子が残っている、または子が全部閉じたのに依頼のマーカーが無い状態のことである。どちらも促さなかった回で、終わったサブエージェントが範囲外の変更を残した・範囲を確かめられなかった（`outside`・`unchecked`）ときは、同じ結果では 1 回だけ止めて促す（`NUDGE_SUBAGENT_OUTCOME`。チケット制御が有効なときだけ。実行中のサブエージェントが居る間と、サブエージェントの中では止めない。9.12）。これらのどれも促さなかった回は、メインエージェントのcwdか、このセッションが書き込んだ（候補の記録 `direct-<セッション>.json`）、チケットに結び付かない直接作業のワークツリー（`.claude/worktrees/<名前>`）に未コミットの変更か取り込み先に入っていないコミットがあり、取り込み中のmarkerも無ければ（チケット制御の有無は問わない）、1 回だけ止めて `integrate` の打ち方と、一時ワークツリーに展開した取り込み結果をユーザに確かめてもらう手順を促す（`NUDGE_INTEGRATE`。6.6）。どれも促さなかった回は、`match: Stop` のルールが渡す回なら止めてその文を渡す（6.6） | `systemMessage`。止めるときは `decision: block` と `reason` も |
| `SubagentStart` | 承認済みで開いている子チケットの一覧を渡す（9.12）。cwd がプロジェクトの中なら、そのプロジェクトのスキルの目録を渡す（11.13）。気づいたスキル候補は最後の報告に節を足して返すよう 1 行渡す（6.6）。`agent_id` があれば（チケット制御の有無によらず）、実行中の印を置き、同じワークツリーで編集する別のサブエージェントが動いていればその案内も渡す（9.12） | `additionalContext` |
| `SubagentStop` | 子のワークツリーの差分を読み、範囲外の変更（スクリプト経由など、`PreToolUse` が事前に止められなかったもの）が残っていれば 1 回だけ差し戻す。ワークツリーを読めず確かめられなかったときは、親へ伝える印を置く（9.12）。差し戻さない回に実行中の印を外す（9.12）。終わり方を `ok`・`fixed`・`outside`・`unchecked`・`unattributed` に分けて `state/subagent-<セッション>-<id>.outcome` に残す。担当の子が決まるのは cwd が子のときだけで、親のツリーからの起動は差し戻さず親へ知らせる（9.12） | 終了コード 2 と標準エラー |
| その他 | 何もせず通す。記録に `event-not-checked` | 無し |

`matcher` は全ツールにする（絞ると書かなかったツールで hook が起動しない）。同じイベントの他の hook は並行して順不同で走るので、
各 hook はそれだけで成立させる。

### 4.2.1 放置したセッションの引き継ぎ促し

会話が大きく育ったまま放置されると、プロンプトキャッシュが時間切れで消え、続きの送り直しの費用が増える。消える前に、次のセッションへの引き継ぎ文書を書かせる。判定は `records/idlehandoff.py`、入口は `entry/cli_idle.py`（`ccnavi idle notice|start|check|reset`）、待機は `.ccnavi/scripts/ccnavi-idle.sh`（`notice`・`start`・`reset`）が受け持つ。導入スクリプトは、本体とは別の 3 行として登録する（`--no-idle` で外す）。

| 登録 | 呼ぶもの | すること |
|---|---|---|
| `Stop`（同期、上限 10 秒） | `ccnavi-idle.sh notice` → `ccnavi idle notice` | 会話の大きさが促す大きさ以上なら、事情と勧めをユーザに説明する `systemMessage` を、1 セッションに 1 回だけ返す（`<state>/idle/<セッション>.notice` が済みの印） |
| `Stop`（`asyncRewake`、上限 3600 秒） | `ccnavi-idle.sh start` → `ccnavi idle start` | 促す大きさ以上なら状態を `waiting`（ランダムな印つき）にして待ち時間を返し、sh が `sleep` する。起きたら `ccnavi idle check` に聞き、`fire` なら続く行を標準エラーに出して終了コード 2 で終わる（asyncRewake がセッションを起こし、標準エラーが指示になる） |
| `UserPromptSubmit`（同期、上限 10 秒） | `ccnavi-idle.sh reset` → `ccnavi idle reset` | 状態を `active` に戻す。起こすときの指示（目印 `[idle-handoff]`）とバックグラウンド作業の完了通知では戻さない |

- 会話の大きさは、トランスクリプトの末尾から、サブエージェントでない最新の応答の `usage`（`input_tokens` + `cache_read_input_tokens` + `cache_creation_input_tokens`）を足して読む。読めなければ促さない。
- キャッシュの保持時間は、直近でキャッシュを書き込んだ応答の `cache_creation` の内訳で見分ける（5 分側に書き込みがあれば 5 分、無ければ 1 時間）。待ち時間は、1 時間なら 55 分、5 分なら 4 分。`CCNAVI_HANDOFF_WAIT_SECONDS` が正の整数ならその秒数にする（確かめ用）。
- 促す大きさは `基準 + CCNAVI_HANDOFF_SUGGEST_TOKENS`（既定 400000、0 で無効）。基準は 0 から始まり、引き継ぎを書かせた（`check` が `fire` を返した）ときの会話の大きさに進む。会話が基準より小さくなったら 0 に戻る。
- 状態は `<state>/idle/<セッション>.json`（`state`・`token`・`due`・`base`）。`fired` の間は、ユーザが打つまで `start` は待機を始めない。古い待機は、印が合わないので何もせず終わる。7 日より古いファイルは `start` が消す。
- 次のときは何もしない: 見ている人がいない実行（`CLAUDE_CODE_SESSION_ATTENDED=0`、`CLAUDE_CODE_ENTRYPOINT` が `sdk` で始まる）、サブエージェントの呼び出し（`agent_id` がある）、`Stop` 以外のイベント、使えないセッション識別子。どの動詞も終了コード 0 で終わる（`ccnavi-idle.sh start` が起こすときの 2 を除く）。

### 4.3 設定の解決

設定は環境変数 `CCNAVI_*` で渡す。プロジェクトは `.claude/settings.json` の `env` ブロックに書く。読む順は
プロセス環境 → `ccnavi.settings.local.json`（ccnavi 自身のソースツリーでだけ読む、1 人の上書き）→
コマンドラインのオプション。変数の一覧と意味は[利用者向けの設定の手引き](../41_guide/settings.md)、内部の挙動は 4.3.1。

`disable` は起動側の環境で指定したときしか有効にならない（REQ-CMN-04）。`ccnavi.settings.local.json` の `disable` は無視して理由を出す。
`.claude/settings.json` の `env` の `disable` はプロセス環境と区別できないので、書く経路を閉じる。`.claude/settings.json` は
コアファイル（8 章）である。`Write` / `Edit` はルールの `deny` が、シェルからの書き込みは組み込みが止め、それでも書かれたら実行後にバックアップから戻す。
`env` は再読み込みされないので、書けても同じセッションでは反映されない。書かれていれば `--lint` が error で名指しし、
導入スクリプトは `--mode disable` を断る。

読めない値は報告して `enable` として扱う。

### 4.3.1 環境変数の内部の挙動

利用者向けの表（[設定の手引き](../41_guide/settings.md)）に書かなかった、変数ごとの内部の挙動。

- `CCNAVI_RESTORE_IF_DENY`: 横で作業する記録が有効なユーザのチェックアウトの変更は、`enable` でも戻さず報告するだけにする。
- `CCNAVI_GUARD_CORE_FILES`: 書き込みを止める側と、バックアップして戻す側の両方が切り替わる。承認済みの置き場と操作記録への書き込み、
  保護済みの sh への環境変数の指定の止めは、この値では切れない。
- `CCNAVI_GUARD_UNWATCHED`: ユーザにも classifier にも確認できないモード（`dontAsk` / `bypassPermissions`）で、ルールがどこも言及しない呼び出しを止めるか。
  `disable` なら判定を返さず、そのモードの取り決めに委ねる（読み切れなかった呼び出しは委ねない）。`dry-run` は無く、
  それ以外の値は `enable` として動き、`--lint` が指摘する。
- `CCNAVI_TICKET_CONTROL`: チケット制御（提案の承認・承認済みチケットの範囲・フェーズの HITL ポイント・サブエージェントの制限）を使うか。
  全体ルールは全プロジェクトが使い、チケットまで使うかをここで決める。`disable` なら `ccnavi user` の操作と `ticket` / `review` の
  サブコマンドは動かず、セッション開始の案内も出ず、VS Code 拡張の「チケット管理」も出ない。それ以外の値は `enable` として動き、`--lint` が error にする。
- `CCNAVI_GUARD_TICKET_APPROVAL`: enable なら、シェルから ccnavi の実行ファイルを `user …` の入口 / `ticket …` / `review …` 付きで打つ形と、
  `ccnavi-user-*.sh` を打つ形を止める（`DENY_TICKET_APPROVAL_CLI`）。
  `user agree` と `user reviewed` と `user close-early` と `user prune` と、書くとき（`--yes`）の `user handoff` と `user mirror` は標準入力がターミナルであることを求める。
  エージェントのコマンド行にこの変数の名前を（読むだけの形のほかで）書く形と、`--guard-ticket-approval` に `enable` 以外を渡す形も
  同じ理由コードで止める（表示・検索の道具だけのコマンドは除く）。ターミナルを求めない入口（`ccnavi-user-decide.sh`・実行ファイルの `--yes`）に、
  変数・置換・glob で組み立てた引数や `xargs` で流し込む引数を渡す形も、読み切れないので同じ理由コードで止める。
  テストやターミナルの無い実行環境（CI など）で切る。`dry-run` は取らず、書かれていたら `enable` として扱い、`--lint` が error にする。
- `CCNAVI_STOP_REVIEW`: Stop の AI 判定（agent 型 Stop hook）の効き方。全体のモードと掛け合わせる（4.4）。登録は `scripts/ccnavi-stop-review.sh` で行う（構成は [stop-review.md](stop-review.md)）。
- `CCNAVI_STOP_REVIEW_AGENT`: `1` のとき、Stop で判定計画を作る。agent hook を登録したワークスペースに置かれる（`scripts/ccnavi-stop-review.sh register` が書く）。
  無ければ何も作らない。評価の集計は `ccnavi stop-review report`。
- `CCNAVI_ENTRY_PATH`: hook が起動する ccnavi 自身。指定すると守る対象に入る。既定は無い（導入スクリプトは振り分けの sh
  `.ccnavi/scripts/ccnavi-launcher.sh` と書き、実行ファイルは `.ccnavi/bin/<os>-<arch>/` に入る。4.6）。拡張子は書かない。Windows の `.exe` は ccnavi が補う。
  hook の `command` を `${CCNAVI_ENTRY_PATH}` で書くのは、守る対象と起動する実体を 1 か所にまとめるため（shell form なので環境変数は shell が展開する）。
  その代わり、`env` からこの 1 行が消えると hook が起動しなくなる。
- `GITHUB_TOKEN` / `GITLAB_TOKEN`: レビューの依頼と確認がリモートを読み書きするときの認証。どちらが要るかは origin の URL で決まる。

**置き場を動かすフラグ。** 置き場は固定で、環境変数では動かない。別の場所を指せるのはフラグ（`--log` / `--state` / `--approved` / `--tickets` / `--projects` /
`--project-home`）だけで、hook からは渡らない。共通レイヤーの置き場（`.ccnavi/config/common/`）は `--project-home` で
ccnavi ディレクトリを動かしても動かず、別の場所を指せるのは `--rules` / `--phases` / `--risk` のフラグだけ。
置き場を動かすこれらのフラグ（`--projects` / `--project-home` を含む）は、どれも診断（`--lint` / `--test` / `--test-expectations` / `--explain`）に限る。
hook からの判定と `ticket` / `review` などのサブコマンドに渡すと無視し、標準エラーに出す。`.ccnavi/scripts/` の sh は受け取った
引数を実行ファイルへそのまま渡すので、診断の外でも効くと、記録を止める・置き場を差し替えるといった形でサブコマンドを動かせてしまうため。

置き場を動かす環境変数は無い。「記録しない」「state を保存しない」「プロジェクトを数えない」も指定できない。プロジェクトを数えたくなければ
`projects/` を作らない。

### 4.4 動作モード

| `CCNAVI_MODE` | 挙動 |
|---|---|
| `enable`（既定） | 判定し、`deny` なら止め、`ask` ならユーザに確認を出す |
| `dry-run` | 同じ判定を行い、一般の保護は呼び出しに手を出さず「`enable` なら何をしていたか」を `additionalContext` で伝える。チケットの関門と補助の保護は、`dry-run` でも `enable` と同じく止める（6.8） |
| `disable` | 判定しない。記録に `mode-disabled` を残す |

戻す働きの 2 つ（`CCNAVI_RESTORE_IF_DENY`、`CCNAVI_GUARD_CORE_FILES`）も同じ 3 値を取り、`CCNAVI_MODE=dry-run` のときは
こちらが `enable` でも `dry-run` として振る舞う。組み合わせの表は [要件 2.2](../11_requirements/common.md)。

Stop の AI 判定の効き方 `CCNAVI_STOP_REVIEW` も同じ 3 値で、`CCNAVI_MODE` に掛かる（全体が `dry-run` なら `dry-run`、
`disable` なら `disable`）。例外は読めない値の扱いで、他の 3 値と違い `enable` ではなく `dry-run` として動く（差し戻しを
既定で有効にしないため）。起動するかどうかは設定でなく、agent 型 Stop hook の登録で決まる（`scripts/ccnavi-stop-review.sh`）。

`CCNAVI_GUARD_TICKET_APPROVAL` と `CCNAVI_TICKET_CONTROL`、`CCNAVI_GUARD_UNWATCHED` は `enable` / `disable` の 2 値。
設定が読めずチケット制御の値が分からないときは、チケット制御は有効とみなす（REQ-CMN-13。判定できないときの扱いは下の表）。
`CCNAVI_LEARNING`（学びの蓄積。9.16）も 2 値で、`enable` のときだけ効く。書き誤りは `--lint` が warn で言い、`enable` として動く（既定も `enable`）。

### 4.5 応答の形と結果

| 場合 | 応答 |
|---|---|
| `enable` の deny / ask | `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny\|ask", "permissionDecisionReason": "…", "additionalContext": "…"}}`。`additionalContext` はルールが文を持つときだけ |
| `enable` の allow、委ねた回 | 判定は出さない。通知（既定を使った、コアファイルを戻した、ルールの文）があれば `additionalContext` だけ |
| `dry-run` の deny / ask | 一般の保護は、`additionalContext` に「enable なら止めていた / 聞いていた」と理由を並べる。終了コード 0。関門と補助の保護は `enable` と同じ応答で止め、理由の末尾に「dry-run でもこの止めは効く」の 1 行を添える |
| `Stop` | `{"systemMessage": "…"}`。`finish`・レビュー依頼までの道・サブエージェント結果・直接作業の `integrate`・`match: Stop` のルールを促すときは、モードによらず（`dry-run` でも）`{"decision": "block", "reason": "…", "systemMessage": "…"}`。Stop でモデルに文を届ける経路は止めることしか無いため |
| `PostToolUse` / `SubagentStop` の差し戻し | 標準エラーに文、終了コード 2。`dry-run` なら `additionalContext` で 0 |

期限は `PreToolUse` で 3 秒。ルール照合の途中で超えたら `deadline-exceeded` として、`enable` は
終了コード 2。`dry-run` は、設定を読まずに安全と分かる読み取り（`hook.failsafe`）だけ 0 で、そのほかのツールの呼び出しは `dry-run` でも止める（REQ-CMN-08）。この block は理由を返せないので、生の文字列に `>` が 50 個を超えて並ぶ入力は、シェルから書き込む形の組み込みの保護だけを適用せずに、ほかのルールの `deny` と `ask` を適用したあとで、`deny` にヒットしていなければ `DENY_REDIRECT_LIMIT` に上げて理由と書き直し方を返す（付録 A）。この保護の照合は `>` の個数 × その後ろの空白の無い語の長さで伸びるので、上限で `>` の個数を抑える。上限の下でも尾が長い入力（400KB 程度）は期限に届きうる。実ルールでの実測では、`*x*` 形の glob の照合が長いと、期限の確認が間に合わずに 3 秒を超える（[12.1](limits.md)）。外側で打ち切られた場合には手が出ない。`PostToolUse` に期限は無く、
git の読み取りが 2 秒で打ち切られる。

payload が JSON でない・オブジェクトでない・`hook_event_name` が無いとき、`hook_event_name`・`tool_name`・
`session_id`・`cwd`・`permission_mode`・`tool_use_id` のどれかが文字列でないとき、`tool_input` がオブジェクトでないとき、
その中の判定が読む欄（`command`・`file_path` など。`hookio.TOOL_INPUT_TEXT_FIELDS`）が文字列でないときは `payload-unusable` で
同じ結果になる。記録を書けないときは標準エラーに出して破棄し、判定は変えない。UTF-8 にできない字（JSON の
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
記録も無く通ってしまう。途中まで書いた標準出力は破棄し、イベントごとに次のように終える。文には `[ccnavi] internal-error`、
例外の型、診断ログ（`logs/diag/ccnavi.log`）の場所を載せ、モデル宛ての文には「繰り返さずユーザに伝える」を添える。

| イベント | 終え方 |
|---|---|
| `PreToolUse`、payload を読む前（イベントが分からない）、または判定中の内部例外 | 標準エラーに文、終了コード 2 で止める。ツールの種類で決め、Write・Edit・NotebookEdit・Agent・Task とツールが分からない回は `dry-run` でも止める。サブエージェントの報告用のツール（`SubagentHandback`・`StructuredOutput`。`modes.REPORT_TOOLS`）はモードによらず通す（体が報告を返せなくなるのを避ける）。Read・Grep・Glob は組み込みの最低限の deny（認証情報の形・`.git` の中・`logs/state/ops/`）にヒットしなければ通し、Bash は狭い許可リスト（`pwd`・`ls`・`cat` / `head` / `tail` / `wc` の単独の 1 パス・`ccnavi-ticket.sh status` / `--help`）だけ通す。PowerShell は止める。未知のツールは `enable` だけ止める |
| `PostToolUse` | ツールはもう走っていて止めるものは無い。`enable` は終了コード 2 と標準エラーで、実行後の検査をしていないことをモデルに言う。`dry-run` は 0 |
| それ以外（`SessionStart`・`UserPromptSubmit`・`Stop`・`SubagentStart`・`SubagentStop`・未知のイベント） | 終了コード 0 で、ユーザに `systemMessage` で知らせる。終了コード 2 で終えると、`Stop` と `SubagentStop` は終われずに続けさせられ、`UserPromptSubmit` は依頼文を消す。ccnavi の不具合が作業そのものを止めないようにする。`SubagentStop` は、子の範囲を確かめられなかった回と同じ印を置き、親に届ける（9.12） |

### 4.6 導入と配布

`scripts/ccnavi-setup.sh <対象>` が、対象プロジェクトに設定を書き、道具を配る。何度打っても同じ形に
なる。既にある値と、ccnavi と関係のない hook はそのまま残す。

| 書くもの | 中身 |
|---|---|
| `.claude/settings.json` の `env` | `CCNAVI_MODE` / `CCNAVI_ENTRY_PATH` / `CCNAVI_RESTORE_IF_DENY` / `CCNAVI_GUARD_CORE_FILES` / `CCNAVI_GUARD_TICKET_APPROVAL` / `CCNAVI_GUARD_UNWATCHED` / `CCNAVI_TICKET_CONTROL`。`--all` は受けるが、今は足すものが無い。置き場の env は書かない（置き場は固定。残っていても読まない） |
| `.claude/settings.json` の `hooks` | 8 つのイベントに実行ファイルを登録する。既に別の表記で登録されていれば足さずに名前を挙げる |
| `.vscode/settings.json` | `git.detectWorktrees: true` と `git.repositoryScanMaxDepth: 2`。足りないキーだけを足す。`--no-vscode` で触らない |
| 配るもの | `dist/ccnavi/` の中身を `.ccnavi/bin/<os>-<arch>/` へ、設定 3 本のひな形、`.ccnavi/scripts/ccnavi-{ticket,review,git,common,fetch,dirs,idle,session-start,sync,clean,branches,start,stage,worktree,launcher}.sh`、ユーザ専用の `ccnavi-user-{agree,push-approved,close-early,decide,clean-branch,lib-review}.sh`、共通部の部品 `ccnavi-common-{state,lock,write-and-push,host,log,review}.sh` と `ccnavi-clean.js`。入口（`ccnavi-session-start.sh`。`ccnavi-fetch.sh` と `ccnavi-dirs.sh` を順に呼ぶ）は `SessionStart` に別の 1 行で登録する（`--no-fetch` で外す）。配布先に既にあるものは、`--force` のときだけ入れ替える。振り分けの sh は配った回に実行ビットを付け、配らなかった回でも外れていれば付け直す（`--no-deploy` の回と、配布元と配布先が同じ回には触らない）。使い方の文書は配布元の `.ccnavi/docs/*.md` を全部 `.ccnavi/docs/` へ配る（`--docs` の索引はここだけ `.ccnavi/` の下でも載せる。配布先の既にあるものは `--force` のときだけ入れ替える）。配布元に `.ccnavi/docs/` が無ければ「配布元に無くて配れないもの」に挙げる。共通の作業・レビュー計画のテンプレートは配布元の `.ccnavi/config/common/templates/*.yml` を 1 本ずつ同じ場所へ配る（既にあるものは `--force` のときだけ入れ替える。配布元に置き場や `*.yml` が無ければ何もせず、何も言わない）。共通の工程スキルは、配布元の `.claude/skills/<名前>/SKILL.md` の frontmatter に `process: true` を持つものについて、`SKILL.md` と `references/common/` の下の `.md` を同じ場所へ 1 ファイルずつ配る（`.DS_Store` や `ccnavi-index.jsonl` のような生成物は配らない。`process: true` の読み方はユーザの発言のときの同期がコピーする判定に寄せ、CRLF の改行と、行末の `# コメント`は許し、閉じの `---` が無いものは配らない。配布先の道にディレクトリでないものがあるときは、そのファイルだけ配らずに挙げる）（既にあるものは `--force` のときだけ入れ替える。ユーザの発言のときの同期がプロジェクトへコピーする元になる。`.ccnavi/docs/skill-processes.md`）。配布元に該当するスキルが無ければ何もせず、何も言わない |
| 配布先の `.gitignore` | 配った機械の置き場 `/.ccnavi/bin/<os>-<arch>/` の 1 行と、`--docs` の索引の `**/ccnavi-index.jsonl` の 1 行と、使い捨ての置き場 `/scratchpad` の 1 行と、実行時の記録の置き場 `/logs/` の 1 行と、ワークツリーの置き場 `/.claude/worktrees/` の 1 行（`--bin user` でも足す）。これらの行は、目印の行（`# ---ccnavi begin---` と `# ---ccnavi end---`）で囲んだ 1 つの領域に入り、`/.claude/worktrees/` は同等以上の `/.claude/worktrees`・`.claude/worktrees/`・`.claude/worktrees` が既にあれば足さず、`!/.claude/worktrees` や `!.claude/worktrees/keep` など配下を含む否定があればユーザの除外として足さない。`/scratchpad` は同等以上の `/scratchpad/`・`scratchpad/`・`scratchpad` が既にあれば足さない。`scratchpad` を否定する行（`!/scratchpad`・`!scratchpad/keep.md` など配下を含む）があればユーザの除外として足さない。`/logs/` は同等以上の `/logs`・`logs/`・`logs` が既にあれば足さず、`logs` の中身を否定する行（`!/logs`・`!/logs/diag` など）があればユーザの除外として足さない。`ccnavi-index.jsonl` の行が既にあれば足さない。`ccnavi-index.jsonl` を否定する行があればユーザの除外として足さない。どちらも配布先が git のリポジトリで、配るときだけ。振り分けの sh は追跡する側に置く。`projects/` の下のプロジェクトの `.gitignore` には、`--docs` が索引を書く前に足す（10） |

置き場は 2 つに分けて固定する。

```
.ccnavi/scripts/ccnavi-launcher.sh      ← CCNAVI_ENTRY_PATH が指す振り分けの sh（追跡する）
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

- 隣（`.ccnavi/scripts/<os>-<arch>/`）は探さない。自己防衛のパス（8.2）がヒットしない置き場になるため
- `bin_dir` は `..` を含むまま使い、正規化しない。シンボリックリンクも解かない
- `bin/` に何も見つからなければ、PATH の `ccnavi`（`ccnavi.exe`）を探す（`ccnavi_launcher_path`）。`bin/` が先で、PATH はそのときだけ。見るのは絶対パスの項目だけで、空と相対の項目（`.`、`~/bin`）は cwd や展開の有無で結果が変わるので飛ばす。PATH の順に、通常ファイルで実行できる最初のものを `exec` する。自分と同じファイル（`ccnavi` という名前で張ったリンク）は、自分に戻って終わらなくなるので飛ばす（`-ef`）。`exec` に失敗しても次の項目へは進まない（`bin/` と同じ。126）。launcher のコピーが PATH に 2 つあると互いを起動し合うので、置かない
- PATH の実行ファイルは隣に onedir の `_internal/` が要る。単体のコピーは動かない。`dist/ccnavi/` を PATH に通すか、そこへのリンクを置く。版は sh と揃えない（食い違いは `ccnavi_compat_skew` がターミナルの操作で言う。hook では見ない）
- 見つからなければ終了コード 127 で、文面に `<os>-<arch>` と探した `bin_dir`、PATH も見たことを出す。在るが実行できなければ `exec` が失敗して 126
- 保護済み sh の `ccnavi_bin` は、`bin/` もソースも無い配布先でだけ PATH の `ccnavi` を返す（無ければ 1 のまま。呼び手の「実行ファイルが無い」の分岐を保つ）。ソースのリポジトリではソースが先
- 自己防衛は PATH の実体も守る。`bin/` の有無に関わらず、PATH に在れば `bin-path-<パスの印>` のキーでバックアップして、同じ形で戻す。キーにパスの印を付けるのは、PATH が変わって指す先が替わったとき、古い実体の参照を新しい実体に適用しないため。止める正規表現は実体のパスの末尾 3 要素で適用する（2 要素だと別の `./bin/ccnavi` までヒットする）。置き場のディレクトリ全体は守らない。PATH が `/usr/local/bin` のように書けない場所なら、戻しは失敗（`failed`）と報告する

hook は sh を直に起動するので、sh に実行ビットが要る。ccnavi のリポジトリでは追跡するモードを 100755 にする。配布先では
導入スクリプトが付け、付いていなければ `--lint` が error で言う（10 章）。

ccnavi のリポジトリでの組み立て: `build.py` が PyInstaller の出力を `dist/ccnavi/` に作り、それを `.ccnavi/bin/<os>-<arch>/` へコピーする。
`dist/` は導入スクリプトの配布元で、代わりに通る sh が env の無いときに探す先でもあるので残す。
コピーは隣の `<os>-<arch>.new` に済ませてから入れ替える。入れ替えに失敗したら `.ccnavi/bin/<os>-<arch>/` は前のままで、`build.py` は 1 を返し、
`dist/` は新しく置き場は前のまま、と言う。組み立ての順序、入れ替えのやり直し、`--version` の `commit` に埋める値の取り方は `build.py` の説明に書いてある。
Windows でも走っている実行ファイルの名前は変えられる（付録 C）ので、入れ替えは名前の変更で行う。

- rename 2 回の間（数 ms）に来た hook は sh が 127 で終わり、その 1 回は判定が走らない
- ワークツリーで組み立てるとコピー先はそのワークツリーの `.ccnavi/bin/` なので、走っている hook（ワークスペースルートの sh）は変わらない
- 組み立て直すと、セッション開始で取った実行ファイルのバックアップ（8.3）と食い違う。ツール呼び出しの外で変わった版は、`enable` なら次のツール呼び出しの前に基準へ取り込む（`selfguard._rebase_if_changed`）。そのあとは、
  ツール呼び出しの中で変わった版だけを、呼び出しの直前の版に戻す（`selfguard._check_heavy`）。戻す前に、書かれていた版は `state/aside/` に退避する。`dry-run` は取り込まず、「このツール呼び出しで変わった」と言う

既定でないパス: 導入スクリプトは、`.claude/settings.json` にいま書かれている `CCNAVI_ENTRY_PATH` で動きを決める。

| いまの `CCNAVI_ENTRY_PATH` | 動き |
|---|---|
| 無い | `.ccnavi/scripts/ccnavi-launcher.sh` を書く |
| `.ccnavi/scripts/ccnavi-launcher.sh` | 何もしない |
| それ以外（ユーザが決めたパス） | 書き換えず、名指しで 1 行出す。導入は止めず、終了コードも変えない。`--check` では揃っていない側に数える。「値が違う env」の一覧には入れない |

`--mode disable` は断る（4.3）。`--check` は書かずに揃っていないところだけを並べ、揃っていなければ終了コード 1。
名指しした `--deploy` が組み立てられていないか、目印が無くて置き場を決められなければ終了コード 2 で断る。ただし、
既定の配布元が使えないだけなら理由を 1 行出して設定は書く。別の機械向けの組み立ては、その機械の置き場へ配り、
この機械で動くものが無いことを言う。仕様は `tests/sh/test_setup.py` と `tests/sh/test_launcher.py` が固定している。

#### 配る sh の役割

`.ccnavi/scripts/` に置く sh の役割。配る一覧は上の「配るもの」の行にある。

| 場所 | 中身 |
|---|---|
| `.ccnavi/scripts/ccnavi-launcher.sh` | hook が起動する振り分けの sh（モード 100755）。原本と配布先で同じパス。1 つ上の `bin/<os>-<arch>/` から、この機械の実行ファイルを選ぶ。無ければ PATH の `ccnavi`、それも無ければ 127 |
| `.ccnavi/scripts/ccnavi-git.sh` | 安全な git だけを通し、出力を抑えて結果だけ返すラッパースクリプト |
| `.ccnavi/scripts/ccnavi-ticket.sh` | チケットの状態を動かす。親だけが呼ぶ。状態を聞く `status` と、気づきの記録を書く `learn` は、サブエージェントも呼べる。本体は `ccnavi ticket`。取り込み済みの親子のチケットでは write-and-push（取り込んでから書き、書いたパスだけをコミットして push するまで完了にしない） |
| `.ccnavi/scripts/ccnavi-review.sh` | レビューの依頼と確認。親だけが呼ぶ。本体は `ccnavi review`。状態を書くサブコマンドは write-and-push の対象の親子のチケットで write-and-push。`decide <N> --preview`（見るだけ）もここにある。ユーザ専用の `close-early`・`decide` は `ccnavi-user-close-early.sh`・`ccnavi-user-decide.sh` に分かれている |
| `.ccnavi/scripts/ccnavi-user-agree.sh` | 承認し、`ccnavi-user-push-approved.sh` でコミットして push する。ユーザがターミナルで打つ。本体は `ccnavi user agree` |
| `.ccnavi/scripts/ccnavi-user-handoff.sh` | `wip/handoff/` の下書きを取り込む。ユーザがターミナルで打つ。実行ファイルの `user handoff` を入口の印を付けて起こす薄い包み。先頭の `--root <ツリー>` は下書きのあるツリーを指す |
| `.ccnavi/scripts/ccnavi-user-mirror.sh` | 共通レイヤーのミラーを見て、揃える。ユーザがターミナルで打つ。実行ファイルの `user mirror` を入口の印を付けて起こす薄い包み |
| `.ccnavi/scripts/ccnavi-user-prune.sh` | 判定ログと state を整理する。ユーザがターミナルで打つ。実行ファイルの `user prune` を入口の印を付けて起こす薄い包み |
| `.ccnavi/scripts/ccnavi-user-push-approved.sh` | 承認済みチケットの置き場だけをコミットし、保護されたブランチでなければ親のブランチへ push する。ユーザが打つ（エージェントからは止まる）。ターミナルの `ccnavi-user-agree.sh`・ボードの承認とフローの保存・ユーザの判断の入口のあとに呼ばれる。`[<親>...]` で親子のチケットを限る。取り込み済みの親子のチケットはロックを取り、取り込んでから送る |
| `.ccnavi/scripts/ccnavi-fetch.sh` | セッション開始時に親ブランチと、ワークツリーの起点になる統合先ブランチを取ってくる。進めるのは fast-forward だけ |
| `.ccnavi/scripts/ccnavi-session-start.sh` | セッション開始時の入口。`ccnavi-fetch.sh` と `ccnavi-dirs.sh` を順に呼ぶ。SessionStart にはこれを登録する |
| `.ccnavi/scripts/ccnavi-idle.sh` | 放置したセッションの引き継ぎ促し（`start`・`notice`・`reset`）。`Stop` に 2 行、`UserPromptSubmit` に 1 行で登録する（4.2.1） |
| `.ccnavi/scripts/ccnavi-dirs.sh` | セッション開始時に、プロジェクト直下のディレクトリを更新が新しい順に最大 10 件出す。超えるときは `ls` を案内する |
| `.ccnavi/scripts/ccnavi-sync.sh` | 親のブランチを取り込む（早送りか merge。衝突したら取りやめてユーザの対応に切り替える）。リモートから消えた親のブランチを診断する（閉じたかはホストの MR で決める） |
| `.ccnavi/scripts/ccnavi-branches.sh` | issue・MR に紐づくブランチを探す。読むだけ。ホストは sh が読み、手元の候補は `ccnavi branches` が集める |
| `.ccnavi/scripts/ccnavi-start.sh` | issue・MR を指定された依頼の着手の入口。`ccnavi-branches.sh` で候補を探し、無ければ Draft MR・ワークツリー・ブランチを作る |
| `.ccnavi/scripts/ccnavi-worktree.sh` | 直接作業のワークツリーを、取り込み先（ユーザのチェックアウトが開いているブランチ）から切る。ずれが無ければ確認なしで取り込み先のHEADから切る。cwdのブランチ違い・取り込み先が統合先でない・`origin/<取り込み先>` とのずれ（遅れ・進み・分岐・無い・リモート未確認）・書きかけがあるときは、切らずに終了コード 3 で状態・勧め・リスク・確認コードを出す。ユーザの了承を得て `--base`・`--accept`・`--copy-wip`/`--no-wip` を付けて打ち直す。`--side-by-side` で、横で作業する間の自動復元を緩める（`ask` ルールで確認が出る）。記録は共通のgitディレクトリの `ccnavi-direct/<名前>`。`--offline`・`--from-origin` は廃止 |
| `.ccnavi/scripts/ccnavi-stage.sh` | 書き込みを止められている保護対象のファイルを `wip/handoff/`（元と同じ階層。ただし `.claude` の階層は `handoff-.claude` に読み替える。cwd が `.claude/worktrees/<名前>` の中ならそのワークツリーの `wip/handoff/`）へ `cp` する。読み替える名前の表は `.ccnavi/scripts/ccnavi-handoff-names.txt`。`--commands` は編集済みの下書きの状態を出す（ターミナルの前のユーザは `mv` の代わりに `ccnavi user handoff` で取り込める）。取り込むのはユーザ |
| `.ccnavi/scripts/ccnavi-clean.sh` / `ccnavi-clean.js` | ワークツリー 1 本の生成物（node_modules・.venv など）を消す。`worktree remove` の前に打つ。node が無ければ sh で同じものを消す。`--worktree <名前>` は掃除と `worktree remove` を 1 本で行う（本体は `ccnavi worktree drop`。cwd が中なら消さない）。子のブランチを消す `--branch` は `ccnavi-user-clean-branch.sh` に分かれている。配る（`scripts/ccnavi-setup.sh` の `DEPLOY_SCRIPTS`） |

#### 配布物の条件

- 実行ファイル 1 つとその同梱物 1 フォルダ。使う側にランタイムの導入を求めない
- 実行前チェックの間に外部プロセスを起動しない（期限に達した hook は呼び出しをそのまま通すので、遅いとガードが働かない）
- 実行後チェックは git を 1 回起動する
- 起動する外部プロセスはローカルの git と、子を閉じるときにリスクの配点の `script` 項目を走らせる `sh` だけ
- 作業ディレクトリに依らず同じ入力に同じ判定を返す
- 実行時の third-party 依存は PyYAML 1 本。読むのは `safe_load` に限り、`load` はエージェントが書けるファイルに向けては使わない

### 4.6.1 導入スクリプトと配布の細部

4.6 の表に収まらない、`scripts/ccnavi-setup.sh` の動きの細部。使い方は[導入と配布の手引き](../41_guide/install.md)にある。

**設定の書き方。**

- `--check` を付けると、書かずに揃っていないところだけを見る。`--all` も受けるが、今は足すものが無い。
- 同じ 1 回で `.vscode/settings.json` も見る。触ってほしくないときは `--no-vscode`。
  `.vscode/settings.json` にコメントや末尾のカンマがあると（`jq` が読めない）、何を足せばよいかだけを出し、`.claude/settings.json` は書く。
  VS Code の他の設定は残し、`false` と書いてあれば変えずに並べて見せる。
- セッション開始時の入口（`ccnavi-session-start.sh`。取り込みの `ccnavi-fetch.sh` と、直下のディレクトリ一覧の `ccnavi-dirs.sh` を順に呼ぶ）も
  `SessionStart` に別の 1 行で登録する。1 台だけで使いリモートに合わせる必要が無ければ `--no-fetch` で外す（ディレクトリ一覧も出なくなる）。
- 既にある値は置き換えず、違えば並べて見せる。置き換えるのは `--mode` か `--ticket-control` を名指しして `--force` を付けたときだけで、
  名指ししていない値はそのままにする。
- 戻す働きの 2 つ（`CCNAVI_RESTORE_IF_DENY` / `CCNAVI_GUARD_CORE_FILES`）は `CCNAVI_MODE` と同じ値で書く。`CCNAVI_MODE` が `dry-run` のうちは、
  この 2 つも `dry-run` になる（4.4）。`enable` へ切り替える前に、記録の `would-restore` で何が戻るはずだったかを確かめられる。
  `dry-run` で入れたままだとこの 2 つも `dry-run` のまま残るので、`--mode enable` で打ち直すか 3 行を書き換える。
- `CCNAVI_GUARD_TICKET_APPROVAL` はモードに合わせず、いつも `enable` で書く。この変数は `enable` か `disable` しか取らない
  （承認は通れば済んでしまうので、報告だけのモードを持てない）。`dry-run` と書かれていたら `--lint` が error にする（今は `enable` として動いている、と添えて）。
- hook は、そのイベントに ccnavi が登録されていなければ足す。別の表記で登録されているように見えるイベントは、足さずに名前を挙げる（知らせずに足すと判定が 2 回走る）。
- チケット制御は `--ticket-control` で聞き、常にこの 1 行を書く。
- 入れ終わったところで、ワークスペースの git の索引に `projects/` の下が載っていないかを見る。載っていれば `--lint` の `(projects)` と同じ条件で、同じ案内を出す。
  ワークスペース自身のソースに `projects/` がある（ぶつかり）なら `projects/` の改名を、入れ子のリポジトリだけが載っている（載せ忘れ）なら
  索引から外して `.gitignore` に `/projects/` を足す手順を案内する。止めず、終了コードも変えない。`--check` でも出すが、導入の不足ではないので
  「揃っていない」には数えない。索引も `.gitignore` も変えない（直すのはユーザ）。

**配布の目印と振り分けの代償。**

- 置き場の名前は、`build.py` が `dist/ccnavi.target` に書く `<os>-<arch>` の目印から取る。別の機械向けの組み立てでも、その機械の置き場に入るだけなので配る。
  Windows と WSL で同じフォルダを開くなら、それぞれの機械で打てば両方が並ぶ。この機械で動くものが無ければ、1 行の出力と最後の「まだ無いもの」の一覧で知らせる。
- arm64 の macOS と Windows は、自分向けが無ければ x86_64 の組み立てを変換して動かす。どこにも無ければ sh は、この機械の `<os>-<arch>` と
  探した置き場を名指しして終了コード 127 で終わる。実行できないときは 126。
- 目印が無いか読めない配布元からは配らない。名指しの `--deploy` なら終了コード 2 で断り、既定の配布元なら理由を出して設定だけ書く。
- 振り分けの代償は、ツール呼び出しのたびに sh の起動と `uname` 1 回ぶんが乗ること（macOS の実測で約 9 ms。Git Bash の Windows ではこれより大きい）。
  VS Code 拡張は sh を通さず、env が振り分けの sh を指していれば、sh と同じ順で `.ccnavi/bin/<os>-<arch>/` の実行ファイルを自分で選んで起動する
  （Windows では sh を直接起動できないため）。実行ファイルが無ければ次の候補へ進む。
- `.ccnavi/bin/` には実行ファイルだけが並ぶ。sh は自分の隣（`.ccnavi/scripts/<os>-<arch>/`）は探さない（自己防衛のパスがヒットしない置き場から起動できる経路を作らないため）。

**ひな形とフェーズ定義。** ルールと配点とフェーズ定義のひな形は、共通レイヤー（`.ccnavi/config/common/`）へ配る。共通レイヤーの `phases.yml` には、
どのプロジェクトでも同じ構成を前提にできる定義（`src/*`・`tests/*` を範囲に持つものなど）だけがある。判定は、これとワークスペース自身のレイヤー
（`.ccnavi/config/project/phases.yml`）の和を読む。自身のレイヤーの `phases.yml` は配らない。そこに置くのは `scope` がそのワークスペースのレイアウトに
依存する定義で、配布元のものは配布元のレイアウトに合わせてあるため。要るならユーザが書く。配布先に既にあれば、`--force` でも触らない。
レイアウトに依存する定義を共通レイヤーに置くと、全プロジェクトに適用されてしまう（[11.4](multi-repo/rules.md)）。
配布先に既にある共通レイヤーのファイルは、`--force` が無ければ入れ替えない。
ルールと配点は、無ければ最後の点検が「まだ無いもの」として並べる。フェーズ定義は、共通レイヤーにも自身のレイヤーにも無いときだけ並べる。
振り分けの sh と、この機械で動く実行ファイル（`.ccnavi/bin/<この機械>/ccnavi`）も同じ一覧に出る。

**実行ビット。** 振り分けの sh は hook が直に起動するので実行ビットが要る。配った回は `chmod +x` を掛け、配らない回でも実行ビットが外れていれば
付け直す（中身は入れ替えない。Windows（`core.filemode=false`）で足した sh は、別の機械で clone した直後に実行ビットが無いことがある）。
`--no-deploy` の回と、配布元と配布先が同じ回には触らない。付けられなかったら名指しする。`--check` は実行ビットの外れた sh を揃っていないものに数え、
`--lint` は error として報告する（10 章）。

**`.gitignore`。** 配った組み立ての置き場は、配布先が git のリポジトリなら `.gitignore` にも足す（入れると履歴から消すのが難しい）。
置き場は配った機械のぶんだけ足す。同じ回に、配った実行ファイルの `--docs` が索引を書けるよう `**/ccnavi-index.jsonl` も足す（10 章）。足す行は、目印の行（`# ---ccnavi begin---` と `# ---ccnavi end---`）で囲んだ 1 つの領域に入れる。
`**/ccnavi-index.jsonl` か `ccnavi-index.jsonl` の行が既にあれば足さない。行は CRLF の `\r` と行末の空白を除いて比べる。
`!**/ccnavi-index.jsonl` や `!docs/ccnavi-index.jsonl` のように `ccnavi-index.jsonl` を否定する行があれば、ユーザが除外しているとみなして足さず、`--check` でもそう言う。
`/scratchpad`（同等以上の `/scratchpad/`・`scratchpad/`・`scratchpad` があれば足さない。`!/scratchpad` や `!scratchpad/keep.md` など配下を含む否定があれば足さずに伝える）と、`/logs/`（同等以上の `/logs`・`logs/`・`logs` があれば足さない。`!/logs` や `!/logs/diag` などの否定があれば足さずに伝える）も同じ回に足す。ワークツリーの置き場 `/.claude/worktrees/` も足す（同等以上の `/.claude/worktrees`・`.claude/worktrees/`・`.claude/worktrees` があれば足さない。`!/.claude/worktrees` や `!.claude/worktrees/keep` など配下を含む否定があれば足さずに伝える。`--bin user` でも足す）。
領域の扱い: 領域が無ければ末尾に領域ごと足す（直前に空行 1 つ。末尾に改行が無ければ先に足す。CRLF のファイルには CRLF で足す）。領域があって行が足りなければ、end の直前に、コメント 1 行と足す行のセットで足す（領域の中に同じコメントがあれば重ねない）。領域の中の既存の行は書き換えない。行が領域の外にあっても足さない。begin と end が 1 つずつ、begin が先になっていない壊れた領域には触らず、行番号と直し方を知らせる（`--check` は「揃っていない」に数える）。領域を使い始める前の古い見出しブロックは、そのまま残す。

```
# ---ccnavi begin--- (scripts/ccnavi-setup.sh が管理する範囲)
# 実行ファイル
/.ccnavi/bin/darwin-arm64/
# --docs の索引
**/ccnavi-index.jsonl
# 使い捨ての置き場
/scratchpad
# 実行時の記録
/logs/
# ワークツリーの置き場
/.claude/worktrees/
# ---ccnavi end---
```

足すのはワークスペースの `.gitignore` だけ。`projects/<名前>/` の各プロジェクトには触らない（そのプロジェクトのチケットの範囲で足す）。
置き場として足すのは `/.ccnavi/bin/<os>-<arch>/` だけ。`.ccnavi/` を丸ごと無視すると sh と `rules.yml` が git から消えるため、振り分けの sh は追跡する。

**実行ファイルの置き方（`--bin`）。** `project`（既定）は上の `.ccnavi/bin/<os>-<arch>/`。`user` は、実体（同梱物ごと）を `~/.local/share/ccnavi/<os>-<arch>/` に置く。macOS・Linux・WSL は、PATH に通す `~/.local/bin/ccnavi` からそこへリンクを張る。Windows（Git Bash）はリンクを張らず（張れないことがある）、実体のフォルダ（`ccnavi.exe` と `_internal/` が並ぶ）を PATH に通す。配布先の `.ccnavi/bin/` には何も置かず、`.gitignore` にも行を追加しない。振り分けの sh は `bin/` に無ければ PATH の `ccnavi` を使うので、起動の仕組みは変わらない。
- 配布元と中身が違えば置き換える。比べ方は `project` と同じ（実体のフォルダを比べる）。リンクは、実体を指す自分のものなら何もせず、別のものが既にあれば `--force` のときだけ置き換える
- ホームディレクトリに書き込むとき（実体かリンクを置く・置き換えるとき）は、書く前にターミナルで確認する。ターミナルでなければ何も書かずに断り、確認に自動で答えるオプションは無い。書くものが無いときと `--check` は確認しない
- この機械で動く実行ファイルだけを置く。`HOME` が無いとき、`--no-deploy` と同時のときは断る
- PATH に通すフォルダ（リンクのフォルダ、Windows は実体のフォルダ）が PATH に入っていなければ、環境に合った足し方を知らせる。Windows は PowerShell でユーザの環境変数 `Path` に追加する手順と画面から追加する手順、macOS・Linux・WSL は使っているシェル（`SHELL`）の設定ファイルに `export` の 1 行を追加する手順（zsh は `~/.zshrc`、macOS の bash は `~/.bash_profile`、Linux の bash は `~/.bashrc`、fish は `fish_add_path`、そのほかは `~/.profile`）。シェルの設定ファイルも Windows の環境変数も書き換えない
- 「まだ無いもの」の点検は、PATH に通すものがあって実行できれば、この機械で動くものがあるとみなす

**入れ替えと断り。** 配布先に既にあるものは触らず、並べて見せる。入れ替えるのは `--force` を付けたときだけ（ルールも sh も配布先で直されている前提）。
例外は、この機械の実行ファイルを置く `.ccnavi/bin/<os>-<arch>/`。配布元の直下の 1 つずつを `diff -rq` で配布先の同じ名前と比べ、1 つでも違えば `--force` が無くても置き換える。配布先にだけあるファイルは、配布元の名前の下にあるものしか見ない。比べられないとき（`diff` が無い、読めない）は違わないものとして扱う。`--check` は、違うものを「揃っていない」に数える。
実行ファイルを入れ替えるときは、配布先に残っていた同梱物を先に消してから配る（前の版が残ると新しい実行ファイルがそれを読み込んでしまう）。
名指しした `--deploy` が組み立てられていなければ、終了コード 2 で断る。既定の配布元が使えないとき（組み立てていない、配布先が ccnavi 自身）は断らず、
配るのを諦めた理由を 1 行出して `.claude/settings.json` は書く。

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
| `degraded` | コマンドを読み切れず生の文字列に適用したかどうか |
| `fallback` | ルールを読めず組み込みの既定で判定したかどうか |
| `paths` / `detail` | 実行後チェックが拾った変更と件数 |
