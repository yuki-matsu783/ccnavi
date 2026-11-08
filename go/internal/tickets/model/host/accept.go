package host

import (
	"fmt"
	"regexp"
	"strings"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

// 投稿に付ける目印。機構自身の投稿を、確認のときに除くため。
const (
	MarkerRequest    = "<!-- ccnavi:request "
	MarkerDecide     = "<!-- ccnavi:decide -->"
	MarkerReady      = "<!-- ccnavi:ready -->"
	MarkerCloseEarly = "<!-- ccnavi:close-early -->"
	// markerAccept は受け入れた指摘のスレッドに残す返信の目印。返信を残したあと、sh がスレッドを解決する。
	markerAccept = "<!-- ccnavi:accept -->"
	markerPrefix = "<!-- ccnavi:"
	// markerIssue は issue の本文に入れる隠しコメント。op は指摘ごとに決まる操作ID で、同じ印の issue を
	// 検索して作り直しで重複しないようにする。
	markerIssue = "<!-- ccnavi:issue op=%s -->"
)

// IssueMarker は操作ID op の issue に入れる目印。
func IssueMarker(op string) string { return fmt.Sprintf(markerIssue, op) }

// 受け入れの返信の文。
const (
	replyHead     = "ccnavi: %sので、この指摘は対応せず、受け入れて進める。"
	replyIssued   = "別の issue に回した: %s。このスレッドは解決済みにする。"
	replyToIssue  = "別の issue に回す（issue の下書きを置く）。このスレッドは解決済みにする。"
	replyResolve  = "このスレッドは解決済みにする。"
	msgNoThreadID = "スレッドの id が無いか読めず、返信と解決ができない（%s）"
	unknownURL    = "不明"
)

// 受け入れの計画。実行ファイルが書き、sh が読んで、返信と解決をホストに投稿する（実行ファイルはネットワークに
// 出ないので）。形は {"version": 1, "digest": <見せた指摘のダイジェスト。close-early は空>, "op": <操作ID>,
// "threads": [{"id", "key", "reply"}]}。sh は id と reply だけを読む。
const acceptPlanVersion = 1

// acceptID はスレッドの id に使える字。GitHub の node id（PRRT_kw…）と GitLab の discussion の id（16 進）の
// どちらも収まる。sh も同じ字でしか API の経路や graphQL の変数に使わない。
var acceptID = regexp.MustCompile(`\A[A-Za-z0-9_=-]+\z`)

// AcceptReply は受け入れたスレッドに残す返信の本文。why は受け入れる場面（1 行）。issueURL を渡すと、
// issue の下書きを置く旨の代わりにその URL を書く（URL は呼び手が後で埋める枠の文字列でもよい）。
func AcceptReply(why string, issue bool, issueURL string) string {
	lines := []string{markerAccept, fmt.Sprintf(replyHead, why)}
	switch {
	case issue && issueURL != "":
		lines = append(lines, fmt.Sprintf(replyIssued, issueURL))
	case issue:
		lines = append(lines, replyToIssue)
	default:
		lines = append(lines, replyResolve)
	}
	return strings.Join(lines, "\n") + "\n"
}

// AcceptPlanProblem は返信できない指摘があればその説明。スレッドの id が無い（または API の経路に
// 使えない字を含む）と、ホストに返信も解決もできない。
func AcceptPlanProblem(threads []Thread) string {
	for _, t := range threads {
		if !acceptID.MatchString(t.ID) {
			where := t.URL
			if where == "" {
				where = unknownURL
			}
			return fmt.Sprintf(msgNoThreadID, where)
		}
	}
	return ""
}

// AcceptItem は計画の 1 行（スレッドと返信の本文）。
type AcceptItem struct {
	Thread Thread
	Reply  string
}

// WriteAcceptPlan は計画を書く。書けなければ理由を返す。op は操作記録の操作ID（sh が送信の段階を記録する
// ときと、置く呼び出しが同じ記録を読むときのキー）。
func WriteAcceptPlan(path string, items []AcceptItem, digest, op string) string {
	threads := make([]any, 0, len(items))
	for _, it := range items {
		threads = append(threads, pyjson.NewObject("id", it.Thread.ID, "key", ThreadKey(it.Thread), "reply", it.Reply))
	}
	body := pyjson.NewObject("version", acceptPlanVersion, "digest", digest, "op", op, "threads", threads)
	return fsio.WriteJSONAs(path, body, fsio.JSONFormat{FinalNewline: true, Newline: fsio.LF})
}

// AcceptPlan は書いた計画を読み戻したもの。
type AcceptPlan struct {
	Digest string
	IDs    []string
	Keys   []string
	Op     string
}

// ReadAcceptPlan は書いた計画。読めなければ nil（計画の段を通っていない）。
func ReadAcceptPlan(path string) *AcceptPlan {
	data, ok := fsio.ReadObject(path)
	if !ok || data.Len() == 0 || !pyfmt.EqualsInt(data.Value("version"), acceptPlanVersion) {
		return nil
	}
	plan := &AcceptPlan{Digest: text(data.Value("digest")), Op: text(data.Value("op"))}
	for _, t := range objects(data.Value("threads")) {
		plan.IDs = append(plan.IDs, text(t.Value("id")))
		plan.Keys = append(plan.Keys, text(t.Value("key")))
	}
	return plan
}
