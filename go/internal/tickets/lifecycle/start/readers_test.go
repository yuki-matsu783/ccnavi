package start

import (
	"testing"

	"ccnavi/internal/infra/golden"
)

// readerCase は testdata/readers.json の 1 件。答え（out・code）は、ccnavi-start.sh が jq で読んでいたときの答えから取った。
type readerCase struct {
	Name   string   `json:"name"`
	Op     string   `json:"op"`
	Args   []string `json:"args"`
	Inputs []string `json:"inputs"`
	Out    string   `json:"out"`
	Code   int      `json:"code"`
}

func runReader(c readerCase) (string, int) {
	in := make([][]byte, len(c.Inputs))
	for i, s := range c.Inputs {
		in[i] = []byte(s)
	}
	arg := func(i int) string {
		if i < len(c.Args) {
			return c.Args[i]
		}
		return ""
	}
	switch c.Op {
	case "issue":
		return Issue(arg(0), in[0])
	case "linked-page":
		return LinkedPage(in[0])
	case "linked-body":
		return LinkedBody(arg(0), in[0])
	case "linked-gitlab":
		return LinkedGitLab(arg(0), in[0])
	case "similar":
		return Similar(arg(0), arg(1), arg(2), in)
	case "list-open":
		return ListOpen(arg(0), in[0], in[1])
	case "pick":
		return Pick(arg(0), arg(1), arg(2), in[0])
	}
	return "", -1
}

func TestReadersMatchTheShAnswers(t *testing.T) {
	for _, c := range golden.Load[[]readerCase](t, "readers") {
		t.Run(c.Op+"/"+c.Name, func(t *testing.T) {
			out, code := runReader(c)
			if out != c.Out || code != c.Code {
				t.Errorf("Go: %q（%d）\n見本: %q（%d）", out, code, c.Out, c.Code)
			}
		})
	}
}
