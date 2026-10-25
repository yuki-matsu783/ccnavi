/**
 * service worker。PAT を持ち、ホストの API を呼ぶのはここだけ。画面で XSS が起きても PAT に届きにくくするため。
 *
 * PAT は `chrome.storage.local` に平文で置く（一度の登録で使い続けるため。対策するのは XSS だけ）。キーは `token:<ホスト>` で、画面の側は
 * このキーを読まない（画面が読むのは `repos` だけ）。PAT の期限は `tokenMeta:<ホスト>` に記録し、
 * 1 日 1 回（`chrome.alarms`）比べて、切れる 7 日前からバッジに出す。
 */
import { ALARM, ensureDailyAlarm } from "../core/alarm.js";
import { loadHosts } from "../core/custom-hosts.js";
import { badgeText, expiryNotice, type TokenMeta } from "../core/expiry.js";
import { dispatch, type Deps } from "../core/protocol.js";
import { readRepos } from "../core/settings.js";

/** ビルドに埋め込んだ通信先。設定画面で足したものは `loadHosts` が `chrome.storage.local` から合わせる */
const BUILT_IN = __CCNAVI_HOSTS__;
const hosts = () => loadHosts(chrome.storage.local, BUILT_IN);
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
  for (const h of await hosts()) {
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
  getHosts: hosts,
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
    return readRepos(got.repos, await hosts());
  },
};

/** 失敗の文面。PAT の値は出さない（ログを共有しても漏れないように） */
function describe(err: unknown): string {
  return err instanceof Error ? `${err.name}: ${err.message}` : String(err);
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  dispatch(message, sender, deps).then(sendResponse, (err: unknown) => {
    // dispatch は投げない作りだが、投げたときに画面が応答を待ち続けないよう、失敗として返す
    console.error(`ccnavi: 要求を処理できなかった: ${describe(err)}`);
    sendResponse({ ok: false, error: "service worker で予期しない失敗が起きた" });
  });
  return true;
});

chrome.action.onClicked.addListener(() => {
  void chrome.tabs.create({ url: chrome.runtime.getURL("board.html") });
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM) refreshBadge().catch((err: unknown) => console.error(`ccnavi: バッジを更新できなかった: ${describe(err)}`));
});
// 起動し直すたびに作り直さない（周期が数え直しになる）。無いときだけ作る
ensureDailyAlarm(chrome.alarms).catch((err: unknown) => console.error(`ccnavi: 毎日のアラームを作れなかった: ${describe(err)}`));
refreshBadge().catch((err: unknown) => console.error(`ccnavi: バッジを更新できなかった: ${describe(err)}`));
