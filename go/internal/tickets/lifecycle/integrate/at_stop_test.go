package integrate

import (
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/tickets/lifecycle/stop"
	"ccnavi/internal/tickets/testkit"
)

// 直接作業の Stop（DirectAtStop）が、促す・促さない・止めずに理由だけを言うを分ける条件を、場面ごとに見る。golden の
// ワークスペースは 1 つの統合先を共有するので、統合先の状態（途中の操作・origin の有無・切り替え）が違う場面はここで
// 別に組む。

// scene は直接作業のワークツリー 1 本と、origin を持つワークスペース。
type scene struct {
	t    *testing.T
	root string
	name string
	tree string
	snap string
}

func newScene(t *testing.T, name string) *scene {
	t.Helper()
	root := clock.Workspace(t)
	remote := filepath.Join(t.TempDir(), "remote.git")
	clock.Git(t, root, "init", "-q", "--bare", remote)
	clock.Git(t, root, "remote", "add", "origin", remote)
	clock.Git(t, root, "push", "-q", "origin", "main")
	clock.Git(t, root, "remote", "set-head", "origin", "main")
	return &scene{t: t, root: root, name: name, snap: strings.TrimSpace(clock.Git(t, root, "rev-parse", "HEAD")),
		tree: testkit.AddWorktree(t, root, name, "main")}
}

// commit は直接作業のワークツリーにコミットを 1 つ足す。
func (s *scene) commit(file string) {
	s.t.Helper()
	testkit.Put(s.t, filepath.Join(s.tree, "src", file), file+"\n")
	commitAll(s.t, s.tree, file)
}

// record は ccnavi-worktree.sh が置く直接作業の記録（版 4）。snap は起点、commonDir は記録が指す共通ディレクトリ（既定は自分）。
func (s *scene) record(snap string, commonDir ...string) {
	s.t.Helper()
	common := filepath.Join(s.root, ".git")
	if len(commonDir) > 0 {
		common = commonDir[0]
	}
	lines := []string{"version 4", "base_branch main", "snap_sha " + snap, "common_dir " + common}
	testkit.Put(s.t, filepath.Join(s.root, ".git", "ccnavi-direct", s.name), strings.Join(lines, "\n")+"\n")
}

func (s *scene) head() string {
	return strings.TrimSpace(clock.Git(s.t, s.tree, "rev-parse", "HEAD"))
}

func (s *scene) stop(session string) *Direct {
	s.t.Helper()
	return DirectAtStop(testkit.Settings(s.t, s.root), s.root, s.tree, session, time.Time{})
}

