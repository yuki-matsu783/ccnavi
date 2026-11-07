package chrome

import (
	"encoding/base64"
	"fmt"
	"math/big"
	"runtime"
	"slices"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/entry/version"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/core"
	"ccnavi/internal/tickets/model/host"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/model/via"
	"ccnavi/internal/tickets/phase"
	"ccnavi/internal/tickets/review/request"
)

// maxTitle は「始める」で受ける issue のタイトルの長さの上限（文字数）。
const maxTitle = 1000

// requireFamily は要求の family（親のブランチ名）が snapshot にあること。
func requireFamily(req *pyjson.Object, s *snapshot) (string, error) {
	family, ok := text(req, "family")
	if !ok || !s.has(family) {
		return "", refused("family のブランチが snapshot にありません")
	}
	return family, nil
}

// opBoard は親子のチケット 1 つの承認待ち（読み取りだけ）。判定の入力は統合先・P・閉包の P_X だけ。
func opBoard(req *pyjson.Object, root string) (*pyjson.Object, error) {
	s, err := readSnapshot(req)
	if err != nil {
		return nil, err
	}
	family, err := requireFamily(req, s)
	if err != nil {
		return nil, err
	}
	hints, err := hintsOf(req, s)
	if err != nil {
		return nil, err
	}
	c, err := s.closure(family, hints)
	if err != nil {
		return nil, err
	}
	if c.over {
		return pyjson.NewObject("family", family, "undecided", c.message(), "closure", c.json()), nil
	}
	if len(c.need) > 0 {
		return nil, refused("閉包の親子チケットをまだ取得していません：" + strings.Join(c.need, ", "))
	}
	ident := c.ident(family)
	if why := firstOf(s.rivalProblem(family, ident), c.ambiguousProblem()); why != "" {
		return pyjson.NewObject("family", family, "ident", ident, "undecided", why, "closure", c.json()), nil
	}
	if why := s.unreadable(c); why != "" {
		return pyjson.NewObject("family", family, "undecided", why, "closure", c.json()), nil
	}
	if err := s.build(root, c.families); err != nil {
		return nil, err
	}
	code, first, errText := preview(root, nil)
	if first == nil {
		return pyjson.NewObject("family", family, "closure", c.json(), "refused", previewRefusal(root, code, errText)), nil
	}
	var mine []string
	batchOf := func(body *pyjson.Object) []*pyjson.Object {
		items, _ := body.Value("batch").([]any)
		var out []*pyjson.Object
		for _, item := range items {
			if e, ok := item.(*pyjson.Object); ok {
				out = append(out, e)
			}
		}
		return out
	}
	for _, e := range batchOf(first) {
		id, _ := e.Value("ticket").(string)
		owner := id
		if p := e.Value("parent"); pyfmt.Truthy(p) {
			owner = pyfmt.Str(p)
		}
		if owner == ident {
			mine = append(mine, id)
		}
	}
	body := first
	narrowed := len(mine) > 0 && len(mine) != len(batchOf(first))
	if narrowed {
		// 画面の本文とダイジェストを、この親子のチケットの分だけで組み直す（1 回の承認は 1 つの P）。
		code, body, errText = preview(root, mine)
		if body == nil {
			return pyjson.NewObject("family", family, "closure", c.json(), "refused", previewRefusal(root, code, errText)), nil
		}
	}
	batch := []any{}
	for _, e := range batchOf(body) {
		if id, _ := e.Value("ticket").(string); slices.Contains(mine, id) {
			batch = append(batch, entry(root, e))
		}
	}
	shownText, digest := "", ""
	if len(batch) > 0 {
		shownText, _ = body.Value("text").(string)
		digest, _ = body.Value("digest").(string)
	}
	var only any
	if narrowed {
		only = mine
	}
	rejected := []any{}
	for _, item := range listOf(first.Value("rejected")) {
		r, ok := item.(*pyjson.Object)
		if !ok {
			continue
		}
		id, _ := r.Value("ticket").(string)
		if familyOf(id) != ident {
			continue
		}
		problems, _ := stringList(r.Value("problems"))
		rejected = append(rejected, pyjson.NewObject("ticket", id, "problems", relativeAll(root, list(problems))))
	}
	problems, _ := stringList(first.Value("problems"))
	why := writeRefusal(s, family)
	return pyjson.NewObject(
		"family", family,
		// 親子のチケットの識別子（親のブランチ名と違うことがある）
		"ident", ident,
		"closure", c.json(),
		// 互換の版と書く先の名前から、書けるかを見る。書けなければ表示だけにする。
		"write", pyjson.NewObject("allowed", why == "", "reason", why),
		// レビュー済みを付けられる候補。通るかは confirm がホストから取得した結果で決める。
		"reviewable", reviewable(root, ident),
		"batch", batch,
		// 画面の本文は提案をツリーからの相対パスで出すので、手を加えずに返す。ダイジェストはこの本文と承認済みの
		// チケットの中身を覆い、手元の `--agree --preview` と同じ値になる。
		"text", shownText,
		"digest", digest,
		// 承認するときに plan へ渡す絞り。このダイジェストを出したときの絞りで、絞らなければ null。
		"only", only,
		"rejected", rejected,
		"problems", relativeAll(root, list(problems)),
	), nil
}

