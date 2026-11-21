package flow

// レビュー済みのマーカーの actor と、依頼の後に動いたかの判定の受入テスト。元: tests/ticket/test_review_actor.py。
//
//  1. 手元の review confirm --actor <アカウント> は、マーカーに actor と via: cli を書く。--actor が無ければマーカーは前と同じ中身
//  2. --actor の形と、review confirm の外で渡されたときは断る
//  3. 依頼の後にユーザが見るものが動いたかは、手元と Chrome が同じに決める。Chrome は compare API の一覧を渡し、
//     打ち切られた（null）なら動いたと数える
//  4. Chrome のレビュー済みのマーカーは、手元の --actor つきのマーカーと経路（via）と時刻のほかは同じ
import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/pyjson"
)

const resultJSON = `{"host": "fixture", "mr": {"number": 7, "url": "u/7"}, "threads": [], "reviews": []}`

// actorTree はフェーズ 1 を依頼まで進めて取得した結果を置いた木と結果ファイル（ready）。
func actorTree(t *testing.T) (*Tree, Fixture) {
	t.Helper()
	tr := chromeTree(t)
	f := tr.reviewedPhaseOne()
	tr.CommitParent("requested")
	Write(t, f.Path, resultJSON)
	return tr, f
}

func (tr *Tree) actorMarkPath(kind string) string {
	return filepath.Join(tr.ParentTree, ".ccnavi", "approved", "phases", "i0001", "1."+kind)
}

func (tr *Tree) confirmAs(f Fixture, extra ...string) run.Result {
	tr.T.Helper()
	return tr.Ccnavi(append([]string{"--cwd", tr.ParentTree, "--phase", "1", "review", "confirm", "--result", f.Path}, extra...)...)
}

// keysOf はマーカーのキーを書かれた順に返す。
func keysOf(t testing.TB, text string) []string {
	t.Helper()
	v, err := pyjson.Loads(text)
	if err != nil {
		t.Fatal(err)
	}
	o, ok := v.(*pyjson.Object)
	if !ok {
		t.Fatalf("オブジェクトでない: %s", text)
	}
	return o.Keys()
}

func sorted(keys []string) []string { k := slices.Clone(keys); slices.Sort(k); return k }

func (tr *Tree) hostCompare() map[string]any {
	tr.T.Helper()
	recorded := js(ReadJSON(tr.T, tr.actorMarkPath("requested")), "head")
	head := strings.TrimSpace(tr.Parent("rev-parse", "HEAD"))
	names := tr.Parent("diff", "--name-only", "--no-renames", recorded+"..HEAD")
	files := []any{}
	for _, n := range strings.Fields(names) {
		files = append(files, n)
	}
	return map[string]any{"base": recorded, "head": head, "files": files}
}

// chromeConfirm は Chrome の confirm。読んだ P の先頭は head（既定は compare の先頭）。
func (tr *Tree) chromeConfirm(compare map[string]any, actor map[string]any, head string) map[string]any {
	tr.T.Helper()
	if head == "" {
		head = js(compare, "head")
	}
	var result any
	_ = json.Unmarshal([]byte(resultJSON), &result)
	extra := map[string]any{"phase": 1, "result": result, "compare": compare}
	if actor != nil {
		extra["actor"] = actor
	}
	return tr.askChrome(tr.chromeRequest("confirm", "i0001", map[string]string{"i0001": head}, extra))
}

func TestWithoutAnActorTheMarkIsAsBefore(t *testing.T) {
	t.Parallel()
	tr, f := actorTree(t)
	OK(t, tr.confirmAs(f))
	text := Read(t, tr.actorMarkPath("reviewed"))
	mark := ParseJSON(t, text)
	if !slices.Equal(sorted(keysOf(t, text)), []string{"accepted", "at", "mr"}) || jf(mark, "mr") != 7 || len(jl(mark, "accepted")) != 0 {
		t.Errorf("mark = %s", text)
	}
}

func TestAnActorGoesIntoTheMark(t *testing.T) {
	t.Parallel()
	tr, f := actorTree(t)
	OK(t, tr.confirmAs(f, "--actor", "octo-reviewer"))
	text := Read(t, tr.actorMarkPath("reviewed"))
	mark := ParseJSON(t, text)
	if !slices.Equal(keysOf(t, text), []string{"mr", "accepted", "actor", "via", "at"}) || jf(mark, "mr") != 7 || js(mark, "actor") != "octo-reviewer" || js(mark, "via") != "cli" {
		t.Errorf("mark = %s", text)
	}
}

