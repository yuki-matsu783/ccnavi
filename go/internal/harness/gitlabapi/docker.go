package gitlabapi

import (
	"bufio"
	"os"
	"strings"

	"ccnavi/internal/infra/env"
)

// Docker の GitLab のトークンのファイル（tokens.env）のキー。
const (
	TokenKey         = env.GitLabToken
	ReviewerTokenKey = env.ProbeReviewerToken
	ReviewerIDKey    = env.ProbeReviewerID
)

// ReadEnvFile は `KEY=VALUE` の行のファイルを読む。無ければ空。
func ReadEnvFile(path string) map[string]string {
	values := map[string]string{}
	f, err := os.Open(path)
	if err != nil {
		return values
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		if key, value, ok := strings.Cut(sc.Text(), "="); ok {
			values[key] = value
		}
	}
	return values
}
