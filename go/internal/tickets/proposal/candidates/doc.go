// Package candidates は、承認の候補を組み、その検査を受け持つ。提案を承認の対象に入れるものは Candidate、外すものは
// Rejected に分ける。計画・コピー（phases:）・改版・欄・ブランチの食い違いは、ここで並べる。承認（--agree）・見せる
// （preview）・確かめる（verify）・--lint は、同じ Candidates を通る。外れるものを数える経路が 2 本あると、片方だけが
// 気づかないうちに弱くなるため。
//
// 子の定義は親のコピーから引き、phases.yml からは引かない。承認のあとに phases.yml を直しても、進行中の親の子の承認が
// 落ちないようにするため。phases.yml は、コピーが同じかを確かめるためだけに読む。
package candidates
