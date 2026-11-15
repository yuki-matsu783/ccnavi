// Package cut は、ccnavi-worktree.sh（直接作業のワークツリーを取り込み先のブランチから切る）の判定・ローカルの git と
// ファイルの操作・出力の文を持つ。ネットワークには出ない（fetch と ls-remote は sh）。
//
// sh は 2 回呼ぶ。答えの形は reply にある。
//
//   - plan: 引数と C1〜C3・同じ識別子・既にあるワークツリーとブランチ・親のワークツリー・取り込みの途中の記録の検査、
//     引き取り（--adopt）と --dry-run の全部。切る道に進むなら、取ってくる取り込み先を `fetch <ブランチ>` と、fetch と
//     ls-remote を打つツリーを `top <パス>` で返す
//   - cut: sh が取ってきた fetch の出力と ls-remote の応答を受け、plan と同じ検査をもう一度してから（出力はしない）、
//     origin とのずれ（C6）・書きかけ（C7）・確認コードを決め、記録を書いて ref を置き、ワークツリーを切る
//
// 記録はリポジトリの共通ディレクトリの ccnavi-direct/<名前>（integrate.WriteRecord。integrate が読む）。
package cut
