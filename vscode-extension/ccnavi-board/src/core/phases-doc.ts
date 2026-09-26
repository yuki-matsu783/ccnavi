/**
 * フェーズの種類（phases.yml）の読み書き。コメントを残したまま書き戻す。
 *
 * phases.yml は先頭に使い方の説明、種類の前に理由のコメントを持つ。risk-doc と同じく
 * `yaml` の Document を保ち、変えるところだけを差し替える。種類の入れ替えや改名は
 * 元のノード（対応表の 1 組）をそのまま別の並びへ移す。
 *
 * ここは種類の意味（どの子がどこまで書けるか、レビューが要るか）には触れない。判定は
 * 実行ファイル（phasetypes.py）の仕事で、書式の検証も `--lint --phases <一時ファイル>` に聞く。
 * 画面の欄は文字のまま持ち、並びの欄（scope / deliverables / overlap / requires / after）は
 * 文字の配列で持つ。
 *
 * 組み込みの既定は持たない（実行ファイルも持たない。既定を組み込むと、意図せずレビューの
 * 要否が決まる）。ファイルが無いときに「作る」で書く雛形は README の例で、置き場の
 * 綴りはそのプロジェクトに合わせて画面で直す前提。
 */
import { isMap, isNode, isSeq, parseDocument, Scalar, YAMLMap, YAMLSeq, type Document, type Pair } from "yaml";

import { ORDERS, PHASE_KINDS, REVIEWS, type PhaseForm, type PhaseKind, type PhaseOrder, type PhasesForm, type PhasesModel, type Review } from "./phases-view.js";
import { yaml11Ambiguous } from "./yaml11.js";

/**
 * 種類の形（`PHASE_KINDS`・`PhaseForm`・`PhasesForm` など）は画面との契約（`phases-view.ts`）にある。
 * ここに置くと、画面がそこから `yaml` を辿って束ねたものに解析器が丸ごと入る。
 */

/** 実行ファイルが読む版（phasetypes.VERSION） */
export const PHASES_VERSION = 1;

/** 範囲が「親の範囲そのまま」であることを言う綴り（phasetypes.INHERIT） */
export const INHERIT = "inherit";

/** 並びで持つ欄。書く順もこの順 */
export const LIST_KEYS = ["deliverables", "overlap", "requires", "after"] as const;
export type ListKey = (typeof LIST_KEYS)[number];

/** 種類の中の欄を書く順。無い欄はこの順の直前の欄の後ろに入る */
const KEY_ORDER = ["kind", "title", "review", "scope", "deliverables", "overlap", "requires", "after", "agent", "when"] as const;

/**
 * ファイルが無いときに「作る」で書き出す雛形。`scope` の綴りは例なので、作ったあとに画面で
 * そのプロジェクトの置き場に直す。
 *
 * **待ち方は dag で、流れを `after` で書く**（調査 → 設計と受入テスト作成 → 実装とテスト）。
 * 一直線（sequential）だと種類どうしの順序がファイルのどこにも無く、図に流れが出ない。
 * 受入テストは実装より先に書く（`overlap` で実装と並行させない）。feedback の種類は
 * `after` を持てない（`phasetypes.py`）ので、レビュー後の対応として別に置く。
 */
export const TEMPLATE_PHASES_TEXT = `# フェーズの種類（設計 9.7）。人が持つ設定で、エージェントは書き換えない。
#
# 親チケットの \`plan:\` に、ここで定義した種類の名前を順に並べる。それが全体計画で、
# \`ccnavi --approve\` が通ることが合意になる。レビューを受けたあとは \`feedback:\` に
# \`kind: feedback\` の種類を並べて改版を出す（対応が無くても \`[]\` で出す）。
#
# \`id\`（キー）と \`title\` はどちらも一意。重なれば --lint が error で止める。
# このファイルが無ければ、フェーズは番号だけの挙動に戻る。
#
# \`order: dag\` なので、各項は \`after\` に挙げた種類（の祖先）だけを待ち、辺で繋がっていない
# 種類は並行して進む。辺の書き漏れは並行として通るので、画面の図で確かめる。
#
# 下は雛形。scope の綴りはこのプロジェクトの置き場に合わせて直す。
version: 1
order: dag

phases:
  research:
    kind: work
    title: 調査
    review: none
    scope: ["wip/research/*"]
    deliverables: ["wip/research/summary.md"]
    when: 既存の振る舞いや依存が分からないとき。分かっているなら飛ばす

  design:
    kind: work
    title: 設計
    review: mr
    scope: ["wip/design/*", "docs/*"]
    deliverables: ["wip/design/*.md"]
    after: [research]
    when: 触る場所が 3 か所を超えるか、外から見える振る舞いが変わるとき

  acceptance:
    kind: work
    title: 受入テスト作成
    review: mr
    scope: ["tests/*"]
    after: [design]
    when: 振る舞いが変わるとき。設計のあと、実装より先に書く

  implement:
    kind: work
    title: 実装とテスト
    review: mr
    scope: ["src/*", "tests/*"]
    requires: [acceptance]
    after: [acceptance]

  implement-feedback:
    kind: feedback
    title: 実装フィードバック対応
    review: mr
    scope: inherit
`;

