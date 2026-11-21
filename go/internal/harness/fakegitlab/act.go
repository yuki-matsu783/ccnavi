package fakegitlab

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

type obj = map[string]any

// call は 1 つの要求の処理に要るもの。状態は読んだ木で、処理が直したものを呼び手が書き戻す。
type call struct {
	s     *Server
	r     *http.Request
	data  obj
	state State
	args  map[string]string
	query url.Values
}

// num は JSON の数（json.Number・float64・int）を整数にする。
func num(v any) int {
	switch n := v.(type) {
	case json.Number:
		i, _ := n.Int64()
		return int(i)
	case float64:
		return int(n)
	case int:
		return n
	}
	return 0
}

func items(m obj, key string) []any {
	list, _ := m[key].([]any)
	return list
}

func push(m obj, key string, v any) { m[key] = append(items(m, key), v) }

func asObj(v any) obj { m, _ := v.(obj); return m }

func (c *call) mrOf(iid int) obj {
	for _, v := range items(c.state, "mrs") {
		if mr := asObj(v); num(mr["iid"]) == iid {
			return mr
		}
	}
	return nil
}

func (c *call) issueOf(iid int) obj {
	for _, v := range items(c.state, "issues") {
		if issue := asObj(v); num(issue["iid"]) == iid {
			return issue
		}
	}
	return nil
}

func (c *call) nextID() int {
	seq := 101
	if v, ok := c.state["seq"]; ok {
		seq = num(v) + 1
	}
	c.state["seq"] = seq
	return seq
}

// issueView は応答に出す issue。状態が内部に持つメモ（`notes`）は、実物の issue には無いので出さない。
func issueView(issue obj) obj {
	out := obj{}
	for k, v := range issue {
		if k != "notes" {
			out[k] = v
		}
	}
	return out
}

// page は per_page・page で切った一覧。空でも JSON では `[]`。
func (c *call) page(list []any) []any {
	per, pageNo := 20, 1
	if v, err := strconv.Atoi(c.query.Get("per_page")); err == nil {
		per = v
	}
	if v, err := strconv.Atoi(c.query.Get("page")); err == nil {
		pageNo = v
	}
	start := (pageNo - 1) * per
	if start < 0 || per < 0 || start >= len(list) {
		return []any{}
	}
	return list[start:min(start+per, len(list))]
}

func (c *call) now() string { return c.s.now() }

func (c *call) author() obj { return obj{"id": 2, "username": "reviewer"} }

func (c *call) wanted(mr obj, key string) bool {
	vals, ok := c.query[key]
	if !ok || len(vals) == 0 {
		return true
	}
	return vals[0] == "all" || vals[0] == str(mr[key])
}

// notFound は無い物（MR・issue・スレッド）への要求。実物と同じく 404。
func notFound(key any) error { return fmt.Errorf("%v", key) }

