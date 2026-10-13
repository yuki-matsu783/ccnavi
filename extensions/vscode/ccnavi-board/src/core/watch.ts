/**
 * 画面が見張る場所。提案（ワークスペース、プロジェクト、全ワークツリーの `wip/proposals/`）、
 * 承認済みチケットとマーカー（同じツリーの `.ccnavi/approved/`。`doing/` `done/` `phases/`）、ワークツリーの登録、
 * 閉じたチケットの退避（ワークスペースの `logs/archive/`。`ccnavi-review.sh ready` が移す）。
 *
 * 7 つの画面（ボード・ルール管理・リスク管理・フェーズ管理・テンプレート管理・フロー編集・計画編集）が同じ場所を見るので、どれか 1 つの画面に
 * 置いて他から引かせず、ここに 1 本だけ置く。glob は OS によらず "/" 区切り。
 */
export const WATCH_PATTERNS = [
  "wip/**/proposals/**",
  "projects/*/wip/**/proposals/**",
  ".claude/worktrees/*/wip/**/proposals/**",
  ".ccnavi/approved/**",
  "projects/*/.ccnavi/approved/**",
  ".claude/worktrees/*/.ccnavi/approved/**",
  "logs/archive/**",
  ".git/worktrees/*",
  "projects/*/.git/worktrees/*",
] as const;

/** 監視の反応をまとめる待ち時間。ほとんどの画面はこれを使う。違う値にする画面は、その場で理由を書く */
export const WATCH_DEBOUNCE_MS = 120;

/** 自分の保存で監視が反応するのを、この間だけ「ファイルの変更を検知しました」と言わない */
export const OWN_WRITE_GRACE_MS = 1500;
