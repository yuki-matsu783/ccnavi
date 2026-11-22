package hostfixture

import (
	"fmt"
	"io"
	"net/url"
	"os"
	"strconv"
	"strings"
)

const gitlabAPI = "https://gitlab.com/api/v4"

// GitlabAnswer は GitLab の見本が 1 つの要求に返す答え（状態, JSON）。rel は API の根からのパスとクエリ（%2F のまま）。root は見本のあるリポジトリの根。
// 拡張の試験（`test/helpers/gitlab-fixture.ts`）と同じ規則で返す。
//
//   - GET /user → user.json（無ければ 403）
//   - GET /projects/<namespace と project を符号化> → {"id": 42, "default_branch": "main"}（MR が同じプロジェクトから出たかを見る）
//   - GET .../merge_requests?state=opened&source_branch=<b> → b が scene.json のものなら mrs.json（フォークの MR を含みうる。呼び手が
//     source_project_id で絞る）、違えば []
//   - GET .../merge_requests/<iid>/discussions?page=<N> → iid が mrs.json のどれかなら discussions.<N>.json（無ければ []。N の既定は 1）
//   - GET .../merge_requests/<iid>/reviewers?page=<N> → 同じく reviewers.<N>.json
//   - 依頼の投稿（GET・POST .../merge_requests/<iid>/notes）は、状態のファイル（state）に溜めて返す。投稿したアカウントは id 201
//     （名前は環境変数 FAKE_GITLAB_POSTER、既定 lab-bot）。見本に置かない（依頼の記録の poster を試すため。ccnavi の依頼のスレッドを
//     未解決から除くのは、依頼を投稿したアカウントが書いたときだけ）
//   - ほかは 404
func GitlabAnswer(getenv Getenv, root, scene, method, rel, body, state string) (int, any) {
	f := fixtures{dir: scenesDir(root, GitLab) + string(os.PathSeparator) + scene}
	meta := asObject(f.decode("scene.json"))
	path, query := splitURL(rel)
	if method == "GET" && path == "/user" {
		if raw, ok := f.load("user.json"); ok {
			return 200, raw
		}
		return 403, object{"message": "403 Forbidden"}
	}
	// 入れ子のグループ（`acme/team`）も、名前空間とプロジェクトをまとめて符号化する（`acme%2Fteam%2Fwidgets`）
	base := "/projects/" + url.PathEscape(str(meta["namespace"])+"/"+str(meta["project"]))
	if method == "GET" && path == base {
		return 200, object{"id": 42, "default_branch": "main"}
	}
	if method == "GET" && path == base+"/merge_requests" {
		if query["state"] == "opened" && query["source_branch"] == str(meta["branch"]) {
			if raw, ok := f.load("mrs.json"); ok {
				return 200, raw
			}
			return 200, nil
		}
		return 200, []any{}
	}
	var pieces []string
	if rest, ok := strings.CutPrefix(path, base+"/"); ok {
		pieces = strings.Split(rest, "/")
	}
	iids := map[string]bool{}
	for _, m := range asList(f.decode("mrs.json")) {
		iids[str(asObject(m)["iid"])] = true
	}
	if method == "GET" && len(pieces) == 3 && pieces[0] == "merge_requests" && (pieces[2] == "discussions" || pieces[2] == "reviewers") {
		if !iids[pieces[1]] {
			return 200, []any{}
		}
		return 200, page(f, pieces[2], query["page"])
	}
	if len(pieces) == 3 && pieces[0] == "merge_requests" && pieces[2] == "notes" {
		posted := readState(state)
		if method == "GET" {
			if pageOr1(query["page"]) == "1" {
				return 200, orEmpty(posted)
			}
			return 200, []any{}
		}
		if method == "POST" && iids[pieces[1]] {
			poster := getenv("FAKE_GITLAB_POSTER")
			if poster == "" {
				poster = "lab-bot"
			}
			note := object{
				"id": 7000 + len(posted) + 1, "body": str(postedBody(body)["body"]), "created_at": "2026-09-29T00:00:00.000Z",
				"author": object{"id": 201, "username": poster},
			}
			if err := writeState(state, append(posted, note)); err != nil {
				return 500, object{"message": err.Error()}
			}
			return 201, note
		}
	}
	return 404, object{"message": "404 Not Found"}
}

