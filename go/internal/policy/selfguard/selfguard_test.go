package selfguard

import (
	"bytes"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/texts"
)

const testRoot = "/repo"

func defaultCommon(root string) []string {
	return []string{root + "/.ccnavi/config/common/rules.yml", root + "/.ccnavi/config/common/phases.yml", root + "/.ccnavi/config/common/risks.yml"}
}

// shellHits は既定の設定で組んだシェルの 1 本が、読んだ命令に当たるか。
func shellHits(t *testing.T) func(string) bool {
	t.Helper()
	rs := &rules.RuleSet{Version: rules.Version}
	AddRules(rs, Options{Root: testRoot, ProjectHome: config.DefaultProjectHome, CommonFiles: defaultCommon(testRoot), Tool: "Bash"})
	i := slices.IndexFunc(rs.Deny, func(r *rules.Rule) bool { return r.ID == ShellRuleID })
	if i < 0 {
		t.Fatal("シェルの 1 本が無い")
	}
	rule := rs.Deny[i]
	return func(command string) bool { return rule.Matches("Bash", shell.Read(command).Text) }
}

// 止める形と通す形の代表（tests/config/test_config_union_holes.py の表から、cd・sh -c の層を要しないもの）。
func TestShellRuleTable(t *testing.T) {
	hits := shellHits(t)
	for _, c := range []struct {
		command string
		want    bool
	}{
		{"rm -rf .ccnavi", true},
		{"mv .ccnavi .ccnavi.bak", true},
		{"cp /tmp/x .claude", true},
		{"rm -r .claude/worktrees/w1", false},
		{"rm -r .ccnavixyz", false},
		{"mv .ccnavi-notes.md notes.md", false},
		{"cat .ccnavi/config/project/rules.yml", false},
		{"cp .ccnavi/config/common/rules.yml /tmp/x", false},
		{"cp /tmp/x .ccnavi/config/common/rules.yml", true},
		{"cp /tmp/x .ccnavi/config/common/rules.yml -S .bak", true},
		{"cp -Svt /tmp/x .ccnavi/config/common/rules.yml", true},
		{"cp -t .ccnavi/config/common /tmp/x", true},
		{"cp -t /tmp/out .ccnavi/config/common/rules.yml", false},
		{"cp .ccnavi/config/common/rules.yml /tmp/x 2>/dev/null", false},
		{"cp /tmp/x .CCNAVI/Common/RULES.yml", true},
		{`cp /tmp/x ".ccnavi/config/common/"rules.yml`, true},
		{"tee .ccnavi/config/common/rules.yml < /tmp/x", true},
		{"dd if=/tmp/x of=.ccnavi/config/common/rules.yml", true},
		{"sed -i s/deny/allow/ .ccnavi/config/common/rules.yml", true},
		{"sed -n 1,20p .ccnavi/config/common/rules.yml", false},
		{"sed -n s/feature-id/x/p .ccnavi/config/common/rules.yml", false},
		{"echo x > .ccnavi/config/common/rules.yml", true},
		{`echo "a > .ccnavi/x"`, false},
		{`echo x > "a  b/.ccnavi/x"`, true},
		{`echo x > "New folder (2)/.ccnavi/x"`, true},
		{"echo x >> logs/decisions.jsonl", true},
		{"rm logs/decisions.20260101.jsonl", true},
		{"rm logs/git-wrapper.log", false},
		{"cp /tmp/decisions.jsonl logs/", true},
		{"cp /tmp/notes.txt logs/", false},
		{"cp /tmp/decisions.jsonl.bak logs/", false},
		{"cp /tmp/* logs/", true},
		{"cp -rT /tmp/d logs", true},
		{"cp -r /tmp/.ccnavi .", true},
		{"cp -r /tmp/.ccnavi /repo/", true},
		{"cp -r /tmp/src .", false},
		{"rm /repo/wip/handoff/.ccnavi/x", false}, // 置き場の中だけを消す形は外す
		{"rm /repo/wip/handoff/.ccnavi/x /repo/.ccnavi/x", true},
		{"echo x > /repo/wip/handoff/.ccnavi/x", true}, // 書き込みは外さない
	} {
		if got := hits(c.command); got != c.want {
			t.Errorf("%q: got %v, want %v", c.command, got, c.want)
		}
	}
}