func TestAMalformedActorIsRefusedAndNothingIsWritten(t *testing.T) {
	t.Parallel()
	tr, f := actorTree(t)
	for _, bad := range []string{"a b", "x/y", "<script>", strings.Repeat("a", 101)} {
		done := Fails(t, tr.confirmAs(f, "--actor", bad))
		Has(t, done.Stderr, "--actor")
		if Exists(tr.actorMarkPath("reviewed")) {
			t.Errorf("%q でマーカーが書かれた", bad)
		}
	}
}

func TestAnActorOutsideReviewConfirmIsRefused(t *testing.T) {
	t.Parallel()
	tr := chromeTree(t)
	Has(t, Fails(t, tr.Ccnavi("--actor", "octo", "--explain", "--json")).Stderr, "review confirm")
}

// ---- 依頼の後に動いたか

func TestCodeMovedAfterTheRequestIsRefusedTheSameByChromeAndLocally(t *testing.T) {
	t.Parallel()
	tr, f := actorTree(t)
	Write(t, filepath.Join(tr.ParentTree, "src", "late.py"), "print(1)\n")
	tr.CommitParent("late code")
	tr.Parent("push", "--quiet", "origin", ParentName)
	compare := tr.hostCompare()
	if !slices.Contains(strs(jl(compare, "files")), "src/late.py") {
		t.Fatalf("files = %v", compare["files"])
	}
	chrome := tr.chromeConfirm(compare, nil, "")
	local := tr.confirmAs(f)
	wantCode(t, local, 1)
	// 手元の案内はワークスペースルートからの絶対パス、Chrome は仮のツリーの部分を除いた相対パス。
	// Windows では案内の中のパスが `/` 区切りで来るので、区切りを揃えてから根を除く。
	root := filepath.ToSlash(tr.Root)
	var same []string
	for _, p := range strs(jl(chrome, "problems")) {
		p = strings.ReplaceAll(filepath.ToSlash(p), root+"/memfs/", "")
		same = append(same, strings.ReplaceAll(p, root+"/", ""))
	}
	var said []string
	for _, l := range strings.Split(strings.TrimRight(local.Stderr, "\r\n"), "\n") {
		l = strings.TrimSuffix(l, "\r")
		said = append(said, strings.ReplaceAll(strings.ReplaceAll(filepath.ToSlash(l), filepath.ToSlash(tr.Root)+"/", ""), root+"/", ""))
	}
	// 手元は理由のあとに「次:」を 1 行足す。それ以外は Chrome と同じ文面。
	if len(said) == 0 || !strings.HasPrefix(said[len(said)-1], "次: ") {
		t.Fatalf("手元の最後が「次:」でない: %v", said)
	}
	said = said[:len(said)-1]
	if !reflect.DeepEqual(same, said) {
		t.Errorf("Chrome と手元の文面が違う:\nChrome: %q\n手元: %q", same, said)
	}
	Has(t, strs(jl(chrome, "problems"))[0], "依頼の後に親の HEAD が動いている")
	if !isNull(chrome, "changes") {
		t.Errorf("changes = %v", chrome["changes"])
	}
}

func TestATruncatedOrMismatchedCompareCountsAsMoved(t *testing.T) {
	t.Parallel()
	tr, _ := actorTree(t)
	compare := tr.hostCompare()
	with := func(k string, v any) map[string]any {
		m := map[string]any{}
		for key, val := range compare {
			m[key] = val
		}
		m[k] = v
		return m
	}
	for _, broken := range []map[string]any{with("files", nil), with("base", strings.Repeat("c", 40)), with("head", strings.Repeat("d", 40))} {
		chrome := tr.chromeConfirm(broken, nil, js(compare, "head"))
		if !isNull(chrome, "changes") {
			t.Errorf("%v: changes = %v", broken, chrome["changes"])
		}
		said := strings.Join(strs(jl(chrome, "problems")), "\n")
		Has(t, said, "依頼の後に親の HEAD が動いている")
		// 何が変わったかは断定しない固定の文。
		Has(t, said, "何が変わったかユーザに確かめてもらい、依頼し直す")
	}
}

