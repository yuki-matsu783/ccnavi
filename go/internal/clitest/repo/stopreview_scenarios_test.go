package repo

import (
	"fmt"
	"maps"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

// StopのAI判定の受入シナリオ 13 件を、場面データから決定的に確かめる。
//
// 場面データは tests/fixtures/stop_review/scenarios/ にある。実機のモデルは呼ばない。各場面について、場面データから
// transcript と提案を組み、hook（実行ファイルを hook として起こす）と `ccnavi stop-review material`・`record` を通して、次を確かめる。
//
//   - 候補になるか、計画の対象と必要な基準が期待と合うか
//   - 判定役に渡る材料に、must_contain の文字列がすべて含まれるか。これで判定役が根拠を受け取れる
//   - record に判定結果を渡したときの、返り値・試行の状態・最終分類・再判定の結果。判定結果は場面データの
//     judge_result*.json、無ければ expected.json の基準ごとの期待から組んだもの
//
// 検査しないもの: 判定役が正しく判定すること。基準ごとの期待は、正しい判定役が出すはずの結果として record に渡す入力であり、
// ここで確かめるのは ccnavi 側の確定の規則だけ。計画と試行は、state の置き場（logs/state/stopreview-plan-*.json・
// stopreview-attempt-*.json）から読む。
//
// 場面の置き方。1 回の Stop の場面は場面の直下、2 回の Stop は stop-1/・stop-2/ に分ける。
//   - transcript.jsonl: その Stop の時点の会話
//   - proposals/<識別子>.txt: Write で書いた提案（wip/proposals/todo/<識別子>.md に置く）
//   - propose.kv: approval-request の結果ファイル。{now}・{commit}・{blob:<パス>} を置き換える。あれば proposals/ の提案を
//     コミットして、送った版にする
//   - judge_result*.json: 判定役の結果を模したもの。$ATTEMPT・$PROPOSAL:<識別子> を置き換える
//   - settings.json（場面の直下）: stop_review の値。無ければ enable
//   - expected.json（場面の直下）: 期待

const (
	sceneCount = 13
	// 基準の結果の「不備あり」。
	failResult = "fail"
	// 対象の名前（最後の回答）。提案の対象は「proposal:<識別子>」。
	stopTarget = "stop"
	// 試行の状態（差し戻し済み）。
	sentBack = "sent_back"
	// 判定結果の分類。
	classTicketWork = "ticket_work"
	classNotTicket  = "not_ticket_work"
	// 最終分類（チケット作業の外）。
	finalOutOfScope = "out_of_scope"
)

// 不備の基準（R1・R2・R4）が直す手順。
var fixes = map[string]string{"R1": "write_proposal", "R2": "revise_proposal", "R4": "send_proposal"}

// flawCriteria は不備の基準。fixes を書くのはこの基準だけ。
var flawCriteria = map[string]bool{"R1": true, "R2": true, "R4": true}

var finals = map[string]bool{"ok": true, "flawed": true, "unchecked": true, "out_of_scope": true}

// scene は場面の expected.json。
type scene struct {
	ID      string       `json:"id"`
	Title   string       `json:"title"`
	Final   string       `json:"final"`
	Judge   string       `json:"judge"`
	Rejudge *string      `json:"rejudge"`
	Stops   []stopExpect `json:"stops"`
}

// Outcome は record の後の結果の期待（Stop の期待と、別の判定結果の期待で共有する）。
type Outcome struct {
	ReplyOK       bool     `json:"reply_ok"`
	ReplyContains []string `json:"reply_contains"`
	State         string   `json:"state"`
	Final         string   `json:"final"`
	Judge         string   `json:"judge"`
	RejudgeResult *string  `json:"rejudge_result"`
}

// stopExpect は 1 回の Stop の期待。
type stopExpect struct {
	Outcome
	Candidate     bool                         `json:"candidate"`
	Effective     string                       `json:"effective"`
	Refs          []string                     `json:"refs"`
	Targets       []string                     `json:"targets"`
	Required      [][2]string                  `json:"required"`
	Criteria      map[string]map[string]string `json:"criteria"`
	MustContain   []string                     `json:"must_contain"`
	Routes        map[string]string            `json:"routes"`
	IsRejudge     bool                         `json:"is_rejudge"`
	OriginalState string                       `json:"original_state"`
	Alternatives  []alternative                `json:"alternatives"`
}

// alternative は同じ Stop に、別の判定結果を当てたときの期待。
type alternative struct {
	Name        string `json:"name"`
	JudgeResult string `json:"judge_result"`
	Outcome
}

// record は 1 回の Stop の再生の結果。
type record struct {
	play     *play
	plan     *planState
	material string
	reply    reply
	want     stopExpect
	attempt  attemptState
	original *attemptState // 再判定なら、record の直後の元の試行
}

// override は、番号 index の Stop で判定結果を file（場面の判定結果のファイル名）に差し替えるための指定。
type override struct {
	index int
	file  string
}

// playScene は場面を Stop の順に再生する。o があれば、その Stop で判定結果を差し替えて止める。
func playScene(t *testing.T, name string, o *override) []record {
	t.Helper()
	p := newPlay(t, name)
	var records []record
	for index, folder := range p.stops {
		want := p.expected.Stops[index]
		p.stage(folder)
		plan := p.plan(index > 0)
		if p.errors != "" {
			t.Fatalf("%s の Stop %d: hook の標準エラー: %s", name, index+1, p.errors)
		}
		if plan == nil {
			t.Fatalf("%s の Stop %d: 計画が書かれていない", name, index+1)
		}
		material := p.material()
		choice := ""
		if o != nil && o.index == index {
			choice = o.file
		}
		reply := p.record(judgeInput(t, p, folder, plan, want, choice))
		var original *attemptState
		if plan.Rejudge {
			original = readState[attemptState](t, p.state, "stopreview-attempt-"+plan.Original+".json")
		}
		records = append(records, record{
			play: p, plan: plan, material: material, reply: reply, want: want,
			attempt: p.attempt(plan), original: original,
		})
		if choice != "" {
			break
		}
	}
	return records
}

// judgeInput は record に渡す判定結果。choice（別の判定結果のファイル名）か、場面の judge_result.json があればそれ、
// 無ければ期待の基準から組む。
func judgeInput(t *testing.T, p *play, folder string, plan *planState, want stopExpect, choice string) string {
	t.Helper()
	if choice != "" {
		path := filepath.Join(folder, choice)
		if !fileExists(path) {
			t.Fatalf("%s が無い", choice)
		}
		return p.judgeText(plan, readText(t, path))
	}
	path := filepath.Join(folder, "judge_result.json")
	if fileExists(path) {
		return p.judgeText(plan, readText(t, path))
	}
	return idealResult(plan, want.Criteria)
}

// requiredPairs は計画から決まる、結果が要る (基準, 対象) の一覧。本文が無い版（送れなかった提案）には R2 を求めない。
func requiredPairs(plan *planState) [][2]string {
	needs := [][2]string{{"R1", stopTarget}, {"R3", stopTarget}}
	noR2 := map[string]bool{}
	for _, k := range plan.NoR2 {
		noR2[k] = true
	}
	for _, target := range plan.Targets {
		if target == stopTarget {
			continue
		}
		if noR2[target] {
			needs = append(needs, [2]string{"R4", target})
		} else {
			needs = append(needs, [2]string{"R2", target}, [2]string{"R4", target})
		}
	}
	return needs
}

// notation は対象キーを、期待の書き方（stop・proposal:<識別子>）にする。
func notation(plan *planState) func(string) string {
	names := map[string]string{}
	for _, p := range plan.Proposals {
		names[p.Key] = "proposal:" + p.Ident
	}
	return func(key string) string {
		if n, ok := names[key]; ok {
			return n
		}
		return key
	}
}

// checkPlan は計画と材料が、場面の期待と合うかを見る。
func checkPlan(t *testing.T, rec record, label string) {
	t.Helper()
	plan, want := rec.plan, rec.want
	nick := notation(plan)
	if plan.IsCandidate != want.Candidate {
		t.Errorf("%s: is_candidate = %v, want %v", label, plan.IsCandidate, want.Candidate)
	}
	if plan.Effective != want.Effective {
		t.Errorf("%s: effective = %q, want %q", label, plan.Effective, want.Effective)
	}
	targets := make([]string, 0, len(plan.Targets))
	for _, k := range plan.Targets {
		targets = append(targets, nick(k))
	}
	if !slices.Equal(targets, want.Targets) {
		t.Errorf("%s: targets = %v, want %v", label, targets, want.Targets)
	}
	if !slices.Equal(plan.Refs, want.Refs) {
		t.Errorf("%s: refs = %v, want %v", label, plan.Refs, want.Refs)
	}
	routes := map[string]string{}
	for _, p := range plan.Proposals {
		routes[p.Ident] = p.Route
	}
	if !maps.Equal(routes, want.Routes) {
		t.Errorf("%s: routes = %v, want %v", label, routes, want.Routes)
	}
	var needs [][2]string
	for _, pair := range requiredPairs(plan) {
		needs = append(needs, [2]string{pair[0], nick(pair[1])})
	}
	if !samePairs(needs, want.Required) {
		t.Errorf("%s: required = %v, want %v", label, needs, want.Required)
	}
	if plan.Rejudge != want.IsRejudge {
		t.Errorf("%s: rejudge = %v, want %v", label, plan.Rejudge, want.IsRejudge)
	}
	if plan.Rejudge && rec.original != nil && rec.original.State != want.OriginalState {
		t.Errorf("%s: 元の試行の state = %q, want %q", label, rec.original.State, want.OriginalState)
	}
	for _, needle := range want.MustContain {
		if !strings.Contains(rec.material, needle) {
			t.Errorf("%s: 材料に「%s」が無い", label, needle)
		}
	}
}

// samePairs は二つの組の並びが、順を問わず同じかを見る。
func samePairs(a, b [][2]string) bool {
	key := func(p [2]string) string { return p[0] + "\x00" + p[1] }
	x := make([]string, 0, len(a))
	y := make([]string, 0, len(b))
	for _, p := range a {
		x = append(x, key(p))
	}
	for _, p := range b {
		y = append(y, key(p))
	}
	slices.Sort(x)
	slices.Sort(y)
	return slices.Equal(x, y)
}

// checkOutcome は record の返り値・試行の状態・最終分類・再判定の結果が、期待と合うかを見る。
func checkOutcome(t *testing.T, rec record, want Outcome, label string) {
	t.Helper()
	if rec.reply.OK != want.ReplyOK {
		t.Errorf("%s: reply.ok = %v, want %v", label, rec.reply.OK, want.ReplyOK)
	}
	if rec.attempt.State != want.State {
		t.Errorf("%s: 試行の state = %q, want %q", label, rec.attempt.State, want.State)
	}
	if rec.attempt.Final != want.Final || rec.attempt.Judge != want.Judge {
		t.Errorf("%s: (final, judge) = (%q, %q), want (%q, %q)", label, rec.attempt.Final, rec.attempt.Judge, want.Final, want.Judge)
	}
	if !rec.reply.OK {
		if !strings.Contains(rec.reply.Reason, rec.plan.AttemptID) {
			t.Errorf("%s: 理由に試行 ID が無い: %s", label, rec.reply.Reason)
		}
		for _, needle := range want.ReplyContains {
			if !strings.Contains(rec.reply.Reason, needle) {
				t.Errorf("%s: 理由に「%s」が無い: %s", label, needle, rec.reply.Reason)
			}
		}
	}
	if want.RejudgeResult != nil {
		if rec.original == nil {
			t.Errorf("%s: 再判定の元の試行が無い", label)
		} else if rec.original.Rejudge != *want.RejudgeResult {
			t.Errorf("%s: 再判定の結果 = %q, want %q", label, rec.original.Rejudge, *want.RejudgeResult)
		}
	}
}

// checkScene は 1 場面を通して確かめる。最後に、別の判定結果（alternatives）を当てた場合も確かめる。
func checkScene(t *testing.T, name string) {
	t.Helper()
	records := playScene(t, name, nil)
	for index, rec := range records {
		label := fmt.Sprintf("%s の Stop %d", name, index+1)
		checkPlan(t, rec, label)
		checkOutcome(t, rec, rec.want.Outcome, label)
	}
	expected := newScene(t, name)
	last := records[len(records)-1]
	if last.want.Final != expected.Final || last.want.Judge != expected.Judge {
		t.Errorf("%s: 最後の (final, judge) = (%q, %q), want (%q, %q)", name, last.want.Final, last.want.Judge, expected.Final, expected.Judge)
	}
	if expected.Rejudge != nil {
		if last.want.RejudgeResult == nil || *last.want.RejudgeResult != *expected.Rejudge {
			t.Errorf("%s: 最後の再判定の結果が %v, want %q", name, last.want.RejudgeResult, *expected.Rejudge)
		}
	}
	for _, alt := range last.want.Alternatives {
		label := name + ": " + alt.Name
		rec := playScene(t, name, &override{index: len(records) - 1, file: alt.JudgeResult})
		got := rec[len(rec)-1]
		checkOutcome(t, got, alt.Outcome, label)
		// 再判定や dry-run の不備検出では差し戻しが残らない。次の Stop で消費されるものが無いため。
		if sentBackLeft(t, got.play.state) {
			t.Errorf("%s: 差し戻しが残っている", label)
		}
	}
}

// newScene は場面の expected.json を読む（checkScene の終わりの照合に使う）。
func newScene(t *testing.T, name string) scene {
	t.Helper()
	return loadJSON[scene](t, filepath.Join(sceneDir(t), name, "expected.json"))
}

// sceneNames は場面の名前（場面データの置き場の直下のディレクトリ）を並べて返す。
func sceneNames(t *testing.T) []string {
	t.Helper()
	var names []string
	for _, n := range listNames(t, sceneDir(t)) {
		if dirExists(filepath.Join(sceneDir(t), n)) {
			names = append(names, n)
		}
	}
	slices.Sort(names)
	return names
}

// TestStopReviewScenes は 13 場面を、場面ごとの部分試験として再生する。git が無い機械では飛ばす。
func TestStopReviewScenes(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git が無い")
	}
	for _, name := range sceneNames(t) {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			checkScene(t, name)
		})
	}
}

