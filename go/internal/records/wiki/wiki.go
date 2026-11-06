package wiki

import (
	"sort"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
)

// 置き場（wiki を含むツリーからの相対。区切りは "/"）。
const (
	PatternsDir = "docs/wiki/patterns"
	ImpactDir   = "docs/wiki/impact"
)

// Target は反映先の種類。
type Target string

// Targets は反映先の種類の全部（表示の順）。
var Targets = []Target{"rule", "risk", "phase", "template", "skill", "docs"}

// patternType はパターンの frontmatter の type。
const patternType = "learning-wiki"

// Decision は採否の記録の status。
type Decision string

const (
	Pending  Decision = "承認待ち"
	Approved Decision = "承認"
	Rejected Decision = "却下"
	Deferred Decision = "延期"
)

var decisions = []Decision{Pending, Approved, Rejected, Deferred}

// State はパターンの状態。
type State string

const (
	Proposing State = "提案中"
	Candidate State = "候補"
	Settled   State = "決着"
)

// Kind は壊れたファイルの種類。
type Kind string

const (
	KindPattern Kind = "pattern"
	KindImpact  Kind = "impact"
)

// MinSourcesToReflect は統合の子がパターンに反映できる観測元の数の下限。1 件だけの気づきは環境の癖や
// 一回限りの事情のことが多いので、パターンにしない。候補にする閾値（DefaultSources）とは別。
const MinSourcesToReflect = 2

// 観測元の数の閾値と、通知する候補の件数の既定（CCNAVI_LEARNING_SOURCES・CCNAVI_LEARNING_NOTICE_COUNT）。
const (
	DefaultSources     = 3
	DefaultNoticeCount = 5
)

// Pattern はパターン 1 本。Name はファイル名から .md を除いたもの。
type Pattern struct {
	Name   string
	Target Target
	// Sources は重複を除いた観測元の ID（大文字小文字だけが違うものは同じ。残すのは最初の綴り）。
	Sources []string
	Title   string
}

// Count は観測元の数。
func (p Pattern) Count() int { return len(p.Sources) }

// Impact は採否の記録 1 本。
type Impact struct {
	Proposal string
	Target   Target
	Files    []string
	// Patterns は動機のパターン名（書かれた綴り）と、そのときの観測元の数（書かれた順）。
	Patterns []PatternCount
	Status   Decision
	Reason   string
	// DecidedAt はタイムゾーン付きの日時。承認待ちは無いことがある。
	DecidedAt *time.Time
}

// PatternCount は採否の記録の patterns の 1 つ。
type PatternCount struct {
	Name  string
	Count int
}

// Broken は frontmatter が壊れたファイル。File は wiki を含むツリーからの相対パス。
type Broken struct {
	File    string
	Kind    Kind
	Problem string
}

// Wiki は読んだ wiki の全部。
type Wiki struct {
	Patterns []Pattern
	Impacts  []Impact
	Broken   []Broken
	// Present は docs/wiki/ の下にパターンか採否の記録のディレクトリが在ったか。
	Present bool
}

// Row はパターンごとの状態と観測元の数。
type Row struct {
	Name   string
	Target Target
	Status State
	Count  int
	Title  string
}

// Reached は観測元の数が閾値以上か。
func (r Row) Reached(threshold int) bool { return r.Count >= threshold }

// key は名前を比べるためのキー。
func key(name string) string { return pathcase.Fold(ospath.Strip(name)) }

func targetsText() string {
	words := make([]string, len(Targets))
	for i, t := range Targets {
		words[i] = string(t)
	}
	return strings.Join(words, " ")
}

func decisionsText() string {
	words := make([]string, len(decisions))
	for i, d := range decisions {
		words[i] = string(d)
	}
	return strings.Join(words, " ")
}

func patternWhere(name string) string { return PatternsDir + "/" + name + ".md" }
func impactWhere(name string) string  { return ImpactDir + "/" + name + ".md" }