// 長い入力でも照合がすぐ返る（#241。割り方が 1 通りに決まる形と、最初の語での決め打ち）。
func TestShellRuleDoesNotBlowUp(t *testing.T) {
	regex, err := pyre.Compile(ShellRegex(testRoot, "", config.DefaultProjectHome, defaultCommon(testRoot)), pyre.IgnoreCase)
	if err != nil {
		t.Fatal(err)
	}
	var texts []string
	for _, tail := range []string{strings.Repeat(" -S", 60), strings.Repeat(" -S -", 60), strings.Repeat(" --suffix", 60), strings.Repeat(" -m x", 60)} {
		texts = append(texts, shell.Read("cp /tmp/x .ccnavi/config/common/rules.yml"+tail+" /tmp/y").Text)
	}
	for _, tail := range []string{strings.Repeat(" /tmp/decisions.jsonl", 300), strings.Repeat(" $x", 300), strings.Repeat(" -T", 300),
		strings.Repeat(" -t logs", 300), strings.Repeat(" -- /tmp/decisions.jsonl", 200), strings.Repeat(" -S -t", 300)} {
		for _, verb := range []string{"cp", "mv"} {
			texts = append(texts, shell.Read(verb+" /tmp/x"+tail+" /tmp/y").Text)
		}
	}
	texts = append(texts, "sed -i"+strings.Repeat(" -i", 4000)+" x")
	for _, text := range texts {
		start := time.Now()
		regex.Search(text)
		if took := time.Since(start); took > 2*time.Second {
			t.Errorf("%q…: %v", text[:20], took)
		}
	}
}

// sed の枝のヒットの仕方（最初の -i で決め打ちしても変わらない）。
func TestSedBranch(t *testing.T) {
	regex, err := pyre.Compile(WriteVerbs+`(\.ccnavi/x)`, 0)
	if err != nil {
		t.Fatal(err)
	}
	for subject, want := range map[string]bool{
		"sed -i s/a/b/ .ccnavi/x":        true,
		"sed -n -i s/a/b/ .ccnavi/x":     true,
		"sed -ni s/a/b/ .ccnavi/x":       true,
		"sed s/a/b/ -i .ccnavi/x":        true,
		"sed --i s/a/b/ .ccnavi/x":       true,
		"sed -i s/a/b/ other":            false,
		"sed -i.ccnavi/x f":              true,
		"sed s/a/b/ .ccnavi/x":           false,
		"sed -i s/a/b/\x00cat .ccnavi/x": false,
	} {
		if got := regex.Search(subject); got != want {
			t.Errorf("%q: got %v", subject, got)
		}
	}
}

// リダイレクトの適用し始めは `>` の並びの頭だけで、行き先の語は並びの文字で始まらない（手数の約束）。
func TestRedirectStarts(t *testing.T) {
	regex, err := pyre.Compile(redirect, 0)
	if err != nil {
		t.Fatal(err)
	}
	// 正規表現の match(text, i) と同じに、i の手前の 1 文字を読んでから始める（i > 0 では `^` が当たらない）。
	after, err := pyre.Compile(`(?s:.)(?:`+redirect+`)`, 0)
	if err != nil {
		t.Fatal(err)
	}
	text := []rune(shell.Read("echo x " + strings.Repeat(">", 50) + ` "a b"`).Text)
	var starts []int
	for i := range text {
		if (i == 0 && regex.Match(string(text))) || (i > 0 && after.Match(string(text[i-1:]))) {
			starts = append(starts, i)
		}
	}
	if want := slices.Index(text, '>') - 1; !slices.Equal(starts, []int{want}) {
		t.Errorf("適用し始め %v, want [%d]", starts, want)
	}
	whole, _ := pyre.Compile(redirect+`\Z`, 0)
	for _, s := range []string{" > >a", " > |a", " > &a"} {
		if whole.Match(s) {
			t.Errorf("%q に当たった", s)
		}
	}
}