export interface PhasesDocument {
  readonly model: PhasesModel;
  /** 編集した内容で書き戻す。元のノードを使い回してコメントを残す。id が重なれば投げる */
  readonly apply: (form: PhasesForm) => string;
}

export function readPhases(text: string): PhasesDocument {
  const doc = parseDocument(text, { keepSourceTokens: false });
  const problems: string[] = [];
  for (const e of doc.errors) {
    problems.push(`YAML として読めません: ${e.message}`);
  }
  if (doc.contents !== null && !isMap(doc.contents)) {
    problems.push("最上位がマップ（キーと値の組の集まり）ではありません。実行ファイルは読めません。保存すると中身を捨てて空のマップから始めます");
  }
  const version = doc.get("version");
  if (version === undefined || version === null) {
    problems.push(`version がありません。保存すると version: ${PHASES_VERSION} を先頭に足します`);
  } else if (version !== PHASES_VERSION) {
    problems.push(`version ${String(version)} は実行ファイルが読めません（読むのは ${PHASES_VERSION}）。フェーズは番号だけの挙動になります`);
  }

  // 実行ファイルは前後の空白を落として読む（phasetypes.parse）。同じ読み方にする
  const orderNode = doc.get("order", true);
  let order: PhaseOrder = "sequential";
  if (orderNode !== undefined && orderNode !== null) {
    const orderText = orderNode instanceof Scalar ? String(orderNode.value ?? "").trim() : "";
    if ((ORDERS as readonly string[]).includes(orderText)) {
      order = orderText as PhaseOrder;
    } else {
      problems.push(`order が ${ORDERS.join(" か ")} ではありません。実行ファイルは読めません。画面は sequential として出し、保存すると書き直します`);
    }
  }

  const phases: PhaseForm[] = [];
  const raw = doc.get("phases", true);
  if (raw === undefined || raw === null) {
    problems.push("phases がありません。実行ファイルは「`phases` が辞書として無い」と報告します。種類を 1 つ以上足して保存してください");
  } else if (!isMap(raw)) {
    problems.push("phases がマップ（キーと値の組の集まり）ではありません。種類は画面に出しません。保存すると中身を捨てて空のマップから始めます");
  } else {
    raw.items.forEach((pair, index) => {
      const id = keyText(pair);
      if (!isMap(pair.value)) {
        problems.push(`種類 ${id || `（${index + 1} 件目）`} の中身がマップ（キーと値の組の集まり）ではありません。画面に出さず、保存するとこの種類は消えます（実行ファイルも読めません）`);
        return;
      }
      phases.push(formOf(index, id, pair.value, problems));
    });
    if (phases.length === 0 && problems.length === 0) {
      problems.push("種類が 1 つもありません。実行ファイルは「`phases` が辞書として無い」と報告します");
    }
  }

  return {
    model: {
      version: typeof version === "number" ? version : null,
      form: { order, phases },
      problems,
    },
    // 書き出すたびに読み直す。元のノードに値を書き込むので、同じ文書を使い回すと
    // 2 回目の `origin` が 1 回目の並び替えの後を指してしまう。
    apply: (edited) => applyTo(parseDocument(text), edited),
  };
}