func firstOf(texts ...string) string {
	for _, t := range texts {
		if t != "" {
			return t
		}
	}
	return ""
}

// previewRefusal は `--agree --preview` が承認待ちを出せなかった理由（標準エラーの文面）。
func previewRefusal(root string, code int, errText string) string {
	if out := relative(root, strings.TrimSpace(errText)); out != "" {
		return out
	}
	return fmt.Sprintf("終了 %d", code)
}

func reviewable(root, ident string) []any {
	conf, _ := config.Load(root)
	out := []any{}
	for _, r := range core.ReviewableOf(conf, root, ident) {
		out = append(out, pyjson.NewObject("phase", r.Phase, "mr", r.MR, "host", r.Host, "children", list(r.Children)))
	}
	return out
}

// writeRefusal はこの親子のチケットの P へ書けない理由（空なら書ける）。同梱の互換の版が統合先と違えば表示だけに
// する。書く先が予約の名前か統合先の名前なら書かない（保護されたブランチと統合先へは書かない）。
func writeRefusal(s *snapshot, family string) string {
	if c := compat(s); !c.same {
		return c.message + "。表示のみとし、承認のボタンは表示しません"
	}
	folded := pyfmt.Casefold(family)
	if slices.Contains(ids.ReservedBranchIDs, folded) || strings.HasPrefix(folded, "release-") || folded == pyfmt.Casefold(s.integration) {
		return family + " は予約された名前か統合先の名前のため、Chrome からは書き込めません"
	}
	return ""
}

// familyTree は親子のチケットの仮のツリーを組む。時刻は組む前に確かめる。答えは snapshot・親のブランチ名・閉包。
func familyTree(req *pyjson.Object, root string) (*snapshot, string, *closure, error) {
	if _, err := stamp(req); err != nil {
		return nil, "", nil, err
	}
	s, err := readSnapshot(req)
	if err != nil {
		return nil, "", nil, err
	}
	family, err := requireFamily(req, s)
	if err != nil {
		return nil, "", nil, err
	}
	hints, err := hintsOf(req, s)
	if err != nil {
		return nil, "", nil, err
	}
	c, err := s.closure(family, hints)
	if err != nil {
		return nil, "", nil, err
	}
	if c.over {
		return nil, "", nil, refused(c.message())
	}
	if len(c.need) > 0 {
		return nil, "", nil, refused("閉包の親子チケットをまだ取得していません：" + strings.Join(c.need, ", "))
	}
	if why := firstOf(s.rivalProblem(family, c.ident(family)), c.ambiguousProblem(), s.unreadable(c)); why != "" {
		return nil, "", nil, refused(why)
	}
	if err := s.build(root, c.families); err != nil {
		return nil, "", nil, err
	}
	return s, family, c, nil
}

