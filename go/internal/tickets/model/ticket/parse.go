package ticket

import (
	"errors"
	"fmt"
	"io/fs"
	"math/big"
	"regexp"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/model/ids"
)

// fileRule は frontmatter の形の苦情の出どころ（識別子がまだ読めていない）。
const fileRule = "(file)"

// forbidden は範囲のパスに書けない表記。`..` は範囲の外へ出る、`~` はプロジェクトの外、`$` は展開されるまで
// 行き先が決まらない。
var forbidden = []string{"..", "~", "$"}

// issueRef は別のリポジトリの課題の表記（`owner/repo#12`。GitLab の入れ子のグループも）。数字は Python の \d。
var issueRef = regexp.MustCompile(`\A([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)#(\p{Nd}+)\z`)

// GlobSpaceReason は範囲の glob に空白がある理由の文。提案・phases.yml の scope・ticket new --scope が同じ文を使う。
const GlobSpaceReason = "空白がある。glob は 1 項目に 1 パターンで、空白は区切りにならない。" +
	"空白で区切って複数のパスを書いたなら、承認しても何も一致せず編集が全部止まる。" +
	"パターンごとに項目を分けて書く。空白を含むファイル名は `?` で置き換える"

// GlobHasSpace は範囲の glob に空白（Python の \s）があるか。空白区切りで複数のパスを 1 項目に書く誤りを断つ。
func GlobHasSpace(glob string) bool { return strings.ContainsFunc(glob, ospath.IsSpace) }

// RegexOverflowDetail は子の範囲の regex を名指しする文。親の検査と定義の検査が同じ文を使い、承認の画面で
// 2 度並べずにまとめられるようにする。
func RegexOverflowDetail(regex string) string {
	return "子の範囲に regex `" + regex + "` は書けない。" +
		"親と定義の上限に収まるかは、判定のときに照合して確かめる"
}

func problem(sev rules.Severity, name, detail string) rules.Problem {
	return rules.Problem{Severity: sev, Rule: name, Detail: detail}
}

// text は YAML から来た値を文字列にする（数字だけの識別子は整数で返ってくる）。null・マップ・列・真偽は空。
func text(v any) string {
	switch v.(type) {
	case nil, *yaml.Map, []any, bool:
		return ""
	}
	return pyfmt.Str(v)
}

// Load はチケットを読んで組み立てる。無いことは不備ではない。fsio を通すので、承認の plan の中では同じ承認で
// 動かしたチケットを動かした後の状態で読む。
func Load(path string) (*Ticket, []rules.Problem) {
	source, body, err := fsio.LoadSource(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, []rules.Problem{problem(rules.Error, path, "チケットを読めない ("+fsio.ErrorText(err)+")")}
	}
	t, problems := Parse(body)
	if t != nil {
		t.Path = ospath.Realpath(path)
		t.Source = source
	}
	return t, problems
}

// Parse は読み込み済みの文面からチケットを組み立てる。欄の読み手のどれかが「これ以上読めない」と言えば止め、
// それまでの苦情を欄の順で全部返す。
func Parse(source string) (*Ticket, []rules.Problem) {
	front, body, problems := frontmatter(source)
	if front == nil {
		return nil, problems
	}
	t := &Ticket{
		ID:        ospath.Strip(text(front.Get("ticket"))),
		Parent:    ospath.Strip(text(front.Get("parent"))),
		Title:     text(front.Get("title")),
		Rationale: text(front.Get("rationale")),
		Raw:       front,
		Body:      body,
	}
	if t.ID == "" {
		return nil, append(problems, problem(rules.Error, "ticket", "チケット識別子がありません。"))
	}
	for _, read := range []func(*Ticket, *yaml.Map, *[]rules.Problem) bool{
		readIdentity, readRelations, readReview, readScope,
	} {
		if read(t, front, &problems) {
			return nil, problems
		}
	}
	// 印の値の正しさはここで見ない（落とすと承認済みチケットが索引に入らない）。MaintenanceProblem が言う。
	if v, ok := front.Lookup(MaintenanceKey); ok {
		if v == nil {
			v = ""
		}
		t.maintenance, t.HasMaintenance = v, true
	}
	return t, problems
}

