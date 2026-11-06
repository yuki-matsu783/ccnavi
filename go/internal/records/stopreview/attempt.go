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

// State は試行の状態。遷移は transitions に限る。
type State string

const (
	planWait State = "plan_wait" // 計画より先に material が来た
	pending  State = "pending"   // 開始待ち
	running  State = "running"   // material を渡した
	done     State = "done"      // record が受理した（最終分類つき）
	expired  State = "expired"   // 期限切れ（未検査）
	sentBack State = "sent_back" // 差し戻しの返り値を出した
	consumed State = "consumed"  // AI による継続の Stop で消費した
)

var transitions = map[State][]State{
	planWait: {pending, done, expired},
	pending:  {running, expired},
	running:  {done, sentBack, expired},
	sentBack: {consumed, expired},
}

func finalState(s State) bool { return s == done || s == expired || s == consumed }

// Status は material の入口（Begin）が見た試行の様子。
type Status string

const (
	StatusCandidate    Status = "candidate"
	StatusNotCandidate Status = "not_candidate"
	StatusWait         Status = "wait"
	StatusExpired      Status = "expired"
)

// 判定者。
const (
	judgeAI     = "ai"
	judgeCcnavi = "ccnavi"
)

// 試行のイベントの名前（状態の名前もそのまま使う）。
const (
	eventFinal     = "final"
	eventRejudge   = "rejudge"
	eventNoKey     = "no_key"
	eventTimeout   = "plan_timeout"
	eventSuspected = "suspected_unrecorded_stop"
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

const reasonUnreadable = "attempt_unreadable"
const reasonExpired = "expired"

func stateOf(a *pyjson.Object) State { return State(a.String(keyState)) }

func (s Store) attemptPath(aid string) string {
	return ospath.Join(s.State, attemptPrefix+safe(aid)+jsonSuffix)
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
	aid := AttemptID(key, plan.Rejudge)
	if _, ok := s.ReadPlan(key); !ok {
		plan.Key, plan.AttemptID = key, aid
		fsio.WriteJSON(s.planPath(key), plan.object())
	}
	attempt := s.readAttempt(aid)
	if attempt == nil {
		fresh := pyjson.NewObject(keyAttemptID, aid, keyKey, key, keyCreated, fsio.UnixSeconds(now), keyState, string(pending), keyOriginal, plan.Original)
		if !plan.IsCandidate {
			byCcnavi(fresh, plan.Final, string(plan.Reason), true)
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
		if attempt = s.readAttempt(aid); attempt == nil {
			if stateOf(fresh) != done {
				byCcnavi(fresh, FinalUnchecked, reasonUnreadable, true)
			}
			s.store(fresh)
			s.logEvent(eventFinal, fresh)
			return aid, nil
		}
	}
	if stateOf(attempt) == planWait {
		if plan.IsCandidate {
			s.move(attempt, pending)
		} else {
			byCcnavi(attempt, plan.Final, string(plan.Reason), false)
			if s.move(attempt, done) {
				s.logEvent(eventFinal, attempt)
			}
		}
	}
	return aid, nil
}

// byCcnavi は候補でない試行の最終分類を ccnavi が書く。settle なら状態も done にする。
func byCcnavi(attempt *pyjson.Object, final Final, reason string, settle bool) {
	if settle {
		attempt.Set(keyState, string(done))
	}
	if final == "" {
		final = FinalSkipped
	}
	attempt.Set(keyFinal, string(final))
	attempt.Set(keyJudge, judgeCcnavi)
	attempt.Set(keyReason, reason)
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
	if ospath.DiskLexists(path) {
		return false, nil // 別のプロセスが先に作った
	}
	return false, errors.New(reason)
}

func (s Store) store(attempt *pyjson.Object) {
	fsio.WriteJSON(s.attemptPath(attempt.String(keyAttemptID)), attempt)
}

func (s Store) move(attempt *pyjson.Object, target State) bool {
	if !slices.Contains(transitions[stateOf(attempt)], target) {
		return false
	}
	attempt.Set(keyState, string(target))
	if target == done || target == sentBack {
		attempt.Set(keyDoneAt, fsio.UnixSeconds(time.Now())) // 作成から完了までの待ち時間を集計で出す
	}
	s.store(attempt)
	s.logEvent(string(target), attempt)
	return true
}

// Begin は material の入口。計画が無ければ plan_wait の試行を置いて待たせる。
func (s Store) Begin(key string, now time.Time) (Status, Plan, error) {
	plan, ok := s.ReadPlan(key)
	if !ok {
		_, err := s.create(pyjson.NewObject(keyAttemptID, AttemptID(key, false), keyKey, key, keyCreated, fsio.UnixSeconds(now), keyState, string(planWait)))
		return StatusWait, Plan{}, err
	}
	if !plan.IsCandidate {
		return StatusNotCandidate, plan, nil
	}
	attempt := s.readAttempt(plan.AttemptID)
	if attempt == nil || stateOf(attempt) == planWait {
		return StatusWait, plan, nil
	}
	switch stateOf(attempt) {
	case pending:
		s.move(attempt, running)
	case expired:
		return StatusExpired, plan, nil
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
	when := fsio.UnixSeconds(now)
	ttl := config.StopReviewTTL.Value().Seconds()
	var out []string
	for _, attempt := range records(s.State, attemptPrefix) {
		if finalState(stateOf(attempt)) {
			continue
		}
		wasSentBack := stateOf(attempt) == sentBack
		started := number(attempt.Value(keyCreated), when)
		if wasSentBack {
			started = number(attempt.Value(keySentAt), when)
		}
		if when-started < ttl {
			continue
		}
		if !wasSentBack {
			attempt.Set(keyFinal, string(FinalUnchecked))
			attempt.Set(keyJudge, judgeCcnavi)
			attempt.Set(keyReason, reasonExpired)
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
	when := fsio.UnixSeconds(now)
	window := config.StopReviewTTL.Value().Seconds()
	found, ok := "", false
	for _, attempt := range records(s.State, attemptPrefix) {
		if stateOf(attempt) == sentBack &&
			strings.HasPrefix(attempt.String(keyKey), session+":") &&
			when-number(attempt.Value(keySentAt), 0) <= window &&
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
	verdict := "inconclusive"
	switch final {
	case FinalOK:
		verdict = "resolved"
	case FinalFlawed:
		verdict = "unresolved"
	}
	attempt.Set(keyRejudge, verdict)
	s.store(attempt)
	s.logEvent(eventRejudge, attempt)
}
