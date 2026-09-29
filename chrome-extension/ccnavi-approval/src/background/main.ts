/**
 * service worker。PAT を持ち、ホストの API を呼ぶのはここだけ（ADR-0093 の 5.5 の 4）。
 *
 * PAT は `chrome.storage.local` に平文で置く（D23）。鍵は `token:<ホスト>` で、画面の側は
 * この鍵を読まない（画面が読むのは `repos` だけ）。
 */
import { dispatch, type Deps } from "../core/protocol.js";

const HOSTS = __CCNAVI_HOSTS__;
const tokenKey = (host: string) => `token:${host}`;

const deps: Deps = {
  hosts: HOSTS,
  extensionId: chrome.runtime.id,
  base: chrome.runtime.getURL(""),
  fetch: (url, init) => fetch(url, { ...init, credentials: "omit", cache: "no-store", redirect: "error" }),
  async getToken(host) {
    const got = await chrome.storage.local.get(tokenKey(host));
    const v = got[tokenKey(host)];
    return typeof v === "string" ? v : "";
  },
  async setToken(host, token) {
    await chrome.storage.local.set({ [tokenKey(host)]: token });
  },
  async clearToken(host) {
    await chrome.storage.local.remove(tokenKey(host));
  },
};

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  dispatch(message, sender, deps).then(sendResponse);
  return true;
});

chrome.action.onClicked.addListener(() => {
  void chrome.tabs.create({ url: chrome.runtime.getURL("board.html") });
});