// stamp は要求の時刻（fsio.Stamp と同じ、オフセット付き）。読めなければ断る。
func stamp(req *pyjson.Object) (string, error) {
	value, ok := text(req, "stamp")
	if !ok || value == "" {
		return "", refused("stamp（オフセット付きの時刻）がありません")
	}
	restore, err := fsio.FixClock(value)
	restore()
	if err != nil {
		return "", refused(err.Error())
	}
	return value, nil
}

// actor は要求の actor（ホストのアカウント）。経路は Chrome。
func actor(req *pyjson.Object) core.Actor {
	account := ""
	if a, ok := req.Value("actor").(*pyjson.Object); ok {
		if v := a.Value("account"); pyfmt.Truthy(v) {
			account = pyfmt.Str(v)
		}
	}
	return core.Actor{Account: account, Via: via.Chrome}
}

// changesJSON は書くものを JSON にする。中身は UTF-8 の本文、読めなければ base64。
func changesJSON(root string, c *core.Changes) *pyjson.Object {
	if c == nil {
		return pyjson.NewObject("changes", nil, "lines", []string{}, "stopped", nil)
	}
	out := pyjson.NewObject()
	for _, b := range c.PerBranch() {
		rows := []any{}
		for _, ch := range b.Changes {
			row := pyjson.NewObject("op", ch.Op, "path", ch.Path)
			if ch.Op != core.OpDelete {
				if utf8.Valid(ch.Content) {
					row.Set("content", string(ch.Content))
				} else {
					row.Set("base64", base64.StdEncoding.EncodeToString(ch.Content))
				}
			}
			rows = append(rows, row)
		}
		out.Set(b.Branch, rows)
	}
	var stopped any
	if s := c.Planned.Stopped; s != nil {
		stopped = pyjson.NewObject("ticket", s.Ticket, "reason", relative(root, s.Reason))
	}
	return pyjson.NewObject("changes", out, "lines", relativeAll(root, c.Lines()), "stopped", stopped)
}

// merged は答えの頭に body の欄を足す（Python の {**a, **b}）。
func merged(head *pyjson.Object, rest ...*pyjson.Object) *pyjson.Object {
	for _, r := range rest {
		for _, k := range r.Keys() {
			head.Set(k, r.Value(k))
		}
	}
	return head
}

// oneBranch は書くものが親のブランチ P の中だけか（1 回の承認は 1 つの P）。提案が P 以外のブランチ（閉包の P_X
// など）にあると、消す先が別のブランチになる。その形は受けない。
func oneBranch(body *pyjson.Object, family string) error {
	changes, _ := body.Value("changes").(*pyjson.Object)
	if changes == nil {
		return nil
	}
	var others []string
	for _, k := range changes.Keys() {
		if k != family {
			others = append(others, k)
		}
	}
	if len(others) == 0 {
		return nil
	}
	slices.Sort(others)
	return refusedf("反映する内容が親ブランチ %s の外（%s）に及んでいます。提案を %s の上に置いてから、承認を依頼してください。",
		family, strings.Join(others, ", "), family)
}

