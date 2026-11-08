package cli

import (
	"cmp"
	"fmt"
	"io"
	"slices"

	"ccnavi/internal/entry/status"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/tickets/approval/wap"
	"ccnavi/internal/tickets/core"
	"ccnavi/internal/tickets/learning"
	"ccnavi/internal/tickets/lifecycle/branch"
	"ccnavi/internal/tickets/lifecycle/consolidate"
	"ccnavi/internal/tickets/lifecycle/ops"
	"ccnavi/internal/tickets/lifecycle/retire"
	"ccnavi/internal/tickets/lifecycle/worktrees"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/useronly"
	"ccnavi/internal/tickets/model/via"
	"ccnavi/internal/tickets/phase"
	"ccnavi/internal/tickets/proposal/propose"
	"ccnavi/internal/tickets/proposal/scaffold"
	"ccnavi/internal/tickets/review/closeout"
	"ccnavi/internal/tickets/review/decide"
	"ccnavi/internal/tickets/review/request"
)

const inStateOnly = "ccnavi: %s には、状態保存ディレクトリ配下のパスを指定してください。\n"

// operate はチケットの状態とレビューの操作を振り分ける（`ticket …`・`review …`・`worktree …` と、ユーザが打つ
// `--reviewed`・`--close-early`）。リモートから取得した結果は `--result <json>` で受け取る。
func operate(in *input, stdout, stderr io.Writer, conf *config.Settings, root string, a opts) int {
	cwd := cwdOf(a)
	words := a.command()
	if len(words) > 0 && words[0] == "worktree" {
		// ワークツリーの片付けはチケット制御が無くても打てる。tidy はチケットを読むので、チケット制御が要る。
		return worktree(stdout, stderr, conf, root, a, words, cwd)
	}
	if a.head("ticket", "next-id") {
		// 番号を答えるだけ。0 は成功、1 は識別子にできない形、2 は数えきれなかった。
		code := branch.RunNextID(stdout, stderr, conf, root, words[2:])
		if code == 0 || code == 2 {
			return code
		}
		return config.ExitError
	}
	if !conf.TicketsEnabled() {
		io.WriteString(stderr, ticketControlOff)
		return config.ExitError
	}
	// write-and-push を通らずに、取り込み済みの親子の状態を書こうとしていれば、何も書かずに止める。
	if bypass := writeAndPushBypass(root, conf, a, cwd); bypass != "" {
		fmt.Fprintf(stderr, "ccnavi: 親子のチケット %s は取り込み済み（write-and-push の対象）。状態の操作は '%s' か '%s' から"+
			"実行してください。write-and-push を通らない書き込みは親のブランチへ送られないので、何も反映せずに中止しました。\n",
			bypass, config.ScriptCommand(root, config.TicketWrapper), config.ScriptCommand(root, config.ReviewWrapper))
		return config.ExitError
	}
	// ユーザの判断の操作（decide・close-early）を先に振り分ける。どれにも当たらなければ、`ticket`・`review` の動詞として読む。
	reviewed, hasReviewed := a.Int("reviewed")
	acceptUnresolved := a.Truthy("accept_unresolved")
	switch {
	case a.Truthy("close_early"):
		return closeEarly(in, stdout, stderr, conf, root, a, cwd)
	case hasReviewed && acceptUnresolved && a.Truthy("check"):
		// 置く前に分かる前提だけ（読むだけ）。sh が取り込み・ロックの前に呼ぶ。
		return okIf(decide.Check(stdout, stderr, conf, root, cwd, reviewed, a.Str("yes"), a.Str("digest")) == 0)
	case hasReviewed && acceptUnresolved && (a.Truthy("preview") || a.Truthy("yes")):
		return decideBoard(stdout, stderr, conf, root, a, cwd, reviewed)
	case hasReviewed && a.Str("choose_out") != "":
		// 対話の decide の前半である。ロックを持ったままユーザを待たないように分けている。
		// 選択とダイジェストを state の置き場に書くだけである。置き場への配置は、sh が write-and-push の中で行う。
		if !inState(conf, a.Str("choose_out")) {
			fmt.Fprintf(stderr, inStateOnly, "--choose-out の出力先")
			return config.ExitError
		}
		return okIf(decide.Choose(in.buf, stdout, stderr, conf, root, cwd, reviewed, a.Str("result"), a.Str("choose_out")) == 0)
	case hasReviewed:
		via.SetRoute(via.Terminal)
		decideActor(a)
		code, err := decide.Reviewed(in.buf, stdout, stderr, conf, root, cwd, reviewed, acceptUnresolved, a.Str("result"))
		return finishOK(stderr, code, err)
	}
	code, err := ticketOrReview(stdout, stderr, conf, root, a, words, cwd)
	return finishOK(stderr, code, err)
}

