package shellguard

import (
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/model/place"
)

// Core は自己防衛の対象をラッパーで消す・戻す形を止めた理由の種類。文面を分ける。
type Core string

const (
	// CoreHit は ccnavi 自身を守る場所（hook の登録・設定・保護済みのスクリプト・実行ファイル・記録）。
	CoreHit Core = "core"
	// CoreStore は承認済みチケットの置き場。マーカーを消すのはユーザがターミナルで打つ手順（sh は通す）。
	CoreStore Core = "store"
	// CoreReview はレビュー待ちの置き場（wip/proposals/review/）。承認済みと同じ扱い。
	CoreReview Core = "review"
	// CoreUnread は対象を読み切れない形。共通ルールの allow（`*ccnavi-git.sh*`）が生の文字列に当たるので、
	// ここで見ないと確認に落ちずに通る。
	CoreUnread Core = "unread"
	// CoreIndex は `checkout -- <パス>`（索引の中身に戻す）。sh はこの形を通さないので restore を案内する。
	CoreIndex Core = "index"
)

// coreSubs はラッパーのサブコマンドのうち、行き先のファイルを消す・別のコミットの中身に戻すもの。`restore` は
// `--source` を付けた形だけ、`checkout` は 2 つ目からの語がパス。`mv` はラッパーが通さないが、通すようになっても
// 同じ場所を守るよう数えておく。
var coreSubs = []string{"rm", "mv", "restore", "checkout"}

// 値を次の語で取るオプション（`=` でつないだ形は 1 語で済む）。値をパスと読み違えないため。
var (
	valueLongs = map[string][]string{
		"restore":  {"--source", "--conflict", "--pathspec-from-file"},
		"checkout": {"--orphan", "--conflict", "--pathspec-from-file"},
		"rm":       {"--pathspec-from-file"},
		"mv":       nil,
	}
	valueShorts = map[string]string{"restore": "s", "checkout": "bB", "rm": "", "mv": ""}
)

// markers は、ディレクトリがこの名前を直に持つなら守る場所を中に含む印（ツリーの頂上・ccnavi ディレクトリの親）。
var markers = []string{config.DefaultProjectHome, config.ClaudeDir, ".git"}

const (
	// expandChars はシェルが実行の時点で値を決める書き方（変数・置換）。
	expandChars = "$`"
	// globChars はどこに当たるかを読みで決められない pathspec。グロブは `/` もまたぐ（git の pathspec）。
	globChars = "*?["
	// unreadLine は読み切れないコマンド行で、当たった語の代わりに出す文。
	unreadLine = "読み切れないコマンド行"
	// mentionLimit は標準入力に流すラッパーの名前を含む語を、文に出す長さ（字）。
	mentionLimit = 80
)

// CheckCorePaths は、`ccnavi-git.sh` で自己防衛の対象を消す・別のコミットの中身に戻す形なら、サブコマンド・当たった語・
// 理由の種類を返す。当たらなければ sub が空。
//
// 当たるとみなすのは次のどれかの場合。シェルから書く形の組み込みと同じ場所の式に書いたとおりの語か、居場所から解いた絶対パスが
// 当たり、そのパスが守る場所を中に含む。承認済み・レビュー待ちの置き場を指す。当たる先を読みで決められない（`:` で始まる
// pathspec・固定の部分の無いグロブ・`--pathspec-from-file`）。対象を読み切れない（変数・置換・読めない `cd` の後の相対パス・
// 読み切れないコマンド行）。`checkout -- <パス>`。通すのは、索引の中身に戻す `restore` と、何も消さない `rm -n`・`--dry-run`。
func CheckCorePaths(conf *config.Settings, root, cwd, subject string, degraded bool) (sub, word string, kind Core) {
	if !mentionsGitScript(subject) {
		return "", "", ""
	}
	if degraded {
		for _, s := range degradedSubs(subject) {
			if s == "" {
				return unknownParent, unreadLine, CoreUnread
			}
			if slices.Contains(coreSubs, s) {
				return s, unreadLine, CoreUnread
			}
		}
		return "", "", ""
	}
	places := shell.PlacedCommands(subject, false)
	if word, ok := fedToStdin(places); ok {
		return unknownParent, word, CoreUnread
	}
	var compiled *pyre.Regexp
	for _, p := range places {
		call, ok := findScriptCall(p.Words)
		if !ok || !isGitScript(call.script) {
			continue
		}
		args := call.args
		if len(args) > 0 && strings.ContainsAny(args[0], openSub) {
			// サブコマンドが文字どおりの語でない。どのサブコマンドが走るかを読めない。
			return args[0], args[0], CoreUnread
		}
		if len(args) == 0 || !slices.Contains(coreSubs, args[0]) {
			continue
		}
		sub := args[0]
		paths, unknown, index := coreTargets(sub, args[1:])
		if unknown != "" {
			if strings.ContainsAny(unknown, expandChars) {
				return sub, unknown, CoreUnread
			}
			return sub, unknown, CoreHit
		}
		if len(paths) == 0 {
			continue
		}
		if compiled == nil {
			pattern := selfguard.ShellRegex(root, conf.Bin, conf.ProjectHome, selfguard.CommonLayerFiles(conf))
			var err error
			if compiled, err = pyre.Compile(pattern, pyre.IgnoreCase); err != nil {
				// 守る場所の式が組めない。どこが守る場所か決められないので止める側に倒す。
				return sub, paths[0], CoreHit
			}
		}
		base, known := "", false
		if p.Known && cwd != "" {
			base, known = p.Here, true
			if !ospath.IsAbs(base) {
				base = ospath.Join(cwd, base)
			}
		}
		for _, path := range paths {
			if hit := coreHit(conf, root, compiled, path, base, known, sub); hit != "" {
				if index && hit == CoreHit {
					hit = CoreIndex
				}
				return sub, path, hit
			}
		}
	}
	return "", "", ""
}

