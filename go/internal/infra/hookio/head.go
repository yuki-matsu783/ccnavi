package hookio

// 文の頭。ccnavi がエージェントとユーザに渡す文（拒否・促し・知らせ）は Head で始め、ほかの道具の出力と見分けられるようにする。
const (
	// Head は文の頭。
	Head = "[ccnavi] "
	// SubjectPrefix は、拒否の文で何にヒットしたかを言う行の頭。
	SubjectPrefix = "subject: "
)

// Tagged は理由コードと添え書きの頭の行（`[ccnavi] CODE (添え書き)`）。
func Tagged(code, detail string) string { return Head + code + " (" + detail + ")" }

// Cited は出所を添えた頭の行（`[ccnavi] CODE (source: 出所)`）。
func Cited(code, source string) string { return Tagged(code, "source: "+source) }
