// Package difflib は Python の difflib と同じ行の差分を作る。上の段がユーザに見せる差分の行を変えないための約束である。
// 照合（SequenceMatcher）は isjunk=None・autojunk=True の既定の形だけを持ち、外には出さない。
// 公開するのは unified diff を作る UnifiedDiff だけである（fromfile・tofile・文脈の行数 n を受け、lineterm は空）。
package difflib
