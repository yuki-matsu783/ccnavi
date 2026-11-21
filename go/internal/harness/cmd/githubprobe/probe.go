package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// apiBase は GitHub の REST と GraphQL の根。
const apiBase = "https://api.github.com"

// check は 1 つの確かめの結果。
type check struct {
	name, detail string
	ok           bool
}

// result は子プロセスの結果（終了コードと標準出力・標準エラー）。
type result struct {
	Code           int
	Stdout, Stderr string
}

// probe は実測の状態。実験場に作った物は片付けの一覧に積む。
type probe struct {
	stdout io.Writer
	log    *os.File

	out, root, exe string
	repo, owner    string
	token          string
	reviewerToken  string
	selfReview     bool
	env            map[string]string

	checks    []check
	apiSeq    int
	stamp     string
	base      string
	srcRoot   string
	stderr    io.Writer
	issue     int
	parent    string
	num       int
	origin    string
	madeBrs   []string
	madePRs   []int
	madeIssue []int
	cleanupNG []string
}

// say は標準出力とログに 1 行足す。
func (p *probe) say(text string) {
	fmt.Fprintln(p.stdout, text)
	p.logText(text + "\n")
}

func (p *probe) logText(text string) {
	_, _ = p.log.WriteString(text)
}

// check は確かめの結果を記録し、ok を返す。止める条件の所では `if !p.check(...) { return false }` と書く。
func (p *probe) check(name string, ok bool, detail string) bool {
	p.checks = append(p.checks, check{name, detail, ok})
	mark := "FAIL"
	if ok {
		mark = "PASS"
	}
	p.say(fmt.Sprintf("%s  %s  %s", mark, name, detail))
	return ok
}

func (p *probe) redact(text string) string { return redact(text, p.token, p.reviewerToken) }

// exec は子プロセスを 1 回起こし、コマンドと結果をログに書く（トークンは隠す）。
// 起こせないとき（道具が無い）は、実測を続けられないので止める。
func (p *probe) exec(args []string, cwd, stdin string, env map[string]string) result {
	cmd := exec.Command(lookPath(args[0], env), args[1:]...)
	cmd.Dir = cwd
	cmd.Env = envList(env)
	cmd.Stdin = readerOf(stdin)
	var so, se bytes.Buffer
	cmd.Stdout, cmd.Stderr = &so, &se
	code := 0
	if err := cmd.Run(); err != nil {
		exitErr, ok := err.(*exec.ExitError)
		if !ok {
			panic(err)
		}
		code = exitErr.ExitCode()
	}
	r := result{Code: code, Stdout: so.String(), Stderr: se.String()}
	p.logText(fmt.Sprintf("\n$ (%s) %s\n  rc=%d\n", cwd, p.redact(strings.Join(args, " ")), code))
	if s := strings.TrimSpace(r.Stdout); s != "" {
		p.logText("  stdout: " + strings.ReplaceAll(p.redact(s), "\n", "\n          ") + "\n")
	}
	if s := strings.TrimSpace(r.Stderr); s != "" {
		p.logText("  stderr: " + strings.ReplaceAll(p.redact(s), "\n", "\n          ") + "\n")
	}
	return r
}

// lookPath は env の PATH から名前を探す。見つからなければ名前のまま（起こしたときに失敗する）。
func lookPath(name string, env map[string]string) string {
	if strings.ContainsRune(name, filepath.Separator) || strings.Contains(name, "/") {
		return name
	}
	for _, dir := range filepath.SplitList(env[pathKey(env)]) {
		candidate := filepath.Join(dir, name)
		if info, err := os.Stat(candidate); err == nil && !info.IsDir() {
			return candidate
		}
	}
	return name
}

// envList は環境を `NAME=value` の並び（名前順）にする。
func envList(env map[string]string) []string {
	out := make([]string, 0, len(env))
	for k, v := range env {
		out = append(out, k+"="+v)
	}
	sort.Strings(out)
	return out
}

// envFromOS は走らせた人の環境（名前から値へ）。
func envFromOS() map[string]string {
	out := map[string]string{}
	for _, kv := range os.Environ() {
		k, v, _ := strings.Cut(kv, "=")
		if k != "" {
			out[k] = v
		}
	}
	return out
}

// git は cwd で git を起こし、標準出力を返す。終了コードが 0 でなければ止める（Python の check=True）。
func (p *probe) git(cwd string, args ...string) string {
	r := p.exec(append([]string{"git"}, args...), cwd, "", p.env)
	if r.Code != 0 {
		panic(fmt.Sprintf("git %s\n%s%s", strings.Join(args, " "), r.Stdout, r.Stderr))
	}
	return r.Stdout
}

