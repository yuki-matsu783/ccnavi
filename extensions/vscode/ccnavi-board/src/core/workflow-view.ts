/**
 * ワークフロー編集タブの、拡張ホストと Webview の間の契約。フロー編集（`flow-view.ts`）と同じ作り。
 *
 * タブは親の提案（`todo/` にある計画付きの提案）ごとに 1 枚で、計画の項どうしの先行（線）を図で引き直す。
 * 拡張ホストが渡すのは「いま何を見せるか」（`WorkflowData`）だけで、画面が返すのはユーザが押した操作
 * （`WorkflowMessage`）だけ。
 *
 * **画面は判定しない。番号も振り直さない。** 線を変えるたびに、画面は元の番号（いま提案に書いてある番号）での
 * 直接の先行（`PlanOrder`。`--order` の形）を組んで拡張ホストに頼み（`order`）、拡張ホストが
 * `ccnavi --plan-order <親> --json --order <JSON>` の答えを返す（`answer`）。振り直した番号・順序の検査
 * （`problems`）・終端に当たらない項（`loose`）・書けるか（`writable`）はその答えのまま描く。断られた線
 * （`refused`）は、線を引く前の答えに戻す。ここにある関数は、見えている番号を元の番号に直すこと（`editedOrder`）と、
 * 答えを見せる文の材料（`renumbered`・`planDiff`・`saveBlocker`）だけで、計画の良し悪しを決めない。
 *
 * **未保存は答えから決める。** 答えの線を元の番号に戻したもの（`orderOf`）が、提案に書いてある先行（`proposed`）と
 * 違えば未保存（`isDirty`）。画面が別に「変えたか」を持たないので、案に戻すと必ず消える。
 *
 * この画面は `retainContextWhenHidden: true`（編集の途中を持つ）。渡し方は `retainedHost` で、入れ物は 1 度しか入らない。
 * 中身（`data`）が届くのは、画面の編集を捨ててよいとき（再読込・取り込む・保存が通った）だけ。
 *
 * ここには VS Code の API も DOM も node も入れない。
 */
import type { AppearanceMessage } from "./appearance.js";
import type { ApprovePlan } from "./approvemodel.js";
import type { PlanOrder, PlanOrderAnswer } from "./planordermodel.js";
import { embedJson, type DataMessage } from "./screen-host.js";
import { isTicketId } from "./ticket-id.js";

/** 計画の欄の名前。`--order` が受けるのはこの 2 つだけ */
export const PARTS = ["plan", "feedback"] as const;

export interface WorkflowPage {
  readonly root: string;
  /** 親の識別子 */
  readonly ticket: string;
  /** 開いたとき（`--order` なし）の答え。提案のままの順序で、「案に戻す」はここへ戻る */
  readonly answer: PlanOrderAnswer;
  /**
   * 未保存のまま閉じた画面から戻す線（元の番号での先行）。あれば画面は開いてすぐこの線で答えを頼み、未保存にする
   */
  readonly draft?: PlanOrder;
}

export type WorkflowData =
  | { readonly kind: "page"; readonly page: WorkflowPage }
  | { readonly kind: "error"; readonly error: string }
  | { readonly kind: "loading"; readonly text: string };

/** 拡張ホスト → 画面。中身を包む形は `screen-host.ts` が決める */
export type ToWorkflow =
  | DataMessage<WorkflowData>
  /** 頼まれた線（`order`）への実行ファイルの答え。`seq` は頼んだときの番号。確かめられなければ `error` */
  | { readonly type: "answer"; readonly seq: number; readonly answer?: PlanOrderAnswer; readonly error?: string }
  /** 頼んだ操作（保存など）ができなかった。画面はボタンを戻して理由を出す */
  | { readonly type: "failed"; readonly message: string }
  /** 頼んだ往復が起きなかった（ユーザが確認をやめた）。画面はボタンを戻す */
  | { readonly type: "cancelled" }
  /** 親か子の提案が外で変わった（開いたときのハッシュと違う）。`answer` は今の提案のまま（`--order` なし）の答え */
  | { readonly type: "changed"; readonly answer: PlanOrderAnswer }
  /** 提案が承認待ち（`todo/`）から無くなった（承認・取り下げ・削除）。画面は読むだけになる */
  | { readonly type: "gone"; readonly reason: string }
  | AppearanceMessage;

