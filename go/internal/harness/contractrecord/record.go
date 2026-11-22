package contractrecord

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	cs "ccnavi/internal/harness/contractshape"
	"ccnavi/internal/harness/gitlabapi"
	"ccnavi/internal/infra/env"
)

// ProjectPrefix は録るために作るプロジェクト名の前置き（禁止文字列の p551- と揃える）。
const ProjectPrefix = "p551"

// API は録るのに使う GitLab の入口（gitlabapi.Client が満たす）。
type API interface {
	Call(method, path, token string, payload any, tag string) (int, any)
	Version(token string) (int, any)
	CreateProject(token, path string) (int, any)
	DeleteProject(token string, id any) (int, any)
	AddMember(token string, id any, userID, level int) (int, any)
	FirstCommit(token string, id any, branch string) (int, any)
	CommitFile(token string, id any, branch, filePath, content, startBranch string) (int, any)
	CreateIssue(token string, id any, title, description string) (int, any)
	CreateIssueNote(token string, id any, iid int, body string) (int, any)
	CreateMRDescribed(token string, id any, source, target, title, description string) (int, any)
	WaitMRChecked(token string, id any, iid int, attempts int) (int, any)
	Approve(token string, id any, iid any) (int, any)
	MergeMR(token string, id any, iid int, attempts int) (int, any)
	SetReviewers(token string, id any, iid int, reviewers []int) (int, any)
	CreateMRNote(token string, id any, iid int, body string) (int, any)
	CreateDiscussion(token string, id any, iid any, body, tag string) (int, any)
}

// Scene は場面を組んだ結果。Context は要求の `${名前}` の置き換え先、Delete は作ったものを消す。
type Scene struct {
	Context map[string]string
	Delete  func()
}

// Config は録る 1 回の入力。
type Config struct {
	// Root はリポジトリの根。API は本物の GitLab の入口。
	Root string
	API  API
	// Token・ReviewerToken・ReviewerID は tokens.env と同名の環境変数から決めた値。
	Token, ReviewerToken string
	ReviewerID           int
	// Image は版の正（gitlabdocker.Image）。RunningImage は動いているコンテナのイメージ。
	Image        string
	RunningImage func() string
	// Keep なら作ったプロジェクトを消さない。
	Keep bool
	// Requests は送る要求（nil なら requests.json の全要求）。BuildScene は場面を組む（nil なら本物の GitLab に組む BuildScene）。
	Requests   []cs.Request
	BuildScene func(c Config, name string) (*Scene, error)
	// Now は記録の日付（nil なら time.Now）。Sleep は待ち（nil なら time.Sleep）。
	Now            func() time.Time
	Sleep          func(time.Duration)
	Stdout, Stderr io.Writer
	DryRun         bool
}

func (c Config) sleep(d time.Duration) {
	if c.Sleep != nil {
		c.Sleep(d)
		return
	}
	time.Sleep(d)
}

func need(label string, status int, body any, ok ...int) (any, error) {
	if len(ok) == 0 {
		ok = []int{200, 201}
	}
	if !slices.Contains(ok, status) {
		return nil, fmt.Errorf("場面の組み立てで失敗: %s → HTTP %d", label, status)
	}
	return body, nil
}

func str(v any) string { return gitlabapi.Text(v) }

