/**
 * ルールファイル（rules.yml）の読み書き。コメントを残したまま書き戻す。
 *
 * rules.yml は先頭に使い方の説明、タイプやルールの前に理由のコメントを持つ。素直に
 * 読んで dump し直すとそれが全部消えるので、`yaml` の Document を保ち、変えるところ
 * だけを差し替える。ルールの入れ替え・タイプの移動は、元のノードをそのまま別のリストへ
 * 移すので、そのルールに付いていたコメントも一緒に動く。
 *
 * ルールの意味（ヒットする・ヒットしない）の判定は実行ファイルの仕事。
 */
import { isMap, isNode, isScalar, isSeq, Pair, parseDocument, Scalar, YAMLMap, YAMLSeq, type Document, type Node } from "yaml";

import { SECTIONS, type PatternKind, type RuleForm, type RulesModel, type Section, type Sections } from "./rules-view.js";
import { asCond, sameCond, type CondNode } from "./rules-when.js";
import { yaml11Ambiguous } from "./yaml11.js";

/**
 * ルールの形（`SECTIONS`・`RuleForm`・`RulesModel`）は画面との契約（`rules-view.ts`）にある。
 * ここに置くと、画面がそこから `yaml` を辿ってバンドルしたものに解析器がすべて入る。
 */

/** ブロック（`>-` / `|-`）で書かれうる文の欄。変えていなければ元の折り返しのまま戻す */
const BLOCK_KEYS = ["message", "additionalContext", "additionalContextOnce"] as const;

export interface RulesDocument {
  readonly model: RulesModel;
  /** 編集したリストで書き戻す。元のノードを使い回してコメントを残す */
  readonly apply: (sections: Sections) => string;
}

