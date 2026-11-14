package gitwrap

import (
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/model/ids"
)

// coreDeny は、ccnavi 自身を守る場所を消す・別のコミットの中身に戻す形の拒否。
func (e *Env) coreDeny(sub, path string) *Deny {
	return &Deny{"core-path", sub + " で ccnavi 自身を守る場所 (" + path + ") を削除するか、別のコミットの中身に戻す形は使用できません。hook の登録・ルールとフェーズの設定・保護済みのスクリプト・実行ファイル・判定の記録があり、削除したり古い中身に戻したりすると ccnavi の止めが外れます。変更が必要なら、何をなぜ変えたいのかを伝えてユーザに依頼してください。衝突の解決の " + e.Self + " restore --ours / --theirs -- <パス> と、索引だけを戻す " + e.Self + " restore --staged <パス> は通ります（*?[ や : で始まる指定・--pathspec-from-file は、守る場所にヒットするかを確かめられないので使用できません。ファイルを 1 つずつ名指ししてください）。"}
}

// wholeTree は、ツリー全体を指す語か。
func wholeTree(word string) bool {
	switch word {
	case ".", ":/", "*", ":/*", "./":
		return true
	}
	return false
}

// rm は消す側だが、消えるのは git が中身を持っているファイルだけ（git rm は索引や HEAD と食い違うファイルを既定で拒む）。
// `rm -rf` の代わりとしてルールが名指しで勧める経路なので、勧めた先が通る形にする。通さないのは -f（コミットしていない
// 変更ごと消える）と、ツリー全体と、ccnavi 自身を守る場所を消す形。-n / --dry-run は何も消さないので通す（オプションは
// パスの後ろにも置けるので、全部を読んでから決める）。
func (e *Env) rm(args []string) *Deny {
	if len(args) == 0 {
		return &Deny{"rm-no-path", "rm は削除するファイルを名指ししてください (" + e.Self + " rm <パス>)。"}
	}
	dry, core := false, ""
	s := spec{flags: "cached quiet dry-run ignore-unmatch sparse pathspec-file-nul force", values: "pathspec-from-file", sflags: "rqnf"}
	d := Walk("rm", s, args, func(ev event) *Deny {
		switch {
		case ev.kind == "opt" && (ev.name == "--force" || ev.name == "-f"):
			return &Deny{"rm-force", "rm の " + ev.name + " (--force) はコミットしていない変更ごと消します。付けずに実行し、git が止めたなら、その中身を確かめてからユーザに伝えてください。"}
		case ev.kind == "opt" && (ev.name == "--dry-run" || ev.name == "-n"):
			dry = true
		case ev.kind == "opt" && ev.name == "--pathspec-from-file":
			if core == "" {
				core = ev.name
			}
		case ev.kind == "pos" && wholeTree(ev.value):
			return &Deny{"rm-whole-tree", "rm にツリー全体 (" + ev.value + ") を渡すと、追跡されているファイルがまとめて消えます。削除するものを 1 つずつ名指ししてください。"}
		case ev.kind == "pos" && core == "" && e.CoreHit(ev.value):
			core = ev.value
		}
		return nil
	})
	if d != nil {
		return d
	}
	if !dry && core != "" {
		return e.coreDeny("rm", core)
	}
	return nil
}

