// Package fakegitlab は GitLab API v4 の模擬サーバである。
//
// `.ccnavi/scripts/` の sh の HTTP の経路（curl・jq での組み立て・ページング・投稿・認証）を、実物の GitLab（4GB 要る）なしで
// 走らせるための的。分かるのは sh が動くことだけで、返す形が実物と同じかどうかは契約テストが見る。
//
// 状態は JSON ファイル 1 つ（最初は {"project": "demo/greeter", "seq": 100, "issues": [], "mrs": []}）。要求のたびにロックして
// 読み、書き戻す（受入シナリオが別プロセスで起こし直しても、続きから動く）。時計は既定で 3 分進めてある（Skew。実物と手元の時計は
// 揃わないので、揃っている前提の実装がすぐ動かなくなるのを防ぐ）。トークンは Token。
//
// テストからは Start（t を取り、置き場を作って、終わりに止める）。手で使う入口や別の道具からは New（t を取らず、ポートを選べる）。
//
//	srv := fakegitlab.Start(t, fakegitlab.State{"project": "ns/proj", "seq": 100, "issues": []any{}, "mrs": []any{}})
//	srv.URL        // http://127.0.0.1:<port>
//	srv.Token      // GITLAB_TOKEN に置く値
//	srv.Requests() // 受けた要求（"GET /api/v4/..."）
package fakegitlab
