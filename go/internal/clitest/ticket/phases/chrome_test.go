package phases

import (
	"encoding/json"
	"flag"
	"fmt"
	"io/fs"
	"maps"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"regexp"
	"slices"
	"sort"
	"strings"
	"sync"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
)

// 判定のコアを Chrome の入口と手元の CLI が同じに使うことの受入テスト（tests/ticket/test_swap_points.py）。
//
//  1. 承認は提案の中身を変えない（承認済みチケットは提案のバイト列のまま。CRLF も）
//  2. 親のワークツリーの HEAD がブランチを指していなければ承認しない
//  3. Chrome の入口（go/cmd/ccnavi-chrome。拡張の WASM と同じ要求を受ける）が同じ入力から、手元の CLI が実際に書いたのと
//     同じバイト列を出す。時刻は、Chrome は要求の時刻、CLI は手元の時計で書くので、時刻の欄だけを除いて比べる。
//     同じ要求と答えを拡張の試験の見本（extensions/chrome/ccnavi-approval/test/fixtures/core-scenarios.json）に置き、
//     拡張の試験が WASM でも同じ答えになることを見る
//  4. Chrome の入口は置き場を既定に固定する
//  5. 記録層（--record-writes）: 各コマンドで書いたパスの一覧が git status の変化と一致する
//
// 見本の形を変えたら `go test ./internal/clitest/ticket/phases -run TestChrome -update-chrome-fixture` で見本を書き直す。

const (
	stamp          = "2026-09-29T12:00:00+0900"
	zeros          = "0000000000000000000000000000000000000000"
	chromeFixture  = "extensions/chrome/ccnavi-approval/test/fixtures/core-scenarios.json"
	snapshotPrefix = ".ccnavi"
)

var updateChrome = flag.Bool("update-chrome-fixture", false, "Chrome 拡張の判定のコアの見本を、入口の答えで書き直す")

var scenarioNames = []string{"approve-new", "approve-reopen", "revise", "feedback-plan", "predecessors", "confirm"}

func scenariosPath() string { return filepath.Join(base.Root(), filepath.FromSlash(chromeFixture)) }

// ---- 木の読み

// filesUnder はツリーの置き場のファイル（相対パス → 中身）。
func filesUnder(t testing.TB, treeRoot string, prefixes ...string) map[string]string {
	t.Helper()
	found := map[string]string{}
	for _, prefix := range prefixes {
		top := filepath.Join(treeRoot, prefix)
		_ = filepath.WalkDir(top, func(path string, d fs.DirEntry, err error) error {
			if err != nil {
				return nil
			}
			if d.IsDir() {
				if d.Name() == ".git" {
					return filepath.SkipDir
				}
				return nil
			}
			rel, _ := filepath.Rel(treeRoot, path)
			raw, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			found[filepath.ToSlash(rel)] = string(raw)
			return nil
		})
	}
	return found
}

// trees はブランチの名前 → ツリーのルート。ワークスペースルートは main。
func (w *ws) trees() map[string]string {
	found := map[string]string{"main": w.root}
	dir := filepath.Join(w.root, ".claude", "worktrees")
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		found[e.Name()] = filepath.Join(dir, e.Name())
	}
	return found
}

func (w *ws) disk() map[string]map[string]string {
	out := map[string]map[string]string{}
	for name, path := range w.trees() {
		out[name] = filesUnder(w.t, path, snapshotPrefix, "wip")
	}
	return out
}

// diffDisk は 2 つの disk の違いを、答えの changes と同じ形（JSON に直した形）で。
func (w *ws) diffDisk(before, after map[string]map[string]string) map[string]any {
	out := map[string]any{}
	for name := range union(before, after) {
		old, cur := before[name], after[name]
		var rows []any
		for _, p := range slices.Sorted(maps.Keys(union(old, cur))) {
			o, inOld := old[p]
			n, inNew := cur[p]
			switch {
			case inOld && !inNew:
				rows = append(rows, map[string]any{"op": "delete", "path": p})
			case !inOld:
				rows = append(rows, map[string]any{"op": "create", "path": p, "content": strings.ReplaceAll(n, "\r\n", "\n")})
			case o != n:
				rows = append(rows, map[string]any{"op": "update", "path": p, "content": strings.ReplaceAll(n, "\r\n", "\n")})
			}
		}
		if rows != nil {
			out[name] = rows
		}
	}
	return out
}

func union[V any](a, b map[string]V) map[string]struct{} {
	out := map[string]struct{}{}
	for k := range a {
		out[k] = struct{}{}
	}
	for k := range b {
		out[k] = struct{}{}
	}
	return out
}

// ---- Chrome の要求

func (w *ws) chromeExe() string { return exe.ChromePath(w.t) }

func strs(v any) []string {
	var out []string
	for _, x := range v.([]any) {
		out = append(out, x.(string))
	}
	return out
}

func hasAnyPrefix(rel string, prefixes []string) bool {
	for _, p := range prefixes {
		if strings.HasPrefix(rel, p+"/") {
			return true
		}
	}
	return false
}

