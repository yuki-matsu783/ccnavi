package useronly

import (
	"errors"
	"fmt"
	"slices"
)

// group はオプションと語をまとめる名前。ボードの組み合わせと、引数を読み切れない形の判定が使う。
type group string

const (
	groupYes           group = "yes"
	groupApproval      group = "approval"
	groupPrune         group = "prune"
	groupHandoff       group = "handoff"
	groupGuard         group = "guard"
	groupVia           group = "via"
	groupAbandon       group = "abandon"
	groupChoices       group = "choices"
	groupDigest        group = "digest"
	groupUser          group = "user"
	groupUserBoardVerb group = "user-board-verb"
	groupBoard         group = "board"
)

// option はユーザ専用のオプション 1 つ。
type option struct {
	name string
	// where は空なら実行ファイルのオプション。sh のオプションならスクリプトの名前（拡張子なし）。
	where string
	// cli は実行ファイルを直に呼ぶ形（`launcher … <オプション>`）として止めるか。
	cli bool
	// previewExempt は `--preview` を単独の語として隣に置いた形（見るだけ）を通すか。
	previewExempt bool
	groups        []group
	// gated は `user` の入口の外で受けたら実行ファイルが断るか（Refused）。
	gated bool
	// unlessPreview は `--preview` と一緒なら断らないか。
	unlessPreview bool
}

const (
	optYes     = "--yes"
	optPreview = "--preview"
	optPrune   = "--prune"
	reviewSh   = "ccnavi-review"
)

var flags = []option{
	{name: optYes, cli: true, gated: true, groups: []group{groupYes}},
	{name: "--agree", cli: true, previewExempt: true, gated: true, unlessPreview: true, groups: []group{groupApproval}},
	// 見るだけの形（`--reviewed … --accept-unresolved --preview`）は `ccnavi-review.sh decide <N> --preview` が
	// 内部で呼ぶので、`user` の外でも断らない。
	{name: "--reviewed", cli: true, gated: true, unlessPreview: true, groups: []group{groupApproval}},
	{name: "--close-early", cli: true, gated: true},
	// 記録を消す形は PruneForm が別の deny で止める（cliForms には入れない）。
	{name: optPrune, previewExempt: true, gated: true, unlessPreview: true, groups: []group{groupPrune}},
	// 保護対象の下書きの取り込みと共通レイヤーのミラーの同期。見るだけの形（`--yes` なし）もユーザ専用
	// （エージェントは `ccnavi-stage.sh --commands` で下書きの状態を見られる）。
	{name: "--handoff", cli: true, gated: true, groups: []group{groupHandoff}},
	{name: "--mirror", cli: true, gated: true, groups: []group{groupHandoff}},
	{name: "--guard-ticket-approval", groups: []group{groupGuard}},
	// ボードの経路の印（`--via board`）。単独では止めないが、`board` の語と並べば止める（boardCombos）。
	{name: "--via", groups: []group{groupVia}},
	// 放棄した親の片付けの記録を消す `worktree retire --abandon`。文字どおりの形は subs が止める。
	{name: "--abandon", groups: []group{groupAbandon}},
	{name: "--choices", where: reviewSh, groups: []group{groupChoices}},
	{name: "--digest", where: reviewSh, groups: []group{groupDigest}},
}

// sub は実行ファイルを直に呼ぶ形のサブコマンド（`<group> [--] <name>`）。
type sub struct {
	groups []string
	names  []string
}

var subs = []sub{
	// チケットの状態とレビューの操作。
	{[]string{"ticket", "review"}, []string{
		"start", "finish", "cancel", "approval-request", "consolidate", "record-risk", "prepare", "requested", "confirm", "ready",
	}},
	// 親を閉じたときの片付け。ブランチを `branch -D` で消すので、呼ぶのは sh の中か、ユーザが `!` を付けて打つときだけ
	// （sh の中の呼び出しは hook を通らない）。
	{[]string{"sync"}, []string{"tidy"}},
	// 操作記録の書き込み。偽の送信済みを書かせない。読む副命令（pending・show）は止めない。
	{[]string{"ops"}, []string{"begin", "stage-set", "applied", "done", "retire", "mark-sent"}},
	{[]string{"worktree"}, []string{"retire", "branch"}},
}

