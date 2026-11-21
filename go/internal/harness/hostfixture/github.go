package hostfixture

import (
	"fmt"
	"io"
	"os"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
)

const githubAPI = "https://api.github.com"

// threadFields は見本の応答が持つ欄。問い合わせからどれかを外せば、実際のホストは答えにその欄を入れないので、スタブも答えない
// （欄の名前を見ずに見本を返すと、問い合わせの欄を削っても試験が通ってしまう）。拡張のスタブと同じ。
var threadFields = []string{
	"reviewThreads", "pageInfo", "hasNextPage", "endCursor", "nodes", "id", "isResolved", "comments", "url", "path", "line", "body", "createdAt",
}

// missingFields は問い合わせに語として現れない欄の名前。
func missingFields(query string, fields []string) []string {
	var missing []string
	for _, f := range fields {
		if !hasWord(query, f) {
			missing = append(missing, f)
		}
	}
	return missing
}

func isWordChar(b byte) bool {
	return b == '_' || (b >= '0' && b <= '9') || (b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z')
}

// hasWord は word が、前後を英数字と _ で挟まれずに q のどこかにあるか。
func hasWord(q, word string) bool {
	for from := 0; ; {
		i := strings.Index(q[from:], word)
		if i < 0 {
			return false
		}
		start, end := from+i, from+i+len(word)
		if (start == 0 || !isWordChar(q[start-1])) && (end == len(q) || !isWordChar(q[end])) {
			return true
		}
		from = start + 1
	}
}

// github は 1 回の答えに要るもの。
type github struct {
	getenv Getenv
	f      fixtures
}

func (g github) rest() object {
	path := g.getenv("FAKE_GITHUB_REST")
	if path == "" {
		return object{}
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return object{}
	}
	return asObject(decodeJSON(raw))
}

func (g github) draft() bool {
	if path := g.getenv("FAKE_GITHUB_DRAFT_STATE"); path != "" {
		if raw, err := os.ReadFile(path); err == nil {
			b, _ := decodeJSON(raw).(bool)
			return b
		}
	}
	if v, ok := g.rest()["draft"]; ok {
		b, _ := v.(bool)
		return b
	}
	return true
}

func (g github) setDraft(value bool) {
	if path := g.getenv("FAKE_GITHUB_DRAFT_STATE"); path != "" {
		_ = os.WriteFile(path, marshal(value), 0o644)
	}
}

// note は FAKE_GITHUB_MUTATIONS に 1 行ずつ JSON で残す（形の試験が読む）。
func (g github) note(entry object) {
	if path := g.getenv("FAKE_GITHUB_MUTATIONS"); path != "" {
		appendLine(path, string(marshal(entry))+"\n")
	}
}

var variableName = regexp.MustCompile(`\$([A-Za-z_]+)`)

// mutation は受け入れの返信と解決（GraphQL の mutation）。
// 受けた問い合わせと変数を残し、問い合わせが使う変数（`$id`・`$b`）を渡していなければ、実際のホストと同じく errors で答える。
// FAKE_GITHUB_FAIL_MUTATION が reply か resolve なら、その mutation を errors で断る。
func (g github) mutation(q string, variables object) (int, any) {
	g.note(object{"query": q, "variables": variables})
	head, _, _ := strings.Cut(q, "{")
	var missing []string
	for _, m := range variableName.FindAllStringSubmatch(head, -1) {
		if _, ok := variables[m[1]]; !ok && !slices.Contains(missing, m[1]) {
			missing = append(missing, m[1])
		}
	}
	slices.Sort(missing)
	if len(missing) > 0 {
		return 200, errorsBody("変数が無い: " + strings.Join(missing, ", "))
	}
	fail := g.getenv("FAKE_GITHUB_FAIL_MUTATION")
	switch {
	case strings.Contains(q, "addPullRequestReviewThreadReply"):
		if fail == "reply" {
			return 200, errorsBody("Could not resolve to a node")
		}
		return 200, object{"data": object{"addPullRequestReviewThreadReply": object{"comment": object{"id": "PRRC_1"}}}}
	case strings.Contains(q, "markPullRequestReadyForReview"):
		g.setDraft(false)
		return 200, object{"data": object{"markPullRequestReadyForReview": object{"pullRequest": object{"number": 42, "isDraft": false}}}}
	case strings.Contains(q, "resolveReviewThread"):
		if fail == "resolve" {
			return 200, errorsBody("Could not resolve to a node")
		}
		return 200, object{"data": object{"resolveReviewThread": object{"thread": object{"isResolved": true}}}}
	}
	return 200, errorsBody("知らない mutation")
}

func errorsBody(message string) object {
	return object{"errors": []any{object{"message": message}}}
}

// graphqlRefusal は FAKE_GITHUB_GRAPHQL が GraphQL を断るときの答え（blocked・forbidden・unauthorized）。
func (g github) graphqlRefusal() (int, any, bool) {
	switch g.getenv("FAKE_GITHUB_GRAPHQL") {
	case "blocked":
		return 403, object{"message": "GitHub GraphQL is not available from Claude Code sessions"}, true
	case "forbidden":
		return 403, object{"message": "Resource not accessible by personal access token"}, true
	case "unauthorized":
		return 401, object{"message": "Bad credentials"}, true
	}
	return 0, nil, false
}

func requestBody(body string) object {
	if body == "" {
		return object{}
	}
	return asObject(decodeJSON([]byte(body)))
}

// restAnswer は REST の代わりの道の答え。コメントの一覧と返信も含む。pieces は `pulls/<番号>/...`。
func (g github) restAnswer(method string, pieces []string, query map[string]string, body, number string) (int, any, bool) {
	if len(pieces) < 2 || pieces[0] != "pulls" || pieces[1] != number {
		return 0, nil, false
	}
	rest := pieces[2:]
	restData := g.rest()
	served := g.getenv("FAKE_GITHUB_GRAPHQL") == "blocked" && g.getenv("FAKE_GITHUB_NO_CCR") == ""
	join := strings.Join(pieces, "/")
	switch {
	case method == "GET" && len(rest) == 0:
		n, _ := strconv.Atoi(number)
		prBody, _ := restData["body"].(string)
		return 200, object{"number": n, "node_id": "PR_kwDOAbCdEs5ABCD", "title": "Draft: 機能を足す", "body": prBody, "draft": g.draft()}, true
	case method == "GET" && len(rest) == 1 && rest[0] == "comments":
		if pageOr1(query["page"]) == "1" {
			return 200, listOrEmpty(restData["comments"]), true
		}
		return 200, []any{}, true
	case method == "POST" && len(rest) == 3 && rest[0] == "comments" && rest[2] == "replies":
		g.note(object{"rest": "POST " + join, "body": requestBody(body)})
		id, _ := strconv.Atoi(rest[1])
		return 201, object{"id": 9001, "in_reply_to_id": id}, true
	}
	if !served || len(rest) == 0 || rest[0] != "ccr" {
		return 0, nil, false
	}
	switch {
	case method == "GET" && len(rest) == 2 && rest[1] == "review_threads":
		return 200, listOrEmpty(restData["review_threads"]), true
	case method == "POST" && len(rest) == 4 && rest[1] == "comments" && rest[3] == "resolve":
		g.note(object{"rest": "POST " + join, "body": requestBody(body)})
		return 200, object{}, true
	case method == "POST" && len(rest) == 2 && rest[1] == "ready_for_review":
		g.note(object{"rest": "POST " + join, "body": requestBody(body)})
		g.setDraft(false)
		return 200, object{}, true
	}
	return 0, nil, false
}

func listOrEmpty(v any) any {
	if l, ok := v.([]any); ok {
		return l
	}
	return []any{}
}

// GithubAnswer は GitHub の見本が 1 つの要求に返す答え（状態, JSON）。rel は API の根からのパスとクエリ。root は見本のあるリポジトリの根。
// 拡張の試験（`test/helpers/host-fixture.ts`）と同じ規則で返す。
//
//   - GET /user → user.json（無ければ 403）
//   - GET /repos/<o>/<r>/pulls?head=<o>:<branch> → branch が scene.json のものなら pulls.json、違えば []
//   - GET /repos/<o>/<r>/pulls/<番号>/reviews?page=<N> → 番号が pulls.json のものなら reviews.<N>.json（無ければ []。N の既定は 1）
//   - POST /graphql の reviewThreads → 番号が同じで、変数の cursor（sh は c、拡張は after）が null なら threads.1.json、
//     threads.<k>.json の endCursor と同じなら threads.<k+1>.json
//   - POST /graphql の viewer → user.json の login
//   - GraphQL は、見本の応答が持つ欄（threadFields・login）が問い合わせに語として全部あるときだけ答える
//   - 依頼の投稿（GET・POST /repos/<o>/<r>/issues/<番号>/comments）は、状態のファイル（state）に溜めて返す
//   - FAKE_GITHUB_GRAPHQL が blocked なら、POST /graphql はすべて 403（Claude Code のクラウドと同じ本文。gh は本文を返し、curl では番号だけが
//     残る）。forbidden なら別の理由の 403、unauthorized なら 401。blocked のときだけ、REST の代わりの道（.../pulls/<番号>/ccr/...）が
//     答える。通常の GitHub では 404（FAKE_GITHUB_NO_CCR を付けると、blocked でも 404）
//   - REST の見本は、環境変数 FAKE_GITHUB_REST が指すファイルに置く（review_threads・comments の配列と、body・draft）。
//     返信・解決・Draft 外しの書き込みは、FAKE_GITHUB_MUTATIONS に {"rest": "<M> <path>", "body": ...} で残す
//   - ほかは 404
func GithubAnswer(getenv Getenv, root, scene, method, rel, body, state string) (int, any) {
	g := github{getenv: getenv, f: fixtures{dir: scenesDir(root, GitHub) + string(os.PathSeparator) + scene}}
	meta := asObject(g.f.decode("scene.json"))
	owner, repo := str(meta["owner"]), str(meta["repo"])
	path, query := splitURL(rel)
	base := "/repos/" + owner + "/" + repo
	if method == "GET" && path == "/user" {
		if raw, ok := g.f.load("user.json"); ok {
			return 200, raw
		}
		// 持ち主の無いトークン（GitHub Actions の GITHUB_TOKEN など）は 403
		return 403, object{"message": "Resource not accessible"}
	}
	if method == "GET" && path == base+"/pulls" {
		if query["head"] == owner+":"+str(meta["branch"]) {
			if raw, ok := g.f.load("pulls.json"); ok {
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
	number := ""
	if pulls := asList(g.f.decode("pulls.json")); len(pulls) > 0 {
		number = str(asObject(pulls[0])["number"])
	}
	if status, data, ok := g.restAnswer(method, pieces, query, body, number); ok {
		return status, data
	}
	if method == "PATCH" && len(pieces) > 0 && pieces[0] == "pulls" {
		n, _ := strconv.Atoi(number)
		return 200, object{"number": n}
	}
	if method == "GET" && len(pieces) == 3 && pieces[0] == "pulls" && pieces[2] == "reviews" {
		if pieces[1] != number {
			return 200, []any{}
		}
		return 200, page(g.f, "reviews", query["page"])
	}
	if len(pieces) == 3 && pieces[0] == "issues" && pieces[2] == "comments" {
		posted := readState(state)
		if method == "GET" {
			if pageOr1(query["page"]) == "1" {
				return 200, orEmpty(posted)
			}
			return 200, []any{}
		}
		if method == "POST" {
			n := len(posted) + 1
			note := object{
				"id": n, "body": str(requestBody(body)["body"]),
				"html_url":   fmt.Sprintf("https://github.com/%s/%s/pull/%s#issuecomment-%d", owner, repo, pieces[1], n),
				"created_at": "2026-09-29T00:00:00Z",
			}
			if err := writeState(state, append(posted, note)); err != nil {
				return 500, object{"message": err.Error()}
			}
			return 201, note
		}
	}
	if method == "POST" && path == "/graphql" {
		if status, data, ok := g.graphqlRefusal(); ok {
			return status, data
		}
		req := requestBody(body)
		q := str(req["query"])
		variables := asObject(req["variables"])
		if variables == nil {
			variables = object{}
		}
		if strings.HasPrefix(strings.TrimLeft(q, " \t\r\n"), "mutation") {
			return g.mutation(q, variables)
		}
		if strings.Contains(q, "reviewThreads") && number != "" && variableNumber(variables) == number {
			return g.threads(q, variables, scene)
		}
		if strings.Contains(q, "viewer") && len(missingFields(q, []string{"login"})) == 0 {
			user := asObject(g.f.decode("user.json"))
			return 200, object{"data": object{"viewer": object{"login": user["login"]}}}
		}
	}
	return 404, object{"message": "Not Found"}
}

// variableNumber は変数の PR の番号（sh は n、拡張は number）。
func variableNumber(variables object) string {
	if v, ok := variables["n"]; ok {
		return str(v)
	}
	return str(variables["number"])
}

// threads は reviewThreads の問い合わせへの答え。cursor が null なら 1 ページ目、endCursor と同じなら次のページ。
func (g github) threads(q string, variables object, scene string) (int, any) {
	if missing := missingFields(q, threadFields); len(missing) > 0 {
		return 200, errorsBody("問い合わせに欄が無い: " + strings.Join(missing, ", "))
	}
	cursor, ok := variables["c"]
	if !ok {
		cursor = variables["after"]
	}
	for k := 1; ; k++ {
		raw, ok := g.f.load("threads." + strconv.Itoa(k) + ".json")
		if !ok {
			return 200, errorsBody(fmt.Sprintf("見本に cursor %s のページが無い", cursorText(cursor)))
		}
		if cursor == nil {
			return 200, raw
		}
		info := asObject(asObject(asObject(asObject(asObject(decodeJSON(raw))["data"])["repository"])["pullRequest"])["reviewThreads"])
		if end := asObject(info["pageInfo"])["endCursor"]; end == cursor || str(end) == str(cursor) {
			cursor = nil
		}
	}
}

func cursorText(c any) string {
	if c == nil {
		return "None"
	}
	return str(c)
}

// GithubCurl は teststub の github-host curl。`ccnavi-review.sh` が打つ形を受ける。
// 環境変数: FAKE_GITHUB_SCENE（必須）・FAKE_GITHUB_STATE・FAKE_GITHUB_LOG・FAKE_GITHUB_NO_USER ほか（GithubAnswer の説明）。
func GithubCurl(args []string, stdin io.Reader, stdout, stderr io.Writer, getenv Getenv) int {
	method, target, body := parseCurl(args, stdin, false)
	if !strings.HasPrefix(target, githubAPI) {
		fmt.Fprintf(stderr, "curl: 見本の外の URL: %s\n", target)
		return 6
	}
	rel := target[len(githubAPI):]
	if getenv("FAKE_GITHUB_NO_USER") != "" && rel == "/user" {
		fmt.Fprintln(stderr, "curl: (22) The requested URL returned error: 403")
		return 22
	}
	status, data := GithubAnswer(getenv, getenv(FixtureRootVar), getenv("FAKE_GITHUB_SCENE"), method, rel, body, getenv("FAKE_GITHUB_STATE"))
	appendLine(getenv("FAKE_GITHUB_LOG"), method+" "+rel+"\n")
	if status >= 400 {
		fmt.Fprintf(stderr, "curl: (22) The requested URL returned error: %d\n", status)
		return 22
	}
	_, _ = stdout.Write(marshal(data))
	return 0
}

// GithubGh は teststub の github-host gh（`gh api --hostname <h> [--method <M>] [--input -] <rel>`）。見本を返し、標準エラーに雑音を出す。
// 標準エラーの雑音は、`ccnavi-review.sh` が gh の応答に標準エラーを混ぜないことを見るため。
// FAKE_GH_SLOW_USER に秒を入れると、`user` の答えをその秒だけ遅らせる（時間の上限の試験）。
func GithubGh(args []string, stdin io.Reader, stdout, stderr io.Writer, getenv Getenv) int {
	if len(args) == 0 || args[0] != "api" {
		return 1
	}
	method, rel, body := "GET", "", ""
	for i := 1; i < len(args); {
		switch a := args[i]; a {
		case "--hostname", "--method", "--input":
			if a == "--method" && i+1 < len(args) {
				method = args[i+1]
			}
			if a == "--input" {
				raw, _ := io.ReadAll(stdin)
				body = string(raw)
			}
			i += 2
		default:
			rel = a
			i++
		}
	}
	fmt.Fprintln(stderr, "gh: 更新があります（雑音）")
	path := rel
	if !strings.HasPrefix(path, "/") {
		path = "/" + path
	}
	if secs := getenv("FAKE_GH_SLOW_USER"); path == "/user" && secs != "" {
		if s, err := strconv.ParseFloat(secs, 64); err == nil {
			time.Sleep(time.Duration(s * float64(time.Second)))
		}
	}
	root, scene := getenv(FixtureRootVar), getenv("FAKE_GITHUB_SCENE")
	meta := asObject(fixtures{dir: scenesDir(root, GitHub) + string(os.PathSeparator) + scene}.decode("scene.json"))
	if path == "/repos/"+str(meta["owner"])+"/"+str(meta["repo"]) {
		_, _ = io.WriteString(stdout, "{}")
		return 0
	}
	status, data := GithubAnswer(getenv, root, scene, method, path, body, getenv("FAKE_GITHUB_STATE"))
	appendLine(getenv("FAKE_GITHUB_LOG"), method+" "+path+"\n")
	_, _ = stdout.Write(marshal(data))
	if status >= 400 {
		// 本物の gh は 4xx でも本文を標準出力へ書き、標準エラーには「gh: <message> (HTTP N)」を出す
		message := ""
		if m, ok := data.(object); ok {
			message = str(m["message"])
		}
		fmt.Fprintf(stderr, "gh: %s (HTTP %d)\n", message, status)
		return 1
	}
	return 0
}
