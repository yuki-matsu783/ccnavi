package closeout

import (
	"fmt"
	"io"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/lifecycle/closing"
	"ccnavi/internal/tickets/lifecycle/retire"
	"ccnavi/internal/tickets/model/archive"
	"ccnavi/internal/tickets/model/host"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/standing"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/model/useronly"
	"ccnavi/internal/tickets/review/request"
)

// readyFile は Draft を外すときにマージリクエストへ残すコメントの下書き。sh が Draft を外してから投稿する。
const readyFile = "review-ready-%s.md"

// textReadyRefused は ready が断ったあとに足す `次:` の文の識別子。
const textReadyRefused = "NEXT_READY_REFUSED"

// ready の文。sh とユーザが読むので変えない。
const (
	msgNoStateNote   = "ccnavi: state の置き場が空。コメントの下書きを置く場所がありません。\n"
	msgNotYet        = "ccnavi: まだ Draft を解除できません:\n"
	msgNotYetAgain   = "ccnavi: まだ Draft を解除できません（クローズ済みのチケットは logs/archive/ へ退避済み）:\n"
	msgProblemLine   = "  - %s\n"
	msgEarlyHint     = "まだ残るものを承知で早めに閉じるなら、ユーザがターミナルで '%s --reason <理由>' を実行します。\n"
	msgParentNotDone = "親 %s の承認済みチケットが %s/%s/ にありません（いまは %s/）。先に '%s finish %s' で親を閉じ、" +
		"コミットして push してから再実行してください"
	msgReadyCheckOK = "確認: ok（ready を打てる状態。何も変えていません）\n"
	msgFailed       = "ccnavi: %s\n"
	msgRetireFailed = "ccnavi: 親を閉じた後の片付けの記録を書けないので止めました: %s。直してから ready を打ち直してください\n"
	msgArchiveFail  = "ccnavi: クローズ済みのチケットを logs/archive/ へ退避できなかった: %s\n"
	msgWipFailed    = "ccnavi: `%s/` を片付けられなかった: %s\n"
	msgMisplaced    = "。クローズ済みのチケットの退避は親のワークツリーの置き場からだけ行う" +
		"（他の親子のチケットまで削除しないため）。Draft はまだ外していない"
	workspaceRootName = "ワークスペースルート"
	noteDone          = "チケット `%s` の作業は終わり、Draft を解除した。"
	noteArchived      = "`%s/` の追跡済みのファイルは ready が削除しました（履歴から戻せる）。" +
		"クローズ済みのチケットとその記録（`%s/` の done/・phases/・flows/）は手元の `logs/archive/` へ" +
		"退避し、このブランチからは削除してある。マージするかどうかはユーザが決める。" +
		"取り込むときは squash で、途中のコミットを既定のブランチに残さない。"
	noteEarly = "（ユーザが早めにクローズした: %s）"
)

// 標準出力の行の頭（sh との契約）。
const (
	outTree    = "tree %s\n"
	outWipFrom = "wip-from %s\n"
	outWip     = "wip %s\n"
)

// Ready は Draft を外してよいかを確かめ、マーカーとコメントの下書きを置き、閉じた親子のチケットを手元の
// `logs/archive/` へ退避して wip/ の追跡済みのファイルを消す。外すのは sh（write-and-push がコミットして push
// してから）。条件は親を閉じられること（closing.CloseProblems）に、親が done/ に在ることを足したもの。
// 同じ親に 2 度打っても通る: 前の回が退避を始めていれば（MR がまだ Draft）ワークツリーの側の条件だけを見て、
// 下書きを書き直し、途中で止まった残りを移す。
func Ready(stdout, stderr io.Writer, conf *config.Settings, root, cwd, resultPath string) (int, error) {
	if archived := archivedParent(conf, root, cwd, resultPath); archived != nil {
		return readyAgain(stdout, stderr, conf, root, archived, resultPath), nil
	}
	parent := readyGate(stderr, conf, root, cwd)
	if parent == nil {
		return config.ExitError, nil
	}
	result, ok := request.ResultWithMR(stderr, resultPath)
	if !ok {
		return config.ExitError, nil
	}
	if conf.State == "" {
		fmt.Fprint(stderr, msgNoStateNote)
		return config.ExitError, nil
	}
	where, err := approval.HomeDir(conf, root, parent.ID, "", parent.Project)
	if err != nil {
		return config.ExitError, err
	}
	// 退避は親のワークツリーの置き場からだけ行う（ワークスペースルートの done/ には他の親子のチケットも在り、
	// まとめて消すことになる）。マーカーも下書きも置く前に確かめる。
	if misplaced := archivePlaceProblem(conf, root, parent, where); misplaced != "" {
		fmt.Fprintf(stderr, msgFailed, misplaced)
		return config.ExitError, nil
	}
	path, failed := readyNote(conf, parent.ID, earlyMark(where, parent.ID))
	if failed != "" {
		fmt.Fprintf(stderr, msgFailed, failed)
		return config.ExitError, nil
	}
	if !request.ParentMark(stderr, where, parent.ID, marks.Ready, pyjson.NewObject("mr", result.MR.Number, "url", result.MR.URL)) {
		return config.ExitError, nil
	}
	return archiveClosed(stdout, stderr, conf, root, parent, where, path, result.MR.Number), nil
}

