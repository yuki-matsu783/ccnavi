package gitwrap

import (
	"os"
	"strings"

	"ccnavi/internal/infra/ospath"
)

// fetch は fetch と pull。手元の参照を書き換える形（--force・--prune）と、履歴を書き換える pull --rebase と、取ってきた
// ものを手元の参照へ直に書く refspec（URL も `:` を含むので同じく止まる）を止める。
//
// pull は取ってきたブランチを今のブランチへ merge するので、統合先のユーザのチェックアウトでは merge と同じく別の
// ブランチを取り込めない。通すのは、位置引数が無い形（上流の更新）と、現在のブランチのリモート + 現在のブランチと同じ
// 名前（大小も含めた完全一致）だけ。位置引数が 0〜1 個の形は git が上流（branch.<名>.merge）を merge するので、上流が
// 統合先自身でなければ止める（上流が未設定なら git が失敗するだけなので通す）。
func (e *Env) fetch(sub string, args []string) *Deny {
	s := spec{
		flags:   "quiet verbose progress no-progress tags no-tags all dry-run no-recurse-submodules force prune prune-tags unshallow show-forced-updates no-show-forced-updates write-fetch-head no-write-fetch-head",
		values:  "jobs depth deepen shallow-since",
		sflags:  "qvntfpP",
		svalues: "j",
	}
	if sub == "pull" {
		s = spec{
			flags:   "quiet verbose ff ff-only no-ff no-rebase stat no-stat no-edit edit commit no-commit progress no-progress tags no-tags force prune unshallow autostash no-autostash",
			values:  "depth deepen shallow-since",
			optvals: "rebase",
			sflags:  "qvnfpr",
		}
	}
	var refs []string
	other := false
	cur := ""
	d := Walk(sub, s, args, func(ev event) *Deny {
		switch ev.kind {
		case "opt":
			switch ev.name {
			case "-f", "--force", "--prune", "-p", "-P", "--prune-tags":
				return &Deny{"fetch-force", ev.name + " は手元の参照を書き換えます。オプション無しの " + e.Self + " " + sub + " で足ります。"}
			case "--rebase", "-r":
				return &Deny{"pull-rebase", "pull の " + ev.name + " は手元のコミットを取ってきた側へ付け直し、履歴を書き換えます。取り込むのは " + e.Self + " pull（merge。統合先のユーザのチェックアウトでは、統合先の更新だけ）か、親のブランチなら " + e.Sync + " <P> です。"}
			}
		case "pos":
			refs = append(refs, ev.value)
			// pull の 1 語目はリモート、2 語目はブランチ、3 語目以降は取ってくる先が増える形（オクトパス）。
			if len(refs) == 1 {
				cur = e.branchNow()
			}
			switch len(refs) {
			case 1:
				remote, _ := e.config("branch." + cur + ".remote")
				if remote == "" {
					remote = "origin"
				}
				other = other || ev.value != remote
			case 2:
				other = other || ev.value != cur
			default:
				other = true
			}
			if strings.ContainsAny(ev.value, ":+") {
				return &Deny{"fetch-refspec", ev.value + " は取ってきたものを手元の参照へ直に書く形（refspec）か URL です。取ってくるのはブランチ名だけで、" + e.Self + " " + sub + " <リモート> <ブランチ> の形で書いてください。手元の ref は " + e.Sync + " <ブランチ> が進めます（親のワークツリー以外のブランチは、取ってきた後の" + e.Self + " merge <リモート>/<ブランチ>）。"}
			}
		}
		return nil
	})
	if d != nil || sub != "pull" {
		return d
	}
	integ := e.targetHere()
	if integ == "" {
		return nil
	}
	if !other && len(refs) <= 1 {
		if up, _ := e.config("branch." + integ + ".merge"); up != "" && up != "refs/heads/"+integ {
			other = true
			refs = append(refs, "（上流 "+strings.TrimPrefix(up, "refs/heads/")+"）")
		}
	}
	if !other {
		return nil
	}
	return e.onTarget(integ, "統合先 "+integ+" のユーザのチェックアウトでは、pull <リモート> <ブランチ>（"+strings.Join(refs, " ")+"）で別のブランチ・リモートを取り込めません（merge できないのと同じです）。統合先へは直接 merge しない決まりなので、取り込みは integrate が行います。", "NOTE_GIT_PULL_ON_TARGET_ALLOWED")
}

// protected は、名前だけで統合先とみなすブランチ。
func protected(branch string) bool {
	switch branch {
	case "main", "master", "develop", "release":
		return true
	}
	return strings.HasPrefix(branch, "release/")
}