function keyText(pair: Pair): string {
  const key = pair.key;
  if (key instanceof Scalar) {
    return key.value === null || key.value === undefined ? "" : String(key.value);
  }
  return typeof key === "string" ? key : "";
}

function formOf(index: number, id: string, map: YAMLMap, problems: string[]): PhaseForm {
  const kindText = scalarText(map, "kind") || "work";
  const kind: PhaseKind = (PHASE_KINDS as readonly string[]).includes(kindText) ? (kindText as PhaseKind) : "work";
  if (kind !== kindText) {
    problems.push(`種類 ${id} の kind \`${kindText}\` は ${PHASE_KINDS.join(" か ")} ではありません。画面は work として出し、保存すると work になります`);
  }
  const reviewText = scalarText(map, "review") || "mr";
  const review: Review = (REVIEWS as readonly string[]).includes(reviewText) ? (reviewText as Review) : "mr";
  if (review !== reviewText) {
    problems.push(`種類 ${id} の review \`${reviewText}\` は ${REVIEWS.join(" か ")} ではありません。画面は mr として出し、保存すると mr になります`);
  }

  const rawScope = map.get("scope", true);
  let inherit = true;
  let scope: string[] = [];
  if (rawScope === undefined || rawScope === null) {
    inherit = true;
  } else if (rawScope instanceof Scalar) {
    if (rawScope.value !== INHERIT && rawScope.value !== null) {
      problems.push(`種類 ${id} の scope \`${String(rawScope.value)}\` は glob の並びか inherit ではありません。画面は inherit として出します`);
    }
    inherit = true;
  } else if (isSeq(rawScope)) {
    inherit = false;
    scope = seqTexts(rawScope);
  } else {
    problems.push(`種類 ${id} の scope がリストでも inherit でもありません。画面は inherit として出します`);
  }

  const lists = {} as Record<ListKey, string[]>;
  for (const key of LIST_KEYS) {
    const raw = map.get(key, true);
    if (raw === undefined || raw === null) {
      lists[key] = [];
    } else if (isSeq(raw)) {
      lists[key] = seqTexts(raw);
    } else {
      problems.push(`種類 ${id} の ${key} がリスト（配列）ではありません。画面は空として出し、保存すると欄が消えます`);
      lists[key] = [];
    }
  }

  return {
    origin: index,
    id,
    title: scalarText(map, "title"),
    kind,
    review,
    inherit,
    scope,
    deliverables: lists.deliverables,
    overlap: lists.overlap,
    requires: lists.requires,
    after: lists.after,
    agent: scalarText(map, "agent"),
    when: scalarText(map, "when"),
  };
}

function seqTexts(seq: YAMLSeq): string[] {
  const out: string[] = [];
  for (const item of seq.items) {
    if (item instanceof Scalar && item.value !== null && item.value !== undefined) {
      out.push(String(item.value));
    } else if (typeof item === "string" || typeof item === "number") {
      out.push(String(item));
    }
  }
  return out;
}

function scalarText(map: YAMLMap, key: string): string {
  const value = map.get(key);
  if (value === undefined || value === null) {
    return "";
  }
  return typeof value === "string" ? value : String(value);
}

