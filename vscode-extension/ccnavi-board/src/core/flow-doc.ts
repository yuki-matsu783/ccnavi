/**
 * 子チケットのフロー（設計 9.3.1、ADR-0085）の読み書き。フロー編集画面と拡張ホストが分け合う。
 *
 * ファイルは YAML の 1 文書（既定 `.ccnavi/approved/flows/<子>.yml`）。形は実行ファイル（`ccnavi/flow.py`）が読むもの。
 *
 *     id, name, description?, version
 *     nodes:          [node, ...]
 *     connections:    [connection, ...]
 *     subAgentFlows?: [{id, name, nodes, connections}, ...]
 *     node       = {id, type, name, position: {x, y}, data: {...}}
 *     connection = {id, from, to, fromPort, toPort, condition?}
 *
 * **知らない欄も知らない種類も落とさない。** 読んだ中身をそのまま持ち、編集はその写しの
 * 触ったところだけを差し替える（`phases-doc.ts` が YAML の知らない欄を残すのと同じ考え）。
 * 欠けた欄（`position` や `data`）も、読むときに既定で補うだけで、触るまで書き足さない。
 * 書き出しは中身から組み直す（コメントや書き方は残らない。人が保存したときだけ書く）。
 *
 * **判定はしない。** 着手中に書けるかは実行ファイルが `flow.locked` で言う（ADR-0085）。
 * 入れ子の段の数（`nesting`）は案内で、止めるのは実行ファイルでも画面でもなく、上限に当たった
 * サブエージェントに Agent ツールが渡らないこと（そのノードで止まってメインへ戻る）。
 *
 * ここには VS Code の API も node も DOM も入れない。画面（React）が束ねて読むため。
 */
import { Document, parseDocument, Scalar, visit, type DocumentOptions, type ParseOptions, type ScalarTag, type SchemaOptions } from "yaml";

import { yaml11Ambiguous } from "./yaml11.js";

/** ノード 1 つ。読んだまま。欄は `nodeType` などの読み口で読む */
export interface FlowNode {
  readonly id: string;
  readonly [key: string]: unknown;
}

/** 線 1 本。読んだまま */
export interface FlowConnection {
  readonly [key: string]: unknown;
}

/** フロー 1 本。`nodes` だけは必ず並び。ほかの欄は読んだまま */
export interface FlowDoc {
  readonly nodes: readonly FlowNode[];
  readonly [key: string]: unknown;
}

export interface FlowPoint {
  readonly x: number;
  readonly y: number;
}

export type FlowRead = { readonly ok: true; readonly doc: FlowDoc } | { readonly ok: false; readonly error: string };

// ---- 種類

/** 画面の部品箱に並べる種類。並びもこの順 */
export const PALETTE = ["start", "end", "prompt", "subAgent", "askUserQuestion", "ifElse", "switch", "skill"] as const;
export type PaletteType = (typeof PALETTE)[number];

/** 種類の呼び名。部品箱とノードの見出しに出す */
export const TYPE_LABELS: Readonly<Record<string, string>> = {
  start: "開始",
  end: "終了",
  prompt: "プロンプト",
  subAgent: "サブエージェント",
  askUserQuestion: "利用者に聞く",
  ifElse: "分岐（if / else）",
  switch: "分岐（switch）",
  branch: "分岐",
  skill: "スキル",
  mcp: "MCP",
  subAgentFlow: "サブフロー",
  codex: "Codex",
  branchSession: "分岐セッション",
  group: "グループ",
};

/** 画面が欄を持つ種類。これ以外は種類の名前と `name` だけで見せ、`data` は読むだけにする */
export function isEditableType(type: string): type is PaletteType {
  return (PALETTE as readonly string[]).includes(type);
}

/** 分岐の出口を持つ種類。出口は `data` の並び（`branches` か `options`）の 1 件ずつ */
export function branchKey(type: string): "branches" | "options" | undefined {
  if (type === "ifElse" || type === "switch" || type === "branch") {
    return "branches";
  }
  if (type === "askUserQuestion") {
    return "options";
  }
  return undefined;
}

