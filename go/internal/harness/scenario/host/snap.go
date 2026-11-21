package host

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"ccnavi/internal/harness/gitlabapi"
)

// 相手側の状態の記録（step の終わりに fake-gitlab・docker-gitlab を読む）。
//
//   - 対象は MR（一覧と 1 件）・スレッドと解決・issue。読むだけで、呼ぶのは GET の API だけ
//   - 承認は MR ごとに読み、MR の `approvals` に残す。取れたら {"taken": true, "approved", "approved_by"}、
//     取れなかったら {"taken": false}（承認が無い＝taken が true で approved が false、と区別できる）
//   - API が個々に失敗したことは、その場所の errors に残して続ける
//   - トークンは記録に出さない。取る項目は選んだものだけにし、知っているトークンの値は伏せる
const (
	// textLimit は説明・メモの本文の長さの上限（文字数）。
	textLimit = 1000
	// pageLimit は一覧で読む件数。1 ページ（100 件）だけ読み、100 件に達したら、切れた可能性を errors に残す。
	pageLimit = 100
	mask      = "***"
)

var (
	mrKeys    = []string{"iid", "title", "state", "draft", "source_branch", "target_branch", "merged_at", "closed_at", "merge_status", "detailed_merge_status", "has_conflicts", "blocking_discussions_resolved", "web_url", "created_at", "updated_at", "sha", "labels"}
	issueKeys = []string{"iid", "title", "state", "web_url", "created_at", "closed_at", "labels"}
	noteKeys  = []string{"id", "created_at", "system", "resolvable", "resolved", "type"}
)

// SnapFile は step の記録のファイル名。
func SnapFile(step int) string { return fmt.Sprintf("host-%d.json", step) }

// Summary は記録の要約。
type Summary struct {
	MRs               int `json:"mrs"`
	MRsOpened         int `json:"mrs_opened"`
	MRsMerged         int `json:"mrs_merged"`
	MRsDraft          int `json:"mrs_draft"`
	MRsApproved       int `json:"mrs_approved"`
	Threads           int `json:"threads"`
	ThreadsUnresolved int `json:"threads_unresolved"`
	ThreadsSystem     int `json:"threads_system"`
	Issues            int `json:"issues"`
	IssuesOpened      int `json:"issues_opened"`
}

// Call は読んだ API とその状態。
type Call struct {
	API    string `json:"api"`
	Status int    `json:"status"`
}

// SnapError は取れなかった所。
type SnapError struct {
	API    string `json:"api"`
	Status *int   `json:"status,omitempty"`
	Error  string `json:"error"`
}

// Record は host-<step>.json の中身。MR・スレッド・メモ・issue は、選んだ項目だけの辞書。
type Record struct {
	Step    int              `json:"step"`
	TakenAt string           `json:"taken_at"`
	Host    Kind             `json:"host"`
	Project string           `json:"project"`
	Summary Summary          `json:"summary"`
	MRs     []map[string]any `json:"mrs"`
	Issues  []map[string]any `json:"issues"`
	Calls   []Call           `json:"calls"`
	Errors  []SnapError      `json:"errors"`
}

// SnapResult は Take の要約。state.json の host_snapshots に残す。
type SnapResult struct {
	Step   int    `json:"step"`
	File   string `json:"file"`
	OK     bool   `json:"ok"`
	Errors int    `json:"errors"`
}

func clip(v any) string {
	text, _ := v.(string)
	if n := utf8.RuneCountInString(text); n > textLimit {
		return string([]rune(text)[:textLimit]) + fmt.Sprintf("…(+%d)", n-textLimit)
	}
	return text
}

// user はユーザの辞書から username だけを取る（ほかの項目は取らない）。
func user(v any) any {
	if m := gitlabapi.AsMap(v); m != nil {
		return m["username"]
	}
	return nil
}

func pick(src map[string]any, keys []string) map[string]any {
	out := map[string]any{}
	for _, k := range keys {
		if v, ok := src[k]; ok {
			out[k] = v
		}
	}
	return out
}

func truthy(v any) bool {
	switch x := v.(type) {
	case bool:
		return x
	case nil:
		return false
	case string:
		return x != ""
	}
	return true
}

func noteView(note map[string]any) map[string]any {
	out := pick(note, noteKeys)
	out["author"] = user(note["author"])
	if truthy(note["resolved_by"]) {
		out["resolved_by"] = user(note["resolved_by"])
	}
	if pos := gitlabapi.AsMap(note["position"]); pos != nil && (truthy(pos["new_path"]) || truthy(pos["old_path"])) {
		out["path"] = firstTruthy(pos["new_path"], pos["old_path"])
		out["line"] = firstTruthy(pos["new_line"], pos["old_line"])
	}
	out["body"] = clip(note["body"])
	return out
}