func TestTooManyRedirects(t *testing.T) {
	for _, c := range []struct {
		subject string
		want    bool
	}{
		{strings.Repeat(">", RedirectLimit), false},
		{strings.Repeat(">", RedirectLimit+1), true},
		{">" + strings.Repeat("a", RawLengthLimit), true},
		{">" + strings.Repeat("a", RawLengthLimit-1), false},
		{strings.Repeat("a", RawLengthLimit*2), false},
		{">" + strings.Repeat("日", RawLengthLimit-1), false}, // 文字で数える
	} {
		if got := TooManyRedirects(c.subject); got != c.want {
			t.Errorf("len %d: got %v", len(c.subject), got)
		}
	}
}

// 浅い PATH の実体（/bin/ccnavi）は区切りで始まる形だけに当て、別のプロジェクトの ./bin/ccnavi は止めない。
// 浅い PATH の実体（`/tmp/x/ccnavi`）は区切りで始まる形だけに当てる。ルールの式は後読みを受けない。
// 組み立てに失敗して保護が黙って外れないことも確かめる。
func TestShallowPathExecutable(t *testing.T) {
	if ospath.Windows {
		t.Skip("PATH の実体の形が違う")
	}
	dir, err := os.MkdirTemp("/tmp", "sg")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	exe := filepath.Join(dir, "ccnavi")
	write(t, exe, "#!/bin/sh\n")
	if err := os.Chmod(exe, 0o755); err != nil {
		t.Fatal(err)
	}
	if len(pathParts(exe)) > 3 {
		t.Skipf("%s が浅くない", exe)
	}
	t.Setenv("PATH", dir)
	rs := &rules.RuleSet{Version: rules.Version}
	AddRules(rs, Options{Root: testRoot, Bin: "/ws/.ccnavi/scripts/ccnavi-launcher.sh", ProjectHome: config.DefaultProjectHome, CommonFiles: defaultCommon(testRoot)})
	byID := map[string]*rules.Rule{}
	for _, r := range rs.Deny {
		byID[r.ID] = r
	}
	if byID[ShellRuleID] == nil || byID[binaryRuleID] == nil {
		t.Fatalf("組み立てられなかったルールがある: %v", slices.Collect(maps.Keys(byID)))
	}
	if !byID[binaryRuleID].Matches(config.ToolWrite, exe) {
		t.Errorf("名指しのツールが %s に当たらない", exe)
	}
	rel := strings.TrimPrefix(exe, "/")
	for command, want := range map[string]bool{
		"cp x " + exe: true, "echo x > " + exe: true, "rm " + exe: true,
		"cp x ./" + rel: false, "cp -t /tmp/out " + exe: false, "cp " + exe + " /tmp/x": false, "cp x vendor/bin/ccnavi-old": false,
	} {
		if got := byID[ShellRuleID].Matches("Bash", shell.Read(command).Text); got != want {
			t.Errorf("%q: %v, want %v", command, got, want)
		}
	}
}

// シェルの 1 本は、Bash に当たるツール名（空は決めない呼び手）のときだけ組み、ほかの並びは変えない。
func TestShellRuleOnlyForBash(t *testing.T) {
	keys := func(tool string) []string {
		rs := &rules.RuleSet{Version: rules.Version}
		AddRules(rs, Options{Root: testRoot, ProjectHome: config.DefaultProjectHome, Tool: tool})
		var out []string
		for _, r := range rs.Deny {
			out = append(out, r.Key())
		}
		return out
	}
	everything := keys("")
	if !slices.Equal(keys("Bash"), everything) {
		t.Error("Bash で並びが変わった")
	}
	for _, tool := range []string{"Edit", "Write", "Read", "bash", "BASH", " Bash", "mcp__shell__Bash"} {
		got := keys(tool)
		if len(got) != len(everything)-1 || slices.ContainsFunc(got, func(k string) bool { return strings.Contains(k, ShellRuleID) }) {
			t.Errorf("%q: %v", tool, got)
		}
	}
}

