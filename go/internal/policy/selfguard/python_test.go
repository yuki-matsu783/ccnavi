package selfguard

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"math/rand/v2"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/policy/rules"
)

// Python の selfguard_shell と突き合わせる。式の文字列が 1 字も違わないこと、組んだルールが同じ呼び出しに同じ答えを
// 返すこと（pyre と除外の関数を含む）を、決めた入力と乱数の入力で見る。Python の答えは testdata に写してある。

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

func fileExists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

type guardCase struct {
	Root     string      `json:"root"`
	Bin      string      `json:"bin"`
	Home     string      `json:"home"`
	Common   []string    `json:"common"`
	Log      string      `json:"log"`
	State    string      `json:"state"`
	Tool     string      `json:"tool"`
	Cwd      string      `json:"cwd"`
	Subjects [][2]string `json:"subjects"`
	Commands []string    `json:"commands"`
	// Env はこの組で使う PATH（写しを持たない突き合わせだけ）。
	Env string `json:"-"`
}

type ruleView struct {
	ID       string            `json:"id"`
	Match    string            `json:"match"`
	Pattern  string            `json:"pattern"`
	Text     string            `json:"text"`
	Values   map[string]string `json:"values"`
	Variants map[string]string `json:"variants"`
	Exempt   bool              `json:"exempt"`
}

type guardResult struct {
	Shell      string      `json:"shell"`
	Binary     string      `json:"binary"`
	BinaryCopy string      `json:"binaryCopy"`
	Rules      []ruleView  `json:"rules"`
	Subjects   [][2]string `json:"subjects"`
	Hits       [][]string  `json:"hits"`
}

func goGuard(c guardCase, subjects [][2]string) guardResult {
	rs := &rules.RuleSet{Version: rules.Version}
	AddRules(rs, Options{Bin: c.Bin, ProjectHome: c.Home, Root: c.Root, CommonFiles: c.Common, Log: c.Log, State: c.State, Tool: c.Tool, Cwd: c.Cwd})
	AddNamedRules(rs, c.Home, c.Root)
	res := guardResult{
		Shell: ShellRegex(c.Root, c.Bin, c.Home, c.Common), Binary: BinaryClause(c.Bin),
		BinaryCopy: binaryClause(c.Bin, copyForm), Rules: []ruleView{}, Subjects: subjects, Hits: [][]string{},
	}
	for _, r := range rs.Deny {
		v := ruleView{ID: r.ID, Match: r.Match, Text: r.Text.ID, Values: r.Text.Values, Variants: r.Text.Variants, Exempt: r.Exempt != nil}
		if v.Values == nil {
			v.Values = map[string]string{}
		}
		if v.Variants == nil {
			v.Variants = map[string]string{}
		}
		if r.Compiled != nil {
			v.Pattern = r.Compiled.Pattern()
		}
		res.Rules = append(res.Rules, v)
	}
	for _, s := range subjects {
		hit := []string{}
		for _, r := range rs.Deny {
			if r.Matches(s[0], s[1]) {
				hit = append(hit, r.ID)
			}
		}
		res.Hits = append(res.Hits, hit)
	}
	return res
}

func compareGuards(t *testing.T, name string, cases []guardCase) {
	t.Helper()
	want := golden.Load[[]guardResult](t, name)
	bad := 0
	for i, c := range cases {
		// 読みは Python の shellread の答えをそのまま当てる（shell.Read の突き合わせは infra/shell が持つ）。
		got := goGuard(c, want[i].Subjects)
		if got.Shell != want[i].Shell || got.Binary != want[i].Binary || got.BinaryCopy != want[i].BinaryCopy {
			bad++
			t.Errorf("case %d %+v: 式が違う\n got  %q\n want %q", i, c, got.Shell, want[i].Shell)
			continue
		}
		gj, _ := json.Marshal(got.Rules)
		wj, _ := json.Marshal(want[i].Rules)
		if !bytes.Equal(gj, wj) {
			bad++
			t.Errorf("case %d: ルールが違う\n got  %s\n want %s", i, gj, wj)
			continue
		}
		for k := range got.Hits {
			if strings.Join(got.Hits[k], ",") != strings.Join(want[i].Hits[k], ",") {
				bad++
				if bad <= 20 {
					t.Errorf("case %d %q %q: got %v want %v", i, got.Subjects[k][0], got.Subjects[k][1], got.Hits[k], want[i].Hits[k])
				}
			}
		}
	}
	if bad > 0 {
		t.Errorf("%d 件が Python と違う", bad)
	}
}

