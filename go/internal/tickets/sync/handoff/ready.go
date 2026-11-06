package handoff

import (
	"strings"
	"time"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/model/place"
)

// gitTimeout は起点の木を読む git の期限。
const gitTimeout = 30 * time.Second

// Pending は表に当たる下書きのうち、取り込み先と中身が違うもの（取り込んでいないもの）の、置き場つきの相対パス。
// ready は wip/ を消すので、残っていれば止める。読めない下書きと取り込み先も「違う」に数える（止める側）。
// 共通の置き場がミラーかどうかは見ない。
func Pending(treeRoot string) []string {
	found, _, refused := Walk(treeRoot)
	if refused != "" {
		return []string{place.Handoff + "/"}
	}
	var out []string
	for _, f := range found {
		raw, _ := flow.ReadBytes(f.Path, treeRoot)
		target, why := Spelled(treeRoot, f.Rel)
		var current []byte
		if why == "" && ospath.DiskLexists(target) {
			current, _ = flow.ReadBytes(target, treeRoot)
		}
		if raw == nil || why != "" || !same(current, raw) {
			out = append(out, place.Handoff+"/"+f.Drel)
		}
	}
	return out
}

// draftFiles は置き場の下にあるファイル（ディレクトリのリンクを含む）の、置き場からの相対パス。
func draftFiles(treeRoot string) []string {
	base := handoffDir(treeRoot)
	var out []string
	walkDir(base, func(dir string, files, dirs []string) {
		for _, n := range files {
			out = append(out, relSlash(ospath.Join(dir, n), base))
		}
		for _, n := range dirs {
			if full := ospath.Join(dir, n); ospath.IsLink(full) {
				out = append(out, relSlash(full, base))
			}
		}
	})
	return out
}

// holds は起点の木の sources のどれかの中身が raw と同じか。起点から読めなければ ok が偽。
func holds(treeRoot, start string, sources []string, raw []byte) (held, ok bool) {
	for _, source := range sources {
		blob, readable := git.Blob(treeRoot, start, source, git.Options{Timeout: gitTimeout})
		if !readable {
			return false, false
		}
		held = held || raw != nil && same(blob, raw)
	}
	return held, true
}

// Survey は置き場の下書きを、start の木の元のパスと突き合わせ、（元と違う写し, 元の無い下書き）を返す。
// ccnavi-git.sh integrate が、取り込み結果に含まれない下書きを見分けるのに使う。パスは置き場つきで置き場の表記のまま。
// 比べるのは Key（大文字小文字・NFC/NFD を区別しない）で、元を読むときは起点の木の表記を使う。
//
//   - 起点の木に同じ下書きがあり中身も同じ: ブランチが持ち込んだものではないので、どちらにも入れない
//   - 元が起点の木に無い: 元の無い下書き（新規のメモ・報告）
//   - 元があり中身が同じ: 編集していない写し。どちらにも入れない
//   - 元があり中身が違う、または読めない（リンクなど）: 元と違う写し
//
// 起点の木を読めなければ ok が偽（呼ぶ側は止める）。
func Survey(treeRoot, start string) (foreign, notes []string, ok bool) {
	if !ospath.IsDir(handoffDir(treeRoot)) {
		return nil, nil, true
	}
	listed := git.Run(treeRoot, []string{"ls-tree", "-r", "-z", "--name-only", start}, git.Options{Timeout: gitTimeout})
	if !listed.OK() {
		return nil, nil, false
	}
	origins := map[string][]string{}
	for _, n := range strings.Split(listed.Out, "\x00") {
		if n != "" {
			origins[Key(n)] = append(origins[Key(n)], n)
		}
	}
	base := handoffDir(treeRoot)
	for _, rel := range draftFiles(treeRoot) {
		shown := place.Handoff + "/" + rel
		raw, _ := flow.ReadBytes(ospath.Join(base, strings.Split(rel, "/")...), treeRoot)
		if own := origins[Key(shown)]; len(own) > 0 {
			if held, _ := holds(treeRoot, start, own, raw); held {
				continue
			}
		}
		sources := origins[Key(names.FromDraft(rel))]
		if len(sources) == 0 {
			notes = append(notes, shown)
		} else if held, _ := holds(treeRoot, start, sources, raw); !held {
			foreign = append(foreign, shown)
		}
	}
	return foreign, notes, true
}
