/**
 * ボード。承認待ちを並べ、承認と承認の取り下げを親のブランチへ書く。
 * 依頼済みのフェーズに MR のスレッドを出し、レビュー済みのマーカーを書く。
 * GitLab とプロジェクトのリポジトリも読み、「始める」（issue から親のブランチを作る）を出す。
 * GitLab へ書いた後、元に戻すコミットでも競合が収まらなかった親子のチケットは「要確認」として記録し（`chrome.storage.local` の `attention`）、
 * ユーザが確かめて外すまで出す。
 *
 * 状態と副作用（読み直し・書く操作・要確認・自動の読み直し）は `controller.ts` が持つ。ここは外界（`chrome`・
 * `window`・Worker・IndexedDB）を渡し、状態が変わるたびに変わった所だけを描き直す。
 *
 * PAT はこのページに来ない。ホストの API は service worker に名前で頼む。
 */
import { renderRepo } from "../core/render.js";
import { createRenderer } from "../core/sanitize.js";
import { blobCache } from "./cache.js";
import { createBoardController, type BoardSnapshot } from "./controller.js";
import { startWorker } from "./py-client.js";

const md = createRenderer(window);
const main = document.getElementById("boards") as HTMLElement;
const status = document.getElementById("status") as HTMLElement;
const banner = document.getElementById("banner") as HTMLElement;
const result = document.getElementById("result") as HTMLElement;

const controller = createBoardController({
  hosts: __CCNAVI_HOSTS__,
  version: __CCNAVI_VERSION__,
  send: (message) => chrome.runtime.sendMessage(message),
  storage: chrome.storage.local,
  openOptions: () => chrome.runtime.openOptionsPage(),
  confirm: (message) => window.confirm(message),
  prompt: (message, value) => window.prompt(message, value),
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

/** 書く操作の間だけ押せなくしたボタン。書かずに終わったら戻す（書いたら読み直しで描き直すので、そのまま捨てる） */
let disabled: HTMLButtonElement[] | null = null;
let drawn: BoardSnapshot | null = null;

function setText(node: HTMLElement, text: string): void {
  if (node.textContent !== text) node.textContent = text;
}

function draw(s: BoardSnapshot): void {
  const prev = drawn;
  drawn = s;
  if (s.busy && disabled === null) {
    disabled = [...document.querySelectorAll<HTMLButtonElement>("button.action")];
    for (const b of disabled) b.disabled = true;
  }

  // リポジトリの欄。変わった欄だけ差し替え（無ければ末尾に足す）、無くなった欄を消す
  if (prev?.sections !== s.sections) {
    if (s.sections.size === 0) main.replaceChildren();
    for (const [key, section] of s.sections) {
      if (prev?.sections.get(key) === section) continue;
      const node = renderRepo(document, md, section.board, controller.actions, { attention: section.attention, issues: section.issues });
      const old = main.querySelector(`section.repo[data-repo="${CSS.escape(key)}"]`);
      if (old) old.replaceWith(node);
      else main.append(node);
    }
    for (const node of main.querySelectorAll<HTMLElement>("section.repo")) if (!s.sections.has(node.dataset.repo ?? "")) node.remove();
  }

  if (prev?.status !== s.status) {
    setText(status, s.status.text);
    if (s.status.bootMs !== undefined) status.dataset.bootMs = String(s.status.bootMs);
    if (s.status.importMs !== undefined) status.dataset.importMs = String(s.status.importMs);
    if (s.status.violations !== undefined) status.dataset.violations = String(s.status.violations);
  }

  if (prev?.banner !== s.banner) {
    banner.replaceChildren(
      ...s.banner.map((line) => {
        const p = document.createElement("p");
        p.className = line.className;
        p.dataset.level = line.level;
        p.textContent = line.text;
        return p;
      }),
    );
  }

  if (prev?.result !== s.result) {
    if (s.result.kind === undefined) delete result.dataset.kind;
    else result.dataset.kind = s.result.kind;
    if (result.className !== s.result.className) result.className = s.result.className;
    setText(result, s.result.text);
  }

  if (document.body.dataset.state !== s.state) document.body.dataset.state = s.state;

  if (!s.busy && s.state !== "writing" && disabled !== null) {
    for (const b of disabled) b.disabled = false;
    disabled = null;
  }
}

controller.subscribe(() => draw(controller.snapshot()));
document.getElementById("refresh")?.addEventListener("click", () => void controller.refresh());
document.getElementById("options")?.addEventListener("click", () => controller.openOptions());
controller.start();
