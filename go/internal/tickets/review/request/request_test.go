package request

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/host"
)

func defaultConf(t *testing.T) *config.Settings {
	t.Helper()
	conf, _ := config.Load(t.TempDir())
	return conf
}

func obj(kv ...any) *pyjson.Object { return pyjson.NewObject(kv...) }

// 取得した結果を拒むときは、読めなかった理由の 1 行だけを出す。「結果にマージリクエストが無い」は、読めた結果に
// mr が無いときだけ（読めなかったときにまで出すと、理由の後ろに誤った 2 行目が付く）。
func TestMatching(t *testing.T) {
	dir := t.TempDir()
	n := 0
	write := func(text string) string {
		n++
		path := filepath.Join(dir, fmt.Sprintf("result%d.json", n))
		if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
		return path
	}
	const noMR = "ccnavi: 結果にマージリクエストがありません。\n"
	cases := []struct {
		name, path string
		mark       *pyjson.Object
		ok         bool
		said       string
	}{
		{"空のパス", "", obj(), false, "ccnavi: --result <json> を指定してください。リモートから取得した結果は sh が渡します。\n"},
		{"エラーの結果", write(`{"error": "boom"}`), obj(), false, "ccnavi: リモートを読めていない: boom\n"},
		{"mr が無い", write(`{"host": "github"}`), obj(), false, noMR},
		{"同じ mr", write(`{"host": "github", "mr": {"number": 7, "url": "u"}}`), obj("host", "github", "mr", 7), true, ""},
		{"違う mr", write(`{"host": "github", "mr": {"number": 8, "url": "u"}}`), obj("host", "github", "mr", 7), false,
			"ccnavi: 依頼したマージリクエスト（7）と結果のもの（8）が違う\n"},
	}
	for _, c := range cases {
		var stderr bytes.Buffer
		_, ok := Matching(&stderr, c.path, c.mark)
		if ok != c.ok || stderr.String() != c.said {
			t.Errorf("%s: ok=%v said=%q", c.name, ok, stderr.String())
		}
	}
	var stderr bytes.Buffer
	if _, ok := Matching(&stderr, filepath.Join(dir, "missing.json"), obj()); ok ||
		!strings.HasPrefix(stderr.String(), "ccnavi: リモートを読めていない: ") || strings.Count(stderr.String(), "\n") != 1 {
		t.Errorf("読めないファイル: %q", stderr.String())
	}
}

// 照合できない依頼の記録（ホストか番号が無い）では通さない（別の MR のスレッドでレビュー済みにできる）。
func TestMatchingProblems(t *testing.T) {
	result := &host.Result{Host: host.GitHub, MR: &host.MergeRequest{Number: 7, URL: "u"}}
	for _, mark := range []*pyjson.Object{obj("mr", 7), obj("host", "github"), obj()} {
		if got := MatchingProblems(result, mark); len(got) != 1 || !strings.Contains(got[0], "依頼し直してください") {
			t.Errorf("%v: %v", mark.Keys(), got)
		}
	}
	if got := MatchingProblems(result, obj("host", "github", "mr", 7)); got != nil {
		t.Errorf("合う記録: %v", got)
	}
	if got := MatchingProblems(result, obj("host", "github", "mr", 8)); len(got) == 0 {
		t.Error("違う番号が通った")
	}
	if got := MatchingProblems(result, obj("host", "gitlab", "mr", 7)); len(got) != 1 ||
		got[0] != "ccnavi: 依頼したホスト（gitlab）と結果のホスト（github）が違う" {
		t.Errorf("違うホスト: %v", got)
	}
	// 小数の 7.0 は Python の str で "7.0" になり、7 と合わない。
	if got := MatchingProblems(result, obj("host", "github", "mr", 7.0)); len(got) != 1 {
		t.Errorf("7.0: %v", got)
	}
}

func TestThreadLabel(t *testing.T) {
	cases := []struct {
		path string
		line int
		body string
		want string
	}{
		{"a.py", 3, "本文\n2 行目", "u1 a.py:3 本文"},
		{"", 0, "本文\n2 行目", "u1 本文"},
		{"a.py", 0, "本文\n2 行目", "u1 a.py 本文"},
		{"", 0, "  \n", "u1"},
		{"", 0, strings.Repeat("あ", 130), "u1 " + strings.Repeat("あ", 120)},
	}
	for _, c := range cases {
		if got := ThreadLabel(host.Thread{URL: "u1", Path: c.path, Line: c.line, Body: c.body}); got != c.want {
			t.Errorf("%q: %q", c.want, got)
		}
	}
}

func TestIsSHA(t *testing.T) {
	cases := map[string]bool{
		strings.Repeat("a", 40): true, "abc1234": true, "abc123": false, "HEAD": false, "@": false,
		"-x": false, strings.Repeat("A", 40): false, "abc1234\n": true, "abc1234\n\n": false,
	}
	for value, want := range cases {
		if isSHA(value) != want {
			t.Errorf("%q: %v", value, !want)
		}
	}
}

