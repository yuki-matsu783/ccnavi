---
type: guide
title: 文面の移行の一覧
description: 拒否・促し・次の一手の文のうち、文面ファイルへ移したものと、まだコード・sh・ルールに残るもの。識別子、出力元、移したかの一覧
tags: [exe-boundary, sh-scripts]
keywords: [文面, 移行, 一覧, 進捗, 文面ファイル, DENY, NUDGE, 次:, ccnavi-start.sh, rules.yml, message, 組み込みルール, texts.load, キャッシュ, 冷たい読み, message_id, 残りの進め方]
---

# 文面の移行の一覧

文面ファイルへの移行は段階的に進める。移したら「移した」にし、移すときは[文面ファイルに文を足す・直す](message-files.md)の手順に従う。

## 移した

| 識別子 | 出力元 | 状態 |
|---|---|---|
| `DENY_TICKET_PARENT` | `hook/judge.py`（親のワークツリーへの書き込み） | 移した |
| `DENY_PHASE_REVIEW__WAITING`・`DENY_PHASE_REVIEW__REQUEST` | `tickets/phase.py` の `hold_reason` | 移した（並行の依頼の断り `PARALLEL_REQUEST_NOTE` と止めている間も通せる操作の案内 `EXEMPT_NOTE` は、`review.py` や `phase.py` の別の文と共有する定数なので、コードに残る） |
| `DENY_NEEDS_USER_TURN__INTEGRATE`・`DENY_NEEDS_USER_TURN__STATE` | `hook/turngate.py` の `message` | 移した（宣言の `Gate.message` が識別子を指す） |
| `NUDGE_INTEGRATE__UNKNOWN_MARKER`・`__CONFIRM`・`__ADOPT`・`__RUN`、`NOTE_INTEGRATE_*` | `tickets/ops_stop_integrate.py` の `integrate_nudge` | 移した（`blocked_notice` の `systemMessage` は残る） |
| `NEXT_START_*`・`NOTE_START_*`・`CHOICES_START_ISSUE_CLOSED` | `ccnavi-start.sh` の `次:`・`次の手:` と、それに続く `案内:` | 移した（`次:`・`次の手:` に続く `案内:` まで。続かない `案内:` は下の「残り」。状態を報告する行・`必須:`・`確認:`・`MCP で行う手順:` は次の一手ではないので残る） |
| `DENY_TICKET_PROJECT_MISMATCH`・`DENY_TICKET_BLOCKED`・`DENY_TICKET_SCOPE` | `hook/judge.py`（取り違え・チケットが成り立たない・範囲の外） | 移した（状態を報告する先頭の行 `subject:`・`ticket:`・`scope:`・`limit:`・`problem:` は残る。`DENY_TICKET_SCOPE` は上限ごとに `what` `why` `next` を `area`・`type`・`parent` で分ける） |
| `NOTE_WORKTREE_LEFTOVER*` | `ccnavi-worktree.sh`（親のワークツリーから直接作業へ切り替えたあとの取り下げの案内。残っているかは実行ファイルの `tree leftover` に聞く） | 移した（新規） |
| `NEXT_PF_*` | `ccnavi-common-host.sh` の `ccnavi_pf_next`（`--check`・失敗時の `次:`・`ccnavi-ticket.sh` の事前確認） | 移した（`integration` の文は共有の `ccnavi_integration_hint` を使うので残る） |
| `DENY_AGENT_*`（`ISOLATION`・`TARGET_INVALID`・`MULTI_TARGET`・`PARENT_TREE`・`NO_CHILD`・`CHILD_UNAVAILABLE`・`CWD_CONFLICT`） | `hook/agentlaunch.py`（`deny-agent.yml`） | 移した（`Mention.invalid` と `unavailable()` の理由の断片は、条件の判断と公開関数の戻り値に使うので、値としてコードに残る） |
| `DENY_SCRIPT_ENV_OVERRIDE`・`DENY_CHILD_PUSH__*`・`DENY_WRAPPER_CORE_PATH__*`・`DENY_SUBAGENT_PARENT_TREE__*`・`DENY_TICKET_FLOW_LOCKED`・`DENY_TICKET_APPROVAL_CLI__*`・`DENY_RECORDS_PRUNE` | `hook/wrapguard.py`・`tickets/flow.py`・`tickets/phase_forms.py`（`deny-guards.yml`） | 移した（1 件の長さの上限のため、`__<区別>` で識別子を分けたものがある。`phase_forms.py` の `EXEMPT_NOTE`・`TURN_DEFINED` は `phase.py` などと共有するのでコードに残る） |
| `DENY_BRACE_EXPANSION`・`DENY_COMMAND_NAME_EXPANSION`・`DENY_BACKQUOTE`・`DENY_AMBIGUOUS_FORM`・`DENY_SUBAGENT_TICKET_OP`・`DENY_INTERNAL_ERROR`・`DENY_PAYLOAD_UNUSABLE`・`DENY_DEADLINE_EXCEEDED`、`NOTE_RAN_BY`・`NOTE_INSIDE_QUOTES`・`NOTE_UNREADABLE__*`・`NOTE_REWRITE_HINT__*` | `hook/reasons.py`（`deny-reasons.yml`） | 移した（`fallen_back`・`undeclared` の本体・`internal_error_notice` は、種類を決められない英語の通知か止めない通知なので残る） |
| `RULE_ASK`・`RULE_ASK__PREFER_WEBFETCH`・`RULE_ASK__ADOPT` | `hook/reasons.py` の `ask_hint`・`_ASK_ALTERNATIVE`（`deny-reasons.yml`） | 移した（`--adopt` の「チケットに結び付く名前には通らない」と、対話のときは出さないことは保つ） |
| `DENY_REDIRECT_LIMIT` | `policy/selfguard_shell.py` の旧 `REDIRECT_LIMIT_MESSAGE`（`deny-policy.yml`。判定のときに `hook/judge.py` が引く） | 移した |
| `NUDGE_TICKET_FINISH`・`NUDGE_TICKET_REQUEST`・`NUDGE_TICKET_FEEDBACK`・`NUDGE_SUBAGENT_OUTCOME`・`NUDGE_STOP_RULE`・`NEXT_STANDING` | `tickets/ops_stop.py`・`ops_stop_request.py`・`ops_stop_feedback.py`・`hook/events.py`・`tickets/standing.py` の `guidance()`（`nudge-stop.yml`） | 移した（共有の `USER_STOP_NOTE` と `subagent_outcome.line()` は値としてコードに残る。`subagent_outcome` は下位の層で文面を引けないので、行だけを返す） |
| `NEXT_GIT_*`・`NEXT_WORKTREE_*`・`NEXT_STAGE_*`・`NEXT_TICKET_PROPOSED`・`NEXT_CLOSE_EARLY` | `ccnavi-git.sh`・`ccnavi-worktree.sh`・`ccnavi-stage.sh`・`ccnavi-ticket.sh`・`ccnavi-user-close-early.sh` の `次:`（`next-sh.yml`） | 移した（`ccnavi-common-state.sh` の `ccnavi_text` の既定の文は残る） |