// BuildScene は本物の GitLab に場面を組む。contractshape.SceneState と同じ場面に、拡張だけの要求（tree・compare・commits）のための
// 2 つ目のコミットを足す。
func BuildScene(c Config, name string) (*Scene, error) {
	api, t := c.API, c.Token
	scene := &Scene{Context: map[string]string{}}
	var projectID int
	scene.Delete = func() {
		if projectID == 0 || c.Keep {
			return
		}
		if status, _ := api.DeleteProject(t, projectID); status < 200 || status >= 300 {
			fmt.Fprintf(c.Stderr, "プロジェクト %d が消せなかった（HTTP %d）\n", projectID, status)
		}
	}
	fail := func(err error) (*Scene, error) { scene.Delete(); return nil, err }
	get := func(label string, status int, body any, ok ...int) (map[string]any, error) {
		b, err := need(label, status, body, ok...)
		return gitlabapi.AsMap(b), err
	}

	status, body := api.CreateProject(t, name)
	project, err := get("プロジェクト作成", status, body)
	if err != nil {
		return fail(err)
	}
	projectID, _ = gitlabapi.Int(project["id"])
	scene.Context["project"] = url.PathEscape(str(project["path_with_namespace"]))
	status, body = api.AddMember(t, projectID, c.ReviewerID, 40)
	if _, err := get("レビュアーを member に", status, body); err != nil {
		return fail(err)
	}
	sha := map[string]string{}
	status, body = api.FirstCommit(t, projectID, "main")
	first, err := get("最初のコミット", status, body)
	if err != nil {
		return fail(err)
	}
	sha["main"] = str(first["id"])
	for _, branch := range []string{"feature", "done", "fresh"} {
		status, body = api.CommitFile(t, projectID, branch, branch+".txt", branch, "main")
		commit, err := get("ブランチ "+branch, status, body)
		if err != nil {
			return fail(err)
		}
		sha[branch] = str(commit["id"])
	}
	// 拡張だけの要求（tree・compare・commits）のための 2 つ目のコミット（ディレクトリと履歴）
	status, body = api.CommitFile(t, projectID, "feature", "docs/note.txt", "note\n", "")
	second, err := get("feature の 2 つ目のコミット", status, body)
	if err != nil {
		return fail(err)
	}
	sha["feature"] = str(second["id"])
	status, body = api.Call("GET", fmt.Sprintf("/projects/%d/repository/tree?ref=%s", projectID, sha["feature"]), t, nil, "")
	treeBody, err := need("feature の tree", status, body)
	if err != nil {
		return fail(err)
	}
	blob := ""
	for _, e := range gitlabapi.AsList(treeBody) {
		if m := gitlabapi.AsMap(e); str(m["name"]) == "feature.txt" {
			blob = str(m["id"])
		}
	}
	status, body = api.CreateIssue(t, projectID, "scene issue", "scene")
	issue, err := get("issue", status, body)
	if err != nil {
		return fail(err)
	}
	issueIID, _ := gitlabapi.Int(issue["iid"])
	status, body = api.CreateIssueNote(t, projectID, issueIID, "scene note")
	if _, err := get("issue のメモ", status, body); err != nil {
		return fail(err)
	}
	status, body = api.CreateMRDescribed(t, projectID, "feature", "main", "scene mr", fmt.Sprintf("Closes #%d", issueIID))
	mr, err := get("open の MR", status, body)
	if err != nil {
		return fail(err)
	}
	mrIID, _ := gitlabapi.Int(mr["iid"])
	status, body = api.CreateMRDescribed(t, projectID, "done", "main", "scene merged", "")
	done, err := get("merged にする MR", status, body)
	if err != nil {
		return fail(err)
	}
	doneIID, _ := gitlabapi.Int(done["iid"])
	status, body = api.WaitMRChecked(t, projectID, doneIID, 24)
	if _, err := get("MR の判定待ち", status, body); err != nil {
		return fail(err)
	}
	// 承認は merged の MR にだけ付ける（open の MR に付けると、承認の system メモが MR のメモ・スレッド・レビュアーの応答に混ざる）。
	// 作者は自分の MR を承認できないので、レビュアーで付ける
	status, body = api.Approve(c.ReviewerToken, projectID, doneIID)
	if _, err := get("MR の承認", status, body); err != nil {
		return fail(err)
	}
	status, body = api.MergeMR(t, projectID, doneIID, 12)
	merged, err := get("MR のマージ", status, body)
	if err != nil {
		return fail(err)
	}
	if str(merged["state"]) != "merged" {
		return fail(errors.New("MR がマージされなかった（merged にならないまま待ちを終えた）"))
	}
	// 直前のマージの直後は 5xx を返すことがあるので、同じ操作（冪等）を数回やり直す
	for attempt := 0; ; attempt++ {
		status, _ = api.SetReviewers(t, projectID, mrIID, []int{c.ReviewerID})
		if status == 200 || attempt == 3 {
			break
		}
		c.sleep(5 * time.Second)
	}
	if status != 200 {
		return fail(fmt.Errorf("場面の組み立てで失敗: レビュアー → HTTP %d", status))
	}
	for _, text := range []string{"one", "two"} {
		status, body = api.CreateMRNote(t, projectID, mrIID, text)
		if _, err := get("MR のメモ", status, body); err != nil {
			return fail(err)
		}
	}
	status, body = api.CreateDiscussion(t, projectID, mrIID, "thread", "")
	thread, err := get("スレッド", status, body)
	if err != nil {
		return fail(err)
	}
	// マージのあとで open の MR の判定がやり直されるので、確定してから録る
	status, body = api.WaitMRChecked(t, projectID, mrIID, 24)
	now := gitlabapi.AsMap(body)
	if status != 200 || slices.Contains([]string{"checking", "unchecked", ""}, str(now["merge_status"])) || now["diff_refs"] == nil {
		return fail(errors.New("open の MR の merge_status / diff_refs が確定しなかった"))
	}
	// MR から issue への参照は非同期に作られる。related_merge_requests に出るまで待つ
	related := false
	for i := 0; i < 24 && !related; i++ {
		status, body = api.Call("GET", fmt.Sprintf("/projects/%d/issues/%d/related_merge_requests", projectID, issueIID), t, nil, "")
		if related = status == 200 && len(gitlabapi.AsList(body)) > 0; !related {
			c.sleep(5 * time.Second)
		}
	}
	if !related {
		return fail(errors.New("issue の related_merge_requests が空のまま"))
	}
	for k, v := range map[string]string{
		"mr.iid": strconv.Itoa(mrIID), "mr.branch": "feature", "mr.merged_branch": "done", "mr.merged_iid": strconv.Itoa(doneIID),
		"mr.new_branch": "fresh", "issue.iid": strconv.Itoa(issueIID), "discussion.id": str(thread["id"]),
		"sha.main": sha["main"], "sha.feature": sha["feature"], "blob.sha": blob,
	} {
		scene.Context[k] = v
	}
	return scene, nil
}