/** 出入口の綴り。`input` / `output` / `branch-<番号>`（実行ファイルの案内 `flow._port_label` も同じ綴りで読む） */
export const INPUT_PORT = "input";
export const OUTPUT_PORT = "output";
export function branchPort(index: number): string {
  return `branch-${index}`;
}

// ---- 入れ子の上限（ADR-0085、付録 C）

/** 入れ子の起動の既定の上限（`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`）。メインの下の段の数 */
export const SPAWN_LIMIT = 3;
/** 子チケットの担当のサブエージェントの段（メインの下 1 段目） */
export const CHILD_LAYER = 1;
/** 子のフローの中で重ねてよい段の数。子の下 2 段まで */
export const NEST_ALLOWED = SPAWN_LIMIT - CHILD_LAYER;

// ---- 読み口（欠けた欄は既定で読む。書き足さない）

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown): string {
  return typeof value === "string" ? value : typeof value === "number" || typeof value === "boolean" ? String(value) : "";
}

export function nodeType(node: FlowNode): string {
  return str(node.type);
}

export function nodeName(node: FlowNode): string {
  return str(node.name);
}

export function nodeData(node: FlowNode): Readonly<Record<string, unknown>> {
  return isRecord(node.data) ? node.data : {};
}

export function nodePosition(node: FlowNode, index = 0): FlowPoint {
  const raw = isRecord(node.position) ? node.position : {};
  const x = typeof raw.x === "number" && Number.isFinite(raw.x) ? raw.x : 80 + (index % 4) * 240;
  const y = typeof raw.y === "number" && Number.isFinite(raw.y) ? raw.y : 80 + Math.floor(index / 4) * 160;
  return { x, y };
}

export function connectionsOf(doc: FlowDoc): readonly FlowConnection[] {
  return Array.isArray(doc.connections) ? (doc.connections as unknown[]).filter(isRecord) : [];
}

export function connectionId(c: FlowConnection): string {
  return str(c.id);
}

export function connectionFrom(c: FlowConnection): string {
  return str(c.from);
}

export function connectionTo(c: FlowConnection): string {
  return str(c.to);
}

export function connectionFromPort(c: FlowConnection): string {
  return str(c.fromPort) || OUTPUT_PORT;
}

export function connectionToPort(c: FlowConnection): string {
  return str(c.toPort) || INPUT_PORT;
}

/** 文字列の欄。無ければ空 */
export function dataText(node: FlowNode, key: string): string {
  return str(nodeData(node)[key]);
}

/** 分岐の出口の並び（`branches` / `options`）。1 件は `{label, condition?, description?, ...}` */
export function branchItems(node: FlowNode): readonly Readonly<Record<string, unknown>>[] {
  const key = branchKey(nodeType(node));
  if (key === undefined) {
    return [];
  }
  const raw = nodeData(node)[key];
  return Array.isArray(raw) ? raw.filter(isRecord) : [];
}

// ---- 読む・書く

/**
 * 真偽値の綴り。PyYAML の resolver.py と同じで、YAML 1.1 の仕様にある `y` `n` は入れない
 * （PyYAML は文字として読む。`position` の `y` が真偽値のキーに化けないように）。
 */
const PY_TRUE: ScalarTag = {
  identify: (value) => value === true,
  default: true,
  tag: "tag:yaml.org,2002:bool",
  test: /^(?:yes|Yes|YES|true|True|TRUE|on|On|ON)$/,
  resolve: () => true,
};
const PY_FALSE: ScalarTag = {
  identify: (value) => value === false,
  default: true,
  tag: "tag:yaml.org,2002:bool",
  test: /^(?:no|No|NO|false|False|FALSE|off|Off|OFF)$/,
  resolve: () => false,
};

