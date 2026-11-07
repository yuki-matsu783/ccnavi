package guard

import (
	"fmt"
	"io"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/policy/ctxfile"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/model/place"
)

const (
	StateRuleID      = rules.ReservedIDPrefix + "ticket-state"
	StateShellRuleID = StateRuleID + "-shell"
	// ProposeRuleID は提案を書いた回の確認の文の数えのキー。表には入れない。
	ProposeRuleID = rules.ReservedIDPrefix + "ticket-propose"
	// RawGitRuleID は生の git を止めるルール。ccnavi 自身の約束を守るので、予約の接頭辞 guard- を持つ。
	RawGitRuleID         = rules.ReservedIDPrefix + "guard-raw-git"
	ApprovedRuleID       = rules.ReservedIDPrefix + "ticket-approved"
	ApprovedShellRuleID  = ApprovedRuleID + "-shell"
	OpsRuleID            = rules.ReservedIDPrefix + "ticket-ops"
	OpsShellRuleID       = OpsRuleID + "-shell"
	textTicketState      = "RULE_TICKET_STATE"
	textTicketApproved   = "RULE_TICKET_APPROVED"
	textTicketOps        = "RULE_TICKET_OPS"
	textRawGit           = "RULE_RAW_GIT"
	shellMatch           = config.ToolBash + "|" + config.ToolPowerShell
	optionalProjectInDir = `(?:[^\\/\s\x00]+[\\/])?`
)

// `git` がコマンドの位置に立つ形。glob の `*git*` だと誘導先の ccnavi-git.sh にも当たって出口ごと塞がるので、
// 前後の切れ目を見る。当てるのは引用とコメントを外してコマンドの切れ目に \x00 を置いた読みなので、位置は行頭か
// \x00 の直後。読み切れずに生の文字列に当てる回のために、シェルの区切りの後ろも位置に数え、その直後の予約語
// （then・do など）も読み飛ばす。パス付き（/usr/bin/git・C:\Git\git.exe）と git.exe も同じ。引用しない
// C:\Git\git.exe はシェルが `\` を外して C:Gitgit.exe と読むので、ドライブ名で始まり git で終わる語も止める。
const (
	separator = `[\x00\n;&|({` + "`" + `!]`
	keyword   = `(?:then|do|else|elif|if|while|until|time)\s++`
	pathChar  = `[^\s\x00\\/;&|({` + "`" + `!]`
	// RawGitRegex は生の git の式。
	RawGitRegex = `(?:^|` + separator + `)\s*+(?:` + keyword + `)*+` +
		`(?:(?:` + pathChar + `*+[\\/])*+|[a-z]:` + pathChar + `*?)git(?:\.exe)?\s`
)

// placeRegex は置き場のパスに当たる式。プロジェクトの名前を挟んだ形（wip/<name>/proposals）にも当たる。
func placeRegex(rel string) string {
	var parts []string
	for _, p := range strings.Split(rel, "/") {
		if p != "" {
			parts = append(parts, pyre.Escape(p))
		}
	}
	if len(parts) < 2 {
		return optionalProjectInDir + strings.Join(parts, pyre.AnySeparator)
	}
	return parts[0] + pyre.AnySeparator + optionalProjectInDir + strings.Join(parts[1:], pyre.AnySeparator)
}

func guardedStates() string {
	names := make([]string, len(place.GuardedStates))
	for i, s := range place.GuardedStates {
		names[i] = string(s)
	}
	return strings.Join(names, "|")
}

// StateDirRegex は直接の作成・移動を止める置き場に当たる式（パス用）。
func StateDirRegex(ticketsRel string) string {
	return `(^|[\\/])` + placeRegex(ticketsRel) + `[\\/](` + guardedStates() + `)[\\/]`
}

