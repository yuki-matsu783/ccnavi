/**
 * 読み込んだフローと編集中のフローの見比べ。「未保存」の判定と、保存の前に見せる差分の一覧に使う。
 *
 * - `sameFlow` は中身が同じか（キーの順序は見ない）。元に戻して読み込んだときと同じ中身になれば、未保存を消す
 * - `diffFlows` は足した・消した・変えたノードと線。ノードは `id` で、線は両端と出入口
 *   （`from` `fromPort` `to` `toPort`）で突き合わせる（ユーザが書いた線は `id` が無いことも重なることもある）。
 *   同じ両端と出入口の線が何本もあれば、配列の順に突き合わせる
 * - `sameFlowIgnoringLayout` は位置とグループ化（`position` `style` `parentId`・`type: group` のノード。サブフローの中も）を除いて見比べる。
 *   「提案あり」の判定に使う。`textDiff` も同じ除き方をする
 * - `textDiff` は同じ突き合わせで、変わった欄の名前だけでなく値の前後（文はそのまま）まで並べる。エージェントの
 *   下書きを取り込む前に見せる。フローの文は担当のサブエージェントへの案内文になるので、ユーザが
 *   中身を読めるように、足したもの・消したものは全部の欄を、変えたものは変わった欄の前と後を出す
 *
 * **良し悪しは言わない。** 保存を止めるかは拡張ホストと実行ファイルが決める。
 *
 * ここには VS Code の API も DOM も node も入れない。
 */
import {
  connectionFrom,
  connectionFromPort,
  connectionsOf,
  connectionTo,
  connectionToPort,
  nodeName,
  nodeType,
  TYPE_LABELS,
  type FlowConnection,
  type FlowDoc,
  type FlowNode,
} from "./flow-doc.js";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** 値が同じか。辞書はキーの順序を見ない。配列は順も見る */
export function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) {
    return true;
  }
  if (Array.isArray(a) || Array.isArray(b)) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((item, i) => sameValue(item, b[i]));
  }
  if (isRecord(a) && isRecord(b)) {
    const keys = Object.keys(a).filter((key) => a[key] !== undefined);
    const other = Object.keys(b).filter((key) => b[key] !== undefined);
    return keys.length === other.length && keys.every((key) => Object.prototype.hasOwnProperty.call(b, key) && sameValue(a[key], b[key]));
  }
  return typeof a === "number" && typeof b === "number" && Number.isNaN(a) && Number.isNaN(b);
}

/** 中身が同じフローか（未保存の判定） */
export function sameFlow(a: FlowDoc, b: FlowDoc): boolean {
  return a === b || sameValue(a, b);
}

/** 見た目だけの欄（位置・グループの大きさと所属）。担当に渡る手順に効かない */
const LAYOUT_KEYS: ReadonlySet<string> = new Set(["position", "style", "parentId"]);

function nodesWithoutLayout(nodes: readonly FlowNode[]): FlowNode[] {
  return nodes
    .filter((node) => nodeType(node) !== "group")
    .map((node) => Object.fromEntries(Object.entries(node).filter(([key]) => !LAYOUT_KEYS.has(key))) as FlowNode);
}

/** 位置とグループを除いたフロー。グループの枠（`type: group`）のノードも除く。サブフロー（`subAgentFlows`）の中も同じ。下書きの見比べに使う */
function withoutLayout(doc: FlowDoc): FlowDoc {
  const flows = doc.subAgentFlows;
  const subFlows = Array.isArray(flows)
    ? flows.map((flow) => (isRecord(flow) && Array.isArray(flow.nodes) ? { ...flow, nodes: nodesWithoutLayout(flow.nodes as FlowNode[]) } : flow))
    : flows;
  return { ...doc, nodes: nodesWithoutLayout(doc.nodes), ...(flows === undefined ? {} : { subAgentFlows: subFlows }) };
}

/** 位置とグループ化の違いを無視して、中身が同じか（「提案あり」の判定） */
export function sameFlowIgnoringLayout(a: FlowDoc, b: FlowDoc): boolean {
  return sameFlow(withoutLayout(a), withoutLayout(b));
}

/** 変わった欄の呼び名。知らない欄は表記のまま */
const FIELD_LABELS: Readonly<Record<string, string>> = {
  name: "名前",
  type: "種類",
  position: "位置",
  data: "中身",
  parentId: "グループ",
  style: "大きさ",
  condition: "条件",
  id: "id",
  description: "説明",
};

export interface NodeChange {
  readonly id: string;
  /** 見せる名前（`名前（id）` の形。名前が無ければ id だけ） */
  readonly label: string;
  /** 変わった欄の呼び名（変えたノードだけ） */
  readonly fields: readonly string[];
}

export interface ConnectionChange {
  /** `開始 → 終了（branch-0）` の形 */
  readonly label: string;
  readonly fields: readonly string[];
}

