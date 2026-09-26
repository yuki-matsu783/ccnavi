/**
 * 子チケットのフロー（設計 9.3.1、ADR-0085）の読み書き。フロー編集画面と拡張ホストが分け合う。
 *
 * ファイルは YAML の 1 文書（既定 `.ccnavi/approved/flows/<子>.yml`）。形は実行ファイル（`ccnavi/flow.py`）が読むもの。
 *
 *     id, name, description?, version
 *     nodes:          [node, ...]
 *     connections:    [connection, ...]
 *     subAgentFlows?: [{id, name, nodes, connections}, ...]
 *     node       = {id, type, name, position: {x, y}, data: {...}, parentId?}
 *     group      = {id, type: "group", name, position: {x, y}, data: {label?}, style: {width, height}}
 *     connection = {id, from, to, fromPort, toPort, condition?}
 *
 * グループ（`type: "group"`）は図の上の囲みで、手順ではない。出入口を持たず、線は繋がない。
 * 中のノードは `parentId` にグループの id を持ち、`position` は**グループの左上からの位置**
 * （React Flow の決まり）。グループは中のノードより前に並べる（React Flow は親を先に読む）。
 * グループの中にグループは置かない。
 *
 * **知らない欄も知らない種類も落とさない。** 読んだ中身をそのまま持ち、編集はその写しの
 * 触ったところだけを差し替える（`phases-doc.ts` が YAML の知らない欄を残すのと同じ考え）。
 * 欠けた欄（`position` や `data`）も、読むときに既定で補うだけで、触るまで書き足さない。
 * 書き出しは中身から組み直す（コメントや書き方は残らない。人が保存したときだけ書く）。
 *
 * **判定はしない。** 読めるか・形が正しいかは実行ファイルが `--lint --flow` で言い（`flow-lint.ts`、ADR-0035）、
 * 着手中に書けるかは実行ファイルが `flow.locked` で言う（ADR-0085）。
 * 入れ子の段の数（`nesting`）は案内で、止めるのは実行ファイルでも画面でもなく、上限に当たった
 * サブエージェントに Agent ツールが渡らないこと（そのノードで止まってメインへ戻る）。
 *
 * ここには VS Code の API も node も DOM も入れない。画面（React）が束ねて読むため。
 */
import { Document, parseDocument, Scalar, visit } from "yaml";

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
 * YAML の本文を、画面が描くために読む。**正しいかは決めない。** 読めるか（大きさ・YAML として読めるか・別名）と
 * 形（`nodes` が無い、`id` が無い・重なる など）の答えは実行ファイル（`--lint --flow`、`flow-lint.ts`）が出し、
 * 画面はそれを通ったものだけを開く（ADR-0035）。読み手はルール設定の画面（`rules-doc.ts`）と同じ `yaml` の既定。
 *
 * ここが断るのは、画面が描けないときだけ。拡張の読み手が読めない（実行ファイルとは読み手が違うので、
 * 実行ファイルが読めても `yaml` が断ることがある。重なったキーなど）か、ノードの並び（`id` が文字列の
 * キーと値の並び）が取れないとき。**例外は外に出さない。**
 */
export function parseFlow(text: string): FlowRead {
  const read = parseFlowValue(text);
  if (!read.ok) {
    return read;
  }
  const doc = asFlowDoc(read.value);
  return doc === undefined ? { ok: false, error: "ノードの並び（id が文字列のノード）が取れないので描けない" } : { ok: true, doc };
}

/**
 * YAML の本文を `yaml` の既定で読んだ中身（形は確かめない）。実行ファイルが読んだ中身と見比べるのに使う
 * （`flow-agree.ts`。描けるかより先に見比べるので、マージキーのような読みの違いも「食い違い」として言える）。
 * 先頭の BOM は 1 つ外す（実行ファイルの `utf-8-sig` と同じ）。**例外は外に出さない。**
 */