// boardCombos はボードで押した操作のオプションの組み合わせ。どのグループからも 1 つずつ並んだら止める。
// 並びの順に見て、最初に揃った組み合わせで文面を決める。名前に依らず止めるので、ターミナルを求めない
// 経路はこれが唯一の防御になる。
var boardCombos = [][]group{
	{groupApproval, groupYes},
	{groupUser, groupUserBoardVerb, groupYes},
	{groupChoices, groupDigest},
	// 印だけでも止める（名前を変えたコピーに `--via board` だけを渡す、`--yes` を展開で隠す形）。
	{groupVia, groupBoard},
}

// Op はターミナルの前のユーザを求める操作。
type Op string

const (
	OpApprove    Op = "approve"
	OpReviewed   Op = "reviewed"
	OpCloseEarly Op = "close-early"
	OpPrune      Op = "prune"
	OpHandoff    Op = "handoff"
	OpMirror     Op = "mirror"
)

// Verb は `ccnavi user <操作>` の 1 つ。
type Verb struct {
	// words は `user` の後ろに続く語。
	words []string
	// internal は読み替えた先の内部のオプション。
	internal []string
	op       Op
	// value は内部の最後のオプションが次の語を値に取るか（`user reviewed 3` → `--reviewed 3`）。
	value bool
	// Script はユーザが打つ入口の sh（`ccnavi-user-<Script>.sh`）。
	Script string
	// terminal は実行ファイルがターミナルを求めるときに標準エラーに出す、ユーザが打つ形。
	terminal string
}

// verbs の読み替え先は今の内部の形で、実行ファイルの引数の文法は変えない。続く語（ツリーからの相対パス、
// プロジェクトの名前）は位置引数としてそのまま渡る。
var verbs = []Verb{
	{words: []string{"agree"}, internal: []string{"--agree"}, op: OpApprove, Script: "agree", terminal: "ccnavi-user-agree.sh"},
	{words: []string{"reviewed"}, internal: []string{"--reviewed"}, op: OpReviewed, value: true, Script: "decide", terminal: "ccnavi-user-decide.sh"},
	{words: []string{"close-early"}, internal: []string{"--close-early"}, op: OpCloseEarly, Script: "close-early", terminal: "ccnavi-user-close-early.sh"},
	{words: []string{"prune"}, internal: []string{optPrune}, op: OpPrune, Script: "prune", terminal: "ccnavi-user-prune.sh"},
	{words: []string{"handoff"}, internal: []string{"--handoff"}, op: OpHandoff, Script: "handoff", terminal: "ccnavi-user-handoff.sh --yes"},
	{words: []string{"mirror"}, internal: []string{"--mirror"}, op: OpMirror, Script: "mirror", terminal: "ccnavi-user-mirror.sh --yes"},
}

// errNotTerminal はターミナルを求めない操作を Terminal に渡したこと。
var errNotTerminal = errors.New("ターミナルを求める操作ではない")

// Terminal は実行ファイルがターミナルを求める操作の、標準エラーに出す表示名（ユーザが打つ sh）。
func Terminal(op Op) (string, error) {
	for _, v := range verbs {
		if v.op == op {
			return v.terminal, nil
		}
	}
	return "", fmt.Errorf("%s: %w", op, errNotTerminal)
}

const (
	userWord = "user"
	// UserScriptPrefix はユーザ専用の sh の名前の頭。位置に依らず、コマンド行に名前が出たら止める。
	UserScriptPrefix = "ccnavi-user-"
	entryOption      = "--entry"
	entrySh          = "sh"
	entryBoard       = "board"
)

