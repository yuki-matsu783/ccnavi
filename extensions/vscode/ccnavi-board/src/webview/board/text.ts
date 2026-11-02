/**
 * カードとフェーズ行に出す言葉。判定は実行ファイルがやっていて、ここはJSONが言ったことを
 * 言い換えるだけ。マーカーや済みから状態を組み直さない。
 * 状態のバッジとフェーズ行の一言は`core/status.ts`。ここはバッジのほかの、注意の行と詳細の行の言葉を持つ。
 */
import { COLUMNS, type Card, type PhaseChip } from "../../core/board.js";
import type { Moved } from "../../core/board-moved.js";
import { isNotStarted } from "../../core/status.js";
import { MAX_WARN_LINES, truncateLines } from "../../core/lintmodel.js";
import type { ApproveRejected } from "../../core/approvemodel.js";

/** 承認対象外の提案の理由。実行ファイルの行頭の「error: 」「warn: 」と、見出しに出している識別子を外し、理由だけを残す */
export function rejectedReason(ticket: string, problem: string): string {
  const body = problem.replace(/^(error|warn): /, "");
  return body.startsWith(`${ticket}: `) ? body.slice(ticket.length + 2) : body;
}

/** 承認対象外の提案を、理由が同じものごとに畳んだ1行。`tickets`は先頭から最大3件、残りは`more` */
export interface RejectedGroup {
  readonly tickets: readonly string[];
  readonly more: number;
  readonly reasons: readonly string[];
}

const MAX_GROUP_TICKETS = 3;

/**
 * 子50件に同じ長文の理由が並ぶと画面が伸びるので、理由の並びが全文一致する提案を1つにまとめる。
 * 並びは先頭`MAX_WARN_LINES`組までで、超えた分は呼ぶ側が`truncateLines`の1行で足す。
 */
export function foldRejected(rejected: readonly ApproveRejected[]): { groups: RejectedGroup[]; more: number } {
  const byReasons = new Map<string, { tickets: string[]; reasons: string[] }>();
  for (const r of rejected) {
    const reasons = truncateLines(r.problems.map((p) => rejectedReason(r.ticket, p)));
    const key = JSON.stringify(reasons);
    const group = byReasons.get(key) ?? { tickets: [], reasons };
    group.tickets.push(r.ticket);
    byReasons.set(key, group);
  }
  const all = [...byReasons.values()].map((g) => ({
    tickets: g.tickets.slice(0, MAX_GROUP_TICKETS),
    more: Math.max(0, g.tickets.length - MAX_GROUP_TICKETS),
    reasons: g.reasons,
  }));
  return { groups: all.slice(0, MAX_WARN_LINES), more: Math.max(0, all.length - MAX_WARN_LINES) };
}

/** ツールバーの絞り込みのチェックボックスの言葉 */
export const FILTER_LABELS = {
  attention: "ユーザ待ちのみ",
  attentionTitle: "承認・レビューなど、ユーザの操作で進むものだけを出します",
  archived: "アーカイブ済みチケットを表示",
  archivedTitle: "Draftを解除したとき（ccnavi-review.sh ready）にローカルのlogs/archive/に移動した、完了・取り消しのチケットを「アーカイブ」の列に表示します。初期状態では表示しません。",
} as const;

/** 列の呼び名。列の順序と同じ1か所（`core/board.ts`の`COLUMNS`）から引く */
const COLUMN_LABELS: Readonly<Record<string, string>> = Object.fromEntries(COLUMNS.map((c) => [c.state, c.label]));

/**
 * 前の読み直しから動いたカードに出す帯の言葉。「未着手 → 作業中」。
 * 前には無くて新しく現れたカードは、どこから来たとも言えないので「新規起票」だけ。
 * 行き先は言わない。カードはもうその列に置かれていて、画面から読める。
 */
export function movedLabel(moved: Moved): string {
  return moved.from === undefined ? "新規起票" : `${COLUMN_LABELS[moved.from] ?? moved.from} → ${COLUMN_LABELS[moved.to] ?? moved.to}`;
}

/**
 * カードの注意の行。例外のときだけ短い言葉で出す（理由の全文は詳細）。
 * 不備・書き込み停止・先行待ち・リスクHIGH以上・複数の場所にある・ワークツリー未作成（承認待ち・開始前の子）・ワークツリーが見つからない（作業中・レビュー中）・未送信。
 * ユーザが動く必要がある状態だけで、普通のことは出さない。先行待ちは識別子の途中で折り返さないよう、`PredecessorNote`で別に出す
 */
