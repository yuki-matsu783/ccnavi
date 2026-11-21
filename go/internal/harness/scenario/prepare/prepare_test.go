package prepare

import (
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/harness/scenario/host"
)

func write(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

// resolve は追記の対象を top の中の実体にする（実行器の芯の ResolveAppend の代わり）。無ければ Refusal。
func resolve(rel, root, top string) (string, error) {
	p := filepath.Join(root, rel)
	if _, err := os.Stat(p); err != nil {
		return "", &core.Refusal{What: "追記の対象のファイルが無い: " + rel, Next: "パスを直す"}
	}
	return p, nil
}

var hooks = Hooks{ResolveAppend: resolve, CommitExists: func(top, base string) bool { return base == "good" }}

func TestRefusalIsReasonNotesAndOneNextLine(t *testing.T) {
	t.Parallel()
	r := &core.Refusal{What: "理由", Next: "直す", Notes: []string{"a", "b"}}
	if got := r.Error(); got != "理由\n案内: a\n案内: b\n次: 直す" {
		t.Errorf("Error() = %q", got)
	}
}

// 作る形は「すでにあるもの」、追記の形は「まだ無いもの」が回せない原因になる（後片付けで消してしまう・追記先が無い）。
func TestClashesAreInvertedForAppend(t *testing.T) {
	t.Parallel()
	root := base.TempDir(t)
	write(t, filepath.Join(root, "exists.txt"), "x")
	files := []core.File{
		{Rel: "exists.txt", Body: "a"},                // 作る形で既にある: 衝突
		{Rel: "new.txt", Body: "a"},                   // 作る形でまだ無い: 通る
		{Rel: "exists.txt", Body: "a", Append: true},  // 追記で既にある: 通る
		{Rel: "missing.txt", Body: "a", Append: true}, // 追記でまだ無い: 衝突
	}
	if got, want := Clashes(files, root), []string{"exists.txt", "missing.txt"}; !reflect.DeepEqual(got, want) {
		t.Errorf("Clashes = %v, want %v", got, want)
	}
}

// 作ったファイルとディレクトリだけを消す。追記は作ったものに数えず、すでにあったディレクトリは消さない。
func TestCleanupRemovesOnlyWhatSetupMade(t *testing.T) {
	t.Parallel()
	root := base.TempDir(t)
	write(t, filepath.Join(root, "kept", "tracked.txt"), "元\n")
	write(t, filepath.Join(root, "old", "keep.txt"), "k")
	files := []core.File{
		{Rel: "old/new.txt", Body: "n"},
		{Rel: "fresh/deep/new.txt", Body: "n"},
		{Rel: "kept/tracked.txt", Body: "追記\n", Append: true},
	}
	made, dirs, err := MakeFiles(hooks, files, root, root)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(made, []string{"old/new.txt", "fresh/deep/new.txt"}) || !reflect.DeepEqual(dirs, []string{"fresh", filepath.Join("fresh", "deep")}) {
		t.Errorf("made = %v, dirs = %v", made, dirs)
	}
	if got, _ := os.ReadFile(filepath.Join(root, "kept", "tracked.txt")); string(got) != "元\n追記\n" {
		t.Errorf("追記 = %q", got)
	}
	RemoveFiles(made, dirs, root)
	for rel, want := range map[string]bool{"old/new.txt": false, "fresh": false, "old/keep.txt": true, "kept/tracked.txt": true, "old": true} {
		if _, err := os.Stat(filepath.Join(root, rel)); (err == nil) != want {
			t.Errorf("%s: 残っている = %v, want %v", rel, err == nil, want)
		}
	}
	if got, _ := os.ReadFile(filepath.Join(root, "kept", "tracked.txt")); string(got) != "元\n追記\n" {
		t.Error("追記は戻さない（戻すのは setup.reset）")
	}
}

// 追記の対象は先に全部確かめてから書き始める。途中で失敗したら、ここまでに作ったものを残さない。
func TestPartialFailureRemovesWhatWasMade(t *testing.T) {
	t.Parallel()
	root := base.TempDir(t)
	write(t, filepath.Join(root, "blocker"), "ファイル")
	// 1 つ目は作れ、2 つ目は親がファイルなので作れない。
	files := []core.File{{Rel: "ok/a.txt", Body: "a"}, {Rel: "blocker/b.txt", Body: "b"}}
	if _, _, err := MakeFiles(hooks, files, root, root); err == nil {
		t.Fatal("作れないのに成功した")
	}
	if _, err := os.Stat(filepath.Join(root, "ok")); !os.IsNotExist(err) {
		t.Errorf("途中まで作ったものが残っている: %v", err)
	}
	// 追記の対象が使えないなら、何も作る前に断る。
	before := []core.File{{Rel: "x.txt", Body: "x"}, {Rel: "nothing.txt", Body: "a", Append: true}}
	_, _, err := MakeFiles(hooks, before, root, root)
	var refusal *core.Refusal
	if !errors.As(err, &refusal) {
		t.Fatalf("err = %v", err)
	}
	if _, err := os.Stat(filepath.Join(root, "x.txt")); !os.IsNotExist(err) {
		t.Error("追記の確認より前にファイルを作った")
	}
	if _, _, err := MakeFiles(hooks, []core.File{{Rel: "a", Body: "a", Append: true}}, root, ""); err == nil {
		t.Error("対象の判定を通っていないのに追記した")
	}
}

func TestCheckBeforeRun(t *testing.T) {
	t.Parallel()
	root := base.TempDir(t)
	m := &host.Manager{} // docker の接続先が無い = 使えない
	cases := []struct {
		name    string
		files   []core.File
		base    string
		kind    host.Kind
		wantErr string
	}{
		{"通る", nil, "good", host.Bare, ""},
		{"base を指さなければ確かめない", nil, "", host.Real, ""},
		{"無いコミットの base は何も変える前に断る", nil, "bad", host.Real, "コミットとして無い"},
		{"使えない追記の対象は確認文より前に断る", []core.File{{Rel: "none.txt", Body: "a", Append: true}}, "", host.Real, "追記の対象"},
		{"止まった docker は、その回だけ断る", nil, "", host.DockerGitLab, "docker-gitlab の GitLab を使えない"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			err := CheckBeforeRun(hooks, tc.files, tc.base, tc.kind, m, root, root)
			if tc.wantErr == "" {
				if err != nil {
					t.Errorf("err = %v", err)
				}
				return
			}
			if err == nil || !strings.Contains(err.Error(), tc.wantErr) || !strings.Contains(err.Error(), "次: ") {
				t.Errorf("err = %v, want %q と次:", err, tc.wantErr)
			}
		})
	}
}

