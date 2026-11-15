// Package refusal は、git がコミット・fast-forward・merge を断った理由を 1 文にする。integrate-done（land）と、sh が
// `ccnavi git refusal <ファイル>` で聞く経路（write-and-push・ccnavi-sync.sh・ccnavi-fetch.sh・
// ccnavi-user-push-approved.sh）が同じ読み方をするよう、ここに 1 つだけ置く。git は起こさず、渡された出力だけを読む。
package refusal
