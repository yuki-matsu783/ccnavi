package config

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"

	"github.com/BurntSushi/toml"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
)

// 置き場の既定（ワークスペースルートからの相対）。env でも上書き設定でも動かない。
// 診断で動かせるのはフラグだけで、hook は引数を渡さないので判定の入口はここから動かない。
var (
	DefaultLog   = filepath.Join("logs", "decisions.jsonl")
	DefaultRules = filepath.Join(DefaultProjectHome, ConfigDir, "common", "rules.yml")
	// DefaultState はセッションごとの一時的な状態。消えても次の起動で取り直せる。
	DefaultState = filepath.Join("logs", "state")
	// DirectTmpDir は integrate が一時ワークツリーを置く場所（下にリポジトリごとの名前）。sh も同じ置き場を使う。
	DirectTmpDir  = filepath.Join("logs", "direct-tmp")
	defaultPhases = filepath.Join(DefaultProjectHome, ConfigDir, "common", "phases.yml")
	defaultRisk   = filepath.Join(DefaultProjectHome, ConfigDir, "common", "risks.yml")

	// LocalClaudeSettings は個人の上書き設定。`env` は Claude Code が起こしたプロセスに渡る。
	LocalClaudeSettings = filepath.Join(ClaudeDir, "settings.local.json")
	// SharedClaudeSettings はリポジトリで共有する Claude Code の設定（hook の登録）。
	SharedClaudeSettings = filepath.Join(ClaudeDir, "settings.json")
)

const (
	// DefaultTickets は提案の置き場（各ワークツリーのルートからの相対、"/" 区切り）。
	DefaultTickets = "wip/proposals"
	// DefaultApproved は承認済みチケットの置き場（各ツリーのルートからの相対、"/" 区切り）。
	// ツリーごとに置くのは、承認をそのツリーの git に乗せて他の機械へ届けるため。
	DefaultApproved = ".ccnavi/approved"
	// DefaultProjectHome は ccnavi ディレクトリ。組み込みの deny を `*/.ccnavi/*` の 1 行で済ませるため 1 つにまとめる。
	DefaultProjectHome = ".ccnavi"
	// ConfigDir は ccnavi ディレクトリの下の設定の置き場。
	ConfigDir = "config"
	// ClaudeDir は Claude Code の設定の置き場（ワークスペースルートの直下）。
	ClaudeDir = ".claude"
	// SkillsDir はスキルの置き場（ClaudeDir の下と、プロジェクトのルートの下）。
	SkillsDir = "skills"
	// DefaultProjects はプロジェクトのリポジトリの置き場（ワークスペースルートからの相対）。
	DefaultProjects = "projects"
	// MdExt は md の文書（チケット・提案・索引に載せる文書・学びの記録など）の拡張子。比べるときは
	// pathcase.HasExt で大文字小文字を区別せずに見る。
	MdExt = ".md"
	// SkillFile はスキル 1 つの本文（SkillsDir/<名前>/ の下）。
	SkillFile = "SKILL.md"
	// SkillHeadLimit は SKILL.md（とサブエージェントの md）の frontmatter を探す頭の長さ（バイト）。本文は読まない。
	SkillHeadLimit = 8 * 1024

	// ownProject は ccnavi 自身のソースツリーを見分ける pyproject.toml の名前。
	ownProject = "ccnavi"
	// localFile は ccnavi 自身のソースツリーでだけ読む上書き設定。
	localFile = "ccnavi.settings.local.json"
)

// binSuffixes は書かれたパスに無いときだけ継ぎ足して探す拡張子（Windows の組み立てだけが付ける）。
var binSuffixes = []string{".exe"}