// 組み立てられない保護は判定を止めずに外し、診断ログに id と件数だけを残す（式は書かない）。
func TestInsertDropsBrokenRule(t *testing.T) {
	root := t.TempDir()
	rs := &rules.RuleSet{Version: rules.Version}
	insert(rs, rules.Spec{ID: recordsRuleID, Match: namedMatch, Regex: "(secret-path", Text: texts.Spec("RULE_GUARD_RECORDS", texts.SpecOptions{})}, root)
	if len(rs.Deny) != 0 {
		t.Fatal("組み立てられない保護が残った")
	}
	log, err := os.ReadFile(filepath.Join(root, "logs", "diag", diagSource+".log"))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(log, []byte(recordsRuleID)) || bytes.Contains(log, []byte("secret-path")) {
		t.Errorf("診断ログ: %s", log)
	}
	insert(rs, rules.Spec{ID: recordsRuleID, Match: namedMatch, Regex: "x$", Text: texts.Spec("RULE_GUARD_RECORDS", texts.SpecOptions{})}, root)
	insert(rs, rules.Spec{ID: binaryRuleID, Match: namedMatch, Regex: "y$", Text: texts.Spec("RULE_GUARD_BINARY", texts.SpecOptions{})}, root)
	if len(rs.Deny) != 2 || rs.Deny[0].ID != binaryRuleID {
		t.Errorf("先頭に挿していない: %v", rs.Deny)
	}
}

// 組み込みのルールが持つ文の識別子・値・場合が、文面ファイルと合っている（tests/core/test_texts_catalog.py の
// BuiltinRuleTextsTest の自己防衛のぶん）。
func TestBuiltinRuleTexts(t *testing.T) {
	repo := repoRoot(t)
	catalog := texts.Load(repo)
	seen := map[string]bool{}
	for _, root := range []string{"", "with-root"} {
		dir := t.TempDir()
		copyMessages(t, dir)
		if root != "" {
			root = dir
		}
		rs := &rules.RuleSet{Version: rules.Version}
		AddRules(rs, Options{Bin: filepath.Join(dir, "bin", "ccnavi"), ProjectHome: config.DefaultProjectHome, Root: root,
			CommonFiles: defaultCommon(dir), Log: filepath.Join(dir, "logs", "decisions.jsonl"), State: filepath.Join(dir, "logs", "state")})
		AddNamedRules(rs, config.DefaultProjectHome, root)
		for _, r := range rs.Deny {
			if r.Text.ID == "" || r.Message != "" {
				t.Errorf("%s: 文の識別子が無いか、文を直接持っている", r.ID)
				continue
			}
			entry := catalog.Entries[r.Text.ID]
			if entry == nil {
				t.Errorf("%s: %s が無い", r.ID, r.Text.ID)
				continue
			}
			var values []string
			for k := range r.Text.Values {
				values = append(values, k)
			}
			if !sameSet(values, entry.Values) {
				t.Errorf("%s: 値 %v, 文面ファイル %v", r.ID, values, entry.Values)
			}
			for name, c := range r.Text.Variants {
				if !slices.Contains(entry.Cases(name), c) {
					t.Errorf("%s: 欄 %s に場合 %s が無い", r.ID, name, c)
				}
			}
			// 文は判定のときの root から引くので、文面ファイルを写した dir を root にして引く。
			said := texts.Say(dir, r.Text.ID, r.Text.Values, r.Text.Variants)
			if strings.Contains(said, "引けませんでした") || strings.Contains(said, "{") || len(texts.LinesOf(said)) > 1 {
				t.Errorf("%s: %q", r.ID, said)
			}
			seen[r.Text.ID] = true
		}
	}
	if len(seen) < 11 {
		t.Errorf("自己防衛の文が %d 件しか無い: %v", len(seen), seen)
	}
}