// TestSceneDataIsPresentAndWellFormed は 13 場面がそろい、期待の書き方が決まりどおりかを見る。
func TestSceneDataIsPresentAndWellFormed(t *testing.T) {
	t.Parallel()
	names := sceneNames(t)
	var numbers []string
	for _, n := range names {
		numbers = append(numbers, n[:2])
	}
	var want []string
	for i := 1; i <= sceneCount; i++ {
		want = append(want, pad2(i))
	}
	if !slices.Equal(numbers, want) {
		t.Fatalf("場面の番号 = %v, want %v", numbers, want)
	}
	for _, name := range names {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			e := newScene(t, name)
			if e.ID != name[:2] {
				t.Errorf("id = %q, want %q", e.ID, name[:2])
			}
			if strings.TrimSpace(e.Title) == "" || strings.Contains(e.Title, "\n") {
				t.Errorf("title = %q", e.Title)
			}
			if !finals[e.Final] {
				t.Errorf("final = %q", e.Final)
			}
			if folders := stopFolders(t, filepath.Join(sceneDir(t), name)); len(e.Stops) != len(folders) {
				t.Errorf("stops = %d, 場面の Stop = %d", len(e.Stops), len(folders))
			}
			for _, stop := range e.Stops {
				if len(stop.MustContain) == 0 {
					t.Errorf("must_contain が空")
				}
				for _, s := range stop.MustContain {
					if s == "" {
						t.Errorf("must_contain に空の文字列")
					}
				}
			}
		})
	}
}

