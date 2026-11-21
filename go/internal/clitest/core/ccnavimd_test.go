package core

import (
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// `--lint` が `.ccnavi/CCNAVI.md` の条件と差し込みの誤りを言うことの受入テスト。
// 条件と差し込みの解き方そのものは go/internal/hook/ccnavimd の go test が見る。

func TestLintSaysWhatIsWrongWithCCNaviMD(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name     string
		text     *string // nil は CCNAVI.md が無い
		contains []string
		absent   []string
	}{
		{"無ければwarnで言う", nil, []string{"(ccnavi-md)"}, nil},
		// 行番号つきで warn に出る。
		{"誤りは行番号つきでwarnに出る", ptr("<!-- ccnavi:if nothing -->\n"), []string{"(ccnavi-md)", "1 行目: 条件 nothing は指定できない"}, nil},
		{"名前に_以外の記号があればerrorで言う", ptr("<!-- ccnavi:if has-docker -->\n<!-- ccnavi:endif -->\n"), []string{"(ccnavi-md)", "条件の名前 has-docker は指定できない", "error"}, nil},
		{"正しければ言わない", ptr("# タイトル\n"), nil, []string{"(ccnavi-md)"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			root := emptyRoot(t)
			mkdir(t, root+"/.ccnavi")
			if c.text != nil {
				writeFile(t, root, ".ccnavi/CCNAVI.md", *c.text)
			}
			out := run.Ccnavi(t, []string{"--lint", "--root", root}, run.Opts{Dir: base.Root()}).Stdout
			mustContain(t, "lint", strings.ToLower(out), toLower(c.contains)...)
			mustNotContain(t, "lint", out, c.absent...)
		})
	}
	t.Run(".ccnavi ディレクトリが無いワークスペースは言わない", func(t *testing.T) {
		t.Parallel()
		out := run.Ccnavi(t, []string{"--lint", "--root", emptyRoot(t)}, run.Opts{Dir: base.Root(), NoMessages: true}).Stdout
		mustNotContain(t, "lint", out, "(ccnavi-md)")
	})
}

func toLower(in []string) []string {
	out := make([]string, len(in))
	for i, s := range in {
		out[i] = strings.ToLower(s)
	}
	return out
}