export function parseFlowValue(text: string): { readonly ok: true; readonly value: unknown } | { readonly ok: false; readonly error: string } {
  try {
    const doc = parseDocument(text.replace(/^\uFEFF/, ""));
    const problem = doc.errors[0];
    if (problem !== undefined) {
      return { ok: false, error: `画面の YAML の読み手で読めないので描けない（${firstLine(problem.message)}）` };
    }
    return { ok: true, value: doc.toJS() };
  } catch (error) {
    return { ok: false, error: `画面の YAML の読み手で読めないので描けない（${firstLine(error instanceof Error ? error.message : String(error))}）` };
  }
}

function firstLine(text: string): string {
  return text.split("\n")[0].replace(/:$/, "").trim();
}

/**
 * 描ける形か。最上位がキーと値の並びで、`nodes` が「文字列の `id` を持つキーと値の並び」の並び。
 * 画面から届いた保存の中身もここで受ける（崩れていたら書かない。正しいかは保存の前に実行ファイルが言う）。
 */
export function asFlowDoc(raw: unknown): FlowDoc | undefined {
  if (!isRecord(raw) || !Array.isArray(raw.nodes)) {
    return undefined;
  }
  if (!raw.nodes.every((node) => isRecord(node) && typeof node.id === "string")) {
    return undefined;
  }
  return raw as FlowDoc;
}

/**
 * 書き出す本文。字下げ 2 のブロック形式で、長い行を折らない。複数行の文は `|` の形で書く。
 * 同じ中身が 2 度出ても別名（`&` / `*`）にしない（実行ファイルは別名を読まない）。
 * 実行ファイル（PyYAML、YAML 1.1）が文字以外に読む綴り（`yes` `0755` `2026-01-01` など）と、
 * 裸や `|` では PyYAML が読めない・別の文字に読む文字列（`needsDoubleQuotes`）は二重引用符で囲む。
 * `y` `n` は PyYAML が文字として読むので囲まない（`position` の `y` をそのまま書く）。
 *
 * 書いたものが実行ファイルに同じ中身で読まれるかは、保存の前に実行ファイルに読ませて見比べる
 * （`flow-agree.ts`）。ここの囲み方はその見比べで止まらずに書くためのもの。
 */
export function serializeFlow(doc: FlowDoc): string {
  return yamlText(doc);
}

/** 値を書き出しと同じ形の YAML にする。画面が欄を持たない種類の `data` を見せるのにも使う */
export function yamlText(value: unknown): string {
  const out = new Document(value, { aliasDuplicateObjects: false });
  visit(out, {
    Scalar(_key, node) {
      if (typeof node.value !== "string") {
        return;
      }
      if ((yaml11Ambiguous(node.value) && !/^[yYnN]$/.test(node.value)) || needsDoubleQuotes(node.value)) {
        node.type = Scalar.QUOTE_DOUBLE;
      }
    },
  });
  // 二重引用符の中は JSON と同じ書き方にする（`yaml` の折り返しや独自のエスケープを使わない。PyYAML は JSON の
  // エスケープを全部読む）。そのうえで JSON が裸のまま残す文字を、PyYAML が読めるエスケープに直す
  return escapeForPyYaml(out.toString({ lineWidth: 0, doubleQuotedAsJSON: true }));
}

/**
 * PyYAML が本文に裸では置けない文字（読み手が断る）。C0 の制御文字（タブ・改行・復帰を除く）、DEL、
 * C1 の制御文字（U+0085 を除く）、U+FFFE・U+FFFF、対になっていないサロゲート
 */
