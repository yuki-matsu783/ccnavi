// Package preflight は、チケット作業の入口の事前確認（ccnavi-start.sh --check と ccnavi-ticket.sh）の判定と文を持つ。
//
// ホストの API とリモートの git が使える状態かを、項目ごとに確かめる。ネットワークに出る部分（ホストへの接続・
// プロジェクトの取得・権限の問い合わせ・ls-remote）は sh が行い、取ってきた事実を渡す。ここは手元で決まる項目
// （道具・origin・統合先・作成者）を自分で読み、事実から項目ごとの結果・勧める対処・行と次の一手を決める。
//
// 項目の結果は ok / ng（確定した不備。直すにはユーザの対応が要る）/ unverified（通信できない・時間切れ・応答が
// 読めないので確かめられなかった）/ skipped（前提の項目が ok でないので確かめなかった）。権限（access）に限り、
// ok に「判定できない」の印を付けてよい。
package preflight

import (
	"os/exec"
	"strings"
	"time"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/lifecycle/integrate"
)

// Items は確かめる項目の並び（行を出す順）。
var Items = []string{"tools", "origin", "integration", "api", "access", "git", "ident"}

// 項目の結果。
const (
	ok         = "ok"
	ng         = "ng"
	unverified = "unverified"
	skipped    = "skipped"
)

// Facts は sh が渡す事実。空の欄は「確かめていない」。
type Facts struct {
	// Top はリポジトリのトップ、Root はワークスペースルート（文面を引く）。
	Top, Root string
	// Parse は origin の URL をホストのものとして読めたか（ok / ng）、ParseWhy と Masked はその理由と伏せた URL。
	Parse, ParseWhy, Masked string
	// Kind・Host・Path はホストの種類（github / gitlab）・名前・リポジトリのパス。CLI と Token は使う CLI とトークンの名前。
	Kind, Host, Path, CLI, Token string
	// Connect はホストへの繋ぎの終了コード（0 繋げた / 4 CLI もトークンも無い / ほか CLI も curl も無い）、Transport は
	// 繋いだ経路（gh・glab・curl）、Probe はプロジェクトを読んだ結果（ok / auth / notfound / network / badjson）。
	Connect, Transport, Probe string
	// Access は書き込みの権限（ok / ng / unknown）と理由。
	Access, AccessWhy string
	// Git はリモートの ls-remote の終了コード、GitErr はその標準エラーの先頭。
	Git, GitErr string
}

// Result は事前確認の答え。
type Result struct {
	Kind  map[string]string
	Why   map[string]string
	Total string // ok / ng / unverified
	Fix   string // 勧める対処（tool origin integration auth notfound access git-auth ident network。無ければ空）
	Auth  string // 認証の出どころ（cli / token / none）
	// AccessUnknown は権限を判定できなかった（ok として続ける）。
	AccessUnknown bool
}

// Plan は手元で決まる項目を確かめ、ホストの API（api）とリモートの git（git）を確かめに行くかを返す。
func Plan(f Facts) (api, remote bool) {
	r := local(f)
	return r.Kind["origin"] == ok && r.Kind["tools"] == ok, r.Kind["origin"] == ok
}

