package wiki

import (
	"sort"
	"strings"
	"time"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
)

// fileLimit は 1 本の上限（バイト）。パターンも採否の記録も、これを超えない。
const fileLimit = 256 * 1024

// regularModes は git のふつうのファイルの mode。
var regularModes = map[string]bool{"100644": true, "100755": true}

const mdExt = ".md"

// 読まない理由。
const (
	problemLink      = "シンボリックリンクは読まない"
	problemTooLarge  = "大きすぎて読まない"
	problemUnread    = "読めない"
	problemIrregular = "ふつうのファイルでない"
)

// stem は .md（大文字小文字を問わない）を除いた名前。.md でなければ空。
func stem(file string) string {
	// 畳んで ".md" になる字は M・m・D・d だけなので、元の綴りの末尾も 3 バイト。
	if pathcase.HasExt(file, mdExt) {
		return file[:len(file)-len(mdExt)]
	}
	return ""
}

type texts struct {
	patterns, impacts map[string]string
	broken            []Broken
	present           bool
}

func newTexts() *texts {
	return &texts{patterns: map[string]string{}, impacts: map[string]string{}}
}

func (t *texts) put(kind Kind, name, body string) {
	if kind == KindPattern {
		t.patterns[name] = body
	} else {
		t.impacts[name] = body
	}
}

func (t *texts) build() *Wiki {
	w := Build(t.patterns, t.impacts, t.present)
	w.Broken = append(w.Broken, t.broken...)
	sort.SliceStable(w.Broken, func(i, j int) bool { return w.Broken[i].File < w.Broken[j].File })
	return w
}

var places = []struct {
	kind Kind
	dir  string
}{{KindPattern, PatternsDir}, {KindImpact, ImpactDir}}

// LoadTree はワークツリーの docs/wiki/ を読む。無ければ Present が偽の空の Wiki。
func LoadTree(treeRoot string) *Wiki {
	t := newTexts()
	for _, place := range places {
		directory := ospath.Join(treeRoot, strings.Split(place.dir, "/")...)
		names, err := ospath.DiskListdir(directory)
		if err != nil {
			continue
		}
		t.present = true
		for _, file := range names {
			name := stem(file)
			if name == "" {
				continue
			}
			path := ospath.Join(directory, file)
			where := place.dir + "/" + file
			_, size, _ := ospath.Stat(path)
			switch {
			case ospath.IsLink(path):
				t.broken = append(t.broken, Broken{where, place.kind, problemLink})
			case !ospath.IsFile(path):
			case size > fileLimit:
				t.broken = append(t.broken, Broken{where, place.kind, problemTooLarge})
			default:
				body, ok := fsio.ReadText(path, fsio.Replace)
				if !ok {
					t.broken = append(t.broken, Broken{where, place.kind, problemUnread})
				} else {
					t.put(place.kind, name, body)
				}
			}
		}
	}
	return t.build()
}

// CommitOf は参照が指すコミットの sha。決められなければ空。deadline がゼロ値なら git の既定の期限。
func CommitOf(repo, ref string, deadline time.Time) string {
	done := git.Run(repo, []string{"rev-parse", "--verify", "--quiet", ref + "^{commit}"}, git.Options{Deadline: deadline})
	if !done.OK() {
		return ""
	}
	return ospath.Strip(done.Out)
}

// LoadCommit はコミット rev の docs/wiki/ を、チェックアウトせずに読む。読めなければ nil。
// docs/wiki/ がそのコミットに無いのは読めた結果で、Present が偽の空の Wiki。期限を過ぎた・git が失敗した・
// blob が読めないときは nil（一部だけ読んだ答えは返さない）。
func LoadCommit(repo, rev string, deadline time.Time) *Wiki {
	listed := git.Run(repo, []string{"ls-tree", "-z", "-l", rev, PatternsDir + "/", ImpactDir + "/"}, git.Options{Deadline: deadline})
	if !listed.OK() {
		return nil
	}
	t := newTexts()
	for _, entry := range strings.Split(listed.Out, "\x00") {
		if entry == "" {
			continue
		}
		meta, path, _ := strings.Cut(entry, "\t")
		fields := strings.FieldsFunc(meta, ospath.IsSpace)
		directory, file := "", path
		if i := strings.LastIndex(path, "/"); i >= 0 {
			directory, file = path[:i], path[i+1:]
		}
		kind := Kind("")
		for _, place := range places {
			if directory == place.dir {
				kind = place.kind
			}
		}
		if len(fields) != 4 || kind == "" {
			continue
		}
		t.present = true
		name := stem(file)
		if name == "" {
			continue
		}
		mode, typ, sha, size := fields[0], fields[1], fields[2], fields[3]
		// Python の size.isdigit() and int(size) > FILE_LIMIT。int() が読めない数字（²）は大きすぎるとして名指しする
		// （Python は例外で止まる。git の出力には現れない）。
		n, sizeErr := pyfmt.ParseInt(size)
		switch {
		case typ != "blob" || !regularModes[mode]:
			t.broken = append(t.broken, Broken{path, kind, problemIrregular})
		case !pyfmt.IsDigit(size) || sizeErr != nil || n > fileLimit:
			t.broken = append(t.broken, Broken{path, kind, problemTooLarge})
		default:
			blob := git.Run(repo, []string{"cat-file", "blob", sha}, git.Options{Deadline: deadline})
			if !blob.OK() {
				return nil
			}
			t.put(kind, name, blob.Out)
		}
	}
	return t.build()
}