// opPlan は親子のチケット 1 つの承認で書くもの。書かない。
func opPlan(req *pyjson.Object, root string) (*pyjson.Object, error) {
	s, family, _, err := familyTree(req, root)
	if err != nil {
		return nil, err
	}
	var only []string
	if v := req.Value("only"); v != nil {
		var ok bool
		if only, ok = stringList(v); !ok {
			return nil, refused("only は識別子のリスト")
		}
	}
	var seen *core.Shown
	if v := req.Value("shown"); v != nil {
		// 見せた一覧とダイジェスト。違えば書くものを出さず、preview からやり直させる。
		obj, _ := v.(*pyjson.Object)
		var idList []string
		var digest string
		ok := obj != nil
		if ok {
			idList, ok = stringList(obj.Value("ids"))
			digest, _ = obj.Value("digest").(string)
		}
		if !ok || digest == "" {
			return nil, refused("shown は見せた識別子のリスト（ids）とダイジェスト（digest）")
		}
		seen = &core.Shown{IDs: idList, Digest: digest}
	}
	if why := writeRefusal(s, family); why != "" {
		return nil, refused(why)
	}
	when, _ := stamp(req)
	who := actor(req)
	conf, _ := config.Load(root)
	restore := via.Session(who.Via, who.Account)
	judged := core.JudgeApproval(conf, root, only, seen)
	var changes *core.Changes
	if judged.Ready() {
		changes, err = core.Plan(conf, root, judged, when)
	}
	restore()
	if err != nil {
		return nil, err
	}
	body := changesJSON(root, changes)
	if err := oneBranch(body, family); err != nil {
		return nil, err
	}
	var code any
	if changes == nil && judged.Code != "" {
		code = judged.Code
	}
	rejected := []any{}
	for _, r := range judged.Gathered.Rejected {
		problems := make([]string, len(r.Problems))
		for i, p := range r.Problems {
			problems[i] = relative(root, p.String())
		}
		rejected = append(rejected, pyjson.NewObject("ticket", r.Ticket.ID, "problems", problems))
	}
	var mismatch any
	if judged.Mismatch != nil {
		mismatch = judged.Mismatch
	}
	return merged(pyjson.NewObject(
		"family", family,
		"identifiers", judged.Gathered.Identifiers(),
		"text", judged.Text,
		"digest", judged.Digest,
		"mismatch", mismatch,
		// 通らなかった根拠の種別（CORE_APPROVE_*、手元の `--agree --yes` と同じ）。通れば null。
		"code", code,
		"refused", relative(root, judged.Gathered.Refused),
		"rejected", rejected,
	), body), nil
}

// reviewInput は confirm と decide が共に読む、レビューの入力。
type reviewInput struct {
	s       *snapshot
	family  string
	ident   string
	number  int
	result  host.Result
	head    string
	compare any
}

func readReview(req *pyjson.Object, root string) (*reviewInput, error) {
	s, family, c, err := familyTree(req, root)
	if err != nil {
		return nil, err
	}
	if why := writeRefusal(s, family); why != "" {
		return nil, refused(why)
	}
	number, ok := integer(req.Value("phase"))
	if !ok {
		return nil, refused("phase はフェーズの番号")
	}
	result := host.ResultOf(req.Value("result"))
	if result.Error != "" {
		return nil, refused("result を解釈できません：" + result.Error)
	}
	return &reviewInput{s: s, family: family, ident: c.ident(family), number: number, result: result,
		head: s.branch(family).head, compare: req.Value("compare")}, nil
}

// hostView は、ホストで読んだ親の先頭と変更の一覧から決めた、依頼の後に動いたかと手で動かした子。依頼の後に親の
// ブランチが動いていて変更の一覧が要るなら needCompare、手で動かした子の移動の時刻が要るなら needManual を返し、
// 拡張が読んでから呼び直す（閉包の need と同じ形）。
type hostView struct {
	needCompare *pyjson.Object
	needManual  *pyjson.Object
	moved       request.Moved
	manual      *phase.Manual
}

