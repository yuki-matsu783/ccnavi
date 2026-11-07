// Package shellguard は、Bash のコマンド行を読み、守る場所を書き換える形を実行前に止める組み込みの検査を持つ。
// 保護済みの sh（`.ccnavi/scripts/ccnavi-*.sh`）の検査の材料を変える環境変数の代入（CheckEnv）、子チケットの
// ワークツリーからの push（CheckChildPush）、git のラッパーで自己防衛の対象を消す・戻す形（CheckCorePaths）、
// サブエージェントが親のワークツリーを書き換える形（CheckParentTreeGit・CheckParentWrite）、ワークスペースルート
// 直下への書き込み（CheckRootWrite）の 6 つである。
//
// どれもルールファイルの外に置く組み込みで、ルールを空にされても消えない。止める広さは sh の検査に合わせ、
// 読み切れない形・居場所を追えない形は止める側に倒す。sh も同じ検査を 2 重目として持つ。
// コマンドの語は infra/shell の読み（PlacedCommands）で取り、`cd` と実行役の行き先（`env -C`・`sudo -D`）を
// 追った居場所から書き込み先を解く。
//
// Python からの対応: hook の wrapguard・parentwrite・rootwrite をまとめた。check_env・check_child_push・
// check_core_paths・check_parent_tree_git → CheckEnv・CheckChildPush・CheckCorePaths・CheckParentTreeGit、
// parentwrite.check → CheckParentWrite（戻りは ParentWrite）、rootwrite.check → CheckRootWrite（戻りは RootWrite）、
// git_calls → GitCalls、blocked → Blocked、worktree_name → WorktreeName、ticket_role → TicketRole（ROLE_* は Role）、
// *_message → EnvMessage・ChildPushMessage・CorePathMessage・ParentTreeMessage・ParentWriteMessage・RootWriteMessage、
// CORE_* → Core の定数、KIND_* → WriteKind の定数、CODE_*・*_RULE_ID → Code*・*RuleID。
// parentwrite.write_targets と resolve_word は私的にした（呼び手は rootwrite とテストだけ）。
// child_push の 3 つ目の戻り（見つけたファイル）は呼び手が使わないので返さない。
package shellguard
