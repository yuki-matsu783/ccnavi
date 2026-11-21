package review

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/hostfixture"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
)

// GitLab の見本から sh が組む JSON と結論（拡張も同じ期待値と比べる）。GitHub の側は hostfixture_test.go。

// originOf は場面の名前空間とプロジェクトに origin を合わせる（入れ子のグループの場面のため）。
func originOf(h hostSite, scene string) {
	h.t.Helper()
	var meta struct{ Namespace, Project string }
	if err := json.Unmarshal([]byte(shkit.Read(h.t, filepath.Join(hostfixture.SceneDir(hostfixture.GitLab, scene), "scene.json"))), &meta); err != nil {
		h.t.Fatal(err)
	}
	h.git("remote", "set-url", "origin", "https://gitlab.com/"+meta.Namespace+"/"+meta.Project+".git")
}

func TestGitLabHostFixtureCopyMatchesEachRecordedScene(t *testing.T) {
	t.Parallel()
	checkScenes(t, hostfixture.GitLab,
		[]string{"hostile", "impostor", "nested", "odd-types", "paged", "replies", "replies-accepted", "requested-changes", "resolved"}, originOf)
}

func TestGitLabHostFixtureScenesSayWhatTheyAreFor(t *testing.T) {
	t.Parallel()
	count := func(scene string) (changes, unresolved int) {
		c, u := concluded(t, hostfixture.GitLab, scene)
		return len(c), len(u)
	}
	want := []struct {
		scene               string
		changes, unresolved int
		why                 string
	}{
		{"resolved", 0, 0, "依頼したアカウントの ccnavi の依頼のスレッドは数えない"},
		{"requested-changes", 1, 0, "変更要求は 1 件"},
		{"impostor", 0, 2, "目印をまねた別の人のスレッドは数える"},
		{"paged", 0, 11, "後のノートが未解決のスレッドは数える"},
		{"hostile", 0, 8, ""},
		{"nested", 0, 0, "入れ子のグループも resolved と同じ"},
		{"odd-types", 0, 2, ""},
		// 依頼コメントへの返信は指摘として数える。依頼だけ・コメントだけ・system だけは数えない
		{"replies", 0, 6, "依頼コメントへの返信は指摘として数える"},
		// 受け入れの返信を足してホストで解決した後は、数えない
		{"replies-accepted", 0, 0, "受け入れて解決した後は数えない"},
	}
	for _, w := range want {
		if c, u := count(w.scene); c != w.changes || u != w.unresolved {
			t.Errorf("%s（%s）: 変更要求 %d・未解決 %d, want %d・%d", w.scene, w.why, c, u, w.changes, w.unresolved)
		}
	}
}

// フォークの同じ名前のブランチの MR（source_project_id が違う）は拾わない。
func TestAForkMergeRequestIsNotPicked(t *testing.T) {
	t.Parallel()
	h := newHostSite(t, hostfixture.GitLab)
	done := h.review("impostor", nil, "fetch")
	if done.Code != 0 {
		t.Fatalf("code = %d\n%s", done.Code, done.Stderr)
	}
	mr, _ := decode(t, done.Stdout)["mr"].(map[string]any)
	if mr["number"] != json.Number("7") {
		t.Errorf("mr = %v, want 番号 7", mr)
	}
}

// プロジェクトの id を読めなければ、MR が無いと取り違えず（作り直さず）、理由を言って止める。
func TestAnUnreadableProjectIDStopsWithAReason(t *testing.T) {
	t.Parallel()
	h := newHostSite(t, hostfixture.GitLab)
	done := h.review("impostor", map[string]string{"FAKE_GITLAB_NO_PROJECT": "1"}, "fetch")
	if done.Code == 0 || !strings.Contains(done.Stderr, "プロジェクト") || !strings.Contains(done.Stderr, "id を読めない") || done.Stdout != "" {
		t.Errorf("code = %d, stdout = %q, stderr = %q", done.Code, done.Stdout, done.Stderr)
	}
}

func TestGitLabConfirmAndDecidePassTheTokenOwner(t *testing.T) {
	t.Parallel()
	cases := map[string]struct {
		extra map[string]string
		actor bool
	}{
		"持ち主を引ける":  {nil, true},
		"持ち主を引けない": {map[string]string{"FAKE_GITLAB_NO_USER": "1"}, false},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			h := newHostSite(t, hostfixture.GitLab)
			done := h.review("resolved", c.extra, "confirm", "--phase", "1")
			passed := h.stub.passed()
			if done.Code != 0 || contains(passed, "--actor=lab-reviewer") != c.actor || (!c.actor && anyHasPrefix(passed, "--actor")) {
				t.Fatalf("confirm: code = %d, 渡した引数 = %v\n%s%s", done.Code, passed, done.Stdout, done.Stderr)
			}
			h.stub.reset(t)
			done = h.review("resolved", c.extra, "decide", "1", "--choices", "{}", "--digest", "d")
			passed = h.stub.passed()
			if done.Code != 0 || contains(passed, "--actor=lab-reviewer") != c.actor || contains(passed, "--via=board") != c.actor {
				t.Errorf("decide: code = %d, 渡した引数 = %v\n%s%s", done.Code, passed, done.Stdout, done.Stderr)
			}
		})
	}
}