func TestOnlyThePlacesMovedSoChromeWrites(t *testing.T) {
	t.Parallel()
	tr, _ := actorTree(t)
	chrome := tr.chromeConfirm(tr.hostCompare(), nil, "")
	if len(jl(chrome, "problems")) != 0 || chrome["changes"] == nil {
		t.Errorf("chrome = %v", chrome)
	}
}

// Chrome のマーカーは、手元の --actor つきのマーカーと経路（via）と時刻のほかは同じ。
func TestChromeAndLocalMarksDifferOnlyByTheRouteAndTheTime(t *testing.T) {
	t.Parallel()
	tr, f := actorTree(t)
	chrome := tr.chromeConfirm(tr.hostCompare(), map[string]any{"account": "octo-reviewer"}, "")
	row := changeRow(jl(chrome, "changes", "i0001"), "1.reviewed")
	if row == nil {
		t.Fatalf("1.reviewed の行が無い: %v", chrome["changes"])
	}
	remoteText := js(row, "content")
	remote := ParseJSON(t, remoteText)
	OK(t, tr.confirmAs(f, "--actor", "octo-reviewer"))
	localText := Read(t, tr.actorMarkPath("reviewed"))
	local := ParseJSON(t, localText)
	if js(remote, "via") != "chrome" || js(local, "via") != "cli" || js(remote, "at") != chromeStamp {
		t.Errorf("via/at: remote %v local %v", remote, local)
	}
	for _, m := range []map[string]any{remote, local} {
		delete(m, "via")
		delete(m, "at")
	}
	if !reflect.DeepEqual(remote, local) {
		t.Errorf("remote %v, local %v", remote, local)
	}
	if !slices.Equal(keysOf(t, remoteText), keysOf(t, localText)) {
		t.Errorf("キーの順: remote %v, local %v", keysOf(t, remoteText), keysOf(t, localText))
	}
}

func TestTheBoardNamesRequestedPhasesUntilTheyAreReviewed(t *testing.T) {
	t.Parallel()
	tr, f := actorTree(t)
	board := tr.askChrome(tr.chromeRequest("board", "i0001", nil, nil))
	want := []any{map[string]any{"phase": 1.0, "mr": 7.0, "host": "fixture", "children": []any{"i0001-01-01"}}}
	if !reflect.DeepEqual(board["reviewable"], want) {
		t.Errorf("reviewable = %v", board["reviewable"])
	}
	OK(t, tr.confirmAs(f))
	tr.CommitParent("reviewed")
	board = tr.askChrome(tr.chromeRequest("board", "i0001", nil, nil))
	if l, ok := board["reviewable"].([]any); !ok || len(l) != 0 {
		t.Errorf("reviewable = %v", board["reviewable"])
	}
}

// ---- Chrome のレビュー済みを入れた後のレビューで決めたこと。どれも厳しくする向き。

const alreadyReviewed = "ccnavi: [CORE_CONFIRM_ALREADY_REVIEWED] フェーズ 1 はレビュー済み"

func TestAReviewedPhaseIsNotConfirmedAgainLocallyOrFromChrome(t *testing.T) {
	t.Parallel()
	tr, f := actorTree(t)
	compare := tr.hostCompare()
	OK(t, tr.confirmAs(f))
	before := Read(t, tr.actorMarkPath("reviewed"))
	again := tr.confirmAs(f, "--actor", "someone")
	wantCode(t, again, 1)
	if first := Lines(again.Stderr)[0]; first != alreadyReviewed {
		t.Errorf("先頭の行 = %q", first)
	}
	if Read(t, tr.actorMarkPath("reviewed")) != before {
		t.Error("マーカーが書き換わった")
	}
	tr.CommitParent("reviewed")
	compare["head"] = strings.TrimSpace(tr.Parent("rev-parse", "HEAD"))
	chrome := tr.chromeConfirm(compare, nil, "")
	if p := strs(jl(chrome, "problems")); !slices.Equal(p, []string{alreadyReviewed}) || !isNull(chrome, "changes") {
		t.Errorf("chrome = %v", chrome)
	}
}

