/**
 * ボードの入口。承認待ちを並べ、承認とレビュー済みを親のブランチへ書く。
 * 依頼済みのフェーズに MR のスレッドを出し、レビュー済みのマーカーを書く。
 * GitLab とプロジェクトのリポジトリも読み、「始める」（issue から親のブランチを作る）を出す。
 * GitLab へ書いた後、元に戻すコミットでも競合が収まらなかった親子のチケットは「要確認」として記録し（`chrome.storage.local` の `attention`）、
 * ユーザが確かめて外すまで出す。
 *
 * 状態と副作用（読み直し・書く操作・要確認・自動の読み直し）は `controller.ts` が持ち、描くのは React（`App.tsx`）。
 * ここは外界（`chrome`・`window`・Worker・IndexedDB）を controller に渡し、`main#boards` に描き始める。
 * 見た目は `style.css`（ビルドが `board.css` に組む）。
 *
 * PAT はこのページに来ない。ホストの API は service worker に名前で頼む。
 */
import { createRoot } from "react-dom/client";

import { loadHosts } from "../core/custom-hosts.js";
import { createRenderer } from "../core/sanitize.js";
import { App } from "./App.js";
import { blobCache } from "./cache.js";
import { createBoardController } from "./controller.js";
import { startWorker } from "./py-client.js";

// 設定画面で足したホストを読んでから組む（開いたあとに足したホストは、開き直すまで出ない）
const hosts = await loadHosts(chrome.storage.local, __CCNAVI_HOSTS__);
const md = createRenderer(window);
const main = document.getElementById("boards") as HTMLElement;
const frame = {
  body: document.body,
  status: document.getElementById("status") as HTMLElement,
  banner: document.getElementById("banner") as HTMLElement,
  result: document.getElementById("result") as HTMLElement,
};

const controller = createBoardController({
  hosts,
  version: __CCNAVI_VERSION__,
  send: (message) => chrome.runtime.sendMessage(message),
  storage: chrome.storage.local,
  openOptions: () => chrome.runtime.openOptionsPage(),
  confirm: (message) => window.confirm(message),
  startWorker,
  blobCache,
  now: () => new Date(),
  editing: () => {
    const active = document.activeElement;
    return active instanceof HTMLInputElement && main.contains(active);
  },
  timers: {
    doc: document,
    now: () => Date.now(),
    setTimeout: (cb, ms) => window.setTimeout(cb, ms),
    clearTimeout: (id) => window.clearTimeout(id as number),
  },
});

createRoot(main, {
  // 欄の境界の外で落ちたら、ルートが外れて何も描かれなくなる。壊れたのか承認待ちが無いのか見分けられるよう、言う
  onUncaughtError: (err) => {
    console.error(err);
    controller.reportRenderError(err);
  },
  onCaughtError: (err) => console.error(err),
}).render(<App controller={controller} md={md} frame={frame} />);
document.getElementById("refresh")?.addEventListener("click", () => void controller.refresh());
document.getElementById("options")?.addEventListener("click", () => controller.openOptions());
controller.start();
