/**
 * 画面の HTML を happy-dom に読み込み、埋め込んだスクリプトを実際に動かすための土台。
 *
 * 画面のスクリプトは文字列で HTML に埋めてあり、型検査が効かない。イベントの付け忘れや
 * 無い id への参照は、DOM で動かして初めて分かる。ここでは VS Code の Webview が渡す
 * `acquireVsCodeApi` を差し替え、postMessage と state を控えて、テストから読めるようにする。
 *
 * happy-dom は innerHTML で入れた script を実行しないので、CSP の meta を外したうえで
 * 本文を入れ、JSON でない script だけを順に window.eval で走らせる。
 * happy-dom で動かないものだけ jsdom に逃がす方針（`test/helpers/jsdom.ts`）。
 * いま逃がしているのは、図の点のドラッグだけ。
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
  /** 欄に文字を入れて input イベントを流す（打っている途中。change は流さない） */
  type(element: Element, value: string): void;
  /** 欄の値を確定する（change イベント）。select を選ぶときもこれ */
  change(element: Element, value?: string): void;
  /** 要素を押す（click イベント） */
  click(element: Element): void;
  /** キーを押す（keydown を流す）。要素を渡さなければ document に流す（画面ぜんたいで受けるもの） */
  key(name: string, element?: Element): void;
  /** セレクタで 1 つ取る。無ければ落とす */
  one<T extends Element = HTMLElement>(selector: string): T;
  /** セレクタで全部取る */
  all<T extends Element = HTMLElement>(selector: string): T[];
  /** 保留中の処理（タイマー・イベント）を流す。画面のスクリプトが例外を投げていれば、ここで落ちる */
  settle(): Promise<void>;
  /** 画面のスクリプトが投げてまだ拾われていない例外。raise（settle / close / click / type / change）が投げると消える */
  readonly errors: Error[];
  close(): Promise<void>;
}

const CSP = /<meta http-equiv="Content-Security-Policy"[^>]*>/;

/** 画面を読ませるときの細工。いまは測定の偽物だけ */
export interface LoadOptions {
  /**
   * 要素の大きさを測れるようにする（`measure: true`）。**図の画面だけが要る。**
   *
   * happy-dom は `ResizeObserver` の殻を持つが `observe()` が何もせず、`offsetWidth` は 0 を返す。
   * React Flow は点の大きさを `ResizeObserver` の報せで知り、測れていない点を `visibility: hidden` の
   * まま置き、**線を 1 本も描かない**。落ちないので、細工をしないとテストは「空の絵」を見て通る。
   *
   * ここで偽るのは大きさだけで、**置き場所は偽らない**（線の経路の正しさはここでは見られない。
   * 見るのは「点と線がその本数あるか」「押すと何が起きるか」まで）。数字は下の `SIZES`。
   */
  readonly measure?: boolean;
}

/** 偽る大きさ。外枠は広め、点は `Graph.css` の `.react-flow__node-phase` と同じ幅 */
const SIZES: readonly [string, number, number][] = [
  [".react-flow__node", 170, 60],
  [".react-flow", 800, 480],
  [".graph", 800, 480],
];

function sizeOf(element: { matches?: (selector: string) => boolean }): [number, number] | undefined {
  if (typeof element.matches !== "function") {
    return undefined;
  }
  for (const [selector, width, height] of SIZES) {
    // `.react-flow__node` は `.react-flow` の下にあるので、細かいほうから見る
    if (element.matches(`${selector}, ${selector} *`)) {
      return [width, height];
    }
  }
  return undefined;
}

/**
 * 大きさを測れるようにする。**画面のスクリプトを走らせる前に入れる**（React Flow は
 * マウントの最中に `ResizeObserver` を張るので、あとから入れても間に合わない）。
 */
function fakeMeasure(window: Window): void {
  const w = window as unknown as { Element: { prototype: object }; HTMLElement: { prototype: object }; ResizeObserver?: unknown };
  const define = (target: object, name: string, get: (self: { matches?: (selector: string) => boolean }) => unknown): void => {
    Object.defineProperty(target, name, {
      configurable: true,
      get(this: { matches?: (selector: string) => boolean }) {
        return get(this);
      },
    });
  };
  define(w.HTMLElement.prototype, "offsetWidth", (self) => sizeOf(self)?.[0] ?? 0);
  define(w.HTMLElement.prototype, "offsetHeight", (self) => sizeOf(self)?.[1] ?? 0);
  (w.Element.prototype as { getBoundingClientRect: () => unknown }).getBoundingClientRect = function (this: { matches?: (selector: string) => boolean }) {
    const [width, height] = sizeOf(this) ?? [0, 0];
    return { x: 0, y: 0, top: 0, left: 0, right: width, bottom: height, width, height, toJSON: () => ({}) };
  };
  // 観測を始めたら 1 度だけ報せる。本物のように大きさが変わることは無いので、繰り返さない
  w.ResizeObserver = class {
    private readonly callback: (entries: { target: unknown; contentRect: unknown }[]) => void;
    constructor(callback: (entries: { target: unknown; contentRect: unknown }[]) => void) {
      this.callback = callback;
    }
    observe(target: { matches?: (selector: string) => boolean }): void {
      const [width, height] = sizeOf(target) ?? [0, 0];
      // 同期で呼ぶと React のマウントの最中に state を触ることになる。次の順番で呼ぶ
      setTimeout(() => this.callback([{ target, contentRect: { x: 0, y: 0, top: 0, left: 0, right: width, bottom: height, width, height } }]), 0);
    }
    unobserve(): void {}
    disconnect(): void {}
  };
}

