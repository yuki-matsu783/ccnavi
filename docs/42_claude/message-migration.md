---
type: guide
title: 文面の移行の一覧
description: 拒否・促し・次の一手の文のうち、文面ファイルへ移したものと、まだコード・sh・ルールに残るもの。識別子、出力元、移したかの一覧
tags: [exe-boundary, sh-scripts]
keywords: [文面, 移行, 一覧, 進捗, 文面ファイル, DENY, NUDGE, 次:, ccnavi-start.sh, rules.yml, message]
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
| `NEXT_START_*`・`NOTE_START_*`・`CHOICES_START_ISSUE_CLOSED` | `ccnavi-start.sh` の `次:`・`次の手:` と、それに続く `案内:` | 移した（全部。状態を報告する行・`必須:`・`確認:`・`MCP で行う手順:` は次の一手ではないので残る） |
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
| 日本語の直書き（Python の拒否・次の一手） | `hook/rootwrite.py` の `message()`（`DENY_SHELL_WORKSPACE_ROOT`）、`hook/post.py`・`hook/post_report.py`（実行後の次の一手）、`hook/docsearch.py`、`tickets/propose.py`（計画の誤りで断るときの `次:`）、`tickets/ticket_guard.py`（状態の置き場・承認済みの置き場・生の git の拒否）、`tickets/approval_checks.py`・`agree_screen.py`・`ops.py`（`次:`）・`review.py`・`phase.py`（CLI の stderr の次の手）、`tickets/flow.py`（サブエージェント向けの手順）、`policy/selfguard.py` | 多数 | 未移行（2026-10 の調査で見つかった分。網羅ではない。数え直して、1 まとまりずつ移す） |
| sh の残りの `次の手:`・`次:`・`案内:` | `ccnavi-ticket.sh`（`次の手:` の選択肢と `案内:`）、`ccnavi-worktree.sh`（取り込みの記録が無い案内・横で作業する案内・了承を得るまでの案内）、`ccnavi-git.sh`（integrate の次の一手の残り）、`ccnavi-start.sh`（リモート未確認・テンプレート）、`ccnavi-stage.sh`・`ccnavi-sync.sh` の `案内:` | 約 20 | 未移行（状態の報告に近いものは残してよい。境目を見て移す） |
| ルールの文 | `rules.yml` の `message`・`additionalContext`・`additionalContextOnce`（インライン） | | 未移行（識別子で引く `message_id` は持たない。`additionalContextFile` で md の本文を出せる） |
| 組み込みのルールの文 | `policy/builtin.py`・`policy/selfguard_shell.py` の `message`（`SHELL_MESSAGE`・`BINARY_MESSAGE`・`COMMON_LAYER_MESSAGE`・`RECORDS_MESSAGE`・`PROJECT_HOME_MESSAGE`・`HANDOFF_*_MESSAGE`・`add_named_rules` の 4 本・`builtin.py` の各ルール） | 約 20 | 未移行。文は `rules.parse` に渡す dict の `message` に文字列で入り、あとで `Rule.message` になる。`texts.say` は呼ぶたびに文面ファイル全部を読む（キャッシュが無い）ので、ルールの組み立て（hook の呼び出しごと）には持ち込めない。移すには `Rule` に文面の識別子と値を持たせ、`reasons.reason_for` が判定の時点で引く設計が要る |
| 配布側の sh の `次:` | `scripts/ccnavi-doctor.sh` 2、`scripts/ccnavi-package.sh` 1、`scripts/ccnavi-install.sh`（`次:` を組み立てる関数）。配布の道具で、ワークスペースの実行ファイルが無いときにも動くため、`ccnavi text` に頼らない | | 対象外 |
| sh の拒否・失敗の文 | `fail "…"` の文（各 sh） | 多数 | 未移行 |

## 点検

- `tests/core/test_texts_catalog.py` は、移した文の識別子と値の名前が、引くコード・sh と一致することを見る
- 移した出力のテストは、文面ファイルを引いた結果に対して書く（`次:` の行が 1 行、値が埋まっている）