// wrapperOptions は sh と拡張が実行ファイルを呼ぶときに先に付ける値つきのオプション。`user` はこれらを
// 読み飛ばした後の最初の語。実行ファイルの読み飛ばしと hook の式の両方がこの一覧から作る。
var wrapperOptions = []string{"--root", "--cwd", "--record-writes", "--record-tree", entryOption}

var entryValues = []string{entrySh, entryBoard}

// wordGroups はオプションではない語のグループ（`user agree --yes` の `user` と `agree`）。
var wordGroups = map[group][]string{
	groupUser: {userWord},
	// ターミナルを求めない経路（`--yes`）を持つ操作。
	groupUserBoardVerb: {"agree", "reviewed", "handoff", "mirror"},
	// `--via` の値のうち、ターミナルを求めない経路を選ぶもの。
	groupBoard: {"board"},
}

// agentScripts はエージェントが sh で打てるスクリプト。止めている間も通す（Exempt）。
var agentScripts = []string{"ticket", "review", "git"}

// holdExemptLauncherFlags は止めている間も通す起動の sh のオプション。読むだけで、次に何をするかを調べる手段。
var holdExemptLauncherFlags = []string{"--docs"}

// roleKind はサブエージェントの役割の表の方式。
type roleKind int

const (
	// roleAllow は一覧の副命令だけ許し、それ以外（副命令が無い呼び出しを含む）は禁止。
	roleAllow roleKind = iota
	// roleDeny は一覧の副命令だけ禁止し、それ以外は許す。
	roleDeny
	// roleAll は副命令に関わらず禁止。
	roleAll
)

// subagentRoles はサブエージェントに許す副命令の表（スクリプト × 副命令）。止めるのは状態を動かす・公開する
// 操作で、読み取りは許す。git のラッパーは禁止する副命令だけを並べる（それ以外は wrapguard の検査に任せる）。
var subagentRoles = []struct {
	stem string
	kind roleKind
	subs []string
}{
	{"ccnavi-ticket", roleAllow, []string{"status", "next-id", "--help", "learn"}},
	// `decide <N> --preview` は review の下見で、許す。
	{"ccnavi-review", roleAllow, []string{"fetch", "origin", "merged", "--help", `decide\s+\S+\s+--preview`}},
	{"ccnavi-git", roleDeny, []string{"push", "worktree", "integrate", "integrate-done"}},
	// 起動の sh は中身が実行ファイルの全副命令に届くので、書き込まないものだけ許す。
	{"ccnavi-launcher", roleAllow, []string{"--docs", "--lint", "--test", "--explain", "--learnings", "--suggest", "--help"}},
	// 意味が近いかの確認をサブエージェントに任せるため、読み取りの副命令は許す。
	{"ccnavi-start", roleAllow, []string{"--list-open", "--check", "--help"}},
	{"ccnavi-sync", roleAll, nil},
	{"ccnavi-worktree", roleAll, nil},
	{"ccnavi-clean", roleAll, nil},
}

// anywhere はオプションの置き場を問わないことを names に言う。
const anywhere = "\x00"

// names はいずれかのグループに入る語とオプションの名前を表の順に返す。where は置き場（空は実行ファイル、
// anywhere はどこでも）。グループが空になるのは表の書き間違いで、テストが見る（空のまま組むと何にでも当たる）。
func names(where string, groups ...group) []string {
	var found []string
	add := func(w string) {
		if !slices.Contains(found, w) {
			found = append(found, w)
		}
	}
	for _, g := range groups {
		for _, w := range wordGroups[g] {
			add(w)
		}
	}
	for _, f := range flags {
		if where != anywhere && where != f.where {
			continue
		}
		for _, g := range f.groups {
			if slices.Contains(groups, g) {
				add(f.name)
				break
			}
		}
	}
	return found
}
