package cut

import (
	"strconv"

	"ccnavi/internal/policy/texts"
)

// ask は、確認が要るときの報告。切らずに終了コード 3 で止まる。書きかけの候補は refs/ccnavi/direct/<名前>/pending に
// 置き、切ったら消す。why は wip・base・accept・code（code は渡された確認コードが今の状態と合わない）。
func (r *run) ask(rs remoteState, cand, confirm string, needAccept bool, why string) {
	if cand != "" {
		ok(r.in.Cwd, "update-ref", "refs/ccnavi/direct/"+r.name+"/pending", cand)
	}
	n := strconv.Itoa
	target, integ := r.target, r.integ
	r.o.Out("確認が要る: ワークツリーはまだ切っていない。")
	if why == "code" {
		r.o.Out(texts.Say(r.in.Root, "NOTE_WORKTREE_CODE_STALE", nil, nil))
	}
	r.o.Out("状態:")
	r.o.Out("  取り込み先 " + target + " (" + r.mainTree + ")。リモートのデフォルトは " + integ)
	if r.c4 {
		r.o.Out("  cwd のブランチは " + r.cwdBranch() + " で、取り込み先と違う")
	}
	switch rs.state {
	case "behind":
		r.o.Out("  origin/" + target + " より " + n(rs.behind) + " 件遅れている")
	case "ahead":
		r.o.Out("  origin/" + target + " より " + n(rs.ahead) + " 件進んでいる（まだ送っていない）")
	case "diverged":
		r.o.Out("  origin/" + target + " と分岐している（先行 " + n(rs.ahead) + " 件・遅れ " + n(rs.behind) + " 件）")
	case "absent":
		r.o.Out("  origin に " + target + " が無い")
	case "unverified":
		r.o.Out("  リモート未確認（" + or(rs.why, "origin に届かない") + "）")
	}
	if r.c5 {
		r.o.Out("  取り込み先がデフォルトブランチ（" + integ + "）でない")
	}
	if r.c7 {
		r.o.Out("  書きかけ: 変更 " + n(r.changed) + "・新規 " + n(r.added) + "（staged " + n(r.staged) + "。コピーする場合、staged かどうかは区別されない）")
	} else if r.staged != 0 {
		r.o.Out("  staged " + n(r.staged) + "（情報）")
	}
	mainQ, targetQ := q(r.mainTree), q(target)
	switch rs.state {
	case "behind":
		r.o.Out("勧め: 先にローカルをリモートに合わせる")
		r.o.Out("  cd " + mainQ + " && git pull --ff-only origin " + targetQ + "   （書きかけは先にコミットか git stash push -u）")
	case "diverged":
		r.o.Out("勧め: 先にローカルをリモートに合わせる（合わせ方はユーザが選ぶ）")
		r.o.Out("  cd " + mainQ + " && git pull --rebase origin " + targetQ)
		r.o.Out("  cd " + mainQ + " && git pull --no-rebase origin " + targetQ)
	case "ahead":
		r.o.Out("勧め: 先に push するか、push せずに進めるかをユーザが決める（cd " + mainQ + " && git push origin " + targetQ + "）")
	case "absent":
		r.o.Out("勧め: origin に " + target + " を push するか、このまま進めるかをユーザが決める")
	case "unverified":
		r.o.Out("勧め: origin に届いてから打ち直すと、リモートとの関係を確かめられる")
	}
	var risks []string
	switch rs.state {
	case "behind", "diverged":
		risks = append(risks, "古いコードの上で作業する。push の前に合わせ直しが要り、そのとき衝突しうる")
	case "ahead":
		risks = append(risks, target+" にだけある先行コミットの上で作業する。push する前に成果だけを送ることはできない")
	case "absent":
		risks = append(risks, "origin に無いブランチの上で作業する。成果は手元の "+target+" にだけ載る")
	case "unverified":
		risks = append(risks, "リモートの最新を確かめないまま作業する。あとで合わせ直しが要りうる")
	}
	if r.c5 {
		risks = append(risks, "最後は "+target+" に 1 コミットで載る。"+integ+" に届くのは "+target+" の MR がマージされたとき")
	}
	if r.c4 {
		risks = append(risks, "cwd のブランチ（"+r.cwdBranch()+"）ではなく、ユーザのチェックアウトの "+target+" へ取り込む")
	}
	if len(risks) > 0 {
		r.o.Out("合わせない場合のリスク:")
		for _, risk := range risks {
			r.o.Out("  - " + risk)
		}
	}
	r.o.Out("選択肢:")
	if r.c7 {
		r.o.Out("  書きかけの扱い")
		r.o.Out("    A. コピーしない (--no-wip) : 書きかけはルートだけに残り、エージェントは HEAD から作業する")
		r.o.Out("    B. コピーする (--copy-wip) : いま確認した時点の書きかけをコピーする。このあとの編集はコピーされない。")
		r.o.Out("       コピーした書きかけそのものは成果に含めない。それに依存する変更は、取り込み結果だけでは動かないことがある")
	}
	r.o.Out("  横で作業するか (--side-by-side)")
	r.o.Out("    このワークツリーが残っている間は、" + r.mainTree + " で見つかった変更（エージェントが Bash で書き換えた分も含む）を")
	r.o.Out("    自動で戻さず、報告にする。ccnavi の設定ファイルは戻す")
	args := ""
	if r.c4 {
		args += " --base " + targetQ
	}
	if needAccept {
		args += " --accept " + confirm
	}
	if r.c7 {
		args += " --no-wip"
	}
	if len(args) > 0 {
		args = args[1:]
	}
	r.o.Out(texts.Say(r.in.Root, "NEXT_WORKTREE_CONFIRM", texts.Values{"name": r.name, "args_q": args}, nil))
	r.o.Out(texts.Say(r.in.Root, "NOTE_WORKTREE_WAIT_FOR_OK", nil, nil))
	r.o.Val("reason", "confirm-"+why)
	r.o.Exit(exitConfirm)
}