// local は手元で決まる項目（道具・origin・統合先・作成者）を確かめる。ほかの項目は skipped のまま。
func local(f Facts) Result {
	r := Result{Kind: map[string]string{}, Why: map[string]string{}}
	for _, item := range Items {
		r.Kind[item] = skipped
	}
	// 1. 道具
	var lack []string
	for _, tool := range []string{"git", "jq"} {
		if _, err := exec.LookPath(tool); err != nil {
			lack = append(lack, tool)
		}
	}
	haveGit := !contains(lack, "git")
	if len(lack) > 0 {
		r.Kind["tools"], r.Why["tools"] = ng, strings.Join(lack, " と ")+" が見つからない"
	} else {
		r.Kind["tools"] = ok
		if _, err := exec.LookPath("curl"); err != nil {
			r.Why["tools"] = "curl は無い（gh / glab が利用できれば足りる）"
		}
	}
	// 2. origin
	if haveGit {
		code, out := git.Output(f.Top, []string{"remote", "get-url", "origin"}, git.Options{})
		switch {
		case code != 0 || strings.TrimSpace(out) == "":
			r.Kind["origin"], r.Why["origin"] = ng, "origin が無い"
		case f.Parse == ok:
			r.Kind["origin"] = ok
		default:
			why := "origin の URL をホストのものとして読めない（" + f.Masked + "）"
			if f.ParseWhy != "" {
				why += "。" + f.ParseWhy
			}
			r.Kind["origin"], r.Why["origin"] = ng, why
		}
	}
	// 3. 統合先
	if r.Kind["origin"] == ok {
		if name, _ := integrate.Integration(f.Top, time.Time{}); name != "" {
			r.Kind["integration"] = ok
		} else {
			r.Kind["integration"], r.Why["integration"] = ng, "統合先（origin/HEAD）が決まらない"
		}
	}
	// 7. 作成者（author と committer の両方。手元の設定だけを見る）
	if haveGit {
		if git.Run(f.Top, []string{"var", "GIT_AUTHOR_IDENT"}, git.Options{}).OK() &&
			git.Run(f.Top, []string{"var", "GIT_COMMITTER_IDENT"}, git.Options{}).OK() {
			r.Kind["ident"] = ok
		} else {
			r.Kind["ident"], r.Why["ident"] = ng, "git の作成者情報（user.name と user.email）が無く、コミットできない"
		}
	}
	return r
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

// Check は、手元で決まる項目と sh が渡した事実から、項目ごとの結果と勧める対処を決める。
func Check(f Facts) Result {
	r := local(f)
	apiKind := ""
	// 4. ホストの API
	if f.Connect != "" {
		switch f.Connect {
		case "0":
			r.Auth = map[string]string{"gh": "cli", "glab": "cli", "curl": "token"}[f.Transport]
			switch f.Probe {
			case ok:
				r.Kind["api"] = ok
			case "auth":
				r.Kind["api"], apiKind, r.Why["api"] = ng, "auth", "ホストが認証を受け付けない（HTTP 401 か 403）"
			case "notfound":
				r.Kind["api"], apiKind, r.Why["api"] = ng, "notfound", "リポジトリ "+f.Path+" が見つからない（HTTP 404）"
			case "badjson":
				r.Kind["api"], apiKind, r.Why["api"] = unverified, "network", "ホストの応答を JSON として読めない"
			default:
				r.Kind["api"], apiKind, r.Why["api"] = unverified, "network", "ホストに届かない（通信の失敗・時間切れ・5xx）"
			}
		case "4":
			r.Auth, r.Kind["api"], apiKind = "none", unverified, "auth"
			r.Why["api"] = f.CLI + " が " + f.Host + " で利用できず、curl に付ける " + f.Token + " もない"
		default:
			r.Auth, r.Kind["api"], apiKind = "none", unverified, "tool"
			r.Why["api"] = f.CLI + " が " + f.Host + " で利用できず、curl もない"
		}
	}
	// 5. 権限
	if r.Kind["api"] == ok && f.Access != "" {
		switch f.Access {
		case ok:
			r.Kind["access"] = ok
		case ng:
			r.Kind["access"], r.Why["access"] = ng, f.AccessWhy
		default:
			r.Kind["access"], r.AccessUnknown, r.Why["access"] = ok, true, "権限を判定できない（"+f.AccessWhy+"）"
		}
	}
	// 6. リモートの git（API の結果によらず行う）
	if f.Git != "" {
		switch {
		case f.Git == "0":
			r.Kind["git"] = ok
		case gitAuthFailed(f.GitErr):
			r.Kind["git"], r.Why["git"] = ng, "git のリモートへの認証が通らない"
		default:
			r.Kind["git"], r.Why["git"] = unverified, "git のリモートに届かない（時間切れか通信の失敗）"
		}
	}
	// まとめ
	r.Total = ok
	for _, item := range Items {
		switch r.Kind[item] {
		case ng:
			r.Total = ng
		case unverified, skipped:
			if r.Total != ng {
				r.Total = unverified
			}
		}
	}
	// 勧める対処: ng か unverified の項目のうち、次の順で最初に当たるもの
	switch {
	case r.Kind["tools"] == ng:
		r.Fix = "tool"
	case r.Kind["origin"] == ng:
		r.Fix = "origin"
	case r.Kind["integration"] == ng:
		r.Fix = "integration"
	case apiKind == "tool" || apiKind == "auth" || apiKind == "notfound":
		r.Fix = apiKind
	case r.Kind["access"] == ng:
		r.Fix = "access"
	case r.Kind["git"] == ng:
		r.Fix = "git-auth"
	case r.Kind["ident"] == ng:
		r.Fix = "ident"
	case r.Total != ok:
		r.Fix = "network"
	}
	return r
}

// Continue は、確かめきれなくても作業を続けてよいか。ホストの API だけを確かめられず（権限は確かめていない）、リモートの
// git は通るとき（子チケットの作業から始める操作は、このあとホストの API が要る操作まで進めてよい）。
func (r Result) Continue() bool {
	return r.Total == unverified && r.Kind["api"] == unverified && r.Kind["access"] == skipped && r.Kind["git"] == ok
}

// gitAuthFailed は、ls-remote の標準エラーが認証の失敗を言っているか。
func gitAuthFailed(text string) bool {
	for _, word := range []string{"Authentication failed", "could not read Username", "Permission denied", "error: 403", "HTTP 403", "returned error: 403"} {
		if strings.Contains(text, word) {
			return true
		}
	}
	return false
}

// Line は項目の 1 行（`<項目>: OK|NG|未確認|省略|注意 (<理由>)`）。
func (r Result) Line(item string) string {
	if item == "access" && r.AccessUnknown {
		return "access: 注意（" + r.Why[item] + "。続行する）"
	}
	word := map[string]string{ok: "OK", ng: "NG", unverified: "未確認"}[r.Kind[item]]
	if word == "" {
		word = "省略"
	}
	if why := r.Why[item]; why != "" {
		return item + ": " + word + " (" + why + ")"
	}
	return item + ": " + word
}

// Next は勧める対処の `次:` の行（無ければ空）。kind・host・path はホストの種類・名前・リポジトリのパス（分からなければ空）。
func Next(root, top, fix, kind, host, path string) string {
	if host == "" {
		host = "?"
	}
	if path == "" {
		path = "?"
	}
	github := kind == "github"
	switch fix {
	case "tool":
		return texts.Say(root, "NEXT_PF_TOOL", nil, nil)
	case "origin":
		return texts.Say(root, "NEXT_PF_ORIGIN", nil, nil)
	case "integration":
		return "次: " + IntegrationHint(top)
	case "auth":
		if github {
			return texts.Say(root, "NEXT_PF_AUTH__GITHUB", texts.Values{"host": host}, nil)
		}
		return texts.Say(root, "NEXT_PF_AUTH__GITLAB", texts.Values{"host": host}, nil)
	case "notfound":
		return texts.Say(root, "NEXT_PF_NOTFOUND", texts.Values{"path": path}, nil)
	case "access":
		return texts.Say(root, "NEXT_PF_ACCESS", texts.Values{"path": path}, nil)
	case "git-auth":
		if github {
			return texts.Say(root, "NEXT_PF_GIT_AUTH__GITHUB", texts.Values{"host": host}, nil)
		}
		return texts.Say(root, "NEXT_PF_GIT_AUTH__GITLAB", texts.Values{"host": host}, nil)
	case "ident":
		return texts.Say(root, "NEXT_PF_IDENT", nil, nil)
	case "network":
		if github {
			return texts.Say(root, "NEXT_PF_NETWORK__GITHUB", nil, nil)
		}
		return texts.Say(root, "NEXT_PF_NETWORK__GITLAB", nil, nil)
	}
	return ""
}

// IntegrationHint は、統合先が決まらないときの案内の 1 文。origin が無ければ「origin が無い」と言い、あれば origin/HEAD を
// 直す方法を示す。
func IntegrationHint(top string) string {
	if git.Run(top, []string{"remote", "get-url", "origin"}, git.Options{}).OK() {
		return "統合先 (origin/HEAD) が決まらない。ユーザに git fetch origin のあと git remote set-head origin -a を頼んでください。"
	}
	return "origin が無いので統合先が決まらない。ユーザに origin を足してもらい、git fetch origin のあと git remote set-head origin -a を頼んでください。"
}