// Settings は解決済みの設定。パスは絶対にしてあるので、後の処理は作業ディレクトリに依存しない。
//
// 欄の多くは書かれたままの値で、読み方はその値を使うパッケージが持つ。
// *Declared は解決する前にユーザが書いた値で、--lint が思い違い（この切り替えに無い値）を名指しするために残す。
type Settings struct {
	Mode string
	// ModeDeclaredInFile はワークツリーの中の設定ファイルが求めたモード、ModeFromEnvironment は
	// セッションを起動したユーザが渡したモード。disable を認めるかをこの 2 つで分ける（ResolveMode）。
	ModeDeclaredInFile  string
	ModeFromEnvironment string

	Log   string
	Rules string
	State string

	DenyRepeat           string
	FailStreakCount      string
	LearningSources      string
	LearningNoticeCount  string
	HandoffSuggestTokens string
	HandoffWaitSeconds   string

	// RestoreIfDeny はルールが deny と宣言した場所を戻すか、GuardCoreFiles は ccnavi 自身の設定ファイルを
	// 戻すか。守る対象の決まり方が違うので、片方だけを切れるよう分けてある。
	RestoreIfDeny  string
	GuardCoreFiles string

	StopReview      string
	StopReviewAgent bool

	GuardTicketApproval         string
	GuardTicketApprovalDeclared string
	GuardUnwatched              string

	// Bin は ccnavi 自身の実行ファイル。空なら守らない（推測すると別のファイルを実体として守ることになる）。
	Bin string

	TicketControl         string
	TicketControlDeclared string
	Learning              string
	LearningDeclared      string

	Tickets     string
	Approved    string
	Phases      string
	Risk        string
	Projects    string
	ProjectHome string

	// Project*Files は名前で指したレイヤーの設定の差し替え（名前 → 絶対パス）。診断のフラグだけが入れ、
	// hook からの判定では空のまま。
	ProjectRulesFiles  map[string]string
	ProjectPhasesFiles map[string]string
	ProjectRiskFiles   map[string]string

	BranchPrefixes         []string
	BranchPrefixesRejected []string
}

// TicketsEnabled はチケット制御が有効か。偽になるのは disable と書かれたときだけ。
func (s *Settings) TicketsEnabled() bool { return s.TicketControl != string(Disable) }

// LearningEnabled は学びの蓄積を使うか。偽になるのは disable と書かれたときだけ。
func (s *Settings) LearningEnabled() bool { return s.Learning != string(Disable) }

// Load は root の設定を解決する。設定ファイルが無い・読めないことは失敗ではない
// （入れただけで編集が要る道具にしない）。返す []string は読み飛ばした上書き設定の問題。
func Load(root string) (*Settings, []string) {
	mode := os.Getenv(env.Mode)
	s := &Settings{
		Mode:                        mode,
		ModeFromEnvironment:         mode,
		Log:                         ospath.Join(root, DefaultLog),
		Rules:                       ospath.Join(root, DefaultRules),
		State:                       ospath.Join(root, DefaultState),
		RestoreIfDeny:               os.Getenv(env.RestoreIfDeny),
		GuardCoreFiles:              os.Getenv(env.GuardCoreFiles),
		StopReview:                  os.Getenv(env.StopReview),
		StopReviewAgent:             os.Getenv(env.StopReviewAgent) == "1",
		GuardTicketApproval:         os.Getenv(env.GuardTicketApproval),
		GuardTicketApprovalDeclared: os.Getenv(env.GuardTicketApproval),
		GuardUnwatched:              os.Getenv(env.GuardUnwatched),
		DenyRepeat:                  os.Getenv(env.DenyRepeat),
		FailStreakCount:             os.Getenv(env.FailStreakCount),
		LearningSources:             os.Getenv(env.LearningSources),
		LearningNoticeCount:         os.Getenv(env.LearningNoticeCount),
		HandoffSuggestTokens:        os.Getenv(env.HandoffSuggestTokens),
		HandoffWaitSeconds:          os.Getenv(env.HandoffWaitSeconds),
		TicketControl:               os.Getenv(env.TicketControl),
		TicketControlDeclared:       os.Getenv(env.TicketControl),
		Learning:                    os.Getenv(env.Learning),
		LearningDeclared:            os.Getenv(env.Learning),
		Tickets:                     DefaultTickets,
		Approved:                    DefaultApproved,
		Phases:                      ospath.Join(root, defaultPhases),
		Risk:                        ospath.Join(root, defaultRisk),
		Projects:                    ospath.Join(root, DefaultProjects),
		ProjectHome:                 DefaultProjectHome,
		ProjectRulesFiles:           map[string]string{},
		ProjectPhasesFiles:          map[string]string{},
		ProjectRiskFiles:            map[string]string{},
	}
	if bin := os.Getenv(env.EntryPath); bin != "" {
		s.Bin = resolveBin(root, bin)
	}
	s.BranchPrefixes, s.BranchPrefixesRejected = branchPrefixes(root)

	if !ownSourceTree(root) {
		return s, nil
	}
	// ccnavi 自身を開発しているときだけ、編集を次のツール呼び出しから効かせる。開発の便宜であって
	// 安全境界ではない。ここに disable と書いても ResolveMode が無視する。
	conf, problems := readLocal(root)
	if conf == nil {
		return s, problems
	}
	if v, ok := conf.Value("mode").(string); ok {
		s.ModeDeclaredInFile = v
		s.Mode = v
	}
	for key, field := range map[string]*string{
		"restore_if_deny":       &s.RestoreIfDeny,
		"guard_core_files":      &s.GuardCoreFiles,
		"guard_ticket_approval": &s.GuardTicketApproval,
		"ticket_control":        &s.TicketControl,
	} {
		if v, ok := conf.Value(key).(string); ok {
			*field = v
		}
	}
	if v, ok := conf.Value("guard_ticket_approval").(string); ok {
		s.GuardTicketApprovalDeclared = v
	}
	if v, ok := conf.Value("ticket_control").(string); ok {
		s.TicketControlDeclared = v
	}
	if v, ok := conf.Value("bin").(string); ok && v != "" {
		s.Bin = resolveBin(root, v)
	}
	return s, problems
}

