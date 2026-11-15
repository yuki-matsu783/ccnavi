// Package stage は ccnavi-stage.sh の本体。書き込みを止められている場所のファイルを、下書きの置き場（wip/handoff/）で
// 直すための支度をする（元を下書きへコピーする・編集が済んだ下書きの取り込みの手順を出す）。
//
// 下書きの置き場の根は、cwd が .claude/worktrees/<名前> の中ならそのワークツリー、それ以外はワークスペースルート。
// 下書きは <根>/wip/handoff/<根からの相対パス> に置く（元と同じ階層を保つので、違う元のファイルが同じ下書きに落ちない）。
// 相対パスの中の .claude の階層は handoff-.claude に読み替える（handoffnames。取り込みの ccnavi-user-handoff.sh が戻す）。
// 取り込める種類の表は handoff の kinds。ネットワークには出ず、git も使わない。
package stage

import (
	"bytes"
	"fmt"
	"io"
	"os"
	"strings"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/model/handoffnames"
	"ccnavi/internal/tickets/sync/handoff"
)

// 終了コード（ccnavi-stage.sh の契約）。
const (
	exitOK    = 0
	exitUnmet = 1 // 編集済みの下書きが 1 つも無い、など
	exitUsage = 2 // 引数か環境の誤り
)

const usage = `sh .ccnavi/scripts/ccnavi-stage.sh <パス>...
sh .ccnavi/scripts/ccnavi-stage.sh --commands <パス>...

  <パス>      直したい元のファイル（置き場の根の下。根は cwd がワークツリーならそのワークツリー、それ以外はワークスペースルート）。cwd からの相対でも絶対でもよい
  --commands  編集が済んだあとに実行します。元と違う下書きだけを、種類ごとに分けて出します
              （取り込みは ccnavi-user-handoff.sh の手順。下見のあと --yes --expect で取り込みます）

下書きは <置き場の根>/wip/handoff/<置き場の根からの相対パス> に cp する
（置き場の根は、cwd がワークツリーならそのワークツリー、それ以外はワークスペースルート）
（既にあれば上書きしない）。.claude の階層は handoff-.claude に読み替えて置く
（.claude/settings.json → wip/handoff/handoff-.claude/settings.json）。取り込みで元に戻す。
複数ファイルを直すときは、すべての下書きを作ってから --commands を実行し、ターンを終える。
`

// run は 1 回の起動。
type run struct {
	root, base, handoff string
	names               *handoffnames.Table
	stderr              io.Writer
}

// say は標準エラーに `ccnavi-stage: ` を付けて出す。
func (r *run) say(text string) { fmt.Fprintln(r.stderr, "ccnavi-stage: "+text) }

// Run は ccnavi-stage.sh の引数 args で、下書きを作るか（既定）、取り込みの手順を出す（--commands）。cwd は打った場所。
func Run(stdout, stderr io.Writer, root, cwd string, args []string) int {
	commands := false
	if len(args) > 0 {
		switch a := args[0]; {
		case a == "-h" || a == "--help" || a == "help":
			io.WriteString(stdout, usage)
			return exitOK
		case a == "--commands":
			commands, args = true, args[1:]
		case strings.HasPrefix(a, "-"):
			fmt.Fprintln(stderr, "ccnavi-stage: "+a+" は指定できません。指定できるのは --commands だけです。")
			return exitUsage
		}
	}
	if len(args) == 0 {
		io.WriteString(stderr, usage)
		return exitUsage
	}
	names, err := handoffnames.Default()
	if err != nil {
		fmt.Fprintln(stderr, "ccnavi-stage: "+err.Error())
		return exitUsage
	}
	r := &run{root: root, base: baseOf(root, cwd), names: names, stderr: stderr}
	r.handoff = r.base + "/wip/handoff"
	// 置き場（か wip/）がリンクなら、コピーと mkdir がリンクの先へ書いてしまう。ワークツリーの置き場はブランチの内容で
	// 決まるので、リンクを含むブランチでは書き先が外へ出る。書かずに断る。
	if !commands && (ospath.IsLink(r.handoff) || ospath.IsLink(ospath.Dirname(r.handoff))) {
		r.say("下書きの置き場（" + r.handoff + "）か wip/ がシンボリックリンクです。リンクの先へは書きません。")
		return exitUsage
	}
	// 下書きを書く前に、全引数が使えるかを確かめる。1 つでも不正なら、何も書かずに止める（先の引数の下書きを残さない）。
	rels := make([]string, len(args))
	bad := false
	for i, arg := range args {
		var usable bool
		if rels[i], usable = r.relative(arg); !usable {
			bad = true
		}
	}
	if bad {
		return exitUsage
	}
	if commands {
		return r.commands(stdout, rels)
	}
	return r.drafts(stdout, rels)
}

