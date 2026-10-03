/**
 * ボード（ADR-0093 段階 3・4・5）。承認待ちを並べ、承認と承認の取り下げを親のブランチへ書く。
 * 段階 4 から、依頼済みのフェーズに MR のスレッドを出し、レビュー済みの印を書く。
 * 段階 5 から、GitLab とプロジェクトのリポジトリも読み、「始める」（issue から親のブランチを作る。8.6）を出す。
 * GitLab へ書いて打ち消しが収まらなかった家族は「要確認」を控え（`chrome.storage.local` の `attention`）、
 * ユーザが確かめて外すまで出す（8.4）。
 *
 * PAT はこのページに来ない。ホストの API は service worker に名前で頼む（5.5 の 4）。
 */
import type { TokenStatus, Response } from "../core/protocol.js";
import type { Issue } from "../core/github.js";
import { renderRepo, type Actions, type Extras } from "../core/render.js";
import { createRenderer } from "../core/sanitize.js";
import { readRepos, repoKey, type RepoConfig } from "../core/settings.js";
import { collectRepo, type Deps, type HostCall, type RepoBoard, type Stats } from "../core/snapshot.js";
import { listIssues, startIssue } from "../core/start.js";
import { approveFamily, confirmPhase, withdrawTicket, type Outcome, type WriteDeps } from "../core/write.js";
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
    if (!res.ok) throw Object.assign(new Error(res.error), { status: res.status });
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

/** 登録したリポジトリ（設定画面の値） */
let repos: RepoConfig[] = [];
/** リポジトリごとの最後のボードと、読んだ issue の一覧（「始める」） */
const boards = new Map<string, RepoBoard>();
const issues = new Map<string, { list: Issue[] | null; error: string }>();

/** プロジェクトのリポジトリのワークスペース（登録したもの）とそのホストへ頼む関数（段階 5） */
function workspaceOf(repo: RepoConfig, stats: Stats): Deps["workspace"] {
  if (!repo.project) return undefined;
  const ws = repos.find((r) => repoKey(r) === repo.workspace && !r.project);
  return ws ? { repo: ws, call: hostCall(ws.host, stats) } : undefined;
}

async function writeDeps(repo: RepoConfig): Promise<WriteDeps> {
  const stats = newStats();
  if (worker === null) worker = startWorker();
  return {
    call: hostCall(repo.host, stats),
    py: worker.call,
    cache: await blobCache(),
    now: () => new Date(),
    stats,
    version: VERSION,
    workspace: workspaceOf(repo, stats),
    kind: HOSTS.find((h) => h.id === repo.host)?.kind ?? "github",
  };
}

/** 「要確認」の家族へは、このブラウザから書かない（11.9.1 の決定 B。ボタンを出さないのに加えて、押す前にも見る） */
async function blocked(repo: RepoConfig, family: string): Promise<boolean> {
  const why = (await readAttention())[repoKey(repo)]?.[family];
  if (!why) return false;
  result.dataset.kind = "refused";
  result.className = "notice error";
  result.textContent = `${family} は要確認のまま。ホストの履歴を確かめて「確かめた」を押すまで、このブラウザからは書かない`;
  return true;
}

type Attention = Record<string, Record<string, string>>;

async function readAttention(): Promise<Attention> {
  const got = await chrome.storage.local.get("attention");
  const v = got.attention;
  return v && typeof v === "object" ? (v as Attention) : {};
}

/** 要確認の家族を控え、ユーザが確かめて外すまでボードに出す。要確認になるのは、打ち消しが収まらない・書いたか確かめられない などのとき（8.4） */
async function noteAttention(repo: RepoConfig, family: string, outcome: Outcome): Promise<void> {
  if (outcome.kind !== "attention") return;
  const all = await readAttention();
  const key = repoKey(repo);
  all[key] = { ...(all[key] ?? {}), [family]: outcome.message };
  await chrome.storage.local.set({ attention: all });
}