// readIdentity は版と識別子と親子の形を読む。読めなければ真。
func readIdentity(t *Ticket, front *yaml.Map, problems *[]rules.Problem) bool {
	name := t.ID
	fail := func(detail string) bool {
		*problems = append(*problems, problem(rules.Error, name, detail))
		return true
	}
	if version := front.Get("version"); !pyfmt.EqualsInt(version, Version) {
		return fail(fmt.Sprintf("チケット書式の版 %s は扱えない（このビルドが読むのは %d）。"+
			"タイプは rules.yml と同じ deny / ask / allow で、`target_directories` は読まない", pyfmt.Repr(version), Version))
	}
	if p := ids.IDProblem(name); p != "" {
		return fail(p)
	}
	if t.Parent == "" {
		for _, key := range []string{"phase", "predecessors"} {
			if front.Has(key) {
				*problems = append(*problems, problem(rules.Warn, name, "`"+key+"` は子だけの欄。親では読まない"))
			}
		}
		return false
	}
	example := ids.ChildID(t.Parent, 5, 1)
	child, ok := ids.ParseChild(name)
	if !ok {
		// 親のつもりで `parent:` を書いた形（`parent:` が自分の識別子のときが典型）。
		lead := ""
		if t.Parent == name {
			lead = "`parent: " + t.Parent + "` が自分の識別子と同じ。"
		}
		return fail(lead + "子の識別子は `" + t.Parent + "-<2 桁のフェーズ番号>-<2 桁の連番>`" +
			" の形で書く（フェーズ 5 の 1 枚目なら `" + example + "`）。" +
			"親の提案を書いているなら、`parent:` と `phase:` の行を消す" +
			"（この 2 つは子だけの欄）。雛形は " +
			"`" + config.Launcher + " ticket new <識別子> --title <タイトル> --plan <型,…>" +
			" --issue <値> --mr <番号>` が作る")
	}
	if child.Parent != t.Parent {
		return fail("子の識別子は `" + t.Parent + "-<2 桁のフェーズ番号>-<2 桁の連番>` の形で書く" +
			"（フェーズ 5 の 1 枚目なら `" + example + "`）。" +
			"親の識別子が名前空間で、フェーズ番号は `phase` と同じ値")
	}
	tooLarge := func(written string) bool {
		return fail(fmt.Sprintf("`phase: %s` は子の識別子に指定できません。識別子のフェーズ番号は"+
			" 2 桁（0〜%d）なので、計画を %d フェーズまでに分ける", written, ids.MaxChildNumber, ids.MaxChildNumber))
	}
	var phase int
	switch v := front.Get("phase").(type) {
	case int:
		if v < 0 {
			return fail("子には `phase`（0 以上の整数）が必要です")
		}
		phase = v
	case *big.Int:
		if v.Sign() < 0 {
			return fail("子には `phase`（0 以上の整数）が必要です")
		}
		return tooLarge(v.String())
	default:
		return fail("子には `phase`（0 以上の整数）が必要です")
	}
	if phase > ids.MaxChildNumber {
		return tooLarge(fmt.Sprint(phase))
	}
	if child.Phase != phase {
		return fail(fmt.Sprintf("子の識別子のフェーズ番号（%02d）が `phase: %d` と"+
			"食い違う。識別子は `%s-%02d-<2 桁の連番>` の形で書く", child.Phase, phase, t.Parent, phase))
	}
	t.Phase = phase
	return false
}

