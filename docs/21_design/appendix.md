---
type: design
title: 付録 A〜C
description: 理由コード、記録の 1 行、実測で確かめた前提
tags: [design-doc, records]
keywords: [付録, 理由コード, 記録, 実測, 前提]
---

[設計書の入口に戻る](../20_design.md)

## 付録 A. 理由コード

| コード | 意味 |
|---|---|
| `DENY_COMMAND_PATTERN` | Bash の実行される部分がルールにヒットした。読み切れない形でも、ヒットしたルールが全部、中で実行されるコマンドでヒットしたときはこれ（6.3.1） |
| `DENY_PATH` | ファイルのパスがヒットした |
| `RULE_ASK` | ルールの `ask` にヒットした。付き添いのあるセッションで、ターン分離の関門の B を確認に出した回も同じコードになる。`rules` は `builtin:turn-gate:<宣言 id>` |
| `UNDECLARED` | どのルールも言及しない。権限モードに委ねた、または確認できる者が居ないので断った |
| `PARSE_UNCERTAIN` | コマンドを読み切れず生の文字列に適用し、どこにもヒットしなかった |
| `DENY_SHELL_WORKSPACE_ROOT` | Bash の書き込み先（`>`・`tee`・`sed -i`・`rm`・`mv`・`cp` など）を、`cd` で移った先をたどって絶対パスに解くと、ルールファイルの `workspace-root` にヒットした。ワークスペースルート直下には書かず、`wip/handoff/`・`scratchpad/`・ワークツリーの中に書くよう案内して止める。ガード自身の保護など、ほかの `deny` にヒットした呼び出しはその文面で止まる。変数・置換・`~`・`{}` で組んだ書き込み先と、インタプリタに渡したコードの中は見ず、実行後チェックが戻す |
| `DENY_REDIRECT_LIMIT` | 生の文字列（読み切れなかった Bash と PowerShell）に `>` が 50 個を超えて含まれ、シェルから書き込む形の保護を適用せずに止めた。権限モードに依らず止め、確認には回さない。Write / Edit で書くかコマンドを分ける案内を付けて返す。ほかのルールの `deny` と `ask` には適用し、`deny` にヒットすればそのルールの `deny` のまま。`allow` には適用しない。上限を掛けるのは、外す保護（ガード自身の設定とチケットの置き場を守る組み込み）がそのツールにヒットするときだけ |
| `DENY_BRACE_EXPANSION` | 引用の外にブレース展開を書いた。ルールより先に止める（6.3） |
| `DENY_COMMAND_NAME_EXPANSION` | コマンド名の位置に変数・置換・グロブを書いた。ルールより先に止める（6.3） |
| `DENY_BACKQUOTE` | 実行されるバッククォートを書いた。ルールより先に止める（6.3） |
| `DENY_AMBIGUOUS_FORM` | シェルによって読みが分かれる形（`$( )` の中の `case` など）か `coproc`・`select` を書いた。ルールより先に止める（6.3） |
| `DENY_TICKET_SCOPE` / `TICKET_ASK` | チケットの範囲の外 / 範囲の `ask` |
| `DENY_TICKET_BLOCKED` | 承認済みチケット自体が信頼できない。範囲を適用する前に止める（親が引けない、番号が親の計画に無い、`project:` が置き場と違う、など。9.4） |
| `DENY_TICKET_PROJECT_MISMATCH` | 行き先のプロジェクトと承認済みチケットの `project:` が違う |
| `DENY_TICKET_FLOW_LOCKED` | 着手中の子のフロー（承認済みの領域の `flows/<子>.yml`。ハードリンクの別名も）に書こうとした。ルールより先に止める（9.3.1） |
| `NOTICE_TICKET_FLOW_CHANGED` | 着手中の子のフローが、着手のときに記録したハッシュから変わっている。`SubagentStart` / `SubagentStop` が知らせるだけで、止めない（9.3.1） |
| `DENY_RECORDS_PRUNE` | 記録と state を消す `ccnavi user prune` をシェルから打った。チケット制御に依らない |
| `DENY_TICKET_APPROVAL_CLI` | 実行ファイルの `user` の入口か `ccnavi-user-*.sh` を打った。実行役のコマンド越しに打った形を含む。ターミナル要求を切る変数とフラグをコマンド行に書いた形も。ターミナルを求めない入口に、止める操作（`user agree` などと `--yes`・`ccnavi-user-decide.sh`・`worktree retire --abandon`・`decide --choices`・`--guard-ticket-approval`）を組み立てうる読み切れない引数（変数・置換・glob・`xargs`）を渡した形と、標準入力から読むシェルに入口の名前を含む文字列を流す形も（9.5、9.18） |
| `DENY_PHASE_REVIEW` | フェーズのレビューで止まっている（レビュー準備中・レビュー待ち） |
| `DENY_AGENT_ISOLATION` | `Agent`・`Task` に `isolation` のキーがある（値によらない）。作業場所は ccnavi が決め、isolation のワークツリー（`.claude/worktrees/agent-<id>`）にはチケットの範囲に該当しない。補助で、チケット制御が無効でも dry-run でも止める。`CCNAVI_MODE=disable` だけ通す（9.12） |
| `DENY_AGENT_NO_CHILD` | 親のワークツリーから、担当の子を指さずに編集する種類のサブエージェントを起動した。作業中の子の識別子を最大 5 件並べる。関門。読み取り専用の種類は止めない（9.12） |
| `DENY_AGENT_MULTI_TARGET` | プロンプトが違うワークツリーを 2 つ以上指している（「触れない」と書いた子も数える）。関門。チケット制御が無効のときも、パスが 2 つ以上なら止める（補助。9.12） |
| `DENY_AGENT_PARENT_TREE` | プロンプトが親のワークツリーだけを指している。関門（9.12） |
| `DENY_AGENT_CHILD_UNAVAILABLE` | 指した子が担当にできない（閉じた・レビュー待ち・blocked・未着手・ワークツリーが無い・プロジェクトの食い違い）。未着手とワークツリーが無い場合だけ `start` の打ち方を示す。関門（9.12） |
| `DENY_AGENT_TARGET_INVALID` | プロンプトのワークツリーのパスが使えない（このワークスペースの外・`..`・シンボリックリンク・登録が無い・名前が無い）。補助（9.12） |
| `DENY_AGENT_CWD_CONFLICT` | `tool_input` の cwd が、プロンプトで決まった担当先と違う。補助（9.12） |
| `NUDGE_TICKET_FEEDBACK` | メインエージェントの `Stop` で、cwd のツリーが承認済みで着手済みの親子に結び付き、親が blocked でなく、親の局面が「フィードバック計画待ち」。同じ状態（親・HEAD・改版を提案済みか）では 1 回だけ止めて、改版を書いて `approval-request` で送りユーザに承認を頼むよう促す。実行中のサブエージェントが居る間は出ない（9.12）。記録の `decision` は `nudge` |
| `NUDGE_TICKET_FINISH` | メインエージェントの `Stop` で、cwd のワークツリーのチケットが着手済みのまま、未コミットの変更が無く、基準点より先に自分のコミットがある。同じ HEAD では 1 回だけ止めて `finish` か続ける理由を促す（9.6）。記録の `decision` は `nudge`。実行中のサブエージェントが居る間は出ない（9.12） |
| `NUDGE_TICKET_REQUEST` | メインエージェントの `Stop` で、承認済みの親に作業中の子が残っているか、子が全部閉じたのにレビューを要するフェーズの `requested` が無い。同じ状態では 1 回だけ止めて、次の一手（`finish`・合流・ELI5・`review-request`）を促す（9.6）。`finish` の促しと重なった回は出ない。実行中のサブエージェントが居る間は出ない（9.12）。記録の `decision` は `nudge` |
| `NUDGE_SUBAGENT_OUTCOME` | メインエージェントの `Stop` で、終わったサブエージェントの結果（`logs/state/subagent-<セッション>-<agent_id>.outcome`）が `outside`（範囲外の変更を残したまま終わった）か `unchecked`（範囲を確かめられなかった）で、まだ促していない。止めて、結果ごとの 1 行と「合流（finish）の前に確かめて直すか、ユーザに伝える」を渡す。同じ結果では 1 回だけ。`unattributed` と正常な終了（`ok`・`fixed`）では出ない。順序は `finish` → 依頼までの道 → この促し → `integrate` → ルール。実行中のサブエージェントが居る間は出ない（9.12）。記録の `decision` は `nudge` |
| `NUDGE_INTEGRATE` | メインエージェントの `Stop` で、チケットに結び付かない直接作業のワークツリー（`.claude/worktrees/<名前>`）に取り込み先に入っていないコミット（直接作業の記録の起点Sからの `S..先頭`。記録が無ければ統合先で数える）があり、取り込み中のmarkerが無いときに出る。未コミットの変更だけでは出ない（直接作業のコミットは `integrate` の直前にまとめて打つ決まりのため）。コミットと未コミットの変更が両方あれば、先にコミットすることを足す。版 4 のmarkerがある取り込みの途中は出さず、版を知らないmarkerだけは、ユーザに片付けを頼むよう促す。markerは、ユーザのチェックアウトのgitディレクトリの `ccnavi-integrate/<名前>/` に置く。取り込み済みのコピー `ccnavi-integrate-done/<名前>` は、branch_shaが今のHEADと同じで、未コミットの変更が無いときだけ記録とみなす。cwdのツリーのほか、このセッションが書き込んだ直接作業のツリー（`logs/state/direct-<セッション>.json`）も調べる。チケット制御の有無は問わない。同じ状態では 1 回だけ止めて、`integrate` の打ち方と、一時ワークツリーに展開した取り込み結果をユーザに確かめてもらう手順（確定は `integrate-done`、やめるなら `integrate --abort`）を、1 行で完結するコマンドで促す。hookは `integrate` を打たない。取り込み先が切り替わった、ユーザのチェックアウトに途中の操作がある、記録が壊れている、などのときは、止めずに理由を `systemMessage` で言う。ユーザのチェックアウトの書きかけでは止めない。記録が無いワークツリーには `--adopt` を案内する。`finish`・依頼までの道の促しと重なった回は出ない。実行中のサブエージェントが居る間も出ない。記録の `decision` は `nudge` |
| `NUDGE_STOP_RULE` | メインエージェントの `Stop` で、`match: Stop` の `allow` のルールが渡す回になった（`every` の刻み）。止めて、ルールの文を渡す。`finish` の促しと重なった回は出ない（6.6）。記録の `decision` は `nudge` |
| `DENY_SUBAGENT_TICKET_OP` | サブエージェントが、役割の表（`user_only.SUBAGENT_ROLES`）で許されていない操作（チケットの状態・レビュー・push・開始や片付けの sh）を打とうとした |
| `DENY_SUBAGENT_PARENT_TREE` | サブエージェントが、親のワークツリーへの merge・commit・書き込みをしようとした。子のワークツリーでのコミットと、衝突を解くための親のブランチの merge は通す。親のワークツリーかは、そのワークツリー自身のチケットで決める（9.12） |
| `DENY_NEEDS_USER_TURN` | ターン分離の関門。A（`ccnavi-git.sh integrate <名前>`）を打ったあと、ユーザの発言をはさまずに B（`ccnavi-git.sh integrate-done <名前>`）を打った。同じコマンドに A と B を連結した形、記録を読み書きできない場合も。ルールより先に止め、`dry-run` でも止める。付き添いのあるセッションでは、連結した形以外の B は止めずに確認（`RULE_ASK`）に出す。どの宣言かは記録の `rules` と文面が言う（6.7） |
| `DENY_CHILD_PUSH` | 子チケットのワークツリーから `ccnavi-git.sh push` を打った。`cd` の行き先が読めない push を含む（9.10） |
| `DENY_SCRIPT_ENV_OVERRIDE` | 保護済みの sh を、sh の検査の材料を変える環境変数と同じコマンド行で呼んだ。補助の保護で、`CCNAVI_GUARD_CORE_FILES` では切れず、`dry-run` でも止める（8.2） |
| `DENY_WRAPPER_CORE_PATH` | `ccnavi-git.sh` の `rm`・`restore --source`・`checkout <ref> <パス>` で、自己防衛の対象か承認済みチケットの置き場かレビュー待ちの置き場（`wip/proposals/review/`。置き場に何かがある間はその親も）を消す・別のコミットの中身に戻そうとした。対象を読み切れない形（変数・置換・居場所の読めない `cd` / `pushd` の後の相対パス）、サブコマンドが文字どおりの語でない形、標準入力から読むシェルに流す文字列の中の呼び出し、走査が読み切れず名前の直後に文字どおりのサブコマンドが無い形と、索引の中身に戻す `checkout -- <パス>` も（8.2） |
| `POST_VIOLATION` / `POST_PREEXISTING` / `POST_TICKET_SCOPE` | 実行後チェック。保護領域の変更 / 前から在った変更 / チケットの範囲外。記録の `code` は 1 つで、件が混ざった回は `POST_VIOLATION` を `POST_TICKET_SCOPE` より先に採り、内訳を `detail` に `codes POST_VIOLATION 2, POST_TICKET_SCOPE 1` の形で書く。前から在った変更だけを知らせた回は `decision` が `allow` のまま `POST_PREEXISTING` |
| `CORE_CONFIRM_NO_PARENT` / `CORE_CONFIRM_NO_PHASE` / `CORE_CONFIRM_ALREADY_REVIEWED` / `CORE_CONFIRM_NOT_REQUESTED` | レビュー済み（`review confirm` と Chrome の「レビュー済み」）が通らなかった。親の承認済みチケットが作業中に無い / そのフェーズの子が無い / もうレビュー済み / 依頼の記録が無い |
| `CORE_CONFIRM_MOVED` / `CORE_CONFIRM_RESULT_MISMATCH` / `CORE_CONFIRM_REVIEW_OPEN` | レビュー済みが通らなかった。依頼の後にユーザが見るものが動いた / 取得した結果が依頼したマージリクエストと照合できない / 変更要求のレビューか未解決のスレッドが残っている |
| `CORE_DECIDE_NO_DIGEST` / `CORE_DECIDE_MISMATCH` / `CORE_DECIDE_CHOICES` / `CORE_DECIDE_THREAD_ID` / `CORE_DECIDE_FOLLOWUP` / `CORE_DECIDE_STOPPED` | Chrome 拡張の decide（入口の `decide`）が通らなかった。親・フェーズ・依頼の記録・変更要求などは `CORE_CONFIRM_*` と同じコードで返す。見せた指摘のダイジェストが無い・読めない / 見せた指摘と今の指摘が違う（`mismatch` に内訳。何も積まない）/ 選択か受け入れ済みのキーが見せた指摘と合わない / 返信と解決をするスレッドの id が無い / 続きの子を起こせない / 書くものを並べる途中で止まった。通らなければ書くものもホストへの書き込みの計画も返さない |
| `CORE_APPROVE_NO_DIGEST` / `CORE_APPROVE_LIST_CHANGED` / `CORE_APPROVE_DIGEST_CHANGED` / `CORE_APPROVE_NOTHING` / `CORE_APPROVE_STOPPED` | ボードの承認（`ccnavi user agree --yes`）が通らなかった。`--digest` が無い / 見せた一覧（識別子）と今の一覧が違う / 識別子は同じで見せた提案ファイルのバイト列が今と違う（この2つは「内容が更新されました」で、最新を表示し直す） / 承認するものが無い / 書く途中で止まった（置いたものは残る）。確認と同じく、標準エラーの文の頭に `[<コード>]` で載せる。Chrome 拡張の承認（入口の `plan`）も、見せたものと違う・承認するものが無いときに同じコードを答えの `code` で返す（`core.approve.refusal_code` で 1 か所で決める） |
| `NOTICE_DOC_FRONTMATTER` | `PostToolUse`。Write で新しく作った md に frontmatter か `type` が無い。止めずに `additionalContext` で 1 度だけ書き方を案内する。索引に載らない md（git が無視するもの・ccnavi ディレクトリの下）と、決まりの文書で付けないとされている md（`README.md`・`CLAUDE.md`・`CHANGELOG.md` と、`.ccnavi/docs/frontmatter.md` の対象外の表の置き場。チケットの置き場・チケットの下書き `wip/design/scripts/i*.md`・`wip/handoff/`・`.claude/skills/yomiyasu/**`）には出さない。案内しても記録（`decisions.jsonl`）の `code` には残らない |
| `NOTICE_PROPOSAL_PARENT_KEY` / `NOTICE_PROPOSAL_PARENT_FIELDS` | `PostToolUse`。Write で新しく作った提案（`wip/proposals/todo/` の直下の md）の書式の誤り。識別子が子の形でないのに `parent:` がある（`parent:` と `phase:` を消すよう言う）/ `parent:` の無い親に `mr:`・`plan:`・`phases:` が欠けている（欠けた欄を名指しし、`ccnavi ticket new` の雛形を勧める）。止めずに `additionalContext` で案内する。Edit・上書きでは出さない。案内しても記録の `code` には残らない |
| `NOTICE_DIRECT_COMMIT` | `PostToolUse`。メインのエージェントが直接作業のワークツリーで `ccnavi-git.sh commit` を成功させた、またはメインの Agent ツールが返ったとき（サブエージェントのコミットを拾う）に、直接作業のワークツリーの HEAD が動いていた。止めずに `additionalContext` で、レビュー手順の次の一手を、ワークツリーごとにセッションで 1 度だけ言う（6.6） |

