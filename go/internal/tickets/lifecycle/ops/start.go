package ops

import (
	"fmt"
	"io"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/learning"
	"ccnavi/internal/tickets/lifecycle/closing"
	"ccnavi/internal/tickets/lifecycle/plan"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/sync/mirror"
)

const (
	textStartChild  = "NEXT_OPS_START_CHILD"
	textStartSkill  = "NOTE_OPS_START_SKILL"
	textStartParent = "NEXT_OPS_START_PARENT"
)

// Start は doing/ の承認済みチケットに、着手の時刻と基準点（ワークツリーの HEAD）を書く。ワークツリーは、承認済みの project の元リポジトリから切られていなければならない。元リポジトリが違えば、判定はそのツリーの元リポジトリで行われ、チケットと食い違う。子ならフローのハッシュも記録する。着手のあとのフローの書き換えは、SubagentStart・SubagentStop が知らせる。
func Start(stdout, stderr io.Writer, conf *config.Settings, root, id string) int {
	found := startGate(stderr, conf, root, id)
	if found == nil {
		return 1
	}
	owner := tree.ProjectRoot(conf.Projects, found.Project)
	if owner == "" {
		owner = root
	}
	worktree := tree.WorktreePath(root, id)
	if !tree.IsWorktreeOf(owner, worktree) {
		project := found.Project
		if project == "" {
			project = "ワークスペース"
		}
		fmt.Fprintf(stderr, "ccnavi: %s のワークツリー %s が無いか、元リポジトリが承認済みチケットの project（%s）と違う。%s\n",
			id, worktree, project, createWords(root, found, worktree))
		return 1
	}
	sha := git.Head(worktree, git.Options{})
	if sha == "" {
		fmt.Fprintf(stderr, "ccnavi: %s の HEAD を読めない\n", worktree)
		return 1
	}
	at := fsio.Stamp()
	if failed := approval.UpdateFields(found.Path, [][2]string{{"started_at", at}, {"base_sha", sha}}); failed != "" {
		fmt.Fprintf(stderr, "ccnavi: %s に着手の欄を保存できませんでした: %s\n", id, failed)
		return 1
	}
	fmt.Fprintf(stdout, "OK: %s に着手した（%s / 基準点 %s）。置き場は %s/ のまま\n", found.ID, at, pyfmt.Head(sha, git.ShortSHA), place.Doing)
	if found.IsChild() {
		started := *found
		started.StartedAt = at
		if where, failed := flow.RecordDigest(conf, root, &started); failed != "" {
			fmt.Fprintf(stderr, "ccnavi: %s のフローのハッシュを記録できない（%s）。着手のあとの書き換えは知らせられない\n", id, failed)
		} else {
			fmt.Fprintf(stdout, "フローのハッシュを %s に記録した。承認済みチケットと同じくユーザがコミットする\n", where)
		}
	}
	if found.Project != "" && !found.IsChild() {
		// 共通レイヤーと工程スキルのコピーは、ここでは書かない（発言ごとの写しが置く）。
		io.WriteString(stdout, "共通レイヤーと工程スキルのプロジェクトへのコピーは、次のユーザの発言で置かれる\n")
	}
	childless := plan.ChildlessNotice(conf, root, found)
	for _, line := range childless {
		io.WriteString(stdout, line+"\n")
	}
	if found.IsChild() {
		io.WriteString(stdout, texts.Say(root, textStartChild, texts.Values{"ticket": found.ID}, nil)+"\n")
		if pt := closing.PhaseType(conf, root, found); pt != nil {
			if skill := mirror.Installed(root, pt.Skill); skill != "" {
				io.WriteString(stdout, texts.Say(root, textStartSkill, texts.Values{"skill": skill}, nil)+"\n")
			}
		}
	} else if len(childless) == 0 {
		io.WriteString(stdout, texts.Say(root, textStartParent, texts.Values{}, nil)+"\n")
	}
	if learning.Applies(conf, root, found) {
		fmt.Fprintf(stdout, "学びの蓄積: 閉じる前に '%s learn' を実行し、"+
			"%s/%s.md に気づきを書いて git add と git commit をする（無ければ learnings: none）\n",
			config.ScriptCommand(root, config.TicketWrapper), learning.LearningDir, found.ID)
	}
	return 0
}

// createWords は、ワークツリーを作る案内を組む。子は親のブランチから `-b <子>` で切る。親は、識別子の名前のブランチを作らない。親のブランチ（MR の元ブランチ）を ccnavi-start.sh で直接チェックアウトする。MR の採用の確認と取り込みも、そこで行う。
func createWords(root string, t *ticket.Ticket, worktree string) string {
	if t.IsChild() {
		where := ""
		if t.Project != "" {
			where = "projects/" + t.Project + " の中で "
		}
		return fmt.Sprintf("先に %s'%s worktree add \"%s\" -b %s' で作ってください",
			where, config.ScriptCommand(root, config.GitWrapper), worktree, t.ID)
	}
	return "親のワークツリーは '" + config.ScriptCommand(root, config.StartWrapper) + " --mr <MR の番号>'" +
		"（MR が無ければ '--issue <issue の番号>'）で、" +
		"親のブランチを直接チェックアウトして作ってください（識別子の名前のブランチは作らない）"
}
