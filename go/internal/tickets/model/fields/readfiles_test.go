package fields

import (
	"strings"
	"testing"
)

const readFilesTicket = "---\nid: p0001-1-01\ncompleted_at: \"\"\n---\n\n# 子\n\n本文\n"

// 別のセッションのぶんは後ろに足し、同じセッションのぶんは置き換える。
func TestSetReadFilesAppendsPerSession(t *testing.T) {
	once := SetReadFiles(readFilesTicket, "s1", []string{"`a.go`（Read 1）"})
	want := readFilesTicket + "\n<!-- ccnavi:read-files -->\n## 参照したファイル\n\n### セッション `s1`\n\n- `a.go`（Read 1）\n"
	if once != want {
		t.Fatalf("got %q, want %q", once, want)
	}
	if !HasReadFiles(once) || HasReadFiles(readFilesTicket) {
		t.Fatal("HasReadFiles")
	}
	twice := SetReadFiles(SetReadFiles(once, "s2", []string{"`b.go`（Bash 2）"}), "s1", []string{"`c.go`（Read 1）"})
	want = readFilesTicket + "\n<!-- ccnavi:read-files -->\n## 参照したファイル\n\n### セッション `s1`\n\n- `c.go`（Read 1）\n" +
		"\n### セッション `s2`\n\n- `b.go`（Bash 2）\n"
	if twice != want {
		t.Fatalf("got %q, want %q", twice, want)
	}
	if got := SetReadFiles(readFilesTicket, "s1", nil); got != readFilesTicket {
		t.Fatalf("empty items: got %q", got)
	}
}

// 照合で除くと、節を足す前と同じ形になる。欄の除き方と一緒に使える。
func TestScriptShapeDropsReadFiles(t *testing.T) {
	written := SetFields(SetReadFiles(readFilesTicket, "s1", []string{"`a.go`（Read 1）"}), [][2]string{{"completed_at", "2026-11-20"}})
	drop := []string{"completed_at", ReadFilesSection}
	before, _ := ScriptShape(readFilesTicket, drop)
	after, _ := ScriptShape(written, drop)
	if before != after {
		t.Fatalf("before %q, after %q", before, after)
	}
	if kept, _ := ScriptShape(written, []string{"completed_at"}); kept == before {
		t.Fatal("drop に無いときは節を残す")
	}
}

// 印の後ろに決まった形でない行があるときは節と認めず、除かない（本文の書き換えを隠さない）。
func TestMalformedReadFilesIsNotDropped(t *testing.T) {
	for name, tail := range map[string]string{
		"本文が続く":     "\n<!-- ccnavi:read-files -->\n## 参照したファイル\n\n### セッション `s1`\n\n- `a.go`\n\n書き換えた本文\n",
		"見出しが無い":    "\n<!-- ccnavi:read-files -->\n### セッション `s1`\n- `a.go`\n",
		"小見出しの前の項目": "\n<!-- ccnavi:read-files -->\n## 参照したファイル\n- `a.go`（Read 1）\n",
		"任意の文の項目":   "\n<!-- ccnavi:read-files -->\n## 参照したファイル\n\n### セッション `s1`\n\n- [x] 受入条件を満たした\n",
		"セッション名に空白": "\n<!-- ccnavi:read-files -->\n## 参照したファイル\n\n### セッション `次は push`\n\n- `a.go`（Read 1）\n",
	} {
		text := readFilesTicket + tail
		if HasReadFiles(text) || strings.Contains(strings.Join(ScriptFieldsSet(text), ","), ReadFilesSection) {
			t.Errorf("%s: 節と認めた", name)
		}
		before, _ := ScriptShape(readFilesTicket, []string{ReadFilesSection})
		if after, _ := ScriptShape(text, []string{ReadFilesSection}); after == before {
			t.Errorf("%s: 除いた", name)
		}
	}
}
