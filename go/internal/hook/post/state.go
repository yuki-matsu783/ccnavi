package post

import (
	"errors"
	"fmt"
	"io/fs"
	"slices"
	"sort"

	"ccnavi/internal/infra/digest"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/useronly"
)

// state に置く記録の名前（`<セッション><接尾辞>`）。records/prune が同じ名前を写して持つ（post_test が突き合わせる）。
const (
	// reportedSuffix は報告済みの変更の記録。キーの集合で、切らずに増やす。
	reportedSuffix = ".reported" + fsio.JSONExt
	// acceptedSuffix はユーザの発言で受け入れた変更の、受け入れたときの指紋。キーごとに持ち、発言のたびに直す。
	acceptedSuffix = ".accepted" + fsio.JSONExt
	// turnSuffix はターンの基準。セッションの記録とは寿命が違う（ターンごとに取り直す）ので分ける。
	turnSuffix     = ".turn" + fsio.JSONExt
	unknownSession = "unknown"

	// baselineLimit はターンの基準を切る件数。報告済みの記録は切らない（切った先の汚れが次の呼び出しで新しい変更になり、
	// 戻されてしまう）。
	baselineLimit = 500
	// sidePrefix は横で作業するチェックアウトの変更を報告したときの指紋のキーの頭。受け入れた変更と同じファイルに置き、
	// 頭で取り違えない。
	sidePrefix = "side:"
	// missingFingerprint は消えているか読めないファイルの指紋。
	missingFingerprint = "-"

	keyReported = "reported"
	keyCaptured = "baseline_captured"
	keyAnnounce = "announced"
	keyBaseline = "baseline"
	keyHeads    = "heads"
)

func statePath(dir, session, suffix string) string {
	return ospath.Join(dir, fsio.SafeNameOr(session, unknownSession, fsio.SafeNameLimit)+suffix)
}

// stringSet は JSON の列のうち文字列だけ。
func stringSet(v any) map[string]bool {
	out := map[string]bool{}
	items, _ := v.([]any)
	for _, item := range items {
		if s, ok := item.(string); ok {
			out[s] = true
		}
	}
	return out
}

