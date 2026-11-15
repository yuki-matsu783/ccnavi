package cut

import (
	"ccnavi/internal/policy/texts"
	"os"
	"regexp"
	"strconv"
	"strings"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/tickets/lifecycle/treeadd"
)

// remoteState は origin/<取り込み先> との関係。
type remoteState struct {
	state          string // even・behind・ahead・diverged・absent・unverified
	ahead, behind  int
	originSha, why string
}

// headLine は ls-remote --heads の 1 行（`<sha> refs/heads/<名前>`）。
var headLine = regexp.MustCompile(`^[0-9a-fA-F]+\s+refs/heads/(.+)$`)

// compare は fetch の結果と手元の origin/<取り込み先> から、ずれを決める。fetch が届かなくても止めず、リモート未確認にする。
func (r *run) compare(remote Remote) remoteState {
	rs := remoteState{state: "even"}
	if !remote.FetchOK {
		if strings.Contains(remote.FetchOut, "couldn't find remote ref") {
			rs.state = "absent"
		} else {
			rs.state = "unverified"
			rs.why, _, _ = strings.Cut(strings.TrimRight(remote.FetchOut, "\n"), "\n")
		}
		return rs
	}
	ref := "refs/remotes/origin/" + r.target
	if !ok(r.in.Cwd, "show-ref", "--verify", "--quiet", ref) {
		rs.state = "absent"
		return rs
	}
	rs.originSha = gitOut(r.in.Cwd, 0, "rev-parse", ref)
	count := func(rng string) int {
		n, _ := git.Count(gitOut(r.in.Cwd, slow, "rev-list", "--count", rng))
		return n
	}
	rs.behind, rs.ahead = count(r.headSha+".."+rs.originSha), count(rs.originSha+".."+r.headSha)
	switch {
	case rs.ahead != 0 && rs.behind != 0:
		rs.state = "diverged"
	case rs.behind != 0:
		rs.state = "behind"
	case rs.ahead != 0:
		rs.state = "ahead"
	}
	return rs
}

// originTaken は、origin に同じ名前・同じ識別子になるブランチがあれば止める（fetch が通ったときだけ見る）。一覧を取れなければ
// リモート未確認にする。
func (r *run) originTaken(remote Remote, rs *remoteState) bool {
	if !remote.FetchOK {
		return true
	}
	if !remote.HeadsOK {
		why, _, _ := strings.Cut(strings.TrimRight(remote.HeadsErr, "\n"), "\n")
		if why == "" {
			why = "理由不明"
		}
		rs.state, rs.why = "unverified", "origin のブランチ一覧を取れなかった（"+why+"）"
		return true
	}
	var names []string
	for _, line := range lines(remote.Heads) {
		if m := headLine.FindStringSubmatch(line); m != nil {
			names = append(names, m[1])
		}
	}
	for _, n := range names {
		if n == r.name {
			r.o.Out("ワークツリーは切っていない。origin にブランチ " + r.name + " が既にある（MR の元ブランチかもしれない）。")
			r.o.Out("既にあるブランチは、MR を探して採用してよいか確かめてから出す。ccnavi-start.sh で始める:")
			r.startHint()
			r.otherName()
			r.o.Val("reason", "origin-same-name")
			r.o.Exit(exitUnmet)
			return false
		}
	}
	return r.sameID(names, "origin")
}

