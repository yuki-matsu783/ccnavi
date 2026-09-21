/**
 * カードとフェーズ行に出す言葉。判定は実行ファイルがやっていて、ここは JSON が言ったことを
 * 言い換えるだけ。マーカーや済みから状態を組み直さない（ADR-0035）。
 */
import type { Card, PhaseChip } from "../../core/board.js";

export const COPY_LABELS = { none: "未承認", open: "承認済", review: "レビュー待ち", closed: "クローズ" } as const;

export const MARK_LABELS: Readonly<Record<string, string>> = {
  pending: "終了を通知",
  skipped: "レビュー省略",
  requested: "レビュー依頼済",
  reviewed: "レビュー済",
};

export const PHASE_STATE_LABELS = { planned: "未計画", active: "進行中", ended: "終了" } as const;

/**
 * レビューが済むまで止めている間の呼び名。依頼を出す前はエージェントの番（合流・push・依頼）で
 * 「レビュー準備中」、出した後は人の番で「レビュー待ち」。実行ファイルの `phase.review_label` と
 * 同じ分け方で、判定した 2 つの真偽値（`gate_closed` / `review_waiting`）を言い換えるだけ。
 */
export function holdLabel(x: { readonly gateClosed: boolean; readonly reviewWaiting: boolean }): string {
  return x.reviewWaiting ? "レビュー待ち" : "レビュー準備中";
}

export function riskText(card: Card): string {
  return card.riskPoints === null ? `リスク ${card.riskLevel}` : `リスク ${card.riskLevel}（${card.riskPoints} 点）`;
}

export function isHighRisk(level: string): boolean {
  return level === "HIGH" || level === "CRITICAL";
}

/** ワークツリーの置き場の末尾（`.claude/worktrees/<名前>` の名前）。読めなければ「あり」 */
export function worktreeName(path: string): string {
  const name = path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() ?? "";
  return name === "" ? "あり" : name;
}

/** フェーズ行の状態の全文。`終了 · レビュー待ち · レビュー依頼済 · レビュー要 · リスク: 25 (MEDIUM) — …` */
export function phaseStatusFull(p: PhaseChip): string {
  const notes: string[] = [];
  if (p.gateClosed) {
    notes.push(holdLabel(p));
  }
  for (const m of p.marks) {
    notes.push(MARK_LABELS[m] ?? m);
  }
  if (p.reviewRequired) {
    notes.push("レビュー要");
  }
  if (p.riskLine !== "") {
    notes.push(p.riskLine);
  }
  return [PHASE_STATE_LABELS[p.state], ...notes].join(" · ");
}

/**
 * フェーズ行の状態の要約。人が動くべきことだけで、無ければ空。項目はカードのバッジと同じ。
 * 止めている間は段の名前を 1 つだけ出す。
 */
export function phaseStatusBrief(p: PhaseChip): string {
  const notes: string[] = [];
  if (p.gateClosed) {
    notes.push(holdLabel(p));
  }
  if (isHighRisk(p.riskLevel)) {
    notes.push(`リスク ${p.riskLevel}`);
  }
  return notes.join(" · ");
}

/** マージリクエストのバッジの文字。番号が読めなければ「MR」だけ */
export function mrText(number: number | null): string {
  return number === null ? "MR" : `MR #${number}`;
}

/** 依頼のマーカーが持つ URL は中身を確かめずに写してあるので、http(s) のときだけリンクにする */
export function isHttpUrl(url: string): boolean {
  return /^https?:\/\//i.test(url);
}
