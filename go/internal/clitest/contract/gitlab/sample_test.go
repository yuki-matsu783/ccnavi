package gitlab

import (
	"bytes"
	"encoding/json"
	"flag"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	cs "ccnavi/internal/harness/contractshape"
)

var updateSample = flag.Bool("update-sample", false, "recorded.sample.json を、いまの模擬の応答から作り直す")

func reads(t testing.TB) map[string][]string { t.Helper(); return cs.ReadsOf(loadFake(t)) }

func TestSampleAgreesWithTheFake(t *testing.T) {
	t.Parallel()
	got := cs.CompareAll(loadSample(t).Requests, run(t, loadFake(t), nil), nil)
	if len(got) != 0 {
		t.Errorf("サンプルと模擬が食い違う: %v", got)
	}
}

// 模擬を変えたら `go test ./internal/clitest/contract/gitlab -run TestSampleIsRegeneratedWithoutChange -update-sample` で作り直す。
func TestSampleIsRegeneratedWithoutChange(t *testing.T) {
	fresh, err := cs.SampleDocument(run(t, loadFake(t), nil))
	if err != nil {
		t.Fatal(err)
	}
	if *updateSample {
		var buf bytes.Buffer
		enc := json.NewEncoder(&buf)
		enc.SetEscapeHTML(false)
		enc.SetIndent("", " ")
		if err := enc.Encode(fresh); err != nil {
			t.Fatal(err)
		}
		if err := cs.WriteAtomic(filepath.Join(cs.DataDir(), "recorded.sample.json"), buf.String()); err != nil {
			t.Fatal(err)
		}
		return
	}
	if !reflect.DeepEqual(loadSample(t), fresh) {
		t.Error("模擬を変えたのでサンプルが古い。-update-sample で作り直す")
	}
}

func TestSampleSaysWhatItIsAndCoversEveryRequest(t *testing.T) {
	t.Parallel()
	doc := loadSample(t)
	if doc.Source != "sample" || !slices.Contains(doc.NotRecorded, "headers") {
		t.Errorf("source = %q, not_recorded = %v", doc.Source, doc.NotRecorded)
	}
	fake := loadFake(t)
	var names []string
	for _, r := range fake {
		names = append(names, r.Name)
	}
	sameNames(t, "サンプルの要求", names, keysOf(doc.Requests))
	if problems := cs.ReadsProblems(fake, doc.Requests); len(problems) != 0 {
		t.Errorf("reads のキーがサンプルに無い: %v", problems)
	}
}

// 1 変異 1 種類。期待したキーと種類が、ちょうど 1 件だけ出る。
func TestEachMutationIsReportedAsExactlyOneDifference(t *testing.T) {
	t.Parallel()
	asMap := func(body any) map[string]any { return body.(map[string]any) }
	fake := func(name string, change func(any) any) func(string, any) any {
		return func(n string, body any) any {
			if n == name {
				return change(body)
			}
			return body
		}
	}
	without := func(key string) func(any) any {
		return func(body any) any {
			out := map[string]any{}
			for k, v := range asMap(body) {
				if k != key {
					out[k] = v
				}
			}
			return out
		}
	}
	with := func(key string, value any) func(any) any {
		return func(body any) any {
			out := shallow(asMap(body))
			out[key] = value
			return out
		}
	}
	dropFromFirst := func(body any) any {
		list := body.([]any)
		first := shallow(asMap(list[0]))
		delete(first, "body")
		return append([]any{first}, list[1:]...)
	}
	stringIid := func(body any) any {
		m := shallow(asMap(body))
		m["iid"] = "1"
		return m
	}
	// サンプルの側を書き換える。
	sample := func(mutate func(doc *cs.Document)) *cs.Document {
		doc := clone(t, loadSample(t))
		mutate(&doc)
		return &doc
	}
	moreRead := copyReads(reads(t))
	moreRead["mr_get"] = append(slices.Clone(moreRead["mr_get"]), "more")

	cases := []struct {
		name   string
		tamper func(string, any) any
		doc    *cs.Document
		reads  map[string][]string
		want   map[string][]cs.Diff
	}{
		{"fake loses a key", fake("mr_get", without("title")), nil, nil, map[string][]cs.Diff{"mr_get": {diff("title", "missing")}}},
		{"fake gains a key", fake("mr_get", with("extra_key", 1)), nil, nil, map[string][]cs.Diff{"mr_get": {diff("extra_key", "extra")}}},
		{"fake changes a type", fake("mr_get", stringIid), nil, nil, map[string][]cs.Diff{"mr_get": {diff("iid", "type")}}},
		{"fake drops a key in one element", fake("notes_get", dropFromFirst), nil, nil, map[string][]cs.Diff{"notes_get": {diff("[].body", "presence")}}},
		{"sample loses a key", nil, sample(func(d *cs.Document) { delete(d.Requests["mr_get"].Shape.Keys, "title") }), nil,
			map[string][]cs.Diff{"mr_get": {diff("title", "extra")}}},
		{"sample gains a read key", nil, sample(func(d *cs.Document) { d.Requests["mr_get"].Shape.Keys["more"] = &cs.Node{T: []string{"string"}} }), moreRead,
			map[string][]cs.Diff{"mr_get": {diff("more", "missing")}}},
		{"sample has another type", nil, sample(func(d *cs.Document) { d.Requests["mr_get"].Shape.Keys["iid"] = &cs.Node{T: []string{"string"}} }), nil,
			map[string][]cs.Diff{"mr_get": {diff("iid", "type")}}},
		{"sample says optional", nil, sample(func(d *cs.Document) { d.Requests["notes_get"].Shape.Items.Opt = []string{"body"} }), nil,
			map[string][]cs.Diff{"notes_get": {diff("[].body", "presence")}}},
		{"status differs", nil, sample(func(d *cs.Document) {
			d.Requests["error_404"] = cs.Entry{Status: 403, Shape: d.Requests["error_404"].Shape}
		}), nil,
			map[string][]cs.Diff{"error_404": {diff("$status", "status")}}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			recorded := loadSample(t).Requests
			if c.doc != nil {
				recorded = c.doc.Requests
			}
			readKeys := reads(t)
			if c.reads != nil {
				readKeys = c.reads
			}
			got := cs.CompareAll(recorded, run(t, loadFake(t), c.tamper), readKeys)
			if !reflect.DeepEqual(got, c.want) {
				t.Errorf("got %v, want %v", got, c.want)
			}
		})
	}
}

