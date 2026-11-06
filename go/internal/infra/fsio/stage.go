package fsio

import (
	"errors"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
)

// 溜める段（承認の plan）。Staging の間、書き込みの関数はディスクに書かずに Op を溜め、
// 読みの関数（ReadText・ReadBytes・ReadJSON・Exists・Lexists・Listdir・LoadSource）は溜めた中身を先に見る。
// 何をどの順に書くかが値として出るので、手元の Writer(FS) はそれを Apply で書き、Chrome は同じ値を
// 1 コミットにする。溜める段では書き込みが失敗しないので、失敗したときの扱いは Policy で前もって付ける。

// FailMode は書けなかったときの扱い。
type FailMode string

const (
	FailStop  FailMode = "stop"  // そこで止め、理由を呼び手へ返す
	FailWarn  FailMode = "warn"  // 標準エラーに言って続ける
	FailLine  FailMode = "line"  // 知らせる行を標準出力に出し、同じ組の残りを飛ばす
	FailQuiet FailMode = "quiet" // 何も出さずに続ける
)

// reasonSlot は Policy.Message の中で理由を差し込む場所。
const reasonSlot = "{reason}"

// Policy は溜める書き込みが書けなかったときの扱い。
type Policy struct {
	OnFail FailMode
	// Message は知らせの本文。{reason} に理由が入る。Prefix はその前に付ける呼び手の用件。
	Message string
	Prefix  string
	// Undo は失敗したときに消すパス、Restore は前の中身へ戻すパス。片方だけ新しくなった形を残さない。
	Undo    []string
	Restore []Prior
	// Places は書けたら「置いた」と数える識別子。
	Places string
	// Group が同じ行と書き込みは 1 つの組で、FailLine で失敗したら残りを飛ばす。
	Group int
	// Ticket は知らせの頭に付ける識別子。
	Ticket string
	// Tag は書けたときに組ごとに記録する名前（Call が実際に書けたものを知るのに使う）。
	Tag string
}

// DefaultPolicy は外側で何も決めていないときの扱い。
func DefaultPolicy() Policy {
	return Policy{OnFail: FailStop, Message: reasonSlot}
}

// FailureText は書けなかった知らせの本文。{reason} だけを差し込み、ほかの "{" "}" はそのまま出す。
func (p Policy) FailureText(reason string) string {
	return p.Prefix + strings.ReplaceAll(p.Message, reasonSlot, reason)
}

// Stream はユーザに見せる行の出し先。
type Stream string

const (
	StreamOut Stream = "stdout"
	StreamErr Stream = "stderr"
)

// Item は溜めたものの 1 つ（*Op・Line・Call）。
type Item interface {
	ItemGroup() int
}

type opKind int

const (
	opWrite opKind = iota
	opRemove
	opUnlink
	opMove
	opReplace
)

// Op は溜めた書き込み 1 つ。Content は書いた後の中身、Gone なら消したもの。Move では Path が行き先。
type Op struct {
	kind       opKind
	how        How
	Path       string
	Content    []byte
	Gone       bool
	Source     string
	isText     bool
	text       string
	newline    Newline
	tempSuffix string
	Policy     Policy
}

func (op *Op) ItemGroup() int { return op.Policy.Group }

// moveTargetExists は並べた後に行き先が置かれていたときの理由（上書きしない）。
const moveTargetExists = "移動先に既にある"

// Apply は溜めた書き込みをディスクに書く（Writer(FS)）。書けたら空文字、駄目なら理由。
// 溜める段の外で呼ぶ。テキストはディスクの改行（NativeNewline なら機械の改行）で書く。
func (op *Op) Apply() string {
	switch op.kind {
	case opRemove:
		Remove(op.Path)
		return ""
	case opUnlink:
		return Unlink(op.Path)
	case opMove:
		if ospath.Lexists(op.Path) {
			return moveTargetExists
		}
		return Move(op.Source, op.Path)
	case opReplace:
		return ReplaceFile(op.Path, op.Content, op.tempSuffix)
	}
	if op.isText {
		return WriteText(op.Path, op.text, op.newline, op.how)
	}
	return WriteFile(op.Path, op.Content, op.how)
}

// Line はユーザに見せる 1 行。書き込みと同じ並びに置き、同じ組が失敗したら出さない。
type Line struct {
	Text   string
	Group  int
	Stream Stream
}

func (l Line) ItemGroup() int { return l.Group }