/**
 * 読みの設定。実行ファイルの読み手（PyYAML の `safe_load`、YAML 1.1）に合わせ、`yes` `on` は真偽値、
 * `0755` は八進として読む（画面と実行ファイルで同じ値に見えるように）。真偽値の綴りは PyYAML のもの
 * （`PY_TRUE` / `PY_FALSE`）に差し替え、日付は文字のまま持つ（日付の値を持つと、書き出すときに形が崩れる）。
 */
const READ_OPTIONS: ParseOptions & DocumentOptions & SchemaOptions = {
  version: "1.1",
  customTags: (tags) => [
    PY_TRUE,
    PY_FALSE,
    ...tags.filter((tag) => (typeof tag === "string" ? tag !== "timestamp" && tag !== "bool" : !tag.tag.endsWith(":timestamp") && !tag.tag.endsWith(":bool"))),
  ],
};

/** 別名（`*名前`）を持つか。実行ファイルと同じく、あれば読まない */
function hasAlias(doc: Document): boolean {
  let found = false;
  visit(doc, {
    Alias() {
      found = true;
      return visit.BREAK;
    },
  });
  return found;
}

/**
 * YAML の本文を読む。形が読めなければ理由を返す（`nodes` の並びが無い、ノードに `id` が無い）。
 * 実行ファイル（`flow.load`）が読めないと言う形は、ここでも読めないと言う。**例外は外に出さない。**
 * 別名（`*名前`）は拒む。同じ部分木を何度も辿らせて、小さなファイルを膨らませられるため。
 */
export function parseFlow(text: string): FlowRead {
  let raw: unknown;
  try {
    const doc = parseDocument(text.replace(/^\uFEFF/, ""), READ_OPTIONS);
    const problem = doc.errors[0];
    if (problem !== undefined) {
      return { ok: false, error: `YAML として読めない（${firstLine(problem.message)}）` };
    }
    if (hasAlias(doc)) {
      return { ok: false, error: "YAML の別名（`*名前`）があるので読まない。同じ部分木を何度も辿らせて膨らませられる" };
    }
    // 別名は上で断っている。ここは念押しで、辿ろうとしたら投げさせる（下の catch が理由にする）
    raw = doc.toJS({ maxAliasCount: 0 });
  } catch (error) {
    return { ok: false, error: `YAML として読めない（${firstLine(error instanceof Error ? error.message : String(error))}）` };
  }
  return checkFlow(raw);
}

function firstLine(text: string): string {
  return text.split("\n")[0].replace(/:$/, "").trim();
}

/** 形を確かめる。画面から届いた保存の中身も、ここを通してから書く */
export function checkFlow(raw: unknown): FlowRead {
  if (!isRecord(raw)) {
    return { ok: false, error: "最上位がキーと値の並びではない" };
  }
  if (!Array.isArray(raw.nodes)) {
    return { ok: false, error: "`nodes` の並びが無い" };
  }
  const ids = new Set<string>();
  for (const [index, node] of raw.nodes.entries()) {
    if (!isRecord(node)) {
      return { ok: false, error: `nodes[${index}] がオブジェクトではない` };
    }
    if (typeof node.id !== "string" || node.id === "") {
      return { ok: false, error: `nodes[${index}] に id が無い` };
    }
    if (ids.has(node.id)) {
      return { ok: false, error: `ノードの id が重なっている（${node.id}）` };
    }
    ids.add(node.id);
  }
  if (raw.connections !== undefined) {
    if (!Array.isArray(raw.connections)) {
      return { ok: false, error: "`connections` が並びではない" };
    }
    for (const [index, c] of raw.connections.entries()) {
      if (!isRecord(c)) {
        return { ok: false, error: `connections[${index}] がオブジェクトではない` };
      }
    }
  }
  return { ok: true, doc: raw as FlowDoc };
}

/** 画面から届いたものを受ける形。形が崩れていたら undefined（書かない） */
export function asFlowDoc(raw: unknown): FlowDoc | undefined {
  const read = checkFlow(raw);
  return read.ok ? read.doc : undefined;
}

