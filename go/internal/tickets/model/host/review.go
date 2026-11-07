package host

import (
	"strings"
	"time"

	"ccnavi/internal/infra/digest"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

// レビューの状態。GitHub の CHANGES_REQUESTED と GitLab の requested_changes は ChangesRequested にまとまる。
const (
	ChangesRequested = "CHANGES_REQUESTED"
	dismissed        = "DISMISSED"
	approved         = "APPROVED"
)

// 変更要求の案内（ホストごと）。判定はホストを問わず同じで、案内だけ出し分ける。
const (
	changesGitLab = "変更を依頼（Request changes）が出ています。解除はレビュアーの承認（Approve）で行います。" +
		"作成者によるレビューの再依頼（Re-request review）で状態が戻るかは未検証です。" +
		"再依頼した場合も、ccnavi で状態を取り直して確かめます"
	changesGitHub = "変更要求（Changes requested）が出ています。" +
		"解除はレビュアーの Approve か、レビューの dismiss で行います"
)

// versionLen は指摘の版の桁数。
const versionLen = 32

// deciding は判断を示したレビューか。コメントだけ（COMMENTED）と書きかけ（PENDING）は判断を変えないので
// 数えない（数えると、変更要求の後に同じユーザがコメントするだけで変更要求が消える）。
func deciding(state string) bool {
	switch pyfmt.Upper(state) {
	case approved, ChangesRequested, dismissed:
		return true
	}
	return false
}

// Effective はレビュアーごとに、判断を示した最新の 1 件。取り下げられた（dismiss）ものは無い扱い。
// 変更要求は同じユーザの Approve か dismiss まで残る。
func Effective(reviews []Review) []Review {
	type key struct {
		name  string
		index int // 書き手も URL も無いレビューは 1 件ずつ別に数える
	}
	// レビュアーを、最初に出た順に並べる。同じレビュアーは時刻の新しい 1 件に置き換える（時刻が同じなら後のもの）。
	var order []key
	latest := map[key]Review{}
	for i, r := range reviews {
		if !deciding(r.State) {
			continue
		}
		k := key{name: r.author}
		if k.name == "" {
			k.name = r.URL
		}
		if k.name == "" {
			k.index = i + 1
		}
		current, seen := latest[k]
		if !seen {
			order = append(order, k)
		}
		if !seen || Epoch(r.submittedAt) >= Epoch(current.submittedAt) {
			latest[k] = r
		}
	}
	// 最後に、最新の判断が取り下げ（dismiss）のレビュアーを除く。取り下げの前の変更要求は、これで消える。
	var out []Review
	for _, k := range order {
		if r := latest[k]; pyfmt.Upper(r.State) != dismissed {
			out = append(out, r)
		}
	}
	return out
}

// ChangesRequestedNote は変更要求が出ているときの案内の 1 文。解除のしかたはホストで違う
// （GitLab は Approve だけ）。GitLab 以外（GitHub・見本の fixture など）は GitHub の案内。
func ChangesRequestedNote(host Kind) string {
	if host == GitLab {
		return changesGitLab
	}
	return changesGitHub
}

// ThreadKey は選択をスレッドに対応づけるキー。URL を先に使い、無ければスレッドの id。
func ThreadKey(t Thread) string {
	if t.URL != "" {
		return t.URL
	}
	return t.ID
}

// ThreadVersion は指摘の版。キーと、改行コードを LF にそろえて NFC にした最初のコメントの本文の SHA-256 の
// 先頭 32 桁。見せた指摘・適用の直前に取り直した指摘・受け入れ台帳の項目が同じかを、この版で照合する。
func ThreadVersion(t Thread) string {
	body := strings.ReplaceAll(strings.ReplaceAll(t.Body, "\r\n", "\n"), "\r", "\n")
	return digest.Hex(digest.SHA256, ThreadKey(t)+"\n"+pyfmt.NFC(body), versionLen)
}

// Unresolved はまだ解決されていない指摘。付いた時刻では絞らない（絞ると、子を 1 本足して依頼をやり直す
// だけで前回の指摘が数から消える）。数えないのは、機構自身の投稿（ccnaviPost）と、受け入れ台帳
// （`accepted.json` の項目）が受け入れ済みとする指摘だけ。poster は依頼を投稿したアカウント。
func Unresolved(threads []Thread, host Kind, poster string, ledger []*pyjson.Object) []Thread {
	var out []Thread
	for _, t := range threads {
		if IsUnresolved(t, host, poster, ledger) {
			out = append(out, t)
		}
	}
	return out
}

// IsUnresolved は Unresolved が数える 1 本か。結果の並びのまま、ほかの条件と合わせて選ぶ呼び手が使う。
func IsUnresolved(t Thread, host Kind, poster string, ledger []*pyjson.Object) bool {
	return !t.Resolved && !ccnaviPost(t, host, poster) && (len(ledger) == 0 || !acceptedByLedger(t, ledger))
}

// ccnaviPost は ccnavi 自身が投稿したスレッドか。GitLab の結果で、書き手が依頼を投稿したアカウントで、
// own が分かればそれ、分からなければ代表の note が目印で始まるとき。GitHub の依頼はスレッドにならないので、
// 目印で始まるスレッドもユーザが書いたものとして数える。投稿者が分からなければ見分けない
// （誰でも目印を書けるので、本文だけで除くと未解決を隠せる）。
func ccnaviPost(t Thread, host Kind, poster string) bool {
	mine := host == GitLab && poster != "" && t.author == poster
	if t.own != nil {
		return mine && *t.own
	}
	return mine && strings.HasPrefix(t.Body, markerPrefix)
}

// acceptedByLedger は、キーと版が台帳の項目と同じで、受け入れたときの最後のコメントから変わっていない
// 指摘か。キーか最後のコメントの id が取れていなければ再提起を見分けられないので、受け入れ済みにしない。
func acceptedByLedger(t Thread, ledger []*pyjson.Object) bool {
	key, version := ThreadKey(t), ThreadVersion(t)
	if key == "" || t.LastCommentID == "" {
		return false
	}
	for _, entry := range ledger {
		if entry.Value("key") != key || entry.Value("version") != version {
			continue
		}
		if text(entry.Value("last_comment_id")) == t.LastCommentID {
			return true
		}
	}
	return false
}

// Epoch はホストの時刻（ISO 8601）のエポック秒。読めなければ 0。時差の無い時刻は手元の時刻として読む。
func Epoch(stamp string) float64 {
	if stamp == "" {
		return 0
	}
	when, zoned, ok := pyfmt.FromISOFormat(strings.ReplaceAll(stamp, "Z", "+00:00"))
	if !ok {
		return 0
	}
	if !zoned {
		when = time.Date(when.Year(), when.Month(), when.Day(), when.Hour(), when.Minute(),
			when.Second(), when.Nanosecond(), time.Local)
	}
	return fsio.UnixSeconds(when)
}
