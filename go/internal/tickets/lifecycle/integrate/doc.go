// Package integrate は、チケットに結び付かない直接作業のワークツリー（`.claude/worktrees/<名前>`。ブランチも同じ名前）が
// `integrate` まで進んでいるかを見る。次のものを 1 か所に置く。Stop の促し（DirectAtStop）、`ccnavi-git.sh integrate`
// が断る「チケットに結び付く」判定（BoundReason）、コミット後の案内と SessionStart の一覧が使う直接作業の判定
// （DirectCheck）、統合先の名前（IntegrationName）。促す側と integrate が断る側で判定が食い違わないように、
// ここに集めている。git とファイルだけを読み、ネットワークには出ない。
//
// 1 本のツリーを調べた結果は hit・Clear・unknown の 3 つのどれかになる。期限切れ・git の失敗・決められないものは
// unknown になる。unknown のツリーは候補から外さず、促した記録も残さない。期限（deadline）のゼロ値は期限なしを表す。
package integrate
