/**
 * GitLab の応答の「形」を作り、比べる。`tests/contract/shape.py` を同じ名前・同じ規則で写したもの。
 * 正本は Python。規則を変えたいときは Python 側を直し、`normalize-cases.json` を直してからここを合わせる。
 * 仕様は `docs/42_claude/gitlab-contract.md`。
 */
import fs from "node:fs";
import path from "node:path";

import { REPO } from "./python.js";

export const DATA = path.join(REPO, "tests", "contract", "gitlab");

export interface Shape {
  t: string[];
  keys?: Record<string, Shape>;
  opt?: string[];
  items?: Shape;
}
export type Diff = [key: string, kind: string];
export interface Recorded {
  status: number;
  shape: Shape;
}
export interface Entry {
  request?: string;
  key?: string;
  kind?: string;
  reason?: string;
}
export interface Req {
  name: string;
  method: string;
  path: string;
  query?: Record<string, string>;
  body?: unknown;
  token: string;
  used_by: string[];
  expect_status: number;
  reads?: string[];
  save?: Record<string, string>;
}

export const MAX_DEPTH = 5;
export const DROPPED_KEYS = new Set(["avatar_url", "web_url", "username", "email", "public_email", "commit_email", "token"]);
// Python の `\d` は Unicode の数字にも、`$` は末尾の改行の前にも当たる。こちらは ASCII だけで厳格（既知の差）
const DATETIME = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)$/;
export const KINDS = ["missing", "extra", "type", "presence", "status"];

export function loadData<T = any>(name: string): T {
  return JSON.parse(fs.readFileSync(path.join(DATA, name), "utf8")) as T;
}

export function typeOf(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "boolean") return "boolean";
  // `1.0` は JSON.parse で整数になる（Python では number。既知の差）
  if (typeof value === "number") return Number.isInteger(value) ? "integer" : "number";
  if (typeof value === "string") return DATETIME.test(value) ? "datetime" : "string";
  if (Array.isArray(value)) return "array";
  if (typeof value === "object") return "object";
  throw new TypeError(`JSON の値ではない: ${typeof value}`);
}

export function normalize(value: unknown, depth = 0): Shape {
  const kind = typeOf(value);
  const node: Shape = { t: [kind] };
  if (depth >= MAX_DEPTH) return node;
  if (kind === "object") {
    node.keys = {};
    for (const k of Object.keys(value as object).sort()) {
      if (!DROPPED_KEYS.has(k)) node.keys[k] = normalize((value as Record<string, unknown>)[k], depth + 1);
    }
  } else if (kind === "array") {
    let items: Shape | undefined;
    for (const element of value as unknown[]) {
      const s = normalize(element, depth + 1);
      items = items === undefined ? s : merge(items, s);
    }
    if (items !== undefined) node.items = items;
  }
  return node;
}

export function merge(a: Shape, b: Shape): Shape {
  const out: Shape = { t: [...new Set([...a.t, ...b.t])].sort() };
  if (a.keys || b.keys) {
    const ka = a.keys ?? {};
    const kb = b.keys ?? {};
    const opt = new Set([...(a.opt ?? []), ...(b.opt ?? [])]);
    if (a.keys && b.keys) {
      for (const k of new Set([...Object.keys(ka), ...Object.keys(kb)])) if (k in ka !== k in kb) opt.add(k);
    }
    out.keys = {};
    for (const k of [...new Set([...Object.keys(ka), ...Object.keys(kb)])].sort()) {
      out.keys[k] = k in ka && k in kb ? merge(ka[k], kb[k]) : (ka[k] ?? kb[k]);
    }
    if (opt.size > 0) out.opt = [...opt].sort();
  }
  if (a.items || b.items) {
    out.items = a.items && b.items ? merge(a.items, b.items) : (a.items ?? b.items);
  }
  return out;
}

export function compare(expected: Shape, actual: Shape, path = ""): Diff[] {
  const here = path || "$";
  if (expected.t.join() !== actual.t.join()) return [[here, "type"]];
  const diffs: Diff[] = [];
  if (expected.keys && actual.keys) {
    const ek = expected.keys;
    const ak = actual.keys;
    for (const k of [...new Set([...Object.keys(ek), ...Object.keys(ak)])].sort()) {
      const sub = path ? `${path}.${k}` : k;
      if (!(k in ak)) diffs.push([sub, "missing"]);
      else if (!(k in ek)) diffs.push([sub, "extra"]);
      else {
        if ((expected.opt ?? []).includes(k) !== (actual.opt ?? []).includes(k)) diffs.push([sub, "presence"]);
        diffs.push(...compare(ek[k], ak[k], sub));
      }
    }
  }
  // 片方が空の配列（items が無い）なら、比べる材料が無い
  if (expected.items && actual.items) diffs.push(...compare(expected.items, actual.items, `${path}[]`));
  return diffs;
}

export function keyPaths(node: Shape, path = ""): Set<string> {
  const found = new Set<string>();
  for (const [k, sub] of Object.entries(node.keys ?? {})) {
    const here = path ? `${path}.${k}` : k;
    found.add(here);
    for (const p of keyPaths(sub, here)) found.add(p);
  }
  if (node.items) {
    const here = `${path}[]`;
    found.add(here);
    for (const p of keyPaths(node.items, here)) found.add(p);
  }
  return found;
}

