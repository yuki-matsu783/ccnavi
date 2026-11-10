package cli

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"os"
	"regexp"
	"strconv"
	"strings"

	"ccnavi/internal/infra/argparse"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fault"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/model/useronly"
	"ccnavi/internal/tickets/model/via"
)

// input は標準入力。payload と対話の答えは同じ読み手から読む（読み手を 2 つ作ると、先読みした分を片方が失う）。
// ターミナルかどうかは元のファイルで見る。
type input struct {
	raw io.Reader
	buf *bufio.Reader
}

func newInput(r io.Reader) *input { return &input{raw: r, buf: bufio.NewReader(r)} }

// terminal は標準入力がターミナルか。
func (in *input) terminal() bool {
	f, ok := in.raw.(*os.File)
	return ok && ospath.IsTerminal(f.Fd())
}

// actorMaxLength は `--actor` の長さの上限（字）。
const actorMaxLength = 100

// actorForm は `--actor` の形。拡張がホストから読むアカウント名の形（`github.ts` の NAME）と同じ。
var actorForm = regexp.MustCompile(`\A[A-Za-z0-9_.-]{1,` + strconv.Itoa(actorMaxLength) + `}\z`)

// Run は 1 回の起動を処理し、終了コードを返す。マーカーに書く経路（via）の既定はエージェントが sh から打つ
// サブコマンド（cli）で、ユーザの判断の経路と hook は枝の中で差し替える。
func Run(stdin io.Reader, stdout, stderr io.Writer, argv []string) (code int) {
	defer via.Session(via.CLI, "")()
	// 経路が決まってからの hook の失敗は parsed の受け（内部エラーの応答）で終わる。ここに来るのは、打った人が
	// 見られる経路の想定外の失敗と、経路が決まる前（引数を読む途中）の失敗だけ。
	defer onPanic(func(r any, f failure) { code = runCrashed(stdout, stderr, argv, r, f) })
	return run(newInput(stdin), stdout, stderr, argv)
}

// runCrashed は経路が決まる前に落ちた回の終わり方。hook として起こされた形（引数なし）なら、経路が決まってからと
// 同じく内部エラーの応答で終える。終了コード 1 で終えると、Claude Code は PreToolUse の 1 を「止めない」と読む。
// 導入スクリプトは hook の command に引数を書かないので、引数なしを hook とみなす。設定はまだ読んでいないので、
// 記録は既定の置き場に書く。
func runCrashed(stdout, stderr io.Writer, argv []string, r any, f failure) int {
	if len(argv) == 0 {
		root := defaultRoot()
		return hookCrashed(stdout, stderr, unloaded(root), root, &hookSoFar{mode: config.Enable}, f)
	}
	fmt.Fprintf(stderr, "ccnavi: 想定外の失敗で止まった: %v\n", r)
	return config.ExitError
}

// finish は error を返す操作の終わり方。読み手に届く理由を運ぶ error（fault.Detail）は、文面ファイルの文で言う。
// 書く側の置き場（親のワークツリー）が決まらないときも、ほかのコピーへは書かないので止める。
func finish(stderr io.Writer, root string, code int, err error) int {
	if err == nil {
		return code
	}
	if d, ok := fault.Of(err); ok && d.ID != "" {
		fmt.Fprintf(stderr, "ccnavi: %s\n", texts.SayFault(root, err))
	} else {
		fmt.Fprintf(stderr, "ccnavi: 想定外の失敗で止まった: %v\n", err)
	}
	return config.ExitError
}

// okIf は成否を終了コードにする。成功は ExitOK、失敗は ExitError。
func okIf(ok bool) int {
	if ok {
		return config.ExitOK
	}
	return config.ExitError
}

