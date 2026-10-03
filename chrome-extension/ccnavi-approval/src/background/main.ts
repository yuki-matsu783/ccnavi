/**
 * service worker。PAT を持ち、ホストの API を呼ぶのはここだけ（ADR-0093 の 5.5 の 4）。
 *
 * PAT は `chrome.storage.local` に平文で置く（D23）。鍵は `token:<ホスト>` で、画面の側は
 * この鍵を読まない（画面が読むのは `repos` だけ）。PAT の期限は `tokenMeta:<ホスト>` に控え（D25）、
 * 1 日 1 回（`chrome.alarms`）比べて、切れる 7 日前からバッジに出す。
 */
import { ALARM, ensureDailyAlarm } from "../core/alarm.js";
import { badgeText, expiryNotice, type TokenMeta } from "../core/expiry.js";
import { dispatch, type Deps } from "../core/protocol.js";
import { readRepos } from "../core/settings.js";

const HOSTS = __CCNAVI_HOSTS__;
const tokenKey = (host: string) => `token:${host}`;
const metaKey = (host: string) => `tokenMeta:${host}`;

async function getToken(host: string): Promise<string> {
  const got = await chrome.storage.local.get(tokenKey(host));
  const v = got[tokenKey(host)];
  return typeof v === "string" ? v : "";
}

async function getMeta(host: string): Promise<TokenMeta> {
  const got = await chrome.storage.local.get(metaKey(host));
  const v = got[metaKey(host)];
  return v && typeof v === "object" ? (v as TokenMeta) : {};
}

/** 期限をバッジに出す。PAT が無いホストは数えない */
async function refreshBadge(): Promise<void> {
  const notices = [];
  for (const h of HOSTS) {
    if ((await getToken(h.id)) === "") continue;
    notices.push(expiryNotice(h.id, await getMeta(h.id), new Date()));
  }
  const text = badgeText(notices);
  await chrome.action.setBadgeText({ text });
  if (text) await chrome.action.setBadgeBackgroundColor({ color: text === "PAT!" ? "#b3261e" : "#8a5a00" });
  const title = notices.filter((n) => n.level !== "ok").map((n) => n.text);
  await chrome.action.setTitle({ title: ["ccnavi 承認ボードを開く", ...title].join("\n") });
}

const deps: Deps = {
  hosts: HOSTS,
  compat: __CCNAVI_COMPAT__,
  extensionId: chrome.runtime.id,
  base: chrome.runtime.getURL(""),
  fetch: (url, init) => fetch(url, { ...init, credentials: "omit", cache: "no-store", redirect: "error" }),
  getToken,
  async setToken(host, token) {
    await chrome.storage.local.set({ [tokenKey(host)]: token });
  },
  async clearToken(host) {
    await chrome.storage.local.remove(tokenKey(host));
  },
  getMeta,
  async setMeta(host, meta) {
    await chrome.storage.local.set({ [metaKey(host)]: meta });
    await refreshBadge();
  },
  now: () => new Date(),
  async getRepos() {
    const got = await chrome.storage.local.get("repos");
    return readRepos(got.repos, HOSTS);
  },
};

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  dispatch(message, sender, deps).then(sendResponse);
  return true;
});

chrome.action.onClicked.addListener(() => {
  void chrome.tabs.create({ url: chrome.runtime.getURL("board.html") });
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM) void refreshBadge();
});
// 起動し直すたびに作り直さない（周期が数え直しになる）。無いときだけ作る
void ensureDailyAlarm(chrome.alarms);
void refreshBadge();
