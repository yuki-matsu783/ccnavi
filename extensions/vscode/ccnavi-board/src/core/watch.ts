/**
 * 画面が見張る場所。提案（ワークスペース、プロジェクト、全ワークツリーの`wip/proposals/`）、
 * 承認済みチケットとマーカー（同じツリーの`.ccnavi/approved/`。`doing/` `done/` `phases/`）、ワークツリーの登録、
 * クローズしたチケットの退避（ワークスペースの`logs/archive/`。`ccnavi-review.sh ready`が移す）。
 *
 * 拡張全体の答え（`board-feed.ts`。ボードとサイドパネルが読む）と6つの画面（ルール管理・リスク管理・フェーズ管理・テンプレート管理・
 * フロー編集・計画編集）が同じ場所を見るので、どれか1つの画面に
 * 置いて他から引かせず、ここに1本だけ置く。globはOSによらず "/" 区切り。
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

/**
 * 取り込み待ち画面が見張る場所。下書きの置き場（全ツリーの`wip/handoff/`）と、取り込み先になる場所（許可リストの種類）と、
 * 設定ファイル。取り込める対象の数は下書きと取り込み先の両方で変わる。
 */
export const HANDOFF_PATTERNS = [
  "wip/handoff/**",
  "projects/*/wip/handoff/**",
  ".claude/worktrees/*/wip/handoff/**",
  ".ccnavi/config/**",
  "projects/*/.ccnavi/config/**",
  ".ccnavi/docs/**",
  ".ccnavi/CCNAVI.md",
  ".claude/skills/**",
  ".claude/settings.json",
  ".claude/settings.local.json",
] as const;

/**
 * 拡張全体で`--explain --json`を読み直す場所（`board-feed.ts`）。ボードの置き場に、取り込み待ちの数（`inbox.handoff`）と
 * チケット制御（`settings.ticket_control`）が変わる場所を足したもの。フローの下書き（`wip/proposals/flows/`）は提案の置き場に入る
 */
export const FEED_PATTERNS = [...WATCH_PATTERNS, ...HANDOFF_PATTERNS] as const;

/** 監視の反応をまとめる待ち時間。ほとんどの画面はこれを使う。違う値にする画面は、その場で理由を書く */
export const WATCH_DEBOUNCE_MS = 120;

/** 自分の保存で監視が反応するのを、この間だけ「ファイルの変更を検知しました」と言わない */
export const OWN_WRITE_GRACE_MS = 1500;