// fixedRoot は写しを機械に依らせないための、無い場所のルート（realpath が綴りを変えない）。
const fixedRoot = "/nonexistent/ws"

// guardCommands は保護の表（tests/config/test_config_union_holes.py・tests/guard/test_selfguard.py の命令）と、
// 読むだけで止めない命令。Python の shellread で読んでから当てる。
var guardCommands = []string{
	// ディレクトリごと消す・退かす
	"rm -rf .ccnavi", "rm -rf projects/lib/.ccnavi", "mv .ccnavi .ccnavi.bak", "mv projects/lib/.ccnavi /tmp/keep",
	"cp /tmp/x .ccnavi", "rm -rf .ccnavi && echo done", "rm -rf .claude", "mv .claude .claude.bak", "cp /tmp/x .claude",
	// 別名と読むだけ
	"rm -r .claudexyz", "rm -r .ccnavixyz", "mv .ccnavi-notes.md notes.md", "rm -r .claude-backup", "rm -r .claude/worktrees/w1",
	"cp .ccnavi/config/project/rules.yml /tmp/x", "cat .ccnavi/config/project/rules.yml",
	"cp .ccnavi/config/common/rules.yml /tmp/x", "cp /nonexistent/ws/.ccnavi/config/common/rules.yml /tmp/x",
	"ln -s .ccnavi/config/common/phases.yml /tmp/x", "install .ccnavi/config/common/risks.yml /tmp/x",
	"cp /tmp/x .ccnavi/config/common/rules.yml", "cp /tmp/x /nonexistent/ws/.ccnavi/config/common/rules.yml",
	"cp /tmp/x .ccnavi/config/common/rules.yml && echo done", "ln -sf /tmp/x .ccnavi/config/common/rules.yml",
	"echo x > .ccnavi/config/common/rules.yml", "rm .ccnavi/config/common/rules.yml",
	"cp -r /tmp/scripts .ccnavi && echo done", "cp /tmp/x .claude; echo done", "cp /tmp/x logs/state && echo done",
	"cp -r /tmp/scripts .ccnavi 2>&1 | cat", "cp -t .ccnavi/scripts /tmp/count.sh",
	// -t の書き方
	"cp -t .ccnavi/config/common /tmp/x", "cp --target-directory=.ccnavi/config/common /tmp/x",
	"cp -t .ccnavi /tmp/x", "cp -t.ccnavi /tmp/x", "cp -vt .ccnavi/config/common /tmp/x", "cp -rft .ccnavi/scripts /tmp/count.sh",
	"cp --target-directory .ccnavi/config/common /tmp/x", "cp --target=.ccnavi/config/common /tmp/x",
	"cp --t .ccnavi/config/common /tmp/x", "ln -st .ccnavi/config/common /tmp/rules.yml",
	"ln --target-directory=.ccnavi/config/common /tmp/rules.yml", "install -Dt .ccnavi/config/common /tmp/rules.yml",
	"install -m 644 -t .ccnavi/config/common /tmp/rules.yml", "cp -t .claude/hooks /tmp/x.sh", "cp -t .claude /tmp/settings.json",
	"cp -t .claude/ /tmp/settings.json", "cp -t logs/state /tmp/x", "cp -t /abs/path/.ccnavi/config/common /tmp/x",
	// 行き先のあとの選択肢
	"cp /tmp/x .ccnavi/config/common/rules.yml -f", "cp /tmp/x .ccnavi/config/common/rules.yml -f -v",
	"cp /tmp/x .ccnavi/config/common/rules.yml -S .bak", "cp /tmp/x .ccnavi/config/common/rules.yml --suffix .bak --verbose",
	"cp /tmp/x .ccnavi/config/common/rules.yml --backup=numbered", "install /tmp/x .ccnavi/config/common/rules.yml -m 644 -o root -g root",
	"ln -s /tmp/x .ccnavi/config/common/rules.yml --force", "cp /tmp/x .ccnavi -r",
	"cp /tmp/x .ccnavi/config/common/rules.yml -f 2>/dev/null", "cp /tmp/x .ccnavi/config/common/rules.yml -S -t",
	"cp /tmp/x .ccnavi/config/common/rules.yml --",
	"cp -Svt /tmp/x .ccnavi/config/common/rules.yml", "cp -S -t /tmp/x .ccnavi/config/common/rules.yml",
	"cp --suffix -t /tmp/x .ccnavi/config/common/rules.yml", "install -m -t /tmp/x .ccnavi/config/common/rules.yml",
	"cp -- -t /tmp/x .ccnavi/config/common/rules.yml", "cp /tmp/x '' .ccnavi/config/common/rules.yml",
	"install /tmp/x --strip .ccnavi/config/common/rules.yml",
	// 迂回
	"echo go && cp /tmp/x .ccnavi/config/common/rules.yml", "cat /tmp/x | cp /dev/stdin .ccnavi/config/common/rules.yml",
	`cp /tmp/x ".ccnavi/config/common/"rules.yml`, "cp /tmp/x .CCNAVI/Common/RULES.yml", "CP /tmp/x .ccnavi/config/common/rules.yml",
	`cp /tmp/x '.ccnavi\config\common\rules.yml'`, "cp -T /tmp/x .ccnavi/config/common/rules.yml",
	"sudo cp /tmp/x .ccnavi/config/common/rules.yml", "sh -c 'cp /tmp/x .ccnavi/config/common/rules.yml'",
	"cp /tmp/x .ccnavi/config/common/rules.yml {fd}>/dev/null", "cp /tmp/settings.json .claude/", "mv /tmp/settings.json .claude/",
	// 読むコピー
	"cp -r .ccnavi /tmp/keep", "cp -v .ccnavi/config/common/rules.yml .ccnavi/config/common/phases.yml /tmp/out/",
	"cp .ccnavi/config/common/rules.yml -t /tmp/out", "cp -t /tmp/out .ccnavi/config/common/rules.yml",
	"cp -T .ccnavi/config/common /tmp/out", "cp .claude/settings.json /tmp/x", "cp .claude/hooks/x.sh /tmp/x",
	"cp logs/decisions.jsonl /tmp/x", "cp -r logs/state /tmp/x", "cp .ccnavi/scripts/ccnavi-git.sh /tmp/x",
	"cp .ccnavi/config/common/rules.yml /tmp/x 2>/dev/null",
	// 書き込みの側
	"mv .ccnavi/config/common/rules.yml /tmp/x", "tee .ccnavi/config/common/rules.yml < /tmp/x",
	"dd if=/tmp/x of=.ccnavi/config/common/rules.yml", "truncate -s 0 .ccnavi/config/common/rules.yml",
	"patch .ccnavi/config/common/rules.yml /tmp/p", "shred .ccnavi/config/common/rules.yml",
	"sed -i s/deny/allow/ .ccnavi/config/common/rules.yml", "sed -n 1,20p .ccnavi/config/common/rules.yml",
	"sed -n s/feature-id/x/p .ccnavi/config/common/rules.yml",
	"cp .ccnavi/config/common/rules.yml /tmp/a > .ccnavi/config/common/phases.yml",
	`echo "a > .ccnavi/x"`, `echo x > "a b/.ccnavi/x"`, `echo x > "New folder (2)/.ccnavi/x"`, `echo ">" .ccnavi/x`,
	// 守るものが入っているディレクトリ
	"cp /tmp/decisions.jsonl logs/", "mv /tmp/decisions.jsonl logs/", "cp /tmp/decisions.jsonl logs", "cp /tmp/decisions.jsonl ./logs/",
	"cp /tmp/decisions.jsonl logs/.", "cp /tmp/decisions.jsonl /repo/logs//", "cp /tmp/decisions.20260101.jsonl logs/",
	"cp -r /tmp/state logs/", "mv /tmp/state logs/", "cp /tmp/notes.txt /tmp/decisions.jsonl logs/",
	"cp /tmp/decisions.jsonl.bak /tmp/decisions.jsonl logs/", "cp -- /tmp/decisions.jsonl -t logs/",
	"cp -S -t /tmp/decisions.jsonl logs/", "cp -t logs /tmp/decisions.jsonl", "cp -tlogs /tmp/decisions.jsonl",
	"cp --target logs /tmp/decisions.jsonl", "cp /tmp/decisions.jsonl -t logs", "CP /tmp/DECISIONS.JSONL LOGS/",
	`cp '/tmp\decisions.jsonl' 'logs\'`, "cp /tmp/* logs/", "cp /tmp/dec?sions.jsonl logs/", `cp "$F" logs/`,
	"cp $(ls /tmp/x) logs/", "cp -r /tmp/d/. logs/", "cp -r . logs/", "cp -rT /tmp/d logs", "cp -r --no-target-directory /tmp/d logs",
	"cp -t logs /tmp/*", "cp -r /tmp/.ccnavi .", "cp -r /tmp/.claude ./", "cp -r /tmp/logs .", "mv /tmp/.ccnavi .",
	"cp -rt . /tmp/.ccnavi", "cp -r /tmp/.ccnavi /nonexistent/ws", "cp -r /tmp/.ccnavi /nonexistent/ws/",
	"cp /tmp/notes.txt logs/", "cp -t logs /tmp/notes.txt", "cp /tmp/decisions.jsonl.bak logs/", "cp /tmp/mydecisions.jsonl logs/",
	"cp /tmp/statement logs/", "cp /tmp/decisions.jsonl backlogs/", "cp /tmp/decisions.jsonl logs/old/", "cp logs/a.log logs/b.log",
	"cp -r /tmp/src .", "cp -r /tmp/.ccnavi /tmp/keep/",
	// 記録・git・リンク・置き場
	"echo x >> logs/decisions.jsonl", "rm logs/decisions.2026.jsonl", "rm -r logs/state", "rm logs/git-wrapper.log",
	"echo x > .git/config", "cat .git/config", "cp .git/config /tmp/x", "cp /tmp/x .git/config", "rm -rf .git", "rm x.git/y",
	"sed -i s/a/b/ .git/config", "sed -i s/.git/x/ f", "sed -e s/a/b/ -i .git/config", "tee /tmp/o < .git/config",
	"dd if=.git/config of=/tmp/x", "dd if=/tmp/x of=.git/config", "find .git -delete", "find . -path ./.git -prune -o -delete",
	"find .git -name x -exec rm {} ;", "ls .git | xargs rm", "cp -r /tmp/x/.git .", "cp ../src/*.py .",
	"ln -s /nonexistent/ws/.claude/settings.json wip/handoff/x", "cp -l .claude/settings.json wip/handoff/x", "cp --li a b",
	"ln -s a b", "link a handoff/b", "rm /nonexistent/ws/wip/handoff/.ccnavi/x", "rm wip/handoff/.ccnavi/x",
	"git rm /nonexistent/ws/wip/handoff/.ccnavi/x", "rm /nonexistent/ws/wip/handoff/x /nonexistent/ws/.ccnavi/x",
	"echo x > ccnavi-git.sh", "echo x > .ccnavi/scripts/ccnavi-git.sh",
	"cp /tmp/ccnavi bin/ccnavi", "cp /tmp/ccnavi dist/", "cp /tmp/x .ccnavi/bin/linux-x86_64/ccnavi", "cp /tmp/x /opt/ccnavi/bin/ccnavi",
	"echo x > /opt/conf/rules.yml", "cp /tmp/x /opt/conf/rules.yml", "cp /opt/conf/rules.yml /tmp/x", "echo x > myrules.yml",
	"echo x > conf/x/rules.yml", "cp /tmp/rules.yml conf/x/", "cp /tmp/x conf", "rm -r conf",
}

