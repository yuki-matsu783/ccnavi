// Package shask は .ccnavi/scripts/ の sh がチケットについて聞くこと（`ccnavi tree <問い> …`）に答える。読むだけで、
// 何も書かない。git はローカルの読み取りだけで、ネットワークには出ない。
//
// sh はチケットの YAML も JSON も自分で読まない。答えは 1 行 1 項目（`<キー> <値>`。sh は jq を使わない）で、頭の行
// （`tree 1`）が無ければ sh は「実行ファイルが答えられなかった」と読み、呼ぶ側が決めた向きに倒す（止める側の検査なら
// 止める側、ツリーを動かす側なら動かさない側）。行・値・終了コードは sh との契約なので変えない。問いは次の 6 つ。
//
//   - `tree parent <ツリー> <名前>`: そのツリーが名前の親のワークツリーか（parent）と、push のゲートが初回の push か
//     消えた親ブランチの再作成かを見分ける材料（approved・requested・archived・started・mr）。
//   - `tree bound <ツリー> <名前>`: 直接作業のワークツリーがチケットに結び付くか（integrate.BoundReason）。
//   - `tree leftover <ツリー> <名前>`: 親のワークツリーに承認待ちの提案・作業中の子・MR が残っているか。
//   - `tree base-bound <チェックアウト> <ブランチ>`: 直接作業の取り込み先のブランチが結び付くか（branch.BaseBound）。
//   - `tree guarded <チェックアウト> <パス>…`: パスが自己防衛の守る対象に当たるか。
//   - `tree handoff-pending <ツリー> [<起点>]`: 取り込んでいない下書き（wip/handoff/）。
//
// Python からの対応: entry/sh_ask を移した。parent_tree・bound_tree・leftover_tree・base_bound_tree・guarded_tree・
// handoff_pending_tree は Answer の 1 つにまとめた（cli が並べていた引数の数の照合も Answer が持つ）。TREE_HEAD →
// head、YES・NO・UNREADABLE は私的な answer の定数。
package shask
