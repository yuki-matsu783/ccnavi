/**
 * 録ったホストの応答の見本（test/fixtures/host/github/<場面>/。ADR-0093 の 8.9。段階 4）を返す代役。
 *
 * リポジトリの sh の試験（tests/sh/github_host.py）も同じ見本を同じ規則で返す。規則は 2 つの代役で揃える:
 *
 * - `GET /repos/<o>/<r>/pulls?head=<o>:<branch>` → `branch` が場面のもの（`scene.json`。差し替えられる）なら
 *   `pulls.json`、違えば `[]`
 * - `GET /repos/<o>/<r>/pulls/<番号>/reviews?page=<N>` → 番号が `pulls.json` のものなら `reviews.<N>.json`
 *   （無ければ `[]`。N の既定は 1）
 * - `POST /graphql` の `reviewThreads` → 番号が同じで、cursor（sh は `c`、拡張は `after`）が null なら `threads.1.json`、
 *   `threads.<k>.json` の `endCursor` と同じなら `threads.<k+1>.json`
 * - `GET /user`・`viewer` → `user.json`
 *
 * 期待値（`expected.json`）は sh が見本から組んだ写しで、拡張の試験（CX-T129）は TS が組んだ写しと比べる。
 */
import fs from "node:fs";
import path from "node:path";
import { HERE } from "./python.js";

export const SCENES = path.join(HERE, "test", "fixtures", "host", "github");

export interface Scene {
  readonly name: string;
  readonly owner: string;
  readonly repo: string;
  branch: string;
  readonly files: Record<string, unknown>;
}

export function sceneNames(): string[] {
  return fs
    .readdirSync(SCENES)
    .filter((n) => fs.statSync(path.join(SCENES, n)).isDirectory())
    .sort();
}

export function loadScene(name: string): Scene {
  const dir = path.join(SCENES, name);
  const files: Record<string, unknown> = {};
  for (const f of fs.readdirSync(dir)) {
    if (f.endsWith(".json")) files[f] = JSON.parse(fs.readFileSync(path.join(dir, f), "utf8"));
  }
  const meta = files["scene.json"] as { owner: string; repo: string; branch: string };
  return { name, owner: meta.owner, repo: meta.repo, branch: meta.branch, files };
}

function prNumber(scene: Scene): number {
  return ((scene.files["pulls.json"] as { number: number }[])[0] ?? { number: -1 }).number;
}

/** 見本に当たる要求なら答え、当たらなければ null（呼び手のほかの道へ） */
export function sceneAnswer(scene: Scene, method: string, u: URL, body: string): { status: number; json: unknown } | null {
  const base = `/repos/${scene.owner}/${scene.repo}`;
  const p = u.pathname.replace(/^\/api\/v3/, "");
  if (method === "GET" && p === `${base}/pulls` && u.searchParams.get("head") === `${scene.owner}:${scene.branch}`) {
    return { status: 200, json: scene.files["pulls.json"] };
  }
  const m = new RegExp(`^${base}/pulls/(\\d+)/reviews$`).exec(p);
  if (method === "GET" && m && Number(m[1]) === prNumber(scene)) {
    const page = u.searchParams.get("page") ?? "1";
    return { status: 200, json: scene.files[`reviews.${page}.json`] ?? [] };
  }
  if (method === "GET" && p === "/user") return { status: 200, json: scene.files["user.json"] };
  if (method === "POST" && p.endsWith("/graphql")) {
    const req = JSON.parse(body || "{}") as { query?: string; variables?: Record<string, unknown> };
    const q = req.query ?? "";
    const v = req.variables ?? {};
    if (q.includes("reviewThreads") && Number(v.number ?? v.n) === prNumber(scene)) {
      let cursor = (v.after ?? v.c ?? null) as string | null;
      for (let k = 1; ; k += 1) {
        const page = scene.files[`threads.${k}.json`] as { data: { repository: { pullRequest: { reviewThreads: { pageInfo: { endCursor: string } } } } } } | undefined;
        if (!page) return { status: 200, json: { errors: [{ message: `見本に cursor ${cursor} のページが無い` }] } };
        if (cursor === null) return { status: 200, json: page };
        if (page.data.repository.pullRequest.reviewThreads.pageInfo.endCursor === cursor) cursor = null;
      }
    }
  }
  return null;
}

/** 見本だけを返す `fetch`（単体試験。見本の外は 404） */
export function sceneFetch(scene: Scene) {
  return async (url: string, init: { method: string; headers: Record<string, string>; body?: string }) => {
    const r = sceneAnswer(scene, init.method, new URL(url), init.body ?? "") ?? { status: 404, json: { message: "Not Found" } };
    return { status: r.status, ok: r.status >= 200 && r.status < 300, json: async () => r.json, headers: { get: () => null } };
  };
}
