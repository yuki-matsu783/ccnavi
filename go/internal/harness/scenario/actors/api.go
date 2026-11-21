package actors

import (
	"fmt"

	"ccnavi/internal/harness/gitlabapi"
	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/harness/scenario/host"
	"ccnavi/internal/records/diaglog"
)

// RunAPI は human_api の操作を書いた順に実行して、止まらずに次の step へ進む。失敗したら 11 を返す
// （進んだときは 0）。操作の結果は 1 件ごとに state に書く（再開しても同じ操作を二度実行しない）。
// idx は step の 0 始まりの番号。
func RunAPI(m *host.Manager, st *core.State, idx int, ops []core.Op, root string, save func() error, out func(string)) int {
	out(fmt.Sprintf("\n== 人間役の操作（step %d、API）==", idx+1))
	if st.Threads == nil {
		st.Threads = map[string]core.ThreadRef{}
	}
	for _, op := range ops {
		res := HumanOp(m, root, st.Host, op, st.Threads)
		res.Step = idx + 1
		st.HumanOps = append(st.HumanOps, res)
		_ = save()
		ok := res.Status >= 200 && res.Status < 300 && res.Error == ""
		detail := fmt.Sprintf("HTTP %d", res.Status)
		result := "成功"
		if !ok {
			result = "失敗"
			detail = res.Error
			if detail == "" {
				detail = "失敗"
			}
		}
		out(fmt.Sprintf("  %s: %s（%s）", op.Text(), result, detail))
		if !ok {
			st.NextStep = idx + 1
			st.Halt = &core.Halt{Step: idx + 1, Kind: "human_api", Reasons: []string{fmt.Sprintf("%s: %s", op.Text(), detail)}}
			_ = save()
			out(fmt.Sprintf("step %d: 人間役の操作が失敗した。後続の step には進まず止める", idx+1))
			return 11
		}
	}
	st.NextStep = idx + 1
	_ = save()
	return 0
}

// HumanOp は人間役（ccnavi-reviewer）の操作を 1 件実行する。Status が 2xx で Error が無ければ成功。
// 再試行しない（thread は冪等でない）。対象の MR は、使い捨てのプロジェクトにちょうど 1 件あるものだけ。
// トークンは結果にもエラーの文字列にも入れない。thread の名前と discussion の対応は threads に足す。
func HumanOp(m *host.Manager, root string, h *host.Host, op core.Op, threads map[string]core.ThreadRef) core.HumanOp {
	res := core.HumanOp{Op: op.Op, As: op.As, Thread: op.Thread}
	fail := func(status int, msg string) core.HumanOp {
		res.Status, res.Error = status, msg
		return res
	}
	defer diaglog.Get(host.LogName, root).Info("人間役の操作", "op", op.Op, "as", op.As, "project_id", h.ProjectID)

	client, values, err := m.HumanAccess()
	if err != nil {
		return fail(0, err.Error())
	}
	token := values[gitlabapi.ReviewerTokenKey]
	if token == "" {
		return fail(0, "人間役のトークンをトークンのファイルから読めない")
	}
	status, body := client.ListMRs(token, h.ProjectID, "all")
	mrs := gitlabapi.AsList(body)
	if status < 200 || status >= 300 || mrs == nil {
		return fail(status, fmt.Sprintf("MR の一覧を取れない（HTTP %d）", status))
	}
	if len(mrs) != 1 {
		return fail(status, fmt.Sprintf("プロジェクトの MR が %d 件ある（ちょうど 1 件が要る）", len(mrs)))
	}
	iid, _ := gitlabapi.Int(gitlabapi.AsMap(mrs[0])["iid"])
	switch op.Op {
	case core.OpThread:
		status, body = client.CreateDiscussion(token, h.ProjectID, iid, op.Body, "human-thread")
		if id := gitlabapi.Text(gitlabapi.AsMap(body)["id"]); status >= 200 && status < 300 && id != "" {
			res.DiscussionID = id
			if op.As != "" {
				threads[op.As] = core.ThreadRef{ID: id, IID: iid}
			}
		}
	case core.OpApprove:
		status, _ = client.Approve(token, h.ProjectID, iid)
	default:
		ref, ok := threads[op.Thread]
		if !ok {
			return fail(0, fmt.Sprintf("名前 %s のスレッドを、この回では立てていない", op.Thread))
		}
		res.DiscussionID = ref.ID
		status, _ = client.ResolveDiscussion(token, h.ProjectID, ref.IID, ref.ID, true)
	}
	res.Status, res.IID = status, iid
	if status < 200 || status >= 300 {
		res.Error = fmt.Sprintf("HTTP %d", status)
	}
	return res
}