// マージリクエストを作ったときのホストの返事（GitLab の形）。
const createdMR = `{"iid": 9, "web_url": "https://gitlab.com/acme/widgets/-/merge_requests/9"}`

// requestOn は review-request を打つ。branch を渡すとそのブランチへ移ってから（場面の MR は i0001 だけ）。
// マージリクエストを作る要求（POST .../merge_requests）が出たら、この試験の curl が書き留める。返すのは結果と、書き留めたファイル。
func (h hostSite) requestOn(branch, cwd string, extra map[string]string) (run.Result, string) {
	h.t.Helper()
	if branch != "" {
		h.git("checkout", "-q", "-b", branch)
	}
	sent := filepath.Join(h.tmp, "created.json")
	catcher := filepath.Join(h.tmp, "bin-create")
	shkit.WriteExec(h.t, filepath.Join(catcher, "curl"), "#!/bin/sh\n"+
		"case \" $* \" in\n"+
		"*\" -X POST \"*\"/merge_requests \"*) cat >"+shq(sent)+"\n"+
		"  printf '%s' '"+createdMR+"'\n"+
		"  exit 0 ;;\n"+
		"esac\n"+
		"exec "+shq(filepath.Join(h.bin, "curl"))+" \"$@\"\n")
	env := map[string]string{"PATH": pathWith(catcher, h.bin), "FAKE_GITLAB_STATE": filepath.Join(h.tmp, "notes.json")}
	for k, v := range extra {
		env[k] = v
	}
	if cwd == "" {
		cwd = h.ws
	}
	return h.reviewIn(cwd, "resolved", env, "review-request", "--phase", "1", "--body-file", "x"), sent
}

// 依頼の記録に投稿したアカウント（ノートの author）が入り、打ち直しても 1 度だけ。
func TestRequestRecordsTheAccountThatPosted(t *testing.T) {
	t.Parallel()
	h := newHostSite(t, hostfixture.GitLab)
	state := filepath.Join(h.tmp, "notes.json")
	for range 2 {
		done := h.review("resolved", map[string]string{"FAKE_GITLAB_STATE": state}, "review-request", "--phase", "1", "--body-file", "x")
		if done.Code != 0 {
			t.Fatalf("code = %d\n%s%s", done.Code, done.Stdout, done.Stderr)
		}
		result := decode(t, shkit.Read(t, h.stub.Result))
		// 投稿者は名前でなく id で残す（名前で比べると取り違える）
		if result["host"] != "gitlab" || result["author"] != "201" || result["mr"].(map[string]any)["number"] != json.Number("7") {
			t.Errorf("result = %v", result)
		}
	}
	var notes []any
	if err := json.Unmarshal([]byte(shkit.Read(t, state)), &notes); err != nil || len(notes) != 1 {
		t.Errorf("投稿したノート = %v（%v）, want 1 件", notes, err)
	}
}

// 依頼は、MR のあるブランチで、マージ先が統合先のときだけ投稿する。MR を作るのは ccnavi-start.sh で、依頼は作らない。
func TestRequestChecksTheMergeRequestAndItsTarget(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name   string
		branch string
		setup  func(h hostSite) string // cwd を返す。空なら ws
		stderr string                  // 空なら成功
		more   string
	}{
		{name: "MR が無い親ブランチへは、作らずに止まる", branch: "i0002", stderr: "対応する open のマージリクエストが"},
		{name: "MR の元ブランチが HEAD で、マージ先が統合先なら通る"},
		{
			name:   "マージ先が統合先と違えば投稿しない（統合先は origin/HEAD が決める）",
			setup:  func(h hostSite) string { setOriginHead(h.t, h.ws, "trunk"); return "" },
			stderr: "マージ先（main）が統合先（trunk）ではない",
		},
		{
			name: "統合先が決まらなければ、main に倒さず案内して止まる",
			setup: func(h hostSite) string {
				h.git("update-ref", "-d", "refs/remotes/origin/main")
				return ""
			},
			stderr: "統合先 (origin/HEAD) が決まらない", more: "git remote set-head origin -a",
		},
		{
			// projects/app の統合先は trunk。ワークスペースの origin/HEAD が main でも、projects/app から打てば trunk と比べる
			name: "プロジェクトは自分の origin/HEAD で判定する",
			setup: func(h hostSite) string {
				setOriginHead(h.t, h.ws, "main")
				project := filepath.Join(h.ws, "projects", "app")
				h.initRepo(project, "i0001", gitlabOrigin)
				setOriginHead(h.t, project, "trunk")
				return project
			},
			stderr: "マージ先（main）が統合先（trunk）ではない",
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			h := newHostSite(t, hostfixture.GitLab)
			cwd := ""
			if c.setup != nil {
				cwd = c.setup(h)
			}
			done, sent := h.requestOn(c.branch, cwd, nil)
			if shkit.Exists(sent) {
				t.Errorf("MR を作る要求が出た")
			}
			if c.stderr == "" {
				if done.Code != 0 {
					t.Errorf("code = %d\n%s%s", done.Code, done.Stdout, done.Stderr)
				}
				return
			}
			if done.Code == 0 || !strings.Contains(done.Stderr, c.stderr) || !strings.Contains(done.Stderr, c.more) || strings.Contains(done.Stdout, "posted") {
				t.Errorf("code = %d\n%s%s", done.Code, done.Stdout, done.Stderr)
			}
		})
	}
}

