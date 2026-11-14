package gitwrap

import "strings"

// 答え。Check の Pass は、引数だけの判定を通り、続きを状態から決めるサブコマンド。Judge の Pass は、続きの判定を
// ラッパー（sh）が持つサブコマンド（integrate・integrate-done）。
const (
	Allow = "allow"
	Pass  = "pass"
)

// Names はラッパーの文に入れる呼び方。Self は `sh <ccnavi-git.sh>`、Sync は `sh <ccnavi-sync.sh>`、Start は
// `sh <ccnavi-start.sh>`。SelfQ と WorktreeQ は、パスを単引用符で囲んだ `sh '<ccnavi-git.sh>'` と `sh '<ccnavi-worktree.sh>'`。
type Names struct {
	Self, Sync, Start, SelfQ, WorktreeQ string
}

// readOnly は読むだけのサブコマンド。全引数の判定で穴は塞いである。
var readOnly = map[string]bool{"status": true, "log": true, "show": true, "diff": true, "blame": true, "shortlog": true,
	"describe": true, "rev-parse": true, "rev-list": true, "ls-files": true, "ls-tree": true, "merge-base": true,
	"diff-tree": true, "whatchanged": true, "show-ref": true}

// byState は、続きの判定に git の状態やワークスペースを読むサブコマンドと、その判定（Judge が引く）。nil は、判定を
// ラッパー（sh）が持つサブコマンド。サブコマンドを足すときは、この表だけを直す。
var byState = map[string]func(e *Env, sub string, args []string, a *Answer){
	"worktree":   func(e *Env, _ string, args []string, a *Answer) { a.Deny, a.Warn = e.worktree(args) },
	"rm":         func(e *Env, _ string, args []string, a *Answer) { a.Deny = e.rm(args) },
	"restore":    func(e *Env, _ string, args []string, a *Answer) { a.Deny = e.restore(args) },
	"merge":      func(e *Env, _ string, args []string, a *Answer) { a.Deny, a.Prepend = e.merge(args) },
	"merge-file": func(e *Env, _ string, args []string, a *Answer) { a.Deny = e.mergeFile(args) },
	"checkout":   func(e *Env, sub string, args []string, a *Answer) { a.Deny = e.checkout(sub, args) },
	"switch":     func(e *Env, sub string, args []string, a *Answer) { a.Deny = e.checkout(sub, args) },
	"fetch":      func(e *Env, sub string, args []string, a *Answer) { a.Deny = e.fetch(sub, args) },
	"pull": func(e *Env, sub string, args []string, a *Answer) {
		if a.Deny = e.fetch(sub, args); a.Deny == nil {
			a.Target = e.targetHere()
		}
	},
	"push":           func(e *Env, _ string, args []string, a *Answer) { a.Deny = e.push(args, a) },
	"integrate":      nil,
	"integrate-done": nil,
}

// Check は、ラッパーが受けた引数 args（先頭がサブコマンド）を通すかを決める。答えは Allow・Pass、または拒否。
func Check(n Names, args []string) (string, *Deny) {
	if len(args) > 0 && strings.HasPrefix(args[0], "-") {
		return "", &Deny{"global-option", "サブコマンドより前のオプション (" + args[0] + ") は受け取りません。" + n.Self + " <サブコマンド> ... の形で書き直してください。設定の一時上書きが必要なら、その理由をユーザに伝えてください。"}
	}
	sub, rest := "", []string(nil)
	if len(args) > 0 {
		sub, rest = args[0], args[1:]
	}
	if d := everyArg(rest); d != nil {
		return "", d
	}
	switch {
	case readOnly[sub] || sub == "add":
		return Allow, nil
	case hasState(sub):
		return Pass, nil
	}
	if d := bySubcommand(n, sub, rest); d != nil {
		return "", d
	}
	return Allow, nil
}

// hasState は、sub が byState にあるか。
func hasState(sub string) bool {
	_, ok := byState[sub]
	return ok
}

// Judge は、ラッパーが受けた引数 args（先頭がサブコマンド）を、引数（Check）と git の状態から通すかを決める。
func Judge(e *Env, args []string) Answer {
	verdict, d := Check(e.Names, args)
	if d != nil || verdict != Pass {
		return Answer{Verdict: verdict, Deny: d}
	}
	judge := byState[args[0]]
	if judge == nil {
		return Answer{Verdict: Pass}
	}
	a := Answer{Verdict: Allow}
	judge(e, args[0], args[1:], &a)
	return a
}

