package host

import (
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"strings"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/pyfmt"
)

// remote は origin の URL の読み方。sh の ccnavi_host_parse（ccnavi-common-host.sh）と Chrome 拡張の
// hostclass.ts と同じ規則で、tests/fixtures/host-kinds.json の同じ表で突き合わせる。
//   - ホストにはポートが付く（localhost:8929）。ssh の git@host:group/proj の `:` はパスの区切りなので、
//     数字だけのときにポートと見なす
//   - ユーザ情報（https://oauth2:token@host/）は authority の最後の `@` までと見る（トークンに `@` が入る）
//   - IPv6 は [::1] の形。sh が読めない表記（ssh://user@host/）はここでも読めない扱いにする
//
// Python の `$` は末尾の改行の前でも当たるので、`\n?` を置いて同じ答えにする。
var remote = regexp.MustCompile(
	`^(?:(?i:https?)://|(?i:ssh)://git@|git@)(?:[^/]*@)?` +
		`(?P<host>\[[^\]/]+\]|[^/:@]+)(?::(?P<port>[0-9]+))?[/:]+(?P<path>.+?)(?:\.git)?/?\n?$`)

// credentials は URL に埋めた資格情報（`//user:token@`）。苦情の文には残さない。
var credentials = regexp.MustCompile(`//[^/@]*@`)

const (
	githubHost    = "github.com"
	githubAPI     = "https://api.github.com"
	gheDomain     = "ghe.com"
	graphQLSuffix = "/graphql"
	gitlabAPIPath = "/api/v4"
	httpPrefix    = "http://"
	hidden        = "//<伏せた>@"
)

// 道具の名前。
const (
	toolJQ   = "jq"
	toolCurl = "curl"
	toolGH   = "gh"
	toolGLab = "glab"
)

const (
	msgGHEPort     = "GHE.com のホスト %s にポート :%s が付いているので、API の URL（https://api.%s）を組めない"
	msgNotAHost    = "origin (%s) が GitHub でも GitLab でもない"
	msgNoJQ        = "jqが見つかりません。結果の JSON を作成できません"
	msgNoTransport = "%s も curl もありません。MCP などでユーザがリモートを読む形にするか、どちらかを入れてください"
	msgNoToken     = "%s が無く、curl に付ける %s もありません。"
)

// Class は origin の URL から決めたホストの分類と接続先。Kind が空なら読めない表記。
//
// Host は小文字にしてポートを除いたもので、照合は `.` の区切りで行う（evilgithub.com や github.com.evil は
// GitHub にならない）。GitHub は github.com と ghe.com・*.ghe.com で、ほかは全部 GitLab（接続先は origin の
// scheme・ホスト・ポートを保つ）。GHE.com の origin にポートが付くと API の URL を組めないので Problem を言う
// （GHE.com の API の URL の規則は実物では未検証）。
type Class struct {
	Kind    Kind
	Host    string
	API     string
	GraphQL string
	Path    string
	Problem string
}

func isGHEHost(host string) bool {
	return host == gheDomain || (strings.HasSuffix(host, "."+gheDomain) && len(host) > len(gheDomain)+1)
}

// Classify は origin の URL を GitHub か GitLab かに分け、接続先を決める。
func Classify(url string) Class {
	m := remote.FindStringSubmatch(url)
	if m == nil {
		return Class{}
	}
	raw, port, path := m[remote.SubexpIndex("host")], m[remote.SubexpIndex("port")], m[remote.SubexpIndex("path")]
	host := pyfmt.Lower(raw)
	switch {
	case host == githubHost:
		return Class{GitHub, host, githubAPI, githubAPI + graphQLSuffix, path, ""}
	case isGHEHost(host) && port != "":
		return Class{Kind: GitHub, Host: host, Path: path, Problem: fmt.Sprintf(msgGHEPort, host, port, host)}
	case isGHEHost(host):
		api := "https://api." + host
		return Class{GitHub, host, api, api + graphQLSuffix, path, ""}
	}
	scheme := "https"
	if strings.EqualFold(pyfmt.Head(url, len(httpPrefix)), httpPrefix) {
		scheme = "http"
	}
	authority := raw
	if port != "" {
		authority += ":" + port
	}
	return Class{GitLab, host, scheme + "://" + authority + gitlabAPIPath, "", path, ""}
}

// TransportProblem は sh がリモートを読み書きできる形になっているか。なっていなければその説明（--lint が言う）。
// sh と同じ順で探す: gh / glab があればそれ、無ければ curl とトークン。
func TransportProblem(url string) string {
	found := Classify(url)
	if found.Kind == "" {
		return fmt.Sprintf(msgNotAHost, credentials.ReplaceAllLiteralString(url, hidden))
	}
	if found.Problem != "" {
		return found.Problem
	}
	if !onPath(toolJQ) {
		return msgNoJQ
	}
	cli, token := toolGH, env.GitHubToken
	if found.Kind == GitLab {
		cli, token = toolGLab, env.GitLabToken
	}
	if onPath(cli) {
		return ""
	}
	if !onPath(toolCurl) {
		return fmt.Sprintf(msgNoTransport, cli)
	}
	if os.Getenv(token) == "" {
		return fmt.Sprintf(msgNoToken, cli, token)
	}
	return ""
}

func onPath(name string) bool {
	_, err := exec.LookPath(name)
	return err == nil
}
