/**
 * テンプレートの編集の手元の写し。テンプレート管理画面の編集欄と拡張ホストが分け合う。
 *
 * ファイルは YAML の 1 文書（`.ccnavi/config/<層>/templates/<名前>.yml`）。形は実行ファイル（`plan_templates.py`）が読むもの。
 *
 *     name, description, when?
 *     plan:     [型の名前 | {type, review?, after?}, ...]     全体計画。`after` は計画の中の添字（1 始まり）
 *     feedback: [同上]                                        フィードバック計画。`after` もこの計画の中の添字
 *     flows:    {全体計画の添字: フローの本文}                  子のフロー（フロー編集画面と同じ形）
 *
 * 画面が持つのは、実行ファイルが読んで返した中身（`--templates --check-file` の `data`）のコピー。編集はそのコピーを作り直すだけで、
 * 項の `after` は「その計画の中の添字」のまま持つ。**番号の付け直しを伴う編集（項の削除・並べ替え・線を引く）は、先行が必ず
 * 自分より小さい添字になるように、ここで添字を振り直す**（実行ファイルの `after` の決まり。守らなければ error になる）。
 *
 * 書き出しは中身から組み直す（コメントや書き方は残らない）。書いたものが実行ファイルに同じ中身で読まれるかは、保存の前に
 * 実行ファイルに読ませて見比べる（`sameData`）。読めるか・計画とフローの形が正しいかは実行ファイルが言い、画面は判定しない。
 *
 * ここには VS Code の API も node も DOM も入れない。画面（React）がバンドルして読むため。
 */
import type { FlowDoc } from "./flow-doc.js";
import { yamlText } from "./flow-doc.js";

/** 計画の 2 つ。全体計画（`plan`）とフィードバック計画（`feedback`） */
export type PlanPart = "plan" | "feedback";

/** 計画の 1 項。`after` は計画の中の添字（1 始まり） */
export interface EditItem {
  readonly type: string;
  /** 項の `review`（`mr` / `defer`）。既定は空 */
  readonly review: string;
  readonly after: readonly number[];
}

/** 編集中のテンプレート */
export interface EditTemplate {
  readonly name: string;
  readonly description: string;
  readonly when: string;
  readonly plan: readonly EditItem[];
  readonly feedback: readonly EditItem[];
  /** 全体計画の添字（文字列）→ フローの本文 */
  readonly flows: Readonly<Record<string, FlowDoc>>;
  /** 画面が知らない欄。消さずに書き戻す */
  readonly extra: Readonly<Record<string, unknown>>;
}

/** 項の `review` の選べる値（空は定義の既定） */
export const ITEM_REVIEWS = ["", "mr", "defer"] as const;