// resolveBin は実行ファイルのパスを、この環境に在る形へ直す。書かれたとおりに在ればそれ、無ければ
// `.exe` を継ぎ足して在るもの。OS で分けないのは、WSL から Windows 側の置き場を指すときも守るため。
// どちらも無ければ書かれたまま返す（空にすると「守らない設定」と見分けられない）。
func resolveBin(root, path string) string {
	full := path
	if !ospath.IsAbs(path) {
		full = ospath.Join(root, path)
	}
	if ospath.DiskExists(full) {
		return full
	}
	for _, suffix := range binSuffixes {
		if !strings.HasSuffix(full, suffix) && ospath.DiskExists(full+suffix) {
			return full + suffix
		}
	}
	return full
}

// ownSourceTree は root が ccnavi を開発しているチェックアウトか（pyproject.toml の project.name）。
// 安全性の検査ではない。
func ownSourceTree(root string) bool {
	data, err := os.ReadFile(filepath.Join(root, "pyproject.toml"))
	if err != nil {
		return false
	}
	var doc map[string]any
	if _, err := toml.Decode(string(data), &doc); err != nil {
		return false
	}
	project, ok := doc["project"].(map[string]any)
	return ok && project["name"] == ownProject
}

// 上書き設定を読み飛ばしたときの文。
const (
	localUnreadable = "%s を読めないので無視した (%v)"
	localNotJSON    = "%s が JSON として読めないので無視した (%v)"
	localNotObject  = "%s がオブジェクトではないので無視した"
)

// readLocal は上書き設定を読む。不正なファイルは報告して読み飛ばす
// （カンマ 1 つの書き損じでガードが消えないように）。
func readLocal(root string) (*pyjson.Object, []string) {
	// UTF-8 でないとき（ospath.errNotUTF8）、Python は内部エラーになるが、ここでは読めないファイルとして扱う。
	text, err := ospath.ReadDiskText(filepath.Join(root, localFile))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, []string{fmt.Sprintf(localUnreadable, localFile, err)}
	}
	conf, err := pyjson.Loads(text)
	if err != nil {
		return nil, []string{fmt.Sprintf(localNotJSON, localFile, err)}
	}
	obj, ok := conf.(*pyjson.Object)
	if !ok {
		return nil, []string{fmt.Sprintf(localNotObject, localFile)}
	}
	return obj, nil
}

// readEnvBlock は Claude Code の設定ファイルの `env`。読めない・形が違うときは ok が偽。
func readEnvBlock(path string) (*pyjson.Object, bool) {
	text, err := ospath.ReadDiskText(path)
	if err != nil {
		return nil, false
	}
	doc, err := pyjson.Loads(text)
	if err != nil {
		return nil, false
	}
	obj, ok := doc.(*pyjson.Object)
	if !ok {
		return nil, false
	}
	block, ok := obj.Value("env").(*pyjson.Object)
	return block, ok
}

