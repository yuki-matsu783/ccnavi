package fakegitlab

import (
	"net/url"
	"regexp"
)

const (
	projPattern  = `^/api/v4/projects/(?P<proj>[^/]+)`
	mrPattern    = projPattern + `/merge_requests/(?P<iid>\d+)`
	issuePattern = projPattern + `/issues/(?P<iid>\d+)`
)

// closing は本文の `Closes #N`（Closes/Fixes/Resolves の活用形。`:` は任意）。実物の閉じる語に合わせる。
var closing = regexp.MustCompile(`(?i)\b(?:clos(?:e[sd]?|ing)|fix(?:e[sd]|ing)?|resolv(?:e[sd]?|ing))\b:?\s+#(\d+)`)

// routes は経路の名前と形。上から順に当てる。
var routes = []struct {
	name    string
	pattern *regexp.Regexp
}{
	{"project", regexp.MustCompile(projPattern + `$`)},
	{"mrs", regexp.MustCompile(projPattern + `/merge_requests$`)},
	{"mr", regexp.MustCompile(mrPattern + `$`)},
	{"discussions", regexp.MustCompile(mrPattern + `/discussions$`)},
	{"discussion", regexp.MustCompile(mrPattern + `/discussions/(?P<did>[^/]+)$`)},
	{"approvals", regexp.MustCompile(mrPattern + `/approvals$`)},
	{"approve", regexp.MustCompile(mrPattern + `/approve$`)},
	{"reviewers", regexp.MustCompile(mrPattern + `/reviewers$`)},
	{"reviewer", regexp.MustCompile(mrPattern + `/reviewers/(?P<uid>\d+)$`)},
	{"notes", regexp.MustCompile(mrPattern + `/notes$`)},
	{"merge", regexp.MustCompile(mrPattern + `/merge$`)},
	{"issues", regexp.MustCompile(projPattern + `/issues$`)},
	{"issue", regexp.MustCompile(issuePattern + `$`)},
	{"issue_mrs", regexp.MustCompile(issuePattern + `/related_merge_requests$`)},
	{"issue_notes", regexp.MustCompile(issuePattern + `/notes$`)},
	{"closes_issues", regexp.MustCompile(mrPattern + `/closes_issues$`)},
	{"discussion_notes", regexp.MustCompile(mrPattern + `/discussions/(?P<did>[^/]+)/notes$`)},
	{"member", regexp.MustCompile(projPattern + `/members/all/(?P<uid>\d+)$`)},
	{"user", regexp.MustCompile(`^/api/v4/user$`)},
	{"version", regexp.MustCompile(`^/api/v4/version$`)},
}

// route は経路の名前と、パスの中の名前付きの部分。当たらなければ名前は空。path は %2F を符号化したままのパス。
func route(path string, query url.Values) (string, map[string]string, url.Values) {
	for _, r := range routes {
		m := r.pattern.FindStringSubmatch(path)
		if m == nil {
			continue
		}
		args := map[string]string{}
		for i, n := range r.pattern.SubexpNames() {
			if n != "" {
				args[n] = m[i]
			}
		}
		return r.name, args, query
	}
	return "", nil, query
}