var pathSubjects = [][2]string{
	{"Write", "/nonexistent/ws/.claude/settings.json"}, {"Edit", "/nonexistent/ws/.claude/settings.local.json"},
	{"Write", "/nonexistent/ws/.claude/hooks/x.sh"}, {"Write", "/nonexistent/ws/.ccnavi/scripts/x.sh"},
	{"Write", "/nonexistent/ws/.ccnavi/config/common/rules.yml"}, {"Write", "/nonexistent/ws/.Ccnavi/config/project/rules.yml"},
	{"Write", "/nonexistent/ws/.claude/worktrees/w1/.ccnavi/config/common/rules.yml"},
	{"Write", "/nonexistent/ws/wip/handoff/.ccnavi/scripts/x.sh"}, {"Write", "/nonexistent/ws/wip/handoff/.claude/settings.json"},
	{"Write", "/nonexistent/ws/docs/wip/handoff/.ccnavi/x"}, {"Write", "/nonexistent/ws/wip/handoff/../.ccnavi/x"},
	{"Write", "/nonexistent/ws/projects/lib/wip/handoff/.ccnavi/x"}, {"Write", "/nonexistent/ws/logs/decisions.jsonl"},
	{"Write", "/nonexistent/ws/logs/decisions.2026.jsonl"}, {"Write", "/nonexistent/ws/logs/state/x"},
	{"Write", "/nonexistent/ws/logs/archive"}, {"Write", "/nonexistent/ws/logs/notes.txt"}, {"Write", "/opt/log/d.jsonl"},
	{"Write", "/opt/log/d.1.jsonl"}, {"Write", "/opt/state/x"}, {"Write", "/opt/conf/rules.yml"}, {"Write", "/opt/conf/other.yml"},
	{"Write", "/nonexistent/ws/conf/x/rules.yml"}, {"Write", "/opt/ccnavi/bin/ccnavi"}, {"Write", "/opt/ccnavi/bin/ccnavi.old"},
	{"Write", "/nonexistent/ws/.ccnavi/bin/linux-x86_64/ccnavi"}, {"Write", "/nonexistent/ws/.ccnavi/scripts/ccnavi-launcher.sh"},
	{"Write", "/nonexistent/ws/x/.claude/settings.json"}, {"NotebookEdit", "/nonexistent/ws/.ccnavi/a.ipynb"},
	{"Read", "/nonexistent/ws/.ccnavi/config/common/rules.yml"},
}

