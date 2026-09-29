/**
 * Pyodide の上の入口（`py/ccnavi_chrome.py`）との約束。判定はすべて Python が出し、
 * ここは形だけを持つ（ADR-0035）。
 */

/** 要求と答えの形の版。`ccnavi_chrome.SCHEMA` と揃える */
export const PY_SCHEMA = 1;

export interface Placement {
  readonly tickets: string;
  readonly approved: string;
  readonly integration_paths: readonly string[];
  readonly integration_files: readonly string[];
  readonly branch_paths: readonly string[];
}

export interface Branch {
  readonly head: string;
  readonly files: Record<string, string>;
  readonly binary: readonly string[];
}

export interface Snapshot {
  readonly integration: { readonly name: string; readonly source: "setting" | "default"; readonly head: string };
  readonly branches: Record<string, Branch>;
  readonly absent: readonly string[];
}

export interface Family {
  readonly name: string;
  readonly title: string;
  readonly state: string;
  readonly closed: boolean;
}

export interface Closure {
  readonly families: readonly string[];
  readonly need: readonly string[];
  readonly absent: readonly string[];
  readonly over_limit: boolean;
  readonly message: string;
}

export interface BatchEntry {
  readonly ticket: string;
  readonly title: string;
  readonly parent: string | null;
  readonly phase: number | null;
  readonly revision: boolean;
  readonly tree: string;
  readonly path: string;
  readonly overflow: readonly string[];
  readonly body: string;
}

export interface BoardResult {
  readonly family: string;
  readonly closure: Closure;
  readonly undecided?: string;
  readonly refused?: string;
  readonly batch?: readonly BatchEntry[];
  readonly text?: string;
  readonly rejected?: readonly { readonly ticket: string; readonly problems: readonly string[] }[];
  readonly problems?: readonly string[];
}

export interface Compat {
  readonly extension: number;
  readonly repository: number | null;
  readonly same: boolean;
  readonly message: string;
}

/** Python を呼ぶ口。Worker でも、試験の Node の Pyodide でも同じ形 */
export type PyCall = (request: Record<string, unknown>) => Promise<Record<string, unknown>>;

export class PyError extends Error {}

async function ask<T>(call: PyCall, op: string, body: Record<string, unknown>, key: string): Promise<T> {
  const res = await call({ schema: PY_SCHEMA, op, ...body });
  if (typeof res.error === "string") {
    throw new PyError(res.error);
  }
  if (res.schema !== PY_SCHEMA) {
    throw new PyError(`Python の答えの形の版が違う（${String(res.schema)}）`);
  }
  return (key === "" ? res : res[key]) as T;
}

export const py = {
  placement: (call: PyCall, settings: string | null) => ask<Placement>(call, "placement", { settings }, "placement"),
  families: (call: PyCall, settings: string | null, snapshot: Snapshot, candidates: readonly string[]) =>
    ask<Family[]>(call, "families", { settings, snapshot, candidates }, "families"),
  closure: (call: PyCall, settings: string | null, snapshot: Snapshot, family: string) =>
    ask<Closure>(call, "closure", { settings, snapshot, family }, ""),
  board: (call: PyCall, settings: string | null, snapshot: Snapshot, family: string) =>
    ask<BoardResult>(call, "board", { settings, snapshot, family }, ""),
  compat: (call: PyCall, snapshot: Snapshot) => ask<Compat>(call, "compat", { snapshot }, "compat"),
};
