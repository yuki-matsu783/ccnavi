// Package env は ccnavi が読む環境変数の名前を 1 か所に集める。
//
// 名前はここの定数だけで書き、コードに文字列で直書きしない。値の読み方（既定値・範囲・単位）は、
// その値を使う責務のパッケージが持つ（設定は infra/settings、時間の上限は infra/tunables など）。
// git に渡す GIT_* は git の起動の決まりなので infra/git が持つ。
package env

// Claude Code が hook に渡す変数。
const (
	ClaudeProjectDir      = "CLAUDE_PROJECT_DIR"
	ClaudeCodeEntrypoint  = "CLAUDE_CODE_ENTRYPOINT"
	ClaudeSessionAttended = "CLAUDE_CODE_SESSION_ATTENDED"
)

// 起動の場所と、hook・sh が実行ファイルへ渡す文脈。
const (
	EntryPath = "CCNAVI_ENTRY_PATH"
	Root      = "CCNAVI_ROOT"
	Docs      = "CCNAVI_DOCS"
	DenyIDs   = "CCNAVI_DENY_IDS"
	Ticket    = "CCNAVI_TICKET"
	Parent    = "CCNAVI_PARENT"
	Head      = "CCNAVI_HEAD"
	BaseSHA   = "CCNAVI_BASE_SHA"
)

// 判定と保護の切り替え。
const (
	Mode                 = "CCNAVI_MODE"
	TicketControl        = "CCNAVI_TICKET_CONTROL"
	BranchPrefixes       = "CCNAVI_BRANCH_PREFIXES"
	DenyRepeat           = "CCNAVI_DENY_REPEAT"
	FailStreakCount      = "CCNAVI_FAIL_STREAK_COUNT"
	RestoreIfDeny        = "CCNAVI_RESTORE_IF_DENY"
	GuardCoreFiles       = "CCNAVI_GUARD_CORE_FILES"
	GuardTicketApproval  = "CCNAVI_GUARD_TICKET_APPROVAL"
	GuardUnwatched       = "CCNAVI_GUARD_UNWATCHED"
	StopReview           = "CCNAVI_STOP_REVIEW"
	StopReviewAgent      = "CCNAVI_STOP_REVIEW_AGENT"
	HandoffSuggestTokens = "CCNAVI_HANDOFF_SUGGEST_TOKENS"
	HandoffWaitSeconds   = "CCNAVI_HANDOFF_WAIT_SECONDS"
	Learning             = "CCNAVI_LEARNING"
	LearningNoticeCount  = "CCNAVI_LEARNING_NOTICE_COUNT"
	LearningSources      = "CCNAVI_LEARNING_SOURCES"
	ScratchpadWarnMB     = "CCNAVI_SCRATCHPAD_WARN_MB"
)

// 時間の上限。
const (
	AskContextWaitSeconds     = "CCNAVI_ASK_CONTEXT_WAIT_SECONDS"
	HookDeadlineSeconds       = "CCNAVI_HOOK_DEADLINE_SECONDS"
	IntegratePendingSeconds   = "CCNAVI_INTEGRATE_PENDING_SECONDS"
	StopReviewTTLSeconds      = "CCNAVI_STOP_REVIEW_TTL_SECONDS"
	SubagentRunningMaxSeconds = "CCNAVI_SUBAGENT_RUNNING_MAX_SECONDS"
	TurnStaleSeconds          = "CCNAVI_TURN_STALE_SECONDS"
	FetchTimeout              = "CCNAVI_FETCH_TIMEOUT"
)

// git のラッパーの記録。
const (
	GitFailLines = "CCNAVI_GIT_FAIL_LINES"
	GitKeepLogs  = "CCNAVI_GIT_KEEP_LOGS"
	GitMaxLines  = "CCNAVI_GIT_MAX_LINES"
)

// 記録の保持と診断ログ。
const (
	LogLevel           = "CCNAVI_LOG_LEVEL"
	LogKeepDays        = "CCNAVI_LOG_KEEP_DAYS"
	LogRotateMB        = "CCNAVI_LOG_ROTATE_MB"
	StateKeepDays      = "CCNAVI_STATE_KEEP_DAYS"
	StopReviewKeepDays = "CCNAVI_STOP_REVIEW_KEEP_DAYS"
)
