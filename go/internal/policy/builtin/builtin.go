package builtin

import (
	"ccnavi/internal/infra/config"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/policy/texts"
)

// 組み込みのルールの id。上の段が名指しで見るものだけを公開する。
const (
	// ConfigViaBashRuleID は Bash からガード自身の設定へ書き込む形。ルールファイルが読めないと既定に戻るので、
	// シェルの書き込みで壊して保護の甘い既定に戻す手順を成り立たせないために、読めない間も止める。
	// 止めるのは書き込む形だけで、場所の名前では止めない（`git add`・`git restore --ours` でマージの衝突を
	// 解く手を止めると、ガードが機能しない状態から出られなくなる）。
	ConfigViaBashRuleID = "builtin:guard-config-via-bash"
	// GitDirRuleID と GitDirShellRuleID は `.git` の中身を直接書き換える形（名指しのツールとシェル）。
	// 適用する先（パスとコマンド文字列）が違うので id を分ける。読むだけは止めない。
	GitDirRuleID      = "builtin:guard-git-dir"
	GitDirShellRuleID = "builtin:guard-git-dir-via-bash"

	recursiveDeleteRuleID = "builtin:recursive-delete"
	gitPushRuleID         = "builtin:git-push"
	gitResetHardRuleID    = "builtin:git-reset-hard"
	credentialsRuleID     = "builtin:credentials"
	readAnythingRuleID    = "builtin:read-anything"
)

const (
	// Fallback は既定に戻ったことを記録に残す値。数えれば、ガードが機能しないまま何回動いたかが分かる。
	Fallback = "builtin:rules"
	// Source は既定を使っているときに返す理由に載せる出所。読めなかったファイルの名前を出すと、
	// 見に行ったユーザが適用されたルールを見つけられない。
	Source = "(ccnavi built-in defaults)"
)

// 文面ファイルの識別子と、ラッパーの有無で分かれる欄。
const (
	textConfigViaBash   = "RULE_CONFIG_VIA_BASH"
	textRecursiveDelete = "RULE_RECURSIVE_DELETE"
	textGitPush         = "RULE_GIT_PUSH"
	textGitResetHard    = "RULE_GIT_RESET_HARD"
	textCredentials     = "RULE_CREDENTIALS"
	textGuardGitDir     = "RULE_GUARD_GIT_DIR"

	fieldNote = "note"
	fieldWhy  = "why"
	fieldNext = "next"
)

const (
	matchBash = "Bash"
	// matchCredentials は常に効く土台で設定で切れないので、誤検知を承知で広げる。Grep・Glob・PowerShell は
	// 入れない（探す先のパスは探索の中身を代表しない）。
	matchCredentials = "Bash|Read|Write|Edit|NotebookEdit"
	matchWriteTools  = "Write|Edit|NotebookEdit"
	matchRead        = "Read"
	globAnything     = "*"
)

// Load は共通レイヤーが読めないときの既定（土台の deny・シェルから設定へ書き込む形・Read を通す allow）。
// シェルの書き込みと照合する場所は呼び出しごとに実際の設定から組む（実行ファイル・ccnavi ディレクトリ・
// 共通レイヤーは設定で動くので、固定の形だと動かしたワークスペースでだけ保護が外れる）。
// 苦情はこのビルドの不備なので、呼び手はそのまま報告してよい。
func Load(root string, conf *config.Settings) (*rules.RuleSet, []rules.Problem) {
	git, wrapper := config.GitCommand(root), config.HasGitWrapper(root)
	specs := append([]rules.Spec{{
		ID:    ConfigViaBashRuleID,
		Match: matchBash,
		// selfguard と同じ形を 1 か所から取る。写すと既定のほうだけが弱くなる。
		Regex: selfguard.ShellRegex(root, conf.Bin, conf.ProjectHome, selfguard.CommonLayerFiles(conf)),
		// 競合の解き方の一文はラッパーがあるときだけ書く（無いときは生の git が止まるので、ユーザに頼む）。
		Text: texts.Spec(textConfigViaBash, texts.SpecOptions{Git: git, Wrapper: wrapper, Cased: []string{fieldNote}}),
	}}, denySpecs(git, wrapper)...)
	deny, problems := rules.Builtin(rules.Deny, specs, root)
	allow, more := rules.Builtin(rules.Allow, allowSpecs, root)
	return &rules.RuleSet{Version: rules.Version, Deny: deny, Allow: allow}, append(problems, more...)
}

// LoadBase は常に効く組み込みの deny（土台）。共通レイヤーと config のルールはこの上に足す。
// root は文面の git の打ち方を決めるだけで、止める形は変えない。
func LoadBase(root string) (*rules.RuleSet, []rules.Problem) {
	deny, problems := rules.Builtin(rules.Deny, denySpecs(config.GitCommand(root), config.HasGitWrapper(root)), root)
	for _, r := range deny {
		r.Base = true
	}
	return &rules.RuleSet{Version: rules.Version, Deny: deny}, problems
}

// ReadAllow は Read を通す allow。共通レイヤーと config が両方無いときだけ足す。
func ReadAllow() []*rules.Rule {
	allow, _ := rules.Builtin(rules.Allow, allowSpecs, "")
	return allow
}

// denySpecs は設定で動かない deny。文面の git の打ち方だけがワークスペースで変わる。
func denySpecs(git string, wrapper bool) []rules.Spec {
	cased := func(id string, fields ...string) rules.TextRef {
		return texts.Spec(id, texts.SpecOptions{Git: git, Wrapper: wrapper, Cased: fields})
	}
	return []rules.Spec{
		{ID: recursiveDeleteRuleID, Match: matchBash, Regex: recursiveDeleteRegex, Text: cased(textRecursiveDelete, fieldNext)},
		{ID: gitPushRuleID, Match: matchBash, Regex: gitPushRegex, Text: cased(textGitPush, fieldWhy, fieldNext)},
		{ID: gitResetHardRuleID, Match: matchBash, Regex: gitResetHardRegex, Text: cased(textGitResetHard, fieldNext)},
		{ID: credentialsRuleID, Match: matchCredentials, Regex: credentialsRegex, Text: texts.Spec(textCredentials, texts.SpecOptions{})},
		{ID: GitDirRuleID, Match: matchWriteTools, Regex: selfguard.GitDirPathRegex, Text: cased(textGuardGitDir, fieldNext)},
		{ID: GitDirShellRuleID, Match: matchBash, Regex: selfguard.GitDirShellRegex(), Text: cased(textGuardGitDir, fieldNext)},
	}
}

// allowSpecs は Read だけを通す。Grep・Glob は入れない（credentials の deny の match に無いので、ここで
// 広く許すと認証情報ごと通る）。
var allowSpecs = []rules.Spec{{ID: readAnythingRuleID, Match: matchRead, Glob: globAnything}}
