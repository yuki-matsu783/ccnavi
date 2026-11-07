// Package git は git を起こす唯一の場所と、ワークツリーで実際に何が変わったかを git に聞く読み手。
//
// 実行前チェックは引数しか見ないので、ビルドの出力や許可したスクリプトの中の書き込みは通ってしまう。
// 実行後に git に「何が起きたか」を聞いてその穴を埋める（止めてはいないので遅れは待ち時間にしかならず、
// 読めなければ何も言わない）。聞く相手はローカルの git だけでネットワークへは出ない。git の管理から
// 外れたファイル（.gitignore の中・ワークツリーの外）は見えない。
//
// Python からの対応: gitcmd と gitstate をまとめた。gitcmd.run → Run、run_until・output_until → Run に
// Options.Deadline（期限が足りなければ Done.TimedOut）、output → Output、成功したときの out.strip() → Text、
// DEADLINE_MARGIN・DEADLINE_FLOOR → DeadlineMargin・DeadlineFloor（足りるかは Expired。ゼロ値の期限は期限無し）、
// gitstate.top_level → TopLevel、head → Head（期限は Options）。各モジュールの git の期限（TIMEOUT_SECONDS など）は
// 目的ごとの DefaultTimeout・StateTimeout・RiskTimeout・ReviewTimeout・MergeTimeout・CleanupTimeout、文に出す sha の
// 12 桁は ShortSHA。上の段が書いていた `merge-base --is-ancestor` → IsAncestor（NotAncestorCode）、
// `status --porcelain` とそのフラグ → Status と Porcelain、`rev-list --count` の数の読み → Count、コミットが変えたパス（diff-tree --root）→ CommitPaths、
// 2 つのツリーの差のパス → TreeDiffPaths、`rev-parse --verify <版>^{tree}` → TreeOf。
package git