func (in *reviewInput) view(req *pyjson.Object, conf *config.Settings, root string) (hostView, error) {
	recorded, has := core.RequestedHead(conf, root, in.ident, in.number)
	if in.compare == nil && has && recorded != "" && recorded != in.head && request.IsSHA(recorded) {
		return hostView{needCompare: pyjson.NewObject("base", recorded, "head", in.head)}, nil
	}
	files, listed, err := compareFiles(in.compare, recorded, has, in.head)
	if err != nil {
		return hostView{}, err
	}
	moved := core.MovedDetailOnHost(conf, root, in.ident, in.number, in.head, files, listed)
	var at *float64
	switch v := req.Value("manual_moved_at").(type) {
	case int:
		f := float64(v)
		at = &f
	case float64:
		at = &v
	case *big.Int:
		f, _ := new(big.Float).SetInt(v).Float64()
		at = &f
	}
	manual := core.ManualFor(conf, root, in.ident, in.number, at)
	if manual != nil && manual.NeedTime {
		marker := "/" + strings.Trim(conf.Approved, "/") + "/"
		paths := []string{}
		for _, p := range manual.Paths {
			if i := strings.Index(p, marker); i >= 0 {
				paths = append(paths, p[i+1:])
			}
		}
		return hostView{needManual: pyjson.NewObject("paths", paths)}, nil
	}
	return hostView{moved: moved, manual: manual}, nil
}

// compareFiles は compare API の変更の一覧（{base, head, files}）。比べた 2 つがマーカーの先頭と読んだ P の先頭で
// なければ使わない。files が null（一覧が打ち切られた・依頼時のコミットが祖先でない・ホストに無い）も使わない。
// 使わないときは listed が偽（動いたと数える）。
func compareFiles(compare any, base string, hasBase bool, headSHA string) ([]string, bool, error) {
	if compare == nil {
		return nil, false, nil
	}
	obj, ok := compare.(*pyjson.Object)
	if !ok {
		return nil, false, refused("compare は {base, head, files}")
	}
	raw := obj.Value("files")
	files, ok := stringList(raw)
	if raw != nil && !ok {
		return nil, false, refused("compare の files はパスのリストか null")
	}
	gotBase, baseOK := obj.Value("base").(string)
	gotHead, headOK := obj.Value("head").(string)
	if raw == nil || !headOK || gotHead != headSHA || hasBase != baseOK || baseOK && gotBase != base {
		return nil, false, nil
	}
	return files, true, nil
}

func pending(family string, extra ...*pyjson.Object) *pyjson.Object {
	out := pyjson.NewObject("family", family)
	return merged(out, extra...)
}

// opConfirm はレビュー済みで書くもの。書かない。result は ccnavi-review.sh が組むのと同じ形の取得した結果
// （{host, mr, threads, reviews}）。依頼の後にユーザが見るものが動いたかは、手元の confirm と同じ関数で決める。
func opConfirm(req *pyjson.Object, root string) (*pyjson.Object, error) {
	in, err := readReview(req, root)
	if err != nil {
		return nil, err
	}
	when, _ := stamp(req)
	conf, _ := config.Load(root)
	v, err := in.view(req, conf, root)
	if err != nil {
		return nil, err
	}
	empty := pyjson.NewObject("problems", []string{}, "code", nil)
	if v.needCompare != nil {
		return pending(in.family, pyjson.NewObject("need_compare", v.needCompare), empty, changesJSON(root, nil)), nil
	}
	if v.needManual != nil {
		return pending(in.family, pyjson.NewObject("need_manual", v.needManual), empty, changesJSON(root, nil)), nil
	}
	checked, err := core.Confirm(conf, root, actor(req), when, in.ident, in.number, in.result, v.moved.Text, v.manual, v.moved)
	if err != nil {
		return nil, err
	}
	body := changesJSON(root, checked.Changes)
	if err := oneBranch(body, in.family); err != nil {
		return nil, err
	}
	return merged(pyjson.NewObject(
		"family", in.family,
		"problems", relativeAll(root, checked.Problems),
		// 通らなかった根拠の種別（CORE_*。文面の頭の `[<コード>]` と同じ）。通れば null。
		"code", nullIfEmpty(checked.Code),
	), body), nil
}

