package cli

import (
	"fmt"
	"io"
	"os"
	"regexp"
	"strings"

	"ccnavi/internal/catalog/docs"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/records/prune"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/useronly"
)

// placeFlag は置き場を動かすフラグと、「渡されなかった」ときの値（argparse の既定と同じ。nil は None）。
type placeFlag struct {
	flag, dest string
	absent     any
}

// layerOverrides はレイヤーの置き場を動かすフラグである。前の 3 本は共通レイヤーの中身、
// 後の 2 本はレイヤーを探す先を指す。どれを外しても、プロジェクトのレイヤーが丸ごと消える。
// そうなると実績リスクが下がり、レビュー待ちを飛ばして閉じられてしまう。
// placeOverrides は記録と状態とチケットの置き場を動かすフラグである。どちらも診断の経路でだけ効く。
// sh は受け取った引数をそのまま渡すので、診断の外で受けると、記録を消した形や置き場を差し替えた形でサブコマンドが
// 動いてしまう。
var (
	layerOverrides = []placeFlag{
		{"--rules", "rules", ""},
		{"--phases", "phases", nil},
		{"--risk", "risk", nil},
		{"--projects", "projects", nil},
		{"--project-home", "project_home", ""},
	}
	placeOverrides = []placeFlag{
		{"--log", "log", nil},
		{"--state", "state", nil},
		{"--approved", "approved", nil},
		{"--tickets", "tickets", ""},
	}
	// wrapperFlags は sh が自分で計算して渡すパスである。sh は自分の分を先に置き、
	// エージェントの引数を後ろに付けるので、argparse の後勝ちでは 2 本目で上書きできてしまう。
	// 2 度渡す正しい使い方は無いので、2 本目があること自体を断る。
	wrapperFlags = []placeFlag{{"--root", "root", nil}, {"--cwd", "cwd", ""}}
	// docsFlags は `--docs` でだけ読むフラグ。
	docsFlags = []placeFlag{
		{"--type", "type", nil},
		{"--tag", "tag", nil},
		{"--keyword", "keyword", nil},
		{"--path", "path", nil},
		{"--text", "text", nil},
		{"--since", "since", nil},
		{"--until", "until", nil},
		{"--sort", "sort", nil},
		{"--reverse", "reverse", false},
		{"--limit", "limit", nil},
		{"--format", "format", nil},
		{"--no-refresh", "no_refresh", false},
	}
)

// notWithDocs は `--docs` と同時に指定できないフラグである。ほかの経路と、その経路でだけ読むフラグが対象になる。
// 何も出さずに無視すると、打ったユーザは反映されたと思ってしまう。`--root` は引く場所そのものなので例外とし、
// `--log`・`--state` は `--docs` が何も記録しないので受け入れる。
var notWithDocs = []string{
	"--lint", "--learnings", "--handoff", "--mirror", "--test", "--test-expectations", "--explain", "--suggest",
	"--prune", "--agree", "--reviewed", "--close-early", "--yes", "--preview", "--verify", "--digest", "--result",
	"--tickets", "--phase", "--abandon", "--body-file", "--reason", "--note", "--target", "--signal",
	"--accept-unresolved", "--flow", "--project-rules-file", "--project-phases-file", "--project-risk-file",
	"--mode", "--rules", "--restore-if-deny", "--guard-core-files", "--guard-ticket-approval", "--ticket-control",
	"--approved", "--phases", "--risk", "--projects", "--project-home", "--cwd", "--permission-mode", "--agent-id",
	"--record-writes", "--record-tree", "--choose-out", "--accept-plan", "--accept-done", "--accept-check",
	"--placed-out", "--actor", "--via",
}

// emptyCounts は空文字も「渡した」と数えるフラグ（既定が None で、空に意味があるもの）。
var emptyCounts = map[string]bool{"approved": true, "phases": true, "risk": true, "projects": true}

const (
	diagnosisOnly = "ccnavi: %s は診断時のみ使用できます。今回の指定は無視されます。\n"
	docsOnly      = "ccnavi: %s は --docs でだけ指定できる\n"
)

// sameValue は Python の ==（文字列・None・bool の値だけを比べる）。
func sameValue(v, absent any) bool {
	switch x := absent.(type) {
	case nil:
		return v == nil
	case string:
		s, ok := v.(string)
		return ok && s == x
	case bool:
		b, ok := v.(bool)
		return ok && b == x
	}
	return false
}

