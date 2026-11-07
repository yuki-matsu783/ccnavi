// Package host は、レビューのホスト側の形を置く。sh（`.ccnavi/scripts/ccnavi-review.sh`）がホストから取ってきた JSON
// （`--result`）を読む形（Result）、機構自身の投稿に付ける目印、受け入れの計画、origin の URL から GitHub か GitLab かを
// 見分ける読み方を置く。ネットワークには出ない。どれも sh との契約である。値を変えるなら、設計書と sh を同時に直す。
package host
