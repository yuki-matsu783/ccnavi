package hint

import (
	"path/filepath"
	"regexp"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/model/fields"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/ticket"
)

// 理由コード（設計 付録 A）。記録（decisions.jsonl）の code には残らない。
const (
	codeParentKey    = "NOTICE_PROPOSAL_PARENT_KEY"
	codeParentFields = "NOTICE_PROPOSAL_PARENT_FIELDS"
)

// parentFields は親の提案が持つ欄。`project:` は置き場が決めるので数えない。
var parentFields = []string{"issue", fields.MRKey, "plan", ticket.PhasesKey}

// createdType は Write の tool_response の type のうち、新しく作ったもの。同じ提案を直すたびには出さない。
const createdType = "create"

// misplacedWorktree はワークツリーが想定外の位置（`projects/<名前>/.claude/worktrees/<識別子>/`）にあるときの、
// プロジェクトのツリーの中での頭。その形でも案内を出す。畳んだパス（folded）に当てるので、置き場も畳んでおく。
var misplacedWorktree = regexp.MustCompile(`^` + regexp.QuoteMeta(folded(tree.WorktreesDir)) + `/[^/]+/`)

// folded はパスを大文字小文字を区別しない比べの形にし、区切りを `/` に揃える。
func folded(path string) string { return ospath.Slashed(pathcase.Fold(filepath.ToSlash(path))) }

// ProposalHint は、親の提案（ツリーの提案の置き場の `todo/` の直下の md）を Write で新しく作ったときの書式の誤りの
// 案内。出さないときは空。
//
// 手で書くと、子の例を写して親に `parent:` と `phase:` を書いたり、`plan:`・`phases:`・`mr:` を落としたりする。承認の
// 検査は後から言うが、書いた時点で言えば直しが 1 手で済む。書式の規則そのものは持たず、子の形は ids.ParseChild を引く。
func ProposalHint(conf *config.Settings, root string, in hookio.Input) string {
	if in.ToolName != config.ToolWrite || in.ToolResponse.String("type") != createdType {
		return ""
	}
	raw := ""
	if v := in.ToolInput.Value("file_path"); pyfmt.Truthy(v) {
		raw = pyfmt.Str(v)
	}
	if !pathcase.HasExt(raw, config.MdExt) || conf.Tickets == "" {
		return ""
	}
	cwd := in.Cwd
	if cwd == "" {
		cwd = root
	}
	written := ospath.Abspath(ospath.Join(cwd, raw))
	where, ok := tree.TreeOf(root, fsio.FullPath(raw, cwd), conf.Projects)
	if !ok {
		return ""
	}
	rel := tree.Relative(where, written)
	folder := folded(strings.Trim(conf.Tickets, "/") + "/" + config.TodoDir + "/")
	inner := misplacedWorktree.ReplaceAllString(folded(rel), "")
	if !strings.HasPrefix(inner, folder) || strings.Contains(inner[len(folder):], "/") {
		return ""
	}
	text, err := fsio.ReadDiskText(written, fsio.Strict)
	if err != nil {
		return ""
	}
	front, err := ticket.FrontMapping(text)
	if err != nil || front == nil {
		return ""
	}
	name := ospath.Strip(front.Text("ticket"))
	if name == "" {
		name, _ = ospath.Splitext(ospath.Basename(written))
	}
	parent := ospath.Strip(front.Text("parent"))
	launcher := config.ScriptCommand(root, config.LauncherScript)
	scaffold := "`" + launcher + " ticket new <識別子> --title <タイトル> --plan <型,…> --issue <値> --mr <番号>`"
	if parent != "" {
		if _, child := ids.ParseChild(name); child {
			// 子の形。親の欄は求めない。別の親を指す誤りは読み込み（ticket）のエラー文が言う。
			return ""
		}
		return hookio.Tagged(codeParentKey, rel) + " 識別子 " + name + " は子の形ではないのに `parent:` が" +
			"あります。親の提案なら `parent:` と `phase:` の行を消してください（子だけの欄）。雛形は " +
			scaffold + " が作ります。"
	}
	var missing []string
	for _, key := range parentFields {
		if !pyfmt.Truthy(front.Get(key)) {
			missing = append(missing, "`"+key+":`")
		}
	}
	if len(missing) == 0 {
		return ""
	}
	return hookio.Tagged(codeParentFields, rel) + " 親の提案に " + strings.Join(missing, "・") +
		" がありません。手で足さず、" + scaffold + " で雛形を作るか、`" + launcher +
		" --plan-order <親> --fill-phases` で `phases:` を差し込んでください。書式は `" + launcher +
		" --docs --keyword 提案書式` で引く。"
}
