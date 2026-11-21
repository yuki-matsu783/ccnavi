// Package git は、git を包む sh と、git のブランチ・統合・ロックを扱う sh を外から起こして確かめるテスト（clitest/sh/git）。
//
// 使い捨ての git のリポジトリと、`.ccnavi/scripts/` の写しを持つワークスペースを毎回組む。読み返すのは標準出力・標準エラー・
// 終了コードと、git とファイルシステムに残ったものだけ（中の関数も変数も見ない）。このパッケージだけが使う道具は helpers_test.go にある。
package git
