/**
 * 画面の HTML を happy-dom に読み込み、埋め込んだスクリプトを実際に動かすための土台。
 *
 * 画面のスクリプトは文字列で HTML に埋めてあり、型検査が効かない。イベントの付け忘れや
 * 無い id への参照は、DOM で動かして初めて分かる。ここでは VS Code の Webview が渡す
 * `acquireVsCodeApi` を差し替え、postMessage と state を控えて、テストから読めるようにする。
 *
 * happy-dom は innerHTML で入れた script を実行しないので、CSP の meta を外したうえで
 * 本文を入れ、JSON でない script だけを順に window.eval で走らせる。
 * happy-dom で動かないもの（今のところ無い）だけ jsdom に逃がす方針。
 */
import type { Document, Element, HTMLElement, Window } from "happy-dom" with { "resolution-mode": "import" };

// happy-dom は ESM だけなので、CommonJS のテストからは動的 import で読む。
const happyDom = import("happy-dom");

export interface Posted {
  readonly type: string;
  readonly [key: string]: unknown;
}

export interface DomPage {
  readonly window: Window;
  readonly document: Document;
  /** 画面が vscode.postMessage で送ったもの（古い順） */
  readonly posted: Posted[];
  /** 画面が vscode.setState で最後に書いたもの */
  state(): unknown;
  /** 拡張側からのメッセージを画面に届け、処理が終わるまで待つ */
  send(message: unknown): Promise<void>;
  /** 欄に文字を入れて input イベントを流す */
  type(element: Element, value: string): void;
  /** 要素を押す（click イベント） */
  click(element: Element): void;
  /** セレクタで 1 つ取る。無ければ落とす */
  one<T extends Element = HTMLElement>(selector: string): T;
  /** セレクタで全部取る */
  all<T extends Element = HTMLElement>(selector: string): T[];
  /** 保留中の処理（タイマー・イベント）を流す */
  settle(): Promise<void>;
  close(): Promise<void>;
}

const CSP = /<meta http-equiv="Content-Security-Policy"[^>]*>/;

export async function loadPage(html: string, initialState?: unknown): Promise<DomPage> {
  const { Window } = await happyDom;
  const window = new Window({ url: "vscode-webview://ccnavi/" });
  const posted: Posted[] = [];
  let state: unknown = initialState;
  (window as unknown as { acquireVsCodeApi: () => unknown }).acquireVsCodeApi = () => ({
    // 画面の中の配列やオブジェクトは happy-dom 側の realm のもので、strict な deepEqual が
    // 「構造は同じだが参照が違う」と落とす。JSON で写してこちらの realm に移す。
    postMessage: (message: Posted) => {
      posted.push(JSON.parse(JSON.stringify(message)) as Posted);
    },
    getState: () => state,
    setState: (next: unknown) => {
      state = JSON.parse(JSON.stringify(next ?? null));
    },
  });
  const inner = html
    .replace(CSP, "")
    .replace(/^\s*<!DOCTYPE html>\s*<html[^>]*>/i, "")
    .replace(/<\/html>\s*$/i, "");
  const document = window.document;
  document.documentElement.innerHTML = inner;
  await window.happyDOM.waitUntilComplete();
  for (const script of Array.from(document.querySelectorAll("script"))) {
    if (script.getAttribute("type")) {
      continue;
    }
    (window as unknown as { eval: (code: string) => unknown }).eval(script.textContent ?? "");
  }
  const settle = async (): Promise<void> => {
    await window.happyDOM.waitUntilComplete();
    await new Promise((resolve) => setTimeout(resolve, 0));
  };
  return {
    window,
    document,
    posted,
    state: () => state,
    async send(message) {
      window.dispatchEvent(new window.MessageEvent("message", { data: message }));
      await settle();
    },
    type(element, value) {
      (element as unknown as { value: string }).value = value;
      element.dispatchEvent(new window.Event("input", { bubbles: true }));
      element.dispatchEvent(new window.Event("change", { bubbles: true }));
    },
    click(element) {
      (element as HTMLElement).click();
    },
    one<T extends Element = HTMLElement>(selector: string): T {
      const found = document.querySelector(selector);
      if (found === null) {
        throw new Error(`見つからない: ${selector}`);
      }
      return found as unknown as T;
    },
    all<T extends Element = HTMLElement>(selector: string): T[] {
      return Array.from(document.querySelectorAll(selector)) as unknown as T[];
    },
    settle,
    async close() {
      await window.happyDOM.close();
    },
  };
}
