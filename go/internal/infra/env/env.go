package env

// Prefix は ccnavi の変数の名前の頭。sh はワークスペースルートと実行ファイルをこの頭の変数から読む。
const Prefix = "CCNAVI_"

// ClaudePrefix は Claude Code の変数の名前の頭。テストの子の環境から外すのに使う。
const ClaudePrefix = "CLAUDE_"

// Claude Code が hook に渡す変数。
const (
	ClaudeProjectDir      = "CLAUDE_PROJECT_DIR"
	ClaudeCodeEntrypoint  = "CLAUDE_CODE_ENTRYPOINT"
	ClaudeEnvFile         = "CLAUDE_ENV_FILE"
	ClaudeSessionAttended = "CLAUDE_CODE_SESSION_ATTENDED"
	// ClaudeSessionID は Claude Code が Bash のコマンドに渡すセッションの識別子（hook の session_id と同じ値）。
	// サブコマンドが、打ったセッションの記録を引くのに使う。
	ClaudeSessionID = "CLAUDE_CODE_SESSION_ID"
)

// 起動の場所と、hook・sh が実行ファイルへ渡す文脈。
const (
	EntryPath = "CCNAVI_ENTRY_PATH"
	// Workspace は走らせる人がワークスペースルートを上書きする変数（sh の探し方の抜け道）。
	Workspace = "CCNAVI_WORKSPACE"
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
	// TestShDir・TestDist は e2e の出どころ（測る sh の置き場と組み立て済みの dist/ccnavi）を差し替える。
	TestShDir = "CCNAVI_TEST_SH_DIR"
	TestDist  = "CCNAVI_TEST_DIST"
)

// テストの道具（harness/fakeclaude）が読む。claude -p のスタブの台本のパスと、セッションの記録の置き場。
const (
	FakeClaudeScript = "CCNAVI_FAKE_CLAUDE_SCRIPT"
	FakeClaudeHome   = "CCNAVI_FAKE_CLAUDE_HOME"
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

// 手動の GitLab の道具（Docker の GitLab のトークンのファイル）が使う人間役の資格情報の名前。
const (
	ProbeReviewerToken = "CCNAVI_PROBE_REVIEWER_TOKEN"
	ProbeReviewerID    = "CCNAVI_PROBE_REVIEWER_ID"
	ProbeRootID        = "CCNAVI_PROBE_ROOT_ID"
	// GitLabPort は Docker の GitLab の公開ポート（既定 8929）。ProbeGitLab は probe・契約の録りの接続先（既定はこのポートの 127.0.0.1）。
	GitLabPort  = "CCNAVI_GITLAB_PORT"
	ProbeGitLab = "CCNAVI_PROBE_GITLAB"
	// ProbePrefix は probe が作るプロジェクト名の前置き、ProbeOut は結果の置き場、ProbeKeep が空でなければ作ったプロジェクトを消さない。
	ProbePrefix = "CCNAVI_PROBE_PREFIX"
	ProbeOut    = "CCNAVI_PROBE_OUT"
	ProbeKeep   = "CCNAVI_PROBE_KEEP"
	// XdgCacheHome があれば、手動の GitLab の状態のファイルはその下に置く。
	XdgCacheHome = "XDG_CACHE_HOME"
)

// 手動の GitHub の道具（harness/cmd/githubprobe）が読む、実験場と出力の場所。
const (
	ProbeRepo      = "CCNAVI_PROBE_REPO"
	ProbeRepoOK    = "CCNAVI_PROBE_REPO_OK"
	ProbeStrictExe = "CCNAVI_PROBE_STRICT_EXE"
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
