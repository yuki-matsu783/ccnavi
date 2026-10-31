---
type: guide
title: 文面の移行の一覧
description: 拒否・促し・次の一手の文のうち、文面ファイルへ移したものと、まだコード・sh・ルールに残るもの。識別子、出力元、移したかの一覧
tags: [exe-boundary, sh-scripts]
keywords: [文面, 移行, 一覧, 進捗, 文面ファイル, DENY, NUDGE, 次:, ccnavi-start.sh, rules.yml, message, 組み込みルール, texts.load, message_id]
---

# 文面の移行の一覧

文面ファイルへの移行は段階的に進める。移したら「移した」の表へ動かし、移すときは[文面ファイルに文を足す・直す](message-files.md)の手順に従う。表は「移した」「残り」の2つだけにする。

## 移した

| 識別子 | 出力元 | 備考 |
|---|---|---|
| `DENY_TICKET_PARENT` | `hook/judge.py`（親のワークツリーへの書き込み） | |
| `DENY_PHASE_REVIEW__WAITING`・`DENY_PHASE_REVIEW__REQUEST` | `tickets/phase/gates.py` の `hold_reason` | 並行の依頼の断り `PARALLEL_REQUEST_NOTE` と止めている間も通せる操作の案内 `EXEMPT_NOTE` は、`tickets/review/request.py` や `tickets/phase/gates.py` の別の文と共有する定数なので、コードに残る |
| `DENY_NEEDS_USER_TURN__INTEGRATE`・`DENY_NEEDS_USER_TURN__STATE`・`NOTICE_ASK_USER_CONFIRM__INTEGRATE` | `hook/turngate.py` の `message`・`ask_message` | 宣言の `Gate.message`・`Gate.ask_message` が識別子を指す |
| `NUDGE_INTEGRATE__UNKNOWN_MARKER`・`__ADOPT`・`__RUN`、`NOTE_INTEGRATE_*` | `tickets/lifecycle/stop_integrate.py` の `integrate_nudge` | `blocked_notice` の `systemMessage` は残る |
| `NEXT_START_*`・`NOTE_START_*`・`CHOICES_START_ISSUE_CLOSED` | `ccnavi-start.sh` の `次:`・`次の手:` と、それに続く `案内:` | `次:`・`次の手:` に続く `案内:` まで。続かない `案内:` は下の「残り」。状態を報告する行・`必須:`・`確認:`・`MCP で行う手順:` は次の一手ではないので残る |
| `DENY_TICKET_PROJECT_MISMATCH`・`DENY_TICKET_BLOCKED`・`DENY_TICKET_SCOPE` | `hook/judge.py`（取り違え・チケットが成り立たない・範囲の外） | 状態を報告する先頭の行 `subject:`・`ticket:`・`scope:`・`limit:`・`problem:` は残る。`DENY_TICKET_SCOPE` は上限ごとに `what` `why` `next` を `area`・`type`・`parent` で分ける |
| `NOTE_WORKTREE_LEFTOVER*` | `ccnavi-worktree.sh`（親のワークツリーから直接作業へ切り替えたあとの取り下げの案内。残っているかは実行ファイルの `tree leftover` に聞く）。切り替えを断る `NOTE_WORKTREE_PARENT_*` も同じ |  |
| `NEXT_PF_*` | `ccnavi-common-host.sh` の `ccnavi_pf_next`（`--check`・失敗時の `次:`・`ccnavi-ticket.sh` の事前確認） | `integration` の文は共有の `ccnavi_integration_hint` を使うので残る |
| `DENY_AGENT_*`（`ISOLATION`・`TARGET_INVALID`・`MULTI_TARGET`・`PARENT_TREE`・`NO_CHILD`・`CHILD_UNAVAILABLE`・`CWD_CONFLICT`） | `hook/agentlaunch.py`（`deny-agent.yml`） | `Mention.invalid` と `unavailable()` の理由の断片は、条件の判断と公開関数の戻り値に使うので、値としてコードに残る |
| `DENY_SCRIPT_ENV_OVERRIDE`・`DENY_CHILD_PUSH__*`・`DENY_WRAPPER_CORE_PATH__*`・`DENY_SUBAGENT_PARENT_TREE__*`・`DENY_TICKET_FLOW_LOCKED`・`DENY_TICKET_APPROVAL_CLI__*`・`DENY_RECORDS_PRUNE` | `hook/wrapguard.py`・`tickets/flow/graph.py`・`tickets/model/phase_forms.py`（`deny-guards.yml`） | 1 件の長さの上限のため、`__<区別>` で識別子を分けたものがある。`tickets/model/phase_forms.py` の `EXEMPT_NOTE`・`TURN_DEFINED` は `tickets/phase/gates.py` などと共有するのでコードに残る |
| `DENY_BRACE_EXPANSION`・`DENY_COMMAND_NAME_EXPANSION`・`DENY_BACKQUOTE`・`DENY_AMBIGUOUS_FORM`・`DENY_SUBAGENT_TICKET_OP`・`DENY_INTERNAL_ERROR`・`DENY_PAYLOAD_UNUSABLE`・`DENY_DEADLINE_EXCEEDED`、`NOTE_RAN_BY`・`NOTE_INSIDE_QUOTES`・`NOTE_UNREADABLE__*`・`NOTE_REWRITE_HINT__*` | `hook/reasons.py`（`deny-reasons.yml`） | `fallen_back`・`undeclared` の本体・`internal_error_notice` は、種類を決められない英語の通知か止めない通知なので残る |
| `RULE_ASK`・`RULE_ASK__PREFER_WEBFETCH`・`RULE_ASK__ADOPT` | `hook/reasons.py` の `ask_hint`・`_ASK_ALTERNATIVE`（`deny-reasons.yml`） | `--adopt` の「チケットに結び付く名前には通らない」と、対話のときは出さないことは保つ |
| `DENY_REDIRECT_LIMIT` | `policy/selfguard_shell.py` の `REDIRECT_LIMIT_MESSAGE`（`deny-policy.yml`。判定のときに `hook/judge.py` が引く） | |
| `NUDGE_TICKET_FINISH`・`NUDGE_TICKET_REQUEST`・`NUDGE_TICKET_FEEDBACK`・`NUDGE_SUBAGENT_OUTCOME`・`NUDGE_STOP_RULE`・`NEXT_STANDING` | `tickets/lifecycle/stop.py`・`tickets/lifecycle/stop_request.py`・`tickets/lifecycle/stop_feedback.py`・`hook/events.py`・`tickets/model/standing.py` の `guidance()`（`nudge-stop.yml`） | 共有の `USER_STOP_NOTE` と `subagent_outcome.line()` は値としてコードに残る。`subagent_outcome` は下位の層で文面を引けないので、行だけを返す |
| `NEXT_GIT_*`・`NEXT_WORKTREE_*`・`NEXT_STAGE_*`・`NEXT_TICKET_PROPOSED`・`NEXT_CLOSE_EARLY` | `ccnavi-git.sh`・`ccnavi-worktree.sh`・`ccnavi-stage.sh`・`ccnavi-ticket.sh`・`ccnavi-user-close-early.sh` の `次:`（`next-sh.yml`） | `ccnavi-common-state.sh` の `ccnavi_text` の既定の文は残る。手が複数の `NEXT_GIT_MERGE_ON_TARGET`・`NEXT_GIT_INTEGRATE_REVIEW`・`NEXT_STAGE_EDIT` は、1 手目を `next`、後を `note` に分けた |
| `NOTE_GIT_*`・`NOTE_WORKTREE_*`（`RECORD_MISSING`・`ADOPT_CODE_STALE`・`CODE_STALE`・`SIDE_BY_SIDE`・`WAIT_FOR_OK`・`REMOTE_BEHIND`・`REMOTE_UNVERIFIED`・`PROJECTS_HINT`）・`NOTE_START_REMOTE_UNCHECKED`・`NOTE_START_TEMPLATES`・`NOTE_STAGE_NONINTERACTIVE`・`NOTE_SYNC_RETIRED_UNANSWERED`・`NOTE_TICKET_NEW_ISSUE` | `ccnavi-git.sh`（integrate・integrate-done・merge・pull・worktree remove の `案内:`）、`ccnavi-worktree.sh`・`ccnavi-start.sh`・`ccnavi-stage.sh`・`ccnavi-sync.sh`・`ccnavi-ticket.sh` の次の一手を含む `案内:`（`note-sh.yml`） | `printf` ではなく文字列の中にあった `案内:` も `$(ccnavi_text …)` に替えた。実行ファイルが古いなどで引けないときは、`ccnavi_text` の一般文（`案内: 文面 <識別子> を引けませんでした`）に落ちる |
| `RULE_*`（`RULE_RECURSIVE_DELETE`・`RULE_GIT_PUSH`・`RULE_GUARD_*`・`RULE_TICKET_*`・`RULE_RAW_GIT`・`RULE_CREDENTIALS`・`RULE_CONFIG_VIA_BASH`） | `policy/builtin.py`・`policy/selfguard_shell.py`・`tickets/model/ticket_guard.py`・`tickets/model/phase_forms.py` の組み込みルールの文（`deny-builtin-rules.yml`） | ルールが識別子と値と場合を持ち、拒否に当たった回に `texts.spoken` が引く。`hook/post_findings.py` の英語の文は `RULE_TICKET_SCOPE__*` として `deny-post.yml` に英語のまま写した |
| `NOTICE_FALLEN_BACK`・`NOTICE_UNDECLARED__*`（`ASK`・`DENY`・`UNREADABLE_ASK`・`UNREADABLE_DENY`） | `hook/reasons.py` の `fallen_back`・`undeclared`（`notice.yml`） | 新しい種類 `notice`。見出し `[ccnavi] UNDECLARED (source: …)` と `subject:` の行はコードに残る。`undeclared` は結末（ask／deny）と、読み切れたかどうかで識別子を分け、退避した回は `what` の `fell_back` を選ぶ。`undeclared` が組まれるのは、どのルールも言及せず ask か deny になった呼び出しのときで、そこに文面ファイルの冷たい読みが加わる |
| `NOTICE_POST_*`・`NOTE_POST_APPROVED_AREA`・`NOTE_POST_PREEXISTING`・`RULE_TICKET_SCOPE__*` | `hook/post.py`・`post_report.py`・`post_findings.py` の実行後の報告（`notice.yml`・`deny-post.yml`） | `path:`・`tree:`・`after:` などの状態の行と、ターンの終わりの報告の「どのパスを戻すか」の断片はコードに残る。`note:` の行は `案内:` で出る。`RULE_TICKET_SCOPE__*` は英語の文のまま |
| `DENY_SHELL_WORKSPACE_ROOT` | `hook/rootwrite.py`（`deny-post.yml`） | 4 欄に分けたので、`なぜ:`・`次:`・`迂回しない:` の行で出る。`迂回しない` は元の文に無く、足した |
| `NOTICE_DOCS_GUIDE`・`NOTICE_DOCS_FAIL_STREAK_LEAD`・`NOTICE_DOCS_FRONTMATTER__*` | `catalog/docsearch.py` の索引の案内と、frontmatter が無い md を書いたときの案内（`notice.yml`） | `stderr` の診断は残る |
| `NEXT_OPS_*`・`NEXT_AGREE_FOLLOWUP`・`NEXT_REVIEW_MR_DIFFERS`・`NEXT_APPROVAL_*` | `tickets/lifecycle/commands.py`（着手・クローズ・完了の `次:`）、`tickets/proposal/agree_screen.py`（承認後の `次:`）、`tickets/review/request.py`（`mr:` に別の値があるときの警告）、`tickets/approval/checks.py`（提案の置き場の誤りで承認しない理由）、`tickets/core/approve.py`（承認を頼む前の検査で提案を直す `NEXT_APPROVAL_FIX_PROPOSAL`。後の手は `案内:`）（`next-cli.yml`） | 状態の報告はコードに残し、次の一手だけを引く。`tickets/approval/checks.py` の 2 件は `Problem.detail` が 2 行になる |