// restore は作業中の変更を破棄する側。reset --hard の代わりにルールが名指しで勧める経路でもあるので、対象を 1 つずつ
// 名指しさせる形だけ通す。--ours / --theirs は衝突したパスにしか使えず、ガード自身の設定が衝突したときに解く方法は
// ここしかない。
//
// 別のコミットの中身で戻す形（--source）と、衝突を片側の中身で解く形（--ours / --theirs）は、承認済みチケットの
// 置き場にヒットするパスには使わせない（置き場の衝突は、どちらの承認を採るかをユーザが決める）。--source は ccnavi
// 自身を守る場所にも使わせない。索引の中身に戻す形（--ours / --theirs による衝突の解決を含む）は通す。
func (e *Env) restore(args []string) *Deny {
	if len(args) == 0 {
		return &Deny{"restore-no-path", "restore は戻すファイルを名指ししてください (" + e.Self + " restore <パス>)。"}
	}
	source, side := false, false
	store, core := "", ""
	s := spec{
		flags:   "staged worktree ours theirs merge quiet progress no-progress overlay no-overlay ignore-unmerged ignore-skip-worktree-bits pathspec-file-nul recurse-submodules no-recurse-submodules",
		values:  "source conflict pathspec-from-file",
		sflags:  "SWqm",
		svalues: "s",
	}
	d := Walk("restore", s, args, func(ev event) *Deny {
		switch ev.kind {
		case "opt":
			switch ev.name {
			case "--source", "-s":
				source = true
			case "--ours", "--theirs":
				side = true
			case "--pathspec-from-file":
				store, core = ev.name, ev.name
			}
		case "pos":
			if wholeTree(ev.value) {
				return &Deny{"restore-whole-tree", "restore にツリー全体 (" + ev.value + ") を渡すと、作業中の変更が気づかないうちに消えます。戻したいファイルを 1 つずつ名指ししてください。"}
			}
			if e.StoreHit(ev.value) {
				store = ev.value
			}
			if core == "" && e.CoreHit(ev.value) {
				core = ev.value
			}
		}
		return nil
	})
	switch {
	case d != nil:
		return d
	case store != "" && side:
		return &Deny{"restore-store-side", "restore --ours / --theirs で承認済みチケットの置き場にヒットするパス (" + store + ") の衝突を片側の内容で解決する形は使用できません。置き場の衝突は、どちらの承認を採るかをユーザが決めます。" + e.Self + " merge --abort で取り込みをやめ、衝突したパスをユーザに伝えてください。"}
	case store != "" && source:
		return &Deny{"restore-store-source", "restore --source で承認済みチケットの置き場にヒットするパス (" + store + ") を別のコミットの中身に戻す形は使用できません。置き場を動かすのはユーザと ccnavi のスクリプトです。置き場の中身が食い違っているなら、ユーザに伝えてください（*?[ や: で始まる指定は、置き場にヒットするかを確かめられないので同じく使用できません。ファイルを1つずつ名指ししてください）。"}
	case source && core != "":
		return e.coreDeny("restore", core)
	}
	return nil
}

// onTarget は、統合先のユーザのチェックアウトで merge・pull を断る文。最初の文に続けて、次の一手と通る形の案内を付ける。
func (e *Env) onTarget(integ, first, allowed string) *Deny {
	return &Deny{"merge-on-target", first + "\n" + texts.Say(e.Root, "NEXT_GIT_MERGE_ON_TARGET", texts.Values{"integ": integ}, nil) +
		"\n" + texts.Say(e.Root, allowed, texts.Values{"integ": integ}, nil)}
}

// merge は早送り以外も通す（main が先に進んだ状態からブランチへ main を取り込む手順を必ず通るため）。止めるのは、衝突を
// ユーザが見ないまま片側を破棄する形（`-X ours`・`-s ours`）と、検査を飛ばす --no-verify。
//
// ユーザの手元で統合先をチェックアウトしている場合は、途中の merge を終わらせる --abort / --continue / --quit 以外を
// 止める（統合先への取り込みは integrate が行う決まり）。ワークツリーの中の merge は、形の指定が無ければ --no-ff を補う
// （自分のコミットがまだ無いと git は早送りで済ませ、範囲の検査が見分ける印（2 つの親を持つコミット）が残らない）。
func (e *Env) merge(args []string) (*Deny, []string) {
	finish, form := false, false
	s := spec{
		flags:   "ff no-ff ff-only edit no-edit commit no-commit stat no-stat no-log squash no-squash quiet verbose progress no-progress abort continue quit signoff no-signoff allow-unrelated-histories summary no-summary verify no-verify autostash no-autostash overwrite-ignore no-overwrite-ignore",
		values:  "message file strategy strategy-option",
		optvals: "log",
		sflags:  "qvne",
		svalues: "mFsX",
	}
	d := Walk("merge", s, args, func(ev event) *Deny {
		if ev.kind != "opt" {
			return nil
		}
		switch ev.name {
		case "--abort", "--continue", "--quit":
			finish = true
		case "--squash", "--ff", "--no-ff", "--ff-only":
			form = true
		case "--strategy", "-s", "--strategy-option", "-X":
			if ev.value == "ours" || ev.value == "theirs" {
				return &Deny{"merge-discard-side", ev.name + " " + ev.value + " を使うと、衝突の片側が気づかないうちに破棄されます。他セッションの書きかけが入っていても差分に残りません。衝突は 1 つずつ中身を見て解いてください。"}
			}
		case "--no-verify":
			return &Deny{"merge-no-verify", ev.name + " はマージ前の検査を飛ばします。検査が失敗するなら、その原因を直してください。"}
		}
		return nil
	})
	if d != nil || finish {
		return d, nil
	}
	if integ := e.targetHere(); integ != "" {
		return e.onTarget(integ, "統合先 "+integ+" のユーザのチェックアウトでは merge できません。統合先へは直接 merge しない決まりなので、取り込みは integrate が行います。", "NOTE_GIT_MERGE_ON_TARGET_ALLOWED"), nil
	}
	if top := e.Top(); !form && top != "" {
		if _, user := e.userCheckout(top); !user {
			return nil, []string{"--no-ff"}
		}
	}
	return nil, nil
}

