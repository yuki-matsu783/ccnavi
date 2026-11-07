package learning

import (
	"math/big"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/records/wiki"
	"ccnavi/internal/tickets/model/place"
)

// 置き場（ツリーのルートからの相対。区切りは "/"）。
const LearningDir = place.Handoff + "/learnings"

// watched は親の finish が未コミットの変更を見る置き場。
var watched = []string{LearningDir, wiki.PatternsDir, wiki.InboxDir}

// 処理の行の種類。
const (
	processReflect = "反映"
	processSkip    = "対象外"
	processIssue   = "issue"
)

// signalError は `- 信号: 誤り`（読んだ手順や文書の誤り＝仕様バグの学び）。対象外にして消せない。
const signalError = "誤り"

const (
	mdExt     = ".md"
	gitkeep   = ".gitkeep"
	fence     = "---"
	headingMk = "## "
)

var regularModes = map[string]bool{"100644": true, "100755": true}

const (
	processPrefix = "- 処理:"
	learningsKey  = "learnings"
	noneCount     = "none"
)

// `\S` と `\d` は Python と同じく Unicode で読むので pyre。区切りには日本語入力で入りやすい全角空白（U+3000）も使える。
var (
	signalLine, signalErr   = pyre.Compile(`^- 信号:[ \t\u3000]*(?P<value>\S+)`, 0)
	issueRest, issueErr     = pyre.Compile(`^#?\d+[ \t\u3000]+\S`, 0)
	processForm, processErr = pyre.Compile(`^- 処理:[ \t\u3000]+(?P<kind>`+processReflect+`|`+processSkip+`|`+processIssue+
		`)(?:[ \t\u3000]+(?P<rest>.*\S))?[ \t\u3000]*$`, 0)
)

// section は気づき 1 件の節。
type section struct {
	title string
	// processLines は `- 処理:` で始まる行の原文。
	processLines []string
	signal       string
}

// process は (反映|対象外|issue, 後ろ)。行が 1 つで形が合うときだけ ok。
func (s *section) process() (kind, rest string, ok bool) {
	if len(s.processLines) != 1 || processErr != nil {
		return "", "", false
	}
	found, ok := processForm.Find(s.processLines[0])
	if !ok {
		return "", "", false
	}
	kind, _ = found.Named("kind")
	rest, _ = found.Named("rest")
	return kind, ospath.Strip(rest), true
}

// record は読み取れた気づきの記録。learnings が nil なら `learnings: none`。
type record struct {
	ident     string
	learnings *big.Int
	sections  []section
}

// parse は記録の文面を読む。読み取りの検査に落ちれば (nil, 理由)。
func parse(ident, text string) (*record, string) {
	text = strings.ReplaceAll(strings.TrimLeft(text, "\uFEFF"), "\r\n", "\n")
	lines := strings.Split(text, "\n")
	if ospath.Strip(lines[0]) != fence {
		return nil, "frontmatter が無い"
	}
	end := frontmatterEnd(lines)
	if end < 0 {
		return nil, "frontmatter が閉じていない"
	}
	loaded, err := yaml.Load(strings.Join(lines[1:end], "\n"))
	if err != nil {
		first := ""
		if parts := ospath.Splitlines(err.Error()); len(parts) > 0 {
			first = parts[0]
		}
		return nil, "frontmatter が YAML として読めない（" + first + "）"
	}
	front, ok := loaded.(*yaml.Map)
	if !ok {
		return nil, "frontmatter が YAML のマッピングでない"
	}
	if !front.Has(learningsKey) {
		return nil, "`learnings` が無い"
	}
	rec := &record{ident: ident}
	switch raw := front.Get(learningsKey); x := raw.(type) {
	case string:
		if x != noneCount {
			return nil, badCount(raw)
		}
	case int:
		if x < 0 {
			return nil, badCount(raw)
		}
		rec.learnings = big.NewInt(int64(x))
	case *big.Int:
		if x.Sign() < 0 {
			return nil, badCount(raw)
		}
		rec.learnings = x
	default:
		return nil, badCount(raw)
	}
	for _, line := range lines[end+1:] {
		n := len(rec.sections)
		switch {
		case strings.HasPrefix(line, headingMk):
			rec.sections = append(rec.sections, section{title: ospath.Strip(line[len(headingMk):])})
		case n > 0 && strings.HasPrefix(line, processPrefix):
			rec.sections[n-1].processLines = append(rec.sections[n-1].processLines, line)
		case n > 0 && signalErr == nil:
			if found, ok := signalLine.Find(line); ok {
				rec.sections[n-1].signal, _ = found.Named("value")
			}
		}
	}
	return rec, ""
}

func badCount(raw any) string {
	return "`learnings: " + pyfmt.Str(raw) + "` は `none` か 0 以上の整数でなければならない"
}

