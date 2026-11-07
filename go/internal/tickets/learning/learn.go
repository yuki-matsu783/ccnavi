package learning

import (
	"io"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathmatch"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/records/wiki"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase"
)

// 終了コード。
const (
	exitWritten = 0
	exitRefused = 1
)

// Note は `ticket learn` の引数。Note が空なら、チケットに結び付く作業に限り記録の雛形と要約を書く。
type Note struct {
	Ident, Note, Target, Signal string
}

// unsafePath は書く先の途中のリンクか、ワークツリーの外を指すもの。無ければ空。
func unsafePath(treeRoot, rel string) string {
	var parts []string
	for _, p := range strings.Split(rel, "/") {
		if p != "" {
			parts = append(parts, p)
		}
	}
	if len(parts) == 0 {
		return ""
	}
	if link, ok := pathmatch.FirstLink(treeRoot, parts, true); ok {
		return link
	}
	cur := ospath.Join(treeRoot, parts...)
	if ospath.DiskExists(cur) && pathmatch.Escapes(treeRoot, cur) {
		return cur
	}
	return ""
}

func boundTicket(conf *config.Settings, root, name string) (*ticket.Ticket, bool) {
	copies, _ := approval.Scan(conf, root, false, nil)
	return tree.Lookup(approval.ByID(copies), name)
}

func parentOf(t *ticket.Ticket) string {
	if t.IsChild() {
		return t.Parent
	}
	return ""
}

// Run は `ticket learn` の本体。結果は stdout、理由は stderr に書き、0（書けた）か 1（止めた）を返す。
// チケットに結び付く作業は自分の記録へ、結び付かない作業は受信箱へ書く。ワークスペースとプロジェクトのルートには
// 書かない（追跡されず、誰がまとめるかも決まらない置き場になる）。
func Run(stdout, stderr io.Writer, conf *config.Settings, root, cwd string, n Note) int {
	if !conf.LearningEnabled() {
		write(stderr, "ccnavi: 学びの蓄積が disable（"+env.Learning+"）\n")
		return exitRefused
	}
	here, ok := phase.WorktreeAt(conf, root, cwd)
	if !ok {
		write(stderr, "ccnavi: ワークスペースルートとプロジェクトのルートには気づきを書けない。"+
			"'"+config.ScriptCommand(root, config.WorktreeWrapper)+" <名前>' でワークツリーを切り、その中で打ち直してください\n")
		return exitRefused
	}
	bound, _ := boundTicket(conf, root, here.Name)
	return learn(stdout, stderr, conf, root, here, bound, n)
}

// learn は Run のうち、今いるワークツリーと、それが結び付くチケット（無ければ nil）が決まった後。
func learn(stdout, stderr io.Writer, conf *config.Settings, root string, here tree.Tree, bound *ticket.Ticket, n Note) int {
	ident := here.Name
	isBound := bound != nil
	if n.Ident != "" && n.Ident != ident {
		write(stderr, "ccnavi: 作業ディレクトリは "+ident+" のもの。他の作業（"+n.Ident+"）の記録へは書けない\n")
		return exitRefused
	}
	now := fsio.Stamp()
	if ospath.Strip(n.Note) == "" {
		if !isBound {
			write(stderr, "ccnavi: チケットに結び付かない作業では --note だけ指定できる（受信箱に書く）\n")
			return exitRefused
		}
		return scaffold(stdout, stderr, conf, root, here, parentOf(bound))
	}
	if msg := validate(n.Note, n.Target, n.Signal); msg != "" {
		write(stderr, "ccnavi: "+msg+"\n")
		return exitRefused
	}
	entry := entryText(n.Note, n.Target, n.Signal, now)
	if isBound {
		return appendRecord(stdout, stderr, root, here.Root, ident, parentOf(bound), entry)
	}
	return writeInbox(stdout, stderr, here.Root, ident, entry, now)
}

func refuseLink(stderr io.Writer, bad string) int {
	write(stderr, "ccnavi: "+bad+" はシンボリックリンクか、ワークツリーの外を指すので反映しません\n")
	return exitRefused
}

func cannotWrite(stderr io.Writer, why string) int {
	write(stderr, "ccnavi: 書けない（"+why+"）\n")
	return exitRefused
}

