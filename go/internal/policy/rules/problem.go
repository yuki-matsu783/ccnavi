package rules

// Severity は苦情の深刻度。ガードを無効にするもの（Error）と、弱めるだけのもの（Warn）を分ける。
type Severity string

const (
	Error Severity = "error"
	Warn  Severity = "warn"
	// Info は書いたとおりに判定に使われているが、書いたユーザが知りたいはずのこと
	// （レイヤーをまたいで同じ定義が重なり、後ろを外した）。Warn に混ぜると本当に緩んだ Warn が埋もれる。
	Info Severity = "info"
)

// KindNotYet は「いまは通らないが、書いた側に直すものは無い」苦情（前のフェーズが閉じていない子）。
// 承認は通さないが、全体を見る lint は Warn にする。
const KindNotYet = "not-yet"

// Problem はルールファイルなどへの苦情 1 件。直せるように名指しする。
type Problem struct {
	Severity Severity
	Rule     string
	Detail   string
	Kind     string
}

// String は Python の str(Problem)。
func (p Problem) String() string {
	rule := p.Rule
	if rule == "" {
		rule = "(file)"
	}
	return string(p.Severity) + ": " + rule + ": " + p.Detail
}