// frontmatterEnd は frontmatter の閉じ `---` の行の位置。frontmatter が無いか閉じなければ -1。
func frontmatterEnd(lines []string) int {
	if len(lines) == 0 || ospath.Strip(lines[0]) != fence {
		return -1
	}
	for i := 1; i < len(lines); i++ {
		if ospath.Strip(lines[i]) == fence {
			return i
		}
	}
	return -1
}

// consistencyProblem は整合の検査。落ちれば条件、通れば空。
func consistencyProblem(rec *record) string {
	n := len(rec.sections)
	if rec.learnings == nil && n > 0 {
		return "`learnings: none` なのに気づきの節が " + strconv.Itoa(n) + " 件ある"
	}
	if rec.learnings != nil && rec.learnings.Cmp(big.NewInt(int64(n))) != 0 {
		return "`learnings: " + rec.learnings.String() + "` と節の数（" + strconv.Itoa(n) + "）が合わない"
	}
	for i := range rec.sections {
		sec := &rec.sections[i]
		if len(sec.processLines) > 1 {
			return "「" + sec.title + "」に処理の行が " + strconv.Itoa(len(sec.processLines)) + " つある"
		}
		if _, _, ok := sec.process(); len(sec.processLines) > 0 && !ok {
			return "「" + sec.title + "」の処理の行の形が分からない" +
				"（`- 処理: 反映 <パターン名>`・`- 処理: 対象外 <理由>`・`- 処理: issue <番号> <理由>`）"
		}
	}
	return ""
}

// entry は HEAD のディレクトリ直下の 1 つ。
type entry struct{ name, mode string }

// listing は HEAD の rel 直下。読めなければ ok が偽で理由。無ければ空。
func listing(treeRoot, rel string) ([]entry, string, bool) {
	done := git.Run(treeRoot, []string{"ls-tree", "-z", "HEAD", rel + "/"}, git.Options{RawPaths: true})
	if !done.OK() {
		return nil, "HEAD の " + rel + "/ を読めない", false
	}
	var found []entry
	for _, line := range strings.Split(done.Out, "\x00") {
		if line == "" {
			continue
		}
		meta, path, _ := strings.Cut(line, "\t")
		parts := strings.FieldsFunc(meta, ospath.IsSpace)
		if len(parts) < 3 {
			continue
		}
		mode := "tree"
		if parts[1] == "blob" {
			mode = parts[0]
		}
		found = append(found, entry{path[strings.LastIndex(path, "/")+1:], mode})
	}
	return found, "", true
}

// headBlobText は HEAD の rel を UTF-8 で読む（改行は読み替えない）。読めなければ ok が偽。
func headBlobText(treeRoot, rel string) (string, bool) {
	data, ok := git.Blob(treeRoot, "HEAD", rel, git.Options{})
	if !ok || data == nil || !utf8.Valid(data) {
		return "", false
	}
	return string(data), true
}

