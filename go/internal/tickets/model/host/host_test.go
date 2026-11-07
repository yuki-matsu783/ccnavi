package host

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/pyjson"
)

var repoRoot = filepath.Join("..", "..", "..", "..", "..")

func readJSON(t *testing.T, parts ...string) any {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(append([]string{repoRoot}, parts...)...))
	if err != nil {
		t.Fatal(err)
	}
	data, err := pyjson.Loads(string(raw))
	if err != nil {
		t.Fatal(err)
	}
	return data
}

// sh・Chrome 拡張と同じ表で、分類と接続先を突き合わせる。
func TestHostKindsTable(t *testing.T) {
	table := readJSON(t, "tests", "fixtures", "host-kinds.json").(*pyjson.Object)
	cases, _ := table.Value("cases").([]any)
	if len(cases) == 0 {
		t.Fatal("表が空")
	}
	for _, raw := range cases {
		c := raw.(*pyjson.Object)
		origin := c.String("origin")
		got := Classify(origin)
		if string(got.Kind) != c.String("kind") || got.Host != c.String("host") || got.Path != c.String("path") {
			t.Errorf("%s: %+v", origin, got)
		}
		if problem, has := c.Get("problem"); has {
			if !strings.Contains(got.Problem, problem.(string)) || got.API != "" || got.GraphQL != "" {
				t.Errorf("%s: problem %+v", origin, got)
			}
		} else if got.Problem != "" || got.API != c.String("api") || got.GraphQL != c.String("graphql") {
			t.Errorf("%s: 接続先 %+v", origin, got)
		}
	}
}

func TestClassifyEdges(t *testing.T) {
	for _, host := range []string{"evilgithub.com", "github.com.evil", "evilghe.com", "ghe.com.evil"} {
		if got := Classify("https://" + host + "/a/b.git").Kind; got != GitLab {
			t.Errorf("%s: %s（区切りで照合する）", host, got)
		}
	}
	for _, url := range []string{"ssh://user@host.example.com/g/p.git", "not a url"} {
		if got := Classify(url).Kind; got != "" {
			t.Errorf("%s: %s（読めない表記）", url, got)
		}
	}
	// 全角の数字はポートにしない
	if got := Classify("git@x.ghe.com:٣:/a/b.git"); got.Problem != "" {
		t.Errorf("非 ASCII の数字: %+v", got)
	}
	// Python の `$` と同じく、末尾の改行の前で当たる
	if got := Classify("https://github.com/acme/widgets.git\n"); got.Kind != GitHub || got.Path != "acme/widgets" {
		t.Errorf("末尾の改行: %+v", got)
	}
}

func TestTransportProblem(t *testing.T) {
	if got := TransportProblem("https://nri.ghe.com:8443/acme/widgets.git"); !strings.Contains(got, "ポート") || !strings.Contains(got, "API の URL") {
		t.Errorf("GHE.com のポート: %q", got)
	}
	got := TransportProblem("ssh://user:SECRET@host.example/a/b")
	if strings.Contains(got, "SECRET") || !strings.Contains(got, "<伏せた>") {
		t.Errorf("資格情報を伏せない: %q", got)
	}
	// トークンに `@` が入っても、ホストの手前の最後の `@` まで伏せる。
	if got := TransportProblem("ssh://oauth2:SEC@RET@host.example/a/b"); !strings.Contains(got, "ssh://<伏せた>@host.example/a/b") {
		t.Errorf("@ を含む資格情報: %q", got)
	}
}

func TestChangesRequestedNoteByHost(t *testing.T) {
	gitlab := ChangesRequestedNote(GitLab)
	if !strings.Contains(gitlab, "解除はレビュアーの承認（Approve）で行います") || strings.Contains(gitlab, "dismiss") {
		t.Errorf("GitLab: %q", gitlab)
	}
	for _, h := range []Kind{GitHub, "fixture", ""} {
		if got := ChangesRequestedNote(h); got != changesGitHub {
			t.Errorf("%q: %q", h, got)
		}
	}
}

