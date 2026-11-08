package stopreview

import (
	"errors"
	"slices"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
)

// Store は試行を置く state の置き場と、試行のイベントを書くログの置き場。Logs が空ならイベントは書かない。
type Store struct {
	State string
	Logs  string
}

// LogsDir は設定の log（`logs/decisions.jsonl` というファイル）が置かれたディレクトリ。Store.Logs に渡す。
func LogsDir(logFile string) string {
	if logFile == "" {
		return ""
	}
	return ospath.Dirname(logFile)
}

// state は試行の状態。遷移は transitions に限る。
type state string

const (
	planWait state = "plan_wait" // 計画より先に material が来た
	pending  state = "pending"   // 開始待ち
	running  state = "running"   // material を渡した
	done     state = "done"      // record が受理した（最終分類つき）
	expired  state = "expired"   // 期限切れ（未検査）
	sentBack state = "sent_back" // 差し戻しの返り値を出した
	consumed state = "consumed"  // AI による継続の Stop で消費した
)

var transitions = map[state][]state{
	planWait: {pending, done, expired},
	pending:  {running, expired},
	running:  {done, sentBack, expired},
	sentBack: {consumed, expired},
}

func finalState(s state) bool { return s == done || s == expired || s == consumed }

// Status は material の入口（Begin）が見た試行の様子。
type Status string

const (
	StatusCandidate    Status = "candidate"
	statusNotCandidate Status = "not_candidate"
	StatusWait         Status = "wait"
	statusExpired      Status = "expired"
)

// judge は試行の最終分類を書いた者。
type judge string

const (
	judgeAI     judge = "ai"
	judgeCcnavi judge = "ccnavi"
)

// event は試行のイベントの名前。状態が移ったときは状態の名前をそのまま使う（eventOf）。
type event string

const (
	eventFinal     event = "final"
	eventRejudge   event = "rejudge"
	eventNoKey     event = "no_key"
	eventTimeout   event = "plan_timeout"
	eventSuspected event = "suspected_unrecorded_stop"
)

func eventOf(s state) event { return event(s) }

// verdict は再判定の結果を元の試行に残す値。
type verdict string

const (
	verdictResolved     verdict = "resolved"
	verdictUnresolved   verdict = "unresolved"
	verdictInconclusive verdict = "inconclusive"
)

// 試行のキー。
const (
	keyAttemptID = "attempt_id"
	keyKey       = "key"
	keyCreated   = "created"
	keyState     = "state"
	keyOriginal  = "original"
	keyFinal     = "final"
	keyJudge     = "judge"
	keyReason    = "reason"
	keyDoneAt    = "done_at"
	keySentAt    = "sent_at"
	keyRejudge   = "rejudge"
	keyAccepted  = "accepted"
	keyTainted   = "tainted"
	keyResults   = "results"
	keyEffective = "effective"
	keyInsuff    = "insufficient"
)

// ccnavi が最終分類を書いた試行の理由（候補にしなかった理由のほか）。
const (
	reasonUnreadable Reason = "attempt_unreadable"
	reasonExpired    Reason = "expired"
	// reasonRejected は判定結果を受理しなかった理由の頭。後ろに拒否の理由が続く。
	reasonRejected Reason = "rejected: "
)

func stateOf(a *pyjson.Object) state { return state(a.String(keyState)) }

func (s Store) attemptPath(aid string) string {
	return fsio.RecordPath(s.State, AttemptPrefix, aid, fsio.JSONExt)
}

func (s Store) readAttempt(aid string) *pyjson.Object {
	if s.State == "" || aid == "" {
		return nil
	}
	return readObject(s.attemptPath(aid))
}