// 相手を用意できなかったら、origin を確かめるよう言って止まり、state に host を残さない。
func TestStartHostRefusesWhenTheTargetHasNoOrigin(t *testing.T) {
	t.Parallel()
	m := &host.Manager{
		TempDir: base.TempDir(t),
		Git:     func([]string, host.GitOpts) host.GitResult { return host.GitResult{Code: 1} },
	}
	st := &core.State{}
	err := StartHost(m, st, host.Setup{Kind: host.Bare}, "UC-99", base.TempDir(t), "top", func() error { return nil })
	var refusal *core.Refusal
	if !errors.As(err, &refusal) || !strings.Contains(refusal.Next, "origin") || st.Host != nil {
		t.Errorf("err = %v, host = %v", err, st.Host)
	}
	if left, _ := filepath.Glob(filepath.Join(m.TempDir, "ccnavi-host-*")); len(left) != 0 {
		t.Errorf("置き場が残った: %v", left)
	}
	// real は相手を用意しない。
	if err := StartHost(m, st, host.Setup{Kind: host.Real}, "UC-99", "r", "top", nil); err != nil || st.Host != nil {
		t.Errorf("real: err = %v, host = %v", err, st.Host)
	}
}

// bare・real・片付け済みの相手は読まず、環境も作らない。
func TestSnapshotAndEnvAreOnlyForALiveFakeOrDockerHost(t *testing.T) {
	t.Parallel()
	m := &host.Manager{}
	for name, h := range map[string]*host.Host{"なし": nil, "bare": {Kind: host.Bare}, "片付け済み": {Kind: host.FakeGitLab, Down: true}} {
		st := &core.State{Host: h}
		if err := SnapshotHost(m, st, 0, "r", base.TempDir(t), func() error { return nil }); err != nil || len(st.HostSnapshots) != 0 {
			t.Errorf("%s: err = %v, snapshots = %v", name, err, st.HostSnapshots)
		}
		if env, err := HostEnv(m, &core.State{Host: h}, nil); (name == "bare" && (env == nil || err != nil)) || (name != "bare" && env != nil) {
			t.Errorf("%s: env = %v, err = %v", name, env, err)
		}
	}
	// 相手側を読めないとき（接続先が決まらない）はエラーで返し、呼び手が step を止める。
	st := &core.State{Host: &host.Host{Kind: host.DockerGitLab}}
	if err := SnapshotHost(m, st, 0, "r", base.TempDir(t), func() error { return nil }); err == nil {
		t.Error("記録の失敗を飲み込んだ")
	}
}