/**
 * 書き出す本文。字下げ 2 のブロック形式で、長い行を折らない。複数行の文は `|` の形で書く。
 * 同じ中身が 2 度出ても別名（`&` / `*`）にしない（実行ファイルは別名を読まない）。
 * 実行ファイル（YAML 1.1）が文字以外に読む綴り（`yes` `0755` `2026-01-01` など）は引用符で囲む。
 * `y` `n` は PyYAML が文字として読むので囲まない（`position` の `y` をそのまま書く）。
 */
export function serializeFlow(doc: FlowDoc): string {
  return yamlText(doc);
}

/** 値を書き出しと同じ形の YAML にする。画面が欄を持たない種類の `data` を見せるのにも使う */
export function yamlText(value: unknown): string {
  const out = new Document(value, { aliasDuplicateObjects: false });
  visit(out, {
    Scalar(_key, node) {
      if (typeof node.value === "string" && yaml11Ambiguous(node.value) && !/^[yYnN]$/.test(node.value)) {
        node.type = Scalar.QUOTE_DOUBLE;
      }
    },
  });
  return out.toString({ lineWidth: 0 });
}

/**
 * ファイルが無いときに見せる最小のフロー（開始 → 終了）。保存するまでファイルは作らない。
 * 識別子と名前は子チケットから付ける。
 */
export function templateFlow(ticket: string, title: string): FlowDoc {
  return {
    id: `${ticket}-flow`,
    name: title === "" ? ticket : `${ticket} ${title}`,
    description: "",
    version: "1.0.0",
    nodes: [
      { id: "start", type: "start", name: "開始", position: { x: 80, y: 160 }, data: { label: "開始" } },
      { id: "end", type: "end", name: "終了", position: { x: 440, y: 160 }, data: { label: "終了" } },
    ],
    connections: [{ id: "c-start-end", from: "start", to: "end", fromPort: OUTPUT_PORT, toPort: INPUT_PORT }],
  };
}

// ---- 編集（どれも新しい写しを返す。触ったところ以外は元のまま）

/** 新しいノードの `data`。画面が欄を持つものだけ */
export function defaultData(type: PaletteType): Record<string, unknown> {
  switch (type) {
    case "start":
      return { label: "開始" };
    case "end":
      return { label: "終了" };
    case "prompt":
      return { prompt: "" };
    case "subAgent":
      return { description: "", prompt: "" };
    case "askUserQuestion":
      return {
        questionText: "",
        multiSelect: false,
        options: [
          { label: "はい", description: "" },
          { label: "いいえ", description: "" },
        ],
      };
    case "ifElse":
      return {
        evaluationTarget: "",
        branches: [
          { label: "真", condition: "" },
          { label: "偽", condition: "" },
        ],
      };
    case "switch":
      return {
        evaluationTarget: "",
        branches: [
          { label: "ケース 1", condition: "" },
          { label: "既定", condition: "default" },
        ],
      };
    case "skill":
      return { name: "", description: "" };
  }
}

/** 使われていない id。`<種類>-<番号>` */
export function freshNodeId(doc: FlowDoc, type: string): string {
  const used = new Set(doc.nodes.map((node) => node.id));
  let n = 1;
  while (used.has(`${type}-${n}`)) {
    n += 1;
  }
  return `${type}-${n}`;
}

function freshConnectionId(doc: FlowDoc, from: string, to: string): string {
  const used = new Set(connectionsOf(doc).map(connectionId));
  let id = `c-${from}-${to}`;
  let n = 2;
  while (used.has(id)) {
    id = `c-${from}-${to}-${n}`;
    n += 1;
  }
  return id;
}

export function addNode(doc: FlowDoc, type: PaletteType, position: FlowPoint): { readonly doc: FlowDoc; readonly id: string } {
  const id = freshNodeId(doc, type);
  const node: FlowNode = { id, type, name: TYPE_LABELS[type] ?? type, position: { x: position.x, y: position.y }, data: defaultData(type) };
  return { doc: { ...doc, nodes: [...doc.nodes, node] }, id };
}

function mapNode(doc: FlowDoc, id: string, change: (node: FlowNode) => FlowNode): FlowDoc {
  return { ...doc, nodes: doc.nodes.map((node) => (node.id === id ? change(node) : node)) };
}