記録の `skip` の理由: `mode-disabled`、`event-not-checked`、`no-subject`、`nothing-to-run`、
`payload-unusable`、`deadline-exceeded`、`internal-error`（ccnavi 自身の例外。4.5）、`tool-cannot-write`、
`worktree-unreadable`（実行後チェックと、`SubagentStop` で子のワークツリーを読めず範囲を確かめられなかった回。9.12）、
`no-turn-baseline`、`stop-review-judge`（Stop の AI 判定役の呼び出し。ほかの hook の副作用を動かさず飛ばした回）の 11 種。
`reason` は `skip` の行だけに付くとは限らない。`stop-review-judge` は、判定役の呼び出しを許した行（`allow`）と、
決まった形でないので止めた行（`deny`）にも付き、`stop-review-worker`（作業側が判定役の入口を使おうとして止めた回）は `deny` の行にだけ付く。

組み込みの判定で止めた文は、先頭の行に理由コードと記録の `rules` と同じ識別子を
`[ccnavi] <コード> (組み込みのルール: <識別子>)` の形で並べる（`builtin:script-env` など）。

ユーザの操作（レビュー済み）が通らなかった答えは、`CORE_*` のコードを答えの `code` に持ち、
文面の行にも `[<コード>]` の形で載せる（`ccnavi: ` で始まる行はその後ろ）。理由が幾つもあるときは、
行ごとに自分のコードを載せ、答えの `code` は最初の理由のものにする。
Chrome 拡張の入口（`py/ccnavi_chrome.py`）が返す承認（`plan`）・レビュー済み（`confirm`）の
JSON も、同じコードをキー `code` に持つ（通れば `null`。`stopped`・`mismatch` と同じく、無いときは `null`）。拡張は
分岐をこのキーで決める。