type recording struct {
	answers map[string]cs.Answer
	shapes  map[string]cs.Entry
}

// recordOnce はプロジェクトを 1 つ作って列を送る。
func recordOnce(c Config, requests []cs.Request) (recording, error) {
	build := c.BuildScene
	if build == nil {
		build = BuildScene
	}
	raw := make([]byte, 4)
	if _, err := rand.Read(raw); err != nil {
		return recording{}, err
	}
	scene, err := build(c, ProjectPrefix+"-"+hex.EncodeToString(raw))
	if err != nil {
		return recording{}, err
	}
	defer scene.Delete()
	out := recording{map[string]cs.Answer{}, map[string]cs.Entry{}}
	for _, req := range requests {
		path, payload, err := req.Resolve(scene.Context)
		if err != nil {
			return out, fmt.Errorf("%s: %w", req.Name, err)
		}
		var body any
		if payload != nil {
			if err := cs.DecodeJSON(payload, &body); err != nil {
				return out, err
			}
		}
		token := ""
		if req.Token == "main" {
			token = c.Token
		}
		status, answer := c.API.Call(req.Method, path, token, body, "")
		for variable, key := range req.Save {
			value, ok := gitlabapi.AsMap(answer)[key]
			if !ok {
				return out, fmt.Errorf("%s: 応答に %s が無い", req.Name, key)
			}
			scene.Context[variable] = fmt.Sprint(value)
		}
		out.answers[req.Name] = cs.Answer{Status: status, Body: answer}
		out.shapes[req.Name] = cs.Entry{Status: status, Shape: cs.Normalize(answer)}
	}
	return out, nil
}

// readsDifferences は reads の各キーの型が、2 回の記録で一致するか。一致しないキーの一覧。
func readsDifferences(requests []cs.Request, first, second map[string]cs.Entry) []string {
	var out []string
	for _, req := range requests {
		for _, key := range req.Reads {
			a, b := cs.NodeAt(first[req.Name].Shape, key), cs.NodeAt(second[req.Name].Shape, key)
			var ta, tb []string
			if a != nil {
				ta = a.T
			}
			if b != nil {
				tb = b.T
			}
			if !slices.Equal(ta, tb) {
				out = append(out, fmt.Sprintf("%s: %s  1 回目 %v / 2 回目 %v", req.Name, key, ta, tb))
			}
		}
	}
	return out
}