// patternSources は HEAD のパターンの観測元。無いか壊れている（`--learnings` が数えない）なら ok が偽。
// ファイル名は大文字小文字を区別せずに探す（`Foo` と `foo.md` は同じパターン）。
func patternSources(treeRoot, name string) ([]string, bool) {
	if name == "" || strings.ContainsAny(name, `/\`) || strings.HasPrefix(name, ".") {
		return nil, false
	}
	names, _, _ := listing(treeRoot, wiki.PatternsDir)
	want := pathcase.Fold(name + mdExt)
	found := ""
	for _, e := range names {
		if pathcase.Fold(e.name) == want && regularModes[e.mode] {
			found = e.name
			break
		}
	}
	if found == "" {
		return nil, false
	}
	text, ok := headBlobText(treeRoot, wiki.PatternsDir+"/"+found)
	if !ok {
		return nil, false
	}
	// 畳んで ".md" になる字は M・m・D・d だけなので、元の綴りの末尾も 3 バイト。
	parsed, problem := wiki.ParsePattern(found[:len(found)-len(mdExt)], text)
	if problem != "" {
		return nil, false
	}
	return parsed.Sources, true
}

// Item は統合の子が処理すべきもの 1 件。Title は節の見出しで、受信箱のファイルでは空。
type Item struct {
	Path, Title, Why string
}

// Describe は一覧の 1 行の本文。
func (i Item) Describe() string {
	where := i.Path
	if i.Title != "" {
		where = i.Path + " の「" + i.Title + "」"
	}
	return where + "（" + i.Why + "）"
}

// Problem は統合では直せない記録 1 つと、その理由。
type Problem struct {
	Path, Why string
}

// Pending は Find の答え。Unreadable は読み取りの検査に、Inconsistent は整合の検査に落ちた記録。
type Pending struct {
	Items        []Item
	Unreadable   []Problem
	Inconsistent []Problem
}

// Broken は統合では直せない記録があるか。
func (p *Pending) Broken() bool { return len(p.Unreadable) > 0 || len(p.Inconsistent) > 0 }

// inboxFiles は HEAD の受信箱のファイル名。`.gitkeep` 以外は全部数える（`.md` 以外も、大文字小文字違いも）。
func inboxFiles(treeRoot string) ([]string, string) {
	names, why, ok := listing(treeRoot, wiki.InboxDir)
	if !ok {
		return nil, why
	}
	var out []string
	for _, e := range names {
		if e.name != gitkeep {
			out = append(out, e.name)
		}
	}
	sort.Strings(out)
	return out, ""
}

// Find は treeRoot の HEAD で、統合の子が処理すべきものを並べる。直せない記録は Items に入れず別に返す。
func Find(treeRoot string) *Pending {
	out := &Pending{}
	names, why, ok := listing(treeRoot, LearningDir)
	if !ok {
		out.Unreadable = append(out.Unreadable, Problem{LearningDir + "/", why})
	}
	sort.Slice(names, func(i, j int) bool {
		if names[i].name != names[j].name {
			return names[i].name < names[j].name
		}
		return names[i].mode < names[j].mode
	})
	for _, e := range names {
		if !strings.HasSuffix(e.name, mdExt) {
			continue
		}
		rel := LearningDir + "/" + e.name
		if !regularModes[e.mode] {
			out.Unreadable = append(out.Unreadable, Problem{rel, "通常のファイルでない（リンクかディレクトリ）"})
			continue
		}
		text, ok := headBlobText(treeRoot, rel)
		if !ok {
			out.Unreadable = append(out.Unreadable, Problem{rel, "読めない（UTF-8 でない、または HEAD に無い）"})
			continue
		}
		rec, why := parse(strings.TrimSuffix(e.name, mdExt), text)
		if rec == nil {
			out.Unreadable = append(out.Unreadable, Problem{rel, why})
			continue
		}
		if bad := consistencyProblem(rec); bad != "" {
			out.Inconsistent = append(out.Inconsistent, Problem{rel, bad})
			continue
		}
		for i := range rec.sections {
			if reason := sectionProblem(treeRoot, rec.ident, &rec.sections[i]); reason != "" {
				out.Items = append(out.Items, Item{rel, rec.sections[i].title, reason})
			}
		}
	}
	inbox, why := inboxFiles(treeRoot)
	if why != "" {
		out.Unreadable = append(out.Unreadable, Problem{wiki.InboxDir + "/", why})
	}
	for _, name := range inbox {
		out.Items = append(out.Items, Item{wiki.InboxDir + "/" + name, "", "受信箱のファイル（判定して消す）"})
	}
	return out
}

// sectionProblem は節が処理済みでない理由。処理済みなら空。
func sectionProblem(treeRoot, ident string, sec *section) string {
	kind, rest, ok := sec.process()
	if !ok {
		return "処理の行が無い"
	}
	switch kind {
	case processSkip:
		if strings.HasPrefix(sec.signal, signalError) {
			return "`信号: 誤り`（仕様バグの学び）は `処理: 対象外` で消せない" +
				"（issue にするなら `- 処理: issue <番号> <理由>`、パターンにするなら `反映`）"
		}
		if rest == "" {
			return "`処理: 対象外` の後ろに理由が無い"
		}
		return ""
	case processIssue:
		if issueErr == nil && issueRest.Match(rest) {
			return ""
		}
		return "`処理: issue` の後ろは `<番号> <理由>` の形で書く"
	}
	if rest == "" {
		return "`処理: 反映` の後ろにパターン名が無い"
	}
	sources, ok := patternSources(treeRoot, rest)
	if !ok {
		return "`処理: 反映 " + rest + "` が指すパターンが無い（" + wiki.PatternsDir + "/" + rest + ".md）"
	}
	named := false
	for _, s := range sources {
		if pathcase.Fold(s) == pathcase.Fold(ident) {
			named = true
		}
	}
	if !named {
		return "パターン " + rest + " の `sources` に観測元の ID（" + ident + "）が無い"
	}
	if len(sources) < wiki.MinSourcesToReflect {
		return "パターン " + rest + " の観測元が " + strconv.Itoa(len(sources)) + " 件で、反映には " +
			strconv.Itoa(wiki.MinSourcesToReflect) + " 件以上が要る" +
			"（1 件の気づきは `対象外 <理由>` か `issue <番号> <理由>` にする）"
	}
	return ""
}

// Uncommitted は記録・パターン・受信箱の置き場にある、未コミットか未追跡の変更のパス。
func Uncommitted(treeRoot string) []string {
	done := git.Status(treeRoot, git.Porcelain{NUL: true, Untracked: git.UntrackedAll, NoRenames: true, Paths: watched},
		git.Options{RawPaths: true})
	if !done.OK() {
		why := done.Stderr
		if why == "" && done.Failure != nil {
			why = done.Failure.Error()
		}
		return []string{"（git の状態を読めない: " + ospath.Strip(why) + "）"}
	}
	var out []string
	for _, e := range strings.Split(done.Out, "\x00") {
		if utf8.RuneCountInString(e) > 3 {
			out = append(out, e[3:])
		}
	}
	sort.Strings(out)
	return out
}
