// Package host は、受入シナリオの `setup.host`（評価の相手）を用意して片付け、step の終わりに相手側の状態を読んで記録する
// 決まりは .agents/docs/acceptance-scenarios.md。
//
//   - bare: 対象の origin への通信を、一時ディレクトリの bare に向ける。git のスタブが URL をホストの形で答える。
//     ホストの API は gh・glab が失敗し、curl は空の routes で 404（「確かめられなかった」）
//   - fake-gitlab: bare に加え、fakegitlab の模擬を起こす（curl のスタブは置かない）
//   - docker-gitlab: Docker の本物の GitLab に使い捨てのプロジェクトを作り、main を push する。回したあとに消す
//   - real: 何もしない
//
// 起こした模擬は Manager が持つ（プロセス内の表。置き場の dir がキー）。状態は Host（JSON にできる。state.json に残す）。
// 子のプロセスを起こすのは、呼び手が渡す GitFunc・BuildStub で、このパッケージは testing に依存しない
// （受入シナリオの入口の実行ファイルからも使う）。
//
// 簡素にした所: 止めた模擬の再開は同じポートだけを試し、取れなければ止まる。記録（Take）の失敗はエラーで返し、
// 呼び手が step を止めて後片付けする（API が個々に失敗したことは、記録の errors に残して続ける）。
package host
