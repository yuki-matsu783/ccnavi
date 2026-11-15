package branch

import (
	"fmt"
	"io"
	"regexp"
	"strconv"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

// Pulls は `branches pulls <番号> <リポジトリのパス> <応答のファイル>`（ccnavi-branches.sh の GitHub の issue 用）。
// sh が取ってきた開いた PR の一覧（GitHub の応答のまま。ページを繋いだ配列）から、issue <番号> に関係する PR を選び、
// `--result` の mrs の形（number・branch・state・url・title・base・fork）の JSON の配列を標準出力に書く。
// 関係するのは、タイトルか本文に `#<番号>` か `issues/<番号>` を含む PR と、元ブランチ名が番号を含む PR（namesNumber）。
// 読めなければ 1（sh は API の失敗として扱う）。
func Pulls(stdout, stderr io.Writer, conf *config.Settings, number, repo, path string) int {
	if !numberText(number) {
		fmt.Fprintf(stderr, "ccnavi: branches pulls の番号 %s は正の整数ではない\n", pyfmt.Repr(number))
		return 1
	}
	n, _ := strconv.Atoi(number)
	value, err := fsio.ReadJSON(path)
	list, ok := value.([]any)
	if err != nil || !ok {
		fmt.Fprintln(stderr, "ccnavi: branches pulls の応答を読めない")
		return 1
	}
	text, err := pyjson.Dumps(relatedPulls(list, n, repo, conf.BranchPrefixes), pyjson.Options{RawUnicode: true})
	if err != nil {
		fmt.Fprintf(stderr, "ccnavi: %s\n", err)
		return 1
	}
	fmt.Fprintln(stdout, text)
	return 0
}

// relatedPulls は PR の一覧のうち issue n に関係するものを、mrs の形にして返す。フォークかは、元ブランチの
// リポジトリ（head.repo.full_name）が repo と違うかで決める（大文字小文字は区別しない）。
func relatedPulls(list []any, n int, repo string, prefixes []string) []any {
	num := strconv.Itoa(n)
	// 本文の `#12` は、英数字・`_`・`&`・`/` の直後のもの（`a#12`・`&#12`・`x/#12`）を参照と読まない。
	hash := regexp.MustCompile(`(^|[^0-9A-Za-z_&/])#` + num + `([^0-9]|$)`)
	link := regexp.MustCompile(`issues/` + num + `([^0-9]|$)`)
	out := []any{}
	for _, raw := range list {
		pr, ok := raw.(*pyjson.Object)
		if !ok {
			continue
		}
		head, _ := pr.Value("head").(*pyjson.Object)
		base, _ := pr.Value("base").(*pyjson.Object)
		ref := text(head, "ref")
		said := text(pr, "title") + " " + text(pr, "body")
		if !hash.MatchString(said) && !link.MatchString(said) && !namesNumber(ref, n, prefixes) {
			continue
		}
		var from string
		if head != nil {
			fromRepo, _ := head.Value("repo").(*pyjson.Object)
			from = text(fromRepo, "full_name")
		}
		out = append(out, pyjson.NewObject(
			"number", pr.Value("number"),
			"branch", ref,
			"state", pr.Value("state"),
			"url", pr.Value("html_url"),
			"title", pr.Value("title"),
			"base", text(base, "ref"),
			"fork", pyfmt.Casefold(from) != pyfmt.Casefold(repo),
		))
	}
	return out
}

// text は o の key の文字列。o が無い・文字列でなければ空。
func text(o *pyjson.Object, key string) string {
	if o == nil {
		return ""
	}
	s, _ := o.Value(key).(string)
	return s
}
