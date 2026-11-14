package start

import (
	"os"
	"slices"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/model/ids"
)

// BranchArgs は BranchOK の材料。
type BranchArgs struct {
	Branch string
	// Strict は、origin のブランチの一覧（ls-remote）を取れなければ止めるか。偽なら手元に残る origin の追跡ブランチで
	// 代わりに確かめて「リモート未確認」と言う。
	Strict bool
	// Heads は origin のブランチの一覧（1 行 1 本）のファイル。空なら取れなかった（HeadsErr がその理由の 1 行）。
	Heads, HeadsErr string
	// Hint は識別子が使えないときに添える案内、IDExit はそのときの終了コード（既定は 1）。
	Hint   string
	IDExit int
}

// BranchOK は、ブランチから作る識別子が親に使えるか（58 文字以内・末尾が -<2 桁> でない・字の種類・NFC）と、同じ識別子に
// なる別のブランチ（origin の全ブランチと手元のブランチ。英字の大文字小文字は区別しない）が無いかを確かめる。
// 見つかれば fail。origin の一覧を取れず手元で代わりに確かめたときは `note <案内>`。
func BranchOK(cwd string, a BranchArgs) (string, int) {
	var o outcome
	answer := map[string]string{}
	for _, line := range ids.IDAnswer(a.Branch) {
		k, v, _ := strings.Cut(line, " ")
		answer[k] = v
	}
	if p := answer["problem"]; p != "" {
		hint, code := a.Hint, a.IDExit
		if hint == "" {
			hint = "ブランチ名を直すか、ユーザに相談してください。"
		}
		if code == 0 {
			code = exitUnmet
		}
		o.fail("adopt-id", code, "ブランチ "+a.Branch+" から作る識別子 "+answer["id"]+" は使えない（"+p+"）。何も作成せずに処理を中断しました。"+hint)
		return o.String(), 0
	}
	var names []string
	remote := a.Heads != ""
	if remote {
		text, _ := os.ReadFile(a.Heads)
		names = ospath.Splitlines(string(text))
	} else {
		if a.Strict {
			o.fail("adopt-heads", exitUnmet, "origin のブランチ一覧を取れなかった（"+a.HeadsErr+"）。同じ識別子になる別のブランチが無いか確かめられないので止めた。")
			return o.String(), 0
		}
		for _, n := range strings.Split(gitOut(cwd, "for-each-ref", "--format=%(refname:lstrip=3)", "refs/remotes/origin"), "\n") {
			if n != "HEAD" {
				names = append(names, n)
			}
		}
	}
	names = append(names, strings.Split(gitOut(cwd, "for-each-ref", "--format=%(refname:short)", "refs/heads"), "\n")...)
	slices.Sort(names)
	want := asciiLower(ids.BranchToID(a.Branch))
	for _, n := range slices.Compact(names) {
		if n != "" && n != a.Branch && asciiLower(ids.BranchToID(n)) == want {
			o.fail("adopt-id-clash", exitUnmet, "ブランチ "+a.Branch+" と "+n+" は、どちらも識別子 "+ids.BranchToID(a.Branch)+"（英字の大文字小文字は区別しない）になる。ワークツリーの置き場が重なるので使えません。何も作成せずに処理を中断しました。ユーザに相談してください。")
			return o.String(), 0
		}
	}
	if !remote {
		o.val("note", "リモート未確認。origin のブランチ一覧を取れなかったので、同じ識別子になる別のブランチは手元に残る origin の追跡ブランチでだけ確かめた")
	}
	return o.String(), 0
}

// asciiLower は英字だけを小文字にする（識別子の字の大文字小文字を、英字に限って区別しない）。
func asciiLower(s string) string {
	return strings.Map(func(r rune) rune {
		if r >= 'A' && r <= 'Z' {
			return r + 'a' - 'A'
		}
		return r
	}, s)
}

// Merged は、同じブランチ名のマージ済み MR の応答（ホストの一覧）を読み、あれば止める（マージ済みのブランチは再利用しない）。
func Merged(kind, branch string, data []byte) (string, int) {
	got, code := Pick("merged-mr", kind, "", data)
	if code != 0 {
		return "", code
	}
	var o outcome
	if number, _, _ := strings.Cut(strings.TrimPrefix(got, "number "), "\n"); got != "" {
		o.fail("adopt-merged", exitUnmet, "同じブランチ名 "+branch+" のマージ済み MR（!"+number+"）がある。マージ済みのブランチは再利用できません。新しいブランチで始めるか、ユーザに相談してください。")
	}
	return o.String(), 0
}

