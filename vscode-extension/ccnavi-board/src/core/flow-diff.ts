/**
 * 読み込んだフローと編集中のフローの見比べ。「未保存」の判定と、保存の前に見せる差分の一覧に使う。
 *
 * - `sameFlow` は中身が同じか（キーの並びは見ない）。元に戻して読み込んだときと同じ中身になれば、未保存を消す
 * - `diffFlows` は足した・消した・変えたノードと線。ノードは `id` で、線は両端と出入口
 *   （`from` `fromPort` `to` `toPort`）で突き合わせる（ユーザが書いた線は `id` が無いことも重なることもある）。
 *   同じ両端と出入口の線が何本もあれば、並びの順に突き合わせる
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

/** `before`（読み込んだ時点）から `after`（いま）への差分 */
export function diffFlows(before: FlowDoc, after: FlowDoc): FlowDiff {
  const meta = changedFields(before, after, META_SKIP);
  const beforeNodes = new Map(before.nodes.map((node) => [node.id, node]));
  const afterNodes = new Map(after.nodes.map((node) => [node.id, node]));
  const addedNodes: NodeChange[] = [];
  const changedNodes: NodeChange[] = [];
  for (const node of after.nodes) {
    const old = beforeNodes.get(node.id);
    if (old === undefined) {
      addedNodes.push({ id: node.id, label: nodeLabel(node), fields: [] });
    } else if (!sameValue(old, node)) {
      changedNodes.push({ id: node.id, label: nodeLabel(node), fields: changedFields(old, node, NODE_SKIP) });
    }
  }
  const removedNodes = before.nodes.filter((node) => !afterNodes.has(node.id)).map((node) => ({ id: node.id, label: nodeLabel(node), fields: [] }));

  // 線は両端と出入口で突き合わせる。同じものが何本もあれば並びの順に
  const pool = new Map<string, FlowConnection[]>();
  for (const c of connectionsOf(before)) {
    const key = connectionKey(c);
    pool.set(key, [...(pool.get(key) ?? []), c]);
  }
  const addedConnections: ConnectionChange[] = [];
  const changedConnections: ConnectionChange[] = [];
  for (const c of connectionsOf(after)) {
    const key = connectionKey(c);
    const matches = pool.get(key) ?? [];
    const old = matches.shift();
    if (old === undefined) {
      addedConnections.push({ label: connectionLabelOf(after, c), fields: [] });
    } else if (!sameValue(old, c)) {
      changedConnections.push({ label: connectionLabelOf(after, c), fields: changedFields(old, c, CONNECTION_SKIP) });
    }
  }
  const removedConnections = [...pool.values()].flat().map((c) => ({ label: connectionLabelOf(before, c), fields: [] }));
  return { meta, addedNodes, removedNodes, changedNodes, addedConnections, removedConnections, changedConnections };
}
