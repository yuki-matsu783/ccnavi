package handoff

import (
	"sort"
	"strconv"
	"strings"

	"golang.org/x/text/unicode/norm"

	"ccnavi/internal/tickets/proposal/templates"
)

// diffContext は差分に添える前後の行数。
const diffContext = 3

// noEOL は差分で、末尾に改行が無いことを言う行。
const noEOL = "\\ 末尾に改行がありません"

const (
	msgInvisible = "差分が見えないのに、バイト列が取り込み先と違う。見えない違いは取り込まない"
	devNull      = "/dev/null"
)

// visibleLines は差分に出す行。改行コード・BOM・制御文字・ゼロ幅の文字・結合文字は \uXXXX にする。
// 末尾に改行が無ければ、そう言う行を足す（改行だけの違いも差分に出す）。
func visibleLines(raw []byte) []string {
	if raw == nil {
		return nil
	}
	lines := strings.Split(decodeReplace(raw, false), "\n")
	tail := lines[len(lines)-1]
	lines = lines[:len(lines)-1]
	out := make([]string, 0, len(lines)+2)
	for _, l := range lines {
		out = append(out, visible(l))
	}
	if tail != "" {
		out = append(out, visible(tail), noEOL)
	}
	return out
}

func visible(text string) string {
	var b strings.Builder
	for _, r := range text {
		if len(templates.HiddenChars(string(r))) > 0 || combining(r) {
			b.WriteString(templates.EscapeChar(r))
		} else {
			b.WriteRune(r)
		}
	}
	return b.String()
}

// combining は unicodedata.combining(ch) != 0（正準結合クラスが 0 でない）。
func combining(r rune) bool {
	return norm.NFD.PropertiesString(string(r)).CCC() != 0
}

func makeDiff(e *Entry) {
	if e.raw == nil || same(e.current, e.raw) {
		return
	}
	from := devNull
	if e.current != nil {
		from = "a/" + e.Rel
	}
	lines := unifiedDiff(visibleLines(e.current), visibleLines(e.raw), from, "b/"+e.Rel, diffContext)
	if len(lines) == 0 {
		e.Errors = append(e.Errors, msgInvisible)
		return
	}
	e.Diff = strings.Join(lines, "\n")
}

// ---- difflib.unified_diff(a, b, fromfile, tofile, n=n, lineterm="") の写し（SequenceMatcher は isjunk=None・autojunk=True）

type opcode struct {
	tag            byte // 'e' equal・'r' replace・'d' delete・'i' insert
	i1, i2, j1, j2 int
}

type match struct{ a, b, size int }

// autojunkMin は、b がこの行数以上のときに、よく出る行を照合の手がかりから外す（difflib の autojunk）。
const autojunkMin = 200

type matcher struct {
	a, b []string
	b2j  map[string][]int
}

func newMatcher(a, b []string) *matcher {
	m := &matcher{a: a, b: b, b2j: map[string][]int{}}
	for i, elt := range b {
		m.b2j[elt] = append(m.b2j[elt], i)
	}
	if n := len(b); n >= autojunkMin {
		ntest := n/100 + 1
		for elt, idxs := range m.b2j {
			if len(idxs) > ntest {
				delete(m.b2j, elt)
			}
		}
	}
	return m
}

func (m *matcher) longest(alo, ahi, blo, bhi int) match {
	besti, bestj, bestsize := alo, blo, 0
	j2len := map[int]int{}
	for i := alo; i < ahi; i++ {
		next := map[int]int{}
		for _, j := range m.b2j[m.a[i]] {
			if j < blo {
				continue
			}
			if j >= bhi {
				break
			}
			k := j2len[j-1] + 1
			next[j] = k
			if k > bestsize {
				besti, bestj, bestsize = i-k+1, j-k+1, k
			}
		}
		j2len = next
	}
	for besti > alo && bestj > blo && m.a[besti-1] == m.b[bestj-1] {
		besti, bestj, bestsize = besti-1, bestj-1, bestsize+1
	}
	for besti+bestsize < ahi && bestj+bestsize < bhi && m.a[besti+bestsize] == m.b[bestj+bestsize] {
		bestsize++
	}
	return match{besti, bestj, bestsize}
}

