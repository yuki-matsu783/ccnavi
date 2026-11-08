package cli

import (
	"fmt"
	"io"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/proposal/planedit"
)

// nextPlanVerify は書き換えたあとに、承認の前に改めて preview で見せるよう言う次の一手。
const nextPlanVerify = "NEXT_PLAN_VERIFY"

// planNotFound は承認待ちの置き場に親の提案が無いときの苦情を書く。
func planNotFound(stderr io.Writer, conf *config.Settings, name string) int {
	fmt.Fprintf(stderr, "ccnavi: %s の親の提案が承認待ちの置き場（%s/%s/）にありません。"+
		"--plan-order には承認待ちの親の識別子を指定してください。\n", name, conf.Tickets, place.Todo)
	return config.ExitError
}

// pickProposal は承認待ちの親の提案を 1 つに決める。同じ識別子が複数のツリーにあれば、打ったツリーの側を採る。
func pickProposal(stderr io.Writer, conf *config.Settings, root, name, suffix string) ([]*ticket.Ticket, *ticket.Ticket, int, bool) {
	proposals := approval.ReadProposals(conf, root, nil).Kept
	cwd, _ := ospath.Getwd()
	target, ambiguous := planedit.Pick(proposals, name, cwd)
	if ambiguous != "" {
		fmt.Fprintf(stderr, "ccnavi: %s%s\n", ambiguous, suffix)
		return nil, nil, config.ExitError, false
	}
	if target == nil {
		return nil, nil, planNotFound(stderr, conf, name), false
	}
	return proposals, target, 0, true
}

// fillPhases は `--plan-order <親> --fill-phases`。計画（plan:・feedback:）のステップが使うフェーズ定義だけを
// phases.yml から読み、提案の phases: をその定義の集まりに差し替える。書けたか変わらなければ 0、書かなければ 1。
func fillPhases(stdout, stderr io.Writer, conf *config.Settings, root, name string) int {
	_, target, code, ok := pickProposal(stderr, conf, root, name, "。何も反映しません。")
	if !ok {
		return code
	}
	done := planedit.Fill(conf, root, target, planedit.CurrentParent(conf, root, target.ID))
	if done.Why != "" {
		fmt.Fprintf(stderr, "ccnavi: %s\n", done.Why)
		return config.ExitError
	}
	if !done.Changed {
		fmt.Fprintf(stdout, "%s の phases: は計画が使う定義と同じです。変更しません。\n", name)
		return config.ExitOK
	}
	line := name + " の phases: に " + strings.Join(done.Copied, ", ") + " を反映しました"
	if len(done.Dropped) > 0 {
		line += "（使わない定義を削除しました: " + strings.Join(done.Dropped, ", ") + "）"
	}
	if len(done.Held) > 0 {
		line += "。承認済みの子チケットに対応するフェーズの定義（" + strings.Join(done.Held, ", ") +
			"）は、承認済みチケットからコピーしました。"
	}
	fmt.Fprintln(stdout, line)
	fmt.Fprintln(stdout, texts.Say(root, nextPlanVerify, nil, nil))
	return config.ExitOK
}

// reorder は `--plan-order <親> --json [--order <JSON>]` と、保存の `--order --expect --write`。読むだけなら答えを
// 出して 0。書くときは、書けたか変わらなければ 0、書かなければ 1（理由は標準エラー。`--json` なら答えの written が
// 偽）。`--order` の形の誤りと、親の提案が無い・読めないときは 1。VS Code の計画編集タブが打つ。
func reorder(stdout, stderr io.Writer, conf *config.Settings, root, name string, asJSON bool, orderText *string,
	write bool, expect string) int {
	// 1. 対象の提案と、承認済みの親の姿を読み、並べ替えの元（source）を作る。
	proposals, target, code, ok := pickProposal(stderr, conf, root, name, "")
	if !ok {
		return code
	}
	current := planedit.CurrentParent(conf, root, target.ID)
	source, why := planedit.Load(conf, root, target, proposals, current)
	if source == nil {
		fmt.Fprintf(stderr, "ccnavi: %s\n", why)
		return config.ExitError
	}
	var order planedit.Order
	if orderText != nil {
		order, why = planedit.ParseOrder(*orderText, source.Parent)
		if order == nil {
			fmt.Fprintf(stderr, "ccnavi: --order: %s\n", why)
			return config.ExitError
		}
	}
	// 2. 並べ替えた結果を出す。書かない形（読むだけ）はここで終わる。
	ans := planedit.Reorder(source, order)
	body := ans.Body()
	if !write {
		writeJSON(stdout, body, false, 0)
		return config.ExitOK
	}
	// 3. 書く。expect は、画面で見た元のファイルの版。食い違えば planedit が書かずに断る。
	saved := ans.Save(expect)
	for _, message := range saved.Messages {
		fmt.Fprintf(stderr, "ccnavi: %s\n", message)
	}
	for _, detail := range saved.Details {
		fmt.Fprintf(stderr, "  %s\n", detail)
	}
	// 書けたら、書いたあとのファイルの版（source_sha）を答えと各 plan に入れる。呼び手は次の保存でこれを expect に使う。
	body.Set("written", saved.Written)
	sha := ""
	if saved.Written {
		cwd, _ := ospath.Getwd()
		sha = planedit.RereadSHA(conf, root, name, current, cwd)
		body.Set("source_sha", sha)
		plans, _ := body.Value("plans").([]any)
		for _, p := range plans {
			if plan, ok := p.(*pyjson.Object); ok {
				plan.Set("source_sha", sha)
			}
		}
	}
	switch {
	case asJSON:
		writeJSON(stdout, body, false, 0)
	case saved.Written:
		fmt.Fprintf(stdout, "%s の計画の順序を書き換えました（source_sha: %s）\n", name, sha)
		fmt.Fprintln(stdout, texts.Say(root, nextPlanVerify, nil, nil))
	case saved.OK:
		fmt.Fprintf(stdout, "%s の計画の順序に変更はありません。ファイルは更新しません。\n", name)
	}
	return okIf(saved.OK)
}
