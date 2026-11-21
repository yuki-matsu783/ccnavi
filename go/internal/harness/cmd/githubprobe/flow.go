package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

// 実測の手順（tests/manual/github/probe_github.py の main の写し）。手順は 0〜6 の段に分け、止める所は false を返す。
// 実験場の PR・課題・ブランチは cleanup で片付ける（finish と、途中で落ちたとき）。

// flow は実測の全部。途中で止まったら、そこから先は行わない。
func (p *probe) flow() {
	if !p.setup() || !p.parentAndChildren() || !p.reviewRequest() || !p.reviewRound() ||
		!p.resolveAndDecide() || !p.readyAndOutdated() || !p.closeEarly() {
		return
	}
	p.urlToken()
}

// setup は 0: API の入口・実験場の書き込み・課題・統合先のブランチ・一時リポジトリの組み立て。
func (p *probe) setup() bool {
	p.say("== 実測の置き場: " + p.out)
	status, me := p.api("GET", "/user", "", nil, "user")
	meLogin := str(obj(me), "login")
	if !p.check("API にトークンで入れる", status == 200, detailOf(status, me, meLogin)) {
		return false
	}
	status, repo := p.api("GET", "/repos/"+p.repo, "", nil, "repo")
	push := flagOf(obj(dig(repo, "permissions")), "push")
	if !p.check("実験場に書ける", status == 200 && push, fmt.Sprintf("%s private=%v", p.repo, dig(repo, "private"))) {
		return false
	}
	p.selfReview = true
	if p.reviewerToken != "" {
		_, rme := p.api("GET", "/user", p.reviewerToken, nil, "reviewer-user")
		p.selfReview = str(obj(rme), "login") == meLogin
	}
	if p.selfReview {
		p.say("人間役は 同じアカウント")
	} else {
		p.say("人間役は 別のアカウント")
	}

	status, issue := p.api("POST", "/repos/"+p.repo+"/issues", "", map[string]any{
		"title": "ccnavi 実測 (使い捨て)", "body": "実測の元になる課題。閉じて終わり。",
	}, "issue-create")
	if !p.check("課題を立てる", status == 201, fmt.Sprintf("#%d", intOf(obj(issue), "number"))) {
		return false
	}
	p.issue = intOf(obj(issue), "number")
	p.madeIssue = append(p.madeIssue, p.issue)

	root := p.root
	must(os.MkdirAll(filepath.Join(root, ".claude"), 0o755))
	p.git(root, "init", "--quiet", "-b", "main")
	mustWrite(filepath.Join(root, "src", "keep.py"), "print(1)\n")
	mustWrite(filepath.Join(root, ".gitignore"), ".claude/\nwip/tmp/\n")
	p.commitAll(root, "init")
	p.origin = "https://github.com/" + p.repo + ".git"
	p.git(root, "remote", "add", "origin", p.origin)
	// 認証は git の設定側に置く。空文字で system / global の helper（GCM）を外し、環境変数 GITHUB_TOKEN を返す helper を足す。
	// トークンをファイルに書かない（置き去りになる）。
	p.git(root, "config", "--add", "credential.helper", "")
	p.git(root, "config", "--add", "credential.helper", "!f() { echo username=x-access-token; echo \"password=$GITHUB_TOKEN\"; }; f")
	// 親のブランチ名はチケットの識別子で決まる（i0001・i0002）。実験場に同じ名前が残っていると上書きや削除で他の人のものに触れるので、あれば止まる
	for _, name := range []string{"i0001", "i0002"} {
		st, _ := p.api("GET", "/repos/"+p.repo+"/branches/"+name, "", nil, "precheck-"+name)
		if !p.check("実験場に "+name+" ブランチが無い", st == 404, fmt.Sprintf("status=%d。手で片付けてから", st)) {
			return false
		}
	}
	// 統合先は main ではなく使い捨てのブランチにする。履歴は無関係でよい
	p.base = "ccnavi-probe-base-" + p.stamp
	pushed := p.exec([]string{"git", "push", "--quiet", "origin", "main:refs/heads/" + p.base}, root, "", p.env)
	if pushed.Code == 0 {
		p.madeBrs = append(p.madeBrs, p.base)
	}
	if !p.check("統合先の使い捨てブランチを push（認証画面なし）", pushed.Code == 0, head(p.redact(strings.TrimSpace(pushed.Stderr)), 200)) {
		return false
	}
	text, err := rulesText()
	must(err)
	mustWrite(filepath.Join(root, ".ccnavi", "config", "common", "rules.yml"), text)
	// sh はワークスペースルートを cwd から上へ探す（.ccnavi/scripts/ccnavi-common.sh を持つ親）。一時リポジトリにも scripts を置く
	must(copyTree(filepath.Join(p.scriptsSrc(), ".ccnavi", "scripts"), filepath.Join(root, ".ccnavi", "scripts")))
	return true
}