// rule は組み込みの deny 1 本。大文字小文字はどの機械でも区別しない（DONE/ も同じ場所。大文字で書く正当な用事は無い）。
func rule(id, match, regex, root string, text rules.TextRef) (*rules.Rule, error) {
	compiled, err := pyre.Compile(regex, pyre.IgnoreCase)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", id, err)
	}
	return &rules.Rule{ID: id, Match: match, Regex: regex, Root: root, Decision: rules.Deny,
		Text: text, Every: rules.EveryDefault, Compiled: compiled}, nil
}

// GuardRules は状態の置き場（review/）を守る 2 本（ツールの書き込みとシェルの書き込み）。通るのは状態を動かす
// スクリプトだけで、その呼び出しには置き場のパスが現れない。シェルの側は前の区切りを求めず、末尾の "/" が無い
// 行き先（mv x wip/proposals/review）でも当てる。コピーの動詞は行き先だけで当て、行き先の読み方は組み込みの
// 保護と同じ部品を使う（別に書くと片方だけが読む書き方が通る）。
func GuardRules(ticketsRel, root string) ([]*rules.Rule, error) {
	spec := texts.Spec(textTicketState, texts.SpecOptions{})
	write, err := rule(StateRuleID, config.WriteMatch, StateDirRegex(ticketsRel), root, spec)
	if err != nil {
		return nil, err
	}
	states := guardedStates()
	review := placeRegex(ticketsRel) + `[\\/](` + states + `)`
	loose := review + `([\\/]|\s|$)`
	shellRegex := strings.Join([]string{
		selfguard.WriteVerbs + loose,
		selfguard.CopyDestinationRegex(selfguard.CopyLastPlace(review), loose),
		selfguard.HolderRegex(selfguard.Under(placeRegex(ticketsRel)), states, false),
	}, "|")
	shell, err := rule(StateShellRuleID, shellMatch, shellRegex, root, spec)
	if err != nil {
		return nil, err
	}
	return []*rules.Rule{write, shell}, nil
}

// dirRules は置き場のディレクトリ全体への書き込みを止める 2 本。書き込みの動詞に加えて、置き場の名前を引数に取る
// 作成・変更・展開の道具とコードを走らせる道具も止める（名前が現れるだけで当たるので、読み取りの道具は巻き込まない）。
func dirRules(ruleID, shellID, placeRel string, spec rules.TextRef, root string) ([]*rules.Rule, error) {
	where := placeRegex(placeRel)
	write, err := rule(ruleID, config.WriteMatch, `(^|[\\/])`+where+`[\\/]`, root, spec)
	if err != nil {
		return nil, err
	}
	loose := where + `([\\/]|\s|$)`
	also := `(^|\x00|[;&|]\s*)(touch|mkdir|chmod|chown|rsync|sponge|unzip|tar|python3?|node|perl|ruby` +
		`|Set-Content|Add-Content|New-Item|Copy-Item|Move-Item|Remove-Item|Out-File)\b` +
		`[^\x00]*` + where
	shellRegex := strings.Join([]string{
		selfguard.WriteVerbs + loose,
		selfguard.CopyDestinationRegex(selfguard.CopyLastPlace(where), loose),
		also,
	}, "|")
	shell, err := rule(shellID, shellMatch, shellRegex, root, spec)
	if err != nil {
		return nil, err
	}
	return []*rules.Rule{write, shell}, nil
}

// ApprovedWriteRule は承認済みの置き場を名前で書くツールから守る 1 本。実行後の保護範囲に足す（シェル版は足さない。
// 実行後はワークツリーの変更を見る）。実行前のゲート（GateRules）と同じ式・同じ文。
func ApprovedWriteRule(approvedRel, root string) (*rules.Rule, error) {
	built, err := dirRules(ApprovedRuleID, ApprovedShellRuleID, approvedRel, texts.Spec(textTicketApproved, texts.SpecOptions{}), root)
	if err != nil {
		return nil, err
	}
	return built[0], nil
}

