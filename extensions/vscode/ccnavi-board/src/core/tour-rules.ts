/**
 * ルール管理画面の案内の見本（`webview/tours/rules.yml` の `sample:`）を、画面が描く形にする。
 *
 * ルールは `rules.yml` と同じ書き方（`glob:` / `regex:` / `when: {all: […]}` / `except:`）で見本に書き、ここで `RuleForm` にする。
 * 判定・期待する判定・候補は、実行ファイルの JSON と同じ形で書き、`testmodel.ts` / `suggestmodel.ts` の読み取りを通す
 * （欠けた項目は既定値で埋まる）。いずれも案内の間に描くだけで、保存にも判定にも使わない。
 * `rules-doc.ts` は YAML の解析器を持つので画面から読めない。同じ読み方をここで小さく持ち、そろっていることはテストが見る。
 */
import type { HookEntry } from "./hooks.js";
import { BOARD_VERSION, parseBoardJson } from "./model.js";
import type { CondNode } from "./rules-when.js";
import { SECTIONS, type RuleForm, type Section, type Sections } from "./rules-view.js";
import { rulesSums, type RulesSum } from "./sums.js";
import { parseSuggestJson, type SuggestJson } from "./suggestmodel.js";
import { parseExpectationsJson, parseTestJson, type ExpectationsJson, type TestJson } from "./testmodel.js";

export interface RulesSample {
  readonly sections: Sections;
  readonly hooks: readonly HookEntry[];
  /** 名前（手順の scene の `judge`）で引く判定の結果 */
  readonly judges: Readonly<Record<string, TestJson>>;
  readonly checked: ExpectationsJson | undefined;
  readonly suggested: SuggestJson | undefined;
  /** 「統合結果」の見本。実行ファイルが出す結果（`sums[]`）と同じ形で書き、ボードの JSON の読み取りを通す */
  readonly sums: readonly RulesSum[];
  /** 保存前の検証エラーの見本（`--lint` の報告を画面が付けた 1 文）。無ければ空 */
  readonly saveError: string;
}

function object(value: unknown, at: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${at} がオブジェクトではない`);
  }
  return value as Record<string, unknown>;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : value === undefined || value === null ? "" : String(value);
}

/** `rules.yml` の書き方の組み合わせ条件（`{all: […]}` / `{any: […]}` / `{not: …}` / `{glob: …}` / `{regex: …}`）を読む */
export function condOfRaw(raw: unknown, at = "when"): CondNode {
  const map = object(raw, at);
  const keys = Object.keys(map);
  if (keys.length !== 1) {
    throw new Error(`${at} のキーはちょうど 1 つでなければならない`);
  }
  const key = keys[0];
  const value = map[key];
  switch (key) {
    case "glob":
    case "regex":
      return { op: key, pattern: str(value) };
    case "all":
    case "any":
      if (!Array.isArray(value)) {
        throw new Error(`${at}.${key} はリスト`);
      }
      return { op: key, children: value.map((item, i) => condOfRaw(item, `${at}.${key}[${i}]`)) };
    case "not":
      return { op: "not", child: condOfRaw(value, `${at}.not`) };
    default:
      throw new Error(`${at} のキー「${key}」は未知のキーです`);
  }
}

function ruleOfRaw(raw: unknown, section: Section, index: number): RuleForm {
  const at = `${section}[${index}]`;
  const r = object(raw, at);
  // glob と regex を両方書いたときは glob（実際の読み取りと同じ）
  const kind = r.glob === undefined && r.regex !== undefined ? "regex" : "glob";
  const when = r.when === undefined ? undefined : condOfRaw(r.when, `${at}.when`);
  const exceptNodes = Array.isArray(r.except) ? r.except.map((item, i) => condOfRaw(item, `${at}.except[${i}]`)) : undefined;
  return {
    origin: { section, index },
    id: str(r.id),
    match: str(r.match),
    kind,
    pattern: when === undefined ? str(r[kind]) : "",
    message: str(r.message),
    additionalContext: str(r.additionalContext),
    additionalContextOnce: str(r.additionalContextOnce),
    additionalContextFile: str(r.additionalContextFile),
    additionalContextOnceFile: str(r.additionalContextOnceFile),
    every: str(r.every),
    ...(exceptNodes === undefined
      ? {}
      : { excepts: exceptNodes.map((node) => (node.op === "glob" || node.op === "regex" ? `${node.op}: ${node.pattern}` : "")), exceptNodes }),
    ...(when === undefined ? {} : { when }),
  };
}

function viaJson<T>(parse: (text: string) => { readonly ok: true; readonly value: T } | { readonly ok: false; readonly error: string }, raw: unknown, at: string): T {
  const parsed = parse(JSON.stringify(raw));
  if (!parsed.ok) {
    throw new Error(`${at}: ${parsed.error}`);
  }
  return parsed.value;
}

/** 統合結果は実行ファイルの JSON（ボードの `sums[]`）と同じ形で書き、実際の読み取り（`parseBoardJson` → `rulesSums`）を通す */
function sumsOfRaw(raw: unknown): readonly RulesSum[] {
  if (raw === undefined) {
    return [];
  }
  if (!Array.isArray(raw)) {
    throw new Error("sample.sums はリスト");
  }
  const parsed = parseBoardJson(JSON.stringify({ version: BOARD_VERSION, sums: raw }));
  if (!parsed.ok) {
    throw new Error(`sample.sums: ${parsed.error}`);
  }
  return rulesSums(parsed.board);
}

export function parseRulesSample(raw: unknown): RulesSample {
  const root = object(raw, "sample");
  const rules = object(root.rules, "sample.rules");
  const sections = {} as Record<Section, RuleForm[]>;
  for (const section of SECTIONS) {
    const list = rules[section] ?? [];
    if (!Array.isArray(list)) {
      throw new Error(`sample.rules.${section} はリスト`);
    }
    sections[section] = list.map((item, index) => ruleOfRaw(item, section, index));
  }
  const hooks: HookEntry[] = (Array.isArray(root.hooks) ? root.hooks : []).map((item, i) => {
    const h = object(item, `sample.hooks[${i}]`);
    return {
      source: h.source === "settings-local" ? "settings-local" : "settings",
      event: str(h.event),
      matcher: str(h.matcher),
      command: str(h.command),
      timeout: typeof h.timeout === "number" ? h.timeout : null,
    } as HookEntry;
  });
  const judges: Record<string, TestJson> = {};
  for (const [name, value] of Object.entries(root.judges === undefined ? {} : object(root.judges, "sample.judges"))) {
    judges[name] = viaJson(parseTestJson, { version: 1, known: true, ...object(value, `sample.judges.${name}`) }, `sample.judges.${name}`);
  }
  return {
    sections,
    hooks,
    judges,
    checked: root.checked === undefined ? undefined : viaJson(parseExpectationsJson, { version: 1, ...object(root.checked, "sample.checked") }, "sample.checked"),
    sums: sumsOfRaw(root.sums),
    saveError: str(root.save_error),
    suggested: root.suggested === undefined ? undefined : viaJson(parseSuggestJson, { version: 1, ...object(root.suggested, "sample.suggested") }, "sample.suggested"),
  };
}