// parentAndChildren は 1: 親 1 本と子 2 本、フェーズ 1。
func (p *probe) parentAndChildren() bool {
	parent := p.worktree("i0001", "main")
	p.parent = parent
	p.propose(parent, ticket{Name: "i0001", Allow: []string{"src/*", "wip/*"}, Review: true, Title: "挨拶を足す", Issue: p.issue})
	p.propose(parent, ticket{Name: "i0001-01-01", Parent: "i0001", Phase: 1, Allow: []string{"src/a/*"}, Review: true, Title: "a を書く"})
	p.propose(parent, ticket{Name: "i0001-01-02", Parent: "i0001", Phase: 1, Allow: []string{"src/b/*"}, Review: false, Title: "b を書く"})
	p.commitAll(parent, "tickets")
	approved := p.exeCall("y\n", "user", "agree")
	p.check("--agree（親 1 子 2）", approved.Code == 0, head(p.redact(strings.TrimSpace(approved.Stderr)), 200))

	// 子より先に親に着手する（親が doing/ に無いと子は始められない）
	started := p.sh(p.script("ccnavi-ticket.sh"), parent, nil, "start", "i0001")
	p.check("ticket start i0001（親）", started.Code == 0, head(strings.TrimSpace(started.Stderr), 200))
	p.commitAll(parent, "start parent")

	children := []string{"i0001-01-01", "i0001-01-02"}
	for _, child := range children {
		tree := p.worktree(child, "i0001")
		started := p.sh(p.script("ccnavi-ticket.sh"), parent, nil, "start", child)
		p.check("ticket start "+child, started.Code == 0, head(strings.TrimSpace(started.Stderr), 200))
		last := child[len(child)-1:]
		mustWrite(filepath.Join(tree, "src", last, "work.py"), fmt.Sprintf("# %s\nprint(%s)\n", child, last))
		p.commitAll(tree, child+": work")
	}
	p.commitAll(parent, "start")

	for _, child := range children {
		done := p.sh(p.script("ccnavi-ticket.sh"), parent, nil, "finish", child)
		p.check("ticket finish "+child, done.Code == 0, head(strings.TrimSpace(done.Stderr), 200))
	}
	p.commitAll(parent, "finish")
	for _, child := range children {
		p.git(parent, "merge", "--quiet", "--no-edit", child)
	}

	said := p.hook("PostToolUse", "Bash", parent, map[string]any{"command": "ls"})
	p.check("フェーズ 1 の終わりの告知", strings.Contains(said, "フェーズ 1（作業） が終わりました"),
		strings.ReplaceAll(head(said, 120), "\n", " "))
	blocked, detail := p.agentGate(parent)
	p.check("レビュー待ちが Agent を止める", blocked, detail)

	// ready が wip/ の追跡済みのファイルを消すことを確かめるため、メモを置いてコミットする
	mustWrite(filepath.Join(parent, wipNote), "src/a と src/b を足した。\n")
	p.commitAll(parent, "wip note")

	body := p.writeOut("request-1.md", "見てほしい点\n\n- src/a と src/b を足した\n")
	early := p.sh(p.script("ccnavi-review.sh"), parent, nil, "review-request", "--phase", "1", "--body-file", body)
	p.check("push 前の request は前提で止まる",
		early.Code != 0 && strings.Contains(early.Stderr, "親ブランチの HEAD が push されていない"),
		tailRunes(early.Stderr, 120))

	pushed := p.sh(p.script("ccnavi-git.sh"), parent, nil, "push", "-u", "origin", "i0001")
	if pushed.Code == 0 {
		p.madeBrs = append(p.madeBrs, "i0001")
	}
	p.check("親の push（ラッパースクリプト経由、認証画面なし）", pushed.Code == 0,
		head(p.redact(strings.TrimSpace(pushed.Stdout+pushed.Stderr)), 120))
	status, _ := p.api("GET", "/repos/"+p.repo+"/branches/i0001", "", nil, "branch-i0001")
	p.check("GitHub に i0001 ブランチがある", status == 200, "")
	return true
}

// wipNote は ready が消すことを確かめる、wip/ の追跡済みのファイル。
const wipNote = "wip/note.md"