const PYYAML_UNPRINTABLE = /[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x84\x86-\x9F\uFFFE\uFFFF]|[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;
/** PyYAML（YAML 1.1）が改行として読む文字（二重引用符の中でも空白に畳まれる）と、文書の頭で読み飛ばす BOM */
const PYYAML_BREAKS = /[\u0085\u2028\u2029\uFEFF]/;

/**
 * 裸でも `|` でも正しく書けない文字列。二重引用符（とエスケープ）で書く。
 *
 * - タブ・復帰・U+0085・U+2028・U+2029・U+FEFF・PyYAML が裸では読めない文字を含む（`a\tb` を裸で書くと PyYAML が
 *   読めない。U+0085 などは PyYAML が改行として読む。本文の頭の U+FEFF は BOM として読み飛ばされる）
 * - 複数行で、1 行目の頭が空白（1 行目が空白だけの `|+` は PyYAML が中身を取り違える。字下げの指示に頼らない）か、
 *   どこかの行の末尾が空白（空白だけの行を含む。行末の空白の扱いを読み手に任せない）。2 行目から先の頭の空白は
 *   `|` のままで読める
 */
export function needsDoubleQuotes(text: string): boolean {
  if (/[\t\r]/.test(text) || PYYAML_BREAKS.test(text) || PYYAML_UNPRINTABLE.test(text)) {
    return true;
  }
  if (!text.includes("\n")) {
    return false;
  }
  return /^[ \t]/.test(text) || text.split("\n").some((line) => /[ \t]$/.test(line));
}

const BREAK_ESCAPES: Readonly<Record<string, string>> = { "\u0085": "\\N", "\u2028": "\\L", "\u2029": "\\P" };

/**
 * JSON の書き方が裸のまま残す文字を、PyYAML の二重引用符のエスケープに直す。こうした文字を含む文字列は
 * どれも二重引用符で書いてある（`needsDoubleQuotes`）ので、本文に裸で出るのは二重引用符の中だけ
 */
function escapeForPyYaml(text: string): string {
  return text.replace(/[\u0085\u2028\u2029\x7F-\x84\x86-\x9F\uFEFF\uFFFE\uFFFF]/g, (ch) => {
    const named = BREAK_ESCAPES[ch];
    if (named !== undefined) {
      return named;
    }
    const code = ch.charCodeAt(0);
    return code <= 0xff ? `\\x${code.toString(16).padStart(2, "0")}` : `\\u${code.toString(16).padStart(4, "0")}`;
  });
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

/**
 * 使われていない id。`<種類>-<番号>`。どこかのノードの `parentId` が指している id も使っているとみなす
 * （指す先の無い `parentId` を持つノードが、新しく作ったグループに黙って入らないように）
 */
export function freshNodeId(doc: FlowDoc, type: string): string {
  const used = new Set(doc.nodes.map((node) => node.id));
  for (const node of doc.nodes) {
    if (typeof node.parentId === "string") {
      used.add(node.parentId);
    }
  }
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

/**
 * ノードを消す。そのノードに出入りする線も消す。
 * グループを消すときは、中のノードは消さずに外へ出す（`ungroup` と同じ。位置は図の上で動かない）。
 */
export function removeNode(doc: FlowDoc, id: string): FlowDoc {
  const released = releaseMembers(doc, id);
  const next: Record<string, unknown> = { ...released, nodes: released.nodes.filter((node) => node.id !== id) };
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
  // グループは囲みで手順ではない。線は繋がない
  if (doc.nodes.some((node) => (node.id === from || node.id === to) && isGroup(node))) {
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

// ---- グループ（図の上の囲み。手順ではない）

export const GROUP_TYPE = "group";
/** 大きさの分からないノードの見積もり。図のノードの幅（`Canvas.css` の `.react-flow__node-step`）とふつうの高さ */
export const NODE_SIZE: FlowSize = { width: 190, height: 90 };
/** 大きさを書いていないグループの大きさ */
export const GROUP_SIZE: FlowSize = { width: 320, height: 200 };
/** グループの最小の大きさ（図で縮めるときの下限） */
export const GROUP_MIN: FlowSize = { width: 120, height: 80 };
/** 囲むときの余白と、上に名前を書く帯の高さ */
const GROUP_PAD = 24;
const GROUP_HEAD = 28;

export interface FlowSize {
  readonly width: number;
  readonly height: number;
}

export function isGroup(node: FlowNode): boolean {
  return nodeType(node) === GROUP_TYPE;
}

function positiveNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : undefined;
}

/** ノードの大きさ。`style` の `width` / `height` があればそれ、無ければ見積もり */
export function nodeSize(node: FlowNode): FlowSize {
  const style = isRecord(node.style) ? node.style : {};
  const fallback = isGroup(node) ? GROUP_SIZE : NODE_SIZE;
  return { width: positiveNumber(style.width) ?? fallback.width, height: positiveNumber(style.height) ?? fallback.height };
}

/**
 * ノードが入っているグループ。`parentId` が在るグループを指しているときだけ。グループ自身は
 * どこにも入らない（グループの中にグループは置かない）。指す先が無い・グループでない `parentId` は
 * 読むだけで効かせない（図でも親にしない。書き換えもしない）
 */
export function groupOf(doc: FlowDoc, node: FlowNode): FlowNode | undefined {
  if (isGroup(node) || typeof node.parentId !== "string" || node.parentId === node.id) {
    return undefined;
  }
  const parent = doc.nodes.find((n) => n.id === node.parentId);
  return parent !== undefined && isGroup(parent) ? parent : undefined;
}

function positionIn(doc: FlowDoc, node: FlowNode): FlowPoint {
  return nodePosition(node, doc.nodes.indexOf(node));
}

/** 図の上の位置（グループの中のノードは、グループの位置を足す）。無いノードは原点 */
export function absolutePosition(doc: FlowDoc, id: string): FlowPoint {
  const node = doc.nodes.find((n) => n.id === id);
  if (node === undefined) {
    return { x: 0, y: 0 };
  }
  const own = positionIn(doc, node);
  const group = groupOf(doc, node);
  if (group === undefined) {
    return own;
  }
  const base = positionIn(doc, group);
  return { x: base.x + own.x, y: base.y + own.y };
}

function withPosition(node: FlowNode, at: FlowPoint): FlowNode {
  const now = isRecord(node.position) ? node.position : {};
  return { ...node, position: { ...now, x: Math.round(at.x), y: Math.round(at.y) } };
}

function withoutParent(node: FlowNode): FlowNode {
  const { parentId: _dropped, ...rest } = node;
  void _dropped;
  return rest as FlowNode;
}

/** グループの中のノードを外へ出す（図の上の位置は変えない）。グループでなければそのまま */
function releaseMembers(doc: FlowDoc, groupId: string): FlowDoc {
  const group = doc.nodes.find((n) => n.id === groupId);
  if (group === undefined || !isGroup(group)) {
    return doc;
  }
  return {
    ...doc,
    nodes: doc.nodes.map((node) => (groupOf(doc, node)?.id === groupId ? withoutParent(withPosition(node, absolutePosition(doc, node.id))) : node)),
  };
}

/** `id` のノードを `before` のノードの直前へ移す（親は子より前に並べる） */
function moveBefore(nodes: readonly FlowNode[], id: string, before: string): FlowNode[] {
  const moving = nodes.find((n) => n.id === id);
  const rest = nodes.filter((n) => n.id !== id);
  const at = rest.findIndex((n) => n.id === before);
  if (moving === undefined || at < 0) {
    return [...nodes];
  }
  return [...rest.slice(0, at), moving, ...rest.slice(at)];
}

/**
 * 選んだノードを新しいグループで囲む。グループの大きさは、囲むノードの外枠に余白を足したもの
 * （大きさの分からないノードは `NODE_SIZE` で見積もる）。
 *
 * グループ自身は囲まない（入れ子にしない）。別のグループに入っているノードは、そのグループから
 * 抜けて新しいグループに移る（元のグループは空でも残す）。囲めるノードが 1 つも無ければ undefined。
 * 新しいグループは、囲んだノードのうち最も前のものの位置に並べる（親を子より前に置くため）。
 */
export function groupNodes(doc: FlowDoc, ids: readonly string[]): { readonly doc: FlowDoc; readonly id: string } | undefined {
  const wanted = new Set(ids);
  const picked = doc.nodes.filter((node) => wanted.has(node.id) && !isGroup(node));
  if (picked.length === 0) {
    return undefined;
  }
  const spots = new Map(picked.map((node) => [node.id, absolutePosition(doc, node.id)]));
  let left = Infinity;
  let top = Infinity;
  let right = -Infinity;
  let bottom = -Infinity;
  for (const node of picked) {
    const at = spots.get(node.id) as FlowPoint;
    const size = nodeSize(node);
    left = Math.min(left, at.x);
    top = Math.min(top, at.y);
    right = Math.max(right, at.x + size.width);
    bottom = Math.max(bottom, at.y + size.height);
  }
  const origin = { x: Math.round(left - GROUP_PAD), y: Math.round(top - GROUP_PAD - GROUP_HEAD) };
  const id = freshNodeId(doc, GROUP_TYPE);
  const group: FlowNode = {
    id,
    type: GROUP_TYPE,
    name: TYPE_LABELS[GROUP_TYPE] ?? GROUP_TYPE,
    position: origin,
    data: {},
    style: { width: Math.round(right - left + GROUP_PAD * 2), height: Math.round(bottom - top + GROUP_PAD * 2 + GROUP_HEAD) },
  };
  const nodes: FlowNode[] = [];
  let placed = false;
  for (const node of doc.nodes) {
    const at = spots.get(node.id);
    if (at === undefined) {
      nodes.push(node);
      continue;
    }
    if (!placed) {
      nodes.push(group);
      placed = true;
    }
    nodes.push({ ...withPosition(node, { x: at.x - origin.x, y: at.y - origin.y }), parentId: id });
  }
  return { doc: { ...doc, nodes }, id };
}

/** グループを解く。中のノードは図の上の同じ位置のまま外へ出し、グループは消す。グループでなければそのまま */
export function ungroup(doc: FlowDoc, groupId: string): FlowDoc {
  const group = doc.nodes.find((n) => n.id === groupId);
  return group === undefined || !isGroup(group) ? doc : removeNode(doc, groupId);
}

/**
 * グループの大きさを変える。左や上の辺を動かしたときは位置（`position`、図の上の位置）も渡す。
 * そのときは中のノードの位置（グループからの位置）をずれた分だけ戻し、図の上では動かさない
 * （React Flow も縮めている間、中のノードをその場に留める）。
 */
export function resizeGroup(doc: FlowDoc, id: string, size: FlowSize, position?: FlowPoint): FlowDoc {
  const group = doc.nodes.find((n) => n.id === id);
  if (group === undefined || !isGroup(group)) {
    return doc;
  }
  const now = positionIn(doc, group);
  const dx = position === undefined ? 0 : Math.round(position.x) - Math.round(now.x);
  const dy = position === undefined ? 0 : Math.round(position.y) - Math.round(now.y);
  const style = isRecord(group.style) ? group.style : {};
  const width = Math.round(Math.max(GROUP_MIN.width, size.width));
  const height = Math.round(Math.max(GROUP_MIN.height, size.height));
  if (dx === 0 && dy === 0 && style.width === width && style.height === height) {
    // 縁を押しただけ。未保存にしない
    return doc;
  }
  return {
    ...doc,
    nodes: doc.nodes.map((node) => {
      if (node.id === id) {
        const resized = { ...node, style: { ...style, width, height } };
        return dx === 0 && dy === 0 ? resized : withPosition(resized, { x: now.x + dx, y: now.y + dy });
      }
      if ((dx !== 0 || dy !== 0) && groupOf(doc, node)?.id === id) {
        const at = positionIn(doc, node);
        return withPosition(node, { x: at.x - dx, y: at.y - dy });
      }
      return node;
    }),
  };
}

/**
 * ノードを図の上の位置 `absolute` に置く（ドラッグを放したとき）。
 *
 * - グループはそのまま動く（中のノードは位置がグループからなので、一緒に動く）
 * - ほかのノードは、真ん中がグループの枠の中に落ちればそのグループに入り、どの枠にも落ちなければ
 *   グループから出る。枠が重なっていれば、後ろに並ぶ（図で上に描かれる）グループに入る
 *
 * 位置もグループも変わらなければ、同じ写しをそのまま返す（押しただけで未保存にしない）。
 */
export function placeNode(doc: FlowDoc, id: string, absolute: FlowPoint): FlowDoc {
  const index = doc.nodes.findIndex((n) => n.id === id);
  if (index < 0) {
    return doc;
  }
  const node = doc.nodes[index];
  const at = { x: Math.round(absolute.x), y: Math.round(absolute.y) };
  const now = nodePosition(node, index);
  if (isGroup(node)) {
    return Math.round(now.x) === at.x && Math.round(now.y) === at.y ? doc : moveNode(doc, id, at);
  }
  const size = nodeSize(node);
  const cx = at.x + size.width / 2;
  const cy = at.y + size.height / 2;
  const host = [...doc.nodes].reverse().find((g) => {
    if (!isGroup(g) || g.id === id) {
      return false;
    }
    const base = positionIn(doc, g);
    const box = nodeSize(g);
    return cx >= base.x && cx <= base.x + box.width && cy >= base.y && cy <= base.y + box.height;
  });
  const current = groupOf(doc, node);
  const base = host === undefined ? { x: 0, y: 0 } : positionIn(doc, host);
  const rel = { x: at.x - base.x, y: at.y - base.y };
  if (host?.id === current?.id && Math.round(now.x) === rel.x && Math.round(now.y) === rel.y) {
    return doc;
  }
  let changed = withPosition(node, rel);
  if (host !== undefined) {
    changed = { ...changed, parentId: host.id };
  } else if (current !== undefined) {
    changed = withoutParent(changed);
  }
  const nodes = doc.nodes.map((n) => (n.id === id ? changed : n));
  if (host !== undefined && doc.nodes.indexOf(host) > index) {
    return { ...doc, nodes: moveBefore(nodes, host.id, id) };
  }
  return { ...doc, nodes };
}

/**
 * ドラッグで動いた点をまとめて置く。位置は React Flow の決まり（グループの中のノードはグループからの位置）で、
 * 写しの今のグループに対して読む。グループを先に置き、そのあとほかのノードを置く（一緒に動いた
 * グループの新しい位置から読むため）。何も変わらなければ同じ写しを返す。
 */
export function placeNodes(doc: FlowDoc, moves: readonly { readonly id: string; readonly position: FlowPoint }[]): FlowDoc {
  const typeOf = new Map(doc.nodes.map((node) => [node.id, isGroup(node)]));
  const ordered = [...moves.filter((m) => typeOf.get(m.id) === true), ...moves.filter((m) => typeOf.get(m.id) === false)];
  let next = doc;
  for (const move of ordered) {
    const node = next.nodes.find((n) => n.id === move.id);
    if (node === undefined) {
      continue;
    }
    const group = groupOf(next, node);
    const base = group === undefined ? { x: 0, y: 0 } : positionIn(next, group);
    next = placeNode(next, move.id, { x: base.x + move.position.x, y: base.y + move.position.y });
  }
  return next;
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
  if (type === GROUP_TYPE) {
    // グループは出入口を持たない（線を繋がない）
    return { inputs: [], outputs: [] };
  }
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
  const unknown = [...new Set(doc.nodes.map(nodeType).filter((type) => !isEditableType(type) && type !== GROUP_TYPE))];
  if (unknown.length > 0) {
    out.push(`この画面で欄を持たない種類がある（${unknown.map((t) => t || "(種類なし)").join(", ")}）。名前と位置だけ変えられ、中身は保存してもそのまま残る`);
  }
  const flows = subFlows(doc).size;
  if (flows > 0) {
    out.push(`サブフロー（subAgentFlows）が ${flows} 本ある。この画面では中身を描かない。保存してもそのまま残る`);
  }
  return out;
}