ルールファイルの外の決めごとで止めた回は、記録の `rules` の先頭に `builtin:` 付きの名前を置く。判定ごとに名前を分け、
別の判定の名前を使い回さない（同じ理由の繰り返し（6.4）のキーと文面が、止めた理由の名前になるように）。

| 名前 | 理由コード |
|---|---|
| `builtin:ticket-state` / `builtin:ticket-state-shell` | 状態の置き場への直接の作成・移動（`wip/proposals/review/` など） |
| `builtin:ticket-approved` / `builtin:ticket-approved-shell` | 承認済みの置き場（`.ccnavi/approved/` の下すべて）への書き込み。関門で、`dry-run` でも `CCNAVI_GUARD_CORE_FILES` の値でも効く。文面は承認の経路・`status`・`finish`・`approval-request` を案内する。名前で書くツール用の 1 本は、実行後の保護範囲にも足す（チケット制御が有効な間。`ticket_guard.approved_write_rule`） |
| `builtin:ticket-ops` / `builtin:ticket-ops-shell` | 操作記録の置き場（`logs/state/ops/`）への書き込み。関門で、`dry-run` でも `CCNAVI_GUARD_CORE_FILES` の値でも効く |
| `builtin:guard-hooks` / `builtin:guard-settings` / `builtin:guard-scripts` / `builtin:guard-ccnavi-config` | 名前で書くツール（`Write` `Edit` `NotebookEdit`）から自己防衛の対象への書き込み。対象と例外は自己防衛の「止める側」の表にある |
| `builtin:credentials` | 認証情報の場所（`.env*`、`.dev.vars`、`.netrc`、`.npmrc`、`.pypirc`、`.aws/`、`.ssh/`、`*.pem` / `*.key`（後ろに別の拡張子が続く形も）、`secrets/`、`credentials/`、`credentials.json`、`id_rsa`、`id_ed25519`）への `Bash` `Read` `Write` `Edit` `NotebookEdit`。常に効く土台で、設定で切れない。見本の `.env.example` も止める。`Grep` `Glob` `PowerShell` は対象外 |
| `builtin:guard-raw-git` | 生の `git`。補助（チケット制御が有効なら `dry-run` でも止める） |
| `builtin:ticket-scope` | `DENY_TICKET_SCOPE` / `TICKET_ASK`、実行後チェックと `SubagentStop` の `POST_TICKET_SCOPE` |
| `builtin:ticket-blocked` | `DENY_TICKET_BLOCKED` |
| `builtin:ticket-project` | `DENY_TICKET_PROJECT_MISMATCH` |
| `builtin:ticket-flow-lock` | `DENY_TICKET_FLOW_LOCKED` |
| `builtin:phase-review` | `DENY_PHASE_REVIEW` |
| `builtin:agent-isolation` / `builtin:agent-no-child` / `builtin:agent-multi-target` / `builtin:agent-parent-tree` / `builtin:agent-child-unavailable` / `builtin:agent-target-invalid` / `builtin:agent-cwd-conflict` | `DENY_AGENT_ISOLATION` / `DENY_AGENT_NO_CHILD` / `DENY_AGENT_MULTI_TARGET` / `DENY_AGENT_PARENT_TREE` / `DENY_AGENT_CHILD_UNAVAILABLE` / `DENY_AGENT_TARGET_INVALID` / `DENY_AGENT_CWD_CONFLICT`（`hook.agentlaunch`） |
| `builtin:subagent-ticket-op` | `DENY_SUBAGENT_TICKET_OP` |
| `builtin:turn-gate:<宣言 id>` | `DENY_NEEDS_USER_TURN`。宣言ごとに名前を分ける（組み込みは `integrate-user-turn`。6.7） |
| `builtin:redirect-limit` | `DENY_REDIRECT_LIMIT` |
| `<ルールファイルのパス>:workspace-root` | `DENY_SHELL_WORKSPACE_ROOT`（Bash の書き込み先にヒットしたとき。Write / Edit にヒットしたときは `DENY_PATH`） |

