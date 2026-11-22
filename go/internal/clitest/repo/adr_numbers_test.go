package repo

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/infra/pathcase"
)

// ADR の番号の決まりを守る（issue #98）。番号は 1 枚につき 1 つ。同じ番号が 2 枚にあると、本文や実装に書いた
// `ADR-00NN` がどちらの決定を指すか読めない。
//
// 見るのは次の 3 つ。
//  1. 番号が重複しない
//  2. タイトルが `# ADR-<番号>:` で始まり、その番号がファイル名と合う
//  3. ADR が、docs/30_adr.md の入口の表にあるディレクトリに置かれている
//
// 止められるのは合流したあとになる。書いた直後の重なりは .claude/hooks/check-adr.sh が止める。
// 読むのは docs/30_adr/ のファイルと docs/30_adr.md だけ。

// `0064-extension-board-in-react.md` → 0064。番号を持たないファイルは外れる。
var adrFileName = regexp.MustCompile(`^(\d{4})-.+\.md$`)

// タイトルの 1 行目。`# ADR-0001: 言語を Go から Python に移す` の形。
var adrTitle = regexp.MustCompile(`^# ADR-(\d{4}): \S`)

// 入口の表の 1 行。「| `30_adr/rules/` | 判定とルール… |」の形。
var adrTopicRow = regexp.MustCompile("(?m)^\\| `30_adr/([\\w-]+)/` \\|")

const (
	adrDirRel   = "docs/30_adr"
	adrIndexRel = "docs/30_adr.md"
	// 数えられていることを確かめる下限。書き方が変わって 1 枚も数えられなくなったときに、重複なしと言って通らないため。
	adrMinimum = 50
)

// adrSheet は docs/30_adr/<主題>/ に置いてある ADR の 1 枚（主題/ファイル名）。
type adrSheet string

func (s adrSheet) number() string { return filepath.Base(string(s))[:4] }
func (s adrSheet) topic() string  { return filepath.Dir(string(s)) }

// adrSheets は主題のディレクトリの中の ADR を、番号の順（ファイル名の順）に並べて返す。
func adrSheets(t *testing.T, adrDir string) []adrSheet {
	t.Helper()
	topics, err := os.ReadDir(adrDir)
	if err != nil {
		t.Fatal(err)
	}
	var found []adrSheet
	for _, topic := range topics {
		if !topic.IsDir() {
			continue
		}
		files, err := os.ReadDir(filepath.Join(adrDir, topic.Name()))
		if err != nil {
			t.Fatal(err)
		}
		for _, f := range files {
			if adrFileName.MatchString(f.Name()) {
				found = append(found, adrSheet(topic.Name()+"/"+f.Name()))
			}
		}
	}
	sort.Slice(found, func(i, j int) bool { return filepath.Base(string(found[i])) < filepath.Base(string(found[j])) })
	return found
}

// adrTitleLine は本文のタイトルの行。頭の frontmatter（.ccnavi/docs/frontmatter.md）を飛ばし、その次の空でない行。
func adrTitleLine(body string) string {
	lines := strings.Split(strings.ReplaceAll(body, "\r\n", "\n"), "\n")
	if len(lines) > 0 && lines[0] == "---" {
		for i := 1; i < len(lines); i++ {
			if lines[i] == "---" {
				lines = lines[i+1:]
				break
			}
		}
	}
	for _, line := range lines {
		if strings.TrimSpace(line) != "" {
			return line
		}
	}
	return ""
}

func adrRead(t *testing.T, adrDir string, s adrSheet) string {
	t.Helper()
	body, err := os.ReadFile(filepath.Join(adrDir, filepath.FromSlash(string(s))))
	if err != nil {
		t.Fatal(err)
	}
	return string(body)
}

// adrPaths はテストの対象の 2 つの場所（docs/30_adr/ と docs/30_adr.md）を、リポジトリの根から組む。
func adrPaths() (dir, index string) {
	root := base.Root()
	return filepath.Join(root, filepath.FromSlash(adrDirRel)), filepath.Join(root, filepath.FromSlash(adrIndexRel))
}

