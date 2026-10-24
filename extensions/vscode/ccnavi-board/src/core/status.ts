/**
 * カードの「状態の札」とフェーズ行の「状態の一言」を決める。純粋な関数で、VS Code にも DOM にも触れない。
 *
 * 札は「状況」、カードの操作欄は「ユーザができること」。両者は別の規則で決め、同時に出てよい
 * （「要確認」の札と［承認］、など）。操作してよいかの最終判断は実行ファイルが持つ。
 * ここは実行ファイルの JSON が判定した欄（`gate_closed`・`review_waiting`・`blocked`・`pending_approval`・
 * マーカー）を表示の言葉に置き換えるだけで、レビューの要否・ゲートの条件・リスクは判定し直さない。
 *
 * 規則は上から見て、最初にヒットしたものを採る。表は `docs/21_design/screens.md` の「状態の札」と同じ。
 */
import type { CopyStatus, PhaseJson } from "./model.js";

/** フェーズ行の状態の一言。ボードが言う言葉は、この 9 つだけ */
export type PhaseStatus =
  | "レビュー待ち"
  | "レビュー準備中"
  | "レビュー済み"
  | "レビュー予定"
  | "レビュー省略"
  | "終了"
  | "進行中"
  | "これから"
  | "要確認";

/** 札の色の系統。色だけに頼らず、言葉は必ず出す */
export type Tone = "check" | "turn" | "wait" | "work" | "done" | "cancel";

export interface Status {
  /** 札の言葉。「完了（早めに終了）」のように補足を括弧で足したものもここに入る */
  readonly label: string;
  /** ツールチップ。理由があるときだけ（無ければ空） */
  readonly title: string;
  readonly tone: Tone;
}

/**
 * フェーズ行の状態の一言。使う欄と優先順位は次のとおり（上から最初にヒットしたもの）。
 *
 * | 順 | 条件 | 言葉 |
 * |---|---|---|
 * | 1 | `gate_closed` かつ `review_waiting` | レビュー待ち |
 * | 2 | `gate_closed` | レビュー準備中 |
 * | 3 | `marks.reviewed` がある | レビュー済み |
 * | 4 | `marks.skipped.deferred_to` があり、その番号のフェーズに `marks.reviewed` がある | レビュー済み |
 * | 5 | `marks.skipped.deferred_to` がある | レビュー予定 |
 * | 6 | `marks.skipped` がある | レビュー省略 |
 * | 7 | `state === "ended"` | 終了 |
 * | 8 | `state === "active"` | 進行中 |
 * | 9 | `state === "planned"` | これから |
 * | 10 | 上のどれでもない | 要確認 |
 *
 * `phases` は同じ親のフェーズ全部（4 で引き受け先を引く）。
 */
export function phaseStatusOf(phase: PhaseJson, phases: readonly PhaseJson[]): PhaseStatus {
  if (phase.gate_closed && phase.review_waiting) {
    return "レビュー待ち";
  }
  if (phase.gate_closed) {
    return "レビュー準備中";
  }
  if (hasMark(phase, "reviewed")) {
    return "レビュー済み";
  }
  const skipped = phase.marks.skipped;
  if (skipped !== undefined) {
    const deferred = deferredTo(skipped);
    if (deferred !== undefined) {
      const target = typeof deferred === "number" ? phases.find((p) => p.number === deferred) : undefined;
      return target !== undefined && hasMark(target, "reviewed") ? "レビュー済み" : "レビュー予定";
    }
    return "レビュー省略";
  }
  switch (phase.state) {
    case "ended":
      return "終了";
    case "active":
      return "進行中";
    case "planned":
      return "これから";
    default:
      return "要確認";
  }
}

function hasMark(phase: PhaseJson, kind: string): boolean {
  return Object.prototype.hasOwnProperty.call(phase.marks, kind);
}

/** `skipped` の `deferred_to`。無い・null なら undefined（延期ではない） */
function deferredTo(skipped: Record<string, unknown>): unknown {
  const value = skipped.deferred_to;
  return value === undefined || value === null ? undefined : value;
}

/** 札の判定に要るカードの欄。`Card`（`board.ts`）がそのまま満たす */
export interface StatusCard {
  readonly isParent: boolean;
  readonly column: string;
  readonly copyStatus: CopyStatus;
  readonly blocked: string;
  readonly scattered: readonly { readonly tree: string; readonly state: string }[];
  readonly pendingApproval: boolean;
  readonly cancelledAt: string;
  readonly startedAt: string;
  /** 親だけ。承認済みの親に改版が出ているか */
  readonly revision: boolean;
  /** 親だけ。早めに閉じたか */
  readonly wrapped: boolean;
  /** 子だけ。自分のフェーズが止まっているか（JSON の `gate_closed`） */
  readonly gateClosed: boolean;
  /** 子だけ。自分のフェーズがユーザのレビュー待ちか（JSON の `review_waiting`） */
  readonly reviewWaiting: boolean;
  /** 子だけ。自分のフェーズの状態の一言。フェーズが引けなければ空 */
  readonly phaseStatus: PhaseStatus | "";
  /** 親だけ。フェーズ行（親のレビュー状態はここから見る。親カードの `gateClosed` は使わない） */
  readonly phases: readonly { readonly status: PhaseStatus }[];
}