// WritePlan は計画を 1 件書いて試行を作り、試行 ID を返す。同じキーで呼び直しても結果は変わらない（冪等）。
// 候補でない計画は、作ると同時に ccnavi が最終分類まで確定する。在るのに読めない試行は、未検査で確定する。
// 次の Stop は別のキーなので、新しい試行になる。計画より先に material が来ていたら、その試行を進める。
// 試行を作れない（権限・ディスク）ときは、書けなかった理由の文をエラーで返す。
func (s Store) WritePlan(key string, plan Plan, now time.Time) (string, error) {
	aid := attemptID(key, plan.Rejudge)
	// 計画は最初の 1 件を残す。呼び直しで上書きすると、先に material が見た計画と食い違う。
	if _, ok := s.ReadPlan(key); !ok {
		plan.key, plan.AttemptID = key, aid
		fsio.WriteJSON(s.planPath(key), plan.object())
	}
	// 試行がまだ無ければ作る。候補でない計画は、作ると同時に最終分類まで確定する。
	attempt := s.readAttempt(aid)
	if attempt == nil {
		fresh := pyjson.NewObject(keyAttemptID, aid, keyKey, key, keyCreated, fsio.UnixSeconds(now), keyState, string(pending), keyOriginal, plan.Original)
		if !plan.IsCandidate {
			byCcnavi(fresh, plan.Final, plan.Reason, true)
		}
		// 排他的に作る。material が先に plan_wait を置く競合で、どちらも上書きしないため。
		created, err := s.create(fresh)
		if err != nil {
			return "", err
		}
		if created {
			if stateOf(fresh) == done {
				s.logEvent(eventFinal, fresh)
			}
			return aid, nil
		}
		// 作ろうとした間に別のプロセス（material）が先に作っていた。読み直せればそちらを進め、
		// 読めなければ作った試行を未検査で確定する。
		if attempt = s.readAttempt(aid); attempt == nil {
			if stateOf(fresh) != done {
				byCcnavi(fresh, FinalUnchecked, reasonUnreadable, true)
			}
			s.store(fresh)
			s.logEvent(eventFinal, fresh)
			return aid, nil
		}
	}
	// material が計画より先に来て待っていた試行を、計画に従って進める。
	if stateOf(attempt) == planWait {
		if plan.IsCandidate {
			s.move(attempt, pending)
		} else {
			byCcnavi(attempt, plan.Final, plan.Reason, false)
			if s.move(attempt, done) {
				s.logEvent(eventFinal, attempt)
			}
		}
	}
	return aid, nil
}

// byCcnavi は候補でない試行の最終分類を ccnavi が書く。settle なら状態も done にする。
func byCcnavi(attempt *pyjson.Object, final Final, reason Reason, settle bool) {
	if settle {
		attempt.Set(keyState, string(done))
	}
	if final == "" {
		final = FinalSkipped
	}
	attempt.Set(keyFinal, string(final))
	attempt.Set(keyJudge, string(judgeCcnavi))
	attempt.Set(keyReason, string(reason))
}

// create は試行のファイルを、まだ無いものとして作る。在れば false（上書きしない）。
func (s Store) create(attempt *pyjson.Object) (bool, error) {
	path := s.attemptPath(attempt.String(keyAttemptID))
	body, err := pyjson.Dumps(attempt, pyjson.Options{RawUnicode: true})
	if err != nil {
		return false, err
	}
	reason := fsio.WriteFile(path, []byte(body), fsio.New)
	if reason == "" {
		return true, nil
	}
	// 有無はディスクだけで決め、溜める段は見ない。
	if ospath.DiskLexists(path) {
		return false, nil // 別のプロセスが先に作った
	}
	return false, errors.New(reason)
}

func (s Store) store(attempt *pyjson.Object) {
	fsio.WriteJSON(s.attemptPath(attempt.String(keyAttemptID)), attempt)
}

func (s Store) move(attempt *pyjson.Object, target state) bool {
	if !slices.Contains(transitions[stateOf(attempt)], target) {
		return false
	}
	attempt.Set(keyState, string(target))
	if target == done || target == sentBack {
		attempt.Set(keyDoneAt, fsio.UnixSeconds(time.Now())) // 作成から完了までの待ち時間を集計で出す
	}
	s.store(attempt)
	s.logEvent(eventOf(target), attempt)
	return true
}

