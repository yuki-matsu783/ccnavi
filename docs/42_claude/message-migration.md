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
| `NEXT_PF_*` | `ccnavi-common-host.sh` の `ccnavi_pf_next`（`--check`・失敗時の `次:`・`ccnavi-ticket.sh` の事前確認） | 移した（`integration` の文は共有の `ccnavi_integration_hint` を使うので残る） |

## 残り

| 種類 | 出力元 | 数の目安 | 状態 |
|---|---|---|---|
| 組み込みの拒否（Python） | `hook/reasons.py`（`reason_for` の組み立て・`subagent_forbidden`・`rewrite`・`internal_error` ほか）、`hook/judge.py` の `DENY_TICKET_SCOPE`・`DENY_TICKET_BLOCKED`・`DENY_TICKET_PROJECT_MISMATCH` ほか、`hook/agentlaunch.py` の `DENY_AGENT_*`、`hook/wrapguard.py`、`tickets/flow.py`、`tickets/phase_forms.py` | 約 30 コード | 未移行 |
| 英語の文面 | `hook/post_report.py`・`hook/post_findings.py`・`reasons.py` の一部（日本語と英語の混在の整理は別に進めている） | | 未移行 |
| Stop の促し | `tickets/ops_stop.py`（`NUDGE_TICKET_FINISH`）、`ops_stop_request.py`（`NUDGE_TICKET_REQUEST`）、`ops_stop_feedback.py`（`NUDGE_TICKET_FEEDBACK`）、`hook/subagent_outcome.py`、`hook/events.py`（`NUDGE_STOP_RULE`）、`tickets/standing.py` の `guidance()` | 6 | 未移行 |
| ルールの文 | `rules.yml` の `message`・`additionalContext`・`additionalContextOnce`（インライン） | | 未移行（識別子で引く `message_id` は持たない。`additionalContextFile` で md の本文を出せる） |
| 組み込みのルールの文 | `policy/builtin.py`・`policy/selfguard_shell.py` の `message` | | 未移行 |
| 確認の理由 | `hook/reasons.py` の `ask_hint`・`_ASK_ALTERNATIVE` | 2 | 未移行 |
| sh の `次:` | `ccnavi-git.sh` 4、`ccnavi-worktree.sh` 3、`ccnavi-stage.sh` 2、`ccnavi-ticket.sh` 2、`ccnavi-common-state.sh` 2（`ccnavi_text` の既定の文）、`ccnavi-user-close-early.sh` 1 | 約 14 | 未移行（`ccnavi-common-state.sh` の既定の文は残す） |
| 配布側の sh の `次:` | `scripts/ccnavi-doctor.sh` 2、`scripts/ccnavi-package.sh` 1、`scripts/ccnavi-install.sh`（`次:` を組み立てる関数）。配布の道具で、ワークスペースの実行ファイルが無いときにも動くため、`ccnavi text` に頼らない | | 対象外 |
| sh の拒否・失敗の文 | `fail "…"` の文（各 sh） | 多数 | 未移行 |

## 点検

- `tests/core/test_texts_catalog.py` は、移した文の識別子と値の名前が、引くコード・sh と一致することを見る
- 移した出力のテストは、文面ファイルを引いた結果に対して書く（`次:` の行が 1 行、値が埋まっている）
