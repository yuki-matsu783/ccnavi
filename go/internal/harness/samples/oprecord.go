package samples

import (
	"cmp"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/oplog"
)

// テストの準備で、操作記録（`logs/state/ops/<操作ID>.json`）を実行ファイルと同じ形で書く。
//
// 実行ファイルの `ops begin` は、判断の中身（shown・choices・plan）を受けない。受け入れの計画をスタブで書く試験は、
// 判断を載せた記録が要るので、ここで書く。記録は tickets/model/oplog の型で書くので、形が食い違えば、
// このパッケージが組めず、読む側（`ccnavi ops …`）の変更に追随する（「食い違えば試験が落ちる」守りと同じ）。
// 記録の読み・段階や適用の欄の書き換えは oplog.Load・StageSet・AppliedSet をそのまま使う。

// BeginRecord は操作を始める記録を書き、操作ID を返す。stages は（段階ID, 種類）を送る順に並べたもの。
func BeginRecord(t testing.TB, state string, kind oplog.Kind, o oplog.Options) string {
	t.Helper()
	id, err := oplog.Begin(state, kind, o)
	if err != nil {
		t.Fatal(err)
	}
	return id
}

// RecordIDs は置いてある記録の操作ID（書いた順）。
func RecordIDs(state string) []string {
	dir := filepath.Join(state, "ops")
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	type found struct {
		mtime int64
		id    string
	}
	var rows []found
	for _, e := range entries {
		id, ok := strings.CutSuffix(e.Name(), ".json")
		if !ok {
			continue
		}
		if info, err := e.Info(); err == nil {
			rows = append(rows, found{info.ModTime().UnixNano(), id})
		}
	}
	slices.SortFunc(rows, func(a, b found) int { return cmp.Or(cmp.Compare(a.mtime, b.mtime), strings.Compare(a.id, b.id)) })
	ids := make([]string, len(rows))
	for i, r := range rows {
		ids[i] = r.id
	}
	return ids
}

// OprecordMain は sh のスタブが `teststub oprecord <state> <計画の出力先> <記録と計画の JSON ファイル>` で起こす口の本体
// （引数は `<state> <計画の出力先> <記録と計画の JSON>`）。記録を作って、その操作ID を入れた計画を書く。
// JSON は `{"begin": {kind・repo・parent・phase・mr・request_head・shown・choices・plan・stages}, "plan": {計画の欄}}`
// （stages は [段階ID, 種類] の並び）。成功は 0、読めない・書けないときは文を stderr に出して 1。
func OprecordMain(args []string, stderr io.Writer) int {
	if err := oprecord(args); err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	return 0
}

func oprecord(args []string) error {
	if len(args) != 3 {
		return fmt.Errorf("使い方: oprecord <state> <計画の出力先> <JSON ファイル>")
	}
	state, planPath, specPath := args[0], args[1], args[2]
	text, err := os.ReadFile(specPath)
	if err != nil {
		return err
	}
	data, err := pyjson.Loads(string(text))
	if err != nil {
		return err
	}
	spec, ok := data.(*pyjson.Object)
	if !ok {
		return fmt.Errorf("%s: 形が合わない", specPath)
	}
	begin, _ := spec.Value("begin").(*pyjson.Object)
	plan, _ := spec.Value("plan").(*pyjson.Object)
	if begin == nil || plan == nil {
		return fmt.Errorf("%s: begin と plan が要る", specPath)
	}
	opts := oplog.Options{
		Repo:        objectOf(begin, "repo"),
		Parent:      begin.String("parent"),
		RequestHead: begin.String("request_head"),
		Choices:     objectOf(begin, "choices"),
		Plan:        objectOf(begin, "plan"),
	}
	opts.Phase, _ = pyfmt.IntOf(begin.Value("phase"))
	opts.MR, _ = pyfmt.IntOf(begin.Value("mr"))
	opts.Shown, _ = begin.Value("shown").([]any)
	stages, _ := begin.Value("stages").([]any)
	for _, s := range stages {
		pair, _ := s.([]any)
		if len(pair) != 2 {
			return fmt.Errorf("stages は [段階ID, 種類] の並び")
		}
		opts.Stages = append(opts.Stages, oplog.StageSpec{ID: fmt.Sprint(pair[0]), Kind: oplog.StageKind(fmt.Sprint(pair[1]))})
	}
	id, err := oplog.Begin(state, oplog.Kind(begin.String("kind")), opts)
	if err != nil {
		return err
	}
	out, err := pyjson.Dumps(plan.With("op", id), pyjson.Options{})
	if err != nil {
		return err
	}
	return os.WriteFile(planPath, []byte(out), 0o644)
}

func objectOf(o *pyjson.Object, key string) *pyjson.Object {
	v, _ := o.Value(key).(*pyjson.Object)
	return v
}
