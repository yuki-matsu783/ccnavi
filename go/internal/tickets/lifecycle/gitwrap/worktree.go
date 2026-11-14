package gitwrap

import (
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/tickets/model/ids"
)

// worktree は `worktree list | add | prune | remove` の判定。add の起点が古いかもしれないときの警告も返す。
func (e *Env) worktree(args []string) (*Deny, string) {
	if len(args) == 0 {
		args = []string{"list"}
	}
	action := "list"
	if args[0] != "" {
		action = args[0]
	}
	switch action {
	case "add":
		return e.worktreeAdd(args[1:])
	case "list", "prune", "remove":
		s := map[string]spec{
			"list":   {flags: "porcelain verbose", values: "expire", sflags: "vz"},
			"prune":  {flags: "dry-run verbose", values: "expire", sflags: "nv"},
			"remove": {flags: "force", sflags: "f"},
		}[action]
		return Walk("worktree", s, args[1:], func(ev event) *Deny {
			if ev.kind == "opt" && (ev.name == "--force" || ev.name == "-f") {
				return &Deny{"worktree-remove-force", "worktree remove --force は、未コミットの変更ごとツリーを削除します。中の変更を確かめ、必要なものを退避してからオプション無しの " + e.Self + " worktree remove を使ってください。"}
			}
			return nil
		}), ""
	}
	return &Deny{"worktree-action", "worktree " + action + " は指定できません。指定できるのは list / add / prune / remove です。"}, ""
}

// addForm は worktree add の引数の読み。
type addForm struct {
	dest, base, newBranch, leaf string
}

// worktreeAdd は worktree add の行き先・名前・起点を確かめる。
//
// 行き先はワークスペースの `.claude/worktrees/` の下に 1 階層で置き、名前をブランチ名（の / を - に置き換えたもの）に
// 揃える。ccnavi は、名前が識別子で、そのブランチをチェックアウトしているものを親チケットのワークツリーとして探す。
// 書き換えずに止める（打ったパスと起きたことが食い違うと、記録を読んだユーザが追えなくなる）。
// 行き先は「オプションでない最初の語」で、知らないオプションは通さない（通すと行き先を取り違える）。
func (e *Env) worktreeAdd(args []string) (*Deny, string) {
	var f addForm
	skip := ""
	for _, word := range args {
		if skip != "" {
			// 直前のオプションの値。-b なら新しいブランチ名。
			if skip == "b" {
				f.newBranch = word
			}
			skip = ""
			continue
		}
		switch word {
		case "-b":
			skip = "b"
		case "--reason":
			skip = "r"
		case "-B":
			return &Deny{"worktree-force-branch", "worktree add -B は、同じ名前の既存のブランチを別のコミットへ移します。そのブランチにしか無いコミットが外れ、親のブランチなら承認済みチケットの置き場ごと中身が変わります。新しく切るなら " + e.Self + " worktree add .claude/worktrees/<名前> -b <名前> <起点>、既にあるブランチを出すなら " + e.Self + " worktree add .claude/worktrees/<ブランチ> <ブランチ> です。直接作業のブランチなら、続けて " + e.WorktreeQ + " <名前> --adopt --base <統合先> で引き取ります（ユーザの確認が要ります。引き取らないと integrate できません）。リモートに合わせるのは " + e.Sync + " <P> です。"}, ""
		case "--detach", "-d":
			return &Deny{"worktree-detach", "worktree add " + word + " はブランチの外（detached HEAD）にワークツリーを作ります。ワークツリーはブランチの上に作り、名前をブランチ名に揃えます（" + e.Self + " worktree add .claude/worktrees/<名前> -b <名前> <起点>）。"}, ""
		case "--force", "-f":
			return &Deny{"worktree-force", "worktree add " + word + " は、他のワークツリーが出しているブランチや、登録の残った作成先を上書きします。" + e.Self + " worktree list で確かめ、不要な登録は " + e.Self + " worktree prune で外してから、オプション無しで実行してください。"}, ""
		case "--checkout", "--no-checkout", "--lock", "--guess-remote", "--no-guess-remote", "--track", "--no-track", "--quiet", "-q":
		default:
			switch {
			case strings.HasPrefix(word, "-"):
				return &Deny{"worktree-option", "worktree add の " + word + " は使用できません。行き先を取り違えると、プロジェクトの中にワークツリーを作ってしまいます。使いたい形があれば、ユーザに伝えて一覧に追加してもらってください。"}, ""
			case f.dest == "":
				f.dest = word
			case f.base == "":
				f.base = word
			}
		}
	}
	if f.dest == "" {
		return &Deny{"worktree-no-dest", "worktree add に作成先がありません。" + e.Self + " worktree add .claude/worktrees/<名前> -b <名前> <起点> の形で実行してください。"}, ""
	}
	abs := e.abs(f.dest)
	if !e.inWorktrees(abs) {
		return &Deny{"worktree-outside", "ワークツリーはワークスペースの .claude/worktrees/ の下に 1 階層で置きます（ワークツリーは git 管理外の固定の置き場）。" + f.dest + " は cwd から解くと " + abs + " になり、ワークスペースの外に出ます。" + e.spell(ospath.Basename(f.dest)) + " と書いてください。"}, ""
	}
	f.leaf = abs[strings.LastIndex(abs, "/")+1:]
	if d := e.addName(f); d != nil {
		return d, ""
	}
	// 統合先から切るなら、起点が古くないか確かめる（名前の検査を通ったあと）。
	return e.fresh(f)
}

