/**
 * 読み込んだフローと編集中のフローの見比べ。「未保存」の判定と、保存の前に見せる差分の一覧に使う。
 *
 * - `sameFlow` は中身が同じか（キーの並びは見ない）。元に戻して読み込んだときと同じ中身になれば、未保存を消す
 * - `diffFlows` は足した・消した・変えたノードと線。ノードは `id` で、線は両端と出入口
 *   （`from` `fromPort` `to` `toPort`）で突き合わせる（ユーザが書いた線は `id` が無いことも重なることもある）。
 *   同じ両端と出入口の線が何本もあれば、並びの順に突き合わせる
 * - `textDiff` は同じ突き合わせで、変わった欄の名前だけでなく値の前後（文はそのまま）まで並べる。エージェントの
 *   下書きを取り込む前に見せる（ADR-0100）。フローの文は担当のサブエージェントへの案内文になるので、ユーザが
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

/** 値が同じか。辞書はキーの並びを見ない。並びは順も見る */
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

/** 変わった欄の呼び名。知らない欄は綴りのまま */
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

  // 線は両端と出入口で突き合わせる。同じものが何本もあれば並びの順に
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

// ---- 値の前後まで見せる差分（下書きの取り込み。ADR-0100）

/** 欄 1 つの前後。足した欄は `before` が無く、消した欄は `after` が無い */
export interface FieldText {
  /** 欄の綴り（`中身.prompt`・`中身.options[0].label` の形。頭の欄だけ呼び名にする） */
  readonly field: string;
  readonly before?: string;
  readonly after?: string;
}

export interface TextChange {
  /** 何が変わったか（`added-node` など。`FlowDiff` の欄の名前に揃える） */
  readonly kind: "meta" | "added-node" | "removed-node" | "changed-node" | "added-connection" | "removed-connection" | "changed-connection";
  /** ノードは `名前（id） 種類`、線は `開始 → 終了（branch-0）`、フロー自体は空 */
  readonly label: string;
  readonly texts: readonly FieldText[];
}

/** 値を見せる文。文字列はそのまま（改行も残す）、ほかは JSON の綴り */
function shown(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (value === undefined) {
    return "";
  }
  try {
    return JSON.stringify(value) ?? String(value);
  } catch {
    return String(value);
  }
}

/** 値を葉まで開く。辞書と並びは中へ下りる（並びは `[番号]`）。空の辞書・並びは葉として扱う */
function leaves(value: unknown, at: string, out: Map<string, string>): void {
  if (isRecord(value) && Object.keys(value).length > 0) {
    for (const [key, inner] of Object.entries(value)) {
      if (inner !== undefined) {
        leaves(inner, at === "" ? fieldLabel(key) : `${at}.${key}`, out);
      }
    }
    return;
  }
  if (Array.isArray(value) && value.length > 0) {
    value.forEach((inner, index) => leaves(inner, `${at}[${index}]`, out));
    return;
  }
  out.set(at, shown(value));
}

function leavesOf(value: Readonly<Record<string, unknown>>, skip: ReadonlySet<string>): Map<string, string> {
  const out = new Map<string, string>();
  for (const [key, inner] of Object.entries(value)) {
    if (!skip.has(key) && inner !== undefined) {
      leaves(inner, fieldLabel(key), out);
    }
  }
  return out;
}

/** 2 つの値の、違う葉の前後。片方にしか無い葉は、もう片方を空にする */
function fieldTexts(a: Readonly<Record<string, unknown>> | undefined, b: Readonly<Record<string, unknown>> | undefined, skip: ReadonlySet<string>): FieldText[] {
  const before = a === undefined ? new Map<string, string>() : leavesOf(a, skip);
  const after = b === undefined ? new Map<string, string>() : leavesOf(b, skip);
  const fields = [...new Set([...before.keys(), ...after.keys()])];
  const out: FieldText[] = [];
  for (const field of fields) {
    const was = before.get(field);
    const now = after.get(field);
    if (was === now) {
      continue;
    }
    out.push({ field, ...(was === undefined ? {} : { before: was }), ...(now === undefined ? {} : { after: now }) });
  }
  return out;
}

/** `before`（いまのフロー）から `after`（下書き）への差分を、値の前後まで */
export function textDiff(before: FlowDoc, after: FlowDoc): readonly TextChange[] {
  const paired = pair(before, after);
  const out: TextChange[] = [];
  const meta = fieldTexts(before, after, META_SKIP);
  if (meta.length > 0) {
    out.push({ kind: "meta", label: "", texts: meta });
  }
  for (const n of paired.addedNodes) {
    out.push({ kind: "added-node", label: nodeLabel(n), texts: fieldTexts(undefined, n, NODE_SKIP) });
  }
  for (const n of paired.removedNodes) {
    out.push({ kind: "removed-node", label: nodeLabel(n), texts: fieldTexts(n, undefined, NODE_SKIP) });
  }
  for (const [old, n] of paired.changedNodes) {
    out.push({ kind: "changed-node", label: nodeLabel(n), texts: fieldTexts(old, n, NODE_SKIP) });
  }
  for (const c of paired.addedConnections) {
    out.push({ kind: "added-connection", label: connectionLabelOf(after, c), texts: fieldTexts(undefined, c, CONNECTION_SKIP) });
  }
  for (const c of paired.removedConnections) {
    out.push({ kind: "removed-connection", label: connectionLabelOf(before, c), texts: fieldTexts(c, undefined, CONNECTION_SKIP) });
  }
  for (const [old, c] of paired.changedConnections) {
    out.push({ kind: "changed-connection", label: connectionLabelOf(after, c), texts: fieldTexts(old, c, CONNECTION_SKIP) });
  }
  return out;
}