func sameSet(a, b []string) bool {
	a, b = slices.Clone(a), slices.Clone(b)
	slices.Sort(a)
	slices.Sort(b)
	return slices.Equal(a, b)
}

func TestResolve(t *testing.T) {
	for _, c := range []struct {
		flag, declared string
		allowed        []config.Mode
		want           config.Mode
		warned         bool
	}{
		{"", "", choices, config.Enable, false},
		{"", " DRY-RUN ", choices, config.DryRun, false},
		{"disable", "enable", choices, config.Disable, false},
		{"", "dry-run", gateChoices, config.Enable, true},
		{"", "off", choices, config.Enable, true},
	} {
		var stderr bytes.Buffer
		got := Resolve(&stderr, c.flag, c.declared, "X", c.allowed)
		if got != c.want || (stderr.Len() > 0) != c.warned {
			t.Errorf("%q %q: %s %q", c.flag, c.declared, got, stderr.String())
		}
	}
	var stderr bytes.Buffer
	Resolve(&stderr, "", "dry-run", "CCNAVI_X", gateChoices)
	if want := "ccnavi: CCNAVI_X='dry-run' は設定ではありません。enable を使います。有効な値は enable, disable です\n"; stderr.String() != want {
		t.Errorf("%q", stderr.String())
	}
}

func TestLayerKeyAndCopyKey(t *testing.T) {
	for _, c := range []struct {
		origin config.Origin
		layer  string
		kind   config.LayerKind
		want   string
	}{
		{config.OriginCommon, config.LayerCommon, config.KindPhases, "phases"},
		{config.OriginSelf, config.LayerSelf, config.KindRules, "rules:self"},
		{config.OriginProject, "lib", config.KindRisk, "risk:lib"},
		// 予約名のプロジェクトは置き場を付ける（共通レイヤーと自身のレイヤーの key とぶつけない）。
		{config.OriginProject, "common", config.KindRules, "rules:projects/common"},
		{config.OriginProject, "Self", config.KindRules, "rules:projects/Self"},
		{config.OriginMirror, "lib", config.KindRules, "rules:mirror/lib"},
	} {
		if got := layerKey(c.origin, c.layer, c.kind); got != c.want {
			t.Errorf("%s %s: %s", c.origin, c.layer, got)
		}
	}
	// `a/b` と `a_b` は置き換えると同じ綴りになるが、digest で分かれる。
	if a, b := copyKey("rules", "a/b"), copyKey("rules", "a_b"); a == b || !strings.HasPrefix(a, "rules.a_b-") {
		t.Errorf("%s %s", a, b)
	}
}

// 掃除は古いセッションの置き場と、どこからも参照されない古い実体を消し、自分の置き場と使われている実体は残す。
func TestSweep(t *testing.T) {
	state := t.TempDir()
	old := time.Now().Add(-10 * 24 * time.Hour)
	put := func(rel, text string, stamp time.Time) {
		path := filepath.Join(state, backupDir, filepath.FromSlash(rel))
		write(t, path, text)
		os.Chtimes(path, stamp, stamp)
		os.Chtimes(filepath.Dir(path), stamp, stamp)
	}
	put("gone/settings", "x", old)
	put("mine/settings", "x", old)
	put("live/bin.ref", "kept 1 1\n", time.Now())
	put("store/kept", "x", old)
	put("store/orphan", "x", old)
	put("store/fresh", "x", time.Now())
	sweep(state, "mine")
	for rel, want := range map[string]bool{
		"gone": false, "mine/settings": true, "live/bin.ref": true, "store/kept": true, "store/orphan": false, "store/fresh": true,
	} {
		if got := fileExists(filepath.Join(state, backupDir, filepath.FromSlash(rel))); got != want {
			t.Errorf("%s: 残った=%v", rel, got)
		}
	}
}

