package wap

import (
	"encoding/json"
	"io/fs"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/hostfixture"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/infra/env"
)

// ユーザの判断の入口（ccnavi-user-push-approved.sh など）は、取り込み済みの親子のチケットなら承認の push を自動で呼ぶ。
// 承認の push は取り込んでから送り、失敗してもコミットを残す。

const pushApproved = "ccnavi-user-push-approved.sh"

func (k *kit) pushApproved(args ...string) run.Result {
	k.t.Helper()
	return k.sh(pushApproved, opts{}, args...)
}

// 名前を省いた承認の push: 親は 1 度だけコミットして push し、子のワークツリーの置き場はコミットせずに言う。
func TestPushApprovedWithoutNamesSkipsChildTrees(t *testing.T) {
	k := newKit(t, "chores", "design")
	rel := approved + "/flows/" + childID + ".yml"
	write(t, filepath.Join(k.tree, parts(rel)), "steps: []\n")
	childTree := k.childTree()
	write(t, filepath.Join(childTree, parts(approved), "doing", "stray.md"), "x\n")
	r := k.pushApproved()
	code(t, r, 1)
	has(t, r.Stderr, "子のワークツリー")
	eq(t, "案内の数", strings.Count(r.Stdout, "承認済みチケットを取り込んだうえで"), 1)
	has(t, strings.Join(k.committed("HEAD"), "\n"), rel)
	eq(t, "子のブランチ", k.remoteSHA(childID), "")
}

func TestPushApprovedTakesInBeforeSending(t *testing.T) {
	k := newKit(t, "chores", "design")
	rel := approved + "/flows/" + childID + ".yml"
	write(t, filepath.Join(k.tree, parts(rel)), "steps: []\n")
	remote := k.remoteCommit("src/remote.py", "y = 2\n")
	okRun(t, k.pushApproved(parentID))
	has(t, strings.Join(k.committed("HEAD"), "\n"), rel)
	eq(t, "リモート", k.remoteParent(), k.head())
	code(t, k.try(k.tree, "merge-base", "--is-ancestor", remote, "HEAD"), 0)
}

func TestPushApprovedKeepsTheCommitWhenThePushFails(t *testing.T) {
	k := newKit(t, "chores", "design")
	rel := approved + "/flows/" + childID + ".yml"
	write(t, filepath.Join(k.tree, parts(rel)), "steps: []\n")
	hook := writeExec(t, filepath.Join(k.remote, "hooks", "pre-receive"), "#!/bin/sh\nexit 1\n")
	r := k.pushApproved(parentID)
	code(t, r, 1)
	has(t, r.Stderr, "コミットはローカルに残っています")
	has(t, strings.Join(k.committed("HEAD"), "\n"), rel)
	// 次の write-and-push は未送信のユーザの判断を見つけて止まり、承認の push を打ち直すよう言う。
	remove(t, hook)
	stopped := k.ticket("start", parentID)
	code(t, stopped, 1)
	has(t, stopped.Stderr, "未送信のユーザの判断")
	okRun(t, k.pushApproved(parentID))
	okRun(t, k.ticket("start", parentID))
}

// 取り込んで消えた下書きは、フローと一緒にコミットして push する。書き直された下書きと追跡していない下書きには触れない。
func TestPushApprovedCarriesARemovedDraftWithTheFlow(t *testing.T) {
	k := newKit(t, "chores", "design")
	drafts := "wip/proposals/flows"
	taken := drafts + "/" + childID + ".yml"
	rewritten := drafts + "/" + parentID + "-01-02.yml"
	untracked := drafts + "/" + parentID + "-01-03.yml"
	for _, rel := range []string{taken, rewritten} {
		write(t, filepath.Join(k.tree, parts(rel)), "nodes: []\n")
	}
	k.git(k.tree, "add", "--", taken, rewritten)
	k.git(k.tree, "commit", "-q", "-m", "drafts")
	k.git(k.tree, "push", "-q", "origin", parentID)
	flowRel := approved + "/flows/" + childID + ".yml"
	write(t, filepath.Join(k.tree, parts(flowRel)), "nodes: []\n")
	remove(t, filepath.Join(k.tree, parts(taken)))
	write(t, filepath.Join(k.tree, parts(rewritten)), "nodes: [x]\n")
	write(t, filepath.Join(k.tree, parts(untracked)), "nodes: []\n")
	okRun(t, k.pushApproved(parentID))
	// 同じ中身なので、改名として読まずに 2 つのパスで見る。
	got := strings.Fields(k.git(k.tree, "show", "--name-only", "--no-renames", "--format=", "HEAD"))
	want := []string{flowRel, taken}
	sort.Strings(got)
	sort.Strings(want)
	eqList(t, "コミット", got, want)
	eq(t, "リモート", k.remoteParent(), k.head())
	left := k.dirty()
	has(t, left, rewritten)
	has(t, left, untracked)
	lacks(t, left, taken)
	eq(t, "索引", k.git(k.tree, "diff", "--cached", "--name-only"), "")
}

