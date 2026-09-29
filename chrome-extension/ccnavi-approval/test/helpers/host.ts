/**
 * service worker の `dispatch` を、模擬の GitHub と控えの無い PAT の置き場で組む。
 */
import { dispatch, type Deps } from "../../src/core/protocol.js";
import { parseHosts } from "../../src/core/hosts.js";
import type { BlobCache, HostCall, Stats } from "../../src/core/snapshot.js";
import type { MockGitHub } from "./mock-github.js";

export const EXT_ID = "abcdefghijklmnopabcdefghijklmnop";
export const BASE = `chrome-extension://${EXT_ID}/`;
export const BOARD = { id: EXT_ID, url: `${BASE}board.html` };
export const OPTIONS = { id: EXT_ID, url: `${BASE}options.html` };

export const HOSTS = parseHosts(
  JSON.stringify({
    hosts: [
      { id: "github.com", kind: "github", api: "https://api.github.com", graphql: "https://api.github.com/graphql", web: "https://github.com" },
      { id: "gitlab.com", kind: "gitlab", api: "https://gitlab.com/api/v4", web: "https://gitlab.com" },
    ],
  }),
);

export function deps(mock: MockGitHub, tokens: Map<string, string> = new Map()): Deps {
  return {
    hosts: HOSTS,
    extensionId: EXT_ID,
    base: BASE,
    fetch: mock.fetch,
    getToken: async (h) => tokens.get(h) ?? "",
    setToken: async (h, t) => void tokens.set(h, t),
    clearToken: async (h) => void tokens.delete(h),
  };
}

export function hostCall(d: Deps, stats: Stats): HostCall {
  return async (op, args) => {
    const res = await dispatch({ kind: "host", host: "github.com", op, args }, BOARD, d);
    if (!res.ok) throw new Error(res.error);
    if (res.counter) {
      stats.rest += res.counter.rest;
      stats.graphql += res.counter.graphql;
    }
    return res.value;
  };
}

export function memoryCache(): BlobCache & { size(): number } {
  const m = new Map<string, string>();
  return { get: async (k) => m.get(k), put: async (k, v) => void m.set(k, v), size: () => m.size };
}

export function newStats(): Stats {
  return { rest: 0, graphql: 0, blobsFetched: 0, blobsCached: 0 };
}
