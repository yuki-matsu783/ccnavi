package host

import (
	"errors"
	"fmt"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

// Kind はホストの種類。Result.Host は sh の分類で、見本の "fixture" のようにこの 2 つ以外も来る。
type Kind string

const (
	GitHub Kind = "github"
	GitLab Kind = "gitlab"
)

const (
	msgUnreadable = "結果を読めない (%s)"
	msgNotJSON    = "結果が JSON として読めない (%s)"
	msgNotMap     = "結果の最上位がマップではない"
)

// Thread はレビューのスレッド 1 つ。本文・URL・位置・書き手は代表の note（最初のコメント）から取る。
type Thread struct {
	ID        string
	Resolved  bool
	URL       string
	Path      string
	Line      int
	Body      string
	CreatedAt string
	// author は最初のコメントを書いたアカウント。GitLab から取得した結果にだけ入る。
	author string
	// LastComment* は最後のコメント（system note を除く）。受け入れた後に付いたコメント（再提起）を
	// 見分けるために取る。取れなければ空。
	LastCommentID     string
	LastCommentAt     string
	LastCommentAuthor string
	// own は note が全部 ccnavi の目印で始まるか。GitLab の結果だけが持ち、JSON の真偽のときだけ入る。
	// nil なら目印と書き手で見る。ユーザが ccnavi の依頼コメントに返信したスレッドは偽。
	own *bool
}

// Review はレビュー 1 件。author は同じレビュアーの最新だけを数えるために要る。
type Review struct {
	State       string
	URL         string
	submittedAt string
	author      string
}

// MergeRequest は MR（PR）。Draft は sh が取得した結果にだけ入る（分からなければ nil）。
type MergeRequest struct {
	Number int
	URL    string
	Draft  *bool
}

// Result は sh がリモートから取得した結果。形は 1 つで、Host と mr は常に要る。confirm と --reviewed は
// Threads と Reviews、requested は投稿の URL・CreatedAt・Author を見る。読めなければ Error に理由。
type Result struct {
	Host      Kind
	MR        *MergeRequest
	Threads   []Thread
	Reviews   []Review
	URL       string
	CreatedAt string
	// Author は投稿したアカウント（requested の結果だけ）。
	Author string
	Error  string
}

// LoadResult は `--result` のファイルを読む。読めなければ Error に理由を入れて返す。
func LoadResult(path string) Result {
	data, err := fsio.ReadJSON(path)
	if err != nil {
		if isDecodeError(err) {
			return Result{Error: fmt.Sprintf(msgNotJSON, err)}
		}
		return Result{Error: fmt.Sprintf(msgUnreadable, err)}
	}
	return ResultOf(data)
}

func isDecodeError(err error) bool {
	var j *pyjson.DecodeError
	var u *fsio.DecodeError
	return errors.As(err, &j) || errors.As(err, &u) ||
		errors.Is(err, pyjson.ErrTooDeep) || errors.Is(err, pyjson.ErrIntDigits)
}

// ResultOf は読んだ JSON の値から組む。Chrome 拡張は sh と同じ形の結果を組んで渡す。マップでない項目は
// 読み飛ばし、値は文字列・整数・真偽に整える。
func ResultOf(data any) Result {
	top, ok := data.(*pyjson.Object)
	if !ok {
		return Result{Error: msgNotMap}
	}
	// sh がホストから取れなかったときは error だけの JSON を渡す。その文をそのまま持ち帰る。
	if e := top.Value("error"); pyfmt.Truthy(e) {
		return Result{Error: pyfmt.Str(e)}
	}
	// 先頭の欄（ホスト・URL・作成時刻・作者）を読む。欄の型が違っても落とさず、文字列に整える。
	r := Result{
		Host:      Kind(text(top.Value("host"))),
		URL:       text(top.Value("url")),
		CreatedAt: text(top.Value("created_at")),
		Author:    text(top.Value("author")),
	}
	// MR の番号が 0 や無しなら、MR は無いものとして nil にする。
	if mr, ok := top.Value("mr").(*pyjson.Object); ok && pyfmt.Truthy(mr.Value("number")) {
		number, _ := pyfmt.IntOf(mr.Value("number"))
		r.MR = &MergeRequest{Number: number, URL: text(mr.Value("url")), Draft: boolean(mr.Value("draft"))}
	}
	// レビューのスレッドと、レビューの判定を順に読む。
	for _, t := range objects(top.Value("threads")) {
		line := 0
		if v := t.Value("line"); pyfmt.Truthy(v) {
			line, _ = pyfmt.IntOf(v)
		}
		r.Threads = append(r.Threads, Thread{
			ID:                text(t.Value("id")),
			Resolved:          pyfmt.Truthy(t.Value("resolved")),
			URL:               text(t.Value("url")),
			Path:              text(t.Value("path")),
			Line:              line,
			Body:              text(t.Value("body")),
			CreatedAt:         text(t.Value("created_at")),
			author:            text(t.Value("author")),
			LastCommentID:     text(t.Value("last_id")),
			LastCommentAt:     text(t.Value("last_at")),
			LastCommentAuthor: text(t.Value("last_author")),
			own:               boolean(t.Value("own")),
		})
	}
	for _, v := range objects(top.Value("reviews")) {
		r.Reviews = append(r.Reviews, Review{
			State: text(v.Value("state")), URL: text(v.Value("url")),
			submittedAt: text(v.Value("submitted_at")), author: text(v.Value("author")),
		})
	}
	return r
}

// text は値を文字列にする。偽の値（nil・空・0 など）は空文字列。
func text(v any) string {
	if !pyfmt.Truthy(v) {
		return ""
	}
	return pyfmt.Str(v)
}

// boolean は JSON の真偽だけを受ける（"true"・1 などは nil）。
func boolean(v any) *bool {
	if b, ok := v.(bool); ok {
		return &b
	}
	return nil
}

// objects は列の中のマップだけを返す。列でなければ空。
func objects(v any) []*pyjson.Object {
	items, _ := v.([]any)
	var out []*pyjson.Object
	for _, item := range items {
		if o, ok := item.(*pyjson.Object); ok {
			out = append(out, o)
		}
	}
	return out
}