/** 名前を変える */
export function renameNode(doc: FlowDoc, id: string, name: string): FlowDoc {
  return mapNode(doc, id, (node) => ({ ...node, name }));
}

/** `data` の欄を差し替える。渡さなかった欄は元のまま */
export function patchData(doc: FlowDoc, id: string, patch: Readonly<Record<string, unknown>>): FlowDoc {
  return mapNode(doc, id, (node) => ({ ...node, data: { ...nodeData(node), ...patch } }));
}

export function moveNode(doc: FlowDoc, id: string, position: FlowPoint): FlowDoc {
  return mapNode(doc, id, (node) => {
    const now = isRecord(node.position) ? node.position : {};
    return { ...node, position: { ...now, x: Math.round(position.x), y: Math.round(position.y) } };
  });
}

/** ノードを消す。そのノードに出入りする線も消す */
export function removeNode(doc: FlowDoc, id: string): FlowDoc {
  const next: Record<string, unknown> = { ...doc, nodes: doc.nodes.filter((node) => node.id !== id) };
  if (Array.isArray(doc.connections)) {
    next.connections = connectionsOf(doc).filter((c) => connectionFrom(c) !== id && connectionTo(c) !== id);
  }
  return next as FlowDoc;
}

/**
 * 線を足す。同じ出口から同じ入口への線が既にあれば足さない。自分へ戻る線も足さない。
 * `connections` が無かったフローには、ここで初めて欄ができる。
 */
export function connect(doc: FlowDoc, from: string, fromPort: string, to: string, toPort: string): FlowDoc {
  if (from === to) {
    return doc;
  }
  const now = connectionsOf(doc);
  if (now.some((c) => connectionFrom(c) === from && connectionTo(c) === to && connectionFromPort(c) === fromPort && connectionToPort(c) === toPort)) {
    return doc;
  }
  const added: FlowConnection = { id: freshConnectionId(doc, from, to), from, to, fromPort, toPort };
  return { ...doc, connections: [...now, added] };
}

/**
 * 線を消す。線は**並びの位置で指す**（人が書いたフローの線は id が無いことも重なることもある）。
 */
export function removeConnectionAt(doc: FlowDoc, index: number): FlowDoc {
  return { ...doc, connections: connectionsOf(doc).filter((_, i) => i !== index) };
}

/** 線の条件（`condition`）。空にしたら欄ごと外す（書いていなかった線と同じに戻す） */
export function setConditionAt(doc: FlowDoc, index: number, condition: string): FlowDoc {
  return {
    ...doc,
    connections: connectionsOf(doc).map((c, i) => {
      if (i !== index) {
        return c;
      }
      const { condition: _dropped, ...rest } = c;
      void _dropped;
      return condition === "" ? rest : { ...rest, condition };
    }),
  };
}

/** フロー自体の名前と説明 */
export function setMeta(doc: FlowDoc, patch: { readonly name?: string; readonly description?: string }): FlowDoc {
  return { ...doc, ...patch };
}

/**
 * 分岐の出口を 1 件足す。
 */
export function addBranch(doc: FlowDoc, id: string, item: Readonly<Record<string, unknown>>): FlowDoc {
  return mapNode(doc, id, (node) => {
    const key = branchKey(nodeType(node));
    if (key === undefined) {
      return node;
    }
    return { ...node, data: { ...nodeData(node), [key]: [...branchItems(node), item] } };
  });
}

/** 分岐の出口の 1 件の欄を差し替える */
export function patchBranch(doc: FlowDoc, id: string, index: number, patch: Readonly<Record<string, unknown>>): FlowDoc {
  return mapNode(doc, id, (node) => {
    const key = branchKey(nodeType(node));
    if (key === undefined) {
      return node;
    }
    const items = branchItems(node).map((item, i) => (i === index ? { ...item, ...patch } : item));
    return { ...node, data: { ...nodeData(node), [key]: items } };
  });
}

