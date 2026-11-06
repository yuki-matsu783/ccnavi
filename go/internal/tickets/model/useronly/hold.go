package useronly

import (
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/shell"
)

// ExemptNote は止めたときの文に添える、通る形の案内。案内どおりの 1 本に `cd … &&` や `| tail` 以外を足すと
// 止まる。文が「何を打つか」だけを言うと、付け足した形で打って止まり、案内と拒否が食い違って見える。
const ExemptNote = "止めている間に通るのは、ccnavi-ticket.sh・ccnavi-review.sh・ccnavi-git.sh と " +
	"ccnavi-launcher.sh --docs を sh で実行する形だけです。" +
	"前に cd、後ろに | head -n 3 のような行数だけの head・tail を付けてもよく、" +
	"ファイル名・リダイレクト（2>&1 など）・ほかのコマンドを足すと止まります。" +
	"提案ファイルは heredoc などでは書けないので、Write・Edit で書きます。"

// TurnDefined はユーザとモデルに見せる文で「ターン」を初めて使うところに置く表記。1 通の中では最初の 1 回だけに使う。
const TurnDefined = "ターン（ユーザが指示を出してから Claude が応答を終えるまで）"

// HeldTools はレビューが済むまで止めるツール。
var HeldTools = slices.Concat(config.AgentTools, config.ShellTools)

var (
	// exemptCommand は止めている間でも通す形。3 本の sh と資料を引く起動の sh を、コマンドの位置で `sh` から
	// 呼ぶ形だけ。名前がどこかに含まれるだけでは通さない。
	exemptCommand = compile(`^(sh|bash)\s+\S*`+holdExemptForms()+`(\s|$)`, 0)
	// exemptCompanion は上の形に付き添ってよい読むだけのコマンド。head・tail は行数の指定だけ（ファイル名・
	// `-f`・リダイレクトは付けられない）、cd は引数 1 個。上の形が 1 本も無ければ付き添いだけでは通さない。
	exemptCompanion = compile(`^(?:head|tail)(?:\s+-n?\s*\d+)?$|^cd\s+\S+$`, 0)
	// forbiddenCommand はサブエージェントに許さない形。名前の大小は問わない（大小を区別しないファイルシステムでは
	// 同じ sh が走る）。push を含めるのは、リモートの枝を送るのが親の仕事だから（サブエージェントが親のツリーへ
	// cd して打てばラッパーは通すので、ここにも止めを持つ）。
	forbiddenCommand = compile(subagentForbiddenForms(), pyre.IgnoreCase)
)

// commands は shell が切ったコマンドの並び。読めなかった生の文字列なら 1 本。
func commands(subject string) []string {
	var out []string
	for _, c := range strings.Split(subject, shell.Sep) {
		if c = ospath.Strip(c); c != "" {
			out = append(out, c)
		}
	}
	return out
}

// Exempt は止めている間でも通してよいか。degraded（読み切れなかった理由）があれば通さない。
func Exempt(subject, degraded string) bool {
	if degraded != "" || buildErr != nil {
		return false
	}
	parts := commands(subject)
	if !slices.ContainsFunc(parts, exemptCommand.match) {
		return false
	}
	for _, c := range parts {
		if !exemptCommand.match(c) && !exemptCompanion.match(c) {
			return false
		}
	}
	return true
}

// Forbidden はサブエージェントに許さない形を含むか。unwrapped は shell が作る、中で実行されるコマンドの層
// （Sep でつないだもの）。禁止の形はコマンドの先頭の `sh` に固定しているので、`env sh …`・`sh -c '…'` は
// 層にも当てる。止める側にだけ足す適用先で、Exempt には渡さない。
func Forbidden(subject, unwrapped string) bool {
	return slices.ContainsFunc(slices.Concat(commands(subject), commands(unwrapped)), forbiddenCommand.search)
}
