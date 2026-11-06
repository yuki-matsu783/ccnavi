package config

// state の下に上の段（policy）が書き、records/prune が消す記録の名前。prune は上の段を読めないので、
// 名前をここに 1 つ置き、書く側と消す側が同じ定数を見る。
const (
	// SelfguardDir は自己防衛のバックアップの置き場（セッションごとのディレクトリが並ぶ）。
	SelfguardDir = "selfguard"
	// SelfguardStore は SelfguardDir の直下の、大きい対象の実体の置き場（セッションではない。prune は消さない）。
	SelfguardStore = "store"
	// OncePrefix はルールの文脈で渡した回の記録の名前の頭（`once-<セッション>-<文脈>.json`）。
	OncePrefix = "once-"
	// StopPrefix は Stop の促しの数えの記録の名前の頭（`stop-<セッション>.json`）。
	StopPrefix = "stop-"
)