// reviewRequest は 2: origin の読み方と request（PR を作って投稿する）。
func (p *probe) reviewRequest() bool {
	parent := p.parent
	seen := p.sh(p.script("ccnavi-review.sh"), parent, nil, "origin")
	p.say("origin の読み方:\n" + p.redact(seen.Stdout+seen.Stderr))
	p.check("origin をホストとパスに読む",
		strings.Contains(seen.Stdout, "path="+p.repo) && strings.Contains(seen.Stdout, "api_base=https://api.github.com") && strings.Contains(seen.Stdout, "kind=github"), "")
	p.check("送り先は gh", strings.Contains(seen.Stdout, "transport=gh"), tailRunes(seen.Stdout, 60))

	body := p.writeOut("request-1.md", "見てほしい点\n\n- src/a と src/b を足した\n")
	requested := p.sh(p.script("ccnavi-review.sh"), parent, nil, "review-request", "--phase", "1", "--body-file", body)
	p.say("request:\n" + p.redact(requested.Stdout+requested.Stderr))
	p.check("request が通る（PR を作って投稿してマーカー）", requested.Code == 0, "")
	pr := p.prOf("i0001")
	if pr != nil {
		p.madePRs = append(p.madePRs, intOf(pr, "number"))
	}
	p.check("PR が Draft で作られている", pr != nil && flagOf(pr, "draft"),
		fmt.Sprintf("%s title=%s", str(pr, "html_url"), str(pr, "title")))
	p.check("PR の宛先が統合先", pr != nil && str(obj(pr["base"]), "ref") == p.base, "")
	p.check("PR の本文に Closes #課題 がある", pr != nil && strings.Contains(str(pr, "body"), fmt.Sprintf("Closes #%d", p.issue)), "")
	if pr == nil {
		return false
	}
	p.num = intOf(pr, "number")
	p.check("依頼のコメントに ccnavi:request のマーカーがある", hasMarker(p.commentsOf(p.num), "<!-- ccnavi:request i0001:1 "), "")

	again := p.sh(p.script("ccnavi-review.sh"), parent, nil, "review-request", "--phase", "1", "--body-file", body)
	p.check("2 度目の request は依頼済みで止まる", again.Code != 0 && strings.Contains(again.Stderr, "依頼済み"), "")

	fetchedRaw := p.sh(p.script("ccnavi-review.sh"), parent, nil, "fetch")
	writeOutFile(p.out, "fetch-1-empty.json", fetchedRaw.Stdout)
	f1 := fetched(fetchedRaw)
	p.check("fetch が JSON を返す（スレッドもレビューも空）",
		fetchedRaw.Code == 0 && f1.Host == "github" && f1.MR.Number == p.num && len(f1.Threads) == 0 && len(f1.Reviews) == 0,
		head(strings.TrimSpace(fetchedRaw.Stderr), 200))
	return true
}

// reviewRound は 3: レビュアーが指摘 → confirm → 解決 → confirm の前半（指摘・未解決での止まり・curl の経路・変更要求）。
func (p *probe) reviewRound() bool {
	parent, num := p.parent, p.num
	reviewer := p.reviewer()
	status, rv := p.api("POST", fmt.Sprintf("/repos/%s/pulls/%d/reviews", p.repo, num), reviewer, map[string]any{
		"event": "COMMENT",
		"body":  "全体の所感",
		"comments": []map[string]any{
			{"path": "src/1/work.py", "line": 1, "side": "RIGHT", "body": "ここは名前を変えてほしい"},
			{"path": "src/2/work.py", "line": 2, "side": "RIGHT", "body": "ここも"},
		},
	}, "review-create")
	p.check("レビュアーが行コメント 2 件つきの COMMENTED レビューを出す",
		status == 200 && str(obj(rv), "state") == "COMMENTED", fmt.Sprintf("status=%d", status))
	human := p.threads(num, "threads-after-review")
	p.check("GraphQL でスレッドが 2 本読める", len(human) == 2 && !anyResolved(human), "")

	fetchedRaw := p.sh(p.script("ccnavi-review.sh"), parent, nil, "fetch")
	writeOutFile(p.out, "fetch-2-unresolved.json", fetchedRaw.Stdout)
	f2 := fetched(fetchedRaw)
	p.check("sh が reviewThreads を読む（2 本・未解決・path と line と URL）", threadsRead(f2.Threads), jsonText(f2.Threads))
	p.check("sh がレビューを読む（COMMENTED 1 件。author は数の id）",
		len(f2.Reviews) == 1 && f2.Reviews[0].State == "COMMENTED" && digits(f2.Reviews[0].Author), jsonText(f2.Reviews))

	checked := p.sh(p.script("ccnavi-review.sh"), parent, nil, "confirm", "--phase", "1")
	p.check("未解決があると confirm は止まる", checked.Code != 0 && strings.Contains(checked.Stderr, "未解決"), head(strings.TrimSpace(checked.Stderr), 200))
	blocked, detail := p.agentGate(parent)
	p.check("その間は止まったまま", blocked, detail)

	// curl の経路（gh を外す）でも同じに読めるか
	curlEnv, err := p.envWithoutGh()
	must(err)
	seen := p.sh(p.script("ccnavi-review.sh"), parent, curlEnv, "origin")
	p.check("gh を外すと curl の経路になる", strings.Contains(seen.Stdout, "transport=curl"), tailRunes(seen.Stdout, 40))
	viaCurl := p.sh(p.script("ccnavi-review.sh"), parent, curlEnv, "fetch")
	writeOutFile(p.out, "fetch-2-unresolved-curl.json", viaCurl.Stdout)
	c2 := fetched(viaCurl)
	p.check("curl の経路の fetch が gh の経路と同じ", viaCurl.Code == 0 && sameFetch(c2, f2), head(strings.TrimSpace(viaCurl.Stderr), 200))

	if !p.selfReview {
		status, _ := p.api("POST", fmt.Sprintf("/repos/%s/pulls/%d/reviews", p.repo, num), reviewer,
			map[string]any{"event": "REQUEST_CHANGES", "body": "直してから"}, "request-changes")
		p.check("別アカウントが変更要求を出す", status == 200, fmt.Sprintf("status=%d", status))
		changed := p.sh(p.script("ccnavi-review.sh"), parent, nil, "fetch")
		writeOutFile(p.out, "fetch-3-changes-requested.json", changed.Stdout)
		checked := p.sh(p.script("ccnavi-review.sh"), parent, nil, "confirm", "--phase", "1")
		p.check("変更要求が立っていると confirm は止まる", checked.Code != 0 && strings.Contains(checked.Stderr, "変更要求"), head(strings.TrimSpace(checked.Stderr), 200))
	} else {
		p.say("自分の PR には変更要求も approve も出せないので、その段階は走らない（docstring 参照）")
	}
	p.outOfDiff(reviewer)
	return true
}

