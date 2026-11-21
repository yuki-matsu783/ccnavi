package contractshape

import (
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
)

// val は JSON の文字列を値にする（1 と 1.5 の区別を保つため、数は字のまま読む）。
func val(t testing.TB, text string) any {
	t.Helper()
	var v any
	if err := DecodeJSON([]byte(text), &v); err != nil {
		t.Fatalf("%s: %v", text, err)
	}
	return v
}

func shapeOf(t testing.TB, text string) *Node { t.Helper(); return Normalize(val(t, text)) }

func diff(t testing.TB, old, new string) []Diff {
	t.Helper()
	return Compare(shapeOf(t, old), shapeOf(t, new))
}

// normalize-cases.json の表は Chrome 拡張の TS も読む。ここと TS で同じ表を読むことで、2 つの正規化が揃う。
func TestNormalizeEveryCaseInTheTable(t *testing.T) {
	t.Parallel()
	var cases []struct {
		Name   string `json:"name"`
		Input  any    `json:"input"`
		Expect *Node  `json:"expect"`
	}
	if err := LoadData("normalize-cases.json", &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) == 0 {
		t.Fatal("表が空")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			t.Parallel()
			if got := Normalize(c.Input); !reflect.DeepEqual(got, c.Expect) {
				t.Errorf("got %+v, want %+v", got, c.Expect)
			}
		})
	}
}

func TestDifferencesAreNamedByKind(t *testing.T) {
	t.Parallel()
	always, sometimes := `[{"a": 1}, {"a": 2}]`, `[{"a": 1}, {}]`
	for _, c := range []struct {
		name, old, new string
		want           []Diff
	}{
		{"same shape with other values", `{"a": 1, "l": [{"b": "x"}]}`, `{"a": 2, "l": [{"b": "y"}]}`, nil},
		{"missing", `{"a": 1, "b": 1}`, `{"a": 1}`, []Diff{{"b", "missing"}}},
		{"extra", `{"a": 1}`, `{"a": 1, "b": 1}`, []Diff{{"b", "extra"}}},
		{"type string", `{"a": 1}`, `{"a": "1"}`, []Diff{{"a", "type"}}},
		{"type number", `{"a": 1}`, `{"a": 1.5}`, []Diff{{"a", "type"}}},
		{"presence", always, sometimes, []Diff{{"[].a", "presence"}}},
	} {
		t.Run(c.name, func(t *testing.T) {
			if got := diff(t, c.old, c.new); !slices.Equal(got, c.want) {
				t.Errorf("got %v, want %v", got, c.want)
			}
		})
	}
}