// 下書きの置き場の中のつながっていないコピーは名指しのツールで書け、ハードリンク・リンクを通るもの・`..` は書けない。
func TestHandoffExempt(t *testing.T) {
	root := ospath.Realpath(t.TempDir())
	settings := filepath.Join(root, ".claude", "settings.json")
	write(t, settings, "{}")
	place := filepath.Join(root, "wip", "handoff")
	plain := filepath.Join(place, ".claude", "plain.json")
	write(t, plain, "{}")
	linked := filepath.Join(place, ".claude", "settings.json")
	hard := os.Link(settings, linked) == nil
	exempt := handoffWriteExempt(root)
	for path, want := range map[string]string{
		plain: "",
		filepath.Join(place, ".claude", "new.json"):                             "",
		filepath.Join(root, "projects", "lib", "wip", "handoff", "x"):           "",
		filepath.Join(root, ".claude", "worktrees", "w", "wip", "handoff", "x"): "",
		filepath.Join(root, "docs", "wip", "handoff", "x"):                      filepath.Join(root, "docs", "wip", "handoff", "x"),
		place + "/../.claude/settings.json":                                     place + "/../.claude/settings.json",
		settings:                                                                settings,
	} {
		if got := exempt(path); got != want {
			t.Errorf("%s: %q", path, got)
		}
	}
	if hard && exempt(linked) != linked {
		t.Error("ハードリンクのコピーを外した")
	}
	if handoffWriteExempt("") != nil {
		t.Error("root が空でも外す関数を返した")
	}
	if !HandoffDeleteWord(root, "", plain) || HandoffDeleteWord(root, "", settings) || HandoffDeleteWord(root, "", "$x") {
		t.Error("削除の語の読みが違う")
	}
	if !HandoffDeleteWord(root, place, ".claude/plain.json") || HandoffDeleteWord(root, "", ".claude/plain.json") {
		t.Error("相対の語の読みが違う")
	}
	link := handoffLinkCommand(root, "")
	for command, want := range map[string]bool{
		"ln -s " + settings + " wip/handoff/x": true, "cp -l a wip/handoff/b": true, "cp --li a HANDOFF/b": true,
		"sudo ln a handoff": true, "ln -s a b": false, "cp a wip/handoff/b": false, "echo handoff": false,
	} {
		if got := link(command) != ""; got != want {
			t.Errorf("%q: %v", command, got)
		}
	}
	if handoffLinkCommand(root, place)("ln -s a b") == "" {
		t.Error("置き場の中で作るリンクを止めない")
	}
}

// 報告は状況ごとに注意を足す（ワークツリー側・共通レイヤーのルールファイル・取り直し・ユーザへの依頼）。
func TestReport(t *testing.T) {
	root := t.TempDir()
	copyMessages(t, root)
	if Report(root, nil) != "" {
		t.Error("結果が無いのに文がある")
	}
	rebased := Report(root, []Outcome{{Target{Key: "bin", Label: "bin/ccnavi", heavy: true}, actionRebased, "d"}})
	common := Report(root, []Outcome{{Target{Key: commonRulesKey, Label: "rules.yml"}, actionRestored, "d"}})
	copied := Report(root, []Outcome{{Target{Key: "rules.w-1", Label: "w/rules.yml", copy: true}, actionRestored, "d"}})
	for _, c := range []struct {
		got  string
		id   string
		want bool
	}{
		{rebased, "NOTICE_SELFGUARD__REBASED", true}, {rebased, "NOTICE_SELFGUARD__ASK_USER", false},
		{common, "NOTICE_SELFGUARD__COMMON_RULES", true}, {common, "NOTICE_SELFGUARD__ASK_USER", true},
		{copied, "NOTICE_SELFGUARD__COPY", true}, {copied, "NOTICE_SELFGUARD__COMMON_RULES", false},
	} {
		line := texts.Say(root, c.id, texts.Values{"draft": commonRulesDraft}, nil)
		if strings.Contains(c.got, line) != c.want {
			t.Errorf("%s: %v\n%s", c.id, !c.want, c.got)
		}
	}
	if !strings.Contains(common, "  rules.yml: restored — d") {
		t.Error(common)
	}
}
