// Package runner は、受入シナリオを回す流れとサブコマンドの中身である（tests/scenarios/drive.py・commands.py・runner.py の写し）。
// 手順と決まりは .agents/docs/acceptance-scenarios.md。普段のテストには含めない（スタブで回す分だけ go test に入る）。
//
//	go run ./internal/harness/cmd/scenarios run <シナリオ> [--confirmed] [--agent script]
//	go run ./internal/harness/cmd/scenarios run-stub <ディレクトリ or glob>        台本のあるシナリオを使い捨てのワークスペースでスタブ（teststub claude）で回す
//	go run ./internal/harness/cmd/scenarios run-all <...> --max-total-usd <USD> [--confirmed <目印>]
//	go run ./internal/harness/cmd/scenarios preflight|next|restore|judge ...
//
// 終了コード: 0 全部通った / 1 不合格がある・setup のパスが合わず回せない / 2 未成立か意味の判定待ちがある /
// 3 人間の確認が要る・回す前の拒否・使い捨てのリポジトリを戻せなかった（手作業が要る）/
// 10 人間の操作で止まった（「次へ」のあと next で続ける。setup.reset の対象と setup.host の置き場は残し、模擬だけ止める）。
//
// 本物の claude を呼ぶのは Runner.Entry が真のとき（入口の実行ファイル cmd/scenarios だけが立てる）。テストが立てても、
// 差し替え口（Runner.Step）に台本を入れるか、スタブ（teststub claude）で回す。
//
// 層（import は同じ層か下へだけ。layers_test の subTiers）: host・core・prepare・judge・world・actors・report・runner。
package runner