func nullIfEmpty(s string) any {
	if s == "" {
		return nil
	}
	return s
}

// opDecide は残った指摘の対応方針（keep・fix・issue）を決める。書かない。VS Code のボードの decide と同じ判断を、
// Chrome が直接書く形で行う。ホストへの書き込み（issue の作成、スレッドへの返信、解決、MR へのコメント）は計画
// （host_plan）として返し、拡張が行う。親のブランチへの書き込みは changes（confirm と同じ形）で返し、拡張が
// 1 コミットにする。書く順は issues、replies、resolves、changes のコミット、comment。
//
// 要求は confirm と同じものに、mode（preview・apply）、choices と shown_digest（apply のとき必須）、carry（ホストで
// 受け入れ済みと拡張が確かめたキー）を足す。答えは mode の側のキー（preview か host_plan）をどの答えにも付ける。
func opDecide(req *pyjson.Object, root string) (*pyjson.Object, error) {
	mode, _ := text(req, "mode")
	if mode != "preview" && mode != "apply" {
		return nil, refused("mode は preview か apply")
	}
	choices, _ := req.Value("choices").(*pyjson.Object)
	if mode == "apply" && choices == nil {
		return nil, refused("choices は {指摘のキー: keep | fix | issue}")
	}
	shownDigest, hasDigest := text(req, "shown_digest")
	if mode == "apply" && !hasDigest {
		return nil, refused("shown_digest は見せた指摘のダイジェスト（文字列）")
	}
	carry := []string{}
	if v, given := req.Get("carry"); given {
		var ok bool
		if carry, ok = stringList(v); !ok {
			return nil, refused("carry は指摘のキーのリスト")
		}
	}
	in, err := readReview(req, root)
	if err != nil {
		return nil, err
	}
	when, _ := stamp(req)
	idle := pyjson.NewObject("reviewed", false, "followup", "", "pending_extra", []string{}, "mismatch", nil)
	if mode == "preview" {
		idle.Set("preview", nil)
	} else {
		idle.Set("host_plan", nil)
	}
	conf, _ := config.Load(root)
	v, err := in.view(req, conf, root)
	if err != nil {
		return nil, err
	}
	empty := pyjson.NewObject("problems", []string{}, "code", nil)
	if v.needCompare != nil {
		return pending(in.family, pyjson.NewObject("need_compare", v.needCompare), empty, idle, changesJSON(root, nil)), nil
	}
	if v.needManual != nil {
		return pending(in.family, pyjson.NewObject("need_manual", v.needManual), empty, idle, changesJSON(root, nil)), nil
	}
	if mode == "preview" {
		shown := core.DecidePreviewOf(conf, root, in.ident, in.number, in.result, v.moved.Text, v.moved, carry)
		var preview any
		if shown.Preview != nil {
			preview = shown.Preview
		}
		return merged(pyjson.NewObject("family", in.family, "problems", relativeAll(root, shown.Problems), "code", nullIfEmpty(shown.Code)),
			idle, pyjson.NewObject("preview", preview), changesJSON(root, nil)), nil
	}
	decided, err := core.Decide(conf, root, actor(req), when, in.ident, in.number, in.result, v.moved.Text, v.moved, choices, shownDigest, carry)
	if err != nil {
		return nil, err
	}
	body := changesJSON(root, decided.Changes)
	if err := oneBranch(body, in.family); err != nil {
		return nil, err
	}
	var plan, mismatch any
	if decided.HostPlan != nil {
		plan = decided.HostPlan
	}
	if decided.Mismatch != nil {
		mismatch = decided.Mismatch
	}
	return merged(pyjson.NewObject(
		"family", in.family,
		"problems", relativeAll(root, decided.Problems),
		"code", nullIfEmpty(decided.Code),
		"host_plan", plan,
		"reviewed", decided.Reviewed,
		"followup", decided.Followup,
		"pending_extra", list(decided.PendingExtra),
		"mismatch", mismatch,
	), body), nil
}