// Text は recorded.json の本文（キーを名前順に、字下げ 1 字）。
func Text(doc cs.Document) (string, error) {
	raw, err := json.Marshal(doc)
	if err != nil {
		return "", err
	}
	var generic any
	if err := cs.DecodeJSON(raw, &generic); err != nil {
		return "", err
	}
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", " ")
	if err := enc.Encode(generic); err != nil {
		return "", err
	}
	return buf.String(), nil
}

// Record は録って置く（DryRun なら標準出力へ）。終了コードを返す。
func Record(c Config) int {
	if c.Token == "" || c.ReviewerToken == "" || c.ReviewerID == 0 {
		fmt.Fprintln(c.Stderr, "トークンが無い。gitlabmanual status で確かめる")
		return 2
	}
	status, version := c.API.Version(c.Token)
	versionBody := gitlabapi.AsMap(version)
	if status != 200 || versionBody == nil {
		fmt.Fprintf(c.Stderr, "GitLab に認証できない（HTTP %d）。何も作らず終える\n", status)
		return 2
	}
	requests := c.Requests
	if requests == nil {
		var err error
		if requests, err = cs.LoadAllRequests(); err != nil { // 全要求（sh と拡張）
			fmt.Fprintln(c.Stderr, err)
			return 2
		}
	}
	first, err := recordOnce(c, requests)
	if err != nil {
		fmt.Fprintln(c.Stderr, err)
		return 1
	}
	second, err := recordOnce(c, requests)
	if err != nil {
		fmt.Fprintln(c.Stderr, err)
		return 1
	}
	var problems []string
	for _, d := range readsDifferences(requests, first.shapes, second.shapes) {
		problems = append(problems, "2 回の記録で reads の形が違う: "+d)
	}
	var allow struct {
		NotRecorded []struct {
			What string `json:"what"`
		} `json:"not_recorded"`
	}
	var forbidden []cs.Forbidden
	if err := cs.LoadData("allow.json", &allow); err != nil {
		fmt.Fprintln(c.Stderr, err)
		return 2
	}
	if err := cs.LoadData("forbidden.json", &forbidden); err != nil {
		fmt.Fprintln(c.Stderr, err)
		return 2
	}
	now := time.Now
	if c.Now != nil {
		now = c.Now
	}
	image, ver, at := c.Image, str(versionBody["version"]), now().UTC().Format("2006-01-02")
	doc := cs.Document{Version: 1, Source: "gitlab", GitlabImage: &image, GitlabVersion: &ver, RecordedAt: &at, NotRecorded: []string{}, Requests: first.shapes}
	for _, n := range allow.NotRecorded {
		doc.NotRecorded = append(doc.NotRecorded, n.What)
	}
	problems = append(problems, cs.PlacementProblems(requests, first.answers, doc, c.RunningImage(), forbidden)...)
	if len(problems) > 0 {
		fmt.Fprintf(c.Stderr, "recorded.json は置かない:\n  %s\n", strings.Join(problems, "\n  "))
		return 1
	}
	text, err := Text(doc)
	if err != nil {
		fmt.Fprintln(c.Stderr, err)
		return 1
	}
	if c.DryRun {
		fmt.Fprint(c.Stdout, text)
		return 0
	}
	path := filepath.Join(cs.DataDir(), "recorded.json")
	if err := cs.WriteAtomic(path, text); err != nil {
		fmt.Fprintln(c.Stderr, err)
		return 1
	}
	fmt.Fprintf(c.Stdout, "置いた: %s（%d 本。2 回の記録で reads の形が一致）\n", path, len(first.shapes))
	return 0
}

// ConfigFromEnv はトークンを tokens.env の値と環境変数から決める（同名の環境変数が先）。
func ConfigFromEnv(tokensFile string, getenv func(string) string) (token, reviewerToken string, reviewerID int) {
	values := gitlabapi.ReadEnvFile(tokensFile)
	pick := func(key string) string {
		if v := getenv(key); v != "" {
			return v
		}
		return values[key]
	}
	reviewerID, _ = strconv.Atoi(pick(env.ProbeReviewerID))
	return pick(env.GitLabToken), pick(env.ProbeReviewerToken), reviewerID
}