// baseOf は置き場の根。cwd とルートは、リンクや表記の違い（macOS の /private、Windows の C:/ と /c/ など）を解いてから比べる。
func baseOf(root, cwd string) string {
	here, rootPhys := phys(cwd), phys(root)
	if rest, under := pathcase.Under(here, rootPhys+"/.claude/worktrees"); under && rest != "." {
		first, _, _ := strings.Cut(ospath.Slashed(rest), "/")
		return root + "/.claude/worktrees/" + first
	}
	return root
}

// phys はリンクを解いたパス（`/` 区切り）。
func phys(p string) string { return ospath.Slashed(ospath.Realpath(p)) }

// under は path が base の下（base そのものは含まない）なら、"/" 区切りの相対。
func under(path, base string) (string, bool) {
	rel, in := pathcase.Under(path, base)
	if !in || rel == "." {
		return "", false
	}
	return ospath.Slashed(rel), true
}

// relative は元のファイルの、置き場の根からの相対パス。使えないパスは理由を言って偽。根の下ならその相対パス。そうでなければ
// ワークスペースルートの下の相対パス（根がワークツリーなら、根の側に同じ相対パスのファイルがあるものとして扱う）。
func (r *run) relative(arg string) (string, bool) {
	abs := ospath.Slashed(ospath.Abspath(arg))
	rel, in := under(abs, r.base)
	if !in {
		if rel, in = under(abs, r.root); !in {
			r.say(arg + " はワークスペースルート（" + r.root + "）の下にありません。")
			return "", false
		}
	}
	folded := pathcase.Fold(rel)
	switch {
	case strings.HasPrefix(folded, "wip/handoff/"):
		r.say(arg + " は下書きの置き場の中です。直したい元のファイルを渡してください。")
		return "", false
	case strings.HasPrefix(folded, pathcase.Fold(".claude/worktrees/")):
		r.say(arg + " は別のワークツリーのファイルです。そのワークツリーの中で実行してください。")
		return "", false
	case strings.ContainsAny(rel, "'\n"):
		r.say(arg + " には引用符か改行が入っていて、取り込みコマンドの 1 行に安全に書けません。")
		return "", false
	}
	orig := r.base + "/" + rel
	if part := r.names.Reserved(rel); part != "" {
		r.say(orig + " の `" + part + "` は、下書きでの階層名として予約されています（表: " + r.root + "/.ccnavi/scripts/ccnavi-handoff-names.txt）。この名前の階層を含むファイルは下書きにできません。")
		return "", false
	}
	if !ospath.IsFile(orig) {
		r.say(orig + " は実在するファイルではありません。")
		return "", false
	}
	// リンクは解かれた先が置き場の根の外かもしれない。元がリンクなら、取り込みがリンクを通常のファイルに置き換えて
	// しまう。どちらも断る（リンクの先の実体を直接渡す）。
	if ospath.IsLink(orig) {
		r.say(orig + " はシンボリックリンクです。リンクの先の実体を渡してください。")
		return "", false
	}
	dir, top := phys(ospath.Dirname(orig)), phys(r.base)
	if _, in := pathcase.Under(dir, top); !in {
		r.say(orig + " は、途中のリンクを解くと置き場の根（" + top + "）の外（" + dir + "）に出ます。")
		return "", false
	}
	return rel, true
}

// draftOf は元の相対パスの下書き。
func (r *run) draftOf(rel string) string { return r.handoff + "/" + r.names.ToDraft(rel) }

// sameContent は 2 つのファイルの中身が同じか（読めなければ違う）。
func sameContent(a, b string) bool {
	x, errA := os.ReadFile(a)
	y, errB := os.ReadFile(b)
	return errA == nil && errB == nil && bytes.Equal(x, y)
}