// fedToStdin は、標準入力から読むシェルに流し込まれた語のうち、呼び出しの語でなくラッパーの名前を含むものを返す
// （`echo '…ccnavi-git.sh rm …' | sh`）。流し込む文字列の中は、サブコマンドも対象も読めない。
func fedToStdin(places []shell.Placed) (string, bool) {
	mention := ""
	stdin := false
	for _, p := range places {
		script := -1
		if call, ok := findScriptCall(p.Words); ok {
			script = call.index
		}
		if mention == "" {
			for i, w := range p.Words {
				if i != script && mentionsGitScript(w) {
					mention = w
					break
				}
			}
		}
		head := stripHeads(p.Words)
		stdin = stdin || shell.ReadsStdin(head[shell.NameIndex(head):])
	}
	if mention != "" && stdin {
		return pyfmt.Head(mention, mentionLimit), true
	}
	return "", false
}

// longOption は `--名前`（`=値` を除く）が full か、git が受ける full の省略形（least 字以上の前方一致）か。
func longOption(arg, full string, least int) bool {
	return len(arg) >= least && strings.HasPrefix(full, arg)
}

// coreTargets は、サブコマンドの引数から、消す・戻す先のパスの語と、読めない指定（あればその語）と、索引の中身に戻す
// `checkout -- <パス>` かどうかを返す。
//
// `restore` は `--source` が無ければ何も返さない。`checkout` は `--` より前の最初の語（行き先か起点）を除く。`rm` は
// `-n`・`--dry-run` があれば（後ろに `--no-dry-run` が無ければ）何も返さない。長いオプションは git が受ける省略形
// （`--sou=`）も同じに読む。オプションを変数で書く形（`restore $O HEAD~1 <パス>`）は見ない。この検査の限界で、sh が止める。
func coreTargets(sub string, args []string) (paths []string, unknown string, index bool) {
	longs, shorts := valueLongs[sub], valueShorts[sub]
	var positional []string
	beforeDashes := 0
	source, dry, afterDashes := false, false, false
	fromFile := ""
	for i := 0; i < len(args); {
		arg := args[i]
		i++
		if afterDashes || arg == "-" || !strings.HasPrefix(arg, "-") {
			positional = append(positional, arg)
			continue
		}
		if arg == "--" {
			afterDashes = true
			beforeDashes = len(positional)
			continue
		}
		if strings.HasPrefix(arg, "--") {
			name, _, eq := strings.Cut(arg, "=")
			if longOption(name, "--pathspec-from-file", len("--pathspec-fr")) {
				fromFile = arg
				name = "--pathspec-from-file"
			}
			if longOption(name, "--source", len("--so")) {
				source = true
				name = "--source"
			}
			if longOption(name, "--dry-run", len("--d")) {
				dry = true
			}
			if longOption(name, "--no-dry-run", len("--no-d")) {
				// git は後に書いたほうを採る。`--dry-run --no-dry-run` は消す形。
				dry = false
			}
			if slices.Contains(longs, name) && !eq {
				i++
			}
			continue
		}
		// まとめた短いオプション。値を取る字が出たら、残りの字か次の語が値。
		rest := []rune(arg[1:])
		for len(rest) > 0 {
			ch := rest[0]
			rest = rest[1:]
			if sub == "restore" && ch == 's' {
				source = true
			}
			if sub == "rm" && ch == 'n' {
				dry = true
			}
			if strings.ContainsRune(shorts, ch) {
				if len(rest) == 0 {
					i++
				}
				break
			}
		}
	}
	if !afterDashes {
		beforeDashes = len(positional)
	}
	if sub == "restore" && !source || sub == "rm" && dry {
		return nil, "", false
	}
	if fromFile != "" {
		// パスをファイルから読む形。どこに当たるかを読みで決められない。
		return nil, fromFile, false
	}
	targets := positional
	if sub == "checkout" {
		// `checkout -- <パス>` は起点が無く、索引の中身に戻す。
		index = beforeDashes == 0 && afterDashes
		if !index {
			targets = positional[min(1, len(positional)):]
		}
	}
	for _, p := range targets {
		if strings.ContainsAny(p, expandChars) {
			return nil, p, false
		}
	}
	return targets, "", index
}