// mergeFile は結果を標準出力に出す形（-p / --stdout）だけを通す。付けないと git は 1 つめのファイルを直に書き換える
// （ファイルを書く手段は Edit / Write にそろえ、hook が行き先を見られるようにする）。git は長いオプションの略も否定の形も
// 受けるので、知っている表記だけ通す。-L と --marker-size は値を次の語で取る（`-L -p` の -p はラベル）。`--` の後ろは
// ファイル名。まとめた短いオプションは分けて書かせる。
func (e *Env) mergeFile(args []string) *Deny {
	stdout, skip, end := false, false, false
	for _, arg := range args {
		if skip {
			skip = false // 直前のオプションの値
			continue
		}
		if end {
			continue
		}
		switch {
		case arg == "--":
			end = true
		case arg == "-p" || arg == "--stdout":
			stdout = true
		case arg == "-L" || arg == "--marker-size" || arg == "--diff-algorithm":
			skip = true
		case arg == "--union" || arg == "--ours" || arg == "--theirs" || arg == "--diff3" || arg == "--zdiff3" ||
			arg == "--object-id" || arg == "-q" || arg == "--quiet" ||
			hasAnyPrefix(arg, "--marker-size=", "--diff-algorithm=") || len(arg) > 2 && arg[:2] == "-L":
		case len(arg) > 0 && arg[0] == '-':
			return &Deny{"merge-file-option", "merge-file の " + arg + " は使用できません。使用できるのは -p (--stdout) と --union / --ours / --theirs / --diff3 / --zdiff3 / --object-id / -L <ラベル> / --marker-size=<数> / -q だけで、短いオプションは 1 つずつ分けて書きます。"}
		}
	}
	if !stdout {
		return &Deny{"merge-file-no-stdout", "merge-file は -p (--stdout) を付けた形のみ使用できます。付けないと1つめのファイルを直に書き換えます。衝突の最中なら " + e.Self + " merge-file -p --union --object-id :2:<パス> :1:<パス> :3:<パス> で出力し、その結果を Edit で書いてください。"}
	}
	return nil
}