// oneWrapperFlagEach は、sh が渡すパスが 2 度来ていないかを見て、1 本にまとめる。2 度来ていたら false を返す。
// 数えるのは argparse に任せる。argv を直接数えると、`--reason=--root` のような別のオプションの値まで数えてしまう
// ためである。
func oneWrapperFlagEach(stderr io.Writer, a opts) bool {
	for _, w := range wrapperFlags {
		v := a.Namespace[w.dest]
		list, isList := v.([]any)
		if v != nil && !isList {
			continue // まとめ済み
		}
		if len(list) > 1 {
			fmt.Fprintf(stderr, "ccnavi: %s は 1 度しか渡せない（%d 度渡された）\n", w.flag, len(list))
			return false
		}
		a.Namespace[w.dest] = w.absent
		if len(list) == 1 {
			a.Namespace[w.dest] = list[0]
		}
	}
	return true
}

// diagnosing は、診断の経路かどうかを返す。置き場を動かすフラグはここでだけ効く。診断は payload を読まず、
// 判定を実行にも記録にも繋げない。そのため、保存していない設定を指しても実運用には影響しない。
func diagnosing(a opts) bool {
	return a.Truthy("lint") || !a.IsNone("test") || a.Str("test_expectations") != "" || a.Truthy("explain") ||
		a.Truthy("suggest")
}

// dropOutsideDiagnosis は診断の外で渡された置き場の差し替えを、標準エラーに出して無視する。
func dropOutsideDiagnosis(stderr io.Writer, a opts) {
	for _, group := range [][]placeFlag{layerOverrides, placeOverrides} {
		for _, f := range group {
			if sameValue(a.Namespace[f.dest], f.absent) {
				continue
			}
			fmt.Fprintf(stderr, diagnosisOnly, f.flag)
			a.Namespace[f.dest] = f.absent
		}
	}
}

// override はフラグを設定に重ねる。フラグは何よりも強い。空文字を受ける欄（記録しない・state を持たない）は、
// 既定が None のフラグで受け渡す。
func override(conf *config.Settings, a opts) {
	for _, o := range []struct {
		dest       string
		field      *string
		acceptsNil bool
	}{
		{"log", &conf.Log, true},
		{"rules", &conf.Rules, false},
		{"state", &conf.State, true},
		{"approved", &conf.Approved, false},
		{"phases", &conf.Phases, true},
		{"risk", &conf.Risk, true},
		{"projects", &conf.Projects, true},
	} {
		v, ok := a.Namespace[o.dest].(string)
		if ok && (o.acceptsNil || v != "") {
			*o.field = v
		}
	}
	// ワークツリーのルートからの相対で書く欄。区切りを "/" に揃え、前後の "/" を除く。
	if v := a.Str("tickets"); v != "" {
		conf.Tickets = strings.Trim(ospath.Slashed(v), "/")
	}
	if v := a.Str("project_home"); v != "" {
		conf.ProjectHome = strings.Trim(ospath.Slashed(v), "/")
	}
}

// realPath はリンクを解いたパス。
func realPath(path string) string { return ospath.Realpath(path) }

// same は 2 つのパスが同じか（大文字小文字を区別しない）。
func same(a, b string) bool { return pathcase.SameNorm(a, b) }

// inside は path が base の下（か同じ）か。どちらも行き着く先のパスで渡す。大文字小文字は区別しない。
func inside(path, base string) bool {
	p := pathcase.Fold(ospath.Normpath(path))
	b := pathcase.Fold(ospath.Normpath(base))
	sep := string(os.PathSeparator)
	return p == b || strings.HasPrefix(p, strings.TrimRight(b, sep)+sep)
}

// docsFlagsGiven は渡された `--docs` 用のフラグ。
func docsFlagsGiven(a opts) []string {
	var out []string
	for _, f := range docsFlags {
		if !sameValue(a.Namespace[f.dest], f.absent) {
			out = append(out, f.flag)
		}
	}
	return out
}

// notWithDocsGiven は `--docs` に添えられた、ほかの経路のフラグを最初の 1 つ返す。
// own は調べている経路自身のフラグで、数えない。`--reviewed 0` の 0 も、渡したものとして数える。
func notWithDocsGiven(a opts, own string) string {
	for _, flag := range notWithDocs {
		if flag == own {
			continue
		}
		name := destOf(flag)
		v := a.Namespace[name]
		if b, ok := v.(bool); v == nil || ok && !b {
			continue
		}
		if s, ok := v.(string); ok && s == "" && !emptyCounts[name] {
			continue
		}
		return flag
	}
	return ""
}

