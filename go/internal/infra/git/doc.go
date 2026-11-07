// Package git は git を起こす唯一の場所と、ワークツリーで実際に何が変わったかを git に聞く読み手。
//
// 実行前チェックは引数しか見ないので、ビルドの出力や許可したスクリプトの中の書き込みは通ってしまう。
// 実行後に git に「何が起きたか」を聞いてその穴を埋める（止めてはいないので遅れは待ち時間にしかならず、
// 読めなければ何も言わない）。聞く相手はローカルの git だけでネットワークへは出ない。git の管理から
// 外れたファイル（.gitignore の中・ワークツリーの外）は見えない。
//
// 起動は Run、標準出力だけ欲しいときは Output、成功したときの前後の空白を除いた文字列は Text。
// 期限は Options.Deadline で渡す。残りが deadlineMargin・deadlineFloor に足りなければ起動せず Done.TimedOut を返す
// （ゼロ値の期限は期限無し）。git の期限は目的ごとの DefaultTimeout・StateTimeout・RiskTimeout・ReviewTimeout・
// MergeTimeout・CleanupTimeout で決め、文に出す sha の 12 桁は ShortSHA。
// 状態の問い合わせは TopLevel・Head・IsAncestor（NotAncestorCode）・Status と Porcelain・Count・CommitPaths・
// TreeDiffPaths・TreeOf。
package git
