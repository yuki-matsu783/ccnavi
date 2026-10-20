---
type: design
title: 付録 A〜C
description: 理由コード、記録の 1 行、実測で確かめた前提
tags: [design-doc, records]
keywords: [付録, 理由コード, 記録, 実測, 前提]
---

[設計書の入口に戻る](../design.md)

## 付録 A. 理由コード

| コード | 意味 |
|---|---|
| `DENY_COMMAND_PATTERN` | Bash の実行される部分がルールに当たった。読み切れない形でも、当たったルールが全部、中で実行されるコマンドで当たったときはこれ（6.3.1） |
| `DENY_PATH` | ファイルのパスが当たった |
| `RULE_ASK` | ルールの `ask` に当たった |
| `UNDECLARED` | どのルールも言及しない。権限モードに委ねた、または確認できる者が居ないので断った |
| `PARSE_UNCERTAIN` | コマンドを読み切れず生の文字列に当て、どこにも当たらなかった |
| `DENY_REDIRECT_LIMIT` | 生の文字列（読み切れなかった Bash と PowerShell）に `>` が 50 個を超えて含まれ、シェルから書き込む形の保護を当てずに止めた。権限モードに依らず止め、確認には回さない。生の文字列への照合は `>` の個数 × その後ろの空白の無い語の長さで伸びる。上限が無いと `>` が増えるほど長さの 2 乗に近づく。上限の下でも `>` 50 個 × 尾の長さで伸び（照合だけで 100KB で約 0.5 秒、400KB で約 1.8 秒）、上限はこれを長さの 50 倍の線形に抑える。期限（3 秒）を超えると `deadline-exceeded` の block（終了コード 2）になって理由を返せない。上限は止める側のまま、Write / Edit で書くかコマンドを分ける案内を付けて返すためのもの。ほかのルールの `deny` と `ask` には今までどおり当て、`deny` に当たればそのルールの `deny` のまま。`allow` には当てない。上限を掛けるのは、外す保護（ガード自身の設定とチケットの置き場を守る組み込み）がそのツールに当たるときだけ |
| `DENY_BRACE_EXPANSION` | 引用の外にブレース展開を書いた。ルールより先に止める（6.3） |
| `DENY_COMMAND_NAME_EXPANSION` | コマンド名の位置に変数・置換・グロブを書いた。ルールより先に止める（6.3） |
| `DENY_BACKQUOTE` | 実行されるバッククォートを書いた。ルールより先に止める（6.3） |
| `DENY_AMBIGUOUS_FORM` | シェルによって読みが分かれる形（`$( )` の中の `case` など）か `coproc`・`select` を書いた。ルールより先に止める（6.3） |
| `DENY_TICKET_SCOPE` / `TICKET_ASK` | チケットの範囲の外 / 範囲の `ask` |
| `DENY_TICKET_BLOCKED` | 承認済みチケット自体が信頼できない。範囲を当てる前に止める（親が引けない、番号が親の計画に無い、`project:` が置き場と違う、など。9.4） |
| `DENY_TICKET_PROJECT_MISMATCH` | 行き先のプロジェクトと承認済みチケットの `project:` が違う |
| `DENY_TICKET_FLOW_LOCKED` | 着手中の子のフロー（承認済みの領域の `flows/<子>.yml`。ハードリンクの別名も）に書こうとした。ルールより先に止める（9.3.1） |
| `NOTICE_TICKET_FLOW_CHANGED` | 着手中の子のフローが、着手のときに記録したハッシュから変わっている。`SubagentStart` / `SubagentStop` が知らせるだけで、止めない（9.3.1） |
| `DENY_RECORDS_PRUNE` | 記録と state を消す `ccnavi user prune`（古い形の `ccnavi --prune`。`--preview` なしのもの）をシェルから打った。チケット制御に依らない |
| `DENY_TICKET_APPROVAL_CLI` | 実行ファイルの `user` の入口（古い形の承認用のオプション付きを含む）か `ccnavi-user-*.sh` を打った。実行役のコマンド越しに打った形を含む。端末要求を切る変数とフラグをコマンド行に書いた形も。端末を求めない入口に、止める操作（`user agree` などと `--yes`・`ccnavi-user-decide.sh`・`worktree retire --abandon`・`decide --choices`・`--guard-ticket-approval`）を組み立てうる読み切れない引数（変数・置換・glob・`xargs`）を渡した形と、標準入力から読むシェルに入口の名前を含む文字列を流す形も（9.5、9.18） |
| `DENY_PHASE_REVIEW` | フェーズのレビューで止まっている（レビュー準備中・レビュー待ち） |
| `NUDGE_TICKET_FINISH` | メインエージェントの `Stop` で、cwd のワークツリーのチケットが着手済みのまま、未コミットの変更が無く、基準点より先に自分のコミットがある。同じ HEAD では 1 回だけ止めて `finish` か続ける理由を促す（9.6）。記録の `decision` は `nudge` |
| `NUDGE_TICKET_REQUEST` | メインエージェントの `Stop` で、承認済みの親に作業中の子が残っているか、子が全部閉じたのにレビューを要するフェーズの `requested` が無い。同じ状態では 1 回だけ止めて、次の一手（`finish`・合流・ELI5・`request`）を促す（9.6）。`finish` の促しと重なった回は出ない。実行中のサブエージェントが居る間は出ない（9.12）。記録の `decision` は `nudge` |
| `NUDGE_SUBAGENT_OUTCOME` | メインエージェントの `Stop` で、終わったサブエージェントの結果（`logs/state/subagent-<セッション>-<agent_id>.outcome`）が `outside`（範囲外の変更を残したまま終わった）か `unchecked`（範囲を確かめられなかった）で、まだ促していない。止めて、結果ごとの 1 行と「合流（finish）の前に確かめて直すか、ユーザに伝える」を渡す。同じ結果では 1 回だけ。`unattributed` と正常な終了（`ok`・`fixed`）では出ない。順序は `finish` → 依頼までの道 → この促し → `integrate` → ルール。実行中のサブエージェントが居る間は出ない（9.12）。記録の `decision` は `nudge` |
| `NUDGE_INTEGRATE` | メインエージェントの `Stop` で、チケットに結び付かない直接作業のワークツリー（`.claude/worktrees/<名前>`）に未コミットの変更か取り込み先に入っていないコミット（直接作業の記録の起点Sからの `S..先頭`。記録が無ければ統合先で数える）があり、取り込み中のmarkerが無いときに出る。markerは、メインのチェックアウトのgitディレクトリの `ccnavi-integrate/<名前>/` に置く。旧形式の取り込み済みの写し `ccnavi-integrate-done/<名前>` は、branch_shaが今のHEADと同じで、未コミットの変更が無いときだけ記録とみなす。cwdのツリーのほか、このセッションが書き込んだ直接作業のツリー（`logs/state/direct-<セッション>.json`）も調べる。チケット制御の有無は問わない。同じ状態では 1 回だけ止めて、`integrate` の打ち方と、一時ワークツリーに展開した取り込み結果をユーザに確かめてもらう手順（`integrate-done`・`integrate --abort`）を、1 行で完結するコマンドで促す。hookは `integrate` を打たない。取り込み先が切り替わった、メインのチェックアウトに途中の操作がある、記録が壊れている、などのときは、止めずに理由を `systemMessage` で言う。メインのチェックアウトの書きかけでは止めない。記録が無いワークツリーには `--adopt` を案内する。`finish`・依頼までの道の促しと重なった回は出ない。実行中のサブエージェントが居る間も出ない。記録の `decision` は `nudge` |
| `NUDGE_STOP_RULE` | メインエージェントの `Stop` で、`match: Stop` の `allow` のルールが渡す回になった（`every` の刻み）。止めて、ルールの文を渡す。`finish` の促しと重なった回は出ない（6.6）。記録の `decision` は `nudge` |
| `DENY_SUBAGENT_TICKET_OP` | サブエージェントが、役割の表（`user_only.SUBAGENT_ROLES`）で許されていない操作（チケットの状態・レビュー・push・開始や片付けの sh）を打とうとした |
| `DENY_CHILD_PUSH` | 子チケットのワークツリーから `ccnavi-git.sh push` を打った。`cd` の行き先が読めない push を含む（9.10） |
| `DENY_SCRIPT_ENV_OVERRIDE` | 保護済みの sh を、sh の検査の材料を変える環境変数と同じコマンド行で呼んだ。補助の保護で、`CCNAVI_GUARD_CORE_FILES` では切れず、`dry-run` でも止める（8.2） |
| `DENY_WRAPPER_CORE_PATH` | `ccnavi-git.sh` の `rm`・`restore --source`・`checkout <ref> <パス>` で、自己防衛の対象か承認済みチケットの置き場かレビュー待ちの置き場（`wip/proposals/review/`。置き場に何かがある間はその親も）を消す・別のコミットの中身に戻そうとした。対象を読み切れない形（変数・置換・居場所の読めない `cd` / `pushd` の後の相対パス）、サブコマンドが文字どおりの語でない形、標準入力から読むシェルに流す文字列の中の呼び出し、走査が読み切れず名前の直後に文字どおりのサブコマンドが無い形と、索引の中身に戻す `checkout -- <パス>` も（8.2） |
| `POST_VIOLATION` / `POST_PREEXISTING` / `POST_TICKET_SCOPE` | 実行後チェック。保護領域の変更 / 前から在った変更 / チケットの範囲外。記録の `code` は 1 つで、件が混ざった回は `POST_VIOLATION` を `POST_TICKET_SCOPE` より先に採り、内訳を `detail` に `codes POST_VIOLATION 2, POST_TICKET_SCOPE 1` の形で書く。前から在った変更だけを知らせた回は `decision` が `allow` のまま `POST_PREEXISTING` |
| `CORE_CONFIRM_NO_PARENT` / `CORE_CONFIRM_NO_PHASE` / `CORE_CONFIRM_ALREADY_REVIEWED` / `CORE_CONFIRM_NOT_REQUESTED` | レビュー済み（`review confirm` と Chrome の「レビュー済み」）が通らなかった。親の承認済みチケットが作業中に無い / そのフェーズの子が無い / もうレビュー済み / 依頼の記録が無い |
| `CORE_CONFIRM_MOVED` / `CORE_CONFIRM_RESULT_MISMATCH` / `CORE_CONFIRM_REVIEW_OPEN` | レビュー済みが通らなかった。依頼の後にユーザが見るものが動いた / 取得した結果が依頼したマージリクエストと照合できない / 変更要求のレビューか未解決のスレッドが残っている |
| `CORE_APPROVE_NO_DIGEST` / `CORE_APPROVE_LIST_CHANGED` / `CORE_APPROVE_DIGEST_CHANGED` / `CORE_APPROVE_NOTHING` / `CORE_APPROVE_STOPPED` | ボードの承認（`ccnavi user agree --yes`）が通らなかった。`--digest` が無い / 見せた一覧（識別子）と今の一覧が違う / 識別子は同じで見せた提案ファイルのバイト列が今と違う（この2つは「内容が更新されました」で、最新を表示し直す） / 承認するものが無い / 書く途中で止まった（置いたものは残る）。確認と同じく、標準エラーの文の頭に `[<コード>]` で載せる。Chrome 拡張の承認（入口の `plan`）も、見せたものと違う・承認するものが無いときに同じコードを答えの `code` で返す（`core_approve.refusal_code` で 1 か所で決める） |
| `NOTICE_DOC_FRONTMATTER` | `PostToolUse`。Write で新しく作った md に frontmatter か `type` が無い。止めずに `additionalContext` で 1 度だけ書き方を案内する。索引に載らない md（git が無視するもの・ccnavi ディレクトリの下）と、決まりの文書が付けないとする md（`README.md`・`CLAUDE.md` と、`.ccnavi/docs/frontmatter.md` の対象外の表の置き場。チケットの置き場・チケットの下書き `wip/design/scripts/i*.md`・`wip/handoff/`・`.claude/skills/yomiyasu/**`）には出さない。案内しても記録（`decisions.jsonl`）の `code` には残らない |
| `NOTICE_PROPOSAL_PARENT_KEY` / `NOTICE_PROPOSAL_PARENT_FIELDS` | `PostToolUse`。Write で新しく作った提案（`wip/proposals/todo/` の直下の md）の書式の誤り。識別子が子の形でないのに `parent:` がある（`parent:` と `phase:` を消すよう言う）/ `parent:` の無い親に `mr:`・`plan:`・`phases:` が欠けている（欠けた欄を名指しし、`ccnavi ticket new` の雛形を勧める）。止めずに `additionalContext` で案内する。Edit・上書きでは出さない。案内しても記録の `code` には残らない |