// readRelations はプロジェクト・先行・計画・フェーズ定義のコピー・課題・MR を読む。読めなければ真。
func readRelations(t *Ticket, front *yaml.Map, problems *[]rules.Problem) bool {
	name := t.ID
	add := func(sev rules.Severity, detail string) { *problems = append(*problems, problem(sev, name, detail)) }
	// frontmatter の `project:` は照合用の宣言。本当のプロジェクトは置いた場所で、Scan が上書きする。
	t.DeclaredProject = ospath.Strip(text(front.Get("project")))
	t.Project = t.DeclaredProject

	switch preds := front.Get("predecessors").(type) {
	case nil:
	case []any:
		for _, p := range preds {
			if s := ospath.Strip(text(p)); s != "" {
				t.Predecessors = append(t.Predecessors, s)
			}
		}
	default:
		add(rules.Error, "`predecessors` はリストで書く")
		return true
	}

	for _, key := range []string{"plan", "feedback"} {
		raw := front.Get(key)
		if raw == nil {
			continue
		}
		if t.IsChild() {
			add(rules.Warn, "`"+key+"` は親だけの欄。子では読まない")
			continue
		}
		// フィードバック計画の番号は全体計画の続き。
		start := 1
		if key == "feedback" {
			start = len(t.Plan) + 1
		}
		items, ok := readPlan(name, key, raw, start, problems)
		if !ok {
			return true
		}
		if key == "plan" {
			t.Plan = items
		} else {
			t.Feedback, t.FeedbackPlanned = items, true
		}
	}
	// 形の誤りも子に書いた phases: も、ここでは落とさず判定の blocked の理由にする（tickets/config）。
	t.PhasesRaw = front.Get(PhasesKey)

	if raw := front.Get("issue"); raw != nil {
		repo, number := issueOf(raw)
		if number == 0 {
			add(rules.Error, "`issue` は課題の番号（正の整数）で書く。別のリポジトリの課題は `owner/repo#12`")
			return true
		}
		if t.IsChild() {
			add(rules.Warn, "`issue` は親だけの欄。子では読まない")
		} else {
			t.Issue, t.IssueRepo = number, repo
		}
	}

	if raw := front.Get("mr"); raw != nil && raw != "" {
		number := mrNumber(raw)
		if number == 0 {
			add(rules.Error, "`mr` はマージリクエストの番号（正の整数）で書く。`!12`・`#12` も可。"+
				"同じリポジトリのものだけ（`owner/repo` 形式は書けない）")
			return true
		}
		if t.IsChild() {
			add(rules.Warn, "`mr` は親だけの欄。子では読まない")
		} else {
			t.MR = number
		}
	}

	if front.Has(ids.BranchKey) {
		add(rules.Error, "`branch` は指定できない欄。親のブランチ名は親のワークツリーの HEAD から読み、"+
			"親の識別子はその `/` を `-` に置き換えたもの。欄を消す")
		return true
	}
	return false
}

// readPlan は計画のリストを読む。定義が在るかを確かめるのは承認の側。start はこの計画の最初の番号。
func readPlan(name, key string, raw any, start int, problems *[]rules.Problem) ([]PlanItem, bool) {
	fail := func(detail string) ([]PlanItem, bool) {
		*problems = append(*problems, problem(rules.Error, name, detail))
		return nil, false
	}
	list, ok := raw.([]any)
	if !ok {
		return fail("`" + key + "` はリストで書く")
	}
	items := []PlanItem{}
	for i, entry := range list {
		where := fmt.Sprintf("%s[%d]", key, i)
		if s, ok := entry.(string); ok && ospath.Strip(s) != "" {
			items = append(items, PlanItem{Type: ospath.Strip(s)})
			continue
		}
		m, ok := entry.(*yaml.Map)
		if !ok {
			return fail(where + " は定義の名前か {type, review, deferred, after}")
		}
		kind := ospath.Strip(text(m.Get("type")))
		if kind == "" {
			return fail(where + " に `type` がありません。")
		}
		review, deferred, why := StepFlags(m, where)
		if why != "" {
			return fail(why)
		}
		item := PlanItem{Type: kind, Review: review, Deferred: deferred}
		readAfter(&item, m.Get("after"), start, start+i)
		items = append(items, item)
	}
	return items, true
}

// StepFlags は計画のステップの review と deferred を読む（review・deferred・誤りの文）。review は定義の review より
// 優先し、deferred: true は次にレビューがあるフェーズと一緒に見る。2 つは同時に書けない。ステップの形を読む入口は
// ここだけを通る。
func StepFlags(entry *yaml.Map, where string) (Need, bool, string) {
	review := Unsaid
	if v, ok := entry.Lookup("review"); ok && v != nil {
		b, isBool := v.(bool)
		if !isBool {
			return Unsaid, false, where + " の `review` は true か false"
		}
		review = NeedOf(b)
	}
	deferred := false
	if v, ok := entry.Lookup("deferred"); ok && v != nil {
		if v != true {
			return Unsaid, false, where + " の `deferred` は true だけ書ける"
		}
		deferred = true
	}
	if review.Said() && deferred {
		return Unsaid, false, where + " に `review` と `deferred: true` は同時に書けない"
	}
	return review, deferred, ""
}

