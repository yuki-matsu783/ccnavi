// Package difflib は Python の difflib の写し。上の段がユーザに見せる差分を、Python と同じ行で作る。
// 照合（SequenceMatcher）は isjunk=None・autojunk=True の既定の形だけを持ち、外には出さない。
//
// Python からの対応: difflib.unified_diff(a, b, fromfile, tofile, n=n, lineterm="") → UnifiedDiff。
package difflib
