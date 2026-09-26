/**
 * カードとフェーズ行に出す言葉。判定は実行ファイルがやっていて、ここは JSON が言ったことを
 * 言い換えるだけ。マーカーや済みから状態を組み直さない（ADR-0035）。
 */
import { COLUMNS, type Card, type PhaseChip } from "../../core/board.js";
import type { Moved } from "../../core/board-moved.js";
import type { HistoryEntryJson } from "../../core/model.js";

export const COPY_LABELS = { none: "未承認", open: "承認済み", review: "レビュー待ち", closed: "クローズ" } as const;

export const MARK_LABELS: Readonly<Record<string, string>> = {
  pending: "エージェントに終了を通知済み",
  skipped: "レビュー省略",
  requested: "レビュー依頼済み",
  reviewed: "レビュー済み",
};

export const PHASE_STATE_LABELS = { planned: "未着手", active: "進行中", ended: "終了" } as const;

/** 列の呼び名。列の並びと同じ 1 か所（`core/board.ts` の `COLUMNS`）から引く */
const COLUMN_LABELS: Readonly<Record<string, string>> = Object.fromEntries(COLUMNS.map((c) => [c.state, c.label]));

/**
 * 前の読み直しから動いたカードに出す帯の言葉。「未着手 → 作業中」。
 * 前には無くて新しく現れたカードは、どこから来たとも言えないので「新規起票」だけ。
 * 行き先は言わない。カードはもうその列に置かれていて、画面から読める。
 */
export function movedLabel(moved: Moved): string {
  if (moved.from === undefined) {
    return "新規起票";
  }
  return `${COLUMN_LABELS[moved.from] ?? moved.from} → ${COLUMN_LABELS[moved.to] ?? moved.to}`;
}

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

/** フェーズ行の状態の全文。`終了 · レビュー待ち · レビュー依頼済み · レビュー要 · リスク: 25 (MEDIUM) — …` */
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

/**
 * 承認画面の本文で、見出しの次の 1 行に説明が付く見出し。実行ファイルが置く文面と同じ綴り
 * （`ccnavi/approval.py` の `screen`）。番号が付く「課題」だけ前方一致で見る。
 *
 * **畳むのはこの並びに載っている見出しの次の行だけ。** 知らない見出しなら何もしない。
 * 向こうの文面が変わったときに、本文の中身が黙って隠れるより、畳まれないほうが軽いため
 * （「エージェントが書いた理由」の本文を隠してはいけない）。
 */
const EXPLAINED_HEADS = new Set([
  "■ このチケットで編集可能な範囲",
  "■ この子チケットで編集可能な範囲",
  "■ チケットで編集対象としているが、書き込めない場所",
  "■ 全体計画",
  "■ 判定に効かない記述",
]);

const EXPLAINED_HEAD_PREFIXES = ["■ 課題: #", "■ 依存している他チケット: "];

/** 承認画面の本文の 1 行と、その行に畳んだ説明 */
export interface BodyLine {
  readonly line: string;
  /** 見出しに畳んだ説明。畳んでいなければ空 */
  readonly note: string;
}

/**
 * 本文を行に切り、説明の付く見出しには次の行を畳んで返す。端末には両方の行がそのまま出るが、
 * 画面では説明を見出しのツールチップに寄せて、本文を短く保つ。
 */
export function approvalBody(text: string): BodyLine[] {
  const lines = text.split("\n");
  const out: BodyLine[] = [];
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const next = lines[i + 1] ?? "";
    const explained = EXPLAINED_HEADS.has(line) || EXPLAINED_HEAD_PREFIXES.some((head) => line.startsWith(head));
    if (explained && next.startsWith("    ")) {
      out.push({ line, note: next.trim() });
      i += 1;
      continue;
    }
    out.push({ line, note: "" });
  }
  return out;
}

/** 履歴（ADR-0086）の置き場の呼び名。列の名前ではなく置き場の名前で言う（`review` は作業中の列にいる） */
const PLACE_LABELS: Readonly<Record<string, string>> = {
  todo: "承認待ち",
  doing: "作業中",
  review: "レビュー待ち",
  done: "完了",
};

/** 履歴の種類の呼び名。知らない種類は綴りのまま出す */
const HISTORY_KIND_LABELS: Readonly<Record<string, string>> = {
  approved: "承認",
  revised: "計画の改版",
  raised: "続きの子として起票",
  started: "着手",
  finished: "作業を終えた",
  cancelled: "取り消し",
  settled: "レビュー済みで閉じた",
  "phase-reopened": "マーカーを消した（子が足された）",
};

/** 親のマーカーの呼び名 */
const PARENT_MARK_LABELS: Readonly<Record<string, string>> = {
  ready: "Draft を外した",
  "close-early": "早期に締めた",
  closed: "親を閉じた",
};

/** 動かした経路の呼び名 */
export const VIA_LABELS: Readonly<Record<string, string>> = {
  cli: "エージェント",
  terminal: "端末",
  board: "ボード",
  hook: "hook",
};

/**
 * 履歴の 1 行の本文。「承認（承認待ち → 作業中）」「フェーズ 1: レビュー依頼済み」「取り消し（作業中 → 完了）: 理由」。
 * 実行ファイルが書いた跡を言い換えるだけで、ここから状態を組み直さない
 */
export function historyText(e: HistoryEntryJson): string {
  const phase = e.phase === null ? "" : `フェーズ ${e.phase}: `;
  if (e.kind === "phase-mark") {
    return `${phase}${MARK_LABELS[e.mark] ?? e.mark}`;
  }
  if (e.kind === "parent-mark") {
    return PARENT_MARK_LABELS[e.mark] ?? e.mark;
  }
  const label = HISTORY_KIND_LABELS[e.kind] ?? e.kind;
  const move =
    e.from !== "" && e.to !== "" && e.from !== e.to
      ? `（${PLACE_LABELS[e.from] ?? e.from} → ${PLACE_LABELS[e.to] ?? e.to}）`
      : e.from === "" && e.to !== ""
        ? `（→ ${PLACE_LABELS[e.to] ?? e.to}）`
        : "";
  const reason = e.reason !== "" ? `: ${e.reason}` : "";
  return `${e.kind === "phase-reopened" ? phase : ""}${label}${move}${reason}`;
}

/** 履歴の時刻。UTC の ISO 8601 を「2026-09-26 09:00 UTC」に。読めない綴りはそのまま */
export function historyAt(at: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2})?Z$/.exec(at);
  return m === null ? at : `${m[1]} ${m[2]} UTC`;
}
