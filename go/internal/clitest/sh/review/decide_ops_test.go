package review

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/shkit"
)

// decide の投稿を操作記録の段階に載せた形（ccnavi-user-decide.sh と ccnavi-user-lib-review.sh）。
//
// 見るのは sh の仕事。
//
//  1. 受け入れの計画が持つ操作ID で、返信・issue・コメントの段階を記録し、投稿の本文の末尾に目印
//     `<!-- ccnavi:op=<操作ID> stage=<段階ID> -->` を足す。すべて送れたら記録を消す（すべて送れる通しは decide_test.go の
//     TestDecideChoicesCreateTheIssueAndPostTheDecision が見る）
//  2. 送れなかった段階は記録に残る。決めたことは戻さず、「引き継ぎ未完了」と `--resume` を言う
//  3. `--resume` は、保存した判断から続きを送る。送り終えた段階は繰り返さない
//  4. 送ったか確定できないときは、ホストの一覧を目印とアカウントで照合する。1 件あれば送り済みとし、0 件でも、反映待ち（送ってすぐ）や
//     アカウント違いのときは自動で送り直さない
//
// 実行ファイルは代わりの sh。`ops …` だけは本物の `ccnavi ops` に渡し、受け入れの計画は実行ファイルと同じ形の操作記録で書く。

// record は残っている操作記録（無ければ nil）。多くても 1 つ。
func (d *decideSite) record() map[string]any {
	d.t.Helper()
	ids := samples.RecordIDs(d.state)
	if len(ids) > 1 {
		d.t.Fatalf("操作記録が 2 つ以上ある: %v", ids)
	}
	if len(ids) == 0 {
		return nil
	}
	return d.load(ids[0])
}

func (d *decideSite) recordPath(id string) string {
	return filepath.Join(d.state, "ops", id+".json")
}

func (d *decideSite) load(id string) map[string]any {
	return decode(d.t, shkit.Read(d.t, d.recordPath(id)))
}

func (d *decideSite) save(rec map[string]any) {
	d.t.Helper()
	data, err := json.Marshal(rec)
	if err != nil {
		d.t.Fatal(err)
	}
	shkit.Write(d.t, d.recordPath(fmt.Sprint(rec["id"])), string(data))
}

// stage は記録の段階 id の行。
func stageRow(t testing.TB, rec map[string]any, id string) map[string]any {
	t.Helper()
	for _, row := range rec["stages"].([]any) {
		if m := row.(map[string]any); m["id"] == id {
			return m
		}
	}
	t.Fatalf("段階 %s が無い: %v", id, rec["stages"])
	return nil
}

func stageStates(rec map[string]any) string {
	out := map[string]string{}
	for _, row := range rec["stages"].([]any) {
		m := row.(map[string]any)
		out[fmt.Sprint(m["id"])] = fmt.Sprint(m["state"])
	}
	return fmt.Sprint(out)
}

// age は段階の時刻を古くする（送ってすぐは、反映待ちとして確定しないため）。
func (d *decideSite) age(id, stage string) {
	d.t.Helper()
	rec := d.load(id)
	stageRow(d.t, rec, stage)["at"] = "2020-01-01T00:00:00+0000"
	d.save(rec)
}

func (d *decideSite) choose(env map[string]string) {
	d.t.Helper()
	if done := d.decide(dOpts{env: env}, "1", "--choices", `{"u1":"issue"}`, "--digest", "d0"); done.Code != 0 {
		d.t.Fatalf("code = %d\n%s%s", done.Code, done.Stdout, done.Stderr)
	}
}

func (d *decideSite) rejecting(kind string, on bool) {
	d.gl.do(func() { d.gl.reject[kind] = on })
}