記録の `skip` の理由: `mode-disabled`、`event-not-checked`、`no-subject`、`nothing-to-run`、
`payload-unusable`、`deadline-exceeded`、`internal-error`（ccnavi 自身の例外。4.5）、`tool-cannot-write`、
`worktree-unreadable`（実行後チェックと、`SubagentStop` で子のワークツリーを読めず範囲を確かめられなかった回。9.12）、
`no-turn-baseline`。

組み込みの判定で止めた文は、先頭の行に理由コードと記録の `rules` と同じ識別子を
`[ccnavi] <コード> (組み込みのルール: <識別子>)` の形で並べる（`builtin:script-env` など）。

ユーザの操作（レビュー済み）が通らなかった答えは、`CORE_*` のコードを答えの `code` に持ち、
文面の行にも `[<コード>]` の形で載せる（`ccnavi: ` で始まる行はその後ろ）。理由が幾つもあるときは、
行ごとに自分のコードを載せ、答えの `code` は最初の理由のものにする。
Chrome 拡張の入口（`py/ccnavi_chrome.py`）が返す承認（`plan`）・レビュー済み（`confirm`）の
JSON も、同じコードを鍵 `code` に持つ（通れば `null`。`stopped`・`mismatch` と同じく、無いときは `null`）。拡張は
分岐をこの鍵で決める。