// readAfter はステップの after を読む。正しい番号は、整数（真偽は数えない）で、その計画の範囲の中（first 以上）で、
// 自分（number）より小さいもの。どんな値でも止めない。
func readAfter(item *PlanItem, raw any, first, number int) {
	if raw == nil {
		return
	}
	list, ok := raw.([]any)
	if !ok {
		item.AfterErrors = append(item.AfterErrors, AfterError{raw, "番号のリストで書く"})
		return
	}
	tooSmall := "1 以上の番号で書く"
	if first > 1 {
		tooSmall = "フィードバック計画のステップはフィードバック計画の番号だけを指せる"
	}
	for _, value := range list {
		var n int
		switch v := value.(type) {
		case int:
			n = v
		case *big.Int:
			// int に収まらない整数は、大きければ自分より大きく、負なら範囲の外。
			if v.Sign() > 0 {
				item.AfterErrors = append(item.AfterErrors, AfterError{value, fmt.Sprintf("自分（%d）より小さい番号だけを指せる", number)})
			} else {
				item.AfterErrors = append(item.AfterErrors, AfterError{value, tooSmall})
			}
			continue
		default:
			item.AfterErrors = append(item.AfterErrors, AfterError{value, "番号（整数）で書く"})
			continue
		}
		switch {
		case n >= number:
			item.AfterErrors = append(item.AfterErrors, AfterError{value, fmt.Sprintf("自分（%d）より小さい番号だけを指せる", number)})
		case n < first:
			item.AfterErrors = append(item.AfterErrors, AfterError{value, tooSmall})
		case slices.Contains(item.After, n):
			if !slices.Contains(item.AfterRepeated, n) {
				item.AfterRepeated = append(item.AfterRepeated, n)
			}
		default:
			item.After = append(item.After, n)
		}
	}
	slices.Sort(item.After)
}

// issueOf は課題の表記を読む（別のリポジトリの `owner/repo` か空、番号か 0）。
func issueOf(raw any) (string, int) {
	if s, ok := raw.(string); ok {
		if m := issueRef.FindStringSubmatch(ospath.Strip(s)); m != nil {
			repo := m[1]
			for _, part := range strings.Split(repo, "/") {
				if part == "." || part == ".." {
					return "", 0
				}
			}
			n, err := pyfmt.ParseInt(m[2])
			if err != nil || n <= 0 {
				return repo, 0
			}
			return repo, n
		}
	}
	return "", issueNumber(raw)
}

// IssueText は `issue:` に書く値（`12` か `owner/repo#12`）。読めなければ空。ticket new が使う。
func IssueText(raw any) string {
	repo, number := issueOf(raw)
	switch {
	case number == 0:
		return ""
	case repo != "":
		return fmt.Sprintf("%s#%d", repo, number)
	}
	return fmt.Sprint(number)
}

// issueNumber は課題の番号。`12` でも `"#12"` でも読む。読めなければ 0。
func issueNumber(raw any) int {
	switch v := raw.(type) {
	case bool:
		return 0
	case int:
		return max(v, 0)
	}
	s := ospath.Strip(strings.TrimLeft(ospath.Strip(pyfmt.Str(raw)), "#"))
	if !pyfmt.IsDigit(s) {
		return 0
	}
	n, err := pyfmt.ParseInt(s)
	if err != nil || n <= 0 {
		return 0
	}
	return n
}

// mrNumber は MR の番号。`12`・`"!12"`・`"#12"` を読む。読めなければ 0。
func mrNumber(raw any) int {
	if s, ok := raw.(string); ok {
		raw = ospath.Strip(strings.TrimLeft(ospath.Strip(s), "!#"))
	}
	return issueNumber(raw)
}

// readReview は人間レビューの要否と理由を読む。読めなければ真。
func readReview(t *Ticket, front *yaml.Map, problems *[]rules.Problem) bool {
	name := t.ID
	switch review := front.Get("human_review").(type) {
	case *yaml.Map:
		required := any(true)
		if v, ok := review.Lookup("required"); ok {
			required = v
		}
		b, ok := required.(bool)
		if !ok {
			*problems = append(*problems, problem(rules.Error, name, "`human_review.required` は true か false"))
			return true
		}
		t.ReviewRequired = NeedOf(b)
		t.ReviewReason = text(review.Get("reason"))
	case bool:
		t.ReviewRequired = NeedOf(review)
	case nil:
	default:
		*problems = append(*problems, problem(rules.Error, name, "`human_review` は {required:, reason:} で書く"))
		return true
	}
	if t.ReviewRequired == Unneeded && t.ReviewReason == "" {
		*problems = append(*problems, problem(rules.Warn, name, "人間レビューを省くなら `human_review.reason` に理由を書いてください"))
	}
	return false
}

