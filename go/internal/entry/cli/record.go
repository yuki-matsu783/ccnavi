package cli

import (
	"fmt"
	"io"
	"maps"
	"os"
	"strings"

	"ccnavi/internal/infra/argparse"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/useronly"
)

// recordDir は `--record-writes` の書き出し先（既定の state の置き場の下）。
const recordDir = "write-and-push"

// recordedRun は `--record-writes <ファイル>` つきの 1 回。書いたパスを集め、終わったら 1 行 1 つで書き出す
// （write-and-push の sh が読み、一覧のパスだけをコミットする）。
//
// 書き出す先は、ワークスペースルートの既定の state の置き場の下の write-and-push/ だけ。`--state` の上書きは見ない
// （上書きで置き場を動かせば、どこへでも書ける経路になる）。比べるのは行き着く先で、途中にリンクがあれば断る。
// コマンドが失敗しても、そこまでに書いたパスで一覧を書く（失敗した回も戻すのに一覧を使う）。一覧を書けなければ、
// コマンドの結果に依らず 1 で終わる。
func recordedRun(in *input, stdout, stderr io.Writer, parser *argparse.Parser, a opts, unmarked *useronly.Verb) int {
	wrapper := opts{maps.Clone(a.Namespace), a.parser}
	if !oneWrapperFlagEach(stderr, wrapper) {
		return config.ExitError
	}
	root := rootOf(wrapper)
	realRoot := realPath(root)
	state := ospath.Join(realRoot, config.DefaultState)
	where := ospath.Join(state, recordDir)
	target := resolvedTarget(a.Str("record_writes"))
	if refused := recordPlaceProblem(where, target, a.Str("record_writes")); refused != "" {
		fmt.Fprintf(stderr, "ccnavi: --record-writes の書き出し先は %s%c... の下だけ（%s）\n", where, os.PathSeparator, refused)
		return config.ExitError
	}
	// `--placed-out` の結果のファイルも載せない（承認が書いた置き場ではなく、呼び手が受け取る控え）。
	placedTarget := ""
	if p := a.Str("placed_out"); p != "" {
		placedTarget = resolvedTarget(p)
	}
	base := realRoot
	if t := a.Str("record_tree"); t != "" {
		base = realPath(ospath.Abspath(t))
	}
	// 上書きした state の置き場も一覧から外す。効くのは診断の経路だけなので、ここでもそこだけを見る
	// （見ると、一覧から外したい場所を指せてしまう）。
	movedState := state
	if diagnosing(a) {
		conf, _ := config.Load(root)
		override(conf, a)
		if conf.State != "" {
			movedState = realPath(conf.State)
		}
	}
	recorder := fsio.StartRecording()
	code := parsed(in, stdout, stderr, parser, a, unmarked)
	written := recorder.Stop()

	var lines strings.Builder
	var reals []string
	for _, path := range written {
		resolved := fsio.ParentResolved(path)
		if inside(resolved, state) || inside(resolved, movedState) || same(resolved, target) ||
			placedTarget != "" && same(resolved, placedTarget) {
			continue
		}
		rel := resolved
		if inside(resolved, base) {
			if r, ok := ospath.Relpath(resolved, base); ok {
				rel = r
			}
		}
		lines.WriteString(strings.ReplaceAll(rel, string(os.PathSeparator), "/") + "\n")
		reals = append(reals, resolved)
	}
	if failed := writeRecordFile(target, lines.String()); failed != "" {
		fmt.Fprintf(stderr, "ccnavi: 反映したパスの一覧を %s に保存できませんでした (%s)\n", target, failed)
		return config.ExitError
	}
	if a.Str("record_tree") != "" {
		if outside := outsidePlaces(root, a, base, reals); len(outside) > 0 {
			fmt.Fprintf(stderr, "ccnavi: write-and-push がコミットするのは状態だけで、置き場の外に書き込みがあった（%s）。"+
				"コミットしない\n", strings.Join(outside, ", "))
			if code != config.ExitOK {
				return code
			}
			return config.ExitError
		}
	}
	return code
}