// abs は cwd から解いた絶対パス（`/` 区切り、`.` と `..` を取り除く）。`\` を区切りとして読むのは Windows だけ
// （ほかの機械では名前の字で、`.claude\worktrees\x` は cwd の直下の 1 つの名前になる）。
func (e *Env) abs(p string) string {
	if ospath.Windows {
		p = ospath.Slashed(p)
	}
	if !isAbsSlashed(p) {
		p = slashedOnWindows(e.Cwd) + "/" + p
	}
	parts, _ := clean(p)
	out := strings.Join(parts, "/")
	if ospath.HasDrive(out, true) {
		return out
	}
	return "/" + out
}

// slashedOnWindows は、Windows でだけ区切りを `/` に揃える。
func slashedOnWindows(p string) string {
	if ospath.Windows {
		return ospath.Slashed(p)
	}
	return p
}

// root はワークスペースルート（`/` 区切り）。
func (e *Env) root() string {
	return e.abs(e.Root)
}

// inWorktrees は、abs がワークスペースの `.claude/worktrees/` の下 1 階層か。
func (e *Env) inWorktrees(abs string) bool {
	rest, ok := strings.CutPrefix(fold(abs), fold(e.root()+"/.claude/worktrees/"))
	return ok && rest != "" && !strings.Contains(rest, "/")
}

// spell は、cwd からワークスペースの `.claude/worktrees/<名前>` を指す書き方。絶対パスだけを出すと、次に別の場所から
// 打つときに使い回せないので、ワークスペースの中なら `../` を並べる。
func (e *Env) spell(name string) string {
	here, ws := e.abs("."), e.root()
	if fold(here) == fold(ws) {
		return ".claude/worktrees/" + name
	}
	if rel, ok := strings.CutPrefix(here, ws+"/"); ok {
		return strings.Repeat("../", strings.Count(rel, "/")+1) + ".claude/worktrees/" + name
	}
	return ws + "/.claude/worktrees/" + name
}

