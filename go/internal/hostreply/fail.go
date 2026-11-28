package hostreply

import (
	"regexp"
	"strings"
)

// 失敗の返事（curl・gh・glab の標準出力と標準エラーを繋いだもの）から HTTP の番号を取る式。
// 本文の中の数字に引かれないよう、まず形の決まった書き方（curl の returned error・gh の (HTTP nnn)・glab の行）だけを
// 全部の行から探し、無ければ最後の空でない行（gh / glab の標準エラー）だけを緩い式で読む。
// 先頭の `.*` は貪欲なので、1 行に 2 つあれば後ろのものを取る（sed の式と同じ）。
var (
	statusStrict = []*regexp.Regexp{
		regexp.MustCompile(`^.*returned error: ([0-9]{3}).*$`),
		regexp.MustCompile(`^.*\(HTTP ([0-9]{3})\).*$`),
		regexp.MustCompile(`^glab: HTTP ([0-9]{3})$`),
		regexp.MustCompile(`^[^0-9]*([0-9]{3}) (?:Not Found|Forbidden|Unauthorized|Gone).*$`),
	}
	statusLoose = []*regexp.Regexp{
		regexp.MustCompile(`^.*HTTP ([0-9]{3}).*$`),
		regexp.MustCompile(`^[^0-9]*([0-9]{3}) [A-Z][a-z]+.*$`),
	}
)

// FailStatus は失敗の返事から HTTP の番号を取り出す。取れなければ空。
func FailStatus(text string) string {
	lines := strings.Split(text, "\n")
	for _, line := range lines {
		if got := firstGroup(statusStrict, line); got != "" {
			return got
		}
	}
	for i := len(lines) - 1; i >= 0; i-- {
		if lines[i] != "" {
			return firstGroup(statusLoose, lines[i])
		}
	}
	return ""
}

func firstGroup(forms []*regexp.Regexp, line string) string {
	for _, re := range forms {
		if m := re.FindStringSubmatch(line); m != nil {
			return m[1]
		}
	}
	return ""
}

// GraphQLBlocked は、GraphQL を使えない実行環境（Claude Code のクラウド）で出る 403 か。
// gh は本文を返すので「GraphQL is not available」の文言で見分ける（別の理由の 403・401 は偽）。curl は -f が本文を捨て、
// 403 の番号だけが残る。番号だけの 403 は認証の失敗と見分けが付かないので真とする。呼び手は REST の道（/ccr/）を試し、
// それも失敗したら元の GraphQL の失敗を言う（通常の GitHub では /ccr/ は 404 になるだけ）。
func GraphQLBlocked(text string) bool {
	if strings.Contains(text, "GraphQL is not available") {
		return true
	}
	return FailStatus(text) == "403" && strings.Contains(text, "returned error: 403")
}

// bodyLimit は画面に出す本文の長さ（文字）。
const bodyLimit = 200

// FailBody は失敗の返事の本文を、画面に出せる形（1 行・制御文字なし・先頭 200 文字・secrets は *** に伏せる）にする。
// 制御文字の並び（U+0000〜U+001F と U+007F）は空白 1 つにする。
func FailBody(text string, secrets []string) string {
	text = strings.ToValidUTF8(text, "�")
	for _, s := range secrets {
		if s != "" {
			text = strings.ReplaceAll(text, s, "***")
		}
	}
	var b strings.Builder
	inRun := false
	for _, r := range text {
		if r <= 0x1f || r == 0x7f {
			if !inRun {
				b.WriteByte(' ')
			}
			inRun = true
			continue
		}
		inRun = false
		b.WriteRune(r)
	}
	return Head(b.String(), bodyLimit)
}

// Probe はプロジェクトを 1 回読んだ結果の分類。failed なら data は失敗の返事で、401・403 は auth、404 は notfound、
// ほかは network（通信の失敗・時間切れ・5xx）。成功なら data は応答で、JSON の表なら ok、そうでなければ badjson。
func Probe(failed bool, data []byte) string {
	if failed {
		switch FailStatus(string(data)) {
		case "401", "403":
			return "auth"
		case "404":
			return "notfound"
		}
		return "network"
	}
	if v, err := Decode(data); err == nil {
		if _, ok := v.(map[string]any); ok {
			return "ok"
		}
	}
	return "badjson"
}
