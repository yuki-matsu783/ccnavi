package hostreply

import (
	"fmt"
	"strings"

	"ccnavi/internal/infra/pathcase"
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

// MergedMR は、MR の一覧から、マージ済みの 1 件を選ぶ。GitHub は閉じた PR の一覧のうち IsMerged のものの先頭、
// GitLab はマージ済みの一覧のうち IsFork でないものの先頭（一覧はマージ済みで絞ってあるので state は見ない）。
// pid を渡せば、GitLab はこのプロジェクトから出たものに限る。無ければ nil。
func MergedMR(kind, pid string, all []any) (any, error) {
	for _, x := range all {
		var ok bool
		var err error
		if kind == "github" {
			ok, err = IsMerged(kind, pid, x)
		} else {
			ok, err = IsFork(kind, pid, x)
			ok = !ok
		}
		if err != nil {
			return nil, err
		}
		if ok {
			return x, nil
		}
	}
	return nil, nil
}

// IsFork は、MR（ホストの応答 1 本）がフォークから出たもの（このプロジェクト・リポジトリの MR でないもの）か。
// GitLab は source_project_id が pid と違うこと。pid が空なら、応答の target_project_id と比べる（どちらかが無ければ
// 偽）。GitHub は head のリポジトリ（head.repo.full_name）が base のリポジトリ（base.repo.full_name）と違うこと
// （大文字小文字は区別しない。どちらかが読めなければ偽）。
func IsFork(kind, pid string, mr any) (bool, error) {
	if kind == "github" {
		head, err := Path(mr, "head", "repo", "full_name")
		if err != nil {
			return false, err
		}
		base, err := Path(mr, "base", "repo", "full_name")
		if err != nil {
			return false, err
		}
		h, hok := head.(string)
		b, bok := base.(string)
		return hok && bok && pathcase.Fold(h) != pathcase.Fold(b), nil
	}
	src, err := Field(mr, "source_project_id")
	if err != nil {
		return false, err
	}
	if pid != "" {
		return !SameNumber(src, pid), nil
	}
	target, err := Field(mr, "target_project_id")
	if err != nil {
		return false, err
	}
	return src != nil && target != nil && Text(src) != Text(target), nil
}

// GitHubMerged は、GitHub の PR の merged_at から、マージ済みかを決める（ホストの `merged` の欄は見ない）。
func GitHubMerged(mergedAt any) bool { return Truthy(mergedAt) }

// IsMerged は、MR 1 本（ホストの応答）がマージ済みかを決める 1 か所。マージ済みで、かつフォークから出たものでないとき真。
// GitLab は state が merged、GitHub は GitHubMerged。pid は IsFork に渡す。
func IsMerged(kind, pid string, mr any) (bool, error) {
	var merged bool
	if kind == "github" {
		at, err := Field(mr, "merged_at")
		if err != nil {
			return false, err
		}
		merged = GitHubMerged(at)
	} else {
		state, err := Field(mr, "state")
		if err != nil {
			return false, err
		}
		merged = state == "merged"
	}
	if !merged {
		return false, nil
	}
	fork, err := IsFork(kind, pid, mr)
	return !fork, err
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
	ok, err := IsMerged(kind, pid, v)
	if err != nil || !ok {
		return nil, err
	}
	return v, nil
}

// oneLine は、ホストから来る値の改行を空白にする（答えの行に紛れさせない）。
func oneLine(s string) string {
	return strings.NewReplacer("\r\n", " ", "\n", " ", "\r", " ").Replace(s)
}