// push は、自分が居るブランチを同じ名前でそのまま送る形だけを通す。通さないのは「戻せなくなる形」（force・delete・
// all/mirror/tags）と「ユーザの判断を経ない形」（別の名前へ送る refspec、統合先への直接の push、子チケットのワークツリーからの
// push）。統合はユーザがマージリクエストで行う。
//
// 送り先が origin で、ワークツリー（`.claude/worktrees/` の下）から送るときは、リモートから消えた親のブランチへ送らないか
// をラッパーが ls-remote で確かめる（gate の行で頼む）。
func (e *Env) push(args []string, a *Answer) *Deny {
	branch, _ := e.git("rev-parse", "--abbrev-ref", "HEAD")
	if branch == "" || branch == "HEAD" {
		return &Deny{"detached-head", "現在のHEADがブランチに属していません（detached HEAD）。送る先が決まらないので使用できません。"}
	}
	top := e.Top()
	name, inWorktree := e.worktreeName(top)
	if inWorktree {
		if parent, child := e.childParent(name); child {
			return &Deny{"push-child-worktree", name + " は子チケットのワークツリーです。子のブランチはリモートへ送りません。親（" + parent + "）が子の成果を取り込んでから、親のワークツリー (.claude/worktrees/" + parent + ") で送ります。子は作業を終えたら結果を報告して終わってください。"}
		}
	}
	// 固定のリストに無い名前の統合先（develop-v1.0.0 など）も止める。名前はこのツリーが属するリポジトリについて決める。
	if protected(branch) || branch == integration(cmpOr(top, e.Cwd)) {
		return &Deny{"push-integration-branch", branch + " は統合先です。統合はユーザがマージリクエストで行うので、ここへ直接は送りません。作業用のブランチから送ってください。"}
	}
	remote := ""
	for _, arg := range args {
		switch {
		case arg == "-u" || arg == "--set-upstream" || arg == "--porcelain" || arg == "--quiet" || arg == "-q" || arg == "--verbose" || arg == "-v":
		case arg == "--no-verify":
			return &Deny{"push-no-verify", arg + " は送る前の検査を飛ばします。検査が失敗するなら原因を直してください。"}
		case arg == "--force-with-lease" || strings.HasPrefix(arg, "--force-with-lease=") || arg == "--force-if-includes" || arg == "-f" || arg == "--force":
			return &Deny{"push-force", arg + " はリモートの履歴を書き換えます。送り直したい理由をユーザに伝えてください。"}
		case arg == "-d" || arg == "--delete":
			return &Deny{"push-delete", arg + " はリモートのブランチを削除します。ユーザに依頼してください。"}
		case arg == "--all" || arg == "--mirror" || arg == "--tags" || arg == "--follow-tags" || arg == "--prune" || arg == "--atomic":
			return &Deny{"push-all", arg + " は今のブランチ以外も動かします。使用できるのは、居るブランチをそのまま送る形だけです。"}
		case strings.HasPrefix(arg, "-"):
			return &Deny{"push-option", "push で " + arg + " は使用できません。使用できるのは 'push [-u] [<リモート>] [" + branch + "]' の形だけです。"}
		case strings.Contains(arg, ":"):
			return &Deny{"push-refspec", arg + " は送り先を直に書く形（refspec や URL）です。設定済みのリモート名だけを使い、居るブランチをそのままの名前で送ってください。"}
		case remote == "":
			remote = arg
		case arg != branch && arg != "HEAD":
			return &Deny{"push-other-branch", arg + " は今居るブランチ（" + branch + "）ではありません。他のブランチは、そこへ移ってから送ってください。"}
		}
	}
	if inWorktree && e.pushesToOrigin(remote, branch) {
		a.GateTop, a.GateBranch = phys(top), branch
	}
	return nil
}

func cmpOr(a, b string) string {
	if a != "" {
		return a
	}
	return b
}

// pushesToOrigin は、送り先が origin か。送り先は git と同じ順で解く: 引数のリモート → branch.<b>.pushRemote →
// remote.pushDefault → branch.<b>.remote → origin。設定済みのリモート名でない引数（`:` を含まない URL やローカルの
// パス）は、origin と同じ URL なら origin とみなす（origin の URL がローカルのパスのとき、ゲートを飛ばせないように）。
func (e *Env) pushesToOrigin(remote, branch string) bool {
	dest := remote
	for _, key := range []string{"branch." + branch + ".pushRemote", "remote.pushDefault", "branch." + branch + ".remote"} {
		if dest != "" {
			break
		}
		dest, _ = e.config(key)
	}
	if dest == "" || dest == "origin" {
		return true
	}
	url, ok := e.config("remote." + dest + ".url")
	if !ok {
		url = dest
	}
	origin, _ := e.config("remote.origin.url")
	return origin != "" && url == origin
}

// childParent は、名前 name のワークツリーが子チケットのもの（`parent:` の行がある）か、とその親の識別子。
//
// 子の承認済みチケットを置くのは、ふつう親のワークツリー。ワークスペースルートだけを見ると、親のツリーに置かれた子を
// 見落とすので、ccnavi が承認済みチケットを探すのと同じツリー（ワークスペースルート・projects/ の下・.claude/worktrees/ の下）
// を全部見る。レビュー待ち（review/）と閉じた承認済みチケット（done/）も見る（子を閉じたあと、親が取り込んで片付けるまでの
// 間もそのツリーは子のもの）。
func (e *Env) childParent(name string) (string, bool) {
	trees := []string{e.Root}
	for _, base := range []string{ospath.Join(e.Root, "projects"), ospath.Join(e.Root, ".claude", "worktrees")} {
		names, _ := os.ReadDir(base)
		for _, n := range names {
			if p := ospath.Join(base, n.Name()); ospath.IsDir(p) {
				trees = append(trees, p)
			}
		}
	}
	for _, t := range trees {
		for _, copy := range []string{
			ospath.Join(t, ".ccnavi", "approved", "doing", name+".md"),
			ospath.Join(t, ".ccnavi", "approved", "done", name+".md"),
			ospath.Join(t, "wip", "proposals", "review", name+".md"),
		} {
			text, err := ospath.ReadDiskText(copy)
			if err != nil {
				continue
			}
			for _, line := range strings.Split(text, "\n") {
				if rest, ok := strings.CutPrefix(line, "parent:"); ok {
					return strings.TrimSpace(rest), true
				}
			}
		}
	}
	return "", false
}
