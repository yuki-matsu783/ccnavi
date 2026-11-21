package gitlabprobe

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"ccnavi/internal/harness/gitlabapi"
	"ccnavi/internal/harness/samples"
)

func intOf(v any) int { n, _ := gitlabapi.Int(v); return n }

func str(v any) string { return gitlabapi.Text(v) }

func copyTree(src, dst string, skip map[string]bool) error {
	return filepath.Walk(src, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(src, path)
		if rel != "." && skip[strings.Split(filepath.ToSlash(rel), "/")[0]] {
			if info.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		target := filepath.Join(dst, rel)
		if info.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(target, data, info.Mode().Perm())
	})
}

// run は 1 周する。土台が崩れたとき（git の失敗など）は panic で止めるので、記録して終わりにする。
func (p *probe) probeAll() {
	defer func() {
		if r := recover(); r != nil {
			p.record("probe が途中で止まった", false, head(p.redact(fmt.Sprint(r)), 300))
		}
	}()
	p.steps()
}

func (p *probe) steps() {
	c := p.client
	rt, vt := p.cfg.RootToken, p.cfg.ReviewerToken
	p.say("== 実測の置き場: %s", p.out)
	status, version := c.Version(rt)
	p.record("API に root トークンで入れる", status == 200, "GitLab "+str(gitlabapi.AsMap(version)["version"]))
	status, me := c.User(vt)
	p.record("レビュアーのトークンで入れる", status == 200, str(gitlabapi.AsMap(me)["username"]))
	if status != 200 {
		return
	}
	reviewerID := intOf(gitlabapi.AsMap(me)["id"])

	// ---- 0. ユーザが用意するもの: プロジェクト、メンバー、課題、main
	projectPath := ProjectName(p.cfg.Prefix, p.stamp)
	status, project := c.CreateProject(rt, projectPath)
	p.record("プロジェクトを作る", status == 201, str(gitlabapi.AsMap(project)["web_url"]))
	if status != 201 {
		return
	}
	pid := intOf(gitlabapi.AsMap(project)["id"])
	p.projectIDs = append(p.projectIDs, pid)
	status, _ = c.AddMember(rt, pid, reviewerID, 40)
	p.record("レビュアーをメンバーにする", status == 201, "")
	status, issue := c.CreateIssue(rt, pid, "greeter に挨拶を足す", "実測の元になる課題")
	issueNo := intOf(gitlabapi.AsMap(issue)["iid"])
	p.record("課題を立てる", status == 201, fmt.Sprintf("#%d", issueNo))

	root := p.root
	p.git(root, "init", "--quiet", "-b", "main")
	// 一時リポジトリをワークスペースにする。sh の道具（scripts と config）を写す
	if err := copyTree(filepath.Join(p.cfg.Root, ".ccnavi"), filepath.Join(root, ".ccnavi"), map[string]bool{"bin": true, "docs": true}); err != nil {
		panic(err)
	}
	write(filepath.Join(root, "src", "keep.py"), "print(1)\n")
	// 実物と同じく、.ccnavi/bin だけ無視する。承認済みチケットは .ccnavi の下で git が追跡する
	write(filepath.Join(root, ".gitignore"), ".claude/\n.ccnavi/bin/\nwip/tmp/\n")
	// 設定は init の commit に入れる（後から書くと、main と手元の config が食い違う）
	write(filepath.Join(root, ".ccnavi", "config", "common", "rules.yml"), rulesJSON)
	write(filepath.Join(root, ".ccnavi", "config", "project", "phases.yml"), samples.PlainPhases)
	p.commitAll(root, "init")
	origin := fmt.Sprintf("%s/root/%s.git", p.cfg.GitLab, projectPath)
	p.git(root, "remote", "add", "origin", origin)
	// 認証は git の設定側に置く。空文字で system・global の helper（GCM）を外し、環境変数 GITLAB_TOKEN を返す helper を足す。
	// ラッパースクリプトが除くのは GIT_CONFIG_COUNT だけで、環境変数は helper の sh に届く。トークンをファイルに書かない（置き去りになる）。
	p.git(root, "config", "--add", "credential.helper", "")
	p.git(root, "config", "--add", "credential.helper", `!f() { echo username=oauth2; echo "password=$GITLAB_TOKEN"; }; f`)
	pushed := p.run([]string{"git", "push", "--quiet", "-u", "origin", "main"}, root, "")
	p.record("main を push（認証画面なし）", pushed.code == 0, head(p.redact(pushed.err), 200))

	// ---- 1. 親 1 本と子 2 本、フェーズ 1
	parentTree := p.worktree("i0001", "main")
	p.propose(parentTree, "i0001", TicketOpts{Allow: []string{"src/*", "wip/*"}, Review: true, Title: "挨拶を足す", Issue: issueNo})
	p.propose(parentTree, "i0001-01-01", TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/a/*"}, Review: true, Title: "a を書く"})
	p.propose(parentTree, "i0001-01-02", TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/b/*"}, Title: "b を書く"})
	p.commitAll(parentTree, "tickets")
	gitSh, ticketSh, reviewSh := p.script("ccnavi-git.sh"), p.script("ccnavi-ticket.sh"), p.script("ccnavi-review.sh")
	pushApprovedSh := p.script("ccnavi-user-push-approved.sh")
	// 子の start は、親のブランチがリモートにあることを前提とする（ccnavi-start 経由の親）
	pp := p.sh(gitSh, parentTree, "push", "-u", "origin", "i0001")
	p.record("親 i0001 を push", pp.code == 0, head(p.redact(pp.err), 200))
	// 親の mr 欄は、親の Draft MR が既にある前提。ccnavi-start が作る MR を、probe は通らずに API で作る。説明に Closes を入れる
	status, parentMR := c.Call("POST", fmt.Sprintf("/projects/%d/merge_requests", pid), rt, map[string]any{
		"source_branch": "i0001", "target_branch": "main", "title": "Draft: 挨拶を足す", "description": fmt.Sprintf("Closes #%d", issueNo),
	}, "mr-create-parent")
	p.record("親の Draft MR を作る（mr: 1 と一致）", status == 201 && intOf(gitlabapi.AsMap(parentMR)["iid"]) == 1, fmt.Sprintf("status=%d", status))
	approved := p.exe("y\n", "user", "agree")
	p.record("--agree（親 1 子 2）", approved.code == 0, head(p.redact(approved.err), 200))
	// 承認の push はユーザが打つ（ccnavi-user-push-approved.sh）。probe はユーザの役を務める
	pa := p.sh(pushApprovedSh, parentTree, "i0001")
	p.record("承認を push（ユーザの役）", pa.code == 0, head(p.redact(pa.both()), 200))
	started := p.sh(ticketSh, parentTree, "start", "i0001")
	p.say("親 i0001 の start（全文）:\n%s", p.redact(started.both()))
	p.record("親 i0001 の start", started.code == 0, head(started.err, 200))

	children := []struct{ id, dir string }{{"i0001-01-01", "a"}, {"i0001-01-02", "b"}}
	for _, ch := range children {
		tree := p.worktree(ch.id, "i0001")
		st := p.sh(ticketSh, parentTree, "start", ch.id)
		p.record("ticket start "+ch.id, st.code == 0, head(st.err, 200))
		// 子の allow の範囲に入るパスに書く
		write(filepath.Join(tree, "src", ch.dir, "work.py"), "# "+ch.id+"\n")
		// finish では学びの記録が必須。learn で雛形を書いてから同じコミットに入れる（learnings: none のまま）
		learned := p.sh(ticketSh, tree, "learn")
		p.record(fmt.Sprintf("learn が雛形を書く（%s）", ch.id), learned.code == 0, head(learned.err, 200))
		p.commitAll(tree, ch.id+": work")
	}
	p.commitAll(parentTree, "start")

	childPush := p.sh(gitSh, filepath.Join(root, ".claude", "worktrees", "i0001-01-01"), "push", "-u", "origin", "i0001-01-01")
	p.record("子のワークツリーからの push はラッパースクリプトが拒む", childPush.code != 0 && strings.Contains(childPush.both(), "子チケット"), "")

	for _, ch := range children {
		fin := p.sh(ticketSh, parentTree, "finish", ch.id)
		p.record("ticket finish "+ch.id, fin.code == 0, head(fin.err, 200))
	}
	p.commitAll(parentTree, "finish")
	for _, ch := range children {
		p.git(parentTree, "merge", "--quiet", "--no-edit", ch.id)
	}

	said := p.hook("PostToolUse", "Bash", parentTree, map[string]any{"command": "ls"})
	// フェーズの名前（番号（タイトル））ごと照合する（製品の文言どおり）
	p.record("フェーズ 1 の終わりの告知", strings.Contains(said, "フェーズ 1（作業） が終わりました"), strings.ReplaceAll(head(said, 120), "\n", " "))
	nextChild := map[string]any{"description": "次の子"}
	p.record("レビュー待ちが Agent を止める", strings.Contains(p.hook("PreToolUse", "Agent", parentTree, nextChild), "DENY_PHASE_REVIEW"), "")

	body := write(filepath.Join(p.out, "request-1.md"), "見てほしい点\n\n- src/a と src/b を足した\n")
	// 未 push の src の変更を 1 つ足してから request する（ccnavi の置き場の差分だけでは止まらない）
	write(filepath.Join(parentTree, "src", "a", "unpushed.py"), "# 未 push\n")
	p.commitAll(parentTree, "未 push の変更")
	early := p.sh(reviewSh, parentTree, "review-request", "--phase", "1", "--body-file", body)
	p.record("push 前の request は前提で止まる", early.code != 0 && strings.Contains(early.err, "push"), tail(early.err, 120))

	pushedParent := p.sh(gitSh, parentTree, "push", "-u", "origin", "i0001")
	p.record("親の push（ラッパースクリプト経由、認証画面なし）", pushedParent.code == 0, head(p.redact(pushedParent.both()), 120))
	status, _ = c.GetBranch(rt, pid, "i0001")
	p.record("GitLab に i0001 ブランチがある", status == 200, "")

	// ---- 2. origin の読み方と request
	originSeen := p.sh(reviewSh, parentTree, "origin")
	p.say("origin の読み方:\n%s", p.redact(originSeen.both()))
	p.record("origin をホスト（ポート付き）とパスに読む",
		strings.Contains(originSeen.stdout, "path=root/"+projectPath) && strings.Contains(originSeen.stdout, "api_base="+p.cfg.GitLab+"/api/v4"), "")

	requested := p.sh(reviewSh, parentTree, "review-request", "--phase", "1", "--body-file", body)
	p.say("request:\n%s", p.redact(requested.both()))
	p.record("request が通る（MR を作って投稿してマーカー）", requested.code == 0, "")
	mr := p.mrOf(pid, "i0001")
	p.record("MR が Draft で作られている", mr != nil && mr["draft"] == true && strings.HasPrefix(str(mr["title"]), "Draft:"),
		fmt.Sprintf("%v title=%v", mr["web_url"], mr["title"]))
	p.record("MR の本文に Closes #課題 がある", mr != nil && strings.Contains(str(mr["description"]), fmt.Sprintf("Closes #%d", issueNo)), "")
	if mr == nil {
		return
	}
	iid := intOf(mr["iid"])
	p.record("依頼の note に ccnavi:request のマーカーがある", p.requestMarked(pid, iid), "")
	again := p.sh(reviewSh, parentTree, "review-request", "--phase", "1", "--body-file", body)
	p.record("2 度目の request は依頼済みで止まる", again.code != 0 && strings.Contains(again.err, "依頼済み"), "")
	fetched := p.sh(reviewSh, parentTree, "fetch")
	write(filepath.Join(p.out, "fetch-1-empty.json"), fetched.stdout)
	p.record("fetch が JSON を返す", fetched.code == 0 && strings.HasPrefix(strings.TrimSpace(fetched.stdout), "{"), head(fetched.err, 200))

	// ---- 3. レビュアーが指摘 → confirm → 変更要求（EE だけ）→ 解決と approve → confirm
	status, disc := c.CreateDiscussion(vt, pid, iid, "ここは名前を変えてほしい", "")
	notes0 := gitlabapi.AsMap(firstOf(gitlabapi.AsMap(disc)["notes"]))
	p.record("レビュアーがスレッドを立てる（位置なし、resolvable）", status == 201 && notes0["resolvable"] == true, "")
	discID := str(gitlabapi.AsMap(disc)["id"])
	fetched = p.sh(reviewSh, parentTree, "fetch")
	write(filepath.Join(p.out, "fetch-2-unresolved.json"), fetched.stdout)
	checked := p.sh(reviewSh, parentTree, "confirm", "--phase", "1")
	p.record("未解決があると confirm は止まる", checked.code != 0 && strings.Contains(checked.err, "未解決"), head(checked.err, 200))
	p.record("その間は止まったまま", strings.Contains(p.hook("PreToolUse", "Agent", parentTree, nextChild), "DENY_PHASE_REVIEW"), "")

	c.SetReviewers(rt, pid, iid, []int{reviewerID})
	status, _ = c.RequestChanges(vt, pid, iid)
	ee := status == 200 || status == 201
	name := "レビュアーが変更要求を出す（EE の機能。CE では 404）"
	if !ee && status == 404 {
		p.skip(name, fmt.Sprintf("status=%d", status)) // CE は確かめていないので PASS に数えない
	} else {
		p.record(name, ee, fmt.Sprintf("status=%d", status))
	}
	status, _ = c.ResolveDiscussion(vt, pid, iid, discID, true)
	p.record("レビュアーがスレッドを解決する", status == 200, "")
	if ee {
		fetched = p.sh(reviewSh, parentTree, "fetch")
		write(filepath.Join(p.out, "fetch-3-changes-requested.json"), fetched.stdout)
		checked = p.sh(reviewSh, parentTree, "confirm", "--phase", "1")
		p.record("変更要求が立っていると confirm は止まる", checked.code != 0 && strings.Contains(checked.err, "変更要求"), head(checked.err, 200))
	}
	status, _ = c.Approve(vt, pid, iid)
	p.record("レビュアーが approve する", status == 200 || status == 201, fmt.Sprintf("status=%d", status))
	_, reviewers := c.Reviewers(rt, pid, iid)
	p.say("approve 後の reviewers: %s", head(jsonText(reviewers), 300))
	fetched = p.sh(reviewSh, parentTree, "fetch")
	write(filepath.Join(p.out, "fetch-4-approved.json"), fetched.stdout)
	checked = p.sh(reviewSh, parentTree, "confirm", "--phase", "1")
	p.record("解決と approve の後は confirm が通る", checked.code == 0, head(checked.both(), 200))
	p.record("止まっていたのが解ける", !strings.Contains(p.hook("PreToolUse", "Agent", parentTree, nextChild), "DENY_PHASE_REVIEW"), "")

	memo := write(filepath.Join(p.out, "comment-1.md"), "チャットで「命名は次のフェーズで直す」と合意した。\n")
	noted := p.sh(reviewSh, parentTree, "comment", "--body-file", memo)
	p.record("comment が投稿される", noted.code == 0, head(noted.both(), 160))
	p.record("comment に ccnavi:comment のマーカーがある", gitlabapi.HasMarker(p.notes(pid, iid), "<!-- ccnavi:comment -->"), "")
	c.CreateDiscussion(vt, pid, iid, "typo。次でよい", "discussion-create-2")
	// 受け入れ（ユーザの decide）は別の親で通す（手順 6）

	// ---- 3b. フィードバック計画の改版（親の done の前提。全体計画の最後がレビュー済みになった後）
	current := ApprovedCopyPath(parentTree, "doing", "i0001")
	revision, err := FeedbackRevision(readText(current))
	if err != nil {
		panic(err)
	}
	write(filepath.Join(parentTree, "wip", "proposals", "todo", "i0001.md"), revision)
	p.commitAll(parentTree, "フィードバック計画の改版")
	agreed := p.exe("y\n", "user", "agree")
	p.record("--agree（フィードバック計画の改版）", agreed.code == 0, tail(p.redact(agreed.err), 200))
	revisedPush := p.sh(pushApprovedSh, parentTree, "i0001")
	p.record("改版の承認を push（ユーザの役）", revisedPush.code == 0, "")
	p.record("承認済みのコピーの feedback 欄が空の計画になる", regexp.MustCompile(`(?m)^feedback: \[\]\s*$`).MatchString(readText(current)), "")

	// ---- 4. 親を閉じて ready（Draft を外す）。親の start は冒頭で済ませている
	// 追跡済みの wip/ は ready が消すので、手で消さない（ready の動きを probe でそのまま通す）
	closed := p.sh(ticketSh, parentTree, "finish", "i0001")
	p.record("親の done", closed.code == 0, head(closed.both(), 200))
	p.commitAll(parentTree, "状態の移動")
	pushedClosed := p.sh(gitSh, parentTree, "push")
	p.record("親の push（引数なし）", pushedClosed.code == 0, head(p.redact(pushedClosed.both()), 120))
	stray := write(filepath.Join(parentTree, "wip", "stray.txt"), "未追跡\n")
	blocked := p.sh(reviewSh, root, "ready", "--parent", "i0001")
	p.record("wip/ に未追跡ファイルがあると ready が止まる", blocked.code != 0, head(p.redact(blocked.err), 160))
	held := p.mrOf(pid, "i0001")
	p.record("止まったとき MR は Draft のまま", held != nil && held["draft"] == true, "")
	_ = os.Remove(stray)
	ready := p.sh(reviewSh, root, "ready", "--parent", "i0001")
	p.say("ready:\n%s", p.redact(ready.both()))
	p.record("ready が通る", ready.code == 0, "")
	mr = p.mrOf(pid, "i0001")
	p.record("MR の Draft が外れ squash が立つ", mr != nil && mr["draft"] != true && mr["squash"] == true,
		fmt.Sprintf("title=%v draft=%v squash=%v", mr["title"], mr["draft"], mr["squash"]))
	p.record("ready の note が MR にある", gitlabapi.HasMarker(p.notes(pid, iid), "<!-- ccnavi:ready -->"), "")
	top := c.Tree(rt, pid, "i0001", "", false)
	hasWip := false
	for _, n := range top {
		hasWip = hasWip || n == "wip"
	}
	p.record("ready のあと GitLab の i0001 に wip/ が無い", len(top) > 0 && !hasWip, fmt.Sprint(top))
	// logs/archive/ は git の外（ワークスペースの下）。git 上は削除される（ticket-flow.md の 12）
	archive := filepath.Join(root, "logs", "archive")
	var archived []string
	_ = filepath.Walk(archive, func(path string, info os.FileInfo, err error) error {
		if err == nil && !info.IsDir() && strings.HasPrefix(info.Name(), "i0001") {
			rel, _ := filepath.Rel(archive, path)
			archived = append(archived, filepath.ToSlash(rel))
		}
		return nil
	})
	sort.Strings(archived)
	hasSuffix := func(suffix string) bool {
		for _, a := range archived {
			if strings.HasSuffix(a, suffix) {
				return true
			}
		}
		return false
	}
	p.record("閉じた親子が logs/archive/ へ移った（ローカル）", hasSuffix("done/i0001.md") && hasSuffix("ready/i0001.json"), head(fmt.Sprint(archived), 160))

	// ---- 5. 別の親をユーザが早めに閉じる（close-early）
	p.closeEarly(pid)
	p.decide(pid)

	// ---- 6. URL にトークンを埋めた origin でも読めて、出力に漏れない
	parent2 := filepath.Join(root, ".claude", "worktrees", "i0002")
	scheme, hostpath, _ := strings.Cut(origin, "://")
	p.git(parent2, "remote", "set-url", "origin", fmt.Sprintf("%s://oauth2:%s@%s", scheme, rt, hostpath))
	seen := p.sh(reviewSh, parent2, "origin")
	p.say("トークン入り URL の origin の読み方:\n%s", p.redact(seen.both()))
	p.record("トークン入り URL でも host と api_base が正しい", strings.Contains(seen.stdout, "api_base="+p.cfg.GitLab+"/api/v4"), "")
	p.record("トークン入り URL のトークンが出力に漏れない", !strings.Contains(seen.both(), rt), "")
	p.git(parent2, "remote", "set-url", "origin", origin)
}

func firstOf(v any) any {
	if l := gitlabapi.AsList(v); len(l) > 0 {
		return l[0]
	}
	return nil
}

func (p *probe) closeEarly(pid int) {
	c, rt, vt := p.client, p.cfg.RootToken, p.cfg.ReviewerToken
	gitSh := p.script("ccnavi-git.sh")
	parent2 := p.worktree("i0002", "main")
	// 親の提案には issue が要る（agree は issue が無いと承認しない）
	status, issue2 := c.CreateIssue(rt, pid, "途中で早めに閉じる親", "実測の 2 本目")
	p.record("課題 2 を立てる", status == 201, "")
	p.propose(parent2, "i0002", TicketOpts{Allow: []string{"src/*", "wip/*"}, Review: true, Title: "途中で早めに閉じる親", Issue: intOf(gitlabapi.AsMap(issue2)["iid"])})
	p.propose(parent2, "i0002-01-01", TicketOpts{Parent: "i0002", Phase: 1, Allow: []string{"src/c/*"}, Review: true, Title: "c を書く"})
	p.commitAll(parent2, "tickets")
	approved := p.exe("y\n", "user", "agree")
	p.record("--agree（i0002）", approved.code == 0, head(p.redact(approved.err), 200))
	p.record("i0002 の push", p.sh(gitSh, parent2, "push", "-u", "origin", "i0002").code == 0, "")
	p.waitBranch(pid, "i0002")
	status, mr2 := c.CreateMR(rt, pid, "i0002", "main", "Draft: 途中で早めに閉じる親")
	p.record("ユーザが i0002 の MR を作る", status == 201, "")
	if status != 201 {
		return
	}
	iid2 := intOf(gitlabapi.AsMap(mr2)["iid"])
	c.CreateDiscussion(vt, pid, iid2, "残る指摘", "discussion-create-3")
	wrapped := p.shIn(p.script("ccnavi-user-close-early.sh"), parent2, "y\n", "--reason", "実測はここまで")
	p.say("close-early:\n%s", p.redact(wrapped.both()))
	p.record("close-early が通る", wrapped.code == 0, "")
	_, issues := c.ListIssues(rt, pid, "opened", 0)
	var made map[string]any
	for _, i := range gitlabapi.AsList(issues) {
		if m := gitlabapi.AsMap(i); strings.Contains(str(m["title"]), "残り") {
			made = m
			break
		}
	}
	detail := "無し"
	if made != nil {
		detail = str(made["web_url"])
	}
	p.record("残りを書き出す issue ができる", made != nil, detail)
	moved := ApprovedCopyPath(parent2, "done", "i0002-01-01")
	p.record("未着手の子が done/ へ動き、cancelled_at が入る", fileExists(moved) && IsCancelledCopy(readText(moved)), moved)
	p.record("close-early のコメントが MR にある", gitlabapi.HasMarker(p.notes(pid, iid2), "<!-- ccnavi:close-early -->"), "")
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

// decide はユーザの受け入れ（decide）を、別の親 i0003 で通す。未解決の指摘が残る → confirm は止まる → decide で受け入れる
// （reviewed が置かれる）→ 同じフェーズへの confirm は「レビュー済み」で拒まれる。
func (p *probe) decide(pid int) {
	c, rt, vt := p.client, p.cfg.RootToken, p.cfg.ReviewerToken
	gitSh, ticketSh, reviewSh := p.script("ccnavi-git.sh"), p.script("ccnavi-ticket.sh"), p.script("ccnavi-review.sh")
	status, issue3 := c.CreateIssue(rt, pid, "受け入れを通す親", "実測の 3 本目")
	p.record("課題 3 を立てる", status == 201, "")
	tree := p.worktree("i0003", "main")
	p.propose(tree, "i0003", TicketOpts{Allow: []string{"src/*", "wip/*"}, Review: true, Title: "受け入れを通す親", Issue: intOf(gitlabapi.AsMap(issue3)["iid"]), MR: 3})
	p.propose(tree, "i0003-01-01", TicketOpts{Parent: "i0003", Phase: 1, Allow: []string{"src/d/*"}, Review: true, Title: "d を書く"})
	p.commitAll(tree, "tickets")
	p.record("i0003 の push", p.sh(gitSh, tree, "push", "-u", "origin", "i0003").code == 0, "")
	p.waitBranch(pid, "i0003")
	status, mr3 := c.CreateMR(rt, pid, "i0003", "main", "Draft: 受け入れを通す親")
	p.record("i0003 の Draft MR（mr: 3 と一致）", status == 201 && intOf(gitlabapi.AsMap(mr3)["iid"]) == 3, fmt.Sprintf("status=%d", status))
	if status != 201 {
		return
	}
	mrNo := intOf(gitlabapi.AsMap(mr3)["iid"])
	agreed := p.exe("y\n", "user", "agree")
	p.record("--agree（i0003）", agreed.code == 0, tail(p.redact(agreed.err), 200))
	p.record("i0003 の承認を push（ユーザの役）", p.sh(p.script("ccnavi-user-push-approved.sh"), tree, "i0003").code == 0, "")
	p.record("親 i0003 の start", p.sh(ticketSh, tree, "start", "i0003").code == 0, "")
	child := "i0003-01-01"
	childTree := p.worktree(child, "i0003")
	p.record("ticket start "+child, p.sh(ticketSh, tree, "start", child).code == 0, "")
	write(filepath.Join(childTree, "src", "d", "work.py"), "# "+child+"\n")
	p.sh(ticketSh, childTree, "learn")
	p.commitAll(childTree, child+": work")
	p.commitAll(tree, "start")
	p.record("ticket finish "+child, p.sh(ticketSh, tree, "finish", child).code == 0, "")
	p.commitAll(tree, "finish")
	p.git(tree, "merge", "--quiet", "--no-edit", child)
	p.record("i0003 の push（request の前提）", p.sh(gitSh, tree, "push", "-u", "origin", "i0003").code == 0, "")
	body := write(filepath.Join(p.out, "request-3.md"), "見てほしい点\n\n- src/d を足した\n")
	requested := p.sh(reviewSh, tree, "review-request", "--phase", "1", "--body-file", body)
	p.record("i0003 の request が通る", requested.code == 0, tail(p.redact(requested.err), 200))
	status, disc := c.CreateDiscussion(vt, pid, mrNo, "名前は今は直さない（decide で受け入れる）", "discussion-create-4")
	p.record("レビュアーがスレッドを立てる（i0003）", status == 201, "")
	discID := str(gitlabapi.AsMap(disc)["id"])
	stopped := p.sh(reviewSh, tree, "confirm", "--phase", "1")
	p.record("未解決の指摘が残ると confirm が止まる（i0003）", stopped.code != 0 && strings.Contains(stopped.err, "未解決"), "")

	preview := p.sh(reviewSh, tree, "decide", "1", "--preview")
	write(filepath.Join(p.out, "decide-preview.json"), preview.stdout)
	var shown map[string]any
	_ = json.Unmarshal([]byte(preview.stdout), &shown)
	threads, _ := shown["threads"].([]any)
	p.record("decide --preview が threads と digest を JSON で返す", preview.code == 0 && len(threads) > 0 && str(shown["digest"]) != "", tail(p.redact(preview.err), 160))
	bare := p.sh(reviewSh, tree, "decide", "1")
	p.record("--preview なしの decide は rc 2 で断られる", bare.code == 2, fmt.Sprintf("rc=%d %s", bare.code, tail(p.redact(bare.err), 120)))
	if len(threads) == 0 {
		return
	}
	choices := map[string]string{}
	for _, t := range threads {
		if key := str(gitlabapi.AsMap(t)["key"]); key != "" {
			choices[key] = "keep"
		}
	}
	choiceJSON, _ := json.Marshal(choices)
	decided := p.sh(p.script("ccnavi-user-decide.sh"), tree, "1", "--choices", string(choiceJSON), "--digest", str(shown["digest"]))
	p.say("decide:\n%s", p.redact(decided.both()))
	p.record("decide（対応しないを選ぶ）が通る", decided.code == 0, tail(p.redact(decided.err), 200))

	// 判定は副作用の状態で見る
	var notes []any
	for _, d := range c.Discussions(rt, pid, mrNo) {
		if m := gitlabapi.AsMap(d); str(m["id"]) == discID {
			notes = gitlabapi.AsList(m["notes"])
			break
		}
	}
	p.record("スレッドが解決済みになる", len(notes) > 0 && gitlabapi.AsMap(notes[0])["resolved"] == true, "")
	p.record("スレッドに返信の note が付く", len(notes) >= 2, fmt.Sprintf("notes=%d", len(notes)))
	markerDir := filepath.Join(tree, ".ccnavi", "approved", "phases", "i0003")
	var placed []string
	entries, _ := os.ReadDir(markerDir)
	for _, e := range entries {
		placed = append(placed, e.Name())
	}
	sort.Strings(placed)
	reviewed := false
	for _, n := range placed {
		reviewed = reviewed || strings.HasSuffix(n, "reviewed")
	}
	p.record("reviewed が置かれる", reviewed, fmt.Sprint(placed))
	again := p.sh(reviewSh, tree, "confirm", "--phase", "1")
	p.record("同じフェーズの confirm は「レビュー済み」で拒まれる", again.code != 0 && strings.Contains(again.err, "レビュー済み"), tail(p.redact(again.err), 120))
}
