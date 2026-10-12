/**
 * 設定画面が service worker と chrome.storage に聞くところ。
 *
 * PAT は service worker に渡して置かせ、この画面では読み返さない（登録済みかと期限だけを聞く）。
 * 通信先は、埋め込んだ一覧と、この画面で足したホスト（`customHosts`）を合わせて読む。
 */
import { CUSTOM_KEY, loadHosts, readCustomHosts } from "../core/custom-hosts.js";
import type { Host } from "../core/hosts.js";
import type { Response } from "../core/protocol.js";
import { readRepos, type RepoConfig } from "../core/settings.js";

/** ビルドに埋め込んだ通信先（組み込み）。設定画面で足したものはここに入らない */
export const BUILT_IN: readonly Host[] = __CCNAVI_HOSTS__;

export async function ask(message: Record<string, unknown>): Promise<Response> {
  return (await chrome.runtime.sendMessage(message)) as Response;
}

/** 組み込みと、設定画面で足したホストの一覧 */
export async function loadAllHosts(): Promise<Host[]> {
  return loadHosts(chrome.storage.local, BUILT_IN);
}

/** 設定画面で足したホストだけ（組み込みは含まない） */
export async function loadCustomHosts(): Promise<Host[]> {
  const got = await chrome.storage.local.get(CUSTOM_KEY);
  return readCustomHosts(got[CUSTOM_KEY], BUILT_IN);
}

/**
 * 保存されている行をそのまま（読めない行も）。足す・外すのときは、対象の行だけを変えて書き戻す。
 * 読めた行だけで書き直すと、形の古い行や一時的に衝突した行が、次の操作で黙って消えるため。
 */
export async function loadRawCustomRows(): Promise<unknown[]> {
  const got = await chrome.storage.local.get(CUSTOM_KEY);
  return Array.isArray(got[CUSTOM_KEY]) ? (got[CUSTOM_KEY] as unknown[]) : [];
}

export async function loadRepos(): Promise<RepoConfig[]> {
  const got = await chrome.storage.local.get("repos");
  return readRepos(got.repos, await loadAllHosts());
}

/** 直前の操作の一言。成功なら ok */
export interface Said {
  readonly text: string;
  readonly ok: boolean;
}
