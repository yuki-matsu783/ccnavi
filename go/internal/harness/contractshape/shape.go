package contractshape

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"sort"
	"strings"

	"ccnavi/internal/harness/base"
)

// MaxDepth より深い入れ物は object / array に丸める。
const MaxDepth = 5

// droppedKeys は環境ごとに変わる欄。値だけでなく有無も比べない。
var droppedKeys = map[string]bool{
	"avatar_url": true, "web_url": true, "username": true, "email": true,
	"public_email": true, "commit_email": true, "token": true,
}

// IsDropped は、形から落とす欄か。
func IsDropped(key string) bool { return droppedKeys[key] }

var datetime = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$`)

// 違いの種類。
const (
	KindMissing  = "missing"
	KindExtra    = "extra"
	KindType     = "type"
	KindPresence = "presence"
	KindStatus   = "status"
)

var kinds = []string{KindMissing, KindExtra, KindType, KindPresence, KindStatus}

// Node は応答の形。Keys が nil でないとき object、Items が nil でないとき array の要素の形。
type Node struct {
	T     []string
	Keys  map[string]*Node
	Opt   []string
	Items *Node
}

type nodeJSON struct {
	T     []string          `json:"t"`
	Keys  *map[string]*Node `json:"keys,omitempty"`
	Opt   []string          `json:"opt,omitempty"`
	Items *Node             `json:"items,omitempty"`
}

// MarshalJSON は keys が空の object でも "keys": {} を出す（object かどうかを keys の有無で持つため）。
func (n Node) MarshalJSON() ([]byte, error) {
	out := nodeJSON{T: n.T, Opt: n.Opt, Items: n.Items}
	if n.Keys != nil {
		out.Keys = &n.Keys
	}
	return marshal(out, "")
}

// UnmarshalJSON は MarshalJSON の逆。
func (n *Node) UnmarshalJSON(data []byte) error {
	var in nodeJSON
	if err := json.Unmarshal(data, &in); err != nil {
		return err
	}
	*n = Node{T: in.T, Opt: in.Opt, Items: in.Items}
	if in.Keys != nil {
		n.Keys = *in.Keys
	}
	return nil
}

// Entry は 1 つの要求の記録（ステータスと形）。
type Entry struct {
	Status int   `json:"status"`
	Shape  *Node `json:"shape"`
}

// Diff は 1 つの違い。Key は `author.id`・`notes[].system`、根は `$`。
type Diff struct{ Key, Kind string }

func marshal(v any, indent string) ([]byte, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if indent != "" {
		enc.SetIndent("", indent)
	}
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	return bytes.TrimRight(buf.Bytes(), "\n"), nil
}

// DecodeJSON は数を json.Number に読む。
func DecodeJSON(data []byte, into any) error {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	return dec.Decode(into)
}

// Root は手で使う入口（TestMain を持たない実行ファイル）が、リポジトリの根を渡す欄。空ならテストの base.Root。
var Root string

// DataDir は契約のデータ（tests/contract/gitlab）の置き場。
func DataDir() string {
	root := Root
	if root == "" {
		root = base.Root()
	}
	return filepath.Join(root, "tests", "contract", "gitlab")
}

// LoadData は DataDir の JSON を読む。
func LoadData(name string, into any) error {
	data, err := os.ReadFile(filepath.Join(DataDir(), name))
	if err != nil {
		return err
	}
	return DecodeJSON(data, into)
}

func typeOf(value any) string {
	switch v := value.(type) {
	case nil:
		return "null"
	case bool:
		return "boolean"
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return "number"
		}
		return "integer"
	case int, int64:
		return "integer"
	case float64:
		return "number"
	case string:
		if datetime.MatchString(v) {
			return "datetime"
		}
		return "string"
	case map[string]any:
		return "object"
	case []any:
		return "array"
	}
	panic(fmt.Sprintf("JSON の値ではない: %T", value))
}

// Normalize は JSON の値を形にする。
func Normalize(value any) *Node { return normalize(value, 0) }

func normalize(value any, depth int) *Node {
	kind := typeOf(value)
	node := &Node{T: []string{kind}}
	if depth >= MaxDepth {
		return node
	}
	switch v := value.(type) {
	case map[string]any:
		node.Keys = map[string]*Node{}
		for k, sub := range v {
			if !droppedKeys[k] {
				node.Keys[k] = normalize(sub, depth+1)
			}
		}
	case []any:
		for _, element := range v {
			shape := normalize(element, depth+1)
			if node.Items == nil {
				node.Items = shape
			} else {
				node.Items = Merge(node.Items, shape)
			}
		}
	}
	return node
}

func sortedUnion(a, b []string) []string {
	set := map[string]bool{}
	for _, s := range a {
		set[s] = true
	}
	for _, s := range b {
		set[s] = true
	}
	return sortedKeys(set)
}

func sortedKeys[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// Merge は同じ場所の 2 つの形を 1 つにする（配列の要素の和集合）。
func Merge(a, b *Node) *Node {
	out := &Node{T: sortedUnion(a.T, b.T)}
	if a.Keys != nil || b.Keys != nil {
		// 片方の object にしかないキーは任意。もう片方が object でない（null など）ときは、
		// そのキーが「無い」のではなく object ごと無いので、任意にしない
		opt := map[string]bool{}
		for _, k := range a.Opt {
			opt[k] = true
		}
		for _, k := range b.Opt {
			opt[k] = true
		}
		if a.Keys != nil && b.Keys != nil {
			for k := range a.Keys {
				if _, ok := b.Keys[k]; !ok {
					opt[k] = true
				}
			}
			for k := range b.Keys {
				if _, ok := a.Keys[k]; !ok {
					opt[k] = true
				}
			}
		}
		out.Keys = map[string]*Node{}
		for k, na := range a.Keys {
			if nb, ok := b.Keys[k]; ok {
				out.Keys[k] = Merge(na, nb)
			} else {
				out.Keys[k] = na
			}
		}
		for k, nb := range b.Keys {
			if _, ok := a.Keys[k]; !ok {
				out.Keys[k] = nb
			}
		}
		if len(opt) > 0 {
			out.Opt = sortedKeys(opt)
		}
	}
	switch {
	case a.Items != nil && b.Items != nil:
		out.Items = Merge(a.Items, b.Items)
	case a.Items != nil:
		out.Items = a.Items
	default:
		out.Items = b.Items
	}
	return out
}

// Compare は期待と実際の形の違いの一覧。path は呼び手が空で呼ぶ。
func Compare(expected, actual *Node) []Diff { return compare(expected, actual, "") }

func compare(expected, actual *Node, path string) []Diff {
	here := path
	if here == "" {
		here = "$"
	}
	if !slices.Equal(expected.T, actual.T) {
		return []Diff{{here, KindType}}
	}
	var diffs []Diff
	if expected.Keys != nil && actual.Keys != nil {
		names := map[string]bool{}
		for k := range expected.Keys {
			names[k] = true
		}
		for k := range actual.Keys {
			names[k] = true
		}
		for _, k := range sortedKeys(names) {
			sub := k
			if path != "" {
				sub = path + "." + k
			}
			ek, inE := expected.Keys[k]
			ak, inA := actual.Keys[k]
			switch {
			case !inA:
				diffs = append(diffs, Diff{sub, KindMissing})
			case !inE:
				diffs = append(diffs, Diff{sub, KindExtra})
			default:
				if slices.Contains(expected.Opt, k) != slices.Contains(actual.Opt, k) {
					diffs = append(diffs, Diff{sub, KindPresence})
				}
				diffs = append(diffs, compare(ek, ak, sub)...)
			}
		}
	}
	// 片方が空の配列（items が無い）なら、比べる材料が無い
	if expected.Items != nil && actual.Items != nil {
		diffs = append(diffs, compare(expected.Items, actual.Items, path+"[]")...)
	}
	return diffs
}

// KeyPaths は形に含まれる全部のキー（Compare と同じ書き方。配列の要素は `[]`）。
func KeyPaths(node *Node) map[string]bool {
	found := map[string]bool{}
	collect(node, "", found)
	return found
}

func collect(node *Node, path string, found map[string]bool) {
	for k, sub := range node.Keys {
		here := k
		if path != "" {
			here = path + "." + k
		}
		found[here] = true
		collect(sub, here, found)
	}
	if node.Items != nil {
		here := path + "[]"
		found[here] = true
		collect(node.Items, here, found)
	}
}

var keyName = regexp.MustCompile(`^[^.\[]+`)

// NodeAt はキーの場所の形。無ければ nil。
func NodeAt(node *Node, key string) *Node {
	rest := key
	for rest != "" {
		if strings.HasPrefix(rest, "[]") {
			node, rest = node.Items, rest[2:]
		} else {
			rest = strings.TrimPrefix(rest, ".")
			name := keyName.FindString(rest)
			if name == "" {
				return nil
			}
			node, rest = node.Keys[name], rest[len(name):]
		}
		if node == nil {
			return nil
		}
	}
	return node
}

// overlaps は、差のキーが読むキーそのものか、その上か（上が無ければ下も無い）。下の差は数えない。
func overlaps(diffKey, readKey string) bool {
	return diffKey == readKey || strings.HasPrefix(readKey, diffKey+".") || strings.HasPrefix(readKey, diffKey+"[]")
}

// SelectReads は reads で差を絞る。reads のキーに関わる差は全部残す。それ以外は、本物にしか無いキーの欠け（missing）だけ落とす。
// 模擬にしか無いもの（extra）や型・常在の違いは残す。
func SelectReads(diffs []Diff, reads []string) []Diff {
	var out []Diff
	for _, d := range diffs {
		if d.Kind != KindMissing || slices.ContainsFunc(reads, func(r string) bool { return overlaps(d.Key, r) }) {
			out = append(out, d)
		}
	}
	return out
}

// Request は requests.json の 1 件。
type Request struct {
	Name         string            `json:"name"`
	Method       string            `json:"method"`
	Path         string            `json:"path"`
	Query        map[string]string `json:"query"`
	Body         any               `json:"body"`
	Token        string            `json:"token"`
	UsedBy       []string          `json:"used_by"`
	ExpectStatus int               `json:"expect_status"`
	Reads        []string          `json:"reads"`
	Save         map[string]string `json:"save"`
}

// UsedByAny は used_by に names のどれかがあるか。
func (r Request) UsedByAny(names ...string) bool {
	return slices.ContainsFunc(r.UsedBy, func(u string) bool { return slices.Contains(names, u) })
}

// ReadsOf は要求の名前ごとの reads。
func ReadsOf(requests []Request) map[string][]string {
	out := map[string][]string{}
	for _, r := range requests {
		out[r.Name] = r.Reads
	}
	return out
}

// ReadsProblems は reads の全キーが記録に存在するか。無いものの一覧。
func ReadsProblems(requests []Request, doc map[string]Entry) []string {
	var out []string
	for _, r := range requests {
		have, ok := doc[r.Name]
		if !ok {
			out = append(out, r.Name+": 記録に無い要求")
			continue
		}
		paths := KeyPaths(have.Shape)
		for _, k := range r.Reads {
			if !paths[k] {
				out = append(out, fmt.Sprintf("%s: reads の %s が記録に無い", r.Name, k))
			}
		}
	}
	return out
}

// CompareAll は要求の名前ごとの違い。recorded・actual は名前ごとの記録。
//
// reads（nil でなければ）を渡すと、本物にしか無いキーの欠け（missing）は reads のキーに関わるものだけ数える。nil なら全部数える。
func CompareAll(recorded, actual map[string]Entry, reads map[string][]string) map[string][]Diff {
	found := map[string][]Diff{}
	for name, want := range recorded {
		got, ok := actual[name]
		if !ok {
			found[name] = []Diff{{"$", KindMissing}}
			continue
		}
		var diffs []Diff
		if want.Status != got.Status {
			diffs = append(diffs, Diff{"$status", KindStatus})
		}
		diffs = append(diffs, Compare(want.Shape, got.Shape)...)
		if reads != nil {
			diffs = SelectReads(diffs, reads[name])
		}
		if len(diffs) > 0 {
			found[name] = diffs
		}
	}
	for name := range actual {
		if _, ok := recorded[name]; !ok {
			found[name] = []Diff{{"$", KindExtra}}
		}
	}
	return found
}

// AllowEntry は意図して許す違い（allow.json）。
type AllowEntry struct {
	Request string `json:"request"`
	Key     string `json:"key"`
	Kind    string `json:"kind"`
	Reason  string `json:"reason"`
}

// ApplyAllow は許可リストを当てる。(残った違い, 許可リストの問題)。
// 問題は、理由が空・種類が不正・実際は差が出ていない（古い許可）。
func ApplyAllow(found map[string][]Diff, entries []AllowEntry) (map[string][]Diff, []string) {
	var problems []string
	used := map[int]bool{}
	left := map[string][]Diff{}
	type id struct{ request, key, kind string }
	index := map[id]int{}
	for i, e := range entries {
		label := fmt.Sprintf("許可リスト[%d] %s/%s/%s", i, e.Request, e.Key, e.Kind)
		if strings.TrimSpace(e.Reason) == "" {
			problems = append(problems, label+": 理由が空")
		}
		if !slices.Contains(kinds, e.Kind) {
			problems = append(problems, fmt.Sprintf("%s: 違いの種類が %v のどれでもない", label, kinds))
		}
		index[id{e.Request, e.Key, e.Kind}] = i
	}
	for name, diffs := range found {
		for _, d := range diffs {
			if hit, ok := index[id{name, d.Key, d.Kind}]; ok {
				used[hit] = true
			} else {
				left[name] = append(left[name], d)
			}
		}
	}
	for i, e := range entries {
		if !used[i] {
			problems = append(problems, fmt.Sprintf("許可リスト[%d] %s/%s/%s: 実際は差が出ていない（古い許可）", i, e.Request, e.Key, e.Kind))
		}
	}
	return left, problems
}

// Forbidden は禁止する文字列（forbidden.json）。
type Forbidden struct {
	Name  string `json:"name"`
	Regex string `json:"regex"`
}

// ScanForbidden は禁止する文字列に当たった名前の一覧。当たりが無ければ空。
func ScanForbidden(text string, forbidden []Forbidden) []string {
	var hits []string
	for _, f := range forbidden {
		if regexp.MustCompile(f.Regex).MatchString(text) {
			hits = append(hits, f.Name)
		}
	}
	return hits
}

// NonEmpty は、録るときに場面が空でない配列を返していることを確かめる要求（空の配列は形を持たず比較から外れる）。
var NonEmpty = []string{
	"mrs_merged", "closes_issues", "issue_related_mrs", "discussions", "reviewers", "notes_get", "issue_notes_get",
}

// Answer は本物の応答（ステータスと本文）。
type Answer struct {
	Status int
	Body   any
}

// AnswerProblems は本物の応答が置いてよいものか。期待のステータスと、非空。
func AnswerProblems(requests []Request, answers map[string]Answer) []string {
	var out []string
	for _, r := range requests {
		got, ok := answers[r.Name]
		if !ok {
			out = append(out, r.Name+": 応答が無い")
			continue
		}
		if got.Status != r.ExpectStatus {
			out = append(out, fmt.Sprintf("%s: HTTP %d（期待は %d）", r.Name, got.Status, r.ExpectStatus))
		}
		if list, _ := got.Body.([]any); slices.Contains(NonEmpty, r.Name) && len(list) == 0 {
			out = append(out, r.Name+": 配列が空（場面が組めていない）")
		}
	}
	return out
}

// Document は recorded.json・recorded.sample.json の中身。
type Document struct {
	Version       int              `json:"version"`
	Source        string           `json:"source"`
	GitlabImage   *string          `json:"gitlab_image"`
	GitlabVersion *string          `json:"gitlab_version"`
	RecordedAt    *string          `json:"recorded_at"`
	NotRecorded   []string         `json:"not_recorded"`
	Requests      map[string]Entry `json:"requests"`
}

// PlacementProblems は recorded.json を置かない理由の一覧。空なら置いてよい。
func PlacementProblems(requests []Request, answers map[string]Answer, doc Document, runningImage string, forbidden []Forbidden) []string {
	var out []string
	if doc.GitlabImage == nil || runningImage != *doc.GitlabImage {
		image := "なし"
		if doc.GitlabImage != nil {
			image = *doc.GitlabImage
		}
		out = append(out, fmt.Sprintf("動いているコンテナのイメージ %s が GITLAB_IMAGE（%s）と違う", runningImage, image))
	}
	out = append(out, AnswerProblems(requests, answers)...)
	out = append(out, ReadsProblems(requests, doc.Requests)...)
	text, err := marshal(doc, "")
	if err != nil {
		return append(out, err.Error())
	}
	if hits := ScanForbidden(string(text), forbidden); len(hits) > 0 {
		out = append(out, fmt.Sprintf("禁止文字列に当たった: %v", hits))
	}
	return out
}

// WriteAtomic は同じ場所の一時ファイルに書いてから rename で置く（途中の状態を見せない）。失敗したら一時ファイルを消し、元のファイルは変えない。
func WriteAtomic(path, text string) error {
	f, err := os.CreateTemp(filepath.Dir(path), ".recorded-*.tmp")
	if err != nil {
		return err
	}
	temp := f.Name()
	_, err = f.WriteString(text)
	if closeErr := f.Close(); err == nil {
		err = closeErr
	}
	if err == nil {
		err = os.Rename(temp, path)
	}
	if err != nil {
		_ = os.Remove(temp)
	}
	return err
}

var placeholder = regexp.MustCompile(`\$\{([a-z_]+(?:\.[a-z_]+)*)\}`)

// Substitute は要求の文字列の `${名前}` を実体に置き換える（入れ子の辞書・配列も）。知らない名前はエラー。
func Substitute(value any, context map[string]string) (any, error) {
	switch v := value.(type) {
	case string:
		var missing string
		out := placeholder.ReplaceAllStringFunc(v, func(m string) string {
			name := placeholder.FindStringSubmatch(m)[1]
			val, ok := context[name]
			if !ok {
				missing = name
			}
			return val
		})
		if missing != "" {
			return nil, fmt.Errorf("置き換えの名前が場面に無い: %s", missing)
		}
		return out, nil
	case map[string]any:
		out := map[string]any{}
		for k, sub := range v {
			s, err := Substitute(sub, context)
			if err != nil {
				return nil, err
			}
			out[k] = s
		}
		return out, nil
	case []any:
		out := make([]any, len(v))
		for i, sub := range v {
			s, err := Substitute(sub, context)
			if err != nil {
				return nil, err
			}
			out[i] = s
		}
		return out, nil
	}
	return value, nil
}

// Resolve は要求の `${名前}` を場面の実体に置き、送る先（query を付けた path）と本文の JSON を返す。本文が無ければ nil。
func (r Request) Resolve(context map[string]string) (string, []byte, error) {
	path, err := substituteString(r.Path, context)
	if err != nil {
		return "", nil, err
	}
	if len(r.Query) > 0 {
		query := url.Values{}
		for k, v := range r.Query {
			s, err := substituteString(v, context)
			if err != nil {
				return "", nil, err
			}
			query.Set(k, s)
		}
		path += "?" + query.Encode()
	}
	if r.Body == nil {
		return path, nil, nil
	}
	body, err := Substitute(r.Body, context)
	if err != nil {
		return "", nil, err
	}
	raw, err := marshal(body, "")
	if err != nil {
		return "", nil, err
	}
	return path, raw, nil
}

func substituteString(text string, context map[string]string) (string, error) {
	out, err := Substitute(text, context)
	if err != nil {
		return "", err
	}
	return out.(string), nil
}

// Placeholders は文字列に含まれる `${名前}` の名前。
func Placeholders(text string) []string {
	var out []string
	for _, m := range placeholder.FindAllStringSubmatch(text, -1) {
		out = append(out, m[1])
	}
	return out
}
