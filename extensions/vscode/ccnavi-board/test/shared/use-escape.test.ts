import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement, act, type JSX } from "react";
import { createRoot } from "react-dom/client";
import { JSDOM } from "jsdom";
import { ESCAPE_LAYER, useEscape } from "../../src/webview/useEscape.js";

/** 面を 1 枚。`active` の間だけ Esc を受け、受けたら名前を記録する */
function Face({ name, layer, active, log }: { name: string; layer: number; active: boolean; log: string[] }): JSX.Element | null {
  useEscape(active, layer, () => log.push(name));
  return null;
}

async function withDom(run: (press: () => void) => Promise<void>): Promise<void> {
  const dom = new JSDOM("<!doctype html><div id=root></div>");
  const g = globalThis as Record<string, unknown>;
  const saved = { window: g.window, document: g.document };
  g.window = dom.window;
  g.document = dom.window.document;
  g.IS_REACT_ACT_ENVIRONMENT = true;
  try {
    await run(() => {
      const event = new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
      dom.window.document.dispatchEvent(event);
    });
  } finally {
    g.window = saved.window;
    g.document = saved.document;
    dom.window.close();
  }
}

test("CB-T1139 Esc は重なった面のうち最も手前の 1 枚だけが受け、手前が閉じると次の面が受ける", async () => {
  await withDom(async (press) => {
    const log: string[] = [];
    const root = createRoot(document.getElementById("root") as HTMLElement);
    const faces = (front: boolean): JSX.Element =>
      createElement(
        "div",
        null,
        createElement(Face, { name: "overlay", layer: ESCAPE_LAYER.overlay, active: true, log }),
        createElement(Face, { name: "tour", layer: ESCAPE_LAYER.tour, active: front, log }),
        createElement(Face, { name: "maximize", layer: ESCAPE_LAYER.maximize, active: true, log }),
      );
    await act(async () => root.render(faces(true)));
    press();
    assert.deepEqual(log, ["tour"]);
    await act(async () => root.render(faces(false)));
    press();
    assert.deepEqual(log, ["tour", "maximize"]);
    await act(async () => root.unmount());
    press();
    assert.deepEqual(log, ["tour", "maximize"], "外れたあとは何も受けない");
  });
});

test("CB-T1140 変換中の Esc は面を閉じない", async () => {
  await withDom(async () => {
    const log: string[] = [];
    const root = createRoot(document.getElementById("root") as HTMLElement);
    await act(async () => root.render(createElement(Face, { name: "overlay", layer: ESCAPE_LAYER.overlay, active: true, log })));
    document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", isComposing: true, bubbles: true }));
    assert.deepEqual(log, []);
    await act(async () => root.unmount());
  });
});