export async function loadPage(html: string, initialState?: unknown, options: LoadOptions = {}): Promise<DomPage> {
  const { Window } = await happyDom;
  const window = new Window({ url: "vscode-webview://ccnavi/" });
  const posted: Posted[] = [];
  // イベントハンドラの中の例外は happy-dom が window の error に流す。黙って通さず、テストを落とす。
  // 非同期のハンドラ（async や Promise）の例外は window の unhandledrejection に来る。
  const errors: Error[] = [];
  const toError = (value: unknown, fallback: string): Error => (value instanceof Error ? value : new Error(String(value ?? fallback)));
  window.addEventListener("error", (event) => {
    const e = event as unknown as { error?: unknown; message?: string };
    errors.push(toError(e.error, e.message ?? "error"));
  });
  window.addEventListener("unhandledrejection", (event) => {
    errors.push(toError((event as unknown as { reason?: unknown }).reason, "unhandledrejection"));
  });
  // 溜めた例外は 1 度投げたら消す。次の raise が同じ文面を繰り返さず、後片付けの close で
  // 本来の失敗を上書きしない。
  const raise = (): void => {
    if (errors.length > 0) {
      const taken = errors.splice(0);
      throw new Error(`画面のスクリプトが例外を投げた（${taken.length} 件）: ${taken[0].message}`, { cause: taken[0] });
    }
  };
  /**
   * 欄に値を入れる。React は制御された欄の `value` を自分の追跡器で差し替えるので、
   * そのまま代入すると「値は変わっていない」と見なされて onChange が呼ばれない。
   * 追跡器は要素の側に載るので、大元（プロトタイプ）の setter を通して入れる。
   */
  const setValue = (element: Element, value: string): void => {
    const descriptor = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element) as object, "value");
    if (typeof descriptor?.set === "function") {
      descriptor.set.call(element, value);
      return;
    }
    (element as unknown as { value: string }).value = value;
  };
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
  if (options.measure === true) {
    fakeMeasure(window);
  }
  document.documentElement.innerHTML = inner;
  await window.happyDOM.waitUntilComplete();
  for (const script of Array.from(document.querySelectorAll("script"))) {
    if (script.getAttribute("type")) {
      continue;
    }
    (window as unknown as { eval: (code: string) => unknown }).eval(script.textContent ?? "");
  }
  // 本物の Webview では script のあとに DOMContentLoaded と load が流れる。待って初期化する書き方にも備える
  document.dispatchEvent(new window.Event("DOMContentLoaded", { bubbles: true }));
  window.dispatchEvent(new window.Event("load"));
  // 画面が React のとき、押した直後には描き直されない。React のスケジューラは happy-dom の
  // VM に MessageChannel が無いと setImmediate / setTimeout を使い、どちらも
  // `waitUntilComplete` は追わない（happy-dom が数えるのは自分が張ったタイマーと取得だけ）。
  // 待ちを 1 回で切ると、機械が混んでいるときに「まだ描き直していない DOM」を見て落ちる。
  // check 相（setImmediate）と timers 相（setTimeout）の両方を何度か空にしてから見る。
  const settle = async (): Promise<void> => {
    for (let round = 0; round < 4; round += 1) {
      await window.happyDOM.waitUntilComplete();
      await new Promise((resolve) => setImmediate(resolve));
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    await window.happyDOM.waitUntilComplete();
    raise();
  };
  raise();
  return {
    window,
    document,
    posted,
    errors,
    state: () => state,
    async send(message) {
      window.dispatchEvent(new window.MessageEvent("message", { data: message }));
      await settle();
    },
    type(element, value) {
      setValue(element, value);
      element.dispatchEvent(new window.Event("input", { bubbles: true }));
      raise();
    },
    change(element, value) {
      if (value !== undefined) {
        setValue(element, value);
      }
      element.dispatchEvent(new window.Event("change", { bubbles: true }));
      raise();
    },
    click(element) {
      (element as HTMLElement).click();
      raise();
    },
    key(name, element) {
      const target = element ?? (document as unknown as Element);
      target.dispatchEvent(new window.KeyboardEvent("keydown", { key: name, bubbles: true }));
      raise();
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
      raise();
    },
  };
}
