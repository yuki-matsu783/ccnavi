package selfguard

import (
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/records/diaglog"
)

// 組み込みの保護の id。どれも rules.ReservedIDPrefix で始まり、ルールファイルからは書けない（消せない）。
const (
	ShellRuleID        = rules.ReservedIDPrefix + "guard-setting-files"
	binaryRuleID       = rules.ReservedIDPrefix + "guard-binary"
	commonLayerRuleID  = rules.ReservedIDPrefix + "guard-common-layer"
	recordsRuleID      = rules.ReservedIDPrefix + "guard-records"
	projectHomeRuleID  = rules.ReservedIDPrefix + "guard-project-home"
	handoffLinkRuleID  = rules.ReservedIDPrefix + "guard-handoff-links"
	handoffLnRuleID    = rules.ReservedIDPrefix + "guard-handoff-ln"
	hooksRuleID        = rules.ReservedIDPrefix + "guard-hooks"
	settingsRuleID     = rules.ReservedIDPrefix + "guard-settings"
	scriptsRuleID      = rules.ReservedIDPrefix + "guard-scripts"
	ccnaviConfigRuleID = rules.ReservedIDPrefix + "guard-ccnavi-config"
)

// shellMatch はシェルから書き込む形の 1 本を当てるツール。
const shellMatch = config.ToolBash

var (
	// namedMatch は名前で書くツール。
	namedMatch = rules.MatchOf(config.WriteTools...)
	// settingsRegex は `.claude/settings.json` と `settings.local.json`（config.SharedClaudeSettings・LocalClaudeSettings）。
	settingsRegex = pyre.AnySeparator + pyre.Escape(config.ClaudeDir) + pyre.AnySeparator + `settings(\.local)?\.json$`
	// hooksGlob は hook の sh の置き場の下。
	hooksGlob = "*/" + config.ClaudeDir + "/hooks/*"
)

// 文面ファイルの識別子。
const (
	textSettingFiles = "RULE_GUARD_SETTING_FILES"
	textBinary       = "RULE_GUARD_BINARY"
	textProjectHome  = "RULE_GUARD_PROJECT_HOME"
	textHandoffLinks = "RULE_GUARD_HANDOFF_LINKS"
	textHandoffLn    = "RULE_GUARD_HANDOFF_LN"
	textRecords      = "RULE_GUARD_RECORDS"
	textCommonLayer  = "RULE_GUARD_COMMON_LAYER"
	textHooks        = "RULE_GUARD_HOOKS"
	textSettings     = "RULE_GUARD_SETTINGS"
	textScripts      = "RULE_GUARD_SCRIPTS"
	textCCNaviConfig = "RULE_GUARD_CCNAVI_CONFIG"
)

// nextVariant は守る場所を直す手順の文の場合の欄（ワークスペースルートの有無で分かれる）。
const nextVariant = "next"

// hint は nextVariant の欄の値。
type hint string

const (
	hintRoot   hint = "root"
	hintNoRoot hint = "noroot"
)

// Options は AddRules が組む保護の設定。
type Options struct {
	Bin         string
	ProjectHome string
	Root        string
	// CommonFiles は共通レイヤーの 3 本（CommonLayerFiles）。
	CommonFiles []string
	// Log と State は記録と state の置き場（診断のフラグで動かせる）。
	Log, State string
	// Tool は判定するツール名。シェルの 1 本（式が大きく、組み立てとコンパイルが判定の時間の多くを占める）は、
	// このツールに当たるときだけ足す。空ならツールを決めない呼び手として足す。
	Tool string
	// Cwd は作業ディレクトリ（置き場の中でリンクを作る形を見分ける）。
	Cwd string
}