/** 画面 → 拡張ホスト。受け側は `asWorkflowMessage` で形を確かめてから使う */
export type WorkflowMessage =
  | { readonly type: "ready" }
  | { readonly type: "reload"; readonly dirty: boolean }
  | { readonly type: "dirty"; readonly dirty: boolean }
  /** 線を変えた。この先行で実行ファイルに振り直させる（書きはしない）。答えは `answer` */
  | { readonly type: "order"; readonly seq: number; readonly order: PlanOrder }
  /** 「この順序に決める」。`expect` は開いたときの `source_sha` */
  | { readonly type: "save"; readonly order: PlanOrder; readonly expect: string }
  /** 未保存の線の写し。未保存のまま閉じられたときに戻すため、拡張ホストが覚えておく。未保存でなくなったら null */
  | { readonly type: "draft"; readonly order: PlanOrder | null }
  /** 外で変わった提案を取り込む（編集を捨てて新しい提案から始める） */
  | { readonly type: "adopt" };

/** 画面から届いた `--order` の形を確かめる。計画は `plan` / `feedback`、番号は数字の文字列、先行は 1 以上の整数 */
export function asPlanOrder(value: unknown): PlanOrder | undefined {
  if (!isRecord(value)) {
    return undefined;
  }
  const out: Record<string, Record<string, number[]>> = {};
  for (const [part, numbers] of Object.entries(value)) {
    if (!(PARTS as readonly string[]).includes(part) || !isRecord(numbers)) {
      return undefined;
    }
    const entries: Record<string, number[]> = {};
    for (const [n, before] of Object.entries(numbers)) {
      if (!/^[0-9]+$/.test(n) || !Array.isArray(before) || !before.every(isNumber)) {
        return undefined;
      }
      entries[n] = [...before];
    }
    out[part] = entries;
  }
  return out;
}

/** 画面から届いたものを確かめる。**形が崩れていたら捨てる**（保存の中身が読めないものを渡さない） */
export function asWorkflowMessage(message: unknown): WorkflowMessage | undefined {
  if (!isRecord(message)) {
    return undefined;
  }
  switch (message.type) {
    case "ready":
    case "adopt":
      return { type: message.type };
    case "reload":
      return { type: "reload", dirty: message.dirty === true };
    case "dirty":
      return typeof message.dirty === "boolean" ? { type: "dirty", dirty: message.dirty } : undefined;
    case "order": {
      const order = asPlanOrder(message.order);
      return order === undefined || typeof message.seq !== "number" || !Number.isFinite(message.seq) ? undefined : { type: "order", seq: message.seq, order };
    }
    case "save": {
      const order = asPlanOrder(message.order);
      return order === undefined || typeof message.expect !== "string" || message.expect === "" ? undefined : { type: "save", order, expect: message.expect };
    }
    case "draft": {
      if (message.order === null) {
        return { type: "draft", order: null };
      }
      const order = asPlanOrder(message.order);
      return order === undefined ? undefined : { type: "draft", order };
    }
    default:
      return undefined;
  }
}

/** 開くコマンドに渡された親の識別子。識別子の形でなければ undefined（開く先は実行ファイルが引き直す） */
export function workflowTicketOf(value: unknown): string | undefined {
  return isTicketId(value) ? value : undefined;
}

/** タブの題。未保存の間は頭に「●」 */
export function workflowTitle(ticket: string, dirty = false): string {
  return `${dirty ? "● " : ""}ccnavi ワークフロー: ${ticket}`;
}

