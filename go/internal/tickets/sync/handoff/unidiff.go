package handoff

import "strconv"

// unifiedDiff は a から b への unified diff の行（ヘッダ 2 行と、前後 context 行つきの塊）を返す。違いが無ければ nil。
// 行単位の最長共通部分列（LCS）で突き合わせる。取り込む下書きは小さいので、素朴な O(行数の積) の表で足りる。
func unifiedDiff(a, b []string, from, to string, context int) []string {
	type op struct {
		tag byte // ' ' 同じ・'-' 消えた・'+' 足された
		i   int  // ' ' '-' では a の位置。'+' では b の位置
	}
	// 前後の同じ行は表に入れない。
	pre := 0
	for pre < len(a) && pre < len(b) && a[pre] == b[pre] {
		pre++
	}
	suf := 0
	for suf < len(a)-pre && suf < len(b)-pre && a[len(a)-1-suf] == b[len(b)-1-suf] {
		suf++
	}
	ma, mb := a[pre:len(a)-suf], b[pre:len(b)-suf]
	if len(ma) == 0 && len(mb) == 0 {
		return nil
	}
	// lcs[i][j] は ma[i:] と mb[j:] の共通部分列の長さ。
	lcs := make([][]int, len(ma)+1)
	for i := range lcs {
		lcs[i] = make([]int, len(mb)+1)
	}
	for i := len(ma) - 1; i >= 0; i-- {
		for j := len(mb) - 1; j >= 0; j-- {
			if ma[i] == mb[j] {
				lcs[i][j] = lcs[i+1][j+1] + 1
			} else {
				lcs[i][j] = max(lcs[i+1][j], lcs[i][j+1])
			}
		}
	}
	var ops []op
	for i := 0; i < pre; i++ {
		ops = append(ops, op{' ', i})
	}
	i, j := 0, 0
	for i < len(ma) || j < len(mb) {
		switch {
		case i < len(ma) && j < len(mb) && ma[i] == mb[j]:
			ops = append(ops, op{' ', pre + i})
			i++
			j++
		case i < len(ma) && (j == len(mb) || lcs[i+1][j] >= lcs[i][j+1]):
			ops = append(ops, op{'-', pre + i})
			i++
		default:
			ops = append(ops, op{'+', pre + j})
			j++
		}
	}
	for k := 0; k < suf; k++ {
		ops = append(ops, op{' ', len(a) - suf + k})
	}

	out := []string{"--- " + from, "+++ " + to}
	n := len(ops)
	for s := 0; s < n; {
		// 次の変更を探し、その手前の context 行から塊を始める。
		for s < n && ops[s].tag == ' ' {
			s++
		}
		if s == n {
			break
		}
		start := max(s-context, 0)
		// 変更の間の同じ行が 2*context 以下なら同じ塊に続ける。
		end, run := s, 0
		for k := s; k < n; k++ {
			if ops[k].tag == ' ' {
				run++
				if run > 2*context {
					break
				}
			} else {
				run = 0
				end = k
			}
		}
		end = min(end+context+1, n)
		// 塊の a・b それぞれの開始位置と行数。
		ai, bi := 0, 0
		for _, o := range ops[:start] {
			if o.tag != '+' {
				ai++
			}
			if o.tag != '-' {
				bi++
			}
		}
		na, nb := 0, 0
		var body []string
		for _, o := range ops[start:end] {
			switch o.tag {
			case ' ':
				na++
				nb++
				body = append(body, " "+a[o.i])
			case '-':
				na++
				body = append(body, "-"+a[o.i])
			default:
				nb++
				body = append(body, "+"+b[o.i])
			}
		}
		out = append(out, "@@ -"+hunkRange(ai, na)+" +"+hunkRange(bi, nb)+" @@")
		out = append(out, body...)
		s = end
	}
	return out
}

// hunkRange は unified diff の範囲（開始行,行数）。行数が 1 なら開始行だけ、0 なら直前の行を指す。
func hunkRange(start, n int) string {
	switch n {
	case 1:
		return strconv.Itoa(start + 1)
	case 0:
		return strconv.Itoa(start) + ",0"
	}
	return strconv.Itoa(start+1) + "," + strconv.Itoa(n)
}