// adrSheetsChecked は数えられていることを確かめてから ADR の一覧を返す。
func adrSheetsChecked(t *testing.T) (adrDir, index string, sheets []adrSheet) {
	t.Helper()
	adrDir, index = adrPaths()
	sheets = adrSheets(t, adrDir)
	if len(sheets) <= adrMinimum {
		t.Fatalf("ADR を数えられていない（%s）: %d 枚", adrDir, len(sheets))
	}
	return adrDir, index, sheets
}

// 番号は 1 枚につき 1 つ。足すときは最後尾の次を採る。
func TestAdrNumbersAreUnique(t *testing.T) {
	t.Parallel()
	_, _, sheets := adrSheetsChecked(t)
	where := map[string][]string{}
	for _, s := range sheets {
		where[s.number()] = append(where[s.number()], string(s))
	}
	var duplicated []string
	for num, names := range where {
		if len(names) > 1 {
			duplicated = append(duplicated, num+": "+strings.Join(names, " / "))
		}
	}
	sort.Strings(duplicated)
	if len(duplicated) > 0 {
		t.Errorf("同じ番号が 2 枚にある。引かれている数が少ないほうを空き番号へ動かし、旧番号を docs/30_adr.md の表に残す: %v", duplicated)
	}
}

// タイトルは `# ADR-<番号>:` で始まり、番号はファイル名と合う。
func TestAdrTitleCarriesItsOwnNumber(t *testing.T) {
	t.Parallel()
	dir, _, sheets := adrSheetsChecked(t)
	var wrong []string
	for _, s := range sheets {
		first := adrTitleLine(adrRead(t, dir, s))
		found := adrTitle.FindStringSubmatch(first)
		switch {
		case found == nil:
			r := []rune(first)
			if len(r) > 40 {
				r = r[:40]
			}
			wrong = append(wrong, string(s)+": タイトルが `# ADR-<番号>: <タイトル>` の形でない（"+string(r)+"）")
		case found[1] != s.number():
			wrong = append(wrong, string(s)+": タイトルの番号が ADR-"+found[1]+" でファイル名と違う")
		}
	}
	if len(wrong) > 0 {
		t.Errorf("%v", wrong)
	}
}

// ADR は、入口の表に載っているディレクトリのどれかに置かれている。名前は大文字小文字を区別せずに比べる。
func TestEverySheetSitsInAListedTopic(t *testing.T) {
	t.Parallel()
	dir, index, sheets := adrSheetsChecked(t)
	body, err := os.ReadFile(index)
	if err != nil {
		t.Fatal(err)
	}
	listed := map[string]bool{} // pathcase.Fold した主題名
	var listedNames []string
	for _, m := range adrTopicRow.FindAllStringSubmatch(string(body), -1) {
		if !listed[pathcase.Fold(m[1])] {
			listed[pathcase.Fold(m[1])] = true
			listedNames = append(listedNames, m[1])
		}
	}
	if len(listed) == 0 {
		t.Fatalf("入口の表からディレクトリを読めない（%s）", index)
	}

	unlisted := map[string]bool{}
	for _, s := range sheets {
		if !listed[pathcase.Fold(s.topic())] {
			unlisted[s.topic()] = true
		}
	}
	if len(unlisted) > 0 {
		t.Errorf("表に載っていないディレクトリがある。docs/30_adr.md の表に 1 行足す: %v", sortedKeys(unlisted))
	}

	placed := map[string]bool{}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if e.IsDir() {
			placed[pathcase.Fold(e.Name())] = true
		}
	}
	var empty []string
	for _, name := range listedNames {
		if !placed[pathcase.Fold(name)] {
			empty = append(empty, name)
		}
	}
	sort.Strings(empty)
	if len(empty) > 0 {
		t.Errorf("表に載っているが、docs/30_adr/ に無いディレクトリがある: %v", empty)
	}
}

// ADR は主題のディレクトリの中に置き、docs/30_adr/ 直下には置かない。
func TestNoSheetSitsAtTheTop(t *testing.T) {
	t.Parallel()
	dir, _ := adrPaths()
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	var top []string
	for _, e := range entries {
		if !e.IsDir() && adrFileName.MatchString(e.Name()) {
			top = append(top, e.Name())
		}
	}
	if len(top) > 0 {
		t.Errorf("主題のディレクトリの外に ADR がある: %v", top)
	}
}
