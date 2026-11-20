package pyre

// startFilter は、式（か、その一部の並び）がどの位置から当たりうるかの見積もり。当たりうる位置を必ず含む
// （外れと言った位置からは決して当たらない）ので、外れの位置を飛ばしても答えは変わらない。照合の前に開始位置を
// 絞り（Python の sre が INFO の接頭辞と文字の集まりで開始位置を飛ばすのと同じ考え方）、1 字の繰り返しの後戻りで
// 続きの試しを絞る（sre の REPEAT_ONE が、続きの 1 字が合う位置でだけ続きを試すのと同じ考え方）。
type startFilter struct {
	// any は、位置を絞れない（空に当たりうる・後方参照で始まる）こと。
	any bool
	// atStart は文字列の先頭で、lineStart は行の頭（先頭か、改行の直後）で当たりうること。
	atStart, lineStart bool
	// tests は、その位置の字がどれかに当たれば当たりうる、1 字の照合。latin1 は U+00FF までの字の答えの表。
	tests  []func(rune) bool
	latin1 charTable
}

// charTable は U+00FF までの字の答えの表。
type charTable [4]uint64

func (t *charTable) has(r rune) bool { return t[r>>6]&(1<<(r&63)) != 0 }

// tableOf は f の U+00FF までの答えを表にする。
func tableOf(f func(rune) bool) charTable {
	var t charTable
	for r := range rune(latin1End) {
		if f(r) {
			t[r>>6] |= 1 << (r & 63)
		}
	}
	return t
}

// latin1End は charTable が持つ範囲の終わり。
const latin1End = 0x100

// ok は位置 i から当たりうるか。nil は絞らない（どこからでも当たりうる）。
func (f *startFilter) ok(s []rune, i int) bool {
	if f == nil || f.any || f.atStart && i == 0 || f.lineStart && (i == 0 || s[i-1] == '\n') {
		return true
	}
	if i >= len(s) {
		return false
	}
	r := s[i]
	if r < latin1End {
		return f.latin1.has(r)
	}
	for _, t := range f.tests {
		if t(r) {
			return true
		}
	}
	return false
}

// filterOf は並び prog がどの位置から当たりうるかを見積もる。
func filterOf(prog []*inst) startFilter {
	var f startFilter
	if f.add(prog) {
		f.any = true
	}
	if !f.any && len(f.tests) > 0 {
		f.latin1 = tableOf(func(r rune) bool {
			for _, t := range f.tests {
				if t(r) {
					return true
				}
			}
			return false
		})
	}
	return f
}

// add は並び prog の始まりに来うる字と位置を f に足し、prog が字を読まずに通り抜けうるか（空に当たりうるか）を返す。
// 幅のない判定（\b・$・先読み・後読み）は、続きと同じ位置を見るので通り抜けとして扱う。
func (f *startFilter) add(prog []*inst) bool {
	for _, n := range prog {
		if n.test != nil {
			f.tests = append(f.tests, n.test)
			return false
		}
		switch n.op {
		case opAt:
			switch {
			case n.at == atBeginningString, n.at == atBeginning && !n.multi:
				f.atStart = true
				return false
			case n.at == atBeginning:
				f.lineStart = true
				return false
			}
		case opBranch:
			through := false
			for _, alt := range n.alts {
				if f.add(alt) {
					through = true
				}
			}
			if !through {
				return false
			}
		case opGroup, opAtomic:
			if !f.add(n.sub) {
				return false
			}
		case opRepeat:
			through := true
			if n.simple != nil {
				f.tests = append(f.tests, n.simple)
				through = false
			} else {
				through = f.add(n.sub)
			}
			if n.min > 0 && !through {
				return false
			}
		case opAssert, opAssertNot:
		case opGroupExists:
			yes, no := f.add(n.yes), f.add(n.no)
			if !yes && !no {
				return false
			}
		default: // 後方参照は、捕えた字が分からず、空にも当たりうる
			f.any = true
			return false
		}
	}
	return true
}