// readyGate は ready の、ホストに聞かずに分かる前提。通れば親。本体と CheckReady が同じものを通る。断るときは
// 理由のあとに早めに閉じる道と `次:` を足す。MR が Draft かは見ない（sh が取ってくる）。
func readyGate(stderr io.Writer, conf *config.Settings, root, cwd string) *ticket.Ticket {
	parent := request.ParentAny(stderr, conf, root, cwd)
	if parent == nil {
		return nil
	}
	problems := closing.CloseProblems(conf, root, parent.ID, nil)
	// 親が done/ に無いまま Draft を外してマージされると、親のブランチが消えて親子の判定が決まらなくなる。
	if parent.State != place.Done {
		problems = append(problems, fmt.Sprintf(msgParentNotDone, parent.ID, conf.Approved, place.Done, parent.State,
			config.ScriptCommand(root, config.TicketWrapper), parent.ID))
	}
	problems = append(problems, request.MergeProblems(conf, root, tree.WorktreePath(root, parent.ID))...)
	if len(problems) == 0 {
		return parent
	}
	fmt.Fprint(stderr, msgNotYet)
	for _, p := range problems {
		fmt.Fprintf(stderr, msgProblemLine, p)
	}
	// 表に必ず在る操作なので、引けない誤りは起きない。
	script, _ := useronly.Terminal(useronly.OpCloseEarly)
	fmt.Fprintf(stderr, msgEarlyHint, config.ScriptCommand(root, script))
	fmt.Fprintln(stderr, texts.Say(root, textReadyRefused, texts.Values{"parent": parent.ID}, nil))
	return nil
}

// CheckReady は `review ready --check`。readyGate を通すだけで何も書かない。sh が取り込みの前に呼ぶ。前の回が
// 退避を始めている打ち直しは、Draft かどうかをホストの結果で決めるので、通して本体に任せる。
func CheckReady(stdout, stderr io.Writer, conf *config.Settings, root, cwd string) int {
	if _, restarted := restartedTree(conf, root, cwd); !restarted && readyGate(stderr, conf, root, cwd) == nil {
		return config.ExitError
	}
	fmt.Fprint(stdout, msgReadyCheckOK)
	return config.ExitOK
}

// archiveClosed は閉じた親子のチケットを退避へ移し、標準出力に下書きのパスと退避したツリーと wip/ から消したものを
// 出す。拾う親は done/ に在る親と、手元の退避に在る親（前の回が途中で止まった残り）。
func archiveClosed(stdout, stderr io.Writer, conf *config.Settings, root string, parent *ticket.Ticket, where, notePath string, mr any) int {
	if misplaced := archivePlaceProblem(conf, root, parent, where); misplaced != "" {
		fmt.Fprintf(stderr, msgFailed, misplaced)
		return config.ExitError
	}
	treeRoot := ""
	if home, ok := tree.TreeOf(root, where, conf.Projects); ok {
		treeRoot = home.Root
	}
	parents := slices.Concat(archive.ClosedParents(where), archive.ArchivedParents(root, parent.Project))
	slices.Sort(parents)
	parents = slices.Compact(parents)
	// 閉じた子は退避へ移って見えなくなるので、片付けの記録（親のワークツリーを片付けた後の列挙元）を先に書く。
	// 書けなければ退避へ進まない（記録が無いと片付け待ちの親を見失う）。打ち直せる。
	if noted := retire.Note(conf, root, parent.ID, parent.Project, treeRoot, mr); noted != "" {
		fmt.Fprintf(stderr, msgRetireFailed, noted)
		return config.ExitError
	}
	todo := archive.PlanMove(where, parents, root, parent.Project)
	if _, failed := archive.Move(root, parent.Project, where, todo, parent.ID, treeRoot); failed != "" {
		fmt.Fprintf(stderr, msgArchiveFail, failed)
		return config.ExitError
	}
	// wip/ の追跡済みのファイルも同じ流れで消す（write-and-push が一緒にコミットする）。
	var removed []string
	head := ""
	if treeRoot != "" {
		var failed string
		removed, head, failed = request.RemoveWip(treeRoot)
		if failed != "" {
			fmt.Fprintf(stderr, msgWipFailed, place.WipRoot, failed)
			return config.ExitError
		}
	}
	fmt.Fprintln(stdout, notePath)
	if treeRoot != "" {
		fmt.Fprintf(stdout, outTree, treeRoot)
	}
	if len(removed) > 0 {
		fmt.Fprintf(stdout, outWipFrom, head)
		for _, rel := range removed {
			fmt.Fprintf(stdout, outWip, rel)
		}
	}
	return config.ExitOK
}

