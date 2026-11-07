package cli

import (
	"errors"
	"fmt"
	"io"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/oplog"
	"ccnavi/internal/tickets/review/decide"
)

const (
	opsUsage = "ccnavi: ops の使い方: pending / show <操作ID> / " +
		"stage-set <操作ID> <段階ID> <pending|sent|unknown> [URL] [アカウント] / " +
		"begin <種類> <親> <フェーズ> <MR> <キー> <段階ID:種類>… / applied <操作ID> <キー> <値> / " +
		"reconcile <操作ID> <段階ID> <URL,…> <yes|no|error> [アカウント] / " +
		"done <操作ID> / retire <親>\n"
	opsMissing  = "ccnavi: 操作記録 %s が無い\n"
	opsOutState = "ccnavi: 書き先は state の置き場の下に限る"
	// keyFromFile はキーの中身をファイルから取る印（本文そのものをキーにして、衝突を避ける）。
	keyFromFile = "@"
)

// opsCommand は `ccnavi ops <副命令>`。操作記録（`logs/state/ops/`）を読み書きする。sh が送信の段階を記録するために
// 呼ぶ。書く副命令（stage-set・done）は、エージェントが偽の送信済みを書けないよう、実行ファイルを直に呼ぶ形を hook が
// 止める。ネットワークにも git にも触れない。
func opsCommand(stdout, stderr io.Writer, conf *config.Settings, words []string) int {
	if conf.State == "" {
		fmt.Fprintln(stderr, "ccnavi: state の置き場が空。操作記録を扱えない")
		return config.ExitError
	}
	code, handled, err := opsStep(stdout, stderr, conf.State, words)
	var opErr *oplog.Error
	switch {
	case errors.As(err, &opErr):
		fmt.Fprintf(stderr, "ccnavi: %s\n", opErr)
		return config.ExitError
	case err != nil:
		return finish(stderr, 0, err)
	case !handled:
		io.WriteString(stderr, opsUsage)
		return config.ExitError
	}
	return code
}

// opsStep は副命令 1 つ。形が合わなければ handled が偽。
func opsStep(stdout, stderr io.Writer, state string, words []string) (code int, handled bool, err error) {
	verb := ""
	if len(words) > 0 {
		verb = words[0]
	}
	arg := func(i int) string {
		if i < len(words) {
			return words[i]
		}
		return ""
	}
	n := len(words)
	switch {
	case verb == "pending" && n == 1:
		for _, r := range oplog.Pending(state) {
			if r.Broken != "" {
				fmt.Fprintf(stdout, "%s broken\n", r.ID)
				continue
			}
			var left []string
			for _, s := range r.Unfinished() {
				left = append(left, s.ID)
			}
			fmt.Fprintf(stdout, "%s %s %s\n", r.ID, r.Kind, strings.Join(left, ","))
		}
		return config.ExitOK, true, nil
	case verb == "show" && n == 2:
		r, err := loadRecord(stderr, state, words[1])
		if r == nil {
			return config.ExitError, true, err
		}
		if err := pyjson.Write(stdout, r.Object(), pyjson.Options{RawUnicode: true}); err != nil {
			return 0, true, err
		}
		return config.ExitOK, true, nil
	case verb == "stage-set" && n >= 4 && n <= 6:
		return config.ExitOK, true, oplog.StageSet(state, words[1], words[2], oplog.State(words[3]), arg(4), arg(5))
	case verb == "applied" && n == 4:
		return config.ExitOK, true, oplog.AppliedSet(state, words[1], words[2], appliedValue(words[3]))
	case verb == "reconcile" && (n == 5 || n == 6):
		r, err := loadRecord(stderr, state, words[1])
		if r == nil {
			return config.ExitError, true, err
		}
		var found []string
		for _, u := range strings.Split(words[3], ",") {
			if u != "" {
				found = append(found, u)
			}
		}
		listing := map[string]oplog.Listing{"yes": oplog.ListedAll, "no": oplog.ListedPart}[words[4]]
		if words[4] != "yes" && words[4] != "no" {
			listing = oplog.ListFailed
		}
		verdict, why := oplog.Reconcile(r, words[2], found, listing, arg(5), time.Now())
		fmt.Fprintln(stdout, strings.TrimRight(string(verdict)+" "+why, " "))
		return config.ExitOK, true, nil
	case verb == "begin" && n >= 7:
		return opsBegin(stdout, stderr, state, words)
	case verb == "post-text" && n == 4:
		return opsPostText(stderr, state, words)
	case verb == "resume-plan" && n == 3:
		r, err := loadRecord(stderr, state, words[1])
		if r == nil {
			return config.ExitError, true, err
		}
		if !inStateDir(state, words[2]) {
			fmt.Fprintln(stderr, opsOutState)
			return config.ExitError, true, nil
		}
		return okOrError(decide.ResumePlan(r, words[2])), true, nil
	case verb == "mark-sent" && n == 8:
		// mark-sent <種類> <親> <MR> <キー> <段階ID> <URL>: 目印で見つけた投稿を送り済みにする
		key := words[4]
		if path, ok := strings.CutPrefix(key, keyFromFile); ok {
			key, _ = fsio.ReadText(path, fsio.Strict)
		}
		if key == "" {
			fmt.Fprintln(stderr, "ccnavi: キーにする本文を読めない")
			return config.ExitError, true, nil
		}
		_, err := oplog.MarkSent(state, oplog.Kind(words[1]), words[2], words[3], key, words[5], words[6])
		return config.ExitOK, true, err
	case verb == "retire" && n == 2:
		for _, id := range oplog.RetireParent(state, words[1]) {
			fmt.Fprintln(stdout, id)
		}
		return config.ExitOK, true, nil
	case verb == "done" && n == 2:
		finished, err := oplog.CompleteIfDone(state, words[1])
		if err != nil {
			return 0, true, err
		}
		if finished {
			fmt.Fprintln(stdout, "done")
		} else {
			fmt.Fprintln(stdout, "open")
		}
		return config.ExitOK, true, nil
	}
	return 0, false, nil
}