ルールファイルの外の決めごとで止めた回は、記録の `rules` の先頭に `builtin:` 付きの名前を置く。判定ごとに名前を分け、
別の判定の名前を使い回さない（同じ理由の繰り返し（6.4）の鍵と文面が、止めた理由の名前になるように）。

| 名前 | 理由コード |
|---|---|
| `builtin:ticket-state` / `builtin:ticket-state-shell` | 状態の置き場への直接の作成・移動（`wip/proposals/review/` など） |
| `builtin:ticket-approved` / `builtin:ticket-approved-shell` | 承認済みの置き場（`.ccnavi/approved/` の下すべて）への書き込み。関門で、`dry-run` でも `CCNAVI_GUARD_CORE_FILES` の値でも効く |
| `builtin:ticket-ops` / `builtin:ticket-ops-shell` | 操作記録の置き場（`logs/state/ops/`）への書き込み。関門 |
| `builtin:guard-raw-git` | 生の `git`。補助（チケット制御が有効なら `dry-run` でも止める） |
| `builtin:ticket-scope` | `DENY_TICKET_SCOPE` / `TICKET_ASK`、実行後チェックと `SubagentStop` の `POST_TICKET_SCOPE` |
| `builtin:ticket-blocked` | `DENY_TICKET_BLOCKED` |
| `builtin:ticket-project` | `DENY_TICKET_PROJECT_MISMATCH` |
| `builtin:ticket-flow-lock` | `DENY_TICKET_FLOW_LOCKED` |
| `builtin:phase-review` | `DENY_PHASE_REVIEW` |
| `builtin:subagent-ticket-op` | `DENY_SUBAGENT_TICKET_OP` |
| `builtin:redirect-limit` | `DENY_REDIRECT_LIMIT` |