// finishOK は error を返す操作の終わり方（0 を ExitOK、ほかを ExitError に寄せる）。
func finishOK(stderr io.Writer, code int, err error) int {
	if err != nil {
		return finish(stderr, code, err)
	}
	return okIf(code == 0)
}

// closeEarly はユーザがターミナルで打つ、親を早めに閉じる操作。`--agree`・`--reviewed` と同じくターミナルを求める。
func closeEarly(in *input, stdout, stderr io.Writer, conf *config.Settings, root string, a opts, cwd string) int {
	if a.Str("result") == "" {
		fmt.Fprintln(stderr, "ccnavi: --close-early を実行するには、--reason <理由> と --result <json> を指定してください。")
		return config.ExitError
	}
	if !fromTerminal(in, conf, stderr, useronly.OpCloseEarly) {
		return config.ExitError
	}
	via.SetRoute(via.Terminal)
	if refused := outsideState(conf, a, "--accept-plan", "--accept-done", "--accept-check"); refused != "" {
		fmt.Fprintf(stderr, inStateOnly, refused)
		return config.ExitError
	}
	code, err := closeout.CloseEarly(in.buf, stdout, stderr, conf, root, cwd, a.Str("reason"), a.Str("result"),
		closeout.EarlyOptions{PlanOut: a.Str("accept_plan"), PlanDone: a.Str("accept_done"),
			PlanCheck: a.Str("accept_check"), NoIssue: a.Truthy("no_issue")})
	return finishOK(stderr, code, err)
}

// decideBoard はボードから打つ decide（見るだけの `--preview` と、押した選択の `--yes`）。
func decideBoard(stdout, stderr io.Writer, conf *config.Settings, root string, a opts, cwd string, reviewed int) int {
	preview, yes := a.Truthy("preview"), a.Str("yes")
	if preview && yes != "" {
		fmt.Fprintln(stderr, "ccnavi: --preview と --yes は同時に指定できません。")
		return config.ExitError
	}
	if !a.Truthy("json") || a.Str("result") == "" {
		fmt.Fprintln(stderr, "ccnavi: ボードから実行する場合は、--json と --result <json> を指定してください。")
		return config.ExitError
	}
	if refused := outsideState(conf, a, "--accept-plan", "--accept-done"); refused != "" {
		fmt.Fprintf(stderr, inStateOnly, refused)
		return config.ExitError
	}
	via.SetRoute(via.Board)
	decideActor(a)
	if preview {
		return okIf(decide.Preview(stdout, stderr, conf, root, cwd, reviewed, a.Str("result")) == 0)
	}
	code, err := decide.Yes(stdout, stderr, conf, root, cwd, reviewed, a.Str("result"), yes, a.Str("digest"),
		decide.YesOptions{PlanOut: a.Str("accept_plan"), PlanDone: a.Str("accept_done")})
	return finishOK(stderr, code, err)
}

// outsideState は、渡されたパスが state の置き場の外にあるフラグ（最初の 1 つ）。
func outsideState(conf *config.Settings, a opts, flags ...string) string {
	for _, flag := range flags {
		if path := a.Str(destOf(flag)); path != "" && !inState(conf, path) {
			return flag
		}
	}
	return ""
}

// inState は出力先が state の置き場の下か。
func inState(conf *config.Settings, path string) bool {
	return inside(realPath(ospath.Abspath(path)), realPath(conf.State))
}

// decideActor は decide のマーカーに入れるアカウントと経路。`--actor` があればアカウントを足し、`--via` があれば
// 経路を差し替える。無ければマーカーは前と同じ。
func decideActor(a opts) {
	actor := a.Str("actor")
	if actor == "" {
		return
	}
	via.SetActor(actor)
	if route := a.Str("via"); route != "" {
		via.SetRoute(via.Route(route))
	}
}