// readScope は判定に使われない欄への注意、スクリプトの欄、範囲の項を読む。範囲が成り立たなければ真。
func readScope(t *Ticket, front *yaml.Map, problems *[]rules.Problem) bool {
	name := t.ID
	for _, key := range []string{"tools", "deny_commands", "target_directories"} {
		if front.Has(key) {
			*problems = append(*problems, problem(rules.Warn, name, "`"+key+"` はこの版の判定が使わない。書いても反映されない。"+
				"範囲は deny / ask / allow のタイプに `match: Write|Edit` で書く"))
		}
	}
	for _, key := range scriptFields {
		*t.scriptField(key) = ospath.Strip(text(front.Get(key)))
	}
	entries, found := readEntries(front, name)
	*problems = append(*problems, found...)
	t.Entries = entries
	if !slices.ContainsFunc(entries, func(e Entry) bool { return e.Decision == rules.Allow || e.Decision == rules.Ask }) {
		*problems = append(*problems, problem(rules.Error, name, "allow にも ask にも、有効な範囲の項が 1 件もありません。"+
			"このチケットは承認しても何も書けない"))
		return true
	}
	if len(entries) > MaxScopeEntries {
		*problems = append(*problems, problem(rules.Error, name, fmt.Sprintf("範囲が %d 件ある（上限 %d 件）。"+
			"作業を分けるか、範囲をまとめてください", len(entries), MaxScopeEntries)))
		return true
	}
	return false
}

