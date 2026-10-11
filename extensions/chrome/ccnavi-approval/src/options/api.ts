/**
 * 設定画面が service worker と chrome.storage に聞くところ。
 *
 * PAT は service worker に渡して置かせ、この画面では読み返さない（登録済みかと期限だけを聞く）。
 * 通信先は埋め込んだ一覧から選ぶだけで、ここで足せない。
 */
import type { Response } from "../core/protocol.js";
import { readRepos, type RepoConfig } from "../core/settings.js";

export const HOSTS = __CCNAVI_HOSTS__;

export async function ask(message: Record<string, unknown>): Promise<Response> {
  return (await chrome.runtime.sendMessage(message)) as Response;
}

export async function loadRepos(): Promise<RepoConfig[]> {
  const got = await chrome.storage.local.get("repos");
  return readRepos(got.repos, HOSTS);
}

/** 直前の操作の一言。成功なら ok */
export interface Said {
  readonly text: string;
  readonly ok: boolean;
}