func TestEffectiveKeepsTheLatestDecisionPerReviewer(t *testing.T) {
	reviews := []Review{
		{State: "CHANGES_REQUESTED", SubmittedAt: "2026-01-01T00:00:00Z", Author: "a"},
		{State: "COMMENTED", SubmittedAt: "2026-01-02T00:00:00Z", Author: "a"}, // 判断を変えない
		{State: "approved", SubmittedAt: "2026-01-01T00:00:00Z", Author: "b"},
		{State: "DISMISSED", SubmittedAt: "2026-01-03T00:00:00Z", Author: "b"},
		{State: "APPROVED", SubmittedAt: "2026-01-01T00:00:00Z"},
		{State: "APPROVED", SubmittedAt: "2026-01-01T00:00:00Z"},
	}
	got := Effective(reviews)
	if len(got) != 3 || got[0].Author != "a" || got[0].State != ChangesRequested {
		t.Errorf("Effective: %+v", got)
	}
}

func TestEpoch(t *testing.T) {
	if got := Epoch("2026-01-01T00:00:00Z"); got != 1767225600 {
		t.Errorf("Z: %v", got)
	}
	if got := Epoch("2026-01-01T09:00:00+09:00"); got != 1767225600 {
		t.Errorf("時差: %v", got)
	}
	for _, bad := range []string{"", "yesterday"} {
		if Epoch(bad) != 0 {
			t.Errorf("%q が読めた", bad)
		}
	}
}

func TestAcceptPlanRoundTrip(t *testing.T) {
	path := filepath.Join(t.TempDir(), "plan.json")
	items := []AcceptItem{{Thread{ID: "PRRT_kw1", URL: "u#1"}, AcceptReply("期限", false, "")}, {Thread{ID: "ab12"}, "r"}}
	if failed := WriteAcceptPlan(path, items, "d", "op1"); failed != "" {
		t.Fatal(failed)
	}
	raw, _ := os.ReadFile(path)
	want := `{"version": 1, "digest": "d", "op": "op1", "threads": [{"id": "PRRT_kw1", "key": "u#1", "reply": "<!-- ccnavi:accept -->\nccnavi: 期限ので、この指摘は対応せず、受け入れて進める。\nこのスレッドは解決済みにする。\n"}, {"id": "ab12", "key": "ab12", "reply": "r"}]}` + "\n"
	if string(raw) != want {
		t.Errorf("計画の形:\n%s", raw)
	}
	plan := ReadAcceptPlan(path)
	if plan == nil || plan.Digest != "d" || plan.Op != "op1" || !slices.Equal(plan.IDs, []string{"PRRT_kw1", "ab12"}) || !slices.Equal(plan.Keys, []string{"u#1", "ab12"}) {
		t.Errorf("読み戻し: %+v", plan)
	}
	if ReadAcceptPlan(filepath.Join(t.TempDir(), "none.json")) != nil {
		t.Error("無い計画が読めた")
	}
	if got := AcceptPlanProblem([]Thread{{ID: "ok"}, {ID: "a/b", URL: "u"}}); got != "スレッドの id が無いか読めず、返信と解決ができない（u）" {
		t.Errorf("AcceptPlanProblem: %q", got)
	}
	if got := AcceptReply("x", true, "https://i/1"); !strings.Contains(got, "別の issue に回した: https://i/1。") {
		t.Errorf("AcceptReply: %q", got)
	}
}

