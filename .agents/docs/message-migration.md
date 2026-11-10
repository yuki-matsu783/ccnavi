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
| `DENY_TICKET_PARENT` | `go/internal/hook/judge`（親のワークツリーへの書き込み） | |
| `DENY_PHASE_REVIEW__WAITING`・`DENY_PHASE_REVIEW__REQUEST` | `go/internal/tickets/phase` の `HoldReason` | 並行の依頼の断り `ParallelRequestNote` と止めている間も通せる操作の案内 `ExemptNote` は、`tickets/review/request` や `tickets/phase` の別の文と共有する定数なので、コードに残る |
| `DENY_NEEDS_USER_TURN__INTEGRATE`・`DENY_NEEDS_USER_TURN__REVIEW_CONFIRM`・`DENY_NEEDS_USER_TURN__STATE`・`NOTICE_ASK_USER_CONFIRM__INTEGRATE` | `go/internal/hook/turngate` の `Message`・`AskMessage` | 宣言の `Gate` の `Message`・`AskMessage` が識別子を指す |
| `NUDGE_INTEGRATE__UNKNOWN_MARKER`・`__ADOPT`・`__RUN`、`NOTE_INTEGRATE_*` | `go/internal/tickets/lifecycle/integrate` の `IntegrateNudge` | `BlockedNotice` の `systemMessage` は残る |
| `NEXT_START_*`・`NOTE_START_*`・`CHOICES_START_ISSUE_CLOSED` | `ccnavi-start.sh` の `次:`・`次の手:` と、それに続く `案内:` | `次:`・`次の手:` に続く `案内:` まで。続かない `案内:` は下の「残り」。状態を報告する行・`必須:`・`確認:`・`MCP で行う手順:` は次の一手ではないので残る |
| `DENY_TICKET_PROJECT_MISMATCH`・`DENY_TICKET_BLOCKED`・`DENY_TICKET_SCOPE` | `go/internal/hook/judge` の `tickets.go`（取り違え・チケットが成り立たない・範囲の外） | 状態を報告する先頭の行 `subject:`・`ticket:`・`scope:`・`limit:`・`problem:` は残る。`DENY_TICKET_SCOPE` は上限ごとに `what` `why` `next` を `area`・`type`・`parent` で分ける |
| `NOTE_WORKTREE_LEFTOVER*` | `ccnavi-worktree.sh`（親のワークツリーから直接作業へ切り替えたあとの取り下げの案内。残っているかは実行ファイルの `tree leftover` に聞く）。切り替えを断る `NOTE_WORKTREE_PARENT_*` も同じ |  |
| `NEXT_PF_*` | `ccnavi-common-host.sh` の `ccnavi_pf_next`（`--check`・失敗時の `次:`・`ccnavi-ticket.sh` の事前確認） | `integration` の文は共有の `ccnavi_integration_hint` を使うので残る |
| `DENY_AGENT_*`（`ISOLATION`・`MULTI_TARGET`・`PARENT_TREE`・`NO_CHILD`・`CHILD_UNAVAILABLE`・`CWD_CONFLICT`） | `go/internal/hook/launch`（`deny-agent.yml`） | `unavailable` の理由の断片は、条件の判断と公開関数の戻り値に使うので、値としてコードに残る |
| `DENY_SCRIPT_ENV_OVERRIDE`・`DENY_CHILD_PUSH__*`・`DENY_WRAPPER_CORE_PATH__*`・`DENY_SUBAGENT_PARENT_TREE__*`・`DENY_TICKET_FLOW_LOCKED`・`DENY_TICKET_APPROVAL_CLI__*`・`DENY_RECORDS_PRUNE` | `go/internal/hook/shellguard`・`go/internal/tickets/flow`・`go/internal/tickets/model/useronly`（`deny-guards.yml`） | 1 件の長さの上限のため、`__<区別>` で識別子を分けたものがある。`tickets/model/useronly` の `ExemptNote`・`TurnDefined` は `tickets/phase` などと共有するのでコードに残る |
| `DENY_BRACE_EXPANSION`・`DENY_COMMAND_NAME_EXPANSION`・`DENY_BACKQUOTE`・`DENY_AMBIGUOUS_FORM`・`DENY_SUBAGENT_TICKET_OP`・`DENY_INTERNAL_ERROR`・`DENY_PAYLOAD_UNUSABLE`・`DENY_DEADLINE_EXCEEDED`、`NOTE_RAN_BY`・`NOTE_INSIDE_QUOTES`・`NOTE_UNREADABLE__*`・`NOTE_REWRITE_HINT__*` | `go/internal/hook/reasons`（`deny-reasons.yml`） | `FallenBack`・`Undeclared` の本体・`InternalErrorNotice` は、種類を決められない英語の通知か止めない通知なので残る |
| `RULE_ASK`・`RULE_ASK__PREFER_WEBFETCH`・`RULE_ASK__ADOPT` | `go/internal/hook/reasons` の `AskHint`・`askAlternative`（`deny-reasons.yml`） | `--adopt` の「チケットに結び付く名前には通らない」と、対話のときは出さないことは保つ |
| `DENY_REDIRECT_LIMIT` | `go/internal/hook/judge`（`deny-policy.yml`。上限は `go/internal/policy/selfguard` の `RedirectLimit`） | |
| `NUDGE_TICKET_FINISH`・`NUDGE_TICKET_REQUEST`・`NUDGE_TICKET_FEEDBACK`・`NUDGE_SUBAGENT_OUTCOME`・`NUDGE_STOP_RULE`・`NEXT_STANDING` | `go/internal/tickets/lifecycle/stop`・`go/internal/hook/events` の `stop.go`・`go/internal/tickets/model/standing` の `Guidance`（`nudge-stop.yml`） | 共有の `UserStopNote` と `hook/outcome` の 1 行は値としてコードに残る。`hook/outcome` は下位の層で文面を引けないので、行だけを返す |
| `NEXT_GIT_*`・`NEXT_WORKTREE_*`・`NEXT_STAGE_*`・`NEXT_TICKET_PROPOSED`・`NEXT_READY_DONE` | `ccnavi-git.sh`・`ccnavi-worktree.sh`・`ccnavi-stage.sh`・`ccnavi-ticket.sh`・`ccnavi-review.sh`（ready の成功後）の `次:`（`next-sh.yml`） | `ccnavi-common-state.sh` の `ccnavi_text` の既定の文は残る |
| `NOTE_GIT_*`・`NOTE_WORKTREE_*`（`RECORD_MISSING`・`ADOPT_CODE_STALE`・`CODE_STALE`・`SIDE_BY_SIDE`・`WAIT_FOR_OK`・`REMOTE_BEHIND`・`REMOTE_UNVERIFIED`・`PROJECTS_HINT`）・`NOTE_START_REMOTE_UNCHECKED`・`NOTE_START_TEMPLATES`・`NOTE_STAGE_NONINTERACTIVE`・`NOTE_SYNC_RETIRED_UNANSWERED`・`NOTE_TICKET_NEW_ISSUE` | `ccnavi-git.sh`（integrate・integrate-done・merge・pull・worktree remove の `案内:`）、`ccnavi-worktree.sh`・`ccnavi-start.sh`・`ccnavi-stage.sh`・`ccnavi-sync.sh`・`ccnavi-ticket.sh` の次の一手を含む `案内:`（`note-sh.yml`） | `printf` ではなく文字列の中にあった `案内:` も `$(ccnavi_text …)` に替えた。実行ファイルが古いなどで引けないときは、`ccnavi_text` の一般文（`案内: 文面 <識別子> を引けませんでした`）に落ちる |
| `RULE_*`（`RULE_RECURSIVE_DELETE`・`RULE_GIT_PUSH`・`RULE_GUARD_*`・`RULE_TICKET_*`・`RULE_RAW_GIT`・`RULE_CREDENTIALS`・`RULE_CONFIG_VIA_BASH`） | `go/internal/policy/builtin/builtin.go`・`go/internal/policy/selfguard/rules.go`・`go/internal/tickets/model/guard`・`go/internal/tickets/model/useronly` の組み込みルールの文（`deny-builtin-rules.yml`） | 識別子と値と場合はルール側にあり、拒否に当たった回に `texts.Spoken` が引く。`go/internal/hook/post` の `findings.go` の英語の文は `RULE_TICKET_SCOPE__*` として `deny-post.yml` に英語のまま写した |
| `NOTICE_FALLEN_BACK`・`NOTICE_UNDECLARED__*`（`ASK`・`DENY`・`UNREADABLE_ASK`・`UNREADABLE_DENY`） | `go/internal/hook/reasons` の `FallenBack`・`Undeclared`（`notice.yml`） | 新しい種類 `notice`。見出し `[ccnavi] UNDECLARED (source: …)` と `subject:` の行はコードに残る。`Undeclared` は結末（ask／deny）と、読み切れたかどうかで識別子を分け、退避した回は `what` の `fell_back` を選ぶ。`Undeclared` が組まれるのは、どのルールも言及せず ask か deny になった呼び出しのときで、そこに文面ファイルの冷たい読みが加わる |
| `NOTICE_POST_*`・`NOTE_POST_APPROVED_AREA`・`NOTE_POST_PREEXISTING`・`RULE_TICKET_SCOPE__*` | `go/internal/hook/post` の実行後の報告（`notice.yml`・`deny-post.yml`） | `path:`・`tree:`・`after:` などの状態の行と、ターンの終わりの報告の「どのパスを戻すか」の断片はコードに残る。`note:` の行は `案内:` で出る。`RULE_TICKET_SCOPE__*` は英語の文のまま |
| `DENY_SHELL_WORKSPACE_ROOT` | `go/internal/hook/shellguard` の `rootwrite.go`（`deny-post.yml`） | 4 欄に分けたので、`なぜ:`・`次:`・`迂回しない:` の行で出る。`迂回しない` は元の文に無く、足した |
| `NOTICE_DOCS_GUIDE`・`NOTICE_DOCS_FAIL_FIRST_LEAD`・`NOTICE_DOCS_FAIL_STREAK_LEAD`・`NOTICE_DOCS_FRONTMATTER__*` | `go/internal/catalog/docs` の `AtStart`・`FailStreakNote`・`MissingFrontmatter`（索引の案内と、frontmatter が無い md を書いたときの案内。`notice.yml`） | `stderr` の診断は残る |
| `NEXT_OPS_*`・`NEXT_AGREE_FOLLOWUP`・`NEXT_REVIEW_MR_DIFFERS`・`NEXT_APPROVAL_*` | `go/internal/tickets/` の `lifecycle/ops`（着手・クローズ・完了の `次:`）、`proposal/agree`（承認後の `次:`）、`review/request`（`mr:` に別の値があるときの警告）、`approval`（提案の置き場の誤りで承認しない理由）、`core`（承認を頼む前の検査で提案を直す `NEXT_APPROVAL_FIX_PROPOSAL`。後の手は `案内:`）（`next-cli.yml`） | 状態の報告はコードに残し、次の一手だけを引く。`tickets/approval` の 2 件は `Problem.detail` が 2 行になる |
| `DENY_RETIRE_PARENT` | `go/internal/tickets/lifecycle/retire` の `RunRetire`（親のワークツリーを消せないとき、子に触る前に断る。`deny-retire.yml`） | 理由（cwd・未コミット・無視ファイル・そのほか）で `what` と `next` を場合分けする。判定は `worktrees.Blocked` |
| `NOTICE_DIRECT_COMMIT` | `go/internal/hook/hint` の `CommitNotice` の直接作業のコミットのあとの案内（`notice.yml`。メインの commit の成功後と、Agent ツールの完了後の両方が同じ関数で出す） | 見出しは無く、`what` の 1 行と `次:`。回数の数え方は `ctxfile` の `additionalContextOnce` と同じ |
| `NOTICE_DIRECT_WORKTREES_HEAD`・`_MORE`・`_TAIL` | `go/internal/tickets/notice` の `DirectWorktrees` の SessionStart の一覧（`notice.yml`） | 1 本ごとの行（名前・未コミット・コミット数・記録の有無）はコードに残る |
| `NOTICE_LAYER_UNREADABLE` | `go/internal/hook/events` の `start.go`（読めないレイヤーの SessionStart の知らせ。`notice.yml`） | `what` の 1 行。`layers` は差し込み |
| `NOTICE_SCRATCHPAD_LARGE` | `go/internal/tickets/notice` の `Scratchpad` の SessionStart の容量の知らせ（`notice.yml`） | `what` の 1 行。大きさ・しきい値・パスは差し込み |
| `NEXT_OPS_START_PARENT`・`NEXT_OPS_START_CHILDLESS`・`NEXT_OPS_RECORD_RISK`・`NEXT_OPS_REFUSED`・`NEXT_OPS_REFUSED__CANCEL`・`NEXT_REVIEW_REQUEST_REFUSED`・`NEXT_DECIDE_REFUSED`・`NEXT_CONFIRM_REFUSED`・`NEXT_READY_REFUSED`・`NEXT_OPS_DROP_OUTSIDE`・`NEXT_REVIEW_CONFIRMED`・`NEXT_REVIEW_LAST`・`NEXT_REVIEW_REQUESTED`・`NEXT_DECIDE_FOLLOWUP`・`NEXT_APPROVAL_REQUEST_CHECKED`・`NEXT_PLAN_VERIFY`・`NEXT_LEARN_*`・`NEXT_NEW_REWRITE`・`NOTE_NEW_*`・`NEXT_REVISE`・`NOTE_REVISE_FEEDBACK`・`NEXT_VERIFY_*`・`NEXT_CONSOLIDATE`・`NEXT_TEMPLATE_DRAFT` | `go/internal/tickets/` の `lifecycle/ops`・`lifecycle/plan`・`lifecycle/worktrees`・`lifecycle/consolidate`・`core`・`review/request`・`review/decide`・`review/closeout`・`proposal/propose`・`proposal/scaffold`・`proposal/agree`・`learning` と、`go/internal/entry/cli`（`--fill-phases`・`--plan-order`・テンプレートの下書き）の `次:`・補足の `案内:`（`next-cli.yml`） | 成功したのに次を言わなかった操作に足し、直書きの `次:` は文面ファイルへ移した。`NEXT_DECIDE_FOLLOWUP` は値として `followupNext` の文を渡す。`decide` が出す指示と文を共有するため。`NEXT_REVIEW_LAST` は `次:` を `finish` の 1 行にし、後の手は `案内:` に置いた |

