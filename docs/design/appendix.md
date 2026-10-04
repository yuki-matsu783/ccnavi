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
| `DENY_COMMAND_PATTERN` | Bash の実行される部分がルールに当たった。読み切れない形でも、当たったルールが全部中で実行されるコマンドで当たったときはこれ（6.3.1） |
| `DENY_PATH` | ファイルのパスが当たった |
| `RULE_ASK` | ルールの `ask` に当たった |
| `UNDECLARED` | どのルールも言及しない。権限モードに委ねた、または確認できる者が居ないので断った |
| `PARSE_UNCERTAIN` | コマンドを読み切れず生の文字列に当て、どこにも当たらなかった |
| `DENY_BRACE_EXPANSION` | 引用の外にブレース展開を書いた。ルールより先に止める（6.3） |
| `DENY_COMMAND_NAME_EXPANSION` | コマンド名の位置に変数・置換・グロブを書いた。ルールより先に止める（6.3） |
| `DENY_BACKQUOTE` | 実行されるバッククォートを書いた。ルールより先に止める（6.3） |
| `DENY_AMBIGUOUS_FORM` | シェルによって読みが分かれる形（`$( )` の中の `case` など）か `coproc`・`select` を書いた。ルールより先に止める（6.3） |
| `DENY_TICKET_SCOPE` / `TICKET_ASK` | チケットの範囲の外 / 範囲の `ask` |
| `DENY_TICKET_BLOCKED` | 承認済みチケット自体が信頼できない。範囲を当てる前に止める（親が引けない、番号が親の計画に無い、`project:` が置き場と違う、など。9.4） |
| `DENY_TICKET_PROJECT_MISMATCH` | 行き先のプロジェクトと承認済みチケットの `project:` が違う |
| `DENY_TICKET_FLOW_LOCKED` | 着手中の子のフロー（承認済みの領域の `flows/<子>.yml`。ハードリンクの別名も）に書こうとした。ルールより先に止める（9.3.1） |
| `NOTICE_TICKET_FLOW_CHANGED` | 着手中の子のフローが、着手のときに記録したハッシュから変わっている。`SubagentStart` / `SubagentStop` が知らせるだけで、止めない（9.3.1） |
| `DENY_RECORDS_PRUNE` | 記録と state を消す `ccnavi --prune`（`--preview` なし）をシェルから打った。チケット制御に依らない |
| `DENY_TICKET_APPROVAL_CLI` | 実行ファイルを承認用のオプション付きで直接打った。実行役のコマンド越しに打った形を含む。端末要求を切る変数とフラグをコマンド行に書いた形も（9.5） |
| `DENY_PHASE_REVIEW` | フェーズのレビューで止まっている（レビュー準備中・レビュー待ち） |
| `NUDGE_TICKET_FINISH` | メインエージェントの `Stop` で、cwd のワークツリーのチケットが着手済みのまま、未コミットの変更が無く基準点より先に自分のコミットがある。同じ HEAD では 1 回だけ止めて `finish` か続ける理由を促す（9.6）。記録の `decision` は `nudge` |
| `NUDGE_STOP_RULE` | メインエージェントの `Stop` で、`match: Stop` の `allow` のルールが渡す回になった（`every` の刻み）。止めて、ルールの文を渡す。`finish` の促しと重なった回は出ない（6.6）。記録の `decision` は `nudge` |
| `DENY_SUBAGENT_TICKET_OP` | サブエージェントがチケットの状態・レビュー・push を動かそうとした |
| `DENY_CHILD_PUSH` | 子チケットのワークツリーから `ccnavi-git.sh push` を打った。`cd` の行き先が読めない push を含む（9.10） |
| `DENY_SCRIPT_ENV_OVERRIDE` | 保護済みの sh を、sh の検査の材料を変える環境変数と同じコマンド行で呼んだ（8.2） |
| `POST_VIOLATION` / `POST_PREEXISTING` / `POST_TICKET_SCOPE` | 実行後チェック。保護領域の変更 / 前から在った変更 / チケットの範囲外 |

記録の `skip` の理由: `mode-disabled`、`event-not-checked`、`no-subject`、`nothing-to-run`、
`payload-unusable`、`deadline-exceeded`、`tool-cannot-write`、`worktree-unreadable`、`no-turn-baseline`。

## 付録 B. 記録の 1 行

`ts`（ISO 8601、ローカルのオフセット付き）、`mode`、`permission_mode`、`event`、`tool`、`subject`
（秘密の形を伏せ（4.7）、1000 字で切り `…(+N)`）、`decision`、`enforced`、`code`、`reason`、`degraded`、`unwrapped`（当たった中で実行されるコマンド。
`\x00` でつなぎ、1000 字で切る。6.3.1）、`fallback`、`detail`、
`tree`、`project`、`source`、`rules[]`、`quoted[]`、`paths[]`、`guarded[]`、`session`、`ms` の 23 欄。空欄は落とす。
`ts` / `mode` / `decision` / `enforced` / `ms` は常に出る。`O_APPEND` で 1 行を 1 回の write で書く。

