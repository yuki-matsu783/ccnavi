// 見本（ホストの応答の見本。harness/hostfixture と extensions/chrome/ccnavi-approval/test/fixtures/host/gitlab）の応答の形を、本物の記録と
// 比べる。仕様は `.agents/docs/gitlab-contract.md` の「見本」の節。
//
//  1. 比べる場面・除く場面・要求名が、見本の実体と合っている（一覧に漏れや、実在しない名前が無い）
//  2. 見本の応答が、recorded.json と reads の範囲で一致する（差は許可リストの host_entries）
//  3. 空のまま通らない: 要求ごとに、比べたキーの数が 0 件でない
//  4. 改変: 見本の応答を書き換えると、場面名・要求名・キー・違いの種類が出て落ちる
package gitlab

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	cs "ccnavi/internal/harness/contractshape"
	"ccnavi/internal/harness/hostfixture"
)

// 比べる場面。paged は discussions の 1 ページ目だけを比べる。
var comparedScenes = []string{"nested", "paged", "replies", "replies-accepted", "requested-changes", "resolved"}

// 異常な値をわざと入れた見本なので、本物の形とは比べない。
var excludedScenes = []string{"hostile", "odd-types", "impostor"}

var (
	// 場面ごとに答えを取る要求（見本が実際に答えるもの）。
	sceneRequests = []string{"user", "mrs_opened", "discussions", "reviewers"}
	// 場面によらない要求。状態ファイルに溜める（場面は 1 つだけ使う）。
	stateRequests = []string{"notes_post", "notes_get"}
	// 見本が答えるが比べない要求（理由は文書の表）。
	excludedRequests = []string{"mrs_merged", "project"}
)

const sourceProjectID = 42

// sceneContext は scene.json と mrs.json から作る文脈（cs.SceneContext は模擬用）。
func sceneContext(t testing.TB, scene string) map[string]string {
	t.Helper()
	dir := hostfixture.SceneDir(hostfixture.GitLab, scene)
	var meta struct {
		Namespace, Project, Branch string
	}
	readJSON(t, filepath.Join(dir, "scene.json"), &meta)
	var mrs []struct {
		Iid             json.Number `json:"iid"`
		SourceProjectID int         `json:"source_project_id"`
	}
	readJSON(t, filepath.Join(dir, "mrs.json"), &mrs)
	for _, m := range mrs {
		if m.SourceProjectID == sourceProjectID {
			return map[string]string{
				"project":   url.PathEscape(meta.Namespace + "/" + meta.Project),
				"mr.iid":    m.Iid.String(),
				"mr.branch": meta.Branch,
			}
		}
	}
	t.Fatalf("%s: source_project_id が %d の MR が無い", scene, sourceProjectID)
	return nil
}

func readJSON(t testing.TB, path string, into any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := cs.DecodeJSON(data, into); err != nil {
		t.Fatalf("%s: %v", path, err)
	}
}

func requestsByName(t testing.TB, names []string) []cs.Request {
	t.Helper()
	byName := map[string]cs.Request{}
	for _, r := range loadFake(t) {
		byName[r.Name] = r
	}
	var out []cs.Request
	for _, n := range names {
		r, ok := byName[n]
		if !ok {
			t.Fatalf("requests.json に無い要求: %s", n)
		}
		out = append(out, r)
	}
	return out
}

// ask は見本に 1 件の要求を送り、(状態, 本文) を返す。
func ask(t testing.TB, scene string, req cs.Request, context map[string]string, state string) (int, any) {
	t.Helper()
	path, err := cs.Substitute(req.Path, context)
	if err != nil {
		t.Fatal(err)
	}
	rel := path.(string)
	if len(req.Query) > 0 {
		query := url.Values{}
		for k, v := range req.Query {
			s, err := cs.Substitute(v, context)
			if err != nil {
				t.Fatal(err)
			}
			query.Set(k, s.(string))
		}
		rel += "?" + query.Encode()
	}
	body := ""
	if req.Body != nil {
		b, err := cs.Substitute(req.Body, context)
		if err != nil {
			t.Fatal(err)
		}
		raw, _ := json.Marshal(b)
		body = string(raw)
	}
	status, answer := hostfixture.GitlabAnswer(func(string) string { return "" }, base.Root(), scene, req.Method, rel, body, state)
	raw, err := json.Marshal(answer)
	if err != nil {
		t.Fatal(err)
	}
	var decoded any
	if err := cs.DecodeJSON(raw, &decoded); err != nil {
		t.Fatal(err)
	}
	return status, decoded
}