// candidate は書きかけを含むスナップショット。ユーザの index とワークツリーには触れない（一時的な index で作る）。
// 日付と名前を固定するので、木と親が同じなら同じ sha になる（確認コードに入れて、確認したあとの編集を検出する）。
func (r *run) candidate() (string, bool) {
	dir := ospath.Join(r.in.Root, "logs", "state")
	_ = fsio.MakeDirs(dir)
	index := ospath.Join(dir, "worktree-index-"+strconv.Itoa(os.Getpid()))
	_ = os.Remove(index)
	defer os.Remove(index)
	run := func(args ...string) (string, bool) {
		done := git.Run(r.mainTree, args, git.Options{Timeout: slow, IndexFile: index, Env: []string{
			"GIT_AUTHOR_NAME=ccnavi", "GIT_AUTHOR_EMAIL=ccnavi@localhost", "GIT_AUTHOR_DATE=1970-01-01T00:00:00+0000",
			"GIT_COMMITTER_NAME=ccnavi", "GIT_COMMITTER_EMAIL=ccnavi@localhost", "GIT_COMMITTER_DATE=1970-01-01T00:00:00+0000"}})
		return strings.TrimSpace(done.Out), done.OK()
	}
	// 除外指定つきの add は、除く先が .gitignore 済みだと終了コード 1 になる。素の add のあとで index から除く。
	// 他のワークツリー（.claude/worktrees/ の下）は、埋め込みリポジトリとして木に入るので取り込まない。
	for _, args := range [][]string{{"read-tree", r.headSha}, {"add", "-A"},
		{"rm", "-r", "--cached", "-q", "--ignore-unmatch", "--", ".claude/worktrees"}} {
		if _, done := run(args...); !done {
			return "", false
		}
	}
	tree, done := run("write-tree")
	if !done {
		return "", false
	}
	return run("-c", "commit.gpgsign=false", "commit-tree", tree, "-p", r.headSha, "-m", "ccnavi: 切った時点の書きかけ")
}

// cut は、ずれと書きかけを確かめ、確認が要れば報告して止め、要らなければ（了承されていれば）記録を書いて切る。
func (r *run) cut(remote Remote) {
	rs := r.compare(remote)
	if !r.originTaken(remote, &rs) {
		return
	}
	if rs.state == "unverified" && ok(r.in.Cwd, "show-ref", "--verify", "--quiet", "refs/remotes/origin/"+r.name) {
		r.o.Out("ワークツリーは切っていない。origin にブランチ " + r.name + " がある（手元に残る追跡ブランチで分かった。MR の元ブランチかもしれない）。")
		r.o.Out("既にあるブランチは、MR を探して採用してよいか確かめてから出す。ccnavi-start.sh で始める:")
		r.startHint()
		r.o.Val("reason", "origin-same-name")
		r.o.Exit(exitUnmet)
		return
	}
	c6 := rs.state != "even"
	cand := ""
	if r.c7 {
		var made bool
		if cand, made = r.candidate(); !made || cand == "" {
			r.fail("snapshot-failed", "書きかけのスナップショットを作れませんでした（"+r.mainTree+"）。ユーザに確かめてください。", exitUnmet)
			return
		}
	}
	origin := rs.originSha
	if origin == "" {
		origin = rs.state
	}
	confirm := code("repo=" + r.commonDir + "|name=" + r.name + "|target=" + r.target + "|head=" + r.headSha + "|origin=" + origin +
		"|rs=" + rs.state + "|default=" + r.integ + "|cwd=" + or(r.topBranch, "-") + "|cand=" + or(cand, "-"))
	needAccept := r.c5 || c6 || r.c7
	switch {
	case r.c7 && r.wipMode == "":
		r.ask(rs, cand, confirm, needAccept, "wip")
		return
	case r.c4 && r.baseArg == "":
		r.ask(rs, cand, confirm, needAccept, "base")
		return
	case needAccept && r.accept == "":
		r.ask(rs, cand, confirm, needAccept, "accept")
		return
	case needAccept && r.accept != confirm:
		r.ask(rs, cand, confirm, needAccept, "code")
		return
	}
	s, wip := r.headSha, false
	if r.c7 && r.wipMode == "copy" {
		s, wip = cand, true
	}
	sShort := short(r.in.Cwd, s)
	confirmWord := "-"
	if needAccept {
		confirmWord = confirm
	}
	wipWord := "no"
	if wip {
		wipWord = "yes"
	}
	path, written := r.writeRecord(r.commonDir, "base_branch", r.target, "base_sha", r.headSha, "snap_sha", s, "snap_wip", wipWord,
		"side_by_side", r.sideWord(), "main_tree", r.mainTree, "common_dir", r.commonDir, "confirm", confirmWord)
	if !written {
		return
	}
	refS, refPending := "refs/ccnavi/direct/"+r.name+"/s", "refs/ccnavi/direct/"+r.name+"/pending"
	undo := func() {
		fsio.Remove(path)
		ok(r.in.Cwd, "update-ref", "-d", refS)
	}
	if !ok(r.in.Cwd, "update-ref", refS, s) {
		undo()
		r.fail("ref-failed", "S の ref（"+refS+"）を置けません。", exitUnmet)
		return
	}
	ok(r.in.Cwd, "update-ref", "-d", refPending)
	if said, added := treeadd.Add(r.in.Root, r.in.Cwd, r.dest, r.name, s); !added {
		r.o.Err(said)
		undo()
		r.fail("worktree-add-refused", "ccnavi-git.sh worktree add が通りませんでした（上の出力の案内に従ってください）。記録と ref は消しました。", exitUnmet)
		return
	}
	r.done(rs, c6, s, sShort, wip)
}

