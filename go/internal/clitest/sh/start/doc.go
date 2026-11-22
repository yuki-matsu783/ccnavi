// Package start は、ワークツリーを切る・親のブランチを取り込む・取ってくる・片付ける sh を、sh を起こして外から確かめるテストの置き場である。
//
// 判断と解釈（ホストの応答の読み方・止める文・識別子の決まり・引数の誤り）は Go の本体の表が見る。ここは経路ごとの通し
// （終了コード・出力・作られた物・ホストへの要求）と、sh の仕事（ホストとの出入り・段取りの順・打ち直し・片付け・時間の枠・ロック）だけを見る。
//
// ファイルの分担
//   - helpers_test.go: 書く・読む・git・写す・LF・sh の起こし方（このパッケージだけの道具）
//   - startenv_test.go: ccnavi-start.sh の土台（bare の origin・ワークスペース・ホストのスタブ・要求の記録）
//   - start_new_test.go・start_adopt_test.go: ccnavi-start.sh（新しく作る経路・事前確認・採用・GitLab）
//   - worktree_test.go: ccnavi-worktree.sh（直接作業のワークツリー）
//   - sync_test.go・sync_lock_test.go・sync_term_*_test.go: ccnavi-sync.sh（取り込みと、ロック。TERM で終わったときのロックは Unix だけ）
//   - fetch_test.go: ccnavi-fetch.sh
//   - clean_test.go・idle_test.go・sessionstart_test.go・workspaceroot_test.go: ccnavi-clean.sh・ccnavi-idle.sh・ccnavi-session-start.sh・ccnavi_workspace
//   - preflight_test.go: ccnavi-ticket.sh が打つ事前確認（承認済みの親の木は harness/ticketkit。ホストは harness/hoststub）
//   - credurl_test.go: hoststub の CredURL（その URL だけに GITLAB_TOKEN を返す credential helper）
package start
