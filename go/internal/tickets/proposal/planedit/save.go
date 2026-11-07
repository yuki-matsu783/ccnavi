package planedit

import (
	"bytes"
	"cmp"
	"fmt"
	"slices"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/model/ticket"
)

var childKeys = []string{"ticket", "phase", "predecessors"}

// childRebuilt は子の提案の ticket:・phase:・predecessors: の値だけを付け替えたバイト列。
func (c *childChange) rebuilt() ([]byte, string) {
	text, ok := decode(c.raw)
	if !ok {
		return nil, "UTF-8 として読めない"
	}
	t := c.ticket
	var edits [][2]string
	if c.renamed() {
		edits = append(edits, [2]string{"ticket", "ticket: " + c.newTicket})
	}
	if c.newPhase != phaseOf(t) {
		edits = append(edits, [2]string{"phase", "phase: " + pyPhase(c.newPhase)})
	}
	if !slices.Equal(c.newPredecessors, t.Predecessors) {
		line, err := flowLine(strs(c.newPredecessors))
		if err != nil {
			return nil, err.Error()
		}
		edits = append(edits, [2]string{"predecessors", "predecessors: " + line})
	}
	built := text
	for _, e := range edits {
		var why string
		if built, why = replaceBlock(built, e[0], []string{e[1]}); why != "" {
			return nil, why
		}
	}
	before, err := ticket.FrontMapping(text)
	var after *yaml.Map
	if err == nil {
		after, err = ticket.FrontMapping(built)
	}
	if err != nil {
		return nil, "組んだ全文を読み直せない（" + err.Error() + "）"
	}
	if before == nil || after == nil {
		return nil, "組んだ全文の frontmatter が読めない"
	}
	if !yaml.Equal(before.Without(childKeys...), after.Without(childKeys...)) {
		return nil, "差し替えると ticket・phase・predecessors のほかの欄が変わるので反映しません"
	}
	if !slices.Equal(body(text), body(built)) {
		return nil, "差し替えると本文が変わるので反映しません"
	}
	read, _ := ticket.Parse(built)
	if read == nil || read.ID != c.newTicket || phaseOf(read) != c.newPhase || !slices.Equal(read.Predecessors, c.newPredecessors) {
		return nil, "組んだ全文が付け替えた子の提案として読めないので反映しません"
	}
	return []byte(built), ""
}

// write は子の提案を付け替え、親の提案を書き換える。書けなければ理由（何も残さない）。読んだときから親か子の
// 中身が変わっていれば書かない。書く順は子が先、親が最後で、付け替える子は新しい名前に書いてから古い名前を消す
// （入れ替わる 2 本は読んだ中身を持っているので上書きしてよい）。フローの下書きも一緒に付け替える。途中で
// 落ちたら、触ったパスを全部書く前の中身へ戻す（無かったものは消す）。
func (a *Answer) write(content []byte) string {
	s := a.source
	for _, f := range s.files() {
		if now, ok := fsio.ReadBytes(f.ticket.Path); !ok || !bytes.Equal(now, f.raw) {
			return "提案 " + f.ticket.ID + " が外で変わった（読んでから書くまでの間）"
		}
	}
	var order []string
	final := map[string][]byte{}
	put := func(path string, data []byte) {
		if _, ok := final[path]; !ok {
			order = append(order, path)
		}
		final[path] = data
	}
	var leaving []string
	for i := range a.changes {
		c := &a.changes[i]
		built, why := c.rebuilt()
		if built == nil {
			return c.ticket.Path + ": " + why
		}
		put(c.newPath(), built)
		if !c.renamed() {
			continue
		}
		leaving = append(leaving, c.ticket.Path)
		if s.conf != nil {
			root := cmp.Or(c.ticket.TreeRoot, s.root)
			oldDraft := flow.DraftFile(s.conf, root, c.ticket.ID)
			if fsio.Lexists(oldDraft) {
				if draft, ok := fsio.ReadBytes(oldDraft); ok {
					put(flow.DraftFile(s.conf, root, c.newTicket), draft)
					leaving = append(leaving, oldDraft)
				}
			}
		}
	}
	leaving = slices.DeleteFunc(leaving, func(p string) bool { _, ok := final[p]; return ok })
	touched := slices.Concat(order, leaving)
	if content != nil {
		touched = append(touched, s.Parent.Path)
	}
	before := make([]fsio.Prior, len(touched))
	for i, p := range touched {
		before[i] = fsio.Prior{Path: p, Gone: true}
		if fsio.Lexists(p) {
			if data, ok := fsio.ReadBytes(p); ok {
				before[i] = fsio.Prior{Path: p, Content: data}
			}
		}
	}
	done := ""
	for _, path := range order {
		if failed := fsio.WriteFile(path, final[path], fsio.Durable); failed != "" {
			done = path + " に保存できませんでした（" + failed + "）"
			break
		}
	}
	if done == "" {
		for _, path := range leaving {
			if failed := fsio.Unlink(path); failed != "" {
				done = path + " を削除できません（" + failed + "）"
				break
			}
		}
	}
	if done == "" && content != nil {
		if failed := fsio.WriteFile(s.Parent.Path, content, fsio.Durable); failed != "" {
			done = s.Parent.Path + " に保存できませんでした（" + failed + "）"
		}
	}
	if done != "" {
		fsio.PutBack(before)
		return done + "。書いたものは戻した"
	}
	return ""
}

