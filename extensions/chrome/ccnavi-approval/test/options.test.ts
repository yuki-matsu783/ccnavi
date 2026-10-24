/**
 * 設定画面（src/options/。React）を jsdom の上で描く。chrome.runtime.sendMessage は service worker と
 * 同じ `dispatch`（記録の無い PAT の置き場）に、chrome.storage.local は手元の入れ物につなぐ。
 *
 * 実機（Chromium）の試験は CX-T071・CX-T159。ここでは欄の扱いと、状態欄（role="status"）の言い直しを見る。
 */
import { after, afterEach, before, beforeEach, test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { act, createElement, type ComponentType } from "react";
import { createRoot, type Root } from "react-dom/client";

import { loadHosts } from "../src/core/custom-hosts.js";
import type { TokenMeta } from "../src/core/expiry.js";
import { dispatch, type Deps } from "../src/core/protocol.js";
import type { RepoConfig } from "../src/core/settings.js";
import { BASE, EXT_ID, HOSTS, OPTIONS } from "./helpers/host.js";

const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: OPTIONS.url });
const g = globalThis as unknown as Record<string, unknown>;
const saved = new Map<string, PropertyDescriptor | undefined>();

/** jsdom の window の持ち物を globalThis に出す。Node にもある FormData・Event などは jsdom の物で置き換える */
function install(name: string, value: unknown): void {
  if (!saved.has(name)) saved.set(name, Object.getOwnPropertyDescriptor(globalThis, name));
  Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
}

// 試験の中の chrome（service worker と storage）
const storage: Record<string, unknown> = {};
const tokens = new Map<string, string>();
const metas = new Map<string, TokenMeta>();
const sent: Record<string, unknown>[] = [];
/** 通信先の許可の呼び出し（request・remove）と、request に答える値 */
const permissionCalls: { op: string; origins: string[] }[] = [];
const grant = { ok: true };
const NOW = new Date("2026-10-01T00:00:00Z");
const deps: Deps = {
  // 通信先は組み込みと、設定画面で足したもの（storage の customHosts）を合わせる
  getHosts: async () => loadHosts(chromeStub.storage.local, HOSTS),
  extensionId: EXT_ID,
  base: BASE,
  fetch: async () => {
    throw new Error("設定画面の試験では通信しない");
  },
  getToken: async (h) => tokens.get(h) ?? "",
  setToken: async (h, t) => void tokens.set(h, t),
  clearToken: async (h) => void tokens.delete(h),
  getMeta: async (h) => metas.get(h) ?? {},
  setMeta: async (h, m) => void metas.set(h, m),
  now: () => NOW,
  getRepos: async () => [],
};
const chromeStub = {
  runtime: {
    sendMessage: async (message: Record<string, unknown>) => {
      sent.push(message);
      return dispatch(structuredClone(message), OPTIONS, deps);
    },
  },
  permissions: {
    request: async (p: { origins: string[] }) => {
      permissionCalls.push({ op: "request", origins: p.origins });
      return grant.ok;
    },
    remove: async (p: { origins: string[] }) => {
      permissionCalls.push({ op: "remove", origins: p.origins });
      return true;
    },
  },
  storage: {
    local: {
      get: async (keys: string | string[] | null) => {
        const names = keys === null ? Object.keys(storage) : typeof keys === "string" ? [keys] : keys;
        return Object.fromEntries(names.filter((k) => k in storage).map((k) => [k, structuredClone(storage[k])]));
      },
      set: async (items: Record<string, unknown>) => void Object.assign(storage, structuredClone(items)),
      remove: async (keys: string | string[]) => {
        for (const k of typeof keys === "string" ? [keys] : keys) delete storage[k];
      },
    },
  },
};

let App: ComponentType;
let root: Root | undefined;
let container: HTMLElement;

before(async () => {
  const win = dom.window as unknown as Record<string, unknown>;
  for (const name of Object.getOwnPropertyNames(dom.window)) {
    if (name in globalThis || name.startsWith("_")) continue;
    let value: unknown;
    try {
      value = win[name];
    } catch {
      // localStorage など、chrome-extension: のページ（不透明なオリジン）で読めない物は出さない
      continue;
    }
    install(name, value);
  }
  for (const name of ["window", "document", "navigator", "FormData", "Event", "EventTarget", "MutationObserver"]) {
    install(name, win[name]);
  }
  install("chrome", chromeStub);
  install("__CCNAVI_HOSTS__", HOSTS);
  // React の act を試験で使う
  install("IS_REACT_ACT_ENVIRONMENT", true);
  ({ App } = await import("../src/options/App.js"));
});