// outOfDiff は差分の外へのレビューが 422 で拒まれ、レビューごと作られないことを見る（差分外の行に付けた指摘の行き先）。
func (p *probe) outOfDiff(reviewer string) {
	num := p.num
	_, before := p.api("GET", fmt.Sprintf("/repos/%s/pulls/%d/reviews?per_page=100", p.repo, num), "", nil, "reviews-before-422")
	nBefore := -1
	if l, ok := before.([]any); ok {
		nBefore = len(l)
	}
	cases := []struct {
		label, path string
		line        int
		expect      string
	}{
		{"差分外の行", "src/1/work.py", 99, "Line could not be resolved"},
		{"差分に無いファイル", "src/keep.py", 1, "Path could not be resolved"},
	}
	for _, c := range cases {
		st, rej := p.api("POST", fmt.Sprintf("/repos/%s/pulls/%d/reviews", p.repo, num), reviewer, map[string]any{
			"event":    "COMMENT",
			"comments": []map[string]any{{"path": c.path, "line": c.line, "side": "RIGHT", "body": "範囲外"}},
		}, "review-outside-diff")
		errs := joinStrings(list(dig(rej, "errors")))
		p.check(c.label+"へのレビューは 422 で拒まれる", st == 422 && strings.Contains(errs, c.expect), fmt.Sprintf("%d %s", st, errs))
	}
	_, after := p.api("GET", fmt.Sprintf("/repos/%s/pulls/%d/reviews?per_page=100", p.repo, num), "", nil, "reviews-after-422")
	nAfter := -1
	if l, ok := after.([]any); ok {
		nAfter = len(l)
	}
	p.check("拒まれたレビューは作られない（レビューの件数が増えない）", nBefore >= 1 && nAfter == nBefore, fmt.Sprintf("%d -> %d", nBefore, nAfter))
}