// ResolveArgs は Resolve の材料。
type ResolveArgs struct {
	// Linked はその MR が閉じる issue の一覧（<owner/repo>#<番号>。1 行 1 件）。Trust なら読まずに、渡された --issue で決める。
	Linked string
	Trust  bool
	// IssueArg・IssueN・IssueR は --issue（無ければ空）、Own は origin のリポジトリ、MR はその MR の番号、Orig は打ち直しの引数。
	IssueArg, IssueN, IssueR, Own, MR, Orig string
}

// IssueShow は、frontmatter と案内に書く issue の形。MR と同じリポジトリなら <番号>、違えば <owner/repo>#<番号>。
func IssueShow(repo, own, number string) string {
	if repo == "" || strings.EqualFold(repo, own) {
		return number
	}
	return repo + "#" + number
}

// Resolve は、MR が閉じる issue を確かめて、frontmatter に書く issue を `issue <形>` で返す。--issue があれば、その issue が
// 閉じる一覧に含まれること。無ければ 1 件に決まること（0 件は止め、2 件以上は一覧を出して終了コード 3）。
func Resolve(root string, a ResolveArgs) (string, int) {
	var o outcome
	if a.Trust {
		o.val("issue", IssueShow(a.IssueR, a.Own, a.IssueN))
		return o.String(), 0
	}
	// 大文字小文字を区別せず重複を除く（先に出た表記を残す）。
	var list []string
	seen := map[string]bool{}
	for _, l := range strings.Split(a.Linked, "\n") {
		if strings.TrimSpace(l) == "" || seen[strings.ToLower(l)] {
			continue
		}
		seen[strings.ToLower(l)] = true
		list = append(list, l)
	}
	show := func(key string) string {
		i := strings.LastIndex(key, "#")
		return IssueShow(key[:i], a.Own, key[i+1:])
	}
	switch {
	case a.IssueArg != "":
		repo := a.IssueR
		if repo == "" {
			repo = a.Own
		}
		want := strings.ToLower(repo + "#" + a.IssueN)
		for _, l := range list {
			if strings.ToLower(l) == want {
				o.val("issue", show(l))
				return o.String(), 0
			}
		}
		shown := "なし"
		if len(list) > 0 {
			shown = strings.Join(list, " ") + " "
		}
		o.fail("issue-not-linked", exitUnmet, "MR !"+a.MR+" は issue #"+a.IssueN+" を閉じるリンクを持たない（参照しているだけ）。この MR が閉じる issue: "+shown+"。説明欄に Closes #"+a.IssueN+" を書くようユーザに頼み、打ち直してください。")
	case len(list) == 0:
		o.fail("issue-none", exitUnmet, "MR !"+a.MR+" が閉じる issue が無い。説明欄に Closes #<番号> を書くようユーザに頼み、打ち直してください。")
	case len(list) > 1:
		o.out("ccnavi-start: MR !" + a.MR + " が閉じる issue が " + itoa(len(list)) + " 件ある。どれで進めるかをユーザに聞いてください。")
		for _, l := range list {
			o.out("  - " + l)
		}
		o.out(texts.Say(root, "NEXT_START_MULTI_ISSUE", texts.Values{"orig_args_q": a.Orig}, nil))
		o.exit(exitAsk)
	default:
		o.val("issue", show(list[0]))
	}
	return o.String(), 0
}

// IssueState は、issue が closed のときに止める（閉じた issue は完了済み・却下・重複のことが多い）。--reopened（closed と
// 分かって続ける）で通す。状態を読めなかった（state が空）ときは止めず、未確認と言う。
func IssueState(root, state, number, real string, reopened bool) (string, int) {
	var o outcome
	switch {
	case state == "":
		o.out("確認: issue #" + number + " の状態: 未確認（ホストから読めなかった。closed かどうかは見ていない）")
	case state != "closed":
	case reopened:
		o.out("案内: issue #" + number + " は closed。--reopened が付いているので、そのまま続ける")
	default:
		o.out("ccnavi-start: issue #" + number + " は closed です。完了済み・却下・重複のことが多いので、何も作成せずに処理を中断しました。ユーザに状況を伝えて、どれにするか聞いて返事を待ってください。直接実装には切り替えないでください。")
		o.out(texts.Say(root, "CHOICES_START_ISSUE_CLOSED", texts.Values{"number": number, "real_args_q": real}, nil))
		o.exit(exitAsk)
	}
	return o.String(), 0
}
