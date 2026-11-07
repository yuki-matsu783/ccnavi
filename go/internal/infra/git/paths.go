package git

import "strings"

// Listing は ls-files が挙げる範囲。
type Listing int

const (
	// ListTracked は追跡しているもの（`ls-files`）。
	ListTracked Listing = iota
	// ListUntracked は無視されていない未追跡のもの（`--others --exclude-standard`）。
	ListUntracked
	// ListAll は両方（`--cached --others --exclude-standard`）。
	ListAll
)

var listingArgs = map[Listing][]string{
	ListTracked:   {"ls-files", "-z"},
	ListUntracked: {"ls-files", "-z", "--others", "--exclude-standard"},
	ListAll:       {"ls-files", "-z", "--cached", "--others", "--exclude-standard"},
}

// Listed は top の下で git が挙げるパス（top からの相対、"/" 区切り、git の並び）。読めたかは done.OK()。
// pathspec は渡さない（呼び手が選ぶ）。利用者の環境の GIT_LITERAL_PATHSPECS などで読み方が変わらないように。
func Listed(top string, l Listing, o Options) ([]string, Done) {
	o.PlainPathspecs = true
	done := Run(top, listingArgs[l], o)
	if !done.OK() {
		return nil, done
	}
	return splitZ(done.Out), done
}

// CheckPaths は check-ignore に渡すパスの書き方。
type CheckPaths int

const (
	// AsWritten はそのまま渡す。`:` で始まる名前は magic と読まれ、git が 128 で止まる（問い合わせの失敗）。
	AsWritten CheckPaths = iota
	// DotSlashed は頭に `./` を付けて magic と読ませない（check-ignore は --literal-pathspecs を受けない）。
	// 返すパスからは `./` を外す。
	DotSlashed
)

// Ignored は paths（top からの相対）のうち git が無視するもの（`check-ignore -z --stdin`。git の並び）。
// ok が偽なら問い合わせに失敗した（理由は done）。どれも無視されないときの 1 は失敗にしない。
func Ignored(top string, paths []string, how CheckPaths, o Options) (found []string, done Done, ok bool) {
	if len(paths) == 0 {
		return nil, Done{}, true
	}
	var input strings.Builder
	for _, p := range paths {
		if how == DotSlashed {
			input.WriteString("./")
		}
		input.WriteString(p + "\x00")
	}
	stdin := input.String()
	o.Input = &stdin
	o.PlainPathspecs = true
	done = Run(top, []string{"check-ignore", "-z", "--stdin"}, o)
	// 0 は 1 つ以上が無視、1 はどれも無視されない。ほかは問い合わせの失敗。
	if done.Failure != nil || (done.Code != 0 && done.Code != 1) {
		return nil, done, false
	}
	found = splitZ(done.Out)
	if how == DotSlashed {
		for i, p := range found {
			found[i] = strings.TrimPrefix(p, "./")
		}
	}
	return found, done, true
}

// splitZ は -z の出力の空でない要素。
func splitZ(out string) []string {
	var found []string
	for _, p := range strings.Split(out, "\x00") {
		if p != "" {
			found = append(found, p)
		}
	}
	return found
}
