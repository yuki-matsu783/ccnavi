package decide

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"strconv"
	"strings"

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

// Choices は行き先の並び（文面と JSON の順）。
var Choices = []Choice{Keep, Fix, Issue}

// Version はボードと取り交わす JSON の版。形を変えたら上げる（拡張の decidemodel.ts と揃える）。版 2 で
// ダイジェストを指摘ごとの版にした（見せたあとに指摘が追加されただけなら適用できる）。
const Version = 2

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
	Base     string
	Keys     []string
	Versions map[string]string
}

// baseDigest は指摘の一覧以外の、見せた材料のダイジェスト。部分ごとの SHA-256 を件数と一緒に並べ、その全体の
// SHA-256（区切りでつなぐと、本文に区切りを書いてつなぎ目をずらせる）。
func baseDigest(d *Decision) string {
	parts := []string{d.Parent.ID, strconv.Itoa(d.Phase.Number), strconv.Itoa(d.Result.MR.Number), d.Result.MR.URL}
	lines := []string{strconv.Itoa(len(parts))}
	for _, p := range parts {
		lines = append(lines, sha256Hex(p))
	}
	return sha256Hex(strings.Join(lines, "\n"))
}

func sha256Hex(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:])
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
	raw, ok := urlsafeB64Decode(encoded)
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
	shown := Shown{Base: base, Versions: map[string]string{}}
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
	if shown.Base != baseDigest(d) {
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
		"version", Version, "parent", d.Parent.ID, "phase", d.Phase.Number,
		"mr", pyjson.NewObject("number", d.Result.MR.Number, "url", d.Result.MR.URL),
		"threads", threads, "digest", Digest(d),
	)
}

// urlsafeB64Decode は Python の base64.urlsafe_b64decode(s + パディング)（検めない形の a2b_base64）。表に無い
// 字は読み飛ばし、パディングがそろえばそこで終わり、半端な桁は読めないとする。
func urlsafeB64Decode(s string) ([]byte, bool) {
	for i := 0; i < len(s); i++ {
		if s[i] >= 0x80 {
			return nil, false // Python は ASCII でない文字列を読まない（ValueError）
		}
	}
	s = strings.NewReplacer("-", "+", "_", "/").Replace(s)
	s += strings.Repeat("=", (4-len(s)%4)%4)
	var out []byte
	quad, pads := 0, 0
	var left byte
	for i := 0; i < len(s); i++ {
		c := s[i]
		if c == '=' {
			pads++
			if quad >= 2 && quad+pads >= 4 {
				return out, true
			}
			continue
		}
		v, ok := b64Value(c)
		if !ok {
			continue
		}
		pads = 0
		switch quad {
		case 0:
			quad, left = 1, v
		case 1:
			quad = 2
			out = append(out, left<<2|v>>4)
			left = v & 0x0f
		case 2:
			quad = 3
			out = append(out, left<<4|v>>2)
			left = v & 0x03
		case 3:
			quad = 0
			out = append(out, left<<6|v)
			left = 0
		}
	}
	return out, quad == 0
}

func b64Value(c byte) (byte, bool) {
	switch {
	case c >= 'A' && c <= 'Z':
		return c - 'A', true
	case c >= 'a' && c <= 'z':
		return c - 'a' + 26, true
	case c >= '0' && c <= '9':
		return c - '0' + 52, true
	case c == '+':
		return 62, true
	case c == '/':
		return 63, true
	}
	return 0, false
}
