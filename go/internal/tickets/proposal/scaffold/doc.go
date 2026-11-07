// Package scaffold は親の提案の雛形を書く。ccnavi ticket new（New）は、選んだフェーズの scope を phases.yml から
// 集めて allow に書いた提案を、識別子と同名の親のワークツリーの todo/ に新規に書き、phases:（定義のコピー）を
// 差し込む。手で書くと scope の写し忘れで子が書けず詰まるため。テンプレートから作るときは、子のフローの下書きも
// 書き、書いた提案に承認の検査を当てて通らなければ戻す。ccnavi ticket revise（Revise）は、承認済みの親のコピーから
// スクリプトだけが書く欄を外した改版の雛形を書く。どちらも既にあるものは上書きせず、ネットワークにも git にも
// 触らない。
//
// Python からの対応: tickets/proposal の scaffold と revise をまとめた。scaffold.run → New（引数は Request。issue・mr
// の None → nil）、revise.run → Revise、parent_worktree・parse_plan・collect_allow・parse_scope・render・render_items・
// _with_feedback_none は私的（render と render_items は 1 つにまとめた）。
package scaffold