## 付録 B. 記録の 1 行

`ts`（ISO 8601、ローカルのオフセット付き）、`mode`、`permission_mode`、`event`、`tool`、`subject`
（秘密の形を伏せ（4.7）、1000 字で切り `…(+N)`）、`decision`、`enforced`、`code`、`reason`、`degraded`、`unwrapped`（ヒットした中で実行されるコマンド。
`\x00` でつなぎ、1000 字で切る。6.3.1）、`fallback`、`detail`、
`tree`、`project`、`source`、`hinted`（この呼び出しで `additionalContextOnce` の案内を渡した回だけ true。6.6）、`rules[]`、`quoted[]`、`paths[]`、`guarded[]`、`session`、`ms` の 24 欄。空欄は省く。
`ts` / `mode` / `decision` / `enforced` / `ms` は常に出る。`O_APPEND` で 1 行を 1 回の write で書く。

`Stop` の 1 行は、ターンの終わりの報告（7.4）と促し（`NUDGE_*`）が重なると、止めた促しの `decision`・`code`・`rules` になる。
報告の `code` と `rules` は `detail` に `report POST_VIOLATION <id>,<id>` の形で残し、`paths` は報告の変わったパスのまま。

#### 各欄の読み方

| 欄 | 中身 |
|---|---|
| `decision` | `allow` `ask` `deny` `handover` `skip`（促した回は `nudge`）。`handover` は権限モードに委ねた回 |
| `enforced` | 実際に適用したか。`dry-run` は、一般の保護では `false`、関門と補助の保護で止めた回は `true` |
| `code` | 判定の根拠の種別。一覧は付録 A。よく出るのは `DENY_COMMAND_PATTERN`、`DENY_PATH`、`RULE_ASK`、`UNDECLARED`、`PARSE_UNCERTAIN`、`DENY_TICKET_SCOPE`。実行後チェックの行は `POST_VIOLATION` などで、件が混ざった回は強いほう 1 つ |
| `rules` | ヒットしたルールの名前（ルールファイル由来は `<相対パス>:<id>`、組み込みは id）。ルールファイルの外の決めごとで止めた回は先頭に括弧付きの名前（`builtin:ticket-scope`、`builtin:phase-review` など。判定ごとに分かれる。一覧は付録 A） |
| `reason` | `skip` の理由（上の一覧）。`stop-review-judge` は `allow`・`deny` の行にも、`stop-review-worker` は `deny` の行に付く。`worktree-unreadable` は `detail` に理由 |
| `hinted` | この呼び出しで `additionalContextOnce` の案内を渡した回だけ `true`。渡した後の呼び出しと、毎回渡す案内だけの呼び出しには出ない（案内がどの呼び出しに付いたかは hook の応答から分からないので残す） |
| `guarded` | ccnavi 自身の設定ファイルについて、その回に起きたことの配列。要素は `<対象>:<動き>`。動きは `saved`（バックアップを取った）・`restored`（バックアップから戻した）・`restored-from-git`（git から戻した）・`would-restore`（予行で言うだけ）・`rebased`（大きい対象が変わったので基準を取り直した）・`accepted`（ユーザの発言で消えたことを受け入れた）・`missing`・`failed`。変わっていない回は残さない |
| `tree` / `project` | 呼び出しの行き先が属するツリーとプロジェクト |
| `permission_mode` | Claude Code から来たモード。`handover` の行と合わせて読む |
| `paths` | 実行後チェックが検知した「種類とパス」 |
| `detail` | 実行後チェックでは `preexisting`・`known`・`restored`（予行では `would-restore`）の件数と、`code` が混ざった回の内訳（`codes POST_VIOLATION 2, POST_TICKET_SCOPE 1`）。`Stop` で促しと報告が重なった回は報告の `report <code> <id,…>` |
| `degraded` | コマンドを読み切れずに生の文字列で判定した回。`command-taken-as-code`、`unterminated-quote`、`unterminated-substitution`、`ambiguous-substitution`、`backquote`（後ろの 2 つは `DENY_AMBIGUOUS_FORM` / `DENY_BACKQUOTE` で止めた回） |
| `quoted` | 引用の中から切り出したコマンドにだけヒットしたルールの id |
| `unwrapped` | 実行役のコマンド（`env`・`sudo`・`sh -c` など）の中のコマンド、または `cd` で移った先から見たパス（6.3.2）でヒットしたときの、そのコマンド。複数なら `\x00` でつなぐ |
| `fallback` | ルールファイルを読めず組み込みの既定で判定した回（`detail` にパス）。自身のレイヤーやプロジェクトのレイヤーが読めなかった回は、そのレイヤーの名前（`self`、`lib` など）が入り、そのレイヤーを空として判定している |
| `source` | 判定を下したルールのレイヤー。`common` / `self` / プロジェクトの名前。`rules` の名前にはレイヤーの前置きが付かない（相対パスで出どころが分かる） |

