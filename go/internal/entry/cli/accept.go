package cli

import (
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/host"
)

// acceptHead は `accept` の答えの頭の行。sh はこれが無ければ計画を読めなかったと読む。
const acceptHead = "accept 1"

// runAccept は `accept plan <計画>` と `accept threads <計画> <取り直した結果> <github|gitlab> <置き場> [<issue の URL>]`。受け入れの
// 計画（`--accept-plan` が書く）を、sh（ccnavi-user-lib-review.sh・ccnavi-user-decide.sh）が jq を使わずに読む形。
// 返信と解決をホストに書くのは sh。
//
//   - plan: `op <操作ID>`・`count <スレッドの数>`
//   - threads: 書く前の確かめ（host.AcceptCheck）に通れば `mr <MR の番号>` と、スレッドごとに `thread <番号> <id> <キー>`。
//     返信の本文は <置き場>/<番号>.reply に書く（本文は複数行なので答えの行に入れない）。本文の issue の URL の枠
//     （host.IssueURLSlot）は、issue の URL（issue を先に作ってから返信する）に置き換える。URL が無ければ止める。止めるなら `stop <文>`
//   - decided <置いた答え> <issue の URL> <警告>: 置いた答え（`user reviewed --yes --json` の出力）の JSON に `issue_url`・`warning`
//     を足した 1 行の JSON（ccnavi-user-decide.sh がボードに返す）。答えが空なら何も出さない
//
// 計画を読めなければ何も答えず 1 で終わる。
func runAccept(stdout, stderr io.Writer, words []string) int {
	switch {
	case len(words) == 2 && words[0] == "plan":
		op, threads, ok := host.ReadAcceptThreads(words[1])
		if !ok {
			return config.ExitError
		}
		fmt.Fprintf(stdout, "%s\nop %s\ncount %d\n", acceptHead, ospath.Squeeze(op), len(threads))
		return config.ExitOK
	case (len(words) == 5 || len(words) == 6) && words[0] == "threads":
		_, threads, ok := host.ReadAcceptThreads(words[1])
		if !ok {
			return config.ExitError
		}
		fmt.Fprintln(stdout, acceptHead)
		result := host.LoadResult(words[2])
		if why := host.AcceptCheck(host.Kind(words[3]), threads, result); why != "" {
			fmt.Fprintln(stdout, "stop "+why)
			return config.ExitOK
		}
		number := 0
		if result.MR != nil {
			number = result.MR.Number
		}
		fmt.Fprintf(stdout, "mr %d\n", number)
		if err := fsio.MakeDirs(words[4]); err != nil {
			fmt.Fprintf(stderr, "ccnavi: 返信の本文の置き場 %s を作れない（%s）\n", words[4], fsio.ErrorText(err))
			return config.ExitError
		}
		issueURL := ""
		if len(words) == 6 {
			issueURL = words[5]
		}
		for _, t := range threads {
			if strings.Contains(t.Reply, host.IssueURLSlot) && issueURL == "" {
				fmt.Fprintln(stdout, "stop 返信に書く issue の URL が無い（issue を作ってから返信する）。ホストには何も書いていない。")
				return config.ExitOK
			}
		}
		for i, t := range threads {
			name := strconv.Itoa(i)
			reply := strings.ReplaceAll(t.Reply, host.IssueURLSlot, issueURL)
			if err := os.WriteFile(ospath.Join(words[4], name+".reply"), []byte(reply), 0o600); err != nil {
				fmt.Fprintf(stderr, "ccnavi: 返信の本文を書けない（%s）\n", fsio.ErrorText(err))
				return config.ExitError
			}
			fmt.Fprintf(stdout, "thread %s %s %s\n", name, t.ID, ospath.Squeeze(t.Key))
		}
		return config.ExitOK
	case len(words) == 4 && words[0] == "decided":
		return acceptDecided(stdout, stderr, words[1], words[2], words[3])
	}
	io.WriteString(stderr, "ccnavi: accept の使い方: plan <計画> / threads <計画> <結果> <github|gitlab> <置き場> / decided <置いた答え> <issue の URL> <警告>\n")
	return config.ExitError
}

// acceptDecided は `accept decided`。置いた答えが JSON の表でなければ 1。
func acceptDecided(stdout, stderr io.Writer, path, issueURL, warning string) int {
	data, err := os.ReadFile(path)
	if err != nil {
		fmt.Fprintf(stderr, "ccnavi: 置いた答え %s を読めない（%s）\n", path, fsio.ErrorText(err))
		return config.ExitError
	}
	if strings.TrimSpace(string(data)) == "" {
		return config.ExitOK
	}
	v, err := pyjson.Loads(string(data))
	top, ok := v.(*pyjson.Object)
	if err != nil || !ok {
		fmt.Fprintf(stderr, "ccnavi: 置いた答え %s が JSON の表ではない\n", path)
		return config.ExitError
	}
	top.Set("issue_url", issueURL)
	top.Set("warning", warning)
	text, err := pyjson.Dumps(top, pyjson.Options{Compact: true, RawUnicode: true})
	if err != nil {
		fmt.Fprintf(stderr, "ccnavi: 置いた答えを書き出せない（%s）\n", err)
		return config.ExitError
	}
	fmt.Fprintln(stdout, text)
	return config.ExitOK
}
