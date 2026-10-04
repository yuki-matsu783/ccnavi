/**
 * 画面が持つフローの中身と、実行ファイルが読んだ中身（`--lint --json --flow` の `flow.data`）を見比べる。
 * フロー編集画面の、開くときと保存の前。
 *
 * 画面の読み手（`yaml`、YAML 1.2）と実行ファイルの読み手（PyYAML、YAML 1.1）は、同じ表記を別の値に読むことがある
 * （`0755` `yes` `1:30` `0o17` `1e3` `1_000` `1.` 日付 `!!float 1` `!!binary` マージキー など）。画面が自分の
 * 読みのまま書き直すと、ノードを動かして保存しただけで PyYAML での意味が変わる。**値の意味の答えは実行ファイルが
 * 持つ**ので、画面は自分の読みが実行ファイルの読みと同じときだけ開き、書く本文を実行ファイルが同じ中身に
 * 読むときだけ保存する。見比べ方は次のとおりに決め打ちする（迷うものは食い違いとして止める）。
 *
 * - 文字列・真偽値・null は同じ値
 * - 実行ファイルの整数（JSON の数）は、画面の整数（`Number.isInteger`）で同じ値
 * - 実行ファイルの浮動小数（`{"$ccnavi": "float", "value"}`）は、画面の整数でない数で同じ値。整数の値を持つ
 *   浮動小数（`1.0`）は、画面が整数として持つ（書けば `1` になる）ので食い違い。有限でないものも食い違い
 * - 並びは長さと各項目。キーと値の並びは、キーの組と各値（画面の側で値が `undefined` のキーは無いものとして読む）
 * - ほかのマーカー（範囲の外の整数・キーが文字列でない辞書・日付・バイト列・集合 など）は画面が同じ値を持てないので
 *   食い違い
 *
 * VS Code の API も node も使わない。
 */

/** 実行ファイル（`flow.as_json`）のマーカーの鍵 */
export const JSON_MARK = "$ccnavi";

export interface FlowMismatch {
  /** 食い違った場所。`ノード a の data.prompt`、`connections[0].condition` など */
  readonly where: string;
  /** 画面の読み */
  readonly screen: string;
  /** 実行ファイルの読み */
  readonly executable: string;
}