// runDocs は `--docs`。ワークスペースと、索引の対象になるプロジェクトの md を引く。
func runDocs(stdout, stderr io.Writer, conf *config.Settings, root string, a opts) int {
	if cmd := a.command(); len(cmd) > 0 {
		fmt.Fprintf(stderr, "ccnavi: --docs にはフラグ以外の語を付けられない（%s）\n", strings.Join(cmd, " "))
		return config.ExitError
	}
	format := docs.Format(a.Str("format"))
	if format == "" {
		format = docs.FormatTable
		if a.Truthy("json") {
			format = docs.FormatJSON
		}
	}
	if a.Truthy("json") && format != docs.FormatJSON {
		fmt.Fprintln(stderr, "ccnavi: --json と、json 以外の --format は同時に指定できない")
		return config.ExitError
	}
	sortBy := docs.Sort(a.Str("sort"))
	if sortBy == "" {
		sortBy = docs.SortPath
	}
	limit, _ := a.Int("limit")
	q := docs.Query{
		Types:    a.Strings("type"),
		Tags:     a.Strings("tag"),
		Keywords: a.Strings("keyword"),
		Paths:    a.Strings("path"),
		Texts:    a.Strings("text"),
		Since:    a.Str("since"),
		Until:    a.Str("until"),
		Sort:     sortBy,
		Reverse:  a.Truthy("reverse"),
		Limit:    limit,
		Format:   format,
	}
	return docs.Run(stdout, stderr, conf, root, q, !a.Truthy("no_refresh"))
}

// runPrune は記録と state の後始末を 1 度実行し、動かしたものを出す。消すのは記録なので、
// `--preview` でなければターミナルを求める。エージェントが打てると、
// しきい値を 0 に近づけて自分の記録を消せてしまうためである。
func runPrune(in *input, stdout, stderr io.Writer, conf *config.Settings, root string, preview bool) int {
	if !preview && !fromTerminal(in, conf, stderr, useronly.OpPrune) {
		return config.ExitError
	}
	report := prune.Run(root, conf.Log, conf.State, "", prune.Options{DryRun: preview})
	for _, line := range prune.Lines(report, preview) {
		fmt.Fprintln(stdout, line)
	}
	for _, problem := range report.Problems {
		fmt.Fprintf(stderr, "ccnavi: %s\n", problem)
	}
	return config.ExitOK
}

// fromTerminal は、ユーザの判断の経路がターミナルの前のユーザから打たれているかを返す。
// 標準入力がターミナルであることは、hook の中や `echo y |` から来ていないことを確かめる最も手間の少ない根拠にな
// る。CCNAVI_GUARD_TICKET_APPROVAL=disable で切れる（テストと端末の無い実行環境のため）。切り方は文面に書かない。
// 読むのはエージェントなので、書けばユーザの判断をエージェント自身が出す方法を教えることになる。
func fromTerminal(in *input, conf *config.Settings, stderr io.Writer, op useronly.Op) bool {
	if conf.GuardTicketApproval == string(config.Disable) {
		return true
	}
	if in.terminal() {
		return true
	}
	shown, _ := useronly.Terminal(op)
	fmt.Fprintf(stderr, "ccnavi: %s はユーザがターミナルから実行する必要があります。"+
		"標準入力がターミナルではないため、実行できません。ユーザにターミナルでの実行を依頼してください。\n", shown)
	return false
}

// `write-and-push sort` の版は sha か `refs/remotes/origin/<親のブランチ>` の形だけ（git の引数として読まれる文字列を
// 入れない）。
var shaForm = regexp.MustCompile(`\A[0-9a-f]{7,64}\z`)

const originRef = "refs/remotes/origin/"

func revisionOK(since string) bool {
	if shaForm.MatchString(since) {
		return true
	}
	name, ok := strings.CutPrefix(since, originRef)
	if !ok {
		return false
	}
	return ids.IsValidID(name) || ids.BranchProblem(name) == ""
}

// resolveGate は承認の経路とチケット制御の切り替え（enable / disable の 2 値）を解く。読めない値は enable にする。
func resolveGate(stderr io.Writer, flag, declared, name string) string {
	return string(selfguard.Resolve(stderr, flag, declared, name, selfguard.GateChoices))
}