// checkout は checkout と switch。ブランチを移る形は通し、ワークツリーの中身を破棄する形を止める（`checkout -- .` は
// 書きかけを何も言わずに消す）。既存のブランチを別のコミットへ移す形（checkout -B / switch -C）も通さない。
// `checkout <ref> <パス>` は `--` が無くてもパスを別のコミットの中身に戻すので、2 つ目からの語が承認済みチケットの置き場か
// ccnavi 自身を守る場所にヒットすれば止める。
//
// 親のワークツリー（`.claude/worktrees/<P>` で、親チケットか提案があるもの）では、語が無い・自分のブランチ・HEAD・
// フォルダ名になるブランチのほかへは移らせない（別のブランチに移ると、リモートでの承認を取り込む処理がこのツリーを飛ばす）。
func (e *Env) checkout(sub string, args []string) *Deny {
	s := spec{
		flags:   "quiet progress no-progress detach track no-track guess no-guess merge overlay no-overlay recurse-submodules no-recurse-submodules ignore-skip-worktree-bits force ours theirs",
		values:  "orphan conflict pathspec-from-file",
		optvals: "track recurse-submodules",
		sflags:  "qmtlf",
		svalues: "bB",
	}
	if sub == "switch" {
		s = spec{
			flags:   "quiet progress no-progress detach track no-track guess no-guess merge recurse-submodules no-recurse-submodules force discard-changes",
			values:  "create orphan conflict force-create",
			optvals: "track recurse-submodules",
			sflags:  "qmtdf",
			svalues: "cC",
		}
	}
	newBranch, detach, words, first := "", false, 0, ""
	d := Walk(sub, s, args, func(ev event) *Deny {
		switch ev.kind {
		case "end":
			return &Deny{"checkout-path", sub + " にパスを渡す形は、そのファイルの書きかけを削除します。戻したいファイルがあるなら " + e.Self + " restore <パス> を名指しで使ってください。"}
		case "pos":
			if ev.value == "." || ev.value == ":/" {
				return &Deny{"checkout-whole-tree", sub + " にツリー全体 (" + ev.value + ") を渡すと、作業中の変更が気づかないうちに消えます。" + e.Self + " restore <パス> を名指しで使ってください。"}
			}
			words++
			if words == 1 {
				first = ev.value
				return nil
			}
			if e.StoreHit(ev.value) {
				return &Deny{"checkout-store", sub + " <ref> <パス> で承認済みチケットの置き場にヒットするパス (" + ev.value + ") を別のコミットの中身に戻す形は使用できません。置き場を動かすのはユーザと ccnavi のスクリプトです。置き場の中身が食い違っているなら、ユーザに伝えてください（*?[ や: で始まる指定は、置き場にヒットするかを確かめられないので同じく使用できません）。"}
			}
			if e.CoreHit(ev.value) {
				return e.coreDeny(sub, ev.value)
			}
		case "opt":
			switch sub + ":" + ev.name {
			case "checkout:--force", "checkout:-f", "switch:--force", "switch:-f":
				return &Deny{"checkout-force", sub + " の " + ev.name + " (--force) は作業中の変更を破棄するので使用できません。退避は " + e.Self + " stash push -u です。"}
			case "switch:--discard-changes", "checkout:--ours", "checkout:--theirs":
				return &Deny{"checkout-discard", ev.name + " は作業中の変更を破棄します。退避は " + e.Self + " stash push -u です。"}
			case "checkout:-B", "switch:-C", "switch:--force-create":
				return &Deny{"checkout-force-branch", sub + " " + ev.name + " は、同じ名前の既存のブランチを別のコミットへ移します。そのブランチにしか無いコミットが気づかないうちに外れ、親のブランチなら承認済みチケットの置き場ごと中身が変わります。リモートに合わせるなら、親のブランチは " + e.Sync + " <P>、ほかのブランチは " + e.Self + " merge <リモート>/<ブランチ> です。分かれていて進めないならユーザに伝えてください。新しいブランチは -b（switch は --create）で切ります。"}
			case "checkout:--pathspec-from-file":
				return &Deny{"checkout-store", sub + " の " + ev.name + " は、どのパスを戻すかをここで読めないので使用できません。戻したいファイルがあるなら " + e.Self + " restore <パス> を名指しで使ってください。"}
			case "checkout:-b", "checkout:--orphan", "switch:--create", "switch:-c", "switch:--orphan":
				newBranch = ev.value
			case "checkout:--detach", "switch:--detach", "switch:-d":
				detach = true
			}
		}
		return nil
	})
	if d != nil {
		return d
	}
	top := e.Top()
	name, ok := e.worktreeName(top)
	// 親チケットか提案があるかを決められない（2）ときも、親のワークツリーとして狭める。
	if !ok || e.ParentTree(phys(top), name) == 1 {
		return nil
	}
	to := ""
	switch {
	case newBranch != "":
		to = newBranch
	case detach:
		to = "（ブランチの外）"
	case words == 1 || words >= 2 && sub == "switch":
		to = first
	}
	// 移り先は、今のブランチか、ブランチ名の / を - に置き換えたものがフォルダ名（識別子）になるブランチだけ
	// （HEAD がずれているときの戻しも通す）。
	if to == "" || to == "HEAD" || ids.BranchToID(to) == name {
		return nil
	}
	if cur, _ := e.git("rev-parse", "--abbrev-ref", "HEAD"); to == cur {
		return nil
	}
	return &Deny{"parent-worktree-switch", "親のワークツリー（.claude/worktrees/" + name + "）では別のブランチ（" + to + "）へ移れません。親のワークツリーは、ブランチ名の / を - に置き換えたものがフォルダ名（" + name + "）になるブランチの上に置きます。ccnavi は親チケットのワークツリーを、名前が親チケットの識別子で、そのブランチをチェックアウトしているものとして探します。別のブランチに移ると、リモートでの承認を取り込む処理（ccnavi-sync.sh と、セッション開始時に ccnavi-fetch.sh が fast-forward で進める処理）がこのワークツリーを飛ばします。親チケットのブランチを一度でも push したか ccnavi-sync.sh で取り込んだことがあると、親と子のチケットの承認・状態の操作（start・finish など）・実行前の判定も止まります。別の作業は別のワークツリーを切ってください（" + e.Self + " worktree add .claude/worktrees/<名前> -b <名前> <起点>）。"}
}