func TestLoadResultErrors(t *testing.T) {
	dir := t.TempDir()
	broken := filepath.Join(dir, "broken.json")
	os.WriteFile(broken, []byte("{"), 0o644)
	if got := LoadResult(broken).Error; !strings.HasPrefix(got, "結果が JSON として読めない (") {
		t.Errorf("壊れた JSON: %q", got)
	}
	if got := LoadResult(filepath.Join(dir, "none.json")).Error; !strings.HasPrefix(got, "結果を読めない ([Errno 2]") {
		t.Errorf("無いファイル: %q", got)
	}
	if got := ResultOf([]any{}).Error; got != msgNotMap {
		t.Errorf("マップでない: %q", got)
	}
	if got := ResultOf(pyjson.NewObject("error", "boom")).Error; got != "boom" {
		t.Errorf("error の欄: %q", got)
	}
	r := ResultOf(pyjson.NewObject("host", "gitlab", "mr", pyjson.NewObject("number", "7", "draft", "yes")))
	if r.MR == nil || r.MR.Number != 7 || r.MR.Draft != nil {
		t.Errorf("mr: %+v", r.MR)
	}
}

// ---- ccnavi の依頼コメントへの返信を指摘として数える（GitLab）。見本は Chrome 拡張の試験と同じ。

const poster = "201"

func scene(t *testing.T, name string) *pyjson.Object {
	t.Helper()
	return readJSON(t, "extensions", "chrome", "ccnavi-approval", "test", "fixtures", "host", "gitlab", name, "expected.json").(*pyjson.Object)
}

func byPrefix(r Result) map[string]Thread {
	out := map[string]Thread{}
	for _, th := range r.Threads {
		out[th.ID[:2]] = th
	}
	return out
}

func openIDs(r Result, ledger []*pyjson.Object) []string {
	var out []string
	for _, th := range Unresolved(r.Threads, r.Host, poster, ledger) {
		out = append(out, th.ID[:2])
	}
	slices.Sort(out)
	return out
}

func TestRepliesToTheRequestAreCounted(t *testing.T) {
	result := ResultOf(scene(t, "replies"))
	threads := byPrefix(result)
	if a := threads["a0"]; a.Body != "この方針で良いか確認したい" || !strings.HasSuffix(a.URL, "#note_5302") || a.Own == nil || *a.Own || a.Author != "202" {
		t.Errorf("a0: %+v", a)
	}
	if b := threads["b0"]; b.Body != "全体の感想です" || b.LastCommentID != "5304" {
		t.Errorf("b0: %+v", b)
	}
	for _, gone := range []string{"c0", "i0"} {
		if _, ok := threads[gone]; ok {
			t.Errorf("%s が取られた", gone)
		}
	}
	if d := threads["d0"]; d.Own == nil || !*d.Own || !ccnaviPost(d, GitLab, poster) {
		t.Errorf("d0: %+v", d)
	}
	for name, id := range map[string]string{"h3": "5317", "h2": "5315", "h1": "5311"} {
		if got := threads[name].LastCommentID; got != id {
			t.Errorf("%s の最後のコメント: %s", name, got)
		}
	}
	want := []string{"a0", "b0", "e0", "h1", "h2", "h3"}
	if got := openIDs(result, nil); !slices.Equal(got, want) {
		t.Errorf("未解決: %v", got)
	}
	// own が真偽でなければ目印と書き手で見る
	copied := scene(t, "replies")
	for _, raw := range copied.Value("threads").([]any) {
		item := raw.(*pyjson.Object)
		if id := item.String("id"); strings.HasPrefix(id, "d0") || strings.HasPrefix(id, "e0") {
			item.Set("own", "true")
		}
	}
	if got := openIDs(ResultOf(copied), nil); !slices.Equal(got, want) {
		t.Errorf("own が文字列のとき: %v", got)
	}
	// 受け入れの返信を足してもキーと版は変わらず、解決済みなので数えない
	accepted := ResultOf(scene(t, "replies-accepted"))
	after := byPrefix(accepted)
	for name, before := range threads {
		if ThreadKey(after[name]) != ThreadKey(before) || ThreadVersion(after[name]) != ThreadVersion(before) {
			t.Errorf("%s のキーか版が変わった", name)
		}
	}
	if got := openIDs(accepted, nil); len(got) != 0 {
		t.Errorf("受け入れの後: %v", got)
	}
}

