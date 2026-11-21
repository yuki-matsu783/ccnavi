package start

import (
	"strconv"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/shkit"
)

// ccnavi-sync.sh のロック（mkdir で取り、ホスト名・pid・開始時刻を書く。古いロックは mv で強制取得する）。
// 同じ機械で持ち主が生きていれば、時刻が古くても強制取得しない。別のホストの pid はこの機械では確かめられないので、時刻だけで見る。
// 強制取得の操作は 1 つずつ（<ロック>.steal が新しければ待つ）。入れ子の印は、持ち主の情報が合うときだけ信じる。
func TestSyncLock(t *testing.T) {
	t.Parallel()
	now := func() int64 { return time.Now().Unix() }
	cases := []struct {
		name string
		// setup はロックを置き、足す環境と、ロックの持ち主の印を返す。
		setup      func(s *syncEnv) (lock string, env map[string]string)
		wantCode   int
		wantStop   bool // 「ロックを他の操作が持っている」と言う
		lockKept   bool
		wantMerged bool // 親の取り込みが進む
	}{
		{"held", func(s *syncEnv) (string, map[string]string) {
			return s.ownLock(base.LiveShPid(s.t), now(), ""), nil
		}, 1, true, true, false},
		{"stale", func(s *syncEnv) (string, map[string]string) {
			return s.ownLock(s.deadPid(), now(), ""), nil
		}, 0, false, false, true},
		// 別のホストの pid は、この機械では確かめられない（コンテナの入れ替わりなど）。ホスト名が違えば時刻だけで見る。
		{"fresh lock of another host with a dead pid", func(s *syncEnv) (string, map[string]string) {
			return s.ownLock(s.deadPid(), now(), "other-host"), nil
		}, 1, false, true, false},
		{"live owner is not robbed after ten minutes", func(s *syncEnv) (string, map[string]string) {
			return s.ownLock(base.LiveShPid(s.t), now()-3600, ""), nil
		}, 1, false, true, false},
		{"old lock of another host is taken over by time", func(s *syncEnv) (string, map[string]string) {
			return s.ownLock(base.LiveShPid(s.t), now()-3600, "other-host"), nil
		}, 0, false, false, true},
		// 強制取得の操作は 1 つずつ。取得用のロック（<ロック>.steal）が新しければ強制取得せずに待つ。
		{"another thief holds the steal gate", func(s *syncEnv) (string, map[string]string) {
			lock := s.ownLock(s.deadPid(), now(), "")
			shkit.Mkdir(s.t, lock+".steal")
			return lock, nil
		}, 1, false, true, false},
		// CCNAVI_LOCK_HELD の持ち主の情報がロックの持ち主と合わなければ入れ子として扱わない。
		{"a forged nesting mark is not trusted", func(s *syncEnv) (string, map[string]string) {
			return s.ownLock(base.LiveShPid(s.t), now(), ""), map[string]string{"CCNAVI_LOCK_HELD": "self/" + syncParent + ":1-2"}
		}, 1, true, true, false},
		{"a true nesting mark is trusted and left in place", func(s *syncEnv) (string, map[string]string) {
			started, pid := now(), base.LiveShPid(s.t)
			mark := strconv.Itoa(pid) + "-" + strconv.FormatInt(started, 10)
			return s.ownLock(pid, started, ""), map[string]string{"CCNAVI_LOCK_HELD": "self/" + syncParent + ":" + mark}
		}, 0, false, true, true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			s := newSync(t)
			lock, extra := c.setup(s)
			head := s.remoteCommit(syncParent, "theirs.txt", "theirs\n")
			done := code(t, s.syncEnvRun(merge(map[string]string{"CCNAVI_LOCK_WAIT": "0"}, extra), syncParent), c.wantCode)
			if c.wantStop {
				has(t, done.Stdout, "ロックを他の操作が持っている")
			}
			eq(t, sha(t, s.tree, "HEAD") == head, c.wantMerged, "取り込みが進んだか")
			eq(t, shkit.Exists(lock), c.lockKept, "ロックが残っているか")
			if c.wantCode == 0 && !c.lockKept {
				truth(t, !shkit.Exists(lock+".steal"), "取得用のロックが残っている")
			}
		})
	}
}