type Segment = string | number;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** 素のキーと値の並び（`Buffer` や `Date` のような作りの違う値は除く） */
function isPlain(value: unknown): value is Record<string, unknown> {
  if (!isRecord(value)) {
    return false;
  }
  const proto: unknown = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function isMark(value: unknown): value is Record<string, unknown> {
  return isRecord(value) && typeof value[JSON_MARK] === "string";
}

/** 画面の中身（`screen`）と実行ファイルが読んだ中身（`executable`）の最初の食い違い。同じなら undefined */
export function flowMismatch(screen: unknown, executable: unknown): FlowMismatch | undefined {
  const found = differ(screen, executable, []);
  if (found === undefined) {
    return undefined;
  }
  return { where: whereOf(found.path, executable), screen: found.screen, executable: found.executable };
}

interface Found {
  readonly path: readonly Segment[];
  readonly screen: string;
  readonly executable: string;
}

function differ(screen: unknown, exec: unknown, at: readonly Segment[]): Found | undefined {
  const here = (): Found => ({ path: at, screen: describeScreen(screen), executable: describeExecutable(exec) });
  if (exec === null || typeof exec === "string" || typeof exec === "boolean") {
    return screen === exec ? undefined : here();
  }
  if (typeof exec === "number") {
    return typeof screen === "number" && Number.isInteger(screen) && screen === exec ? undefined : here();
  }
  if (Array.isArray(exec)) {
    if (!Array.isArray(screen) || screen.length !== exec.length) {
      return here();
    }
    for (let i = 0; i < exec.length; i += 1) {
      const inner = differ(screen[i], exec[i], [...at, i]);
      if (inner !== undefined) {
        return inner;
      }
    }
    return undefined;
  }
  if (isMark(exec)) {
    if (exec[JSON_MARK] === "float" && typeof exec.value === "number" && Number.isFinite(exec.value) && !Number.isInteger(exec.value)) {
      return typeof screen === "number" && screen === exec.value ? undefined : here();
    }
    return here();
  }
  if (isRecord(exec)) {
    if (!isPlain(screen)) {
      return here();
    }
    const keys = Object.keys(screen).filter((key) => screen[key] !== undefined);
    for (const key of keys) {
      if (!Object.prototype.hasOwnProperty.call(exec, key)) {
        return { path: [...at, key], screen: describeScreen(screen[key]), executable: "なし" };
      }
    }
    for (const key of Object.keys(exec)) {
      if (!keys.includes(key)) {
        return { path: [...at, key], screen: "なし", executable: describeExecutable(exec[key]) };
      }
      const inner = differ(screen[key], exec[key], [...at, key]);
      if (inner !== undefined) {
        return inner;
      }
    }
    return undefined;
  }
  return here();
}

/** 場所の表記。`nodes` の中はノードの id で言う（実行ファイルの読みの id。無ければ並びの位置） */
function whereOf(at: readonly Segment[], executable: unknown): string {
  if (at.length === 0) {
    return "最上位";
  }
  if (at[0] === "nodes" && typeof at[1] === "number" && isRecord(executable) && Array.isArray(executable.nodes)) {
    const node = executable.nodes[at[1]];
    const id = isRecord(node) && typeof node.id === "string" ? node.id : undefined;
    if (id !== undefined) {
      const rest = pathText(at.slice(2));
      return rest === "" ? `ノード ${JSON.stringify(id)}` : `ノード ${JSON.stringify(id)} の ${rest}`;
    }
  }
  return pathText(at);
}

function pathText(at: readonly Segment[]): string {
  let out = "";
  for (const part of at) {
    if (typeof part === "number") {
      out += `[${part}]`;
    } else if (/^[A-Za-z_][A-Za-z0-9_-]*$/.test(part)) {
      out += out === "" ? part : `.${part}`;
    } else {
      out += `[${JSON.stringify(part)}]`;
    }
  }
  return out;
}

const SHOWN_LIMIT = 60;

function quoted(text: string): string {
  const json = JSON.stringify(text);
  return json.length > SHOWN_LIMIT ? `${json.slice(0, SHOWN_LIMIT)}…` : json;
}

/** 画面の値の言い方 */
export function describeScreen(value: unknown): string {
  if (value === undefined) {
    return "なし";
  }
  if (value === null) {
    return "null";
  }
  if (typeof value === "string") {
    return `文字列 ${quoted(value)}`;
  }
  if (typeof value === "boolean") {
    return `真偽値 ${value}`;
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? `整数 ${value}` : `数 ${value}`;
  }
  if (Array.isArray(value)) {
    return `リスト（${value.length} 件）`;
  }
  if (isPlain(value)) {
    return "マップ（キーと値の組）";
  }
  if (isRecord(value)) {
    return `${(value as object).constructor?.name ?? "値"}（JSON にできない値）`;
  }
  return typeof value;
}

/** 実行ファイルの値の言い方 */
export function describeExecutable(value: unknown): string {
  if (value === null) {
    return "null";
  }
  if (typeof value === "string") {
    return `文字列 ${quoted(value)}`;
  }
  if (typeof value === "boolean") {
    return `真偽値 ${value}`;
  }
  if (typeof value === "number") {
    return `整数 ${value}`;
  }
  if (Array.isArray(value)) {
    return `リスト（${value.length} 件）`;
  }
  if (isMark(value)) {
    const text = typeof value.text === "string" ? quoted(value.text).slice(1, -1) : "";
    switch (value[JSON_MARK]) {
      case "float":
        if (typeof value.value === "number") {
          return `浮動小数 ${Number.isInteger(value.value) ? value.value.toFixed(1) : String(value.value)}`;
        }
        return `浮動小数 ${text}`;
      case "int":
        return `整数 ${text}（画面の数値では正確に表せません）`;
      case "map":
        return "キーが文字列でないマップ";
      case "date":
        return `日付 ${text}`;
      case "datetime":
        return `日時 ${text}`;
      case "bytes":
        return "バイト列（!!binary）";
      case "set":
        return "集合（!!set）";
      case "tuple":
        return "組（!!omap / !!pairs）";
      default:
        return `${text || "値"}（JSON にできない値）`;
    }
  }
  if (isRecord(value)) {
    return "マップ（キーと値の組）";
  }
  return typeof value;
}

/** 開くときに食い違ったときの文面 */
export function openMismatchText(found: FlowMismatch): string {
  return (
    `画面と実行ファイルとで、値の読み方が食い違っています（${found.where}。画面: ${found.screen}、実行ファイル: ${found.executable}）。` +
    "このまま画面で直して保存すると、値の意味が変わります。エディタで引用符を付けるなどして、" +
    "実行ファイルが意図どおりの値として読むように直してください"
  );
}

/** 保存の前に食い違ったときの文面 */
export function saveMismatchText(found: FlowMismatch): string {
  return (
    `書き出す本文を、実行ファイルが画面とは違う値として読みます（${found.where}。画面: ${found.screen}、実行ファイル: ${found.executable}）。` +
    "保存すると値の意味が変わるため、書き込みません"
  );
}