func TestDirectAtStopScenes(t *testing.T) {
	const (
		none   = "促さない"
		nudge  = "促す"
		reason = "止めずに理由を言う"
	)
	cases := []struct {
		name  string
		setup func(s *scene)
		kind  string
		// notice は止めずに言う文に含む語。ahead は促すときの入っていないコミットの数。
		notice string
		ahead  int
	}{
		{name: "起点が今の先頭なら、起点より後のコミットが無いので促さない", setup: func(s *scene) {
			s.commit("a")
			s.record(s.head())
		}, kind: none},
		{name: "起点より前のコミットは数えない", setup: func(s *scene) {
			s.commit("a")
			s.record(s.head())
			s.commit("b")
		}, kind: nudge, ahead: 1},
		{name: "統合先のツリーに未コミットの変更があっても促す", setup: func(s *scene) {
			s.commit("a")
			s.record(s.snap)
			testkit.Put(s.t, filepath.Join(s.root, "src", "someone.go"), "z\n")
		}, kind: nudge, ahead: 1},
		{name: "記録のある直接作業は、origin に同名のブランチがあっても促す", setup: func(s *scene) {
			s.commit("a")
			s.record(s.snap)
			clock.Git(s.t, s.root, "update-ref", "refs/remotes/origin/"+s.name, s.name)
		}, kind: nudge, ahead: 1},
		{name: "未コミットだけなら促さない", setup: func(s *scene) {
			s.record(s.snap)
			testkit.Put(s.t, filepath.Join(s.tree, "src", "draft.go"), "y\n")
		}, kind: none},
		{name: "取り込み先が切り替えられていて未コミットだけなら、取り込めない理由だけを言う", setup: func(s *scene) {
			s.record(s.snap)
			testkit.Put(s.t, filepath.Join(s.tree, "src", "draft.go"), "y\n")
			clock.Git(s.t, s.root, "switch", "-q", "-c", "other")
		}, kind: reason, notice: "今は取り込めません"},
		{name: "取り込みの途中に取り込み先が切り替わったら、途中だと言う", setup: func(s *scene) {
			s.commit("a")
			s.record(s.snap)
			testkit.Put(s.t, filepath.Join(s.root, ".git", RecordDir, s.name, markerFile), "version 4\n")
			clock.Git(s.t, s.root, "switch", "-q", "-c", "other")
		}, kind: reason, notice: "取り込みの途中ですが"},
		{name: "統合先のツリーが merge の途中なら、理由を言う", setup: func(s *scene) {
			s.commit("a")
			s.record(s.snap)
			testkit.Put(s.t, filepath.Join(s.root, ".git", "MERGE_HEAD"), strings.Repeat("0", 40)+"\n")
		}, kind: reason, notice: "MERGE_HEAD"},
		{name: "統合先のツリーが squash の途中なら、理由を言う", setup: func(s *scene) {
			s.commit("a")
			testkit.Put(s.t, filepath.Join(s.root, ".git", "SQUASH_MSG"), "squash\n")
		}, kind: reason, notice: "SQUASH_MSG"},
		{name: "別のリポジトリの記録なら、理由を言う", setup: func(s *scene) {
			s.commit("a")
			s.record(s.snap, filepath.Join(filepath.Dir(s.root), "elsewhere", ".git"))
		}, kind: reason, notice: "共通ディレクトリ"},
		{name: "origin が無ければ、統合先が決まらないと言う", setup: func(s *scene) {
			s.commit("a")
			clock.Git(s.t, s.root, "remote", "remove", "origin")
		}, kind: reason, notice: "origin が無いので統合先が決まらない"},
		{name: "origin に HEAD も main も無ければ、set-head を頼む", setup: func(s *scene) {
			s.commit("a")
			clock.Git(s.t, s.root, "remote", "set-head", "origin", "-d")
			clock.Git(s.t, s.root, "update-ref", "-d", "refs/remotes/origin/main")
		}, kind: reason, notice: "git remote set-head origin -a"},
		{name: "統合先の参照が無いときは、コミットだけでは数えられず促さない", setup: func(s *scene) {
			s.commit("a")
			clock.Git(s.t, s.root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/nonexistent")
		}, kind: none},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			s := newScene(t, "scratch")
			c.setup(s)
			d := s.stop("s1")
			switch {
			case c.kind == none:
				if d != nil {
					t.Errorf("促さないはずが返った: %+v", d)
				}
			case d == nil:
				t.Fatalf("%s はずが nil", c.kind)
			case c.kind == nudge:
				if d.Blocked != "" || d.ahead != c.ahead {
					t.Errorf("理由 %q・入っていないコミット %d、want 理由なし・%d", d.Blocked, d.ahead, c.ahead)
				}
			default:
				if note := BlockedNotice(d); d.Blocked == "" || !strings.Contains(note, c.notice) {
					t.Errorf("言う文 %q、want %q を含む", note, c.notice)
				}
			}
		})
	}
}

// integrate が断る名前（release 系・master・develop）の直接作業は、コミットも未コミットも促さない。
func TestDirectAtStopLeavesOutNamesThatIntegrateRefuses(t *testing.T) {
	for _, name := range []string{"release-x", "master", "develop", "release"} {
		s := newScene(t, name)
		s.commit("a")
		testkit.Put(t, filepath.Join(s.tree, "src", "draft.go"), "y\n")
		if d := s.stop("s-" + name); d != nil {
			t.Errorf("%s を促した: %+v", name, d)
		}
	}
}

// 同じ状態では 1 回だけ。促したあとの同じセッションと、cwd がワークスペースルートの回は促さない。
func TestDirectAtStopAsksOncePerStateAndNotAtTheRoot(t *testing.T) {
	s := newScene(t, "scratch")
	s.commit("a")
	s.record(s.snap)
	conf := testkit.Settings(t, s.root)
	first := DirectAtStop(conf, s.root, s.tree, "s1", time.Time{})
	if first == nil || first.Blocked != "" {
		t.Fatalf("1 回目: %+v", first)
	}
	if why := stop.RememberNudge(conf.State, "s1", first); why != "" {
		t.Fatal(why)
	}
	if again := DirectAtStop(conf, s.root, s.tree, "s1", time.Time{}); again != nil {
		t.Errorf("同じ状態で 2 回目を促した: %+v", again)
	}
	s.commit("b")
	if moved := DirectAtStop(conf, s.root, s.tree, "s1", time.Time{}); moved == nil || moved.ahead != 2 {
		t.Errorf("進んだ状態: %+v", moved)
	}
	if other := DirectAtStop(conf, s.root, s.tree, "s2", time.Time{}); other == nil {
		t.Error("別のセッションは自分の記録を持つ")
	}
	if rooted := DirectAtStop(conf, s.root, s.root, "", time.Time{}); rooted != nil {
		t.Errorf("ワークスペースルートで促した: %+v", rooted)
	}
}