// scaffold は記録が無いか空なら雛形を書き、判定ログの要約を書き直す。
func scaffold(stdout, stderr io.Writer, conf *config.Settings, root string, here tree.Tree, parent string) int {
	ident := here.Name
	bad := unsafePath(here.Root, RecordRel(ident))
	if bad == "" {
		bad = unsafePath(here.Root, digestRel(ident))
	}
	if bad != "" {
		return refuseLink(stderr, bad)
	}
	recPath := ospath.Join(here.Root, strings.Split(RecordRel(ident), "/")...)
	if err := fsio.MakeDirs(ospath.Dirname(recPath)); err != nil {
		return cannotWrite(stderr, fsio.ErrorText(err))
	}
	blank := true
	if ospath.DiskExists(recPath) {
		text, err := fsio.ReadDiskText(recPath, fsio.Replace)
		if err != nil {
			return cannotWrite(stderr, fsio.ErrorText(err))
		}
		blank = ospath.Strip(text) == ""
	}
	if blank {
		if msg := fsio.WriteText(recPath, scaffoldText(ident, parent), fsio.NativeNewline, fsio.Atomic); msg != "" {
			return cannotWrite(stderr, msg)
		}
	}
	records, recorded := audit.ReadRecords(conf.Log)
	digestPath := ospath.Join(here.Root, strings.Split(digestRel(ident), "/")...)
	format := fsio.JSONFormat{Indent: fsio.JSONIndentOne, ASCII: true, FinalNewline: true, How: fsio.Atomic}
	if msg := fsio.WriteJSONAs(digestPath, buildDigest(records, recorded, &here.Name), format); msg != "" {
		return cannotWrite(stderr, msg)
	}
	write(stdout, "記録: "+RecordRel(ident)+"\n")
	write(stdout, "要約: "+digestRel(ident)+"\n")
	write(stdout, texts.Say(root, "NEXT_LEARN_SCAFFOLD_COMMIT", nil, nil)+"\n")
	return exitWritten
}

// appendRecord はチケットに結び付く作業の記録に節を足す。
func appendRecord(stdout, stderr io.Writer, root, treeRoot, ident, parent, entry string) int {
	rel := RecordRel(ident)
	if bad := unsafePath(treeRoot, rel); bad != "" {
		return refuseLink(stderr, bad)
	}
	recPath := ospath.Join(treeRoot, strings.Split(rel, "/")...)
	if err := fsio.MakeDirs(ospath.Dirname(recPath)); err != nil {
		return cannotWrite(stderr, fsio.ErrorText(err))
	}
	text := scaffoldText(ident, parent)
	if ospath.DiskExists(recPath) {
		read, err := fsio.ReadDiskText(recPath, fsio.Strict)
		if err != nil {
			return cannotWrite(stderr, fsio.ErrorText(err))
		}
		text = read
	}
	updated, err := appendEntry(text, entry, ident, parent)
	if err != nil {
		write(stderr, "ccnavi: 気づきの記録 "+rel+" を読み取れません（"+err.Error()+"）。直すか、消してから打ち直してください\n")
		return exitRefused
	}
	if msg := fsio.WriteText(recPath, updated, fsio.NativeNewline, fsio.Atomic); msg != "" {
		return cannotWrite(stderr, msg)
	}
	write(stdout, "追記した: "+rel+"\n")
	write(stdout, texts.Say(root, "NEXT_LEARN_NOTE_COMMIT", nil, nil)+"\n")
	return exitWritten
}

// writeInbox はチケットに結び付かない作業の気づきを、受信箱の名前が重ならない新しいファイルに書く。
func writeInbox(stdout, stderr io.Writer, treeRoot, ident, entry, now string) int {
	if bad := unsafePath(treeRoot, wiki.InboxDir); bad != "" {
		return refuseLink(stderr, bad)
	}
	stamp := utc14()
	body := []byte(inboxText(ident, entry, now))
	for n := range inboxTries {
		rel := inboxRel(ident, stamp, n)
		path := ospath.Join(treeRoot, strings.Split(rel, "/")...)
		if fsio.Lexists(path) {
			continue
		}
		if msg := fsio.WriteFile(path, body, fsio.New); msg != "" {
			// 確かめてから書くまでの間に置かれたものも、重なった名前として次を試す。
			if fsio.Lexists(path) {
				continue
			}
			return cannotWrite(stderr, msg)
		}
		write(stdout, "受信箱に書いた: "+rel+"\n")
		return exitWritten
	}
	write(stderr, "ccnavi: 受信箱の名前が決まらない\n")
	return exitRefused
}