export interface FlowDiff {
  /** フロー自体の欄（名前・説明など、`nodes` と `connections` 以外）で変わったもの */
  readonly meta: readonly string[];
  readonly addedNodes: readonly NodeChange[];
  readonly removedNodes: readonly NodeChange[];
  readonly changedNodes: readonly NodeChange[];
  readonly addedConnections: readonly ConnectionChange[];
  readonly removedConnections: readonly ConnectionChange[];
  readonly changedConnections: readonly ConnectionChange[];
}

export function isEmptyDiff(diff: FlowDiff): boolean {
  return (
    diff.meta.length === 0 &&
    diff.addedNodes.length === 0 &&
    diff.removedNodes.length === 0 &&
    diff.changedNodes.length === 0 &&
    diff.addedConnections.length === 0 &&
    diff.removedConnections.length === 0 &&
    diff.changedConnections.length === 0
  );
}

function fieldLabel(key: string): string {
  return FIELD_LABELS[key] ?? key;
}

function changedFields(a: Readonly<Record<string, unknown>>, b: Readonly<Record<string, unknown>>, skip: ReadonlySet<string>): string[] {
  const keys = [...new Set([...Object.keys(a), ...Object.keys(b)])].filter((key) => !skip.has(key));
  return keys.filter((key) => !sameValue(a[key], b[key])).map(fieldLabel);
}

function nodeLabel(node: FlowNode): string {
  const name = nodeName(node);
  const type = TYPE_LABELS[nodeType(node)] ?? nodeType(node);
  const head = name !== "" ? `${name}（${node.id}）` : node.id;
  return type !== "" && type !== name ? `${head} ${type}` : head;
}

function endpointName(doc: FlowDoc, id: string): string {
  const node = doc.nodes.find((n) => n.id === id);
  return node === undefined ? id : nodeName(node) || id;
}

function connectionKey(c: FlowConnection): string {
  return JSON.stringify([connectionFrom(c), connectionFromPort(c), connectionTo(c), connectionToPort(c)]);
}

function connectionLabelOf(doc: FlowDoc, c: FlowConnection): string {
  const port = connectionFromPort(c);
  return `${endpointName(doc, connectionFrom(c))} → ${endpointName(doc, connectionTo(c))}${port === "output" ? "" : `（${port}）`}`;
}

const NODE_SKIP: ReadonlySet<string> = new Set(["id"]);
const CONNECTION_SKIP: ReadonlySet<string> = new Set(["from", "to", "fromPort", "toPort"]);
const META_SKIP: ReadonlySet<string> = new Set(["nodes", "connections"]);

/** 突き合わせた結果。変えたものは（前, 後） */
interface Paired {
  readonly addedNodes: readonly FlowNode[];
  readonly removedNodes: readonly FlowNode[];
  readonly changedNodes: readonly (readonly [FlowNode, FlowNode])[];
  readonly addedConnections: readonly FlowConnection[];
  readonly removedConnections: readonly FlowConnection[];
  readonly changedConnections: readonly (readonly [FlowConnection, FlowConnection])[];
}

function pair(before: FlowDoc, after: FlowDoc): Paired {
  const beforeNodes = new Map(before.nodes.map((node) => [node.id, node]));
  const afterNodes = new Map(after.nodes.map((node) => [node.id, node]));
  const addedNodes: FlowNode[] = [];
  const changedNodes: (readonly [FlowNode, FlowNode])[] = [];
  for (const node of after.nodes) {
    const old = beforeNodes.get(node.id);
    if (old === undefined) {
      addedNodes.push(node);
    } else if (!sameValue(old, node)) {
      changedNodes.push([old, node]);
    }
  }
  const removedNodes = before.nodes.filter((node) => !afterNodes.has(node.id));

  // 線は両端と出入口で突き合わせる。同じものが何本もあれば配列の順に
  const pool = new Map<string, FlowConnection[]>();
  for (const c of connectionsOf(before)) {
    const key = connectionKey(c);
    pool.set(key, [...(pool.get(key) ?? []), c]);
  }
  const addedConnections: FlowConnection[] = [];
  const changedConnections: (readonly [FlowConnection, FlowConnection])[] = [];
  for (const c of connectionsOf(after)) {
    const key = connectionKey(c);
    const matches = pool.get(key) ?? [];
    const old = matches.shift();
    if (old === undefined) {
      addedConnections.push(c);
    } else if (!sameValue(old, c)) {
      changedConnections.push([old, c]);
    }
  }
  const removedConnections = [...pool.values()].flat();
  return { addedNodes, removedNodes, changedNodes, addedConnections, removedConnections, changedConnections };
}

