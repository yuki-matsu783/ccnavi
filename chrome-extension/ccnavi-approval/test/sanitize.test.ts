/**
 * 悪意のある Markdown の描画（ADR-0093 の 5.5 の 2・6）。jsdom の上で DOMPurify を通す。
 * 実機の Chromium でも同じ見本を描く（test/e2e/）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import { createRenderer } from "../src/core/sanitize.js";
import { HOSTILE_MARKDOWN } from "./fixtures/repo.js";

function render(md: string) {
  const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only" });
  const r = createRenderer(dom.window as unknown as Parameters<typeof createRenderer>[0]);
  const div = dom.window.document.createElement("div");
  div.append(r.markdown(md));
  return div;
}

test("CX-T030 script・img・svg・iframe・form・style は残らない", () => {
  const div = render(HOSTILE_MARKDOWN);
  for (const tag of ["script", "img", "svg", "iframe", "form", "input", "button", "style", "object", "embed"]) {
    assert.equal(div.querySelectorAll(tag).length, 0, tag);
  }
});

test("CX-T031 on* 属性と style 属性は残らない", () => {
  const div = render(HOSTILE_MARKDOWN);
  for (const node of Array.from(div.querySelectorAll("*"))) {
    for (const attr of Array.from(node.attributes)) {
      assert.ok(!/^on/i.test(attr.name), `${node.tagName} ${attr.name}`);
      assert.notEqual(attr.name, "style");
    }
  }
});

test("CX-T032 リンクは http・https・mailto だけ。javascript:・data:・実体参照や大文字で崩したスキームは href を外す", () => {
  const div = render(HOSTILE_MARKDOWN);
  const hrefs = Array.from(div.querySelectorAll("a")).map((a) => a.getAttribute("href"));
  const kept = hrefs.filter((h): h is string => h !== null);
  assert.deepEqual(kept.sort(), ["https://example.com/ok", "mailto:a@example.com"]);
  assert.ok(hrefs.length >= 6, `リンクの数: ${hrefs.length}`);
  for (const a of Array.from(div.querySelectorAll("a[href]"))) {
    assert.equal(a.getAttribute("rel"), "noopener noreferrer");
    assert.equal(a.getAttribute("target"), "_blank");
  }
});

test("CX-T033 普通の Markdown（見出し・強調・コード・表・リスト）はそのまま描く", () => {
  const div = render("## 見出し\n\n- **強い**\n- `code`\n\n| a | b |\n|---|---|\n| 1 | 2 |\n");
  assert.equal(div.querySelector("h2")?.textContent, "見出し");
  assert.equal(div.querySelector("strong")?.textContent, "強い");
  assert.equal(div.querySelector("code")?.textContent, "code");
  assert.equal(div.querySelectorAll("td").length, 2);
});

test("CX-T034 相対リンクと # だけのリンクも href を外す（拡張の中のページへ移らせない）", () => {
  const div = render("[a](board.html) [b](#x) [c](//evil.example.com/) [d](chrome-extension://x/y)");
  assert.deepEqual(Array.from(div.querySelectorAll("a")).map((a) => a.getAttribute("href")), [null, null, null, null]);
});
