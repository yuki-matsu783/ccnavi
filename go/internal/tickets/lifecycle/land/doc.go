// Package land は、直接作業の取り込み（ccnavi-git.sh の integrate・integrate --abort・integrate-done）を行う。
//
// チケットを起こさない直接作業のワークツリー（.claude/worktrees/<名前>。ブランチも同じ名前）を、切った時点でユーザの
// チェックアウトが開いていたブランチ（取り込み先）へ 1 コミットで載せる。確認の間はユーザのチェックアウトの索引と
// ワークツリーに書かず、一時ワークツリーで作ったコミットを `merge --ff-only --no-overwrite-ignore` で載せる。
// チケットの作業はマージリクエストで出すので、チケットに結び付くワークツリーは断る。
//
//   - integrate <名前>: 作業ブランチの先頭の木から wip/handoff/ の差分を起点 S に戻して T とし、
//     `merge-tree --write-tree --merge-base=S HEAD T` で C（取り込み結果の木）を計算する。C を一時ワークツリー
//     （logs/direct-tmp/<repo>/<名前>）に展開し、記録（marker）と専用の ref（refs/ccnavi/direct/<名前>/{t,c}）に
//     残して止まる
//   - integrate-done <名前>: 一時ワークツリーで C を 1 コミット K にし（フックを通す）、K の親と木を確かめてから、
//     ユーザのチェックアウトで `merge --ff-only --no-overwrite-ignore K` を打つ。そのあとワークツリー・一時ワークツリー・
//     ブランチ・ref・記録を片付ける。何度打ち直してもよい
//   - integrate --abort <名前>: 一時ワークツリーと ref と記録を消す。作業ワークツリーとユーザのチェックアウトには触らない
//
// 記録は <ユーザのチェックアウトの git ディレクトリ>/ccnavi-integrate/<名前>/marker。版が 4 でない記録は、版を知らない
// 壊れた記録として止める。ロック（logs/state/locks/<repo>/.integrate）はラッパー（sh）が取ってから呼ぶ。
package land