export interface Note {
  readonly key: "issue" | "blocked" | "risk" | "seen" | "worktree" | "unsent";
  readonly text: string;
  /** バッジの色の系統（CSSのクラス） */
  readonly kind: string;
}

export function notesOf(card: Card): Note[] {
  const notes: Note[] = [];
  if (card.issues.length > 0) {
    notes.push({ key: "issue", text: `不備${card.issues.length}件`, kind: "issue" });
  }
  if (card.blocked !== "") {
    notes.push({ key: "blocked", text: "書き込み停止中", kind: "blocked" });
  }
  if (!card.worktreeExists && card.copyStatus !== "closed" && card.copyStatus !== "archived") {
    // 承認待ち（none）と開始前はまだ作っていないだけ。作業中・レビュー中に無いのは異常（warnの見た目）
    const pending = card.copyStatus === "none" || isNotStarted(card);
    notes.push({ key: "worktree", text: pending ? "ワークツリー未作成" : "ワークツリーが見つからない", kind: pending ? "worktree" : "worktree none" });
  }
  if (isHighRisk(card.riskLevel)) {
    notes.push({ key: "risk", text: riskText(card), kind: `risk risk-${card.riskLevel.toLowerCase()}` });
  }
  // ほかのツリー上にもチケットがあること自体は普通なので数では出さない。どれが正か決まらないときだけ言う。
  if (card.scattered.length > 0) {
    notes.push({ key: "seen", text: `複数の場所にある（${card.scattered.length}か所）`, kind: "seen" });
  }
  if (card.remote !== null && card.remote.ahead !== null && card.remote.ahead > 0) {
    notes.push({ key: "unsent", text: `未送信${card.remote.ahead}件`, kind: "unsent" });
  }
  return notes;
}

/** 詳細の1行。`href`があれば普通のリンク（ボタンの寸法にしない）。値が複数なら1行ずつ */
export interface DetailRow {
  readonly key: string;
  readonly label: string;
  readonly values: readonly string[];
  readonly href?: string;
  readonly title?: string;
}

/**
 * 詳細に畳む項目。issue・ブランチ・ワークツリー名・プロジェクト・取り消しの理由と、
 * 注意の行に出した例外の理由の全文、リスク（JSONの文言のまま）、人間レビューの要否と理由、フェーズごとのレビューの要否とリスク。
 * MRは詳細に入れず、カード本体の位置の下に出す（`Card.tsx`の`MrLink`）。
 * base / 合流のshaとマーカー名は画面に出さない（チケットのファイルにだけ残る）。
 * 出すものが無い行は作らない。
 */
export function detailRows(card: Card): DetailRow[] {
  const rows: DetailRow[] = [];
  const add = (row: DetailRow): void => {
    if (row.values.some((v) => v !== "")) {
      rows.push(row);
    }
  };
  add({ key: "issues", label: "不備", values: card.issues });
  add({ key: "blocked", label: "書き込み停止の理由", values: [card.blocked] });
  add({
    key: "scattered",
    label: "複数の場所",
    values: card.scattered.map((s) => `${s.tree || "main"}:${s.state}`),
  });
  add({
    key: "predecessors",
    label: "先行",
    values: card.predecessorsUnmet.map((p) => `${p.ticket}: ${p.label}`),
  });
  if (card.column !== "archived") {
    add({
      key: "review",
      label: "人間レビュー",
      values: [`${card.reviewRequired === null ? "宣言なし" : card.reviewRequired ? "必須" : "不要"}${card.reviewReason === "" ? "" : `（${card.reviewReason}）`}`],
    });
  }
  add({ key: "risk", label: "リスク", values: card.riskLevel === "" ? [] : [riskText(card)] });
  for (const p of card.phases) {
    add({ key: `phase-${p.number}`, label: `フェーズ${p.label}`, values: [phaseDetail(p)] });
  }
  add({ key: "parent-mr", label: "親のMR", values: card.parentMr === null ? [] : [`!${card.parentMr}`] });
  add({ key: "issue", label: "issue", values: card.issueNumber === null ? [] : [`#${card.issueNumber}`] });
  add({ key: "branch", label: "ブランチ", values: [card.branch] });
  add({
    key: "worktree",
    label: "ワークツリー",
    values: card.worktreeExists ? [worktreeName(card.worktreePath)] : [],
    title: card.worktreePath,
  });
  add({ key: "remote", label: "リモートとの差", values: remoteItems(card) });
  add({ key: "project", label: "プロジェクト", values: [card.project] });
  add({ key: "cancel", label: "取り消しの理由", values: [card.cancelReason] });
  return rows;
}

