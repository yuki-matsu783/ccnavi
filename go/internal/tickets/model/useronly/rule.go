package useronly

import (
	"fmt"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/policy/texts"
)

// Python から実行ファイルの中身を走らせる表記。実行役（`uv run`・`poetry run`・`pipenv run`。間の選択肢と値は
// 読み分けない）、インタプリタ（`python`・`python3.12`・`py` と `.exe`）とその選択肢（`-u`・`-W x`・`-Xutf8` など）、
// 入口（`-m ccnavi`・`-mccnavi`・`-um ccnavi`・`-m ccnavi.__main__`・配布物の `main.py`）。拾いきれない表記
// （`python -c`・名前を変えたコピー）は HumanPathForm と HiddenUserEntry が名前に依らない検査で見る。
const (
	pyRunner      = `(?:\S*[\\/])?(?:uv|poetry|pipenv)(?:\.exe)?\s+run\b(?:\s+[^\s\x00;&|]+)*?`
	pyInterpreter = `(?:\S*[\\/])?(?:py|python[\w.]*)(?:\.exe)?`
	pyOptions     = `(?:\s+(?:-[WX]\s*[^\s\x00;&|-]\S*|-[A-Za-z0-9.]+))*?`
	pyEnd         = `(?=[\s\x00;&|]|$)`
	pyEntry       = `(?:\s+-[A-Za-z]*m\s*ccnavi(?:\.__main__)?` + pyEnd + `|\s+(?:\S*[\\/])?main\.py` + pyEnd + `)`
)

// TicketApprovalRule は ccnavi の実行ファイルをユーザの判断の経路に使う形を止めるルール。承認の sh
// （`ccnavi-user-*.sh`）、チケットの状態とレビューのサブコマンド、`--agree`（`--preview` の付いた見るだけの形を
// 除く）・`--yes`、`user` の入口（prune は PruneForm）、放棄した親の片付けの記録を消す `worktree retire` も止める。
// 実行ファイルの側もターミナルを求めるが、文字列で止めておけば「なぜ通らないのか」が当たったルールの id で分かる。
// ボードの経路とターミナル要求を切る形は、実行ファイルのパスに頼らず HumanPathForm が見る。
//
// 大文字小文字は区別しない。Windows と macOS の既定のファイルシステムは名前の大小を区別しないので、
// `CCNAVI.EXE --agree` でも同じものが走る。引数の形まで広がるが、実行ファイルの引数は大小を区別するので、
// 広がるのは止める側だけ（`--PREVIEW` で免除の形になっても、実行ファイルがその引数を受け付けない）。
func TicketApprovalRule(binPath, root string) (*rules.Rule, error) {
	exe := []string{`ccnavi(\.exe)?`}
	if clause := selfguard.BinaryClause(binPath); clause != "" {
		exe = append(exe, clause)
	}
	// 実行ファイルを直に呼ぶ形と、起動の sh（`sh .ccnavi/scripts/ccnavi-launcher.sh …`）。
	exe = append(exe, `ccnavi-launcher\.sh`)
	launcher := `(` + pyRunner + pyOptions + pyEntry +
		`|(?:` + pyRunner + `\s+)?` + pyInterpreter + pyOptions + pyEntry +
		`|(?:` + pyRunner + `\s+)?((sh|bash)(\s+-\S+)*\s+)?(\S*[\\/])?(` + strings.Join(exe, "|") + `))`
	expression := `(?i)(^|\x00|[;&|]\s*)(&\s*)?` + launcher + `\s+[^\x00]*` + cliForms(notPreview) +
		`|(^|\x00|[;&|]\s*)(&\s*)?` + launcher + userEntryForms(pruneVerb) +
		`|` + scriptForms()
	compiled, err := pyre.Compile(expression, 0)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", TicketApprovalRuleID, err)
	}
	return &rules.Rule{
		ID:       TicketApprovalRuleID,
		Match:    strings.Join(config.ShellTools, "|"),
		Regex:    expression,
		Text:     texts.Spec(textTicketApproval, texts.SpecOptions{}),
		Every:    1,
		Decision: rules.Deny,
		Root:     root,
		Compiled: compiled,
	}, nil
}