func TestPushApprovedWithoutNamesCarriesTheFamilyThroughWriteAndPush(t *testing.T) {
	k := newKit(t, "chores", "design")
	write(t, filepath.Join(k.tree, parts(approved+"/flows/"+childID+".yml")), "steps: []\n")
	k.remoteCommit("src/remote.py", "y = 2\n")
	r := okRun(t, k.pushApproved())
	has(t, r.Stdout, "承認済みチケットを取り込んだうえで")
	eq(t, "リモート", k.remoteParent(), k.head())
}

// ---- Chrome のレビュー済みと手元の confirm の突き合わせ

// 取り込み済みの親子のチケットでは、手元の CLI を直に打つと write-and-push に断られる（--record-tree が無い）。そこで write-and-push と
// 同じ手順（ccnavi-review.sh review-request と confirm。GitHub のスタブは録った見本）で手元を回し、同じ状態から Chrome の入口が出す
// 書くものと、write-and-push が親のブランチへ書いて送ったものを比べる。違ってよいのは経路（via）と時刻（at）だけ。
// アカウント（actor）は、手元は sh がトークンの持ち主を引いたもの、Chrome は PAT の持ち主で、同じ見本なので同じになる。

const chromeScene = "resolved"

type chromeKit struct {
	*kit
	path string
}

func newChromeKit(t *testing.T) *chromeKit {
	need(t, "jq")
	k := newKit(t)
	shimDir := filepath.Join(k.base, "shim")
	writeExec(t, filepath.Join(shimDir, "git"), fill(urlGit, "URL", "https://github.com/acme/widgets.git", "GIT", k.gitPath))
	fakes := filepath.Join(k.base, "fakes")
	hostfixture.Install(t, fakes, hostfixture.GitHub)
	c := &chromeKit{kit: k, path: strings.Join([]string{shimDir, fakes, os.Getenv(env.Path)}, string(os.PathListSeparator))}
	k.finishPhaseOne()
	k.git(k.tree, "merge", "-q", "--no-edit", childID)
	k.git(k.tree, "push", "-q", "origin", parentID)
	return c
}

func (c *chromeKit) review(scene string, args ...string) run.Result {
	c.t.Helper()
	vars := map[string]string{
		env.Path: c.path, env.GitHubToken: "t0k", "FAKE_GITHUB_SCENE": scene,
		"FAKE_GITHUB_STATE": filepath.Join(c.base, "comments.json"),
	}
	for name, value := range hostfixture.Env(hostfixture.GitHub, scene) {
		vars[name] = value
	}
	return c.sh("ccnavi-review.sh", opts{dir: c.tree, env: vars}, args...)
}

// snapshotFiles は base の下の prefixes（.git は除く）にあるファイルを、base からの相対パス（/ 区切り）→ 中身で返す。
func snapshotFiles(t testing.TB, root string, prefixes []string) map[string]string {
	t.Helper()
	found := map[string]string{}
	for _, prefix := range prefixes {
		top := filepath.Join(root, parts(prefix))
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
			rel, _ := filepath.Rel(root, path)
			found[filepath.ToSlash(rel)] = read(t, path)
			return nil
		})
	}
	return found
}

// hostCompare は GitHub の compare API の答えを、拡張の compareFiles と同じ規則で一覧にする。GitHub は改名を検出して filename と
// previous_filename を返し、拡張は両方を入れる。ここでは git の改名の検出（--find-renames）で同じ組を作り、同じ規則で並べる
// （TS の関数そのものは拡張の試験 CX-T130・139 が見る）。
func (c *chromeKit) hostCompare(from, head string) []string {
	out := c.git(c.tree, "diff", "--name-status", "--find-renames", from+".."+head)
	files := []string{}
	for _, line := range strings.Split(out, "\n") {
		cols := strings.Split(line, "\t")
		switch {
		case len(cols) < 2:
		case strings.HasPrefix(cols[0], "R") && len(cols) >= 3:
			files = append(files, cols[2], cols[1])
		default:
			files = append(files, cols[1])
		}
	}
	return files
}

func (c *chromeKit) requestedMark() string { return approved + "/phases/" + parentID + "/1.requested" }

