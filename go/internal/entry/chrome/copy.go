package chrome

import (
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
