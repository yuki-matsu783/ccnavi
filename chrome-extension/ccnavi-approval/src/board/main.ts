/**
 * 読み取り専用ボード（ADR-0093 段階 1）。承認・取り下げ・レビュー済み・「始める」は持たない。
 */
import type { Response } from "../core/protocol.js";
import { renderRepo } from "../core/render.js";
import { createRenderer } from "../core/sanitize.js";
import { readRepos } from "../core/settings.js";
import { collectRepo, type HostCall, type Stats } from "../core/snapshot.js";
import { blobCache } from "./cache.js";
import { startWorker, type PyWorker } from "./py-client.js";

const HOSTS = __CCNAVI_HOSTS__;
const md = createRenderer(window);
const main = document.getElementById("boards") as HTMLElement;
const status = document.getElementById("status") as HTMLElement;
let worker: PyWorker | null = null;

function hostCall(host: string, stats: Stats): HostCall {
  return async (op, args) => {
    const res = (await chrome.runtime.sendMessage({ kind: "host", host, op, args })) as Response;
    if (!res.ok) throw new Error(res.error);
    if (res.counter) {
      stats.rest += res.counter.rest;
      stats.graphql += res.counter.graphql;
    }
    return res.value;
  };
}

async function refresh(): Promise<void> {
  document.body.dataset.state = "loading";
  main.replaceChildren();
  const got = await chrome.storage.local.get("repos");
  const repos = readRepos(got.repos, HOSTS);
  if (repos.length === 0) {
    status.textContent = "リポジトリが登録されていない。設定画面で登録する";
    document.body.dataset.state = "done";
    return;
  }
  status.textContent = "Python（Pyodide）を起こしている…";
  if (worker === null) worker = startWorker();
  const t = await worker.init();
  status.textContent = `Pyodide ${t.pyodide}: 起動 ${t.boot_ms} ms・読み込み ${t.import_ms} ms`;
  status.dataset.bootMs = String(t.boot_ms);
  status.dataset.importMs = String(t.import_ms);
  status.dataset.violations = String(t.violations.length);
  const cache = await blobCache();
  for (const repo of repos) {
    const stats: Stats = { rest: 0, graphql: 0, blobsFetched: 0, blobsCached: 0 };
    const board = await collectRepo(repo, { call: hostCall(repo.host, stats), py: worker.call, cache, now: () => new Date(), stats });
    main.append(renderRepo(document, md, board));
  }
  document.body.dataset.state = "done";
}

document.getElementById("refresh")?.addEventListener("click", () => void refresh());
document.getElementById("options")?.addEventListener("click", () => void chrome.runtime.openOptionsPage());
refresh().catch((err) => {
  status.textContent = `読めなかった: ${(err as Error).message ?? String(err)}`;
  document.body.dataset.state = "error";
});
