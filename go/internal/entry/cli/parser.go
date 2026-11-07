package cli

import (
	"slices"
	"strings"

	"ccnavi/internal/infra/argparse"
)

// bareYes は値なしで渡された `--yes`。取り込み（`user handoff --yes`）とミラーの同期（`user mirror --yes`）だけが読む
// （ほかの経路の `--yes` は識別子を取る）。
const bareYes = "\x00bare"

// 前方一致は受けない（argparse は allow_abbrev=False で組む）。受けると `--close` や `--review` が `--close-early` /
// `--reviewed` として走り、省略せずに書いた形しか見ない組み込みの deny に止められない。help の本文は usage の自前の文。
func newParser() *argparse.Parser {
	p := argparse.New("ccnavi")
	str := func(flag string) { p.Add(argparse.Arg{Flags: []string{flag}, Default: ""}) }
	none := func(flag string, metavar ...string) {
		p.Add(argparse.Arg{Flags: []string{flag}, Metavar: metavar})
	}
	flag := func(flags ...string) { p.Add(argparse.Arg{Flags: flags, Kind: argparse.StoreTrue}) }
	list := func(flag string) { p.Add(argparse.Arg{Flags: []string{flag}, Kind: argparse.Append}) }
	integer := func(flag string) { p.Add(argparse.Arg{Flags: []string{flag}, Type: argparse.Int}) }

	// sh が計算して渡すパス。2 度来ていないかを見るので並びで受ける（wrapperFlags）。
	list("--root")
	str("--mode")
	str("--rules")
	none("--log")
	none("--state")
	str("--restore-if-deny")
	str("--guard-core-files")
	str("--guard-ticket-approval")
	str("--ticket-control")
	str("--result")
	flag("--lint")
	flag("--agree")
	flag("--preview")
	flag("--verify")
	none("--plan-order", "PARENT")
	flag("--fill-phases")
	none("--order", "JSON")
	flag("--write")
	none("--expect", "SHA")
	p.Add(argparse.Arg{Flags: []string{"--yes"}, Nargs: argparse.Optional, Const: bareYes, Default: ""})
	str("--digest")
	p.Add(argparse.Arg{Flags: []string{"--placed-out"}, Default: "", Metavar: []string{"FILE"}})
	p.Add(argparse.Arg{Flags: []string{"--test"}, Nargs: 2, Metavar: []string{"TOOL", "SUBJECT"}})
	str("--permission-mode")
	str("--agent-id")
	p.Add(argparse.Arg{Flags: []string{"--test-expectations"}, Default: "", Metavar: []string{"FILE"}})
	flag("--explain")
	flag("--prune")
	flag("--suggest")
	flag("--json")
	str("--tickets")
	none("--approved")
	none("--phases")
	none("--risk")
	none("--projects")
	str("--project-home")
	str("--project-rules-file")
	str("--project-phases-file")
	str("--project-risk-file")
	str("--flow")
	flag("--docs")
	list("--type")
	list("--tag")
	list("--keyword")
	list("--path")
	list("--text")
	none("--since")
	none("--until")
	none("--sort")
	flag("-r", "--reverse")
	integer("--limit")
	none("--format")
	flag("--no-refresh")
	flag("--learnings")
	flag("--templates")
	none("--show", "NAME")
	str("--layer")
	none("--check-file", "PATH")
	none("--draft", "PARENT")
	str("--name")
	str("--to")
	flag("--handoff")
	flag("--mirror")
	p.Add(argparse.Arg{Flags: []string{"command"}, Nargs: argparse.ZeroOrMore})
	list("--cwd")
	integer("--phase")
	flag("--check")
	none("--abandon", "PARENT")
	str("--body-file")
	str("--reason")
	str("--title")
	str("--plan")
	none("--issue")
	integer("--mr")
	p.Add(argparse.Arg{Flags: []string{"--scope"}, Kind: argparse.Append, Default: []string{}})
	str("--template")
	flag("--with-feedback")
	flag("--feedback")
	str("--note")
	str("--target")
	str("--signal")
	integer("--reviewed")
	flag("--accept-unresolved")
	flag("--close-early")
	flag("-h", "--help")
	flag("--all")
	flag("--version")
	str("--record-writes")
	str("--record-tree")
	str("--choose-out")
	str("--accept-plan")
	str("--accept-done")
	str("--accept-check")
	flag("--no-issue")
	str("--actor")
	str("--via")
	return p
}

