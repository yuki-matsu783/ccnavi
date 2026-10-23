/**
 * 直近の日数: 既定 7 日、ボードの入力欄の保存と検査。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import { renderBoard, renderBoards } from "./helpers/board-dom.js";
import { createRenderer } from "../src/core/sanitize.js";
import { DEFAULT_RECENT_DAYS, MAX_RECENT_DAYS, readRepos, saveRecentDays, type RepoConfig, type StorageArea } from "../src/core/settings.js";
import type { RepoBoard } from "../src/core/snapshot.js";
import { HOSTS } from "./helpers/host.js";

function storage(initial: Record<string, unknown> = {}): StorageArea & { data: Record<string, unknown> } {
  const data: Record<string, unknown> = structuredClone(initial);
  return {
    data,
    async get(keys) {
      const list = keys === null ? Object.keys(data) : Array.isArray(keys) ? keys : [keys];
      return Object.fromEntries(list.filter((k) => k in data).map((k) => [k, structuredClone(data[k])]));
    },
    async set(items) {
      Object.assign(data, structuredClone(items));
    },
  };
}

const row = (repo: string, recentDays?: number) => ({ host: "github.com", owner: "acme", repo, integration: "", ...(recentDays === undefined ? {} : { recentDays }), extraBranches: [], project: "", workspace: "" });
const days = (repos: RepoConfig[]) => repos.map((r) => r.recentDays);

test("CX-T210 検索期間の既定は 7 日", () => {
  assert.equal(DEFAULT_RECENT_DAYS, 7);
});

test("CX-T211 ボードの入力欄からの保存: 範囲の中なら保存し、外れた値・空・小数はエラーで保存しない", async () => {
  const area = storage({ repos: [row("a", 7), row("b", 7)] });
  assert.equal(await saveRecentDays(area, HOSTS, "github.com/acme/b", "14"), 14);
  assert.deepEqual(days(readRepos(area.data.repos, HOSTS)), [7, 14]);
  assert.equal(await saveRecentDays(area, HOSTS, "github.com/acme/b", "0"), 0);
  assert.equal(await saveRecentDays(area, HOSTS, "github.com/acme/b", String(MAX_RECENT_DAYS)), MAX_RECENT_DAYS);
  const before = structuredClone(area.data.repos);
  for (const bad of ["-1", String(MAX_RECENT_DAYS + 1), "1.5", "abc", "", " "]) {
    await assert.rejects(saveRecentDays(area, HOSTS, "github.com/acme/b", bad), /検索期間には、0〜90の整数を入力してください/, bad);
  }
  await assert.rejects(saveRecentDays(area, HOSTS, "github.com/acme/none", "5"), /は登録されていません/);
  assert.deepEqual(area.data.repos, before);
});

test("CX-T212 ボードの入力欄: リポジトリごとに出し、変えると呼び手に文字列を渡す。渡さなければ出さない", () => {
  const dom = new JSDOM("<!doctype html><body></body>");
  const doc = dom.window.document;
  const md = createRenderer(dom.window as unknown as Parameters<typeof createRenderer>[0]);
  const mk = (repo: string, recentDays: number): RepoBoard =>
    ({ repo: { host: "github.com", owner: "acme", repo, integration: "", recentDays, extraBranches: [], project: "", workspace: "" }, error: "取れなかった", families: [], candidates: [], missingExtras: [] }) as unknown as RepoBoard;
  const got: [string, string][] = [];
  const actions = { approve() {}, setRecentDays: (b: RepoBoard, v: string) => void got.push([b.repo.repo, v]) };
  // 2 つの欄は 1 回の描画で同じ文書に並べる（renderBoard を続けて呼ぶと前の欄は文書から外れる）
  const [a, b] = renderBoards(doc, md, [mk("a", 7), mk("b", 30)], actions);
  assert.equal(doc.querySelectorAll("form.recent-days input").length, 2);
  const inputA = a.querySelector<HTMLInputElement>("form.recent-days input")!;
  const inputB = b.querySelector<HTMLInputElement>("form.recent-days input")!;
  assert.ok(doc.body.contains(inputA) && doc.body.contains(inputB));
  assert.equal(inputA.value, "7");
  assert.equal(inputB.value, "30");
  assert.equal(inputB.max, "90");
  inputB.value = "5";
  b.querySelector<HTMLFormElement>("form.recent-days")!.dispatchEvent(new dom.window.Event("submit", { cancelable: true }));
  assert.deepEqual(got, [["b", "5"]]);
  assert.equal(renderBoard(doc, md, mk("c", 7), { approve() {} }).querySelector("form.recent-days"), null);
});
