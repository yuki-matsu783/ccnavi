// Package ccnavimd は、セッションの頭で渡す作業の決まり（`.ccnavi/CCNAVI.md`）の条件と差し込みを解いて 1 つの文にする。
// 何を言うかは md が持ち、ここは md の書き方（`<!-- ccnavi:if 名前 -->`・`else`・`endif` と `{{名前}}`）を解くだけ。
// 組み込みに無い条件は、ワークスペースの `.ccnavi/scripts/ccnavimd.sh` の関数 `ccnavi_cond_<名前>` が決める
// （0 が真、1 が偽、それ以外は判定できないので真）。条件は文と判定を食い違わせないための仕掛けでもあり、
// ワークツリーの行はそれを支える deny（`workspace-root`）があるときだけ載る。
//
// 付き添いの有無（Unattended）もここが持つ。見るのは Claude Code が文書にしていない環境変数なので、判定を 1 か所に置く。
//
// Python からの対応: hook/ccnavimd。session_text → SessionText、unattended → Unattended、path → Path、
// name_problems → NameProblems、problems → Problems、script_names → ScriptNames。render・script_flags・flags_for・
// values_for と条件の表（CONDITIONS・SUBSTITUTIONS）は私的にした（上の段は SessionText だけを呼ぶ）。
package ccnavimd