// chromeRequest は拡張が組むのと同じ要求（統合先 main、親 i0001、録った見本、依頼の後の変更の一覧）に Chrome の入口が答えた JSON。
func (c *chromeKit) chromeRequest(scene string) map[string]any {
	t := c.t
	t.Helper()
	chrome := exe.ChromePath(t)
	place := samples.ChromePlacement(t, chrome)
	strs := func(key string) []string {
		var out []string
		for _, v := range place[key].([]any) {
			out = append(out, v.(string))
		}
		return out
	}
	keep := []string{}
	for _, p := range strs("integration_paths") {
		keep = append(keep, p+"/")
	}
	integrationFiles := strs("integration_files")
	mainFiles := map[string]string{}
	for rel, text := range snapshotFiles(t, c.ws, []string{".ccnavi", ".claude"}) {
		matched := false
		for _, p := range keep {
			matched = matched || strings.HasPrefix(rel, p)
		}
		for _, f := range integrationFiles {
			matched = matched || rel == f
		}
		if matched {
			mainFiles[rel] = text
		}
	}
	mine := snapshotFiles(t, c.tree, strs("branch_paths"))
	var recorded struct {
		Head string `json:"head"`
	}
	if err := json.Unmarshal([]byte(read(t, filepath.Join(c.tree, parts(c.requestedMark())))), &recorded); err != nil {
		t.Fatal(err)
	}
	head := c.head()
	var expected any
	if err := json.Unmarshal([]byte(read(t, filepath.Join(hostfixture.SceneDir(hostfixture.GitHub, scene), "expected.json"))), &expected); err != nil {
		t.Fatal(err)
	}
	zero := strings.Repeat("0", 40)
	request, err := json.Marshal(map[string]any{
		"schema": samples.ChromeSchema, "op": "confirm", "family": parentID, "phase": 1,
		"stamp": "2026-09-29T12:00:00+0900", "actor": map[string]any{"account": "octo-reviewer"},
		"result":  expected,
		"compare": map[string]any{"base": recorded.Head, "head": head, "files": c.hostCompare(recorded.Head, head)},
		"snapshot": map[string]any{
			"integration": map[string]any{"name": "main", "source": "default", "head": zero},
			"branches": map[string]any{
				"main":   map[string]any{"head": zero, "files": mainFiles},
				parentID: map[string]any{"head": head, "files": mine},
			},
			"absent": []any{},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	var answer map[string]any
	if err := json.Unmarshal([]byte(samples.ChromeHandle(t, chrome, string(request), filepath.Join(c.base, "memfs"))), &answer); err != nil {
		t.Fatal(err)
	}
	if _, bad := answer["error"]; bad {
		t.Fatalf("Chrome の入口がエラーを返した: %v", answer)
	}
	return answer
}

var settledBy = regexp.MustCompile(`(?m)^settled_by: "[a-z-]+"\n`)

// normalized は経路・時刻を除く（違ってよいもの）。子を動かした経路の欄（settled_by）も経路。
func normalized(t testing.TB, path, text string) string {
	t.Helper()
	if strings.HasSuffix(path, ".reviewed") || strings.HasSuffix(path, ".json") {
		var data map[string]any
		if err := json.Unmarshal([]byte(text), &data); err != nil {
			t.Fatalf("%s: %v", path, err)
		}
		delete(data, "via")
		delete(data, "at")
		out, _ := json.Marshal(data)
		return string(out)
	}
	return settledBy.ReplaceAllString(text, "")
}

// topKeys は JSON の最上位のキーを書いた順に返す。
func topKeys(t testing.TB, text string) []string {
	t.Helper()
	dec := json.NewDecoder(strings.NewReader(text))
	if _, err := dec.Token(); err != nil {
		t.Fatal(err)
	}
	var keys []string
	for dec.More() {
		key, err := dec.Token()
		if err != nil {
			t.Fatal(err)
		}
		keys = append(keys, key.(string))
		var skip json.RawMessage
		if err := dec.Decode(&skip); err != nil {
			t.Fatal(err)
		}
	}
	return keys
}

func (c *chromeKit) request() string {
	c.t.Helper()
	body := write(c.t, filepath.Join(c.base, "body.md"), "見てほしい\n")
	okRun(c.t, c.review(chromeScene, "review-request", "--phase", "1", "--body-file", body))
	has(c.t, strings.Join(c.committed("HEAD"), "\n"), c.requestedMark())
	return body
}

// sameRefusal は Chrome が通さない理由が、write-and-push で回した手元の confirm の標準エラーにそのまま出ることを見る。
func (c *chromeKit) sameRefusal(scene string) map[string]any {
	t := c.t
	t.Helper()
	answer := c.chromeRequest(scene)
	if answer["changes"] != nil {
		t.Errorf("%s: Chrome が通した: %v", scene, answer["changes"])
	}
	problems, _ := answer["problems"].([]any)
	if len(problems) == 0 {
		t.Errorf("%s: Chrome が理由を言わない", scene)
	}
	head, remote := c.head(), c.remoteParent()
	local := c.review(scene, "confirm", "--phase", "1")
	if local.Code == 0 {
		t.Errorf("%s: 手元が通った", scene)
	}
	said := strings.Split(strings.ReplaceAll(local.Stderr, c.ws+string(os.PathSeparator), ""), "\n")
	for _, p := range problems {
		found := false
		for _, line := range said {
			found = found || line == p.(string)
		}
		if !found {
			t.Errorf("%s: 手元の標準エラーに %q が無い:\n%s", scene, p, local.Stderr)
		}
	}
	eq(t, scene+" の HEAD とリモート", [2]string{c.head(), c.remoteParent()}, [2]string{head, remote})
	eq(t, scene+" の未コミット", c.dirty(), "")
	return answer
}

func firstProblem(answer map[string]any) string {
	return answer["problems"].([]any)[0].(string)
}

func TestUnresolvedThreadsAndChangeRequestsStopBothTheSame(t *testing.T) {
	c := newChromeKit(t)
	c.request()
	// paged の取ってきた状態は約 50,000 字。1 引数の上限が小さい環境（Windows）では取れない。
	said := map[string]string{}
	for _, scene := range []string{"paged", "changes-requested", "cr-commented", "pending"} {
		if scene == "paged" && !base.CanPassArgument(60_000) {
			continue
		}
		said[scene] = firstProblem(c.sameRefusal(scene))
	}
	if text, ok := said["paged"]; ok {
		has(t, text, "未解決のスレッドが 4 件")
	}
	for _, scene := range []string{"changes-requested", "cr-commented", "pending"} {
		has(t, said[scene], "変更要求（Changes requested）が出ています")
	}
}

func TestChromeWritesWhatWriteAndPushWritesAndSends(t *testing.T) {
	c := newChromeKit(t)
	body := c.request()
	answer := c.chromeRequest(chromeScene)
	if problems, _ := answer["problems"].([]any); len(problems) != 0 {
		t.Fatalf("Chrome が止めた: %v", problems)
	}
	rows, _ := answer["changes"].(map[string]any)[parentID].([]any)

	// 手元を直に打つと write-and-push に断られる（だから write-and-push の手順で回す）。文面まで見る
	direct := c.exe(opts{}, "--cwd", c.tree, "review", "confirm", "--phase", "1", "--result", body)
	if direct.Code == 0 {
		t.Error("手元を直に打てた")
	}
	has(t, direct.Stderr, "親子のチケット "+parentID+" は取り込み済み（write-and-push の対象）")
	has(t, direct.Stderr, "何も反映せずに中止しました。")

	before := c.head()
	okRun(t, c.review(chromeScene, "confirm", "--phase", "1"))
	eq(t, "リモート", c.remoteParent(), c.head())
	eq(t, "未コミット", c.dirty(), "")
	var paths []string
	for _, row := range rows {
		paths = append(paths, row.(map[string]any)["path"].(string))
	}
	sent := strings.Fields(c.git(c.tree, "diff", "--name-only", "--no-renames", before+"..HEAD"))
	sort.Strings(sent)
	sort.Strings(paths)
	eqList(t, "送った経路", sent, paths)
	var remoteText string
	for _, r := range rows {
		row := r.(map[string]any)
		path, content := row["path"].(string), row["content"]
		full := filepath.Join(c.tree, parts(path))
		if row["op"] == "delete" {
			if exists(full) {
				t.Errorf("%s: 消すはずが残っている", path)
			}
			continue
		}
		eq(t, path, normalized(t, path, content.(string)), normalized(t, path, read(t, full)))
		if strings.HasSuffix(path, "1.reviewed") {
			remoteText = content.(string)
		}
	}
	localText := read(t, filepath.Join(c.tree, parts(approved+"/phases/"+parentID+"/1.reviewed")))
	var remote, local map[string]any
	if err := json.Unmarshal([]byte(remoteText), &remote); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal([]byte(localText), &local); err != nil {
		t.Fatal(err)
	}
	eq(t, "Chrome の actor と via", [2]any{remote["actor"], remote["via"]}, [2]any{"octo-reviewer", "chrome"})
	eq(t, "手元の actor と via", [2]any{local["actor"], local["via"]}, [2]any{"octo-reviewer", "cli"})
	if !reflect.DeepEqual(topKeys(t, remoteText), topKeys(t, localText)) {
		t.Errorf("キーの並びが違う: %v / %v", topKeys(t, remoteText), topKeys(t, localText))
	}

	// レビュー済みに重ねて打てば、手元も Chrome も同じ文面で止め、何も送らない
	again := c.sameRefusal(chromeScene)
	eq(t, "重ねて打った理由", firstProblem(again), "ccnavi: [CORE_CONFIRM_ALREADY_REVIEWED] フェーズ 1 はレビュー済み")
	if n := len(again["problems"].([]any)); n != 1 {
		t.Errorf("理由が %d 件", n)
	}
}