func (c *call) act(method, name string) (int, any, error) {
	iid := 0
	if v, ok := c.args["iid"]; ok {
		iid, _ = strconv.Atoi(v)
	}
	// 無い MR は 404（実物と同じ）。issue の iid とは別の番号なので、issue_* の経路は除く。
	// issue_* でない経路の iid は、すべて MR のもの
	_, hasIID := c.args["iid"]
	if hasIID && !strings.HasPrefix(name, "issue") && c.mrOf(iid) == nil {
		return 0, nil, notFound(iid)
	}
	post, put, get := method == "POST", method == "PUT", method == "GET"
	switch {
	case name == "user":
		return 200, obj{"id": 1, "username": "fake-user", "name": "Fake User", "state": "active"}, nil
	case name == "member":
		uid, _ := strconv.Atoi(c.args["uid"])
		return 200, obj{"id": uid, "username": "fake-user", "state": "active", "access_level": 40}, nil
	case name == "project":
		return 200, obj{
			"id": 1, "path_with_namespace": c.state["project"], "default_branch": "main", "archived": false,
			"issues_enabled": true, "issues_access_level": "enabled",
			"permissions": obj{
				"project_access": obj{"access_level": 40, "notification_level": 3},
				"group_access":   nil,
			},
		}, nil
	case name == "mrs" && get:
		found := []any{}
		for _, v := range items(c.state, "mrs") {
			if mr := asObj(v); c.wanted(mr, "state") && c.wanted(mr, "source_branch") {
				found = append(found, c.mrView(mr))
			}
		}
		return 200, c.page(found), nil
	case name == "mrs" && post:
		mr := obj{
			"iid": len(items(c.state, "mrs")) + 1, "state": "opened",
			"source_branch": c.str("source_branch", ""), "target_branch": c.str("target_branch", "main"),
			"title": c.str("title", ""), "description": c.str("description", ""),
			"discussions": []any{}, "reviewers": []any{}, "notes": []any{}, "created_at": c.now(),
		}
		mr["web_url"] = fmt.Sprintf("http://%s/%s/-/merge_requests/%d", hostOf(c.r), str(c.state["project"]), mr["iid"])
		push(c.state, "mrs", mr)
		return 201, c.mrView(mr), nil
	case name == "mr" && get:
		return 200, c.mrView(c.mrOf(iid)), nil
	case name == "mr" && put:
		// タイトルの書き換え（Draft を外す）と squash だけを受ける。
		mr := c.mrOf(iid)
		if v, ok := c.data["title"]; ok {
			mr["title"] = v
		}
		if v, ok := c.data["squash"]; ok {
			mr["squash"] = truthy(v)
		}
		return 200, c.mrView(mr), nil
	case name == "discussions" && get:
		return 200, c.page(items(c.mrOf(iid), "discussions")), nil
	case name == "discussions" && post:
		mr := c.mrOf(iid)
		id := c.nextID()
		note := obj{
			"id": id, "body": c.str("body", ""),
			// マージリクエスト上のスレッドは、差分の位置が無くても解決できる。
			"resolvable": true, "resolved": false, "created_at": c.now(), "type": "DiscussionNote",
			"author": obj{"id": 2, "username": c.str("author", "reviewer")},
		}
		if path := c.str("path", ""); path != "" {
			line := any(1)
			if v, ok := c.data["line"]; ok {
				line = v
			}
			note["position"] = obj{"new_path": path, "new_line": line}
		}
		discussion := obj{"id": fmt.Sprintf("d%d", id), "individual_note": false, "notes": []any{note}}
		push(mr, "discussions", discussion)
		return 201, discussion, nil
	case name == "discussion" && put:
		mr := c.mrOf(iid)
		resolved := c.query.Get("resolved") == "true" || !c.query.Has("resolved")
		for _, v := range items(mr, "discussions") {
			d := asObj(v)
			if str(d["id"]) != c.args["did"] {
				continue
			}
			for _, n := range items(d, "notes") {
				if note := asObj(n); truthy(note["resolvable"]) {
					note["resolved"] = resolved
				}
			}
			return 200, d, nil
		}
		return 0, nil, notFound(c.args["did"])
	case name == "approvals" && get:
		return 200, approvalsView(c.mrOf(iid)), nil
	case name == "approve" && post:
		// 同じ人の 2 回目は、承認を増やさない。実物は 201 を返す（応答の本文は承認の状態）
		mr := c.mrOf(iid)
		for _, v := range items(mr, "approved_by") {
			if num(asObj(asObj(v)["user"])["id"]) == 2 {
				return 201, approvalsView(mr), nil
			}
		}
		push(mr, "approved_by", obj{"approved_at": c.now(), "user": obj{"id": 2, "username": "reviewer"}})
		return 201, approvalsView(mr), nil
	case name == "reviewers" && get:
		return 200, c.page(items(c.mrOf(iid), "reviewers")), nil
	case name == "reviewer" && put:
		mr := c.mrOf(iid)
		uid, _ := strconv.Atoi(c.args["uid"])
		state := "reviewed"
		if c.query.Has("state") {
			state = c.query.Get("state")
		}
		for _, v := range items(mr, "reviewers") {
			if r := asObj(v); num(asObj(r["user"])["id"]) == uid {
				r["state"] = state
				r["updated_at"] = c.now()
				return 200, r, nil
			}
		}
		entry := obj{
			"user": obj{"id": uid, "username": fmt.Sprintf("human%d", uid)}, "state": state,
			"created_at": c.now(), "updated_at": c.now(),
		}
		push(mr, "reviewers", entry)
		return 201, entry, nil
	case name == "notes" && post:
		note := c.newNote()
		push(c.mrOf(iid), "notes", note)
		return 201, note, nil
	case name == "notes" && get:
		return 200, c.page(items(c.mrOf(iid), "notes")), nil
	case name == "merge" && put:
		mr := c.mrOf(iid)
		mr["state"] = "merged"
		mr["merged_at"] = c.now()
		return 200, c.mrView(mr), nil
	case name == "issues" && get:
		views := []any{}
		for _, v := range items(c.state, "issues") {
			views = append(views, issueView(asObj(v)))
		}
		return 200, c.page(views), nil
	case name == "issues" && post:
		issue := obj{
			"iid": len(items(c.state, "issues")) + 1, "title": c.str("title", ""), "description": c.str("description", ""),
			"state": "opened", "created_at": c.now(), "author": c.author(),
		}
		issue["web_url"] = fmt.Sprintf("http://%s/%s/-/issues/%d", hostOf(c.r), str(c.state["project"]), issue["iid"])
		push(c.state, "issues", issue)
		return 201, issue, nil
	case name == "closes_issues" && get:
		wanted := map[int]bool{}
		for _, m := range closing.FindAllStringSubmatch(str(c.mrOf(iid)["description"]), -1) {
			n, _ := strconv.Atoi(m[1])
			wanted[n] = true
		}
		found := []any{}
		for _, v := range items(c.state, "issues") {
			// 実物の closes_issues は references を返さない（sh は代替で受ける）
			if issue := asObj(v); wanted[num(issue["iid"])] {
				found = append(found, issueView(issue))
			}
		}
		return 200, c.page(found), nil
	case strings.HasPrefix(name, "issue") && (get || put || post):
		issue := c.issueOf(iid)
		if issue == nil {
			return 0, nil, notFound(iid)
		}
		switch {
		case name == "issue" && get:
			return 200, issueView(issue), nil
		case name == "issue_mrs" && get:
			// 本文で `#N` と参照した MR（実物の related_merge_requests は参照した MR を含む）
			ref := regexp.MustCompile(fmt.Sprintf(`#%d(?:\D|$)`, iid))
			found := []any{}
			for _, v := range items(c.state, "mrs") {
				if mr := asObj(v); ref.MatchString(str(mr["description"])) {
					found = append(found, c.mrView(mr))
				}
			}
			return 200, c.page(found), nil
		case name == "issue_notes" && get:
			return 200, c.page(items(issue, "notes")), nil
		case name == "issue_notes" && post:
			note := c.newNote()
			push(issue, "notes", note)
			return 201, note, nil
		case name == "issue" && put:
			if str(c.data["state_event"]) == "close" {
				issue["state"] = "closed"
				issue["closed_at"] = c.now()
			}
			return 200, issueView(issue), nil
		}
	case name == "discussion_notes" && post:
		for _, v := range items(c.mrOf(iid), "discussions") {
			d := asObj(v)
			if str(d["id"]) != c.args["did"] {
				continue
			}
			note := obj{
				"id": c.nextID(), "body": c.str("body", ""), "system": false, "resolvable": true, "resolved": false,
				"created_at": c.now(), "type": "DiscussionNote", "author": c.author(),
			}
			push(d, "notes", note)
			return 201, note, nil
		}
		return 0, nil, notFound(c.args["did"])
	}
	return 405, obj{"message": fmt.Sprintf("405 %s %s", method, name)}, nil
}