// opts は読めた引数。Python の argparse の Namespace と同じく、経路の途中で値を差し替える（sh が渡すパスを 1 本に
// まとめる、診断の外の差し替えを捨てる）。欄の名前は argparse の dest。
type opts struct{ argparse.Namespace }

func (a opts) command() []string { return a.Strings("command") }

// head は位置引数の頭の n 語が words と同じか（Python の list(args.command[:n]) == words）。
func (a opts) head(words ...string) bool {
	cmd := a.command()
	return len(cmd) >= len(words) && slices.Equal(cmd[:len(words)], words)
}

// unset は Python の user_only._unset（None・False・空文字）。0 は渡したと数える。
func unset(v any) bool {
	switch x := v.(type) {
	case nil:
		return true
	case bool:
		return !x
	case string:
		return x == ""
	}
	return false
}

// destOf は `--foo-bar` の dest（foo_bar）。
func destOf(flag string) string { return strings.ReplaceAll(strings.TrimLeft(flag, "-"), "-", "_") }

// jsonOutOfTest は `--test` が取る 2 語に混ざった `--json` を外し、末尾へ回す。`--json` はツールの名前にも
// 調べるコマンドにもならない語なので、出力の形の指定として読む。
func jsonOutOfTest(argv []string) []string {
	at := slices.Index(argv, "--test")
	if at < 0 {
		return argv
	}
	taken := argv[at+1 : min(at+3, len(argv))]
	if !slices.Contains(taken, "--json") {
		return argv
	}
	var kept []string
	for _, w := range taken {
		if w != "--json" {
			kept = append(kept, w)
		}
	}
	return slices.Concat(argv[:at+1], kept, argv[min(at+3, len(argv)):], []string{"--json"})
}

// beforeDashes は `--` より前の語。
func beforeDashes(argv []string) []string {
	if end := slices.Index(argv, "--"); end >= 0 {
		return argv[:end]
	}
	return argv
}

// takeTestWords は `--test` と続く 2 語を argparse に渡す前に抜き出す。続く 2 語をそのまま取れば、2 語の中の
// オプションの名前（`--test Bash --permission-mode auto ls`）も語として取り、testUsage の案内まで届く。
// 2 語に満たないときと `--` の後ろにあるときは、そのまま argparse に任せる。
func takeTestWords(argv []string) ([]string, []string) {
	if !slices.Contains(beforeDashes(argv), "--test") {
		return argv, nil
	}
	at := slices.Index(argv, "--test")
	words := argv[at+1 : min(at+3, len(argv))]
	if len(words) < 2 {
		return argv, nil
	}
	return slices.Concat(argv[:at], argv[at+3:]), slices.Clone(words)
}

// helpOfTest は `--test ... --help` を `--help test`（test の節）の形にする。`--test --help` は語が足りず
// argparse の誤りで落ち、`--help` に届かないため。
func helpOfTest(argv []string) []string {
	head := beforeDashes(argv)
	if !slices.Contains(head, "--test") || !(slices.Contains(head, "-h") || slices.Contains(head, "--help")) {
		return argv
	}
	at := slices.Index(argv, "--test")
	n := 0
	for _, w := range argv[at+1 : min(at+3, len(argv))] {
		if strings.HasPrefix(w, "-") {
			break
		}
		n++
	}
	return slices.Concat(argv[:at], argv[at+1+n:], []string{"test"})
}