// コメントだけ送れなかった判断を、--resume で続ける一連の流れ。
//
//  1. 送れなかった段階は記録に残り、答えは「引き継ぎ未完了」と --resume を言う
//  2. --resume は先に聞き、n なら何も送らない
//  3. 送ってすぐは、ホストに 0 件でも反映待ちかもしれないので確定しない
//  4. 前に送ったアカウントと今のトークンの持ち主が違えば、0 件でも確定しない
//  5. 時間が経ち、同じアカウントなら、残った段階（コメント）だけを送り、記録を消す
func TestDecideAFailedNoteIsResumedOnlyWhenItIsSafe(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	d.rejecting("note", true)
	done := d.decide(dOpts{}, "1", "--choices", `{"u1":"issue"}`, "--digest", "d0")
	answer := answerOf(t, done.Stdout)
	warning := fmt.Sprint(answer["warning"])
	if done.Code != 0 || !strings.Contains(warning, "引き継ぎ未完了") || !strings.Contains(warning, "--resume") || !strings.HasSuffix(fmt.Sprint(answer["issue_url"]), "/issues/1") {
		t.Fatalf("code = %d, 答え = %v\n%s", done.Code, answer, done.Stderr)
	}
	rec := d.record()
	if got := stageStates(rec); got != "map[issue:sent note:unknown reply:sent]" || rec["applied"].(map[string]any)["local"] != true {
		t.Fatalf("段階 = %s, applied = %v", got, rec["applied"])
	}
	id := fmt.Sprint(rec["id"])
	d.rejecting("note", false)
	issuesBefore, _ := d.gl.stored()

	stopped := d.decide(dOpts{input: "n\n"}, "--resume")
	if _, notes := d.gl.stored(); stopped.Code == 0 || len(notes) != 0 || d.record() == nil {
		t.Fatalf("n で止まっていない: code = %d, notes = %v", stopped.Code, notes)
	}

	// 送ってすぐは反映待ちかもしれない。自動では送らない
	waiting := d.decide(dOpts{input: "y\n"}, "--resume")
	if _, notes := d.gl.stored(); len(notes) != 0 || !strings.Contains(waiting.Stderr, "確定できない") || d.record() == nil {
		t.Fatalf("反映待ちで送った: notes = %v\n%s", notes, waiting.Stderr)
	}

	// 前に送ったアカウントと違えば、時間が経っても確定しない
	rec = d.load(id)
	note := stageRow(t, rec, "note")
	mine := note["account"]
	note["state"], note["account"] = "unknown", "someone-else"
	d.save(rec)
	d.age(id, "note")
	other := d.decide(dOpts{input: "y\n"}, "--resume")
	if _, notes := d.gl.stored(); len(notes) != 0 || !strings.Contains(other.Stderr, "確定できない") || d.record() == nil {
		t.Fatalf("アカウント違いで送った: notes = %v\n%s", notes, other.Stderr)
	}

	// 時間が経ち、同じアカウントなら、残った段階（コメント）だけを送る
	rec = d.load(id)
	note = stageRow(t, rec, "note")
	note["state"], note["account"] = "unknown", mine
	d.save(rec)
	d.age(id, "note")
	resumed := d.decide(dOpts{input: "y\n"}, "--resume")
	issues, notes := d.gl.stored()
	if resumed.Code != 0 || !strings.Contains(resumed.Stdout, "続きを送った") || len(issues) != len(issuesBefore) || len(notes) != 1 ||
		!strings.Contains(fmt.Sprint(notes[0]["body"]), "stage=note") || d.record() != nil {
		t.Errorf("code = %d, issue %d→%d, notes = %d\n%s%s", resumed.Code, len(issuesBefore), len(issues), len(notes), resumed.Stdout, resumed.Stderr)
	}
}

// issue への送信に失敗しても、--resume で続きを送れる（回帰。issue はいつでも選べる）。
func TestDecideResumeSendsTheIssueAfterTheIssuePostFailed(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	d.rejecting("issue", true)
	d.choose(nil)
	rec := d.record()
	if rec == nil || strings.Contains(stageStates(rec), "issue:sent") {
		t.Fatalf("送れなかった段階が記録に残っていない: %v", rec)
	}
	d.rejecting("issue", false)
	id := fmt.Sprint(rec["id"])
	d.age(id, "issue")
	resumed := d.decide(dOpts{input: "y\n"}, "--resume")
	issues, _ := d.gl.stored()
	if resumed.Code != 0 || len(issues) != 1 || !strings.Contains(fmt.Sprint(issues[0]["description"]), "stage=issue") || d.record() != nil {
		t.Errorf("code = %d, issues = %d\n%s%s", resumed.Code, len(issues), resumed.Stdout, resumed.Stderr)
	}
}