// ---- 答えを画面の状態に整える

/** 番号 → 先行の写しを、番号も先行も小さい順に並べ、重なりを落とす（見比べるため） */
function sorted(map: Readonly<Record<string, readonly number[]>>, numbers: readonly number[]): Record<string, number[]> {
  const out: Record<string, number[]> = {};
  for (const n of [...numbers].sort((a, b) => a - b)) {
    out[String(n)] = [...new Set(map[String(n)] ?? [])].sort((a, b) => a - b);
  }
  return out;
}

/** 計画 1 つの線を、元の番号での直接の先行に戻す。全部の番号を並べ、先行の無い番号は空 */
function partOrder(plan: ApprovePlan): Record<string, number[]> {
  const fromOf = new Map(plan.items.map((item) => [item.number, item.from]));
  const raw: Record<string, number[]> = {};
  for (const item of plan.items) {
    raw[String(item.from)] = (plan.after[String(item.number)] ?? []).map((m) => fromOf.get(m)).filter((m): m is number => m !== undefined);
  }
  return sorted(
    raw,
    plan.items.map((item) => item.from),
  );
}

/** 提案に書いてある先行（元の番号）。全部の番号を並べる */
function proposedOrder(plan: ApprovePlan): Record<string, number[]> {
  return sorted(
    plan.proposed,
    plan.items.map((item) => item.from),
  );
}

function sameOrder(a: Readonly<Record<string, readonly number[]>>, b: Readonly<Record<string, readonly number[]>>): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** 答えの線を、元の番号（いま提案に書いてある番号）での直接の先行に戻す（両方の計画） */
export function orderOf(answer: PlanOrderAnswer): PlanOrder {
  const out: Record<string, Record<string, number[]>> = {};
  for (const plan of answer.plans) {
    out[plan.part] = partOrder(plan);
  }
  return out;
}

/** 図で変えた線 1 本。番号は図に見えている番号（振り直したあとの番号）。`to` が `from` を待つ */
export interface PlanEdit {
  readonly part: string;
  readonly from: number;
  readonly to: number;
  /** 真なら足す、偽なら消す */
  readonly add: boolean;
}

/**
 * いまの答えに線を 1 本足す・消した先行を、`--order` の形で組む。見えている番号を元の番号に直すだけで、振り直さない。
 * 提案と同じになった計画は渡さない（何も渡さなければ提案のまま）。番号が計画に無ければ undefined
 */
export function editedOrder(answer: PlanOrderAnswer, edit: PlanEdit): PlanOrder | undefined {
  const target = answer.plans.find((plan) => plan.part === edit.part);
  const fromOf = new Map((target?.items ?? []).map((item) => [item.number, item.from]));
  const from = fromOf.get(edit.from);
  const to = fromOf.get(edit.to);
  if (target === undefined || from === undefined || to === undefined || from === to) {
    return undefined;
  }
  const out: Record<string, Record<string, number[]>> = {};
  for (const plan of answer.plans) {
    let order = partOrder(plan);
    if (plan.part === edit.part) {
      const before = (order[String(to)] ?? []).filter((m) => m !== from);
      order = sorted(
        { ...order, [String(to)]: edit.add ? [...before, from] : before },
        plan.items.map((item) => item.from),
      );
    }
    if (!sameOrder(order, proposedOrder(plan))) {
      out[plan.part] = order;
    }
  }
  return out;
}

/**
 * 答えの線のうち、提案と違う計画だけを `--order` の形で返す（保存と、未保存のまま閉じたときの写しに使う）。
 * 提案のままなら空
 */
export function changedOrder(answer: PlanOrderAnswer): PlanOrder {
  const out: Record<string, Record<string, number[]>> = {};
  for (const plan of answer.plans) {
    const order = partOrder(plan);
    if (!sameOrder(order, proposedOrder(plan))) {
      out[plan.part] = order;
    }
  }
  return out;
}