// guardConfigs は設定の組（既定・動かした ccnavi ディレクトリ・ルートの外の共通レイヤー・振り分けの sh など）。
func guardConfigs() []guardCase {
	common := func(dir string) []string {
		return []string{dir + "/rules.yml", dir + "/phases.yml", dir + "/risks.yml"}
	}
	return []guardCase{
		{Root: fixedRoot, Home: ".ccnavi", Common: common(fixedRoot + "/.ccnavi/config/common")},
		{Root: fixedRoot, Home: ".ccnavi", Common: common(fixedRoot + "/.ccnavi/config/common"), Tool: "Bash"},
		{Root: fixedRoot, Home: ".ccnavi", Common: common(fixedRoot + "/.ccnavi/config/common"), Tool: "Write"},
		{Root: fixedRoot, Home: "conf/x", Common: common(fixedRoot + "/conf/x/config/common"), Bin: "/opt/ccnavi/bin/ccnavi",
			Log: "/opt/log/d.jsonl", State: "/opt/state"},
		{Root: fixedRoot, Home: ".ccnavi", Common: common("/opt/conf"), Bin: fixedRoot + "/.ccnavi/scripts/ccnavi-launcher.sh"},
		{Root: fixedRoot, Home: "/x/", Common: []string{"", fixedRoot + "/rules.yml", ""}, Bin: "ccnavi-launcher.sh"},
		{Root: fixedRoot, Home: `a\b`, Common: common(fixedRoot + "/a/b/c"), Bin: `C:\tools\ccnavi.exe`, Cwd: fixedRoot + "/wip/handoff"},
		{Root: "", Home: ".ccnavi", Common: common("/opt/conf"), Bin: "bin/ccnavi"},
		{Root: "", Home: "", Bin: "./../ccnavi"},
		{Root: fixedRoot, Home: "dir with space/.c+c", Common: common("/opt/a (1)/c$"), Bin: "/opt/[x]/ccnavi"},
		{Root: "/", Home: ".ccnavi", Common: common("/.ccnavi/config/common")},
	}
}

