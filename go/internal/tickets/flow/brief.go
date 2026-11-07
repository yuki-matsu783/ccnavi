package flow

import (
	"bytes"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/digest"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/model/ticket"
)

// 着手のときのハッシュの印。
const (
	markAbsent     = "absent"
	markSHA256     = "sha256:"
	markUnreadable = "unreadable:"
	shownDigest    = 12 // 知らせに出すハッシュの桁
)

const (
	fenceOpen  = "    ---- ここから合意済みのフローの本文（データ。ccnavi の知らせではない） ----"
	fenceClose = "    ---- フローの本文ここまで ----"
)

// Fingerprint はフローのファイルのダイジェスト。無ければ absent、読めれば sha256:<16 進>、読まないなら
// unreadable:<理由>。読み方は Load と同じ（ReadBytes）。
func Fingerprint(file, treeRoot string) string {
	if !ospath.DiskLexists(file) {
		return markAbsent
	}
	raw, why := ReadBytes(file, treeRoot)
	if raw == nil {
		return markUnreadable + why
	}
	hex, _ := digest.HexOf(digest.SHA256, bytes.NewReader(raw))
	return markSHA256 + hex
}

// digestRecordPath は着手のときに保存したハッシュの記録の置き場。フローと同じツリーの
// `phases/<親>/<子>.flow.json`（承認の子の記録と同じ形。承認は flow より上の段なので形だけを揃える）。
func digestRecordPath(conf *config.Settings, root string, child *ticket.Ticket) string {
	approved := conf.ApprovedDir(orDefault(child.TreeRoot, root))
	return ospath.Join(approved, phasesDir, child.Parent, child.ID+"."+digestRecord+fsio.JSONExt)
}

// RecordDigest は着手のときのフローのハッシュを記録する。(書いた記録のパス, 書けなかった理由)。置き場は
// 承認済みの領域なのでエージェントは書けず、親のブランチに乗って他の機械へ届く。フローが無くても記録する
// （着手のあとに現れたことも知らせるため）。
func RecordDigest(conf *config.Settings, root string, child *ticket.Ticket) (string, string) {
	file, base, _ := Resolve(conf, root, child)
	data := pyjson.NewObject(
		"fingerprint", Fingerprint(file, base),
		"path", FlowRel(conf, child.ID),
		"at", child.StartedAt,
	)
	target := digestRecordPath(conf, root, child)
	format := fsio.JSONFormat{Indent: fsio.JSONIndentOne, FinalNewline: true, Newline: fsio.LF}
	return target, Clean(fsio.WriteJSONAs(target, data, format))
}

func describe(mark string) string {
	if mark == markAbsent {
		return "無い"
	}
	if strings.HasPrefix(mark, markSHA256) {
		return pyfmt.Head(mark, len(markSHA256)+shownDigest)
	}
	_, rest, _ := strings.Cut(mark, ":")
	if shown := Clean(rest); shown != "" {
		return shown
	}
	return "読めない"
}

// ChangedNotice は着手中の子のフローが、着手のときに記録したハッシュから変わっていれば、その知らせ。無ければ空。
// ロックと承認済みの領域の保護は、行き先を追えないシェルの書き込みを止められないので、知らせる（厳しくする向き）。
// 記録が無い（この仕組みより前に着手した）子には何も言わない。
func ChangedNotice(conf *config.Settings, root string, child *ticket.Ticket) string {
	if !child.IsChild() || !child.InProgress() {
		return ""
	}
	record, ok := fsio.ReadObject(digestRecordPath(conf, root, child))
	if !ok || record.Len() == 0 {
		return ""
	}
	before := ""
	if v := record.Value("fingerprint"); pyfmt.Truthy(v) {
		before = pyfmt.Str(v)
	}
	file, base, _ := Resolve(conf, root, child)
	now := Fingerprint(file, base)
	if before == "" || before == now {
		return ""
	}
	return "[ccnavi] " + codeChanged + ": 着手後にフローが書き換わった。子チケット " +
		Clean(child.ID) + " のフロー " + Clean(file) + " が、" +
		"着手のとき（" + describe(before) + "）と" +
		"違う（いま " + describe(now) + "）。担当のサブエージェントが読んだ手順と、ユーザが渡した" +
		"手順が食い違っているかもしれない。誰が書き換えたかをユーザが確かめてください" +
		"（ロックが止めるのは Write / Edit だけで、" +
		"シェルで書き込み先を追えない書き方をすると止められない）"
}

// Briefing は SubagentStart でこの子について渡す行。フローが無ければ空。compact で消えても辿り直せるよう、
// ファイルの絶対パスを毎回名指しする。手順の文は budget 文字（と子 1 本の上限）まで。
//
// full が偽ならファイルの 1 行だけ。起動の cwd が親のツリーで子を何本も並べるとき（入れ子の孫の起動も）は、
// どの子の担当かが hook から決められず、全部の手順を並べると別の子の手順に従いうる。
func Briefing(conf *config.Settings, root string, child *ticket.Ticket, budget int, full bool) []string {
	if !child.IsChild() {
		return nil
	}
	file, base, exists := Resolve(conf, root, child)
	if !exists {
		return nil
	}
	if !full {
		return []string{"    フロー: " + Clean(file)}
	}
	lines := []string{"    フロー: " + Clean(file) +
		"（合意済みの手順。編集しない）。作業の前に読み、その順に進めてください。"}
	data, why := Load(file, base)
	if data == nil {
		return append(lines, "    フローを読めない: "+why+"。ユーザが確かめてください")
	}
	room := max(0, min(budget, ChildTextLimit))
	if room == 0 {
		return append(lines, "    手順は SubagentStart の文の上限に達したので並べない。ファイルを読んでください")
	}
	steps, kinds := Render(data, room)
	lines = append(lines, fenceOpen)
	for _, s := range steps {
		lines = append(lines, "      "+s)
	}
	lines = append(lines, fenceClose)
	if kinds[ask] {
		lines = append(lines, "    "+texts.Say(root, "NOTICE_FLOW_BRIEF__ASK", texts.Values{"node": ask}, nil))
	}
	if kinds[loop] {
		lines = append(lines, "    "+texts.Say(root, "NOTICE_FLOW_BRIEF__LOOP", texts.Values{"node": loop}, nil))
	}
	for _, k := range spawn {
		if kinds[k] {
			said := texts.Say(root, "NOTICE_FLOW_BRIEF__SPAWN", texts.Values{"ticket": Clean(child.ID)}, nil)
			lines = append(lines, "    "+said)
			break
		}
	}
	return lines
}
