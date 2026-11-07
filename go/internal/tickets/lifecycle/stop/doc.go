// Package stop は、Stop でエージェントが終えようとしたときの、チケット作業の促しを決める。促しは 3 種類ある。
// finish の打ち忘れ（Unfinished）、レビューの依頼までの途中（Pending）、フィードバック計画の待ち（Waiting）である。
// どれも判定の deny ではなく、1 回だけの促しにとどまる。同じ状態で 2 度促さないように、促した状態の要約を
// セッションごとの記録（nudged-<セッション>.json）にキーごとに残す。git か置き場を読めなければ促さない。
// これは保護ではない。
//
// Python からの対応: tickets/lifecycle の stop・stop_request・stop_feedback をまとめた。
// unfinished_at_stop → UnfinishedAtStop、pending_at_stop → PendingAtStop、waiting_at_stop → WaitingAtStop、
// nudged_before・remember_nudge（3 つ）→ NudgedBefore・RememberNudge（Nudge を満たす型で分ける）、
// finish_nudge・request_nudge・feedback_nudge → FinishNudge・RequestNudge・FeedbackNudge、nudge_path → NudgePath、
// base_off_head → BaseOffHead、worktree_head_sha → HeadSHA（deadline のゼロ値は期限なし）、
// CODE_* → Code*、STEP_* → Step*、USER_STOP_NOTE → UserStopNote。
package stop
