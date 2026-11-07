package cli

import (
	"fmt"
	"io"
	"slices"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/records/stopreview"
	"ccnavi/internal/tickets/approval"
)

const (
	// planWait・planPoll は material が計画の届くのを待つ時間と間隔（暫定。成立性の検証の後に決める）。
	planWait     = 5 * time.Second
	planPoll     = 250 * time.Millisecond
	notCandidate = "STOP_REVIEW_NOT_CANDIDATE"
)

// 候補でない理由のうち、ここで決めるもの（ほかは stopreview.Begin の状態の名前）。
const (
	whyNoState     = "no_state"
	whyNoKey       = "no_key"
	whyPlanTimeout = "plan_timeout"
	whyError       = "error"
)

// writeNotCandidate は「候補でない」の応答を書く。判定役はこの形（目印の行、理由の行、返り値の行）を読んで、
// 判定をせずに終える。終了コードは 0（判定役の失敗が作業を止めてはならない）。
func writeNotCandidate(stdout io.Writer, why string) int {
	fmt.Fprintf(stdout, "%s\nreason: %s\n", notCandidate, why)
	fmt.Fprint(stdout, "return: ")
	writeJSON(stdout, stopreview.OKReturn.Object(), true, 0)
	return config.ExitOK
}

// writeOK は判定役が返す返り値 {"ok": true} を書く。record が拒否・失敗したときもこれを返し、作業を止めない。
func writeOK(stdout io.Writer) int {
	writeJSON(stdout, stopreview.OKReturn.Object(), true, 0)
	return config.ExitOK
}

// runStopReview は `ccnavi stop-review report|material|record`。Stop の agent hook（判定役）が Bash から打つ入口。
// material・record は終了コード 0 で終わる（判定役の失敗が作業を止めてはならない）。出力はいつも決まった形にする。
// LLM は呼ばない。
//
//   - material <セッション> <transcript>: 共有キーで判定計画を取り、材料を返す。候補でなければその旨と返り値
//   - record <セッション> <transcript> [<JSON>]: 判定結果（JSON。無ければ標準入力）を検証し、agent hook がそのまま
//     返す返り値（JSON 1 行）だけを書く。拒否・実行できない場合も {"ok": true}
//   - report: 評価の集計（ユーザが採用を判断する材料）
func runStopReview(in *input, stdout, stderr io.Writer, conf *config.Settings, words []string, root string) int {
	if len(words) == 2 && words[1] == "report" {
		return stopReviewReport(stdout, stderr, conf)
	}
	// 1. 引数を読む。record の JSON は最後の引数でも渡せる。形が合わなければ使い方を言って、成功の応答で終える。
	inline := ""
	if len(words) == 5 && words[1] == "record" {
		// JSON を最後の引数で渡す形（権限の許可に合わせた、パイプ・ヒアドキュメントの無い形）
		inline, words = words[4], words[:4]
	}
	recording := len(words) > 1 && words[1] == "record"
	if len(words) != 4 || !slices.Contains([]string{"material", "record"}, words[1]) {
		fmt.Fprintln(stderr, "ccnavi: stop-review は material か record に <セッション> <transcript> を渡す")
		if recording {
			return writeOK(stdout)
		}
		return config.ExitOK
	}
	verb, session, transcript := words[1], words[2], words[3]
	// skip は判定をしない回の応答。record は {"ok": true}、material は候補でない旨を返す。
	skip := func(why string) int {
		if recording {
			return writeOK(stdout)
		}
		return writeNotCandidate(stdout, why)
	}
	if conf.State == "" {
		return skip(whyNoState)
	}
	// 2. 判定役と判定の計画を結ぶ共有キーを決める。決まらなければ候補にしない。
	store := stopreview.Store{State: conf.State, Logs: stopreview.LogsDir(conf.Log)}
	key := stopreview.SharedKey(session, transcript)
	if key == "" {
		store.LogNoKey(stopreview.SideJudge)
		return skip(whyNoKey)
	}
	// 3. 本体。想定外の失敗は、判定役の返り値を保ったまま skip に倒す。期限切れの計画は、先に片付ける。
	code, fail := stopReviewStep(func() (int, error) {
		store.ExpireOverdue(time.Now())
		if recording {
			return stopReviewRecord(in, stdout, conf, store, key, root, inline)
		}
		return stopReviewMaterial(stdout, conf, store, key, transcript, root)
	})
	if fail != nil {
		fmt.Fprintf(stderr, "ccnavi: stop-review %s に失敗した: %s\n", verb, fail.kind)
		return skip(whyError)
	}
	return code
}

// stopReviewStep は material・record の本体を走らせ、想定外の失敗（error と panic）を失敗として返す。
func stopReviewStep(step func() (int, error)) (code int, fail *failure) {
	defer onPanic(func(_ any, f failure) { fail = &f })
	code, err := step()
	if err != nil {
		f := failedWith(err)
		return 0, &f
	}
	return code, nil
}

// stopReviewReport は評価の集計を出す。ユーザが判定役を採用するかを決める材料。
func stopReviewReport(stdout, stderr io.Writer, conf *config.Settings) (code int) {
	// 集計の失敗で落ちない。壊れた記録を名指しする
	defer onPanic(func(r any, f failure) {
		fmt.Fprintf(stderr, "ccnavi: 評価の集計に失敗した（%s: %v）\n", f.kind, r)
		code = config.ExitError
	})
	fmt.Fprintln(stdout, stopreview.Report(conf.State, stopreview.LogsDir(conf.Log)))
	return config.ExitOK
}

