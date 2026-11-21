// このファイルは T1 の ticketkit（ブランチ p3-t1 の go/internal/harness/ticketkit）の写し。ticketkit を取り込んだら、写しを消して import に置き換える。
// 写しとの違いは、Cached が写しを取らず毎回組むこと（kit_tree_test.go）。

package flow

import (
	"encoding/json"
	"path/filepath"
	"strconv"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
)

// Fixture はリモートの代わりの結果ファイル（JSON）。sh がリモートから取ってくる形そのもので、テストはネットワークに出ないので、
// sh の代わりにこのファイルを --result で渡す。
type Fixture struct {
	T    testing.TB
	Path string
}

// Thread は結果ファイルのレビューのスレッド 1 件。
type Thread struct {
	ID        string `json:"id"`
	Resolved  bool   `json:"resolved"`
	URL       string `json:"url"`
	Path      string `json:"path,omitempty"`
	Line      int    `json:"line,omitempty"`
	Body      string `json:"body"`
	CreatedAt string `json:"created_at,omitempty"`
}

// Review は結果ファイルのレビュー 1 件。
type Review struct {
	State       string `json:"state"`
	Author      string `json:"author,omitempty"`
	SubmittedAt string `json:"submitted_at,omitempty"`
	URL         string `json:"url,omitempty"`
}

// Data は結果ファイルの中身。
func (f Fixture) Data() map[string]any { return ReadJSON(f.T, f.Path) }

// Update は結果ファイルを読み、fn で書き換えて書き戻す。
func (f Fixture) Update(fn func(data map[string]any)) {
	f.T.Helper()
	data := f.Data()
	fn(data)
	raw, err := json.Marshal(data)
	if err != nil {
		f.T.Fatal(err)
	}
	Write(f.T, f.Path, string(raw))
}

// SetThreads はスレッドを差し替える。
func (f Fixture) SetThreads(threads ...Thread) {
	f.T.Helper()
	f.Update(func(d map[string]any) { d["threads"] = toAny(f.T, threads) })
}

// SetReviews はレビューを差し替える。
func (f Fixture) SetReviews(reviews ...Review) {
	f.T.Helper()
	f.Update(func(d map[string]any) { d["reviews"] = toAny(f.T, reviews) })
}

// Comments は投稿されたコメントの数。
func (f Fixture) Comments() []map[string]any {
	f.T.Helper()
	list, _ := f.Data()["comments"].([]any)
	out := make([]map[string]any, len(list))
	for i, c := range list {
		out[i], _ = c.(map[string]any)
	}
	return out
}

func toAny(t testing.TB, v any) any {
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	var out any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

// Fixture は Remote が置く結果ファイル（置いてあるものを指す）。
func (tr *Tree) Fixture() Fixture {
	return Fixture{T: tr.T, Path: filepath.Join(tr.Root, "fixture.json")}
}

// Remote は origin（bare）と、合流して push した親ブランチを作り、結果ファイルを返す。merge の子（nil なら
// i0001-01-01 と i0001-01-02）を親のブランチへ合流し、merged_sha を記録する。
func (tr *Tree) Remote(merge []string) Fixture {
	tr.T.Helper()
	if merge == nil {
		merge = []string{"i0001-01-01", "i0001-01-02"}
	}
	bare := filepath.Join(tr.Root, "origin.git")
	tr.Git(tr.Root, "init", "--quiet", "--bare", bare)
	tr.Git(tr.Root, "remote", "add", "origin", bare)
	for _, child := range merge {
		tr.Merge(child)
	}
	tr.Parent("push", "--quiet", "origin", ParentName)
	f := tr.Fixture()
	Write(tr.T, f.Path, `{"host": "fixture", "mr": {"number": 7, "url": "https://example/mr/7"}}`)
	return f
}

// Merge は子を親のブランチへ合流し、merged_sha を記録する（push はしない）。
func (tr *Tree) Merge(child string) {
	tr.T.Helper()
	tr.Parent("merge", "--quiet", "--no-edit", child)
	samples.RecordMerge(tr.T, tr.ParentTree, child)
}

// MergeAndPush は Merge して親のブランチを origin へ push する（Remote の後で足りない子を合流するとき）。
func (tr *Tree) MergeAndPush(child string) {
	tr.T.Helper()
	tr.Merge(child)
	tr.Parent("push", "--quiet", "origin", ParentName)
}

// PhaseArgs は --cwd 親のツリー --phase <n> の並び。
func (tr *Tree) PhaseArgs(phase string) []string {
	return []string{"--cwd", tr.ParentTree, "--phase", phase}
}

// Request は sh の request と同じ 3 段階（prepare → 投稿（結果ファイルのコメントに足す）→ requested）。
func (tr *Tree) Request(f Fixture) run.Result { return tr.RequestPhase(f, "1") }

// RequestPhase は Request のフェーズ指定。
func (tr *Tree) RequestPhase(f Fixture, phase string) run.Result {
	tr.T.Helper()
	body := Write(tr.T, filepath.Join(tr.Root, "body.md"), "見てほしい点\n")
	prepared := tr.Ccnavi(append(tr.PhaseArgs(phase), "--body-file", body, "review", "prepare")...)
	if prepared.Code != 0 {
		return prepared
	}
	// 出力は依頼の本文のパス 1 行だけ。
	text := Read(tr.T, Lines(prepared.Stdout)[0])
	data := f.Data()
	mr, _ := data["mr"].(map[string]any)
	posted, _ := data["comments"].([]any)
	url := mr["url"].(string) + "#note-" + strconv.Itoa(len(posted)+1)
	f.Update(func(d map[string]any) {
		d["comments"] = append(posted, map[string]any{"body": text, "url": url, "created_at": "2026-01-01T00:00:00Z"})
	})
	raw, err := json.Marshal(map[string]any{"host": "fixture", "mr": mr, "url": url, "created_at": "2026-01-01T00:00:00Z"})
	if err != nil {
		tr.T.Fatal(err)
	}
	result := Write(tr.T, filepath.Join(tr.Root, "posted.json"), string(raw))
	return tr.Ccnavi(append(tr.PhaseArgs(phase), "review", "requested", "--result", result)...)
}

// Confirm は `review confirm`（フェーズ 1）。
func (tr *Tree) Confirm(f Fixture) run.Result {
	tr.T.Helper()
	return tr.Ccnavi(append(tr.PhaseArgs("1"), "review", "confirm", "--result", f.Path)...)
}

// Reviewed は、ユーザがターミナルで打つ経路（user reviewed 1 --accept-unresolved）。stdin は 1 件ずつの選択。
func (tr *Tree) Reviewed(f Fixture, stdin string, extra ...string) run.Result {
	tr.T.Helper()
	args := append([]string{"--cwd", tr.ParentTree, "user", "reviewed", "1", "--accept-unresolved", "--result", f.Path}, extra...)
	return tr.Call(Call{Stdin: stdin}, args...)
}

// Decide は `user reviewed 1 --accept-unresolved --result <結果> <extra…>`（stdin なし）。
func (tr *Tree) Decide(f Fixture, extra ...string) run.Result { return tr.Reviewed(f, "", extra...) }

// RequestedMark は依頼のマーカー（フェーズ 1）。
func (tr *Tree) RequestedMark() string {
	return filepath.Join(tr.Approved, "phases", ParentName, "1.requested")
}

// PhaseMark はフェーズのマーカー（phases/i0001/<name>）の場所。
func (tr *Tree) PhaseMark(name string) string {
	return filepath.Join(tr.Approved, "phases", ParentName, name)
}