// Call は Writer(FS) が同じ組の書き込みを済ませた後に呼ぶ手順。Run は組の中で書けた Tag の集まりを受け、
// 見せる行を返す。Lines は全部書けたときに出る行（Chrome は 1 コミットで全部書く）。
type Call struct {
	Run   func(done map[string]bool) []string
	Group int
	Lines []string
}

func (c Call) ItemGroup() int { return c.Group }

type view struct {
	spelled string
	content []byte
	gone    bool
	existed bool // 最初に溜めたときにディスクに在ったか（作るのか直すのかを分ける）
}

// Stage は溜めたものの並びと、溜めた後の中身の見え方。
type Stage struct {
	Items  []Item
	views  map[string]*view
	order  []string
	groups int
}

// NewGroup は新しい組の番号。
func (s *Stage) NewGroup() int {
	s.groups++
	return s.groups
}

// AddLine は見せる行を並びに足す。
func (s *Stage) AddLine(text string, group int, stream Stream) {
	s.Items = append(s.Items, Line{Text: text, Group: group, Stream: stream})
}

// Touched は溜めた後の中身の 1 つ。
type Touched struct {
	Path    string // 絶対パス（綴りは最初に溜めたとき）
	Content []byte
	Gone    bool
	Existed bool // 溜める前にディスクに在ったか
}

// Touched は溜めた後の中身を、最初に溜めた順に返す。
func (s *Stage) Touched() []Touched {
	out := make([]Touched, 0, len(s.order))
	for _, key := range s.order {
		v := s.views[key]
		out = append(out, Touched{Path: v.spelled, Content: v.content, Gone: v.gone, Existed: v.existed})
	}
	return out
}

func (s *Stage) get(path string) (*view, bool) {
	v, ok := s.views[stageKey(path)]
	return v, ok
}

func (s *Stage) set(path string, content []byte, gone bool) {
	key := stageKey(path)
	v, ok := s.views[key]
	if !ok {
		v = &view{existed: ospath.Lexists(path)}
		s.views[key] = v
		s.order = append(s.order, key)
	}
	v.spelled, v.content, v.gone = ospath.Abspath(path), content, gone
}

// namesIn はこのディレクトリの直下で、溜めた書き込みが足した名前と消した名前。
func (s *Stage) namesIn(directory string) ([]string, map[string]bool) {
	base := stageKey(directory)
	var added []string
	dropped := map[string]bool{}
	for _, key := range s.order {
		if ospath.Dirname(key) != base {
			continue
		}
		v := s.views[key]
		if name := ospath.Basename(v.spelled); v.gone {
			dropped[name] = true
		} else {
			added = append(added, name)
		}
	}
	return added, dropped
}

func (s *Stage) put(op Op, quiet bool) string {
	op.Policy = policy
	if quiet {
		op.Policy.OnFail = FailQuiet
	}
	if op.kind == opMove {
		s.set(op.Source, nil, true)
	}
	s.set(op.Path, op.Content, op.Gone)
	s.Items = append(s.Items, &op)
	return ""
}

func stageKey(path string) string {
	return pathcase.Fold(ospath.Abspath(path))
}

var (
	current *Stage
	policy  = DefaultPolicy()
)

// ErrNestedStaging は溜める段の中で溜める段を始めようとした。
var ErrNestedStaging = errors.New("溜める段は入れ子にしない")

// Staging は、返した関数を呼ぶまでの書き込みをディスクに書かずに溜める。入れ子にはしない。
func Staging() (*Stage, func(), error) {
	if current != nil {
		return nil, func() {}, ErrNestedStaging
	}
	stage := &Stage{views: map[string]*view{}}
	current = stage
	return stage, func() { current = nil }, nil
}

// CurrentStage は溜めている段。溜めていなければ nil。
func CurrentStage() *Stage {
	return current
}

// SetPolicy は、返した関数を呼ぶまでに溜める書き込みの扱いを change で変える（外側の扱いを上書きする）。
func SetPolicy(change func(*Policy)) (restore func()) {
	before := policy
	next := before
	next.Undo = append([]string(nil), before.Undo...)
	next.Restore = append([]Prior(nil), before.Restore...)
	change(&next)
	policy = next
	return func() { policy = before }
}

// stagedContent は溜める段で見えるパスの中身。staged が false なら溜める段はそのパスを知らない。
// ok が false なら溜める段で消したもの。
func stagedContent(path string) (content []byte, staged, ok bool) {
	if current == nil {
		return nil, false, false
	}
	v, found := current.get(path)
	if !found {
		return nil, false, false
	}
	return v.content, true, !v.gone
}