/** フェーズごとの詳細（レビューの要否・リスク）。JSONの文言をそのまま使う */
function phaseDetail(p: PhaseChip): string {
  const parts = [p.reviewRequired ? "レビュー必須" : "レビュー不要"];
  if (p.riskLine !== "") {
    parts.push(p.riskLine);
  }
  return parts.join(" · ");
}

/**
 * 親のカードの、リモートとの差。未送信・遅れ。ワークツリーが無い親（`remote`が無い）は出さない。
 * 未送信と遅れは最後に取ってきたoriginとの差なので古いかもしれない。読めなければ「不明」と言う。
 */
export function remoteItems(card: Card): string[] {
  const r = card.remote;
  if (r === null) {
    return [];
  }
  const count = (n: number | null): string => (n === null ? "不明" : n === 0 ? "なし" : `${n}件`);
  return [`未送信${count(r.ahead)}`, `遅れ${count(r.behind)}`];
}

export function riskText(card: Card): string {
  return card.riskPoints === null ? `リスク${card.riskLevel}` : `リスク${card.riskLevel}（${card.riskPoints}点）`;
}

export function isHighRisk(level: string): boolean {
  return level === "HIGH" || level === "CRITICAL";
}

/** ワークツリーの置き場の末尾（`.claude/worktrees/<名前>`の名前）。読めなければ「あり」 */
export function worktreeName(path: string): string {
  const name = path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() ?? "";
  return name === "" ? "あり" : name;
}

/** MRのリンクの文字。番号が読めなければ「MR」だけ */
export function mrText(number: number | null): string {
  return number === null ? "MR" : `MR #${number}`;
}

/** 依頼のマーカーが持つURLは中身を確かめずにそのまま取り込んであるので、http(s)のときだけリンクにする */
export function isHttpUrl(url: string): boolean {
  return /^https?:\/\//i.test(url);
}

/**
 * 承認画面の本文で、見出しの次の1行に説明が付く見出し。実行ファイルが置く文面と同じ表記
 * （`src/ccnavi/tickets/proposal/agree_screen.py`の`screen`）。番号が付く「issue」だけ前方一致で見る。
 *
 * まとめるのはこのリストに載っている見出しの次の行だけ。 知らない見出しなら何もしない。
 * 向こうの文面が変わったときに、本文の中身が気づかないうちに隠れるより、まとめられないほうが軽いため
 * （「エージェントが記載した理由」の本文を隠してはいけない）。
 */
const EXPLAINED_HEADS = new Set([
  "■ このチケットで編集可能な範囲",
  "■ この子チケットで編集可能な範囲",
  "■ チケットで編集対象としているが、書き込めない場所",
  "■ 全体計画",
  "■ 判定に効かない記述",
]);

const EXPLAINED_HEAD_PREFIXES = ["■ issue: #", "■ 先行: "];

/** 承認画面の本文の1行と、その行にまとめた説明 */
export interface BodyLine {
  readonly line: string;
  /** 見出しにまとめた説明。まとめていなければ空 */
  readonly note: string;
}

/**
 * 本文を行に切り、説明の付く見出しには次の行をまとめて返す。ターミナルには両方の行がそのまま出るが、
 * 画面では説明を見出しのツールチップにまとめて、本文を短く保つ。
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

/**
 * 先行を満たしていないカードのバッジ。何が止まるかはカードの今で分ける。止めるのは承認と着手（`start`）だけで、
 * 作業中の作業・`finish`・書き込みは止めない。先行ごとの状態は実行ファイルが付けた言葉（`label`）のまま出す。
 * `lead`と`ids`は`text`を分けたもので、画面が識別子の途中で折り返さないために使う
 */
export function predecessorsBadge(card: Card): { readonly text: string; readonly lead: string; readonly ids: readonly string[]; readonly title: string } {
  const list = card.predecessorsUnmet.map((p) => p.ticket);
  const ids = list.join(", ");
  const detail = card.predecessorsUnmet.map((p) => `${p.ticket}: ${p.label}`).join("\n");
  const started = card.startedAt !== "" || card.copyStatus === "review";
  if (started) {
    return {
      text: `先行チケットの条件が未達成（${ids}）`,
      lead: "先行チケットの条件が未達成",
      ids: list,
      title: `作業を始めています。先行チケットがdone/にないか取り消されているため、開始条件を満たしていません。ただし、作業とfinishコマンドの実行は制限されません。\n${detail}`,
    };
  }
  const what = card.copyStatus === "none" ? "承認・着手は" : "着手は";
  return {
    text: `先行待ち（${ids}）`,
    lead: "先行待ち",
    ids: list,
    title: `先行チケットが取り消されずに完了し、done/に移動するまで、${what}できません\n${detail}`,
  };
}
