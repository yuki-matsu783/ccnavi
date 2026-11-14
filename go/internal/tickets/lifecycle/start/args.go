package start

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
)

// Options は ccnavi-start.sh の引数を読んだもの。
type Options struct {
	IssueArg, MRArg, BranchArg                string
	Slug, Prefix, Title, Repo                 string
	NewTitle, NewBody                         string
	Dry, MCP, Similar, Reopened, Check, Lists bool
	// What は issue か mr（開始の種類）、Number はその番号。IssueN・IssueR は --issue の番号とリポジトリ（番号だけなら空）。
	What, Number, IssueN, IssueR string
	// Orig は打ち直しの案内に使う引数の並び（引数ごとに単引用符で囲む）、Real はそこから --dry-run を外したもの。
	Orig, Real string
}

var (
	wordForm  = regexp.MustCompile(`^[a-z0-9]+(-[a-z0-9]+)*$`)
	repoForm  = regexp.MustCompile(`^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$`)
	issueRepo = regexp.MustCompile(`^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+$`)
)

// newTitleLimit は --new-issue のタイトルの上限（字）。
const newTitleLimit = 200

// Args は ccnavi-start.sh の引数 argv を読んで確かめる。cwd は打った場所、root はワークスペースルート。
//
// 答えは outcome の形。誤りは `fail`（終了コード 2。--repo の食い違いと git の外は 1）で、使い方を出すときは先に `usage yes`。
// --help は `help`。読めたら、値を `what`・`number`・`issue`・`issue-n`・`issue-r`・`mr`・`branch`・`slug`・`prefix`・
// `title`・`repo`・`new-title`・`body`・`orig`・`real`・`top` の行で、指定の有無を `dry`・`mcp`・`similar`・`reopened`・
// `check`・`list-open` の行（`yes`）で返す。
func Args(cwd, root string, argv []string) (string, int) {
	var o outcome
	opt, ok := parseArgs(&o, argv)
	if !ok {
		return o.String(), 0
	}
	top := strings.TrimRight(gitOut(cwd, "rev-parse", "--show-toplevel"), "\n")
	if top == "" {
		o.fail("not-git", exitUnmet, "cwd（"+ospath.Slashed(cwd)+"）が git のリポジトリの中ではありません。ワークスペースか projects/<名前>/ の中で実行してください。")
		return o.String(), 0
	}
	if opt.Repo != "" && !sameRepo(&o, cwd, root, opt) {
		return o.String(), 0
	}
	for _, kv := range [][2]string{{"what", opt.What}, {"number", opt.Number}, {"issue", opt.IssueArg}, {"issue-n", opt.IssueN},
		{"issue-r", opt.IssueR}, {"mr", opt.MRArg}, {"branch", opt.BranchArg}, {"slug", opt.Slug}, {"prefix", opt.Prefix},
		{"title", opt.Title}, {"repo", opt.Repo}, {"new-title", opt.NewTitle}, {"body", opt.NewBody}, {"orig", opt.Orig},
		{"real", opt.Real}, {"top", top}} {
		o.val(kv[0], kv[1])
	}
	for _, kv := range []struct {
		key string
		on  bool
	}{{"dry", opt.Dry}, {"mcp", opt.MCP}, {"similar", opt.Similar}, {"reopened", opt.Reopened}, {"check", opt.Check}, {"list-open", opt.Lists}} {
		if kv.on {
			o.val(kv.key, "yes")
		}
	}
	return o.String(), 0
}

// gitOut は cwd で git を起こした標準出力（失敗なら空）。
func gitOut(cwd string, args ...string) string {
	code, out := git.Output(cwd, args, git.Options{})
	if code != 0 {
		return ""
	}
	return out
}

