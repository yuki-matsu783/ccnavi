package catchup

import (
	"cmp"
	"fmt"
	"io"
	"strings"

	"ccnavi/internal/entry/lint"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/lifecycle/retire"
	"ccnavi/internal/tickets/model/ids"
)

const (
	// retiredHead・checkHead は答えの頭の行。読む側（ボード・テスト）は、これが無ければ「実行できなかった」と読む。
	retiredHead = "retired 1"
	checkHead   = "check 1"
	// noValue は答えの値が無いときの印。
	noValue = "-"
)

// formCheck は sh から渡る値 1 つの形の検査。
type formCheck struct {
	name, value string
	ok          func(string) bool
}

// formsOK は値がどれも形に合い、パスとして読まれる ".." を含まないか。合わない最初の値を kind（「形式」など）で言う。
func formsOK(stderr io.Writer, verb, kind string, checks ...formCheck) bool {
	for _, c := range checks {
		if !c.ok(c.value) || strings.Contains(c.value, "..") {
			fmt.Fprintf(stderr, "ccnavi: %s の%s %s は有効な%sではありません。\n", verb, c.name, pyfmt.Repr(c.value), kind)
			return false
		}
	}
	return true
}

// Retired は `sync retired`。親を閉じた後の片付けの列挙元（`state/retire/<親>.json`）。読めない・mr が無いものは
// incomplete で、読む側は一覧の案内だけを出し、自動では片付けない。
func Retired(stdout io.Writer, conf *config.Settings) int {
	fmt.Fprintln(stdout, retiredHead)
	for _, e := range retire.Entries(conf) {
		if e.Incomplete != "" {
			fmt.Fprintf(stdout, "incomplete %s %s\n", e.Parent, ospath.Squeeze(e.Incomplete))
			continue
		}
		fmt.Fprintf(stdout, "retired %s %s %s %s\n", e.Parent, e.Repo, cmp.Or(e.MR, noValue), cmp.Or(e.Branch, noValue))
	}
	return config.ExitOK
}

// Tidy は `sync tidy <親> <リポジトリ> <ブランチ> <sha>` と、ccnavi-sync.sh の片付け（merged）。親の MR がマージ済みと
// 分かった後に、親のワークツリーと手元のブランチなどを片付ける（retire.RunMerged）。headSHA はマージ済みの MR の先頭
// （sh がホストから取ってきた）。ネットワークには出ない。
func Tidy(stdout, stderr io.Writer, root string, conf *config.Settings, cwd, family, repo, branchName, headSHA string) int {
	if !formsOK(stderr, "sync tidy", "形式",
		formCheck{"識別子", family, ids.IsValidID},
		formCheck{"リポジトリ", repo, ids.IsValidName},
		formCheck{"sha", headSHA, git.IsSHA}) {
		return config.ExitError
	}
	// 片付けは MR のマージの後に走るので、合流と同じ期限で待つ。
	// ブランチ名は sh から渡る。git の名前の規則に合い、`-` で始まらず（git のオプションと読まれない）、
	// 親の識別子から作る名前と一致するものだけを扱う。
	checkedRef := git.Run(root, []string{"check-ref-format", "--branch", branchName}, git.Options{Timeout: git.MergeTimeout})
	if branchName == "" || strings.HasPrefix(branchName, "-") || !checkedRef.OK() {
		fmt.Fprintf(stderr, "ccnavi: sync tidy のブランチ %s はブランチ名の形ではない\n", pyfmt.Repr(branchName))
		return config.ExitError
	}
	if made := ids.BranchToID(branchName); made != family {
		// 親のブランチだけを消す。別のブランチを渡して消させない。
		fmt.Fprintf(stderr, "ccnavi: sync tidy のブランチ %s は親 %s のブランチではない（ブランチから作る識別子は %s）\n",
			pyfmt.Repr(branchName), family, pyfmt.Repr(made))
		return config.ExitError
	}
	// sh はワークスペース自身を self と呼ぶが、内部ではプロジェクト名を空で表す。
	project := repo
	if repo == selfKey {
		project = ""
	}
	return retire.RunMerged(stdout, conf, root, family, project, branchName, headSHA, cwd)
}

// selfKey はワークスペース自身のリポジトリの名前。
const selfKey = "self"

// Check は `sync check <親> [<リポジトリ>]`。取り込みの後の検査で、頭に `check 1`、続けて 1 行 1 件（`error <detail>`
// か `warn <detail>`）。error が 1 つでもあれば 1。repo は self かプロジェクト名（空なら探す）。
func Check(stdout, stderr io.Writer, root string, conf *config.Settings, family, repo string) int {
	if !formsOK(stderr, "sync check", "識別子の形式",
		formCheck{"識別子", family, ids.IsValidID},
		formCheck{"リポジトリ", cmp.Or(repo, selfKey), ids.IsValidName}) {
		return config.ExitError
	}
	fmt.Fprintln(stdout, checkHead)
	failed := config.ExitOK
	for _, p := range checked(conf, root, family, repo) {
		if p.severity == rules.Error {
			failed = config.ExitError
		}
		fmt.Fprintf(stdout, "%s %s\n", p.severity, p.detail)
	}
	return failed
}

// problem は取り込みの後の検査の 1 件。
type problem struct {
	severity rules.Severity
	detail   string
}

// checked は取り込みの後の検査（lint.FamilyCheck）の結果を、1 行にした理由で返す。
func checked(conf *config.Settings, root, family, repo string) []problem {
	var out []problem
	for _, p := range lint.FamilyCheck(conf, root, family, repo) {
		out = append(out, problem{severity: p.Severity, detail: ospath.Squeeze(p.Detail)})
	}
	return out
}