// runHost は場面ごと・要求ごとの記録。tamper は形にする前に本文を書き換える。
// notes_post・notes_get は場面によらないので、比べる場面の最初の 1 つで 1 回だけ打つ。
func runHost(t testing.TB, tamper func(name string, body any) any) map[string]map[string]cs.Entry {
	t.Helper()
	sceneReqs := requestsByName(t, sceneRequests)
	stateReqs := requestsByName(t, stateRequests)
	state := filepath.Join(base.TempDir(t), "state.json")
	out := map[string]map[string]cs.Entry{}
	for i, scene := range comparedScenes {
		context := sceneContext(t, scene)
		answers := map[string]cs.Entry{}
		reqs := slices.Clone(sceneReqs)
		if i == 0 {
			reqs = append(reqs, stateReqs...)
		}
		for _, req := range reqs {
			status, body := ask(t, scene, req, context, state)
			if tamper != nil {
				body = tamper(req.Name, body)
			}
			answers[req.Name] = cs.Entry{Status: status, Shape: cs.Normalize(body)}
		}
		out[scene] = answers
	}
	return out
}

// comparedCount は reads のうち、見本にも本物の記録にも存在するキーの数。
func comparedCount(reads []string, recorded, got cs.Entry) int {
	mine, real := cs.KeyPaths(got.Shape), cs.KeyPaths(recorded.Shape)
	n := 0
	for _, k := range reads {
		if mine[k] && real[k] {
			n++
		}
	}
	return n
}

// hostProblems は失敗の理由の一覧。空なら一致している。
func hostProblems(t testing.TB, recorded map[string]cs.Entry, ran map[string]map[string]cs.Entry, entries []cs.AllowEntry) []string {
	t.Helper()
	readKeys := cs.ReadsOf(loadFake(t))
	requests := requestsByName(t, append(slices.Clone(sceneRequests), stateRequests...))
	var out []string
	found := map[string][]cs.Diff{}
	whereByRequest := map[string]map[cs.Diff][]string{}
	for _, scene := range comparedScenes {
		answers := ran[scene]
		var asked []cs.Request
		for _, r := range requests {
			if _, ok := answers[r.Name]; ok {
				asked = append(asked, r)
			}
		}
		for _, p := range cs.ReadsProblems(asked, answers) {
			out = append(out, scene+": "+p)
		}
		want := map[string]cs.Entry{}
		reads := map[string][]string{}
		for name := range answers {
			want[name] = recorded[name]
			reads[name] = readKeys[name]
			if n := comparedCount(readKeys[name], recorded[name], answers[name]); n == 0 {
				out = append(out, fmt.Sprintf("%s/%s: 比べたキーが 0 件", scene, name))
			}
		}
		for name, diffs := range cs.CompareAll(want, answers, reads) {
			if whereByRequest[name] == nil {
				whereByRequest[name] = map[cs.Diff][]string{}
			}
			for _, d := range diffs {
				whereByRequest[name][d] = append(whereByRequest[name][d], scene)
				if !slices.Contains(found[name], d) {
					found[name] = append(found[name], d)
				}
			}
		}
	}
	left, allowProblems := cs.ApplyAllow(found, entries)
	out = append(out, allowProblems...)
	for name, diffs := range left {
		for _, d := range diffs {
			out = append(out, fmt.Sprintf("%s %s %s（場面: %s）", name, d.Key, d.Kind, strings.Join(whereByRequest[name][d], ", ")))
		}
	}
	return out
}