## 付録 C. 実測で確かめた前提

設計が乗っている Claude Code の振る舞い。版が変わったら測り直す。

| 前提 | 実測 |
|---|---|
| `PreToolUse` で `permissionDecision` と `additionalContext` を 1 つの応答に入れたとき、両方がモデルに届くか | 届く（Claude Code 2.1.235）。deny と ask では `permissionDecisionReason` と一緒に届く |
| ask の `permissionDecisionReason` が誰に見えるか | ユーザの確認ダイアログに出る。Yes を押した後もモデルには届かない。No のときは拒否の定型文だけがモデルに届いてターンが終わる。allow の文面はどこにも出ない |
| `/compact` の後に `SessionStart` が来るか | `source: compact` の `SessionStart` が同じ `session_id` で届く |
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
| `isolation: worktree` で起動したサブエージェントの hook が受け取る `cwd` | 未実測。止めるかどうかは cwd で親を引くので、受け取る cwd によって止まるかどうかが分かれる |

### 入れ子のサブエージェント（Claude Code 2.1.282）

実測は上限 3 の `claude -p` でメイン → 子 → 孫を起動して見た。「文書」は公式文書
（hooks・sub-agents）の記述で、実測していない。

| 前提 | 実測・文書 |
|---|---|
| 入れ子の上限 | 文書: 既定は 3 層まで（`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`、1 で入れ子を無効）。実測: クラウドの環境（claude.ai/code）はプロセスの env で 1 を入れている（`.claude/settings.json` には無い） |
| 上限に達したサブエージェントに Agent ツールはあるか | 無い（実測）。遅延ツールの一覧にも出ない。起動を待ってハングすることはなく、普通に終わる |
| 孫は Agent ツールを持てるか | 持てる（実測、上限 3） |
| 孫の `PreToolUse` に `agent_id` が付くか | 付く（実測）。孫の Bash にも `DENY_SUBAGENT_TICKET_OP` が当たった |
| 深さ 1 のサブエージェントに `agent_id` が届くか | 届く（実測。`logs/state/approved-<セッション>-<agent_id>.json` のファイル名で確かめた） |
| `SubagentStart` / `SubagentStop` は孫でも来るか | 子・孫それぞれで来る（実測） |
| `logs/decisions.jsonl` の `session` | メイン・子・孫で同じ値（実測）。`decisions.jsonl` は `agent_id` / `agent_type` / `cwd` を記録しない。誰の呼び出しかを確かめる手掛かりは `logs/state/approved-<セッション>-<agent_id>.json` |
| サブエージェントの `cwd` | 文書: メインの `cwd`（`cd` は持ち越さない）。孫の `cwd` も親のワークツリーになる |
| 親の `agent_id` や深さは hook の入力にあるか | 文書: 無い |
| 起動側は子の終了を待つか | 文書: 非対話 / SDK では待たない |
| AskUserQuestion をサブエージェントに渡せるか | 文書: `tools` に書いても全部のサブエージェントから外される |
| 自動 compact で `SubagentStart` の文は残るか | 文書: 起動時に入れた文は compact で消えうる。同じサブエージェントの次の起動（再開）で `SubagentStart` がまた走り、文脈にその文が無ければ入れ直す |
| `PreCompact` / `PostCompact` | 文書: サブエージェントの compact でも来て、`agent_id` / `agent_type` を持つ。どちらも `systemMessage` を捨て、`additionalContext` を受けるとは書かれていない（`PostCompact` は decision control も無い）。メインの compact は別の文脈のサブエージェントに影響しない |

合成した payload で確かめた判定（同じ日）。書き込みは書き先のワークツリーで判定し `agent_id` を見ないので、
孫も子と同じに判定される。残る弱点は 4 つ。

| | 弱点 | 今の扱い |
|---|---|---|
| G2 | 孫の `cwd` が親のワークツリーなので、`SubagentStart` が親の子を全部並べ、`SubagentStop` が兄弟の子の変更でも差し戻す | フローの案内が、入れ子のプロンプトに担当の子・ワークツリー・範囲を書かせる（9.12）。緩和であって、防いではいない |
| G3 | 孫は、兄弟の子のワークツリーにもその範囲の中なら書ける | 同上 |
| G4 | 差し戻しを無視した知らせが、起動した子にしか届かない | 入れ子の起動なら `systemMessage` にも載せる（9.12） |
| G5 | Bash の実行後チェックは `cwd` のツリーだけを見る | そのまま |