export function nodeAt(node: Shape, key: string): Shape | null {
  let cur: Shape | undefined = node;
  let rest = key;
  while (rest) {
    if (rest.startsWith("[]")) {
      cur = cur.items;
      rest = rest.slice(2);
    } else {
      if (rest.startsWith(".")) rest = rest.slice(1);
      const name = /^[^.[]+/.exec(rest);
      if (!name) return null;
      cur = cur.keys?.[name[0]];
      rest = rest.slice(name[0].length);
    }
    if (cur === undefined) return null;
  }
  return cur;
}

export function overlaps(diffKey: string, readKey: string): boolean {
  return diffKey === readKey || readKey.startsWith(diffKey + ".") || readKey.startsWith(diffKey + "[]");
}

export function selectReads(diffs: Diff[], reads: string[]): Diff[] {
  return diffs.filter(([key, kind]) => kind !== "missing" || reads.some((r) => overlaps(key, r)));
}

export function readsOf(requests: Req[]): Record<string, string[]> {
  return Object.fromEntries(requests.map((r) => [r.name, [...(r.reads ?? [])]]));
}

export function readsProblems(requests: Req[], doc: Record<string, { shape: Shape }>): string[] {
  const out: string[] = [];
  for (const r of requests) {
    const have = doc[r.name];
    if (have === undefined) {
      out.push(`${r.name}: 記録に無い要求`);
      continue;
    }
    const paths = keyPaths(have.shape);
    for (const k of r.reads ?? []) if (!paths.has(k)) out.push(`${r.name}: reads の ${k} が記録に無い`);
  }
  return out;
}

export function compareAll(
  recorded: Record<string, Recorded>,
  actual: Record<string, Recorded>,
  reads?: Record<string, string[]>,
): Record<string, Diff[]> {
  const found: Record<string, Diff[]> = {};
  for (const [name, want] of Object.entries(recorded)) {
    const got = actual[name];
    if (got === undefined) {
      found[name] = [["$", "missing"]];
      continue;
    }
    let diffs: Diff[] = [];
    if (want.status !== got.status) diffs.push(["$status", "status"]);
    diffs.push(...compare(want.shape, got.shape));
    if (reads !== undefined) diffs = selectReads(diffs, reads[name] ?? []);
    if (diffs.length > 0) found[name] = diffs;
  }
  for (const name of Object.keys(actual).filter((n) => !(n in recorded)).sort()) found[name] = [["$", "extra"]];
  return found;
}

export function applyAllow(found: Record<string, Diff[]>, entries: Entry[]): [Record<string, Diff[]>, string[]] {
  const problems: string[] = [];
  const used = new Set<number>();
  const left: Record<string, Diff[]> = {};
  const label = (i: number) => `許可リスト[${i}] ${entries[i].request}/${entries[i].key}/${entries[i].kind}`;
  entries.forEach((e, i) => {
    if (!String(e.reason ?? "").trim()) problems.push(`${label(i)}: 理由が空`);
    if (!KINDS.includes(e.kind as string)) problems.push(`${label(i)}: 違いの種類が ${KINDS} のどれでもない`);
  });
  const index = new Map<string, number>();
  entries.forEach((e, i) => index.set(JSON.stringify([e.request, e.key, e.kind]), i));
  for (const [name, diffs] of Object.entries(found)) {
    for (const [key, kind] of diffs) {
      const hit = index.get(JSON.stringify([name, key, kind]));
      if (hit === undefined) (left[name] ??= []).push([key, kind]);
      else used.add(hit);
    }
  }
  entries.forEach((_, i) => {
    if (!used.has(i)) problems.push(`${label(i)}: 実際は差が出ていない（古い許可）`);
  });
  return [left, problems];
}

// 空の配列は形を持たず比較から外れるので、場面が配列を返していることを確かめる
export const NON_EMPTY = ["mrs_merged", "closes_issues", "issue_related_mrs", "discussions", "reviewers", "notes_get", "issue_notes_get"];

export function answerProblems(requests: Req[], answers: Record<string, [status: number, body: unknown]>): string[] {
  const out: string[] = [];
  for (const r of requests) {
    const got = answers[r.name];
    if (got === undefined) {
      out.push(`${r.name}: 応答が無い`);
      continue;
    }
    const [status, body] = got;
    if (status !== r.expect_status) out.push(`${r.name}: HTTP ${status}（期待は ${r.expect_status}）`);
    if (NON_EMPTY.includes(r.name) && !(Array.isArray(body) && body.length > 0)) out.push(`${r.name}: 配列が空（場面が組めていない）`);
  }
  return out;
}

const PLACEHOLDER = /\$\{([a-z_]+(?:\.[a-z_]+)*)\}/g;

export function substitute<T>(value: T, context: Record<string, string>): T {
  if (typeof value === "string") {
    return value.replace(PLACEHOLDER, (_, name: string) => {
      if (!(name in context)) throw new Error(`置き換え先が無い: ${name}`);
      return String(context[name]);
    }) as T;
  }
  if (Array.isArray(value)) return value.map((v) => substitute(v, context)) as T;
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, substitute(v, context)])) as T;
  }
  return value;
}
