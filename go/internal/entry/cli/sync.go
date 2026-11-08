package cli

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
	// syncRetiredHead・syncCheckHead は答えの頭の行。sh はこれが無ければ「検査を実行できなかった」と読み、親子の
	// チケットを止めない（検査の error と分ける）。
	syncRetiredHead = "retired 1"
	syncCheckHead   = "check 1"
	// selfRepo はワークスペース自身のリポジトリの名前。
	selfRepo = "self"
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

// syncPaths は `ccnavi-sync.sh` が要るパスと名前を 1 行 1 項目（`<キー> <値>`）で返す。sh は jq を使わない。
// 置き場は固定（設定の既定）で env では動かない。ネットワークにも git にも触らない。
func syncPaths(stdout io.Writer, conf *config.Settings) int {
	slashed := func(p string) string { return strings.Trim(ospath.Slashed(p), "/") }
	fmt.Fprintf(stdout, "approved %s\n", slashed(conf.Approved))
	fmt.Fprintf(stdout, "proposals %s\n", slashed(conf.Tickets))
	fmt.Fprintf(stdout, "home %s\n", slashed(cmp.Or(conf.ProjectHome, config.DefaultProjectHome)))
	return config.ExitOK
}

// syncRetired は親を閉じた後の片付けの列挙元（`state/retire/<親>.json`）。読めない・mr が無いものは incomplete で、
// sh は一覧の案内だけを出し、自動では片付けない。
func syncRetired(stdout io.Writer, conf *config.Settings) int {
	fmt.Fprintln(stdout, syncRetiredHead)
	for _, e := range retire.Entries(conf) {
		if e.Incomplete != "" {
			fmt.Fprintf(stdout, "incomplete %s %s\n", e.Parent, ospath.Squeeze(e.Incomplete))
			continue
		}
		fmt.Fprintf(stdout, "retired %s %s %s %s\n", e.Parent, e.Repo, cmp.Or(e.MR, noValue), cmp.Or(e.Branch, noValue))
	}
	return config.ExitOK
}

// syncTidy は親の MR がマージ済みと分かった後の片付け（親のワークツリーと手元のブランチ、残っていた子のブランチ）。
// headSHA はマージ済みの MR の先頭（sh がホストから取ってきた）。ネットワークには出ない。
func syncTidy(stdout, stderr io.Writer, root string, conf *config.Settings, cwd, family, repo, branchName, headSHA string) int {
	if !formsOK(stderr, "sync tidy", "形式",
		formCheck{"識別子", family, ids.IsValidID},
		formCheck{"リポジトリ", repo, ids.IsValidName},
		formCheck{"sha", headSHA, shaForm.MatchString}) {
		return config.ExitError
	}
	// 片付けは MR のマージの後に走るので、合流と同じ期限で待つ。
	// ブランチ名は sh から渡る。git の名前の規則に合い、`-` で始まらず（git のオプションと読まれない）、
	// 親の識別子から作る名前と一致するものだけを扱う。
	checked := git.Run(root, []string{"check-ref-format", "--branch", branchName}, git.Options{Timeout: git.MergeTimeout})
	if branchName == "" || strings.HasPrefix(branchName, "-") || !checked.OK() {
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
	if repo == selfRepo {
		project = ""
	}
	return retire.RunMerged(stdout, conf, root, family, project, branchName, headSHA, cwd)
}

// syncCheck は取り込みの後の検査。頭に `check 1`、続けて 1 行 1 件（`error <detail>` か `warn <detail>`）。error が
// 1 つでもあれば 1。repo は self かプロジェクト名（空なら探す）。
func syncCheck(stdout, stderr io.Writer, root string, conf *config.Settings, family, repo string) int {
	if !formsOK(stderr, "sync check", "識別子の形式",
		formCheck{"識別子", family, ids.IsValidID},
		formCheck{"リポジトリ", cmp.Or(repo, selfRepo), ids.IsValidName}) {
		return config.ExitError
	}
	problems := lint.FamilyCheck(conf, root, family, repo)
	fmt.Fprintln(stdout, syncCheckHead)
	failed := config.ExitOK
	for _, p := range problems {
		fmt.Fprintf(stdout, "%s %s\n", p.Severity, ospath.Squeeze(p.Detail))
		if p.Severity == rules.Error {
			failed = config.ExitError
		}
	}
	return failed
}