// resolveAndDecide は 3 の後半（1 本目の解決・2 本目の解決・comment・decide の受け入れ）。
func (p *probe) resolveAndDecide() bool {
	parent, num, reviewer := p.parent, p.num, p.reviewer()
	human := p.threads(num, "threads-resolve")
	first := findThread(human, "src/1/work.py")
	if first == nil {
		panic("src/1/work.py のスレッドが無い")
	}
	res := p.resolve(first.ID, reviewer, "thread-resolve")
	p.check("レビュアーが 1 本目のスレッドを解決する", dig(res, "data", "resolveReviewThread", "thread", "isResolved") == true, "")
	resolvedFirst := fetched(p.sh(p.script("ccnavi-review.sh"), parent, nil, "fetch"))
	p.check("sh が解決済みを読み分ける（1 本解決・1 本未解決）", countResolved(resolvedFirst.Threads) == 1 && len(resolvedFirst.Threads) == 2,
		jsonText(resolvedFirst.Threads))
	checked := p.sh(p.script("ccnavi-review.sh"), parent, nil, "confirm", "--phase", "1")
	p.check("1 本残っていれば confirm は止まる", checked.Code != 0 && strings.Contains(checked.Stderr, "未解決"), head(strings.TrimSpace(checked.Stderr), 200))

	second := otherThread(human, first.ID)
	if second == nil {
		panic("2 本目のスレッドが無い")
	}
	res2 := p.resolve(second.ID, reviewer, "thread-resolve-2")
	p.check("レビュアーが 2 本目のスレッドを解決する", dig(res2, "data", "resolveReviewThread", "thread", "isResolved") == true, "")
	fetchedAll := p.sh(p.script("ccnavi-review.sh"), parent, nil, "fetch")
	writeOutFile(p.out, "fetch-4-resolved.json", fetchedAll.Stdout)
	checked = p.sh(p.script("ccnavi-review.sh"), parent, nil, "confirm", "--phase", "1")
	p.check("全部解決の後は confirm が通る", checked.Code == 0, head(strings.TrimSpace(checked.Stdout+checked.Stderr), 200))
	rc, decision, reason := p.hookFull("PreToolUse", "Agent", parent, map[string]any{"description": "次の子"})
	p.check("止まっていたのが解ける", rc == 0 && decision != "deny" && !strings.Contains(reason, "DENY_PHASE_REVIEW"),
		fmt.Sprintf("rc=%d decision=%q", rc, decision))

	memo := p.writeOut("comment-1.md", "チャットで「命名は次のフェーズで直す」と合意した。\n")
	noted := p.sh(p.script("ccnavi-review.sh"), parent, nil, "comment", "--body-file", memo)
	p.check("comment が投稿される", noted.Code == 0, head(strings.TrimSpace(noted.Stdout+noted.Stderr), 160))
	p.check("comment に ccnavi:comment のマーカーがある", hasMarker(p.commentsOf(num), "<!-- ccnavi:comment -->"), "")

	// 3 本目のスレッド。decide で受け入れる
	st, _ := p.api("POST", fmt.Sprintf("/repos/%s/pulls/%d/reviews", p.repo, num), reviewer, map[string]any{
		"event":    "COMMENT",
		"body":     "もう 1 件",
		"comments": []map[string]any{{"path": "src/1/work.py", "line": 2, "side": "RIGHT", "body": "typo。次でよい"}},
	}, "review-create-2")
	p.check("レビュアーが 3 本目のスレッドを立てる", st == 200, "")
	thirdIDs := []string{}
	for _, t := range p.threads(num, "threads-3") {
		if !t.IsResolved {
			thirdIDs = append(thirdIDs, t.ID)
		}
	}

	// 読む側（ccnavi-review.sh decide <N> --preview）で threads と digest を取り、書く側（ユーザ専用の ccnavi-user-decide.sh）は人間役としてこの probe が打つ
	preview := p.sh(p.script("ccnavi-review.sh"), parent, nil, "decide", "1", "--preview")
	writeOutFile(p.out, "decide-preview.json", preview.Stdout)
	var shown map[string]any
	_ = json.Unmarshal([]byte(preview.Stdout), &shown)
	threads := list(shown["threads"])
	p.check("decide --preview が threads と digest を JSON で返す", preview.Code == 0 && len(threads) > 0 && str(shown, "digest") != "",
		tailRunes(p.redact(preview.Stderr), 160))
	choices := map[string]string{}
	for _, t := range threads {
		if key := str(obj(t), "key"); key != "" {
			choices[key] = "keep"
		}
	}
	choicesJSON, _ := json.Marshal(choices)
	accepted := p.sh(p.script("ccnavi-user-decide.sh"), parent, nil, "1", "--choices", string(choicesJSON), "--digest", str(shown, "digest"))
	p.say("decide:\n" + p.redact(accepted.Stdout+accepted.Stderr))
	p.check("decide で未解決を受け入れる", accepted.Code == 0 && strings.Contains(accepted.Stdout, "対応しない 1 件"), head(strings.TrimSpace(accepted.Stderr), 200))
	var acc []string
	for _, c := range p.commentsOf(num) {
		if strings.HasPrefix(str(c, "body"), "<!-- ccnavi:decide -->") {
			acc = append(acc, str(c, "body"))
		}
	}
	p.check("受け入れのコメントが PR に書き込まれる", len(acc) > 0 && strings.Contains(acc[len(acc)-1], "#discussion_r"),
		head(lastOr(acc), 200))
	checked = p.sh(p.script("ccnavi-review.sh"), parent, nil, "confirm", "--phase", "1")
	p.check("フェーズが決着済みなら confirm は「レビュー済み」で止まる", checked.Code != 0 && strings.Contains(checked.Stderr, "レビュー済み"), tailRunes(checked.Stderr, 120))
	p.say(fmt.Sprintf("3 本目のスレッド: %v", thirdIDs))
	return true
}

