package cli

import (
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/tickets/model/hostreply"
)

// runHost は `ccnavi host <問い> …`（ccnavi-common-host.sh・ccnavi-common-review.sh・ccnavi-review.sh 用）。sh がホストから
// 取ってきた応答（失敗したときはホストの返事）をファイルで受け、読んだ結果を 1 行 1 項目で答える。
//
//   - fail <返事>: `status <HTTP の番号>`（取れたとき）・`blocked yes|no`（GraphQL を使えない環境の 403 か）・`body <本文>`
//   - probe ok|failed <応答か返事>: プロジェクトを 1 回読んだ結果（`probe ok|auth|notfound|network|badjson`）
//   - mr-state <github|gitlab> <scheme> <ホスト> <パス> <番号> ok|failed <応答か返事>: `state <状態>` と `answer <2 行目>`
//   - find-mr <github|gitlab> <プロジェクトの id か -> <一覧>: 親のブランチの開いた MR（`number`・`url`・`source`・`target`・`draft`）
//   - merged <github|gitlab> <番号か -> <プロジェクトの id か -> <応答>: `merged <番号> <sha> <元ブランチ>` か `none`
//   - copy <github|gitlab> <番号> <URL> <Draft: true|false|-> <graphql|rest|discussions> <レビュー> <スレッド>...:
//     取ってきた時点の状態の JSON（`--result` に渡すもの）。REST の道の応答が配列でなければ終了コード 3
//   - marked <github|gitlab> <目印> <アカウント> <URL> <一覧>: 目印を含む投稿の `urls <URL,URL,…>`
//   - undraft title <github|gitlab> <応答>: Draft を外すときのタイトル（`node`・`title`・`new`）
//   - undraft done <graphql|rest|gitlab> <応答>: まだ Draft か（`draft true|false`）
//
// 読めない応答は終了コード 1（sh は API の失敗と同じに扱う）。
func runHost(stdout, stderr io.Writer, args []string) int {
	usage := func() int {
		fmt.Fprintf(stderr, "ccnavi: host の引数が読めない（%s）\n", pyfmt.Repr(args))
		return config.ExitError
	}
	unreadable := func(err error) int {
		fmt.Fprintf(stderr, "ccnavi: ホストの応答を読めない（%s）\n", err)
		return config.ExitError
	}
	file := func(path string) []byte {
		data, _ := os.ReadFile(path)
		return data
	}
	dash := func(s string) string {
		if s == "-" {
			return ""
		}
		return s
	}
	n := len(args)
	if n == 0 {
		return usage()
	}
	switch op := args[0]; {
	case op == "fail" && n == 2:
		text := string(file(args[1]))
		if status := hostreply.FailStatus(text); status != "" {
			fmt.Fprintf(stdout, "status %s\n", status)
		}
		fmt.Fprintf(stdout, "blocked %s\n", yesNo(hostreply.GraphQLBlocked(text)))
		fmt.Fprintf(stdout, "body %s\n", hostreply.FailBody(text, []string{os.Getenv(env.GitHubToken), os.Getenv(env.GitLabToken)}))
	case op == "probe" && n == 3 && (args[1] == "ok" || args[1] == "failed"):
		fmt.Fprintf(stdout, "probe %s\n", hostreply.Probe(args[1] == "failed", file(args[2])))
	case op == "mr-state" && n == 8 && (args[6] == "ok" || args[6] == "failed"):
		state, answer := hostreply.MRState(args[1], args[2], args[3], args[4], args[5], args[6] == "failed", file(args[7]))
		fmt.Fprintf(stdout, "state %s\nanswer %s\n", state, answer)
	case op == "find-mr" && n == 4:
		got, err := hostreply.FindMR(args[1], dash(args[2]), file(args[3]))
		if err != nil {
			return unreadable(err)
		}
		fmt.Fprint(stdout, got)
	case op == "merged" && n == 5:
		got, err := hostreply.Merged(args[1], dash(args[2]), dash(args[3]), file(args[4]))
		if err != nil {
			return unreadable(err)
		}
		fmt.Fprint(stdout, got)
	case op == "copy" && n >= 8:
		return hostCopy(stdout, stderr, args[1:], file)
	case op == "marked" && n == 6:
		got, err := hostreply.Marked(args[1], args[2], args[3], args[4], file(args[5]))
		if err != nil {
			return unreadable(err)
		}
		fmt.Fprintf(stdout, "urls %s\n", got)
	case op == "undraft" && n == 4 && args[1] == "title":
		fmt.Fprint(stdout, hostreply.UndraftTitle(args[2], file(args[3])))
	case op == "undraft" && n == 4 && args[1] == "done":
		fmt.Fprintf(stdout, "draft %s\n", hostreply.UndraftDone(args[2], file(args[3])))
	default:
		return usage()
	}
	return config.ExitOK
}

// hostCopy は `host copy`。args は copy の後ろ（<種類> <番号> <URL> <Draft> <形> <レビュー> <スレッド>...）。
func hostCopy(stdout, stderr io.Writer, args []string, file func(string) []byte) int {
	kind, number, url, draftArg, form := args[0], args[1], args[2], args[3], hostreply.ThreadsForm(args[4])
	if !numberText(number) {
		fmt.Fprintf(stderr, "ccnavi: host copy の MR の番号 %s は数ではない\n", pyfmt.Repr(number))
		return config.ExitError
	}
	var draft *bool
	switch draftArg {
	case "true", "false":
		d := draftArg == "true"
		draft = &d
	case "-":
	default:
		fmt.Fprintf(stderr, "ccnavi: host copy の Draft %s は true・false・- のどれか\n", pyfmt.Repr(draftArg))
		return config.ExitError
	}
	var threads [][]byte
	for _, path := range args[6:] {
		threads = append(threads, file(path))
	}
	when := time.Now().UTC().Format("2006-01-02T15:04:05Z")
	got, err := hostreply.NewCopy(kind, number, url, draft, form, threads, file(args[5]), when)
	if errors.Is(err, hostreply.ErrNotArrays) {
		return exitNotArrays
	}
	if err != nil {
		fmt.Fprintf(stderr, "ccnavi: 取ってきた MR のスレッドとレビューを読めない（%s）\n", err)
		return config.ExitError
	}
	fmt.Fprintln(stdout, got.JSON())
	return config.ExitOK
}

// numberText は 0 で始まらない数字だけの綴りか。
func numberText(s string) bool {
	return s != "" && s[0] != '0' && strings.Trim(s, "0123456789") == ""
}

// exitNotArrays は、REST の道の応答が配列でないときの終了コード（sh は REST の代わりの道も失敗したと言う）。
const exitNotArrays = 3