## 付録 B. 記録の 1 行

`ts`（ISO 8601、ローカルのオフセット付き）、`mode`、`permission_mode`、`event`、`tool`、`subject`
（秘密の形を伏せ（4.7）、1000 字で切り `…(+N)`）、`decision`、`enforced`、`code`、`reason`、`degraded`、`unwrapped`（当たった中で実行されるコマンド。
`\x00` でつなぎ、1000 字で切る。6.3.1）、`fallback`、`detail`、
`tree`、`project`、`source`、`hinted`（この呼び出しで `additionalContextOnce` の案内を渡した回だけ true。6.6）、`rules[]`、`quoted[]`、`paths[]`、`guarded[]`、`session`、`ms` の 24 欄。空欄は省く。
`ts` / `mode` / `decision` / `enforced` / `ms` は常に出る。`O_APPEND` で 1 行を 1 回の write で書く。

`Stop` の 1 行は、ターンの終わりの報告（7.4）と促し（`NUDGE_*`）が重なると、止めた促しの `decision`・`code`・`rules` になる。
報告の `code` と `rules` は `detail` に `report POST_VIOLATION <id>,<id>` の形で残し、`paths` は報告の変わったパスのまま。

## 付録 C. 実測で確かめた前提

設計の前提にしている Claude Code の振る舞い。版が変わったら測り直す。