func firstTruthy(a, b any) any {
	if truthy(a) {
		return a
	}
	return b
}

func threadView(d map[string]any) map[string]any {
	var notes []map[string]any
	for _, n := range gitlabapi.AsList(d["notes"]) {
		if m := gitlabapi.AsMap(n); m != nil {
			notes = append(notes, m)
		}
	}
	system := len(notes) > 0
	var resolvable []map[string]any
	for _, n := range notes {
		system = system && truthy(n["system"])
		if truthy(n["resolvable"]) {
			resolvable = append(resolvable, n)
		}
	}
	// 解決できるメモがあり、すべて解決済みなら true。解決できないスレッドは null
	var resolved any
	if len(resolvable) > 0 {
		all := true
		for _, n := range resolvable {
			all = all && truthy(n["resolved"])
		}
		resolved = all
	}
	views := make([]map[string]any, 0, len(notes))
	for _, n := range notes {
		views = append(views, noteView(n))
	}
	return map[string]any{
		"id":              d["id"],
		"individual_note": truthy(d["individual_note"]),
		// 本物の GitLab は、承認・解決などのシステムのメモもスレッドとして返す（模擬は返さない）
		"system":   system,
		"resolved": resolved,
		"notes":    views,
	}
}

// approvalsView は承認の応答から、残す 3 つ（approved・approved_by[].user.username・approved_by[].approved_at）だけを取る。
// user_can_approve・user_has_approved は、読んだトークンから見た値で意味が曖昧なので残さない。
func approvalsView(body map[string]any) map[string]any {
	given := []map[string]any{}
	for _, a := range gitlabapi.AsList(body["approved_by"]) {
		if m := gitlabapi.AsMap(a); m != nil {
			given = append(given, map[string]any{"username": user(m["user"]), "approved_at": m["approved_at"]})
		}
	}
	return map[string]any{"taken": true, "approved": body["approved"] == true, "approved_by": given}
}

func mrView(mr map[string]any) map[string]any {
	out := pick(mr, mrKeys)
	// 古い GitLab は draft の代わりに work_in_progress を返す
	if _, ok := out["draft"]; !ok {
		if wip, ok := mr["work_in_progress"]; ok {
			out["draft"] = wip
		}
	}
	out["author"] = user(mr["author"])
	if truthy(mr["merged_by"]) {
		out["merged_by"] = user(mr["merged_by"])
	}
	out["description"] = clip(mr["description"])
	return out
}

func issueView(issue map[string]any) map[string]any {
	out := pick(issue, issueKeys)
	out["author"] = user(issue["author"])
	out["description"] = clip(issue["description"])
	return out
}

func summarize(mrs, issues []map[string]any) Summary {
	s := Summary{MRs: len(mrs), Issues: len(issues)}
	for _, mr := range mrs {
		switch mr["state"] {
		case "opened":
			s.MRsOpened++
		case "merged":
			s.MRsMerged++
		}
		if truthy(mr["draft"]) {
			s.MRsDraft++
		}
		if gitlabapi.AsMap(mr["approvals"])["approved"] == true {
			s.MRsApproved++
		}
		threads, _ := mr["threads"].([]map[string]any)
		for _, t := range threads {
			if t["system"] == true {
				s.ThreadsSystem++
				continue
			}
			s.Threads++
			if t["resolved"] == false {
				s.ThreadsUnresolved++
			}
		}
	}
	for _, i := range issues {
		if i["state"] == "opened" {
			s.IssuesOpened++
		}
	}
	return s
}