// blocked は、どのサブコマンドでも止める長いオプションの名前。略した表記（`--outp=x`）も止める。
var blocked = []string{"output", "upload-pack", "receive-pack", "exec-path", "exec", "ext-diff", "textconv", "git-dir",
	"work-tree", "namespace"}

// everyArg は、全引数（値も `--` の後ろも）を見て、どのサブコマンドでも許さない形を拒否する。
func everyArg(args []string) *Deny {
	const outputOrExec = " は、読むだけのサブコマンドをファイル書き込みや外部コマンド実行に変えます。出力を保存したいなら、このラッパースクリプトが logs/ に全量を残すのでそちらを読んでください。"
	for _, arg := range args {
		switch {
		case arg == "-c" || arg == "--config-env" || strings.HasPrefix(arg, "--config-env="):
			return &Deny{"config-override", "設定の一時上書き (" + arg + ") は受け取りません。設定の上書きを付けない形で書き直してください。"}
		case arg == "--output" || strings.HasPrefix(arg, "--output=") || strings.HasPrefix(arg, "--upload-pack") ||
			strings.HasPrefix(arg, "--receive-pack") || strings.HasPrefix(arg, "--exec-path") ||
			strings.HasPrefix(arg, "--exec=") || arg == "--ext-diff" || arg == "--textconv":
			return &Deny{"output-or-exec", arg + outputOrExec}
		case arg == "--git-dir" || strings.HasPrefix(arg, "--git-dir=") || arg == "--work-tree" ||
			strings.HasPrefix(arg, "--work-tree=") || arg == "--namespace" || strings.HasPrefix(arg, "--namespace=") ||
			arg == "-C":
			return &Deny{"moved-root", arg + " は判定の起点を別のツリーへ動かします。対象のツリーの中で実行してください。"}
		case len(arg) > 2 && strings.HasPrefix(arg, "--"):
			// parse-options を使うサブコマンドは長いオプションの略を受けるので、止める名前の頭にヒットする表記も止める。
			// `--text`（diff の正式な名前）だけは textconv の頭でも通す。
			name, _, _ := strings.Cut(arg[2:], "=")
			for _, bad := range blocked {
				if !strings.HasPrefix(bad, name) {
					continue
				}
				if name == "text" {
					continue
				}
				return &Deny{"output-or-exec", arg + " は、止めているオプション（--" + bad + "）の略として読まれます。読むだけのサブコマンドをファイル書き込みや外部コマンド実行、判定の起点の移動に変えるので使用できません。"}
			}
		}
	}
	return nil
}

// bySubcommand は、引数だけで決まるサブコマンドごとの判定。
func bySubcommand(n Names, sub string, args []string) *Deny {
	switch sub {
	case "cat-file":
		// `--textconv`・`--filters` は設定の外部コマンドを走らせる。通すのは中身と型を読む形だけ。
		return Walk(sub, spec{flags: "batch batch-check batch-all-objects buffer unordered allow-unknown-type", sflags: "etsp"},
			args, func(event) *Deny { return nil })
	case "grep":
		return grep(n, args)
	case "branch":
		return branch(n, args)
	case "tag":
		return tag(n, args)
	case "remote":
		return remote(n, args)
	case "stash":
		return stash(n, args)
	case "commit":
		return commit(args)
	case "reset":
		return &Deny{"reset", "reset は作業中の変更やコミットを削除します。退避は " + n.Self + " stash push -u、戻すのは " + n.Self + " restore <パス> です。リモートに合わせたいなら、親のブランチは " + n.Sync + " <P> を実行してください（早送りか merge で取り込み、衝突したら取りやめます）。ほかのブランチは " + n.Self + " fetch <リモート> <ブランチ> のあと" + n.Self + " merge <リモート>/<ブランチ> です。分かれていて進めない（squash マージの後で fast-forward できない、など）なら、ブランチを移さずにユーザに伝えてください。"}
	case "clean":
		return &Deny{"clean", sub + " は作業中の変更を削除します。退避は " + n.Self + " stash push -u、戻すのは " + n.Self + " restore <パス> です。"}
	case "rebase", "cherry-pick", "revert", "am", "apply", "bisect", "filter-branch", "replace", "update-ref", "symbolic-ref",
		"reflog", "gc", "notes":
		return &Deny{"history-rewrite", sub + " は履歴か参照を書き換えます。使用できません。必要な理由をユーザに伝えてください。"}
	case "config":
		return &Deny{"config", "config は設定を読み書きします。値には資格情報が混ざるので使用できません。必要な値はユーザに尋ねてください。"}
	case "clone", "submodule", "lfs":
		return &Deny{"import", sub + " は外から中身を持ち込みます。使用できません。ユーザに依頼してください。"}
	}
	return &Deny{"not-allowed", sub + " は許可リストにありません。指定できる形は sh .ccnavi/scripts/ccnavi-git.sh --help で確認してください。"}
}