チケットの子ごとの記録（`.ccnavi/approved/phases/<親>/<子>.risk.json` と `.judge.json`）にも項目ごとに `source` が入る。
フェーズ定義を根拠に置くフェーズのマーカーには、そのフェーズ定義のレイヤーが入る。

#### 秘密の伏せ方

`subject`・`unwrapped`・`detail` は、書く直前に秘密の形（`Authorization:` / `Cookie:` の値、`token=` / `password=` /
`SECRET_KEY=` / `MYSQL_PWD=` などの値、`ghp_…` / `glpat-…` / `AKIA…` / `sk-…` / `AIza…` / `npm_…` / JWT などのトークン、
URL の `user:<値>@`、mysql の `-p<値>`、sshpass・docker login の `-p`、redis-cli の `-a` など）を伏せる（4.7）。
18 字未満の値は `***`、それより長い値は先頭 6 字と末尾 4 字を残す（`ghp_Ab***Q7r8`）。判定は伏せる前の文字列で下す。

## 付録 C. 実測で確かめた前提

設計の前提にしている Claude Code の振る舞い。版が変わったら測り直す。

| 前提 | 実測 |
|---|---|
| `PreToolUse` で `permissionDecision` と `additionalContext` を 1 つの応答に入れたとき、両方がモデルに届くか | 届く（Claude Code 2.1.235）。deny と ask では `permissionDecisionReason` と一緒に届く |
| ask の `permissionDecisionReason` が誰に見えるか | ユーザの確認ダイアログに出る。Yes を押した後もモデルには届かない。No のときは拒否の定型文だけがモデルに届いてターンが終わる。allow の文面はどこにも出ない |
| `/compact` の後に `SessionStart` が来るか | `source: compact` の `SessionStart` が同じ `session_id` で届く |
| サブエージェントの `PostToolUse`・`SubagentStart`・`SubagentStop` の `session_id` は親と同じか | 同じ（フォアグラウンド・バックグラウンドとも）。`agent_id`（と `agent_type`）が付き、`cwd` は起動した側のまま（`isolation: worktree` を除く。下の行）。親（メイン）の呼び出しには `agent_id` が付かない |
| サブエージェントの `PostToolUse` の `tool_input` のパスは絶対か相対か | 絶対パスで来た（`Write`・`Edit` の `file_path`）。相対で来ても `cwd` から解く実装にしてある |
| フォアグラウンドとバックグラウンドのサブエージェントで payload の形は違うか | 違わない。バックグラウンドは `PostToolUse` が親の `Agent` 呼び出しより後に来ることがある |
| 入れ子のサブエージェント（サブエージェントがさらに起動する体）の payload | 実測できない。この版ではサブエージェントが `Agent` ツールを持たなかった（`ToolSearch` でも見つからない） |
| `SubagentStop` が来て実行中の印が外れるか | 来る（フォアグラウンド・バックグラウンドとも、`agent_id` つき）。印は `SubagentStop` で外れる |
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
| `$( )` の中の `case` は bash 3.2 と zsh で同じに読まれるか | 読みが分かれる。`case` が現れたら縮退させている（許容した誤検知） |
| 複合コマンドの 1 つの区間が読めないとき | 全体が縮退する。安全側なのでそのまま |
| `SubagentStart` の `additionalContext` がサブエージェントに届くか | 届く（実測）。hook が返した指示が、親の依頼文に無くてもサブエージェントの最終報告に現れる |
| `SubagentStart` の payload に `tools` が来るか | 来ない（実測）。キーは `agent_id`・`agent_type`・`cwd`・`hook_event_name`・`prompt_id`・`scratchpad_dir`・`session_id`・`transcript_path`。編集するサブエージェントかは `.claude/agents` の定義と組み込みの一覧で見分ける（`hook/agentkind.py`）。同じワークツリーの案内（9.12）は、`additionalContext` が届く前提で書いてある |
| `isolation: worktree` で起動したサブエージェントの hook が受け取る `cwd` | 隔離したワークツリーのパス（実測）。親の `PreToolUse`（`Agent`）の `cwd` はリポジトリのルートで、`SubagentStart` とサブエージェント内の `PreToolUse`（`Bash`・`Read`）の `cwd` は `<ルート>/.claude/worktrees/agent-<id>`。`agent_id`・`agent_type` も付く。`isolation` は値にも cwd にもよらず起動の PreToolUse で止める（`DENY_AGENT_ISOLATION`）ので、この実測は止める判定に影響しない |