func (c *call) newNote() obj {
	return obj{"id": c.nextID(), "body": c.str("body", ""), "created_at": c.now(), "author": c.author()}
}

// str は本文の文字列の項目。無ければ def。
func (c *call) str(key, def string) string {
	if v, ok := c.data[key]; ok {
		return str(v)
	}
	return def
}

func truthy(v any) bool { b, _ := v.(bool); return b }

// approvalsView は承認の状態。実物の CE は、承認の前後ともに 200 でこの形を返す。
// `user_can_approve`・`user_has_approved` は、読んだトークンから見た値。模擬は誰でも承認できる扱い。
func approvalsView(mr obj) obj {
	approved := []any{}
	for _, v := range items(mr, "approved_by") {
		a := asObj(v)
		user := obj{}
		for k, x := range asObj(a["user"]) {
			user[k] = x
		}
		approved = append(approved, obj{"approved_at": a["approved_at"], "user": user})
	}
	return obj{"approved": len(approved) > 0, "approved_by": approved, "user_can_approve": true, "user_has_approved": false}
}

func (c *call) mrView(mr obj) obj {
	view := obj{}
	for _, k := range []string{"iid", "state", "source_branch", "target_branch", "title", "description", "web_url", "created_at", "merged_at"} {
		if v, ok := mr[k]; ok {
			view[k] = v
		}
	}
	// 実物の MR も持つ項目。sh は `.draft // .work_in_progress` で Draft かを読み、マージ済みの MR の探索は `.sha` を読む
	title := strings.ToLower(str(mr["title"]))
	view["draft"] = strings.HasPrefix(title, "draft:") || strings.HasPrefix(title, "wip:") || strings.HasPrefix(title, "[draft]")
	view["sha"] = strings.Repeat("0", 40)
	if v, ok := mr["sha"]; ok {
		view["sha"] = v
	}
	// 実物の MR も持つ項目。sh（ccnavi-common-review.sh の find_mr など）が、jq でこれを見て「このプロジェクトから出た MR か」を
	// 絞り込む。模擬のプロジェクトの id は 1（`project` の答え）
	view["source_project_id"] = 1
	if v, ok := mr["source_project_id"]; ok {
		view["source_project_id"] = v
	}
	return view
}