// 依頼の本文は、依頼文に目印が付いたものだけ（案内や追記を足さない）。
func TestRequestPostsTheRequestBodyAsIs(t *testing.T) {
	t.Parallel()
	h := newHostSite(t, hostfixture.GitLab)
	state := filepath.Join(h.tmp, "notes.json")
	done := h.review("resolved", map[string]string{"FAKE_GITLAB_STATE": state}, "review-request", "--phase", "1", "--body-file", "x")
	var notes []struct{ Body string }
	if err := json.Unmarshal([]byte(shkit.Read(t, state)), &notes); done.Code != 0 || err != nil || len(notes) == 0 ||
		!strings.HasPrefix(notes[0].Body, "<!-- ccnavi:request i0001:1 key=k -->") {
		t.Errorf("code = %d, notes = %v, err = %v\n%s%s", done.Code, notes, err, done.Stdout, done.Stderr)
	}
}

// requestWithBody は --body-file に body（ws からの相対か絶対）を渡して review-request を打つ。
func (h hostSite) requestWithBody(body string) run.Result {
	h.t.Helper()
	return h.review("resolved", map[string]string{"FAKE_GITLAB_STATE": filepath.Join(h.tmp, "notes.json")}, "review-request", "--phase", "1", "--body-file", body)
}

// 依頼文が scratchpad/ の中にあれば、成功のあとに request が消す。相対のパスは打った場所から読む。
// 中か外か（`..` の解き方・名前だけ似た場所）は Go の pathcase が見る。
func TestRequestRemovesABodyFileUnderScratchpad(t *testing.T) {
	t.Parallel()
	h := newHostSite(t, hostfixture.GitLab)
	if err := os.MkdirAll(filepath.Join(h.ws, "scratchpad", "sub"), 0o755); err != nil {
		t.Fatal(err)
	}
	body := shkit.Write(t, filepath.Join(h.ws, "scratchpad", "req.md"), "依頼文\n")
	done := h.requestWithBody("scratchpad/sub/../req.md")
	if done.Code != 0 || shkit.Exists(body) || !strings.Contains(done.Stdout, "案内: 依頼文 ") || !strings.Contains(done.Stdout, "消した") {
		t.Errorf("code = %d, 依頼文が残る = %v\n%s%s", done.Code, shkit.Exists(body), done.Stdout, done.Stderr)
	}
}

// scratchpad/ の外（ここでは絶対パスで渡した外のファイル）は消さない。
func TestRequestKeepsABodyFileOutsideScratchpad(t *testing.T) {
	t.Parallel()
	h := newHostSite(t, hostfixture.GitLab)
	outside := shkit.Write(t, filepath.Join(h.tmp, "mine.md"), "利用者のファイル\n")
	done := h.requestWithBody(outside)
	if done.Code != 0 || strings.Contains(done.Stdout, "消した") || !shkit.Exists(outside) {
		t.Errorf("code = %d, 残っている = %v\n%s%s", done.Code, shkit.Exists(outside), done.Stdout, done.Stderr)
	}
}

// 消せなかったときは警告だけで、終了コードは 0 のまま（依頼は済んでいる）。
func TestRequestOnlyWarnsWhenTheBodyFileCannotBeRemoved(t *testing.T) {
	t.Parallel()
	if os.Getuid() <= 0 { // root は書き込めないディレクトリでも消せる。Windows は -1
		t.Skip("書き込めないディレクトリを作れない")
	}
	h := newHostSite(t, hostfixture.GitLab)
	body := shkit.Write(t, filepath.Join(h.ws, "scratchpad", "locked", "req.md"), "依頼文\n")
	locked := filepath.Dir(body)
	if err := os.Chmod(locked, 0o555); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(locked, 0o755) })
	done := h.requestWithBody("scratchpad/locked/req.md")
	if done.Code != 0 || !shkit.Exists(body) || !strings.Contains(done.Stderr, "消せなかった") {
		t.Errorf("code = %d, 残っている = %v\n%s%s", done.Code, shkit.Exists(body), done.Stdout, done.Stderr)
	}
}