// grep は、一致したファイルを外部コマンドで開く `-O` / `--open-files-in-pager` を止める（まとめた短いオプションと略も見る）。
// 値を取る短いオプション（-e -f -A -B -C -m）より後ろの字は値なので数えない。
func grep(n Names, args []string) *Deny {
	const instead = "一致したファイルを見るなら、" + "%s" + " grep -l <型> で一覧を出し、Read で開いてください。"
	insteadOf := strings.Replace(instead, "%s", n.Self, 1)
	for _, arg := range args {
		switch {
		case arg == "--":
			return nil
		case len(arg) > 2 && strings.HasPrefix(arg, "--"):
			name, _, _ := strings.Cut(arg[2:], "=")
			if strings.HasPrefix("open-files-in-pager", name) && len(name) >= 2 {
				return &Deny{"grep-pager", arg + " は一致したファイルを外部コマンドで開きます。使用できません。" + insteadOf}
			}
		case len(arg) > 1 && strings.HasPrefix(arg, "-"):
			for _, ch := range arg[1:] {
				if ch == 'O' {
					return &Deny{"grep-pager", arg + " には -O（--open-files-in-pager）が含まれます。一致したファイルを外部コマンドで開くので使用できません。" + insteadOf}
				}
				if strings.ContainsRune("efABCm", ch) {
					break
				}
			}
		}
	}
	return nil
}

// branch は、消す・改名する・複製する・追跡先を書き換える形を止める（一覧に入れた上で名前を見る）。
func branch(n Names, args []string) *Deny {
	s := spec{
		flags:   "list all remotes verbose quiet show-current no-column no-color ignore-case omit-empty no-track delete create-reflog no-create-reflog force move copy unset-upstream edit-description",
		values:  "sort format points-at set-upstream-to",
		optvals: "contains no-contains merged no-merged color column abbrev track",
		sflags:  "arvqdlitDMCfmuc",
	}
	return Walk("branch", s, args, func(e event) *Deny {
		if e.kind != "opt" {
			return nil
		}
		switch e.name {
		case "-D":
			return &Deny{"branch-delete-unmerged", "branch -D は未マージのブランチを削除します。安全側の削除 (" + n.Self + " branch -d <名前>) を試し、それでも削除したいならユーザに依頼してください。"}
		case "-M", "-C", "-c", "--move", "--copy":
			return &Deny{"branch-force-move", "branch " + e.name + " はブランチを改名するか複製し、同じ名前の既存のブランチを上書きすることがあります。ブランチの名前はワークツリーの名前とチケットの識別子に結び付いていて、変えると着手やレビューのときに名前からチケットを引けなくなります。必要な理由をユーザに伝えてください。"}
		case "--force", "--set-upstream-to", "--unset-upstream", "--edit-description":
			return &Deny{"branch-force", "branch " + e.name + " はブランチを強制的に削除するか、設定を書き換えます。安全側の削除 (" + n.Self + " branch -d <名前>) を試し、それでも必要ならユーザに依頼してください。"}
		case "-f", "-m", "-u":
			return &Deny{"branch-move", "branch " + e.name + " はブランチを強制的に動かすか、追跡先を書き換えます。必要な理由をユーザに伝えてください。"}
		}
		return nil
	})
}