// addName は、行き先の名前とブランチ名が揃うか、既にあるブランチを出す形なら識別子が親に使えるかを確かめる。
// -b <ブランチ> は新しく切り、-b が無く起点だけなら既にあるブランチ（手元か origin/）を出す。
func (e *Env) addName(f addForm) *Deny {
	if f.newBranch != "" {
		if id := ids.BranchToID(f.newBranch); id != f.leaf {
			return &Deny{"worktree-name", "worktree add の新しいブランチ名（-b " + f.newBranch + "）から作る識別子（" + f.newBranch + " の / を - に置き換えたもの）が作成先の名前（" + f.leaf + "）と違います。ワークツリーの名前は、ブランチ名の / を - に置き換えたものにします。ccnavi は親チケットのワークツリーを、名前が親チケットの識別子で、そのブランチをチェックアウトしているものとして探します。名前が違うと、リモートでの承認を取り込む処理（ccnavi-sync.sh と、セッション開始時に ccnavi-fetch.sh が fast-forward で進める処理）がそのワークツリーを見つけられません。" + e.Self + " worktree add .claude/worktrees/" + id + " -b " + f.newBranch + " <起点> の形にしてください。"}
		}
		return nil
	}
	if f.base == "" {
		return nil
	}
	if id := ids.BranchToID(f.base); id != f.leaf {
		return &Deny{"worktree-name", "worktree add " + f.dest + " " + f.base + " は、" + f.base + " を名前の違う作成先（" + f.leaf + "）に出すか、ブランチの外（detached HEAD）に作ります。ワークツリーの名前は、ブランチ名の / を - に置き換えたものにします。新しく切るなら " + e.Self + " worktree add " + f.dest + " -b " + f.leaf + " " + f.base + "、既にあるブランチ " + f.base + " を出すなら行き先を .claude/worktrees/" + id + " にしてください。直接作業のブランチなら、続けて " + e.WorktreeQ + " <名前> --adopt --base <統合先> で引き取ります（ユーザの確認が要ります。引き取らないと integrate できません）。"}
	}
	if !e.hasRef("refs/heads/"+f.base) && !e.hasRef("refs/remotes/origin/"+f.base) {
		// 名前が揃っていても、ブランチでない（タグ・sha）ならブランチの外に作る。
		return &Deny{"worktree-detach", "worktree add " + f.dest + " " + f.base + " の " + f.base + " は手元のブランチでも origin/" + f.base + " でもないので、ブランチの外（detached HEAD）にワークツリーを作ります。新しく切るなら " + e.Self + " worktree add " + f.dest + " -b " + f.base + " <起点> にしてください。"}
	}
	// 既にあるブランチをそのまま出す形は、親のワークツリーを作る形でもある。識別子が親に使えるか（58 文字以内・末尾が
	// -<2 桁> でない・字の種類・NFC）を見る。子の識別子の形（<親>-<2 桁>-<2 桁>）は子のワークツリーを出し直す形なので、
	// 64 文字と字だけを見る。
	answer := map[string]string{}
	for _, line := range ids.IDAnswer(f.base) {
		key, value, _ := strings.Cut(line, " ")
		answer[key] = value
	}
	problem := answer["problem"]
	if answer["child"] == "yes" {
		problem = answer["direct"]
	}
	if problem != "" {
		return &Deny{"worktree-id", "worktree add " + f.dest + " " + f.base + " は指定できません。" + f.base + " から作る識別子 " + answer["id"] + " は親のワークツリーに指定できません（" + problem + "）。既にあるブランチを親にするときは " + e.Start + " --mr <MR の番号> で始めてください（採用してよいかを確かめてから出します）。"}
	}
	return nil
}

// freshWindow は、最後の fetch がこの時間より前なら、起点が古いかもしれないとみなす時間。
const freshWindow = 10 * time.Minute