// parseArgs は引数を読む。誤りなら o に fail を書いて偽。
func parseArgs(o *outcome, argv []string) (Options, bool) {
	opt := Options{Prefix: "feature"}
	var orig, real []string
	for _, a := range argv {
		orig = append(orig, quote(a))
		if a != "--dry-run" {
			real = append(real, quote(a))
		}
	}
	opt.Orig, opt.Real = strings.Join(orig, " "), strings.Join(real, " ")
	failed := func(id, msg string) (Options, bool) {
		o.fail(id, exitUsage, msg)
		return opt, false
	}
	for i := 0; i < len(argv); i++ {
		a := argv[i]
		value := func() (string, bool) {
			if i+1 >= len(argv) {
				return "", false
			}
			i++
			return argv[i], true
		}
		switch a {
		case "--issue", "--mr", "--branch":
			v, ok := value()
			if !ok {
				return failed("no-number", a+" の後ろに値を渡してください。")
			}
			target, id := map[string]*string{"--issue": &opt.IssueArg, "--mr": &opt.MRArg, "--branch": &opt.BranchArg}[a], "two-"+a[2:]
			if *target != "" {
				return failed(id, a+" は 1 つだけ渡してください。")
			}
			*target = v
		case "--new-issue", "--issue-body":
			v, ok := value()
			if !ok {
				return failed("no-value", a+" の後ろに値を渡してください。")
			}
			if a == "--issue-body" {
				opt.NewBody = v
				continue
			}
			if opt.NewTitle != "" {
				return failed("two-new-issue", "--new-issue は 1 つだけ渡してください。")
			}
			opt.NewTitle = v
		case "--slug", "--prefix", "--title", "--repo":
			v, ok := value()
			if !ok {
				return failed("no-value", a+" の後ろに値を渡してください。")
			}
			*map[string]*string{"--slug": &opt.Slug, "--prefix": &opt.Prefix, "--title": &opt.Title, "--repo": &opt.Repo}[a] = v
		case "--dry-run":
			opt.Dry = true
		case "--mcp-checked":
			opt.MCP = true
		case "--similar-checked":
			opt.Similar = true
		case "--reopened":
			opt.Reopened = true
		case "--check":
			opt.Check = true
		case "--list-open":
			opt.Lists = true
		case "-h", "--help", "help":
			o.val("help", "yes")
			return opt, false
		default:
			o.val("usage", "yes")
			return failed("unknown-arg", "未対応の引数です（"+a+"）。")
		}
	}
	if !combination(o, &opt, len(argv)) || !numbers(o, &opt) {
		return opt, false
	}
	if opt.Slug != "" && !wordForm.MatchString(opt.Slug) {
		return failed("bad-slug", "--slug は小文字・数字・ハイフンだけで書いてください（"+opt.Slug+"）。")
	}
	if !wordForm.MatchString(opt.Prefix) {
		return failed("bad-prefix", "--prefix は小文字・数字・ハイフンだけで書いてください（"+opt.Prefix+"）。")
	}
	if opt.What == "mr" && (opt.Slug != "" || opt.Title != "") {
		return failed("mr-with-new", "--mr には --slug と --title を付けられません（既存の MR の元ブランチで続けるだけです）。")
	}
	if opt.Repo != "" && !repoForm.MatchString(opt.Repo) {
		return failed("bad-repo", "--repo は owner/repo の形で書いてください（"+opt.Repo+"）。")
	}
	return opt, true
}

// combination は、一緒に渡せない引数と、--new-issue のタイトルを確かめる。count は引数の数。
func combination(o *outcome, opt *Options, count int) bool {
	failed := func(id, msg string) bool {
		o.fail(id, exitUsage, msg)
		return false
	}
	switch {
	case opt.Check:
		if count != 1 {
			return failed("check-alone", "--check は単独で使います（ほかの引数とは一緒に渡せません）。")
		}
		return true
	case opt.Lists:
		if count != 1 {
			return failed("list-open-alone", "--list-open は単独で使います（ほかの引数とは一緒に渡せません）。")
		}
		return true
	case opt.IssueArg == "" && opt.MRArg == "" && opt.NewTitle == "":
		o.val("usage", "yes")
		return failed("no-kind", "--issue <番号> か --mr <番号> を渡してください（チケット作業は issue と MR のセットです。issue がまだ無ければ --new-issue <タイトル>）。")
	}
	if opt.NewTitle != "" {
		if opt.IssueArg != "" || opt.MRArg != "" || opt.BranchArg != "" || opt.MCP {
			return failed("new-issue-alone", "--new-issue は --issue・--mr・--branch・--mcp-checked と一緒に渡せません（issue を作ってから、その番号で進みます）。")
		}
		opt.NewTitle = strings.Trim(clean(opt.NewTitle), " ")
		if opt.NewTitle == "" {
			return failed("bad-new-issue", "--new-issue のタイトルが空です。作る issue のタイトルを渡してください。")
		}
		if utf8.RuneCountInString(opt.NewTitle) > newTitleLimit {
			return failed("bad-new-issue", "--new-issue のタイトルが長すぎます（200 文字まで）。")
		}
	}
	switch {
	case opt.NewTitle == "" && opt.NewBody != "":
		return failed("body-alone", "--issue-body は --new-issue と一緒のときだけ渡せます。")
	case opt.NewTitle == "" && opt.Similar:
		return failed("similar-alone", "--similar-checked は --new-issue と一緒のときだけ渡せます。")
	case opt.Reopened && opt.IssueArg == "":
		return failed("reopened-alone", "--reopened は --issue と一緒のときだけ渡せます。")
	case opt.BranchArg != "" && !opt.MCP:
		return failed("branch-alone", "--branch は --mcp-checked と一緒のときだけ渡せます。")
	}
	return true
}