// readyAndOutdated は 4: 親を閉じて ready（Draft を外す）と、行を書き換えた後の古いスレッドの読み方。
func (p *probe) readyAndOutdated() bool {
	parent, num := p.parent, p.num
	closed := p.sh(p.script("ccnavi-ticket.sh"), parent, nil, "finish", "i0001")
	p.check("親の done", closed.Code == 0, head(strings.TrimSpace(closed.Stdout+closed.Stderr), 200))
	p.commitAll(parent, "状態の移動")
	ready := p.sh(p.script("ccnavi-review.sh"), p.root, nil, "ready", "--parent", "i0001")
	p.say("ready:\n" + p.redact(ready.Stdout+ready.Stderr))
	p.check("ready が通る（wip/ の追跡済みのファイルも消える）", ready.Code == 0 && strings.Contains(ready.Stdout, wipNote), "")
	pr := p.prOf("i0001")
	title := str(pr, "title")
	p.check("PR の Draft が外れ、タイトルから Draft: が消える",
		pr != nil && !flagOf(pr, "draft") && !strings.HasPrefix(strings.ToLower(title), "draft"),
		fmt.Sprintf("title=%s draft=%v", title, dig(pr, "draft")))
	p.check("ready のコメントが PR にある", hasMarker(p.commentsOf(num), "<!-- ccnavi:ready -->"), "")

	// コメントを付けた行を書き換えるコミットを push すると、スレッドは古く（outdated）なり、GraphQL の line は null になる。sh は 0 に読む。
	// push すると依頼の後に親の HEAD が動くので、依頼・confirm の段階が全部済んだ ready の後でやる
	mustWrite(filepath.Join(parent, "src", "2", "work.py"), "# rewritten\n")
	p.commitAll(parent, "コメントを付けた行を書き換える")
	moved := p.sh(p.script("ccnavi-git.sh"), parent, nil, "push")
	p.check("書き換えのコミットを push", moved.Code == 0, head(p.redact(strings.TrimSpace(moved.Stdout+moved.Stderr)), 120))

	var outdated thread
	for i := 0; i < 8; i++ { // 古くなった印が付くまで少しかかる
		outdated = thread{}
		for _, t := range p.threads(num, "threads-outdated") {
			if t.first().Path == "src/2/work.py" {
				outdated = t
				break
			}
		}
		if outdated.IsOutdated {
			break
		}
		time.Sleep(3 * time.Second)
	}
	node := outdated.first()
	p.check("GraphQL は古いスレッドの line を null で返す", outdated.IsOutdated && node.Line == nil,
		fmt.Sprintf("isOutdated=%v line=%s originalLine=%s", outdated.IsOutdated, intOrNull(node.Line), intOrNull(node.OriginalLine)))
	fetchedRaw := p.sh(p.script("ccnavi-review.sh"), parent, nil, "fetch")
	writeOutFile(p.out, "fetch-3-outdated.json", fetchedRaw.Stdout)
	var old []fetchThread
	for _, t := range fetched(fetchedRaw).Threads {
		if t.Path == "src/2/work.py" {
			old = append(old, t)
		}
	}
	p.check("sh は古いスレッドを line 0・解決済みのまま読む",
		fetchedRaw.Code == 0 && len(old) == 1 && old[0].Line == 0 && old[0].Resolved, jsonText(old))
	return true
}

