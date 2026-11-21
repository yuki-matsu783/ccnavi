// Package review は、レビューの sh（ccnavi-review.sh・ccnavi-user-decide.sh・ccnavi-user-lib-review.sh と、共通部の
// ccnavi-common-review.sh）を外から起こして確かめるテストである（tests/sh/test_review_*.py・test_decide_*.py・
// test_comment_staged.py の移し）。
//
// 見るのは sh の仕事だけ。ホストの応答の読み（マージ済みか・MR の状態・未解決の数え方）は実行ファイル側にあり、
// go/internal/tickets/model/host の表が見る。
//
// 置き場
//   - kit_test.go: 使い捨てのワークスペース（site）、実行ファイルの代わりの sh（writeStub）、PATH を絞る道具、操作記録つきの計画（writePlan・keepPlan）
//   - hostsite_test.go・hostfixture_test.go・gitlabhost_test.go: 録ったホストの応答の見本（hostfixture）から sh が組む JSON と、
//     マーカーのアカウント（actor）、review-request の組。`-update-host-fixture` で見本の expected.json を書き直す
//   - restfallback_test.go・hint_test.go: GraphQL が 403 の環境で GitHub の 4 か所が REST に替わること、失敗の案内
//   - decide_kit_test.go・decide_test.go・decide_ops_test.go・decide_recovery_test.go: ユーザ専用の decide（投稿・復旧・照合）。
//     ホストは httptest の小さな GitLab、実行ファイルは代わりの sh（`ops` は本物へ渡す）
//   - mrstate_test.go・merged_test.go・origin_test.go・rootcwd_test.go・comment_staged_test.go: 副命令ごとの約束
//
// 試験用の道具は、このパッケージだけが使うのでここに置く（共有が要るようになったら harness へ上げる）。
package review
