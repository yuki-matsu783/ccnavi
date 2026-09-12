/**
 * ルールファイル（rules.yml）の読み書き。コメントを残したまま書き戻す。
 *
 * rules.yml は先頭に使い方の説明、区画やルールの前に理由のコメントを持つ。素直に
 * 読んで dump し直すとそれが全部消えるので、`yaml` の Document を保ち、変えるところ
 * だけを差し替える。ルールの入れ替え・区画の移動は、元のノードをそのまま別の並びへ
 * 移すので、そのルールに付いていたコメントも一緒に動く。
 *
 * ここはルールの意味（当たる・当たらない）には触れない。判定は実行ファイルの仕事。
 */
import { isMap, isSeq, parseDocument, Scalar, YAMLMap, YAMLSeq, type Document } from "yaml";

export const SECTIONS = ["deny", "ask", "allow"] as const;
export type Section = (typeof SECTIONS)[number];
export type PatternKind = "glob" | "regex";

/** 画面で編集する 1 件。`origin` は読み込んだときの位置で、新しいルールは null */
export interface RuleForm {
  readonly origin: { readonly section: Section; readonly index: number } | null;
  readonly id: string;
  readonly match: string;
  readonly kind: PatternKind;
  readonly pattern: string;
  readonly message: string;
  /** 当たったときにモデルへ渡す文（`additionalContext`）。無ければ空 */
  readonly additionalContext: string;
  /** 1 つの文脈で最初に当たったときだけ渡す文（`additionalContextOnce`）。無ければ空 */
  readonly additionalContextOnce: string;
  /** 文に続けて本文を渡すファイル（`additionalContextFile`）。ルートからの相対パス。無ければ空 */
  readonly additionalContextFile: string;
  /** 最初に当たったときだけ本文を渡すファイル（`additionalContextOnceFile`）。無ければ空 */
  readonly additionalContextOnceFile: string;
}

/** ブロック（`>-` / `|-`）で書かれうる文の欄。変えていなければ元の折り返しのまま戻す */
const BLOCK_KEYS = ["message", "additionalContext", "additionalContextOnce"] as const;

export interface RulesModel {
  readonly version: number | null;
  readonly sections: Readonly<Record<Section, readonly RuleForm[]>>;
  /** 読み込み時の苦情。ルールの形が読めなかった場所。あっても他は出す */
  readonly problems: readonly string[];
}

export interface RulesDocument {
  readonly model: RulesModel;
  /** 編集した並びで書き戻す。元のノードを使い回してコメントを残す */
  readonly apply: (sections: Readonly<Record<Section, readonly RuleForm[]>>) => string;
}

export function readRules(text: string): RulesDocument {
  const doc = parseDocument(text, { keepSourceTokens: false });
  const problems: string[] = [];
  for (const e of doc.errors) {
    problems.push(`YAML として読めない: ${e.message}`);
  }
  const sections = {} as Record<Section, RuleForm[]>;
  for (const section of SECTIONS) {
    sections[section] = [];
    const seq = doc.get(section, true);
    if (seq === undefined || seq === null) {
      continue;
    }
    if (!isSeq(seq)) {
      problems.push(`タイプ ${section} が並びではない。このタイプは画面に出さない`);
      continue;
    }
    seq.items.forEach((item, index) => {
      if (!isMap(item)) {
        problems.push(`タイプ ${section} の ${index + 1} 件目が対応表ではない。画面に出さない`);
        return;
      }
      sections[section].push(formOf(section, index, item));
    });
  }
  const version = doc.get("version");
  return {
    model: {
      version: typeof version === "number" ? version : null,
      sections,
      problems,
    },
    // 書き出すたびに読み直す。元のノードに値を書き込むので、同じ文書を使い回すと
    // 2 回目の `origin` が 1 回目の並び替えの後を指してしまう。
    apply: (edited) => applyTo(parseDocument(text), text, edited),
  };
}

function formOf(section: Section, index: number, map: YAMLMap): RuleForm {
  const glob = scalarText(map, "glob");
  const regex = scalarText(map, "regex");
  const kind: PatternKind = glob === "" && regex !== "" ? "regex" : "glob";
  return {
    origin: { section, index },
    id: scalarText(map, "id"),
    match: scalarText(map, "match"),
    kind,
    pattern: kind === "glob" ? glob : regex,
    message: scalarText(map, "message"),
    additionalContext: scalarText(map, "additionalContext"),
    additionalContextOnce: scalarText(map, "additionalContextOnce"),
    additionalContextFile: scalarText(map, "additionalContextFile"),
    additionalContextOnceFile: scalarText(map, "additionalContextOnceFile"),
  };
}

function scalarText(map: YAMLMap, key: string): string {
  const value = map.get(key);
  if (value === undefined || value === null) {
    return "";
  }
  return typeof value === "string" ? value : String(value);
}

