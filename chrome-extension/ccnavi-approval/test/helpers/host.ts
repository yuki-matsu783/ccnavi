/**
 * service worker の `dispatch` を、模擬の GitHub と控えの無い PAT の置き場で組む。
 */
import type { TokenMeta } from "../../src/core/expiry.js";
import type { RepoConfig } from "../../src/core/settings.js";
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

export function deps(
  mock: MockGitHub,
  tokens: Map<string, string> = new Map(),
  metas: Map<string, TokenMeta> = new Map(),
  now: () => Date = () => new Date(),
  repos: RepoConfig[] = REPOS,
): Deps {
  return {
    hosts: HOSTS,
    extensionId: EXT_ID,
    base: BASE,
    fetch: mock.fetch,
    getToken: async (h) => tokens.get(h) ?? "",
    setToken: async (h, t) => void tokens.set(h, t),
    clearToken: async (h) => void tokens.delete(h),
    getMeta: async (h) => metas.get(h) ?? {},
    setMeta: async (h, m) => void metas.set(h, m),
    now,
    getRepos: async () => repos,
  };
}

/** 設定画面で登録したリポジトリ（模擬の GitHub の acme/widgets） */
export const REPOS: RepoConfig[] = [{ host: "github.com", owner: "acme", repo: "widgets", integration: "", recentDays: 3, extraBranches: [], project: "", workspace: "" }];

/** 設定画面で登録した GitLab のリポジトリ（模擬の GitLab の acme/widgets。段階 5） */
export const GITLAB_REPO: RepoConfig = { host: "gitlab.com", owner: "acme", repo: "widgets", integration: "", recentDays: 3, extraBranches: [], project: "", workspace: "" };

export function hostCall(d: Deps, stats: Stats, host = "github.com"): HostCall {
  return async (op, args) => {
    const res = await dispatch({ kind: "host", host, op, args }, BOARD, d);
    if (!res.ok) throw Object.assign(new Error(res.error), { status: res.status });
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
