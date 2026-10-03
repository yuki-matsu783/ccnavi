/**
 * ホストの応答の見本（test/fixtures/host/gitlab/<場面>/）を返す GitLab の代役。見本は本物の形に合わせて
 * 手で組んだもの（ADR-0093 の 8.9、段階 5）。
 *
 * リポジトリの sh の試験（tests/sh/gitlab_host.py）も同じ見本を同じ規則で返す。規則は 2 つの代役で揃える:
 *
 * - `GET /api/v4/projects/<namespace と project をまとめて符号化>` → `{"id": 42}`（入れ子のグループも同じ綴り）
 * - `GET .../merge_requests?state=opened&source_branch=<b>` → `b` が場面のもの（`scene.json`。差し替えられる）なら
 *   `mrs.json`（フォークの MR を含みうる。呼び手が `source_project_id` で絞る）、違えば `[]`
 * - `GET .../merge_requests/<iid>/discussions?page=<N>`・`.../reviewers?page=<N>` → iid が `mrs.json` のどれかなら
 *   `discussions.<N>.json`・`reviewers.<N>.json`（無ければ `[]`。N の既定は 1）
 * - `GET /api/v4/user` → `user.json`
 *
 * 期待値（`expected.json`）は sh が見本から組んだ写しで、拡張の試験（CX-T144）は TS が組んだ写しと比べる。
 */
import fs from "node:fs";
import path from "node:path";
import { HERE } from "./python.js";

export const GITLAB_SCENES = path.join(HERE, "test", "fixtures", "host", "gitlab");

export interface GitLabScene {
  readonly name: string;
  readonly namespace: string;
  readonly project: string;
  readonly poster: string;
  branch: string;
  readonly files: Record<string, unknown>;
}

export function gitlabSceneNames(): string[] {
  return fs
    .readdirSync(GITLAB_SCENES)
    .filter((n) => fs.statSync(path.join(GITLAB_SCENES, n)).isDirectory())
    .sort();
}

export function loadGitLabScene(name: string): GitLabScene {
  const dir = path.join(GITLAB_SCENES, name);
  const files: Record<string, unknown> = {};
  for (const f of fs.readdirSync(dir)) {
    if (f.endsWith(".json")) files[f] = JSON.parse(fs.readFileSync(path.join(dir, f), "utf8"));
  }
  const meta = files["scene.json"] as { namespace: string; project: string; branch: string; poster: string };
  return { name, namespace: meta.namespace, project: meta.project, branch: meta.branch, poster: meta.poster, files };
}

function iids(scene: GitLabScene): number[] {
  return (scene.files["mrs.json"] as { iid: number }[]).map((m) => m.iid);
}

/** 見本に当たる要求なら答え、当たらなければ null（呼び手のほかの経路へ）。`u.pathname` は API の根（`/api/v4`）を含む */
export function gitlabSceneAnswer(scene: GitLabScene, method: string, u: URL): { status: number; json: unknown } | null {
  const p = u.pathname.replace(/^.*?\/api\/v4/, "");
  const base = `/projects/${encodeURIComponent(`${scene.namespace}/${scene.project}`)}`;
  if (method === "GET" && p === "/user") return { status: 200, json: scene.files["user.json"] };
  if (method === "GET" && p === base) return { status: 200, json: { id: 42, default_branch: "main" } };
  if (method === "GET" && p === `${base}/merge_requests` && u.searchParams.get("state") === "opened" && u.searchParams.get("source_branch") === scene.branch) {
    return { status: 200, json: scene.files["mrs.json"] };
  }
  const rest = p.startsWith(`${base}/`) ? p.slice(base.length) : "";
  const m = /^\/merge_requests\/(\d+)\/(discussions|reviewers)$/.exec(rest);
  if (method === "GET" && m) {
    if (!iids(scene).includes(Number(m[1]))) return { status: 200, json: [] };
    const page = u.searchParams.get("page") ?? "1";
    return { status: 200, json: scene.files[`${m[2]}.${page}.json`] ?? [] };
  }
  return null;
}

/** 見本だけを返す `fetch`（単体試験。見本の外は 404） */
export function gitlabSceneFetch(scene: GitLabScene) {
  return async (url: string, init: { method: string; headers: Record<string, string>; body?: string }) => {
    const r = gitlabSceneAnswer(scene, init.method, new URL(url)) ?? { status: 404, json: { message: "404 Not Found" } };
    return { status: r.status, ok: r.status >= 200 && r.status < 300, json: async () => r.json, headers: { get: () => null } };
  };
}