`shlex` は引用・`#`・改行・行継続・`<<` の扱いがシェルと違う。`shellread.py` は先に原文を走査してから語の分割だけを
shlex に任せる。走査か shlex のどちらかに手を入れたら `tests/core/test_shellread.py` の `SHELL_CASES`（bash 3.2 と zsh で実測）を回す。
引用された `<<` で縮退する形は、許容する誤検知（6.3、12.2）。

### 探すツールと `.gitignore`（ripgrep の既定の挙動）

`Grep` が読むファイルは ripgrep の既定の挙動に従い、ccnavi の側では変えられない。`Grep` が止められるのは起点のパスだけで、探した先で読まれたファイルは判定に届かないため、`Grep` と `Glob` は `credentials` の対象にしない。降りた先は `.gitignore` と `permissions.deny` の `Read(...)` が受け持つ。

| 前提 | 実測 |
|---|---|
| 起点に指定したパスそのものに `.gitignore` はヒットするか | ヒットしない。ignore されたディレクトリやファイルを `path` に渡すと、その中は読まれる |
| `.git` が無いディレクトリで `.gitignore` は読まれるか | 読まれない（ripgrep の `--require-git`）。clone していない置き場、git 化していない `projects/<名前>/` が該当する |
| 降りた先に `.git` があるとき | そこから下はそのリポジトリの `.gitignore` が適用される。ワークスペースルートが git 管理下でなくても、`.git` がファイル（ワークツリーの gitdir ポインタ）でも同じ |
| 隠しファイル（ドット始まり）は弾かれるか | 弾かれない。ignore されていなければ `Grep` が読む（素の `rg` の既定とは違う） |
| `.ignore`・`.rgignore`・`.git/info/exclude`・`core.excludesFile`（既定では `~/.config/git/ignore`）は読まれるか | 読まれる。どれも弾く側に働く |
| ワークスペースルートが git 管理下で `.gitignore` に `/projects/` があるとき、ルートからの `Grep` は `projects/` へ降りるか | 降りない。起点に指定した場所には ignore がヒットしないので、`cd projects/<名前>` してから呼ぶと探せる |
| `Read` ツールに `.gitignore` は関係するか | しない。`builtin:credentials` と `Read()` の `deny` が見る |