func TestNestedKeyIsNamedWithItsPathAndAnEmptyArrayIsNotCompared(t *testing.T) {
	t.Parallel()
	got := diff(t, `{"n": [{"author": {"id": 1}}]}`, `{"n": [{"author": {"id": "1"}}]}`)
	if want := []Diff{{"n[].author.id", "type"}}; !slices.Equal(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
	if got := diff(t, `{"l": []}`, `{"l": [{"a": 1}]}`); len(got) != 0 {
		t.Errorf("空の配列は比べない: %v", got)
	}
}

func TestStatusAndMissingRequestAreReported(t *testing.T) {
	t.Parallel()
	one := Entry{200, shapeOf(t, `{"a": 1}`)}
	got := CompareAll(map[string]Entry{"x": one, "y": one}, map[string]Entry{"x": {404, one.Shape}, "z": one}, nil)
	want := map[string][]Diff{"x": {{"$status", "status"}}, "y": {{"$", "missing"}}, "z": {{"$", "extra"}}}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
}

func TestAllowRemovesTheAllowedDifferenceAndKeepsTheRest(t *testing.T) {
	t.Parallel()
	found := map[string][]Diff{"mr": {{"a", "missing"}, {"b", "extra"}}}
	entry := func(mutate func(*AllowEntry)) AllowEntry {
		e := AllowEntry{Request: "mr", Key: "a", Kind: "missing", Reason: "理由"}
		mutate(&e)
		return e
	}
	left, problems := ApplyAllow(found, []AllowEntry{entry(func(*AllowEntry) {})})
	if want := map[string][]Diff{"mr": {{"b", "extra"}}}; !reflect.DeepEqual(left, want) || len(problems) != 0 {
		t.Errorf("left = %v, problems = %v", left, problems)
	}

	// 問題になる項目。`*` はワイルドカードではなく、kind が違えば差は残る。
	for _, c := range []struct {
		name  string
		found map[string][]Diff
		entry AllowEntry
		text  string
	}{
		{"empty reason", found, entry(func(e *AllowEntry) { e.Reason = " " }), "理由が空"},
		{"bad kind", map[string][]Diff{}, entry(func(e *AllowEntry) { e.Kind = "other" }), "違いの種類"},
		{"stale", found, entry(func(e *AllowEntry) { e.Key = "zzz" }), "古い許可"},
		{"star is not a wildcard", found, entry(func(e *AllowEntry) { e.Key = "*" }), "古い許可"},
	} {
		t.Run(c.name, func(t *testing.T) {
			_, problems := ApplyAllow(c.found, []AllowEntry{c.entry})
			if !slices.ContainsFunc(problems, func(p string) bool { return strings.Contains(p, c.text) }) {
				t.Errorf("problems = %v, want %q", problems, c.text)
			}
		})
	}
	left, _ = ApplyAllow(found, []AllowEntry{entry(func(e *AllowEntry) { e.Key = "*" })})
	if !reflect.DeepEqual(left, found) {
		t.Errorf("`*` で差が消えた: %v", left)
	}
	left, _ = ApplyAllow(found, []AllowEntry{entry(func(e *AllowEntry) { e.Kind = "extra" })})
	if !slices.Contains(left["mr"], Diff{"a", "missing"}) {
		t.Errorf("kind が違うのに許された: %v", left)
	}
}

func TestReadsSelectWhichKeysAreCompared(t *testing.T) {
	t.Parallel()
	found := func(t *testing.T, real, fake string, reads []string) map[string][]Diff {
		recorded := map[string]Entry{"x": {200, shapeOf(t, real)}}
		actual := map[string]Entry{"x": {200, shapeOf(t, fake)}}
		var r map[string][]string
		if reads != nil {
			r = map[string][]string{"x": reads}
		}
		return CompareAll(recorded, actual, r)
	}
	for _, c := range []struct {
		name, real, fake string
		reads            []string
		want             map[string][]Diff
	}{
		{"a read key the fake lacks", `{"a": 1, "b": 1}`, `{"b": 1}`, []string{"a"}, map[string][]Diff{"x": {{"a", "missing"}}}},
		{"a read key of another type", `{"a": 1}`, `{"a": "1"}`, []string{"a"}, map[string][]Diff{"x": {{"a", "type"}}}},
		{"a key that is not read is not compared", `{"a": 1, "b": 1}`, `{"a": 1}`, []string{"a"}, map[string][]Diff{}},
		{"a key only the fake has is reported", `{"a": 1}`, `{"a": 1, "b": 1}`, []string{"a"}, map[string][]Diff{"x": {{"b", "extra"}}}},
		{"no reads means all keys", `{"a": 1, "b": 1}`, `{"a": 1}`, nil, map[string][]Diff{"x": {{"b", "missing"}}}},
		{"a missing parent covers the read key below", `{"o": {"q": 1}}`, `{}`, []string{"o.q"}, map[string][]Diff{"x": {{"o", "missing"}}}},
		{"a read array does not cover its elements", `{"l": [{"q": 1}]}`, `{"l": [{"z": 1}]}`, []string{"l"}, map[string][]Diff{"x": {{"l[].z", "extra"}}}},
		{"a read element key is compared", `{"l": [{"q": 1}]}`, `{"l": [{"z": 1}]}`, []string{"l[].q"},
			map[string][]Diff{"x": {{"l[].q", "missing"}, {"l[].z", "extra"}}}},
	} {
		t.Run(c.name, func(t *testing.T) {
			if got := found(t, c.real, c.fake, c.reads); !reflect.DeepEqual(got, c.want) {
				t.Errorf("got %v, want %v", got, c.want)
			}
		})
	}

	// ステータスと、記録の無い要求は reads に関わらず出る。
	recorded := map[string]Entry{"x": {200, shapeOf(t, `{"a": 1}`)}}
	reads := map[string][]string{"x": {}}
	if got, want := CompareAll(recorded, map[string]Entry{"x": {404, recorded["x"].Shape}}, reads), map[string][]Diff{"x": {{"$status", "status"}}}; !reflect.DeepEqual(got, want) {
		t.Errorf("status: got %v, want %v", got, want)
	}
	if got, want := CompareAll(recorded, map[string]Entry{}, reads), map[string][]Diff{"x": {{"$", "missing"}}}; !reflect.DeepEqual(got, want) {
		t.Errorf("missing request: got %v, want %v", got, want)
	}
}

func TestKeyPathsNodeAtAndReadsProblems(t *testing.T) {
	t.Parallel()
	node := shapeOf(t, `{"a": {"b": 1}, "l": [{"c": "x"}]}`)
	paths := KeyPaths(node)
	for _, k := range []string{"a", "a.b", "l", "l[]", "l[].c"} {
		if !paths[k] {
			t.Errorf("KeyPaths に %s が無い: %v", k, paths)
		}
	}
	if len(paths) != 5 {
		t.Errorf("KeyPaths = %v", paths)
	}
	for key, want := range map[string]string{"a.b": "integer", "l[].c": "string"} {
		if n := NodeAt(node, key); n == nil || n.T[0] != want {
			t.Errorf("NodeAt(%s) = %v, want %s", key, n, want)
		}
	}
	if NodeAt(node, "a.zzz") != nil {
		t.Error("無いキーの形が返った")
	}
	rootList := shapeOf(t, `[{"id": 1}]`)
	if p := KeyPaths(rootList); len(p) != 2 || !p["[]"] || !p["[].id"] {
		t.Errorf("根が配列の KeyPaths = %v", p)
	}
	if n := NodeAt(rootList, "[].id"); n == nil || n.T[0] != "integer" {
		t.Errorf("根が配列の NodeAt = %v", n)
	}

	requests := []Request{{Name: "x", Reads: []string{"a", "l[].c", "gone"}}}
	doc := map[string]Entry{"x": {200, shapeOf(t, `{"a": 1, "l": [{"c": 1}]}`)}}
	if got, want := ReadsProblems(requests, doc), []string{"x: reads の gone が記録に無い"}; !slices.Equal(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
	if got, want := ReadsProblems(requests, nil), []string{"x: 記録に無い要求"}; !slices.Equal(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
}

func TestSubstituteReplacesInNestedValuesAndRejectsUnknownNames(t *testing.T) {
	t.Parallel()
	got, err := Substitute(val(t, `{"p": "/a/${project}/${mr.iid}", "l": ["x${mr.iid}"], "n": 1}`), map[string]string{"project": "P", "mr.iid": "3"})
	if err != nil {
		t.Fatal(err)
	}
	if want := val(t, `{"p": "/a/P/3", "l": ["x3"], "n": 1}`); !reflect.DeepEqual(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
	if _, err := Substitute("${nope}", nil); err == nil {
		t.Error("知らない名前が通った")
	}
}

func TestWriteAtomicReplacesTheFileAndLeavesNoTemporaryFile(t *testing.T) {
	t.Parallel()
	dir := t.TempDir()
	path := filepath.Join(dir, "recorded.json")
	if err := os.WriteFile(path, []byte("old\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := WriteAtomic(path, "new\n"); err != nil {
		t.Fatal(err)
	}
	if data, _ := os.ReadFile(path); string(data) != "new\n" {
		t.Errorf("内容 = %q", data)
	}
	if names := listNames(t, dir); !slices.Equal(names, []string{"recorded.json"}) {
		t.Errorf("残った物 = %v", names)
	}
}

// 置き先が置き換えられない（中身のあるディレクトリ）ときは、失敗を返し、元の物を変えず、一時ファイルも残さない。
func TestWriteAtomicKeepsTheOldFileWhenThePlacementFails(t *testing.T) {
	t.Parallel()
	dir := t.TempDir()
	path := filepath.Join(dir, "recorded.json")
	if err := os.MkdirAll(filepath.Join(path, "inner"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := WriteAtomic(path, "new\n"); err == nil {
		t.Fatal("置けないのに成功した")
	}
	if names := listNames(t, dir); !slices.Equal(names, []string{"recorded.json"}) {
		t.Errorf("残った物 = %v", names)
	}
	if _, err := os.Stat(filepath.Join(path, "inner")); err != nil {
		t.Errorf("元の物が変わった: %v", err)
	}
}

func listNames(t testing.TB, dir string) []string {
	t.Helper()
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, e := range entries {
		names = append(names, e.Name())
	}
	return names
}