/** `before`（読み込んだ時点）から `after`（いま）への差分 */
export function diffFlows(before: FlowDoc, after: FlowDoc): FlowDiff {
  const paired = pair(before, after);
  const node = (n: FlowNode): NodeChange => ({ id: n.id, label: nodeLabel(n), fields: [] });
  return {
    meta: changedFields(before, after, META_SKIP),
    addedNodes: paired.addedNodes.map(node),
    removedNodes: paired.removedNodes.map(node),
    changedNodes: paired.changedNodes.map(([old, n]) => ({ id: n.id, label: nodeLabel(n), fields: changedFields(old, n, NODE_SKIP) })),
    addedConnections: paired.addedConnections.map((c) => ({ label: connectionLabelOf(after, c), fields: [] })),
    removedConnections: paired.removedConnections.map((c) => ({ label: connectionLabelOf(before, c), fields: [] })),
    changedConnections: paired.changedConnections.map(([old, c]) => ({ label: connectionLabelOf(after, c), fields: changedFields(old, c, CONNECTION_SKIP) })),
  };
}

// ---- 値の前後まで見せる差分（下書きの取り込み）

/** 値の種類（画面が前後に添える）。`1` と `"1"`、`true` と `"true"`、`null` と `"null"` を見分けるため */
export type ValueKind = "文字列" | "数" | "真偽" | "null" | "辞書" | "配列" | "その他";

/** 欄 1 つの前後。足した欄は `before` が無く、消した欄は `after` が無い */
export interface FieldText {
  /** 欄の表記（`中身.prompt`・`中身.options[0].label` の形。頭の欄は呼び名にし、記号や英字以外を含むキーは `["…"]` で囲む） */
  readonly field: string;
  readonly before?: string;
  readonly beforeKind?: ValueKind;
  readonly after?: string;
  readonly afterKind?: ValueKind;
}

export interface TextChange {
  /** 何が変わったか（`added-node` など。`FlowDiff` の欄の名前に揃える） */
  readonly kind: "meta" | "added-node" | "removed-node" | "changed-node" | "added-connection" | "removed-connection" | "changed-connection";
  /** ノードは `名前（id） 種類`、線は `開始 → 終了（branch-0）`、フロー自体は空 */
  readonly label: string;
  readonly texts: readonly FieldText[];
}

export interface TextDiff {
  readonly changes: readonly TextChange[];
  /**
   * 欄ごとの前後に分けて見せられなかった理由（欄の表記が重なる・違うのに違う欄が見つからない）。あれば画面は取り込ませない。
   * 見せられなかったものは、生の JSON の前後を `texts` に入れてある
   */
  readonly problem?: string;
}

function kindOf(value: unknown): ValueKind {
  if (typeof value === "string") {
    return "文字列";
  }
  if (typeof value === "number") {
    return "数";
  }
  if (typeof value === "boolean") {
    return "真偽";
  }
  if (value === null) {
    return "null";
  }
  if (Array.isArray(value)) {
    return "配列";
  }
  return isRecord(value) ? "辞書" : "その他";
}

/** 値を見せる文。文字列はそのまま（改行も残す）、ほかは JSON の表記。種類は `kindOf` で別に添える */
function shown(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value) ?? String(value);
  } catch {
    return String(value);
  }
}

/** 英字・数字・`_` `-` だけのキーは `.キー` で、ほかは `["キー"]` で書く（`.` や `[` を含むキーで表記が重ならないように） */
const PLAIN_KEY = /^[A-Za-z_][A-Za-z0-9_-]*$/;

/** 頭の欄の表記。呼び名の在る欄は呼び名、英字のキーはそのまま、ほかは `["キー"]`（呼び名と重ならない） */
function headLabel(key: string): string {
  if (Object.prototype.hasOwnProperty.call(FIELD_LABELS, key)) {
    return FIELD_LABELS[key];
  }
  return PLAIN_KEY.test(key) ? key : `[${JSON.stringify(key)}]`;
}

/** 葉 1 つ。突き合わせは `path`（キーと番号の配列の JSON。型ごと持つので重ならない）で、`field` は見せるだけ */
interface Leaf {
  readonly field: string;
  readonly value: unknown;
}

/** 値を葉まで開く。辞書と配列は中へ下りる。空の辞書・配列は葉として扱う */
function leaves(value: unknown, path: readonly (string | number)[], field: string, out: Map<string, Leaf>): void {
  if (isRecord(value) && Object.keys(value).length > 0) {
    for (const [key, inner] of Object.entries(value)) {
      if (inner !== undefined) {
        leaves(inner, [...path, key], PLAIN_KEY.test(key) ? `${field}.${key}` : `${field}[${JSON.stringify(key)}]`, out);
      }
    }
    return;
  }
  if (Array.isArray(value) && value.length > 0) {
    value.forEach((inner, index) => leaves(inner, [...path, index], `${field}[${index}]`, out));
    return;
  }
  out.set(JSON.stringify(path), { field, value });
}

