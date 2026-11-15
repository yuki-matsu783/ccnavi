package chrome

import (
	"encoding/json"
	"strconv"

	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/hostreply"
)

// opCopy は、拡張の service worker が取ってきた MR のスレッドとレビューの応答（replies）から、取ってきた時点の状態
// （`ccnavi host copy` と同じ形。confirm・decide の result）を組む。ホストへの問い合わせとページの送りは拡張が持ち、
// 読みは sh の review と同じ hostreply が持つ。
//
// replies は {host, mr: {number, url}, threads: [ページ...], reviews: [レビュー...], fetched_at}。threads は、GitHub なら
// GraphQL の reviewThreads の応答をページごとに、GitLab なら全ページを繋いだ discussions を 1 つだけ並べる。
func opCopy(req *pyjson.Object, _ string) (*pyjson.Object, error) {
	r, ok := req.Value("replies").(*pyjson.Object)
	if !ok {
		return nil, refused("replies がありません")
	}
	kind, _ := text(r, "host")
	form := map[string]hostreply.ThreadsForm{"github": hostreply.FormGraphQL, "gitlab": hostreply.FormDiscussions}[kind]
	mr, _ := r.Value("mr").(*pyjson.Object)
	var number int
	var url string
	if mr != nil {
		number, ok = integer(mr.Value("number"))
		url, _ = text(mr, "url")
	}
	pages, isList := r.Value("threads").([]any)
	when, _ := text(r, "fetched_at")
	if form == "" || mr == nil || !ok || number <= 0 || !isList {
		return nil, refused("replies の形が違います（host・mr・threads）")
	}
	threads := make([][]byte, 0, len(pages))
	for _, p := range pages {
		b, err := pyjson.Dumps(p, pyjson.Options{RawUnicode: true})
		if err != nil {
			return nil, err
		}
		threads = append(threads, []byte(b))
	}
	reviews, err := pyjson.Dumps(r.Value("reviews"), pyjson.Options{RawUnicode: true})
	if err != nil {
		return nil, err
	}
	got, err := hostreply.NewCopy(kind, strconv.Itoa(number), url, nil, form, threads, []byte(reviews), when)
	if err != nil {
		return nil, refusedf("取ってきたマージリクエスト（%s）のスレッドかレビューを読めません（%s）", url, err)
	}
	out, err := pyjson.Loads(got.JSON())
	if err != nil {
		return nil, err
	}
	return pyjson.NewObject("copy", out), nil
}

// opWriteInfo は decide の書く前の確かめの材料を、service worker が取ってきた応答（replies。形は
// hostreply.WriteReplies）から読む。答えは {info: <hostreply.WriteInfo>}。
func opWriteInfo(req *pyjson.Object, _ string) (*pyjson.Object, error) {
	raw, err := pyjson.Dumps(req.Value("replies"), pyjson.Options{RawUnicode: true})
	if err != nil {
		return nil, err
	}
	info, err := hostreply.WriteInfoOf([]byte(raw))
	if err != nil {
		return nil, refusedf("書く前の確認に取ってきた応答を読めません（%s）", err)
	}
	return answerOf("info", info)
}

// opPrecheck は decide の書く計画（plan。decide の host_plan）と書く前の確かめの材料（info。write-info の答え）を
// 突き合わせる。答えは {precheck: <hostreply.PrecheckResult>}。
func opPrecheck(req *pyjson.Object, _ string) (*pyjson.Object, error) {
	var plan hostreply.WritePlan
	var info hostreply.WriteInfo
	if err := valueAs(req.Value("plan"), &plan); err != nil {
		return nil, refused("precheck の plan の形が違います")
	}
	if err := valueAs(req.Value("info"), &info); err != nil {
		return nil, refused("precheck の info の形が違います")
	}
	return answerOf("precheck", hostreply.Precheck(plan, info))
}

// valueAs は要求の値を Go の型に読む（JSON を経由する）。
func valueAs(v any, out any) error {
	raw, err := pyjson.Dumps(v, pyjson.Options{RawUnicode: true})
	if err != nil {
		return err
	}
	return json.Unmarshal([]byte(raw), out)
}

// answerOf は Go の値を答えの key に置く（JSON を経由する）。
func answerOf(key string, v any) (*pyjson.Object, error) {
	raw, err := json.Marshal(v)
	if err != nil {
		return nil, err
	}
	got, err := pyjson.Loads(string(raw))
	if err != nil {
		return nil, err
	}
	return pyjson.NewObject(key, got), nil
}

// opCloses は「始める」の、開いた MR の本文のそれぞれが issue を閉じるか（sh の start の代わりの読み方と同じ
// hostreply.ClosesIssue）。要求は {repo: "<owner>/<repo>", issue: <番号>, bodies: [本文か null...]}、答えは
// {closes: [真偽...]}（bodies と同じ順）。
func opCloses(req *pyjson.Object, _ string) (*pyjson.Object, error) {
	repo, okRepo := text(req, "repo")
	issue, okIssue := integer(req.Value("issue"))
	bodies, okList := req.Value("bodies").([]any)
	if !okRepo || repo == "" || !okIssue || issue <= 0 || !okList {
		return nil, refused("closes の要求の形が違います（repo・issue・bodies）")
	}
	out := make([]any, 0, len(bodies))
	for _, b := range bodies {
		body, _ := b.(string)
		out = append(out, hostreply.ClosesIssue(repo, issue, body))
	}
	return pyjson.NewObject("closes", out), nil
}
