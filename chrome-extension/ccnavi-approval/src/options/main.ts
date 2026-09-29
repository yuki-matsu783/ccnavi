/**
 * 設定画面。リポジトリ（統合先の名前・直近 N 日・指定のブランチ）と PAT を登録する。
 *
 * PAT は service worker に渡して置かせ、この画面では読み返さない（登録済みかだけを聞く）。
 * 通信先は焼き込んだ一覧から選ぶだけで、ここで足せない（D24）。
 */
import type { Response } from "../core/protocol.js";
import { normalizeRepo, readRepos, repoKey, type RepoConfig } from "../core/settings.js";

const HOSTS = __CCNAVI_HOSTS__;
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

async function ask(message: Record<string, unknown>): Promise<Response> {
  return (await chrome.runtime.sendMessage(message)) as Response;
}

async function loadRepos(): Promise<RepoConfig[]> {
  const got = await chrome.storage.local.get("repos");
  return readRepos(got.repos, HOSTS);
}

function say(id: string, text: string, ok: boolean): void {
  const node = $(id);
  node.textContent = text;
  node.className = ok ? "notice ok" : "notice error";
}

async function drawRepos(): Promise<void> {
  const list = $<HTMLUListElement>("repo-list");
  list.replaceChildren();
  for (const r of await loadRepos()) {
    const li = document.createElement("li");
    const integ = r.integration === "" ? "デフォルトブランチ" : r.integration;
    const extra = r.extraBranches.length > 0 ? `・指定 ${r.extraBranches.join(", ")}` : "";
    li.textContent = `${repoKey(r)}（統合先 ${integ}・直近 ${r.recentDays} 日${extra}） `;
    const del = document.createElement("button");
    del.type = "button";
    del.textContent = "外す";
    del.addEventListener("click", async () => {
      const rest = (await loadRepos()).filter((o) => repoKey(o) !== repoKey(r));
      await chrome.storage.local.set({ repos: rest });
      await drawRepos();
    });
    li.append(del);
    list.append(li);
  }
}

async function drawTokens(): Promise<void> {
  const list = $<HTMLUListElement>("token-list");
  list.replaceChildren();
  for (const h of HOSTS) {
    const res = await ask({ kind: "token.status", host: h.id });
    const set = res.ok && (res.value as { set: boolean }).set;
    const li = document.createElement("li");
    li.textContent = `${h.id}: ${set ? "登録済み" : "未登録"}${h.kind === "gitlab" ? "（GitLab の読み取りは段階 5）" : ""}`;
    list.append(li);
  }
}

function fillHosts(select: HTMLSelectElement): void {
  for (const h of HOSTS) {
    const opt = document.createElement("option");
    opt.value = h.id;
    opt.textContent = h.id;
    select.append(opt);
  }
}

fillHosts($<HTMLSelectElement>("repo-host"));
fillHosts($<HTMLSelectElement>("token-host"));

$<HTMLFormElement>("repo-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = new FormData(e.target as HTMLFormElement);
  try {
    const repo = normalizeRepo(Object.fromEntries(form.entries()) as Record<string, unknown>, HOSTS);
    const rest = (await loadRepos()).filter((o) => repoKey(o) !== repoKey(repo));
    await chrome.storage.local.set({ repos: [...rest, repo] });
    say("repo-msg", `${repoKey(repo)} を登録した`, true);
    await drawRepos();
  } catch (err) {
    say("repo-msg", (err as Error).message, false);
  }
});

$<HTMLFormElement>("token-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target as HTMLFormElement;
  const host = String(new FormData(form).get("host") ?? "");
  const input = $<HTMLInputElement>("token-value");
  const res = await ask({ kind: "token.set", host, token: input.value.trim() });
  input.value = "";
  say("token-msg", res.ok ? `${host} の PAT を登録した。差し替えたなら古いトークンをホストで失効させる` : res.error, res.ok);
  await drawTokens();
});

$<HTMLButtonElement>("token-clear").addEventListener("click", async () => {
  const host = $<HTMLSelectElement>("token-host").value;
  const res = await ask({ kind: "token.clear", host });
  say("token-msg", res.ok ? `${host} の PAT を消した。ホストでも失効させる` : res.error, res.ok);
  await drawTokens();
});

const links = $<HTMLUListElement>("token-links");
for (const h of HOSTS) {
  const li = document.createElement("li");
  const a = document.createElement("a");
  a.href = h.kind === "github" ? `${h.web}/settings/personal-access-tokens/new` : `${h.web}/-/user_settings/personal_access_tokens`;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.textContent = `${h.id} で PAT を作る`;
  li.append(a);
  links.append(li);
}

void drawRepos();
void drawTokens();