// TestTheJudgeResultFilesAgreeWithTheExpectedCriteria は、場面の判定結果が expected.json の基準ごとの期待と食い違わないかを見る。
func TestTheJudgeResultFilesAgreeWithTheExpectedCriteria(t *testing.T) {
	t.Parallel()
	for _, name := range sceneNames(t) {
		e := newScene(t, name)
		folders := stopFolders(t, filepath.Join(sceneDir(t), name))
		for i, folder := range folders {
			path := filepath.Join(folder, "judge_result.json")
			if !fileExists(path) {
				continue
			}
			want := e.Stops[i]
			t.Run(name+"/"+filepath.Base(folder), func(t *testing.T) {
				t.Parallel()
				result := loadJSON[judgeFile](t, path)
				if result.Classification == classNotTicket {
					if want.Final != finalOutOfScope {
						t.Errorf("out_of_scope のはず（final = %q）", want.Final)
					}
					return
				}
				got := map[string]map[string]string{}
				for _, row := range result.Results {
					target := strings.ReplaceAll(row.Target, "$PROPOSAL:", "proposal:")
					if got[row.Criterion] == nil {
						got[row.Criterion] = map[string]string{}
					}
					got[row.Criterion][target] = row.Result
				}
				if !maps.EqualFunc(got, want.Criteria, maps.Equal[map[string]string, map[string]string]) {
					t.Errorf("判定結果の基準 = %v, want %v", got, want.Criteria)
				}
			})
		}
	}
}

// judgeFile は場面の判定結果（judge_result*.json）の、照合に使う欄。
type judgeFile struct {
	Classification string `json:"classification"`
	Results        []struct {
		Criterion string `json:"criterion"`
		Target    string `json:"target"`
		Result    string `json:"result"`
	} `json:"results"`
}

// pad2 は 2 桁の番号（01 など）。
func pad2(n int) string { return fmt.Sprintf("%02d", n) }