function applyTo(doc: Document, edited: PhasesForm): string {
  const seen = new Set<string>();
  const seenOrigins = new Set<number>();
  for (const form of edited.phases) {
    const id = form.id.trim();
    if (seen.has(id)) {
      // 同じキーを 2 つ書くと、実行ファイル（yaml.safe_load）は後ろで黙って上書きし、種類が 1 つ消える。
      throw new Error(`id \`${id}\` が 2 つあります。同じ id の種類は 1 つにしてください`);
    }
    seen.add(id);
    if (form.origin !== null) {
      if (seenOrigins.has(form.origin)) {
        // 同じ元ノードを 2 か所に置くと、後から書いた欄が両方に出て、キーも重なる。
        throw new Error(`${form.origin + 1} 件目の種類が 2 回送られました。更新してから編集し直してください`);
      }
      seenOrigins.add(form.origin);
    }
  }

  // 空か、最上位が対応表でないファイルは、中身を捨てて対応表から始める（読み込みの苦情で言ってある）。
  const top: YAMLMap = isMap(doc.contents) ? doc.contents : new YAMLMap();
  if (doc.contents !== top) {
    doc.contents = top;
  }

  // version。無ければ先頭に足す。違う版はそのまま（lint が言う）。
  if (!top.has("version")) {
    top.items.unshift(doc.createPair("version", PHASES_VERSION));
  }

  // order。sequential は既定なので、元から欄が無ければ書かない。dag は version の直後に置く。
  if (edited.order === "dag" || top.has("order")) {
    const current = top.get("order", true);
    if (current instanceof Scalar) {
      if (String(current.value ?? "").trim() !== edited.order) {
        current.value = edited.order;
      }
    } else if (current !== undefined && current !== null) {
      // 並びや対応表で書かれた欄は、同じ鍵を 2 つにせず、その場で置き換える
      top.set("order", edited.order);
    } else {
      const at = top.items.findIndex((p) => isNode(p.key) && (p.key as Scalar).value === "version");
      top.items.splice(at + 1, 0, doc.createPair("order", edited.order));
    }
  }

  // phases。元の組を先に全部拾っておき、編集した並びへ移す。
  // `origin` は読み込んだときの生の位置。中身が対応表でない組は画面に載らず、保存で消える（苦情で言ってある）。
  const existing = top.get("phases", true);
  const originals: Pair[] = isMap(existing) ? existing.items.slice() : [];
  const adopted = isMap(existing) ? adoptLeadingComment(existing, originals[0]) : undefined;
  const pairs = edited.phases.map((form) => {
    const id = form.id.trim();
    const original = form.origin === null ? undefined : originals[form.origin];
    const pair = original !== undefined && isMap(original.value) ? original : doc.createPair(id, new YAMLMap());
    // 改名は組のキーの値だけを替える。キーの前のコメントはキーに付いているので一緒に動く。
    // 実行ファイル（PyYAML）が別の型に読む語（yes / no / on / off など）は囲む。
    let key: Scalar;
    if (pair.key instanceof Scalar) {
      key = pair.key;
    } else {
      key = doc.createNode(id) as Scalar;
      pair.key = key;
    }
    if (key.value !== id) {
      key.value = id;
    }
    if (yaml11Ambiguous(id) && (key.type === Scalar.PLAIN || key.type === undefined)) {
      key.type = Scalar.QUOTE_DOUBLE;
    }
    writePhase(doc, pair.value as YAMLMap, form, original === undefined);
    return pair;
  });
  if (isMap(existing) && !existing.flow) {
    // 先頭の種類を消したときは、付け替えたコメントを対応表の見出しとして戻す。
    if (adopted !== undefined && !pairs.includes(adopted) && !existing.commentBefore) {
      existing.commentBefore = (adopted.key as Scalar).commentBefore ?? null;
    }
    keepSpacing(existing.items, pairs);
    // 対応表の前後のコメントは対応表のノードに付いているので、対応表は残して中身だけ替える。
    existing.items = pairs;
  } else {
    // 無い、対応表でない、`{}` の 1 行書き（flow）は、ブロックの対応表に置き換える。
    // flow のまま中身を入れると全部が 1 行になり、以後の差分とコメントの置き場が壊れる。
    const map = new YAMLMap();
    map.items = pairs;
    top.set("phases", map);
  }
  return doc.toString({ lineWidth: 0, flowCollectionPadding: false });
}

/**
 * 対応表の先頭の組の前にあるコメントは、読み込みでは対応表のほうに付く。
 * そのままだと先頭の種類を移したときにコメントが置き去りになるので、組のキーに付け直す。
 * 付け直した組を返す（呼び手は、その組が消えたときにコメントを対応表へ戻す）。
 */
function adoptLeadingComment(map: YAMLMap, first: Pair | undefined): Pair | undefined {
  if (first === undefined || !map.commentBefore) {
    return undefined;
  }
  const key = first.key;
  if (key instanceof Scalar && !key.commentBefore) {
    key.commentBefore = map.commentBefore;
    map.commentBefore = null;
    return first;
  }
  return undefined;
}

/**
 * 種類の前の空行は、種類ではなく「対応表の何番目か」に付いていたものとして揃える。
 * 先頭に来た種類が空行を連れてくると `phases:` の直後に空白だけの行が出るため。
 */