function drawRepo(board: RepoBoard, attention: Attention): HTMLElement {
  const key = repoKey(board.repo);
  const extras: Extras = { attention: attention[key] ?? {}, issues: issues.get(key) };
  const node = renderRepo(document, md, board, actions, extras);
  const old = main.querySelector(`section.repo[data-repo="${CSS.escape(key)}"]`);
  if (old) old.replaceWith(node);
  else main.append(node);
  return node;
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
      ? `${what}を書いた（${outcome.oid.slice(0, 7)}${outcome.rounds > 1 ? `。途中で親のブランチの先頭が動いたので、${outcome.rounds} 回目で書いた` : ""}）。書いた後の中身も確かめた`
      : outcome.kind === "changed"
        ? `${what}を書かなかった: ${outcome.message}`
        : outcome.kind === "conflict"
          ? `${what}を書けなかった（自動ではやり直さない）: ${outcome.message}`
          : outcome.kind === "attention"
            ? `${what}は要確認になった。書けたかどうかと何が残ったかを、ホストの履歴で確かめてください: ${outcome.message}`
            : `${what}を書かなかった: ${outcome.message}`;
  result.textContent = head;
  result.className = `notice ${outcome.kind === "written" ? "ok" : "error"}`;
}

const actions: Actions = {
  approve(repoBoard: RepoBoard, family) {
    void act(async () => {
      if (await blocked(repoBoard.repo, family.family.name)) return false;
      const r = family.result;
      if (!r?.digest || !r.batch) return false;
      const deps = await writeDeps(repoBoard.repo);
      const ids = r.batch.map((e) => e.ticket);
      // ホストの Approve が外れうることを出す（8.10。止めはしない）
      let warn = "";
      try {
        const prs = (await deps.call("pullApprovals", [repoBoard.repo.owner, repoBoard.repo.repo, family.family.name])) as { number: number }[];
        if (prs.length > 0) warn = `\n\n注意: マージリクエスト ${prs.map((p) => `#${p.number}`).join(", ")} に Approve が付いている。このコミットを書くと、その Approve が外れることがある`;
      } catch {
        // 読めなくても承認は止めない（注意の表示だけ）
      }
      if (!window.confirm(`${family.family.name} に ${ids.join(", ")} の承認を 1 コミットで書く。${warn}`)) return false;
      const outcome = await approveFamily(repoBoard.repo, family.family.name, { ids, digest: r.digest, only: r.only ?? null }, deps);
      await noteAttention(repoBoard.repo, family.family.name, outcome);
      say(outcome, "承認");
      return true;
    });
  },
  review(repoBoard: RepoBoard, family, phase) {
    void act(async () => {
      if (await blocked(repoBoard.repo, family.family.name)) return false;
      const deps = await writeDeps(repoBoard.repo);
      let warn = "";
      try {
        const prs = (await deps.call("pullApprovals", [repoBoard.repo.owner, repoBoard.repo.repo, family.family.name])) as { number: number }[];
        if (prs.length > 0) warn = `\n\n注意: マージリクエスト ${prs.map((p) => `#${p.number}`).join(", ")} に Approve が付いている。このコミットを書くと、その Approve が外れることがある`;
      } catch {
        // 読めなくても止めない（注意の表示だけ。8.10）
      }
      const msg = `${family.family.name} のフェーズ ${phase} をレビュー済みにする。レビュー待ちの子を done/ へ動かし、マーカーと合わせて 1 コミットで書く。書く前に、押した時点のスレッドとレビューを読み直して確かめる。${warn}`;
      if (!window.confirm(msg)) return false;
      const outcome = await confirmPhase(repoBoard.repo, family.family.name, phase, deps);
      await noteAttention(repoBoard.repo, family.family.name, outcome);
      say(outcome, "レビュー済み");
      return true;
    });
  },
  withdraw(repoBoard: RepoBoard, family, ticket) {
    void act(async () => {
      if (await blocked(repoBoard.repo, family.family.name)) return false;
      const reason = window.prompt(`${ticket} の承認を取り下げて todo/ に戻す。理由（任意）`, "");
      if (reason === null) return false;
      const deps = await writeDeps(repoBoard.repo);
      const outcome = await withdrawTicket(repoBoard.repo, family.family.name, ticket, reason, deps);
      await noteAttention(repoBoard.repo, family.family.name, outcome);
      say(outcome, "取り下げ");
      return true;
    });
  },
  loadIssues(repoBoard: RepoBoard) {
    void (async () => {
      const key = repoKey(repoBoard.repo);
      try {
        issues.set(key, { list: await listIssues(repoBoard.repo, await writeDeps(repoBoard.repo)), error: "" });
      } catch (err) {
        issues.set(key, { list: null, error: `issue を読めなかった: ${(err as Error).message ?? String(err)}` });
      }
      drawRepo(boards.get(key) ?? repoBoard, await readAttention());
    })();
  },
  start(repoBoard: RepoBoard, issue: Issue) {
    void act(async () => {
      if (!window.confirm(`issue #${issue.number} から親のブランチを統合先 ${repoBoard.integration?.name ?? ""} の先頭に作る（マージリクエストは作らない）`)) return false;
      const deps = await writeDeps(repoBoard.repo);
      const taken = [...repoBoard.candidates, ...repoBoard.families.map((f) => f.family.name)];
      const out = await startIssue(repoBoard.repo, issue.number, repoBoard.seen ?? null, taken, deps);
      result.dataset.kind = out.kind === "started" ? "written" : out.kind;
      result.className = `notice ${out.kind === "started" ? "ok" : "error"}`;
      result.textContent =
        out.kind === "started"
          ? `親のブランチ ${out.name} を作った（${out.head.slice(0, 7)}）。エージェントに ${out.name} で作業を始めるよう頼んでください`
          : `始めなかった: ${out.message}`;
      return true;
    });
  },
  dismiss(repoBoard: RepoBoard, family: string) {
    void (async () => {
      if (!window.confirm(`${family} の要確認を外す。ホストの履歴を見て、親のブランチの置き場が正しいと確かめられたときだけ外してください`)) return;
      const all = await readAttention();
      const key = repoKey(repoBoard.repo);
      if (all[key]) delete all[key][family];
      await chrome.storage.local.set({ attention: all });
      drawRepo(boards.get(key) ?? repoBoard, all);
    })();
  },
};

