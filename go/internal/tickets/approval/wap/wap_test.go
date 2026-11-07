package wap

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
)

// tests/ticket/test_write_and_push.py の FamilyTest・SortTest を、CLI を通さずに移したもの。sh の側（ロック・
// 取り込み・コミット・push）と --record-tree は CLI を通すテストが見る。

const approved = ".ccnavi/approved"

func runGit(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "GIT_CONFIG_GLOBAL="+os.DevNull, "GIT_CONFIG_NOSYSTEM=1",
		"GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@example.com", "GIT_COMMITTER_NAME=t", "GIT_COMMITTER_EMAIL=t@example.com")
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return string(out)
}

type fixture struct {
	t    *testing.T
	root string
	home string
	conf *config.Settings
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git が無い")
	}
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	runGit(t, root, "init", "-q", "-b", "main")
	runGit(t, root, "commit", "-q", "--allow-empty", "-m", "init")
	runGit(t, root, "worktree", "add", "-q", "-b", "i0001", filepath.Join(".claude", "worktrees", "i0001"))
	conf, _ := config.Load(root)
	return &fixture{t, root, filepath.Join(root, ".claude", "worktrees", "i0001"), conf}
}

func (f *fixture) put(rel, text string) string {
	f.t.Helper()
	path := filepath.Join(f.home, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		f.t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		f.t.Fatal(err)
	}
	return rel
}

func (f *fixture) remove(rel string) {
	f.t.Helper()
	if err := os.Remove(filepath.Join(f.home, filepath.FromSlash(rel))); err != nil {
		f.t.Fatal(err)
	}
}

func (f *fixture) commit(message string) {
	runGit(f.t, f.home, "add", "-A")
	runGit(f.t, f.home, "commit", "-q", "--allow-empty", "-m", message)
}

// sort は分けの行を (分け, パス) の名前の順で返す。
func (f *fixture) sort(since string) map[string]string {
	f.t.Helper()
	var out, errOut bytes.Buffer
	if code := Sort(&out, &errOut, f.conf, f.root, "i0001", since); code != 0 {
		f.t.Fatalf("%d: %s", code, errOut.String())
	}
	lines := strings.Split(strings.TrimSuffix(out.String(), "\n"), "\n")
	if lines[0] != Head {
		f.t.Fatalf("頭の行が無い: %q", out.String())
	}
	got := map[string]string{}
	for _, line := range lines[1:] {
		if kind, path, ok := strings.Cut(line, " "); ok && kind != "why" {
			got[path] = kind
		}
	}
	return got
}

func TestSort(t *testing.T) {
	marks := approved + "/phases/i0001/"
	cases := []struct {
		name  string
		setup func(f *fixture) map[string]string
		since bool
	}{
		{"変わっていなければ何も無い", func(f *fixture) map[string]string { return map[string]string{} }, false},
		{"hook のマーカーは b", func(f *fixture) map[string]string {
			return map[string]string{
				f.put(marks+"2.pending", `{"review": true, "source": "common", "at": "t"}`): "b",
				f.put(marks+"3.skipped", `{"deferred_to": 4, "at": "t"}`):                   "b",
			}
		}, false},
		{"別の親・全角の数字のマーカーは b にしない", func(f *fixture) map[string]string {
			return map[string]string{
				f.put(approved+"/phases/i0099/2.pending", `{"review": true, "at": "t"}`): "d",
				f.put(marks+"２.pending", `{"review": true, "at": "t"}`):                  "d",
			}
		}, false},
		{"偽のマーカーとユーザの判断のマーカー", func(f *fixture) map[string]string {
			return map[string]string{
				f.put(marks+"1.reviewed", `{"by": "decide", "at": "t"}`):                  "c",
				f.put(marks+"2.skipped", `{"by": "close-early", "at": "t"}`):              "c",
				f.put(marks+"3.pending", `{"review": true, "at": "t", "reviewed": true}`): "d",
				f.put(marks+"4.pending", "\xff"):                                          "d",
				f.put(marks+"close-early.json", "{}"):                                     "c",
				f.put(marks+"accepted.json", "{}"):                                        "c",
			}
		}, false},
		{"書き直した hook のマーカーは d", func(f *fixture) map[string]string {
			rel := f.put(marks+"2.pending", `{"review": true, "at": "t"}`)
			f.commit("mark")
			f.put(rel, `{"review": true, "at": "u"}`)
			return map[string]string{rel: "d"}
		}, false},
		{"ユーザの判断が一緒に書く移動とマーカーの消去は c", func(f *fixture) map[string]string {
			review := f.put("wip/proposals/review/i0001-01-01.md", "x\n")
			mark := f.put(marks+"1.pending", `{"review": true, "at": "t"}`)
			early := f.put(approved+"/doing/i0001-01-02.md", "y\n")
			f.commit("in review")
			f.remove(review)
			f.remove(mark)
			f.remove(early)
			return map[string]string{
				review: "c", mark: "c", early: "c",
				f.put(approved+"/done/i0001-01-01.md", "x\n"): "c",
				f.put(approved+"/done/i0001-01-02.md", "y\n"): "c",
			}
		}, false},
		{"フロー・承認・ほかのファイル", func(f *fixture) map[string]string {
			return map[string]string{
				f.put(approved+"/flows/i0001-01-01.yml", "steps: []\n"): "c",
				f.put(approved+"/doing/i0001-01-02.md", "x\n"):          "c",
				f.put(approved+"/notes.txt", "x\n"):                     "d",
				f.put("wip/proposals/review/i0001-01-01.md", "x\n"):     "d",
				f.put(marks+"workflow.yml", "order: dag\n"):             "d",
				f.put(approved+"/done/i0001-01-09.md", "x\n"):           "d",
			}
		}, false},
		{"record-risk の記録は keep、一時ファイルは skip", func(f *fixture) map[string]string {
			return map[string]string{
				f.put(marks+"i0001-01-01.judge.json", "{}"):            "keep",
				f.put(approved+"/flows/.i0001-01-01.yml.123.tmp", "x"): "skip",
				f.put(approved+"/doing/.i0001.md.abc.part", "x"):       "skip",
				f.put(approved+"/doing/i0001.md.ccnavi-sync", "x"):     "skip",
			}
		}, false},
		{"版から後にコミットに入った変更", func(f *fixture) map[string]string {
			pending := f.put(marks+"2.pending", `{"review": true, "at": "t"}`)
			reviewed := f.put(marks+"1.reviewed", `{"by": "decide", "at": "t"}`)
			judge := f.put(marks+"i0001-01-01.judge.json", "{}")
			f.put("src/code.py", "x = 1\n")
			f.commit("unsent")
			// コミット済みの judge.json は見分けない（未コミットのときだけ keep）。
			return map[string]string{pending: "b", reviewed: "c", judge: "d"}
		}, true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := newFixture(t)
			f.commit("settle")
			since := ""
			if c.since {
				since = strings.TrimSpace(runGit(t, f.home, "rev-parse", "HEAD"))
			}
			want := c.setup(f)
			if got := f.sort(since); !reflect.DeepEqual(got, want) {
				t.Errorf("\n got %v\nwant %v", got, want)
			}
		})
	}
}