// GateRules は承認済みの置き場と操作記録の置き場を守る 4 本。動かせるのは ccnavi のスクリプトとユーザの操作だけで、
// エージェントの直接の書き込みは dry-run でも止める（チケット制御が有効な間）。操作記録を書き直すと送り直しの
// 根拠を偽れる。
func GateRules(approvedRel, root string) ([]*rules.Rule, error) {
	approved, err := dirRules(ApprovedRuleID, ApprovedShellRuleID, approvedRel, texts.Spec(textTicketApproved, texts.SpecOptions{}), root)
	if err != nil {
		return nil, err
	}
	ops, err := dirRules(OpsRuleID, OpsShellRuleID, place.OpsRel, texts.Spec(textTicketOps, texts.SpecOptions{}), root)
	if err != nil {
		return nil, err
	}
	return append(approved, ops...), nil
}

// RawGitRule は生の git を止めるルール。チケット制御の有無や共通レイヤーの有無によらずいつも足す（ラッパーの中の
// 可否は git がラッパーの外から打てれば外れる）。ラッパーが無いワークスペースでは文をユーザに頼む形にする。
// 土台（Base）なので、ほかのレイヤーの deny にも当たった回はそちらだけを言う。
func RawGitRule(root string) (*rules.Rule, error) {
	text := texts.Spec(textRawGit, texts.SpecOptions{Wrapper: config.HasGitWrapper(root), Cased: []string{"why", "next"}})
	r, err := rule(RawGitRuleID, config.ToolBash, RawGitRegex, root, text)
	if err != nil {
		return nil, err
	}
	r.Base = true
	return r, nil
}

// ProposeNotice は提案を書いた回に、承認を頼む前の確認を 1 つの文脈で 1 度だけ伝える文。空なら渡さない。
// 判定には足さない（文であって判定ではない。表に allow を 1 本足すと、todo/ が言及する場所になって既定の扱いを
// 通らなくなり、提案の置き場に deny・ask を書いたワークスペースにだけ届かなくなる）。渡るのは書き込みの前なので、
// 止まった回にも届く。
func ProposeNotice(stderr io.Writer, conf *config.Settings, root string, in hookio.Input, subject string) string {
	if !slices.Contains(config.WriteTools, in.ToolName) || subject == "" {
		return ""
	}
	// ワークスペースの中に限る。ツリーはどれもルートの下なので、正しい置き場は全部入り、外は入らない。
	pattern := `^` + rules.RootPattern(root) + `[\\/][^\x00]*` + placeRegex(conf.Tickets) + `[\\/]` + string(place.Todo) + `[\\/]`
	here, err := pyre.Compile(pattern, pyre.IgnoreCase)
	if err != nil || !here.Search(subject) {
		return ""
	}
	said, _ := ctxfile.Deliver(stderr, conf.State, in, []*rules.Rule{proposeRule(conf.Tickets)}, ctxfile.Options{})
	return said
}

// proposeRule は文と数えのキーになる id を持つ入れ物。表には入れない。
func proposeRule(ticketsRel string) *rules.Rule {
	return &rules.Rule{
		ID:    ProposeRuleID,
		Every: rules.EveryDefault,
		AdditionalContextOnce: "チケットの提案を書こうとしています（" + ticketsRel + "/todo/ は承認待ちの置き場で、" +
			"ここに書いただけでは判定の対象になりません）。" +
			"ユーザに承認を依頼する前に、" +
			"'sh .ccnavi/scripts/ccnavi-ticket.sh approval-request <親の識別子> --check' で" +
			"承認できる状態かを確かめてください。承認済みチケットは置きません。" +
			"終了コードが答えです。0 なら前提は通っています" +
			"（approval-request（--check なし）で送ります）。" +
			"1 なら理由が出ます（承認の対象に入らない、親のワークツリーに未コミットの変更がある、" +
			"ホストや git のリモートが使えない、など。何も送っていません）。" +
			"直してから打ち直してください。" +
			"**識別子には、いま書いたものを渡してください。** " +
			"省くと識別子が無いと言われて止まります。",
	}
}
