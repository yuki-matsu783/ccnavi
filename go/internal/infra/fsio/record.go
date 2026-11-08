package fsio

import (
	"os"

	"ccnavi/internal/infra/ospath"
)

// Recorder は書いた・消したパスを集める。write-and-push はこの一覧のパスだけをコミットするので、
// 置き場を書くコードはここの関数だけを通す（素の os の書き込みを使わない）。一覧に漏れた場所は、
// 書いたのにコミットされないまま残る。
type Recorder struct {
	seen  map[string]bool
	paths []string
}

// recorders は記録中の Recorder。入れ子にすると外側にも同じものが入る。
var recorders []*Recorder

// StartRecording は、Stop を呼ぶまでに書いた・消したパスを集め始める。
func StartRecording() *Recorder {
	r := &Recorder{seen: map[string]bool{}}
	recorders = append(recorders, r)
	return r
}

// Stop は記録をやめ、集めたパス（絶対パス、書いた順、重複なし）を返す。
func (r *Recorder) Stop() []string {
	for i, other := range recorders {
		if other == r {
			recorders = append(recorders[:i], recorders[i+1:]...)
			break
		}
	}
	return r.paths
}

func record(path, failed string) string {
	if failed != "" || len(recorders) == 0 {
		return failed
	}
	// 行き着く先のパスで記録する（ディレクトリのリンクを解く。macOS の /var → /private/var など）。
	// 最後の名前は解かない（消したファイル・リンクそのものを書いたときも、その名前で数える）。
	key := ospath.Normpath(ParentResolved(ospath.Abspath(path)))
	for _, r := range recorders {
		if !r.seen[key] {
			r.seen[key] = true
			r.paths = append(r.paths, key)
		}
	}
	return ""
}

// NoteInput は読んだ入力を、書いたものと同じく一覧に載せる（在るときだけ。リンクは載せない）。
// 読んだ側の write-and-push がコミットして push する記録（record-risk の `<子>.judge.json`）に使う。
func NoteInput(path string) {
	if len(recorders) == 0 {
		return
	}
	if info, err := os.Lstat(path); err == nil && info.Mode()&os.ModeSymlink == 0 {
		record(path, "")
	}
}

// NoteRemoved はもう無いパスを、消したものとして一覧に載せる（無いときだけ）。
// ワークツリーから先に消えていた追跡済みのファイルの削除も、write-and-push のコミットに含めるため。
func NoteRemoved(path string) {
	if len(recorders) > 0 && !ospath.DiskLexists(path) {
		record(path, "")
	}
}
