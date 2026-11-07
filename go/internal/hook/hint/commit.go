package hint

import (
	"io"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/hook/shellguard"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/ctxfile"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/lifecycle/integrate"
)

const (
	// commitOnceID は数えのキー（ctxfile の記録）の頭。ワークツリーの名前ごとに分ける。
	commitOnceID = rules.ReservedIDPrefix + "direct-commit-review"
	commitText   = "NOTICE_DIRECT_COMMIT"
	// recentCommit は直前の commit と見なす古さ。コミット前の検査が長引く分を見込む。
	recentCommit = 10 * time.Minute
	commitSub    = "commit"
)

// noCommit はコミットせずに内容だけを出すオプション（`git commit --dry-run` と、それを含意する出力形式）。
var noCommit = []string{"--dry-run", "--short", "--porcelain", "--long"}

var reflogAt = regexp.MustCompile(`@\{(\d+)\}`)

// reflogArgs は HEAD の履歴の最後の 1 件（時刻と操作）を読む git の引数。
var reflogArgs = []string{"reflog", "-1", "--date=unix", "--format=%gd|%gs"}

// CommitNotice は直接作業のコミットのあとの案内の文。出さないときは空。
func CommitNotice(stderr io.Writer, conf *config.Settings, root string, in hookio.Input) string {
	if in.AgentID != "" || conf.State == "" {
		return ""
	}
	command := ""
	if v := in.ToolInput.Value("command"); pyfmt.Truthy(v) {
		command = pyfmt.Str(v)
	}
	isCommit := in.ToolName == config.ToolBash && strings.Contains(pyfmt.Lower(command), pyfmt.Lower(config.GitWrapper))
	if !isCommit && !slices.Contains(config.AgentTools, in.ToolName) {
		return ""
	}
	deadline := time.Now().Add(git.CommitHintTimeout)
	var found []string
	if isCommit {
		for _, call := range shellguard.GitCalls(in.Cwd, command, shell.Read(command).Degraded, commitSub) {
			if !call.Known || slices.ContainsFunc(call.Args, func(a string) bool { return slices.Contains(noCommit, a) }) {
				continue
			}
			name := ""
			if here, ok := tree.TreeOf(root, call.Place, conf.Projects); ok {
				name = directName(conf, root, here, deadline)
			}
			found = append(found, name)
		}
	} else {
		for _, here := range tree.Worktrees(root, conf.Projects) {
			found = append(found, directName(conf, root, here, deadline))
		}
	}
	return carry(stderr, conf, root, in, found)
}

// carry は名前ごとに 1 回だけ文脈として文面を返す。大文字小文字だけが違う名前は 1 つに数える。
func carry(stderr io.Writer, conf *config.Settings, root string, in hookio.Input, found []string) string {
	var notes []ctxfile.Once
	seen := map[string]bool{}
	for _, name := range found {
		key := pathcase.Fold(name)
		if name == "" || seen[key] {
			continue
		}
		seen[key] = true
		notes = append(notes, ctxfile.Once{
			ID:   commitOnceID + rules.IDSeparator + key,
			Text: texts.Say(root, commitText, texts.Values{"name": name}, nil),
		})
	}
	return ctxfile.DeliverOnce(stderr, conf.State, in, notes...)
}

// directName は here が、いま直前にコミットが積まれた直接作業のワークツリーなら、その名前。違えば空。
func directName(conf *config.Settings, root string, here tree.Tree, deadline time.Time) string {
	if here.IsMain() || !justCommitted(here.Root, deadline) {
		return ""
	}
	if reason, unsure := integrate.DirectCheck(conf, root, here, deadline); reason != "" || unsure {
		return ""
	}
	return here.Name
}

// justCommitted は HEAD の履歴の最後が直前の commit か。読めなければ偽。
//
// `--dry-run` やコミットが積まれなかった呼び出しでは HEAD が動かず、最後の項目は古い commit のままなので時刻で切る。
// 近似なので、失敗した commit でも直前の 10 分以内に別の commit があれば真になる。案内は名前ごとに 1 回なので、
// 誤って出ても害は小さい。
func justCommitted(worktree string, deadline time.Time) bool {
	done := git.Run(worktree, reflogArgs, git.Options{Deadline: deadline})
	if !done.OK() {
		return false
	}
	when, what, _ := strings.Cut(ospath.Strip(done.Out), "|")
	m := reflogAt.FindStringSubmatch(when)
	if m == nil || !strings.HasPrefix(what, commitSub) {
		return false
	}
	stamp, err := strconv.ParseInt(m[1], 10, 64)
	if err != nil {
		return false
	}
	return time.Since(time.Unix(stamp, 0)) <= recentCommit
}