// ticketOrReview は `ticket <動詞> …` と `review <動詞> …`。形が合わなければ話題の使い方を出して 1。
func ticketOrReview(stdout, stderr io.Writer, conf *config.Settings, root string, a opts, words []string,
	cwd string) (int, error) {
	at := func(i int) string {
		if i < len(words) {
			return words[i]
		}
		return ""
	}
	// words は `<ticket|review> <動詞> <対象>…`。足りない語は空として扱う。
	kind, verb, target := at(0), at(1), at(2)
	number, hasPhase := a.Int("phase")
	check := a.Truthy("check")
	switch {
	case kind == "ticket" && verb == "status":
		// 読むだけ（サブエージェントにも許す）。親は 1 件だけ（照合は status が持つ）。
		return status.Run(stdout, stderr, root, conf, words[2:]), nil
	case kind == "ticket" && verb == "approval-request" && target != "":
		// 親子の提案を、書いたパスの一覧に載せるだけ。コミットと push は sh が write-and-push の手順で行う。
		return propose.Run(stdout, stderr, conf, root, target, check, func(idents []string) int {
			return core.ApprovalRequestGate(stderr, conf, root, idents)
		}), nil
	case kind == "ticket" && slices.Contains([]string{"start", "finish", "cancel"}, verb) && target != "":
		// --check は副作用の前の検査だけ。cancel は理由を渡し、他の 2 つは対象だけを渡す。
		switch {
		case check:
			// 副作用の前に分かる検査だけ。sh が取り込みやコミットの前に呼ぶ。
			return ops.Check(stdout, stderr, conf, root, ops.Verb(verb), target, a.Str("reason")), nil
		case verb == "start":
			return ops.Start(stdout, stderr, conf, root, target), nil
		case verb == "finish":
			return ops.Finish(stdout, stderr, conf, root, target), nil
		}
		return ops.Cancel(stdout, stderr, conf, root, target, a.Str("reason"), true), nil
	case kind == "ticket" && verb == "consolidate" && target != "":
		if check {
			return consolidate.Check(stdout, stderr, conf, root, target), nil
		}
		return consolidate.Run(stdout, stderr, conf, root, target), nil
	case kind == "ticket" && verb == "learn" && len(words) <= 3:
		return learning.Run(stdout, stderr, conf, root, cwd, learning.Note{Ident: target, Note: a.Str("note"),
			Target: a.Str("target"), Signal: a.Str("signal")}), nil
	case kind == "ticket" && verb == "revise":
		if len(words) != 3 {
			fmt.Fprintln(stderr, "ccnavi: ticket revise <識別子> [--feedback]")
			return 1, nil
		}
		return scaffold.Revise(stdout, stderr, conf, root, cwd, target, a.Truthy("feedback")), nil
	case kind == "ticket" && verb == "new":
		if len(words) != 3 {
			fmt.Fprintln(stderr, "ccnavi: ticket new <識別子> --title <タイトル> --plan <型,型,...> [--scope <glob>]"+
				" --issue <N|owner/repo#N> --mr <N> か --template <名前> [--layer <名前>] [--with-feedback]")
			return 1, nil
		}
		req := scaffold.Request{ID: target, Title: a.Str("title"), Plan: a.Str("plan"), Template: a.Str("template"),
			Layer: a.Str("layer"), WithFeedback: a.Truthy("with_feedback"), Scope: a.Strings("scope")}
		if !a.IsNone("issue") {
			issue := a.Str("issue")
			req.Issue = &issue
		}
		if mr, ok := a.Int("mr"); ok {
			req.MR = &mr
		}
		return scaffold.New(stdout, stderr, conf, root, cwd, req), nil
	case kind == "ticket" && verb == "record-risk":
		if len(words) < 5 {
			fmt.Fprintln(stderr, "ccnavi: ticket record-risk には <子> <項目> yes|no と --reason <根拠> を指定してください。")
			return 1, nil
		}
		return ops.RecordRisk(stdout, stderr, conf, root, words[2], words[3], words[4], a.Str("reason")), nil
	case kind == "review" && verb == "prepare":
		switch {
		case !hasPhase || a.Str("body_file") == "":
			fmt.Fprintln(stderr, "ccnavi: review prepare には --phase <N> と --body-file <path> を指定してください。")
			return 1, nil
		case check:
			return request.Check(stdout, stderr, conf, root, cwd, number, a.Str("body_file")), nil
		}
		return request.Prepare(stdout, stderr, conf, root, cwd, number, a.Str("body_file")), nil
	case kind == "review" && verb == "confirm" && check:
		if !hasPhase {
			fmt.Fprintln(stderr, "ccnavi: review confirm --check には --phase <N> を指定してください。")
			return 1, nil
		}
		return core.CheckConfirm(stdout, stderr, conf, root, cwd, number), nil
	case kind == "review" && verb == "ready" && check:
		return closeout.CheckReady(stdout, stderr, conf, root, cwd), nil
	// ここから下の review の操作は、リモートから取得した結果（--result）を受けて書く。
	case kind == "review" && (verb == "requested" || verb == "confirm"):
		if !hasPhase || a.Str("result") == "" {
			fmt.Fprintf(stderr, "ccnavi: review %s には --phase <N> と --result <json> を指定してください。\n", verb)
			return 1, nil
		}
		if verb == "requested" {
			return request.Requested(stdout, stderr, conf, root, cwd, number, a.Str("result"))
		}
		return core.ConfirmLocal(stdout, stderr, conf, root, cwd, number, a.Str("result"), a.Str("actor"))
	case kind == "review" && verb == "ready":
		if a.Str("result") == "" {
			fmt.Fprintln(stderr, "ccnavi: review ready には --result <json> を指定してください。")
			return 1, nil
		}
		return closeout.Ready(stdout, stderr, conf, root, cwd, a.Str("result"))
	}
	io.WriteString(stderr, topicOrOverview(kind))
	return 1, nil
}