// loadRecord は操作記録を読む。無ければ言って nil。
func loadRecord(stderr io.Writer, state, id string) (*oplog.Record, error) {
	r, err := oplog.Load(state, id)
	if err != nil {
		return nil, err
	}
	if r == nil {
		fmt.Fprintf(stderr, opsMissing, id)
	}
	return r, nil
}

// opsBegin は `begin <種類> <親> <フェーズ> <MR> <キー> <段階ID:種類>…`。キーから決めた ID で記録を作り（既にあれば
// 作らない）、ID を出す。エージェントの review-request・comment・ready が、打ち直しで同じ記録に当たるため。
func opsBegin(stdout, stderr io.Writer, state string, words []string) (int, bool, error) {
	kind, parent, phaseText, mrText, key := words[1], words[2], words[3], words[4], words[5]
	if path, ok := strings.CutPrefix(key, keyFromFile); ok {
		body, _ := fsio.ReadText(path, fsio.Strict)
		if body == "" {
			// 読めない・空のときにパスで代えると、本文が違っても同じ操作になってしまう
			fmt.Fprintf(stderr, "ccnavi: キーにする本文のファイルを読めない（%s）\n", path)
			return config.ExitError, true, nil
		}
		key = body
	}
	var stages []oplog.StageSpec
	for _, spec := range words[6:] {
		id, kindText, _ := strings.Cut(spec, ":")
		stage := oplog.StageKind(kindText)
		if stage == "" {
			stage = oplog.StageComment
		}
		stages = append(stages, oplog.StageSpec{ID: id, Kind: stage})
	}
	id, err := oplog.Begin(state, oplog.Kind(kind), oplog.Options{Parent: parent, Phase: digits(phaseText),
		MR: digits(mrText), Stages: stages, ID: oplog.IDFor(kind, parent, phaseText, mrText, key)})
	if err != nil {
		return 0, true, err
	}
	fmt.Fprintln(stdout, id)
	return config.ExitOK, true, nil
}

// digits は数字だけの語の数（Python の int(x) if x.isdigit() else 0）。
func digits(text string) int {
	if !pyfmt.IsDigit(text) {
		return 0
	}
	n, _ := pyfmt.ParseInt(text)
	return n
}

// opsPostText は `post-text <操作ID> <段階ID> <ファイル>`。段階で投稿する本文を、保存した判断から作り直して書く。
func opsPostText(stderr io.Writer, state string, words []string) (int, bool, error) {
	r, err := loadRecord(stderr, state, words[1])
	if r == nil {
		return config.ExitError, true, err
	}
	if r.Kind != oplog.KindDecide || r.Applied == nil || !pyfmt.Truthy(r.Applied.Value("local")) {
		fmt.Fprintln(stderr, "ccnavi: この記録は、まだ適用が済んでいない（投稿の本文は作れない）")
		return config.ExitError, true, nil
	}
	text, ok := decide.PostText(r, words[2])
	if !ok {
		fmt.Fprintf(stderr, "ccnavi: 段階 %s の本文は作れない\n", words[2])
		return config.ExitError, true, nil
	}
	if !inStateDir(state, words[3]) {
		fmt.Fprintln(stderr, opsOutState)
		return config.ExitError, true, nil
	}
	if failed := fsio.WriteText(words[3], text, fsio.LF, fsio.Plain); failed != "" {
		fmt.Fprintf(stderr, "ccnavi: 本文を %s に書けない（%s）\n", words[3], failed)
		return config.ExitError, true, nil
	}
	return config.ExitOK, true, nil
}

// appliedValue は `applied` の値（true・false は真偽、ほかは文字列）。
func appliedValue(text string) any {
	switch text {
	case "true":
		return true
	case "false":
		return false
	}
	return text
}

// inStateDir は出力先が state の置き場の下か。
func inStateDir(state, path string) bool { return inState(&config.Settings{State: state}, path) }