export function readRules(text: string): RulesDocument {
  const doc = parseDocument(text, { keepSourceTokens: false });
  const problems: string[] = [];
  for (const e of doc.errors) {
    problems.push(`YAML として解釈できません: ${e.message}`);
  }
  const sections = {} as Record<Section, RuleForm[]>;
  for (const section of SECTIONS) {
    sections[section] = [];
    const seq = doc.get(section, true);
    if (seq === undefined || seq === null) {
      continue;
    }
    if (!isSeq(seq)) {
      problems.push(`タイプ ${section} がリスト（配列）ではありません。このタイプは画面に出しません`);
      continue;
    }
    seq.items.forEach((item, index) => {
      if (!isMap(item)) {
        problems.push(`タイプ ${section} の ${index + 1} 件目がマップ（キーと値の組の集まり）ではありません。画面に出しません`);
        return;
      }
      const form = formOf(section, index, item);
      if (form.when !== undefined && (item.has("glob") || item.has("regex"))) {
        problems.push(`タイプ ${section} の ${index + 1} 件目（${form.id || "id 未設定"}）は when と glob / regex を両方指定しています。どちらか 1 つにしてください`);
      } else if (form.bothPatterns === true) {
        problems.push(`タイプ ${section} の ${index + 1} 件目（${form.id || "id 未設定"}）は glob と regex を両方指定しています。どちらか 1 つにしてください`);
      }
      sections[section].push(form);
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
    every: scalarText(map, "every"),
    ...exceptsOf(map),
    ...(map.has("when") ? { when: condOfYaml(map.get("when", true)) } : {}),
    // 実行ファイルと同じく、when が無いときは値の空でない glob / regex だけを数える（`glob: ''` は書いていない扱い）
    ...(!map.has("when") && scalarText(map, "glob") !== "" && scalarText(map, "regex") !== "" ? { bothPatterns: true } : {}),
  };
}

/** 組み合わせ条件のノードの深さをここまでで打ち切る（読めない入れ子で止まらないため。上限の 8 を言うのは画面と lint） */
const READ_DEPTH = 64;

/** YAML の値を、そのまま JSON にできる形にする（読めなかったノードを書き戻すため） */
function plainOf(value: unknown): unknown {
  return isNode(value) ? value.toJSON() : (value ?? null);
}

function shown(value: unknown): string {
  const text = JSON.stringify(plainOf(value)) ?? "null";
  return text.length > 80 ? `${text.slice(0, 80)}…` : text;
}

function invalid(value: unknown, reason: string): CondNode {
  return { op: "invalid", text: shown(value), reason, raw: plainOf(value) };
}

/**
 * `when:` のノード（と `except:` の各要素）を組み合わせ条件にする。形が違うところは `invalid` にして残し、
 * 他のノードは読む（1 か所の誤りで組み合わせ条件ぜんたいを見せなくしない）。
 */
export function condOfYaml(value: unknown, depth = 1): CondNode {
  if (depth > READ_DEPTH) {
    return invalid(value, "入れ子が深すぎて読み込めません");
  }
  if (!isMap(value)) {
    return invalid(value, "ノードはマップ（キー 1 つ）で指定します");
  }
  if (value.items.length !== 1) {
    return invalid(value, `ノードのキーはちょうど 1 つです（${value.items.length} 個あります）`);
  }
  const pair = value.items[0];
  const key = isScalar(pair.key) ? String(pair.key.value) : String(pair.key);
  const inner = pair.value;
  switch (key) {
    case "glob":
    case "regex":
      if (!isScalar(inner) || typeof inner.value !== "string") {
        return invalid(value, `${key} の値は文字列です`);
      }
      // 引用符の無い `yes` `on` `0755` などは、実行ファイル（PyYAML、YAML 1.1）が文字列以外に読み、
      // 読み込みが error にする。ここ（YAML 1.2）では文字列に見えるので、誤りとして揃えて挙げる
      if (inner.type === Scalar.PLAIN && yaml11Ambiguous(inner.value)) {
        return invalid(value, `${key} の値 ${inner.value} は引用符で囲んでください（実行ファイルは文字列として読みません）`);
      }
      return { op: key, pattern: inner.value };
    case "all":
    case "any":
      if (!isSeq(inner)) {
        return invalid(value, `${key} の値はリストです`);
      }
      return { op: key, children: inner.items.map((item) => condOfYaml(item, depth + 1)) };
    case "not":
      if (inner === null || inner === undefined || (isScalar(inner) && inner.value === null)) {
        return { op: "not", child: null };
      }
      return { op: "not", child: condOfYaml(inner, depth + 1) };
    default:
      return invalid(value, `知らないキー ${key} です（glob / regex / all / any / not）`);
  }
}

/**
 * `except:` の各パターンを、見せる形（`glob: …` / `regex: …`）にする。書いていなければ欄ごと省く。
 * 形の誤り（マッピングでない要素など）もそのまま文字にして見せる。止めるのは保存前の `--lint`。
 */
function exceptsOf(map: YAMLMap): { excepts?: readonly string[]; exceptNodes?: readonly CondNode[] } {
  const seq = map.get("except", true);
  if (seq === undefined || seq === null) {
    return {};
  }
  if (!isSeq(seq)) {
    return { excepts: [String(map.get("except"))], exceptNodes: [invalid(seq, "except の値はリストです")] };
  }
  return {
    exceptNodes: seq.items.map((item) => condOfYaml(item)),
    excepts: seq.items.map((item) => {
      if (!isMap(item)) {
        return String(item);
      }
      return item.items.map((pair) => `${String((pair.key as Scalar).value)}: ${String((pair.value as Scalar | null)?.value ?? "")}`).join(", ");
    }),
  };
}

/** 刻みとして読める文字なら、その刻み。読めない文字と空なら null（報告するのは lint） */
function everyOf(written: string): number | null {
  const text = written.trim();
  if (!/^\d+$/.test(text)) {
    return null;
  }
  const value = Number(text);
  return value >= 1 ? value : null;
}

function scalarText(map: YAMLMap, key: string): string {
  const value = map.get(key);
  if (value === undefined || value === null) {
    return "";
  }
  return typeof value === "string" ? value : String(value);
}

/** 変えていないブロック（`>-` / `|-`）の文を、元の折り返しのまま戻すための記録 */
interface BlockKeep {
  readonly node: YAMLMap;
  readonly key: (typeof BLOCK_KEYS)[number];
  readonly value: unknown;
  readonly raw: string;
}

function applyTo(doc: Document, text: string, edited: Sections): string {
  // 元のノードを先に全部拾っておく。タイプをまたいで移すので、リストを書き換える前に取る。
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
      // タイプのコメントはリストのノードに付いているので、リストは残して中身だけ替える。
      // 空になったら `[]`、1 件でも入ったらブロックのリストに戻す。
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
 * リストの先頭のルールの前にあるコメントは、読み込みではリストのほうに付く。
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
 * 書き出しはブロックの文を折り返し直す。ユーザが書いた折り返しと違う形になるので、
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

/**
 * 欄を順に書く。変わっていない欄は触らず、元の書き方（引用符・折り返し）を残す。
 * `except:` は画面で編集しないので、元のノードを使い回し、書いてあればそのまま残る。
 */
function writeFields(doc: Document, node: YAMLMap, form: RuleForm): void {
  setText(doc, node, "id", form.id, Scalar.PLAIN);
  setText(doc, node, "match", form.match, Scalar.PLAIN);
  if (form.when !== undefined) {
    writeWhen(doc, node, form.when, form.dropExcept === true);
  } else {
    // glob と regex は必ず引用符で囲む。囲まないと YAML が先に解釈する（rules.yml の先頭の注意）。
    const other: PatternKind = form.kind === "glob" ? "regex" : "glob";
    if (node.has(other)) {
      node.delete(other);
    }
    setText(doc, node, form.kind, form.pattern, Scalar.QUOTE_SINGLE, node.has(form.kind) ? undefined : "match");
  }
  // 「組み合わせ条件に変換」で except を組み合わせ条件へ移したときだけ消す
  if (form.dropExcept === true) {
    node.delete("except");
  }
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
  setEvery(doc, node, form.every);
}

/**
 * 組み合わせ条件（`when:`）を書く。読み込んだときと同じ組み合わせ条件なら何もしない（コメントも書き方も、
 * 両方書いてしまった `glob` も、そのまま残す。報告するのは lint）。
 *
 * 変えたときは `glob` / `regex` を消し、組み合わせ条件を書く。元のノードは使い回す。子は、同じ中身のノードを先に
 * 拾い（消した・並べ替えた子のコメントが別の子へ付け替わらないように）、残りを似たノードに適用して
 * 値だけを書き換える（patchChildren）。包む・not を外すときも元のノードを中で使い回す。新しい個別条件は単引用符で囲む。
 *
 * まだ `when:` が無い（「組み合わせ条件に変換」した）ときは、元の `glob` / `regex` と、移す `except:` の
 * 各要素を、変換した形（`all: [本体, not: {any: 除くもの}]`）に組んで元のノードとして適用する。
 * 書いてあったコメント（前置き・行末）が、対応する個別条件に付いたまま残る。
 */
function writeWhen(doc: Document, node: YAMLMap, tree: CondNode, movingExcept: boolean): void {
  const current = node.get("when", true);
  if (current !== undefined && sameCond(condOfYaml(current), tree)) {
    return;
  }
  const donor = current === undefined ? convertedFrom(node, movingExcept) : undefined;
  node.delete("glob");
  node.delete("regex");
  const next = patchCond(doc, current ?? donor, tree);
  if (current !== undefined && next === current) {
    return;
  }
  if (node.has("when")) {
    node.set("when", next);
    return;
  }
  const at = node.items.findIndex((p) => String((p.key as Scalar).value) === "match");
  node.items.splice(at >= 0 ? at + 1 : node.items.length, 0, doc.createPair("when", next));
}

/**
 * glob / regex 1 つ（と `except:`）のルールの元のノードを、「組み合わせ条件に変換」した形に組む。
 * 個別条件は元のペアの値（行末のコメント付き）をそのまま持ち、キーの前のコメントは個別条件の前へ移す。
 * `except:` の各要素はノードごと移す（`movingExcept` のときだけ）
 */
function convertedFrom(node: YAMLMap, movingExcept: boolean): YAMLMap | undefined {
  const body = node.items.find((p) => isScalar(p.key) && (p.key.value === "glob" || p.key.value === "regex") && isScalar(p.value));
  const children: Node[] = [];
  if (body !== undefined) {
    const key = body.key as Scalar;
    const leafMap = new YAMLMap();
    leafMap.items.push(new Pair(new Scalar(key.value), body.value));
    leafMap.commentBefore = key.commentBefore;
    key.commentBefore = undefined;
    children.push(leafMap);
  }
  if (movingExcept) {
    const exceptPair = node.items.find((p) => isScalar(p.key) && p.key.value === "except");
    const seq = exceptPair?.value;
    if (exceptPair !== undefined && isSeq(seq) && seq.items.length > 0) {
      // 先頭の要素の前のコメントは、読み込みではリストのほうに付く
      const first = seq.items[0];
      if (seq.commentBefore && isNode(first) && !first.commentBefore) {
        first.commentBefore = seq.commentBefore;
        seq.commentBefore = undefined;
      }
      const anyItems = new YAMLSeq();
      anyItems.items = seq.items.slice();
      const anyMap = new YAMLMap();
      anyMap.items.push(new Pair(new Scalar("any"), anyItems));
      const notMap = new YAMLMap();
      notMap.items.push(new Pair(new Scalar("not"), anyMap));
      notMap.commentBefore = (exceptPair.key as Scalar).commentBefore;
      children.push(notMap);
    }
  }
  if (children.length === 0) {
    return undefined;
  }
  const all = new YAMLSeq();
  all.items = children;
  const root = new YAMLMap();
  root.items.push(new Pair(new Scalar("all"), all));
  return root;
}

function mapOf(doc: Document, key: string, value: unknown): YAMLMap {
  const map = new YAMLMap();
  map.items.push(doc.createPair(key, value));
  return map;
}

function quoted(doc: Document, text: string): Scalar {
  const scalar = doc.createNode(text) as Scalar;
  scalar.type = Scalar.QUOTE_SINGLE;
  return scalar;
}

/** 元のノードが 1 キーのマップなら、そのペアとキー */
function soleOf(existing: unknown): { readonly pair: Pair | undefined; readonly key: string | undefined } {
  const pair = isMap(existing) && existing.items.length === 1 ? (existing.items[0] as Pair) : undefined;
  const key = pair !== undefined && isScalar(pair.key) ? String(pair.key.value) : undefined;
  return { pair, key };
}

function patchCond(doc: Document, existing: unknown, tree: CondNode): Node {
  if (tree.op === "invalid") {
    // 読めなかったノードは、読んだ値のまま返す（画面では直せないので、壊さないことだけをする）
    if (isNode(existing) && sameCond(condOfYaml(existing), tree)) {
      return existing;
    }
    return doc.createNode(tree.raw);
  }
  let { pair, key } = soleOf(existing);
  // all / any で包んだ（not を包んだときも含む）なら、元のノードは新しい演算子の子になる
  const wrapped = isNode(existing) && wrapsInGroup(condOfYaml(existing), tree);
  if (key === "not" && tree.op !== "not" && !wrapped && pair !== undefined && isNode(pair.value)) {
    // not を外した: 中身のノードを使い回す（中身に付いたコメントを残す）。not に付いていた
    // 前置きのコメントは中身へ移す（not ごと消すと、そのノードに書いた理由が消える）
    const outer = existing as Node;
    existing = pair.value;
    carryComment(outer, existing as Node);
    ({ pair, key } = soleOf(existing));
  }
  switch (tree.op) {
    case "glob":
    case "regex": {
      if (pair !== undefined && (key === "glob" || key === "regex") && isScalar(pair.value)) {
        if (key !== tree.op) {
          (pair.key as Scalar).value = tree.op;
        }
        const scalar = pair.value;
        if (scalar.value !== tree.pattern) {
          scalar.value = tree.pattern;
        }
        // 書き方は元のまま。ただし引用符の無い `on` `yes` などは実行ファイル（PyYAML）が真偽値に
        // 読むので囲む（setText と同じ。読み込みはこれを誤りに挙げている）
        if ((scalar.type === Scalar.PLAIN || scalar.type === undefined) && yaml11Ambiguous(tree.pattern)) {
          scalar.type = Scalar.QUOTE_SINGLE;
        }
        return existing as Node;
      }
      return mapOf(doc, tree.op, quoted(doc, tree.pattern));
    }
    case "all":
    case "any": {
      if (!wrapped && pair !== undefined && (key === "all" || key === "any") && isSeq(pair.value)) {
        if (key !== tree.op) {
          (pair.key as Scalar).value = tree.op;
        }
        const seq = pair.value;
        // 先頭の子の前のコメントは、読み込みではリストのほうに付く。子へ移しておけば、
        // 先頭の子を消したときにコメントも一緒に消え、新しい先頭に残らない（convertedFrom と同じ扱い）
        const first = seq.items[0];
        if (seq.commentBefore && isNode(first) && !first.commentBefore) {
          first.commentBefore = seq.commentBefore;
          seq.commentBefore = undefined;
        }
        seq.items = patchChildren(doc, seq.items, tree.children);
        if (seq.items.length === 0) {
          seq.flow = true;
        }
        return existing as Node;
      }
      // 演算子で包んだ: 元のノードを子の候補にする（包む前のノードのコメントを残す）
      const seq = new YAMLSeq();
      seq.items = patchChildren(doc, existing === undefined || existing === null ? [] : [existing], tree.children);
      seq.flow = seq.items.length === 0;
      return mapOf(doc, tree.op, seq);
    }
    case "not": {
      if (pair !== undefined && key === "not") {
        pair.value = tree.child === null ? null : patchCond(doc, pair.value ?? undefined, tree.child);
        return existing as Node;
      }
      // not で包んだ: 元のノードが中身に似ていれば中身に適用する
      if (tree.child === null) {
        return mapOf(doc, "not", null);
      }
      const inner = isNode(existing) && likeness(condOfYaml(existing), tree.child, false) > 0 ? existing : undefined;
      return mapOf(doc, "not", patchCond(doc, inner, tree.child));
    }
  }
}

/** 外したノードの前置きのコメントを、残すノードの前置きへ足す */
function carryComment(from: Node, to: Node): void {
  if (!from.commentBefore) {
    return;
  }
  to.commentBefore = to.commentBefore ? `${from.commentBefore}\n${to.commentBefore}` : from.commentBefore;
  to.spaceBefore = to.spaceBefore || from.spaceBefore;
}

/**
 * 新しいノードが、元のノードを all / any で包んだ形か。子のどれかが元のノードと同じか、
 * 元のノードとして直したと見るより、子のどれかのほうが元のノードに近いとき
 */
function wrapsInGroup(old: CondNode, next: CondNode): boolean {
  if (!isGroup(next) || sameCond(old, next)) {
    return false;
  }
  if (next.children.some((child) => sameCond(child, old))) {
    return true;
  }
  const direct = isGroup(old) ? groupLikeness(old, next, true) : 0;
  return next.children.some((child) => likeness(old, child, true) > direct);
}

/**
 * 演算子の子を書く。同じ中身の元のノードを先に適用する（並べ替え・消しただけなら、これで全部決まる）。
 *
 * 残りの子（中身を直した・足した・包んだ子）は、前後でヒットした子に挟まれた元のノードに適用する
 * （消されていない子は元の相対順のまま並ぶので、挟まれた範囲の外へは適用しない）。挟まれた範囲で
 * 数が同じなら順に適用する（直しただけ。種類が違えば適用せずに新しいノードにする。not や all / any で
 * 包んだ・外したものは同じ種類に数える）。数が違うと、どれを消してどれを直したのかが位置では
 * 決まらない。位置で適用すると消した子のコメントが別の子に付くので、似ているもの（個別条件なら文字が
 * 重なる）どうしだけを適用し、似たものが無い子は新しいノードにする。最後に、範囲の外に残ったもの
 * （並べ替えてから直した子）も、似ているものどうしだけを適用する。
 */
function patchChildren(doc: Document, olds: readonly unknown[], children: readonly CondNode[]): Node[] {
  const read = olds.map((item) => condOfYaml(item));
  const used = new Set<number>();
  const chosen: (number | undefined)[] = children.map((child) => {
    const j = read.findIndex((r, k) => !used.has(k) && sameCond(r, child));
    if (j >= 0) {
      used.add(j);
      return j;
    }
    return undefined;
  });
  const assign = (i: number, k: number): void => {
    chosen[i] = k;
    used.add(k);
  };
  // 似ている組から順に適用する。同じ似かたなら位置の近い組
  const pairUp = (rest: readonly number[], candidates: readonly number[]): void => {
    const pairs: { i: number; k: number; score: number }[] = [];
    for (const i of rest) {
      for (const k of candidates) {
        const score = likeness(read[k], children[i], true);
        if (score > 0) {
          pairs.push({ i, k, score });
        }
      }
    }
    pairs.sort((a, b) => b.score - a.score || Math.abs(a.i - a.k) - Math.abs(b.i - b.k));
    for (const { i, k } of pairs) {
      if (chosen[i] === undefined && !used.has(k)) {
        assign(i, k);
      }
    }
  };
  // 同じ中身でヒットした子のあいだに続く、ヒットしていない子の並び
  const runs: number[][] = [];
  let run: number[] = [];
  children.forEach((_, i) => {
    if (chosen[i] === undefined) {
      run.push(i);
      return;
    }
    if (run.length > 0) {
      runs.push(run);
    }
    run = [];
  });
  if (run.length > 0) {
    runs.push(run);
  }
  for (const r of runs) {
    const before = r[0] - 1;
    const after = r[r.length - 1] + 1;
    const lo = before >= 0 ? (chosen[before] as number) : -1;
    const hi = after < children.length ? (chosen[after] as number) : olds.length;
    const gap = olds.flatMap((_, k) => (k > lo && k < hi && !used.has(k) ? [k] : []));
    if (gap.length === r.length) {
      r.forEach((i, n) => {
        if (likeness(read[gap[n]], children[i], false) > 0) {
          assign(i, gap[n]);
        }
      });
    } else {
      pairUp(r, gap);
    }
  }
  pairUp(
    children.flatMap((_, i) => (chosen[i] === undefined ? [i] : [])),
    olds.flatMap((_, k) => (used.has(k) ? [] : [k])),
  );
  return children.map((child, i) => {
    const j = chosen[i];
    return patchCond(doc, j === undefined ? undefined : olds[j], child);
  });
}

/**
 * 元のノードと新しいノードがどれだけ似ているか。0 は別物。同じ種類なら 1 から 2 の間で大きいほど近い。
 * not や all / any で包んだ・外した組は、中身の似かたの半分（0 から 1）。
 *
 * `strict` のときは、種類が同じでも中身が重ならなければ 0 にする（個別条件なら文字が前か後ろで 1 字も
 * 揃わない、演算子なら似た子が 1 つも無い）。位置で決まらない組を適用するときに使う。
 */
function likeness(old: CondNode, next: CondNode, strict: boolean): number {
  if (isLeaf(old) && isLeaf(next)) {
    const text = textLikeness(old.pattern, next.pattern);
    return strict && text === 0 ? 0 : 1 + text;
  }
  if (isGroup(old) && isGroup(next)) {
    // 同じ演算子として直したか、演算子を演算子で包んだかの、近いほう
    return Math.max(groupLikeness(old, next, strict), ...next.children.map((child) => (isGroup(child) ? groupLikeness(old, child, strict) / 2 : 0)));
  }
  if (old.op === "not" && next.op === "not") {
    if (old.child === null || next.child === null) {
      return 1;
    }
    const inner = likeness(old.child, next.child, strict);
    return strict && inner === 0 ? 0 : 1 + inner / 2;
  }
  let score = 0;
  // 包んだ: not の中身か、演算子の子のどれかが元のノードに似ている
  if (next.op === "not" && next.child !== null) {
    score = Math.max(score, likeness(old, next.child, strict) / 2);
  }
  if (isGroup(next)) {
    score = Math.max(score, ...next.children.map((child) => likeness(old, child, strict) / 2));
  }
  // 外した: 元の not の中身が新しいノードに似ている
  if (old.op === "not" && old.child !== null) {
    score = Math.max(score, likeness(old.child, next, strict) / 2);
  }
  return score;
}

type CondLeaf = Extract<CondNode, { op: "glob" | "regex" }>;
type CondGroup = Extract<CondNode, { op: "all" | "any" }>;

function isLeaf(node: CondNode): node is CondLeaf {
  return node.op === "glob" || node.op === "regex";
}

function isGroup(node: CondNode): node is CondGroup {
  return node.op === "all" || node.op === "any";
}

/** 演算子どうしの似かた。同じ中身の子の割合で 1 から 2。`strict` なら、似た子が 1 つも無いと 0 */
function groupLikeness(old: CondGroup, next: CondGroup, strict: boolean): number {
  const shared = next.children.filter((child) => old.children.some((o) => sameCond(o, child))).length;
  if (strict && shared === 0 && (old.children.length > 0 || next.children.length > 0)) {
    const near = next.children.some((child) => old.children.some((o) => likeness(o, child, true) > 0));
    if (!near) {
      return 0;
    }
  }
  return 1 + shared / Math.max(1, old.children.length, next.children.length);
}

/** 2 つの文字の近さ（前と後ろで揃っている文字数の割合）。0 から 1 */
function textLikeness(a: string, b: string): number {
  const longest = Math.max(a.length, b.length);
  if (longest === 0) {
    return 1;
  }
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) {
    head += 1;
  }
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) {
    tail += 1;
  }
  return (head + tail) / longest;
}

/**
 * 刻みの欄（`every`）。書かれたままの文字を受け取り、1 以上の整数に読めれば数として、
 * 読めなければ打った文字のまま書く（止めるのは保存前の `--lint`）。空なら欄ごと消す。
 * 元に書いてある文字と同じなら何もしない。`every: "5"` のような書き方が、気づかないうちに画面で
 * 直されないためで、直してしまうと lint の error が操作で消える。欄が無ければ match の直後に足す。
 */
function setEvery(doc: Document, node: YAMLMap, written: string): void {
  if (written === scalarText(node, "every")) {
    return;
  }
  if (written === "") {
    node.delete("every");
    return;
  }
  const value: string | number = everyOf(written) ?? written;
  const current = node.get("every", true);
  if (current instanceof Scalar) {
    current.value = value;
    // 数に直したときだけ書き方も数のもの（引用符無し）に戻す。文字のままなら元の書き方を残す。
    if (typeof value === "number") {
      current.type = Scalar.PLAIN;
    }
    return;
  }
  const at = node.items.findIndex((p) => String((p.key as Scalar).value) === "match");
  node.items.splice(at >= 0 ? at + 1 : node.items.length, 0, doc.createPair("every", value));
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
    // ただし実行ファイル（PyYAML）が別の型に読む語を裸で書くことになるなら囲む。
    // `id: on` は "True" に、`id: no` は空の id に読まれる。
    if (current.value !== value) {
      current.value = value;
      if (current.type === Scalar.PLAIN && yaml11Ambiguous(value)) {
        current.type = Scalar.QUOTE_DOUBLE;
      }
    }
    return;
  }
  const scalar = doc.createNode(value) as Scalar;
  scalar.type = style === Scalar.PLAIN && yaml11Ambiguous(value) ? Scalar.QUOTE_DOUBLE : style;
  const pair = doc.createPair(key, scalar);
  const at = after === undefined ? -1 : node.items.findIndex((p) => String((p.key as Scalar).value) === after);
  if (at >= 0) {
    node.items.splice(at + 1, 0, pair);
  } else {
    node.items.push(pair);
  }
}

/** 画面が送ってきた配列を、形だけ確かめて受け取る */
export function asSections(raw: unknown): Sections | undefined {
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
  const excepts = Array.isArray(r.excepts) ? r.excepts.filter((v): v is string => typeof v === "string") : undefined;
  let when: CondNode | undefined;
  if (r.when !== undefined) {
    when = asCond(r.when);
    if (when === undefined) {
      return undefined;
    }
  }
  let exceptNodes: CondNode[] | undefined;
  if (Array.isArray(r.exceptNodes)) {
    exceptNodes = [];
    for (const item of r.exceptNodes) {
      const node = asCond(item);
      if (node === undefined) {
        return undefined;
      }
      exceptNodes.push(node);
    }
  }
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
    every: text(r.every),
    ...(excepts === undefined ? {} : { excepts }),
    ...(exceptNodes === undefined ? {} : { exceptNodes }),
    ...(when === undefined ? {} : { when }),
    ...(r.dropExcept === true ? { dropExcept: true } : {}),
    ...(r.bothPatterns === true ? { bothPatterns: true } : {}),
  };
}