// chromeRequest は手元のツリーから、拡張が組むのと同じ形の要求を作る（統合先は main）。ブランチの先頭は既定で 0 を並べたもの。
func (w *ws) chromeRequest(op, family string, heads map[string]string, extra map[string]any) map[string]any {
	w.t.Helper()
	place := samples.ChromePlacement(w.t, w.chromeExe())
	branches := map[string]any{}
	for name, path := range w.trees() {
		files := map[string]string{}
		if name == "main" {
			integrationFiles := strs(place["integration_files"])
			for rel, body := range filesUnder(w.t, path, ".ccnavi", ".claude") {
				if hasAnyPrefix(rel, strs(place["integration_paths"])) || slices.Contains(integrationFiles, rel) {
					files[rel] = body
				}
			}
		} else {
			for rel, body := range filesUnder(w.t, path, ".ccnavi", "wip") {
				if hasAnyPrefix(rel, strs(place["branch_paths"])) {
					files[rel] = body
				}
			}
		}
		head := zeros
		if h, ok := heads[name]; ok {
			head = h
		}
		branches[name] = map[string]any{"head": head, "files": files}
	}
	request := map[string]any{
		"schema": samples.ChromeSchema, "op": op, "family": family, "stamp": stamp,
		"snapshot": map[string]any{
			"integration": map[string]any{"name": "main", "source": "setting", "head": zeros},
			"branches":    branches, "absent": []any{},
		},
	}
	for k, v := range extra {
		request[k] = v
	}
	return request
}

// handle は要求 1 件を入口に渡す。
func (w *ws) handle(request any, dir string) map[string]any {
	w.t.Helper()
	b, err := json.Marshal(request)
	if err != nil {
		w.t.Fatal(err)
	}
	return jsonOf(w.t, samples.ChromeHandle(w.t, w.chromeExe(), string(b), filepath.Join(w.root, dir)))
}

func (w *ws) askChrome(request any) map[string]any {
	w.t.Helper()
	answer := w.handle(request, "memfs")
	if _, bad := answer["error"]; bad {
		w.t.Fatalf("入口が error を返した: %v", answer["error"])
	}
	return answer
}

// ---- 見本の書き直し

var fixtureMu sync.Mutex