### 入れ子のサブエージェント（Claude Code 2.1.282）

実測では、上限 3 の `claude -p` でメイン → 子 → 孫を起動して確かめた。「文書」は公式文書
（hooks・sub-agents）の記述で、実測していない。

| 前提 | 実測・文書 |
|---|---|
| 入れ子の上限 | 文書: 既定は 3 層まで（`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`、1 で入れ子を無効）。実測: クラウドの環境（claude.ai/code）はプロセスの env で 1 を入れている（`.claude/settings.json` には無い） |
| 上限に達したサブエージェントに Agent ツールはあるか | 無い（実測）。遅延ツールの一覧にも出ない。起動を待ってハングすることはなく、普通に終わる |
| 孫は Agent ツールを持てるか | 持てる（実測、上限 3） |
| 孫の `PreToolUse` に `agent_id` が付くか | 付く（実測）。孫の Bash にも `DENY_SUBAGENT_TICKET_OP` がヒットした |
| 深さ 1 のサブエージェントに `agent_id` が届くか | 届く（実測。`logs/state/subagent-<セッション>-<agent_id>.running` のファイル名で確かめた） |
| `SubagentStart` / `SubagentStop` は孫でも来るか | 子・孫それぞれで来る（実測） |
| `logs/decisions.jsonl` の `session` | メイン・子・孫で同じ値（実測）。`decisions.jsonl` は `agent_id` / `agent_type` / `cwd` を記録しない。誰の呼び出しかを確かめる手掛かりは `logs/state/subagent-<セッション>-<agent_id>.running` |
| サブエージェントの `cwd` | 文書: メインの `cwd`（`cd` は持ち越さない。`isolation: worktree` のときは隔離したワークツリー）。孫の `cwd` も親のワークツリーになる |
| 親の `agent_id` や深さは hook の入力にあるか | 文書: 無い |
| 起動側は子の終了を待つか | 文書: 非対話では待つ（待ち上限の既定は連続 10 分の idle）。ただし下のバックグラウンドのサブエージェントの計測では、待たれなかった（計測時の環境変数は下の行を参照） |
| AskUserQuestion をサブエージェントに渡せるか | 文書: `tools` に書いても全部のサブエージェントから外される |
| 自動 compact で `SubagentStart` の文は残るか | 文書: 起動時に入れた文は compact で消えうる。同じサブエージェントの次の起動（再開）で `SubagentStart` がまた走り、文脈にその文が無ければ入れ直す |
| `PreCompact` / `PostCompact` | 文書: サブエージェントの compact でも来て、`agent_id` / `agent_type` を持つ。どちらも `systemMessage` を破棄し、`additionalContext` を受けるとは書かれていない（`PostCompact` は decision control も無い）。メインの compact は別の文脈のサブエージェントに影響しない |