/**
 * 分岐の出口を 1 件消す。その出口から出る線は消し、後ろの出口（`branch-<番号>`）の線は番号を 1 つ詰める。
 * 詰めないと、残った線が 1 つずれた出口に付き替わる。
 */
export function removeBranch(doc: FlowDoc, id: string, index: number): FlowDoc {
  const next = mapNode(doc, id, (node) => {
    const key = branchKey(nodeType(node));
    if (key === undefined) {
      return node;
    }
    return { ...node, data: { ...nodeData(node), [key]: branchItems(node).filter((_, i) => i !== index) } };
  });
  if (!Array.isArray(doc.connections)) {
    return next;
  }
  const connections: FlowConnection[] = [];
  for (const c of connectionsOf(next)) {
    if (connectionFrom(c) !== id) {
      connections.push(c);
      continue;
    }
    const match = /^(.*?)(\d+)$/.exec(connectionFromPort(c));
    if (match === null) {
      connections.push(c);
      continue;
    }
    const n = Number(match[2]);
    if (n === index) {
      continue;
    }
    connections.push(n > index ? { ...c, fromPort: `${match[1]}${n - 1}` } : c);
  }
  return { ...next, connections };
}

// ---- 出入口

export interface PortInfo {
  readonly id: string;
  /** 出口の名前（分岐の `label`）。1 本しかない出口は空 */
  readonly label: string;
}

export interface Ports {
  readonly inputs: readonly PortInfo[];
  readonly outputs: readonly PortInfo[];
}

/**
 * ノードの出入口。種類ごとの既定に、読んだ線が使っている綴りを足す（人が書いたフローが別の綴りを
 * 使っていても、線を落とさずに描くため）。
 */
export function portsOf(node: FlowNode, connections: readonly FlowConnection[]): Ports {
  const type = nodeType(node);
  const inputs: PortInfo[] = type === "start" ? [] : [{ id: INPUT_PORT, label: "" }];
  const outputs: PortInfo[] = [];
  const key = branchKey(type);
  const multi = type === "askUserQuestion" && nodeData(node).multiSelect === true;
  if (type === "end") {
    // 出口なし
  } else if (key !== undefined && !multi) {
    branchItems(node).forEach((item, index) => outputs.push({ id: branchPort(index), label: str(item.label) }));
  } else {
    outputs.push({ id: OUTPUT_PORT, label: "" });
  }
  for (const c of connections) {
    if (connectionFrom(c) === node.id && !outputs.some((p) => p.id === connectionFromPort(c))) {
      outputs.push({ id: connectionFromPort(c), label: connectionFromPort(c) });
    }
    if (connectionTo(c) === node.id && !inputs.some((p) => p.id === connectionToPort(c))) {
      inputs.push({ id: connectionToPort(c), label: "" });
    }
  }
  return { inputs, outputs };
}

/** 線に添える言葉。`condition` があればそれ、無ければ出口の名前（実行ファイルの案内と同じ読み方） */
/**
 * 線の言葉に使う値の綴り。実行ファイルの `flow._text` と同じ読み方にする。真偽値は空、数は整数ならその綴り
 * （`1.0` は `1`）、文字列はそのまま、ほかは空
 */
function labelText(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  return "";
}

export function connectionLabel(doc: FlowDoc, c: FlowConnection): string {
  const condition = labelText(c.condition);
  if (condition !== "") {
    return condition;
  }
  const from = doc.nodes.find((node) => node.id === connectionFrom(c));
  if (from === undefined) {
    return "";
  }
  // 出口が項目の id とちょうど同じか、`branch-<番号>` の番号が項目の位置。部分一致や末尾の数字だけでは当てない
  // （実行ファイルの案内 `flow._port_label` と同じ読み方。出口・id・言葉の値も `flow._text` と同じく、
  // 真偽値は空として読む）
  const port = labelText(c.fromPort);
  if (port === "") {
    return "";
  }
  const items = branchItems(from);
  const byId = items.find((item) => labelText(item.id) !== "" && labelText(item.id) === port);
  if (byId !== undefined) {
    return labelText(byId.label);
  }
  const match = /^branch-(\d{1,6})$/.exec(port);
  if (match === null) {
    return "";
  }
  const item = items[Number(match[1])];
  return item === undefined ? "" : labelText(item.label);
}