// 依頼の後に、ユーザが見るものが動いたか。ccnavi 自身の置き場だけの変更は数えない。読めなかったら動いたと数える。
func TestMovedDetail(t *testing.T) {
	conf := defaultConf(t)
	mark := obj("head", strings.Repeat("a", 40))
	b := strings.Repeat("b", 40)
	cases := []struct {
		name    string
		mark    *pyjson.Object
		head    string
		changed []string
		listed  bool
		project bool
		text    string
		kind    MovedKind
	}{
		{"同じ HEAD", mark, strings.Repeat("a", 40), nil, false, false, "", ""},
		{"承認済みの置き場だけ", mark, b, []string{".ccnavi/approved/x.md"}, true, false, "", ""},
		{"提案の置き場だけ", mark, b, []string{"wip/proposals/todo/x.md"}, true, false, "", ""},
		{"読めない", mark, b, nil, false, false, "依頼の後に親の HEAD が動いている", KindMoved},
		{"コード", mark, b, []string{"src/app.py"}, true, false, "依頼の後に親の HEAD が動いている", KindMoved},
		{"置き場と外", mark, b, []string{".ccnavi/approved/x", "README.md"}, true, false, "依頼の後に親の HEAD が動いている", KindMoved},
		{"記録が無い", obj(), b, nil, true, false, "記録されていない", ""},
		{"sha でない記録", obj("head", "HEAD"), b, nil, true, false, "動いている", KindMoved},
		{"ミラーだけ（プロジェクト）", mark, b, []string{".ccnavi/config/common/rules.yml", "skills/process-test/SKILL.md"}, true, true, "動いている", KindMirror},
		{"ミラーと外（プロジェクト）", mark, b, []string{".ccnavi/config/common/rules.yml", "src/keep.py"}, true, true, "動いている", KindMoved},
		{"ミラーだけ（ワークスペース）", mark, b, []string{".ccnavi/config/common/rules.yml"}, true, false, "動いている", KindMoved},
		{"読めない（プロジェクト）", mark, b, nil, false, true, "動いている", KindMoved},
	}
	for _, c := range cases {
		got := MovedDetail(conf, c.mark, c.head, c.changed, c.listed, c.project)
		if c.text == "" && got.Text != "" || !strings.Contains(got.Text, c.text) || got.Kind != c.kind {
			t.Errorf("%s: %+v", c.name, got)
		}
	}
	if got := MovedDetail(conf, mark, b, nil, false, false).Text; got != "依頼の後に親の HEAD が動いている（依頼時 aaaaaaaaaaaa、いま bbbbbbbbbbbb）" {
		t.Errorf("文: %q", got)
	}
}

// 止めたあとの説明は固定の文だけ。何が変わったかは断定しない。push されていないだけ（kind が空）なら足さない。
func TestMovedExplanation(t *testing.T) {
	fixed := "依頼時のコミットといまの HEAD が違う。何が変わったかユーザに確かめてもらい、依頼し直す。"
	cases := []struct {
		moved Moved
		want  []string
	}{
		{Moved{"x", KindMoved}, []string{fixed}},
		{Moved{"x", KindMirror}, []string{fixed, msgExplainMirror}},
		{Moved{Text: "親ブランチの HEAD が push されていない"}, nil},
		{Moved{}, nil},
	}
	for _, c := range cases {
		if got := MovedExplanation(c.moved); !reflect.DeepEqual(got, c.want) {
			t.Errorf("%+v: %v", c.moved, got)
		}
	}
	if !strings.Contains(msgExplainMirror, "ミラーのずれ") || !strings.Contains(msgExplainMirror, "再 `review-request`") {
		t.Error("ミラーの文")
	}
}

func TestReviewedMark(t *testing.T) {
	cases := []struct {
		account, route, stamp, want string
	}{
		{"", "", "", `{"mr": 7, "accepted": ["u1"]}`},
		{"me", "cli", "", `{"mr": 7, "accepted": ["u1"], "actor": "me", "via": "cli"}`},
		{"", "", "T", `{"mr": 7, "accepted": ["u1"], "at": "T"}`},
	}
	for _, c := range cases {
		got, _ := pyjson.Dumps(ReviewedMark(7, []string{"u1"}, c.account, c.route, c.stamp), pyjson.Options{})
		if got != c.want {
			t.Errorf("%+v: %s", c, got)
		}
	}
}

func TestShlexQuote(t *testing.T) {
	for in, want := range map[string]string{"": "''", "/a/b-c_d.e": "/a/b-c_d.e", "a b": "'a b'", "it's": `'it'"'"'s'`, "日本": "'日本'"} {
		if got := shlexQuote(in); got != want {
			t.Errorf("%q: %q", in, got)
		}
	}
}