// stopReviewMaterial は判定役に渡す材料を書く。判定の計画が届くまで少し待ち、届かなければ候補にしない。
func stopReviewMaterial(stdout io.Writer, conf *config.Settings, store stopreview.Store, key, transcript, root string) (int, error) {
	deadline := time.Now().Add(planWait)
	var status stopreview.Status
	var plan stopreview.Plan
	for {
		var err error
		status, plan, err = store.Begin(key, time.Now())
		if err != nil {
			return 0, err
		}
		if status != stopreview.StatusWait || !time.Now().Before(deadline) {
			break
		}
		time.Sleep(planPoll)
	}
	if status == stopreview.StatusWait {
		store.LogPlanTimeout(key)
		return writeNotCandidate(stdout, whyPlanTimeout), nil
	}
	if status != stopreview.StatusCandidate {
		return writeNotCandidate(stdout, string(status)), nil
	}
	// 計画が候補になったら、判定の基準と、このターンの会話から材料を組む。
	criteria := stopreview.LoadCriteria(root)
	material, err := stopreview.TurnMaterial(transcript)
	if err != nil {
		return 0, err
	}
	// 定義が計画を作った時から変わっていたら、材料が変わったとして扱う（record が拒否する）。
	changed := stopreview.MaterialVersion(criteria) != plan.MaterialVersion
	store.NoteInsufficient(key, material.Insufficient || changed || criteria == "")
	bodies := map[string]string{}
	for _, p := range plan.Proposals {
		bodies[p.Key] = proposalBody(root, p)
	}
	rendered := stopreview.RenderMaterial(plan, material, criteria, bodies)
	stopreview.SaveMaterial(store.Logs, plan.AttemptID, criteria, rendered)
	fmt.Fprintln(stdout, rendered)
	return config.ExitOK, nil
}

// proposalBody は送った版の提案の本文。送ったコミットから読む（ワークツリーの今の姿ではなく、送った版）。読めなければ
// 空（材料に本文が無いと示す）。送っていない提案は、書いた md の今の中身を読む。
func proposalBody(root string, p stopreview.Proposal) string {
	// 送っていない提案は今のファイルから、送った提案は送ったコミットから読む。root の外のパスや `..` を含むパスは読まない。
	var parts []string
	if p.Route == stopreview.RouteUnsent {
		for _, f := range p.Files {
			if !pathcase.HasExt(f.Path, config.MdExt) || root == "" || !inside(realPath(f.Path), realPath(root)) {
				continue
			}
			text, err := fsio.ReadDiskTextHead(f.Path, stopreview.MaterialLimit, fsio.Replace)
			if err != nil {
				continue
			}
			parts = append(parts, "--- "+f.Path+"（未送信）---\n"+text)
		}
		return strings.Join(parts, "\n")
	}
	if root == "" || !shaForm.MatchString(p.Commit) {
		return ""
	}
	for _, f := range p.Files {
		if !pathcase.HasExt(f.Path, config.MdExt) || strings.HasPrefix(f.Path, "/") ||
			slices.Contains(strings.Split(f.Path, "/"), "..") {
			continue
		}
		shown := git.Run(root, []string{"show", p.Commit + ":" + f.Path}, git.Options{Timeout: git.ReadTimeout})
		if shown.OK() {
			parts = append(parts, "--- "+f.Path+" ---\n"+headRunes(shown.Out, stopreview.MaterialLimit))
		}
	}
	return strings.Join(parts, "\n")
}

// headRunes は先頭 n 字。
func headRunes(s string, n int) string {
	for i := range s {
		if n == 0 {
			return s[:i]
		}
		n--
	}
	return s
}

// stopReviewRecord は判定役の結果を検証して受け付け、agent hook がそのまま返す返り値を書く。
// 結果の JSON が読めなければ nil として渡し、受け付ける側が拒否する。
func stopReviewRecord(in *input, stdout io.Writer, conf *config.Settings, store stopreview.Store, key, root, inline string) (int, error) {
	text := inline
	if text == "" {
		raw, err := io.ReadAll(in.buf)
		if err != nil {
			return 0, err
		}
		text = string(raw)
	}
	result, err := pyjson.Loads(text)
	if err != nil {
		result = nil
	}
	criteria := stopreview.LoadCriteria(root)
	plan, _ := store.ReadPlan(key)
	idents := map[string]string{}
	for _, p := range plan.Proposals {
		idents[p.Key] = p.Ident
	}
	out := store.Accept(key, stopreview.AcceptInput{
		Result:          result,
		MaterialVersion: stopreview.MaterialVersion(criteria),
		StillUnapproved: func(target string) bool { return !approvedNow(conf, root, idents[target]) },
	})
	writeJSON(stdout, out.Object(), false, 0)
	return config.ExitOK, nil
}

// approvedNow は提案が今、承認済みの置き場にあるか。判定の途中で別の PC から承認されたかを確かめる。
func approvedNow(conf *config.Settings, root, ident string) bool {
	if ident == "" {
		return false
	}
	copies, _ := approval.Scan(conf, root, false, nil)
	_, found := tree.Lookup(approval.ByID(copies), ident)
	return found
}
