/**
 * カードとフェーズ行に出す言葉。判定は実行ファイルがやっていて、ここは JSON が言ったことを
 * 言い換えるだけ。マーカーや済みから状態を組み直さない。
 */
import { COLUMNS, type Card, type PhaseChip } from "../../core/board.js";
import type { Moved } from "../../core/board-moved.js";

export const COPY_LABELS = { none: "未承認", open: "承認済み", review: "レビュー待ち", closed: "クローズ", archived: "アーカイブ済み" } as const;

/** 承認済みチケットの着手の状態。`start` で `started_at` が入るまで、範囲は適用されない */
export const STARTED_LABELS = { unstarted: "未着手", started: "着手済み" } as const;
export const STARTED_TITLES = {
  unstarted: "承認済みですが、未着手です。start コマンドで着手時刻（started_at）が記録されるまで、このチケットの編集可能範囲は適用されません。",
  started: "着手済みです。このチケットの編集可能範囲が適用されています。",
} as const;

/** ツールバーの絞り込みのチェックボックスの言葉 */
export const FILTER_LABELS = {
  attention: "要対応のみ",
  attentionTitle: "ユーザが対応する必要があるカードだけを表示します（承認待ち・レビュー準備中／レビュー待ち・ワークツリーなし・HIGH 以上のリスク・不備）",
  archived: "アーカイブ済みチケットを表示",
  archivedTitle: "Draft を解除したとき（ccnavi-review.sh ready）にローカルの logs/archive/ に移動した、クローズ済みのチケットを「アーカイブ」の列に表示します。初期状態では表示しません。",
} as const;

export const MARK_LABELS: Readonly<Record<string, string>> = {
  pending: "エージェントに終了を通知済み",
  skipped: "レビュー省略",
  requested: "レビュー依頼済み",
  reviewed: "レビュー済み",
};

export const PHASE_STATE_LABELS = { planned: "未着手", active: "進行中", ended: "終了" } as const;

/** 列の呼び名。列の順序と同じ 1 か所（`core/board.ts` の `COLUMNS`）から引く */
const COLUMN_LABELS: Readonly<Record<string, string>> = Object.fromEntries(COLUMNS.map((c) => [c.state, c.label]));

/**
 * 前の読み直しから動いたカードに出す帯の言葉。「未着手 → 作業中」。
 * 前には無くて新しく現れたカードは、どこから来たとも言えないので「新規起票」だけ。
 * 行き先は言わない。カードはもうその列に置かれていて、画面から読める。
 */
export function movedLabel(moved: Moved): string {
  return moved.from === undefined ? "新規起票" : `${COLUMN_LABELS[moved.from] ?? moved.from} → ${COLUMN_LABELS[moved.to] ?? moved.to}`;
}

/** 親のカードの、issue・MR・ブランチ名の項目。無いものは出さない */
export function originItems(card: Card): string[] {
  const items: string[] = [];
  if (card.issueNumber !== null) {
    items.push(`issue #${card.issueNumber}`);
  }
  if (card.parentMr !== null) {
    items.push(`MR !${card.parentMr}`);
  }
  if (card.branch !== "") {
    items.push(`ブランチ ${card.branch}`);
  }
  return items;
}

/**
 * 親のカードの、リモートとの差。未送信・遅れ。ワークツリーが無い親（`remote` が無い）は出さない。
 * 未送信と遅れは最後に取ってきた origin との差なので古いかもしれない。読めなければ「不明」と言う。
 */
export function remoteItems(card: Card): string[] {
  const r = card.remote;
  if (r === null) {
    return [];
  }
  const count = (n: number | null): string => (n === null ? "不明" : n === 0 ? "なし" : `${n} 件`);
  return [`未送信 ${count(r.ahead)}`, `遅れ ${count(r.behind)}`];
}

/**
 * レビューが済むまで止めている間の呼び名。依頼を出す前はエージェントの番（合流・push・依頼）で
 * 「レビュー準備中」、出した後はユーザの番で「レビュー待ち」。実行ファイルの `phase.review_label` と
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

/**
 * フェーズ行の状態の項目のリスト。画面は ` · ` で区切って並べ、項目の途中では折り返さない。
 * `終了 · レビュー待ち · レビュー依頼済み · レビュー要 · リスク: 25 (MEDIUM) — …`
 */
export function phaseStatusItems(p: PhaseChip): string[] {
  const notes: string[] = [];
  if (p.gateClosed) {
    notes.push(holdLabel(p));
  }
  for (const m of p.marks) {
    notes.push(MARK_LABELS[m] ?? m);
  }
  if (p.reviewRequired) {
    notes.push("レビュー必須");
  }
  if (p.riskLine !== "") {
    notes.push(p.riskLine);
  }
  if (p.manual !== "") {
    notes.push(p.manual);
  }
  return [PHASE_STATE_LABELS[p.state], ...notes];
}

/** マージリクエストのバッジの文字。番号が読めなければ「マージリクエスト」だけ */
export function mrText(number: number | null): string {
  return number === null ? "マージリクエスト" : `マージリクエスト #${number}`;
}

/** 依頼のマーカーが持つ URL は中身を確かめずにそのまま取り込んであるので、http(s) のときだけリンクにする */
export function isHttpUrl(url: string): boolean {
  return /^https?:\/\//i.test(url);
}

/**
 * 承認画面の本文で、見出しの次の 1 行に説明が付く見出し。実行ファイルが置く文面と同じ表記
 * （`src/ccnavi/tickets/agree_screen.py` の `screen`）。番号が付く「課題」だけ前方一致で見る。
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

const EXPLAINED_HEAD_PREFIXES = ["■ 課題: #", "■ 先行: "];

/** 承認画面の本文の 1 行と、その行にまとめた説明 */
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
 * 着手済みの作業・`finish`・書き込みは止めない。先行ごとの状態は実行ファイルが付けた言葉（`label`）のまま出す。
 * `lead` と `ids` は `text` を分けたもので、画面が識別子の途中で折り返さないために使う
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
      title: `着手済みです。先行チケットが done/ にないか、取り消されているため、開始条件を満たしていません。ただし、着手済みの作業や finish コマンドの実行は制限されません。\n${detail}`,
    };
  }
  const what = card.copyStatus === "none" ? "承認・着手は" : "着手は";
  return {
    text: `先行待ち（${ids}）`,
    lead: "先行待ち",
    ids: list,
    title: `先行チケットが取り消されずに完了し、done/ に移動するまで、${what}できません\n${detail}`,
  };
}