// Build はファイル名（.md を除く）から本文への対応から Wiki を組む。並びは名前順で、読み順によらない。
func Build(patternTexts, impactTexts map[string]string, present bool) *Wiki {
	w := &Wiki{Present: present}
	seen := map[string]string{}
	for _, name := range sortedKeys(patternTexts) {
		if first, ok := seen[key(name)]; ok {
			w.Broken = append(w.Broken, Broken{patternWhere(name), KindPattern, first + " と大文字小文字だけが違う同名"})
			continue
		}
		seen[key(name)] = name
		p, problem := ParsePattern(name, patternTexts[name])
		if problem != "" {
			w.Broken = append(w.Broken, Broken{patternWhere(name), KindPattern, problem})
			continue
		}
		w.Patterns = append(w.Patterns, p)
	}
	for _, name := range sortedKeys(impactTexts) {
		i, problem := parseImpact(impactTexts[name])
		if problem != "" {
			w.Broken = append(w.Broken, Broken{impactWhere(name), KindImpact, problem})
			continue
		}
		w.Impacts = append(w.Impacts, i)
	}
	return w
}

func sortedKeys(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func mentions(i Impact, name string) bool {
	for _, p := range i.Patterns {
		if key(p.Name) == key(name) {
			return true
		}
	}
	return false
}

func countIn(i Impact, name string) int {
	best := 0
	for _, p := range i.Patterns {
		if key(p.Name) == key(name) && p.Count > best {
			best = p.Count
		}
	}
	return best
}

// later は a が b より新しいか（日時をタイムゾーンを揃えて比べ、同じなら提案の名前順で後のもの）。
func later(a, b Impact) bool {
	if c := a.DecidedAt.Compare(*b.DecidedAt); c != 0 {
		return c > 0
	}
	return key(a.Proposal) > key(b.Proposal)
}

// status はパターンの状態。count は今の観測元の数、impacts は壊れていない採否の記録の全部。
func status(name string, count int, impacts []Impact) State {
	var last *Impact
	for idx := range impacts {
		i := impacts[idx]
		if !mentions(i, name) {
			continue
		}
		if i.Status == Pending {
			return Proposing
		}
		if i.DecidedAt != nil && (last == nil || later(i, *last)) {
			last = &impacts[idx]
		}
	}
	if last == nil {
		return Candidate
	}
	if last.Status == Deferred && count > countIn(*last, name) {
		return Candidate
	}
	return Settled
}

// Rows はパターンごとの状態と観測元の数（名前順）。
func Rows(w *Wiki) []Row {
	patterns := append([]Pattern(nil), w.Patterns...)
	sort.SliceStable(patterns, func(i, j int) bool { return key(patterns[i].Name) < key(patterns[j].Name) })
	out := make([]Row, 0, len(patterns))
	for _, p := range patterns {
		out = append(out, Row{p.Name, p.Target, status(p.Name, p.Count(), w.Impacts), p.Count(), p.Title})
	}
	return out
}

// Candidates は候補のパターン（名前順）。
func Candidates(w *Wiki) []Row {
	var out []Row
	for _, r := range Rows(w) {
		if r.Status == Candidate {
			out = append(out, r)
		}
	}
	return out
}

// PendingImpacts は承認待ちの採否の記録（提案の名前順）。
func PendingImpacts(w *Wiki) []Impact {
	var out []Impact
	for _, i := range w.Impacts {
		if i.Status == Pending {
			out = append(out, i)
		}
	}
	sort.SliceStable(out, func(a, b int) bool { return key(out[a].Proposal) < key(out[b].Proposal) })
	return out
}

// SourcesThreshold は書かれた閾値。読めない値と 1 未満は既定。
func SourcesThreshold(written string) int { return config.Int(written, DefaultSources, 1) }

// NoticeCount は書かれた通知の件数。読めない値と 1 未満は既定。
func NoticeCount(written string) int { return config.Int(written, DefaultNoticeCount, 1) }
