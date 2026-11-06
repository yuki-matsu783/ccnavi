// Package git は git を起こす唯一の場所と、ワークツリーで実際に何が変わったかを git に聞く読み手。
//
// 実行前チェックは引数しか見ないので、ビルドの出力や許可したスクリプトの中の書き込みは通ってしまう。
// 実行後に git に「何が起きたか」を聞いてその穴を埋める（止めてはいないので遅れは待ち時間にしかならず、
// 読めなければ何も言わない）。聞く相手はローカルの git だけでネットワークへは出ない。git の管理から
// 外れたファイル（.gitignore の中・ワークツリーの外）は見えない。
//
// Python からの対応: gitcmd と gitstate をまとめた。gitcmd.run → Run、run_until → RunUntil、
// output_until → RunUntil の Done（Code・Out・TimedOut）、DEADLINE_MARGIN・DEADLINE_FLOOR → DeadlineMargin・
// DeadlineFloor（足りるかは Expired）、gitstate.top_level → TopLevel、head → Head。
package git
