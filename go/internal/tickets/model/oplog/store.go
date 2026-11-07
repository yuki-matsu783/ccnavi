package oplog

import (
	"errors"
	"io/fs"
	"os"
	"time"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

const (
	// lockWait は読み書きのロックを待つ長さ、lockStale は置き去りのロックとみなす古さ、lockPoll は待つ間隔。
	lockWait  = 5 * time.Second
	lockStale = 30 * time.Second
	lockPoll  = 20 * time.Millisecond
	lockExt   = ".rmw"
	lockPerm  = 0o666
)

// Options は Begin に渡す中身。ID を渡すとその ID で作る（復旧で同じ ID を使い回す）。
type Options struct {
	Repo        *pyjson.Object
	Parent      string
	Phase       int
	MR          int
	RequestHead string
	Shown       []any
	Choices     *pyjson.Object
	Plan        *pyjson.Object
	Stages      []StageSpec
	ID          string
}

// Begin は操作を始める記録を保存して、操作ID を返す。保存できなければ *Error を返し、呼び手は何もしない
// （返信・解決にも進まない）。同じ ID の記録が既にあれば上書きせず、まだ無い段階だけを足す
// （単発の投稿が段階を順に足していく）。
func Begin(state string, kind Kind, o Options) (string, error) {
	if !knownKind(kind) {
		return "", fail(msgBadKind, kind)
	}
	id := o.ID
	if id == "" {
		id = newID()
	}
	if err := checkID(id); err != nil {
		return "", err
	}
	if fsio.Exists(recordPath(state, id)) {
		return id, addStages(state, id, o.Stages)
	}
	stamp := now()
	r := &Record{
		ID: id, Kind: kind, Repo: orEmpty(o.Repo), Parent: o.Parent, Phase: o.Phase, MR: o.MR,
		requestHead: o.RequestHead, Shown: o.Shown, Choices: orEmpty(o.Choices), Plan: orEmpty(o.Plan),
		Applied: pyjson.NewObject(), createdAt: stamp, updatedAt: stamp,
	}
	for _, spec := range o.Stages {
		r.Stages = append(r.Stages, newStage(spec))
	}
	return id, save(state, r)
}

func addStages(state, id string, specs []StageSpec) error {
	if len(specs) == 0 {
		return nil
	}
	return update(state, id, func(r *Record) (bool, error) {
		if r == nil {
			return false, nil
		}
		added := false
		for _, spec := range specs {
			if r.stage(spec.ID) == nil {
				r.Stages = append(r.Stages, newStage(spec))
				added = true
			}
		}
		return added, nil
	})
}

func save(state string, r *Record) error {
	if err := fsio.MakeDirs(dir(state)); err != nil {
		return fail(msgCannotSave, fsio.ErrorText(err))
	}
	r.updatedAt = now()
	format := fsio.JSONFormat{Indent: fsio.JSONIndentOne, FinalNewline: true, Newline: fsio.LF, How: fsio.Atomic}
	if failed := fsio.WriteJSONAs(recordPath(state, r.ID), r.Object(), format); failed != "" {
		return fail(msgCannotSave, failed)
	}
	return nil
}

// Load は記録を読む。無ければ nil, nil。壊れていれば *Error。
func Load(state, id string) (*Record, error) {
	if err := checkID(id); err != nil {
		return nil, err
	}
	text, ok := fsio.ReadText(recordPath(state, id), fsio.Strict)
	if !ok {
		return nil, nil
	}
	data, err := pyjson.Loads(text)
	if err != nil {
		return nil, fail(msgNotJSON, id)
	}
	obj, ok := data.(*pyjson.Object)
	if !ok {
		return nil, fail(msgBadShape, id)
	}
	return fromObject(id, obj)
}

// update はロックを持ったまま読み、change が真を返したら保存する。
func update(state, id string, change func(*Record) (bool, error)) error {
	unlock, err := lock(state, id)
	if err != nil {
		return err
	}
	defer unlock()
	r, err := Load(state, id)
	if err != nil {
		return err
	}
	changed, err := change(r)
	if err != nil || !changed {
		return err
	}
	return save(state, r)
}

// lock は記録の読み書きの間だけ持つ短いロック。並行する更新が互いの書き込みを失わせないため。
func lock(state, id string) (unlock func(), err error) {
	if err := checkID(id); err != nil {
		return nil, err
	}
	if err := fsio.MakeDirs(dir(state)); err != nil {
		return nil, &Error{fsio.ErrorText(err)}
	}
	path := ospath.Join(dir(state), id+lockExt)
	deadline := time.Now().Add(lockWait)
	for {
		f, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, lockPerm)
		if err == nil {
			return func() {
				f.Close()
				os.Remove(path)
			}, nil
		}
		if !errors.Is(err, fs.ErrExist) {
			return nil, &Error{fsio.ErrorText(err)}
		}
		// 待つ上限は先に見る（消せない・読めないときに、上限を飛ばして回り続けない）
		if time.Now().After(deadline) {
			return nil, &Error{msgBusy}
		}
		if mtime, _, ok := ospath.Stat(path); ok && time.Since(mtime) > lockStale {
			os.Remove(path)
			continue
		}
		time.Sleep(lockPoll)
	}
}

// StageSet は段階の状態を書く。Sent に進めるときは URL と送ったアカウントを残す。Sent は戻さない
// （Sent の段階は繰り返さない）。無い段階・知らない状態は *Error。
func StageSet(state, id, stageID string, to State, url, account string) error {
	if err := checkID(id); err != nil {
		return err
	}
	if !knownState(to) {
		return fail(msgBadState, to)
	}
	return update(state, id, func(r *Record) (bool, error) {
		if r == nil {
			return false, fail(msgMissing, id)
		}
		s := r.stage(stageID)
		if s == nil {
			return false, fail(msgNoStage, id, stageID)
		}
		if s.State == StateSent && to != StateSent {
			return false, fail(msgSentBack, stageID)
		}
		// 試行の回数は、状態が変わるか待ちに戻すたびに数える（送信済みへの変更は数えない）。
		if (to != s.State || to == StatePending) && to != StateSent {
			s.tries++
		}
		s.State = to
		s.at = now()
		if url != "" {
			s.URL = url
		}
		if account != "" {
			s.account = account
		}
		return true, nil
	})
}

// AppliedSet はローカルに適用したもの（子・マーカー・台帳の項目・push した先頭など）を残す。
func AppliedSet(state, id, key string, value any) error {
	return update(state, id, func(r *Record) (bool, error) {
		if r == nil {
			return false, fail(msgMissing, id)
		}
		r.Applied.Set(key, value)
		return true, nil
	})
}

// MarkSent は、別の経路（先頭の目印の照合）で送り済みと分かった単発の投稿を記録の上でも送り済みにし、
// 操作ID を返す。記録が無ければ作る。mr は sh から来た綴りのまま（数字でなければ 0 として残す）。
func MarkSent(state string, kind Kind, parent, mr, key, stageID, url string) (string, error) {
	id := IDFor(string(kind), parent, "0", mr, key)
	number := 0
	if pyfmt.IsDigit(mr) {
		number, _ = pyfmt.ParseInt(mr)
	}
	if _, err := Begin(state, kind, Options{
		Parent: parent, MR: number, Stages: []StageSpec{{stageID, StageComment}}, ID: id,
	}); err != nil {
		return "", err
	}
	return id, StageSet(state, id, stageID, StateSent, url, "")
}

func remove(state, id string) { fsio.Remove(recordPath(state, id)) }