## 残り

| 種類 | 出力元 | 状態 |
|---|---|---|
| 組み込みの拒否（Python） | `hook/reasons.py` の `internal_error_notice`（ツールの呼び出しを持たないイベントで、ccnavi 自身に不具合があるときの文。文面ファイルに頼らない）、`hook/wrapguard.py` などが組んで渡す他の文 | 移さない（`internal_error_notice`）。ほかは未移行 |
| 英語の文面 | `hook/reasons.py` の一部 | 未移行（日本語と英語の混在の整理は別に進めている。移すときは文をそのまま写す） |
| 日本語の直書き（Python の拒否・次の一手） | `tickets/proposal/propose.py`（計画の誤りで断るときの `次:`）、`tickets/approval/checks.py` の残り（`root` を持たず `Problem.detail` に入る文）、`tickets/review/request.py` の残り（問題の一覧の要素・`root` を持たない関数の文）、`tickets/phase/gates.py`（`review_next`・`_next_hint` の状態を伝える文）、`tickets/flow/graph.py`（サブエージェント向けの手順）、`policy/selfguard.py`（設定ファイルを戻したときの知らせ） | 未移行。1 まとまりずつ移す。`hook/post.py` の状態を報告する行と、`catalog/docsearch.py` の `stderr` の診断は移さない |
| 共有する定数の文 | `PARALLEL_REQUEST_NOTE`・`EXEMPT_NOTE`・`USER_STOP_NOTE`・`TURN_DEFINED` など、複数の出力と下位の層が使う値 | 移さない（コードに残る） |
| sh の残りの `次の手:`・`次:`・`案内:` | `ccnavi-ticket.sh` の `次の手:` の選択肢、`ccnavi-git.sh`（`ig_note`）・`ccnavi-worktree.sh`・`ccnavi-start.sh` の状態を報告する `案内:`、`ccnavi-dirs.sh`・`ccnavi-review.sh` の `案内:` | 移さない（状態の報告に近い）。次の一手を含む `案内:` が見つかったら、その行だけを `ccnavi_text` に分けて移す |
| ルールの文 | `rules.yml` の `message`・`additionalContext`・`additionalContextOnce`（インライン） | 移さない（識別子で引く `message_id` を持たない。`additionalContextFile` で md の本文を出せる） |
| 配布側の sh の `次:` | `scripts/ccnavi-doctor.sh`・`scripts/ccnavi-package.sh`・`scripts/ccnavi-install.sh` | 対象外（配布の道具で、ワークスペースの実行ファイルが無いときにも動くため、`ccnavi text` に頼らない） |
| sh の拒否・失敗の文 | `fail "…"` の文（各 sh） | 移さない（失敗の理由の報告が大半で、次の一手は文ごとに違う。1 件ごとに実行ファイルを起こすと失敗の経路が遅くなり、実行ファイルに不具合があるときに理由を言えなくなる）。次の一手を含む `fail` が見つかったら、その `次:` の行だけを `ccnavi_text` に分けて移す |