// HookView は Claude Code が起こした hook に渡るはずの値（解決する前の生の値）。
type HookView struct {
	Mode          string
	TicketControl string
}

// ReadHookView は hook が受け取るはずの CCNAVI_MODE と CCNAVI_TICKET_CONTROL を診断のために組み直す。
// 別のプロセス（VS Code 拡張）から起こされると settings.json の `env` は渡らないので、プロセスの環境の上に
// Claude Code と同じ順（共有 → 個人）で重ねる。判定は Load だけを読む。
func ReadHookView(root string) HookView {
	values := map[string]string{env.Mode: os.Getenv(env.Mode), env.TicketControl: os.Getenv(env.TicketControl)}
	for _, rel := range []string{SharedClaudeSettings, LocalClaudeSettings} {
		block, ok := readEnvBlock(filepath.Join(root, rel))
		if !ok {
			continue
		}
		for name := range values {
			if v, ok := block.Value(name).(string); ok {
				values[name] = v
			}
		}
	}
	ticketControl := values[env.TicketControl]
	if ownSourceTree(root) {
		if conf, _ := readLocal(root); conf != nil {
			if v, ok := conf.Value("ticket_control").(string); ok {
				ticketControl = v
			}
		}
	}
	return HookView{Mode: values[env.Mode], TicketControl: ticketControl}
}

// defaultBranchPrefixes は親の識別子の先頭の語の既定。`release` は統合先の名前（`release-*`）に当たるので入れない。
var defaultBranchPrefixes = []string{"feature", "hotfix", "fix", "bugfix", "chore", "refactor", "docs"}

// DefaultBranchPrefixes は defaultBranchPrefixes の写しを返す（呼び手が書き換えても元は変わらない）。
func DefaultBranchPrefixes() []string { return slices.Clone(defaultBranchPrefixes) }

// reservedBranchIDs は統合先や保護されたブランチの名前（ccnavi-git.sh の push の拒否と同じリスト）。親の識別子の
// 先頭の語にも使えない。持ち主は tickets/model/ids（ids.ReservedBranchIDs）で、infra は tickets を読めないのでここに置く。
var reservedBranchIDs = []string{"main", "master", "develop", "release"}

// ReservedBranchIDs は reservedBranchIDs の写しを返す（呼び手が書き換えても元は変わらない）。
func ReservedBranchIDs() []string { return slices.Clone(reservedBranchIDs) }

var prefixPattern = regexp.MustCompile(`^[a-z][a-z0-9]*$`)

// IsBranchPrefix は親の識別子の先頭の語に使える形か（英小文字で始まる英小文字と数字。予約の名前でない）。
func IsBranchPrefix(word string) bool {
	if !prefixPattern.MatchString(word) {
		return false
	}
	for _, r := range reservedBranchIDs {
		if word == r {
			return false
		}
	}
	return true
}

// parseBranchPrefixes は先頭の語のリスト（カンマか空白区切り）を読む。使える語が無ければ既定のリスト。
func parseBranchPrefixes(text string) (words, rejected []string) {
	fields := strings.FieldsFunc(text, func(r rune) bool { return r == ',' || ospath.IsSpace(r) })
	for _, word := range fields {
		if !IsBranchPrefix(word) {
			rejected = append(rejected, word)
			continue
		}
		dup := false
		for _, w := range words {
			dup = dup || w == word
		}
		if !dup {
			words = append(words, word)
		}
	}
	if len(words) == 0 {
		words = append([]string(nil), defaultBranchPrefixes...)
	}
	return words, rejected
}

// branchPrefixes は環境変数を先に見て、無ければターミナルで打つときのために個人 → 共有の設定ファイルの `env`。
func branchPrefixes(root string) ([]string, []string) {
	if v, ok := os.LookupEnv(env.BranchPrefixes); ok {
		return parseBranchPrefixes(v)
	}
	for _, rel := range []string{LocalClaudeSettings, SharedClaudeSettings} {
		block, ok := readEnvBlock(filepath.Join(root, rel))
		if !ok {
			continue
		}
		if v, ok := block.Value(env.BranchPrefixes).(string); ok {
			return parseBranchPrefixes(v)
		}
	}
	return append([]string(nil), defaultBranchPrefixes...), nil
}