| `NEXT_APPROVAL_REQUEST_PLAN_WRONG` | `go/internal/tickets/proposal/propose` の `approval-request` が計画の誤りで断るときの `次:`（`next-cli.yml`） | 行頭の `次: ` を直書きする箇所が無くなったので、`tests/core/test_next_line_literal.py` の許可リストは空 |
| `NOTICE_PHASE_NEXT__*`・`NOTICE_REVIEW_NEXT__*` | `go/internal/tickets/phase` の `nextHint`（フェーズが終わったあとの次のフェーズの案内）と `ReviewNext`（レビュー待ちの子の次の操作。`finish` の出力と `status`）（`notice.yml`） | `what` の 1 行だけで出るので、`次の操作:` などの見出しは呼ぶ側が付けたまま。`nextHint` は `root` を取る |
| `NOTICE_REVIEW_REQUEST_DEFERRED`・`NOTICE_REVIEW_CHANGES_REQUESTED`・`NOTICE_REVIEW_UNRESOLVED__DECIDE` | `go/internal/tickets/review/request` の依頼・確認を断る行のうち、直し方を含む 1 行のもの（`notice.yml`） | 最後のフィードバック対応のレビューの複数行の選択肢は残る |
| `NOTICE_FLOW_BRIEF__*` | `go/internal/tickets/flow` の `Briefing`（サブエージェントに渡す、問い・繰り返し・サブエージェントのノードの扱い。`notice.yml`） | 行頭の空白はコードが付ける |
| `NOTICE_SELFGUARD__*` | `go/internal/policy/selfguard/report.go` の `Report`（設定ファイルを戻したときの知らせに足す注意。`notice.yml`） | 見出しと結果の行はコードに残る。`Report` は `root` を取る |
| `ERROR_NO_PARENT_WORKTREE`・`ERROR_ISSUE_IDENTIFIER`・`ERROR_USER_ENTRY` | `go/internal/tickets/approval` の `NoParentError`、`go/internal/tickets/model/ids` の `IssueError`、`go/internal/tickets/model/useronly` の `EntryError`（`error.yml`） | 新しい種類 `error`。エラーが識別子と値を運び（`go/internal/infra/fault`）、`go/internal/entry/cli` の `finish` と入口が `texts.SayFault` で引く。`ERROR_USER_ENTRY`・`ERROR_ISSUE_IDENTIFIER` の `what` には、どこを直すかを言う `detail` を差し込む。ボードの `go/internal/entry/chrome` は `IssueError` を 1 行の `What` で出す |

