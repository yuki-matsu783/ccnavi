// Package gitlabapi は、GitLab API v4 を人間役のトークンで直に呼ぶ部品である（tests/manual/gitlab/gitlab_api.py の写し）。
//
// 手動の GitLab の道具（probe・契約の録り・Docker の操作）と、受入シナリオの相手の準備・step の終わりの相手側の状態の読み取りが共有する。
// sh と同じ道具（curl・glab）を使わないのは、片方の不具合がもう片方に隠れないようにするため。
//
// 環境変数は読まず、接続先は NewClient の引数、トークンは各メソッドの引数で受ける（Docker の GitLab の接続先と
// トークンのファイルの場所だけは docker.go にまとめてある）。HTTP の 4xx・5xx は error にせず、状態の数字で返す。
// 接続の失敗は状態 0 と {"error": …} の本文で返す。GET だけは失敗したら RetryWait おいて Attempts 回まで試す
// （書き込みは送り直すと二重に作られるので 1 回で止める）。
//
// 本文は encoding/json で読んだ値（map[string]any・[]any・string・json.Number・bool・nil）で、AsMap・AsList・Int・Text で取り出す。
package gitlabapi
