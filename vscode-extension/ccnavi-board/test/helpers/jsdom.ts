/**
 * happy-dom で動かないものを走らせる逃げ道（`dom.ts` の方針）。**いま逃がしているのは 1 つだけ**で、
 * 図の点を掴んで離す仕草（`onNodeDragStop`）。
 *
 * happy-dom では、d3-drag が張る待ちが終わらず**テストが固まる**（実測。90 秒で打ち切り）。
 * jsdom では同じ仕草がそのまま通る。逆に jsdom は起動が重いので、**ここへ来るのは happy-dom で
 * 走らないものだけ**にする。普段の画面のテストは `dom.ts` のまま。
 *
 * jsdom にも無いものが 2 つあるので、ここで埋める。
 *
 * - `ResizeObserver`（`dom.ts` の `measure` と同じ理由。点の大きさが測れないと線が 1 本も出ない）
 * - `DOMMatrixReadOnly`（React Flow が点の transform を読むのに使う。無いと例外で止まる）
 *
 * どちらも**大きさと行列を偽るだけ**で、本物の配置はしない。だから、ここで見てよいのは
 * 「動かしたら控えに入るか」までで、**動いた先の座標そのものは見ない**（図の倍率で決まる）。
 */
import type { JSDOM as JSDOMType } from "jsdom" with { "resolution-mode": "import" };

// jsdom は CommonJS なので、そのまま require で読む（happy-dom と違って動的 import は要らない）
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { JSDOM } = require("jsdom") as { JSDOM: typeof JSDOMType };

const CSP = /<meta http-equiv="Content-Security-Policy"[^>]*>/;

/** 偽る大きさ。`dom.ts` の `SIZES` と同じ数字にしてある */
const SIZES: readonly [string, number, number][] = [
  [".react-flow__node", 170, 60],
  [".react-flow", 800, 480],
  [".graph", 800, 480],
];

export interface JsdomPage {
  /** 画面が vscode.setState で最後に書いたもの */
  state(): unknown;
  /** 画面が vscode.postMessage で送ったもの（古い順） */
  readonly posted: { readonly type: string }[];
  /** セレクタで 1 つ取る。無ければ落とす */
  one(selector: string): Element;
  /** セレクタで全部取る */
  all(selector: string): Element[];
  /**
   * 要素を掴んで離す（`dx`・`dy` は画面の px）。d3-drag は mousedown を要素で、
   * mousemove と mouseup を window で受けるので、そのとおりに流す。
   * **動いた先の座標は約束しない**（図の倍率で決まる）
   */
  drag(element: Element, dx: number, dy: number): Promise<void>;
  /** 描き直しとタイマーが片付くまで待つ */
  settle(): Promise<void>;
  close(): void;
}

export async function loadPageJsdom(html: string, initialState?: unknown): Promise<JsdomPage> {
  let state: unknown = initialState;
  const posted: { readonly type: string }[] = [];
  const dom = new JSDOM(html.replace(CSP, ""), {
    runScripts: "dangerously",
    pretendToBeVisual: true,
    url: "vscode-webview://ccnavi/",
    beforeParse(window) {
      const w = window as unknown as Record<string, unknown> & { HTMLElement: { prototype: object }; Element: { prototype: { getBoundingClientRect: unknown } } };
      w.acquireVsCodeApi = () => ({
        postMessage: (message: { type: string }) => {
          posted.push(JSON.parse(JSON.stringify(message)) as { type: string });
        },
        getState: () => state,
        setState: (next: unknown) => {
          state = JSON.parse(JSON.stringify(next ?? null));
        },
      });
      const sizeOf = (element: { matches?: (selector: string) => boolean }): [number, number] => {
        for (const [selector, width, height] of SIZES) {
          if (element.matches?.(`${selector}, ${selector} *`) === true) {
            return [width, height];
          }
        }
        return [0, 0];
      };
      for (const [name, index] of [
        ["offsetWidth", 0],
        ["offsetHeight", 1],
      ] as const) {
        Object.defineProperty(w.HTMLElement.prototype, name, {
          configurable: true,
          get(this: { matches?: (selector: string) => boolean }) {
            return sizeOf(this)[index];
          },
        });
      }
      w.Element.prototype.getBoundingClientRect = function (this: { matches?: (selector: string) => boolean }) {
        const [width, height] = sizeOf(this);
        return { x: 0, y: 0, top: 0, left: 0, right: width, bottom: height, width, height, toJSON: () => ({}) };
      };
      // 点の transform を読むのに使う。行列の綴りだけ読めればよい
      w.DOMMatrixReadOnly = class {
        readonly m11: number;
        readonly m12: number;
        readonly m21: number;
        readonly m22: number;
        readonly m41: number;
        readonly m42: number;
        constructor(text?: string) {
          const found = /matrix\(([^)]+)\)/.exec(String(text ?? ""));
          const [m11, m12, m21, m22, m41, m42] = found === null ? [1, 0, 0, 1, 0, 0] : found[1].split(",").map(Number);
          this.m11 = m11;
          this.m12 = m12;
          this.m21 = m21;
          this.m22 = m22;
          this.m41 = m41;
          this.m42 = m42;
        }
      };
      w.DOMMatrix = w.DOMMatrixReadOnly;
      w.ResizeObserver = class {
        private readonly callback: (entries: { target: unknown; contentRect: unknown }[]) => void;
        constructor(callback: (entries: { target: unknown; contentRect: unknown }[]) => void) {
          this.callback = callback;
        }
        observe(target: { matches?: (selector: string) => boolean }): void {
          const [width, height] = sizeOf(target);
          setTimeout(() => this.callback([{ target, contentRect: { x: 0, y: 0, top: 0, left: 0, right: width, bottom: height, width, height } }]), 0);
        }
        unobserve(): void {}
        disconnect(): void {}
      };
    },
  });
  const window = dom.window as unknown as { document: Document; MouseEvent: new (type: string, init: unknown) => Event; dispatchEvent: (event: Event) => boolean; close: () => void };
  const settle = async (): Promise<void> => {
    for (let round = 0; round < 3; round += 1) {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
  };
  // React がマウントし、点が測られて線が出るまで待つ
  await new Promise((resolve) => setTimeout(resolve, 400));
  const mouse = (type: string, x: number, y: number): Event => new window.MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, view: dom.window });
  return {
    posted,
    state: () => state,
    one(selector) {
      const found = window.document.querySelector(selector);
      if (found === null) {
        throw new Error(`見つからない: ${selector}`);
      }
      return found;
    },
    all(selector) {
      return Array.from(window.document.querySelectorAll(selector));
    },
    async drag(element, dx, dy) {
      element.dispatchEvent(mouse("mousedown", 10, 10));
      // d3-drag は動きを見てから掴む。1 回では「押しただけ」になることがあるので 2 回流す
      window.dispatchEvent(mouse("mousemove", 10 + Math.round(dx / 2), 10 + Math.round(dy / 2)));
      window.dispatchEvent(mouse("mousemove", 10 + dx, 10 + dy));
      window.dispatchEvent(mouse("mouseup", 10 + dx, 10 + dy));
      await settle();
    },
    settle,
    close() {
      window.close();
    },
  };
}
