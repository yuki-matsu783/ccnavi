package host

// 決めた対応方針（decide）を、ホストへ何をどの順で書くか。ターミナル（sh が実行ファイルのサブコマンドから読む）と
// Chrome 拡張（WASM の decide が計画に載せる）が、同じ並びを使う。実行ファイルはネットワークに出ないので、
// 送るのは呼び手（sh・拡張の service worker）で、ここは並びだけを決める。
//
// 順は、issue の作成（続きのコメントを含む）、スレッドへの返信、スレッドの解決、そのあとの決めた内容のコメント。
// 親のブランチへのコミットは、解決までと決めたコメントの間に入る（WriteStep.AfterCommit）。
//
// issue を先にする理由は、解決したのに issue が無い状態を作らないため（返信と解決は、受け入れたことを MR の画面で
// 見えなくする）。途中で止まっても、MR の画面に issue が残り、操作 ID の印から続きを探せる。返信は全部を送って
// から、解決を全部行う（解決に失敗しても、返信が重ならない順）。ADR-0172 と ADR-0139。

// WriteKind は書き込みの種類。
type WriteKind string

// 書き込みの種類。
const (
	WriteIssue     WriteKind = "issue"      // issue の作成
	WriteIssuePart WriteKind = "issue-part" // issue の続きのコメント（Part は 1 始まり）
	WriteReply     WriteKind = "reply"      // スレッドへの返信
	WriteResolve   WriteKind = "resolve"    // スレッドの解決
	WriteNote      WriteKind = "note"       // 決めた内容の MR へのコメント
)

// WriteStep は書き込み 1 つ。Key は指摘のキー（返信・解決・issue）。決めた内容のコメントは空。
type WriteStep struct {
	Kind WriteKind
	Key  string
	Part int
}

// AfterCommit は、親のブランチへのコミットのあとに書くか。
func (s WriteStep) AfterCommit() bool { return s.Kind == WriteNote }

// IssueWrite は作る issue 1 つ。Parts は本文の続きのコメントの数。
type IssueWrite struct {
	Key   string
	Parts int
}

// WriteOrder は、書き込みの並びを決める。threads は返信して解決する指摘のキー、note は決めた内容のコメントを書くか。
func WriteOrder(issues []IssueWrite, threads []string, note bool) []WriteStep {
	var out []WriteStep
	for _, i := range issues {
		out = append(out, WriteStep{Kind: WriteIssue, Key: i.Key})
		for n := 1; n <= i.Parts; n++ {
			out = append(out, WriteStep{Kind: WriteIssuePart, Key: i.Key, Part: n})
		}
	}
	for _, k := range threads {
		out = append(out, WriteStep{Kind: WriteReply, Key: k})
	}
	for _, k := range threads {
		out = append(out, WriteStep{Kind: WriteResolve, Key: k})
	}
	if note {
		out = append(out, WriteStep{Kind: WriteNote})
	}
	return out
}