// tag は一覧だけを通す。位置の引数（`tag <名前>` はタグを作る）は -l / --list のときの絞り込みだけ。
func tag(n Names, args []string) *Deny {
	list, words, skip := false, "", false
	for _, arg := range args {
		if skip {
			skip = false // 直前のオプションの値
			continue
		}
		switch {
		case arg == "-l" || arg == "--list":
			list = true
		case arg == "--contains" || arg == "--no-contains" || arg == "--points-at" || arg == "--merged" || arg == "--no-merged":
			skip = true
		case hasAnyPrefix(arg, "--contains=", "--no-contains=", "--points-at=", "--merged=", "--no-merged=", "--sort=", "--format=") ||
			arg == "-n" || (strings.HasPrefix(arg, "-n") && len(arg) > 2 && arg[2] >= '0' && arg[2] <= '9'):
		case strings.HasPrefix(arg, "-"):
			return &Deny{"tag-write", "tag は一覧のみ使用できます (" + arg + " は不可)。タグを作る・削除するのはユーザに依頼してください。"}
		default:
			words = arg
		}
	}
	if words != "" && !list {
		return &Deny{"tag-write", "tag " + words + " はタグを作ります。tag は一覧のみ使用できます（絞り込むなら " + n.Self + " tag -l <型>）。タグを作るのはユーザに依頼してください。"}
	}
	return nil
}

func hasAnyPrefix(s string, prefixes ...string) bool {
	for _, p := range prefixes {
		if strings.HasPrefix(s, p) {
			return true
		}
	}
	return false
}

// remote は一覧だけを通す。
func remote(n Names, args []string) *Deny {
	for _, arg := range args {
		switch arg {
		case "-v", "--verbose", "show", "get-url":
			continue
		case "add", "set-url", "set-head", "set-branches", "remove", "rm", "rename", "prune", "update":
			return &Deny{"remote-write", "remote " + arg + " は取得先・送信先を書き換えます。ユーザに依頼してください。"}
		}
		if strings.HasPrefix(arg, "-") {
			return &Deny{"remote-option", "remote は一覧のみ使用できます (" + arg + " は不可)。一覧は " + n.Self + " remote -v、1 つの取得先は " + n.Self + " remote get-url <名前> で見られます。ほかの操作が必要ならユーザに依頼してください。"}
		}
	}
	return nil
}

// stash は退避と戻すことだけを通す。破棄（drop・clear）は止める。
func stash(n Names, args []string) *Deny {
	action := "push"
	if len(args) > 0 && args[0] != "" {
		action = args[0]
	}
	switch {
	case action == "list" || action == "show" || action == "push" || action == "save" || action == "pop" || action == "apply" ||
		strings.HasPrefix(action, "-"):
		return nil
	case action == "drop" || action == "clear":
		return &Deny{"stash-drop", "stash " + action + " は退避した変更を破棄します。中身を " + n.Self + " stash show -p で確かめ、不要と判断した理由をユーザに伝えてください。"}
	}
	return &Deny{"stash-action", "stash " + action + " は指定できません。指定できるのは list / show / push / pop / apply です。"}
}

// commit は、検査を飛ばす形（--no-verify・-n）と、直前のコミットを書き換える --amend を止める。中身の点検は
// /commit スキルと ask のルールの側でやる。
func commit(args []string) *Deny {
	s := spec{
		flags:    "all quiet verbose signoff no-signoff only include allow-empty allow-empty-message dry-run short porcelain long branch null status no-status reset-author edit no-edit verify no-verify pathspec-file-nul amend",
		values:   "message file author date cleanup trailer pathspec-from-file template fixup squash",
		optvals:  "untracked-files",
		sflags:   "aqvsoiezn",
		svalues:  "mFt",
		soptvals: "u",
	}
	return Walk("commit", s, args, func(e event) *Deny {
		if e.kind != "opt" {
			return nil
		}
		switch e.name {
		case "--no-verify", "-n":
			return &Deny{"commit-no-verify", "commit の " + e.name + " (--no-verify) はコミット前の検査を飛ばします。検査が失敗するなら、その原因を直してください。"}
		case "--amend":
			return &Deny{"commit-amend", "commit --amend は直前のコミットを書き換えます。直すなら新しいコミットを積んでください。"}
		}
		return nil
	})
}