// or は a が空なら b。
func or(a, b string) string {
	if a == "" {
		return b
	}
	return a
}

// done は切ったあとの報告。
func (r *run) done(rs remoteState, c6 bool, s, sShort string, wip bool) {
	r.o.Out("ワークツリーを切った: " + r.dest)
	r.o.Out("ブランチ: " + r.name + "（起点 " + sShort + "）")
	r.o.Out("取り込み先: " + r.target + "（" + r.mainTree + "）")
	if wip {
		r.o.Out("起点: " + sShort + "（確認した時点の書きかけを含む。確認したあとの編集はコピーされない）")
	} else {
		r.o.Out("起点: " + sShort + "（" + r.target + " の HEAD。書きかけは含まない）")
	}
	r.sideLine()
	if c6 {
		switch rs.state {
		case "behind", "diverged":
			r.o.Out(texts.Say(r.in.Root, "NOTE_WORKTREE_REMOTE_BEHIND", texts.Values{"target": r.target, "behind": strconv.Itoa(rs.behind), "main_tree": r.mainTree, "branch": r.target}, nil))
		case "ahead":
			r.o.Out("案内: 手元の " + r.target + " は origin/" + r.target + " より " + strconv.Itoa(rs.ahead) + " 件進んでいる（まだ送っていない）")
		case "absent":
			r.o.Out("案内: origin に " + r.target + " が無い。成果は手元の " + r.target + " に載る")
		case "unverified":
			r.o.Out(texts.Say(r.in.Root, "NOTE_WORKTREE_REMOTE_UNVERIFIED", texts.Values{"target": r.target}, nil))
		}
	}
	// ワークスペースのルートで実行され、projects/ の下にリポジトリがあるときは、projects/ の変更なら cd してから実行し直す
	// よう案内する。断らない（ワークスペース自身の作業もルートから実行するのが正しく、どのプロジェクトの話かは分からない）。
	if samePath(phys(r.top), phys(r.in.Root)) {
		names, _ := ospath.DiskListdir(ospath.Join(r.in.Root, "projects"))
		for _, n := range names {
			if ospath.DiskLexists(ospath.Join(r.in.Root, "projects", n, ".git")) {
				r.o.Out(texts.Say(r.in.Root, "NOTE_WORKTREE_PROJECTS_HINT", nil, nil))
				break
			}
		}
	}
	// 親のワークツリーから切り替えたとき（承認前の提案だけ。それ以外は切る前に断った）、承認待ちの提案・MR は取り下げられずに残る。
	parent := ospath.Basename(r.top)
	if f := r.in.Leftover(r.top, parent); f.Leftover {
		r.o.Out(texts.Say(r.in.Root, "NOTE_WORKTREE_LEFTOVER", texts.Values{"parent": parent}, nil))
		if f.Proposal {
			r.o.Out(texts.Say(r.in.Root, "NOTE_WORKTREE_LEFTOVER_PROPOSAL", texts.Values{"parent": parent}, nil))
		}
		if f.MR != "" {
			r.o.Out(texts.Say(r.in.Root, "NOTE_WORKTREE_LEFTOVER_MR", texts.Values{"mr": "#" + f.MR}, nil))
		}
	}
	if r.others != "" {
		r.o.Out(texts.Say(r.in.Root, "NOTE_WORKTREE_OTHER_ACTIVE", texts.Values{"others": r.others}, nil))
	}
	r.next()
	r.o.Val("snap", s)
}