// closeEarly は 5: 別の親をユーザが早めに閉じる（close-early）。
func (p *probe) closeEarly() bool {
	parent2 := p.worktree("i0002", "main")
	p.propose(parent2, ticket{Name: "i0002", Allow: []string{"src/*", "wip/*"}, Review: true, Title: "途中で早めに閉じる親"})
	p.propose(parent2, ticket{Name: "i0002-01-01", Parent: "i0002", Phase: 1, Allow: []string{"src/c/*"}, Review: true, Title: "c を書く"})
	mustWrite(filepath.Join(parent2, "src", "c.py"), "print('c')\n")
	p.commitAll(parent2, "tickets")
	approved := p.exeCall("y\n", "user", "agree")
	p.check("--agree（i0002）", approved.Code == 0, head(p.redact(strings.TrimSpace(approved.Stderr)), 200))
	pushed2 := p.sh(p.script("ccnavi-git.sh"), parent2, nil, "push", "-u", "origin", "i0002")
	if pushed2.Code == 0 {
		p.madeBrs = append(p.madeBrs, "i0002")
	}
	p.check("i0002 の push", pushed2.Code == 0, "")
	status, pr2 := p.api("POST", "/repos/"+p.repo+"/pulls", "", map[string]any{
		"head": "i0002", "base": p.base, "title": "Draft: 途中で早めに閉じる親", "draft": true,
	}, "pr-create-i0002")
	p.check("ユーザが i0002 の PR を作る", status == 201, "")
	if status != 201 {
		return true
	}
	number := intOf(obj(pr2), "number")
	p.madePRs = append(p.madePRs, number)
	p.api("POST", fmt.Sprintf("/repos/%s/pulls/%d/reviews", p.repo, number), p.reviewer(), map[string]any{
		"event":    "COMMENT",
		"comments": []map[string]any{{"path": "src/c.py", "line": 1, "side": "RIGHT", "body": "残る指摘"}},
	}, "review-create-3")
	wrapped := p.shIn(p.script("ccnavi-user-close-early.sh"), parent2, "y\n", "--reason", "実測はここまで")
	p.say("close-early:\n" + p.redact(wrapped.Stdout+wrapped.Stderr))
	p.check("close-early が通る", wrapped.Code == 0, "")

	// 作った issue は出力の「残りを #N に書き出した」から引く（一覧は作った直後だと載らないことがある）
	found := regexp.MustCompile(`残りを #(\d+) に書き出した`).FindStringSubmatch(wrapped.Stdout)
	var made map[string]any
	if found != nil {
		n := 0
		fmt.Sscanf(found[1], "%d", &n)
		p.madeIssue = append(p.madeIssue, n)
		_, got := p.api("GET", fmt.Sprintf("/repos/%s/issues/%s", p.repo, found[1]), "", nil, "issue-close-early")
		made = obj(got)
	}
	madeURL := "無し"
	if made != nil {
		madeURL = str(made, "html_url")
	}
	p.check("残りを書き出す issue ができる",
		found != nil && str(made, "state") == "open" && strings.Contains(str(made, "title"), "残り"), madeURL)
	_, statErr := os.Stat(filepath.Join(parent2, ".ccnavi", "approved", "done", "i0002-01-01.md"))
	p.check("未着手の子が取り消されて done/ へ動く", statErr == nil, "")
	p.check("close-early のコメントが PR にある", hasMarker(p.commentsOf(number), "<!-- ccnavi:close-early -->"), "")
	return true
}

// urlToken は 6: URL にトークンを埋めた origin でも読めて、出力に漏れない。
// URL は引数にも .git/config にも置かない（ps に見える・落ちると残る）。git の環境変数での設定で、この 1 回の呼び出しの origin だけを差し替える。
func (p *probe) urlToken() {
	parent2 := filepath.Join(p.root, ".claude", "worktrees", "i0002")
	scheme, hostpath, _ := strings.Cut(p.origin, "://")
	urlEnv := map[string]string{}
	for k, v := range p.env {
		urlEnv[k] = v
	}
	urlEnv["GIT_CONFIG_COUNT"] = "1"
	urlEnv["GIT_CONFIG_KEY_0"] = "remote.origin.url"
	urlEnv["GIT_CONFIG_VALUE_0"] = scheme + "://x-access-token:" + p.token + "@" + hostpath
	seen := p.sh(p.script("ccnavi-review.sh"), parent2, urlEnv, "origin")
	p.say("トークン入り URL の origin の読み方:\n" + p.redact(seen.Stdout+seen.Stderr))
	p.check("トークン入り URL でも host と api_base が正しい",
		strings.Contains(seen.Stdout, "api_base=https://api.github.com") && strings.Contains(seen.Stdout, "path="+p.repo), "")
	leaked := strings.Contains(seen.Stdout+seen.Stderr, p.token)
	viaURL := p.sh(p.script("ccnavi-review.sh"), parent2, urlEnv, "fetch")
	leaked = leaked || strings.Contains(viaURL.Stdout+viaURL.Stderr, p.token)
	p.check("トークン入り URL のトークンが origin / fetch の出力に漏れない", !leaked, "")
}

// --- 補助 -----------------------------------------------------------------

// reviewer はレビュアーのトークン（別のアカウントが無ければ自分のトークン）。
func (p *probe) reviewer() string {
	if p.reviewerToken != "" {
		return p.reviewerToken
	}
	return p.token
}

// resolve はスレッドを解決する（GraphQL の resolveReviewThread）。
func (p *probe) resolve(id, token, tag string) map[string]any {
	return p.graphql("mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{id isResolved}}}",
		map[string]any{"id": id}, token, tag)
}