/** 書く操作を 1 つずつ走らせる。書いた（書こうとした）ときだけ、ボードを読み直して描き直す */
async function act(fn: () => Promise<boolean>): Promise<void> {
  if (busy) return;
  busy = true;
  const buttons = [...document.querySelectorAll<HTMLButtonElement>("button.action")];
  for (const b of buttons) b.disabled = true;
  document.body.dataset.state = "writing";
  let tried = true;
  try {
    tried = await fn();
  } catch (err) {
    result.dataset.kind = "failed";
    result.textContent = `書けなかった: ${(err as Error).message ?? String(err)}`;
  } finally {
    busy = false;
    if (tried) {
      await refresh(false);
    } else {
      for (const b of buttons) b.disabled = false;
      document.body.dataset.state = "done";
    }
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
  repos = readRepos(got.repos, HOSTS);
  if (repos.length === 0) {
    status.textContent = "リポジトリが登録されていない。設定画面で登録してください";
    document.body.dataset.state = "done";
    return;
  }
  status.textContent = "Python（Pyodide）を起動している…";
  if (worker === null) worker = startWorker();
  const t = await worker.init();
  status.textContent = `Pyodide ${t.pyodide}: 起動 ${t.boot_ms} ms・読み込み ${t.import_ms} ms`;
  status.dataset.bootMs = String(t.boot_ms);
  status.dataset.importMs = String(t.import_ms);
  status.dataset.violations = String(t.violations.length);
  const cache = await blobCache();
  const attention = await readAttention();
  for (const repo of repos) {
    const stats = newStats();
    const board = await collectRepo(repo, { call: hostCall(repo.host, stats), py: worker.call, cache, now: () => new Date(), stats, workspace: workspaceOf(repo, stats) });
    boards.set(repoKey(repo), board);
    drawRepo(board, attention);
  }
  await drawBanner(repos);
  document.body.dataset.state = "done";
}

document.getElementById("refresh")?.addEventListener("click", () => void refresh());
document.getElementById("options")?.addEventListener("click", () => void chrome.runtime.openOptionsPage());
refresh().catch((err) => {
  status.textContent = `ボードを読み込めなかった: ${(err as Error).message ?? String(err)}`;
  document.body.dataset.state = "error";
});
