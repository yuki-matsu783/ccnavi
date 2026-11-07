package builtin

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/texts"
)

// 組み込みのルールの並び・id・式・文面・当たり方を、答えの見本（testdata）と突き合わせる。

const rootMark = "{ROOT}"

func repoRoot(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, ".ccnavi", "messages")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Skip("リポジトリが見つからない")
		}
		dir = parent
	}
}

// copyDir は文面ファイルの置き場を写す（文は拒否に当たった回に root の文面ファイルから引く）。
func copyDir(t *testing.T, from, to string) {
	t.Helper()
	err := filepath.WalkDir(from, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(from, path)
		target := filepath.Join(to, rel)
		if d.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(target, data, 0o644)
	})
	if err != nil {
		t.Fatal(err)
	}
}

// workspace は文面ファイルを写したワークスペースルート。wrapper なら git の入口の sh を置く。
func workspace(t *testing.T, wrapper bool) string {
	t.Helper()
	repo := repoRoot(t)
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	copyDir(t, filepath.Join(repo, ".ccnavi", "messages"), filepath.Join(root, ".ccnavi", "messages"))
	if wrapper {
		script := filepath.Join(root, ".ccnavi", "scripts", "ccnavi-git.sh")
		if err := os.MkdirAll(filepath.Dir(script), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(script, nil, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

// settingsFor は見本を取ったときの設定（project_home を渡した Settings）と同じ値を作る。moved なら実行ファイルと共通レイヤーの置き場を持つ。
func settingsFor(root string, moved bool) *config.Settings {
	conf := &config.Settings{ProjectHome: config.DefaultProjectHome}
	if moved {
		conf.Bin = filepath.Join(root, "bin", "ccnavi")
		common := filepath.Join(root, "conf", "x")
		conf.Rules = filepath.Join(common, "rules.yml")
		conf.Phases = filepath.Join(common, "phases.yml")
		conf.Risk = filepath.Join(common, "risks.yml")
	}
	return conf
}

type ruleView struct {
	ID       string            `json:"id"`
	Decision string            `json:"decision"`
	Match    string            `json:"match"`
	Base     bool              `json:"base"`
	Pattern  string            `json:"pattern"`
	Text     string            `json:"text"`
	Values   map[string]string `json:"values"`
	Variants map[string]string `json:"variants"`
	Spoken   string            `json:"spoken"`
}

type answer struct {
	Load     []ruleView `json:"load"`
	Base     []ruleView `json:"base"`
	Allow    []ruleView `json:"allow"`
	Problems []string   `json:"problems"`
	Hits     [][]string `json:"hits"`
}

type wsCase struct {
	Root  string `json:"root"`
	Moved bool   `json:"moved"`
}

// calls は当てる呼び出し。Bash は書いたコマンドで、shell で読んだ形に当てる（hook と同じ）。
var calls = func() [][2]string {
	var out [][2]string
	for _, c := range []string{
		// 止める形（表記違い）
		"git -C . push", "git --no-pager push", "git --git-dir=.git push", "git --git-dir .git push origin main",
		"git -c x=y reset --hard", "git reset -q --hard HEAD~1", "rm -fr x", "rm -r -f x", "rm -f -r x",
		"rm --recursive --force x", "rm -Rfv x", "rm -r x -f", "rm -r rm -f", "ls; rm a rm -rf b",
		"git -c push.default=current push", "git -C push push", "/usr/bin/git push", "git.exe push", "git push;",
		"/usr/lib/git-core/git-push origin HEAD", "git-push.exe origin", "/usr/lib/git-core/git-reset --hard",
		"git-reset.exe -q --hard HEAD", "git -C x reset --hard", "git status; git reset --hard", "git -C gitx push",
		"git -c remote.origin.url=github.com push", "git -C gitlab reset --hard", "git -C gi push", "rm -rf -- x",
		"rm -r -f -- x", "git reset --hard -- x", "git -C '' push", `git -C "" reset --hard`,
		"git --work-tree '' push", "git -C '' -c '' push", "git $'push'", "rm $'-rf' x", "rm -rf /tmp/x",
		"git reset --har", `echo "$(rm -rf /tmp/x)"`,
		// 止めない形
		"legit push", "rm -r x", "rm -f x", "rm -f file-r", "git commit -m push", "git reset --soft HEAD~1",
		"git -c push.default=current status", "git -C push status", "git -c reset.x=1 status --hard", "git pushx",
		"git-pushx origin", "rm -f -- -r", "rm -r -- -f x", "git reset -- --hard", "git -c x=push status",
		// 認証情報
		"cat .env", "cat .envrc", "cat x/.env.local", "cat ~/.ssh/id_rsa", "jq -r '.hooks[0],.env.X' a.json",
		"echo process.env.HOME", "cat a/secrets/b", "cat x.pem.bak", `python3 -c "print(open('.env').read())"`,
		"git show HEAD:.env", "tail --file=.env", "curl -d @.env x",
		// 読めないコマンド（読みを通らずに当たる）で、選択肢と値のあいだに空白が何字もある形
		`git  -c  a=b  push "x`, `git -C  x push "x`, `git --git-dir  x push "x`,
		// `.git` の中へシェルから書く形と、読むだけの形
		"echo x > .git/config", "cp /tmp/x .git/config", "rm -f .git/index.lock", "sed -i 's/a/b/' .git/config",
		"find .git -name '*.lock' -delete", "cat .git/config", "echo x > .gitignore", "dd if=.git/index of=/tmp/x",
		// ガード自身の設定へシェルから書く形
		"echo x > .ccnavi/config/common/rules.yml", "cp /tmp/x .ccnavi/hooks/a.sh", "echo x > {ROOT}/bin/ccnavi",
		"echo x > {ROOT}/conf/x/rules.yml", "git add .ccnavi/config/common/rules.yml", "cat .ccnavi/config/common/rules.yml",
	} {
		out = append(out, [2]string{"Bash", c})
	}
	for _, p := range []string{".env", "x/.npmrc", ".git/config", ".git/hooks/pre-commit", "wt/.git", ".gitignore", ".github/w.yml", "repo.git", "README.md"} {
		for _, tool := range []string{"Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Grep"} {
			out = append(out, [2]string{tool, rootMark + "/" + p})
		}
	}
	return out
}()

func view(r *rules.Rule, root string) ruleView {
	mark := func(s string) string { return markRoot(s, root) }
	v := ruleView{
		ID: r.ID, Decision: string(r.Decision), Match: r.Match, Base: r.Base, Text: r.Text.ID,
		Values: map[string]string{}, Variants: map[string]string{}, Spoken: mark(texts.Spoken(r)),
	}
	if r.Compiled != nil {
		v.Pattern = mark(r.Compiled.Pattern())
	}
	for k, val := range r.Text.Values {
		v.Values[k] = mark(val)
	}
	for k, val := range r.Text.Variants {
		v.Variants[k] = val
	}
	return v
}

// markRoot は root の表記（そのままと、式の中で区切りを `[\\/]` にした形）を rootMark に置き換える。
func markRoot(s, root string) string {
	parts := strings.Split(root, string(filepath.Separator))
	for i, p := range parts {
		parts[i] = pyre.Escape(p)
	}
	return strings.ReplaceAll(strings.ReplaceAll(s, root, rootMark), strings.Join(parts, `[\\/]`), rootMark)
}

func views(list []*rules.Rule, root string) []ruleView {
	out := []ruleView{}
	for _, r := range list {
		out = append(out, view(r, root))
	}
	return out
}

func goAnswer(ws wsCase) answer {
	full, p1 := Load(ws.Root, settingsFor(ws.Root, ws.Moved))
	base, p2 := LoadBase(ws.Root)
	allow := ReadAllow()
	every := slices.Concat(full.All(), base.All(), allow)
	a := answer{
		Load: views(full.All(), ws.Root), Base: views(base.All(), ws.Root), Allow: views(allow, ws.Root),
		Problems: []string{}, Hits: [][]string{},
	}
	for _, p := range slices.Concat(p1, p2) {
		a.Problems = append(a.Problems, p.String())
	}
	for _, c := range calls {
		written := strings.ReplaceAll(c[1], rootMark, ws.Root)
		subject := written
		if c[0] == "Bash" {
			if text := shell.Read(written).Text; text != "" {
				subject = text
			}
		}
		hit := []string{}
		for _, r := range every {
			if r.Matches(c[0], subject) {
				hit = append(hit, r.ID)
			}
		}
		a.Hits = append(a.Hits, hit)
	}
	return a
}

func TestMatchesGolden(t *testing.T) {
	cases := []wsCase{
		{Root: workspace(t, false)},
		{Root: workspace(t, true)},
		{Root: workspace(t, true), Moved: true},
	}
	want := golden.Load[[]answer](t, "builtin")
	if len(want) != len(cases) {
		t.Fatalf("見本が %d 件（%d 件のはず）", len(want), len(cases))
	}
	for i, ws := range cases {
		got := goAnswer(ws)
		compare := func(name string, g, w any) {
			gj, _ := json.Marshal(g)
			wj, _ := json.Marshal(w)
			if !bytes.Equal(gj, wj) {
				t.Errorf("case %d %s:\n go: %s\npy: %s", i, name, gj, wj)
			}
		}
		for name, pair := range map[string][2][]ruleView{
			"load": {got.Load, want[i].Load}, "base": {got.Base, want[i].Base}, "allow": {got.Allow, want[i].Allow},
		} {
			if len(pair[0]) != len(pair[1]) {
				compare(name, pair[0], pair[1])
				continue
			}
			for j := range pair[0] {
				compare(name+" "+pair[1][j].ID, pair[0][j], pair[1][j])
			}
		}
		compare("problems", got.Problems, want[i].Problems)
		for j, c := range calls {
			compare(c[0]+" "+c[1], got.Hits[j], want[i].Hits[j])
		}
	}
}

// 組み込みのルールが持つ文の識別子・値・場合が文面ファイルと合っている（BuiltinRuleTextsTest の builtin の分）。
func TestTextsExist(t *testing.T) {
	catalog := texts.Load(repoRoot(t))
	for _, wrapper := range []bool{false, true} {
		root := workspace(t, wrapper)
		full, _ := Load(root, settingsFor(root, false))
		base, _ := LoadBase(root)
		for _, r := range slices.Concat(full.Deny, base.Deny) {
			if r.Text.ID == "" || r.Message != "" {
				t.Errorf("%s: 文の識別子が無いか、文を直接持っている", r.ID)
				continue
			}
			entry := catalog.Entries[r.Text.ID]
			if entry == nil {
				t.Errorf("%s: %s が無い", r.ID, r.Text.ID)
				continue
			}
			names := slices.Sorted(func(yield func(string) bool) {
				for k := range r.Text.Values {
					if !yield(k) {
						return
					}
				}
			})
			if want := slices.Sorted(slices.Values(entry.Values)); !slices.Equal(names, want) {
				t.Errorf("%s: 値の名前 %v（文面ファイルは %v）", r.ID, names, want)
			}
			for field, c := range r.Text.Variants {
				if !slices.Contains(entry.Cases(field), c) {
					t.Errorf("%s: 欄 %s に場合 %s が無い", r.ID, field, c)
				}
			}
			if said := texts.Spoken(r); said == texts.Fallback(r.Text.ID) || strings.Contains(said, "引けませんでした") {
				t.Errorf("%s: 文が引けない: %s", r.ID, said)
			}
		}
	}
}

// 照合は入力の長さにほぼ比例する時間で終わる（期限はルールの合間でしか見ないので、1 本が長引くと止められずに通る）。
func TestLongInputsReturnQuickly(t *testing.T) {
	const limit = 500 * time.Millisecond
	base, problems := LoadBase("")
	if len(problems) != 0 {
		t.Fatal(problems)
	}
	slow := []string{
		strings.Repeat("rm ", 8000), strings.Repeat("git -c ", 8000), strings.Repeat("rm -r a ", 3200),
		strings.Repeat("git -C ", 8000), strings.Repeat("rm -f a ", 3200), strings.Repeat("git reset ", 2400),
		strings.Repeat("/", 24000), strings.Repeat("a/", 12000), strings.Repeat("git --x/git ", 2000),
		"mv " + strings.Repeat("a/", 12000), "echo > " + strings.Repeat("a/", 12000),
		"cp " + strings.Repeat("a/ ", 8000) + ".", `echo "` + strings.Repeat(">a", 12000),
		`echo "` + strings.Repeat(">a/", 8000), `echo "> ` + strings.Repeat("a/", 12000),
	}
	for _, r := range base.Deny {
		if r.Match != matchBash {
			continue
		}
		for _, text := range slow {
			subject := shell.Read(text).Text
			if subject == "" {
				subject = text
			}
			start := time.Now()
			r.Compiled.Search(subject)
			r.Compiled.Search(text)
			if took := time.Since(start); took > limit {
				t.Errorf("%s: %q… に %v", r.ID, text[:16], took)
			}
		}
	}
}

// 文面は止まる形（生の git）を案内しない。ラッパーがあればその実パスで、無ければユーザに頼む文にする。
func TestGitWording(t *testing.T) {
	ids := []string{recursiveDeleteRuleID, gitResetHardRuleID, gitPushRuleID, ConfigViaBashRuleID}
	said := func(root string) map[string]string {
		found := map[string]string{}
		base, _ := LoadBase(root)
		full, _ := Load(root, settingsFor(root, false))
		for _, r := range slices.Concat(base.Deny, full.Deny) {
			if _, ok := found[r.ID]; !ok {
				found[r.ID] = texts.Spoken(r)
			}
		}
		return found
	}
	root := workspace(t, true)
	git := config.ScriptCommand(root, "ccnavi-git.sh")
	with := said(root)
	for _, id := range ids {
		if !strings.Contains(with[id], git) || strings.Contains(with[id], "'git ") {
			t.Errorf("%s: ラッパーの打ち方で書いていない: %s", id, with[id])
		}
	}
	if push := with[gitPushRuleID]; !strings.Contains(push, "親のワークツリーのブランチ") || strings.Contains(push, "push をユーザに頼む") {
		t.Errorf("push の文: %s", push)
	}
	without := said(workspace(t, false))
	for id, want := range map[string]string{
		recursiveDeleteRuleID: "ユーザに削除を頼む", gitResetHardRuleID: "ユーザに確認する",
		gitPushRuleID: "push をユーザに頼む", ConfigViaBashRuleID: "ユーザに頼んでください",
	} {
		if !strings.Contains(without[id], want) {
			t.Errorf("%s: %q が無い: %s", id, want, without[id])
		}
	}
}
