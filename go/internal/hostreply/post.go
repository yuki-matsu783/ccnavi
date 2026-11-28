package hostreply

import (
	"errors"
	"regexp"
	"strconv"
	"strings"
)

// ErrNotPosted は、投稿の応答に投稿の URL（GitLab は id）が無いこと。sh は投稿できたか確定できないと読む。
var ErrNotPosted = errors.New("投稿の応答に URL か id が無い")

// Post は投稿 1 つ（`ccnavi host posted`・`found`・`sent` の答え）。値はどれも 1 行にしてある。
type Post struct {
	URL       string
	Number    string // issue の番号（issue のときだけ）
	CreatedAt string
	Author    string
}

// Lines は `url`・`number`・`created_at`・`author` の 1 行 1 項目（空の欄は出さない）。
func (p Post) Lines() string {
	var b strings.Builder
	for _, kv := range [][2]string{{"url", p.URL}, {"number", p.Number}, {"created_at", p.CreatedAt}, {"author", p.Author}} {
		if kv[1] != "" {
			b.WriteString(kv[0] + " " + kv[1] + "\n")
		}
	}
	return b.String()
}

// PostForm は投稿の種類。
type PostForm string

const (
	// PostComment は MR か issue へのコメント。GitLab の URL は対象の URL に `#note_<id>` を足して組む。
	PostComment PostForm = "comment"
	// PostIssue は issue の作成。
	PostIssue PostForm = "issue"
)

// Posted は投稿した応答（ホストの返事のまま）を読む。target は対象（MR か issue）の URL。
// URL（GitLab のコメントは id）が無ければ ErrNotPosted。
func Posted(kind string, form PostForm, target string, data []byte) (Post, error) {
	v, err := Decode(data)
	if err != nil {
		return Post{}, err
	}
	var p Post
	if form == PostIssue {
		p, err = issueOf(kind, v)
	} else {
		p, err = commentOf(kind, target, v)
	}
	if err == nil && p.URL == "" {
		err = ErrNotPosted
	}
	return p, err
}

// Found は、コメントの一覧（ホストの応答のまま）から、本文が mark で始まる最初のコメントを読む。無ければ偽。
func Found(kind, mark, target string, list []byte) (Post, bool, error) {
	v, err := Decode(list)
	if err != nil {
		return Post{}, false, err
	}
	all, err := List(v)
	if err != nil {
		return Post{}, false, err
	}
	for _, x := range all {
		got, err := Field(x, "body")
		if err != nil {
			return Post{}, false, err
		}
		body, err := Str(got)
		if err != nil {
			return Post{}, false, err
		}
		if !strings.HasPrefix(body, mark) {
			continue
		}
		p, err := commentOf(kind, target, x)
		if err == nil && p.URL == "" {
			err = ErrNotPosted
		}
		return p, err == nil, err
	}
	return Post{}, false, nil
}

// Sent は、操作記録の送り済みの URL から答えを組む（ホストに問い合わせない）。issue の番号は URL の最後の区切り
// （数でなければ 0）。
func Sent(form PostForm, url string) Post {
	p := Post{URL: oneLine(url)}
	if form == PostIssue {
		last := url[strings.LastIndex(url, "/")+1:]
		if _, err := strconv.Atoi(last); err != nil {
			last = "0"
		}
		p.Number = last
	}
	return p
}

func commentOf(kind, target string, v any) (Post, error) {
	var p picker
	var post Post
	if kind == "github" {
		post = Post{
			URL:       oneLine(Text(p.at(v, "", "html_url"))),
			CreatedAt: oneLine(Text(p.at(v, "", "created_at"))),
			Author:    oneLine(Text(p.at(v, "", "user", "login"))),
		}
	} else {
		id := p.raw(v, "id")
		if id != nil {
			post.URL = oneLine(target + "#note_" + ToString(id))
		}
		post.CreatedAt = oneLine(Text(p.at(v, "", "created_at")))
		post.Author = oneLine(ToString(p.at(v, "", "author", "id")))
	}
	return post, p.err
}

func issueOf(kind string, v any) (Post, error) {
	var p picker
	number, url := "number", "html_url"
	if kind != "github" {
		number, url = "iid", "web_url"
	}
	post := Post{Number: oneLine(Text(p.at(v, "", number))), URL: oneLine(Text(p.at(v, "", url)))}
	return post, p.err
}

// AccountMaxLength はアカウント名の長さの上限（字）。
const AccountMaxLength = 100

// AccountForm はアカウント名の形（英数字と _ . - の 1〜100 字）。`--actor` と、拡張がホストから読む形（`github.ts` の NAME）と同じ。
var AccountForm = regexp.MustCompile(`\A[A-Za-z0-9_.-]{1,` + strconv.Itoa(AccountMaxLength) + `}\z`)

// Account は、トークンの持ち主を聞いた応答（GitHub は GET /user、GitLab は GET /user）からアカウント名を読む。
// GitHub は login、GitLab は username。読めない・形に合わない・- で始まるときは空（マーカーに残すだけなので止めない）。
func Account(kind string, data []byte) string {
	v, err := Decode(data)
	if err != nil {
		return ""
	}
	name := "username"
	if kind == "github" {
		name = "login"
	}
	got, err := Field(v, name)
	if err != nil || !Truthy(got) {
		return ""
	}
	who := strings.ReplaceAll(Text(got), "\r", "")
	if !AccountForm.MatchString(who) || strings.HasPrefix(who, "-") {
		return ""
	}
	return who
}