function leavesOf(value: Readonly<Record<string, unknown>>, skip: ReadonlySet<string>): Map<string, Leaf> {
  const out = new Map<string, Leaf>();
  for (const [key, inner] of Object.entries(value)) {
    if (!skip.has(key) && inner !== undefined) {
      leaves(inner, [key], headLabel(key), out);
    }
  }
  return out;
}

/** 2 つの値の、違う葉の前後。片方にしか無い葉は、もう片方を空にする。見せる表記が重なれば `ambiguous` */
function fieldTexts(
  a: Readonly<Record<string, unknown>> | undefined,
  b: Readonly<Record<string, unknown>> | undefined,
  skip: ReadonlySet<string>,
): { readonly texts: FieldText[]; readonly ambiguous: boolean } {
  const before = a === undefined ? new Map<string, Leaf>() : leavesOf(a, skip);
  const after = b === undefined ? new Map<string, Leaf>() : leavesOf(b, skip);
  const paths = [...new Set([...before.keys(), ...after.keys()])];
  const texts: FieldText[] = [];
  const fields = new Map<string, string>();
  let ambiguous = false;
  for (const path of paths) {
    const was = before.get(path);
    const now = after.get(path);
    const field = (now ?? was)?.field ?? "";
    const seen = fields.get(field);
    if (seen !== undefined && seen !== path) {
      ambiguous = true;
    }
    fields.set(field, path);
    if (was !== undefined && now !== undefined && sameValue(was.value, now.value)) {
      continue;
    }
    texts.push({
      field,
      ...(was === undefined ? {} : { before: shown(was.value), beforeKind: kindOf(was.value) }),
      ...(now === undefined ? {} : { after: shown(now.value), afterKind: kindOf(now.value) }),
    });
  }
  return { texts, ambiguous };
}

/** 欄ごとに分けられなかったときの、生の JSON の前後 */
function rawTexts(a: unknown, b: unknown): FieldText[] {
  return [
    {
      field: "（全体の JSON）",
      ...(a === undefined ? {} : { before: shown(a), beforeKind: kindOf(a) }),
      ...(b === undefined ? {} : { after: shown(b), afterKind: kindOf(b) }),
    },
  ];
}

/**
 * `before`（いまのフロー）から `after`（下書き）への差分を、値の前後まで。欄の表記が重なるか、違うのに違う欄が
 * 見つからないものは、生の JSON の前後を入れて `problem` で言う（画面は取り込ませない）
 */
export function textDiff(rawBefore: FlowDoc, rawAfter: FlowDoc): TextDiff {
  const before = withoutLayout(rawBefore);
  const after = withoutLayout(rawAfter);
  const paired = pair(before, after);
  const changes: TextChange[] = [];
  let problem: string | undefined;
  const add = (kind: TextChange["kind"], label: string, a: Readonly<Record<string, unknown>> | undefined, b: Readonly<Record<string, unknown>> | undefined, skip: ReadonlySet<string>): void => {
    const made = fieldTexts(a, b, skip);
    if (made.ambiguous) {
      problem = "欄の名前が重なって見分けられないもの（`.` や `[` を含むキーなど）があるため、欄ごとの前後に分けられません";
      changes.push({ kind, label, texts: rawTexts(a, b) });
    } else if (made.texts.length === 0 && a !== undefined && b !== undefined && !sameValue(a, b)) {
      problem = "中身が違うのに、違う欄を見つけられません";
      changes.push({ kind, label, texts: rawTexts(a, b) });
    } else if (made.texts.length > 0) {
      changes.push({ kind, label, texts: made.texts });
    }
  };
  const metaOf = (doc: FlowDoc): Record<string, unknown> => Object.fromEntries(Object.entries(doc).filter(([key]) => !META_SKIP.has(key)));
  add("meta", "", metaOf(before), metaOf(after), META_SKIP);
  for (const n of paired.addedNodes) {
    add("added-node", nodeLabel(n), undefined, n, NODE_SKIP);
  }
  for (const n of paired.removedNodes) {
    add("removed-node", nodeLabel(n), n, undefined, NODE_SKIP);
  }
  for (const [old, n] of paired.changedNodes) {
    add("changed-node", nodeLabel(n), old, n, NODE_SKIP);
  }
  for (const c of paired.addedConnections) {
    add("added-connection", connectionLabelOf(after, c), undefined, c, CONNECTION_SKIP);
  }
  for (const c of paired.removedConnections) {
    add("removed-connection", connectionLabelOf(before, c), c, undefined, CONNECTION_SKIP);
  }
  for (const [old, c] of paired.changedConnections) {
    add("changed-connection", connectionLabelOf(after, c), old, c, CONNECTION_SKIP);
  }
  return problem === undefined ? { changes } : { changes, problem };
}