// drafts は元を下書きへコピーする。下書きが既にあれば上書きしない（編集中のものを消さないため）。
func (r *run) drafts(stdout io.Writer, rels []string) int {
	var staged []string
	for _, rel := range rels {
		orig, draft := r.base+"/"+rel, r.draftOf(rel)
		staged = append(staged, orig)
		switch {
		case ospath.DiskLexists(draft) && sameContent(orig, draft):
			fmt.Fprintln(stdout, "下書きは既にある（元と同じ）: "+draft)
		case ospath.DiskLexists(draft):
			fmt.Fprintln(stdout, "下書きは既にある（編集済み。上書きしない）: "+draft)
		default:
			if err := copyFile(orig, draft); err != nil {
				r.say(draft + " を作れません（" + fsio.ErrorText(err) + "）。")
				return exitUnmet
			}
			fmt.Fprintln(stdout, "下書きを作った: "+draft)
		}
	}
	fmt.Fprintln(stdout, texts.Say(r.root, "NEXT_STAGE_EDIT", texts.Values{"staged_q": strings.Join(staged, " ")}, nil))
	return exitOK
}

// copyFile は元の中身と実行の権限を下書きへ写す（cp と同じ）。親のディレクトリは作る。
func copyFile(orig, draft string) error {
	data, err := os.ReadFile(orig)
	if err != nil {
		return err
	}
	info, err := os.Stat(orig)
	if err != nil {
		return err
	}
	if err := fsio.MakeDirs(ospath.Dirname(draft)); err != nil {
		return err
	}
	return os.WriteFile(draft, data, info.Mode().Perm())
}

// commands は、元と違う下書きに、取り込みの手順を出す（全種類が同じ手順）。ユーザが ccnavi-user-handoff.sh で下見（検査・
// 差分・ハッシュ）をしてから --yes --expect で取り込む。mv で差し替える案内は出さない（検査も差分も --expect も通らず、
// 下書きを作った後に直された元を黙って上書きするため）。
func (r *run) commands(stdout io.Writer, rels []string) int {
	var imps, missing, same, outside []string
	for _, rel := range rels {
		orig, draft := r.base+"/"+rel, r.draftOf(rel)
		switch {
		case !ospath.IsFile(draft):
			missing = append(missing, rel)
		case sameContent(orig, draft):
			same = append(same, rel)
		case handoff.KindOf(handoff.Key(rel)) != nil:
			imps = append(imps, "'"+rel+"'")
		default:
			outside = append(outside, rel)
		}
	}
	for _, c := range []struct {
		list []string
		say  string
	}{
		{missing, "下書きがまだ無い（先に <パス> だけで実行します）: "},
		{same, "下書きが元と同じ（まだ編集していない）: "},
		{outside, "取り込みの対象外のパスです（種類の表にありません。取り込めないので、保護対象でなければ直接直します）: "},
	} {
		if len(c.list) > 0 {
			r.say(c.say + strings.Join(c.list, " "))
		}
	}
	if len(imps) == 0 {
		r.say("取り込む編集済みの下書きがありません。")
		return exitUnmet
	}
	// 下書きのあるツリー（ワークツリーの中で作った下書きは、そのワークツリーが根）。
	hsh := "sh " + shell.SingleQuote(r.root+"/.ccnavi/scripts/ccnavi-user-handoff.sh")
	if r.base != r.root {
		hsh += " --root " + shell.SingleQuote(r.base)
	}
	listed := strings.Join(imps, " ")
	fmt.Fprintln(stdout, "取り込みは、ユーザが次の順で行います（検査・差分・ハッシュを見て、読んだうえで取り込む。差し替えはしない）:")
	fmt.Fprintln(stdout, hsh+" "+listed)
	fmt.Fprintln(stdout, hsh+" "+listed+" --yes --expect <上のハッシュ>")
	if len(missing)+len(same)+len(outside) > 0 {
		return exitUnmet
	}
	fmt.Fprintln(stdout, texts.Say(r.root, "NEXT_STAGE_GUIDE", texts.Values{"guide": "取り込みの手順"}, nil))
	fmt.Fprintln(stdout, texts.Say(r.root, "NOTE_STAGE_NONINTERACTIVE", nil, nil))
	return exitOK
}