// run は引数を読み、組み合わせを検査してから、実際の処理へ渡す。
//
// ユーザ専用の入口（`ccnavi user <操作>`）は今の内部の形に読み替える。入口から来たかどうかは引数の結果に入れない
// （オプションで偽装できないようにするため）。印（`--entry`）の無い入口は、parsed が設定を読んでから断る
// （テストの逃げ道を設定で見るため）。
func run(in *input, stdout, stderr io.Writer, argv []string) int {
	// ユーザ専用の入口（`ccnavi user …`）の印と動詞を取り除き、内部の形の引数に直す。
	argv, marker, err := useronly.TakeEntry(argv)
	var entry *useronly.Verb
	if err == nil {
		argv, entry, err = useronly.Rewrite(argv)
	}
	if err != nil {
		// 入口の使い方の誤り（useronly.EntryError）。設定は読む前なので、文面は既定のルートから引く。
		fmt.Fprintf(stderr, "ccnavi: %s\n", texts.SayFault(defaultRoot(), err))
		return config.ExitBlock
	}
	// 引数を読む。`--test` の語と `--help`・`--json` の位置は、argparse に渡す前に整える（どの順に書いても同じに読むため）。
	parser := newParser()
	rest, testWords := takeTestWords(jsonOutOfTest(helpOfTest(argv)))
	ns, err := parser.Parse(rest)
	if err != nil {
		var bad *argparse.Error
		if errors.As(err, &bad) {
			io.WriteString(stderr, parser.ErrorText(bad.What))
		}
		return config.ExitError
	}
	// `--test` は後ろの 2 語（TOOL と SUBJECT）を取る。整えるときに取り出した語をここで戻す。
	a := opts{ns, parser}
	if testWords != nil && a.IsNone("test") {
		a.Namespace["test"] = []any{testWords[0], testWords[1]}
	}
	if a.Truthy("help") {
		// ヘルプは何も動かさないので、ユーザ専用のオプションと一緒でも（`--help --prune`）断らずに節を出す。
		io.WriteString(stderr, helpText(a))
		return config.ExitError
	}
	// ユーザ専用のオプションは、`user` の入口の外では断る。見るだけの形（`--agree --preview` など）は通す。
	if entry == nil {
		if refused := useronly.Refused(func(o string) bool { return !unset(a.Namespace[destOf(o)]) }); refused != "" {
			fmt.Fprintf(stderr, "ccnavi: %s\n", refused)
			return config.ExitBlock
		}
	}
	if refused := usageRefusal(parser, a); refused != "" {
		fmt.Fprintf(stderr, "ccnavi: %s\n", refused)
		return config.ExitError
	}
	// 入口の印（--entry）が無いのにユーザ専用の入口から来た回は、unmarked に残す。parsed が設定を読んでから断る。
	var unmarked *useronly.Verb
	if entry != nil && marker == "" {
		unmarked = entry
	}
	// 書き込みの記録を取る回（試験が使う）は、通常の経路を記録の枠で包む。--version は何も書かないので包まない。
	if a.Str("record_writes") != "" && !a.Truthy("version") {
		return recordedRun(in, stdout, stderr, parser, a, unmarked)
	}
	return parsed(in, stdout, stderr, parser, a, unmarked)
}

// usageRefusal は設定を読む前に分かる組み合わせの誤り。正しければ空。
func usageRefusal(parser *argparse.Parser, a opts) string {
	if a.Truthy("all") {
		return "--all は --help と一緒にだけ指定できます（全文を出す）。"
	}
	if refused := testUsage(parser, a); refused != "" {
		return refused
	}
	sideways := a.Truthy("handoff") || a.Truthy("mirror")
	if a.Str("yes") == bareYes && !sideways {
		return "--yes には識別子を渡す（値なしの --yes は user handoff と user mirror だけ）"
	}
	if a.Str("record_tree") != "" && a.Str("record_writes") == "" {
		return "--record-tree は --record-writes と一緒に指定してください。"
	}
	actor := a.Str("actor")
	if actor != "" && !actorForm.MatchString(actor) {
		return "--actor にはホストのアカウント名（英数字と _ . - の 1〜" + strconv.Itoa(actorMaxLength) + " 字）を渡す"
	}
	if actor != "" && !a.head("review", "confirm") && !decideWrites(a) {
		return "--actor は review confirm か、反映する形の decide（--reviewed <N> --accept-unresolved）と一緒に指定してください。"
	}
	// 取り込みとミラーの `--via` の組み合わせは、それぞれの入口が見る（`--json` なら JSON で答える）。
	route := a.Str("via")
	if !sideways && route != "" && (actor == "" || !decideWrites(a)) {
		return "--via は decide の --actor と一緒に指定してください。"
	}
	if route != "" && route != string(via.Terminal) && route != string(via.Board) {
		return fmt.Sprintf("--via に渡せるのは %s か %s", via.Terminal, via.Board)
	}
	// ターミナルで 1 件ずつ選ぶ形（--yes 無し）はボードの経路でない。
	if route == string(via.Board) && a.Str("yes") == "" && !sideways {
		return "--via board は --yes（ボードで押した選択）と一緒に指定してください。"
	}
	return ""
}

// testUsage は `--test` と、その payload の欄の組み合わせの誤り。どれも黙って無視せずに断る。無視すると、
// 試したつもりの呼び出しと違う答えを読む。
func testUsage(parser *argparse.Parser, a opts) string {
	if a.IsNone("test") {
		if a.Str("permission_mode") != "" {
			return "--permission-mode は --test でだけ指定できる"
		}
		if a.Str("agent_id") != "" {
			return "--agent-id は --test でだけ指定できる"
		}
		return ""
	}
	for _, word := range a.Strings("test") {
		name, _, _ := strings.Cut(word, "=")
		if parser.HasOption(name) {
			return "--test の TOOL と SUBJECT にオプション（" + word + "）が入った。" +
				"オプションは --test <TOOL> <SUBJECT> の前か後ろに置く"
		}
	}
	if cmd := a.command(); len(cmd) > 0 {
		return "--test は TOOL と SUBJECT の 2 語だけを取る（余った語: " + strings.Join(cmd, " ") + "）"
	}
	return ""
}

// decideWrites は書く形の decide か（`--reviewed N --accept-unresolved` の、見るだけでないもの）。
func decideWrites(a opts) bool {
	return !a.IsNone("reviewed") && a.Truthy("accept_unresolved") && !a.Truthy("preview") && !a.Truthy("check") &&
		a.Str("choose_out") == "" && a.Str("accept_plan") == ""
}