const worktreeUsage = "ccnavi: worktree drop <名前> か worktree clean <名前> [--preview] か worktree tidy <親> [--phase <N>]" +
	" か worktree remaining <親> か worktree retire <親> か worktree retire --abandon <親>" +
	" か worktree branch <名前> [confirmed] か worktree name <名前> か worktree id <ブランチ>" +
	" か worktree issue-id <番号> <先頭の語> [<プロジェクト>] --title <タイトル>\n"

// worktree は `worktree drop|clean|tidy|remaining|retire|branch|name|id|issue-id …`。sh が状態を書く操作の後に呼ぶ。
// 名前は、clean 以外は識別子の形だけを受ける（パスを渡して任意の場所を消させない。clean は RunClean が置き場の直下の
// 名前かを見る）。
func worktree(stdout, stderr io.Writer, conf *config.Settings, root string, a opts, words []string, cwd string) int {
	verb := ""
	if len(words) > 1 {
		verb = words[1]
	}
	// 1. 放棄した親の記録を消す形（retire --abandon）と、引数が名前だけで済む動詞（name・id・issue-id・branch・clean）。
	if !a.IsNone("abandon") {
		// 放棄した親の片付けの記録を消す。ユーザがターミナルで打つ（hook がエージェントからは止める）。
		if !slices.Equal(words, []string{"worktree", "retire"}) || !ids.IsValidID(a.Str("abandon")) {
			fmt.Fprintln(stderr, "ccnavi: worktree retire --abandon <親> の形で指定してください（親は有効な識別子）。")
			return config.ExitError
		}
		return okIf(retire.RunAbandon(stdout, stderr, conf, root, a.Str("abandon")) == 0)
	}
	switch {
	case verb == "name" && len(words) == 3:
		// 直接作業の名前の形を言う（warn の 1 行）。sh の作成の後に呼ぶ。
		if text := ids.DirectNameWarning(words[2], conf.BranchPrefixes, root); text != "" {
			fmt.Fprintf(stdout, "warn: %s\n", text)
		}
		return config.ExitOK
	case verb == "id" && len(words) == 3:
		// ブランチ名から識別子を作り、使えるかを言う（字の種類・NFC・長さはここで判定する）。
		for _, line := range ids.IDAnswer(words[2]) {
			fmt.Fprintln(stdout, line)
		}
		return config.ExitOK
	case verb == "issue-id" && (len(words) == 4 || len(words) == 5):
		return issueID(stdout, stderr, a, words)
	case verb == "branch" && (len(words) == 3 || len(words) == 4 && words[3] == "confirmed"):
		// 親を閉じたときに片付ける子として記録したブランチを、条件を確かめ直して消す。
		if !ids.IsValidID(words[2]) {
			fmt.Fprintf(stderr, "ccnavi: worktree branch の %s は有効な識別子の形式ではありません。\n", pyfmt.Repr(words[2]))
			return config.ExitError
		}
		return okIf(retire.RunBranch(stdout, stderr, conf, root, words[2], len(words) == 4) == 0)
	case verb == "clean" && len(words) == 3:
		return okIf(worktrees.RunClean(stdout, stderr, root, words[2], a.Truthy("preview")) == 0)
	}
	// 2. 親の識別子を取る動詞（drop・tidy・remaining・retire）。識別子の形を確かめてから、動詞ごとの処理へ渡す。
	if !slices.Contains([]string{"drop", "tidy", "remaining", "retire"}, verb) || len(words) != 3 {
		io.WriteString(stderr, worktreeUsage)
		return config.ExitError
	}
	target := words[2]
	if !ids.IsValidID(target) {
		fmt.Fprintf(stderr, "ccnavi: worktree %s の %s は有効な識別子の形式ではありません。\n", verb, pyfmt.Repr(target))
		return config.ExitError
	}
	// drop はチケット制御が無くても打てる。ほかの動詞はチケットを読むので、チケット制御が要る。
	if verb == "drop" {
		return okIf(worktrees.RunDrop(stdout, root, target, cwd) == 0)
	}
	if !conf.TicketsEnabled() {
		io.WriteString(stderr, ticketControlOff)
		return config.ExitError
	}
	switch verb {
	case "remaining":
		return okIf(worktrees.RunRemaining(stdout, conf, root, target) == 0)
	case "retire":
		return okIf(retire.RunRetire(stdout, stderr, conf, root, target, cwd) == 0)
	}
	var phaseNo *int
	if n, ok := a.Int("phase"); ok {
		phaseNo = &n
	}
	return okIf(worktrees.RunTidy(stdout, conf, root, target, phaseNo, cwd) == 0)
}