func TestDecideResumeAfterAResolveFailureDoesNotReplyTwice(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	d.gl.do(func() { d.gl.failResolve = true })
	first := d.decide(dOpts{}, "1", "--choices", `{"u1":"issue"}`, "--digest", "d0")
	if first.Code == 0 || d.gl.count("reply") != 1 {
		t.Fatalf("code = %d, 返信 = %d\n%s", first.Code, d.gl.count("reply"), first.Stdout)
	}
	d.gl.do(func() { d.gl.failResolve = false })
	done := d.decide(dOpts{input: "y\n"}, "--resume")
	if done.Code != 0 || d.gl.count("reply") != 1 || d.gl.count("resolve") != 1 { // 返信済みのスレッドに重ねない
		t.Errorf("code = %d, 書き込み = %v\n%s%s", done.Code, d.gl.kinds(), done.Stdout, done.Stderr)
	}
}

func TestDecideResumeWithoutAPendingDecisionSaysSo(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	done := d.decide(dOpts{input: "y\n"}, "--resume")
	if done.Code != 0 || !strings.Contains(done.Stdout, "未完了の判断はありません") {
		t.Errorf("code = %d\n%s%s", done.Code, done.Stdout, done.Stderr)
	}
}

// 送れたのに応答だけ失敗したコメントは、ホストの一覧との照合で送り済みと分かり、送り直さない。
func TestDecideANoteThatLandedButLostItsResponseIsNotPostedTwice(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	d.gl.do(func() { d.gl.lostResponse["note"] = true })
	d.choose(nil)
	rec := d.record()
	if rec == nil || !strings.Contains(stageStates(rec), "note:unknown") {
		t.Fatalf("段階 = %v", rec)
	}
	d.age(fmt.Sprint(rec["id"]), "note")
	done := d.decide(dOpts{input: "y\n"}, "--resume")
	if _, notes := d.gl.stored(); done.Code != 0 || len(notes) != 1 || d.record() != nil { // ホストに在るので、送り直さない
		t.Errorf("code = %d, notes = %d\n%s%s", done.Code, len(notes), done.Stdout, done.Stderr)
	}
}

// 試験の準備（samples の操作記録）が書いた記録を、本物の実行ファイル（ccnavi ops）が読み書きできる通しの確認。
// 形が食い違えば、ここで読めずに落ちる。
func TestTheOpsCommandReadsTheRecordWrittenByTheSamples(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	id := writePlan(t, d.state, filepath.Join(d.tmp, "plan-op.json"), decideBegin(d.mrURL))
	got := run.Ccnavi(t, []string{"--root", d.ws, "ops", "get", id})
	if got.Code != 0 {
		t.Fatalf("ops get: code = %d\n%s%s", got.Code, got.Stdout, got.Stderr)
	}
	for _, want := range []string{"stage reply", "stage issue", "stage note", "reply.state pending"} {
		if !strings.Contains(got.Stdout, want) {
			t.Errorf("ops get の出力に %q が無い:\n%s", want, got.Stdout)
		}
	}
	// 書き換えもできる（段階を送り済みにして、samples 側の読みで確かめる）
	if sent := run.Ccnavi(t, []string{"--root", d.ws, "ops", "stage-set", id, "reply", "sent", "u", "me"}); sent.Code != 0 {
		t.Fatalf("ops stage-set: code = %d\n%s%s", sent.Code, sent.Stdout, sent.Stderr)
	}
	if rec := d.load(id); stageRow(t, rec, "reply")["state"] != "sent" {
		t.Errorf("段階 = %v", rec["stages"])
	}
}
