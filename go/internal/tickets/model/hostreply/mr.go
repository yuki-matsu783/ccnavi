package hostreply

import (
	"fmt"
	"strings"
)

// OpenMR は、開いた MR の一覧（ホストの応答のまま）から 1 件を選ぶ。GitHub は先頭、GitLab はこのプロジェクト
// （source_project_id が pid）から出たものの先頭（フォークから出た同じ名前のブランチの MR を拾わない）。無ければ nil。
func OpenMR(kind, pid string, all []any) (any, error) {
	for _, x := range all {
		if kind == "github" {
			return x, nil
		}
		src, err := Field(x, "source_project_id")
		if err != nil {
			return nil, err
		}
		if SameNumber(src, pid) {
			return x, nil
		}
	}
	return nil, nil
}

// MergedMR は、MR の一覧から、マージ済みの 1 件を選ぶ。GitHub は閉じた PR の一覧のうち merged_at のあるものの先頭、
// GitLab はマージ済みの一覧の先頭。pid を渡せば、GitLab はこのプロジェクトから出たものに限る。無ければ nil。
func MergedMR(kind, pid string, all []any) (any, error) {
	for _, x := range all {
		key, want := "merged_at", ""
		if kind != "github" {
			if pid == "" {
				return x, nil
			}
			key, want = "source_project_id", pid
		}
		v, err := Field(x, key)
		if err != nil {
			return nil, err
		}
		if (want == "" && v != nil) || (want != "" && SameNumber(v, want)) {
			return x, nil
		}
	}
	return nil, nil
}

// FindMR は、開いた MR の一覧から親のブランチの MR を選び、`number`・`url`・`source`・`target`・`draft` の 1 行 1 項目で
// 返す（`ccnavi-review.sh` の find_mr）。無ければ空。読めなければ ErrShape など。
func FindMR(kind, pid string, data []byte) (string, error) {
	v, err := Decode(data)
	if err != nil {
		return "", err
	}
	all, err := List(v)
	if err != nil {
		return "", err
	}
	x, err := OpenMR(kind, pid, all)
	if err != nil || !Truthy(x) {
		return "", err
	}
	keys := []string{"number", "html_url", "head.ref", "base.ref"}
	if kind != "github" {
		keys = []string{"iid", "web_url", "source_branch", "target_branch"}
	}
	var b strings.Builder
	for i, name := range []string{"number", "url", "source", "target"} {
		got, err := Path(x, strings.Split(keys[i], ".")...)
		if err != nil {
			return "", err
		}
		if i >= 2 {
			got = Alt(got, "")
		}
		fmt.Fprintf(&b, "%s %s\n", name, oneLine(Text(got)))
	}
	draft, err := Field(x, "draft")
	if err != nil {
		return "", err
	}
	if kind != "github" {
		wip, _ := Field(x, "work_in_progress")
		draft = Alt(draft, wip)
	}
	fmt.Fprintf(&b, "draft %s\n", oneLine(Text(Alt(draft, false))))
	return b.String(), nil
}

// Merged は、MR がマージ済みかを読む（`ccnavi-review.sh merged`）。number があれば MR 1 本の応答、無ければ元ブランチで
// 絞った一覧（GitHub は閉じた PR、GitLab はマージ済みの MR）。GitLab はこのプロジェクト（pid）から出たものだけ。
// 答えは `merged <番号> <先頭の sha> <元ブランチ>` か `none` の 1 行。読めなければ error。
func Merged(kind, number, pid string, data []byte) (string, error) {
	v, err := Decode(data)
	if err != nil {
		return "", err
	}
	var x any
	if number != "" {
		x, err = mergedOne(kind, pid, v)
	} else {
		var all []any
		if all, err = List(v); err == nil {
			x, err = MergedMR(kind, pid, all)
		}
	}
	if err != nil {
		return "", err
	}
	if x == nil {
		return "none\n", nil
	}
	keys := [][]string{{"number"}, {"head", "sha"}, {"head", "ref"}}
	if kind != "github" {
		keys = [][]string{{"iid"}, {"sha"}, {"source_branch"}}
	}
	words := []string{"merged"}
	for i, k := range keys {
		got, err := Path(x, k...)
		if err != nil {
			return "", err
		}
		if i > 0 {
			got = Alt(got, "")
		}
		words = append(words, oneLine(Text(got)))
	}
	return strings.Join(words, " ") + "\n", nil
}

// mergedOne は MR 1 本の応答 v がマージ済みなら v、そうでなければ nil。
func mergedOne(kind, pid string, v any) (any, error) {
	if kind == "github" {
		at, err := Field(v, "merged_at")
		if err != nil || at == nil {
			return nil, err
		}
		return v, nil
	}
	state, err := Field(v, "state")
	if err != nil {
		return nil, err
	}
	src, err := Field(v, "source_project_id")
	if err != nil {
		return nil, err
	}
	if state == "merged" && SameNumber(src, pid) {
		return v, nil
	}
	return nil, nil
}

// oneLine は、ホストから来る値の改行を空白にする（答えの行に紛れさせない）。
func oneLine(s string) string {
	return strings.NewReplacer("\r\n", " ", "\n", " ", "\r", " ").Replace(s)
}