// Begin は material の入口。計画が無ければ plan_wait の試行を置いて待たせる。
func (s Store) Begin(key string, now time.Time) (Status, Plan, error) {
	plan, ok := s.ReadPlan(key)
	if !ok {
		_, err := s.create(pyjson.NewObject(keyAttemptID, attemptID(key, false), keyKey, key, keyCreated, fsio.UnixSeconds(now), keyState, string(planWait)))
		return StatusWait, Plan{}, err
	}
	if !plan.IsCandidate {
		return statusNotCandidate, plan, nil
	}
	attempt := s.readAttempt(plan.AttemptID)
	if attempt == nil || stateOf(attempt) == planWait {
		return StatusWait, plan, nil
	}
	switch stateOf(attempt) {
	case pending:
		s.move(attempt, running)
	case expired:
		return statusExpired, plan, nil
	}
	return StatusCandidate, plan, nil
}

// NoteInsufficient は material が材料不足（切り詰め・読めない・定義が変わった）と見たことを試行に残す。
// record はそれを未検査に倒す。
func (s Store) NoteInsufficient(key string, insufficient bool) {
	plan, ok := s.ReadPlan(key)
	if !ok {
		return
	}
	if attempt := s.readAttempt(plan.AttemptID); attempt != nil {
		attempt.Set(keyInsuff, insufficient)
		s.store(attempt)
	}
}

// ExpireOverdue は期限（config.StopReviewTTL）を過ぎても終わっていない試行を、未検査の期限切れとして確定し、
// その試行 ID を返す。差し戻した試行の最終分類（不備検出）は残し、閉じるだけにする。
func (s Store) ExpireOverdue(now time.Time) []string {
	ttl := config.StopReviewTTL.Value()
	var out []string
	for _, attempt := range records(s.State, AttemptPrefix) {
		if finalState(stateOf(attempt)) {
			continue
		}
		wasSentBack := stateOf(attempt) == sentBack
		started := moment(attempt.Value(keyCreated), now)
		if wasSentBack {
			started = moment(attempt.Value(keySentAt), now)
		}
		if now.Sub(started) < ttl {
			continue
		}
		if !wasSentBack {
			attempt.Set(keyFinal, string(FinalUnchecked))
			attempt.Set(keyJudge, string(judgeCcnavi))
			attempt.Set(keyReason, string(reasonExpired))
		}
		if s.move(attempt, expired) {
			s.logEvent(eventFinal, attempt)
			out = append(out, attempt.String(keyAttemptID))
		}
	}
	return out
}

// ConsumeContinuation は、stop_hook_active の Stop で、同じセッションに差し戻した試行があれば消費して、その試行 ID を返す。
// 差し戻しから期限を過ぎたものは消費しない。ユーザが割り込んだ後の別の連鎖を、AI による継続と取り違えないため。
func (s Store) ConsumeContinuation(session string, now time.Time) (string, bool) {
	window := config.StopReviewTTL.Value()
	found, ok := "", false
	for _, attempt := range records(s.State, AttemptPrefix) {
		if stateOf(attempt) == sentBack &&
			strings.HasPrefix(attempt.String(keyKey), session+":") &&
			now.Sub(moment(attempt.Value(keySentAt), epoch)) <= window &&
			s.move(attempt, consumed) {
			found, ok = attempt.String(keyAttemptID), true
		}
	}
	return found, ok
}

// resolveOriginal は再判定の結果を元の試行に残す。解消と言えるのは終了可のときだけ。未検査や対象外では決められない。
func (s Store) resolveOriginal(original string, final Final) {
	attempt := s.readAttempt(original)
	if attempt == nil {
		return
	}
	result := verdictInconclusive
	switch final {
	case finalOK:
		result = verdictResolved
	case finalFlawed:
		result = verdictUnresolved
	}
	attempt.Set(keyRejudge, string(result))
	s.store(attempt)
	s.logEvent(eventRejudge, attempt)
}