/** 変えていないブロック（`>-` / `|-`）の文を、元の折り返しのまま戻すための控え */
interface BlockKeep {
  readonly node: YAMLMap;
  readonly key: (typeof BLOCK_KEYS)[number];
  readonly value: unknown;
  readonly raw: string;
}

function applyTo(
  doc: Document,
  text: string,
  edited: Readonly<Record<Section, readonly RuleForm[]>>,
): string {
  // 元のノードを先に全部拾っておく。区画をまたいで移すので、並びを書き換える前に取る。
  const originals = {} as Record<Section, YAMLMap[]>;
  const keeps: BlockKeep[] = [];
  for (const section of SECTIONS) {
    const seq = doc.get(section, true);
    originals[section] = isSeq(seq) ? seq.items.filter(isMap) : [];
    if (isSeq(seq)) {
      adoptLeadingComment(seq, originals[section][0]);
    }
    for (const node of originals[section]) {
      keeps.push(...blockKeeps(node, text));
    }
  }

  const placed: YAMLMap[] = [];
  for (const section of SECTIONS) {
    const forms = edited[section];
    const nodes = forms.map((form) => {
      const original = form.origin ? originals[form.origin.section][form.origin.index] : undefined;
      const node = original ?? newRuleNode(doc);
      writeFields(doc, node, form);
      placed.push(node);
      return node;
    });
    const existing = doc.get(section, true);
    if (isSeq(existing)) {
      // 区画のコメントは並びのノードに付いているので、並びは残して中身だけ替える。
      // 空になったら `[]`、1 件でも入ったらブロックの並びに戻す。
      existing.items = nodes;
      existing.flow = nodes.length === 0;
    } else if (nodes.length > 0) {
      const seq = new YAMLSeq();
      seq.items = nodes;
      doc.set(section, seq);
    }
  }
  return restoreBlocks(doc.toString({ lineWidth: 0 }), placed, keeps);
}

/**
 * 並びの先頭のルールの前にあるコメントは、読み込みでは並びのほうに付く。
 * そのままだと先頭のルールを移したときにコメントが置き去りになるので、ルールに付け直す。
 */
function adoptLeadingComment(seq: YAMLSeq, first: YAMLMap | undefined): void {
  if (first === undefined || !seq.commentBefore) {
    return;
  }
  if (!first.commentBefore) {
    first.commentBefore = seq.commentBefore;
    seq.commentBefore = null;
  }
}

function newRuleNode(doc: Document): YAMLMap {
  const node = doc.createNode({}) as YAMLMap;
  return node;
}

/**
 * 書き出しはブロックの文を折り返し直す。人が書いた折り返しと違う形になるので、
 * 値を変えていない文（message / additionalContext）は元の文字列をそのまま埋め戻す。
 * 書き出した文書を読み直して同じ位置の範囲を求め、後ろから差し替える。
 */
function blockKeeps(node: YAMLMap, text: string): BlockKeep[] {
  const keeps: BlockKeep[] = [];
  for (const key of BLOCK_KEYS) {
    const scalar = node.get(key, true);
    if (!(scalar instanceof Scalar) || scalar.range === undefined || scalar.range === null) {
      continue;
    }
    if (scalar.type !== Scalar.BLOCK_FOLDED && scalar.type !== Scalar.BLOCK_LITERAL) {
      continue;
    }
    keeps.push({ node, key, value: scalar.value, raw: text.slice(scalar.range[0], scalar.range[1]) });
  }
  return keeps;
}

function restoreBlocks(output: string, placed: readonly YAMLMap[], keeps: readonly BlockKeep[]): string {
  const wanted: BlockKeep[] = [];
  for (const keep of keeps) {
    const scalar = keep.node.get(keep.key, true);
    if (scalar instanceof Scalar && scalar.value === keep.value) {
      wanted.push(keep);
    }
  }
  if (wanted.length === 0) {
    return output;
  }
  const again = parseDocument(output);
  const written: YAMLMap[] = [];
  for (const section of SECTIONS) {
    const seq = again.get(section, true);
    if (isSeq(seq)) {
      written.push(...seq.items.filter(isMap));
    }
  }
  if (written.length !== placed.length) {
    return output;
  }
  const edits: { start: number; end: number; raw: string }[] = [];
  for (const keep of wanted) {
    const i = placed.indexOf(keep.node);
    if (i < 0) {
      continue;
    }
    const scalar = written[i].get(keep.key, true);
    if (!(scalar instanceof Scalar) || !scalar.range) {
      continue;
    }
    edits.push({ start: scalar.range[0], end: scalar.range[1], raw: keep.raw });
  }
  let result = output;
  for (const edit of edits.sort((a, b) => b.start - a.start)) {
    result = result.slice(0, edit.start) + edit.raw + result.slice(edit.end);
  }
  return result;
}

