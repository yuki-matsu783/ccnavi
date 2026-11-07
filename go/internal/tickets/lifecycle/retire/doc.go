// Package retire は親を閉じたときの手元の片付け（ワークツリーとブランチ）を持つ。`ready` が子のチケットを退避へ
// 移す前に子の一覧を state の `retire/<親>.json` に書き（Note）、`worktree retire <親>` がそれを読んで子のワークツリー・
// 子のブランチ・親のワークツリーを消し（RunRetire）、MR のマージが分かった後の `sync tidy` が親のブランチと残りを
// 消す（RunMerged）。消すのは成果が失われないと確かめられたものだけで、確かめられなければ消さずに理由と手で
// 消すコマンドを言う。取り消した子のブランチはユーザが確認したときだけ消す（RunBranch の confirmed）。
//
// 合流した子のブランチは、先頭が合流した sha と同じで、その sha が push 済みの親（origin/<親のブランチ>、または
// MR の先頭）の祖先のときだけ消す。親のブランチは、手元の先頭が push 済みの先頭と MR の先頭の両方に含まれるとき
// だけ消す（MR の先頭は sh が渡す値なので、それだけでは信じない）。ブランチを消せるのはここだけで、ネットワーク
// には出ない。
//
// Python からの対応: tickets/lifecycle/retire。note → Note、entries・Entry → Entries・Entry、run_abandon・
// run_retire・run_branch・run_merged → RunAbandon・RunRetire・RunBranch・RunMerged。record_path・judge_child・
// hand_command・repo_dir・Child・Outcome と状態の定数は私的。
package retire