// numbers は --issue と --mr の値を読み、開始の種類と番号を決める。
func numbers(o *outcome, opt *Options) bool {
	failed := func(id, msg string) bool {
		o.fail(id, exitUsage, msg)
		return false
	}
	positive := func(s string) bool {
		return s != "" && s[0] != '0' && strings.Trim(s, "0123456789") == ""
	}
	if opt.IssueArg != "" {
		opt.IssueN = opt.IssueArg
		if i := strings.LastIndex(opt.IssueArg, "#"); i >= 0 {
			opt.IssueR, opt.IssueN = opt.IssueArg[:i], opt.IssueArg[i+1:]
			if !issueRepo.MatchString(opt.IssueR) {
				return failed("bad-issue", "--issue の値が読めません（"+opt.IssueArg+"）。<番号> か <owner/repo>#<番号> で渡してください。")
			}
		}
		if !positive(opt.IssueN) {
			return failed("bad-number", "issue の番号は 0 で始まらない正の整数で渡してください（"+opt.IssueArg+"）。")
		}
		if len(opt.IssueN) > 9 {
			return failed("bad-number", "issue の番号が長すぎます（"+opt.IssueArg+"）。")
		}
	}
	switch {
	case opt.MRArg != "":
		if !positive(opt.MRArg) {
			return failed("bad-number", "MR の番号は 0 で始まらない正の整数で渡してください（"+opt.MRArg+"）。")
		}
		if len(opt.MRArg) > 9 {
			return failed("bad-number", "MR の番号が長すぎます（"+opt.MRArg+"）。")
		}
		opt.What, opt.Number = "mr", opt.MRArg
	case opt.IssueArg != "":
		opt.What, opt.Number = "issue", opt.IssueN
	}
	return true
}

// repoOfURL は git の URL のパスの最後の 2 つ（owner/repo）。読めなければ空。
func repoOfURL(url string) string {
	url = strings.ReplaceAll(url, "\r", "")
	url = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9+.-]*://`).ReplaceAllString(url, "")
	url = regexp.MustCompile(`^[^/@]*@`).ReplaceAllString(url, "")
	url = regexp.MustCompile(`^[^/:]*[:/]`).ReplaceAllString(url, "")
	url = strings.TrimSuffix(strings.TrimRight(url, "/"), ".git")
	parts := strings.Split(url, "/")
	if len(parts) < 2 {
		return ""
	}
	return parts[len(parts)-2] + "/" + parts[len(parts)-1]
}

// originRepo は dir の origin の owner/repo。
func originRepo(dir string) string {
	return repoOfURL(strings.TrimRight(gitOut(dir, "remote", "get-url", "origin"), "\n"))
}

// sameRepo は、依頼のリポジトリ（--repo）が、打った場所の origin と同じかを確かめる。違えば移動先を案内して止める
// （ホストには問い合わせる前）。大文字小文字は区別しない。
func sameRepo(o *outcome, cwd, root string, opt Options) bool {
	here := originRepo(cwd)
	if here == "" || strings.EqualFold(here, opt.Repo) {
		return true
	}
	head := "依頼のリポジトリ（" + opt.Repo + "）と、実行した場所のリポジトリ（" + here + "）が違います。ホストには問い合わせていません。"
	script := shq(ospath.Join(root, ".ccnavi", "scripts", "ccnavi-start.sh"))
	if strings.EqualFold(originRepo(root), opt.Repo) {
		o.fail("wrong-repo", exitUnmet, head+"ワークスペースルートに移動して実行し直してください: cd "+shq(root)+" && sh "+script+" "+opt.Orig)
		return false
	}
	dirs, _ := filepath.Glob(filepath.Join(root, "projects", "*"))
	for _, d := range dirs {
		if _, err := os.Stat(filepath.Join(d, ".git")); err != nil {
			continue
		}
		if r := originRepo(d); r != "" && strings.EqualFold(r, opt.Repo) {
			name := "projects/" + filepath.Base(d)
			o.fail("wrong-repo", exitUnmet, head+name+"/ に移動して実行し直してください: cd "+shq(ospath.Join(root, name))+" && sh "+script+" "+opt.Orig)
			return false
		}
	}
	o.fail("wrong-repo", exitUnmet, head+opt.Repo+" を origin に持つプロジェクトは projects/ に見つかりません（まだ clone していないなど）。ユーザに伝えて止まってください。")
	return false
}