// Saved は保存（--write）の結果。Messages は 1 行ずつの知らせ、Details はその下に並べる項。
type Saved struct {
	OK       bool
	Written  bool
	Messages []string
	Details  []string
}

// Save は振り直した計画を書き戻す。開いたときのハッシュ（expect）と検査を通らなければ書かない。
func (a *Answer) Save(expect string) Saved {
	if expect != a.source.sha() {
		return Saved{Messages: []string{"提案が外で変わった（--expect が親と子の提案の今の中身と違う）。何も反映しません。" +
			"--plan-order <親> --json で読み直す"}}
	}
	if len(a.renumbered.refused) > 0 {
		var lines []string
		for _, r := range a.renumbered.refused {
			lines = append(lines, "先行の線を引けない: "+r.text)
		}
		return Saved{Messages: append(lines, "何も反映しません。")}
	}
	for _, check := range []struct {
		details []any
		header  string
	}{
		{problemTexts(a.problems), "振り直した計画の順序の検査に error がある。何も反映しません。"},
		{problemTexts(a.revisionProblems), "改版で通らない形（子が承認された番号を固定しているので、その番号のステップは変えられない）。何も反映しません。"},
		{strs(a.childProblems), "子の提案を付け替えられない。何も反映しません。"},
	} {
		if len(check.details) > 0 {
			details := make([]string, len(check.details))
			for i, d := range check.details {
				details[i] = d.(string)
			}
			return Saved{Messages: []string{check.header}, Details: details}
		}
	}
	content, why := a.rebuilt()
	if why != "" {
		return Saved{Messages: []string{a.source.Parent.Path + ": " + why + "。何も反映しません。"}}
	}
	if content == nil && len(a.changes) == 0 {
		return Saved{OK: true}
	}
	if failed := a.write(content); failed != "" {
		return Saved{Messages: []string{failed}}
	}
	var renamed []string
	for i := range a.changes {
		c := &a.changes[i]
		if c.renamed() {
			renamed = append(renamed, fmt.Sprintf("子の提案 %s を %s に付け替えた（phase: %s → %s）",
				c.ticket.ID, c.newTicket, pyPhase(phaseOf(c.ticket)), pyPhase(c.newPhase)))
		}
	}
	return Saved{OK: true, Written: true, Messages: renamed}
}

func pyPhase(v any) string {
	if v == nil {
		return "None"
	}
	return fmt.Sprint(v)
}
