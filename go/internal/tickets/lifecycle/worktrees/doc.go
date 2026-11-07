// Package worktrees は、`.claude/worktrees/<名前>` のワークツリーを片付ける（`ccnavi worktree drop|tidy|remaining|clean`）。
// また、承認した子のワークツリーの計画を組む（承認の JSON の worktrees）。
//
// 消すのは、cwd が中に無く、未コミットの変更（未追跡を含む）が無く、git のワークツリーとして登録されたものだけである。
// 消し方は次のとおり。まず生成物（node_modules など）を消す。次に、git が無視しているそれ以外のもの（.env など）を、
// ワークスペースルートの scratchpad/worktrees/<名前>/ へ rename で退避する。最後に `git worktree remove` を
// （--force なしで）実行する。これは、Windows で深い node_modules が remove を途中で止めるのを防ぐためである。
// 退避したものは消さない。消さないときは何も消さず、理由を言う。git はローカルの操作だけで、ネットワークには出ない。
//
// Python からの対応: tickets/lifecycle/worktrees。drop・blocked・report・run_drop → Drop・Blocked・Report・RunDrop、
// Dropped（removed → Removed）と REMOVED などの状態 → Status の定数、tidy_targets・family_for・run_tidy →
// TidyTargets・FamilyFor・RunTidy、run_remaining → RunRemaining（remaining・Left は私的）、run_clean → RunClean、
// _owner → Owner（retire が使う）、child_worktrees → ChildWorktrees（dict と CHILD_* は
// ticket.WorktreePlan と ticket.WorktreeState。proposal/agree と同じ型を読む）。_long は落とした（Go の os は Windows の長いパスを自分で扱う）。
package worktrees