function keepSpacing(before: readonly Pair[], after: readonly Pair[]): void {
  const slots = before.map((p) => isNode(p.key) && p.key.spaceBefore === true);
  const last = slots.length > 0 ? slots[slots.length - 1] : false;
  after.forEach((pair, i) => {
    if (isNode(pair.key)) {
      pair.key.spaceBefore = i < slots.length ? slots[i] : last;
    }
  });
}

/** 欄を順に書く。変わっていない欄は触らず、元の書き方（引用符）を残す。新しい種類は kind と review を必ず書く */
function writePhase(doc: Document, node: YAMLMap, form: PhaseForm, isNew: boolean): void {
  // kind と review は実行ファイルに既定がある（work / mr）。元から欄が無くて既定のままなら足さない。
  if (isNew || node.has("kind") || form.kind !== "work") {
    setValue(doc, node, "kind", form.kind, Scalar.PLAIN);
  }
  setOrDelete(doc, node, "title", form.title.trim(), Scalar.PLAIN);
  if (isNew || node.has("review") || form.review !== "mr") {
    setValue(doc, node, "review", form.review, Scalar.PLAIN);
  }

  // scope。inherit は綴りで書く（欄が無いのも inherit だが、意図が読めるように残す）。
  if (form.inherit) {
    const current = node.get("scope", true);
    if (current instanceof Scalar) {
      if (current.value !== INHERIT) {
        current.value = INHERIT;
      }
    } else {
      if (node.has("scope")) {
        node.delete("scope");
      }
      const scalar = doc.createNode(INHERIT) as Scalar;
      scalar.type = Scalar.PLAIN;
      insertAfter(doc, node, "scope", scalar, before("scope", node));
    }
  } else {
    // glob は二重引用符で囲む。`*` で始まる値を裸で書くと YAML が別名として読む。
    setList(doc, node, "scope", form.scope, Scalar.QUOTE_DOUBLE, true);
  }

  // deliverables は glob なので引用符付き。overlap / requires / after は種類の id なので裸のまま。
  setList(doc, node, "deliverables", form.deliverables, Scalar.QUOTE_DOUBLE, false);
  setList(doc, node, "overlap", form.overlap, Scalar.PLAIN, false);
  setList(doc, node, "requires", form.requires, Scalar.PLAIN, false);
  setList(doc, node, "after", form.after, Scalar.PLAIN, false);

  setOrDelete(doc, node, "agent", form.agent.trim(), Scalar.PLAIN);
  setOrDelete(doc, node, "when", form.when.trim(), Scalar.PLAIN);
}

/**
 * 並びの欄を書く。空なら欄ごと消す（`keepEmpty` が真なら `[]` で残す。scope の `[]` は
 * 「何も書けない」の意味で、消すと inherit に変わってしまう）。
 * 変わっていなければ触らない。変わっていれば flow（1 行）の並びで書き直す。
 */
function setList(
  doc: Document,
  node: YAMLMap,
  key: string,
  values: readonly string[],
  style: Scalar.Type,
  keepEmpty: boolean,
): void {
  const cleaned = values.map((v) => v.trim()).filter((v) => v !== "");
  if (cleaned.length === 0 && !keepEmpty) {
    if (node.has(key)) {
      node.delete(key);
    }
    return;
  }
  const current = node.get(key, true);
  if (isSeq(current) && sameTexts(seqTexts(current), cleaned)) {
    return;
  }
  const seq = new YAMLSeq();
  seq.flow = true;
  seq.items = cleaned.map((v) => {
    const scalar = doc.createNode(v) as Scalar;
    scalar.type = style === Scalar.PLAIN && yaml11Ambiguous(v) ? Scalar.QUOTE_DOUBLE : style;
    return scalar;
  });
  if (current !== undefined && current !== null) {
    node.set(key, seq);
  } else {
    insertAfter(doc, node, key, seq, before(key, node));
  }
}