/** 未保存か。答えの線が、提案に書いてある先行と違う計画があれば真 */
export function isDirty(answer: PlanOrderAnswer): boolean {
  return answer.plans.some((plan) => !sameOrder(partOrder(plan), proposedOrder(plan)));
}

/** 番号の変わる項（保存の前に見せる） */
export interface Renumbered {
  readonly part: string;
  readonly title: string;
  readonly from: number;
  readonly number: number;
}

export function renumbered(answer: PlanOrderAnswer): Renumbered[] {
  return answer.plans.flatMap((plan) =>
    plan.items.filter((item) => item.number !== item.from).map((item) => ({ part: plan.part, title: item.title || item.type, from: item.from, number: item.number })),
  );
}

/** 外で変わった提案の差分の 1 行。番号ごとに、定義と待ちの前後 */
export interface PlanDiffRow {
  readonly part: string;
  readonly number: number;
  readonly before: string;
  readonly after: string;
}

/** 項 1 つを 1 行の文にする。「題（定義）: 待つ 1, 2」。延期なら「・延期」を添える */
function itemText(plan: ApprovePlan | undefined, number: number): string {
  const item = plan?.items.find((i) => i.number === number);
  if (plan === undefined || item === undefined) {
    return "（無い）";
  }
  const waits = plan.after[String(number)] ?? [];
  return `${item.title || item.type}（${item.type}）${item.deferred ? "・延期" : ""}: ${waits.length === 0 ? "何も待たない" : `待つ ${waits.join(", ")}`}`;
}

/** 開いたときの提案と、外で変わったあとの提案の差分。変わらない番号は並べない */
export function planDiff(before: readonly ApprovePlan[], after: readonly ApprovePlan[]): PlanDiffRow[] {
  const rows: PlanDiffRow[] = [];
  for (const part of PARTS) {
    const old = before.find((plan) => plan.part === part);
    const now = after.find((plan) => plan.part === part);
    const numbers = [...new Set([...(old?.items ?? []), ...(now?.items ?? [])].map((item) => item.number))].sort((a, b) => a - b);
    for (const number of numbers) {
      const was = itemText(old, number);
      const is = itemText(now, number);
      if (was !== is) {
        rows.push({ part, number, before: was, after: is });
      }
    }
  }
  return rows;
}

/** 画面の状態のうち、保存を止めるもの */
export interface SaveState {
  /** 線を変えて、実行ファイルの答えを待っている */
  readonly pending: boolean;
  /** 提案が外で変わった（取り込むまで保存できない） */
  readonly changed: boolean;
  /** 提案が承認待ちから無くなった */
  readonly gone: boolean;
}

/**
 * 保存できない理由。空なら保存できる。書けるかは実行ファイルの `writable` のまま読み、理由の言葉を選ぶだけ
 */
export function saveBlocker(answer: PlanOrderAnswer, state: SaveState): string {
  if (state.gone) {
    return "提案が承認待ちにありません";
  }
  if (state.changed) {
    return "提案が外で変わったため、保存できません。「取り込む」で新しい提案から始めてください";
  }
  if (state.pending) {
    return "線を変えた順序を ccnavi に確かめています";
  }
  if (!isDirty(answer)) {
    return "順序を変えていません";
  }
  if (answer.writable) {
    return "";
  }
  if (answer.child_problems.length > 0) {
    return "子の提案を付け替えられないため、保存できません";
  }
  if (answer.revision_problems.length > 0) {
    return "改版で通らない形（子が承認された番号の錠）のため、保存できません";
  }
  return "順序の検査に error があるため、保存できません";
}

/** 最初の中身を埋める `<script type="application/json">` の id */
export const DATA_ID = "ccnavi-workflow-data";

export function embedData(data: WorkflowData): string {
  return embedJson(data);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 1;
}