// propose は提案の本文を親のワークツリーの todo/ に書く。
func (p *probe) propose(tree string, t ticket) {
	mustWrite(filepath.Join(tree, "wip", "proposals", "todo", t.Name+".md"), ticketText(t))
}

// writeOut は OUT に本文を書き、パスを返す。
func (p *probe) writeOut(name, text string) string {
	path, err := writeFile(filepath.Join(p.out, name), text)
	must(err)
	return path
}

// commitAll は tree の全部をコミットする（変更が無ければ何もしない）。
func (p *probe) commitAll(tree, msg string) {
	p.git(tree, "add", "-A")
	if strings.TrimSpace(p.git(tree, "status", "--porcelain")) == "" {
		return // 取り込み済みの親子では ccnavi が自分でコミットしている
	}
	p.git(tree, "commit", "--quiet", "-m", msg)
}

// worktree は一時リポジトリに、name のワークツリーを base から切る。
func (p *probe) worktree(name, base string) string {
	path := filepath.Join(p.root, ".claude", "worktrees", name)
	p.git(p.root, "worktree", "add", "--quiet", path, "-b", name, base)
	return path
}

// script は .ccnavi/scripts の配布スクリプトの、実測が使う絶対パス（ccnavi の根から）。
func (p *probe) script(name string) string {
	return filepath.Join(p.scriptsSrc(), ".ccnavi", "scripts", name)
}

// scriptsSrc は配布スクリプトの置かれたこのチェックアウトの根（probe が起こす sh の出どころ）。
func (p *probe) scriptsSrc() string { return p.srcRoot }

// envWithoutGh は gh を PATH から外した実測の環境（gh が無ければ同じ環境）。
func (p *probe) envWithoutGh() (map[string]string, error) {
	gh := lookPath("gh", p.env)
	if gh == "gh" {
		return p.env, nil
	}
	return withoutGh(p.env, gh, filepath.Join(p.out, "nogh-bin"))
}

// copyTree は src の下の通常のファイルを dst へ写す（符号リンクは写さない）。
func copyTree(src, dst string) error {
	return filepath.WalkDir(src, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(src, path)
		target := filepath.Join(dst, rel)
		if d.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		if !d.Type().IsRegular() {
			return nil
		}
		return copyFile(path, target)
	})
}

// --- 小さな判定 ---------------------------------------------------------

func detailOf(status int, body any, login string) string {
	if status == 200 {
		return login
	}
	return fmt.Sprint(body)
}

func anyResolved(ts []thread) bool {
	for _, t := range ts {
		if t.IsResolved {
			return true
		}
	}
	return false
}

func countResolved(ts []fetchThread) int {
	n := 0
	for _, t := range ts {
		if t.Resolved {
			n++
		}
	}
	return n
}

// threadsRead は sh が 2 本の未解決のスレッドを path・line・URL つきで読んだか。
func threadsRead(ts []fetchThread) bool {
	if len(ts) != 2 {
		return false
	}
	pairs := map[string]bool{}
	for _, t := range ts {
		if t.Resolved || !strings.HasPrefix(t.URL, "https://github.com/") || !strings.Contains(t.URL, "#discussion_r") || !strings.HasSuffix(t.CreatedAt, "Z") {
			return false
		}
		pairs[fmt.Sprintf("%s:%d", t.Path, t.Line)] = true
	}
	return pairs["src/1/work.py:1"] && pairs["src/2/work.py:2"]
}

func findThread(ts []thread, path string) *thread {
	for i := range ts {
		if ts[i].first().Path == path {
			return &ts[i]
		}
	}
	return nil
}

func otherThread(ts []thread, id string) *thread {
	for i := range ts {
		if ts[i].ID != id {
			return &ts[i]
		}
	}
	return nil
}

func joinStrings(items []any) string {
	var out []string
	for _, v := range items {
		if s, ok := v.(string); ok {
			out = append(out, s)
		}
	}
	return strings.Join(out, " ")
}

func lastOr(items []string) string {
	if len(items) == 0 {
		return ""
	}
	return items[len(items)-1]
}

func intOrNull(v *int) string {
	if v == nil {
		return "null"
	}
	return fmt.Sprint(*v)
}

func jsonText(v any) string {
	data, _ := json.Marshal(v)
	return head(string(data), 300)
}

func must(err error) {
	if err != nil {
		panic(err)
	}
}

func mustWrite(path, text string) {
	_, err := writeFile(path, text)
	must(err)
}

// writeOutFile は dir に本文を書く。
func writeOutFile(dir, name, text string) {
	mustWrite(filepath.Join(dir, name), text)
}