// archivePlaceProblem は退避の元が親のワークツリー（`.claude/worktrees/<親>`）の承認済みの領域でなければ、その理由。
func archivePlaceProblem(conf *config.Settings, root string, parent *ticket.Ticket, where string) string {
	home, ok := tree.TreeOf(root, where, conf.Projects)
	expected := tree.WorktreePath(root, parent.ID)
	if ok && !home.IsMain() && standing.SameTree(ospath.Realpath(home.Root), ospath.Realpath(expected)) {
		return ""
	}
	shown := workspaceRootName
	if ok && home.Name != "" {
		shown = home.Name
	}
	return approval.MisplacedHomeText(root, parent.ID, shown) + msgMisplaced
}

// earlyMark は早めに閉じたマーカーの中身。無い・空なら nil。
func earlyMark(approvedDir, parentID string) *pyjson.Object {
	if mark, ok := marks.ReadParent(approvedDir, parentID, marks.CloseEarly); ok && mark.Len() > 0 {
		return mark
	}
	return nil
}

// readyNote は Draft を外したときのコメントの下書きを書く。パスと、書けなかった理由（書けたら空）。
func readyNote(conf *config.Settings, parentID string, early *pyjson.Object) (string, string) {
	lines := []string{host.MarkerReady, fmt.Sprintf(noteDone, parentID), fmt.Sprintf(noteArchived, place.WipRoot, conf.Approved)}
	if early != nil {
		reason := ""
		if v, has := early.Get("reason"); has {
			reason = pyfmt.Str(v)
		}
		lines = append(lines, fmt.Sprintf(noteEarly, reason))
	}
	path := ospath.Join(conf.State, fmt.Sprintf(readyFile, parentID))
	return path, request.WriteDraft(path, strings.Join(lines, "\n")+"\n")
}

// restartedTree は cwd のワークツリーが ready の退避を始めた後なら、そのツリー。作業中に親が無く、始めた目印が
// 今のツリーのもののとき。Draft かどうかは見ない（archivedParent が見る）。
func restartedTree(conf *config.Settings, root, cwd string) (tree.Tree, bool) {
	if cwd == "" {
		cwd, _ = ospath.Getwd()
	}
	t, ok := tree.TreeOf(root, cwd, conf.Projects)
	if !ok || t.IsMain() {
		return tree.Tree{}, false
	}
	if ospath.DiskLexists(approval.CopyPath(conf.ApprovedDir(t.Root), t.Name)) {
		return tree.Tree{}, false
	}
	if !archive.ReadyStarted(root, t.Project, t.Name, t.Root) {
		return tree.Tree{}, false
	}
	return t, true
}

// archivedParent は cwd のワークツリーの親が ready の退避を始めた後なら、その親。打ち直しと見るのは、sh が
// 取得した MR がまだ Draft で（外れていれば ready は終わっている）、restartedTree が真で、親が done/ に残る
// （退避が途中で止まった）か手元の退避にだけ在る（Draft を外し損ねた）ときだけ。前の回が条件を確かめている。
func archivedParent(conf *config.Settings, root, cwd, resultPath string) *ticket.Ticket {
	t, ok := restartedTree(conf, root, cwd)
	if !ok {
		return nil
	}
	result, ok := request.ReadResult(io.Discard, resultPath)
	if !ok || result.MR == nil || result.MR.Draft == nil || !*result.MR.Draft {
		return nil
	}
	done := approval.ClosedPath(conf.ApprovedDir(t.Root), t.Name)
	if ospath.DiskLexists(done) {
		copy, _ := approval.LoadCopy(done, false)
		if copy == nil || copy.Parent != "" {
			return nil
		}
		copy.Project = t.Project
		return copy
	}
	return archive.ArchivedParent(root, t.Project, t.Name)
}

// readyAgain は退避を始めた親に打ち直した ready。ワークツリーの側の条件だけを見て、下書きを書き直す。
func readyAgain(stdout, stderr io.Writer, conf *config.Settings, root string, parent *ticket.Ticket, resultPath string) int {
	treeRoot := tree.WorktreePath(root, parent.ID)
	if problems := request.MergeProblems(conf, root, treeRoot); len(problems) > 0 {
		fmt.Fprint(stderr, msgNotYetAgain)
		for _, p := range problems {
			fmt.Fprintf(stderr, msgProblemLine, p)
		}
		return config.ExitError
	}
	result, ok := request.ResultWithMR(stderr, resultPath)
	if !ok {
		return config.ExitError
	}
	if conf.State == "" {
		fmt.Fprint(stderr, msgNoStateNote)
		return config.ExitError
	}
	// 早めに閉じたマーカーは、途中で止まった回ならツリーに、移し終えていれば退避に在る。
	where := conf.ApprovedDir(treeRoot)
	early := earlyMark(where, parent.ID)
	if early == nil {
		early = earlyMark(archive.BaseDir(root, parent.Project), parent.ID)
	}
	path, failed := readyNote(conf, parent.ID, early)
	if failed != "" {
		fmt.Fprintf(stderr, msgFailed, failed)
		return config.ExitError
	}
	return archiveClosed(stdout, stderr, conf, root, parent, where, path, result.MR.Number)
}