## 残り

| 種類 | 出力元 | 数の目安 | 状態 |
|---|---|---|---|
| 組み込みの拒否（Python） | `hook/reasons.py` の `fallen_back`・`undeclared`（`_UNDECLARED_*`）・`internal_error_notice`（種類を決められない英語の通知）、`hook/wrapguard.py` などが組んで渡す他の文 | 少数 | 未移行（英語の文面の整理と一緒に進める） |
| 英語の文面 | `hook/post_findings.py`・`reasons.py` の一部（日本語と英語の混在の整理は別に進めている） | | 未移行 |
| 日本語の直書き（Python の拒否・次の一手） | `hook/rootwrite.py` の `message()`（`DENY_SHELL_WORKSPACE_ROOT`）、`hook/post.py`・`hook/post_report.py`（実行後の次の一手）、`hook/docsearch.py`、`tickets/approval_checks.py`・`agree_screen.py`・`ops.py`（`次:`）・`review.py`・`phase.py`（CLI の stderr の次の手）、`tickets/flow.py`（サブエージェント向けの手順）、`policy/selfguard.py` | 多数 | 未移行（2026-10 の調査で見つかった分。網羅ではない。数え直して、1 まとまりずつ移す） |
| sh の残りの `次の手:`・`次:`・`案内:` | `ccnavi-ticket.sh`（`次の手:` の選択肢と `案内:`）、`ccnavi-worktree.sh`（取り込みの記録が無い案内・横で作業する案内・了承を得るまでの案内）、`ccnavi-git.sh`（integrate の次の一手の残り）、`ccnavi-start.sh`（リモート未確認・テンプレート）、`ccnavi-stage.sh`・`ccnavi-sync.sh` の `案内:` | 約 40 | 未移行（状態の報告に近いものは残す。境目は[残りの進め方](#残りの進め方)の項目 3） |
| ルールの文 | `rules.yml` の `message`・`additionalContext`・`additionalContextOnce`（インライン） | | 未移行（識別子で引く `message_id` は持たない。`additionalContextFile` で md の本文を出せる） |
| 組み込みのルールの文 | `policy/builtin.py`・`policy/selfguard_shell.py` の `message`（`SHELL_MESSAGE`・`BINARY_MESSAGE`・`COMMON_LAYER_MESSAGE`・`RECORDS_MESSAGE`・`PROJECT_HOME_MESSAGE`・`HANDOFF_*_MESSAGE`・`add_named_rules` の 4 本・`builtin.py` の各ルール）、`tickets/ticket_guard.py`・`tickets/phase_forms.py` が `Rule` に直接入れる文 | 判定 1 回で 24 本 | 未移行。文は `rules.parse` に渡す dict の `message` か `Rule(message=…)` に文字列で入る。`texts.load` は署名（`os.listdir` と各ファイルの `os.stat`）でキャッシュするので、温まったあとの `texts.say` は 1 回約 40 µs で済む。ただしキャッシュはプロセスの中だけで、hook は 1 呼び出し 1 プロセスなので、組み立ての時点で引くと拒否に当たらない呼び出しにも冷たい読み（約 45 ms）が乗る。進め方は[残りの進め方](#残りの進め方)の項目 1 |
| 配布側の sh の `次:` | `scripts/ccnavi-doctor.sh` 2、`scripts/ccnavi-package.sh` 1、`scripts/ccnavi-install.sh`（`次:` を組み立てる関数）。配布の道具で、ワークスペースの実行ファイルが無いときにも動くため、`ccnavi text` に頼らない | | 対象外 |
| sh の拒否・失敗の文 | `fail "…"` の文（各 sh） | 約 210 | 移さない（[残りの進め方](#残りの進め方)の項目 3） |

## 残りの進め方

「残り」の表を、移すか移さないかと順に分ける。数と時間は、この checkout のコードで数え、Linux（Python 3.13）で測った値で、目安として読む。計測スクリプトはリポジトリに入れていない。`logs/decisions.jsonl` はこの checkout に無く、頻度の根拠はコードの呼び出し頻度で付けた。

### 測ったこと

| 測ったもの | 値 |
|---|---|
| 文面ファイル | 10 本、約 87 KB、118 件 |
| 冷たい `texts.load`（プロセスで最初の 1 回） | 約 44〜52 ms。うち YAML の読みが約 33 ms、検査が約 6 ms |
| 署名の検査（`os.listdir` と 10 本の `os.stat`） | 約 27 µs |
| 温まった `texts.load` と `texts.say` | それぞれ約 30〜50 µs |
| `say` を 16 回続けた時間 | 約 0.6〜0.9 ms |
| 実行前の判定 1 回の中身（`--test`、import 後） | `Bash ls` で中央値約 90 ms、`Read` で約 66〜84 ms |
| 同じ判定の前に冷たい `texts.load` を 1 回足した場合 | `Bash ls` で約 136〜145 ms、`Read` で約 103〜109 ms（約 +20〜55 ms） |

- 判定 1 回で組むルール 1 本ごとに `texts.say` を呼んでも、署名の検査にかかる時間は約 0.7 ms（27 µs × 24 本）にとどまる。ルールを組み立てる回数は、毎回の `stat`（署名の検査）の時間に影響しない
- 時間がかかるのは冷たい読みだけ。キャッシュは `texts` のモジュールの中にあり、hook は 1 呼び出しで 1 プロセス（`cli.run` が 1 回の起動で 1 判定）なので、呼び出しをまたいで温まらない。組み立てで引くと、拒否に当たらない大半の呼び出しにも約 45 ms が乗る。いまは拒否に当たった呼び出しだけがその時間を負担している
- `texts.say` 1 回の時間は、温まっていれば約 40 µs、冷たければ約 45 ms。以前の見積もりの約 2 ms は、どちらにも当たらない
- プロセス全体の壁時間は import が大半を占め（約 430 ms、ばらつき ±50 ms）、上の差は測定のばらつきに隠れる。差を見るには判定の中身だけを測る

### 数えたこと（項目 1 の材料）

- `policy/builtin.py` の `"message"` は 7 件（`_config_via_bash` 1、`_deny` 6）。`_config_via_bash` は組み込みの既定に戻ったときだけ足す。`.git` 向けの 2 本は同じ文（`_git_dir_message`）
- `policy/selfguard_shell.py` の `"message"` は 11 件（`*_MESSAGE` の定数 7、`add_named_rules` の 4 本）。実行ファイルの 1 本（`BINARY_MESSAGE`）は実行ファイルのパスがあるときだけ足す
- `Rule` を直接作って文を入れるものが、`tickets/ticket_guard.py` に 5 か所（判定では 7 本になる。状態の置き場、承認済みの置き場、操作記録の置き場、生の git。置き場の 3 つは名前で書くツールとシェルの 2 本が同じ文）、`tickets/phase_forms.py` に 1 か所（承認の操作）。`hook/post_findings.py` は英語の文で `Rule` を 1 本作る
- ラッパーの有無で文が分かれるのは、`builtin.py` の 5 系統（再帰削除、push、reset、`.git` の 2 本（同じ文）、設定のシェル書き込み）と `ticket_guard.py` の生の git
- 実行前の判定 1 回（`--test Bash ls`）で作られる `Rule` は 39 本。`message` つきは 29 本で、うち `builtin:` は 24 本（`builtin.py` 6、`selfguard_shell.py` 10、`ticket_guard.py` 7、`phase_forms.py` 1）。`Write`・`Read` では 23 本
- 組み立ての呼び出しは、判定 1 回で `builtin.load_base` 1、`selfguard_shell.add_rules` 1、`add_named_rules` 1、`rules.parse` 11〜12、`selfguard_shell._insert` 9〜10（`Bash` は 1 つ多い）。実行後の保護範囲（`events.watched_for`）は、触ったツリーごとに `load_rules` と `add_named_rules` を呼ぶ（測っていない。コードから）
- `Rule.spoken_message()` を呼ぶのは 3 か所: `hook/reasons.py` の `reason_for`、`hook/post_report.py` の `_messages`、`tickets/session_rejects.py`

### 項目 1: 組み込みルールの文

| 案 | 中身 | 利点 | 欠点 | コード量 | 性能 |
|---|---|---|---|---|---|
| (a) 識別子を持たせ、判定の時点で引く | 組み込みの `Rule` に `message_id` と値（と場合）を持たせ、`spoken_message` の呼び手が `texts.say` で引く | 組み立てで `texts` を触らないので、通る呼び出しに読みが乗らない。当たったときだけ引く（拒否の文はいまもそうしている）。識別子で `test_texts_catalog` の対象にできる | `Rule` に欄が 3 つ増える。`Rule.key()`（レイヤーをまたいだ同一判定）に入れる。`rules` が `texts` を import できない（`texts` が `rules` を import する）ので、引く関数を呼び手から渡す。呼び手 3 か所を直す | `Rule` と `_build` で約 +40 行、呼び手 3 か所で約 +20 行。組み立ての側は、文字列 24 本を `(識別子, 値)` に替えて約 −150 行。文面ファイルに 24 件ほど足す | 通る呼び出しは今と同じ。当たった呼び出しは冷たい読み約 45 ms（拒否に当たる他の文と同じで、増えない） |
| (b) 組み立ての時点で引く | `_deny`・`add_rules` などが `texts.say` を呼び、できた文字列を `message` に入れる | `Rule` を変えない。差分が最小 | 判定のたびに冷たい読みが加わる（+20〜55 ms、中身の約 1.3〜1.5 倍）。24 本の `say` で約 1 ms。文面ファイルが壊れていると、24 本すべてが既定の短い文になる | 組み立ての側の置き換えだけ（約 −150 行） | 全呼び出しに約 +20〜55 ms |
| (c) 遅れて評価する文字列 | `message` に、引くまで評価しない値を入れる | `Rule` の欄は 1 つ | `message` を文字列とみなす箇所（`rule_lint`、`entry/suggest.py`、`entry/diagnose_board.py`、`Rule.key()`）が型に合わなくなる。識別子が欄に見えず、点検が拾いにくい | 約 +60 行と、各所の分岐 | (a) と同じ |

推奨は (a)。決め手は性能で、(b) は全呼び出しに冷たい読みを足す。(c) は (a) の欄を隠すだけで、型が合わなくなる箇所が増える。

- 識別子は `RULE_` で始める（種類は `deny`）。名前は組み込みのルールの id から決める（`builtin:git-push` なら `RULE_GIT_PUSH`）。場合の分け（ラッパーの有無）は `what` の場合分けにする。`DENY_TICKET_SCOPE` の前例がある
- `message_id` と値は組み込みの `Rule`（`from_file` が偽のもの）にだけ持たせる。ルールファイルからは書けない。`rules.yml` に `message_id` を開かない（項目 2）
- `message` が空だと `rules.parse` が「message がない」と断る（`_NEEDS_MESSAGE` の検査）。`message_id` があれば通す変更を、`Rule` と `_build` の行数に含める。`message` を直接読む `rule_lint`・`entry/suggest.py`・`entry/diagnose_board.py`・`Rule.key()` には、空のまま渡さない。`key()` は識別子と値を含め、表示と検査は識別子を引いた文を使う
- 既存の文の `{root}` は値として渡す。`{script:<名前>}` は文面ファイルの書式にそのまま使える。`{名前}` などの差し込みと `{{` `}}` の扱いは文面ファイルの決まりに合わせる
- いまの文は 1 段落の自由文なので、`what` `why` `next` `no_bypass` の 4 欄に分け直す。欄は 1 つ 600 字まで、1 件で 2000 字までなので、長い文（承認済みの置き場の約 530 字。置き場のパスで変わる）は欄に分けて収める。分け直しは文の見直しにもなるので、レビューの負担はここがいちばん大きい
- 引く側は、`reasons.reason_for`・`post_report._messages`・`session_rejects` の 3 か所。`texts.say` が引けなかったときは既定の文が出て、判定は変わらない（`texts` の約束）
- 設計書 `docs/21_design/messages.md` の「`message_id` は持たない」の段と、それを決めた記録は、実装の PR で扱う。設計書は組み込みだけが持つと直し、決定の記録は既存のものを書き換えず、新しく1枚書く

### 項目 2: `rules.yml` の `message`

- `message_id` を持たない方針は変えない。ルールの書式の版（`rules.VERSION`）、レイヤーの合成、`--lint`、`--explain` に手を入れることになり、配布先が `rules.yml` に書く 1 本ずつの文は、その場で読める自由文のほうが書きやすい
- `rule_lint.py` に、文の型（what／why／next／迂回しない）の検査は足さない。自由文から型を見分けるには語を推測するしかなく、正しい 1 文（「ここは読み取り専用です」）を誤って違反にしてしまう。ADR 番号の検査も足さない。配布先は自分の ADR を持ってよく、番号を書くことが誤りではない
- 型は書き方の手引きで伝える。`.ccnavi/docs/grow-rules.md` が「なぜ止めたか＋代わりに何をするか」を既に言っている。足すなら、拒否の文の 4 欄（`stop-patterns.md` の型）を指す 1 行を加える
- 工数: 実装の PR は 0。手引きの 1 行は、項目 1 のどれかの PR に含めてよい

### 項目 3: 残りの仕分けと順

順は、エージェントに届く頻度の高い順。頻度はコードから読んだもの（呼び出しのたびに組まれるか、特定の操作でだけ出るか）で、記録の集計ではない。

| 順 | 出力元 | 仕分け | 根拠 |
|---|---|---|---|
| 1 | 組み込みのルールの文（`builtin.py`・`selfguard_shell.py`・`ticket_guard.py`・`phase_forms.py`） | 移す（項目 1） | 判定のたびに 24 本が組まれ、生の git・push・設定の書き込みなど、エージェントが最もよく当たると見込む拒否の文（当たった回数ではなく、組まれる回数からの推定） |
| 2 | `reasons.py` の `undeclared`・`fallen_back` | 移す（項目 4） | ルールが言及しない呼び出しのたびに返る。配布先では allow が増えるまで最も多い。`fallen_back` は設定を読めない間の全呼び出しに付く |
| 3 | `hook/post.py`・`post_report.py`・`post_findings.py` の次の一手 | 移す。`stderr.write` の状態の報告は残す | 実行後のチェックで、書き込みのたびに戻し方や取り込み前の確かめ方を言う。`post_findings.py` は英語の文。`post_report._messages` の組み込みルールの文は項目 1 で一緒に移る |
| 4 | `tickets/ops.py`・`agree_screen.py`（`次:`） | 移す | チケットの操作ごとに出る `次:`。`ops.py` に 4 件 |
| 5 | `tickets/approval_checks.py` | 移す | 承認の依頼の前提が満たされないときに返る。直し方を言う文が約 18 件 |
| 6 | sh の `案内:` のうち `ccnavi-git.sh`（`printf '案内:` が 15 本） | 移す | 取り込み（integrate）の経路で、チケットごとに出る。状態の報告（1 本）は残す |
| 7 | sh の `案内:` のうち `ccnavi-worktree.sh`（11 本）、`ccnavi-start.sh`（9 本）、`ccnavi-stage.sh`・`ccnavi-sync.sh`・`ccnavi-ticket.sh` | 移す。`ccnavi-dirs.sh` の見出し 2 本、`issue は closed`（状態）、依頼文を消した報告は残す | ワークツリーの作成・着手でチケットごとに 1 回 |
| 8 | `tickets/review.py`・`phase.py` の CLI の stderr の次の手 | 移す。`ccnavi: …を読めない` などの状態の報告は残す | レビューとフェーズの操作でだけ出る |
| 9 | `hook/rootwrite.py` の `message()`（`DENY_SHELL_WORKSPACE_ROOT`） | 移す | 1 件。`turngate` と同じく判定の時点で引けるので、仕組みの変更は要らない |
| 10 | `hook/docsearch.py` の使い方の案内（3 か所） | 移す。`stderr.write` の「引かない」理由は残す | `--docs` を使う場面でだけ出る |
| 11 | `policy/selfguard.py` の知らせ（約 6 件） | 移す | 設定ファイルを戻したときだけ出る。まれ |
| 12 | 英語の文面の `reasons.py` の残り | 項目 4 に含める | |
| — | sh の `fail "…"`（約 210 件。`fail <理由> "…"` の形を数えた: `ccnavi-start.sh` 88、`ccnavi-worktree.sh` 48、`ccnavi-review.sh` 36、`ccnavi-common-review.sh` 16、`ccnavi-user-decide.sh` 14、`ccnavi-branches.sh` 11） | 移さない | 失敗の理由の報告が大半で、次の一手は文ごとに違う。1 件ごとに実行ファイルを起こす（import を含めて約 0.4 秒）と失敗の経路が遅くなり、実行ファイルに不具合があるときに理由を言えなくなる。`ccnavi-user-*.sh` はユーザが打つのでエージェントに届かない。次の一手を含む `fail` が見つかったら、その `次:` の行だけを `ccnavi_text` に分けて移す |
| — | `stderr.write("ccnavi: …")` の診断（`post.py`・`docsearch.py`・`review.py` など） | 移さない | 内部の不具合や状態の報告で、次の一手ではない |
| — | `tickets/flow.py` のサブエージェント向けの手順 | 移さない | 値の断片で、`DENY_*` が引く共有の定数（移行の一覧の「移した」の注） |

### 項目 4: 種類を決められない通知

- `undeclared`・`fallen_back` は、新しい種類 `notice`（識別子の頭は `NOTICE_`）を足して移す。必須は `what`、任意は `why` `next` `note`。出力は `deny` と同じ見出し（`次: `）を使う。`nudge` に入れないのは、`nudge` の識別子は記録の `code` と同じという決まりがあり、`UNDECLARED` の見出し（`[ccnavi] UNDECLARED (source: …)`。読み切れなかった回は `PARSE_UNCERTAIN`）と `subject:` の行はコードに残るため。`undeclared` は、読み切れたかどうかと ask／deny の組（4 通り）と、`{then}` の 2 通りを場合分けにする
- 実装に入る前に、2 つを確かめる
  - `undeclared` は、ルールが言及せず ask か deny になった呼び出しのたびに文を組む（通した回と、判定を渡した回は組まない）。頻度は測っていない。移すと、その呼び出しに冷たい読み約 45 ms が加わる。冷たい読みは YAML の読みが大半（約 33 ms）なので、同じ PR の前に、文面ファイルを必要な 1 本だけ読む形にするかを測って決める。重複した識別子の検査は全ファイルを読む前提なので、その形にするなら検査を別（`--lint`）に回す。そのままにするなら、この数字を PR に書く
  - 英語の文面は整理が別に進んでいる。移すときは文をそのまま写す。言い換えは整理の側で、文面ファイルの上で直す
- `internal_error_notice` は移さない。ツールの呼び出しを持たないイベントで、ccnavi 自身に不具合があるときに出る文なので、不具合があるかもしれない文面ファイルに頼らない。まれで、止めない通知でもある
- 共有する定数の文（`PARALLEL_REQUEST_NOTE`・`EXEMPT_NOTE`・`USER_STOP_NOTE`・`TURN_DEFINED` など）は移さない。複数の文と、下位の層が使う値だからで、移行の一覧の「移した」の注と同じ扱い

### 工数の見積もり

実装の PR の数は、出力元の種類 1 つにつき 1 つ。

| 項目 | PR | 中身 |
|---|---|---|
| 1 | 3 | ① 仕組み（`Rule` の欄、`_build` の検査、`key()`、引く 3 か所、点検の拡張）と `builtin.py`。② `selfguard_shell.py`。③ `ticket_guard.py` と `phase_forms.py` |
| 2 | 0 | 方針は変えない。手引きの 1 行は項目 1 に含める |
| 3 | 8〜9 | 実行後の報告（`post*.py`）1〜2、`ops.py`・`agree_screen.py` 1、`approval_checks.py` 1、sh の `案内:`（`ccnavi-git.sh`、`ccnavi-worktree.sh`、`ccnavi-start.sh` とほか）3、`review.py`・`phase.py` 1、`rootwrite.py` と `docsearch.py` と `selfguard.py` 1 |
| 4 | 1〜2 | `notice` の種類と `undeclared`・`fallen_back`。冷たい読みを短くするなら、その PR を前に 1 つ |
| 合計 | 12〜14 | |

進める順は項目 3 の順。ただし項目 1 の仕組み（①）を最初に出す。`Rule` の欄と引く側の形がここで固まり、あとの PR はその形に合わせるだけになる。

## 点検

- `tests/core/test_texts_catalog.py` は、移した文の識別子と値の名前が、引くコード・sh と一致することを見る
- 移した出力のテストは、文面ファイルを引いた結果に対して書く（`次:` の行が 1 行、値が埋まっている）
- `test_texts_catalog.py` は、`say` の第 1 引数が定数の呼び出しだけを拾う。組み込みルールの `message_id` は `Rule` の欄に入るので、項目 1 の最初の PR で、組み込みの `Rule` の識別子と値の名前を点検の対象に足す
