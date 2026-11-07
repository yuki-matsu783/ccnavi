package handoff

import (
	"ccnavi/internal/infra/pyjson"
)

// 拡張（handoffmodel.ts）が読む欄。キーの順も Python の答えと同じにする。

func strs(in []string) []any {
	out := make([]any, len(in))
	for i, s := range in {
		out[i] = s
	}
	return out
}

func (e *Entry) json() *pyjson.Object {
	return pyjson.NewObject(
		"path", e.Rel,
		"draft", e.draftRel(),
		"kind", string(e.Kind.ID),
		"state", string(e.State()),
		"board", e.Board(),
		"selected", e.Selected,
		"errors", strs(e.Errors),
		"warnings", strs(e.Warnings),
		"diff", e.Diff,
		"entry_sha", e.sha(),
	)
}

// JSON は見るだけの形（user handoff --json）の答え。
func (p *Plan) JSON() *pyjson.Object {
	ignored := make([]any, len(p.Ignored))
	for i, g := range p.Ignored {
		ignored[i] = pyjson.NewObject("path", g.path, "why", string(g.why))
	}
	entries := make([]any, len(p.Entries))
	for i, e := range p.Entries {
		entries[i] = e.json()
	}
	out := pyjson.NewObject(
		"version", JSONVersion,
		"tree", p.Tree,
		"ignored", len(p.Ignored),
		"ignored_paths", ignored,
		"entries", entries,
		"expect", p.Expect(),
		"ok", p.OK(),
	)
	if p.Error != "" {
		out.Set("error", p.Error)
	}
	return out
}

// errorJSON は失敗だけを言う答え（取り込みの失敗と、入口が組み合わせの誤りで断るときの形）。
func errorJSON(text string) *pyjson.Object {
	return pyjson.NewObject("version", JSONVersion, "ok", false, "error", text)
}

// JSON は取り込み（user handoff --yes --json）の答え。
func (a *Applied) JSON() *pyjson.Object {
	if !a.OK {
		return errorJSON(a.Error)
	}
	applied := make([]any, len(a.Applied))
	for i, e := range a.Applied {
		applied[i] = pyjson.NewObject("path", e.Rel, "kind", string(e.Kind.ID))
	}
	kept := make([]any, len(a.KeptDrafts))
	for i, k := range a.KeptDrafts {
		kept[i] = pyjson.NewObject("path", k.Path, "reason", k.Reason)
	}
	return pyjson.NewObject(
		"version", JSONVersion,
		"ok", true,
		"applied", applied,
		"removed_drafts", strs(a.RemovedDrafts),
		"kept_drafts", kept,
	)
}
