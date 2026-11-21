// Package git は、git を包む sh と、git のブランチ・統合・ロックを扱う sh を外から起こして確かめるテスト（clitest/sh/git）。
// tests/sh/ の test_gitwrap・test_integrate_sh・test_integration_sh・test_parent_branch_sh・test_branches_sh・
// test_lock_exclusion・test_find_marked・test_stage_sh・test_stage_names・test_check_no_wip の移し。
//
// 使い捨ての git のリポジトリと、`.ccnavi/scripts/` の写しを持つワークスペースを毎回組む。読み返すのは標準出力・標準エラー・
// 終了コードと、git とファイルシステムに残ったものだけ（中の関数も変数も見ない）。このパッケージだけが使う道具は helpers_test.go にある。
package git