// compatState は同梱の互換の版と、統合先の CCNAVI_COMPAT の比べ。互換のマーカーはワークスペースの統合先にある
// （プロジェクトのリポジトリはワークスペースのものを読む）。
type compatState struct {
	repository *int
	same       bool
	message    string
}

func compat(s *snapshot) compatState {
	ours := version.Compat
	var theirs *int
	if text, ok := s.workspaceFiles().text(config.CompatFile); ok && text != "" {
		if n, found := config.DeclaredCompat(text); found {
			theirs = &n
		}
	}
	switch {
	case theirs == nil:
		return compatState{message: fmt.Sprintf("統合先の %s に互換の版（CCNAVI_COMPAT）が記載されていません。拡張は互換 %d です", config.CompatFile, ours)}
	case *theirs == ours:
		return compatState{repository: theirs, same: true}
	}
	hint := "リポジトリの ccnavi の更新をお待ちください"
	if *theirs > ours {
		hint = "拡張を更新してください"
	}
	return compatState{repository: theirs, message: fmt.Sprintf("拡張は互換 %d、リポジトリは互換 %d です。%s", ours, *theirs, hint)}
}

func opCompat(req *pyjson.Object, _ string) (*pyjson.Object, error) {
	s, err := readSnapshot(req)
	if err != nil {
		return nil, err
	}
	c := compat(s)
	return pyjson.NewObject("compat", pyjson.NewObject(
		"extension", version.Compat, "repository", orNull(c.repository), "same", c.same, "message", c.message,
	)), nil
}

// closedMatch は識別子が統合先の done/ でクローズ済みか（大文字小文字を区別しない）。
func closedMatch(s *snapshot, folded string) bool {
	for _, id := range s.closed() {
		if pyfmt.Casefold(id) == folded {
			return true
		}
	}
	return false
}

func closedProblem(ident, integ string) string {
	return ident + " は統合先 " + integ + " の done/ でクローズ済みです（クローズ済みの識別子は再利用できません）"
}

// opStart は issue から始める親のブランチの名前と、始められない理由。名前は issue の番号とタイトルから
// ids.IssueIdentifier が決める（先頭の語は prefix、無ければ feature）。ブランチは作らない（拡張の service worker が
// 作る）。入力は統合先（プロジェクトならワークスペースの統合先も）と、拡張が見たブランチ（snapshot と taken）。
func opStart(req *pyjson.Object, _ string) (*pyjson.Object, error) {
	s, err := readSnapshot(req)
	if err != nil {
		return nil, err
	}
	number, ok := integer(req.Value("issue"))
	if !ok || number <= 0 {
		return nil, refused("issue は正の整数（issue の番号）")
	}
	taken, ok := orEmpty(req.Value("taken"))
	if !ok {
		return nil, refused("taken はブランチ名のリスト")
	}
	title := ""
	if v := req.Value("title"); pyfmt.Truthy(v) {
		t, ok := v.(string)
		if !ok || utf8.RuneCountInString(t) > maxTitle {
			return nil, refused("title は issue のタイトル（1000 文字まで）")
		}
		title = t
	}
	prefix := ids.DefaultIssuePrefix
	if v := req.Value("prefix"); pyfmt.Truthy(v) {
		p, ok := v.(string)
		if !ok || !config.IsBranchPrefix(p) {
			return nil, refusedf("prefix は先頭の語（英小文字と数字）: %s", pyfmt.Repr(v))
		}
		prefix = p
	}
	ident, _, err := ids.IssueIdentifier(number, title, s.project, prefix)
	if err != nil {
		return nil, refused(err.Error())
	}
	integ := s.integration
	folded := pyfmt.Casefold(ident)
	t := &ticket.Ticket{ID: ident, Issue: number, Project: s.project}
	problems := list(t.BranchNameProblems(integ, append([]string{prefix}, config.DefaultBranchPrefixes...)))
	if closedMatch(s, folded) {
		problems = append(problems, closedProblem(ident, integ))
	}
	var same []string
	for _, n := range append(slices.Clone(s.order), taken...) {
		if n != integ && pyfmt.Casefold(n) == folded && !slices.Contains(same, n) {
			same = append(same, n)
		}
	}
	if len(same) > 0 {
		slices.Sort(same)
		problems = append(problems, "同じ名前のブランチが既にあります（"+strings.Join(same, ", ")+"）")
	}
	for _, name := range slices.Sorted(slices.Values(s.order)) {
		if name == integ {
			continue
		}
		var hits []string
		for _, f := range s.ticketsIn(s.branch(name), nil) {
			if (pyfmt.Casefold(f.t.ID) == folded || pyfmt.Casefold(familyOf(f.t.ID)) == folded) && !slices.Contains(hits, f.t.ID) {
				hits = append(hits, f.t.ID)
			}
		}
		if len(hits) > 0 {
			slices.Sort(hits)
			problems = append(problems, "未クローズの親子チケット "+name+" に同じ識別子があります（"+strings.Join(hits, ", ")+"）")
		}
	}
	if c := compat(s); !c.same {
		problems = append(problems, c.message)
	}
	if len(problems) > 0 {
		problems = append(problems, "この Issue からは開始できません。識別子を手動で指定して（フォールバック）開始してください。")
	}
	return pyjson.NewObject("identifier", ident, "integration", integ, "problems", problems), nil
}