const KNOWN = new Set(["name", "description", "when", "plan", "feedback", "flows"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readItems(value: unknown): EditItem[] | undefined {
  if (value === undefined || value === null) {
    return [];
  }
  if (!Array.isArray(value)) {
    return undefined;
  }
  const out: EditItem[] = [];
  for (const entry of value) {
    if (typeof entry === "string") {
      out.push({ type: entry, review: "", after: [] });
    } else if (isRecord(entry) && typeof entry.type === "string") {
      const after = Array.isArray(entry.after) ? entry.after.filter((n): n is number => typeof n === "number") : [];
      out.push({ type: entry.type, review: typeof entry.review === "string" ? entry.review : "", after: [...new Set(after)].sort((a, b) => a - b) });
    } else {
      return undefined;
    }
  }
  return out;
}

/** 実行ファイルが読んで返した中身（`data`）から編集の写しを作る。形が違えば undefined（編集を始めない） */
export function fromData(raw: unknown): EditTemplate | undefined {
  if (!isRecord(raw) || typeof raw.name !== "string") {
    return undefined;
  }
  const plan = readItems(raw.plan);
  const feedback = readItems(raw.feedback);
  if (plan === undefined || feedback === undefined) {
    return undefined;
  }
  const flows: Record<string, FlowDoc> = {};
  if (isRecord(raw.flows)) {
    for (const [key, body] of Object.entries(raw.flows)) {
      if (!isRecord(body)) {
        return undefined;
      }
      flows[key] = body as unknown as FlowDoc;
    }
  }
  const extra: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(raw)) {
    if (!KNOWN.has(key)) {
      extra[key] = value;
    }
  }
  return {
    name: raw.name,
    description: typeof raw.description === "string" ? raw.description : "",
    when: typeof raw.when === "string" ? raw.when : "",
    plan,
    feedback,
    flows,
    extra,
  };
}

/** 空の名前から始める新しいテンプレート。`type` は最初の項の型 */
export function blankTemplate(name: string, type: string): EditTemplate {
  return { name, description: "", when: "", plan: [{ type, review: "", after: [] }], feedback: [], flows: {}, extra: {} };
}

function itemData(item: EditItem): unknown {
  const type = item.type.trim();
  const review = item.review.trim();
  if (review === "" && item.after.length === 0) {
    return type;
  }
  const out: Record<string, unknown> = { type };
  if (review !== "") {
    out.review = review;
  }
  if (item.after.length > 0) {
    out.after = [...item.after].sort((a, b) => a - b);
  }
  return out;
}

/**
 * 書き出す中身（実行ファイルの `Template.as_data()` と同じ形・同じ順）。文字は実行ファイルが読むときと同じに前後の空白を取る。
 * フィードバック計画とフローは、空なら欄ごと省く
 */
export function toData(template: EditTemplate): Record<string, unknown> {
  const out: Record<string, unknown> = { name: template.name.trim(), description: template.description.trim() };
  if (template.when.trim() !== "") {
    out.when = template.when.trim();
  }
  out.plan = template.plan.map(itemData);
  if (template.feedback.length > 0) {
    out.feedback = template.feedback.map(itemData);
  }
  const keys = Object.keys(template.flows)
    .filter((key) => template.flows[key] !== undefined)
    .sort((a, b) => Number(a) - Number(b));
  if (keys.length > 0) {
    const flows: Record<string, unknown> = {};
    for (const key of keys) {
      flows[key] = template.flows[key];
    }
    out.flows = flows;
  }
  Object.assign(out, template.extra);
  return out;
}

/** YAML の文面。保存のときだけ使う */
export function serializeTemplate(template: EditTemplate): string {
  return yamlText(toData(template));
}

/** キーを並べ直した JSON。中身の比べ合いに使う（キーの順は無視する） */
function canonical(value: unknown): string {
  return JSON.stringify(value, (_key, v: unknown) => {
    if (isRecord(v)) {
      return Object.fromEntries(Object.entries(v).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
    }
    return v;
  });
}

/** 書こうとした中身と、実行ファイルが読み返した中身（`data`）が同じか */
export function sameData(intended: unknown, read: unknown): boolean {
  return canonical(JSON.parse(JSON.stringify(intended))) === canonical(read);
}

// ---- 番号（図の項の番号は、フィードバック計画が全体計画の続き）

/** 図の番号 → 計画と添字（1 始まり）。範囲外は undefined */
export function locate(template: EditTemplate, number: number): { readonly part: PlanPart; readonly index: number } | undefined {
  const planned = template.plan.length;
  if (number >= 1 && number <= planned) {
    return { part: "plan", index: number };
  }
  if (number > planned && number <= planned + template.feedback.length) {
    return { part: "feedback", index: number - planned };
  }
  return undefined;
}

function withPart(template: EditTemplate, part: PlanPart, items: readonly EditItem[], flows?: Readonly<Record<string, FlowDoc>>): EditTemplate {
  return { ...template, [part]: items, ...(flows === undefined ? {} : { flows }) };
}

function partOf(template: EditTemplate, part: PlanPart): readonly EditItem[] {
  return template[part];
}

// ---- 項の編集（どれも新しいコピーを返す。できない編集は元のまま返す）

/** 項の型・`review` を差し替える */
export function setItem(template: EditTemplate, part: PlanPart, index: number, patch: { readonly type?: string; readonly review?: string }): EditTemplate {
  const items = partOf(template, part);
  if (index < 1 || index > items.length) {
    return template;
  }
  return withPart(
    template,
    part,
    items.map((item, i) => (i === index - 1 ? { ...item, ...(patch.type === undefined ? {} : { type: patch.type }), ...(patch.review === undefined ? {} : { review: patch.review }) } : item)),
  );
}

/** 末尾に項を足す。既定では前の末尾の項を待たせる（順に進む形）。計画が空なら何も待たない */
export function addItem(template: EditTemplate, part: PlanPart, type: string): EditTemplate {
  const items = partOf(template, part);
  const after = items.length === 0 ? [] : [items.length];
  return withPart(template, part, [...items, { type, review: "", after }]);
}

/** 項を `order`（新しい添字の順に、古い添字を並べたもの。入れない古い添字は落とす）で並べ直す。全体計画ならフローのキーも合わせる */
function remap(template: EditTemplate, part: PlanPart, order: readonly number[]): EditTemplate {
  // order[新しい添字-1] = 古い添字
  const items = partOf(template, part);
  const toNew = new Map<number, number>();
  order.forEach((old, i) => toNew.set(old, i + 1));
  const next = order.map((old) => {
    const item = items[old - 1];
    return { ...item, after: item.after.map((n) => toNew.get(n)).filter((n): n is number => n !== undefined).sort((a, b) => a - b) };
  });
  if (part !== "plan") {
    return withPart(template, part, next);
  }
  const flows: Record<string, FlowDoc> = {};
  for (const [key, body] of Object.entries(template.flows)) {
    const moved = toNew.get(Number(key));
    if (moved !== undefined) {
      flows[String(moved)] = body;
    }
  }
  return withPart(template, part, next, flows);
}

/**
 * 項を消す。全体計画の最後の 1 項は消せない（計画が空になる）。消す項を待っていた項は、消す項の先行を引き継いで待つ
 * （途中の項を消しても、後ろの項が前の項と並行して始まる項に変わらない）。後ろの項の添字と、フローのキーを振り直す
 */
export function removeItem(template: EditTemplate, part: PlanPart, index: number): EditTemplate {
  const items = partOf(template, part);
  if (index < 1 || index > items.length || (part === "plan" && items.length <= 1)) {
    return template;
  }
  const inherited = items[index - 1].after;
  const bridged = items.map((item) =>
    item.after.includes(index) ? { ...item, after: [...new Set([...item.after, ...inherited])].filter((n) => n !== index).sort((a, b) => a - b) } : item,
  );
  const order = items.map((_, i) => i + 1).filter((i) => i !== index);
  return remap(withPart(template, part, bridged), part, order);
}

/** 先行が必ず自分より小さい添字か */
function ordered(items: readonly EditItem[]): boolean {
  return items.every((item, i) => item.after.every((n) => n >= 1 && n < i + 1));
}

/** 項を 1 つ前（`delta` = -1）か後ろ（+1）へ動かせるか。先行か後続を越える動きはできない */
export function canMove(template: EditTemplate, part: PlanPart, index: number, delta: -1 | 1): boolean {
  const items = partOf(template, part);
  const to = index + delta;
  if (index < 1 || index > items.length || to < 1 || to > items.length) {
    return false;
  }
  const order = items.map((_, i) => i + 1);
  [order[index - 1], order[to - 1]] = [order[to - 1], order[index - 1]];
  return ordered(remap(template, part, order)[part]);
}

/** 項を前後へ動かす。動かせなければ元のまま */
export function moveItem(template: EditTemplate, part: PlanPart, index: number, delta: -1 | 1): EditTemplate {
  if (!canMove(template, part, index, delta)) {
    return template;
  }
  const items = partOf(template, part);
  const order = items.map((_, i) => i + 1);
  const to = index + delta;
  [order[index - 1], order[to - 1]] = [order[to - 1], order[index - 1]];
  return remap(template, part, order);
}

/**
 * 先行が自分より小さい添字になるように、項を並べ直す（元の順をなるべく保つ並べ替え）。すでに満たしていれば元のまま。
 * 先行が循環していて並べられなければ undefined
 */
export function renumber(template: EditTemplate, part: PlanPart): EditTemplate | undefined {
  const items = partOf(template, part);
  if (ordered(items)) {
    return template;
  }
  const placed: number[] = [];
  const done = new Set<number>();
  while (placed.length < items.length) {
    const next = items.findIndex((item, i) => !done.has(i + 1) && item.after.every((n) => done.has(n) || n < 1 || n > items.length));
    if (next < 0) {
      return undefined;
    }
    placed.push(next + 1);
    done.add(next + 1);
  }
  return remap(template, part, placed);
}

/** 図の番号 `from` を `to` が待つ線を引く。同じ計画の中だけ。引いたあと、先行が小さい添字になるよう並べ直す */
export function connectItems(template: EditTemplate, from: number, to: number): EditTemplate {
  const a = locate(template, from);
  const b = locate(template, to);
  if (a === undefined || b === undefined || a.part !== b.part || a.index === b.index) {
    return template;
  }
  const items = partOf(template, a.part);
  if (items[b.index - 1].after.includes(a.index)) {
    return template;
  }
  const next = withPart(
    template,
    a.part,
    items.map((item, i) => (i === b.index - 1 ? { ...item, after: [...item.after, a.index].sort((x, y) => x - y) } : item)),
  );
  return renumber(next, a.part) ?? template;
}

/** 図の番号 `from` を `to` が待つ線を消す */
export function disconnectItems(template: EditTemplate, from: number, to: number): EditTemplate {
  const a = locate(template, from);
  const b = locate(template, to);
  if (a === undefined || b === undefined || a.part !== b.part) {
    return template;
  }
  const items = partOf(template, a.part);
  if (!items[b.index - 1].after.includes(a.index)) {
    return template;
  }
  return withPart(
    template,
    a.part,
    items.map((item, i) => (i === b.index - 1 ? { ...item, after: item.after.filter((n) => n !== a.index) } : item)),
  );
}

// ---- 説明・フロー

export function setMeta(template: EditTemplate, patch: { readonly name?: string; readonly description?: string; readonly when?: string }): EditTemplate {
  return { ...template, ...patch };
}

/** 全体計画の `index` 番の項にフローを置く（置き換える） */
export function setFlow(template: EditTemplate, index: number, doc: FlowDoc): EditTemplate {
  if (index < 1 || index > template.plan.length) {
    return template;
  }
  return { ...template, flows: { ...template.flows, [String(index)]: doc } };
}

/** 全体計画の `index` 番の項のフローを外す */
export function removeFlow(template: EditTemplate, index: number): EditTemplate {
  const key = String(index);
  if (!(key in template.flows)) {
    return template;
  }
  const flows = { ...template.flows };
  delete flows[key];
  return { ...template, flows };
}
