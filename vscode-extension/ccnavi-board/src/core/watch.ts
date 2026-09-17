/**
 * 画面が見張る場所。提案（ワークスペース、プロジェクト、全ワークツリーの `wip/proposals/`）、
 * 承認済みチケットとマーカー（同じツリーの `.ccnavi/approved/`。`doing/` `done/` `phases/`）、ワークツリーの登録。
 *
 * 4 つの画面（ボード・ルール設定・リスク管理・フェーズ管理）が同じ場所を見るので、どれか 1 つの画面に
 * 置いて他から引かせず、ここに 1 本だけ置く。glob は OS によらず "/" 区切り。
 */
export const WATCH_PATTERNS = [
  "wip/**/proposals/**",
  "projects/*/wip/**/proposals/**",
  ".claude/worktrees/*/wip/**/proposals/**",
  ".ccnavi/approved/**",
  "projects/*/.ccnavi/approved/**",
  ".claude/worktrees/*/.ccnavi/approved/**",
  ".git/worktrees/*",
  "projects/*/.git/worktrees/*",
] as const;
