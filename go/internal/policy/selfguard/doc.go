// Package selfguard は自己防衛を担う。ccnavi 自身を成り立たせている設定（hook の登録、各レイヤーの設定 3 本、
// 実行ファイル）を、エージェントに書き換えさせない。働きは 2 つある。
// 止める側は、守る場所への書き込み（シェルと名指しのツール）を止める組み込みのルールを、ルールファイルの外から足す。
// ルールに書くと、ルールを消せば保護も消えてしまうためである。
// 戻す側は、守る対象を実行前にバックアップし、実行後に変わっていれば直前の断面へ戻す。git から戻すのは
// バックアップが無いときだけにする。git から戻すと戻し先がコミット済みの内容になり、ユーザの書きかけを消すためである。
//
// 守る対象には、ワークツリー側のコピーも含む。今は読まれないが、統合で統合先の設定になるからである。
// 実行ファイルは大きいので、セッション開始で 1 度だけ中身のハッシュの名前で store/ に置く。照合は大きさと
// 更新時刻で行う。ユーザの発言（Accept）があれば、ターンの間の変更をユーザの手によるものとして受け入れる。
//
// Python からの対応: selfguard_targets・selfguard・selfguard_shell をまとめた。targets → Targets、
// SETTINGS・GATE_SETTINGS → Choices・GateChoices、resolve → Resolve、before・after・accept・at_start →
// Before・After・Accept・AtStart、report → Report、ACTION_* → Action の定数、BACKUP_DIR・STORE_DIR → config.SelfguardDir・SelfguardStore（records/prune と同じ定数）、
// guard_shell_regex → ShellRegex、common_layer_files → CommonLayerFiles、git_dir_shell_regex → GitDirShellRegex、
// GIT_DIR_PATH_REGEX → GitDirPathRegex、_WRITE_VERBS → WriteVerbs、_NOT_A_WORD → NotAWord、under → Under、
// holder_regex → HolderRegex（named_only は引数）、copy_destination_regex・copy_last_place → CopyDestinationRegex・
// CopyLastPlace、binary_clause(p) → BinaryClause、too_many_redirects → TooManyRedirects、REDIRECT_LIMIT・
// RAW_LENGTH_LIMIT → RedirectLimit・RawLengthLimit、add_rules → AddRules（Options。tool=None は Tool が空）、
// add_named_rules → AddNamedRules、handoff_delete_word → HandoffDeleteWord（base=None は空）、SHELL_RULE_ID → ShellRuleID。
// 上の段が使わない sweep・records_regex・common_layer_regex・handoff_* の残り・ほかの *_RULE_ID は私的にした。
// 呼び手の無い ACTION_SAVED・NAMED_RULE_IDS は移さなかった。before などの関数の形は Step。
package selfguard
