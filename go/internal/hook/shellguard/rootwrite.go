package shellguard

import (
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/texts"
)

const (
	// CodeRoot はシェルからワークスペースルート直下へ書き込む形を止めた理由コード。
	CodeRoot = "DENY_SHELL_WORKSPACE_ROOT"
	// RootRuleID は文面の見出しに出す名前。記録のルール名は、適用したルール（RootWrite.Rule）の名前。
	RootRuleID = rules.ReservedIDPrefix + "guard-root-shell-write"
	// rootSourceRuleID は適用するルールの id（ルールファイルの Write / Edit の決まり）。
	rootSourceRuleID = "workspace-root"
)

// RootWrite はワークスペースルート直下への書き込み。Target は書いたとおりの語、Path は解いたパス、Rule は適用した
// ルール、Origin は語を取り出した元のコマンド。当たらなければ Rule が nil。
type RootWrite struct {
	Target string
	Path   string
	Rule   *rules.Rule
	Origin string
}

// CheckRootWrite は、シェルの書き込み先がワークスペースルート直下の決まりに当たるかを見る。
//
// Write / Edit はルールファイルの `workspace-root` が止める。シェルでも同じ場所を書けると、`sed -i`・`>`・`tee` で
// 同じ決まりを素通りできてしまう。そこで書き込み先を CheckParentWrite と同じ読みで取り出し、解いた絶対パスを同じルールに
// Write として当てる。置き場の決まりをここに写さないので、ルールを直せば Write とシェルの両方が変わる。共通レイヤー以外の
// レイヤーのルールは id に `<レイヤー名>:` が付くので、その形も同じ決まりとして扱う。ほかの deny に当たらなかった呼び出しだけに
// 使う（守る場所は専用の保護が先に止める）。インタプリタに渡したコード、変数で組み立てた書き込み先、居場所が読めない相対パスは
// 見ない。実行後チェックがワークツリーの実物を見て戻す。
func CheckRootWrite(set *rules.RuleSet, cwd, subject string, layers []string) RootWrite {
	var found []*rules.Rule
	for _, r := range set.Deny {
		if r.ID == rootSourceRuleID || strings.HasSuffix(r.ID, rules.IDSeparator+rootSourceRuleID) {
			found = append(found, r)
		}
	}
	if len(found) == 0 {
		return RootWrite{}
	}
	for _, t := range writeTargets(cwd, subject, layers) {
		path := resolveWord(t.word, t.base, t.known)
		if path == "" {
			continue
		}
		// 既存のディレクトリは中へ書くものとしても当てる（`cp a .` はルート自身ではなく直下に書く）。決まりの式が
		// 区切りを要るため、区切りを足した形も見る。
		spelled := []string{path}
		if ospath.IsDir(path) {
			spelled = append(spelled, strings.TrimRight(path, ospath.Sep)+ospath.Sep)
		}
		for _, r := range found {
			for _, p := range spelled {
				if r.Matches(config.ToolWrite, p) {
					return RootWrite{t.word, path, r, t.origin}
				}
			}
		}
	}
	return RootWrite{}
}

// RootWriteMessage は止めた理由。文は文面ファイルが持つ。
func RootWriteMessage(root string, w RootWrite) string {
	what := "plain"
	if w.Origin != "" {
		what = "origin"
	}
	return texts.Say(root, CodeRoot, texts.Values{"target": w.Target, "path": w.Path, "origin": w.Origin},
		texts.Variants{"what": what})
}