| 前提 | 実測 |
|---|---|
| `PreToolUse` で `permissionDecision` と `additionalContext` を 1 つの応答に入れたとき、両方がモデルに届くか | 届く（Claude Code 2.1.235）。deny と ask では `permissionDecisionReason` と一緒に届く |
| ask の `permissionDecisionReason` が誰に見えるか | ユーザの確認ダイアログに出る。Yes を押した後もモデルには届かない。No のときは拒否の定型文だけがモデルに届いてターンが終わる。allow の文面はどこにも出ない |
| `/compact` の後に `SessionStart` が来るか | `source: compact` の `SessionStart` が同じ `session_id` で届く |
| サブエージェントの `PostToolUse`・`SubagentStart`・`SubagentStop` の `session_id` は親と同じか | 同じ（前景・背景とも）。`agent_id`（と `agent_type`）が付き、`cwd` は起動した側のまま。親（メイン）の呼び出しには `agent_id` が付かない |
| サブエージェントの `PostToolUse` の `tool_input` のパスは絶対か相対か | 絶対パスで来た（`Write`・`Edit` の `file_path`）。相対で来ても `cwd` から解く実装にしてある |
| 前景と背景のサブエージェントで payload の形は違うか | 違わない。背景は `PostToolUse` が親の `Agent` 呼び出しより後に来ることがある |
| 入れ子のサブエージェント（サブエージェントがさらに起動する体）の payload | 実測できない。この版ではサブエージェントが `Agent` ツールを持たなかった（`ToolSearch` でも見つからない） |
| `SubagentStop` が来て実行中の印が外れるか | 来る（前景・背景とも、`agent_id` つき）。印は `SubagentStop` で外れる |
| 期限の数値（残り − 0.5 秒・0.2 秒以下は起動しない・SessionStart の 2 秒）で足りるか | Linux では足りる（ワークツリー 8 本・記録 1 件で、Stop も SessionStart も 1 回 0.3 秒未満）。Windows の Git Bash は未測定で、測ってから見直す |
| `/clear` の後の `SessionStart` の `session_id` と `source` | 未実測（ユーザの操作が要る）。`compact` は上の行のとおり |
| `${CLAUDE_PROJECT_DIR}` は `env` で展開されるか | hook の `command` では展開されるが `env` では展開されない |
| `env` は再読み込みされるか | されない。セッションを開き直すまで古い値が残る。hook の `command` は即座に反映される |
| Bash ツールの環境に `CLAUDE_PROJECT_DIR` は来るか | 来ない。hook の環境にだけ来る |
| `.claude/settings.json` に独自のトップレベルキーを足せるか | スキーマ検証に弾かれる |
| Windows は実行中の実行ファイルを上書きできるか | できない。名前の変更はできる |
| `shlex` は引用された `<<` と素の `<<` を区別するか | しない |
| `shlex` は二重引用の中の `$( )` を区切るか | 区切らない。引用の中を 1 語として返す |
| `shlex` は語の途中の `#` をどう読むか | コメントの始まりにする（`echo a#b c` → `echo a`）。bash は `a#b c` と出す |
| `shlex` は行継続を知っているか | 知らない。`\` と改行をトークンの中に残す |
| `SubagentStart` の `additionalContext` がサブエージェントに届くか | 未実測。届かなければ、サブエージェント内の最初の `PreToolUse` で渡す形に変える |
| `SubagentStart` の payload に `tools` が来るか | 未実測。来れば編集するサブエージェントかの見分けに使い、来なければ `.claude/agents` の定義と組み込みの一覧で見分ける（`hook/agentkind.py`）。同じ作業ツリーの案内（9.12）も、上の行のとおり `additionalContext` が届くことを前提にする |
| `isolation: worktree` で起動したサブエージェントの hook が受け取る `cwd` | 未実測。止めるかどうかは cwd で親を引くので、受け取る cwd によって止まるかどうかが分かれる |

### 入れ子のサブエージェント（Claude Code 2.1.282）

実測では、上限 3 の `claude -p` でメイン → 子 → 孫を起動して確かめた。「文書」は公式文書
（hooks・sub-agents）の記述で、実測していない。

| 前提 | 実測・文書 |
|---|---|
| 入れ子の上限 | 文書: 既定は 3 層まで（`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`、1 で入れ子を無効）。実測: クラウドの環境（claude.ai/code）はプロセスの env で 1 を入れている（`.claude/settings.json` には無い） |
| 上限に達したサブエージェントに Agent ツールはあるか | 無い（実測）。遅延ツールの一覧にも出ない。起動を待ってハングすることはなく、普通に終わる |
| 孫は Agent ツールを持てるか | 持てる（実測、上限 3） |
| 孫の `PreToolUse` に `agent_id` が付くか | 付く（実測）。孫の Bash にも `DENY_SUBAGENT_TICKET_OP` が当たった |
| 深さ 1 のサブエージェントに `agent_id` が届くか | 届く（実測。`logs/state/subagent-<セッション>-<agent_id>.running` のファイル名で確かめた） |
| `SubagentStart` / `SubagentStop` は孫でも来るか | 子・孫それぞれで来る（実測） |
| `logs/decisions.jsonl` の `session` | メイン・子・孫で同じ値（実測）。`decisions.jsonl` は `agent_id` / `agent_type` / `cwd` を記録しない。誰の呼び出しかを確かめる手掛かりは `logs/state/subagent-<セッション>-<agent_id>.running` |
| サブエージェントの `cwd` | 文書: メインの `cwd`（`cd` は持ち越さない）。孫の `cwd` も親のワークツリーになる |
| 親の `agent_id` や深さは hook の入力にあるか | 文書: 無い |
| 起動側は子の終了を待つか | 文書: 非対話 / SDK では待たない |
| AskUserQuestion をサブエージェントに渡せるか | 文書: `tools` に書いても全部のサブエージェントから外される |
| 自動 compact で `SubagentStart` の文は残るか | 文書: 起動時に入れた文は compact で消えうる。同じサブエージェントの次の起動（再開）で `SubagentStart` がまた走り、文脈にその文が無ければ入れ直す |
| `PreCompact` / `PostCompact` | 文書: サブエージェントの compact でも来て、`agent_id` / `agent_type` を持つ。どちらも `systemMessage` を捨て、`additionalContext` を受けるとは書かれていない（`PostCompact` は decision control も無い）。メインの compact は別の文脈のサブエージェントに影響しない |

合成した payload で確かめた判定（同じ日）。書き込みは書き先のワークツリーで判定し、`agent_id` を見ない。そのため、
孫も子と同じように判定される。残る弱点は 4 つ。

| | 弱点 | 今の扱い |
|---|---|---|
| G2 | 孫の `cwd` が親のワークツリーなので、`SubagentStart` が親の子を全部並べ、`SubagentStop` が兄弟の子の変更でも差し戻す | フローの案内が、入れ子のプロンプトに担当の子・ワークツリー・範囲を書かせる（9.12）。緩和であって、防いではいない |
| G3 | 孫は、兄弟の子のワークツリーにもその範囲の中なら書ける | 同上 |
| G4 | 差し戻しを無視した知らせが、起動した子にしか届かない | 入れ子の起動なら `systemMessage` にも載せる（9.12） |
| G5 | Bash の実行後チェックは `cwd` のツリーだけを見る | そのまま |