// issueID は `worktree issue-id <番号> <先頭の語> [<プロジェクト>] --title <タイトル>`。issue から新しい親のブランチ名
// （= 識別子）を作る（Chrome の「始める」と同じ関数）。
func issueID(stdout, stderr io.Writer, a opts, words []string) int {
	number := 0
	if pyfmt.IsDigit(words[2]) {
		n, err := pyfmt.ParseInt(words[2])
		if err != nil {
			fmt.Fprintf(stderr, "ccnavi: %s\n", err)
			return config.ExitError
		}
		number = n
	}
	project := ""
	if len(words) == 5 {
		project = words[4]
	}
	ident, shortened, err := ids.IssueIdentifier(number, a.Str("title"), project, words[3])
	if err != nil {
		fmt.Fprintf(stderr, "ccnavi: %s\n", err)
		return config.ExitError
	}
	fmt.Fprintf(stdout, "id %s\n", ident)
	if shortened {
		// タイトルが長くて slug を短くした。ccnavi-start.sh は短い --slug を付けさせる。
		fmt.Fprintln(stdout, "long yes")
	}
	return config.ExitOK
}

// checkable は `--check` で検査だけを通せる状態の操作（書かないので write-and-push を通らなくてよい）。
var checkable = [][]string{
	{"ticket", "start"}, {"ticket", "finish"}, {"ticket", "cancel"}, {"ticket", "approval-request"},
	{"ticket", "consolidate"}, {"review", "confirm"}, {"review", "ready"},
}

// writeAndPushBypass は、write-and-push を通らずに取り込み済みの親子のチケットの状態を書こうとしているときの、
// その親の識別子を返す。write-and-push の対象の親子のチケットでは、
// 状態の操作を sh が `--record-tree` を付けて起こす。付いていなければ、sh の引数の読み違いか直打ちで、
// write-and-push を通っていないことになる。ユーザの判断の操作（`--close-early`）と書かない形は見ない。
func writeAndPushBypass(root string, conf *config.Settings, a opts, cwd string) string {
	words := a.command()
	if a.Str("record_tree") != "" {
		return ""
	}
	if a.Truthy("check") && len(words) >= 2 && slices.ContainsFunc(checkable, func(w []string) bool {
		return slices.Equal(words[:2], w)
	}) {
		return ""
	}
	family := ""
	switch {
	case len(words) >= 3 && words[0] == "ticket" &&
		slices.Contains([]string{"start", "finish", "cancel", "approval-request", "consolidate"}, words[1]):
		family = wap.FamilyOf(words[2])
	case len(words) >= 2 && words[0] == "review" && slices.Contains([]string{"requested", "confirm", "ready"}, words[1]),
		decideWrites(a):
		if cwd != "" {
			if parent, _ := phase.ParentAt(conf, root, cwd, nil); parent != nil {
				family = cmp.Or(parent.Parent, parent.ID)
			}
		}
	}
	if family == "" || !ids.IsValidID(family) {
		return ""
	}
	if verdict, _, _ := wap.TargetOf(conf, root, family); verdict == wap.Yes {
		return family
	}
	return ""
}
