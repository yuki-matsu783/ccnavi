package ticket

import (
	"fmt"
	"slices"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/tickets/model/ids"
)

// BranchNameProblems は新規の提案の識別子が、親のブランチ名の規則に合わないところ（ユーザに見せる文。深刻度は
// 呼び手が決める）。ref として安全な形、統合先や保護されたブランチの名前を使わない、新しい親は
// `<先頭の語>-<番号>-<slug>` の形で番号は issue の番号、を見る。他のチケットと並べて見る検査は lint の側。
// integration は統合先の名前（渡したときだけ予約に足す）、prefixes は使える先頭の語。
func (t *Ticket) BranchNameProblems(integration string, prefixes []string) []string {
	name := t.ID
	folded := pyfmt.Casefold(name)
	var found []string
	if strings.Contains(name, "..") {
		found = append(found, "識別子に `..` を含む。git のブランチ名に使えない")
	}
	if strings.HasSuffix(folded, ".lock") {
		found = append(found, "識別子が `.lock` で終わる。git のブランチ名に使えない")
	} else if strings.HasSuffix(name, ".") {
		found = append(found, "識別子が `.` で終わる。git のブランチ名に使えない")
	}
	if t.IsChild() {
		return found
	}
	if slices.Contains(config.ReservedBranchIDs(), folded) || strings.HasPrefix(folded, "release-") {
		found = append(found, "識別子が統合先や保護されたブランチの名前（main・master・develop・release・release-*）"+
			"と同じ。親のブランチ名が統合先と同じになる")
	} else if integration != "" && folded == pyfmt.Casefold(integration) {
		found = append(found, "識別子が統合先の名前（"+integration+"）と同じ。親のブランチ名が統合先と同じになる")
	}
	return append(found, t.formProblems(prefixes)...)
}

// formProblems は新しい親の識別子が `<先頭の語>-<番号>-<slug>` の形と issue: に合っているか。別のリポジトリの課題は
// 番号を比べない（その番号はこのリポジトリの issue と紛れる）。
func (t *Ticket) formProblems(prefixes []string) []string {
	name := t.ID
	form, ok := ids.FormOf(name, prefixes)
	ownIssue := t.Issue != 0 && t.IssueRepo == ""
	if !ok {
		shape := "`<先頭の語>-<番号>-<slug>` の形（先頭の語は " + strings.Join(prefixes, "・") + " のどれか）"
		if ownIssue {
			want := fmt.Sprintf("feature-%d-", t.Issue)
			if t.Project != "" {
				want += t.Project + "-"
			}
			return []string{fmt.Sprintf("親の識別子は %s にする。`issue: %d` から始めるなら `%s<slug>` など", shape, t.Issue, want)}
		}
		return []string{"親の識別子は " + shape + " にする。番号は issue の番号" +
			"（`ccnavi-start.sh --issue <番号>` が決める名前にする）"}
	}
	var found []string
	slug, project := pyfmt.Casefold(form.Slug), pyfmt.Casefold(t.Project)
	switch {
	case ownIssue && form.Number != t.Issue:
		found = append(found, fmt.Sprintf("識別子の番号 %d が `issue: %d` と違う。"+
			"issue から始める識別子は `%s-%d-<slug>` にする", form.Number, t.Issue, form.Prefix, t.Issue))
	case ownIssue && t.Project != "" && !strings.HasPrefix(slug, project+"-") && slug != project:
		found = append(found, fmt.Sprintf("プロジェクト %s の issue から始める識別子は "+
			"`%s-%d-%s-<slug>` にする。issue の番号はリポジトリごとなので、"+
			"ワークスペースや別のプロジェクトの同じ番号の issue と名前を分ける", t.Project, form.Prefix, t.Issue, t.Project))
	}
	if n := utf8.RuneCountInString(name); n > ids.SuggestedIDLength {
		found = append(found, fmt.Sprintf("識別子が %d 文字を超える（%d 文字）。"+
			"ワークツリーのパスが Windows の上限（MAX_PATH）に近づくので slug を短くする", ids.SuggestedIDLength, n))
	}
	return found
}