// coreHit はパスの語 1 つが自己防衛の対象に当たるなら、理由の種類。当たるかを決められない語も当たる側に倒す。
// base はそのコマンドが居る場所で、known が偽なら読めない。
func coreHit(conf *config.Settings, root string, compiled *pyre.Regexp, path, base string, known bool, sub string) Core {
	word := strings.ReplaceAll(path, `\`, "/")
	if strings.HasPrefix(word, ":") {
		return CoreHit
	}
	globbed := false
	if g := strings.IndexAny(word, globChars); g >= 0 {
		// グロブの前の固定の部分（最後の `/` まで）のディレクトリとして見る。
		globbed = true
		word = dirPart(word[:g])
		if strings.Trim(word, "/.") == "" {
			return CoreHit
		}
	}
	if !known && !ospath.IsAbs(word) {
		// 居場所が読めない（`cd "$D"`・`pushd`）。相対パスはどこを指すかを決められない。
		return CoreUnread
	}
	full := ""
	if known {
		full = ospath.Normpath(ospath.Join(base, word))
	}
	if inStore(conf, word, full) {
		return CoreStore
	}
	if inReview(conf, word, full, sub) {
		return CoreReview
	}
	if sub == "rm" && !globbed && selfguard.HandoffDeleteWord(root, base, word) {
		// 下書きの置き場の中の削除は守る場所の外。グロブは外さない（シェルの側と同じ。`.?` が `..` に当たりうる）。
		return ""
	}
	if compiled.Search("rm " + word) {
		return CoreHit
	}
	if !known {
		return ""
	}
	if compiled.Search("rm " + strings.ReplaceAll(full, `\`, "/")) {
		return CoreHit
	}
	if holdsCore(conf, root, full) {
		return CoreHit
	}
	return ""
}

// dirPart は最後の `/` までの部分（`/` が無ければ空）。
func dirPart(s string) string {
	if k := strings.LastIndex(s, "/"); k >= 0 {
		return s[:k+1]
	}
	return ""
}

// placeForms は置き場の判定で比べる 2 つの表記（書いた語と解いた先。解いた先が無ければ書いた語だけ）。
func placeForms(word, full string) []string {
	written := word
	if !ospath.IsAbs(word) {
		written = ospath.Normpath(word)
	}
	if full == "" {
		return []string{written}
	}
	return []string{written, full}
}

// slashedLower は `/…/` で囲んだ小文字の形（部分の一致を区切りで見る）。
func slashedLower(form string) string {
	return "/" + strings.Trim(pyfmt.Lower(strings.ReplaceAll(form, `\`, "/")), "/") + "/"
}

// inStore は承認済みチケットの置き場（とその下）を指すか。置き場の親は守る場所として扱う。
func inStore(conf *config.Settings, word, full string) bool {
	approved := conf.Approved
	if approved == "" {
		approved = config.DefaultApproved
	}
	store := pyfmt.Lower(strings.Trim(strings.ReplaceAll(approved, `\`, "/"), "/"))
	if store == "" {
		return false
	}
	for _, form := range placeForms(word, full) {
		text := slashedLower(form)
		if !strings.HasPrefix(text, "/../") && strings.Contains(text, "/"+store+"/") {
			return true
		}
	}
	return false
}

// inReview はレビュー待ちの置き場（とその下）か、置き場の親を指すか。
//
// 置き場はツリーごとにあり、どのツリーのものかは問わない。親（`wip/`・`wip/proposals/`）を丸ごと消すと置き場ごと消えるが、
// 空なら消えるものが無いので通す。親を閉じた後の `ccnavi-git.sh rm -r wip` の案内がこの形で、そのときレビュー待ちの子は残っていない。
// 空の親を通すのは `rm` だけ。`restore --source`・`checkout <ref> --` で親を戻すと、いまは空でも過去のコミットにあった
// レビュー待ちの提案が戻りうるので、中身に依らず当てる。
func inReview(conf *config.Settings, word, full, sub string) bool {
	tickets := conf.Tickets
	if tickets == "" {
		tickets = config.DefaultTickets
	}
	review := pyfmt.Lower(strings.Trim(strings.ReplaceAll(tickets, `\`, "/"), "/") + "/" + string(place.Review))
	parts := strings.Split(review, "/")
	where, known := full, full != ""
	if !known && ospath.IsAbs(word) {
		where, known = word, true
	}
	for _, form := range placeForms(word, full) {
		text := slashedLower(form)
		if strings.HasPrefix(text, "/../") {
			continue
		}
		if strings.Contains(text, "/"+review+"/") {
			return true
		}
		for n := 1; n < len(parts); n++ {
			parent := strings.Join(parts[:n], "/")
			if !strings.HasSuffix(text, "/"+parent+"/") {
				continue
			}
			if !known || sub != "rm" {
				return true
			}
			return hasEntries(ospath.Join(where, strings.Split(review[len(parent)+1:], "/")...))
		}
	}
	return false
}

// hasEntries はディレクトリに何かがあるか。無いディレクトリは空と見る。読めなければ当たる側に倒す。
func hasEntries(path string) bool {
	if !ospath.DiskLexists(path) {
		return false
	}
	if !ospath.IsDir(path) {
		return true
	}
	names, err := ospath.DiskListdir(path)
	return err != nil || len(names) > 0
}

// holdsCore は full が守る場所を中に含むディレクトリか。
func holdsCore(conf *config.Settings, root, full string) bool {
	anchors := append([]string{root, conf.Bin, conf.Projects, conf.Log, conf.State}, selfguard.CommonLayerFiles(conf)...)
	forms := []string{pathcase.Fold(ospath.Abspath(full)), pathcase.Fold(ospath.Realpath(full))}
	for _, anchor := range anchors {
		if anchor == "" {
			continue
		}
		for _, a := range []string{ospath.Abspath(anchor), ospath.Realpath(anchor)} {
			a = pathcase.Fold(a)
			for _, f := range forms {
				// 区切りだけのパス（`/`）は全部を含む。
				f = strings.TrimRight(f, ospath.Sep)
				if a == f || a == ospath.Sep && f == "" || strings.HasPrefix(a, f+ospath.Sep) {
					return true
				}
			}
		}
	}
	if !ospath.IsDir(full) {
		return false
	}
	names, err := ospath.DiskListdir(full)
	if err != nil {
		return true
	}
	children := []string{full}
	for _, n := range names {
		children = append(children, ospath.Join(full, n))
	}
	for _, child := range children {
		if !ospath.IsDir(child) {
			continue
		}
		for _, m := range markers {
			if ospath.DiskLexists(ospath.Join(child, m)) {
				return true
			}
		}
	}
	return false
}

// CorePathMessage は自己防衛の対象をラッパーで消す・戻す形を止めた文。
func CorePathMessage(root, sub, word string, kind Core) string {
	values := texts.Values{"sub": sub, "word": word}
	switch kind {
	case CoreStore:
		return texts.Say(root, "DENY_WRAPPER_CORE_PATH__STORE", values, nil)
	case CoreReview:
		return texts.Say(root, "DENY_WRAPPER_CORE_PATH__REVIEW", values, nil)
	case CoreIndex:
		return texts.Say(root, "DENY_WRAPPER_CORE_PATH__INDEX", values, nil)
	case CoreUnread:
		call := config.GitWrapper + " " + sub
		if sub == unknownParent {
			call = config.GitWrapper + " の呼び出し"
		}
		return texts.Say(root, "DENY_WRAPPER_CORE_PATH__UNREAD", texts.Values{"call": call, "word": word}, nil)
	}
	return texts.Say(root, "DENY_WRAPPER_CORE_PATH__HIT", values, nil)
}