func TestSortRefusesWithoutTheParentTree(t *testing.T) {
	f := newFixture(t)
	var out, errOut bytes.Buffer
	if code := Sort(&out, &errOut, f.conf, f.root, "i0099", ""); code != config.ExitError || out.Len() != 0 ||
		!strings.Contains(errOut.String(), "親子のチケット i0099 の親のワークツリーが決まらない") {
		t.Errorf("%d %q %q", code, out.String(), errOut.String())
	}
	if code := Sort(&out, &errOut, f.conf, f.root, "i0001", "nope"); code != config.ExitError ||
		!strings.Contains(errOut.String(), "nope..HEAD の差分を読めない") {
		t.Errorf("%d %q", code, errOut.String())
	}
}

func family(t *testing.T, f *fixture, id string) map[string][]string {
	t.Helper()
	var out bytes.Buffer
	if code := Family(&out, f.conf, f.root, id); code != 0 {
		t.Fatal(code)
	}
	lines := strings.Split(strings.TrimSuffix(out.String(), "\n"), "\n")
	if lines[0] != Head {
		t.Fatalf("頭の行が無い: %q", out.String())
	}
	got := map[string][]string{}
	for _, line := range lines[1:] {
		key, value, _ := strings.Cut(line, " ")
		got[key] = append(got[key], value)
	}
	return got
}

func TestFamily(t *testing.T) {
	f := newFixture(t)
	got := family(t, f, "i0001-01-01")
	if got["family"][0] != "i0001" || got["target"][0] != "no" || !strings.Contains(got["why"][0], "origin がありません") ||
		got["tree"] != nil {
		t.Errorf("origin が無い: %v", got)
	}
	runGit(t, f.root, "remote", "add", "origin", filepath.Join(f.root, "nowhere.git"))
	got = family(t, f, "i0001")
	want := map[string][]string{
		"family": {"i0001"}, "repo": {"self"}, "branch": {"i0001"}, "approved": {approved},
		"review": {"wip/proposals/review"}, "target": {"yes"}, "tree": {f.home},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("\n got %v\nwant %v", got, want)
	}
	runGit(t, f.home, "checkout", "-q", "-b", "elsewhere")
	got = family(t, f, "i0001")
	if got["target"][0] != "stop" || !strings.Contains(got["why"][0], "合わない") || len(got["hint"]) == 0 ||
		got["branch_refused"] == nil {
		t.Errorf("別のブランチ: %v", got)
	}
	runGit(t, f.root, "worktree", "remove", "--force", f.home)
	if got = family(t, f, "i0001"); got["target"][0] != "stop" || !strings.Contains(got["why"][0], "親のワークツリー") {
		t.Errorf("親のワークツリーが無い: %v", got)
	}
}

func TestFamilyOfAbsolutePlaces(t *testing.T) {
	f := newFixture(t)
	f.conf.Approved = filepath.Join(f.root, "elsewhere")
	got := family(t, f, "i0001")
	if got["target"][0] != "no" || got["approved"] != nil || !strings.Contains(got["why"][0], "絶対パス") {
		t.Errorf("%v", got)
	}
}

func TestFamilyOf(t *testing.T) {
	for id, want := range map[string]string{"i0001-01-02": "i0001", "i0001": "i0001", "web-1-x-10-99": "web-1-x", "a-1-2": "a-1-2"} {
		if got := FamilyOf(id); got != want {
			t.Errorf("%s: %s", id, got)
		}
	}
}

func TestOneLine(t *testing.T) {
	got := []string{oneLine(" a \t b\n\x1cc "), oneLine("")}
	if want := []string{"a b c", ""}; !reflect.DeepEqual(got, want) {
		t.Errorf("%q", got)
	}
}