// opAdopt は既にある MR の元ブランチを親として採用してよいか。ブランチ名が保護された名前や使えない書き方でないこと、
// 識別子が使えること、同じ識別子になる別のブランチが無いこと（names は origin の全ブランチの名前）、識別子が
// 統合先の done/ でクローズ済みでないこと、互換の版が合うことを見る。MR が open か・フォークか・マージ先は、
// ホストに聞く拡張の側で確かめる。
func opAdopt(req *pyjson.Object, _ string) (*pyjson.Object, error) {
	s, err := readSnapshot(req)
	if err != nil {
		return nil, err
	}
	name, _ := text(req, "branch")
	if name == "" {
		return nil, refused("branch は MR の元ブランチの名前")
	}
	names, ok := orEmpty(req.Value("names"))
	if !ok {
		return nil, refused("names はブランチ名のリスト")
	}
	integ := s.integration
	ident := ids.BranchToID(name)
	problems := []string{}
	if pyfmt.Casefold(name) == pyfmt.Casefold(integ) {
		problems = append(problems, "ブランチ "+name+" は統合先の名前です")
	}
	if why := ids.BranchProblem(name); why != "" {
		problems = append(problems, "ブランチ "+name+" は採用できません（"+why+"）")
	}
	if why := ids.AdoptionIDProblem(name); why != "" {
		problems = append(problems, "識別子 "+ident+" は指定できません（"+why+"）")
	}
	folded := pyfmt.Casefold(ident)
	var clash []string
	for _, n := range names {
		if n != name && pyfmt.Casefold(ids.BranchToID(n)) == folded && !slices.Contains(clash, n) {
			clash = append(clash, n)
		}
	}
	if len(clash) > 0 {
		slices.Sort(clash)
		problems = append(problems, "ブランチ "+name+" と "+strings.Join(clash, ", ")+" は、どちらも識別子 "+ident+" になります")
	}
	if closedMatch(s, folded) {
		problems = append(problems, closedProblem(ident, integ))
	}
	if c := compat(s); !c.same {
		problems = append(problems, c.message)
	}
	return pyjson.NewObject("identifier", ident, "integration", integ, "problems", problems), nil
}

func opVersion(*pyjson.Object, string) (*pyjson.Object, error) {
	return pyjson.NewObject("version", version.Version, "compat", version.Compat, "go", runtime.Version()), nil
}