// AddRules はガード自身を守るルールを、ルールファイルの外から判定の先頭に足す（ルールに書かせると消せるため）。
// 足すのは、シェルから書き込む形・実行ファイル・ccnavi ディレクトリ・置き場のリンク 2 本・記録の置き場・共通レイヤーの 3 本。
// 先頭に挿すので、後に足したものほど先に当たる（共通レイヤーの 1 本が ccnavi ディレクトリの 1 本より先）。
func AddRules(rs *rules.RuleSet, o Options) {
	next := nextHint(o.Root)
	if o.Tool == "" || rules.ToolMatches(shellMatch, o.Tool) {
		insert(rs, rules.Spec{
			ID: ShellRuleID, Match: shellMatch,
			Regex: ShellRegex(o.Root, o.Bin, o.ProjectHome, o.CommonFiles),
			// 置き場へ cp で写す形も止める。写すのは ccnavi-stage.sh で、その案内は文が持つ。
			Text: texts.Spec(textSettingFiles, texts.SpecOptions{Variants: texts.Variants{nextVariant: string(next)}}),
			// 置き場の中だけを消すコマンドは外す。書き込みは外さない。
			Exempt: handoffDeleteExempt(o.Root),
		}, o.Root)
	}
	if clause := BinaryClause(o.Bin); clause != "" {
		insert(rs, rules.Spec{
			ID: binaryRuleID, Match: namedMatch,
			// 当てる先は解決済みの絶対パスなので、末尾で閉じる。
			Regex: clause + "$", Text: texts.Spec(textBinary, texts.SpecOptions{}),
		}, o.Root)
	}
	if glob := projectHomeGlob(o.ProjectHome); glob != "" {
		insert(rs, rules.Spec{
			ID: projectHomeRuleID, Match: namedMatch, Glob: glob,
			Text:   texts.Spec(textProjectHome, texts.SpecOptions{}),
			Exempt: handoffWriteExempt(o.Root),
		}, o.Root)
	}
	if o.Root != "" {
		// 当てる対象は関数が決める（式は 1 文字で組み立てが軽いので、ツールに依らず足す）。
		insert(rs, rules.Spec{
			ID: handoffLinkRuleID, Match: namedMatch, Regex: ".",
			Text: texts.Spec(textHandoffLinks, texts.SpecOptions{}), Exempt: handoffLinkTarget(o.Root),
		}, o.Root)
		insert(rs, rules.Spec{
			ID: handoffLnRuleID, Match: shellMatch, Regex: ".",
			Text: texts.Spec(textHandoffLn, texts.SpecOptions{}), Exempt: handoffLinkCommand(o.Root, o.Cwd),
		}, o.Root)
	}
	insert(rs, rules.Spec{
		ID: recordsRuleID, Match: namedMatch, Regex: recordsRegex(o.Log, o.State),
		Text: texts.Spec(textRecords, texts.SpecOptions{}),
	}, o.Root)
	if common := commonLayerRegex(o.Root, o.CommonFiles); common != "" {
		insert(rs, rules.Spec{
			ID: commonLayerRuleID, Match: namedMatch, Regex: common,
			Text: texts.Spec(textCommonLayer, texts.SpecOptions{}),
		}, o.Root)
	}
}

// AddNamedRules は、hook・settings・scripts・共通の設定 3 本を名前で書くツールから守る 4 本を、先頭に足す。
// ルールファイルが読めない間も、実行後の保護範囲に同じ 4 本を使う。実行前は AddRules の後に呼ぶ（専用の文が先に並ぶため）。
// 置き場の中のつながっていないコピーは外す。ccnavi ディレクトリの名前が空なら、scripts と共通の設定は足さない。
func AddNamedRules(rs *rules.RuleSet, projectHome, root string) {
	exempt := handoffWriteExempt(root)
	variants := texts.SpecOptions{Variants: texts.Variants{nextVariant: string(nextHint(root))}}
	named := []rules.Spec{
		{ID: hooksRuleID, Glob: hooksGlob, Text: texts.Spec(textHooks, variants)},
		{ID: settingsRuleID, Regex: settingsRegex, Text: texts.Spec(textSettings, variants)},
	}
	if parts := escapedParts(homeName(projectHome)); len(parts) > 0 {
		home := strings.Join(parts, pyre.AnySeparator)
		named = append(named,
			rules.Spec{ID: scriptsRuleID, Glob: "*/" + homeName(projectHome) + "/" + config.ScriptsDir + "/*", Text: texts.Spec(textScripts, variants)},
			rules.Spec{
				ID:    ccnaviConfigRuleID,
				Regex: pyre.AnySeparator + strings.Join([]string{home, config.ConfigDir, config.LayerCommon, `(rules|phases|risks)\.yml$`}, pyre.AnySeparator),
				Text:  texts.Spec(textCCNaviConfig, variants),
			})
	}
	for _, s := range named {
		s.Match, s.Exempt = namedMatch, exempt
		insert(rs, s, root)
	}
}

// nextHint は守る場所を直す手順の文の場合。
func nextHint(root string) hint {
	if root != "" {
		return hintRoot
	}
	return hintNoRoot
}

// insert は組み込みの 1 本を判定の先頭に挿す。組み立てられないのはこのビルドの書き損じなので、判定は止めない。
// 止めると、直すための呼び出しごと止まってしまう。黙って外すと保護が欠けたことに気づけないので、診断ログに id と問題の件数だけを
// 残す（式には守る先のパスが入るため、式そのものはログに出さない）。
func insert(rs *rules.RuleSet, s rules.Spec, root string) {
	built, problems := rules.Builtin(rules.Deny, []rules.Spec{s}, root)
	if len(problems) > 0 || len(built) == 0 {
		diaglog.Get(diagSource, root).Warn("組み込みの保護を組み立てられず外した", "rule", s.ID, "problems", len(problems))
		return
	}
	rs.Deny = append([]*rules.Rule{built[0]}, rs.Deny...)
}

// diagSource は診断ログの出どころ（実行ファイルと同じ）。
const diagSource = "ccnavi"