// fresh は、統合先から切る worktree add の起点が古くないかを確かめる。hook は git を実行せず、ネットワークにも出ない決まりなので、
// 判定は `ccnavi git check` が行い、fetch が失敗した印はラッパーが書く。ここで fetch は打たない（副作用が増えるため）。
//
//   - 最後の fetch が 10 分より前、または FETCH_HEAD が無い（共通の git ディレクトリの FETCH_HEAD の更新時刻）
//   - 起点が手元の統合先で、origin/<統合先> より遅れている（origin/<統合先> が起点なら対象外）
//
// fetch を試して失敗した印（ラッパーの fetch が失敗したときに共通の git ディレクトリへ置く）が 10 分以内にあるときは、
// 1 つ目の条件では止めず、警告だけにする（オフラインで詰まないため）。起点を省いたときは今のブランチが起点になる。
// 既にあるブランチを出す形は新しく切らないので対象外。
func (e *Env) fresh(f addForm) (*Deny, string) {
	if f.newBranch == "" && (f.base != "" || e.hasRef("refs/heads/"+f.leaf)) {
		return nil, ""
	}
	top := e.Top()
	if top == "" {
		return nil, ""
	}
	integ := integration(top)
	if integ == "" {
		return nil, ""
	}
	from := f.base
	if from == "" || from == "HEAD" {
		from = e.branchNow()
	}
	fromOrigin := false
	switch from {
	case integ:
	case "origin/" + integ:
		fromOrigin = true
	default:
		return nil, ""
	}
	if !e.hasRef("refs/remotes/origin/" + integ) {
		return nil, ""
	}
	dir, ok := e.git("rev-parse", "--path-format=absolute", "--git-common-dir")
	if !ok || dir == "" {
		return nil, ""
	}
	behind, ahead := 0, 0
	if !fromOrigin {
		if !e.hasRef("refs/heads/" + integ) {
			return nil, ""
		}
		behind = e.count(integ + "..origin/" + integ)
		ahead = e.count("origin/" + integ + ".." + integ)
	}
	// 失敗した fetch も FETCH_HEAD を触る（開いて空にする）ので、失敗の印を先に見る。
	stale, offline := false, false
	switch {
	case recent(ospath.Join(dir, FetchFailedMark)):
		stale, offline = true, true
	case !ospath.IsFile(ospath.Join(dir, "FETCH_HEAD")):
		stale = true
	case !recent(ospath.Join(dir, "FETCH_HEAD")):
		stale = true
	}
	n, a := strconv.Itoa(behind), strconv.Itoa(ahead)
	if behind != 0 && ahead != 0 {
		return &Deny{"worktree-base-diverged", "ワークツリーの基点 " + integ + " が origin/" + integ + " と分岐しています（先行 " + a + " 件・遅れ " + n + " 件）。ワークツリーの基点は merge では進められません。基点を origin/" + integ + " にして切ると、手元にだけある先行 " + a + " 件を知らないブランチになり、統合で詰まります。ユーザに頼んで、先行分を push するか別のブランチに退避してから、手元の " + integ + " を origin/" + integ + " に揃えてください。揃えたら再実行します。"}, ""
	}
	cut := "`" + e.SelfQ + " worktree add " + shQuote(f.dest) + " -b " + shQuote(f.leaf) + " --no-track " + shQuote("origin/"+integ) + "`"
	if behind != 0 {
		return &Deny{"worktree-base-behind", "ワークツリーの基点 " + integ + " が origin/" + integ + " より " + n + " 件遅れています。古い統合先から切ると、取り込み済みの変更を知らないブランチになります。直接作業なら `" + e.WorktreeQ + " " + shQuote(f.leaf) + "` で切り直すと、起点を自動で選びます。手で切るなら `" + e.SelfQ + " fetch origin " + shQuote(integ) + "` のあと、" + cut + " を実行してください（手元の " + integ + " は進めない）。"}, ""
	}
	if stale && !offline {
		return &Deny{"worktree-fetch-stale", "ワークツリーの基点 " + from + " の元になる origin/" + integ + " が古いかもしれません（最後の fetch が 10 分より前、または fetch の記録がありません）。まず `" + e.SelfQ + " fetch origin " + shQuote(integ) + "` を実行し、`" + e.SelfQ + " log --oneline " + shQuote(integ+"..origin/"+integ) + "` で遅れを確かめてから、再実行してください。遅れていれば、直接作業なら `" + e.WorktreeQ + " " + shQuote(f.leaf) + "` で切り直します。手で切るなら " + cut + " を実行してください（手元の " + integ + " は進めない）。"}, ""
	}
	if stale {
		return nil, "fetch を試して失敗した記録があるため、origin/" + integ + " が古いかもしれないまま " + from + " から切りました（オフラインの可能性）。つながったら " + e.Self + " fetch origin " + integ + " で遅れを確かめてください。"
	}
	return nil, ""
}

// FetchFailedMark は、ラッパーの fetch が失敗したときに共通の git ディレクトリへ置く印の名前（成功すれば消す）。
const FetchFailedMark = "ccnavi-fetch-failed"

// recent は、ファイルが在り、更新が freshWindow より新しいか。
func recent(path string) bool {
	mtime, _, ok := ospath.Stat(path)
	return ok && time.Since(mtime) <= freshWindow
}

// count は `rev-list --count <範囲>`。数えられなければ 0。
func (e *Env) count(rng string) int {
	out, _ := e.git("rev-list", "--count", rng)
	n, _ := strconv.Atoi(strings.TrimSpace(out))
	return n
}