// Collect は MR（一覧と 1 件ずつ）・スレッド・issue を読んで、記録（step・時刻などを除く）を返す。読むだけ。
// 一覧が取れなければ、その欄は空にして Errors に残す。
func Collect(client Reader, token string, project any) Record {
	rec := Record{MRs: []map[string]any{}, Issues: []map[string]any{}, Calls: []Call{}, Errors: []SnapError{}}
	read := func(label string, status int, body any) any {
		rec.Calls = append(rec.Calls, Call{label, status})
		if status < 200 || status >= 300 {
			rec.Errors = append(rec.Errors, SnapError{API: label, Status: &status, Error: fmt.Sprintf("HTTP %d", status)})
			return nil
		}
		return body
	}
	capped := func(label string, n int) {
		if n >= pageLimit {
			rec.Errors = append(rec.Errors, SnapError{API: label, Error: fmt.Sprintf("%d 件で切った（ページを送らない）", pageLimit)})
		}
	}
	status, body := client.ListMRs(token, project, "all")
	listed := read("list_mrs", status, body)
	if status == 0 {
		// 相手に接続できない。残りを読んでも同じ待ちを重ねるだけなので、ここでやめる
		rec.Errors = append(rec.Errors, SnapError{API: "snapshot", Error: "相手に接続できないので、残りは読まない"})
		return rec
	}
	list := gitlabapi.AsList(listed)
	if listed != nil && list == nil {
		rec.Errors = append(rec.Errors, SnapError{API: "list_mrs", Error: "応答が配列ではない"})
	}
	capped("list_mrs", len(list))
	for _, item := range list {
		listItem := gitlabapi.AsMap(item)
		iid := listItem["iid"]
		status, body := client.GetMR(token, project, iid)
		detail := gitlabapi.AsMap(read(fmt.Sprintf("get_mr %v", iid), status, body))
		if detail == nil {
			detail = listItem // 1 件の詳細が取れなければ、一覧の分は残す（Errors にも残る）
		}
		view := mrView(detail)
		label := fmt.Sprintf("list_discussions %v", iid)
		status, body = client.ListDiscussions(token, project, iid)
		if found, ok := read(label, status, body).([]any); ok {
			capped(label, len(found))
			threads := []map[string]any{}
			for _, d := range found {
				if m := gitlabapi.AsMap(d); m != nil {
					threads = append(threads, threadView(m))
				}
			}
			view["threads"] = threads
		} else {
			view["threads"] = []map[string]any{}
			view["threads_unavailable"] = true
		}
		status, body = client.ListApprovals(token, project, iid)
		if got := gitlabapi.AsMap(read(fmt.Sprintf("list_approvals %v", iid), status, body)); got != nil {
			view["approvals"] = approvalsView(got)
		} else {
			view["approvals"] = map[string]any{"taken": false}
		}
		rec.MRs = append(rec.MRs, view)
	}
	status, body = client.ListIssues(token, project, "all", pageLimit)
	if found, ok := read("list_issues", status, body).([]any); ok {
		capped("list_issues", len(found))
		for _, i := range found {
			if m := gitlabapi.AsMap(i); m != nil {
				rec.Issues = append(rec.Issues, issueView(m))
			}
		}
	}
	rec.Summary = summarize(rec.MRs, rec.Issues)
	return rec
}

// maskSecrets は text の中の秘密（と、その JSON のエスケープ形）を `***` に置き換える。長いものから。
func maskSecrets(text string, secrets []string) string {
	sorted := append([]string(nil), secrets...)
	sort.Slice(sorted, func(i, j int) bool { return len(sorted[i]) > len(sorted[j]) })
	for _, s := range sorted {
		if s == "" {
			continue
		}
		escaped, _ := json.Marshal(s)
		text = strings.ReplaceAll(text, s, mask)
		text = strings.ReplaceAll(text, string(escaped[1:len(escaped)-1]), mask)
	}
	return text
}

// Take は相手側の状態を読んで out/host-<step>.json に書き、要約を返す。API が個々に失敗したことは記録の errors に残して
// 続ける。相手に近づけない（接続先が決まらない）・書けないときはエラーで返す（呼び手が step を止めて後片付けする）。
func (m *Manager) Take(root string, h *Host, step int, out string) (SnapResult, error) {
	access, err := m.APIAccess(h)
	if err != nil {
		return SnapResult{}, fmt.Errorf("相手側の状態を読む入口を作れない: %w", err)
	}
	rec := Collect(access.Client, access.Token, access.Project)
	rec.Step, rec.Host, rec.Project = step, h.Kind, h.Project
	rec.TakenAt = time.Now().Format(time.RFC3339)
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", " ")
	if err := enc.Encode(rec); err != nil {
		return SnapResult{}, err
	}
	name := SnapFile(step)
	if err := os.WriteFile(filepath.Join(out, name), []byte(maskSecrets(buf.String(), access.Secrets)), 0o644); err != nil {
		return SnapResult{}, fmt.Errorf("相手側の状態を書けない: %w", err)
	}
	result := SnapResult{Step: step, File: name, OK: len(rec.Errors) == 0, Errors: len(rec.Errors)}
	if result.OK {
		log(root).Info("相手側の状態を残した", "step", step, "file", name, "mrs", rec.Summary.MRs, "issues", rec.Summary.Issues)
	} else {
		log(root).Error("相手側の状態を取れなかった所がある", "step", step, "file", name, "errors", result.Errors)
	}
	return result, nil
}