after(() => {
  for (const [name, desc] of saved) {
    if (desc) Object.defineProperty(globalThis, name, desc);
    else delete g[name];
  }
  dom.window.close();
});

beforeEach(() => {
  for (const k of Object.keys(storage)) delete storage[k];
  tokens.clear();
  metas.clear();
  sent.length = 0;
  permissionCalls.length = 0;
  grant.ok = true;
});

afterEach(async () => {
  const r = root;
  root = undefined;
  if (r) await act(async () => r.unmount());
  container?.remove();
});

/** 頼みの往復（storage と dispatch）が済むまで回す */
async function settle(): Promise<void> {
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

async function mount(): Promise<HTMLElement> {
  container = document.createElement("div");
  document.body.append(container);
  const r = createRoot(container);
  root = r;
  await act(async () => r.render(createElement(App)));
  await settle();
  return container;
}

function $<T extends Element = HTMLElement>(selector: string): T {
  const el = container.querySelector<T>(selector);
  assert.ok(el, `${selector} が無い`);
  return el;
}

function fill(values: Record<string, string>): void {
  for (const [name, value] of Object.entries(values)) {
    $<HTMLInputElement | HTMLSelectElement>(`#repo-form [name=${name}]`).value = value;
  }
}

async function click(selector: string): Promise<void> {
  await act(async () => $<HTMLButtonElement>(selector).click());
  await settle();
}

const submitRepo = () => click("#repo-form button[type=submit]");
const submitToken = () => click("#token-form button[type=submit]");
const text = (selector: string) => $(selector).textContent ?? "";
const storedRepos = () => (storage.repos ?? []) as RepoConfig[];
const TOKEN = "github_pat_options_test_0123456789";

test("CX-T233 設定画面: 実機の試験とボードが読む欄の ID が描画に残り、状態欄は何も言っていない間も role=status で置いてある", async () => {
  await mount();
  for (const id of [
    "repo-form",
    "repo-list",
    "repo-host",
    "repo-project",
    "repo-workspace",
    "repo-msg",
    "token-form",
    "token-list",
    "token-host",
    "token-value",
    "token-expires",
    "token-clear",
    "token-msg",
    "token-links",
  ]) {
    assert.ok(container.querySelector(`#${id}`), `#${id} が無い`);
  }
  for (const name of ["owner", "repo", "integration", "recentDays", "extraBranches", "project", "workspace", "host"]) {
    assert.ok(container.querySelector(`#repo-form [name=${name}]`), `#repo-form [name=${name}] が無い`);
  }
  assert.ok(container.querySelector("#repo-form button[type=submit]"));
  assert.ok(container.querySelector("#token-form button[type=submit]"));
  assert.deepEqual(
    [...$<HTMLSelectElement>("#repo-host").options].map((o) => o.value),
    HOSTS.map((h) => h.id),
  );
  assert.deepEqual(
    [...$<HTMLSelectElement>("#token-host").options].map((o) => o.value),
    HOSTS.map((h) => h.id),
  );
  for (const id of ["repo-msg", "token-msg"]) {
    const msg = $(`#${id}`);
    assert.equal(msg.getAttribute("role"), "status");
    assert.equal(msg.className, "status");
    assert.equal(msg.textContent, "");
  }
  assert.equal($("#repo-list").children.length, 0);
  // PAT の欄: 開いた直後に、ホストごとの登録の有無を service worker に聞く
  assert.deepEqual(text("#token-list"), "github.com（GitHub）: 未登録gitlab.com（GitLab）: 未登録");
  assert.deepEqual(
    sent.map((m) => [m.kind, m.host]),
    HOSTS.map((h) => ["token.status", h.id]),
  );
});

test("CX-T234 設定画面のリポジトリ: 登録すると一覧に出し、プロジェクトはワークスペースを選んでから登録する。外すと一覧と storage から消える", async () => {
  await mount();
  fill({ owner: "acme", repo: "widgets" });
  await submitRepo();
  assert.equal(text("#repo-msg"), "github.com/acme/widgets を登録しました。");
  assert.equal($("#repo-msg").className, "status ok");
  assert.equal(text("#repo-list"), "github.com/acme/widgets（統合先 デフォルトブランチ・直近 7 日）登録を削除");
  assert.deepEqual(storedRepos(), [
    { host: "github.com", owner: "acme", repo: "widgets", integration: "", recentDays: 7, extraBranches: [], project: "", workspace: "" },
  ]);
  // 登録したワークスペースが、プロジェクトのワークスペースの選択肢に出る
  assert.deepEqual(
    [...$<HTMLSelectElement>("#repo-workspace").options].map((o) => o.value),
    ["", "github.com/acme/widgets"],
  );

  // プロジェクト名を入れてワークスペースを選ばないと、登録しない
  fill({ owner: "acme", repo: "app", project: "app", workspace: "" });
  await submitRepo();
  assert.match(text("#repo-msg"), /^プロジェクトのリポジトリには、ワークスペースのリポジトリ（<ホスト>\/<オーナー>\/<リポジトリ>）を選択してください$/);
  assert.equal($("#repo-msg").className, "status error");
  assert.equal(storedRepos().length, 1);

  // 選べば、統合先・日数・指定のブランチと一緒に登録する
  fill({ owner: "acme", repo: "app", integration: "develop", recentDays: "0", extraBranches: "a, b  a", project: "app", workspace: "github.com/acme/widgets" });
  await submitRepo();
  assert.equal(text("#repo-msg"), "github.com/acme/app を登録しました。");
  assert.equal(
    text("#repo-list"),
    "github.com/acme/widgets（統合先 デフォルトブランチ・直近 7 日）登録を削除" +
      "github.com/acme/app（統合先 develop・直近 0 日・指定 a, b・プロジェクト app（ワークスペース github.com/acme/widgets））登録を削除",
  );
  assert.deepEqual(storedRepos()[1], {
    host: "github.com",
    owner: "acme",
    repo: "app",
    integration: "develop",
    recentDays: 0,
    extraBranches: ["a", "b"],
    project: "app",
    workspace: "github.com/acme/widgets",
  });
  // プロジェクトのリポジトリはワークスペースの選択肢に出さない
  assert.deepEqual(
    [...$<HTMLSelectElement>("#repo-workspace").options].map((o) => o.value),
    ["", "github.com/acme/widgets"],
  );

  // 別のタブでワークスペースが外された後に、そのワークスペースのプロジェクトを登録しようとしても止める
  fill({ owner: "acme", repo: "lib", project: "lib", workspace: "github.com/acme/widgets" });
  storage.repos = storedRepos().filter((r) => r.repo !== "widgets");
  await submitRepo();
  assert.equal(text("#repo-msg"), "ワークスペースのリポジトリ github.com/acme/widgets を先に登録してください");
  assert.equal($("#repo-msg").className, "status error");
  assert.deepEqual(
    storedRepos().map((r) => r.repo),
    ["app"],
  );
  // 戻す（画面の一覧は読み直していないので、まだ 2 件のまま）
  storage.repos = [
    { host: "github.com", owner: "acme", repo: "widgets", integration: "", recentDays: 7, extraBranches: [], project: "", workspace: "" },
    ...storedRepos(),
  ];
  assert.equal($("#repo-list").children.length, 2);

  // 外す（読み直した一覧から外し、ほかは残す）
  await click("#repo-list li:nth-child(2) button");
  assert.deepEqual(
    storedRepos().map((r) => r.repo),
    ["widgets"],
  );
  assert.equal(text("#repo-list"), "github.com/acme/widgets（統合先 デフォルトブランチ・直近 7 日）登録を削除");
  await click("#repo-list li button");
  assert.deepEqual(storedRepos(), []);
  assert.equal($("#repo-list").children.length, 0);
});

test("CX-T216 設定画面のリポジトリ: 直近の日数は 0〜90。外れた値は欄の制約で送らず、制約を外して送っても登録しない", async () => {
  await mount();
  const days = $<HTMLInputElement>("#repo-form [name=recentDays]");
  assert.deepEqual([days.type, days.min, days.max, days.value], ["number", "0", "90", "7"]);
  // 欄の制約で止まる（submit が 1 度も起きない）。画面の検査（#repo-msg）まで届いて止まったのではない
  let submits = 0;
  const form = $<HTMLFormElement>("#repo-form");
  const count = (): void => void (submits += 1);
  form.addEventListener("submit", count);
  for (const value of ["91", "-1", "1.5"]) {
    fill({ owner: "acme", repo: "widgets", recentDays: value });
    assert.equal(days.checkValidity(), false, value);
    await submitRepo();
    assert.equal(submits, 0, value);
    assert.equal(text("#repo-msg"), "", value);
    assert.equal(storage.repos, undefined, value);
  }
  form.removeEventListener("submit", count);
  // ブラウザの検査を通らずに送られても（novalidate）、画面の検査で止める
  $<HTMLFormElement>("#repo-form").noValidate = true;
  for (const value of ["91", "-1", "1.5"]) {
    fill({ owner: "acme", repo: "widgets", recentDays: value });
    await submitRepo();
    assert.equal(text("#repo-msg"), "検索期間には、0〜90 の整数を入力してください。", value);
    assert.equal($("#repo-msg").className, "status error");
    assert.equal(storage.repos, undefined, value);
  }
  // 境目の 90 は登録する
  fill({ owner: "acme", repo: "widgets", recentDays: "90" });
  await submitRepo();
  assert.equal(text("#repo-msg"), "github.com/acme/widgets を登録しました。");
  assert.equal(storedRepos()[0]?.recentDays, 90);
  assert.equal(text("#repo-list"), "github.com/acme/widgets（統合先 デフォルトブランチ・直近 90 日）登録を削除");
});

test("CX-T217 設定画面の PAT: 登録すると登録済みと期限を出し、欄を空にして画面に読み返さない。消すと未登録に戻す。形の違う PAT は断る", async () => {
  await mount();
  const value = $<HTMLInputElement>("#token-value");
  // PAT の欄は伏せ字で、フォームの値（FormData）にも入らない
  assert.deepEqual([value.type, value.getAttribute("autocomplete"), value.getAttribute("name")], ["password", "off", null]);

  value.value = "short";
  await submitToken();
  assert.equal(text("#token-msg"), "PAT の形が正しくありません（英数字と _ - . で 8〜255 字）");
  assert.equal($("#token-msg").className, "status error");
  assert.equal(value.value, "");
  assert.equal(tokens.size, 0);
  assert.match(text("#token-list"), /github\.com（GitHub）: 未登録/);

  // 期限を入れずに登録: 期限が分からないと出す
  value.value = TOKEN;
  await submitToken();
  assert.equal(text("#token-msg"), "github.com の PAT を登録しました。差し替えた場合は、古いトークンをホスト側で失効させてください。");
  assert.equal($("#token-msg").className, "status ok");
  assert.equal(tokens.get("github.com"), TOKEN);
  assert.equal(value.value, "");
  assert.match(text("#token-list"), /github\.com（GitHub）: 登録済み（github\.com の PAT の期限が不明です（/);
  assert.ok(!container.innerHTML.includes(TOKEN));
  assert.ok(!document.body.innerHTML.includes(TOKEN));

  // GitLab に期限つきで登録: 切れる 7 日前からは残りの日数を出す
  $<HTMLSelectElement>("#token-host").value = "gitlab.com";
  value.value = TOKEN;
  $<HTMLInputElement>("#token-expires").value = "2026-10-03";
  await submitToken();
  assert.equal(text("#token-msg"), "gitlab.com の PAT を登録しました。差し替えた場合は、古いトークンをホスト側で失効させてください。");
  assert.deepEqual(metas.get("gitlab.com"), { manual: "2026-10-03" });
  assert.equal([value.value, $<HTMLInputElement>("#token-expires").value].join(""), "");
  assert.match(text("#token-list"), /gitlab\.com（GitLab）: 登録済み（gitlab\.com の PAT はあと 3 日で切れます（2026-10-03）。/);
  assert.ok(!container.innerHTML.includes(TOKEN));

  // 送った頼み: PAT は token.set にだけ載る
  const sets = sent.filter((m) => m.kind === "token.set");
  assert.deepEqual(
    sets.map((m) => [m.host, m.expires]),
    [
      ["github.com", ""],
      ["github.com", ""],
      ["gitlab.com", "2026-10-03"],
    ],
  );
  assert.ok(sent.filter((m) => m.kind !== "token.set").every((m) => !JSON.stringify(m).includes(TOKEN)));

  // 消す: 選んだホストだけ
  await click("#token-clear");
  assert.equal(text("#token-msg"), "gitlab.com の PAT を削除しました。ホスト側でも失効させてください。");
  assert.equal($("#token-msg").className, "status ok");
  assert.deepEqual([...tokens.keys()], ["github.com"]);
  assert.match(text("#token-list"), /gitlab\.com（GitLab）: 未登録/);
  assert.match(text("#token-list"), /github\.com（GitHub）: 登録済み/);
});

/** 状態欄の中身が作り直されたか（子の足し引き）を数える */
function watchStatus(id: string): { readonly changes: () => number; readonly stop: () => void } {
  const records: MutationRecord[] = [];
  const observer = new MutationObserver((list) => records.push(...list));
  observer.observe($(`#${id}`), { childList: true, subtree: true, characterData: true });
  return {
    changes: () => {
      records.push(...observer.takeRecords());
      const n = records.filter((r) => r.type === "childList" && r.addedNodes.length > 0).length;
      records.length = 0;
      return n;
    },
    stop: () => observer.disconnect(),
  };
}

test("CX-T218 設定画面の状態欄: 同じ一言が続いても、言い直すたびに中身を作り直す（読み上げをやり直させる）", async () => {
  await mount();
  // リポジトリ: 同じ登録を 2 回
  const repo = watchStatus("repo-msg");
  fill({ owner: "acme", repo: "widgets" });
  await submitRepo();
  const first = $("#repo-msg").firstElementChild;
  assert.ok(repo.changes() > 0);
  fill({ owner: "acme", repo: "widgets" });
  await submitRepo();
  assert.equal(text("#repo-msg"), "github.com/acme/widgets を登録しました。");
  assert.ok(repo.changes() > 0, "同じ一言の 2 回目で状態欄が作り直されていない");
  assert.notEqual($("#repo-msg").firstElementChild, first);
  // 同じ誤りが続いても同じ
  fill({ owner: "acme", repo: "widgets", recentDays: "91" });
  $<HTMLFormElement>("#repo-form").noValidate = true;
  await submitRepo();
  repo.changes();
  const err = $("#repo-msg").firstElementChild;
  await submitRepo();
  assert.equal(text("#repo-msg"), "検索期間には、0〜90 の整数を入力してください。");
  assert.ok(repo.changes() > 0, "同じ誤りの 2 回目で状態欄が作り直されていない");
  assert.notEqual($("#repo-msg").firstElementChild, err);
  repo.stop();

  // PAT: 同じ登録を 2 回、同じ消すを 2 回
  const token = watchStatus("token-msg");
  for (const step of [
    async () => {
      $<HTMLInputElement>("#token-value").value = TOKEN;
      await submitToken();
    },
    () => click("#token-clear"),
  ]) {
    await step();
    token.changes();
    const before = $("#token-msg").firstElementChild;
    const said = text("#token-msg");
    await step();
    assert.equal(text("#token-msg"), said);
    assert.ok(token.changes() > 0, `同じ一言（${said}）の 2 回目で状態欄が作り直されていない`);
    assert.notEqual($("#token-msg").firstElementChild, before);
  }
  token.stop();
});

const fillHost = (kind: string, url: string) => {
  $<HTMLSelectElement>("#host-kind").value = kind;
  $<HTMLInputElement>("#host-url").value = url;
};
const submitHost = () => click("#host-form button[type=submit]");
const hostList = () => text("#host-list");
const EMPTY_HOSTS = "追加のホストは登録されていません（GitHub.com と GitLab.com は標準で利用できます）。";

test("CX-T240 設定画面の通信先: localhost:8080 の GitLab を許可を求めてから足す。PAT の欄とリポジトリの選び先に出し、外すと PAT と許可も消える", async () => {
  await mount();
  assert.equal(hostList(), EMPTY_HOSTS);
  fillHost("gitlab", "http://localhost:8080/");
  await submitHost();
  assert.deepEqual(permissionCalls, [{ op: "request", origins: ["http://localhost/*"] }]);
  assert.equal(text("#host-msg"), "http://localhost:8080（GitLab）を追加しました。「PAT」欄でトークンを登録してください。");
  assert.equal($("#host-msg").className, "status ok");
  assert.deepEqual(storage.customHosts, [{ kind: "gitlab", url: "http://localhost:8080" }]);
  assert.equal(hostList(), "http://localhost:8080（GitLab）削除");
  const ids = ["github.com", "gitlab.com", "localhost_8080_http"];
  assert.deepEqual(
    [...$<HTMLSelectElement>("#token-host").options].map((o) => o.value),
    ids,
  );
  assert.deepEqual(
    [...$<HTMLSelectElement>("#repo-host").options].map((o) => o.value),
    ids,
  );
  // PAT を登録してから外す（外すと PAT は消え、許可も外す）
  $<HTMLSelectElement>("#token-host").value = "localhost_8080_http";
  $<HTMLInputElement>("#token-value").value = TOKEN;
  await submitToken();
  assert.equal(tokens.get("localhost_8080_http"), TOKEN);
  await click("#host-list button");
  assert.equal(tokens.has("localhost_8080_http"), false);
  assert.deepEqual(storage.customHosts, []);
  assert.deepEqual(permissionCalls.at(-1), { op: "remove", origins: ["http://localhost/*"] });
  assert.equal(text("#host-msg"), "localhost_8080_http と、このブラウザに保存されていた PAT を削除しました。ホスト側でも PAT を失効させてください。");
  assert.equal(hostList(), EMPTY_HOSTS);
});

test("CX-T241 設定画面の通信先: 許可を断られたら足さない。https でない・組み込みと同じ名前・パスつきは足さずに理由を出す", async () => {
  await mount();
  grant.ok = false;
  fillHost("gitlab", "https://gitlab.example.com");
  await submitHost();
  assert.match(text("#host-msg"), /許可しないと追加できません/);
  assert.equal($("#host-msg").className, "status error");
  assert.equal(storage.customHosts, undefined);
  assert.equal(hostList(), EMPTY_HOSTS);

  grant.ok = true;
  fillHost("gitlab", "http://gitlab.example.com");
  await submitHost();
  assert.match(text("#host-msg"), /https のみ指定できます/);

  fillHost("gitlab", "https://gitlab.com");
  await submitHost();
  assert.match(text("#host-msg"), /登録済みか、標準の通信先と名前が重複しています。/);

  fillHost("gitlab", "https://gitlab.example.com/gitlab");
  await submitHost();
  assert.match(text("#host-msg"), /オリジンのみ/);
  assert.equal(storage.customHosts, undefined);
});

test("CX-T242 設定画面の通信先: リポジトリが残っているホストは外さない（先にリポジトリを外させる）", async () => {
  await mount();
  fillHost("gitlab", "http://localhost:8080");
  await submitHost();
  storage.repos = [{ host: "localhost_8080_http", owner: "acme", repo: "widgets", integration: "", recentDays: 7, extraBranches: [], project: "", workspace: "" }];
  await click("#host-list button");
  assert.equal(text("#host-msg"), "localhost_8080_http にリポジトリが登録されています。先にリポジトリの登録を削除してから、ホストを削除してください。");
  assert.deepEqual(storage.customHosts, [{ kind: "gitlab", url: "http://localhost:8080" }]);
  assert.equal(permissionCalls.some((c) => c.op === "remove"), false);
});

test("CX-T250 設定画面の通信先: 同じ許可を別の行が使っているあいだは外しても許可を外さない。読めない行は足す・外すのあとも残す", async () => {
  await mount();
  storage.customHosts = [{ kind: "x", url: "ftp://old.example.com" }];
  fillHost("gitlab", "https://gitlab.example.com");
  await submitHost();
  fillHost("gitlab", "https://gitlab.example.com:8443");
  await submitHost();
  assert.equal(permissionCalls.filter((c) => c.op === "request").length, 2);
  // 8443 を外す。同じ許可（https://gitlab.example.com/*）はもう 1 つの行が使っているので、許可は外さない
  const rows = [...container.querySelectorAll("#host-list button")];
  await act(async () => (rows[1] as HTMLButtonElement).click());
  await settle();
  assert.equal(permissionCalls.some((c) => c.op === "remove"), false);
  // 読めない行は残っている
  assert.deepEqual(storage.customHosts, [{ kind: "x", url: "ftp://old.example.com" }, { kind: "gitlab", url: "https://gitlab.example.com" }]);
});
