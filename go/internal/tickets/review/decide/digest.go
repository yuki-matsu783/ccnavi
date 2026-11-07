package decide

import (
	"encoding/base64"
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/digest"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/host"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase"
)

// Choice は残った指摘の行き先。ユーザが指摘ごとに選ぶ。値は JSON の選択にそのまま出る。
type Choice string

const (
	Keep  Choice = "keep"
	Fix   Choice = "fix"
	Issue Choice = "issue"
)

// choiceOrder は行き先の並び（文面と JSON の順）。
var choiceOrder = []Choice{Keep, Fix, Issue}

// Choices は choiceOrder の写しを返す（呼び手が書き換えても元は変わらない）。
func Choices() []Choice { return slices.Clone(choiceOrder) }

// version はボードと取り交わす JSON の版。形を変えたら上げる（拡張の decidemodel.ts と揃える）。版 2 で
// ダイジェストを指摘ごとの版にした（見せたあとに指摘が追加されただけなら適用できる）。
const version = 2

// digestPrefix はダイジェストの前置き。版 1 のダイジェスト（64 桁の 16 進）と見分ける。
const digestPrefix = "v2."

// target は決める材料のうち指摘の一覧以外（親・フェーズ・MR）の欄の名前。食い違いの一覧に出る。
const target = "target"

// Decision は残った指摘を決める前の、見せる材料。見せる（Preview）と適用が同じものから組む。Result の MR は
// nil でない。Extra は見せたあとに追加された未解決の指摘で、今回の判断の対象ではなく未対応として残す。
type Decision struct {
	Parent     *ticket.Ticket
	Phase      *phase.Phase
	Result     host.Result
	Unresolved []host.Thread
	Extra      []host.Thread
}

// Shown はダイジェストを読み戻したもの。Keys は見せた指摘のキー（ダイジェストの並び）、Versions はその版。
type Shown struct {
	base     string
	Keys     []string
	Versions map[string]string
}

// baseDigest は指摘の一覧以外の、見せた材料のダイジェスト。部分ごとの SHA-256 を件数と一緒に並べ、その全体の
// SHA-256（区切りでつなぐと、本文に区切りを書いてつなぎ目をずらせる）。
func baseDigest(d *Decision) string {
	parts := []string{d.Parent.ID, strconv.Itoa(d.Phase.Number), strconv.Itoa(d.Result.MR.Number), d.Result.MR.URL}
	lines := []string{strconv.Itoa(len(parts))}
	for _, p := range parts {
		lines = append(lines, digest.Hex(digest.SHA256, p, 0))
	}
	return digest.Hex(digest.SHA256, strings.Join(lines, "\n"), 0)
}

func versionsOf(threads []host.Thread) map[string]any {
	out := make(map[string]any, len(threads))
	for _, t := range threads {
		out[host.ThreadKey(t)] = host.ThreadVersion(t)
	}
	return out
}

// Digest は見せた指摘のダイジェスト。不透明な文字列として sh とボードを往復する。中身は基礎のダイジェストと
// 指摘ごとの版で、適用のときに取り直した指摘と照合する（並びは問わない）。
func Digest(d *Decision) string {
	body := pyjson.NewObject("b", baseDigest(d), "t", versionsOf(d.Unresolved))
	raw, err := pyjson.Dumps(body, pyjson.Options{SortKeys: true, Compact: true})
	if err != nil {
		return digestPrefix
	}
	return digestPrefix + base64.RawURLEncoding.EncodeToString([]byte(raw))
}

// ParseDigest はダイジェストを読み戻す。読めなければ偽（版 1 の値を含む）。
func ParseDigest(text string) (Shown, bool) {
	encoded, ok := strings.CutPrefix(text, digestPrefix)
	if !ok {
		return Shown{}, false
	}
	raw, ok := digest.URLSafeB64Decode(encoded)
	if !ok {
		return Shown{}, false
	}
	for i := 0; i < len(raw); i++ {
		if raw[i] >= 0x80 {
			return Shown{}, false
		}
	}
	value, err := pyjson.Loads(string(raw))
	if err != nil {
		return Shown{}, false
	}
	body, ok := value.(*pyjson.Object)
	if !ok {
		return Shown{}, false
	}
	base, ok := body.Value("b").(string)
	if !ok {
		return Shown{}, false
	}
	versions, ok := body.Value("t").(*pyjson.Object)
	if !ok {
		return Shown{}, false
	}
	shown := Shown{base: base, Versions: map[string]string{}}
	for _, key := range versions.Keys() {
		v, ok := versions.Value(key).(string)
		if !ok {
			return Shown{}, false
		}
		shown.Keys = append(shown.Keys, key)
		shown.Versions[key] = v
	}
	return shown, true
}

// ChangedSinceShown は見せた材料と今の材料の食い違い。基礎が違えば target、見せた指摘のうち無くなった・
// 解決済みになった・本文や位置が変わったものはそのキー。見せたあとに追加された指摘は数えない。
func ChangedSinceShown(d *Decision, shown Shown) []string {
	var out []string
	if shown.base != baseDigest(d) {
		out = append(out, target)
	}
	now := versionsOf(d.Unresolved)
	for _, key := range shown.Keys {
		if v, ok := now[key]; !ok || v != shown.Versions[key] {
			out = append(out, key)
		}
	}
	return out
}

// JSON は見せる材料（`--preview --json` の形。ボードの DecidePreview）。
func JSON(d *Decision) *pyjson.Object {
	threads := make([]any, 0, len(d.Unresolved))
	for _, t := range d.Unresolved {
		threads = append(threads, pyjson.NewObject(
			"key", host.ThreadKey(t), "url", t.URL, "path", t.Path, "line", t.Line, "body", t.Body,
			"version", host.ThreadVersion(t),
			"last_comment", pyjson.NewObject("id", t.LastCommentID, "at", t.LastCommentAt, "author", t.LastCommentAuthor),
		))
	}
	return pyjson.NewObject(
		"version", version, "parent", d.Parent.ID, "phase", d.Phase.Number,
		"mr", pyjson.NewObject("number", d.Result.MR.Number, "url", d.Result.MR.URL),
		"threads", threads, "digest", Digest(d),
	)
}