// keepScenario は拡張の試験の見本に置く場面（書き直すときだけ書く）。見本の答えが今の入口と合うことは
// TestTheFixtureAnswersAreWhatTheEntrySays が見る。
func keepScenario(t testing.TB, name string, request, answer any) {
	t.Helper()
	if !*updateChrome {
		return
	}
	fixtureMu.Lock()
	defer fixtureMu.Unlock()
	held := map[string]any{}
	if raw, err := os.ReadFile(scenariosPath()); err == nil {
		_ = json.Unmarshal(raw, &held)
	}
	held[name] = map[string]any{"request": request, "answer": answer}
	body, err := marshalIndent(held)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(scenariosPath(), []byte(body+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
}

func marshalIndent(v any) (string, error) {
	var buf strings.Builder
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", " ")
	err := enc.Encode(v)
	return strings.TrimRight(buf.String(), "\n"), err
}

// ---- 時刻などの正規化

var stampPattern = regexp.MustCompile(`\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:[+-]\d{4}|Z)`)
var settledBy = regexp.MustCompile(`(?m)^settled_by: "[a-z-]+"\n`)

// normalized は経路（via・settled_by）の欄を除き、書いた時刻を伏せた changes。
// 手元の CLI（board・cli）と Chrome で違う所。時刻は、Chrome は要求の stamp、CLI は手元の時計で書く。
func normalized(t testing.TB, changes any) any {
	t.Helper()
	out := map[string]any{}
	branches, _ := changes.(map[string]any)
	for name, rows := range branches {
		var kept []any
		for _, r := range rows.([]any) {
			row := map[string]any{}
			for k, v := range r.(map[string]any) {
				row[k] = v
			}
			if content, ok := row["content"].(string); ok {
				content = strings.ReplaceAll(content, "\r\n", "\n")
				content = settledBy.ReplaceAllString(content, "")
				content = stampPattern.ReplaceAllString(content, "<stamp>")
				row["content"] = content
				if strings.HasSuffix(row["path"].(string), ".reviewed") {
					mark := jsonOf(t, content)
					delete(mark, "via")
					row["content"] = mark
				}
			}
			kept = append(kept, row)
		}
		out[name] = kept
	}
	return out
}

func flat(lines any) []string {
	var out []string
	for _, l := range lines.([]any) {
		for _, part := range strings.Split(l.(string), "\n") {
			if strings.TrimSpace(part) != "" {
				out = append(out, part)
			}
		}
	}
	return out
}

func sameJSON(t testing.TB, what string, got, want any) {
	t.Helper()
	if !reflect.DeepEqual(roundTrip(t, got), roundTrip(t, want)) {
		t.Errorf("%s が違う:\n got  %v\n want %v", what, got, want)
	}
}

func roundTrip(t testing.TB, v any) any {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	var out any
	if err := json.Unmarshal(b, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

// ---- 1・2. 提案の中身と HEAD

func TestTheApprovedCopyIsTheProposal(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	text := w.parent(planOf("research"), samples.ParentOpts{})
	w.propose(parentName, text)
	w.commitParent()
	before := read(t, w.todo(parentName))
	preview := jsonOf(t, w.ccnavi("--agree", "--preview", "--json").Stdout)
	contains(t, "preview", preview["text"].(string), "提案: wip/proposals/todo/i0001.md")
	lacks(t, "preview", preview["text"].(string), w.root)
	w.mustApprove()
	if read(t, w.doing(parentName)) != before {
		t.Error("承認が提案の中身を変えた")
	}
}

func TestACRLFProposalMovesByteForByte(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	text := strings.ReplaceAll(w.parent(planOf("research"), samples.ParentOpts{}), "\n", "\r\n")
	write(t, w.todo(parentName), text)
	w.commitParent()
	w.mustApprove()
	if read(t, w.doing(parentName)) != text {
		t.Error("CRLF の提案のバイト列が変わった")
	}
}

func TestADetachedHeadIsNotApproved(t *testing.T) {
	t.Parallel()
	// 親のワークツリーの HEAD がブランチを指していなければ、ほかのツリーで代用せずに止まる。
	w := newWS(t)
	w.git(w.parentTree, "checkout", "--quiet", "--detach")
	w.propose(parentName, w.parent(planOf("research"), samples.ParentOpts{}))
	w.commitParent()
	if r := w.approve(); r.Code == 0 || w.isApproved(parentName) {
		t.Errorf("HEAD が外れた親が承認された: code %d", r.Code)
	}
}

// ---- 3. Chrome の入口と手元の CLI

// newChromeWS は統合先の互換のマーカーを置き、コミットの日時を固定した作業場。コミットの sha は日時で固定する。
// CLI が書く時刻は手元の時計なので、見本の要求は書き直すたびに変わる（Chrome が書く時刻は要求の stamp）。
func newChromeWS(t *testing.T) *ws {
	w := newWS(t)
	w.env = map[string]string{"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
	write(t, filepath.Join(w.root, ".ccnavi", "scripts", "ccnavi-common.sh"), "#!/bin/sh\nCCNAVI_COMPAT="+exe.Compat(t)+"\n")
	return w
}

// reviewedPhaseOne はフェーズ 1（MR で見る設計）の子を閉じて合流し、レビューを依頼したところまで進める。
func (w *ws) reviewedPhaseOne() string {
	return w.reviewedFamily()
}

func TestTheFixtureAnswersAreWhatTheEntrySays(t *testing.T) {
	t.Parallel()
	// 見本の要求を Chrome の入口に渡すと、見本の答えが返る。拡張の試験は WASM で同じことを見る。
	w := newWS(t)
	var held map[string]struct {
		Request any `json:"request"`
		Answer  any `json:"answer"`
	}
	if err := json.Unmarshal([]byte(read(t, scenariosPath())), &held); err != nil {
		t.Fatal(err)
	}
	names := slices.Sorted(maps.Keys(held))
	equalStrings(t, "見本の場面", names, slices.Sorted(slices.Values(scenarioNames)))
	for _, name := range names {
		t.Run(name, func(t *testing.T) {
			answer := w.as(t).handle(held[name].Request, filepath.Join("replay", name))
			sameJSON(t, name+" の答え（-update-chrome-fixture を付けて見本を書き直す）", answer, held[name].Answer)
		})
	}
}

// sameAsCLI は同じ状態で、Chrome の plan と手元の CLI（preview → --yes）を比べる。
func (w *ws) sameAsCLI(name, family string, only ...string) map[string]any {
	w.t.Helper()
	extra := map[string]any{}
	if len(only) > 0 {
		extra["only"] = only
	}
	request := w.chromeRequest("plan", family, nil, extra)
	answer := w.askChrome(request)
	preview := jsonOf(w.t, w.ccnavi(append([]string{"--agree", "--preview", "--json"}, only...)...).Stdout)
	if answer["text"] != preview["text"] || answer["digest"] != preview["digest"] {
		w.t.Errorf("画面の本文かダイジェストが CLI と違う:\n%v\n%v", answer["text"], preview["text"])
	}
	var tickets []string
	for _, b := range preview["batch"].([]any) {
		tickets = append(tickets, b.(map[string]any)["ticket"].(string))
	}
	sort.Strings(tickets)
	equalStrings(w.t, "identifiers", strs(answer["identifiers"]), tickets)
	sameJSON(w.t, "rejected", answer["rejected"], preview["rejected"])
	before := w.disk()
	args := append([]string{"user", "agree", "--yes", strings.Join(tickets, ","), "--digest", preview["digest"].(string), "--json"}, only...)
	result := w.ccnavi(args...)
	if result.Code != 0 {
		w.t.Fatalf("user agree --yes: code %d\n%s%s", result.Code, result.Stdout, result.Stderr)
	}
	body := jsonOf(w.t, result.Stdout)
	if answer["stopped"] != nil {
		w.t.Errorf("stopped = %v", answer["stopped"])
	}
	equalStrings(w.t, "lines", flat(answer["lines"]), flat(body["lines"]))
	sameJSON(w.t, "changes", normalized(w.t, answer["changes"]), normalized(w.t, w.diffDisk(before, w.disk())))
	keepScenario(w.t, name, request, answer)
	return answer
}

func TestChromeApproveNew(t *testing.T) {
	t.Parallel()
	w := newChromeWS(t)
	// 親の allow は計画の範囲と重なる形にする（重ならない親の警告は子の超過と混ざる）
	w.propose(parentName, w.parent(planOf("research", "design"), samples.ParentOpts{Allow: []string{"wip/*"}}))
	w.propose("i0001-01-01", childReview("i0001-01-01", 1, samples.Off, "wip/research/*"))
	w.commitParent()
	answer := w.sameAsCLI("approve-new", parentName)
	equalStrings(t, "identifiers", strs(answer["identifiers"]), []string{"i0001", "i0001-01-01"})
}

func TestChromeApproveReopensAReviewedPhase(t *testing.T) {
	t.Parallel()
	w := newChromeWS(t)
	w.family(planOf("design"))
	w.propose("i0001-01-01", childText("i0001-01-01", 1, "wip/design/*"))
	w.commitParent()
	w.mustApprove()
	marks := filepath.Join(w.approved, "phases", parentName)
	write(t, filepath.Join(marks, "1.requested"), `{"at": "x"}`)
	write(t, filepath.Join(marks, "1.reviewed"), `{"at": "x"}`)
	w.propose("i0001-01-02", childText("i0001-01-02", 1, "wip/design/*"))
	w.commitParent()
	answer := w.sameAsCLI("approve-reopen", parentName)
	found := false
	for _, r := range answer["changes"].(map[string]any)[parentName].([]any) {
		m := r.(map[string]any)
		found = found || (m["op"] == "delete" && m["path"] == ".ccnavi/approved/phases/i0001/1.reviewed")
	}
	if !found {
		t.Error("レビュー済みのマーカーを消す変更が無い")
	}
}

func TestChromeRevision(t *testing.T) {
	t.Parallel()
	w := newChromeWS(t)
	w.family(planOf("research", "design"))
	w.propose(parentName, samples.ParentText(parentName, planOf("research", "design", "acceptance"), samples.ParentOpts{}))
	w.commitParent()
	answer := w.sameAsCLI("revise", parentName)
	if !slices.Contains(strs(answer["lines"]), "  i0001 の全体計画を改版した") {
		t.Errorf("lines = %v", answer["lines"])
	}
}

func TestChromeFeedbackPlan(t *testing.T) {
	t.Parallel()
	w := newChromeWS(t)
	fixture := w.reviewedPhaseOne()
	w.mustOK(w.confirm(fixture, 1), "confirm")
	w.commitParent("reviewed")
	w.propose(parentName, samples.ParentText(parentName, planOf("design"), samples.ParentOpts{Feedback: noFeedback}))
	w.commitParent()
	// 合流した子のワークツリーを片付ける。手元の判定は全ツリーの承認済みチケットを読むので、残すと手元だけが子のツリーの
	// 古い承認済みチケットを読み、判定が読んだ中身で作るダイジェストが Chrome（統合先と P だけを読む）と食い違う。
	w.git(w.root, "worktree", "remove", "--force", filepath.Join(w.root, ".claude", "worktrees", "i0001-01-01"))
	answer := w.sameAsCLI("feedback-plan", parentName)
	if !slices.Contains(strs(answer["lines"]), "  i0001 のフィードバック計画を改版した") {
		t.Errorf("lines = %v", answer["lines"])
	}
}

func withPredecessors(text string, predecessors []string) string {
	if len(predecessors) == 0 {
		return text
	}
	b, _ := json.Marshal(predecessors)
	return strings.Replace(text, "human_review:", "predecessors: "+string(b)+"\nhuman_review:", 1)
}

func doneChild(name, parent string, predecessors []string) string {
	text := samples.ChildTextReview(name, parent, 1, []string{"wip/research/*"}, samples.Off)
	text = strings.Replace(text, `completed_at: ""`, `completed_at: "2026-01-01T00:00:00+0000"`, 1)
	return withPredecessors(text, predecessors)
}

func TestChromePredecessorsAcrossFamilies(t *testing.T) {
	t.Parallel()
	// 多階層の先行（閉包）。通る子と、先行が閉じていないので外れる子（文面まで同じ）。
	w := newChromeWS(t)
	type copyRow struct{ state, text string }
	child := func(name, parent string) string {
		return samples.ChildTextReview(name, parent, 1, []string{"wip/research/*"}, samples.Off)
	}
	copies := map[string][]copyRow{
		"i0001": {{"doing", samples.ParentText("i0001", planOf("research"), samples.ParentOpts{})}, {"done", doneChild("i0001-01-01", "i0001", nil)}},
		"i0002": {{"doing", samples.ParentText("i0002", planOf("research"), samples.ParentOpts{})}, {"done", doneChild("i0002-01-01", "i0002", []string{"i0001-01-01"})}, {"doing", child("i0002-01-02", "i0002")}},
		"i0003": {{"doing", samples.ParentText("i0003", planOf("research"), samples.ParentOpts{})}},
	}
	trees := map[string]string{"i0001": w.parentTree}
	for _, family := range []string{"i0002", "i0003"} {
		trees[family] = w.worktree(family, "main")
	}
	for family, rows := range copies {
		for _, r := range rows {
			ident := strings.SplitN(strings.SplitN(r.text, "ticket: ", 2)[1], "\n", 2)[0]
			write(t, filepath.Join(trees[family], ".ccnavi", "approved", r.state, ident+".md"), r.text)
		}
	}
	todo := filepath.Join(trees["i0003"], "wip", "proposals", "todo")
	write(t, filepath.Join(todo, "i0003-01-01.md"), withPredecessors(child("i0003-01-01", "i0003"), []string{"i0002-01-01"}))
	write(t, filepath.Join(todo, "i0003-01-02.md"), withPredecessors(child("i0003-01-02", "i0003"), []string{"i0002-01-02"}))
	for _, path := range trees {
		w.git(path, "add", "-A")
		w.git(path, "commit", "--quiet", "-m", "copies")
	}
	answer := w.sameAsCLI("predecessors", "i0003")
	equalStrings(t, "identifiers", strs(answer["identifiers"]), []string{"i0003-01-01"})
	rejected := answer["rejected"].([]any)
	if len(rejected) != 1 || rejected[0].(map[string]any)["ticket"] != "i0003-01-02" {
		t.Errorf("rejected = %v", rejected)
	}
}

// hostCompare は compare API の代わり: 依頼時の先頭から親の今の先頭までに変わったパス。
func (w *ws) hostCompare() map[string]any {
	w.t.Helper()
	recorded := readJSON(w.t, filepath.Join(w.parentTree, ".ccnavi", "approved", "phases", parentName, "1.requested"))["head"].(string)
	head := strings.TrimSpace(w.git(w.parentTree, "rev-parse", "HEAD"))
	names := w.git(w.parentTree, "diff", "--name-only", "--no-renames", recorded+"..HEAD")
	return map[string]any{"base": recorded, "head": head, "files": strings.Fields(names)}
}

func (w *ws) confirmRequest(result, compare map[string]any, extra map[string]any) map[string]any {
	e := map[string]any{"phase": 1, "result": result}
	if compare != nil {
		e["compare"] = compare
	}
	for k, v := range extra {
		e[k] = v
	}
	return w.chromeRequest("confirm", parentName, map[string]string{parentName: compare["head"].(string)}, e)
}

func hostResult(threads []any) map[string]any {
	if threads == nil {
		threads = []any{}
	}
	return map[string]any{"host": "fixture", "mr": map[string]any{"number": 7, "url": "u/7"}, "threads": threads, "reviews": []any{}}
}

// refusedByChangesRequested は変更要求のある結果を、Chrome の入口と手元の CLI の両方に渡す。
func (w *ws) refusedByChangesRequested(fixture string, result, compare map[string]any) (map[string]any, run.Result) {
	cr := map[string]any{}
	for k, v := range result {
		cr[k] = v
	}
	cr["reviews"] = []any{map[string]any{"state": "CHANGES_REQUESTED", "url": "r/1"}}
	refused := w.askChrome(w.confirmRequest(cr, compare, nil))
	writeJSON(w.t, fixture, cr)
	return refused, w.confirm(fixture, 1)
}

func TestChromeARefusalHasTheSameTextAsTheCLI(t *testing.T) {
	t.Parallel()
	w := newChromeWS(t)
	fixture := w.reviewedPhaseOne()
	w.commitParent("requested")
	refused, local := w.refusedByChangesRequested(fixture, hostResult(nil), w.hostCompare())
	equalStrings(t, "problems", strs(refused["problems"]), splitLines(local.Stderr))
	if !strings.HasPrefix(strs(refused["problems"])[0], "ccnavi: [CORE_CONFIRM_REVIEW_OPEN] ") {
		t.Errorf("problems[0] = %v", refused["problems"])
	}
}

func TestChromeConfirm(t *testing.T) {
	t.Parallel()
	// レビュー済み: レビュー待ちの子を done/ へ動かし、マーカーを置く。
	w := newChromeWS(t)
	fixture := w.reviewedPhaseOne()
	w.commitParent("requested")
	result := hostResult(nil)
	compare := w.hostCompare()
	// 依頼の後に親の先頭が動いていれば、入口が compare の一覧を求める（比べる相手は入口が出す）
	need := w.askChrome(w.chromeRequest("confirm", parentName, map[string]string{parentName: compare["head"].(string)}, map[string]any{"phase": 1, "result": result}))
	sameJSON(t, "need_compare", need["need_compare"], map[string]any{"base": compare["base"], "head": compare["head"]})
	if need["changes"] != nil {
		t.Error("compare を求める段で changes を返した")
	}
	// 変更要求があれば通さない。名指しする相手は手元の CLI の標準エラーと同じ。
	refused, local := w.refusedByChangesRequested(fixture, result, compare)
	if local.Code != 1 {
		t.Errorf("local code = %d", local.Code)
	}
	equalStrings(t, "problems[1:]", strs(refused["problems"])[1:], splitLines(local.Stderr)[1:])
	if refused["changes"] != nil {
		t.Error("拒んだのに changes がある")
	}
	// 分岐の材料は文面ではなくキー（文面にも同じコードが載る）
	if refused["code"] != "CORE_CONFIRM_REVIEW_OPEN" {
		t.Errorf("code = %v", refused["code"])
	}
	contains(t, "problems[0]", strs(refused["problems"])[0], "[CORE_CONFIRM_REVIEW_OPEN] ")
	writeJSON(t, fixture, result)

	request := w.confirmRequest(result, compare, nil)
	answer := w.askChrome(request)
	if len(answer["problems"].([]any)) != 0 || answer["code"] != nil {
		t.Errorf("problems = %v, code = %v", answer["problems"], answer["code"])
	}
	before := w.disk()
	localOK := w.confirm(fixture, 1)
	if localOK.Code != 0 {
		t.Fatalf("code %d\n%s%s", localOK.Code, localOK.Stdout, localOK.Stderr)
	}
	if answer["stopped"] != nil {
		t.Errorf("stopped = %v", answer["stopped"])
	}
	// 手元の CLI だけが、エージェント向けの `次:` の行を足す。Chrome を押すのはユーザなので返さない。
	var cli []string
	for _, l := range splitLines(localOK.Stdout) {
		if !strings.HasPrefix(l, "次: ") {
			cli = append(cli, l)
		}
	}
	equalStrings(t, "lines", flat(answer["lines"]), cli)
	sameJSON(t, "changes", normalized(t, answer["changes"]), normalized(t, w.diffDisk(before, w.disk())))
	rows := answer["changes"].(map[string]any)[parentName].([]any)
	has := func(op, path string) bool {
		for _, r := range rows {
			m := r.(map[string]any)
			if m["op"] == op && m["path"] == path {
				return true
			}
		}
		return false
	}
	if !has("create", ".ccnavi/approved/done/i0001-01-01.md") || !has("delete", "wip/proposals/review/i0001-01-01.md") {
		t.Errorf("子の移動が changes に無い: %v", rows)
	}
	for _, r := range rows {
		m := r.(map[string]any)
		if strings.HasSuffix(m["path"].(string), "1.reviewed") {
			// Chrome のマーカーは経路（chrome）を持つ。アカウントは要求に無いので書かない。
			sameJSON(t, "マーカー", jsonOf(t, m["content"].(string)), map[string]any{"mr": 7, "accepted": []any{}, "via": "chrome", "at": stamp})
		}
	}
	keepScenario(t, "confirm", request, answer)
}

func TestChromeConfirmAHandMovedChildAsksForTheTimeAndAcceptsOldThreads(t *testing.T) {
	t.Parallel()
	// 手で done/ に動かされた子: 移動の時刻を拡張に求め、移動前のスレッドを台帳に受ける。
	w := newChromeWS(t)
	w.reviewedPhaseOne()
	waiting := filepath.Join(w.parentTree, "wip", "proposals", "review", "i0001-01-01.md")
	done := filepath.Join(w.parentTree, ".ccnavi", "approved", "done", "i0001-01-01.md")
	write(t, done, read(t, waiting))
	remove(t, waiting)
	w.commitParent("moved by hand")
	old := map[string]any{"id": "t1", "url": "https://h/c/1", "body": "直して", "created_at": "2026-01-01T00:00:00Z", "last_id": "c1"}
	result := hostResult([]any{old})
	compare := w.hostCompare()
	need := w.askChrome(w.confirmRequest(result, compare, nil))
	sameJSON(t, "need_manual", need["need_manual"], map[string]any{"paths": []any{".ccnavi/approved/done/i0001-01-01.md"}})
	if need["changes"] != nil {
		t.Error("時刻を求める段で changes を返した")
	}
	moved := w.askChrome(w.confirmRequest(result, compare, map[string]any{"manual_moved_at": 4e9}))
	if len(moved["problems"].([]any)) != 0 {
		t.Errorf("problems = %v", moved["problems"])
	}
	rows := moved["changes"].(map[string]any)[parentName].([]any)
	var paths []string
	for _, r := range rows {
		m := r.(map[string]any)
		paths = append(paths, m["path"].(string))
		if strings.HasSuffix(m["path"].(string), "1.reviewed") && jsonOf(t, m["content"].(string))["via"] != "manual" {
			t.Errorf("via = %v", m["content"])
		}
	}
	if !slices.Contains(paths, ".ccnavi/approved/phases/i0001/accepted.json") {
		t.Errorf("accepted.json が無い: %v", paths)
	}
	// 移動の時刻を読めなければ確かめられないので、未解決の指摘は受け入れない
	unknown := w.askChrome(w.confirmRequest(result, compare, map[string]any{"manual_moved_at": 0}))
	if unknown["changes"] != nil || len(unknown["problems"].([]any)) == 0 {
		t.Errorf("時刻が読めないのに受け入れた: %v", unknown)
	}
}

func TestChromeAnUnreadableStampIsAnErrorNotAnException(t *testing.T) {
	t.Parallel()
	w := newChromeWS(t)
	w.propose(parentName, w.parent(planOf("research"), samples.ParentOpts{}))
	w.commitParent()
	request := w.chromeRequest("plan", parentName, nil, map[string]any{"stamp": "2026-09-29 12:00"})
	answer := w.handle(request, "m")
	contains(t, "error", fmt.Sprint(answer["error"]), "時刻の形が違う")
}

// ---- 4. 置き場は既定に固定する

var movedEnvNames = []string{"CCNAVI_TICKETS_PROPOSAL", "CCNAVI_TICKETS_APPROVED", "CCNAVI_PROJECT_HOME"}

// movedPlacementEnv は既定の置き場を動かす環境（答えに効かないことを見る）。
func movedPlacementEnv() map[string]string {
	return map[string]string{"CCNAVI_TICKETS_PROPOSAL": "moved/proposals", "CCNAVI_TICKETS_APPROVED": "moved/approved", "CCNAVI_PROJECT_HOME": "moved/home"}
}

var placementKeys = []string{"tickets", "approved", "layer_dir", "integration_paths", "integration_files", "branch_paths", "workspace_paths", "workspace_files", "project_paths"}

func defaultPlacement() map[string]any {
	const tickets, approved, layer, messages = "wip/proposals", ".ccnavi/approved", ".ccnavi/config/project", ".ccnavi/messages"
	done := approved + "/done"
	list := func(items ...string) []any {
		s := slices.Sorted(slices.Values(items))
		out := make([]any, len(s))
		for i, v := range s {
			out[i] = v
		}
		return out
	}
	return map[string]any{
		"tickets": tickets, "approved": approved, "layer_dir": layer,
		"integration_paths": list(done, samples.CommonLayer, layer, messages),
		"integration_files": []any{samples.SettingsFile, samples.CompatFile},
		"branch_paths":      []any{approved, tickets},
		"workspace_paths":   list(samples.CommonLayer, layer, messages),
		"workspace_files":   []any{samples.SettingsFile, samples.CompatFile},
		"project_paths":     list(done, layer),
	}
}

func settingsText(env map[string]string) string {
	all := map[string]string{"CCNAVI_MODE": "enforce"}
	for k, v := range env {
		all[k] = v
	}
	b, _ := json.Marshal(map[string]any{"env": all})
	return string(b)
}

// handleWithEnv は入口の子プロセスに環境を足して要求を渡す（手元のプロセスの環境を読まないことを見る）。
func (w *ws) handleWithEnv(request any, dir string, env map[string]string) map[string]any {
	w.t.Helper()
	b, _ := json.Marshal(request)
	path := filepath.Join(w.root, dir)
	if err := os.MkdirAll(path, 0o755); err != nil {
		w.t.Fatal(err)
	}
	cmd := exec.Command(w.chromeExe(), path)
	cmd.Stdin = strings.NewReader(string(b) + "\n")
	cmd.Env = os.Environ()
	for k, v := range env {
		cmd.Env = append(cmd.Env, k+"="+v)
	}
	out, err := cmd.Output()
	if err != nil {
		w.t.Fatalf("ccnavi-chrome: %v", err)
	}
	return jsonOf(w.t, strings.TrimSpace(string(out)))
}

func (w *ws) askPlacement(extra map[string]any, env map[string]string) map[string]any {
	w.t.Helper()
	request := map[string]any{"schema": samples.ChromeSchema, "op": "placement"}
	for k, v := range extra {
		request[k] = v
	}
	answer := w.handleWithEnv(request, "memfs", env)
	if _, bad := answer["error"]; bad {
		w.t.Fatalf("error: %v", answer["error"])
	}
	return answer["placement"].(map[string]any)
}

func (w *ws) assertDefaultPlacement(place map[string]any) {
	w.t.Helper()
	got := map[string]any{}
	for _, k := range placementKeys {
		got[k] = place[k]
	}
	sameJSON(w.t, "置き場", got, defaultPlacement())
	// 置き場の env は答えに載らない（欄ごと消してもよい）。
	if env, _ := place["env"].(map[string]any); len(env) != 0 {
		w.t.Errorf("置き場の env が答えに載った: %v", env)
	}
}

func (w *ws) boardRequest() (map[string]any, map[string]any) {
	w.t.Helper()
	w.propose(parentName, w.parent(planOf("research", "design"), samples.ParentOpts{}))
	w.propose("i0001-01-01", childReview("i0001-01-01", 1, samples.Off, "wip/research/*"))
	w.commitParent()
	request := w.chromeRequest("board", parentName, nil, nil)
	plain := w.handleWithEnv(request, "m1", nil)
	if _, bad := plain["error"]; bad {
		w.t.Fatalf("error: %v", plain["error"])
	}
	var tickets []string
	for _, e := range plain["batch"].([]any) {
		tickets = append(tickets, e.(map[string]any)["ticket"].(string))
	}
	equalStrings(w.t, "batch", tickets, []string{"i0001", "i0001-01-01"})
	return request, plain
}

func TestChromePlacementIgnoresEnvInTheIntegrationSettings(t *testing.T) {
	t.Parallel()
	w := newChromeWS(t)
	moved := movedPlacementEnv()
	w.assertDefaultPlacement(w.askPlacement(map[string]any{"settings": settingsText(moved)}, nil))
	// 絶対パスの形も断らず、既定の置き場を使う。
	for _, form := range []string{"/x", "C:/x", "~/x"} {
		abs := map[string]string{}
		for i, name := range movedEnvNames {
			abs[name] = form + "/" + string(rune('0'+i))
		}
		w.assertDefaultPlacement(w.askPlacement(map[string]any{"settings": settingsText(abs)}, nil))
	}
}

func TestChromeTheBoardAnswerDoesNotChangeWithTheEnv(t *testing.T) {
	t.Parallel()
	w := newChromeWS(t)
	request, plain := w.boardRequest()
	moved := movedPlacementEnv()
	withEnv := map[string]any{}
	for k, v := range request {
		withEnv[k] = v
	}
	withEnv["settings"] = settingsText(moved)
	sameJSON(t, "board の答え", w.handleWithEnv(withEnv, "m2", nil), plain)
}

func TestChromeTheLocalProcessEnvIsNotRead(t *testing.T) {
	t.Parallel()
	// 手元のプロセスの環境は読まない（回帰の見張り）。判定まで通る操作でも混ざらない。
	w := newChromeWS(t)
	local := map[string]string{}
	for i, name := range movedEnvNames {
		local[name] = "local/" + string(rune('0'+i))
	}
	w.assertDefaultPlacement(w.askPlacement(nil, local))
	request, plain := w.boardRequest()
	sameJSON(t, "board の答え", w.handleWithEnv(request, "m2", local), plain)
}

// ---- 5. 記録層

func (w *ws) placeDir() string { return filepath.Join(w.root, "logs", "state", "write-and-push") }

func (w *ws) record(target string, args ...string) (run.Result, []string) {
	w.t.Helper()
	if target == "" {
		target = filepath.Join(w.placeDir(), "self", "i0001.t.writes")
	}
	r := w.ccnavi(append([]string{"--record-writes", target}, args...)...)
	var listed []string
	if st, err := os.Stat(target); err == nil && st.Mode().IsRegular() {
		listed = splitLines(read(w.t, target))
	}
	return r, listed
}

func (w *ws) changed(treeRoot string) []string {
	w.t.Helper()
	out := w.git(treeRoot, "status", "--porcelain", "--untracked-files=all", "--no-renames")
	prefix, _ := filepath.Rel(w.root, treeRoot)
	var rows []string
	for _, line := range splitLines(out) {
		rows = append(rows, strings.TrimPrefix(filepath.ToSlash(prefix)+"/"+line[3:], "./"))
	}
	sort.Strings(rows)
	return rows
}

func TestEachCommandListsWhatItWrote(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.propose(parentName, w.parent(planOf("research"), samples.ParentOpts{}))
	w.propose("i0001-01-01", childReview("i0001-01-01", 1, samples.Off, "wip/research/*"))
	w.commitParent()
	digest := jsonOf(t, w.ccnavi("--agree", "--preview", "--json").Stdout)["digest"].(string)
	steps := [][]string{
		{"user", "agree", "--yes", "i0001,i0001-01-01", "--digest", digest, "--json"},
		{"ticket", "start", parentName},
	}
	for _, args := range steps {
		r, listed := w.record("", args...)
		w.mustOK(r, strings.Join(args, " "))
		sort.Strings(listed)
		equalStrings(t, strings.Join(args, " "), listed, w.changed(w.parentTree))
		w.commitParent(strings.Join(args, " "))
	}
	tree := w.worktree("i0001-01-01", parentName)
	r, listed := w.record("", "ticket", "start", "i0001-01-01")
	w.mustOK(r, "start child")
	sort.Strings(listed)
	equalStrings(t, "start child", listed, w.changed(w.parentTree))
	w.commitParent("start 01")
	write(t, filepath.Join(tree, "wip", "research", "summary.md"), "s\n")
	w.git(tree, "add", "-A")
	w.git(tree, "commit", "--quiet", "-m", "work")
	r, listed = w.record("", "ticket", "finish", "i0001-01-01")
	w.mustOK(r, "finish child")
	sort.Strings(listed)
	equalStrings(t, "finish child", listed, w.changed(w.parentTree))
	if len(listed) == 0 {
		t.Error("finish が書いたパスが空")
	}
}

func TestAFailedCommandStillWritesItsList(t *testing.T) {
	t.Parallel()
	// コマンドが失敗しても一覧は書く（write-and-push が戻すのに使う）。終了コードはコマンドのもの。
	w := newWS(t)
	r, listed := w.record("", "ticket", "start", "nope")
	if r.Code == 0 {
		t.Error("存在しない識別子の着手が通った")
	}
	if !exists(filepath.Join(w.placeDir(), "self", "i0001.t.writes")) {
		t.Error("一覧を書かなかった")
	}
	if len(listed) != 0 {
		t.Errorf("listed = %v", listed)
	}
}

func TestAnUnwritableListEndsWithOne(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	target := filepath.Join(w.placeDir(), "self", "busy")
	if err := os.MkdirAll(filepath.Join(target, "inside"), 0o755); err != nil {
		t.Fatal(err)
	}
	r, _ := w.record(target, "--agree", "--preview", "--json")
	if r.Code != 1 || !strings.Contains(r.Stderr, "反映したパスの一覧を") {
		t.Errorf("code %d\n%s", r.Code, r.Stderr)
	}
}

func (w *ws) refusedList(target string, extra ...string) {
	w.t.Helper()
	r := w.ccnavi(append(extra, "--record-writes", target, "ticket", "start", parentName)...)
	if r.Code != 1 || !strings.Contains(r.Stderr, "--record-writes の書き出し先は") {
		w.t.Errorf("書き出し先を断らない: code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
}

func TestTheListGoesOnlyUnderTheDefaultStatePlace(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	outside := filepath.Join(w.root, "wip", "list.txt")
	w.refusedList(outside)
	if exists(outside) {
		t.Error("置き場の外に書いた")
	}
	// `..` で出るパス。
	w.refusedList(filepath.Join(w.placeDir(), "..", "..", "..", "wip", "list.txt"))
	if exists(outside) {
		t.Error("`..` で置き場の外に書いた")
	}
	// `--state` で置き場を動かしても、書き出し先は動かない。
	moved := filepath.Join(w.root, "wip")
	w.refusedList(filepath.Join(moved, "write-and-push", "list.txt"), "--state", moved)
	if exists(filepath.Join(moved, "write-and-push")) {
		t.Error("--state で動かした置き場に書いた")
	}
}

func TestLinksUnderThePlaceAreNotFollowed(t *testing.T) {
	t.Parallel()
	if !base.CanSymlink() {
		t.Skip("リンクを作れない")
	}
	w := newWS(t)
	outsideDir := filepath.Join(w.root, "wip", "elsewhere")
	if err := os.MkdirAll(outsideDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(w.placeDir(), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outsideDir, filepath.Join(w.placeDir(), "self")); err != nil {
		t.Fatal(err)
	}
	w.refusedList(filepath.Join(w.placeDir(), "self", "list.txt"))
	if entries, _ := os.ReadDir(outsideDir); len(entries) != 0 {
		t.Error("リンクの先に書いた")
	}
	// 行き先そのものがリンク。リンクの先は書き換えない。
	victim := write(t, filepath.Join(w.root, "wip", "victim.txt"), "keep\n")
	link := filepath.Join(w.placeDir(), "link.writes")
	if err := os.Symlink(victim, link); err != nil {
		t.Fatal(err)
	}
	w.refusedList(link)
	if read(t, victim) != "keep\n" {
		t.Error("リンクの先を書き換えた")
	}
}

func TestALinkedWorkspaceRootListsRelativePaths(t *testing.T) {
	t.Parallel()
	// ワークスペースルートがリンク越し（macOS の /var → /private/var）でも、相対で書く。
	if !base.CanSymlink() {
		t.Skip("リンクを作れない")
	}
	w := newWS(t)
	linked := w.root + "-link"
	if err := os.Symlink(w.root, linked); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Remove(linked) })
	w.propose(parentName, w.parent(planOf("research"), samples.ParentOpts{}))
	w.commitParent()
	digest := jsonOf(t, w.ccnavi("--agree", "--preview", "--json").Stdout)["digest"].(string)
	target := filepath.Join(linked, "logs", "state", "write-and-push", "self", "l.writes")
	r := run.Ccnavi(t, []string{"--root", linked, "--record-writes", target, "user", "agree", "--guard-core-files", "disable",
		"--restore-if-deny", "disable", "--guard-ticket-approval", "disable", "--yes", parentName, "--digest", digest, "--json"}, run.Opts{Dir: base.Root()})
	w.mustOK(r, "user agree via link")
	listed := splitLines(read(t, target))
	sort.Strings(listed)
	equalStrings(t, "一覧", listed, w.changed(w.parentTree))
}
