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

// 上の段（tickets）が持つ置き場と記録の名前のうち、下の段（records・policy）も名前で見るもの。下の段は tickets を
// 読めないので、名前をここに置き、tickets/model の定数（括弧の中）がこれを参照する。
const (
	// OpsDir は state の下の操作記録の置き場（oplog.dirName）。prune は送り終えていない記録を残す側に数える。
	OpsDir = "ops"
	// DirectPrefix は直接作業の候補の記録の名前の頭（directcand。`direct-<セッション>.json`）。
	DirectPrefix = "direct-"
	// ArchiveName は閉じた親子のチケットの退避の置き場の名前（place.archiveName。logs/ の下）。自己防衛が守る。
	ArchiveName = "archive"
	// WipRoot と HandoffName は下書きの置き場 wip/handoff の 2 段（place.WipRoot・place.Handoff）。
	WipRoot     = "wip"
	HandoffName = "handoff"
	// TodoDir は提案の置き場の下の、承認前の提案の置き場（place.Todo）。
	TodoDir = "todo"
)
