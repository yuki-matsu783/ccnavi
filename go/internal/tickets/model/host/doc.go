// Package host はレビューのホスト側の形。sh（`.ccnavi/scripts/ccnavi-review.sh`）がホストから取ってきた
// JSON（`--result`）を読む形（Result）、機構自身の投稿に付ける目印、受け入れの計画、origin の URL から
// GitHub か GitLab かを見分ける読み方を置く。ネットワークには出ない。どれも sh との契約なので、
// 値を変えるなら設計書と sh を同時に直す。
//
// Python からの対応: review_host をこのパッケージにした。Result.load → LoadResult、Result.from_data → ResultOf、
// MARKER_ISSUE.format(op=…) → IssueMarker、_unresolved → Unresolved、epoch → Epoch（エポック秒）、
// classify → Classify（HostClass → Class）、remote_kind → Classify(url).Kind、thread_key・thread_version →
// ThreadKey・ThreadVersion、accept_* → Accept*、read・write_accept_plan → ReadAcceptPlan・WriteAcceptPlan。
// Thread.own と MergeRequest.draft の None は nil の *bool。
package host
