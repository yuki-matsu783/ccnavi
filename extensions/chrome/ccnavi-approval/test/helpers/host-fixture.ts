/**
 * 録ったホストの応答の見本（test/fixtures/host/github/<場面>/）。
 *
 * リポジトリの sh の試験（tests/sh/github_host.py）も同じ見本を同じ規則で返す。規則は 2 つの代役で揃える:
 *
 * - `GET /repos/<o>/<r>/pulls?head=<o>:<branch>` → `branch` が場面のもの（`scene.json`。差し替えられる）なら
 *   `pulls.json`、違えば `[]`
 * - `GET /repos/<o>/<r>/pulls/<番号>/reviews?page=<N>` → 番号が `pulls.json` のものなら `reviews.<N>.json`
 *   （無ければ `[]`。N の既定は 1）
 * - `POST /graphql` の `reviewThreads` → 番号が同じで、cursor（sh は `c`、拡張は `after`）が null なら `threads.1.json`、
 *   `threads.<k>.json` の `endCursor` と同じなら `threads.<k+1>.json`
 * - `GET /user` → `user.json`
 * - GraphQL は、見本の応答が持つ欄（`THREAD_FIELDS`）が問い合わせに語として全部あるときだけ答える
 *
 * 期待値（`expected.json`）は sh が見本から組んだ結果で、拡張の試験（CX-T129）は TS が組んだ結果と比べる。
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

/** レビューの場面。取り下げの見本（`scene.json` の `kind` が `withdraw`）は `withdraw-fixture.ts` が読む */
export function sceneNames(): string[] {
  return fs
    .readdirSync(SCENES)
    .filter((n) => fs.statSync(path.join(SCENES, n)).isDirectory() && sceneKind(SCENES, n) !== "withdraw")
    .sort();
}

/** 場面の種類（`scene.json` の `kind`。レビューの場面は持たない） */
export function sceneKind(dir: string, name: string): string {
  const meta = JSON.parse(fs.readFileSync(path.join(dir, name, "scene.json"), "utf8")) as { kind?: unknown };
  return typeof meta.kind === "string" ? meta.kind : "";
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

/**
 * 見本の応答が持つ欄。問い合わせがどれかを落とせば、本物は答えにその欄を入れないので、代役も答えない
 * （欄の名前を見ずに見本を返すと、問い合わせの欄を削っても試験が通ってしまう）。sh の代役と同じ順序。
 */
export const THREAD_FIELDS = ["reviewThreads", "pageInfo", "hasNextPage", "endCursor", "nodes", "id", "isResolved", "comments", "url", "path", "line", "body", "createdAt"];

/** 問い合わせに語として現れない欄の名前 */
export function missingFields(query: string, fields: readonly string[]): string[] {
  return fields.filter((f) => !new RegExp(`(?<![A-Za-z0-9_])${f}(?![A-Za-z0-9_])`).test(query));
}

/** 見本に当たる要求なら答え、当たらなければ null（呼び手のほかの経路へ） */
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
      const missing = missingFields(q, THREAD_FIELDS);
      if (missing.length > 0) return { status: 200, json: { errors: [{ message: `問い合わせに欄が無い: ${missing.join(", ")}` }] } };
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
