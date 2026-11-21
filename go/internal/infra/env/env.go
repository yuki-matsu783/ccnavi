package env

// Prefix は ccnavi の変数の名前の頭。sh はワークスペースルートと実行ファイルをこの頭の変数から読む。
const Prefix = "CCNAVI_"

// Claude Code が hook に渡す変数。
const (
	ClaudeProjectDir      = "CLAUDE_PROJECT_DIR"
	ClaudeCodeEntrypoint  = "CLAUDE_CODE_ENTRYPOINT"
	ClaudeSessionAttended = "CLAUDE_CODE_SESSION_ATTENDED"
	// ClaudeSessionID は Claude Code が Bash のコマンドに渡すセッションの識別子（hook の session_id と同じ値）。
	// サブコマンドが、打ったセッションの記録を引くのに使う。
	ClaudeSessionID = "CLAUDE_CODE_SESSION_ID"
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

// テストの道具（harness/exe）が読む。組まずに使う実行ファイルのパス。
const (
	TestExe       = "CCNAVI_TEST_EXE"
	TestChromeExe = "CCNAVI_TEST_CHROME_EXE"
	TestStubExe   = "CCNAVI_TEST_STUB_EXE"
)

// Compat は sh（ccnavi-common.sh）が互換の版を宣言する変数。環境からは読まず、`--lint` が sh の中の代入を読む。
const Compat = "CCNAVI_COMPAT"

// 時間の上限。
const (
	AskContextWaitSeconds     = "CCNAVI_ASK_CONTEXT_WAIT_SECONDS"
	HookDeadlineSeconds       = "CCNAVI_HOOK_DEADLINE_SECONDS"
	IntegratePendingSeconds   = "CCNAVI_INTEGRATE_PENDING_SECONDS"
	StopReviewTTLSeconds      = "CCNAVI_STOP_REVIEW_TTL_SECONDS"
	SubagentRunningMaxSeconds = "CCNAVI_SUBAGENT_RUNNING_MAX_SECONDS"
	TurnStaleSeconds          = "CCNAVI_TURN_STALE_SECONDS"
	FetchTimeout              = "CCNAVI_FETCH_TIMEOUT"
	// WriteAndPushCommitTimeout は write-and-push のコミット 1 回の秒（既定 60）。
	WriteAndPushCommitTimeout = "CCNAVI_WRITE_AND_PUSH_COMMIT_TIMEOUT"
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

// git が読む変数。ccnavi が起こす git に渡す・外すもの（infra/git）。
const (
	// GitOptionalLocks=0 は status が index のついでの更新に取る index.lock を止める。
	GitOptionalLocks = "GIT_OPTIONAL_LOCKS"
	// GitNoLazyFetch=1 は partial clone の遅延取得を止める。
	GitNoLazyFetch = "GIT_NO_LAZY_FETCH"
	// pathspec の読み方を変える変数。
	GitLiteralPathspecs = "GIT_LITERAL_PATHSPECS"
	GitGlobPathspecs    = "GIT_GLOB_PATHSPECS"
	GitNoglobPathspecs  = "GIT_NOGLOB_PATHSPECS"
	GitIcasePathspecs   = "GIT_ICASE_PATHSPECS"
	// 見るリポジトリ・ワークツリー・索引を決める変数。git の hook の中などで設定されている。
	GitDir       = "GIT_DIR"
	GitWorkTree  = "GIT_WORK_TREE"
	GitIndexFile = "GIT_INDEX_FILE"
	GitCommonDir = "GIT_COMMON_DIR"
	// 設定ファイル（~/.gitconfig と /etc/gitconfig）の置き場。テストが走った機械の設定を締め出すのに使う。
	GitConfigGlobal   = "GIT_CONFIG_GLOBAL"
	GitConfigSystem   = "GIT_CONFIG_SYSTEM"
	GitConfigNoSystem = "GIT_CONFIG_NOSYSTEM"
	// Msys2ArgConvExcl は Git for Windows の sh（MSYS）が、ネイティブの exe に渡す引数を Windows のパスに書き換えるのを止める接頭辞の並び。
	Msys2ArgConvExcl = "MSYS2_ARG_CONV_EXCL"
)

// ccnavi の外が決める変数。リモートの API に付けるトークンと、実行ファイルの探し方・家の場所・端末の幅。
const (
	GitHubToken = "GITHUB_TOKEN"
	GitLabToken = "GITLAB_TOKEN"
	Path        = "PATH"
	// Tmpdir は POSIX の一時置き場（Windows は TMP・TEMP）。
	Tmpdir      = "TMPDIR"
	Home        = "HOME"
	UserProfile = "USERPROFILE"
	HomeDrive   = "HOMEDRIVE"
	HomePath    = "HOMEPATH"
	UserName    = "USERNAME"
	Columns     = "COLUMNS"
)
