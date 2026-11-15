// Package shask は、.ccnavi/scripts/ の sh がチケットについて聞くこと（`ccnavi tree <問い> …`）に答える。
// 読むだけで、何も書かない。git はローカルの読み取りだけを使い、ネットワークには出ない。
//
// sh はチケットの YAML も JSON も自分では読まない。答えは 1 行 1 項目（`<キー> <値>`。
// sh は jq を使わない）で返す。頭の行（`tree 1`）が無ければ、sh は「実行ファイルが答えられなかった」と読み、
// 呼ぶ側が決めた答えにする（止める側の検査なら止める側、ツリーを動かす側なら動かさない側）。
// 行・値・終了コードは sh との契約なので変えない。問いは次の 2 つ。
//
//   - `tree parent <ツリー> <名前> [<ブランチ>]`: そのツリーが名前の親のワークツリーかを返す（parent）。加えて、
//     承認済みか・頼んだ印・退避・着手済み・MR（approved・requested・archived・started・mr）を返す。ブランチを
//     渡せば、親のブランチを一度は送った形跡（trace。pushTrace）も返す。
//   - `tree gate <ツリー> <ブランチ> <識別子> <秒>`: 親のブランチの push のゲートの次の一手（gate pass・stop・
//     ls-remote）を返す（pushGate）。ls-remote は sh が打つ。
//
// 実行ファイルの中の段取り（ccnavi-worktree.sh の cut）には、親のワークツリーに残っているもの（Leftover）を関数で渡す。
package shask
