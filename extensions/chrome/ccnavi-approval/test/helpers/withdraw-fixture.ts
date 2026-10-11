/**
 * 取り下げの見本（test/fixtures/host/<github|gitlab>/<場面>/。`scene.json` の `kind` が `withdraw`）。
 * 承認コミットを引いて提案を読むまでのホストの応答を、実際の形に合わせて手で組んだもの。
 *
 * GitHub の見本は次のとおり。
 * - `commits.json`: `GET /repos/<o>/<r>/commits?sha=<先頭>&path=<doing/>` の答え
 * - `objects.json`: GraphQL の `object(expression: "<コミット>:<パス>")` の答え（`{__typename, oid}` か null）
 * - `history.json`: GraphQL の first-parent の履歴（`history(first: …)`）の答え全体
 * - `blobs.json`: GraphQL の `object(oid:) { ... on Blob }` の答え（oid ごと）
 *
 * GitLab の見本は次のとおり。
 * - `commits.json`: `GET .../repository/commits?ref_name=<先頭>&path=<doing/>` の答え
 * - `first_parent.json`: `GET .../repository/commits?ref_name=<先頭>&first_parent=true` の答え
 * - `trees.json`: `GET .../repository/tree?ref=<コミット>&path=<ディレクトリ>` の答え（`<コミット>:<ディレクトリ>` ごと。null は 404）
 * - `blobs.json`: `GET .../repository/blobs/<sha>` の答え（base64）
 *
 * `expected.json` は取り下げの判定（`withdraw` の `problems`）の期待値。
 */
import fs from "node:fs";
import path from "node:path";
import { sceneKind } from "./host-fixture.js";
import { HERE } from "./python.js";

export const WITHDRAW_ROOT = path.join(HERE, "test", "fixtures", "host");

export interface WithdrawScene {
  readonly name: string;
  readonly host: "github" | "gitlab";
  /** GitHub は owner、GitLab は namespace */
  readonly owner: string;
  readonly repo: string;
  readonly branch: string;
  readonly head: string;
  readonly ident: string;
  readonly files: Record<string, unknown>;
}

export function withdrawSceneNames(host: "github" | "gitlab"): string[] {
  const dir = path.join(WITHDRAW_ROOT, host);
  return fs
    .readdirSync(dir)
    .filter((n) => fs.statSync(path.join(dir, n)).isDirectory() && sceneKind(dir, n) === "withdraw")
    .sort();
}

export function loadWithdrawScene(host: "github" | "gitlab", name: string): WithdrawScene {
  const dir = path.join(WITHDRAW_ROOT, host, name);
  const files: Record<string, unknown> = {};
  for (const f of fs.readdirSync(dir)) {
    if (f.endsWith(".json")) files[f] = JSON.parse(fs.readFileSync(path.join(dir, f), "utf8"));
  }
  const meta = files["scene.json"] as { owner?: string; namespace?: string; repo?: string; project?: string; branch: string; head: string; ident: string };
  return {
    name,
    host,
    owner: meta.owner ?? meta.namespace ?? "",
    repo: meta.repo ?? meta.project ?? "",
    branch: meta.branch,
    head: meta.head,
    ident: meta.ident,
    files,
  };
}

type Answer = { status: number; json: unknown };

function githubAnswer(scene: WithdrawScene, doing: string, method: string, u: URL, body: string): Answer | null {
  const base = `/repos/${scene.owner}/${scene.repo}`;
  const p = u.pathname.replace(/^\/api\/v3/, "");
  if (method === "GET" && p === `${base}/commits`) {
    const hit = u.searchParams.get("sha") === scene.head && u.searchParams.get("path") === doing;
    return { status: 200, json: hit ? scene.files["commits.json"] : [] };
  }
  if (method === "POST" && p.endsWith("/graphql")) {
    const req = JSON.parse(body || "{}") as { query?: string; variables?: Record<string, unknown> };
    const q = req.query ?? "";
    const v = req.variables ?? {};
    if (q.includes("history(first:")) {
      return v.oid === scene.head ? { status: 200, json: scene.files["history.json"] } : { status: 200, json: { data: { repository: { object: null } } } };
    }
    const objects = scene.files["objects.json"] as Record<string, unknown>;
    const blobs = scene.files["blobs.json"] as Record<string, unknown>;
    const repository: Record<string, unknown> = {};
    for (const [k, value] of Object.entries(v)) {
      const e = /^e(\d+)$/.exec(k);
      if (e && q.includes("object(expression:")) repository[`p${e[1]}`] = objects[String(value)] ?? null;
      const o = /^o(\d+)$/.exec(k);
      if (o && q.includes("... on Blob")) repository[`b${o[1]}`] = blobs[String(value)] ?? null;
    }
    return { status: 200, json: { data: { repository } } };
  }
  return null;
}

function gitlabAnswer(scene: WithdrawScene, doing: string, method: string, u: URL): Answer | null {
  if (method !== "GET") return null;
  const p = u.pathname.replace(/^.*?\/api\/v4/, "");
  const base = `/projects/${encodeURIComponent(`${scene.owner}/${scene.repo}`)}/repository`;
  if (p === `${base}/commits` && u.searchParams.get("ref_name") === scene.head) {
    if (u.searchParams.get("first_parent") === "true") return { status: 200, json: scene.files["first_parent.json"] };
    return { status: 200, json: u.searchParams.get("path") === doing ? scene.files["commits.json"] : [] };
  }
  if (p === `${base}/tree` && u.searchParams.get("page") === "1") {
    const rows = (scene.files["trees.json"] as Record<string, unknown>)[`${u.searchParams.get("ref")}:${u.searchParams.get("path") ?? ""}`];
    return rows ? { status: 200, json: rows } : { status: 404, json: { message: "404 Tree Not Found" } };
  }
  const m = new RegExp(`^${base.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}/blobs/([0-9a-f]{40})$`).exec(p);
  if (m) {
    const blob = (scene.files["blobs.json"] as Record<string, unknown>)[m[1]];
    return blob ? { status: 200, json: blob } : { status: 404, json: { message: "404 Blob Not Found" } };
  }
  return null;
}

/** 見本だけを返す `fetch`。`doing` は承認済みチケットのパス。見本の外は 404 */
export function withdrawSceneFetch(scene: WithdrawScene, doing: string) {
  return async (url: string, init: { method: string; headers: Record<string, string>; body?: string }) => {
    const u = new URL(url);
    const r =
      (scene.host === "github" ? githubAnswer(scene, doing, init.method, u, init.body ?? "") : gitlabAnswer(scene, doing, init.method, u)) ??
      { status: 404, json: { message: "Not Found" } };
    return { status: r.status, ok: r.status >= 200 && r.status < 300, json: async () => r.json, headers: { get: () => null } };
  };
}
