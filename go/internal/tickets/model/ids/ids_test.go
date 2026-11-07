package ids

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/testkit"
)

// Python・sh・TS が同じ表（tests/fixtures/branch-to-id.json）で変換と採用の可否の一致を確かめる。
func TestBranchToIDTable(t *testing.T) {
	data, err := os.ReadFile(filepath.Join(testkit.RepoRoot(t), "tests", "fixtures", "branch-to-id.json"))
	if err != nil {
		t.Fatal(err)
	}
	var table struct {
		Convert []struct{ Branch, ID string }
		Adopt   []struct {
			Branch string
			OK     bool
		}
	}
	if err := json.Unmarshal(data, &table); err != nil {
		t.Fatal(err)
	}
	for _, c := range table.Convert {
		if got := BranchToID(c.Branch); got != c.ID {
			t.Errorf("BranchToID(%q) = %q, want %q", c.Branch, got, c.ID)
		}
	}
	for _, c := range table.Adopt {
		if problem := AdoptionIDProblem(c.Branch); (problem == "") != c.OK {
			t.Errorf("AdoptionIDProblem(%q) = %q", c.Branch, problem)
		}
	}
}

// 予約した名前は infra/config の先頭の語にも使えない（config はこのリストのコピーを持つ）。
func TestReservedNamesAreNotBranchPrefixes(t *testing.T) {
	for _, name := range ReservedBranchIDs {
		if config.IsBranchPrefix(name) {
			t.Errorf("%s が先頭の語に使える", name)
		}
	}
}

func TestParseChild(t *testing.T) {
	cases := []struct {
		id   string
		want Child
		ok   bool
	}{
		{"web-12-05-01", Child{"web-12", 5, 1}, true},
		{"i0050-02-01", Child{"i0050", 2, 1}, true},
		{"i0050-2-01", Child{}, false},
		{"i0050-02-01\n", Child{}, false},
		{"-02-01", Child{}, false},
	}
	for _, c := range cases {
		got, ok := ParseChild(c.id)
		if ok != c.ok || got != c.want {
			t.Errorf("ParseChild(%q) = %v %v", c.id, got, ok)
		}
	}
	if ChildID("p", 5, 1) != "p-05-01" {
		t.Error(ChildID("p", 5, 1))
	}
}

const idsOracle = `
import json, sys
from ccnavi.infra import settings
from ccnavi.tickets.model import ticket_ids as ti
c = json.load(sys.stdin)
P = settings.DEFAULT_BRANCH_PREFIXES
def ident(n, title, project, prefix):
    try:
        return list(ti.issue_identifier_parts(n, title, project, prefix)) + [""]
    except ValueError as e:
        return ["", False, str(e)]
json.dump({
    "id": [ti.id_problem(x) for x in c["names"]],
    "branch": [ti.branch_problem(x) for x in c["names"]],
    "answer": [ti.id_answer(x) for x in c["names"]],
    "tail": [bool(ti.child_tail_pattern().search(x)) for x in c["names"]],
    "number": [ti.identifier_number(x, P) or 0 for x in c["names"]],
    "warning": [ti.direct_name_warning(x, P) for x in c["names"][:3]] + [ti.direct_name_warning("x", P, "/ws")],
    "serial": [ti.next_serial(c["names"], P), ti.next_serial(c["names"], P, skip_children=False)],
    "issue": [ident(*a) for a in c["issues"]],
}, sys.stdout, ensure_ascii=False)
`

type idsAnswers struct {
	ID      []string   `json:"id"`
	Branch  []string   `json:"branch"`
	Answer  [][]string `json:"answer"`
	Tail    []bool     `json:"tail"`
	Number  []int      `json:"number"`
	Warning []string   `json:"warning"`
	Serial  []int      `json:"serial"`
	Issue   [][]any    `json:"issue"`
}

var idsNames = []string{
	"feature-12-login", "x", "feature/12-login", "Feature-3-x", "a-01", "feature-7-x-01-02", "feature-9-x-01",
	"", "ガ", "あ-1", "１", ".hidden", "a..b", "a//b", "/a", "a/", "a.", "x/.y", "x/y.lock", "refs/x", "Origin/x",
	"x/HEAD", "x/fetch_head", "main", "MAIN/x", "release-1", "Release/2", "develop", "a b", "a;b",
	strings.Repeat("a", 59), strings.Repeat("a", 65), strings.Repeat("b/", 101) + "c",
	"docs-5-日本語",
}

var idsIssues = [][]any{
	{12, "Login page", "", "feature"},
	{12, "ログイン画面を直す", "web", "fix"},
	{12, "!!!", "", "feature"},
	{12, "Ａｂｃ　ﾃｽﾄ", "", "feature"},
	{12, "release 1 01", "", "feature"},
	{12, "alpha beta gamma delta epsilon zeta eta theta iota kappa", "", "feature"},
	{12, strings.Repeat("長", 60), "", "feature"},
	{0, "x", "", "feature"},
	{3, "x", "bad name", "feature"},
	{3, "x", "", "main"},
	{3, "x", "", "Bad"},
}

func TestIDsMatchPython(t *testing.T) {
	want := testkit.Answers[idsAnswers](t, "ids", idsOracle, map[string]any{"names": idsNames, "issues": idsIssues})
	p := config.DefaultBranchPrefixes
	got := idsAnswers{}
	for _, x := range idsNames {
		got.ID = append(got.ID, IDProblem(x))
		got.Branch = append(got.Branch, BranchProblem(x))
		got.Answer = append(got.Answer, IDAnswer(x))
		got.Tail = append(got.Tail, HasChildTail(x))
		n, _ := IdentifierNumber(x, p)
		got.Number = append(got.Number, n)
	}
	for _, x := range idsNames[:3] {
		got.Warning = append(got.Warning, DirectNameWarning(x, p, ""))
	}
	got.Warning = append(got.Warning, DirectNameWarning("x", p, "/ws"))
	got.Serial = []int{NextSerial(idsNames, p, true), NextSerial(idsNames, p, false)}
	for _, a := range idsIssues {
		ident, short, err := IssueIdentifier(a[0].(int), a[1].(string), a[2].(string), a[3].(string))
		msg := ""
		if err != nil {
			msg = err.Error()
		}
		got.Issue = append(got.Issue, []any{ident, short, msg})
	}
	gotJSON, _ := json.Marshal(got)
	var round idsAnswers
	_ = json.Unmarshal(gotJSON, &round)
	for _, f := range []struct {
		name      string
		got, want any
	}{
		{"id", round.ID, want.ID}, {"branch", round.Branch, want.Branch}, {"answer", round.Answer, want.Answer},
		{"tail", round.Tail, want.Tail}, {"number", round.Number, want.Number}, {"warning", round.Warning, want.Warning},
		{"serial", round.Serial, want.Serial}, {"issue", round.Issue, want.Issue},
	} {
		if !reflect.DeepEqual(f.got, f.want) {
			t.Errorf("%s\n got  %v\n want %v", f.name, f.got, f.want)
		}
	}
}

func TestSameID(t *testing.T) {
	if !SameID("I0001", "i0001") || SameID("i0001", "i0002") {
		t.Error("SameID")
	}
}