const CHECK = "要確認";

function check(title: string): Status {
  return { label: CHECK, title, tone: "check" };
}

/** 正が決まらない・書き込み停止中の理由。無ければ空 */
function troubleOf(card: StatusCard): string {
  const reasons: string[] = [];
  if (card.blocked !== "") {
    reasons.push(`書き込み停止中: ${card.blocked}`);
  }
  if (card.scattered.length > 0) {
    const where = card.scattered.map((s) => `${s.tree || "main"}:${s.state}`).join(", ");
    reasons.push(`複数の場所にあります（${card.scattered.length} か所）: ${where}`);
  }
  return reasons.join("\n");
}

function closed(card: StatusCard): boolean {
  return card.column === "done" || (card.column === "archived" && card.cancelledAt === "");
}

/** 読めた欄。どの規則にもヒットしなかったときに、ツールチップへ出す */
function fieldsOf(card: StatusCard): string {
  return [
    `列: ${card.column}`,
    `置き場: ${card.copyStatus}`,
    `承認待ち: ${card.pendingApproval ? "はい" : "いいえ"}`,
    `着手: ${card.startedAt === "" ? "なし" : card.startedAt}`,
    card.isParent ? "" : `フェーズ: ${card.phaseStatus === "" ? "引けない" : card.phaseStatus}`,
  ]
    .filter((line) => line !== "")
    .join("\n");
}

/**
 * カードの状態の札。上から最初にヒットしたものを採る。
 *
 * 子
 * 1. `blocked` か `scattered` がある → 要確認
 * 2. `pendingApproval` → 承認待ち
 * 3. 取り消し（`cancelledAt` あり。アーカイブも同じ）→ 取り消し
 * 4. 完了の列（`done/`）か、取り消しでないアーカイブ → 完了
 * 5. `copyStatus === "review"`: 自分のフェーズの状態を使う（レビュー待ち／レビュー準備中／レビュー予定。それ以外は要確認）
 * 6. `copyStatus === "open"`: 自分のフェーズが `reviewWaiting` か `gateClosed` なら要確認。そうでなければ
 *    `startedAt` が空なら開始前、あれば作業中
 * 7. 上のどれにもヒットしない → 要確認（読めた欄をツールチップに出す）
 *
 * 親（親のレビュー状態はフェーズ一覧から見る。親カードの `gateClosed` は使わない）
 * 1. `blocked` か `scattered` → 要確認
 * 2. `pendingApproval` → 承認待ち（承認済みの親に改版が出ていれば「承認待ち（改版）」）
 * 3. 取り消し → 取り消し
 * 4. 完了・アーカイブ → 完了（早めに閉じていれば「完了（早めに終了）」）
 * 5. フェーズのどれかがレビュー待ち → レビュー待ち
 * 6. フェーズのどれかがレビュー準備中 → レビュー準備中
 * 7. `copyStatus === "open"` → 開始前／作業中
 * 8. 上のどれにもヒットしない → 要確認
 */
export function statusOf(card: StatusCard): Status {
  const trouble = troubleOf(card);
  if (trouble !== "") {
    return check(trouble);
  }
  if (card.pendingApproval) {
    const label = card.isParent && card.revision ? "承認待ち（改版）" : "承認待ち";
    return { label, title: "", tone: "turn" };
  }
  if (card.cancelledAt !== "") {
    return { label: "取り消し", title: "", tone: "cancel" };
  }
  if (card.isParent) {
    return parentStatus(card);
  }
  return childStatus(card);
}

function parentStatus(card: StatusCard): Status {
  if (closed(card)) {
    return { label: card.wrapped ? "完了（早めに終了）" : "完了", title: "", tone: "done" };
  }
  if (card.phases.some((p) => p.status === "レビュー待ち")) {
    return { label: "レビュー待ち", title: "", tone: "turn" };
  }
  if (card.phases.some((p) => p.status === "レビュー準備中")) {
    return { label: "レビュー準備中", title: "", tone: "wait" };
  }
  if (card.copyStatus === "open") {
    return card.startedAt === "" ? { label: "開始前", title: "", tone: "wait" } : { label: "作業中", title: "", tone: "work" };
  }
  return check(fieldsOf(card));
}

function childStatus(card: StatusCard): Status {
  if (closed(card)) {
    return { label: "完了", title: "", tone: "done" };
  }
  if (card.copyStatus === "review") {
    switch (card.phaseStatus) {
      case "レビュー待ち":
        return { label: "レビュー待ち", title: "", tone: "turn" };
      case "レビュー準備中":
        return { label: "レビュー準備中", title: "", tone: "wait" };
      case "レビュー予定":
        return { label: "レビュー予定", title: "", tone: "wait" };
      default:
        return check("レビュー待ちの置き場にあるが、フェーズの状態と合いません");
    }
  }
  if (card.copyStatus === "open") {
    if (card.reviewWaiting || card.gateClosed) {
      return check("作業中なのに、フェーズがレビューで止まっています");
    }
    return card.startedAt === "" ? { label: "開始前", title: "", tone: "wait" } : { label: "作業中", title: "", tone: "work" };
  }
  return check(fieldsOf(card));
}
