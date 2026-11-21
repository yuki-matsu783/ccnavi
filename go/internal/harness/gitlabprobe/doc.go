// Package gitlabprobe は、実物の GitLab に sh 5 本と実行ファイルを適用して 1 周する（消した tests/manual/gitlab/probe_gitlab.py の写し）。
// ユーザが手で走らせる道具で、自動テストは本物の GitLab を立てない（組み立ての純粋な部分と引数の扱いだけを見る）。
//
//	gitlabmanual probe
//
// 一時リポジトリを作り、使い捨てのプロジェクトを GitLab に作って、`ccnavi-ticket.sh`・`ccnavi-git.sh`・`ccnavi-review.sh`・
// `ccnavi-user-push-approved.sh`・`ccnavi-user-close-early.sh`・`ccnavi-user-decide.sh` を実物に通す。人間役（レビュアー）は
// 別ユーザのトークンで API を直に呼ぶ（gitlabapi）。sh と同じ道具を使わないのは、片方の不具合がもう片方に隠れないようにするため。
//
// 用意するもの
//   - 動いている GitLab（既定 `http://127.0.0.1:8929`。CCNAVI_PROBE_GITLAB で変える）。`gitlabmanual up` で立てる
//     （手順は .agents/docs/gitlab-manual.md）
//   - トークン 2 本（GITLAB_TOKEN（root、エージェント役）と CCNAVI_PROBE_REVIEWER_TOKEN（人間役））。`up` が 0600 のファイルに書く
//   - 組み立て済みの実行ファイル（`dist/ccnavi/ccnavi`。CCNAVI_ENTRY_PATH で差し替えられる）、jq と curl（sh が使う）
//
// push は URL にトークンを埋めない（埋めると origin の URL に混ざる）。一時リポジトリの credential.helper を空文字で一度リセットして
// （system・global の GCM を外す）から、環境変数 GITLAB_TOKEN を返す helper を足す。
//
// 結果と生の JSON は CCNAVI_PROBE_OUT（既定は一時ディレクトリ）に残る。変更要求（request_changes）は GitLab EE の機能で、CE では 404
// になる。CE ではその段階を SKIP とする（確かめていないものを PASS に数えない）。まとめは `PASS n / SKIP m / FAIL k`。
// FAIL があれば終了コードは 1。`--help` は説明を出して終わる（トークンも実行ファイルも要らない）。
//
// 組み立ての純粋な部分は ProjectName・SummaryLine・ApprovedCopyPath・FeedbackRevision・IsCancelledCopy・TicketText。
package gitlabprobe
