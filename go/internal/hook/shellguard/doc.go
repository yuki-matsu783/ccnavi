// Package shellguard は、Bash のコマンド行を読み、守る場所を書き換える形を実行前に止める組み込みの検査をする。
// 検査は次の 6 つ。保護済みの sh（`.ccnavi/scripts/ccnavi-*.sh`）の検査の材料を変える環境変数の代入（CheckEnv）、子チケットの
// ワークツリーからの push（CheckChildPush）、git のラッパーで自己防衛の対象を消す・戻す形（CheckCorePaths）、
// サブエージェントが親のワークツリーを書き換える形（CheckParentTreeGit・CheckParentWrite）、ワークスペースルート直下への
// 書き込み（CheckRootWrite）。理由コードとルール名（Code*・*RuleID）は検査と一緒にここに置く（置き場の決まりは reasons の doc）。
//
// どれもルールファイルの外に置く組み込みで、ルールを空にされても消えない。止める広さは sh の検査に合わせ、読み切れない形・
// 居場所を追えない形は止める側に倒す。sh も同じ検査を 2 つ目として持つ。コマンドの語は infra/shell の読み（PlacedCommands）で取り、
// `cd` と実行役の行き先（`env -C`・`sudo -D`）を追った居場所から、書き込み先を解く
package shellguard