func (m *matcher) blocks() []match {
	la, lb := len(m.a), len(m.b)
	queue := [][4]int{{0, la, 0, lb}}
	var found []match
	for len(queue) > 0 {
		q := queue[len(queue)-1]
		queue = queue[:len(queue)-1]
		alo, ahi, blo, bhi := q[0], q[1], q[2], q[3]
		x := m.longest(alo, ahi, blo, bhi)
		if x.size == 0 {
			continue
		}
		found = append(found, x)
		if alo < x.a && blo < x.b {
			queue = append(queue, [4]int{alo, x.a, blo, x.b})
		}
		if x.a+x.size < ahi && x.b+x.size < bhi {
			queue = append(queue, [4]int{x.a + x.size, ahi, x.b + x.size, bhi})
		}
	}
	sort.Slice(found, func(i, j int) bool {
		if found[i].a != found[j].a {
			return found[i].a < found[j].a
		}
		if found[i].b != found[j].b {
			return found[i].b < found[j].b
		}
		return found[i].size < found[j].size
	})
	var out []match
	cur := match{}
	for _, x := range found {
		if cur.a+cur.size == x.a && cur.b+cur.size == x.b {
			cur.size += x.size
			continue
		}
		if cur.size > 0 {
			out = append(out, cur)
		}
		cur = x
	}
	if cur.size > 0 {
		out = append(out, cur)
	}
	return append(out, match{la, lb, 0})
}

func (m *matcher) opcodes() []opcode {
	i, j := 0, 0
	var out []opcode
	for _, x := range m.blocks() {
		var tag byte
		switch {
		case i < x.a && j < x.b:
			tag = 'r'
		case i < x.a:
			tag = 'd'
		case j < x.b:
			tag = 'i'
		}
		if tag != 0 {
			out = append(out, opcode{tag, i, x.a, j, x.b})
		}
		i, j = x.a+x.size, x.b+x.size
		if x.size > 0 {
			out = append(out, opcode{'e', x.a, i, x.b, j})
		}
	}
	return out
}

func (m *matcher) grouped(n int) [][]opcode {
	codes := m.opcodes()
	if len(codes) == 0 {
		codes = []opcode{{'e', 0, 1, 0, 1}}
	}
	if c := &codes[0]; c.tag == 'e' {
		c.i1, c.j1 = max(c.i1, c.i2-n), max(c.j1, c.j2-n)
	}
	if c := &codes[len(codes)-1]; c.tag == 'e' {
		c.i2, c.j2 = min(c.i2, c.i1+n), min(c.j2, c.j1+n)
	}
	var groups [][]opcode
	var group []opcode
	for _, c := range codes {
		if c.tag == 'e' && c.i2-c.i1 > 2*n {
			group = append(group, opcode{'e', c.i1, min(c.i2, c.i1+n), c.j1, min(c.j2, c.j1+n)})
			groups = append(groups, group)
			group = nil
			c.i1, c.j1 = max(c.i1, c.i2-n), max(c.j1, c.j2-n)
		}
		group = append(group, c)
	}
	if len(group) > 0 && !(len(group) == 1 && group[0].tag == 'e') {
		groups = append(groups, group)
	}
	return groups
}

func unifiedRange(start, stop int) string {
	beginning, length := start+1, stop-start
	if length == 1 {
		return strconv.Itoa(beginning)
	}
	if length == 0 {
		beginning--
	}
	return strconv.Itoa(beginning) + "," + strconv.Itoa(length)
}

func unifiedDiff(a, b []string, fromFile, toFile string, n int) []string {
	var out []string
	for _, group := range newMatcher(a, b).grouped(n) {
		if out == nil {
			out = append(out, "--- "+fromFile, "+++ "+toFile)
		}
		first, last := group[0], group[len(group)-1]
		out = append(out, "@@ -"+unifiedRange(first.i1, last.i2)+" +"+unifiedRange(first.j1, last.j2)+" @@")
		for _, c := range group {
			if c.tag == 'e' {
				for _, l := range a[c.i1:c.i2] {
					out = append(out, " "+l)
				}
				continue
			}
			if c.tag == 'r' || c.tag == 'd' {
				for _, l := range a[c.i1:c.i2] {
					out = append(out, "-"+l)
				}
			}
			if c.tag == 'r' || c.tag == 'i' {
				for _, l := range b[c.j1:c.j2] {
					out = append(out, "+"+l)
				}
			}
		}
	}
	return out
}