func TestScenesAreDecidedOneByOne(t *testing.T) {
	t.Parallel()
	want := append(slices.Clone(comparedScenes), excludedScenes...)
	got := hostfixture.SceneNames(t, hostfixture.GitLab)
	slices.Sort(want)
	if !slices.Equal(got, want) {
		t.Errorf("見本の場面 = %v, 比べる・除くの一覧 = %v", got, want)
	}
	// 要求名は requests.json にある（無いと requestsByName が落とす）。
	requestsByName(t, slices.Concat(sceneRequests, stateRequests, excludedRequests))
}

type hostSample struct {
	recorded map[string]cs.Entry
	entries  []cs.AllowEntry
}

func loadHostSample(t *testing.T) hostSample {
	t.Helper()
	doc := loadRecorded(t)
	allow := load[struct {
		HostEntries []cs.AllowEntry `json:"host_entries"`
	}](t, "allow.json")
	return hostSample{doc.Requests, allow.HostEntries}
}

func TestTheSampleAgreesWithTheRecordingExceptForTheAllowList(t *testing.T) {
	t.Parallel()
	h := loadHostSample(t)
	if problems := hostProblems(t, h.recorded, runHost(t, nil), h.entries); len(problems) != 0 {
		t.Errorf("見本と記録が食い違う: %v", problems)
	}
}

func TestEveryRequestComparedSomeKeysAndNotesArePostedBeforeTheyAreRead(t *testing.T) {
	t.Parallel()
	h := loadHostSample(t)
	ran := runHost(t, nil)
	readKeys := cs.ReadsOf(loadFake(t))
	seen := map[string]bool{}
	for scene, answers := range ran {
		for name, got := range answers {
			seen[name] = true
			if comparedCount(readKeys[name], h.recorded[name], got) == 0 {
				t.Errorf("%s/%s: 比べたキーが 0 件", scene, name)
			}
		}
	}
	for _, name := range slices.Concat(sceneRequests, stateRequests) {
		if !seen[name] {
			t.Errorf("%s を比べていない", name)
		}
	}
	// notes は最初の場面で投稿してから読む。ほかの場面では投稿しない。
	first := ran[comparedScenes[0]]
	if first["notes_post"].Status != 201 || first["notes_get"].Shape.Items == nil {
		t.Errorf("notes_post = %d, notes_get = %v", first["notes_post"].Status, first["notes_get"].Shape)
	}
	for _, scene := range comparedScenes[1:] {
		if _, ok := ran[scene]["notes_post"]; ok {
			t.Errorf("%s で notes_post を打った", scene)
		}
	}
}

func TestAKeyTheSampleLacksIsReportedWithTheScene(t *testing.T) {
	t.Parallel()
	h := loadHostSample(t)
	dropSystem := func(name string, body any) any {
		if name == "discussions" {
			for _, d := range body.([]any) {
				for _, n := range d.(map[string]any)["notes"].([]any) {
					delete(n.(map[string]any), "system")
				}
			}
		}
		return body
	}
	out := hostProblems(t, h.recorded, runHost(t, dropSystem), h.entries)
	hit := slices.IndexFunc(out, func(p string) bool {
		return strings.Contains(p, "discussions") && strings.Contains(p, "[].notes[].system missing")
	})
	if hit < 0 || !strings.Contains(out[hit], comparedScenes[0]) {
		t.Errorf("problems = %v", out)
	}
}

func TestAnEmptyDiscussionsFailsWithZeroKeys(t *testing.T) {
	t.Parallel()
	h := loadHostSample(t)
	empty := func(name string, body any) any {
		if name == "discussions" {
			return []any{}
		}
		return body
	}
	out := hostProblems(t, h.recorded, runHost(t, empty), h.entries)
	if !slices.ContainsFunc(out, func(p string) bool { return strings.Contains(p, "discussions") && strings.Contains(p, "0 件") }) {
		t.Errorf("problems = %v", out)
	}
}
