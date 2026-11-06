package marks

import (
	"strconv"

	"ccnavi/internal/infra/fsio"
)

// Cleared は Clear の答え。Kinds は消せた種類（溜める段では消す種類）。Failed は reviewed を消せなかった理由
// （空でなければ呼び手は止める）。Warnings はほかの種類を消せなかった知らせ（残っていて判定に使われる）。
type Cleared struct {
	Kinds    []Kind
	Failed   string
	Warnings []string
}

// clearOrder は消す順。reviewed を先に消す。消せなければ止めるので、ほかに手を付ける前に決める。
var clearOrder = []Kind{Reviewed, Requested, Skipped, Pending}

// Clear はこのフェーズのマーカーを全部消す（同じ番号に子を足した）。reviewed が残るとフェーズは済んだまま
// 読まれ、足した子を見ずに先へ進むので、消せなければ止める（Failed）。ほかは消せなくても止めない。
//
// 溜める段（承認の plan）では消す書き込みを溜め、見せる行は Writer(FS) が実際に消せた種類で作る（fsio.Call）。
// announce は消せた種類から見せる行を作る（nil なら行を出さない）。
func Clear(approvedDir, parent string, phase int, announce func([]Kind) []string) Cleared {
	var present []Kind
	for _, kind := range clearOrder {
		if fsio.Lexists(MarkPath(approvedDir, parent, phase, kind)) {
			present = append(present, kind)
		}
	}
	if stage := fsio.CurrentStage(); stage != nil {
		group := stage.NewGroup()
		for _, kind := range present {
			path := MarkPath(approvedDir, parent, phase, kind)
			restore := fsio.SetPolicy(func(p *fsio.Policy) {
				p.Group, p.Tag = group, string(kind)
				clearPolicy(p, path, phase, kind)
			})
			fsio.Unlink(path)
			restore()
		}
		kinds := inOrder(present, nil)
		say := func(k []Kind) []string {
			if announce == nil || len(k) == 0 {
				return nil
			}
			return announce(k)
		}
		run := func(done map[string]bool) []string {
			return say(inOrder(nil, done))
		}
		stage.Items = append(stage.Items, fsio.Call{Run: run, Group: group, Lines: say(kinds)})
		return Cleared{Kinds: kinds}
	}
	var cleared []Kind
	out := Cleared{}
	for _, kind := range present {
		path := MarkPath(approvedDir, parent, phase, kind)
		reason := fsio.Unlink(path)
		if reason == "" {
			cleared = append(cleared, kind)
			continue
		}
		p := fsio.DefaultPolicy()
		clearPolicy(&p, path, phase, kind)
		text := p.FailureText(reason)
		if kind == Reviewed {
			out.Failed = text
			break
		}
		out.Warnings = append(out.Warnings, text)
	}
	out.Kinds = inOrder(cleared, nil)
	return out
}

// inOrder は Kinds の順に並べた種類。list に在るか、done（fsio.Call が受ける書けた Tag）に在るもの。
func inOrder(list []Kind, done map[string]bool) []Kind {
	var out []Kind
	for _, k := range Kinds {
		if done[string(k)] || contains(list, k) {
			out = append(out, k)
		}
	}
	return out
}

func contains(list []Kind, k Kind) bool {
	for _, x := range list {
		if x == k {
			return true
		}
	}
	return false
}

func clearPolicy(p *fsio.Policy, path string, phase int, kind Kind) {
	n := strconv.Itoa(phase)
	p.Prefix = ""
	if kind == Reviewed {
		p.OnFail = fsio.FailStop
		p.Message = "フェーズ " + n + " のレビュー済みのマーカー " + path + " を削除できません（{reason}）。" +
			"残るとフェーズは済んだまま読まれるので、ここで止める。ユーザがマーカーを削除します。"
		return
	}
	p.OnFail = fsio.FailWarn
	p.Message = "フェーズ " + n + " のマーカー " + path + " を削除できませんでした（{reason}）。" +
		string(kind) + " は残っていて有効なまま"
}