function sameTexts(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

/** 文字の欄。空なら欄ごと消す */
function setOrDelete(doc: Document, node: YAMLMap, key: string, value: string, style: Scalar.Type): void {
  if (value === "") {
    if (node.has(key)) {
      node.delete(key);
    }
    return;
  }
  setValue(doc, node, key, value, style);
}

function setValue(doc: Document, node: YAMLMap, key: string, value: string, style: Scalar.Type): void {
  const current = node.get(key, true);
  if (current instanceof Scalar) {
    // 書き方（引用符）は元のまま。値が変わっても書き方まで変えない。
    // ただし実行ファイル（PyYAML）が別の型に読む語を裸で書くことになるなら囲む。
    if (current.value !== value) {
      current.value = value;
      if (current.type === Scalar.PLAIN && yaml11Ambiguous(value)) {
        current.type = Scalar.QUOTE_DOUBLE;
      }
    }
    return;
  }
  if (current !== undefined && current !== null) {
    node.delete(key);
  }
  const scalar = doc.createNode(value) as Scalar;
  scalar.type = style === Scalar.PLAIN && yaml11Ambiguous(value) ? Scalar.QUOTE_DOUBLE : style;
  insertAfter(doc, node, key, scalar, before(key, node));
}

/** `key` を足すとき、その直前に置くべき欄（KEY_ORDER で前にあって、いま在るもの） */
function before(key: string, node: YAMLMap): string | undefined {
  const at = (KEY_ORDER as readonly string[]).indexOf(key);
  for (let i = at - 1; i >= 0; i -= 1) {
    if (node.has(KEY_ORDER[i])) {
      return KEY_ORDER[i];
    }
  }
  return undefined;
}

function insertAfter(doc: Document, node: YAMLMap, key: string, value: unknown, after?: string): void {
  const pair = doc.createPair(key, value);
  const at = after === undefined ? -1 : node.items.findIndex((p) => String((p.key as Scalar).value) === after);
  if (at >= 0) {
    node.items.splice(at + 1, 0, pair);
  } else if (after === undefined) {
    node.items.unshift(pair);
  } else {
    node.items.push(pair);
  }
}

/** 画面が送ってきた内容を、形だけ確かめて受け取る */
export function asPhasesForm(raw: unknown): PhasesForm | undefined {
  if (typeof raw !== "object" || raw === null) {
    return undefined;
  }
  const r = raw as Record<string, unknown>;
  if (!Array.isArray(r.phases)) {
    return undefined;
  }
  if (typeof r.order !== "string" || !(ORDERS as readonly string[]).includes(r.order)) {
    return undefined;
  }
  const phases: PhaseForm[] = [];
  for (const item of r.phases) {
    const form = asPhase(item);
    if (form === undefined) {
      return undefined;
    }
    phases.push(form);
  }
  return { order: r.order as PhaseOrder, phases };
}

function asPhase(raw: unknown): PhaseForm | undefined {
  if (typeof raw !== "object" || raw === null) {
    return undefined;
  }
  const r = raw as Record<string, unknown>;
  let origin: number | null = null;
  if (r.origin !== null && r.origin !== undefined) {
    if (typeof r.origin !== "number" || !Number.isInteger(r.origin) || r.origin < 0) {
      return undefined;
    }
    origin = r.origin;
  }
  if (typeof r.kind !== "string" || !(PHASE_KINDS as readonly string[]).includes(r.kind)) {
    return undefined;
  }
  if (typeof r.review !== "string" || !(REVIEWS as readonly string[]).includes(r.review)) {
    return undefined;
  }
  const lists = [r.scope, r.deliverables, r.overlap, r.requires, r.after].map(texts);
  if (lists.some((l) => l === undefined)) {
    return undefined;
  }
  return {
    origin,
    id: text(r.id),
    title: text(r.title),
    kind: r.kind as PhaseKind,
    review: r.review as Review,
    inherit: r.inherit === true,
    scope: lists[0] as string[],
    deliverables: lists[1] as string[],
    overlap: lists[2] as string[],
    requires: lists[3] as string[],
    after: lists[4] as string[],
    agent: text(r.agent),
    when: text(r.when),
  };
}

/** 並びの欄は文字の配列で持つ。無ければ空。文字以外が混ざっていれば形が違う */
function texts(value: unknown): string[] | undefined {
  if (value === undefined || value === null) {
    return [];
  }
  if (!Array.isArray(value) || !value.every((v) => typeof v === "string")) {
    return undefined;
  }
  return value as string[];
}

/** 欄は文字で持つ。数で来ても文字にする（JSON を経ても同じ形にするため） */
function text(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  return typeof value === "number" ? String(value) : "";
}