func sorted(set map[string]bool) []string {
	out := make([]string, 0, len(set))
	for k := range set {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// readError は記録を読めなかった理由。無いのは普通の状態なので空。
func readError(err error) string {
	if err == nil || errors.Is(err, fs.ErrNotExist) {
		return ""
	}
	return err.Error()
}

// reported は報告済みの記録。baseline は起動時（か初回）の基準を持つか、announced は起動時の変更をもう伝えたか。
type reported struct {
	keys      map[string]bool
	baseline  bool
	announced bool
}

// loadReported は報告済みの記録を読む。読めなければ「初めて」として扱う（記録を失ったときに、前から在った変更を
// 直前の実行のせいにするより、もう一度取り直すほうが害が小さい）。
func (c *Checker) loadReported(session string) reported {
	none := reported{keys: map[string]bool{}, announced: true}
	if c.State == "" {
		return none
	}
	data, err := fsio.ReadJSON(statePath(c.State, session, reportedSuffix))
	if err != nil {
		if why := readError(err); why != "" {
			fmt.Fprintf(c.Stderr, "ccnavi: 実行後チェックの記録を読めない: %s\n", why)
		}
		return none
	}
	obj, ok := data.(*pyjson.Object)
	if !ok {
		return none
	}
	if _, ok := obj.Value(keyReported).([]any); !ok {
		return none
	}
	if captured, _ := obj.Value(keyCaptured).(bool); !captured {
		return none
	}
	said, isBool := obj.Value(keyAnnounce).(bool)
	return reported{keys: stringSet(obj.Value(keyReported)), baseline: true, announced: !isBool || said}
}

// saveReported は記録を書く。書けなくてもチェックは止めない（同じ変更をもう一度報告するのは、うるさいが見落とすよりよい）。
func (c *Checker) saveReported(session string, keys map[string]bool, announced bool) {
	if c.State == "" {
		return
	}
	body := pyjson.NewObject(keyReported, sorted(keys), keyCaptured, true, keyAnnounce, announced)
	if failed := fsio.WriteJSON(statePath(c.State, session, reportedSuffix), body); failed != "" {
		fmt.Fprintf(c.Stderr, "ccnavi: 実行後チェックの記録を保存できませんでした: %s\n", failed)
	}
}

// loadAccepted は {キー: 指紋}。無い・読めないなら空（受け入れた変更が無いのと同じ）。
func (c *Checker) loadAccepted(session string) map[string]string {
	out := map[string]string{}
	if c.State == "" {
		return out
	}
	obj, _ := fsio.ReadObject(statePath(c.State, session, acceptedSuffix))
	if obj == nil {
		return out
	}
	for _, k := range obj.Keys() {
		if v, ok := obj.Value(k).(string); ok {
			out[k] = v
		}
	}
	return out
}

func (c *Checker) saveAccepted(session string, accepted map[string]string) {
	if c.State == "" {
		return
	}
	keys := make([]string, 0, len(accepted))
	for k := range accepted {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	body := pyjson.NewObject()
	for _, k := range keys {
		body.Set(k, accepted[k])
	}
	if failed := fsio.WriteJSON(statePath(c.State, session, acceptedSuffix), body); failed != "" {
		fmt.Fprintf(c.Stderr, "ccnavi: 受け入れた変更の記録を保存できませんでした: %s\n", failed)
	}
}

// heads はツリーごとの HEAD。記録の並び（書くときに名前順にそろえる）を保つ。
type heads struct {
	order []string
	sha   map[string]string
}

func (h *heads) put(top, sha string) {
	if _, ok := h.sha[top]; !ok {
		h.order = append(h.order, top)
	}
	h.sha[top] = sha
}

// turn はこのターンの基準。known が偽なら、ターンの始まりを見ていない（登録されていないか、そのとき読めなかった）。
type turn struct {
	baseline map[string]bool
	known    bool
	heads    heads
}

// loadTurn はこのターンの基準を読む。heads を持たない記録（壊れたもの）も、無いときと同じく基準なしとして扱う。
func (c *Checker) loadTurn(session string) turn {
	none := turn{baseline: map[string]bool{}, heads: heads{sha: map[string]string{}}}
	if c.State == "" {
		return none
	}
	data, err := fsio.ReadJSON(statePath(c.State, session, turnSuffix))
	if err != nil {
		if why := readError(err); why != "" {
			fmt.Fprintf(c.Stderr, "ccnavi: %sの基準を読めない: %s\n", useronly.TurnDefined, why)
		}
		return none
	}
	obj, ok := data.(*pyjson.Object)
	if !ok {
		return none
	}
	if _, ok := obj.Value(keyBaseline).([]any); !ok {
		return none
	}
	recorded, ok := obj.Value(keyHeads).(*pyjson.Object)
	if !ok {
		return none
	}
	out := turn{baseline: stringSet(obj.Value(keyBaseline)), known: true, heads: heads{sha: map[string]string{}}}
	for _, k := range recorded.Keys() {
		if v, ok := recorded.Value(k).(string); ok {
			out.heads.put(k, v)
		}
	}
	return out
}

func (c *Checker) saveTurn(session string, baseline map[string]bool, h heads) {
	if c.State == "" {
		return
	}
	keys := sorted(baseline)
	keys = keys[:min(len(keys), baselineLimit)]
	tops := slices.Clone(h.order)
	sort.Strings(tops)
	recorded := pyjson.NewObject()
	for _, top := range tops {
		recorded.Set(top, h.sha[top])
	}
	body := pyjson.NewObject(keyBaseline, keys, keyHeads, recorded)
	if failed := fsio.WriteJSON(statePath(c.State, session, turnSuffix), body); failed != "" {
		fmt.Fprintf(c.Stderr, "ccnavi: %sの基準を保存できませんでした: %s\n", useronly.TurnDefined, failed)
	}
}

// fingerprint は変わったファイルの中身の指紋。受け入れたあとに同じファイルが書き換えられたかを、キー（種類とパス）とは
// 別に見分ける。消えているか読めないときは missingFingerprint。
func fingerprint(f *finding) string {
	if sum, ok := fsio.DiskDigest(f.change.Full, digest.SHA256); ok {
		return sum
	}
	return missingFingerprint
}