// randomSubjects は守る場所・書き込みの形・選択肢・目印を混ぜた、読んだあとの形の文字列。
func randomSubjects(rnd *rand.Rand, n int) [][2]string {
	words := []string{
		"cp", "mv", "rm", "ln", "install", "sed", "tee", "dd", "truncate", "patch", "shred", "find", "xargs", "cat", "echo",
		"CP", "Rm", "-i", "-i.bak", "-ni", "--in-place", "--i", "-n", "-e", "-r", "-f", "-t", "-T", "-vt", "-S", "-m", "-Svt",
		"--target-directory=", "--suffix", "--no-target-directory", "--", "-", ">", ">>", "&>", "2", "<", "|", "&&", ";",
		".ccnavi", ".ccnavi/config/common/rules.yml", ".Ccnavi/x", ".claude", ".claude/", ".claude/settings.json",
		".claude/settings.local.json", ".claude/hooks/x", ".claude/worktrees/w", "logs", "logs/", "logs/decisions.jsonl",
		"logs/decisions.1.jsonl", "logs/state", "logs/archive/x", "decisions.jsonl", "state", ".", "..", "./", "*", "$x", "`x`",
		"{a,b}", "/tmp/x", "/nonexistent/ws", "/nonexistent/ws/.ccnavi", "conf/x/rules.yml", "conf", "rules.yml", "myrules.yml",
		"/opt/conf/rules.yml", "/opt/ccnavi/bin/ccnavi", "bin/ccnavi", "dist/", ".ccnavi/bin/linux-x86_64/ccnavi",
		"ccnavi-launcher.sh", "scripts/ccnavi-launcher.sh", "ccnavi-git.sh", ".git", ".git/config", "x.git/y", ".git.",
		"of=.git/x", "if=.git/x", "-delete", "-exec", "-path", "wip/handoff/x", "/nonexistent/ws/wip/handoff/a", "handoff",
		"--link", "-s", "-l", "a=1", "sudo", "env", "s/a/b/", "'.git'", `"`, "{fd}", "1",
	}
	seps := []string{" ", " ", " ", " ", "\x00", "\x01", ""}
	var out [][2]string
	for range n {
		var b strings.Builder
		k := 1 + rnd.IntN(9)
		for j := range k {
			if j > 0 {
				b.WriteString(seps[rnd.IntN(len(seps))])
			}
			b.WriteString(words[rnd.IntN(len(words))])
		}
		tool, subject := "Bash", b.String()
		if rnd.IntN(6) == 0 {
			// パスは NUL を含まない（Python は realpath で ValueError になる）。
			tool, subject = "Write", strings.ReplaceAll(subject, "\x00", "/")
		}
		out = append(out, [2]string{tool, subject})
	}
	return out
}