// sh は sh の配布スクリプトを起こす（環境は env。nil なら実測の環境）。
func (p *probe) sh(script, cwd string, env map[string]string, args ...string) result {
	if env == nil {
		env = p.env
	}
	return p.exec(append([]string{"sh", script}, args...), cwd, "", env)
}

// shIn は sh に標準入力を渡す。
func (p *probe) shIn(script, cwd, stdin string, args ...string) result {
	return p.exec(append([]string{"sh", script}, args...), cwd, stdin, p.env)
}

// exeCall は ccnavi の実行ファイルを、一時リポジトリの根で起こす（--root を無視する古い exe が、本体に書かないように cwd も根にする）。
func (p *probe) exeCall(stdin string, args ...string) result {
	return p.exec(append([]string{p.exe, "--root", p.root}, args...), p.root, stdin, p.env)
}

// hookFull は PreToolUse などの hook を 1 回打つ。(終了コード, permissionDecision, 理由か追加の文脈)。
func (p *probe) hookFull(event, tool, cwd string, input map[string]any) (int, string, string) {
	payload := map[string]any{
		"hook_event_name": event,
		"tool_name":       tool,
		"cwd":             cwd,
		"session_id":      "probe",
		"tool_input":      input,
	}
	data, _ := json.Marshal(payload)
	r := p.exeCall(string(data))
	if strings.TrimSpace(r.Stdout) == "" {
		return r.Code, "", ""
	}
	var top any
	if err := json.Unmarshal([]byte(r.Stdout), &top); err != nil {
		return r.Code, "", r.Stdout
	}
	out := obj(obj(top)["hookSpecificOutput"])
	reason := str(out, "permissionDecisionReason")
	if reason == "" {
		reason = str(out, "additionalContext")
	}
	return r.Code, str(out, "permissionDecision"), reason
}

// hook は hookFull の理由か追加の文脈だけを返す。
func (p *probe) hook(event, tool, cwd string, input map[string]any) string {
	_, _, reason := p.hookFull(event, tool, cwd, input)
	return reason
}

// agentGate は子を起こす Agent が止められているか。止められている = exe が正常終了し、deny で、理由に DENY_PHASE_REVIEW がある。
func (p *probe) agentGate(cwd string) (bool, string) {
	rc, decision, reason := p.hookFull("PreToolUse", "Agent", cwd, map[string]any{"description": "次の子"})
	blocked := rc == 0 && decision == "deny" && strings.Contains(reason, "DENY_PHASE_REVIEW")
	return blocked, fmt.Sprintf("rc=%d decision=%q reason=%q", rc, decision, head(reason, 60))
}

// api は人間役の REST を 1 回呼ぶ。生の応答は OUT に残す。再送は GET だけ（POST を送り直すと二重に作る）。
// 戻り値は HTTP の状態（通信の失敗は 0）と、本文（JSON。読めなければ文字列）。
func (p *probe) api(method, path, token string, payload any, tag string) (int, any) {
	p.apiSeq++
	if token == "" {
		token = p.token
	}
	var data []byte
	if payload != nil {
		data, _ = json.Marshal(payload)
	}
	attempts := 1
	if method == "GET" {
		attempts = 3
	}
	client := &http.Client{Timeout: 60 * time.Second}
	status, raw := 0, ""
	for attempt := 1; attempt <= attempts; attempt++ {
		req, _ := http.NewRequest(method, apiBase+path, bytes.NewReader(data))
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("Accept", "application/vnd.github+json")
		req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("User-Agent", "ccnavi-probe")
		res, err := client.Do(req)
		if err != nil {
			msg, _ := json.Marshal(map[string]string{"error": err.Error()})
			status, raw = 0, string(msg)
			p.logText(fmt.Sprintf("\nAPI %s %s 失敗 (%v) %d 回目\n", method, path, err, attempt))
			time.Sleep(5 * time.Second)
			continue
		}
		body, _ := io.ReadAll(res.Body)
		_ = res.Body.Close()
		status, raw = res.StatusCode, string(body)
		break
	}
	var body any
	if raw == "" {
		body = map[string]any{}
	} else if err := json.Unmarshal([]byte(raw), &body); err != nil {
		body = raw
	}
	slug := tag
	if slug == "" {
		slug = head(strings.ReplaceAll(strings.ReplaceAll(strings.Trim(path, "/"), "/", "_"), "?", "_"), 60)
	}
	name := filepath.Join(p.out, fmt.Sprintf("api-%02d-%s-%s.json", p.apiSeq, method, slug))
	_, _ = writeFile(name, prettyJSON(map[string]any{"status": status, "path": path, "payload": payload, "body": body}))
	p.logText(fmt.Sprintf("\nAPI %s %s -> %d\n", method, path, status))
	return status, body
}