// page は `<name>.<N>.json`（N が数字でない・ファイルが無ければ []）。
func page(f fixtures, name, n string) any {
	n = pageOr1(n)
	if _, err := strconv.Atoi(n); err == nil {
		if raw, ok := f.load(name + "." + n + ".json"); ok {
			return raw
		}
	}
	return []any{}
}

func pageOr1(n string) string {
	if n == "" {
		return "1"
	}
	return n
}

func orEmpty(l []any) []any {
	if l == nil {
		return []any{}
	}
	return l
}

func postedBody(body string) object {
	if body == "" {
		return object{}
	}
	return asObject(decodeJSON([]byte(body)))
}

// GitlabCurl は teststub の gitlab-host curl。`ccnavi-review.sh` が打つ形（`-fsS -X <M> -H ... [--data-binary @-] <URL>`）を受ける。
// 見本の外の URL は 6 で、状態が 400 以上なら curl の書き方で言って 22 で終わる。
//
// 環境変数: FAKE_GITLAB_SCENE（場面。必須）・FAKE_GITLAB_STATE（投稿の溜め先）・FAKE_GITLAB_LOG（要求の記録）・FAKE_GITLAB_NO_USER・
// FAKE_GITLAB_NO_PROJECT（プロジェクトそのもの `GET /projects/<パス>` だけを 404 にする）。
func GitlabCurl(args []string, stdin io.Reader, stdout, stderr io.Writer, getenv Getenv) int {
	if requireFixtureEnv(GitLab, stderr, getenv) {
		return 1
	}
	method, target, body := parseCurl(args, stdin)
	if !strings.HasPrefix(target, gitlabAPI) {
		fmt.Fprintf(stderr, "curl: 見本の外の URL: %s\n", target)
		return 6
	}
	rel := target[len(gitlabAPI):]
	if getenv("FAKE_GITLAB_NO_USER") != "" && rel == "/user" {
		fmt.Fprintln(stderr, "curl: (22) The requested URL returned error: 403")
		return 22
	}
	if getenv("FAKE_GITLAB_NO_PROJECT") != "" && !strings.Contains(strings.TrimPrefix(rel, "/projects/"), "/") {
		// プロジェクトそのもの（`GET /projects/<符号化したパス>`）だけを読めなくする
		fmt.Fprintln(stderr, "curl: (22) The requested URL returned error: 404")
		return 22
	}
	status, data := GitlabAnswer(getenv, getenv(FixtureRootVar), getenv("FAKE_GITLAB_SCENE"), method, rel, body, getenv("FAKE_GITLAB_STATE"))
	appendLine(getenv("FAKE_GITLAB_LOG"), method+" "+rel+"\n")
	if status >= 400 {
		fmt.Fprintf(stderr, "curl: (22) The requested URL returned error: %d\n", status)
		return 22
	}
	_, _ = stdout.Write(marshal(data))
	return 0
}

// parseCurl は curl の引数から、メソッド・URL・本文（--data-binary があれば標準入力）を取る。
func parseCurl(args []string, stdin io.Reader) (method, target, body string) {
	method = "GET"
	for i := 0; i < len(args); {
		a := args[i]
		switch {
		case a == "-X":
			if i+1 < len(args) {
				method = args[i+1]
			}
			i += 2
		case a == "-H" || a == "--header" || a == "--connect-timeout" || a == "--max-time":
			i += 2
		case a == "--data-binary":
			raw, _ := io.ReadAll(stdin)
			body = string(raw)
			i += 2
		default:
			if strings.HasPrefix(a, "http") {
				target = a
			}
			i++
		}
	}
	return method, target, body
}