func TestGuardRulesAgainstPython(t *testing.T) {
	if ospath.Windows {
		t.Skip("写しは POSIX のパスで取った")
	}
	t.Setenv("PATH", "/nonexistent/path")
	rnd := rand.New(rand.NewPCG(7, 11))
	var cases []guardCase
	for i, c := range guardConfigs() {
		c.Subjects = append(append([][2]string{}, pathSubjects...), randomSubjects(rnd, 400)...)
		c.Commands = guardCommands
		if i%3 != 0 {
			c.Commands = nil
		}
		cases = append(cases, c)
	}
	compareGuards(t, "guard", cases)
}

// TestGuardRulesRandomAgainstPython は乱数で組んだ設定（区切り・空白・式の記号・大文字・非 ASCII を含むパス）と、
// 乱数の呼び出しで突き合わせる。写しは持たない（Python があるときだけ回る）。
func TestGuardRulesRandomAgainstPython(t *testing.T) {
	if ospath.Windows {
		t.Skip("POSIX のパスで組む")
	}
	t.Setenv("PATH", "/nonexistent/path")
	rnd := rand.New(rand.NewPCG(11, 3)) // 見本と比べるので種を固定する
	parts := []string{"a", "B", ".ccnavi", ".claude", "logs", "conf", "x y", "(1)", "[z]", "$v", "a+b", "日本", "Straße", ".", "..", "", "bin",
		"ccnavi", "ccnavi-launcher.sh", "scripts", "rules.yml", "phases.yml", "risks.yml", "c.d", "-t", "*", "^", "{}", "|"}
	path := func(abs bool) string {
		var b []string
		for range 1 + rnd.IntN(4) {
			b = append(b, parts[rnd.IntN(len(parts))])
		}
		sep := "/"
		if rnd.IntN(8) == 0 {
			sep = `\`
		}
		p := strings.Join(b, sep)
		if abs {
			p = "/nonexistent/" + p
		}
		return p
	}
	maybe := func(s string) string {
		if rnd.IntN(3) == 0 {
			return ""
		}
		return s
	}
	var cases []guardCase
	for range 120 {
		root := maybe("/nonexistent/" + path(false))
		common := []string{}
		for range rnd.IntN(4) {
			p := path(rnd.IntN(2) == 0)
			if root != "" && rnd.IntN(2) == 0 {
				p = root + "/" + path(false)
			}
			common = append(common, maybe(p))
		}
		tool := []string{"", "Bash", "Write"}[rnd.IntN(3)]
		cases = append(cases, guardCase{
			Root: root, Bin: maybe(path(rnd.IntN(2) == 0)), Home: maybe(path(false)), Common: common,
			Log: maybe(path(true)), State: maybe(path(true)), Tool: tool, Cwd: maybe(root + "/wip/handoff"),
			Subjects: randomSubjects(rnd, 25),
		})
	}
	// 写しの guard.json と同じ形では大きいので、組んだ式とルールは要約にし、当たったルールはそのまま見本にする。
	type seen struct {
		Built string     `json:"built"`
		Hits  [][]string `json:"hits"`
	}
	var all []seen
	for _, c := range cases {
		g := goGuard(c, c.Subjects)
		built, _ := json.Marshal([]any{g.Shell, g.Binary, g.BinaryCopy, g.Rules})
		all = append(all, seen{fmt.Sprintf("%x", sha256.Sum256(built))[:16], g.Hits})
	}
	golden.Check(t, "guard-random", all)
}

// TestGuardRulesWithFilesAgainstPython は実際のファイル（PATH の実体・ハードリンクでつながった下書き・リンクの居場所）で
// 確かめる。パスが機械で変わるので、当たったルールだけを Go の答えの見本（golden.Check）にする。
func TestGuardRulesWithFilesAgainstPython(t *testing.T) {
	if ospath.Windows {
		t.Skip("リンクを作れない機械がある")
	}
	dir := ospath.Realpath(t.TempDir())
	root := filepath.Join(dir, "ws")
	pathDir := filepath.Join(dir, "home", "u", ".local", "bin")
	for _, d := range []string{filepath.Join(root, ".claude"), filepath.Join(root, "wip", "handoff", ".claude"), pathDir, filepath.Join(dir, "bin")} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	settings := filepath.Join(root, ".claude", "settings.json")
	write(t, settings, "{}")
	linked := filepath.Join(root, "wip", "handoff", ".claude", "settings.json")
	if err := os.Link(settings, linked); err != nil {
		t.Skip(err)
	}
	plain := filepath.Join(root, "wip", "handoff", ".claude", "plain.json")
	write(t, plain, "{}")
	if err := os.Symlink(filepath.Join(root, ".claude"), filepath.Join(root, "wip", "handoff", "sym")); err != nil {
		t.Fatal(err)
	}
	exe := filepath.Join(pathDir, "ccnavi")
	write(t, exe, "#!/bin/sh\n")
	if err := os.Chmod(exe, 0o755); err != nil {
		t.Fatal(err)
	}
	shallow := filepath.Join(dir, "bin", "ccnavi")
	write(t, shallow, "#!/bin/sh\n")
	if err := os.Chmod(shallow, 0o755); err != nil {
		t.Fatal(err)
	}
	launcher := filepath.Join(root, ".ccnavi", "scripts", "ccnavi-launcher.sh")
	subjects := [][2]string{
		{"Write", linked}, {"Write", plain}, {"Write", filepath.Join(root, "wip", "handoff", "sym", "settings.json")},
		{"Write", filepath.Join(root, "wip", "handoff", ".claude", "new.json")}, {"Write", settings},
		{"Bash", "rm " + linked}, {"Bash", "rm " + plain}, {"Bash", "rm " + plain + "\x00rm " + settings},
		{"Bash", "rm " + plain + "\x00echo ok"}, {"Bash", "git rm -- " + plain}, {"Bash", "sh rm " + plain},
		{"Bash", "rm --pathspec-from-file=x " + plain}, {"Bash", "ln -s a b"}, {"Bash", "cp -s a b"},
		{"Bash", "cp x " + exe}, {"Bash", "cp x ./home/u/.local/bin/ccnavi"}, {"Bash", "cp x " + shallow}, {"Bash", "cp x ./bin/ccnavi"},
		{"Write", exe}, {"Write", shallow},
	}
	var cases []guardCase
	for _, pathEnv := range []string{pathDir, filepath.Join(dir, "bin")} {
		for _, cwd := range []string{"", filepath.Join(root, "wip", "handoff"), root} {
			cases = append(cases, guardCase{
				Root: root, Bin: launcher, Home: ".ccnavi", Cwd: cwd, Common: []string{filepath.Join(root, ".ccnavi", "config", "common", "rules.yml")},
				Subjects: subjects, Env: pathEnv,
			})
		}
	}
	// 式と置き場の答えはパスを含むので、どのルールに当たったかだけを見本にする。
	var hits [][][]string
	for _, c := range cases {
		t.Setenv("PATH", c.Env)
		hits = append(hits, goGuard(c, c.Subjects).Hits)
	}
	golden.Check(t, "guard-files", hits)
}

func write(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

type partsAnswer struct {
	Git       string   `json:"git"`
	Holder    []string `json:"holder"`
	Under     []string `json:"under"`
	Copy      []string `json:"copy"`
	Verbs     string   `json:"verbs"`
	NotAWord  string   `json:"notaword"`
	Path      string   `json:"path"`
	Limits    []int    `json:"limits"`
	Redirects []bool   `json:"redirects"`
}

func TestPublicPartsAgainstPython(t *testing.T) {
	holders := [][3]any{{`logs`, `decisions\.jsonl`, false}, {`\.`, `\.git`, true}, {`a[\\/]b`, `x|y`, false}}
	subjects := []string{">", strings.Repeat(">", 50), strings.Repeat(">", 51), ">" + strings.Repeat("a", RawLengthLimit),
		">" + strings.Repeat("a", RawLengthLimit-1), strings.Repeat("a", RawLengthLimit*2), ">" + strings.Repeat("日", RawLengthLimit)}
	want := golden.Load[partsAnswer](t, "parts")
	if GitDirShellRegex() != want.Git || WriteVerbs != want.Verbs || NotAWord != want.NotAWord || GitDirPathRegex != want.Path {
		t.Errorf("git の式か部品が違う\n got  %q\n want %q", GitDirShellRegex(), want.Git)
	}
	if want.Limits[0] != RedirectLimit || want.Limits[1] != RawLengthLimit {
		t.Errorf("上限が違う: %v", want.Limits)
	}
	for i, h := range holders {
		d, n := h[0].(string), h[1].(string)
		if got := HolderRegex(d, n, h[2].(bool)); got != want.Holder[i] {
			t.Errorf("HolderRegex(%q, %q)\n got  %q\n want %q", d, n, got, want.Holder[i])
		}
		if Under(d) != want.Under[i] || CopyDestinationRegex(CopyLastPlace(d), n) != want.Copy[i] {
			t.Errorf("Under・CopyDestinationRegex(%q) が違う", d)
		}
	}
	for i, s := range subjects {
		if TooManyRedirects(s) != want.Redirects[i] {
			t.Errorf("TooManyRedirects(#%d) = %v", i, !want.Redirects[i])
		}
	}
}
