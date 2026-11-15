package start

import (
	"fmt"
	"regexp"
	"strings"

	"ccnavi/internal/policy/texts"
)

// GuidanceArgs は、ホストに届かないときの案内に要る値。ccnavi-start.sh が `<名前>=<値>` で渡す。
type GuidanceArgs struct {
	Stage      string // mcp-args・search・title・merged・issue-link・create
	Why        string // 届かなかった理由
	Kind       string // github か gitlab
	Path       string // owner/repo
	Host       string // ホストの名前
	What       string // issue か mr（開始の種類）
	Number     string // 開始の番号
	Issue      string // --issue の値
	MR         string // --mr の値
	Branch     string // --branch の値
	StopBranch string // 止まるまでに決まった元ブランチ
	StopMR     string // 閉じる issue を確かめていた MR の番号
	StopTarget string // MR のマージ先
	StopTitle  string // MR のタイトル
	StopWT     string // 作ったワークツリー
	Orig       string // 打ち直しに使う元の引数（引用済み）
	Total      string // 候補の数
}

// quote は sh で 1 語として打てる形に引用する（ccnavi-start.sh の sh_quote と同じ）。
func quote(s string) string { return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'" }

// httpStatus は理由の中の HTTP の状態（`HTTP 401` など）を見つける。
var httpStatus = regexp.MustCompile(`HTTP [0-9]{3}`)

// Guidance は、ホストに届かないときに ccnavi-start.sh が標準出力に出す案内を組み立てる。GitHub は MCP で代行する
// 手順を、GitLab は理由の 1 語と、ユーザに認証を頼んで打ち直す次の一手を出す。文面ファイルの文は root から引く。
// 文面ファイルの文を引けなければ、組み立てた分を捨てて unreadable（sh が短い既定の文で止める）。
func Guidance(root string, a GuidanceArgs) (string, int) {
	var b strings.Builder
	p := func(format string, args ...any) { fmt.Fprintf(&b, format, args...) }
	say := func(id string, values texts.Values) bool {
		entry := texts.Find(root, id)
		if entry == nil {
			return false
		}
		s, err := texts.Render(entry, values, texts.Variants{}, root)
		if err != nil {
			return false
		}
		b.WriteString(s + "\n")
		return true
	}
	owner, repoName, found := strings.Cut(a.Path, "/")
	if !found {
		repoName = a.Path // sh の ${path#*/} と同じく、/ が無ければ全体
	}
	or := func(v, def string) string {
		if v == "" {
			return def
		}
		return v
	}
	branchV := "<元ブランチ>"
	if a.StopBranch != "" {
		branchV = quote(a.StopBranch)
	}
	const start = "sh .ccnavi/scripts/ccnavi-start.sh"
	var cmd string
	switch a.Stage {
	case "create":
		cmd = fmt.Sprintf("%s --issue %s --mr <作った番号> --branch %s --mcp-checked", start, a.Number, branchV)
	case "issue-link":
		cmd = fmt.Sprintf("%s --mr %s --issue %s --branch %s --mcp-checked", start, a.StopMR, or(a.Issue, "<issue の番号>"), branchV)
	case "mcp-args":
		cmd = fmt.Sprintf("%s --issue %s --mr %s --branch %s --mcp-checked", start,
			or(a.Issue, "<issue の番号>"), or(a.MR, "<MR の番号>"), or(a.Branch, "<元ブランチ>"))
	default:
		cmd = start + " " + a.Orig
	}
	// 既存の候補や MR を採用する道（手元の候補がある・--mr が指定されている）は、3 つがそろわないと進めない。
	adopt := (a.Total != "" && a.Total != "0") || a.What == "mr" || a.MR != ""
	adoptCmd := fmt.Sprintf("%s --issue %s --mr %s --branch %s --mcp-checked", start,
		or(a.Issue, "<issue の番号>"), or(a.MR, "<MR の番号>"), or(a.Branch, branchV))

	if a.Stage == "mcp-args" {
		missing := ""
		for _, m := range []struct{ value, name string }{{a.Issue, "--issue"}, {a.MR, "--mr"}, {a.Branch, "--branch"}} {
			if m.value == "" {
				missing += " " + m.name
			}
		}
		p("ccnavi-start: ホストを見られない間は、--mcp-checked は --issue・--mr・--branch の 3 つがそろうときだけ指定できます（足りない:%s）。何も作成せずに処理を中断しました。\n", missing)
		id := "NEXT_START_MCP_ARGS__GITLAB"
		if a.Kind == "github" {
			id = "NEXT_START_MCP_ARGS__GITHUB"
		}
		if !say(id, texts.Values{"cmd_q": cmd}) {
			return "", unreadable
		}
		return b.String(), ok
	}
	done := func() {
		p("完了済みの処理:\n")
		if a.Stage == "create" {
			p("  ブランチ: %s（push 済み）\n  ワークツリー: %s\n", a.StopBranch, a.StopWT)
		} else {
			p("  なし（ブランチもワークツリーも作っていない）\n")
		}
	}
	if a.Kind != "github" {
		word := "応答なし"
		switch {
		case httpStatus.MatchString(a.Why):
			_, after, _ := strings.Cut(a.Why, "HTTP ")
			before, _, _ := strings.Cut(after, ":")
			word = "HTTP " + before
		case strings.Contains(a.Why, "利用できず"):
			word = strings.TrimSuffix(a.Why, "。")
		}
		p("ccnavi-start: GitLab の API に届かない（%s）。処理を中断しました。\n", word)
		done()
		host := or(a.Host, "?")
		if !say("NEXT_START_HOST__GITLAB", texts.Values{"host": host, "args_q": a.Orig}) {
			return "", unreadable
		}
		return b.String(), ok
	}

	const mcp = "MCP（mcp__github__のツール）で代行してください。"
	switch a.Stage {
	case "search":
		p("ccnavi-start: GitHub APIに接続できず、%s #%s に紐づく既存の MR を確かめられなかった（%s）。既存の MR を見落として重複を作らないため、何も作成せずに処理を中断しました。%s\n", a.What, a.Number, a.Why, mcp)
	case "title":
		p("ccnavi-start: GitHub APIに接続できず、issue #%s を読めなかった（%s）。実在を確かめられないので、何も作成せずに処理を中断しました。%s\n", a.Number, a.Why, mcp)
	case "merged":
		p("ccnavi-start: GitHub APIに接続できず、ブランチ %s と同じ名前のマージ済みの MR があるかを確かめられなかった（%s）。マージ済みのブランチを再利用しないため、何も作成せずに処理を中断しました。%s\n", a.StopBranch, a.Why, mcp)
	case "issue-link":
		p("ccnavi-start: MR !%s が閉じる issue を確かめられなかった（%s）。issue と MR のセットが確定しないため、続けずに処理を中断しました。%s\n", a.StopMR, a.Why, mcp)
	default:
		p("ccnavi-start: GitHub APIに接続できず、プルリクエストを作れなかった（%s）。%s\n", a.Why, mcp)
	}
	done()
	p("MCP で行う手順:\n")
	switch a.Stage {
	case "issue-link":
		p("  1. mcp__github__pull_request_read を method=get owner=%s repo=%s pullNumber=%s で呼び、head.ref（元ブランチ）と本文を読む。本文の Closes / Fixes / Resolves #<番号> が、この MR が閉じる issue\n", owner, repoName, a.StopMR)
		p("  2. mcp__github__issue_read を method=get owner=%s repo=%s issue_number=<その番号> で呼び、issue が実在することを確かめる（Closes が無い・複数あるときは、ユーザに伝えて止める）\n", owner, repoName)
		p("  3. 元ブランチと issue が決まったら、打ち直す: %s\n", cmd)
	case "search":
		if a.What == "issue" {
			p("  1. mcp__github__search_pull_requests を query=「repo:%s/%s is:pr is:open %s」で呼ぶ。タイトル・本文に #%s を含むか、元ブランチの名前に %s を含む（前後が - / _ . か端。区切り以外の文字が隣なら含まない）PR が、この issue に紐づく MR\n", owner, repoName, a.Number, a.Number, a.Number)
			p("  2. 見つかったら、一覧をユーザに見せ、既存のブランチで続けるか・新しく切るか・やめるかを聞く（続けるなら、その PR の元ブランチ（head.ref）と本文の Closes を確かめ、--mr <その PR の番号> --issue <閉じる issue> --branch <head.ref> --mcp-checked を付けて打ち直す）\n")
			p("  3. 紐づく PR が無いと確かめたら、mcp__github__issue_read を method=get owner=%s repo=%s issue_number=%s で呼び、issue が実在することも確かめる（無ければ番号を作らず、ユーザに伝えて止める）\n", owner, repoName, a.Number)
			if adopt {
				p("  4. 手元に候補があるので、--mcp-checked を足すだけでは進めない。紐づく PR が見つからないときは、ユーザに相談して止まる\n")
			} else {
				p("  4. 実在を確かめたら、同じコマンドに --mcp-checked を付けて再実行する: %s --mcp-checked\n", cmd)
			}
		} else {
			p("  1. mcp__github__pull_request_read を method=get owner=%s repo=%s pullNumber=%s で呼び、head.ref（元ブランチ）と head.repo.full_name を読む\n", owner, repoName, a.Number)
			p("  2. head.repo.full_name が %s/%s と違えばフォークの MR。元ブランチがこのリポジトリに無いので作業を始められない。ユーザに伝えて止める\n", owner, repoName)
			p("  3. 同じなら head.ref を元ブランチとし、本文の Closes で閉じる issue を確かめ（mcp__github__issue_read で実在も確かめる）、--mr %s --issue <閉じる issue> --branch <head.ref> --mcp-checked を付けて打ち直す\n", a.Number)
		}
	case "merged":
		p("  1. mcp__github__list_pull_requests を owner=%s repo=%s head=%s:%s state=closed で呼び、マージ済み（merged_at がある）の PR が無いことを確かめる\n", owner, repoName, owner, a.StopBranch)
		p("     mcp__github__* がセッションに無いときは gh で代行する: gh api -X GET repos/%s/%s/pulls -f state=closed -f per_page=100 -f head=%s:%s（-X GET を付ける。-f だけだと POST になる。-f なら gh が値をエンコードする）\n", owner, repoName, owner, a.StopBranch)
		p("  2. あれば、マージ済みのブランチは再利用できない。ユーザに伝えて止める\n")
		if adopt {
			p("  3. 無いと確かめたら、既存の MR を採用する形で、issue・MR・元ブランチの 3 つをそろえて打ち直す（値が確かめられないときは、ユーザに相談して止まる）: %s\n", adoptCmd)
		} else {
			p("  3. 無いと確かめたら、同じコマンドに --mcp-checked を付けて再実行する: %s --mcp-checked\n", cmd)
		}
	case "title":
		p("  1. mcp__github__issue_read を method=get owner=%s repo=%s issue_number=%s で呼び、issue が実在することを確かめてタイトルを読む（無ければ番号を作らず、ユーザに伝えて止める）\n", owner, repoName, a.Number)
		p("  2. タイトルから ASCII の小文字・数字・ハイフンの語を決め、再実行する: %s --slug <語> --title '<タイトル>' --mcp-checked\n", cmd)
	default:
		p("  1. mcp__github__list_pull_requests を owner=%s repo=%s head=%s:%s state=open で呼び、このブランチの開いた PR が無いことを確かめる（あればその番号を <作った番号> にして 3 へ）\n", owner, repoName, owner, a.StopBranch)
		p("  2. mcp__github__create_pull_request を次の値で呼ぶ\n")
		p("       owner: %s\n       repo: %s\n       head: %s\n       base: %s\n       title: %s\n       body: Closes #%s\n       draft: true\n", owner, repoName, a.StopBranch, a.StopTarget, a.StopTitle, a.Number)
		p("  3. 作れた PR の番号を <作った番号> に入れて、打ち直す: %s（ブランチとワークツリーがあるので、続きから進んでワークツリーのパスを出す）\n", cmd)
	}
	return b.String(), ok
}
