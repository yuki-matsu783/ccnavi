/**
 * ボード（ADR-0093 段階 3）。承認待ちを並べ、承認と承認の取り下げを親のブランチへ書く。
 * レビュー済み・「始める」は持たない（段階 4・5）。
 *
 * PAT はこのページに来ない。ホストの API は service worker に名前で頼む（5.5 の 4）。
 */
import type { TokenStatus, Response } from "../core/protocol.js";
import { renderRepo, type Actions } from "../core/render.js";
import { createRenderer } from "../core/sanitize.js";
import { readRepos, type RepoConfig } from "../core/settings.js";
import { collectRepo, type HostCall, type RepoBoard, type Stats } from "../core/snapshot.js";
import { approveFamily, withdrawTicket, type Outcome, type WriteDeps } from "../core/write.js";
import { blobCache } from "./cache.js";
import { startWorker, type PyWorker } from "./py-client.js";

const HOSTS = __CCNAVI_HOSTS__;
const VERSION = __CCNAVI_VERSION__;
const md = createRenderer(window);
const main = document.getElementById("boards") as HTMLElement;
const status = document.getElementById("status") as HTMLElement;
const banner = document.getElementById("banner") as HTMLElement;
const result = document.getElementById("result") as HTMLElement;
let worker: PyWorker | null = null;
let busy = false;

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

function newStats(): Stats {
  return { rest: 0, graphql: 0, blobsFetched: 0, blobsCached: 0 };
}

async function writeDeps(repo: RepoConfig): Promise<WriteDeps> {
  const stats = newStats();
  if (worker === null) worker = startWorker();
  return { call: hostCall(repo.host, stats), py: worker.call, cache: await blobCache(), now: () => new Date(), stats, version: VERSION };
}

/** PAT の期限の帯（D25）。PAT の無いホストは出さない */
async function drawBanner(repos: readonly RepoConfig[]): Promise<void> {
  banner.replaceChildren();
  for (const host of new Set(repos.map((r) => r.host))) {
    const res = (await chrome.runtime.sendMessage({ kind: "token.status", host })) as Response;
    const st = res.ok ? (res.value as TokenStatus) : null;
    if (!st?.set || !st.notice || st.notice.level === "ok") continue;
    const p = document.createElement("p");
    p.className = `notice ${st.notice.level === "expired" ? "error" : "warn"}`;
    p.dataset.level = st.notice.level;
    p.textContent = st.notice.text;
    banner.append(p);
  }
}

function say(outcome: Outcome, what: string): void {
  result.dataset.kind = outcome.kind;
  const head =
    outcome.kind === "written"
      ? `${what}を書いた（${outcome.oid.slice(0, 7)}${outcome.rounds > 1 ? `、先頭が動いたので ${outcome.rounds} 周目で書いた` : ""}）。書いた後の中身も確かめた`
      : outcome.kind === "changed"
        ? `${what}を書かなかった: ${outcome.message}`
        : outcome.kind === "conflict"
          ? `${what}を書けなかった（人に回す）: ${outcome.message}`
          : `${what}を書かなかった: ${outcome.message}`;
  result.textContent = head;
  result.className = `notice ${outcome.kind === "written" ? "ok" : "error"}`;
}

const actions: Actions = {
  approve(repoBoard: RepoBoard, family) {
    void act(async () => {
      const r = family.result;
      if (!r?.digest || !r.batch) return;
      const deps = await writeDeps(repoBoard.repo);
      const ids = r.batch.map((e) => e.ticket);
      // ホストの Approve が外れうることを出す（8.10。止めはしない）
      let warn = "";
      try {
        const prs = (await deps.call("pullApprovals", [repoBoard.repo.owner, repoBoard.repo.repo, family.family.name])) as { number: number }[];
        if (prs.length > 0) warn = `\n\n注意: MR ${prs.map((p) => `#${p.number}`).join(", ")} に Approve が付いている。このコミットで MR の Approve が外れることがある`;
      } catch {
        // 読めなくても承認は止めない（注意の表示だけ）
      }
      if (!window.confirm(`${family.family.name} に ${ids.join(", ")} の承認を書く（1 コミット）。${warn}`)) return;
      say(await approveFamily(repoBoard.repo, family.family.name, { ids, digest: r.digest, only: r.only ?? null }, deps), "承認");
    });
  },
  withdraw(repoBoard: RepoBoard, family, ticket) {
    void act(async () => {
      const reason = window.prompt(`${ticket} の承認を取り下げて todo/ に戻す。理由（任意）`, "");
      if (reason === null) return;
      const deps = await writeDeps(repoBoard.repo);
      say(await withdrawTicket(repoBoard.repo, family.family.name, ticket, reason, deps), "取り下げ");
    });
  },
};

async function act(fn: () => Promise<void>): Promise<void> {
  if (busy) return;
  busy = true;
  document.body.dataset.state = "writing";
  for (const b of document.querySelectorAll<HTMLButtonElement>("button.action")) b.disabled = true;
  try {
    await fn();
  } catch (err) {
    result.dataset.kind = "failed";
    result.textContent = `書けなかった: ${(err as Error).message ?? String(err)}`;
  } finally {
    busy = false;
    await refresh(false);
  }
}

async function refresh(clearResult = true): Promise<void> {
  document.body.dataset.state = "loading";
  if (clearResult) {
    result.textContent = "";
    delete result.dataset.kind;
  }
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
    const stats = newStats();
    const board = await collectRepo(repo, { call: hostCall(repo.host, stats), py: worker.call, cache, now: () => new Date(), stats });
    main.append(renderRepo(document, md, board, actions));
  }
  await drawBanner(repos);
  document.body.dataset.state = "done";
}

document.getElementById("refresh")?.addEventListener("click", () => void refresh());
document.getElementById("options")?.addEventListener("click", () => void chrome.runtime.openOptionsPage());
refresh().catch((err) => {
  status.textContent = `読めなかった: ${(err as Error).message ?? String(err)}`;
  document.body.dataset.state = "error";
});