/** 欄を順に書く。変わっていない欄は触らず、元の書き方（引用符・折り返し）を残す */
function writeFields(doc: Document, node: YAMLMap, form: RuleForm): void {
  setText(doc, node, "id", form.id, Scalar.PLAIN);
  setText(doc, node, "match", form.match, Scalar.PLAIN);
  // glob と regex は必ず引用符で囲む。囲まないと YAML が先に解釈する（rules.yml の先頭の注意）。
  const other: PatternKind = form.kind === "glob" ? "regex" : "glob";
  if (node.has(other)) {
    node.delete(other);
  }
  setText(doc, node, form.kind, form.pattern, Scalar.QUOTE_SINGLE, node.has(form.kind) ? undefined : "match");
  // allow のルールは文面を持たないことがある。空のまま欄を足すと lint が「文面が無い」と
  // 言う対象が増えるだけなので、書いてあるか元から在るときだけ書く。
  if (form.message !== "" || node.has("message")) {
    setText(doc, node, "message", form.message, Scalar.BLOCK_FOLDED);
  }
  // additionalContext も同じ。書いてあるか元から在るときだけ。
  if (form.additionalContext !== "" || node.has("additionalContext")) {
    setText(doc, node, "additionalContext", form.additionalContext, Scalar.BLOCK_FOLDED);
  }
  if (form.additionalContextOnce !== "" || node.has("additionalContextOnce")) {
    setText(doc, node, "additionalContextOnce", form.additionalContextOnce, Scalar.BLOCK_FOLDED);
  }
  // ファイルのパスは 1 行の値。文の欄の直後に置く（文が無ければ末尾）。
  if (form.additionalContextFile !== "" || node.has("additionalContextFile")) {
    setText(doc, node, "additionalContextFile", form.additionalContextFile, Scalar.PLAIN, "additionalContext");
  }
  if (form.additionalContextOnceFile !== "" || node.has("additionalContextOnceFile")) {
    setText(doc, node, "additionalContextOnceFile", form.additionalContextOnceFile, Scalar.PLAIN, "additionalContextOnce");
  }
}

function setText(
  doc: Document,
  node: YAMLMap,
  key: string,
  value: string,
  style: Scalar.Type,
  after?: string,
): void {
  const current = node.get(key, true);
  if (current instanceof Scalar) {
    // 書き方（引用符・折り返し）は元のまま。値が変わっても書き方まで変えない。
    if (current.value !== value) {
      current.value = value;
    }
    return;
  }
  const scalar = doc.createNode(value) as Scalar;
  scalar.type = style;
  const pair = doc.createPair(key, scalar);
  const at = after === undefined ? -1 : node.items.findIndex((p) => String((p.key as Scalar).value) === after);
  if (at >= 0) {
    node.items.splice(at + 1, 0, pair);
  } else {
    node.items.push(pair);
  }
}

/** 画面が送ってきた並びを、形だけ確かめて受け取る */
export function asSections(raw: unknown): Readonly<Record<Section, readonly RuleForm[]>> | undefined {
  if (typeof raw !== "object" || raw === null) {
    return undefined;
  }
  const out = {} as Record<Section, RuleForm[]>;
  for (const section of SECTIONS) {
    const list = (raw as Record<string, unknown>)[section];
    if (!Array.isArray(list)) {
      return undefined;
    }
    const forms: RuleForm[] = [];
    for (const item of list) {
      const form = asForm(item);
      if (form === undefined) {
        return undefined;
      }
      forms.push(form);
    }
    out[section] = forms;
  }
  return out;
}

function asForm(raw: unknown): RuleForm | undefined {
  if (typeof raw !== "object" || raw === null) {
    return undefined;
  }
  const r = raw as Record<string, unknown>;
  const origin = r.origin;
  let parsedOrigin: RuleForm["origin"] = null;
  if (typeof origin === "object" && origin !== null) {
    const o = origin as Record<string, unknown>;
    if (
      typeof o.section !== "string" ||
      !(SECTIONS as readonly string[]).includes(o.section) ||
      typeof o.index !== "number" ||
      !Number.isInteger(o.index) ||
      o.index < 0
    ) {
      return undefined;
    }
    parsedOrigin = { section: o.section as Section, index: o.index };
  }
  if (r.kind !== "glob" && r.kind !== "regex") {
    return undefined;
  }
  const text = (v: unknown) => (typeof v === "string" ? v : "");
  return {
    origin: parsedOrigin,
    id: text(r.id),
    match: text(r.match),
    kind: r.kind,
    pattern: text(r.pattern),
    message: text(r.message),
    additionalContext: text(r.additionalContext),
    additionalContextOnce: text(r.additionalContextOnce),
    additionalContextFile: text(r.additionalContextFile),
    additionalContextOnceFile: text(r.additionalContextOnceFile),
  };
}