合成した payload で確かめた判定。書き込みは書き先のワークツリーで判定し、`agent_id` を見ない。そのため、
孫も子と同じように判定される。残る弱点は 4 つ。

| | 弱点 | 今の扱い |
|---|---|---|
| G2 | 孫の `cwd` が親のワークツリーなので、`SubagentStart` が親の子を全部並べ、`SubagentStop` が兄弟の子の変更でも差し戻す | 入れ子の起動では担当の子の識別子を 1 つ書かせ、担当の案内（子・ワークツリー・範囲）を起動前にプロンプトへ書き足す（9.12）。緩和であって、防いではいない |
| G3 | 孫は、兄弟の子のワークツリーにもその範囲の中なら書ける | 同上 |
| G4 | 差し戻しを無視した知らせが、起動した子にしか届かない | 入れ子の起動なら `systemMessage` にも載せる（9.12） |
| G5 | Bash の実行後チェックは `cwd` のツリーだけを見る | そのまま |

### GitLab の実物

GitLab の実物で分かったこと。

| 分かったこと | どうしたか |
|---|---|
| 変更要求（`POST .../request_changes`）は EE 限定 | 適用されない。CE の `reviewers` の `state` は `unreviewed` / `reviewed` / `approved` だけ |
| URL にトークンを埋めた origin はそのままでは `origin` の出力に出る | sh はユーザの情報を除いて伏せる。実行ファイルの `remote_kind` も読み飛ばす |
| ラッパースクリプト経由の push は `GIT_CONFIG_COUNT` を引き継がないので、環境変数で credential helper を差し替えても反映されない | 認証は git の設定側に置く。ただし gh / glab で認証済みなら、ccnavi が `ccnavi_git_auth` でその helper を足す（利用者の環境変数での差し替えは効かない）。probe はリポジトリの `credential.helper` を空にしてから足す |
| トークンは `docker exec -i gitlab gitlab-rails runner -` に Ruby を流して作れる（`tests/manual/gitlab/make_gitlab_tokens.rb`） | root と reviewer の 2 人分を作る |
| 起動直後は API の `PUT` が 30 秒を超えることがある | probe は 120 秒で 3 回まで待つ |

### 非対話の起動（Claude Code 2.1.310）

`claude -p`（haiku）を、リポジトリの外の空のディレクトリで回して確かめた。SessionStart の hook に環境変数と payload を書き出す設定を `--settings` で渡した。

| 確かめたこと | 結果 |
|---|---|
| `CLAUDE_CODE_SESSION_ATTENDED` の値 | 親の対話セッションが `1` のとき、その子の `claude -p`（`--output-format stream-json` でも同じ）は `0`。親の値の継承ではなく、`-p` の起動で Claude Code が設定する |
| `CLAUDE_CODE_ENTRYPOINT` の値 | 親の環境に値があると `-p` でも親の値が残る（親が `remote_desktop` なら子も同じ）。外して `-p` で起動すると `sdk-cli` |
| SessionStart の payload | `session_id`・`transcript_path`・`cwd`・`scratchpad_dir`・`hook_event_name`・`source` だけ。対話かどうかの欄はない |
| hook の標準入力・標準出力 | `-p` の実行で、どちらも tty でない |
| バックグラウンドの Bash | 75 秒の待ちは 83 秒で終わり、成果ファイルができた。660 秒の待ちは、途中で Bash の timeout にかかったため、モデルが timeout を延ばして起動し直し、全体で 21 分かけて完了した。どちらも `-p` は完了まで待った |
| バックグラウンドのサブエージェント（60 秒の作業） | 2 回とも 17〜21 秒で終了し、成果は残らなかった。メインが「完了通知を待つ」と言ってターンを終えた時点で、プロセスが終わった。この計測では `CLAUDE_CODE_BG_TASKS_REPORT_RUNNING` を外していなかった（値は記録していない）。外した状態の計測は別にある |
| フォアグラウンドのサブエージェント（45 秒の作業） | 57 秒かけて完了まで待ち、成果ファイルができ、結果の報告まで返った |

確かめていないこと。

- ターミナルの対話 CLI での値（初回の設定画面で止まり、SessionStart まで進めなかった）、SDK からの起動、人が付き添う IDE 拡張などの入口で `0` になるか、他の版、コンテナでの自動実行そのもの
- 各実験は 1〜2 回で、再現性は見ていない
- フォアグラウンドのサブエージェントを複数並べたときの先行関係と、10 分を超えるフォアグラウンドのサブエージェント
- 待ち上限と裏の Bash の既定を 60 分にした場合の、60 分ちょうどの待ち（約 55 分と約 50 分の待ちは完了まで待たれた）

再現は、SessionStart の hook で環境変数を書き出す設定を `--settings` で渡し、`claude -p` を空のディレクトリで回す。