// graphql は GraphQL を 1 回呼ぶ。本文が辞書でなければ {status, body} にする。
func (p *probe) graphql(query string, variables map[string]any, token, tag string) map[string]any {
	if tag == "" {
		tag = "graphql"
	}
	status, body := p.api("POST", "/graphql", token, map[string]any{"query": query, "variables": variables}, tag)
	if m := obj(body); m != nil {
		return m
	}
	return map[string]any{"status": status, "body": body}
}

// prettyJSON は字下げ 1 の JSON（非 ASCII はそのまま）。
func prettyJSON(v any) string {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", " ")
	_ = enc.Encode(v)
	return buf.String()
}

// thread は GraphQL の reviewThreads の 1 件（最初のコメントだけ）。
type thread struct {
	ID         string `json:"id"`
	IsResolved bool   `json:"isResolved"`
	IsOutdated bool   `json:"isOutdated"`
	Comments   struct {
		Nodes []threadNode `json:"nodes"`
	} `json:"comments"`
}

type threadNode struct {
	URL          string `json:"url"`
	Path         string `json:"path"`
	Line         *int   `json:"line"`
	OriginalLine *int   `json:"originalLine"`
	Body         string `json:"body"`
	CreatedAt    string `json:"createdAt"`
}

// first は最初のコメント（無ければ空）。
func (t thread) first() threadNode {
	if len(t.Comments.Nodes) == 0 {
		return threadNode{}
	}
	return t.Comments.Nodes[0]
}

const threadsQuery = "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n)" +
	"{reviewThreads(first:100){nodes{id isResolved isOutdated comments(first:1)" +
	"{nodes{url path line originalLine body createdAt}}}}}}}"

// threads は人間役が GraphQL で直に読むスレッド。sh の読みと突き合わせる。読めなければ空。
func (p *probe) threads(number int, tag string) []thread {
	res := p.graphql(threadsQuery, map[string]any{"o": p.owner, "r": strings.SplitN(p.repo, "/", 2)[1], "n": number}, "", tag)
	nodes := dig(res, "data", "repository", "pullRequest", "reviewThreads", "nodes")
	data, err := json.Marshal(nodes)
	var out []thread
	if err != nil || json.Unmarshal(data, &out) != nil {
		return nil
	}
	return out
}

// fetchOut は sh の fetch の出力のうち、実測が読む欄。
type fetchOut struct {
	Host string `json:"host"`
	MR   struct {
		Number int `json:"number"`
	} `json:"mr"`
	Threads []fetchThread `json:"threads"`
	Reviews []fetchReview `json:"reviews"`
}

type fetchThread struct {
	Path      string `json:"path"`
	Line      int    `json:"line"`
	Resolved  bool   `json:"resolved"`
	URL       string `json:"url"`
	CreatedAt string `json:"created_at"`
}

type fetchReview struct {
	State  string `json:"state"`
	Author any    `json:"author"`
}

// fetched は fetch の標準出力を読む。読めなければ零値。
func fetched(r result) fetchOut {
	var f fetchOut
	_ = json.Unmarshal([]byte(r.Stdout), &f)
	return f
}

// sameFetch は、curl と gh の経路の fetch が同じ欄の値を持つか（host・mr・threads・reviews）。
func sameFetch(a, b fetchOut) bool {
	key := func(f fetchOut) string {
		data, _ := json.Marshal(map[string]any{"host": f.Host, "mr": f.MR, "threads": f.Threads, "reviews": f.Reviews})
		return string(data)
	}
	return key(a) == key(b)
}

// digits は author が数の id（文字列の数字）か。
func digits(v any) bool {
	s, ok := v.(string)
	if !ok || s == "" {
		return false
	}
	for _, c := range s {
		if c < '0' || c > '9' {
			return false
		}
	}
	return true
}

// prOf は head が branch の PR（無ければ nil）。
func (p *probe) prOf(branch string) map[string]any {
	_, found := p.api("GET", fmt.Sprintf("/repos/%s/pulls?state=all&head=%s:%s", p.repo, p.owner, branch), "", nil, "pr-list-"+branch)
	if l := list(found); len(l) > 0 {
		return obj(l[0])
	}
	return nil
}

// commentsOf は課題（PR）のコメント。
func (p *probe) commentsOf(number int) []map[string]any {
	_, found := p.api("GET", fmt.Sprintf("/repos/%s/issues/%d/comments?per_page=100", p.repo, number), "", nil, fmt.Sprintf("comments-%d", number))
	var out []map[string]any
	for _, c := range list(found) {
		out = append(out, obj(c))
	}
	return out
}