// resolvedTarget は書き出し先の行き着く先（ディレクトリのリンクを解き、名前はそのまま）。
func resolvedTarget(given string) string {
	abs := ospath.Abspath(given)
	return ospath.Join(realPath(ospath.Dirname(abs)), ospath.Basename(abs))
}

// outsidePlaces は一覧のうち、write-and-push の置き場（承認済み・レビュー待ち）の外のもの。コミットするのは状態だけ。
// 例外は `review ready` が消した途中の作業の置き場（wip/）のファイル（消したもの、つまり今は無いものだけ）。
func outsidePlaces(root string, a opts, base string, reals []string) []string {
	conf, _ := config.Load(root)
	override(conf, a)
	tickets := conf.Tickets
	if tickets == "" {
		tickets = config.DefaultTickets
	}
	ticketsDir := strings.ReplaceAll(tickets, "/", string(os.PathSeparator))
	places := []string{realPath(conf.ApprovedDir(base)), realPath(ospath.Join(base, ticketsDir, "review"))}
	if a.head("ticket", "approval-request") {
		// 提案を送る操作だけは、承認待ちの提案（todo/）と子のフローの下書き（flows/）も置き場に数える。
		for _, name := range []string{"todo", "flows"} {
			places = append(places, realPath(ospath.Join(base, ticketsDir, name)))
		}
	}
	readying := a.head("review", "ready")
	var found []string
	for _, r := range reals {
		if inside(r, base) && anyInside(r, places) {
			continue
		}
		if readying && readyRemovedWip(r, base) {
			continue
		}
		found = append(found, r)
	}
	return found
}

func anyInside(path string, bases []string) bool {
	for _, b := range bases {
		if inside(path, b) {
			return true
		}
	}
	return false
}

// readyRemovedWip は `review ready` が消した途中の作業の置き場（wip/）の下のファイルか。ready が消すものを選ぶのと
// 同じ判定を、write-and-push のツリーからの相対に当てる。通すのはツリーの中の、今は無い（消した）ものだけ。
func readyRemovedWip(resolved, base string) bool {
	if ospath.DiskLexists(resolved) || !inside(resolved, base) || same(resolved, base) {
		return false
	}
	rel, ok := ospath.Relpath(resolved, base)
	if !ok {
		return false
	}
	parts := strings.Split(rel, string(os.PathSeparator))
	for _, p := range parts {
		if p == "" || p == "." || p == ".." {
			return false
		}
	}
	return place.InWip(strings.Join(parts, "/"))
}

// recordPlaceProblem は書き出し先が where の下でない・途中にリンクがある理由。よければ空。
func recordPlaceProblem(where, target, given string) string {
	switch {
	case ospath.DiskLexists(where) && ospath.IsLink(where):
		return "write-and-push がシンボリックリンク"
	case !inside(target, where) || same(target, where):
		return given + " は置き場の外"
	case ospath.IsLink(target):
		return given + " はシンボリックリンク"
	}
	return ""
}

// writeRecordFile は一覧を書く。同じディレクトリの一時ファイルに書いて置き換えるので、行き先のリンクは辿らない。
func writeRecordFile(target, text string) string {
	directory := ospath.Dirname(target)
	if err := fsio.MakeDirs(directory); err != nil {
		return fsio.ErrorText(err)
	}
	// 作った後にも、途中のディレクトリがリンクで外へ出ていないかを見る。
	if !same(realPath(directory), directory) {
		return "書き出し先のディレクトリがリンクを含む"
	}
	// fsio の Atomic は差し替えた後の権限を素の open に戻すが、Python（mkstemp と os.replace）は 0600 のまま残す。
	// 同じ権限にするため、ここは一時ファイルを手で作る。
	f, err := os.CreateTemp(directory, ".record.*.part")
	if err != nil {
		return fsio.ErrorText(err)
	}
	part := f.Name()
	_, err = f.WriteString(text)
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err == nil {
		err = os.Rename(part, target)
	}
	if err != nil {
		os.Remove(part)
		return fsio.ErrorText(err)
	}
	return ""
}