// ---- 入れ子の段と注意

export interface Nesting {
  /** 子の下に重なる段の数の最大。subAgent / subAgentFlow が 1 段 */
  readonly depth: number;
  /** サブフローが自分を（間接にでも）呼んでいる */
  readonly cyclic: boolean;
}

function subFlows(doc: FlowDoc): ReadonlyMap<string, readonly FlowNode[]> {
  const found = new Map<string, readonly FlowNode[]>();
  const raw = doc.subAgentFlows;
  if (!Array.isArray(raw)) {
    return found;
  }
  for (const flow of raw) {
    if (isRecord(flow) && typeof flow.id === "string" && Array.isArray(flow.nodes)) {
      found.set(flow.id, flow.nodes.filter((n): n is FlowNode => isRecord(n) && typeof n.id === "string"));
    }
  }
  return found;
}

/**
 * 子の下に何段の入れ子が重なるか。`subAgent` は 1 段。`subAgentFlow` は 1 段で、そのサブフローの中の
 * ノードがさらに重なる。子は メインの下 1 段目なので、既定の上限（メインの下 3 段）なら子の下は 2 段まで。
 */
export function nesting(doc: FlowDoc): Nesting {
  const flows = subFlows(doc);
  let cyclic = false;
  const walk = (nodes: readonly FlowNode[], seen: ReadonlySet<string>): number => {
    let max = 0;
    for (const node of nodes) {
      const type = nodeType(node);
      if (type === "subAgent") {
        max = Math.max(max, 1);
      } else if (type === "subAgentFlow") {
        const ref = dataText(node, "subAgentFlowId");
        if (seen.has(ref)) {
          cyclic = true;
          max = Math.max(max, 1);
          continue;
        }
        const inner = flows.get(ref);
        max = Math.max(max, 1 + (inner === undefined ? 0 : walk(inner, new Set([...seen, ref]))));
      }
    }
    return max;
  };
  const depth = walk(doc.nodes, new Set());
  return { depth, cyclic };
}

/**
 * 図の上に出す注意。**当てはまるときだけ出す。** 良し悪しは決めない（保存は止めない）。
 */
export function flowNotices(doc: FlowDoc): readonly string[] {
  const out: string[] = [];
  const { depth, cyclic } = nesting(doc);
  if (depth > NEST_ALLOWED) {
    out.push(
      `入れ子のサブエージェントが子の下に ${depth} 段重なる。既定の上限はメインの下 ${SPAWN_LIMIT} 段で、子（1 段目）の下は ${NEST_ALLOWED} 段まで。` +
        "上限に当たった段では Agent ツールが渡らず、そのノードで止まってメインへ戻る",
    );
  }
  if (cyclic) {
    out.push("サブフローが自分を呼んでいる（subAgentFlowId が巡っている）。段の数は数えきれない");
  }
  const starts = doc.nodes.filter((node) => nodeType(node) === "start").length;
  if (starts === 0) {
    out.push("開始（start）のノードが無い。担当のサブエージェントは先頭のノードから読む");
  } else if (starts > 1) {
    out.push(`開始（start）のノードが ${starts} つある。案内はどの開始からも辿って並べる`);
  }
  const unknown = [...new Set(doc.nodes.map(nodeType).filter((type) => !isEditableType(type)))];
  if (unknown.length > 0) {
    out.push(`この画面で欄を持たない種類がある（${unknown.map((t) => t || "(種類なし)").join(", ")}）。名前と位置だけ変えられ、中身は保存してもそのまま残る`);
  }
  const flows = subFlows(doc).size;
  if (flows > 0) {
    out.push(`サブフロー（subAgentFlows）が ${flows} 本ある。この画面では中身を描かない。保存してもそのまま残る`);
  }
  return out;
}