// requestPosted は依頼を投稿した形（作成者つき）まで進め、before を挟んで requested を打つ。
func requestPosted(t *testing.T, before func(tr *Tree), extra func(tr *Tree) []string) (*Tree, run.Result) {
	t.Helper()
	tr := chromeTree(t)
	tr.FamilyPlan(planOf("design"), nil)
	tr.ApproveChild("i0001-01-01", 1, "wip/design/*")
	tr.RunChild("i0001-01-01", File{"wip/design/plan.md", "d\n"})
	OK(t, tr.CloseChild("i0001-01-01"))
	tr.CommitParent("close 01")
	tr.Merge("i0001-01-01")
	tr.Origin()
	body := Write(t, filepath.Join(tr.Root, "body.md"), "見てほしい\n")
	OK(t, tr.Ccnavi("--cwd", tr.ParentTree, "--phase", "1", "--body-file", body, "review", "prepare"))
	posted := Write(t, filepath.Join(tr.Root, "posted.json"), `{"host": "gitlab", "mr": {"number": 7, "url": "u/7"}, "url": "u/7#1", "created_at": "t", "author": "ccnavi-bot"}`)
	if before != nil {
		before(tr)
	}
	var more []string
	if extra != nil {
		more = extra(tr)
	}
	return tr, tr.Ccnavi(append(append([]string{}, more...), "--cwd", tr.ParentTree, "--phase", "1", "review", "requested", "--result", posted)...)
}

func (tr *Tree) doingText() string { return Read(tr.T, tr.ApprovedPath("doing", "i0001")) }

func (tr *Tree) setDoingMR(value string) {
	tr.T.Helper()
	replacement := "\n"
	if value != "" {
		replacement = "\nmr: " + value + "\n"
	}
	Write(tr.T, tr.ApprovedPath("doing", "i0001"), strings.Replace(tr.doingText(), "\nmr: 1\n", replacement, 1))
	tr.Parent("add", "-A")
	tr.Parent("commit", "--quiet", "-m", "mr")
}

func TestTheRequestRecordsThePosterWhenKnown(t *testing.T) {
	t.Parallel()
	tr, done := requestPosted(t, nil, nil)
	wantCode(t, done, 0)
	if js(ReadJSON(t, tr.actorMarkPath("requested")), "poster") != "ccnavi-bot" {
		t.Errorf("requested = %v", ReadJSON(t, tr.actorMarkPath("requested")))
	}
}

// 承認済みチケットの mr: が空なら、依頼で MR の番号を書き足す。
func TestTheRequestWritesMRIntoAnEmptyFieldOnly(t *testing.T) {
	t.Parallel()
	tr, done := requestPosted(t, func(tr *Tree) {
		tr.setDoingMR("")
		Lacks(t, tr.doingText(), "mr: 1")
	}, nil)
	wantCode(t, done, 0)
	Has(t, tr.doingText(), "mr: 7\n")
	Has(t, done.Stderr, "`mr:` に MR !7 を追記しました")
	if strings.Count(tr.doingText(), "mr: 7") != 1 {
		t.Error("mr: 7 が 1 行でない")
	}
}

// write-and-push は書いたパスの一覧のパスだけをコミットする。mr: の書き足しも一覧に入る。
func TestTheMRWriteIsInTheWriteAndPushWriteList(t *testing.T) {
	t.Parallel()
	var listing string
	_, done := requestPosted(t, func(tr *Tree) { tr.setDoingMR("") }, func(tr *Tree) []string {
		listing = filepath.Join(tr.Root, "logs", "state", "write-and-push", "demo", "writes.txt")
		if err := os.MkdirAll(filepath.Dir(listing), 0o755); err != nil {
			t.Fatal(err)
		}
		return []string{"--record-writes", listing}
	})
	wantCode(t, done, 0)
	rel := filepath.ToSlash(filepath.Join(".ccnavi", "approved", "doing", "i0001.md"))
	found := false
	for _, l := range Lines(Read(t, listing)) {
		found = found || strings.HasSuffix(filepath.ToSlash(filepath.Clean(strings.TrimSpace(l))), rel)
	}
	if !found {
		t.Errorf("書いたパスの一覧に %s が無い: %s", rel, Read(t, listing))
	}
}