## 残り

| 種類 | 出力元 | 状態 |
|---|---|---|
| 組み込みの拒否（実行ファイル） | `go/internal/hook/reasons` の `InternalErrorNotice`（ツールの呼び出しを持たないイベントで、ccnavi 自身に不具合があるときの文。文面ファイルに頼らない）、`go/internal/hook/shellguard` などが組んで渡す他の文 | 移さない（`InternalErrorNotice`）。ほかは未移行 |
| 英語の文面 | `go/internal/hook/reasons` の一部 | 未移行（日本語と英語の混在の整理は別に進めている。移すときは文をそのまま写す） |
| dry-run の英語の文 | `go/internal/hook/judge/refuse.go` の `dryRunHoldsNote` と `[ccnavi dry-run] enable would have … this call:` の頭（`go/internal/infra/config` の `DryRunWould`）、`go/internal/hook/events` の差し戻しの頭（`… would have sent this back as a correction:`）、`go/internal/hook/post/report.go` の `would:` の行 | 未移行（英語のまま。移すときは文をそのまま写す） |
| 日本語の直書き（実行ファイルの拒否・次の一手） | `go/internal/tickets/approval` の `Problem` の詳しい文に入る文（`MRProblems`・親の識別子の衝突・先行の苦情・`MisplacedHomeText`）、`go/internal/tickets/review/request` の `MergeProblems`・`RemoveWip` の問題の文と、最後のフィードバック対応のレビューで未解決のスレッドを断る複数行の選択肢、`go/internal/tickets/flow` の状態を報告する行（書き換わった知らせ・読めない理由）、`go/internal/tickets/phase` の `Announce` の準備中の子へのレビューの依頼の案内（人間レビュー要の子・リスクの行・依頼の対象の断片を 1 文に組む） | 移さない。理由と直し方が 1 つの文に混ざった理由の文で、分けると理由の断片になる。`root` を受け取らない関数の文や、後ろに別の文を足して使う呼び出し（`MisplacedHomeText` は `ready` が続きを足す）もある。複数行の選択肢は 1 欄 1 行の形に収まらない。次の一手だけが文の末尾に切り出せるものが見つかったら、その部分だけを移す。`go/internal/hook/post` の `report.go` の状態を報告する行と、`go/internal/catalog/docs` の `Run` の `stderr` の診断も移さない |
| 共有する定数の文 | `PARALLEL_REQUEST_NOTE`・`EXEMPT_NOTE`・`USER_STOP_NOTE`・`TURN_DEFINED` など、複数の出力と下位の層が使う値 | 移さない（コードに残る） |
| sh の残りの `次の手:`・`次:`・`案内:` | `ccnavi-ticket.sh` の `次の手:` の選択肢、`ccnavi-git.sh`（`ig_note`）・`ccnavi-worktree.sh`・`ccnavi-start.sh` の状態を報告する `案内:`、`ccnavi-dirs.sh`・`ccnavi-review.sh` の `案内:` | 移さない（状態の報告に近い）。次の一手を含む `案内:` が見つかったら、その行だけを `ccnavi_text` に分けて移す |
| ルールの文 | `rules.yml` の `message`・`additionalContext`・`additionalContextOnce`（インライン） | 移さない（識別子で引く `message_id` を持たない。`additionalContextFile` で md の本文を出せる） |
| 配布側の sh の `次:` | `scripts/ccnavi-doctor.sh`・`scripts/ccnavi-package.sh`・`scripts/ccnavi-install.sh` | 対象外（配布の道具で、ワークスペースの実行ファイルが無いときにも動くため、`ccnavi text` に頼らない） |
| sh の拒否・失敗の文 | `fail "…"` の文（各 sh） | 移さない（失敗の理由の報告が大半で、次の一手は文ごとに違う。1 件ごとに実行ファイルを起こすと失敗の経路が遅くなり、実行ファイルに不具合があるときに理由を言えなくなる）。次の一手を含む `fail` が見つかったら、その `次:` の行だけを `ccnavi_text` に分けて移す |