// readEntries は frontmatter のタイプの欄の項目を、判定に使える Entry の列にする。読むのは書き込みのツールを
// 含む項目だけ。glob は大文字小文字を区別せずに組む（`src/Components/*` と `src/components/*` は同じ範囲。
// 区別が要る regex は `(?-i:...)` で囲む）。
func readEntries(front *yaml.Map, name string) ([]Entry, []rules.Problem) {
	var problems []rules.Problem
	var entries []Entry
	bad := func(detail string) { problems = append(problems, problem(rules.Error, name, detail)) }
	for _, section := range rules.Sections() {
		raw := front.Get(string(section))
		if raw == nil {
			continue
		}
		list, ok := raw.([]any)
		if !ok {
			bad("`" + string(section) + "` がリストではない")
			continue
		}
		for i, item := range list {
			where := fmt.Sprintf("%s[%d]", section, i)
			m, ok := item.(*yaml.Map)
			if !ok {
				bad(where + " がマッピングではない")
				continue
			}
			match := text(m.Get("match"))
			writes := false
			for _, w := range strings.Split(match, "|") {
				if slices.Contains(config.WriteTools(), ospath.Strip(w)) {
					writes = true
				}
			}
			if !writes {
				problems = append(problems, problem(rules.Warn, name, where+" の match `"+match+"` は書き込みのツールを含まない。"+
					"この版の判定はパスの範囲しか見ないので反映されない"))
				continue
			}
			glob := ospath.Strip(text(m.Get("glob")))
			regex := ospath.Strip(text(m.Get("regex")))
			written := glob
			if written == "" {
				written = regex
			}
			switch {
			case glob == "" && regex == "":
				bad(where + " に glob も regex もありません。")
				continue
			case glob != "" && regex != "":
				bad(where + " に glob と regex の両方がある")
				continue
			}
			if label := forbiddenIn(written); label != "" {
				bad(where + " の範囲に" + label + "は書けない")
				continue
			}
			if glob != "" && GlobHasSpace(glob) {
				bad(where + " の glob `" + glob + "` に" + GlobSpaceReason)
				continue
			}
			if glob != "" && (ospath.IsAbs(glob) || hasDriveColon(glob)) {
				bad(where + " の範囲 `" + glob + "` が絶対パス。ワークツリーのルートからの相対で書く")
				continue
			}
			glob = strings.Trim(strings.ReplaceAll(glob, `\`, "/"), "/")
			expression := regex
			if expression == "" {
				expression = "^" + pyre.TranslateGlob(glob)
			}
			compiled, err := pyre.Compile(expression, pyre.IgnoreCase)
			if err != nil {
				bad(where + " を式にできない: " + err.Error())
				continue
			}
			entries = append(entries, Entry{Decision: section, Glob: glob, Regex: regex, Compiled: compiled})
		}
	}
	return entries, problems
}

// hasDriveColon は 2 文字目が ":" か（`C:src` のようなドライブの頭）。
func hasDriveColon(glob string) bool {
	r := []rune(glob)
	return len(r) > 1 && r[1] == ':'
}

func forbiddenIn(path string) string {
	for _, literal := range forbidden {
		if strings.Contains(path, literal) {
			return "`" + literal + "`"
		}
	}
	return ""
}

// closingFence は閉じの `---` の行の番号。先頭の行（開き）は数えない。無ければ -1。
func closingFence(lines []string) int {
	for i := 1; i < len(lines); i++ {
		if ospath.Strip(lines[i]) == Fence {
			return i
		}
	}
	return -1
}

// FrontMapping は frontmatter を読んだマッピング。Parse と同じ切り方で、苦情の文は組まない。形でない・閉じて
// いない・マッピングでないときは nil。YAML として読めなければ yaml の error（理由の文は呼び手が組む）。
// 欄を書き換えた全文を読み直して、ほかが元と同じかを比べるのに使う。
func FrontMapping(source string) (*yaml.Map, error) {
	lines := ospath.Splitlines(source)
	if len(lines) == 0 || ospath.Strip(lines[0]) != Fence {
		return nil, nil
	}
	end := closingFence(lines)
	if end < 0 {
		return nil, nil
	}
	v, err := yaml.Load(strings.Join(lines[1:end], "\n"))
	if err != nil {
		return nil, err
	}
	m, _ := v.(*yaml.Map)
	return m, nil
}

// frontmatter は先頭の frontmatter を読み、マッピング・本文・苦情を返す。止めたときはマッピングが nil。
// BOM は読み飛ばさない（「先頭の 1 バイト目から `---`」が契約で、読み飛ばすと同じファイルが書き手の道具ごとに
// 違う内容で通る）。目に見えない原因だけを名指しする。
func frontmatter(source string) (*yaml.Map, string, []rules.Problem) {
	stop := func(detail string) (*yaml.Map, string, []rules.Problem) {
		return nil, "", []rules.Problem{problem(rules.Error, fileRule, detail)}
	}
	lines := ospath.Splitlines(source)
	if len(lines) == 0 || ospath.Strip(lines[0]) != Fence {
		if len(lines) > 0 && ospath.Strip(strings.TrimLeft(lines[0], yaml.BOM)) == Fence {
			return stop("先頭に BOM (U+FEFF) が付いていて `" + Fence + "` で始まっていない。" +
				"BOM 無しの UTF-8 で保存し直してください")
		}
		return stop("先頭が `" + Fence + "` で始まっていない")
	}
	end := closingFence(lines)
	if end < 0 {
		return stop("frontmatter が `" + Fence + "` で閉じていない")
	}
	v, err := yaml.Load(strings.Join(lines[1:end], "\n"))
	if err != nil {
		return stop("frontmatter を YAML として読めない (" + err.Error() + ")")
	}
	if v == nil {
		return stop("frontmatter が空")
	}
	front, ok := v.(*yaml.Map)
	if !ok {
		return stop("frontmatter がマッピングではない")
	}
	body := strings.Join(lines[end+1:], "\n")
	if body != "" && !strings.HasSuffix(body, "\n") {
		body += "\n"
	}
	return front, body, nil
}

// DoneCopy は閉じたチケットのコピーの識別子と親。親かどうかは Parent が空かで決める。
type DoneCopy struct {
	Ticket string
	Parent string
}

// CopyFields は閉じたチケットのコピー（手元の退避・承認済みの done/）から識別子と親だけを取り出す。チケットとしての
// 検査は掛けない（検査に失敗するチケットでも閉じた記録として読む）。frontmatter が読めなければ偽。
func CopyFields(source string) (DoneCopy, bool) {
	front, _, problems := frontmatter(source)
	if front == nil || len(problems) > 0 {
		return DoneCopy{}, false
	}
	field := func(key string) string {
		switch v := front.Get(key).(type) {
		case nil, *yaml.Map, []any:
			return ""
		default:
			return ospath.Strip(pyfmt.Str(v))
		}
	}
	return DoneCopy{Ticket: field("ticket"), Parent: field("parent")}, true
}