func TestTheRequestWithTheSameMROrAnotherMR(t *testing.T) {
	cases := []struct {
		name, mr string
		warns    bool
	}{
		{"the same mr does nothing", "7", false},
		// 依頼そのものは通る（マーカーは置けている）。値は上書きしない。
		{"another mr warns and keeps the value", "9", true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			var held string
			tr, done := requestPosted(t, func(tr *Tree) { tr.setDoingMR(c.mr); held = tr.doingText() }, nil)
			wantCode(t, done, 0)
			if held != tr.doingText() {
				t.Error("承認済みチケットが書き換わった")
			}
			if c.warns {
				Has(t, done.Stderr, "警告")
				Has(t, done.Stderr, "別の値が既にある")
				Has(t, done.Stderr, "!7")
			} else {
				Lacks(t, done.Stderr, "追記しました")
				Lacks(t, done.Stderr, "警告")
			}
		})
	}
}

// ---- decide のマーカーにも actor と via（confirm と同じ形）。

func (tr *Tree) decideWith(f Fixture, extra ...string) run.Result {
	tr.T.Helper()
	base := []string{"--cwd", tr.ParentTree, "user", "reviewed", "1", "--accept-unresolved"}
	preview := OK(tr.T, tr.Ccnavi(append(append([]string{}, base...), "--preview", "--json", "--result", f.Path)...))
	digest := js(ParseJSON(tr.T, preview.Stdout), "digest")
	args := append(append([]string{}, base...), "--yes", "{}", "--digest", digest, "--json", "--result", f.Path)
	return tr.Ccnavi(append(args, extra...)...)
}

func TestWithoutAnActorTheDecideMarkIsAsBefore(t *testing.T) {
	t.Parallel()
	tr, f := actorTree(t)
	OK(t, tr.decideWith(f))
	if got := sorted(keysOf(t, Read(t, tr.actorMarkPath("reviewed")))); !slices.Equal(got, []string{"accepted", "at", "mr"}) {
		t.Errorf("keys = %v", got)
	}
}

func TestAnActorAndTheWayGoIntoTheDecideMark(t *testing.T) {
	for _, via := range []string{"board", "terminal"} {
		t.Run(via, func(t *testing.T) {
			t.Parallel()
			tr, f := actorTree(t)
			OK(t, tr.decideWith(f, "--actor=octo-reviewer", "--via="+via))
			text := Read(t, tr.actorMarkPath("reviewed"))
			mark := ParseJSON(t, text)
			if !slices.Equal(keysOf(t, text), []string{"mr", "accepted", "actor", "via", "at"}) || js(mark, "actor") != "octo-reviewer" || js(mark, "via") != via {
				t.Errorf("mark = %s", text)
			}
		})
	}
}

func TestTheWayIsRefusedAloneOrWithTheWrongWord(t *testing.T) {
	t.Parallel()
	tr, f := actorTree(t)
	for _, c := range []struct {
		extra []string
		word  string
	}{
		{[]string{"--via=board"}, "--via は decide の --actor"},
		{[]string{"--actor=octo", "--via=chrome"}, "--via に渡せるのは terminal か board"},
	} {
		Has(t, Fails(t, tr.decideWith(f, c.extra...)).Stderr, c.word)
		if Exists(tr.actorMarkPath("reviewed")) {
			t.Errorf("%v でマーカーが書かれた", c.extra)
		}
	}
	preview := Fails(t, tr.Ccnavi("--cwd", tr.ParentTree, "user", "reviewed", "1", "--accept-unresolved", "--preview", "--json", "--result", f.Path, "--actor=octo"))
	Has(t, preview.Stderr, "--actor")
}

// ターミナルで 1 件ずつ選ぶ形（--yes 無し）に --via board は受けない。
func TestBoardIsRefusedForTheWayThatPicksOneByOne(t *testing.T) {
	t.Parallel()
	tr, f := actorTree(t)
	done := Fails(t, tr.Ccnavi("--cwd", tr.ParentTree, "user", "reviewed", "1", "--accept-unresolved", "--result", f.Path, "--actor=octo", "--via=board"))
	Has(t, done.Stderr, "--via board は --yes")
	if Exists(tr.actorMarkPath("reviewed")) {
		t.Error("マーカーが書かれた")
	}
}