func TestOwnTakesOnlyJSONBooleans(t *testing.T) {
	for _, v := range []any{"true", "false", 1, 0, nil, []any{}, pyjson.NewObject(), "yes"} {
		r := ResultOf(pyjson.NewObject("threads", []any{pyjson.NewObject("id", "x", "own", v)}))
		if r.Threads[0].Own != nil {
			t.Errorf("own=%v が読まれた", v)
		}
	}
	r := ResultOf(pyjson.NewObject("threads", []any{pyjson.NewObject("id", "x", "own", false)}))
	if r.Threads[0].Own == nil || *r.Threads[0].Own {
		t.Error("own=false が読めない")
	}
}

func TestCcnaviPost(t *testing.T) {
	yes, no := true, false
	marked := "<!-- ccnavi:request i0001:1 -->\n依頼"
	cases := []struct {
		host           Kind
		poster, author string
		own            *bool
		body           string
		want           bool
	}{
		{GitLab, poster, poster, &yes, marked, true},
		{GitLab, poster, poster, &no, marked, false},
		{GitLab, poster, poster, nil, marked, true},
		{GitLab, poster, poster, nil, "ふつうの指摘", false},
		{GitLab, poster, "202", nil, marked, false},
		{GitLab, poster, "202", &yes, marked, false},
		{GitLab, "", poster, &yes, marked, false},
		{GitHub, poster, poster, &yes, marked, false},
	}
	for i, c := range cases {
		if got := ccnaviPost(Thread{ID: "x", Author: c.author, Own: c.own, Body: c.body}, c.host, c.poster); got != c.want {
			t.Errorf("%d: %v", i, got)
		}
	}
}

// 回帰: system note を最後のコメントに数えていた間は、受け入れた後に system note が付くだけで再提起になった。
func TestLedgerComparesTheLastNonSystemComment(t *testing.T) {
	result := ResultOf(scene(t, "replies"))
	h3 := byPrefix(result)["h3"]
	entry := pyjson.NewObject("key", ThreadKey(h3), "version", ThreadVersion(h3), "last_comment_id", "5317")
	if !acceptedByLedger(h3, []*pyjson.Object{entry}) {
		t.Error("受け入れ済みにならない")
	}
	stale := entry.With("last_comment_id", "5318")
	if acceptedByLedger(h3, []*pyjson.Object{stale}) || !slices.Contains(openIDs(result, []*pyjson.Object{stale}), "h3") {
		t.Error("最後のコメントが違う項目で受け入れ済みになった")
	}
	// 後から付いた返信は版を変えないが、台帳の判定では再提起になる
	shown := Thread{ID: "d", URL: "m#note_10", Body: "最初の返信", LastCommentID: "11"}
	later := shown
	later.LastCommentID = "12"
	edited := shown
	edited.Body = "書き換えた"
	if ThreadVersion(shown) != ThreadVersion(later) || ThreadVersion(shown) == ThreadVersion(edited) {
		t.Error("版の入力が違う")
	}
	e := pyjson.NewObject("key", ThreadKey(shown), "version", ThreadVersion(shown), "last_comment_id", "11")
	if !acceptedByLedger(shown, []*pyjson.Object{e}) || acceptedByLedger(later, []*pyjson.Object{e}) {
		t.Error("台帳の再提起の判定が違う")
	}
}

func TestThreadVersionNormalizesNewlinesAndNFC(t *testing.T) {
	a := Thread{URL: "u", Body: "a\r\nb\rc é"}
	b := Thread{URL: "u", Body: "a\nb\nc é"}
	if ThreadVersion(a) != ThreadVersion(b) || len(ThreadVersion(a)) != versionLen {
		t.Error("正規化が違う")
	}
}