func shallow(m map[string]any) map[string]any {
	out := make(map[string]any, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

func copyReads(m map[string][]string) map[string][]string {
	out := make(map[string][]string, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

// reads のキーの親が記録に無いとき、その下の reads は記録に無いと数える。
func TestAReadKeyBelowAMissingParentIsAProblem(t *testing.T) {
	t.Parallel()
	doc := clone(t, loadSample(t))
	delete(doc.Requests["reviewers"].Shape.Items.Keys, "user")
	got := cs.ReadsProblems(loadFake(t), doc.Requests)
	if want := []string{"reviewers: reads の [].user.id が記録に無い"}; !slices.Equal(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
}

func TestNoForbiddenStringInTheRecordingsAndRequests(t *testing.T) {
	t.Parallel()
	forbidden := load[[]cs.Forbidden](t, "forbidden.json")
	texts := dataTexts(t)
	for _, name := range []string{"recorded.sample.json", "requests.json"} {
		if _, ok := texts[name]; !ok {
			t.Fatalf("%s が無い", name)
		}
	}
	for name, text := range texts {
		if hits := cs.ScanForbidden(text, forbidden); len(hits) != 0 {
			t.Errorf("%s: 禁止文字列に当たった %v", name, hits)
		}
	}
}

// 禁止文字列の 1 つずつが、サンプルに混ぜると捕まる（検査が空振りしていない確認）。
func TestEachForbiddenStringIsCaughtWhenMixedIntoTheSample(t *testing.T) {
	t.Parallel()
	forbidden := load[[]cs.Forbidden](t, "forbidden.json")
	samples := map[string]string{
		"glpat-": "glpat-abcdef", "gldt-": "gldt-abcdef", "glptt-": "glptt-abcdef", "glrt-": "glrt-abcdef",
		"PRIVATE-TOKEN": "PRIVATE-TOKEN: x", "Bearer": "Bearer abc", "メールの形": "a.b@example.com",
		"root": "root", "ccnavi-reviewer": "ccnavi-reviewer", "p551-": "p551-abcd1234",
	}
	var names []string
	for _, f := range forbidden {
		names = append(names, f.Name)
	}
	sameNames(t, "forbidden.json の名前", names, keysOf(samples))
	base := dataTexts(t)["recorded.sample.json"]
	for name, word := range samples {
		t.Run(name, func(t *testing.T) {
			mixed := strings.Replace(base, `"version": 1`, `"version": 1, "leak": "`+word+`"`, 1)
			if mixed == base {
				t.Fatal("混ぜられなかった")
			}
			if got := cs.ScanForbidden(mixed, forbidden); !slices.Equal(got, []string{name}) {
				t.Errorf("got %v, want [%s]", got, name)
			}
		})
	}
	// 語の境界: root_ref のようなキーは捕まえない。
	if got := cs.ScanForbidden(`{"root_ref": 1, "groot": 2}`, forbidden); len(got) != 0 {
		t.Errorf("境界を越えて捕まえた: %v", got)
	}
}
